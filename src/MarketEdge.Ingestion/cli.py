"""Command-line entry point for the MarketEdge data-ingestion pipeline.

Subcommands
-----------
    seed tickers          Populate the ticker master from the stock catalog.
    ingest bars           Fetch & store daily OHLCV bars (the Stage 2 base data).
    ingest technical      Compute & store the latest daily technical snapshot.
    ingest fundamentals   Best-effort analyst / EPS forecast ingestion.

Every subcommand accepts a universe selector:
    --market {india|us}   (required)
    --limit N             Cap the number of tickers (ordered by symbol).
    --test-sample         Restrict to rows flagged IsTestSample = 1.
    --sectors 1,2,3       Restrict to the given SectorId values.

The ``ingest bars`` command is the critical path: it resolves the universe, seeds
the tickers (plus the market benchmark), fetches daily bars in throttled batches and
bulk-upserts them, then refreshes each ticker's BarsAvailable count.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from queue import Queue

import pandas as pd

import db
import fetch
from config import Config
from observability import configure_logging

logger = logging.getLogger("ingestion")


# --- yfinance throttle / crumb resilience ------------------------------------------------
# Yahoo aggressively rate-limits ("Too Many Requests") and rotates the auth crumb
# ("Invalid Crumb") on the per-ticker fundamentals endpoints. These helpers retry such
# transient failures with exponential backoff + jitter and refresh the crumb on auth errors
# so a flaky fetch recovers instead of silently dropping the ticker's data.
_TRANSIENT_YF_MARKERS = (
    "too many requests",
    "rate limited",
    "invalid crumb",
    "unable to access this feature",
    "429",
    "curl",
    "timed out",
    "timeout",
    "connection",
)


def _is_transient_yf_error(exc: Exception) -> bool:
    """True when a yfinance error looks like a Yahoo throttle / crumb hiccup worth retrying."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_YF_MARKERS)


def _reset_yf_crumb() -> None:
    """Best-effort clear of yfinance's cached crumb/cookie so the next call re-handshakes."""
    try:
        from yfinance import data as _yfdata  # noqa: PLC0415 - optional internal
        singleton = _yfdata.YfData()
        for attr in ("_crumb", "_cookie"):
            try:
                setattr(singleton, attr, None)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def _yf_fetch(fn, *, symbol: str, what: str, default=None, log=None):
    """Call a flaky yfinance accessor with exponential-backoff retry on Yahoo throttling.

    Retries up to ``Config.YFINANCE_MAX_RETRIES`` times when the error looks like a rate
    limit / invalid-crumb hiccup, sleeping ``YFINANCE_RETRY_BASE_DELAY * 2**attempt`` seconds
    (plus jitter) between attempts and refreshing the crumb on auth failures. Returns
    ``default`` if every attempt fails (or on a non-transient error), logging once via ``log``
    (defaults to ``logger.debug``).
    """
    log = log or logger.debug
    attempts = max(1, Config.YFINANCE_MAX_RETRIES)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient_yf_error(exc) or attempt == attempts - 1:
                break
            low = str(exc).lower()
            if "crumb" in low or "unable to access" in low:
                _reset_yf_crumb()
            delay = Config.YFINANCE_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.75)
            logger.debug("Retrying %s for %s in %.1fs (attempt %d/%d): %s",
                         what, symbol, delay, attempt + 1, attempts, exc)
            time.sleep(delay)
    log("No %s for %s: %s", what, symbol, last_exc)
    return default

# How many tickers' worth of bars to MERGE per upsert round-trip.
UPSERT_TICKER_BATCH = 50

# Minimum seconds between live progress writes (throttles JobRuns updates).
_PROGRESS_MIN_INTERVAL = 2.0


class _ProgressReporter:
    """Reports in-band per-item progress to a JobRun row, throttled to avoid write spam.

    A long step is given a ``[start, end]`` slice of the overall 0-100 bar by the runner;
    as the step's per-symbol loop advances, this maps ``done/total`` onto that slice and
    writes ``JobRuns.Progress`` at most every ``_PROGRESS_MIN_INTERVAL`` seconds. A no-op
    when no run-id was passed (e.g. inline/manual CLI runs). Never raises into the loop.
    """

    def __init__(self, conn, run_id: int | None, start: int, end: int, total: int,
                 stage_key: str | None = None) -> None:
        self._conn = conn
        self._run_id = run_id
        self._start = max(0, min(100, int(start)))
        self._end = max(self._start, min(100, int(end)))
        self._total = max(1, int(total))
        self._last_pct = self._start
        self._last_write = 0.0
        self._stage_key = stage_key

    def update(self, done: int) -> None:
        if self._run_id is None:
            return
        frac = min(1.0, max(0.0, done / self._total))
        pct = self._start + int(round(frac * (self._end - self._start)))
        if pct <= self._last_pct:
            return
        now = time.monotonic()
        if pct < self._end and (now - self._last_write) < _PROGRESS_MIN_INTERVAL:
            return
        try:
            db.update_job_progress(self._conn, self._run_id, pct)
            if self._stage_key:
                stage_pct = int(round(frac * 100))
                db.update_job_stage_progress(self._conn, self._run_id, self._stage_key, stage_pct)
            self._last_pct = pct
            self._last_write = now
        except Exception:  # noqa: BLE001 - progress is best-effort, never abort the run
            logger.debug("Progress update failed for run %s", self._run_id, exc_info=True)


def _progress_reporter(args, conn, total: int) -> _ProgressReporter:
    """Build a reporter from the (optional) ``--run-id/--progress-start/--progress-end`` args."""
    run_id = getattr(args, "run_id", None)
    start = getattr(args, "progress_start", None)
    end = getattr(args, "progress_end", None)
    stage_key = getattr(args, "stage_key", None)
    if run_id is None or start is None or end is None:
        return _ProgressReporter(conn, None, 0, 0, max(1, total))
    return _ProgressReporter(conn, int(run_id), int(start), int(end), total, stage_key=stage_key)


# --------------------------------------------------------------------------- #
# Universe helpers
# --------------------------------------------------------------------------- #
def _parse_sectors(value: str | None) -> list[int] | None:
    if not value:
        return None
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out or None


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    out = [p.strip().upper() for p in value.split(",") if p.strip()]
    return out or None


