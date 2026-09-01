"""Screener.in reported-fundamentals source for the Indian market.

Screener.in publishes **reported** quarterly financials (sales, operating profit, OPM,
net profit, reported EPS) that are consistently better than yfinance for NSE names:
consolidated figures, correct INR units and none of yfinance's missing-quarter gaps.

It publishes **no analyst data at all** — no EPS estimates, no forward consensus, no
ratings, no price targets. Those five inputs carry 0.65 of ``FUND_WEIGHTS`` and 0.45 of
``DIRECTION_WEIGHTS`` in :mod:`confidence`, so Screener can never be a whole-record
replacement for Yahoo. The ingestion path therefore uses **field-level precedence**:
Screener wins for the reported financials below, Yahoo stays the sole source for
everything analyst-derived, and Yahoo also backfills any reported field Screener
couldn't supply. See ``_try_earnings_fundamentals`` in :mod:`cli`.

Screener has no public API and no documented rate limit, sits behind Cloudflare and
starts returning 429 well under 1 req/s. A full India universe is ~2,285 symbols, so an
unthrottled run would be blocked almost immediately. Four mechanisms keep a complete run
safe, all enforced *process-wide* because the fundamentals loop is multi-threaded
(``FUNDAMENTALS_THREADS``) — per-call sleeps would produce N times the intended rate:

1. :class:`_TokenBucket` — one shared bucket, so the aggregate outbound rate is capped
   regardless of how many worker threads are calling.
2. :class:`_Gate` circuit breaker — after ``SCREENER_BREAKER_THRESHOLD`` consecutive
   blocks the source is cut out and every remaining symbol in the run silently falls
   back to Yahoo, instead of burning the whole job on retries. A single half-open probe
   is allowed after a cooldown that doubles on each re-trip.
3. Per-run call budget (``SCREENER_MAX_CALLS_PER_RUN``) — a hard ceiling on how long a
   run can spend here; once spent, the remainder of the run uses Yahoo.
4. ``Retry-After``-aware exponential backoff with jitter on 429/503.

The biggest lever is upstream of all four: reported financials only change when a
company announces results, so the caller skips symbols whose stored data is already
newer than their last earnings date. After the first backfill a nightly run touches only
the handful of symbols that just reported.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date

import requests
from lxml import html as lxml_html

from config import Config

logger = logging.getLogger("ingestion.screener")

BASE_URL = "https://www.screener.in"

# Screener reports INR money rows in crore; the DB stores absolute rupees (matching
# yfinance's quarterly_income_stmt), so money values are scaled by 1e7 on the way in.
# Getting this wrong is silent: OPM/margins are ratios and would still look correct
# while Revenue/OperatingProfit/NetProfit came out 10,000,000x too small.
CRORE = 10_000_000.0

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Quarterly-table row labels -> our field names. Screener suffixes expandable rows with
# a "+" and non-breaking spaces, so labels are normalised to lowercase letters/% first.
_ROW_ALIASES = {
    "sales": "revenue",
    "revenue": "revenue",
    "income": "revenue",
    "operatingprofit": "operating_profit",
    "opm%": "opm",
    "netprofit": "net_profit",
    "epsinrs": "eps",
}


def _norm_label(text: str) -> str:
    return re.sub(r"[^a-z%]", "", (text or "").lower())


def _parse_number(text: str) -> float | None:
    """Parse a Screener cell ('1,234.56', '-12', '1,234 %', '') into a float."""
    if text is None:
        return None
    cleaned = (text.replace(",", "")
                   .replace("%", "")
                   .replace("\u2212", "-")   # unicode minus
                   .replace("\xa0", " ")
                   .strip())
    if not cleaned or cleaned in {"-", "—", "–"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _quarter_end(label: str) -> date | None:
    """'Jun 2025' -> date(2025, 6, 30): the END of that reporting month."""
    if not label:
        return None
    m = re.search(r"([A-Za-z]{3})[a-z]*\s*(\d{4})", label.replace("\xa0", " ").strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    year = int(m.group(2))
    if month == 12:
        return date(year, 12, 31)
    first_of_next = date(year, month + 1, 1)
    return date.fromordinal(first_of_next.toordinal() - 1)


@dataclass
class ScreenerQuarterly:
    """Reported quarterly financials for one symbol, in absolute INR (money) / % (OPM)."""
    symbol: str
    latest_quarter_end: date | None = None
    revenue: float | None = None
    revenue_prev_q: float | None = None
    revenue_yoy_q: float | None = None
    operating_profit: float | None = None
    operating_profit_prev_q: float | None = None
    operating_profit_yoy_q: float | None = None
    opm: float | None = None
    opm_prev_q: float | None = None
    opm_yoy_q: float | None = None
    net_profit: float | None = None
    net_profit_prev_q: float | None = None
    net_profit_yoy_q: float | None = None
    # (quarter_end, reported_eps) most recent first. Screener has no analyst estimate,
    # so these fill the *actual* EPS slots only — never estimate/surprise.
    eps_quarters: list[tuple[date, float]] = field(default_factory=list)
    consolidated: bool = True

    def has_financials(self) -> bool:
        return self.revenue is not None or self.net_profit is not None


# --- throttling primitives ----------------------------------------------------------- #
class _TokenBucket:
    """Thread-safe token bucket. One instance guards *all* outbound Screener calls."""

    def __init__(self, rate_per_min: float, burst: int) -> None:
        self._rate = max(rate_per_min, 0.1) / 60.0
        self._capacity = float(max(burst, 1))
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(min(wait, 5.0))


class _Gate:
    """Per-run circuit breaker + call budget shared by every worker thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._calls = 0
            self._consecutive_blocks = 0
            self._open_until = 0.0
            self._cooldown = float(Config.SCREENER_BREAKER_COOLDOWN)
            self._trips = 0
            self._blocked = 0
            self._errors = 0
            self._hits = 0

    def allow(self) -> tuple[bool, str | None]:
        with self._lock:
            if Config.SCREENER_MAX_CALLS_PER_RUN > 0 and self._calls >= Config.SCREENER_MAX_CALLS_PER_RUN:
                return False, "call budget exhausted"
            if self._open_until and time.monotonic() < self._open_until:
                return False, "circuit breaker open"
            self._calls += 1
            return True, None

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_blocks = 0
            self._open_until = 0.0
            self._cooldown = float(Config.SCREENER_BREAKER_COOLDOWN)
            self._hits += 1

    def record_block(self) -> None:
        """A 429/403/503 — the signal that we are being throttled."""
        with self._lock:
            self._blocked += 1
            self._consecutive_blocks += 1
            if self._consecutive_blocks >= Config.SCREENER_BREAKER_THRESHOLD:
                self._open_until = time.monotonic() + self._cooldown
                self._trips += 1
                logger.warning(
                    "Screener circuit breaker tripped after %d consecutive blocks; "
                    "falling back to yfinance for %.0fs",
                    self._consecutive_blocks, self._cooldown)
                self._consecutive_blocks = 0
                self._cooldown = min(self._cooldown * 2, 3600.0)

    def record_error(self) -> None:
        with self._lock:
            self._errors += 1

    def stats(self) -> dict:
        with self._lock:
            return {"calls": self._calls, "hits": self._hits, "blocked": self._blocked,
                    "errors": self._errors, "breaker_trips": self._trips}


