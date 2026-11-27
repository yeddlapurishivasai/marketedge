"""Run-scoped Stage 4 persistence; Stage 2 tables are never read or written."""
from __future__ import annotations

import json
import math
from datetime import datetime

_TABLES = {"india": "IndianStage4AnalysisResults", "us": "USStage4AnalysisResults"}
_COLUMNS = {
    "RunId": "run_id", "WeekNumber": "week_number", "Symbol": "symbol",
    "CompanyName": "company_name", "SectorId": "sector_id", "SectorName": "sector_name",
    "ClosePrice": "close_price", "MA10": "ma10", "MA30": "ma30",
    "MA30FourWeeksAgo": "ma30_four_weeks_ago", "MarketCap": "market_cap",
    "IsStage4": "is_stage4", "AnalysisStatus": "analysis_status", "ErrorMessage": "error_message",
    "PriceAsOfDate": "price_as_of_date", "EligibilityCapturedAt": "eligibility_captured_at",
    "Classification": "classification", "WeeksInStage4": "weeks_in_stage4",
    "RSScore": "rs_score", "RSDelta1w": "rs_delta_1w", "RSDelta2w": "rs_delta_2w",
    "RSDelta3w": "rs_delta_3w", "MomentumScore": "momentum_score",
    "ROC1w": "roc_1w", "ROC2w": "roc_2w", "ROC3w": "roc_3w",
    "Quadrant": "quadrant", "ADRatio": "ad_ratio", "ADClassification": "ad_classification",
}


def table_for(market: str) -> str:
    if market not in _TABLES:
        raise ValueError(f"Unsupported Stage 4 market: {market}")
    return _TABLES[market]


def selection_signature(parameters: dict) -> tuple:
    return (
        parameters.get("minMarketCap"), parameters.get("maxMarketCap"),
        tuple(sorted(set(parameters.get("sectorIds") or []))),
        parameters.get("limit"), bool(parameters.get("testSampleOnly")),
    )


def load_run(conn, run_id: int) -> dict | None:
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT Id, JobType, Market, WeekNumber, Status, Parameters FROM dbo.JobRuns WHERE Id = ?",
        run_id,
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row.Id, "job_type": row.JobType, "market": row.Market,
        "week_number": row.WeekNumber, "status": row.Status,
        "parameters": json.loads(row.Parameters) if row.Parameters else {},
    }


def claim_run(conn, run_id: int, market: str, week: str, now: datetime) -> bool:
    cursor = conn.cursor()
    row = cursor.execute(
        "UPDATE dbo.JobRuns SET Status = 'running', StartedAt = ?, ErrorMessage = NULL "
        "OUTPUT INSERTED.Id WHERE Id = ? AND JobType = 'stage4_analysis' "
        "AND Market = ? AND WeekNumber = ? AND Status = 'queued'",
        now, run_id, market, week,
    ).fetchone()
    conn.commit()
    return row is not None


def fail_expired_claim(conn, run_id: int, market: str, now: datetime, cutoff: datetime) -> bool:
    row = conn.cursor().execute(
        "UPDATE dbo.JobRuns SET Status = 'failed', CompletedAt = ?, ErrorMessage = ? "
        "OUTPUT INSERTED.Id WHERE Id = ? AND JobType = 'stage4_analysis' AND Market = ? "
        "AND Status = 'running' AND (StartedAt IS NULL OR StartedAt <= ?)",
        now, "Stage 4 worker claim expired after its run timeout. Retry unresolved stocks from this run.",
        run_id, market, cutoff,
    ).fetchone()
    conn.commit()
    return row is not None


def save_parameters(conn, run_id: int, parameters: dict) -> None:
    conn.cursor().execute(
        "UPDATE dbo.JobRuns SET Parameters = ? WHERE Id = ? AND Status = 'running'",
        json.dumps(parameters, default=str), run_id,
    )
    conn.commit()


def load_results(conn, market: str, run_id: int) -> list[dict]:
    cursor = conn.cursor()
    rows = cursor.execute(
        f"SELECT {', '.join('[' + c + ']' for c in _COLUMNS)} "
        f"FROM dbo.{table_for(market)} WHERE RunId = ? ORDER BY Symbol",
        run_id,
    ).fetchall()
    return [{key: getattr(row, column) for column, key in _COLUMNS.items()} for row in rows]


def save_result(conn, market: str, result: dict) -> None:
    columns = list(_COLUMNS)
    values = []
    for key in _COLUMNS.values():
        value = result.get(key)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Non-finite Stage 4 result: {result['symbol']}/{key}")
        values.append(value)
    conn.cursor().execute(
        f"INSERT INTO dbo.{table_for(market)} ({', '.join('[' + c + ']' for c in columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        *values,
    )
    conn.commit()


def completed_history(conn, market: str, week: str, requested: dict) -> dict[str, dict[str, dict]]:
    """Newest completed snapshot per earlier week with matching selection filters."""
    cursor = conn.cursor()
    runs = cursor.execute(
        "SELECT Id, WeekNumber, Parameters FROM dbo.JobRuns "
        "WHERE JobType = 'stage4_analysis' AND Market = ? AND WeekNumber < ? "
        "AND Status = 'completed' ORDER BY WeekNumber DESC, CompletedAt DESC, Id DESC",
        market, week,
    ).fetchall()
    history: dict[str, dict[str, dict]] = {}
    for row in runs:
        parameters = json.loads(row.Parameters) if row.Parameters else {}
        if selection_signature(parameters) != selection_signature(requested) or row.WeekNumber in history:
            continue
        history[row.WeekNumber] = {r["symbol"]: r for r in load_results(conn, market, row.Id)}
    return history


def save_classifications(conn, market: str, run_id: int, results: list[dict]) -> None:
    cursor = conn.cursor()
    for result in results:
        cursor.execute(
            f"UPDATE dbo.{table_for(market)} SET Classification = ?, WeeksInStage4 = ? "
            "WHERE RunId = ? AND Symbol = ?",
            result.get("classification"), result.get("weeks_in_stage4"), run_id, result["symbol"],
        )
    conn.commit()