def _resolve_universe(conn, args) -> list[dict]:
    sectors = _parse_sectors(getattr(args, "sectors", None))
    symbols = _parse_symbols(getattr(args, "symbols", None))
    universe = db.get_universe(
        conn,
        args.market,
        limit=args.limit,
        test_sample_only=args.test_sample,
        sector_ids=sectors,
        symbols=symbols,
    )
    logger.info("Resolved %s tickers for market=%s", len(universe), args.market)
    return universe


def _apply_missing_filter(conn, args, universe: list[dict], kind: str) -> list[dict]:
    """Drop tickers that already have ``kind`` output when --missing is set."""
    if getattr(args, "force", False) or not getattr(args, "missing", False):
        return universe
    present = db.get_present_tickers(conn, args.market, kind)
    filtered = [u for u in universe if u["symbol"].upper() not in present]
    logger.info(
        "Missing-only (%s): %s of %s tickers lack data and will be processed.",
        kind, len(filtered), len(universe),
    )
    return filtered


def _apply_earnings_window_filter(conn, args, universe: list[dict]) -> list[dict]:
    """Restrict the run to tickers 'due' for refresh based on NextEarningsDate.

    Enabled by ``--earnings-window-days N`` (the optimized daily run). Bypassed entirely by
    ``--force`` or when an explicit ``--symbols`` list is given. A ticker is kept when it has
    no NextEarningsDate yet or is reporting within +/- N days, so the daily job touches only
    names near an earnings event instead of the whole universe.
    """
    window = getattr(args, "earnings_window_days", None)
    if window is None:
        return universe
    if getattr(args, "force", False):
        logger.info("Force mode: ignoring --earnings-window-days; processing all %s tickers.",
                    len(universe))
        return universe
    if _parse_symbols(getattr(args, "symbols", None)):
        return universe
    due = db.get_due_earnings_tickers(conn, args.market, date.today(), int(window))
    filtered = [u for u in universe if u["symbol"].upper() in due]
    logger.info(
        "Earnings window (+/-%sd): %s of %s tickers are due and will be processed.",
        window, len(filtered), len(universe),
    )
    return filtered


def _seed(conn, market: str, universe: list[dict]) -> None:
    """Seed the universe plus the market benchmark into the ticker master."""
    rows = list(universe)
    rows.append({"symbol": fetch.benchmark_symbol(market), "exchange": "INDEX", "is_fno": False})
    db.seed_tickers(conn, market, rows)


# --------------------------------------------------------------------------- #
# Frame -> row conversion
# --------------------------------------------------------------------------- #
def _to_bar_rows(ticker: str, frame: pd.DataFrame, cutoff: date | None = None) -> list[tuple]:
    rows: list[tuple] = []
    for index, series in frame.iterrows():
        bar_date = index.date() if isinstance(index, (pd.Timestamp, datetime)) else index
        if not isinstance(bar_date, date):
            continue
        if cutoff is not None and bar_date < cutoff:
            continue
        rows.append(
            (
                ticker,
                bar_date,
                db._clean(series.get("Open")),
                db._clean(series.get("High")),
                db._clean(series.get("Low")),
                db._clean(series.get("Close")),
                _clean_int(series.get("Volume")),
                db._clean(series.get("Adj Close")),
            )
        )
    return rows


