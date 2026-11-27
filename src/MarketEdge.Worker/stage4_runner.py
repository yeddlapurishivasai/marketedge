"""Closed-week Stage 4 analysis for each market's existing F&O catalog."""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from config import Config
from db import get_connection, get_stocks, update_job_status
from ingestion_runner import run_bars_refresh_inline
from job_stages import StageTracker
from market_hours import market_local_now
from stage_analysis import (
    calculate_stage4, fetch_benchmark_weekly_from_daily, load_market_cap_from_db,
    load_weekly_price_from_db, week_exclusive_end,
)
from stage4_store import (
    claim_run, completed_history, fail_expired_claim, load_results, load_run, save_classifications,
    save_parameters, save_result, selection_signature, table_for,
)

logger = logging.getLogger(__name__)
_SCOPE_KEYS = ("minMarketCap", "maxMarketCap", "sectorIds", "limit", "testSampleOnly")


class Stage4Cancelled(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def latest_closed_week(market: str, now: datetime | None = None) -> str:
    local = market_local_now(market, now)
    if local is None:
        raise ValueError(f"Cannot resolve exchange timezone for {market}")
    monday = local.date() - timedelta(days=local.weekday())
    close_minutes = 15 * 60 + 30 if market == "india" else 16 * 60
    if local.weekday() < 4 or (
        local.weekday() == 4 and local.hour * 60 + local.minute < close_minutes
    ):
        monday -= timedelta(days=7)
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


def closed_week_end(market: str, week: str, now: datetime | None = None) -> date:
    if not re.fullmatch(r"\d{4}-W\d{2}", week or ""):
        raise ValueError("Stage 4 week must use YYYY-Www")
    end = week_exclusive_end(week)
    if end is None or week > latest_closed_week(market, now):
        raise ValueError(f"Stage 4 requires a valid completed week: {week}")
    return end


def scope_for(parameters: dict) -> str:
    if parameters.get("testSampleOnly"):
        return "sample"
    if any(parameters.get(k) is not None and parameters.get(k) != [] for k in _SCOPE_KEYS[:-1]):
        return "filtered"
    return "full"


def symbol_batches(symbols: list[str], max_chars: int = 6000) -> list[list[str]]:
    batches: list[list[str]] = []
    batch: list[str] = []
    size = 0
    for symbol in symbols:
        if len(symbol) + 1 > max_chars:
            raise ValueError(f"Symbol exceeds refresh argument limit: {symbol}")
        if batch and size + len(symbol) + 1 > max_chars:
            batches.append(batch)
            batch, size = [], 0
        batch.append(symbol)
        size += len(symbol) + 1
    if batch:
        batches.append(batch)
    return batches


def classify_results(results: list[dict], history: dict[str, dict[str, dict]], week: str) -> None:
    target_monday = date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)

    def label(day: date) -> str:
        year, number, _ = day.isocalendar()
        return f"{year}-W{number:02d}"

    previous_week = label(target_monday - timedelta(days=7))
    for result in results:
        result["classification"] = None
        result["weeks_in_stage4"] = None
        if result["analysis_status"] != "evaluated":
            continue
        symbol = result["symbol"]
        previous = history.get(previous_week, {}).get(symbol)
        previous_stage4 = previous is not None and previous.get("is_stage4") is True
        if not result["is_stage4"]:
            result["weeks_in_stage4"] = 0
            if previous_stage4:
                result["classification"] = "removed"
            continue
        ever = any(records.get(symbol, {}).get("is_stage4") is True for records in history.values())
        result["classification"] = "continuing" if previous_stage4 else ("reentry" if ever else "new")
        streak, day = 1, target_monday - timedelta(days=7)
        while history.get(label(day), {}).get(symbol, {}).get("is_stage4") is True:
            streak += 1
            day -= timedelta(days=7)
        result["weeks_in_stage4"] = streak


def _check_active(conn, run_id: int, started: float) -> None:
    row = conn.cursor().execute("SELECT Status FROM dbo.JobRuns WHERE Id = ?", run_id).fetchone()
    if row is None or row.Status != "running":
        raise Stage4Cancelled(f"Stage 4 run {run_id} is no longer active")
    if time.monotonic() - started > Config.MAX_RUN_TIMEOUT:
        raise TimeoutError(f"Stage 4 run exceeded {Config.MAX_RUN_TIMEOUT} seconds")


