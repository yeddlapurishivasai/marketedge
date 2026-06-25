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
import logging
import math
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

import db
import fetch
from config import Config
from observability import configure_logging

logger = logging.getLogger("ingestion")

# How many tickers' worth of bars to MERGE per upsert round-trip.
UPSERT_TICKER_BATCH = 50


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

        symbols = [u["symbol"] for u in universe]
        benchmark = fetch.benchmark_symbol(args.market)
        all_symbols = [benchmark, *symbols]

        logger.info("Fetching daily bars for %s symbols (incl. benchmark)...", len(all_symbols))
        frames = fetch.fetch_daily_bars(all_symbols, args.market)

        cutoff = date.today() - timedelta(days=Config.DAILY_LOOKBACK_DAYS)
        total_rows = 0
        pending: list[tuple] = []
        ingested_tickers = 0
        for symbol in all_symbols:
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
        symbols = [u["symbol"] for u in universe]
        if not symbols:
            logger.warning("No tickers resolved; nothing to ingest.")
            return 0

        logger.info("Fetching market caps for %s symbols...", len(symbols))
        market_caps = fetch.fetch_market_caps(symbols, args.market)

        written = 0
        for symbol in symbols:
            bars = db.get_recent_bars(conn, args.market, symbol, lookback=400)
            snapshot = _snapshot_from_bars(bars)
            if snapshot is None:
                logger.warning("No bars for %s; skipping technical snapshot.", symbol)
                continue
            snapshot["ticker"] = symbol
            snapshot["market_cap"] = market_caps.get(symbol)
            db.upsert_ticker_technical(conn, args.market, snapshot)
            written += 1

        logger.info("Technical ingestion complete: %s snapshots written.", written)
    finally:
        conn.close()
    return 0


def cmd_ingest_fundamentals(args) -> int:
    """Analyst snapshot + EPS forecast + valuation (market cap) ingestion.

    yfinance fundamentals APIs are version-dependent and flaky, so every ticker is
    guarded independently: a single failure is logged with context, counted, and
    skipped — it never aborts the run (FR-007). The run ends with a structured
    summary of per-type counts and duration (FR-008).
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
        "tickers": 0,
    }

    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        symbols = [u["symbol"] for u in universe]
        counts["tickers"] = len(symbols)
        today = date.today()

        # Valuation (market cap) in parallel, best-effort (FR-004 / US3).
        market_caps: dict[str, int | None] = {}
        try:
            market_caps = fetch.fetch_market_caps(symbols, args.market)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market-cap fetch failed wholesale; continuing: %s", exc)

        for symbol in symbols:
            yf_symbol = fetch.to_yfinance_symbol(symbol, args.market)
            try:
                ticker = yf.Ticker(yf_symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not open ticker %s: %s", symbol, exc)
                counts["analyst_failed"] += 1
                continue

            if _try_analyst_snapshot(conn, args.market, symbol, ticker, today):
                counts["analyst_ok"] += 1
            try:
                counts["eps_rows"] += _try_eps_forecasts(conn, args.market, symbol, ticker, today)
            except Exception as exc:  # noqa: BLE001
                logger.warning("EPS ingestion failed for %s: %s", symbol, exc)
                counts["eps_failed"] += 1

            mc = market_caps.get(symbol)
            if mc is None:
                counts["valuation_skipped"] += 1
            else:
                try:
                    db.upsert_market_cap(conn, args.market, symbol, today, mc)
                    counts["valuation_ok"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Market-cap upsert failed for %s: %s", symbol, exc)
                    counts["valuation_failed"] += 1

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
            "eps rows=%s failed=%s | valuation ok=%s skipped=%s failed=%s | %ss",
            counts["tickers"], counts["analyst_ok"], counts["analyst_failed"],
            counts["eps_rows"], counts["eps_failed"], counts["valuation_ok"],
            counts["valuation_skipped"], counts["valuation_failed"], duration,
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
    try:
        info = getattr(ticker, "info", {}) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("No analyst info for %s: %s", symbol, exc)
        return False
    rating = info.get("recommendationKey")
    num = info.get("numberOfAnalystOpinions")
    if rating is None and num is None:
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
                "current_year_eps": _safe_num(info.get("forwardEps")),
                "next_year_eps": None,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed analyst snapshot for %s: %s", symbol, exc)
        return False


_PERIOD_MAP = {"0q": ("Q", 0), "+1q": ("Q", 1), "0y": ("Y", 0), "+1y": ("Y", 1)}


def _try_eps_forecasts(conn, market, symbol, ticker, as_of) -> int:
    try:
        est = ticker.earnings_estimate
    except Exception as exc:  # noqa: BLE001
        logger.debug("No earnings estimate for %s: %s", symbol, exc)
        return 0
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


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _add_universe_args(parser: argparse.ArgumentParser) -> None:
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
    _add_universe_args(bars)
    bars.set_defaults(func=cmd_ingest_bars)

    technical = ingest_sub.add_parser("technical", help="Ingest daily technical snapshots.")
    _add_universe_args(technical)
    technical.set_defaults(func=cmd_ingest_technical)

    fundamentals = ingest_sub.add_parser(
        "fundamentals", help="Ingest analyst/EPS data (best-effort)."
    )
    _add_universe_args(fundamentals)
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
