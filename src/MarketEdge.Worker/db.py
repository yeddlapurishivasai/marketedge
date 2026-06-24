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

FUNDAMENTALS_TABLES = {
    "india": "IndianStockFundamentals",
    "us": "USStockFundamentals",
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
    test_sample_only: bool = False,
) -> list[dict[str, Any]]:
    market_key = market.lower()
    if market_key not in MARKET_TABLES:
        raise ValueError(f"Unsupported market: {market}")

    stock_table, sector_table, _ = MARKET_TABLES[market_key]

    top_clause = f"TOP {limit}" if limit else ""
    where_clauses = []
    params: list[Any] = []

    if test_sample_only:
        where_clauses.append("st.IsTestSample = 1")

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
    """Upsert a single stock analysis result keyed by (WeekNumber, Symbol).

    Within a week a symbol has exactly one row. Re-runs and retry runs overwrite the
    existing row (stamping the latest RunId) rather than inserting duplicates, so the
    week's result set is a single upserted snapshot.
    """
    import math
    table = _results_table(market)

    def _clean(v: Any) -> Any:
        """Convert NaN/inf to None for SQL Server compatibility."""
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v

    merge_query = f"""
        MERGE dbo.{table} AS target
        USING (SELECT ? AS WeekNumber, ? AS Symbol) AS src
        ON target.WeekNumber = src.WeekNumber AND target.Symbol = src.Symbol
        WHEN MATCHED THEN UPDATE SET
            RunId = ?, CompanyName = ?, SectorId = ?, SectorName = ?,
            ClosePrice = ?, MA10 = ?, MA30 = ?, MarketCap = ?,
            IsStage2 = ?, Classification = ?, WeeksInStage2 = ?,
            RSScore = ?, RSRank = ?, RS1w = ?, RS2w = ?, RS3w = ?,
            RSDelta1w = ?, RSDelta2w = ?, RSDelta3w = ?,
            MomentumScore = ?, ROC1w = ?, ROC2w = ?, ROC3w = ?,
            Quadrant = ?, ADRatio = ?, ADClassification = ?
        WHEN NOT MATCHED THEN INSERT (
            RunId, WeekNumber, Symbol, CompanyName, SectorId, SectorName,
            ClosePrice, MA10, MA30, MarketCap,
            IsStage2, Classification, WeeksInStage2,
            RSScore, RSRank, RS1w, RS2w, RS3w,
            RSDelta1w, RSDelta2w, RSDelta3w,
            MomentumScore, ROC1w, ROC2w, ROC3w,
            Quadrant, ADRatio, ADClassification
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    week_number = result["week_number"]
    symbol = result["symbol"]
    run_id = result["run_id"]

    # Column values shared by the UPDATE (minus the natural key) and INSERT branches.
    common = (
        result["company_name"],
        result["sector_id"],
        result["sector_name"],
        _clean(result.get("close_price")),
        _clean(result.get("ma10")),
        _clean(result.get("ma30")),
        _clean(result.get("market_cap")),
        int(bool(result.get("is_stage2"))),
        result.get("classification"),
        result.get("weeks_in_stage2"),
        _clean(result.get("rs_score")),
        result.get("rs_rank"),
        _clean(result.get("rs_1w")),
        _clean(result.get("rs_2w")),
        _clean(result.get("rs_3w")),
        _clean(result.get("rs_delta_1w")),
        _clean(result.get("rs_delta_2w")),
        _clean(result.get("rs_delta_3w")),
        _clean(result.get("momentum_score")),
        _clean(result.get("roc_1w")),
        _clean(result.get("roc_2w")),
        _clean(result.get("roc_3w")),
        result.get("quadrant"),
        _clean(result.get("ad_ratio")),
        result.get("ad_classification"),
    )

    params = (
        (week_number, symbol)            # USING src
        + (run_id,) + common             # WHEN MATCHED UPDATE
        + (run_id, week_number, symbol) + common  # WHEN NOT MATCHED INSERT
    )

    cursor = conn.cursor()
    cursor.execute(merge_query, params)
    conn.commit()


def update_market_cap(
    conn: pyodbc.Connection,
    market: str,
    stock_id: int,
    market_cap: int | float | None,
) -> None:
    """Upsert the latest market cap for a stock into its fundamentals table."""
    if market_cap is None:
        return
    market_key = market.lower()
    if market_key not in FUNDAMENTALS_TABLES:
        raise ValueError(f"Unsupported market: {market}")
    table = FUNDAMENTALS_TABLES[market_key]
    cursor = conn.cursor()
    cursor.execute(
        f"""
        MERGE dbo.{table} AS target
        USING (SELECT ? AS StockId, ? AS MarketCap) AS src
        ON target.StockId = src.StockId
        WHEN MATCHED THEN
            UPDATE SET MarketCap = src.MarketCap, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN
            INSERT (StockId, MarketCap, UpdatedAt, CreatedAt)
            VALUES (src.StockId, src.MarketCap, GETUTCDATE(), GETUTCDATE());
        """,
        stock_id,
        market_cap,
    )
    conn.commit()


def get_week_number(conn: pyodbc.Connection, run_id: int) -> str:
    """Return the WeekNumber stamped on a JobRun (empty string if none)."""
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT WeekNumber FROM dbo.JobRuns WHERE Id = ?", run_id
    ).fetchone()
    return row.WeekNumber if row and row.WeekNumber else ""


def get_completed_symbols_for_week(conn: pyodbc.Connection, market: str, week_number: str) -> set[str]:
    """Symbols that already have a result row for the given week (any IsStage2 value)."""
    table = _results_table(market)
    cursor = conn.cursor()
    rows = cursor.execute(
        f"SELECT Symbol FROM dbo.{table} WHERE WeekNumber = ?",
        week_number,
    ).fetchall()
    return {row.Symbol for row in rows}


def get_week_results(conn: pyodbc.Connection, market: str, week_number: str) -> list[dict[str, Any]]:
    """Load the full week's result set (used for post-processing over the whole week)."""
    table = _results_table(market)
    cursor = conn.cursor()
    rows = cursor.execute(
        f"SELECT Symbol, IsStage2, RSScore FROM dbo.{table} WHERE WeekNumber = ?",
        week_number,
    ).fetchall()
    return [
        {
            "symbol": row.Symbol,
            "is_stage2": bool(row.IsStage2),
            "rs_score": float(row.RSScore) if row.RSScore is not None else None,
        }
        for row in rows
    ]


