import base64
import json
import logging
import math
import time
from datetime import datetime, timezone, date, timedelta
from threading import Lock
from typing import Any

import pandas as pd
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.queue import QueueClient

from config import Config
from rs_rating import compute_rs_ratings
from job_stages import StageTracker
from db import (
    get_completed_symbols_for_week,
    get_connection,
    get_consecutive_stage2_weeks,
    get_ever_stage2_symbols,
    get_previous_stage2_symbols,
    get_stocks,
    get_week_number,
    get_week_results,
    save_single_result,
    update_job_status,
)
from stage_analysis import (
    calculate_stage2,
    classify_stocks,
    compute_rs_ranks,
    fetch_benchmark_data,
    fetch_benchmark_weekly_from_daily,
    fetch_market_caps,
    fetch_price_data,
    load_market_cap_from_db,
    load_weekly_price_from_db,
    week_exclusive_end,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

_listener_lock = Lock()
_listener_status: dict[str, Any] = {
    "queue_listener": "starting",
    "state": "initializing",
    "current_run_id": None,
    "current_market": None,
    "last_error": None,
    "last_message_at": None,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _set_listener_status(**updates: Any) -> None:
    with _listener_lock:
        _listener_status.update(updates)


def get_worker_status() -> dict[str, Any]:
    with _listener_lock:
        return dict(_listener_status)


def _coerce_number(value: Any) -> float | None:
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_metrics(
    market: str,
    total_stocks: int,
    filtered_stocks: int,
    stage2_count: int = 0,
    processed_stocks: int = 0,
    price_data_count: int = 0,
    skipped_stocks: int = 0,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
) -> dict[str, Any]:
    return {
        "market": market,
        "totalStocks": total_stocks,
        "filteredStocks": filtered_stocks,
        "stage2Count": stage2_count,
        "processedStocks": processed_stocks,
        "priceDataCount": price_data_count,
        "skippedStocks": skipped_stocks,
        "minMarketCap": min_market_cap,
        "maxMarketCap": max_market_cap,
    }


class RunCancelled(Exception):
    """Raised when a run is cancelled by the user."""
    pass


def _check_cancelled(conn: Any, run_id: int) -> None:
    """Check if the run has been cancelled in the DB; raise if so."""
    row = conn.cursor().execute(
        "SELECT Status FROM dbo.JobRuns WHERE Id = ?", run_id
    ).fetchone()
    if row and row.Status == "cancelled":
        raise RunCancelled(f"Run {run_id} was cancelled by user")


def _fetch_single_market_cap(symbol: str, market: str) -> int | None:
    """Fetch market cap for a single stock."""
    from stage_analysis import _to_yfinance_symbol
    import yfinance as yf
    yf_symbol = _to_yfinance_symbol(symbol, market)
    try:
        fast_info = yf.Ticker(yf_symbol).fast_info
        mc = fast_info.get("market_cap") or fast_info.get("marketCap")
        return mc
    except Exception as exc:
        logger.warning("Failed to fetch market cap for %s: %s", symbol, exc)
        return None


def _fetch_single_price_data(symbol: str, market: str, end_date: Any = None) -> Any:
    """Fetch weekly price data for a single stock.

    When ``end_date`` is provided, fetches a bounded window ending at that date
    (point-in-time for a past week) instead of the default relative lookback period.
    """
    from stage_analysis import _to_yfinance_symbol, WEEKLY_LOOKBACK_PERIOD, WEEKLY_INTERVAL, WEEKLY_LOOKBACK_DAYS
    from datetime import timedelta
    import yfinance as yf
    yf_symbol = _to_yfinance_symbol(symbol, market)
    for attempt in range(Config.YFINANCE_MAX_RETRIES):
        try:
            if end_date is not None:
                start_date = end_date - timedelta(days=WEEKLY_LOOKBACK_DAYS)
                raw = yf.download(
                    tickers=yf_symbol,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    interval=WEEKLY_INTERVAL,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
            else:
                raw = yf.download(
                    tickers=yf_symbol,
                    period=WEEKLY_LOOKBACK_PERIOD,
                    interval=WEEKLY_INTERVAL,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
            if raw.empty:
                return None
            # yfinance returns MultiIndex (Price, Ticker) even for single tickers
            if isinstance(raw.columns, pd.MultiIndex):
                raw = raw.droplevel("Ticker", axis=1)
            frame = raw.dropna(how="all").tail(60)
            if frame.empty or "Close" not in frame.columns:
                return None
            return frame
        except Exception as exc:
            logger.warning("Price fetch attempt %s/%s for %s: %s", attempt + 1, Config.YFINANCE_MAX_RETRIES, symbol, exc)
            if attempt < Config.YFINANCE_MAX_RETRIES - 1:
                time.sleep(Config.YFINANCE_BATCH_DELAY * (2 ** attempt))
    return None


def process_message(message_content: str) -> None:
    # .NET API sends base64-encoded JSON
    try:
        decoded = base64.b64decode(message_content).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        payload = json.loads(message_content)

    # Scanner jobs (feature 011) are dispatched to a dedicated runner. Existing stage-2
    # messages have no jobType, so default to the stage-2 path below.
    if str(payload.get("jobType", "")).lower() == "scanner":
        from scanners.runner import run_scanner_job
        run_scanner_job(payload)
        return

    # Data-ingestion jobs run the bundled ingestion CLI on the worker (the Python host).
    if str(payload.get("jobType", "")).lower() == "ingestion":
        from ingestion_runner import run_ingestion_job
        run_ingestion_job(payload)
        return

    # Single-stock refresh: re-ingest one symbol's data then recompute its score.
    if str(payload.get("jobType", "")).lower() == "stock_refresh":
        from ingestion_runner import run_stock_refresh_job
        run_stock_refresh_job(payload)
        return

    # Nightly fundamentals-only refresh for the stage2 universe.
    if str(payload.get("jobType", "")).lower() == "fundamentals":
        from ingestion_runner import run_fundamentals_job
        run_fundamentals_job(payload)
        return

    # Market regime (feature 013): refresh benchmark/volatility bars + compute & persist the full regime.
    if str(payload.get("jobType", "")).lower() == "market_regime":
        from market_regime_runner import run_market_regime_job
        run_market_regime_job(payload)
        return

    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    min_market_cap = _coerce_number(payload.get("minMarketCap"))
    max_market_cap = _coerce_number(payload.get("maxMarketCap"))
    sector_ids = payload.get("sectorIds")  # list[int] or None
    limit = payload.get("limit")  # int or None
    if limit is not None:
        limit = int(limit)
    test_sample_only = bool(payload.get("testSampleOnly"))
    retry_failed_only = bool(payload.get("retryFailedOnly"))
    week_number = payload.get("weekNumber")  # may be None; resolved from JobRuns below

    _set_listener_status(
        queue_listener="running",
        state="processing",
        current_run_id=run_id,
        current_market=market,
        last_error=None,
        last_message_at=_utcnow().isoformat(),
    )

    logger.info(
        "Processing run %s for market %s (sectors=%s, limit=%s, test_sample_only=%s, retry_failed_only=%s)",
        run_id, market, sector_ids, limit, test_sample_only, retry_failed_only,
    )
    conn = None
    tracker: StageTracker | None = None

    try:
        conn = get_connection()

        # Idempotency: skip if this run is already completed or cancelled
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT Status FROM dbo.JobRuns WHERE Id = ?", run_id
        ).fetchone()
        if row and row.Status in ("completed", "cancelled"):
            logger.info("Run %s already %s — skipping (idempotent)", run_id, row.Status)
            return

        # Resolve the week this run belongs to (results are upserted per week).
        if not week_number:
            week_number = get_week_number(conn, run_id)
        if not week_number:
            raise ValueError(f"Run {run_id} has no WeekNumber — cannot key results")

        # Point-in-time: if the target week is fully in the past, bound all price fetches
        # to that week's close so the analysis reflects how the week actually looked.
        as_of_end = week_exclusive_end(week_number)
        if as_of_end is not None and as_of_end > date.today():
            as_of_end = None  # current/ongoing week — fetch through today (live)
        logger.info(
            "Run %s is for week %s (%s)",
            run_id, week_number,
            f"point-in-time, prices up to {as_of_end}" if as_of_end else "live/current",
        )

        update_job_status(
            conn,
            run_id,
            "running",
            progress=0,
            metrics=_build_metrics(market, 0, 0, min_market_cap=min_market_cap, max_market_cap=max_market_cap),
            started_at=_utcnow(),
            error="",
        )

        # Stage roadmap for this run (see StageTracker). The tracker owns the overall
        # Progress bar (weighted average of the stages) as well as the per-stage breakdown.
        tracker = StageTracker(
            conn,
            run_id,
            [
                ("refresh_bars", "Refresh bars", 15),
                ("rs", "RS ratings", 10),
                ("benchmark", "Benchmark", 5),
                ("analyze", "Analyze stocks", 60),
                ("finalize", "Finalize", 10),
            ],
            write_progress=True,
        )
        tracker.publish()

        # Fetch all stocks and group by sector
        all_stocks = get_stocks(conn, market, sector_ids=sector_ids, limit=limit, test_sample_only=test_sample_only)
        if not all_stocks:
            raise ValueError(f"No stocks found for market {market} with given filters")

        # Retry mode: only process symbols in the selected universe that lack a result
        # row for this week (failed/pending tickers from an earlier run).
        if retry_failed_only:
            done_symbols = get_completed_symbols_for_week(conn, market, week_number)
            before = len(all_stocks)
            all_stocks = [s for s in all_stocks if s["symbol"] not in done_symbols]
            logger.info(
                "Retry mode: %s of %s symbols already have results for week %s — processing %s remaining",
                before - len(all_stocks), before, week_number, len(all_stocks),
            )
            if not all_stocks:
                logger.info("Nothing to retry for week %s — all selected symbols already processed", week_number)

        total_stocks = len(all_stocks)
        sectors_map: dict[int, list[dict[str, Any]]] = {}
        for stock in all_stocks:
            sid = stock["sector_id"]
            sectors_map.setdefault(sid, []).append(stock)

        sector_list = list(sectors_map.items())
        total_sectors = len(sector_list)
        logger.info("Found %s stocks across %s sectors", total_stocks, total_sectors)

        # Refresh the analyzed universe's daily bars before reading them (Stage 2 runs entirely
        # on the DB, and the RS-ratings step below also reads {Market}Bars1D). The scheduled
        # pre-close scan only refreshes bars for the *stage2* universe, so without this a
        # non-stage2 stock would be evaluated on stale bars and a name that just entered Stage 2
        # could never be discovered.
        #
        # Refresh for a live/current-week run, and also for the most-recently-ended week so a
        # delayed or retried weekend run (executing the Monday+ after the target week, when
        # as_of_end lands on the current week's Monday) still freshens the bars. Older
        # point-in-time backfills are skipped: re-fetching the whole market for each would be
        # wasteful and the target window predates the rolling ingested-bars horizon anyway.
        # Best-effort: a refresh failure logs and the run continues on existing bars.
        today = date.today()
        current_week_monday = today - timedelta(days=today.weekday())
        bars_are_current = as_of_end is None or as_of_end >= current_week_monday
        if bars_are_current and all_stocks:
            tracker.start("refresh_bars")
            try:
                from ingestion_runner import run_bars_refresh_inline

                # Prefer the universe *filters* over enumerating symbols so a full-market run
                # stays well under the OS command-line length limit. In retry mode the remaining
                # set is usually small, so scope to those symbols when it is safely short.
                retry_syms = [s["symbol"] for s in all_stocks] if retry_failed_only else []
                if retry_syms and len(retry_syms) <= 400:
                    refresh_kwargs: dict[str, Any] = {"symbols": retry_syms}
                else:
                    refresh_kwargs = {
                        "sector_ids": sector_ids,
                        "limit": limit,
                        "test_sample": test_sample_only,
                    }

                bars_failed, bars_tail = run_bars_refresh_inline(market, **refresh_kwargs)
                if bars_failed:
                    logger.warning(
                        "Run %s: daily-bars refresh reported a failure — continuing with "
                        "existing bars.\n%s", run_id, bars_tail,
                    )
                    tracker.skip("refresh_bars", detail="refresh reported a failure — using existing bars")
                else:
                    logger.info(
                        "Run %s: refreshed daily bars for the analyzed universe before analysis",
                        run_id,
                    )
                    tracker.complete("refresh_bars")
            except Exception:
                logger.exception(
                    "Run %s: daily-bars refresh failed — continuing with existing bars", run_id
                )
                tracker.skip("refresh_bars", detail="refresh failed — using existing bars")
        else:
            tracker.skip("refresh_bars", detail="point-in-time run — using ingested bars")

        # Workflow step: refresh persisted RS ratings from ingested bars for this run's
        # universe. Reads only ingested data (no network); failures must not abort Stage 2.
        tracker.start("rs")
        try:
            rs_summary = compute_rs_ratings(
                conn,
                market,
                test_sample_only=test_sample_only,
                symbols=[s["symbol"] for s in all_stocks],
            )
            logger.info("RS ratings step complete: %s", rs_summary)
            tracker.complete("rs")
        except Exception:
            logger.exception("RS ratings step failed — continuing Stage 2 run")
            tracker.skip("rs", detail="RS step failed — continuing")

        # Fetch benchmark once (the index is not part of the ingested universe, so this is the
        # only remaining yfinance call — one download per run, not per stock).
        tracker.start("benchmark")
        benchmark_data = fetch_benchmark_weekly_from_daily(market, end_date=as_of_end)
        tracker.complete("benchmark")

        # Process stock by stock within each sector
        all_results: list[dict[str, Any]] = []
        current_stage2_symbols: set[str] = set()
        total_processed = 0
        total_skipped = 0
        total_filtered = 0
        run_start_time = time.monotonic()
        run_timeout = Config.MAX_RUN_TIMEOUT

        tracker.start("analyze")
        for sector_idx, (sector_id, sector_stocks) in enumerate(sector_list):
            # Check cancellation and timeout before each sector
            _check_cancelled(conn, run_id)
            elapsed = time.monotonic() - run_start_time
            if elapsed > run_timeout:
                raise TimeoutError(
                    f"Run exceeded timeout of {run_timeout}s "
                    f"(elapsed {elapsed:.0f}s, processed {total_processed} stocks in {sector_idx} sectors)"
                )

            sector_name = sector_stocks[0]["sector_name"]
            logger.info(
                "=== Sector %s/%s: %s (id=%s, %s stocks) ===",
                sector_idx + 1, total_sectors, sector_name, sector_id, len(sector_stocks),
            )

            for stock_idx, stock in enumerate(sector_stocks):
                symbol = stock["symbol"]

                # 1. Market cap from ingested fundamentals (no network).
                mc = load_market_cap_from_db(conn, market, symbol)
                stock["market_cap"] = mc

                # 2. Apply market cap filter
                if min_market_cap is not None or max_market_cap is not None:
                    if mc is None:
                        total_processed += 1
                        continue
                    if min_market_cap is not None and mc < min_market_cap:
                        total_processed += 1
                        continue
                    if max_market_cap is not None and mc > max_market_cap:
                        total_processed += 1
                        continue

                total_filtered += 1

                # 3. Price data from ingested daily bars, resampled to weekly (no network).
                price_frame = load_weekly_price_from_db(conn, market, symbol, end_date=as_of_end)

                # 4. Analyze
                total_processed += 1
                try:
                    analysis = calculate_stage2(price_frame, benchmark_data)
                except Exception as exc:
                    total_skipped += 1
                    logger.warning("Skipping %s — analysis error: %s", symbol, exc)
                    continue
                if analysis is None:
                    total_skipped += 1
                    logger.warning("Skipping %s — insufficient data", symbol)
                    continue

                result = {
                    "run_id": run_id,
                    "week_number": week_number,
                    "symbol": symbol,
                    "company_name": stock["company_name"],
                    "sector_id": stock["sector_id"],
                    "sector_name": stock["sector_name"],
                    "market_cap": mc,
                    "weeks_in_stage2": 0,
                    **analysis,
                }
                if result["is_stage2"]:
                    current_stage2_symbols.add(symbol)
                all_results.append(result)

                # 5. Save immediately
                save_single_result(conn, market, result)

                # 6. Log every 10 stocks
                if (stock_idx + 1) % 10 == 0 or stock_idx == len(sector_stocks) - 1:
                    logger.info(
                        "  %s: %s/%s stocks, %s Stage 2 so far",
                        sector_name, stock_idx + 1, len(sector_stocks), len(current_stage2_symbols),
                    )

            # Update progress after each sector (stock-based, not sector-based)
            analyze_pct = min(int(total_processed / total_stocks * 100), 100) if total_stocks else 100
            tracker.progress(
                "analyze", analyze_pct,
                detail=f"{total_processed}/{total_stocks} stocks · {len(current_stage2_symbols)} Stage 2",
                metrics=_build_metrics(
                    market, total_stocks, total_filtered,
                    stage2_count=len(current_stage2_symbols),
                    processed_stocks=total_processed,
                    price_data_count=len(all_results) + total_skipped,
                    skipped_stocks=total_skipped,
                    min_market_cap=min_market_cap, max_market_cap=max_market_cap,
                ),
            )
            logger.info(
                "Sector %s/%s complete — %s processed, %s Stage 2",
                sector_idx + 1, total_sectors, total_processed, len(current_stage2_symbols),
            )

        # --- Post-processing over the FULL week's result set ---
        # Results are upserted per week, so classification/ranks/weeks must be computed
        # across every symbol in the week (including symbols processed in an earlier run
        # for the same week), not just this run's symbols. Read the week's snapshot back.
        tracker.start("finalize")
        week_results = get_week_results(conn, market, week_number)
        current_stage2_symbols = {r["symbol"] for r in week_results if r["is_stage2"]}

        previous_stage2_symbols = get_previous_stage2_symbols(conn, market, week_number)
        ever_stage2_symbols = get_ever_stage2_symbols(conn, market, week_number)
        classifications = classify_stocks(
            current_stage2_symbols,
            previous_stage2_symbols,
            ever_stage2_symbols,
        )

        results_table = "IndianStageAnalysisResults" if market == "india" else "USStageAnalysisResults"
        cursor = conn.cursor()
        for symbol, cls in classifications.items():
            if cls:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET Classification = ? WHERE WeekNumber = ? AND Symbol = ?",
                    cls, week_number, symbol,
                )
        conn.commit()

        # Relative-strength ranks computed across the whole week.
        ranked = compute_rs_ranks(
            [{"symbol": r["symbol"], "rs_score": r["rs_score"]} for r in week_results]
        )
        for r in ranked:
            if r.get("rs_rank") is not None:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET RSRank = ? WHERE WeekNumber = ? AND Symbol = ?",
                    r["rs_rank"], week_number, r["symbol"],
                )
        conn.commit()

        # WeeksInStage2 from consecutive prior weeks, for every symbol in the week.
        stage2_symbols_list = list(current_stage2_symbols)
        prior_weeks = get_consecutive_stage2_weeks(conn, market, stage2_symbols_list, week_number)
        for r in week_results:
            sym = r["symbol"]
            weeks_val = (prior_weeks.get(sym, 0) + 1) if r["is_stage2"] else 0
            cursor.execute(
                f"UPDATE dbo.{results_table} SET WeeksInStage2 = ? WHERE WeekNumber = ? AND Symbol = ?",
                weeks_val, week_number, sym,
            )
        conn.commit()

        metrics = _build_metrics(
            market, total_stocks, total_filtered,
            stage2_count=len(current_stage2_symbols),
            processed_stocks=total_processed,
            price_data_count=len(all_results) + total_skipped,
            skipped_stocks=total_skipped,
            min_market_cap=min_market_cap, max_market_cap=max_market_cap,
        )
        metrics["resultCount"] = len(week_results)
        metrics["newCount"] = sum(1 for v in classifications.values() if v == "new")
        metrics["reentryCount"] = sum(1 for v in classifications.values() if v == "reentry")
        metrics["continuingCount"] = sum(1 for v in classifications.values() if v == "continuing")
        metrics["removedCount"] = sum(1 for v in classifications.values() if v == "removed")

        tracker.finish(
            status="completed",
            metrics=metrics,
            completed_at=_utcnow(),
            error="",
        )
        logger.info("Completed run %s with %s results", run_id, len(all_results))
    except RunCancelled:
        logger.info("Run %s was cancelled by user — stopping gracefully", run_id)
        _set_listener_status(state="idle", current_run_id=None, current_market=None)
        return
    except Exception as exc:
        logger.exception("Failed to process run %s", run_id)
        _set_listener_status(last_error=str(exc))
        failure_stages = None
        if tracker is not None:
            tracker.fail_running(detail=str(exc)[:200])
            failure_stages = tracker.snapshot()
        failure_conn = conn
        try:
            if failure_conn is None:
                failure_conn = get_connection()
            update_job_status(
                failure_conn,
                run_id,
                "failed",
                error=str(exc),
                stages=failure_stages,
                completed_at=_utcnow(),
            )
        except Exception:
            logger.exception("Failed to update job status for run %s after error", run_id)
        finally:
            if failure_conn is not None and failure_conn is not conn:
                failure_conn.close()
        raise
    finally:
        if conn is not None:
            conn.close()
        _set_listener_status(state="idle", current_run_id=None, current_market=None)


def _create_queue_client() -> QueueClient:
    queue_client = QueueClient.from_connection_string(
        conn_str=Config.AZURE_STORAGE_CONNECTION_STRING,
        queue_name=Config.QUEUE_NAME,
    )
    try:
        queue_client.create_queue()
    except ResourceExistsError:
        pass
    return queue_client


def start_queue_listener() -> None:
    logger.info("Starting queue listener for %s", Config.QUEUE_NAME)
    _set_listener_status(queue_listener="running", state="idle", last_error=None)

    queue_client = None
    while True:
        try:
            if queue_client is None:
                queue_client = _create_queue_client()

            message = next(
                queue_client.receive_messages(
                    messages_per_page=1,
                    visibility_timeout=Config.QUEUE_VISIBILITY_TIMEOUT,
                ),
                None,
            )

            if message is None:
                time.sleep(Config.QUEUE_POLL_INTERVAL)
                continue

            logger.info("Received queue message %s", message.id)
            try:
                process_message(message.content)
                queue_client.delete_message(message)
                logger.info("Deleted processed queue message %s", message.id)
            except Exception:
                logger.exception("Processing failed for queue message %s", message.id)
                time.sleep(Config.QUEUE_POLL_INTERVAL)
        except AzureError as exc:
            logger.warning("Queue listener hit Azure error: %s", exc)
            _set_listener_status(last_error=str(exc), state="reconnecting")
            queue_client = None
            time.sleep(Config.QUEUE_POLL_INTERVAL)
        except Exception as exc:
            logger.exception("Queue listener hit unexpected error")
            _set_listener_status(last_error=str(exc), state="reconnecting")
            queue_client = None
            time.sleep(Config.QUEUE_POLL_INTERVAL)
