"""Market regime job runner (feature 013).

A ``market_regime`` job:

1. ``benchmark`` — fetches ~2y of daily bars for the market's **index-class** symbols (the
   benchmark index and its volatility proxy) and upserts them into
   ``{Market}BenchmarkBars1D``. These are the only network calls in the job.
2. ``breadth`` — loads the active stock universe's ingested closes from ``{Market}Bars1D``
   (no network) and the persisted benchmark bars, then computes **everything**: the
   participation percentages, benchmark returns/52w-high distance/volatility, the benchmark
   *condition* label (§3.1), the *breadth* composite label/score (§3.2), and the combined
   *effective regime* + posture (§4).
3. ``persist`` — upserts the fully-computed ``{Market}RegimeSnapshots`` row for the as-of
   date (idempotent per ``AsOfDate``).

All regime business logic lives here in the worker; the API is a thin reader that returns the
latest persisted snapshot row.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from config import Config
from db import get_connection, update_job_status
from job_stages import StageTracker
from market_hours import is_market_open, market_local_now

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOLS = {"india": "^NSEI", "us": "^GSPC"}
VOLATILITY_SYMBOLS = {"india": "^INDIAVIX", "us": "^VIX"}

_STOCK_BARS = {"india": "IndianBars1D", "us": "USBars1D"}
_BENCH_BARS = {"india": "IndianBenchmarkBars1D", "us": "USBenchmarkBars1D"}
_SNAPSHOTS = {"india": "IndianRegimeSnapshots", "us": "USRegimeSnapshots"}

# ~2 years of daily history: comfortably covers SMA200, the 300-bar condition window, and 1y
# returns with headroom.
_INDEX_LOOKBACK_PERIOD = "2y"

# A stock needs at least this many closes to count as a "valid" breadth participant.
_MIN_VALID_BARS = 20


def _table(mapping: dict[str, str], market: str) -> str:
    t = mapping.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


# --------------------------------------------------------------------------- #
# Pure math helpers (imported directly by unit tests)
# --------------------------------------------------------------------------- #
def _sma(closes: list[float], n: int) -> float | None:
    """Simple moving average of the last ``n`` closes, or ``None`` when too short."""
    if len(closes) < n or n <= 0:
        return None
    window = closes[-n:]
    return sum(window) / n


def _pct(count: int, total: int) -> float | None:
    return round(100.0 * count / total, 2) if total else None


def compute_participation(closes_by_ticker: dict[str, list[float]]) -> dict[str, Any]:
    """Percentage-of-universe participation metrics from each ticker's chronological closes.

    Each SMA percentage is computed over only the stocks that have enough history for that
    SMA (per-SMA denominator), so a short-history name never counts against a longer SMA.
    Returns the six participation percentages (or ``None``) plus ``evaluated_count`` — the
    number of stocks with at least :data:`_MIN_VALID_BARS` closes.
    """
    above = {10: 0, 20: 0, 50: 0, 200: 0}
    have = {10: 0, 20: 0, 50: 0, 200: 0}
    sma20_gt_50 = sma20_50_have = 0
    sma50_gt_200 = sma50_200_have = 0
    evaluated = 0

    for closes in closes_by_ticker.values():
        valid = [c for c in closes if c is not None]
        if len(valid) < _MIN_VALID_BARS:
            continue
        evaluated += 1
        last = valid[-1]
        smas: dict[int, float | None] = {n: _sma(valid, n) for n in (10, 20, 50, 200)}
        for n in (10, 20, 50, 200):
            if smas[n] is not None:
                have[n] += 1
                if last > smas[n]:
                    above[n] += 1
        if smas[20] is not None and smas[50] is not None:
            sma20_50_have += 1
            if smas[20] > smas[50]:
                sma20_gt_50 += 1
        if smas[50] is not None and smas[200] is not None:
            sma50_200_have += 1
            if smas[50] > smas[200]:
                sma50_gt_200 += 1

    return {
        "evaluated_count": evaluated,
        "pct_above_sma10": _pct(above[10], have[10]),
        "pct_above_sma20": _pct(above[20], have[20]),
        "pct_above_sma50": _pct(above[50], have[50]),
        "pct_above_sma200": _pct(above[200], have[200]),
        "pct_sma20_above_sma50": _pct(sma20_gt_50, sma20_50_have),
        "pct_sma50_above_sma200": _pct(sma50_gt_200, sma50_200_have),
    }


def _pct_change(latest: float, base: float | None) -> float | None:
    if base is None or base == 0:
        return None
    return round((latest / base - 1.0) * 100.0, 4)


def compute_benchmark_context(frame: pd.DataFrame | None) -> dict[str, Any]:
    """Benchmark returns + 52-week-high distance from a date-indexed OHLC frame.

    ``frame`` must be sorted ascending by date with a ``Close`` column (``High`` optional).
    Returns YTD / 1-week / 1-month / 1-year percent changes and the percent distance from the
    trailing 52-week high (``<= 0`` when below). All values are ``None`` when uncomputable.
    """
    empty = {
        "benchmark_ytd_pct": None, "benchmark_1w_pct": None, "benchmark_1m_pct": None,
        "benchmark_1y_pct": None, "benchmark_pct_from_52w_high": None,
    }
    if frame is None or frame.empty or "Close" not in frame.columns:
        return empty

    closes = frame["Close"].dropna()
    if closes.empty:
        return empty

    dates = [d.date() if hasattr(d, "date") else d for d in closes.index]
    values = [float(v) for v in closes.tolist()]
    latest_date = dates[-1]
    latest = values[-1]

    def _close_on_or_before(cutoff: date) -> float | None:
        chosen = None
        for d, v in zip(dates, values):
            if d <= cutoff:
                chosen = v
            else:
                break
        return chosen

    # YTD: last close of the previous year, else the first close of the current year.
    ytd_base = _close_on_or_before(date(latest_date.year, 1, 1) - timedelta(days=1))
    if ytd_base is None:
        current_year = [v for d, v in zip(dates, values) if d.year == latest_date.year]
        ytd_base = current_year[0] if current_year else None

    high_window = frame["High"] if "High" in frame.columns else frame["Close"]
    high_window = high_window.dropna().tail(252)
    high_52w = float(high_window.max()) if not high_window.empty else None

    return {
        "benchmark_ytd_pct": _pct_change(latest, ytd_base),
        "benchmark_1w_pct": _pct_change(latest, _close_on_or_before(latest_date - timedelta(days=7))),
        "benchmark_1m_pct": _pct_change(latest, _close_on_or_before(latest_date - timedelta(days=30))),
        "benchmark_1y_pct": _pct_change(latest, _close_on_or_before(latest_date - timedelta(days=365))),
        "benchmark_pct_from_52w_high": _pct_change(latest, high_52w),
    }


# --------------------------------------------------------------------------- #
# Regime logic — the single source of truth for §3.1 / §3.2 / §4 (the API only
# reads the persisted result; it contains no thresholds or combination rules).
# --------------------------------------------------------------------------- #
def _closed_below_sma20_within_last(closes: list[float], lookback: int, require_all: bool = False) -> bool:
    """Whether the index closed below its trailing SMA20 within the last ``lookback`` sessions.

    With ``require_all`` every one of those sessions must be below (the "two sessions" rule).
    """
    if len(closes) < 21:
        return False
    start = len(closes) - lookback
    any_below = False
    all_below = True
    for i in range(start, len(closes)):
        if i < 20:
            all_below = False
            continue
        window = closes[i - 19:i + 1]  # 20 closes ending at i
        sma20 = sum(window) / 20.0
        below = closes[i] < sma20
        any_below = any_below or below
        all_below = all_below and below
    return all_below if require_all else any_below


def compute_condition(symbol: str | None, as_of: date | None,
                      closes: list[float], volumes: list[int | None]) -> dict[str, Any]:
    """Benchmark condition label (spec §3.1) from chronological closes/volumes.

    Returns a dict with the label, tone, explanation, the SMA distances, the volume-vs-average
    percentage, and ``available``. When there are no closes the condition is *unavailable*
    (never inferred from breadth).
    """
    if not closes:
        return {
            "condition_label": "Unavailable", "condition_tone": "grey",
            "condition_explanation": "No benchmark bars ingested yet.",
            "condition_available": False, "condition_as_of": None,
            "condition_close": None, "condition_sma20": None, "condition_sma50": None,
            "condition_sma200": None, "condition_close_vs_sma20_pct": None,
            "condition_close_vs_sma50_pct": None, "condition_close_vs_sma200_pct": None,
            "condition_volume_vs_avg_pct": None,
        }

    close = closes[-1]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    valid_vol = [float(v) for v in volumes[-50:] if v is not None and v > 0]
    avg_vol50 = (sum(valid_vol) / len(valid_vol)) if valid_vol else None
    current_vol = volumes[-1] if volumes else None
    volume_confirmed = current_vol is not None and avg_vol50 is not None and current_vol > avg_vol50

    recently_cautious = _closed_below_sma20_within_last(closes, 5)
    below_two_sessions = _closed_below_sma20_within_last(closes, 2, require_all=True)

    def _pct_vs(sma: float | None) -> float | None:
        return round((close / sma - 1.0) * 100.0, 2) if sma and sma > 0 else None

    vol_pct = (round((current_vol / avg_vol50 - 1.0) * 100.0, 2)
               if current_vol is not None and avg_vol50 and avg_vol50 > 0 else None)

    if sma200 is not None and close <= sma200 * 0.90:
        label, tone = "Pessimistic", "red"
        why = "Index is 10%+ below its 200-day average — broad downtrend."
    elif sma50 is not None and close < sma50:
        label, tone = "Bearish", "red"
        why = "Index is below its 50-day average."
    elif below_two_sessions or (recently_cautious and not volume_confirmed):
        label, tone = "Cautious", "yellow"
        why = ("Index has closed below its 20-day average for two sessions."
               if below_two_sessions
               else "Recent dip below the 20-day average with no volume-confirmed recovery.")
    elif sma20 is not None and close > sma20 * 1.10:
        label, tone = "Euphoric", "green"
        why = "Index is 10%+ above its 20-day average — extended; watch for pullback."
    elif sma20 is not None and close > sma20 and (not recently_cautious or volume_confirmed):
        label, tone = "Uptrend", "green"
        why = "Index is above its 20-day average in a constructive trend."
    else:
        label, tone = "Neutral", "grey"
        why = "No dominant trend signal."

    return {
        "condition_label": label, "condition_tone": tone, "condition_explanation": why,
        "condition_available": True, "condition_as_of": as_of,
        "condition_close": round(close, 4),
        "condition_sma20": round(sma20, 4) if sma20 is not None else None,
        "condition_sma50": round(sma50, 4) if sma50 is not None else None,
        "condition_sma200": round(sma200, 4) if sma200 is not None else None,
        "condition_close_vs_sma20_pct": _pct_vs(sma20),
        "condition_close_vs_sma50_pct": _pct_vs(sma50),
        "condition_close_vs_sma200_pct": _pct_vs(sma200),
        "condition_volume_vs_avg_pct": vol_pct,
    }


# (key, label, threshold, direction) — direction ">" means positive when value > threshold.
_BREADTH_SIGNAL_SPEC = [
    ("pct_above_sma10", "pctAboveSma10", "% above SMA10", 60.0, ">"),
    ("pct_above_sma20", "pctAboveSma20", "% above SMA20", 60.0, ">"),
    ("pct_above_sma50", "pctAboveSma50", "% above SMA50", 60.0, ">"),
    ("pct_above_sma200", "pctAboveSma200", "% above SMA200", 60.0, ">"),
    ("pct_sma20_above_sma50", "pctSma20AboveSma50", "% SMA20 > SMA50", 60.0, ">"),
    ("pct_sma50_above_sma200", "pctSma50AboveSma200", "% SMA50 > SMA200", 60.0, ">"),
    ("benchmark_ytd_pct", "benchmarkYtdPct", "Benchmark YTD", 5.0, ">"),
    ("benchmark_1w_pct", "benchmark1wPct", "Benchmark 1-week", 1.0, ">"),
    ("benchmark_1m_pct", "benchmark1mPct", "Benchmark 1-month", 3.0, ">"),
    ("benchmark_1y_pct", "benchmark1yPct", "Benchmark 1-year", 15.0, ">"),
    ("benchmark_pct_from_52w_high", "benchmarkPctFrom52wHigh", "Distance from 52w high", -5.0, ">"),
    ("volatility_close", "volatilityClose", "Volatility", 15.0, "<"),
]


def _fmt_threshold(direction: str, threshold: float) -> str:
    t = f"{threshold:g}"
    return f"{direction} {t}"


def compute_breadth(facts: dict[str, Any]) -> dict[str, Any]:
    """Breadth composite score/label (spec §3.2) from the raw participation/context facts.

    ``facts`` provides each signal's raw value (or ``None``). A ``None`` signal is excluded
    from the denominator (§8) rather than counted as negative. Returns the label, tone, score
    (percent of positive signals among available), counts, availability, and the per-signal
    breakdown (for the UI).
    """
    signals: list[dict[str, Any]] = []
    for key, dto_key, label, threshold, direction in _BREADTH_SIGNAL_SPEC:
        value = facts.get(key)
        positive: bool | None
        if value is None:
            positive = None
        elif direction == ">":
            positive = value > threshold
        else:
            positive = value < threshold
        signals.append({
            "key": dto_key, "label": label,
            "value": round(float(value), 4) if value is not None else None,
            "threshold": _fmt_threshold(direction, threshold),
            "positive": positive,
        })

    available = [s for s in signals if s["positive"] is not None]
    positives = sum(1 for s in available if s["positive"])
    score = round(100.0 * positives / len(available)) if available else None

    if score is None:
        label, tone = "Unavailable", "grey"
    elif score > 80:
        label, tone = "Bullish", "green"
    elif score > 60:
        label, tone = "Positive", "green"
    elif score > 40:
        label, tone = "Neutral", "grey"
    elif score > 20:
        label, tone = "Negative", "yellow"
    else:
        label, tone = "Bearish", "red"

    return {
        "breadth_label": label, "breadth_tone": tone, "breadth_score": score,
        "breadth_positive_signals": positives, "breadth_available_signals": len(available),
        "breadth_available": score is not None, "signals": signals,
    }


def combine_regime(condition_label: str, breadth_label: str) -> dict[str, str]:
    """Combine the two component labels into an effective regime + posture (spec §4)."""
    if condition_label == "Unavailable" or breadth_label == "Unavailable":
        return {"regime": "Unavailable", "regime_label": "Unavailable", "regime_tone": "grey",
                "posture": "Insufficient data to determine market context."}

    bullish_cond = condition_label in ("Uptrend", "Euphoric")
    neutral_cond = condition_label == "Neutral"
    weak_cond = condition_label == "Cautious"
    bearish_cond = condition_label in ("Bearish", "Pessimistic")

    strong_breadth = breadth_label in ("Bullish", "Positive")
    neutral_breadth = breadth_label == "Neutral"
    weak_breadth = breadth_label in ("Negative", "Bearish")

    if bullish_cond and strong_breadth:
        regime = "RiskOn"
    elif bearish_cond and weak_breadth:
        regime = "RiskOff"
    elif (weak_cond and (neutral_breadth or weak_breadth)) or (bearish_cond and neutral_breadth):
        regime = "Caution"
    elif ((bullish_cond and neutral_breadth) or (neutral_cond and (strong_breadth or neutral_breadth))
          or (weak_cond and strong_breadth)):
        regime = "SelectiveRiskOn"
    else:
        regime = "Mixed"

    mapping = {
        "RiskOn": ("Risk On", "green",
                   "Broad market supportive — take breakout/setup risk aggressively within scanner rules."),
        "SelectiveRiskOn": ("Selective Risk On", "green",
                            "Constructive but not broad — be selective and favour the strongest setups."),
        "Caution": ("Caution", "yellow",
                    "Deteriorating tape — tighten risk, reduce size, demand high-quality setups."),
        "RiskOff": ("Risk Off", "red",
                    "Hostile tape — prioritise capital preservation; avoid new breakout risk."),
        "Mixed": ("Mixed", "yellow",
                  "Trend and breadth disagree — treat context as unresolved; wait for confirmation."),
    }
    regime_label, tone, posture = mapping[regime]
    return {"regime": regime, "regime_label": regime_label, "regime_tone": tone, "posture": posture}


# --------------------------------------------------------------------------- #
# Data loading / persistence
# --------------------------------------------------------------------------- #
def _load_universe_closes(conn, market: str) -> tuple[dict[str, list[float]], date | None]:
    """Load chronological closes per ticker from ``{Market}Bars1D`` (no network).

    Returns ``(closes_by_ticker, as_of)`` where ``as_of`` is the latest bar date across the
    universe. Trims each series to the most recent 260 bars (enough for SMA200).
    """
    bars_table = _table(_STOCK_BARS, market)
    query = f"""
        SELECT b.Ticker, b.BarDate, b.[Close]
        FROM dbo.{bars_table} b
        WHERE b.[Close] IS NOT NULL
        ORDER BY b.Ticker, b.BarDate
    """
    rows = conn.cursor().execute(query).fetchall()
    closes_by_ticker: dict[str, list[float]] = {}
    as_of: date | None = None
    for r in rows:
        closes_by_ticker.setdefault(r.Ticker, []).append(float(r.Close))
        bd = r.BarDate if isinstance(r.BarDate, date) else pd.Timestamp(r.BarDate).date()
        if as_of is None or bd > as_of:
            as_of = bd
    # Trim to the deepest SMA window plus a little headroom.
    for ticker, closes in closes_by_ticker.items():
        if len(closes) > 260:
            closes_by_ticker[ticker] = closes[-260:]
    return closes_by_ticker, as_of


def _load_benchmark_frame(conn, market: str, symbol: str) -> pd.DataFrame | None:
    """Load an index symbol's persisted daily bars into an ascending date-indexed frame.

    Reads from ``{Market}BenchmarkBars1D`` so condition/context always use the stored data
    (works even if this run's network fetch failed but prior bars exist). ``None`` when empty.
    """
    table = _table(_BENCH_BARS, market)
    query = f"""
        SELECT BarDate, [Close], High, Volume
        FROM dbo.{table}
        WHERE Symbol = ? AND [Close] IS NOT NULL
        ORDER BY BarDate
    """
    rows = conn.cursor().execute(query, symbol).fetchall()
    if not rows:
        return None
    idx = [r.BarDate if isinstance(r.BarDate, date) else pd.Timestamp(r.BarDate).date() for r in rows]
    frame = pd.DataFrame(
        {
            "Close": [float(r.Close) for r in rows],
            "High": [float(r.High) if r.High is not None else float(r.Close) for r in rows],
            "Volume": [int(r.Volume) if r.Volume is not None else None for r in rows],
        },
        index=pd.to_datetime(idx),
    )
    return frame


def _fetch_index_frame(symbol: str) -> pd.DataFrame | None:
    """Download ~2y of daily bars for an index-class symbol; ``None`` on failure/empty."""
    try:
        raw = yf.download(
            tickers=symbol, period=_INDEX_LOOKBACK_PERIOD, interval="1d",
            auto_adjust=False, progress=False, threads=False,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort; caller degrades gracefully
        logger.warning("Index fetch failed for %s: %s", symbol, exc)
        return None
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.droplevel("Ticker", axis=1) if "Ticker" in raw.columns.names else raw.droplevel(1, axis=1)
    if "Close" not in raw.columns:
        return None
    return raw.dropna(how="all").sort_index()


def _fetch_live_index_price(symbol: str) -> float | None:
    """Best-effort **live** last price for an index-class symbol (used during market hours).

    Tries ``fast_info`` (cheap, near-real-time) first, then a 1-minute intraday history as a
    fallback. Returns ``None`` on any failure so the caller degrades to the stored daily bar.
    """
    try:
        ticker = yf.Ticker(symbol)
        fi = getattr(ticker, "fast_info", None)
        if fi is not None:
            for accessor in (
                lambda: fi["lastPrice"],
                lambda: fi["last_price"],
                lambda: fi.last_price,  # type: ignore[union-attr]
            ):
                try:
                    price = accessor()
                except Exception:  # noqa: BLE001 - try the next accessor shape
                    continue
                p = _f(price)
                if p is not None and p > 0:
                    return p
    except Exception as exc:  # noqa: BLE001 - fall through to intraday history
        logger.debug("fast_info live price failed for %s: %s", symbol, exc)

    try:
        intraday = yf.Ticker(symbol).history(period="1d", interval="1m", auto_adjust=False)
        if intraday is not None and not intraday.empty and "Close" in intraday.columns:
            closes = intraday["Close"].dropna()
            if not closes.empty:
                p = _f(float(closes.iloc[-1]))
                if p is not None and p > 0:
                    return p
    except Exception as exc:  # noqa: BLE001 - best-effort; caller degrades gracefully
        logger.warning("Live intraday price fetch failed for %s: %s", symbol, exc)
    return None


def _apply_live_price(frame: pd.DataFrame | None, trading_date: date, price: float) -> pd.DataFrame | None:
    """Overlay a live ``price`` onto ``trading_date``'s row (provisional intraday close).

    Updates Close (and keeps High consistent) for an existing row, or appends a new provisional
    bar when the day is missing. Volume is intentionally left untouched: today's partial volume
    stays as-is (or NULL for a fresh row) so the condition's volume-confirmation never treats a
    partial session as a full day. Returns the (possibly new) frame.
    """
    ts = pd.Timestamp(trading_date)
    if frame is None or frame.empty:
        frame = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    if ts in frame.index:
        frame.loc[ts, "Close"] = price
        if "High" in frame.columns:
            cur_high = frame.loc[ts, "High"]
            frame.loc[ts, "High"] = price if pd.isna(cur_high) else max(float(cur_high), price)
        if "Low" in frame.columns:
            cur_low = frame.loc[ts, "Low"]
            frame.loc[ts, "Low"] = price if pd.isna(cur_low) else min(float(cur_low), price)
    else:
        row = {c: None for c in frame.columns}
        row.update({"Open": price, "High": price, "Low": price, "Close": price})
        frame.loc[ts] = pd.Series(row)
        frame = frame.sort_index()
    return frame


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 4)


def _upsert_bench_bars(conn, table: str, symbol: str, frame: pd.DataFrame) -> int:
    """Upsert an index symbol's daily bars into ``{Market}BenchmarkBars1D``."""
    staged: list[tuple] = []
    for ts, row in frame.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        vol = row.get("Volume")
        staged.append((
            symbol, bar_date,
            _f(row.get("Open")), _f(row.get("High")), _f(row.get("Low")), _f(row.get("Close")),
            int(vol) if pd.notna(vol) else None, _f(row.get("Adj Close")),
        ))
    if not staged:
        return 0
    cur = conn.cursor()
    cur.execute("IF OBJECT_ID('tempdb..#BenchBars') IS NOT NULL DROP TABLE #BenchBars;")
    cur.execute(
        """
        CREATE TABLE #BenchBars (
            Symbol NVARCHAR(30) NOT NULL, BarDate DATE NOT NULL,
            [Open] DECIMAL(18,4) NULL, High DECIMAL(18,4) NULL, Low DECIMAL(18,4) NULL,
            [Close] DECIMAL(18,4) NULL, Volume BIGINT NULL, AdjClose DECIMAL(18,4) NULL);
        """
    )
    cur.fast_executemany = True
    cur.executemany(
        "INSERT INTO #BenchBars (Symbol, BarDate, [Open], High, Low, [Close], Volume, AdjClose) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        staged,
    )
    cur.execute(
        f"""
        MERGE dbo.{table} AS tgt USING #BenchBars AS src
        ON tgt.Symbol = src.Symbol AND tgt.BarDate = src.BarDate
        WHEN MATCHED THEN UPDATE SET [Open]=src.[Open], High=src.High, Low=src.Low,
            [Close]=src.[Close], Volume=src.Volume, AdjClose=src.AdjClose
        WHEN NOT MATCHED THEN INSERT (Symbol, BarDate, [Open], High, Low, [Close], Volume, AdjClose)
            VALUES (src.Symbol, src.BarDate, src.[Open], src.High, src.Low, src.[Close], src.Volume, src.AdjClose);
        """
    )
    cur.execute("DROP TABLE #BenchBars;")
    conn.commit()
    return len(staged)


def _persist_regime(conn, market: str, as_of: date, data: dict[str, Any]) -> None:
    """Idempotent upsert of the fully-computed regime snapshot for ``(market, as_of)``."""
    table = _table(_SNAPSHOTS, market)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM dbo.{table} WHERE AsOfDate = ?", as_of)
    cur.execute(
        f"""
        INSERT INTO dbo.{table}
            (AsOfDate, ConditionAsOfDate, BreadthAsOfDate, BenchmarkSymbol, VolatilitySymbol,
             EvaluatedCount,
             Regime, RegimeLabel, RegimeTone, Posture, Available,
             ConditionLabel, ConditionTone, ConditionExplanation, ConditionAvailable,
             ConditionClose, ConditionSma20, ConditionSma50, ConditionSma200,
             ConditionCloseVsSma20Pct, ConditionCloseVsSma50Pct, ConditionCloseVsSma200Pct,
             ConditionVolumeVsAvgPct,
             BreadthLabel, BreadthTone, BreadthScore, BreadthPositiveSignals,
             BreadthAvailableSignals, BreadthAvailable, SignalsJson,
             PctAboveSma10, PctAboveSma20, PctAboveSma50, PctAboveSma200,
             PctSma20AboveSma50, PctSma50AboveSma200,
             BenchmarkYtdPct, Benchmark1wPct, Benchmark1mPct, Benchmark1yPct,
             BenchmarkPctFrom52wHigh, VolatilityClose, IsIntraday)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?,                 ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        as_of, data.get("condition_as_of"), data.get("breadth_as_of"),
        data.get("benchmark_symbol"), data.get("volatility_symbol"),
        data.get("evaluated_count", 0),
        data["regime"], data["regime_label"], data["regime_tone"], data.get("posture"),
        1 if data.get("available") else 0,
        data["condition_label"], data["condition_tone"], data.get("condition_explanation"),
        1 if data.get("condition_available") else 0,
        data.get("condition_close"), data.get("condition_sma20"), data.get("condition_sma50"),
        data.get("condition_sma200"), data.get("condition_close_vs_sma20_pct"),
        data.get("condition_close_vs_sma50_pct"), data.get("condition_close_vs_sma200_pct"),
        data.get("condition_volume_vs_avg_pct"),
        data["breadth_label"], data["breadth_tone"], data.get("breadth_score"),
        data.get("breadth_positive_signals", 0), data.get("breadth_available_signals", 0),
        1 if data.get("breadth_available") else 0, data.get("signals_json"),
        data.get("pct_above_sma10"), data.get("pct_above_sma20"),
        data.get("pct_above_sma50"), data.get("pct_above_sma200"),
        data.get("pct_sma20_above_sma50"), data.get("pct_sma50_above_sma200"),
        data.get("benchmark_ytd_pct"), data.get("benchmark_1w_pct"),
        data.get("benchmark_1m_pct"), data.get("benchmark_1y_pct"),
        data.get("benchmark_pct_from_52w_high"), data.get("volatility_close"),
        1 if data.get("is_intraday") else 0,
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Job entry point
# --------------------------------------------------------------------------- #
def run_market_regime_job(payload: dict) -> None:
    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    benchmark_symbol = BENCHMARK_SYMBOLS.get(market)
    volatility_symbol = VOLATILITY_SYMBOLS.get(market)
    if benchmark_symbol is None:
        raise ValueError(f"Unsupported market: {market}")

    conn = get_connection()
    tracker: StageTracker | None = None
    try:
        cursor = conn.cursor()
        row = cursor.execute("SELECT Status FROM dbo.JobRuns WHERE Id = ?", run_id).fetchone()
        if row and row.Status in ("completed", "cancelled"):
            logger.info("Regime run %s already %s — skipping (idempotent)", run_id, row.Status)
            return

        update_job_status(conn, run_id, "running", progress=0,
                          started_at=datetime.now(timezone.utc).replace(tzinfo=None))
        tracker = StageTracker(
            conn, run_id,
            [("benchmark", "Benchmark bars", 30), ("breadth", "Breadth", 50), ("persist", "Persist snapshot", 20)],
            write_progress=True,
        )
        tracker.publish()

        # 1. Refresh benchmark + volatility index bars (best-effort per symbol). During market
        #    hours, overlay each index's live last price onto today's provisional bar so the
        #    condition/context reflect the live index level (breadth stays on stored closes).
        tracker.start("benchmark")
        market_open = is_market_open(market)
        local_now = market_local_now(market)
        trading_date = local_now.date() if local_now is not None else None
        live_applied = False

        bench_frame = _fetch_index_frame(benchmark_symbol)
        if market_open and trading_date is not None:
            live = _fetch_live_index_price(benchmark_symbol)
            if live is not None:
                bench_frame = _apply_live_price(bench_frame, trading_date, live)
                live_applied = True
        if bench_frame is not None:
            _upsert_bench_bars(conn, _table(_BENCH_BARS, market), benchmark_symbol, bench_frame)

        vol_frame = None
        if volatility_symbol:
            vol_frame = _fetch_index_frame(volatility_symbol)
            if market_open and trading_date is not None:
                vlive = _fetch_live_index_price(volatility_symbol)
                if vlive is not None:
                    vol_frame = _apply_live_price(vol_frame, trading_date, vlive)
            if vol_frame is not None:
                _upsert_bench_bars(conn, _table(_BENCH_BARS, market), volatility_symbol, vol_frame)

        detail = f"benchmark {'ok' if bench_frame is not None else 'unavailable'}, " \
                 f"volatility {'ok' if vol_frame is not None else 'unavailable'}" \
                 f"{' (intraday live)' if live_applied else ''}"
        tracker.complete("benchmark", detail=detail)

        # 2. Compute the full regime: participation + benchmark condition + breadth + combine.
        tracker.start("breadth")
        closes_by_ticker, breadth_as_of = _load_universe_closes(conn, market)
        participation = compute_participation(closes_by_ticker)

        # Benchmark condition + returns context from the persisted benchmark bars.
        bench_db = _load_benchmark_frame(conn, market, benchmark_symbol)
        condition_as_of: date | None = None
        cond_closes: list[float] = []
        cond_vols: list[int | None] = []
        if bench_db is not None and not bench_db.empty:
            condition_as_of = bench_db.index[-1].date()
            cond_closes = [float(x) for x in bench_db["Close"].tolist()]
            cond_vols = [int(v) if pd.notna(v) else None for v in bench_db["Volume"].tolist()]
        condition = compute_condition(benchmark_symbol, condition_as_of, cond_closes, cond_vols)
        context = compute_benchmark_context(bench_db)

        # Volatility close from persisted volatility bars.
        vol_close = None
        if volatility_symbol:
            vol_db = _load_benchmark_frame(conn, market, volatility_symbol)
            if vol_db is not None:
                vseries = vol_db["Close"].dropna()
                vol_close = round(float(vseries.iloc[-1]), 4) if not vseries.empty else None

        facts = {**participation, **context, "volatility_close": vol_close}
        breadth = compute_breadth(facts)
        regime = combine_regime(condition["condition_label"], breadth["breadth_label"])

        # As-of is the freshest available component date; intraday this is the live condition
        # date (today), which may lead a still-EOD breadth date (surfaced by the freshness lag).
        candidate_dates = [d for d in (condition_as_of, breadth_as_of) if d is not None]
        as_of = max(candidate_dates) if candidate_dates else None
        if as_of is None:
            raise ValueError(f"No ingested bars (universe or benchmark) for market {market}")
        # A snapshot is intraday only when a live index price was actually overlaid this run.
        is_intraday = bool(live_applied)
        live_tag = " · intraday" if is_intraday else ""
        tracker.complete("breadth", detail=f"{participation['evaluated_count']} stocks · {regime['regime']}{live_tag}")

        # 3. Persist the fully-computed regime snapshot (idempotent per as-of date).
        tracker.start("persist")
        snapshot = {
            "benchmark_symbol": benchmark_symbol,
            "volatility_symbol": volatility_symbol,
            "volatility_close": vol_close,
            "evaluated_count": participation["evaluated_count"],
            "breadth_as_of": breadth_as_of,
            "is_intraday": is_intraday,
            "signals_json": json.dumps(breadth["signals"]),
            "available": condition["condition_available"] and breadth["breadth_available"],
            **participation,
            **context,
            **condition,
            **breadth,
            **regime,
        }
        _persist_regime(conn, market, as_of, snapshot)
        tracker.complete("persist", detail=f"{as_of}{live_tag}")

        metrics = {
            "market": market,
            "asOf": str(as_of),
            "regime": regime["regime"],
            "condition": condition["condition_label"],
            "breadth": breadth["breadth_label"],
            "breadthScore": breadth["breadth_score"],
            "evaluated": participation["evaluated_count"],
            "benchmarkSymbol": benchmark_symbol,
            "benchmarkAvailable": bench_db is not None,
            "volatilityAvailable": vol_close is not None,
        }
        tracker.finish(status="completed", metrics=metrics,
                       completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        logger.info("Regime run %s completed for %s (as_of=%s, regime=%s, evaluated=%s)",
                    run_id, market, as_of, regime["regime"], participation["evaluated_count"])
    except Exception as exc:
        logger.exception("Regime run %s failed", run_id)
        try:
            failure_stages = None
            if tracker is not None:
                tracker.fail_running(detail=str(exc)[:200])
                failure_stages = tracker.snapshot()
            update_job_status(conn, run_id, "failed", error=str(exc), stages=failure_stages,
                              completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        except Exception:
            pass
        raise
    finally:
        conn.close()