def _evaluate(conn, market: str, stock: dict, benchmark: pd.DataFrame, end: date,
              run_id: int, week: str, captured: datetime) -> dict:
    result = {
        "run_id": run_id, "week_number": week, "symbol": stock["symbol"],
        "company_name": stock["company_name"], "sector_id": stock["sector_id"],
        "sector_name": stock["sector_name"], "eligibility_captured_at": captured,
        "analysis_status": "insufficient", "is_stage4": None, "classification": None,
        "weeks_in_stage4": None,
    }
    try:
        result["market_cap"] = stock.get("market_cap")
        prices = load_weekly_price_from_db(conn, market, stock["symbol"], end_date=end)
        result["price_as_of_date"] = prices.attrs.get("last_bar_date")
        target_friday = pd.Timestamp(end - timedelta(days=3))
        if prices.empty or len(prices) < 52:
            result["error_message"] = "At least 52 aligned weekly observations are required"
        elif prices.index.max() != target_friday:
            result["analysis_status"] = "stale"
            result["error_message"] = "No stock prices for the target closed week"
        elif (benchmark.attrs.get("last_bar_date") is not None
              and (result["price_as_of_date"] is None
                   or result["price_as_of_date"] < benchmark.attrs["last_bar_date"])):
            result["analysis_status"] = "stale"
            result["error_message"] = "Stock prices are older than the benchmark's last target-week session"
        else:
            analysis = calculate_stage4(prices, benchmark)
            if analysis is None:
                result["error_message"] = "Missing, invalid, or misaligned weekly inputs"
            else:
                result.update(analysis, analysis_status="evaluated")
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        logger.exception("Stage 4 calculation failed for %s/%s", market, stock["symbol"])
        result.update(analysis_status="error", error_message=str(exc)[:2000])
    return result


def _metrics(results: list[dict], scope: str, warnings: list[str], eligible_count: int | None = None) -> dict:
    return {
        "eligibleStocks": len(results) if eligible_count is None else eligible_count,
        "processedStocks": len(results),
        "evaluatedStocks": sum(r["analysis_status"] == "evaluated" for r in results),
        "stage4Count": sum(r.get("is_stage4") is True for r in results),
        "skippedStocks": sum(r["analysis_status"] in ("insufficient", "stale") for r in results),
        "errorStocks": sum(r["analysis_status"] == "error" for r in results),
        "scope": scope, "warnings": warnings,
    }


def _freeze_universe(conn, market: str, parameters: dict, warnings: list[str] | None = None) -> list[dict]:
    cap_filter = parameters.get("minMarketCap") is not None or parameters.get("maxMarketCap") is not None
    stocks = get_stocks(
        conn, market, sector_ids=parameters.get("sectorIds"),
        limit=None if cap_filter else parameters.get("limit"),
        test_sample_only=bool(parameters.get("testSampleOnly")), fno_only=True,
    )
    selected = []
    missing_caps = 0
    for stock in stocks:
        cap = load_market_cap_from_db(conn, market, stock["symbol"])
        minimum, maximum = parameters.get("minMarketCap"), parameters.get("maxMarketCap")
        if minimum is not None or maximum is not None:
            if cap is None:
                missing_caps += 1
                logger.warning("Stage 4 excluded %s: market cap unavailable for requested filter", stock["symbol"])
                continue
            if minimum is not None and cap < float(minimum):
                continue
            if maximum is not None and cap > float(maximum):
                continue
        selected.append({**stock, "market_cap": cap})
    if missing_caps and warnings is not None:
        warnings.append(f"{missing_caps} F&O stocks excluded because market cap was unavailable for the requested filter.")
    limit = parameters.get("limit")
    return selected[:limit] if limit is not None else selected


