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


def _apply_missing_filter(conn, args, universe: list[dict], kind: str) -> list[dict]:
    """Drop tickers that already have ``kind`` output when --missing is set."""
    if not getattr(args, "missing", False):
        return universe
    present = db.get_present_tickers(conn, args.market, kind)
    filtered = [u for u in universe if u["symbol"].upper() not in present]
    logger.info(
        "Missing-only (%s): %s of %s tickers lack data and will be processed.",
        kind, len(filtered), len(universe),
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
        universe = _apply_missing_filter(conn, args, universe, "technical")
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
        "earnings_ok": 0, "earnings_skipped": 0,
        "signals_ok": 0, "signals_skipped": 0,
        "tickers": 0,
    }

    conn = db.get_connection()
    try:
        universe = _resolve_universe(conn, args)
        universe = _apply_missing_filter(conn, args, universe, "earnings")
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

            try:
                if _try_earnings_fundamentals(conn, args.market, symbol, ticker, today):
                    counts["earnings_ok"] += 1
                else:
                    counts["earnings_skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Earnings fundamentals failed for %s: %s", symbol, exc)
                counts["earnings_skipped"] += 1

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

            try:
                if _try_stock_signals(conn, args.market, symbol, ticker, today):
                    counts["signals_ok"] += 1
                else:
                    counts["signals_skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Catalyst signals failed for %s: %s", symbol, exc)
                counts["signals_skipped"] += 1

            if Config.YFINANCE_TICKER_DELAY > 0:
                time.sleep(Config.YFINANCE_TICKER_DELAY)

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
    try:
        info = getattr(ticker, "info", {}) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("No analyst info for %s: %s", symbol, exc)
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
    if (rating is None and num is None and trailing_eps is None and forward_eps is None
            and target_low is None and target_mean is None and target_high is None):
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

    # Earnings announcement dates (reported only).
    last_date = prev_date = last_eps = last_surprise = None
    try:
        ed = ticker.get_earnings_dates(limit=12)
    except Exception as exc:  # noqa: BLE001
        logger.debug("No earnings dates for %s: %s", symbol, exc)
        ed = None
    if ed is not None and hasattr(ed, "iterrows") and not ed.empty:
        reported = []
        for idx, erow in ed.iterrows():
            rep = erow.get("Reported EPS")
            try:
                idate = idx.date()
            except Exception:  # noqa: BLE001
                continue
            if rep is not None and not (isinstance(rep, float) and math.isnan(rep)) \
                    and idate <= as_of:
                reported.append((idate, _safe_num(rep), _safe_num(erow.get("Surprise(%)"))))
        reported.sort(key=lambda r: r[0], reverse=True)
        if reported:
            last_date, last_eps, last_surprise = reported[0]
        if len(reported) > 1:
            prev_date = reported[1][0]

    # Nothing usable at all -> skip.
    if revenue is None and net_profit is None and last_date is None:
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
        "last_reported_eps": last_eps, "last_eps_surprise_pct": last_surprise,
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
    bars.set_defaults(func=cmd_ingest_bars)

    technical = ingest_sub.add_parser("technical", help="Ingest daily technical snapshots.")
    _add_universe_args(technical, allow_missing=True)
    technical.set_defaults(func=cmd_ingest_technical)

    fundamentals = ingest_sub.add_parser(
        "fundamentals", help="Ingest analyst/EPS data (best-effort)."
    )
    _add_universe_args(fundamentals, allow_missing=True)
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