def clear_run_results(conn: pyodbc.Connection, market: str, run_id: int) -> None:
    """Delete any existing results for a run (legacy; no longer used in week-keyed mode)."""
    table = _results_table(market)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM dbo.{table} WHERE RunId = ?", run_id)
    conn.commit()


def get_previous_stage2_symbols(conn: pyodbc.Connection, market: str, week_number: str) -> set[str]:
    """Stage 2 symbols from the most recent prior week (excludes the current week)."""
    table = _results_table(market)
    cursor = conn.cursor()
    prev_week = cursor.execute(
        f"SELECT MAX(WeekNumber) AS WeekNumber FROM dbo.{table} WHERE WeekNumber < ?",
        week_number,
    ).fetchone()

    if prev_week is None or not prev_week.WeekNumber:
        return set()

    rows = cursor.execute(
        f"SELECT Symbol FROM dbo.{table} WHERE WeekNumber = ? AND IsStage2 = 1",
        prev_week.WeekNumber,
    ).fetchall()
    return {row.Symbol for row in rows}


def get_ever_stage2_symbols(conn: pyodbc.Connection, market: str, week_number: str) -> set[str]:
    """Distinct symbols that were ever Stage 2 in any week before the current week."""
    table = _results_table(market)
    cursor = conn.cursor()
    rows = cursor.execute(
        f"""
        SELECT DISTINCT Symbol
        FROM dbo.{table}
        WHERE IsStage2 = 1 AND WeekNumber < ?
        """,
        week_number,
    ).fetchall()
    return {row.Symbol for row in rows}


def get_consecutive_stage2_weeks(
    conn: pyodbc.Connection, market: str, symbols: list[str], week_number: str
) -> dict[str, int]:
    """
    For each symbol, count consecutive prior weeks (before the current week) where it was
    in Stage 2. Returns dict of symbol -> weeks_count (0 if not Stage 2 the prior week).
    """
    if not symbols:
        return {}

    table = _results_table(market)
    cursor = conn.cursor()

    # Distinct prior weeks, newest first.
    weeks = cursor.execute(
        f"SELECT DISTINCT WeekNumber FROM dbo.{table} WHERE WeekNumber < ? ORDER BY WeekNumber DESC",
        week_number,
    ).fetchall()

    if not weeks:
        return {s: 0 for s in symbols}

    week_list = [w.WeekNumber for w in weeks]
    week_placeholders = ",".join("?" * len(week_list))
    symbol_placeholders = ",".join("?" * len(symbols))

    rows = cursor.execute(
        f"""
        SELECT Symbol, WeekNumber, IsStage2
        FROM dbo.{table}
        WHERE WeekNumber IN ({week_placeholders})
          AND Symbol IN ({symbol_placeholders})
        """,
        *week_list,
        *symbols,
    ).fetchall()

    from collections import defaultdict
    symbol_weeks: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        symbol_weeks[row.Symbol][row.WeekNumber] = bool(row.IsStage2)

    result: dict[str, int] = {}
    for symbol in symbols:
        count = 0
        weeks_for_symbol = symbol_weeks.get(symbol, {})
        for wk in week_list:
            if wk in weeks_for_symbol:
                if weeks_for_symbol[wk]:
                    count += 1
                else:
                    break  # Streak broken
            # If the symbol had no row that week (e.g. different sector filter), skip it.
        result[symbol] = count

    return result