_bucket: _TokenBucket | None = None
_bucket_lock = threading.Lock()
_gate = _Gate()
_session: requests.Session | None = None
_session_lock = threading.Lock()


def _get_bucket() -> _TokenBucket:
    global _bucket
    with _bucket_lock:
        if _bucket is None:
            _bucket = _TokenBucket(Config.SCREENER_RATE_PER_MIN, Config.SCREENER_BURST)
        return _bucket


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": Config.SCREENER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-IN,en;q=0.9",
            })
            _session = s
        return _session


def reset_run_state() -> None:
    """Clear breaker/budget counters at the start of a run."""
    _gate.reset()


def stats() -> dict:
    return _gate.stats()


def is_enabled(market: str) -> bool:
    """Screener.in only covers Indian listings."""
    return Config.SCREENER_ENABLED and market.lower() == "india"


# --- fetch ---------------------------------------------------------------------------- #
def _get(url: str) -> requests.Response | None:
    """One rate-limited GET with Retry-After-aware backoff. None if it never succeeded."""
    attempts = max(Config.SCREENER_MAX_RETRIES, 1)
    for attempt in range(attempts):
        allowed, reason = _gate.allow()
        if not allowed:
            logger.debug("Screener skipped (%s): %s", reason, url)
            return None

        _get_bucket().acquire()
        try:
            resp = _get_session().get(url, timeout=Config.SCREENER_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            _gate.record_error()
            logger.debug("Screener request failed for %s: %s", url, exc)
            if attempt == attempts - 1:
                return None
            time.sleep(Config.SCREENER_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5))
            continue

        if resp.status_code == 200:
            _gate.record_success()
            return resp
        if resp.status_code == 404:
            _gate.record_success()  # a real answer, just not a page we want
            return None
        if resp.status_code in (429, 403, 503):
            _gate.record_block()
            if attempt == attempts - 1:
                return None
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else None
            except ValueError:
                delay = None
            if delay is None:
                delay = Config.SCREENER_RETRY_BASE_DELAY * (2 ** attempt)
            delay = min(delay, 120.0) + random.uniform(0, 0.75)
            logger.debug("Screener %s for %s; sleeping %.1fs", resp.status_code, url, delay)
            time.sleep(delay)
            continue

        _gate.record_error()
        return None
    return None


