import base64
import json
import logging
import math
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.queue import QueueClient

from config import Config
from db import (
    clear_run_results,
    get_connection,
    get_ever_stage2_symbols,
    get_previous_stage2_symbols,
    get_stocks,
    save_single_result,
    update_job_status,
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

    _set_listener_status(
        queue_listener="running",
        state="processing",
        current_run_id=run_id,
        current_market=market,
        last_error=None,
        last_message_at=_utcnow().isoformat(),
    )

    logger.info("Processing run %s for market %s (sectors=%s, limit=%s)", run_id, market, sector_ids, limit)
    conn = None

    try:
        conn = get_connection()
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

        stocks = get_stocks(conn, market, sector_ids=sector_ids, limit=limit)
        if not stocks:
            raise ValueError(f"No stocks found for market {market} with given filters")

        total_stocks = len(stocks)
        market_caps: dict[str, Any] = {}

        if min_market_cap is not None or max_market_cap is not None:
            market_caps = fetch_market_caps(
                [stock["symbol"] for stock in stocks],
                market,
                batch_delay=max(Config.YFINANCE_BATCH_DELAY, 1.0),
            )
            filtered_stocks: list[dict[str, Any]] = []
            for stock in stocks:
                market_cap = market_caps.get(stock["symbol"])
                stock["market_cap"] = market_cap
                if market_cap is None:
                    continue
                if min_market_cap is not None and market_cap < min_market_cap:
                    continue
                if max_market_cap is not None and market_cap > max_market_cap:
                    continue
                filtered_stocks.append(stock)
            stocks = filtered_stocks
        else:
            for stock in stocks:
                stock["market_cap"] = None

        filtered_count = len(stocks)
        if not stocks:
            raise ValueError("No stocks remain after applying market cap filters")

        update_job_status(
            conn,
            run_id,
            "running",
            progress=10,
            metrics=_build_metrics(
                market,
                total_stocks,
                filtered_count,
                min_market_cap=min_market_cap,
                max_market_cap=max_market_cap,
            ),
        )

        benchmark_data = fetch_benchmark_data(market)
        symbols = [stock["symbol"] for stock in stocks]
        total_batches = max(1, math.ceil(len(symbols) / Config.YFINANCE_BATCH_SIZE))
        price_data: dict[str, Any] = {}

        for batch_number in range(total_batches):
            batch_symbols = symbols[
                batch_number * Config.YFINANCE_BATCH_SIZE : (batch_number + 1) * Config.YFINANCE_BATCH_SIZE
            ]
            batch_data = fetch_price_data(
                batch_symbols,
                market,
                batch_size=Config.YFINANCE_BATCH_SIZE,
                batch_delay=Config.YFINANCE_BATCH_DELAY,
                max_retries=Config.YFINANCE_MAX_RETRIES,
            )
            price_data.update(batch_data)
            progress = 10 + int((batch_number + 1) / total_batches * 60)
            update_job_status(
                conn,
                run_id,
                "running",
                progress=progress,
                metrics=_build_metrics(
                    market,
                    total_stocks,
                    filtered_count,
                    price_data_count=len(price_data),
                    min_market_cap=min_market_cap,
                    max_market_cap=max_market_cap,
                ),
            )

        current_stage2_symbols: set[str] = set()
        results: list[dict[str, Any]] = []
        skipped_stocks = 0

        for index, stock in enumerate(stocks, start=1):
            analysis = calculate_stage2(price_data.get(stock["symbol"]), benchmark_data)
            if analysis is None:
                skipped_stocks += 1
                logger.warning("Skipping %s due to insufficient or missing data", stock["symbol"])
            else:
                result = {
                    "run_id": run_id,
                    "symbol": stock["symbol"],
                    "company_name": stock["company_name"],
                    "sector_id": stock["sector_id"],
                    "sector_name": stock["sector_name"],
                    "market_cap": stock.get("market_cap") or market_caps.get(stock["symbol"]),
                    **analysis,
                }
                if result["is_stage2"]:
                    current_stage2_symbols.add(stock["symbol"])
                results.append(result)

                # Save each result immediately (incremental)
                save_single_result(conn, market, result)

            if index == len(stocks) or index % max(1, len(stocks) // 10) == 0:
                progress = 70 + int(index / len(stocks) * 20)
                update_job_status(
                    conn,
                    run_id,
                    "running",
                    progress=progress,
                    metrics=_build_metrics(
                        market,
                        total_stocks,
                        filtered_count,
                        stage2_count=len(current_stage2_symbols),
                        processed_stocks=index,
                        price_data_count=len(price_data),
                        skipped_stocks=skipped_stocks,
                        min_market_cap=min_market_cap,
                        max_market_cap=max_market_cap,
                    ),
                )

        previous_stage2_symbols = get_previous_stage2_symbols(conn, market)
        ever_stage2_symbols = get_ever_stage2_symbols(conn, market)
        classifications = classify_stocks(
            current_stage2_symbols,
            previous_stage2_symbols,
            ever_stage2_symbols,
        )

        # Update classifications in DB for each result
        results_table = "IndianStageAnalysisResults" if market == "india" else "USStageAnalysisResults"
        cursor = conn.cursor()
        for result in results:
            cls = classifications.get(result["symbol"])
            result["classification"] = cls
            if cls:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET Classification = ? WHERE RunId = ? AND Symbol = ?",
                    cls, run_id, result["symbol"],
                )
        conn.commit()

        # Compute RS ranks and update in DB
        compute_rs_ranks(results)
        for result in results:
            if result.get("rs_rank") is not None:
                cursor.execute(
                    f"UPDATE dbo.{results_table} SET RSRank = ? WHERE RunId = ? AND Symbol = ?",
                    result["rs_rank"], run_id, result["symbol"],
                )
        conn.commit()

        metrics = _build_metrics(
            market,
            total_stocks,
            filtered_count,
            stage2_count=len(current_stage2_symbols),
            processed_stocks=len(stocks),
            price_data_count=len(price_data),
            skipped_stocks=skipped_stocks,
            min_market_cap=min_market_cap,
            max_market_cap=max_market_cap,
        )
        metrics["resultCount"] = len(results)
        metrics["newCount"] = sum(1 for value in classifications.values() if value == "new")
        metrics["reentryCount"] = sum(1 for value in classifications.values() if value == "reentry")
        metrics["continuingCount"] = sum(1 for value in classifications.values() if value == "continuing")
        metrics["removedCount"] = sum(1 for value in classifications.values() if value == "removed")

        update_job_status(
            conn,
            run_id,
            "completed",
            progress=100,
            metrics=metrics,
            completed_at=_utcnow(),
            error="",
        )
        logger.info("Completed run %s with %s results", run_id, len(results))
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
                queue_client.receive_messages(messages_per_page=1, visibility_timeout=300),
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