def _clean_int(value) -> int | None:
    cleaned = db._clean(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_seed_tickers(args) -> int:
    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        _seed(conn, args.market, universe)
    finally:
        conn.close()
    logger.info("Seed complete: %s tickers (+benchmark) for %s", len(universe), args.market)
    return 0


def cmd_ingest_bars(args) -> int:
    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        if not universe:
            logger.warning("No tickers resolved; nothing to ingest.")
            return 0

        _seed(conn, args.market, universe)

        universe = _apply_missing_filter(conn, args, universe, "bars")
        if not universe:
            logger.info("Missing-only bars: nothing missing; skipping fetch.")
            return 0

        symbols = [u["symbol"] for u in universe]
        benchmark = fetch.benchmark_symbol(args.market)
        all_symbols = [benchmark, *symbols]

        logger.info("Fetching daily bars for %s symbols (incl. benchmark)...", len(all_symbols))
        frames = fetch.fetch_daily_bars(all_symbols, args.market)

        cutoff = date.today() - timedelta(days=Config.DAILY_LOOKBACK_DAYS)
        total_rows = 0
        pending: list[tuple] = []
        ingested_tickers = 0
        reporter = _progress_reporter(args, conn, len(all_symbols))
        for processed, symbol in enumerate(all_symbols):
            reporter.update(processed)
            frame = frames.get(symbol)
            if frame is None or frame.empty:
                continue
            pending.extend(_to_bar_rows(symbol, frame, cutoff))
            ingested_tickers += 1
            if ingested_tickers % UPSERT_TICKER_BATCH == 0 and pending:
                total_rows += db.upsert_bars(conn, args.market, pending)
                pending = []

        if pending:
            total_rows += db.upsert_bars(conn, args.market, pending)

        reporter.update(len(all_symbols))

        pruned = db.prune_old_bars(conn, args.market, cutoff)
        db.refresh_bars_available(conn, args.market)

        logger.info(
            "Bars ingestion complete: %s tickers with data, %s bar rows upserted "
            "(of %s requested), %s stale rows pruned, window >= %s.",
            ingested_tickers, total_rows, len(all_symbols), pruned, cutoff,
        )
    finally:
        conn.close()
    return 0


def _snapshot_from_bars(bars: list[dict]) -> dict | None:
    """Compute close/day_pct/open/high/low/high_52w/from_52w_high from recent bars."""
    if not bars:
        return None
    last = bars[-1]
    close = last.get("close")
    if close is None:
        return None
    prev_close = bars[-2].get("close") if len(bars) > 1 else None
    day_pct = None
    if prev_close:
        try:
            day_pct = (close - prev_close) / prev_close * 100.0
        except ZeroDivisionError:
            day_pct = None

    highs = [b["high"] for b in bars if b.get("high") is not None]
    high_52w = max(highs) if highs else None
    from_52w_high = None
    if high_52w:
        from_52w_high = (close - high_52w) / high_52w * 100.0

    return {
        "close": close,
        "day_pct": day_pct,
        "open": last.get("open"),
        "high": last.get("high"),
        "low": last.get("low"),
        "high_52w": high_52w,
        "from_52w_high": from_52w_high,
        "as_of_date": last.get("bar_date"),
    }


def cmd_ingest_technical(args) -> int:
    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        universe = _apply_missing_filter(conn, args, universe, "technical")
        symbols = [u["symbol"] for u in universe]
        if not symbols:
            logger.warning("No tickers resolved; nothing to ingest.")
            return 0

        logger.info("Fetching market caps for %s symbols...", len(symbols))
        market_caps = fetch.fetch_market_caps(symbols, args.market)

        reporter = _progress_reporter(args, conn, len(symbols))
        written = 0
        for idx, symbol in enumerate(symbols):
            reporter.update(idx)
            bars = db.get_recent_bars(conn, args.market, symbol, lookback=400)
            snapshot = _snapshot_from_bars(bars)
            if snapshot is None:
                logger.warning("No bars for %s; skipping technical snapshot.", symbol)
                continue
            snapshot["ticker"] = symbol
            snapshot["market_cap"] = market_caps.get(symbol)
            db.upsert_ticker_technical(conn, args.market, snapshot)
            written += 1

        reporter.update(len(symbols))
        logger.info("Technical ingestion complete: %s snapshots written.", written)
    finally:
        conn.close()
    return 0


def _process_symbol_fundamentals(conn, market, symbol, yf, today, market_caps) -> dict:
    """Run all per-symbol fundamentals work (analyst / EPS / earnings / valuation / signals).

    Self-contained so it can run on a worker thread with its own DB ``conn``. Returns the
    per-type count deltas to aggregate on the caller; every yfinance call is guarded so one
    flaky ticker never aborts the run.
    """
    c = {
        "analyst_ok": 0, "analyst_failed": 0,
        "eps_rows": 0, "eps_failed": 0,
        "valuation_ok": 0, "valuation_skipped": 0, "valuation_failed": 0,
        "earnings_ok": 0, "earnings_skipped": 0,
        "signals_ok": 0, "signals_skipped": 0,
    }
    yf_symbol = fetch.to_yfinance_symbol(symbol, market)
    try:
        ticker = yf.Ticker(yf_symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not open ticker %s: %s", symbol, exc)
        c["analyst_failed"] += 1
        return c

    if _try_analyst_snapshot(conn, market, symbol, ticker, today):
        c["analyst_ok"] += 1
    try:
        c["eps_rows"] += _try_eps_forecasts(conn, market, symbol, ticker, today)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EPS ingestion failed for %s: %s", symbol, exc)
        c["eps_failed"] += 1

    try:
        if _try_earnings_fundamentals(conn, market, symbol, ticker, today):
            c["earnings_ok"] += 1
        else:
            c["earnings_skipped"] += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earnings fundamentals failed for %s: %s", symbol, exc)
        c["earnings_skipped"] += 1

    mc = market_caps.get(symbol)
    if mc is None:
        c["valuation_skipped"] += 1
    else:
        try:
            db.upsert_market_cap(conn, market, symbol, today, mc)
            c["valuation_ok"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market-cap upsert failed for %s: %s", symbol, exc)
            c["valuation_failed"] += 1

    try:
        if _try_stock_signals(conn, market, symbol, ticker, today):
            c["signals_ok"] += 1
        else:
            c["signals_skipped"] += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Catalyst signals failed for %s: %s", symbol, exc)
        c["signals_skipped"] += 1

    # Recompute the screener idea from the freshly-written earnings + analyst snapshots.
    try:
        db.refresh_fundamental_idea(conn, market, symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fundamental idea refresh failed for %s: %s", symbol, exc)

    if Config.YFINANCE_TICKER_DELAY > 0:
        time.sleep(Config.YFINANCE_TICKER_DELAY)
    return c


def cmd_ingest_fundamentals(args) -> int:
    """Analyst snapshot + EPS forecast + valuation (market cap) ingestion.

    yfinance fundamentals APIs are version-dependent and flaky, so every ticker is
    guarded independently: a single failure is logged with context, counted, and
    skipped — it never aborts the run (FR-007). The per-symbol work is fanned out across
    ``Config.FUNDAMENTALS_THREADS`` worker threads (each with its own DB connection) since
    it is dominated by sequential per-ticker yfinance network calls. The run ends with a
    structured summary of per-type counts and duration (FR-008).
    """
    try:
        import yfinance as yf  # noqa: PLC0415 - imported lazily; optional path
    except Exception as exc:  # noqa: BLE001
        logger.error("yfinance unavailable; cannot ingest fundamentals: %s", exc)
        return 1

    started = time.monotonic()
    counts = {
        "analyst_ok": 0, "analyst_failed": 0,
        "eps_rows": 0, "eps_failed": 0,
        "valuation_ok": 0, "valuation_skipped": 0, "valuation_failed": 0,
        "earnings_ok": 0, "earnings_skipped": 0,
        "signals_ok": 0, "signals_skipped": 0,
        "tickers": 0,
    }

    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        universe = _apply_missing_filter(conn, args, universe, "fundamentals")
        universe = _apply_earnings_window_filter(conn, args, universe)
        symbols = [u["symbol"] for u in universe]
        counts["tickers"] = len(symbols)
        today = date.today()
        reporter = _progress_reporter(args, conn, len(symbols))

        # Valuation (market cap) in parallel, best-effort (FR-004 / US3).
        market_caps: dict[str, int | None] = {}
        try:
            market_caps = fetch.fetch_market_caps(symbols, args.market)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market-cap fetch failed wholesale; continuing: %s", exc)

        workers = max(1, min(int(Config.FUNDAMENTALS_THREADS), len(symbols) or 1))
        logger.info("Processing fundamentals for %s symbols with %s worker(s)...",
                    len(symbols), workers)

        def _aggregate(part: dict) -> None:
            for k, v in part.items():
                counts[k] += v

        if workers <= 1:
            for idx, symbol in enumerate(symbols):
                reporter.update(idx)
                _aggregate(_process_symbol_fundamentals(
                    conn, args.market, symbol, yf, today, market_caps))
        else:
            # One DB connection per worker (pyodbc connections are not thread-safe to share).
            pool: Queue = Queue()
            worker_conns = [db.get_connection() for _ in range(workers)]
            for wc in worker_conns:
                pool.put(wc)

            def _task(sym: str) -> dict:
                wc = pool.get()
                try:
                    return _process_symbol_fundamentals(
                        wc, args.market, sym, yf, today, market_caps)
                finally:
                    pool.put(wc)

            done = 0
            try:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = [ex.submit(_task, s) for s in symbols]
                    for fut in as_completed(futures):
                        try:
                            _aggregate(fut.result())
                        except Exception as exc:  # noqa: BLE001 - never abort the whole run
                            logger.warning("Fundamentals worker failed: %s", exc)
                        done += 1
                        reporter.update(done)
            finally:
                for wc in worker_conns:
                    try:
                        wc.close()
                    except Exception:  # noqa: BLE001
                        pass

        reporter.update(len(symbols))
        duration = round(time.monotonic() - started, 1)
        logger.info(
            "Fundamentals run complete",
            extra={
                "market": args.market,
                "duration_s": duration,
                **counts,
            },
        )
        logger.info(
            "Fundamentals summary: %s tickers | analyst ok=%s failed=%s | "
            "eps rows=%s failed=%s | valuation ok=%s skipped=%s failed=%s | "
            "earnings ok=%s skipped=%s | signals ok=%s skipped=%s | %ss",
            counts["tickers"], counts["analyst_ok"], counts["analyst_failed"],
            counts["eps_rows"], counts["eps_failed"], counts["valuation_ok"],
            counts["valuation_skipped"], counts["valuation_failed"],
            counts["earnings_ok"], counts["earnings_skipped"],
            counts["signals_ok"], counts["signals_skipped"], duration,
        )
    finally:
        conn.close()
    return 0


def _safe_num(value):
    try:
        if value is None:
            return None
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _try_analyst_snapshot(conn, market, symbol, ticker, as_of) -> bool:
    info = _yf_fetch(lambda: ticker.info, symbol=symbol, what="analyst info",
                     default=None, log=logger.warning)
    if not info:
        return False
    rating = info.get("recommendationKey")
    num = info.get("numberOfAnalystOpinions")
    # Trailing (TTM, actual) and forward (projected) annual EPS. The EPS-upside score is
    # the implied price move if the current P/E is held constant: price scales with EPS,
    # so upside% = (forwardEps / trailingEps - 1) * 100. We store trailing as the base
    # (CurrentYearEps) and forward as the projection (NextYearEps); the scoring engine's
    # _upside_eps computes (NextYearEps / CurrentYearEps - 1) * 100 from these.
    trailing_eps = _safe_num(info.get("trailingEps"))
    forward_eps = _safe_num(info.get("forwardEps"))
    # Analyst 12-month price targets (low / mean / high) → bear / base / bull price scenarios.
    target_low = _safe_num(info.get("targetLowPrice"))
    target_mean = _safe_num(info.get("targetMeanPrice"))
    target_high = _safe_num(info.get("targetHighPrice"))

    # Recommendation distribution trend (Strong Buy / Buy / Hold / Underperform / Sell), most
    # recent period first. From yfinance ``recommendations`` (rows: 0m/-1m/-2m/-3m).
    recommendations = _fetch_recommendations(symbol, ticker)
    recommendations_json = json.dumps(recommendations) if recommendations else None

    # Latest analyst rating action with research-firm terminology (Overweight / Outperform /
    # Neutral / etc.) from yfinance ``upgrades_downgrades``.
    rating_firm = rating_grade = rating_action = rating_date = None
    latest = _fetch_latest_rating(symbol, ticker)
    if latest is not None:
        rating_firm, rating_grade, rating_action, rating_date = latest

    if (rating is None and num is None and trailing_eps is None and forward_eps is None
            and target_low is None and target_mean is None and target_high is None
            and recommendations_json is None and rating_grade is None):
        return False
    try:
        db.upsert_analyst_snapshot(
            conn, market,
            {
                "ticker": symbol,
                "as_of_date": as_of,
                "consensus_rating": rating,
                "num_analysts": _safe_num(num),
                "current_quarter_eps": None,
                "next_quarter_eps": None,
                "current_year_eps": trailing_eps,
                "next_year_eps": forward_eps,
                "target_low_price": target_low,
                "target_mean_price": target_mean,
                "target_high_price": target_high,
                "recommendations_json": recommendations_json,
                "latest_rating_firm": rating_firm,
                "latest_rating_grade": rating_grade,
                "latest_rating_action": rating_action,
                "latest_rating_date": rating_date,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed analyst snapshot for %s: %s", symbol, exc)
        return False


def _fetch_recommendations(symbol, ticker) -> list[dict]:
    """Monthly recommendation distribution from yfinance, most recent period first.

    Returns a list of ``{period, strongBuy, buy, hold, sell, strongSell}`` dicts. Empty on
    failure or when the (flaky) endpoint returns nothing.
    """
    try:
        rec = _yf_fetch(lambda: ticker.recommendations, symbol=symbol, what="recommendations")
    except Exception as exc:  # noqa: BLE001
        logger.debug("No recommendations for %s: %s", symbol, exc)
        return []
    if rec is None or not hasattr(rec, "iterrows") or rec.empty:
        return []

    def _int(value):
        n = _safe_num(value)
        return int(n) if n is not None else 0

    out = []
    for _, rrow in rec.iterrows():
        period = rrow.get("period")
        out.append({
            "period": str(period) if period is not None else "",
            "strongBuy": _int(rrow.get("strongBuy")),
            "buy": _int(rrow.get("buy")),
            "hold": _int(rrow.get("hold")),
            "sell": _int(rrow.get("sell")),
            "strongSell": _int(rrow.get("strongSell")),
        })
    return out


def _fetch_latest_rating(symbol, ticker):
    """Most recent analyst rating action: (firm, grade, action, date) or None.

    Sourced from yfinance ``upgrades_downgrades`` (indexed by GradeDate; columns Firm,
    ToGrade, FromGrade, Action). Grade carries the firm-specific terminology such as
    "Overweight" / "Outperform" / "Neutral".
    """
    ud = _yf_fetch(lambda: ticker.upgrades_downgrades, symbol=symbol,
                   what="upgrades/downgrades")
    if ud is None or not hasattr(ud, "iterrows") or ud.empty:
        return None
    try:
        ud = ud.sort_index(ascending=False)
    except Exception:  # noqa: BLE001
        pass
    for idx, urow in ud.iterrows():
        grade = urow.get("ToGrade")
        if grade is None or (isinstance(grade, float) and math.isnan(grade)) or str(grade).strip() == "":
            continue
        firm = urow.get("Firm")
        action = urow.get("Action")
        try:
            gdate = idx.date()
        except Exception:  # noqa: BLE001
            gdate = None
        return (
            str(firm).strip() if firm is not None else None,
            str(grade).strip(),
            str(action).strip() if action is not None else None,
            gdate,
        )
    return None


_PERIOD_MAP = {"0q": ("Q", 0), "+1q": ("Q", 1), "0y": ("Y", 0), "+1y": ("Y", 1)}


def _try_eps_forecasts(conn, market, symbol, ticker, as_of) -> int:
    est = _yf_fetch(lambda: ticker.earnings_estimate, symbol=symbol, what="earnings estimate")
    if est is None or not hasattr(est, "iterrows") or est.empty:
        return 0

    written = 0
    for period_key, row in est.iterrows():
        mapping = _PERIOD_MAP.get(str(period_key))
        if mapping is None:
            continue
        period_type, _ = mapping
        try:
            db.upsert_eps_forecast(
                conn, market,
                {
                    "ticker": symbol,
                    "as_of_date": as_of,
                    "period_type": period_type,
                    "period_end_date": as_of,
                    "consensus_eps": _safe_num(row.get("avg")),
                    "high_eps": _safe_num(row.get("high")),
                    "low_eps": _safe_num(row.get("low")),
                    "num_estimates": _safe_num(row.get("numberOfAnalysts")),
                    "revisions_up": 0,
                    "revisions_down": 0,
                },
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed EPS forecast for %s (%s): %s", symbol, period_key, exc)
    return written


def _row_value(stmt, labels, col_idx):
    """Return a numeric value from a quarterly statement row, trying label aliases."""
    if stmt is None or not hasattr(stmt, "index"):
        return None
    cols = list(stmt.columns)
    if col_idx >= len(cols):
        return None
    for label in labels:
        if label in stmt.index:
            try:
                return _safe_num(stmt.loc[label].iloc[col_idx])
            except Exception:  # noqa: BLE001
                continue
    return None


def _earnings_history_quarters(ticker, symbol, as_of):
    """Per-quarter EPS estimate/actual/surprise from yfinance ``earnings_history``.

    Uses the quoteSummary ``earningsHistory`` JSON module — the same endpoint family as the
    analyst snapshot, and far more reliable than the lxml-scraped ``get_earnings_dates`` (which
    is separately rate-limited and often returns NaN reported EPS). Returns up to 4
    ``(quarter_end_date, estimate, actual, surprise_pct)`` tuples, most recent first, for
    reported quarters only. ``surprise_pct`` is normalised to percent units (Yahoo reports it
    as a fraction, e.g. 0.062 -> 6.2) to match the get_earnings_dates convention.
    """
    eh = _yf_fetch(lambda: ticker.get_earnings_history(), symbol=symbol,
                   what="earnings history")
    if eh is None or not hasattr(eh, "iterrows") or eh.empty:
        return []
    out = []
    for idx, erow in eh.iterrows():
        try:
            qd = idx.date() if hasattr(idx, "date") else None
        except Exception:  # noqa: BLE001
            qd = None
        if qd is None or qd > as_of:
            continue
        actual = _safe_num(erow.get("epsActual"))
        if actual is None:
            continue
        estimate = _safe_num(erow.get("epsEstimate"))
        surprise = _safe_num(erow.get("surprisePercent"))
        if surprise is not None:
            surprise *= 100.0  # fraction -> percent
        elif estimate not in (None, 0):
            surprise = (actual - estimate) / abs(estimate) * 100.0
        out.append((qd, estimate, actual, surprise))
    out.sort(key=lambda r: r[0], reverse=True)
    return out[:4]


def _income_stmt_eps_quarters(stmt):
    """Per-quarter reported EPS from the quarterly income statement (reliable fallback).

    Returns up to 4 ``(quarter_end_date, estimate=None, actual_eps, surprise=None)`` tuples,
    most recent first. Used when yfinance's flaky ``get_earnings_dates`` yields no reported
    EPS — the income statement's ``Diluted EPS`` (or ``Basic EPS``) row is sourced from a
    different, far more reliable endpoint so the last-4-quarters history still populates.
    Estimate/surprise are unavailable from this source and left as None.
    """
    if stmt is None or not hasattr(stmt, "index") or not hasattr(stmt, "columns"):
        return []
    eps_labels = ["Diluted EPS", "Basic EPS"]
    label = next((l for l in eps_labels if l in stmt.index), None)
    if label is None:
        return []
    out = []
    for i, col in enumerate(stmt.columns):
        try:
            qd = col.date()
        except Exception:  # noqa: BLE001
            continue
        eps = _row_value(stmt, [label], i)
        if eps is None:
            continue
        out.append((qd, None, eps, None))
    out.sort(key=lambda r: r[0], reverse=True)
    return out[:4]


def _pct_change(cur, base):
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base) * 100.0


def _margin(numerator, revenue):
    if numerator is None or revenue is None or revenue == 0:
        return None
    return numerator / revenue * 100.0


def _trend(cur, prev):
    if cur is None or prev is None:
        return None
    if cur > prev:
        return "expanding"
    if cur < prev:
        return "decreasing"
    return "flat"


def _epoch_to_date(ts):
    """Convert a Unix epoch (seconds) to a UTC date; None on bad/empty input."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _calendar_next_earnings(ticker, as_of):
    """Earliest upcoming earnings date from ticker.calendar, else None."""
    cal = _yf_fetch(lambda: ticker.calendar, symbol="(calendar)", what="calendar")
    if cal is None:
        return None
    raw = None
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
    elif cal is not None and hasattr(cal, "loc"):
        try:
            raw = cal.loc["Earnings Date"]
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        return None
    values = list(raw) if isinstance(raw, (list, tuple)) else list(getattr(raw, "values", [raw]))
    candidates = []
    for v in values:
        d = None
        try:
            d = v.date() if hasattr(v, "date") else (v if isinstance(v, date) else None)
        except Exception:  # noqa: BLE001
            d = None
        if isinstance(d, date) and d >= as_of:
            candidates.append(d)
    return min(candidates) if candidates else None


def _try_earnings_fundamentals(conn, market, symbol, ticker, as_of) -> bool:
    """Compute reported quarterly earnings fundamentals from yfinance and upsert.

    Reads ``quarterly_income_stmt`` (Total Revenue / Operating Income / Net Income) for
    current / previous-quarter / year-ago-quarter columns, plus ``get_earnings_dates`` for
    the last two reported announcement dates. Best-effort; returns True if a row was written.
    """
    try:
        stmt = ticker.quarterly_income_stmt
    except Exception as exc:  # noqa: BLE001
        logger.debug("No quarterly income stmt for %s: %s", symbol, exc)
        stmt = None

    revenue = op_profit = net_profit = None
    revenue_pq = op_profit_pq = net_profit_pq = None
    revenue_yo = op_profit_yo = net_profit_yo = None
    latest_q_end = None

    if stmt is not None and hasattr(stmt, "columns") and len(stmt.columns) > 0:
        rev_labels = ["Total Revenue", "Operating Revenue"]
        op_labels = ["Operating Income", "Operating Income Or Loss"]
        ni_labels = ["Net Income", "Net Income Common Stockholders",
                     "Net Income Continuous Operations"]
        revenue = _row_value(stmt, rev_labels, 0)
        op_profit = _row_value(stmt, op_labels, 0)
        net_profit = _row_value(stmt, ni_labels, 0)
        revenue_pq = _row_value(stmt, rev_labels, 1)
        op_profit_pq = _row_value(stmt, op_labels, 1)
        net_profit_pq = _row_value(stmt, ni_labels, 1)
        revenue_yo = _row_value(stmt, rev_labels, 4)
        op_profit_yo = _row_value(stmt, op_labels, 4)
        net_profit_yo = _row_value(stmt, ni_labels, 4)
        try:
            latest_q_end = stmt.columns[0].date()
        except Exception:  # noqa: BLE001
            latest_q_end = None

    # Earnings announcement dates + reported-EPS history (reported quarters only).
    last_date = prev_date = last_eps = last_surprise = next_date = None
    eps_quarters = []  # (date, estimate, actual, surprise_pct), most recent first
    future_dates = []  # upcoming (unreported) earnings dates
    ed = _yf_fetch(lambda: ticker.get_earnings_dates(limit=12), symbol=symbol,
                   what="earnings dates")
    if ed is not None and hasattr(ed, "iterrows") and not ed.empty:
        reported = []
        for idx, erow in ed.iterrows():
            rep = erow.get("Reported EPS")
            try:
                idate = idx.date()
            except Exception:  # noqa: BLE001
                continue
            has_rep = rep is not None and not (isinstance(rep, float) and math.isnan(rep))
            if has_rep and idate <= as_of:
                reported.append((idate, _safe_num(erow.get("EPS Estimate")),
                                 _safe_num(rep), _safe_num(erow.get("Surprise(%)"))))
            elif idate > as_of:
                future_dates.append(idate)
        reported.sort(key=lambda r: r[0], reverse=True)
        eps_quarters = reported[:4]
        if reported:
            last_date, last_eps, last_surprise = reported[0][0], reported[0][2], reported[0][3]
        if len(reported) > 1:
            prev_date = reported[1][0]
        if future_dates:
            next_date = min(future_dates)

    # Fallbacks: yfinance's get_earnings_dates is an lxml HTML scrape that is frequently
    # rate-limited (separate quota from the JSON APIs) and returns nothing or all-NaN Reported
    # EPS — leaving the EpsQ* history, LastReportedEps and the EPS-beat empty. Backfill from
    # more reliable endpoints, best first:
    #   1. earnings_history (quoteSummary JSON, same path as the analyst snapshot) — carries
    #      estimate + actual + surprise%, so the EPS-beat scoring input still populates.
    #   2. quarterly income statement "Diluted EPS" (timeseries endpoint) — actual EPS only
    #      (no estimate/surprise), as a last resort so at least the reported EPS shows.
    # These sources are keyed by quarter-END, not announcement date, so they ONLY fill the EPS
    # values/beat — never last/prev_earnings_date (the idea key). When the announcement date is
    # unavailable this run, the upsert preserves the existing LastEarningsDate (COALESCE-on-
    # null), keeping the idea keyed to its real announcement date and avoiding duplicate ideas.
    if not eps_quarters:
        hist = _earnings_history_quarters(ticker, symbol, as_of)
        if hist:
            eps_quarters = hist[:4]
            if last_eps is None:
                last_eps = hist[0][2]
            if last_surprise is None:
                last_surprise = hist[0][3]
    if not eps_quarters:
        stmt_eps = _income_stmt_eps_quarters(stmt)
        if stmt_eps:
            eps_quarters = stmt_eps[:4]
            if last_eps is None:
                last_eps = stmt_eps[0][2]

    # Flatten up to 4 most-recent reported quarters into fixed Q1..Q4 column slots.
    eps_hist = {}
    for i in range(4):
        n = i + 1
        qd, qest, qact, qsurp = eps_quarters[i] if i < len(eps_quarters) else (None, None, None, None)
        eps_hist[f"eps_q{n}_date"] = qd
        eps_hist[f"eps_q{n}_estimate"] = qest
        eps_hist[f"eps_q{n}_actual"] = qact
        eps_hist[f"eps_q{n}_surprise_pct"] = qsurp

    # Valuation multiples from ticker.info (best-effort; flaky/rate-limited endpoint).
    trailing_pe = forward_pe = None
    info = _yf_fetch(lambda: ticker.info, symbol=symbol, what="info") or None
    if isinstance(info, dict):
        trailing_pe = _safe_num(info.get("trailingPE"))
        forward_pe = _safe_num(info.get("forwardPE"))
        # yfinance's get_earnings_dates sometimes lags a full quarter behind the actual
        # most-recent report (e.g. BHEL: table jumps Jan-19 -> Jul-30, skipping the Mar-31
        # quarter reported on May 4). info.earningsTimestamp carries the true last
        # announcement; when it's in the past and ~a quarter newer than the table's latest,
        # a quarter was missed -> advance last/prev so the idea reflects the real result.
        # The missed quarter's reported EPS isn't available from this endpoint; we leave the
        # EPS fields untouched (preserve_on_null protects them against the flaky endpoint, so
        # forcing them NULL here would risk wiping good data on a transient empty fetch).
        info_last = _epoch_to_date(info.get("earningsTimestamp"))
        if info_last is not None and info_last <= as_of and (
            last_date is None or (info_last - last_date).days >= 45
        ):
            if last_date is not None:
                prev_date = last_date
            last_date = info_last
        # Fallback for next earnings date when get_earnings_dates lacks future rows,
        # so (almost) every stock gets a NextEarningsDate to drive daily-run scheduling.
        if next_date is None:
            for key in ("earningsTimestampStart", "earningsTimestamp", "earningsTimestampEnd"):
                ts = info.get(key)
                cand = _epoch_to_date(ts)
                if cand is not None and cand >= as_of:
                    next_date = cand
                    break

    # Second fallback: ticker.calendar (only when still unknown; extra network call).
    if next_date is None:
        next_date = _calendar_next_earnings(ticker, as_of)

    # Nothing usable at all -> skip.
    if revenue is None and net_profit is None and last_date is None and next_date is None:
        return False

    opm = _margin(op_profit, revenue)
    opm_pq = _margin(op_profit_pq, revenue_pq)
    opm_yo = _margin(op_profit_yo, revenue_yo)
    net_margin = _margin(net_profit, revenue)
    earnings_yoy = _pct_change(net_profit, net_profit_yo)
    earnings_qoq = _pct_change(net_profit, net_profit_pq)
    earnings_increasing = None
    if net_profit is not None and net_profit_yo is not None:
        earnings_increasing = bool(net_profit > net_profit_yo and net_profit > 0)

    row = {
        "ticker": symbol,
        "as_of_date": as_of,
        "latest_quarter_end": latest_q_end,
        "revenue": revenue, "revenue_prev_q": revenue_pq, "revenue_yoy_q": revenue_yo,
        "revenue_growth_yoy_pct": _pct_change(revenue, revenue_yo),
        "operating_profit": op_profit, "operating_profit_prev_q": op_profit_pq,
        "operating_profit_yoy_q": op_profit_yo,
        "opm": opm, "opm_prev_q": opm_pq, "opm_yoy_q": opm_yo,
        "net_profit": net_profit, "net_profit_prev_q": net_profit_pq,
        "net_profit_yoy_q": net_profit_yo, "net_margin_pct": net_margin,
        "earnings_growth_yoy_pct": earnings_yoy, "earnings_growth_qoq_pct": earnings_qoq,
        "earnings_increasing": earnings_increasing,
        "operating_profit_trend": _trend(op_profit, op_profit_pq),
        "opm_trend": _trend(opm, opm_pq),
        "last_earnings_date": last_date, "prev_earnings_date": prev_date,
        "next_earnings_date": next_date,
        "last_reported_eps": last_eps, "last_eps_surprise_pct": last_surprise,
        "trailing_pe": trailing_pe, "forward_pe": forward_pe,
        **eps_hist,
    }
    try:
        db.upsert_earnings_fundamentals(conn, market, row)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed earnings fundamentals upsert for %s: %s", symbol, exc)
        return False


def _human_num(value) -> str:
    """Compact human-readable magnitude (1.2B / 3.4M / 567) for token-friendly text."""
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


_CWIP_LABELS = [
    "Construction In Progress", "Capital Work In Progress",
    "Construction In Progress Net", "ConstructionInProgress",
]

# Lightweight keyword classifier mapping news headlines to the catalyst categories the AI
# workflow cares about. Deterministic (no AI): the tags are hints, not verdicts; the AI does
# the final interpretation. Keys are compact tag labels; values are lower-cased substrings.
_NEWS_CATEGORIES: dict[str, tuple[str, ...]] = {
    "M&A": (
        "merger", "merges", "merge with", "acquire", "acquisition", "acquires", "takeover",
        "to buy", "buys ", "buyout", "majority stake", "controlling stake", "stake in",
        "open offer", "amalgamation",
    ),
    "NEW-BIZ": (
        "launch", "launches", "unveils", "enters", "foray", "new product", "new plant",
        "new facility", "expands into", "order win", "bags order", "wins order", "wins contract",
        "bags contract", "secures order", "partnership", "tie-up", "tieup", "tie up",
        "collaborat", "joint venture", " jv ", "mou", "signs ", "signs pact", "signs deal",
        "to set up", "commissions", "capacity expansion",
    ),
    "SPINOFF": (
        "spin-off", "spinoff", "spin off", "demerger", "demerge", "hive off", "hive-off",
        "carve out", "carve-out", "split into", "separate listing",
    ),
    "POLICY": (
        "government", "govt", "policy", "regulation", "regulator", "tariff", "subsidy",
        "incentive", " pli", "budget", "ban on", "import duty", "export duty", "duty hike",
        "sanction", "gst", "rbi ", "sebi", "ministry", "scheme", "approval", "clearance",
        "mandate", "norms",
    ),
    "DEMAND/PRICING": (
        "price hike", "price increase", "raises price", "hikes price", "pricing power",
        "shortage", "supply crunch", "supply gap", "tight supply", "strong demand",
        "demand surge", "record demand", "price cut", "cuts price", "oversupply", "glut",
    ),
}


def _tag_headline(title: str) -> list[str]:
    """Return the catalyst-category tags matched by a headline (keyword heuristic)."""
    text = f" {title.lower()} "
    return [tag for tag, kws in _NEWS_CATEGORIES.items() if any(k in text for k in kws)]


def _extract_news(ticker, since, limit: int = 8) -> list[dict]:
    """Recent news as dicts, tolerant of both the legacy and newer yfinance schemas."""
    try:
        raw = ticker.news
    except Exception as exc:  # noqa: BLE001
        logger.debug("No news: %s", exc)
        return []
    if not raw:
        return []
    items: list[dict] = []
    for art in raw:
        if not isinstance(art, dict):
            continue
        content = art.get("content")
        ts = None
        if isinstance(content, dict):  # newer schema
            title = content.get("title")
            pub = (content.get("provider") or {}).get("displayName")
            pub_date = content.get("pubDate") or content.get("displayTime")
            link = ((content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url"))
            if pub_date:
                try:
                    ts = datetime.fromisoformat(str(pub_date).replace("Z", "+00:00")).date()
                except Exception:  # noqa: BLE001
                    ts = None
        else:  # legacy schema
            title = art.get("title")
            pub = art.get("publisher")
            link = art.get("link")
            epoch = art.get("providerPublishTime")
            if epoch:
                try:
                    ts = datetime.utcfromtimestamp(int(epoch)).date()
                except Exception:  # noqa: BLE001
                    ts = None
        if not title:
            continue
        if since is not None and ts is not None and ts < since:
            continue
        title = str(title).strip()
        items.append({
            "title": title, "publisher": pub,
            "date": ts.isoformat() if ts else None, "link": link,
            "tags": _tag_headline(title),
        })
        if len(items) >= limit:
            break
    return items


def _try_stock_signals(conn, market, symbol, ticker, as_of,
                       news_days: int = 7, news_limit: int = 8) -> bool:
    """Scrape lightweight catalyst signals and upsert a compact AI-ready summary.

    Sources (yfinance only, reusing the already-open ``ticker`` to avoid extra throttling):
      * CWIP / "Construction In Progress" from ``quarterly_balance_sheet`` -> major-capex trend
        (India reports this widely; many US issuers do not, so it can be absent there).
      * ``Ticker.news`` recent headlines -> raw evidence of M&A / spin-off / new business /
        policy catalysts, which the AI workflow classifies.
    Kept SEPARATE from the user-entered StockNote. Best-effort; returns True if a row written.
    """
    cwip = cwip_prev = change_pct = trend = None
    cwip_as_of = None
    try:
        bs = ticker.quarterly_balance_sheet
    except Exception as exc:  # noqa: BLE001
        logger.debug("No quarterly balance sheet for %s: %s", symbol, exc)
        bs = None
    if bs is not None and hasattr(bs, "columns") and len(bs.columns) > 0:
        cwip = _row_value(bs, _CWIP_LABELS, 0)
        cwip_prev = _row_value(bs, _CWIP_LABELS, 1)
        change_pct = _pct_change(cwip, cwip_prev)
        if cwip is not None and cwip_prev is not None:
            trend = "rising" if cwip > cwip_prev else "falling" if cwip < cwip_prev else "flat"
        if cwip is not None:
            try:
                cwip_as_of = bs.columns[0].date()
            except Exception:  # noqa: BLE001
                cwip_as_of = None

    since = as_of - timedelta(days=news_days)
    news = _extract_news(ticker, since, limit=news_limit)

    if cwip is None and not news:
        return False

    detected = sorted({tag for n in news for tag in n.get("tags", [])})

    lines = [f"AS OF {as_of.isoformat()} (scraped)"]
    if detected:
        lines.append(f"SIGNALS DETECTED: {', '.join(detected)}")
    if cwip is not None:
        cap = f"CAPEX(CWIP): {_human_num(cwip)} vs {_human_num(cwip_prev)} prev Q"
        if change_pct is not None:
            cap += f" ({change_pct:+.0f}%, {(trend or 'flat').upper()})"
        if cwip_as_of is not None:
            cap += f" [as of {cwip_as_of.isoformat()}]"
        lines.append(cap)
    else:
        lines.append("CAPEX(CWIP): n/a")
    if news:
        lines.append(f"NEWS({news_days}d):")
        for n in news:
            pub = f" ({n['publisher']})" if n["publisher"] else ""
            tags = f"  [{', '.join(n['tags'])}]" if n.get("tags") else ""
            lines.append(f"- {n['date'] or '?'} | {n['title']}{pub}{tags}")
    else:
        lines.append(f"NEWS({news_days}d): none")

    row = {
        "ticker": symbol,
        "capex_cwip": cwip, "capex_cwip_prev_q": cwip_prev,
        "capex_change_pct": change_pct, "capex_trend": trend,
        "capex_as_of": cwip_as_of,
        "news_json": json.dumps(news, ensure_ascii=False) if news else None,
        "signals_text": "\n".join(lines),
    }
    try:
        db.upsert_stock_signals(conn, market, row)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed signals upsert for %s: %s", symbol, exc)
        return False
# --------------------------------------------------------------------------- #
def _add_universe_args(parser: argparse.ArgumentParser, allow_missing: bool = False) -> None:
    parser.add_argument("--market", required=True, choices=["india", "us"])
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of tickers.")
    parser.add_argument(
        "--test-sample", action="store_true", help="Restrict to IsTestSample = 1 rows."
    )
    parser.add_argument("--sectors", default=None, help="Comma-separated SectorId list.")
    parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list to restrict the run (e.g. a single symbol refresh).",
    )
    if allow_missing:
        parser.add_argument(
            "--missing", action="store_true",
            help="Only process tickers that are missing this step's output (gap-fill).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Force a full refresh: bypass --missing and earnings-window filters.",
        )


def _add_progress_args(parser: argparse.ArgumentParser) -> None:
    """Optional hooks for the queue runner to report live in-band progress.

    The worker passes ``--run-id`` plus the ``[--progress-start, --progress-end]`` slice of
    the overall 0-100 bar allotted to this step; the step writes JobRuns.Progress as its
    per-symbol loop advances. Hidden from ``--help`` (internal wiring, not for manual use).
    """
    parser.add_argument("--run-id", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--progress-start", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--progress-end", type=int, default=None, help=argparse.SUPPRESS)
    # Which JobRun stage this step maps to; lets the step report its own per-stage progress
    # into JobRuns.Stages alongside the overall Progress bar. Hidden internal wiring.
    parser.add_argument("--stage-key", default=None, help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="MarketEdge data ingestion.")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Seed master data.")
    seed_sub = seed.add_subparsers(dest="resource", required=True)
    seed_tickers = seed_sub.add_parser("tickers", help="Seed the ticker master.")
    _add_universe_args(seed_tickers)
    seed_tickers.set_defaults(func=cmd_seed_tickers)

    ingest = sub.add_parser("ingest", help="Ingest market data.")
    ingest_sub = ingest.add_subparsers(dest="resource", required=True)

    bars = ingest_sub.add_parser("bars", help="Ingest daily OHLCV bars.")
    _add_universe_args(bars, allow_missing=True)
    _add_progress_args(bars)
    bars.set_defaults(func=cmd_ingest_bars)

    technical = ingest_sub.add_parser("technical", help="Ingest daily technical snapshots.")
    _add_universe_args(technical, allow_missing=True)
    _add_progress_args(technical)
    technical.set_defaults(func=cmd_ingest_technical)

    fundamentals = ingest_sub.add_parser(
        "fundamentals", help="Ingest analyst/EPS data (best-effort)."
    )
    _add_universe_args(fundamentals, allow_missing=True)
    fundamentals.add_argument(
        "--earnings-window-days", type=int, default=None,
        help="Optimized daily run: only process tickers whose NextEarningsDate is within "
             "+/- N days (or not yet captured). Bypassed by --force or --symbols.",
    )
    _add_progress_args(fundamentals)
    fundamentals.set_defaults(func=cmd_ingest_fundamentals)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    resource = getattr(args, "resource", None) or args.command
    market = getattr(args, "market", None)
    configure_logging(f"marketedge-ingestion-{resource}", market=market)

    logger.info(
        "Config: batch_size=%s batch_delay=%ss retries=%s threads=%s period=%s",
        Config.YFINANCE_BATCH_SIZE, Config.YFINANCE_BATCH_DELAY,
        Config.YFINANCE_MAX_RETRIES, Config.YFINANCE_THREADS, Config.DAILY_LOOKBACK_PERIOD,
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