def run_stage4_job(payload: dict) -> None:
    run_id = int(payload["runId"])
    market = str(payload["market"]).lower()
    table_for(market)
    conn = None
    tracker = None
    claimed = False
    started = time.monotonic()
    warnings: list[str] = []
    results: list[dict] = []
    scope = "full"
    eligible_count = 0
    try:
        conn = get_connection()
        run = load_run(conn, run_id)
        if run is None or run["job_type"] != "stage4_analysis" or run["market"] != market:
            raise ValueError("Stage 4 payload does not match the persisted job")
        if run["status"] != "queued":
            if run["status"] == "running":
                now = utcnow()
                if fail_expired_claim(conn, run_id, market, now, now - timedelta(seconds=Config.MAX_RUN_TIMEOUT)):
                    logger.error("Stage 4 run %s had an expired worker claim; marked failed for retry", run_id)
                    return
            logger.info("Stage 4 run %s already %s; skipping delivery", run_id, run["status"])
            return
        week = run["week_number"]
        if payload.get("weekNumber", week) != week:
            raise ValueError("Stage 4 payload week does not match the persisted job")
        claimed = claim_run(conn, run_id, market, week, utcnow())
        if not claimed:
            return
        end = closed_week_end(market, week)
        parameters = run["parameters"]
        scope = scope_for(parameters)
        tracker = StageTracker(conn, run_id, [
            ("resolve", "F&O universe", 5), ("refresh_bars", "Refresh F&O bars", 25),
            ("benchmark", "Benchmark", 5), ("analyze", "Analyze Stage 4", 55),
            ("finalize", "Finalize", 10),
        ], preserve_terminal_status=True)
        tracker.start("resolve")
        source_id = parameters.get("retrySourceRunId")
        carried: dict[str, dict] = {}
        if source_id is not None:
            source = load_run(conn, int(source_id))
            if (source is None or source["job_type"] != "stage4_analysis"
                    or source["market"] != market or source["week_number"] != week
                    or source["status"] not in ("failed", "completed", "cancelled")):
                raise ValueError("Retry source must be a finished Stage 4 run for the same market/week")
            source_parameters = source["parameters"]
            if selection_signature(parameters) != selection_signature(source_parameters):
                raise ValueError("Retry scope does not match its source run")
            stocks = source_parameters.get("universeSnapshot")
            if not stocks:
                raise ValueError("Retry source has no frozen F&O universe")
            captured = datetime.fromisoformat(source_parameters["eligibilityCapturedAt"])
            carried = {r["symbol"]: r for r in load_results(conn, market, int(source_id))
                       if r["analysis_status"] == "evaluated"}
        else:
            stocks = _freeze_universe(conn, market, parameters, warnings)
            captured = utcnow()
        if not stocks:
            raise ValueError(f"No F&O stocks match the selected {market} scope")
        eligible_count = len(stocks)
        parameters.update(
            universeSymbols=[s["symbol"] for s in stocks], universeSnapshot=stocks,
            eligibilityCapturedAt=captured.isoformat(), scope=scope,
        )
        save_parameters(conn, run_id, parameters)
        tracker.complete("resolve", detail=f"{len(stocks)} F&O stocks ({scope})")
        pending = [s for s in stocks if s["symbol"] not in carried]
        _check_active(conn, run_id, started)
        if pending and week == latest_closed_week(market):
            tracker.start("refresh_bars")
            batches = symbol_batches([s["symbol"] for s in pending])
            for index, batch in enumerate(batches):
                _check_active(conn, run_id, started)
                failed, detail = run_bars_refresh_inline(
                    market, symbols=batch, history_days=760,
                    timeout_seconds=max(1, int(Config.MAX_RUN_TIMEOUT - (time.monotonic() - started))),
                )
                if failed:
                    warning = f"Bars refresh batch {index + 1} failed; evaluating verified existing data. {detail}"
                    warnings.append(warning)
                    logger.warning("%s", warning)
                tracker.progress("refresh_bars", 100 * (index + 1) / len(batches))
            tracker.complete("refresh_bars", detail="; ".join(warnings)[:2000] or "Two-year history refreshed")
        else:
            tracker.skip("refresh_bars", detail="Historical run or no unresolved symbols")
        _check_active(conn, run_id, started)
        tracker.start("benchmark")
        benchmark = fetch_benchmark_weekly_from_daily(market, end_date=end)
        target_friday = pd.Timestamp(end - timedelta(days=3))
        if (benchmark.empty or len(benchmark) < 52 or benchmark.index.max() != target_friday
                or not pd.to_numeric(benchmark["Close"], errors="coerce").map(
                    lambda v: pd.notna(v) and math.isfinite(v) and v > 0
                ).all()):
            raise ValueError("Benchmark lacks valid target-week coverage and 52 weekly observations")
        tracker.complete("benchmark")
        tracker.start("analyze")
        for index, stock in enumerate(stocks):
            _check_active(conn, run_id, started)
            symbol = stock["symbol"]
            if symbol in carried:
                result = dict(carried[symbol], run_id=run_id, classification=None, weeks_in_stage4=None)
            else:
                result = _evaluate(conn, market, stock, benchmark, end, run_id, week, captured)
            save_result(conn, market, result)
            results.append(result)
            tracker.progress(
                "analyze", 100 * (index + 1) / len(stocks),
                detail=f"{index + 1}/{len(stocks)} F&O stocks",
                metrics=_metrics(results, scope, warnings, eligible_count),
            )
        if not any(r["analysis_status"] == "evaluated" for r in results):
            raise ValueError("No F&O stocks had sufficient valid closed-week data")
        unknown = sum(r["analysis_status"] != "evaluated" for r in results)
        if unknown:
            warnings.append(f"{unknown} stocks could not be evaluated; they are not confirmed Stage 4 exits.")
        _check_active(conn, run_id, started)
        tracker.start("finalize")
        history = completed_history(conn, market, week, parameters)
        classify_results(results, history, week)
        save_classifications(conn, market, run_id, results)
        _check_active(conn, run_id, started)
        tracker.finish(metrics=_metrics(results, scope, warnings), completed_at=utcnow(), error="")
        logger.info("Completed Stage 4 run %s: %s", run_id, _metrics(results, scope, warnings))
    except Stage4Cancelled:
        logger.info("Stage 4 run %s cancelled", run_id)
    except Exception as exc:
        logger.exception("Stage 4 run %s failed", run_id)
        if conn is not None and claimed:
            conn.rollback()
            if tracker is not None:
                tracker.fail_running(detail=str(exc)[:2000])
            update_job_status(
                conn, run_id, "failed", error=str(exc)[:4000], completed_at=utcnow(),
                stages=tracker.snapshot() if tracker else None,
                metrics=_metrics(results, scope, warnings, eligible_count), active_only=True,
            )
        raise
    finally:
        if conn is not None:
            conn.close()