def _parse_quarters(doc, symbol: str, consolidated: bool) -> ScreenerQuarterly | None:
    """Pull the Quarterly Results table out of a Screener company page."""
    tables = doc.xpath('//section[@id="quarters"]//table')
    if not tables:
        return None
    table = tables[0]

    headers = [(_t.text_content() or "").strip() for _t in table.xpath(".//thead//th")]
    # First header cell is the row-label column.
    quarter_labels = headers[1:]
    if not quarter_labels:
        return None

    rows: dict[str, list[float | None]] = {}
    for tr in table.xpath(".//tbody/tr"):
        cells = tr.xpath("./td")
        if len(cells) < 2:
            continue
        key = _ROW_ALIASES.get(_norm_label(cells[0].text_content()))
        if not key or key in rows:
            continue
        rows[key] = [_parse_number(c.text_content()) for c in cells[1:]]

    if not rows:
        return None

    n = len(quarter_labels)
    # Column dates drive every lookup below. Screener's quarterly table is NOT guaranteed to
    # be a contiguous run of quarters — small/irregular reporters and recently-listed names
    # can have gaps or fewer columns — so a positional "4 columns back = year ago" offset is
    # wrong. It silently returns either None (when the table is short) or, worse, a real
    # number from the WRONG quarter, manufacturing a plausible-looking but fake YoY move.
    # Everything is therefore matched on the parsed quarter-end date instead.
    col_dates = [_quarter_end(lbl) for lbl in quarter_labels]

    latest_idx = None
    for i in range(min(len(col_dates), n) - 1, -1, -1):
        if col_dates[i] is not None:
            latest_idx = i
            break
    if latest_idx is None:
        return None
    latest_date = col_dates[latest_idx]

    def _shift_months(d: date, months: int) -> date:
        total = d.year * 12 + (d.month - 1) + months
        y, m = divmod(total, 12)
        m += 1
        return date(y, 12, 31) if m == 12 else date.fromordinal(date(y, m + 1, 1).toordinal() - 1)

    def _index_for(target: date, tol_days: int = 45) -> int | None:
        """Column whose quarter-end is nearest ``target``, or None if none is close enough."""
        best = best_delta = None
        for i, d in enumerate(col_dates):
            if d is None or i >= n:
                continue
            delta = abs((d - target).days)
            if delta <= tol_days and (best_delta is None or delta < best_delta):
                best, best_delta = i, delta
        return best

    idx_latest = latest_idx
    idx_prev_q = _index_for(_shift_months(latest_date, -3))
    idx_yoy_q = _index_for(_shift_months(latest_date, -12))

    def at(key: str, idx: int | None) -> float | None:
        series = rows.get(key)
        if not series or idx is None:
            return None
        return series[idx] if 0 <= idx < len(series) else None

    def money(key: str, idx: int | None) -> float | None:
        v = at(key, idx)
        return None if v is None else v * CRORE

    out = ScreenerQuarterly(symbol=symbol, consolidated=consolidated)
    out.latest_quarter_end = latest_date
    out.revenue = money("revenue", idx_latest)
    out.revenue_prev_q = money("revenue", idx_prev_q)
    out.revenue_yoy_q = money("revenue", idx_yoy_q)
    out.operating_profit = money("operating_profit", idx_latest)
    out.operating_profit_prev_q = money("operating_profit", idx_prev_q)
    out.operating_profit_yoy_q = money("operating_profit", idx_yoy_q)
    out.opm = at("opm", idx_latest)
    out.opm_prev_q = at("opm", idx_prev_q)
    out.opm_yoy_q = at("opm", idx_yoy_q)
    out.net_profit = money("net_profit", idx_latest)
    out.net_profit_prev_q = money("net_profit", idx_prev_q)
    out.net_profit_yoy_q = money("net_profit", idx_yoy_q)

    eps_quarters: list[tuple[date, float]] = []
    for i in range(min(len(col_dates), n) - 1, -1, -1):
        if len(eps_quarters) >= 4:
            break
        val = at("eps", i)
        qend = col_dates[i]
        if val is not None and qend is not None:
            eps_quarters.append((qend, val))
    out.eps_quarters = eps_quarters

    return out if out.has_financials() else None


def fetch_quarterly(symbol: str) -> ScreenerQuarterly | None:
    """Fetch reported quarterly financials for an NSE ``symbol``.

    Returns ``None`` whenever Screener is unavailable for any reason — disabled, budget
    spent, breaker open, blocked, 404 or unparseable markup. Every one of those is a
    normal outcome that the caller handles by falling back to yfinance, so this never
    raises.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    for consolidated in (True, False):
        suffix = "consolidated/" if consolidated else ""
        url = f"{BASE_URL}/company/{sym}/{suffix}"
        resp = _get(url)
        if resp is None:
            continue
        try:
            doc = lxml_html.fromstring(resp.content)
            parsed = _parse_quarters(doc, sym, consolidated)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Screener parse failed for %s: %s", sym, exc)
            parsed = None
        if parsed is not None:
            return parsed
    return None
