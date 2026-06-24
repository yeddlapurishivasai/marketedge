import base64
import json
import logging
import math
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import pandas as pd
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.queue import QueueClient

from config import Config
from db import (
    clear_run_results,
    get_connection,
    get_consecutive_stage2_weeks,
    get_ever_stage2_symbols,
    get_previous_stage2_symbols,
    get_stocks,
    save_single_result,
    update_job_status,
    update_market_cap,
)
from stage_analysis import (
    calculate_stage2,
    classify_stocks,
    compute_rs_ranks,
    fetch_benchmark_data,
    fetch_market_caps,
    fetch_price_data,
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


def _fetch_single_price_data(symbol: str, market: str) -> Any:
    """Fetch weekly price data for a single stock."""
    from stage_analysis import _to_yfinance_symbol, WEEKLY_LOOKBACK_PERIOD, WEEKLY_INTERVAL
    import yfinance as yf
    yf_symbol = _to_yfinance_symbol(symbol, market)
    for attempt in range(Config.YFINANCE_MAX_RETRIES):
        try:
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
    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    min_market_cap = _coerce_number(payload.get("minMarketCap"))
    max_market_cap = _coerce_number(payload.get("maxMarketCap"))
    sector_ids = payload.get("sectorIds")  # list[int] or None
    limit = payload.get("limit")  # int or None
    if limit is not None:
        limit = int(limit)
    test_sample_only = bool(payload.get("testSampleOnly"))

    _set_listener_status(
        queue_listener="running",
        state="processing",
        current_run_id=run_id,
        current_market=market,
        last_error=None,
        last_message_at=_utcnow().isoformat(),
    )

    logger.info("Processing run %s for market %s (sectors=%s, limit=%s, test_sample_only=%s)", run_id, market, sector_ids, limit, test_sample_only)
    conn = None

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

        update_job_status(
            conn,
            run_id,
            "running",
            progress=0,
            metrics=_build_metrics(market, 0, 0, min_market_cap=min_market_cap, max_market_cap=max_market_cap),
            started_at=_utcnow(),
            error="",
        )

        # Clear any previous results for this run (supports re-runs)
        clear_run_results(conn, market, run_id)

        # Fetch all stocks and group by sector
        all_stocks = get_stocks(conn, market, sector_ids=sector_ids, limit=limit, test_sample_only=test_sample_only)
        if not all_stocks:
            raise ValueError(f"No stocks found for market {market} with given filters")

        total_stocks = len(all_stocks)
        sectors_map: dict[int, list[dict[str, Any]]] = {}
        for stock in all_stocks:
            sid = stock["sector_id"]
            sectors_map.setdefault(sid, []).append(stock)

        sector_list = list(sectors_map.items())
        total_sectors = len(sector_list)
        logger.info("Found %s stocks across %s sectors", total_stocks, total_sectors)

        # Fetch benchmark once
        benchmark_data = fetch_benchmark_data(market)

        # Process stock by stock within each sector
        all_results: list[dict[str, Any]] = []
        current_stage2_symbols: set[str] = set()
        total_processed = 0
        total_skipped = 0
        total_filtered = 0
        stock_delay = max(Config.YFINANCE_BATCH_DELAY / 2, 1.0)
        sector_delay = Config.YFINANCE_BATCH_DELAY * 3
        run_start_time = time.monotonic()
        run_timeout = Config.MAX_RUN_TIMEOUT

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

                # 1. Fetch market cap for this stock
                mc = _fetch_single_market_cap(symbol, market)
                stock["market_cap"] = mc
                # Persist the freshly fetched market cap to the fundamentals table
                if mc is not None:
                    try:
                        update_market_cap(conn, market, stock["id"], mc)
                    except Exception as exc:
                        logger.warning("Failed to persist market cap for %s: %s", symbol, exc)
                time.sleep(stock_delay)

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

                # 3. Fetch price data for this stock
                price_frame = _fetch_single_price_data(symbol, market)
                time.sleep(stock_delay)

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
            progress = min(int(total_processed / total_stocks * 90), 90)
            update_job_status(
                conn, run_id, "running", progress=progress,
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

            # Throttle between sectors
            if sector_idx < total_sectors - 1:
                logger.info("Pausing %ss between sectors...", sector_delay)
                time.sleep(sector_delay)

        # --- Post-processing (classifications, ranks, weeks) ---
        previous_stage2_symbols = get_previous_stage2_symbols(conn, market)
        ever_stage2_symbols = get_ever_stage2_symbols(conn, market)
        classifications = classify_stocks(
            current_stage2_symbols,
            previous_stage2_symbols,
            ever_stage2_symbols,
        )

        results_table = "IndianStageAnalysisResults" if market == "india" else "USStageAnalysisResults"
        cursor = conn.cursor()
        for result in all_results:
            cls = classifications.get(result["symbol"])
            result["classification"] = cls
            if cls:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET Classification = ? WHERE RunId = ? AND Symbol = ?",
                    cls, run_id, result["symbol"],
                )
        conn.commit()

        compute_rs_ranks(all_results)
        for result in all_results:
            if result.get("rs_rank") is not None:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET RSRank = ? WHERE RunId = ? AND Symbol = ?",
                    result["rs_rank"], run_id, result["symbol"],
                )
        conn.commit()

        stage2_symbols_list = list(current_stage2_symbols)
        prior_weeks = get_consecutive_stage2_weeks(conn, market, stage2_symbols_list)
        for result in all_results:
            sym = result["symbol"]
            if result.get("is_stage2"):
                result["weeks_in_stage2"] = prior_weeks.get(sym, 0) + 1
            else:
                result["weeks_in_stage2"] = 0
            cursor.execute(
                f"UPDATE dbo.{results_table} SET WeeksInStage2 = ? WHERE RunId = ? AND Symbol = ?",
                result["weeks_in_stage2"], run_id, sym,
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
        metrics["resultCount"] = len(all_results)
        metrics["newCount"] = sum(1 for v in classifications.values() if v == "new")
        metrics["reentryCount"] = sum(1 for v in classifications.values() if v == "reentry")
        metrics["continuingCount"] = sum(1 for v in classifications.values() if v == "continuing")
        metrics["removedCount"] = sum(1 for v in classifications.values() if v == "removed")

        update_job_status(
            conn,
            run_id,
            "completed",
            progress=100,
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
        failure_conn = conn
        try:
            if failure_conn is None:
                failure_conn = get_connection()
            update_job_status(
                failure_conn,
                run_id,
                "failed",
                error=str(exc),
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
