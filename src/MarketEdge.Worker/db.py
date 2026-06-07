import json
import logging
from datetime import datetime
from typing import Any

import pyodbc

from config import Config

logger = logging.getLogger(__name__)

MARKET_TABLES = {
    "india": ("IndianStocks", "IndianSectors", "IndianStageAnalysisResults"),
    "us": ("USStocks", "USSectors", "USStageAnalysisResults"),
}


def get_connection() -> pyodbc.Connection:
    logger.debug("Opening SQL Server connection")
    return pyodbc.connect(Config.SQL_CONNECTION_STRING)


def _results_table(market: str) -> str:
    market_key = market.lower()
    if market_key not in MARKET_TABLES:
        raise ValueError(f"Unsupported market: {market}")
    return MARKET_TABLES[market_key][2]


def get_stocks(
    conn: pyodbc.Connection,
    market: str,
    sector_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    market_key = market.lower()
    if market_key not in MARKET_TABLES:
        raise ValueError(f"Unsupported market: {market}")

    stock_table, sector_table, _ = MARKET_TABLES[market_key]

    top_clause = f"TOP {limit}" if limit else ""
    where_clauses = []
    params: list[Any] = []

    if sector_ids:
        placeholders = ",".join("?" * len(sector_ids))
        where_clauses.append(f"st.SectorId IN ({placeholders})")
        params.extend(sector_ids)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    query = f"""
        SELECT {top_clause}
            st.Id,
            st.Symbol,
            st.CompanyName,
            st.SectorId,
            sec.SectorName
        FROM dbo.{stock_table} st
        INNER JOIN dbo.{sector_table} sec ON sec.Id = st.SectorId
        {where_sql}
        ORDER BY st.Symbol
    """

    cursor = conn.cursor()
    rows = cursor.execute(query, params).fetchall()
    return [
        {
            "id": row.Id,
            "symbol": row.Symbol,
            "company_name": row.CompanyName,
            "sector_id": row.SectorId,
            "sector_name": row.SectorName,
        }
        for row in rows
    ]


def update_job_status(
    conn: pyodbc.Connection,
    run_id: int,
    status: str,
    progress: int | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    assignments = ["Status = ?"]
    values: list[Any] = [status]

    if progress is not None:
        assignments.append("Progress = ?")
        values.append(progress)

    if metrics is not None:
        assignments.append("Metrics = ?")
        values.append(json.dumps(metrics, default=str))

    if error is not None:
        assignments.append("ErrorMessage = ?")
        values.append(error)

    if started_at is not None:
        assignments.append("StartedAt = ?")
        values.append(started_at)

    if completed_at is not None:
        assignments.append("CompletedAt = ?")
        values.append(completed_at)

    values.append(run_id)
    query = f"UPDATE dbo.JobRuns SET {', '.join(assignments)} WHERE Id = ?"

    cursor = conn.cursor()
    cursor.execute(query, values)
    conn.commit()
    logger.debug("Updated job %s to status %s", run_id, status)


def save_single_result(conn: pyodbc.Connection, market: str, result: dict[str, Any]) -> None:
    """Save a single stock analysis result immediately after processing."""
    table = _results_table(market)

    insert_query = f"""
        INSERT INTO dbo.{table} (
            RunId, Symbol, CompanyName, SectorId, SectorName,
            ClosePrice, MA10, MA30, MarketCap,
            IsStage2, Classification,
            RSScore, RSRank, RSMomentum,
            MomentumScore, ROC12w, ROC26w, ROC52w,
            Quadrant, ADRatio, ADClassification
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    params = (
        result["run_id"],
        result["symbol"],
        result["company_name"],
        result["sector_id"],
        result["sector_name"],
        result.get("close_price"),
        result.get("ma10"),
        result.get("ma30"),
        result.get("market_cap"),
        int(bool(result.get("is_stage2"))),
        result.get("classification"),
        result.get("rs_score"),
        result.get("rs_rank"),
        result.get("rs_momentum"),
        result.get("momentum_score"),
        result.get("roc_12w"),
        result.get("roc_26w"),
        result.get("roc_52w"),
        result.get("quadrant"),
        result.get("ad_ratio"),
        result.get("ad_classification"),
    )

    cursor = conn.cursor()
    cursor.execute(insert_query, params)
    conn.commit()


def clear_run_results(conn: pyodbc.Connection, market: str, run_id: int) -> None:
    """Delete any existing results for a run (for re-runs)."""
    table = _results_table(market)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM dbo.{table} WHERE RunId = ?", run_id)
    conn.commit()


def get_previous_stage2_symbols(conn: pyodbc.Connection, market: str) -> set[str]:
    table = _results_table(market)
    cursor = conn.cursor()
    latest_run = cursor.execute(
        f"""
        SELECT TOP 1 jr.Id
        FROM dbo.JobRuns jr
        WHERE jr.JobType = 'stage2_analysis'
          AND jr.Market = ?
          AND jr.Status = 'completed'
          AND EXISTS (
              SELECT 1 FROM dbo.{table} sar WHERE sar.RunId = jr.Id
          )
        ORDER BY COALESCE(jr.CompletedAt, jr.CreatedAt) DESC, jr.Id DESC
        """,
        market,
    ).fetchone()

    if latest_run is None:
        return set()

    rows = cursor.execute(
        f"SELECT Symbol FROM dbo.{table} WHERE RunId = ? AND IsStage2 = 1",
        latest_run.Id,
    ).fetchall()
    return {row.Symbol for row in rows}


def get_ever_stage2_symbols(conn: pyodbc.Connection, market: str) -> set[str]:
    table = _results_table(market)
    cursor = conn.cursor()
    rows = cursor.execute(
        f"""
        SELECT DISTINCT sar.Symbol
        FROM dbo.{table} sar
        INNER JOIN dbo.JobRuns jr ON jr.Id = sar.RunId
        WHERE jr.JobType = 'stage2_analysis'
          AND jr.Market = ?
          AND jr.Status = 'completed'
          AND sar.IsStage2 = 1
        """,
        market,
    ).fetchall()
    return {row.Symbol for row in rows}

