"""SQL Server DML for the ingestion pipeline (pyodbc).

DML only — the schema is owned by the ``src/MarketEdge.Database`` dacpac. India and US
are handled symmetrically via the ``MARKET_TABLES`` lookup. All writes are idempotent
upserts (MERGE) on the natural key.
"""
import logging
import math
from datetime import date, timedelta
from typing import Any

import pyodbc

import confidence
from config import Config

logger = logging.getLogger(__name__)

# market -> (catalog_stocks, tickers, bars1d, ticker_technical, analyst_snapshot, eps_forecasts, ticker_len)
MARKET_TABLES = {
    "india": {
        "stocks": "IndianStocks",
        "tickers": "IndianTickers",
        "bars1d": "IndianBars1D",
        "technical": "IndianTickerTechnical",
        "analyst": "IndianAnalystSnapshot",
        "eps": "IndianEpsForecasts",
        "earnings": "IndianEarningsFundamentals",
        "note": "IndianStockNote",
        "signals": "IndianStockSignals",
        "ideas": "IndianFundamentalIdeas",
        "trades": "IndianBreakouts",
        "scanner_results": "IndianTechnicalScannerResults",
        "ticker_len": 30,
    },
    "us": {
        "stocks": "USStocks",
        "tickers": "USTickers",
        "bars1d": "USBars1D",
        "technical": "USTickerTechnical",
        "analyst": "USAnalystSnapshot",
        "eps": "USEpsForecasts",
        "earnings": "USEarningsFundamentals",
        "note": "USStockNote",
        "signals": "USStockSignals",
        "ideas": "USFundamentalIdeas",
        "trades": "USBreakouts",
        "scanner_results": "USTechnicalScannerResults",
        "ticker_len": 20,
    },
}


def tables_for(market: str) -> dict[str, Any]:
    key = market.lower()
    if key not in MARKET_TABLES:
        raise ValueError(f"Unsupported market: {market}")
    return MARKET_TABLES[key]


def get_connection() -> pyodbc.Connection:
    logger.debug("Opening SQL Server connection")
    return pyodbc.connect(Config.SQL_CONNECTION_STRING)


def update_job_progress(conn: pyodbc.Connection, run_id: int, progress: int) -> None:
    """Set the live ``Progress`` (0-100) on a JobRun row.

    Lets a long ingestion step report fine-grained, in-band progress (e.g. per-symbol in
    the fundamentals loop) so the UI doesn't sit at a single percentage for the whole step.
    Best-effort: a failed progress write must never abort ingestion.
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dbo.JobRuns SET Progress = ? WHERE Id = ?",
        [int(progress), int(run_id)],
    )
    conn.commit()


def _clean(value: Any) -> Any:
    """Convert NaN/inf to None for SQL Server compatibility."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def get_universe(
    conn: pyodbc.Connection,
    market: str,
    limit: int | None = None,
    test_sample_only: bool = False,
    sector_ids: list[int] | None = None,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the symbol universe from the catalog (symbol + is_fno)."""
    t = tables_for(market)
    top_clause = f"TOP {int(limit)}" if limit else ""
    where: list[str] = []
    params: list[Any] = []

    if test_sample_only:
        where.append("IsTestSample = 1")
    if sector_ids:
        placeholders = ",".join("?" * len(sector_ids))
        where.append(f"SectorId IN ({placeholders})")
        params.extend(sector_ids)
    if symbols:
        placeholders = ",".join("?" * len(symbols))
        where.append(f"UPPER(Symbol) IN ({placeholders})")
        params.extend(symbols)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"""
        SELECT {top_clause} Symbol, IsFno
        FROM dbo.{t['stocks']}
        {where_sql}
        ORDER BY Symbol
    """
    rows = conn.cursor().execute(query, params).fetchall()
    return [{"symbol": r.Symbol, "is_fno": bool(r.IsFno)} for r in rows]


def get_present_tickers(conn: pyodbc.Connection, market: str, kind: str) -> set[str]:
    """Return the set of tickers (upper-cased) that already have output for ``kind``.

    kind:
        "bars"       -> tickers whose master row reports BarsAvailable > 0
        "technical"  -> tickers with at least one technical snapshot
        "earnings"   -> tickers with an earnings-fundamentals snapshot
        "signals"    -> tickers with an auto-signals snapshot
    Used to drive ``--missing`` gap-fill runs.
    """
    t = tables_for(market)
    if kind == "bars":
        query = f"SELECT Ticker FROM dbo.{t['tickers']} WHERE BarsAvailable IS NOT NULL AND BarsAvailable > 0"
    elif kind == "technical":
        query = f"SELECT DISTINCT Ticker FROM dbo.{t['technical']}"
    elif kind == "earnings":
        query = f"SELECT Ticker FROM dbo.{t['earnings']}"
    elif kind == "signals":
        query = f"SELECT Ticker FROM dbo.{t['signals']}"
    else:
        raise ValueError(f"Unsupported kind: {kind}")
    rows = conn.cursor().execute(query).fetchall()
    return {r[0].upper() for r in rows if r[0]}


def get_due_earnings_tickers(
    conn: pyodbc.Connection, market: str, today: date, window_days: int
) -> set[str]:
    """Tickers (upper-cased) whose fundamentals are 'due' for the optimized daily run.

    A ticker is due when it has no earnings-fundamentals row yet, OR its NextEarningsDate
    is unknown (NULL), OR NextEarningsDate falls within +/- ``window_days`` of ``today``.
    This lets the daily run skip the bulk of stocks that are nowhere near reporting while
    still capturing each name just before it reports (refreshed estimates) and just after
    (reported actuals), plus backfilling anything not yet captured.
    """
    t = tables_for(market)
    lo = today - timedelta(days=window_days)
    hi = today + timedelta(days=window_days)
    # Universe master rows missing an earnings snapshot, or whose snapshot is due.
    query = f"""
        SELECT m.Ticker
        FROM dbo.{t['tickers']} AS m
        LEFT JOIN dbo.{t['earnings']} AS e ON e.Ticker = m.Ticker
        WHERE e.Ticker IS NULL
           OR e.NextEarningsDate IS NULL
           OR e.NextEarningsDate BETWEEN ? AND ?
    """
    rows = conn.cursor().execute(query, (lo, hi)).fetchall()
    return {r[0].upper() for r in rows if r[0]}


def seed_tickers(conn: pyodbc.Connection, market: str, rows: list[dict[str, Any]]) -> int:
    """Upsert ticker-master rows keyed by Ticker. Returns the number processed."""
    t = tables_for(market)
    table = t["tickers"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker, ? AS Exchange, ? AS IsFno) AS src
        ON tgt.Ticker = src.Ticker
        WHEN MATCHED THEN UPDATE SET
            Exchange = COALESCE(src.Exchange, tgt.Exchange),
            IsFno = src.IsFno,
            Active = 1,
            UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT (Ticker, Exchange, IsFno)
            VALUES (src.Ticker, src.Exchange, src.IsFno);
    """
    payload = [(r["symbol"], r.get("exchange"), 1 if r.get("is_fno") else 0) for r in rows]
    cursor = conn.cursor()
    cursor.executemany(merge, payload)
    conn.commit()
    logger.info("Seeded %s tickers into %s", len(payload), table)
    return len(payload)


def upsert_bars(conn: pyodbc.Connection, market: str, rows: list[tuple]) -> int:
    """Bulk upsert daily bars via a temp staging table + a single MERGE.

    ``rows`` is a list of tuples:
        (ticker, bar_date, open, high, low, close, volume, adj_close)
    spanning any number of tickers. Returns the number of rows staged.
    """
    if not rows:
        return 0
    t = tables_for(market)
    table = t["bars1d"]
    ticker_len = t["ticker_len"]
    cursor = conn.cursor()

    cursor.execute("IF OBJECT_ID('tempdb..#BarsStage') IS NOT NULL DROP TABLE #BarsStage;")
    cursor.execute(
        f"""
        CREATE TABLE #BarsStage (
            Ticker NVARCHAR({ticker_len}) NOT NULL,
            BarDate DATE NOT NULL,
            [Open] DECIMAL(18,4) NULL,
            High DECIMAL(18,4) NULL,
            Low DECIMAL(18,4) NULL,
            [Close] DECIMAL(18,4) NULL,
            Volume BIGINT NULL,
            AdjClose DECIMAL(18,4) NULL
        );
        """
    )
    cursor.fast_executemany = True
    cursor.executemany(
        "INSERT INTO #BarsStage (Ticker, BarDate, [Open], High, Low, [Close], Volume, AdjClose) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cursor.execute(
        f"""
        MERGE dbo.{table} AS tgt
        USING #BarsStage AS src
        ON tgt.Ticker = src.Ticker AND tgt.BarDate = src.BarDate
        WHEN MATCHED THEN UPDATE SET
            [Open] = src.[Open], High = src.High, Low = src.Low,
            [Close] = src.[Close], Volume = src.Volume, AdjClose = src.AdjClose
        WHEN NOT MATCHED THEN INSERT (Ticker, BarDate, [Open], High, Low, [Close], Volume, AdjClose)
            VALUES (src.Ticker, src.BarDate, src.[Open], src.High, src.Low, src.[Close], src.Volume, src.AdjClose);
        """
    )
    cursor.execute("DROP TABLE #BarsStage;")
    conn.commit()
    return len(rows)


def upsert_ticker_technical(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert a single (Ticker, AsOfDate) daily technical snapshot."""
    t = tables_for(market)
    table = t["technical"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker, ? AS AsOfDate) AS src
        ON tgt.Ticker = src.Ticker AND tgt.AsOfDate = src.AsOfDate
        WHEN MATCHED THEN UPDATE SET
            [Close] = ?, DayPct = ?, [Open] = ?, High = ?, Low = ?,
            High52w = ?, From52wHigh = ?, MarketCap = ?, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT
            (Ticker, AsOfDate, [Close], DayPct, [Open], High, Low, High52w, From52wHigh, MarketCap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    common = (
        _clean(row.get("close")), _clean(row.get("day_pct")), _clean(row.get("open")),
        _clean(row.get("high")), _clean(row.get("low")), _clean(row.get("high_52w")),
        _clean(row.get("from_52w_high")), _clean(row.get("market_cap")),
    )
    params = [row["ticker"], row["as_of_date"], *common, row["ticker"], row["as_of_date"], *common]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


    params = [row["ticker"], row["as_of_date"], *common, row["ticker"], row["as_of_date"], *common]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


def upsert_market_cap(
    conn: pyodbc.Connection, market: str, ticker: str, as_of_date, market_cap
) -> None:
    """Upsert ONLY the MarketCap on the (Ticker, AsOfDate) technical snapshot.

    Unlike ``upsert_ticker_technical`` this never clobbers the price columns: on a
    matched row it updates MarketCap alone; otherwise it inserts a minimal row.
    """
    t = tables_for(market)
    table = t["technical"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker, ? AS AsOfDate) AS src
        ON tgt.Ticker = src.Ticker AND tgt.AsOfDate = src.AsOfDate
        WHEN MATCHED THEN UPDATE SET MarketCap = ?, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT (Ticker, AsOfDate, MarketCap)
            VALUES (?, ?, ?);
    """
    mc = _clean(market_cap)
    cursor = conn.cursor()
    cursor.execute(merge, [ticker, as_of_date, mc, ticker, as_of_date, mc])
    conn.commit()


def upsert_analyst_snapshot(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert a single (Ticker, AsOfDate) analyst consensus snapshot."""
    t = tables_for(market)
    table = t["analyst"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker, ? AS AsOfDate) AS src
        ON tgt.Ticker = src.Ticker AND tgt.AsOfDate = src.AsOfDate
        WHEN MATCHED THEN UPDATE SET
            ConsensusRating = ?, NumAnalysts = ?, CurrentQuarterEps = ?,
            NextQuarterEps = ?, CurrentYearEps = ?, NextYearEps = ?,
            TargetLowPrice = ?, TargetMeanPrice = ?, TargetHighPrice = ?,
            RecommendationsJson = COALESCE(?, tgt.RecommendationsJson),
            LatestRatingFirm = COALESCE(?, tgt.LatestRatingFirm),
            LatestRatingGrade = COALESCE(?, tgt.LatestRatingGrade),
            LatestRatingAction = COALESCE(?, tgt.LatestRatingAction),
            LatestRatingDate = COALESCE(?, tgt.LatestRatingDate),
            UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT
            (Ticker, AsOfDate, ConsensusRating, NumAnalysts, CurrentQuarterEps,
             NextQuarterEps, CurrentYearEps, NextYearEps,
             TargetLowPrice, TargetMeanPrice, TargetHighPrice,
             RecommendationsJson, LatestRatingFirm, LatestRatingGrade,
             LatestRatingAction, LatestRatingDate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    common = (
        row.get("consensus_rating"), _clean(row.get("num_analysts")),
        _clean(row.get("current_quarter_eps")), _clean(row.get("next_quarter_eps")),
        _clean(row.get("current_year_eps")), _clean(row.get("next_year_eps")),
        _clean(row.get("target_low_price")), _clean(row.get("target_mean_price")),
        _clean(row.get("target_high_price")),
        row.get("recommendations_json"), row.get("latest_rating_firm"),
        row.get("latest_rating_grade"), row.get("latest_rating_action"),
        _clean(row.get("latest_rating_date")),
    )
    params = [row["ticker"], row["as_of_date"], *common, row["ticker"], row["as_of_date"], *common]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


def upsert_eps_forecast(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert a single (Ticker, AsOfDate, PeriodType, PeriodEndDate) EPS forecast."""
    t = tables_for(market)
    table = t["eps"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker, ? AS AsOfDate, ? AS PeriodType, ? AS PeriodEndDate) AS src
        ON tgt.Ticker = src.Ticker AND tgt.AsOfDate = src.AsOfDate
           AND tgt.PeriodType = src.PeriodType AND tgt.PeriodEndDate = src.PeriodEndDate
        WHEN MATCHED THEN UPDATE SET
            ConsensusEps = ?, HighEps = ?, LowEps = ?, NumEstimates = ?,
            RevisionsUp = ?, RevisionsDown = ?, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT
            (Ticker, AsOfDate, PeriodType, PeriodEndDate, ConsensusEps, HighEps, LowEps,
             NumEstimates, RevisionsUp, RevisionsDown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    key = (row["ticker"], row["as_of_date"], row["period_type"], row["period_end_date"])
    vals = (
        _clean(row.get("consensus_eps")), _clean(row.get("high_eps")), _clean(row.get("low_eps")),
        _clean(row.get("num_estimates")), int(row.get("revisions_up") or 0),
        int(row.get("revisions_down") or 0),
    )
    params = [*key, *vals, *key, *vals]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


def upsert_earnings_fundamentals(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert a single per-ticker earnings fundamentals snapshot (PK = Ticker)."""
    t = tables_for(market)
    table = t["earnings"]
    cols = (
        "AsOfDate", "LatestQuarterEnd",
        "Revenue", "RevenuePrevQ", "RevenueYoyQ", "RevenueGrowthYoyPct",
        "OperatingProfit", "OperatingProfitPrevQ", "OperatingProfitYoyQ",
        "Opm", "OpmPrevQ", "OpmYoyQ",
        "NetProfit", "NetProfitPrevQ", "NetProfitYoyQ", "NetMarginPct",
        "EarningsGrowthYoyPct", "EarningsGrowthQoqPct",
        "EarningsIncreasing", "OperatingProfitTrend", "OpmTrend",
        "LastEarningsDate", "PrevEarningsDate", "NextEarningsDate", "LastReportedEps", "LastEpsSurprisePct",
        "EpsQ1Date", "EpsQ1Estimate", "EpsQ1Actual", "EpsQ1SurprisePct",
        "EpsQ2Date", "EpsQ2Estimate", "EpsQ2Actual", "EpsQ2SurprisePct",
        "EpsQ3Date", "EpsQ3Estimate", "EpsQ3Actual", "EpsQ3SurprisePct",
        "EpsQ4Date", "EpsQ4Estimate", "EpsQ4Actual", "EpsQ4SurprisePct",
        "TrailingPe", "ForwardPe",
    )
    keys = (
        "as_of_date", "latest_quarter_end",
        "revenue", "revenue_prev_q", "revenue_yoy_q", "revenue_growth_yoy_pct",
        "operating_profit", "operating_profit_prev_q", "operating_profit_yoy_q",
        "opm", "opm_prev_q", "opm_yoy_q",
        "net_profit", "net_profit_prev_q", "net_profit_yoy_q", "net_margin_pct",
        "earnings_growth_yoy_pct", "earnings_growth_qoq_pct",
        "earnings_increasing", "operating_profit_trend", "opm_trend",
        "last_earnings_date", "prev_earnings_date", "next_earnings_date", "last_reported_eps", "last_eps_surprise_pct",
        "eps_q1_date", "eps_q1_estimate", "eps_q1_actual", "eps_q1_surprise_pct",
        "eps_q2_date", "eps_q2_estimate", "eps_q2_actual", "eps_q2_surprise_pct",
        "eps_q3_date", "eps_q3_estimate", "eps_q3_actual", "eps_q3_surprise_pct",
        "eps_q4_date", "eps_q4_estimate", "eps_q4_actual", "eps_q4_surprise_pct",
        "trailing_pe", "forward_pe",
    )
    # Earnings-announcement dates and reported-EPS history come from yfinance's flaky,
    # rate-limited get_earnings_dates endpoint (separate from quarterly_income_stmt). On a
    # run where it returns nothing, these incoming values are NULL while the income-statement
    # financials still write — so we must NOT clobber previously-captured values with NULL.
    # Preserve the existing column value when the incoming value is NULL (COALESCE-on-null).
    preserve_on_null = {
        "LastEarningsDate", "PrevEarningsDate", "NextEarningsDate", "LastReportedEps", "LastEpsSurprisePct",
        "EpsQ1Date", "EpsQ1Estimate", "EpsQ1Actual", "EpsQ1SurprisePct",
        "EpsQ2Date", "EpsQ2Estimate", "EpsQ2Actual", "EpsQ2SurprisePct",
        "EpsQ3Date", "EpsQ3Estimate", "EpsQ3Actual", "EpsQ3SurprisePct",
        "EpsQ4Date", "EpsQ4Estimate", "EpsQ4Actual", "EpsQ4SurprisePct",
        "TrailingPe", "ForwardPe",
    }
    set_clause = ", ".join(
        f"{c} = COALESCE(?, tgt.{c})" if c in preserve_on_null else f"{c} = ?"
        for c in cols
    ) + ", UpdatedAt = GETUTCDATE()"
    insert_cols = "Ticker, " + ", ".join(cols)
    insert_ph = "?, " + ", ".join("?" for _ in cols)
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker) AS src
        ON tgt.Ticker = src.Ticker
        WHEN MATCHED THEN UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_ph});
    """
    vals = [_clean(row.get(k)) for k in keys]
    params = [row["ticker"], *vals, row["ticker"], *vals]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


def refresh_fundamental_idea(conn: pyodbc.Connection, market: str, ticker: str) -> None:
    """Recompute the screener 'idea' for a ticker from its earnings + analyst snapshots.

    An idea is keyed by (Ticker, EarningsDate = LastEarningsDate). Earnings-based metrics
    (EPS beat %, YoY OPM/operating-profit expansion) are stamped to the reported result;
    analyst fields (latest upgrade/downgrade + price targets) are refreshed from the most
    recent analyst snapshot on every run (daily detection). When a newer result lands, the
    EarningsDate advances -> a new row is inserted and older rows for the ticker are flagged
    IsStale = 1 so the UI hides them (a future purge job deletes them).
    """
    t = tables_for(market)
    ideas, earnings, analyst = t["ideas"], t["earnings"], t["analyst"]
    merge = f"""
        MERGE dbo.{ideas} AS tgt
        USING (
            SELECT
                e.Ticker,
                e.LastEarningsDate AS EarningsDate,
                e.LastEpsSurprisePct AS EpsBeatPct,
                CASE WHEN e.Opm IS NOT NULL AND e.OpmYoyQ IS NOT NULL
                     THEN e.Opm - e.OpmYoyQ END AS OpmExpansionYoyPct,
                CASE WHEN e.OperatingProfitYoyQ IS NOT NULL AND e.OperatingProfitYoyQ <> 0
                     THEN (e.OperatingProfit - e.OperatingProfitYoyQ)
                          / ABS(e.OperatingProfitYoyQ) * 100 END AS OperatingProfitExpansionYoyPct,
                a.LatestRatingFirm, a.LatestRatingGrade, a.LatestRatingAction, a.LatestRatingDate,
                a.TargetLowPrice, a.TargetMeanPrice, a.TargetHighPrice
            FROM dbo.{earnings} e
            OUTER APPLY (
                SELECT TOP 1 LatestRatingFirm, LatestRatingGrade, LatestRatingAction, LatestRatingDate,
                       TargetLowPrice, TargetMeanPrice, TargetHighPrice
                FROM dbo.{analyst} s
                WHERE s.Ticker = e.Ticker
                ORDER BY s.AsOfDate DESC
            ) a
            WHERE e.Ticker = ? AND e.LastEarningsDate IS NOT NULL
        ) AS src
        ON tgt.Ticker = src.Ticker AND tgt.EarningsDate = src.EarningsDate
        WHEN MATCHED THEN UPDATE SET
            EpsBeatPct = src.EpsBeatPct,
            OpmExpansionYoyPct = src.OpmExpansionYoyPct,
            OperatingProfitExpansionYoyPct = src.OperatingProfitExpansionYoyPct,
            LatestRatingFirm = src.LatestRatingFirm,
            LatestRatingGrade = src.LatestRatingGrade,
            LatestRatingAction = src.LatestRatingAction,
            LatestRatingDate = src.LatestRatingDate,
            TargetLowPrice = src.TargetLowPrice,
            TargetMeanPrice = src.TargetMeanPrice,
            TargetHighPrice = src.TargetHighPrice,
            IsStale = 0,
            UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT (
            Ticker, EarningsDate, EpsBeatPct, OpmExpansionYoyPct, OperatingProfitExpansionYoyPct,
            LatestRatingFirm, LatestRatingGrade, LatestRatingAction, LatestRatingDate,
            TargetLowPrice, TargetMeanPrice, TargetHighPrice
        ) VALUES (
            src.Ticker, src.EarningsDate, src.EpsBeatPct, src.OpmExpansionYoyPct,
            src.OperatingProfitExpansionYoyPct, src.LatestRatingFirm, src.LatestRatingGrade,
            src.LatestRatingAction, src.LatestRatingDate, src.TargetLowPrice, src.TargetMeanPrice,
            src.TargetHighPrice
        );
    """
    stale = f"""
        UPDATE i SET IsStale = 1, UpdatedAt = GETUTCDATE()
        FROM dbo.{ideas} i
        JOIN dbo.{earnings} e ON e.Ticker = i.Ticker
        WHERE i.Ticker = ? AND i.IsStale = 0
          AND e.LastEarningsDate IS NOT NULL
          AND i.EarningsDate < e.LastEarningsDate;
    """
    cursor = conn.cursor()
    cursor.execute(merge, [ticker])
    cursor.execute(stale, [ticker])
    conn.commit()

    _refresh_idea_confidence(conn, market, ticker)


def _fnum(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _refresh_idea_confidence(conn: pyodbc.Connection, market: str, ticker: str) -> None:
    """Score the ticker's active idea (per-metric + fundamental + technical + overall).

    Reads the just-written idea metrics, the latest close (for target upside) and the
    realised paper-trade record, computes the confidence blend (see ``confidence.py``)
    and stamps the scores + rationale JSON onto the idea row.
    """
    t = tables_for(market)
    ideas, technical, trades, results = (
        t["ideas"], t["technical"], t["trades"], t["scanner_results"],
    )
    cur = conn.cursor()

    idea = cur.execute(
        f"""SELECT EarningsDate, EpsBeatPct, OpmExpansionYoyPct, OperatingProfitExpansionYoyPct,
                   LatestRatingGrade, LatestRatingDate, TargetMeanPrice
            FROM dbo.{ideas} WHERE Ticker = ? AND IsStale = 0""",
        [ticker],
    ).fetchone()
    if idea is None:
        return

    today = date.today()
    earnings_date = idea.EarningsDate
    rating_date = idea.LatestRatingDate
    days_since_earnings = (today - earnings_date).days if earnings_date else None
    days_since_rating = (today - rating_date).days if rating_date else None

    close_row = cur.execute(
        f"""SELECT TOP 1 [Close] FROM dbo.{technical}
            WHERE Ticker = ? AND [Close] IS NOT NULL ORDER BY AsOfDate DESC""",
        [ticker],
    ).fetchone()
    close = _fnum(close_row[0]) if close_row else None

    # Realised trade record for this stock (success = closed at a profit).
    tr = cur.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN PnLPct > 0 THEN 1 ELSE 0 END) AS wins
            FROM dbo.{trades} WHERE Ticker = ? AND Status = 'closed'""",
        [ticker],
    ).fetchone()
    own_total = int(tr.total or 0)
    own_wins = int(tr.wins or 0)

    # Triggering scanner: most recent trade's entry scanner, else latest scanner hit.
    scanner = cur.execute(
        f"""SELECT TOP 1 EntryScanner FROM dbo.{trades}
            WHERE Ticker = ? AND EntryScanner IS NOT NULL ORDER BY EntryAt DESC""",
        [ticker],
    ).fetchone()
    scanner_name = scanner[0] if scanner else None
    if not scanner_name:
        sc = cur.execute(
            f"""SELECT TOP 1 ScannerName FROM dbo.{results}
                WHERE Symbol = ? ORDER BY ScanDate DESC""",
            [ticker],
        ).fetchone()
        scanner_name = sc[0] if sc else None

    scanner_wins = scanner_total = 0
    if scanner_name:
        sw = cur.execute(
            f"""SELECT
                    SUM(CASE WHEN (Status='closed' AND PnLPct > 0)
                              OR (Status='active' AND PnLPct > 0) THEN 1 ELSE 0 END) AS Wins,
                    SUM(CASE WHEN Status='closed' OR (Status='active' AND PnLPct IS NOT NULL)
                             THEN 1 ELSE 0 END) AS Total
                FROM dbo.{trades}
                WHERE EntryScanner = ?""",
            [scanner_name],
        ).fetchone()
        if sw is not None:
            scanner_wins = int(sw.Wins or 0)
            scanner_total = int(sw.Total or 0)

    technical_conf, technical_detail = confidence.technical_confidence(
        own_wins, own_total, scanner_wins, scanner_total, scanner_name,
    )

    scores = confidence.compute_confidence(
        eps_beat_pct=_fnum(idea.EpsBeatPct),
        opm_expansion_pp=_fnum(idea.OpmExpansionYoyPct),
        op_expansion_pct=_fnum(idea.OperatingProfitExpansionYoyPct),
        rating_grade=idea.LatestRatingGrade,
        target_mean=_fnum(idea.TargetMeanPrice),
        close=close,
        days_since_earnings=days_since_earnings,
        days_since_rating=days_since_rating,
        technical=technical_conf,
        technical_detail=technical_detail,
    )

    cur.execute(
        f"""UPDATE dbo.{ideas} SET
                EpsBeatConfidence = ?,
                OpmExpansionConfidence = ?,
                OperatingProfitExpansionConfidence = ?,
                AnalystRatingConfidence = ?,
                TargetUpsideConfidence = ?,
                FundamentalConfidence = ?,
                TechnicalConfidence = ?,
                OverallConfidence = ?,
                DaysSinceEarnings = ?,
                DaysSinceRating = ?,
                ConfidenceRationaleJson = ?,
                UpdatedAt = GETUTCDATE()
            WHERE Ticker = ? AND IsStale = 0""",
        [
            scores["eps_beat_confidence"],
            scores["opm_expansion_confidence"],
            scores["operating_profit_expansion_confidence"],
            scores["analyst_rating_confidence"],
            scores["target_upside_confidence"],
            scores["fundamental_confidence"],
            scores["technical_confidence"],
            scores["overall_confidence"],
            days_since_earnings,
            days_since_rating,
            scores["rationale_json"],
            ticker,
        ],
    )
    conn.commit()


def get_stock_note(conn: pyodbc.Connection, market: str, ticker: str) -> str | None:
    """Return the saved free-text note for a ticker, or None."""
    t = tables_for(market)
    table = t["note"]
    cursor = conn.cursor()
    cursor.execute(f"SELECT NoteText FROM dbo.{table} WHERE Ticker = ?", [ticker])
    found = cursor.fetchone()
    return found[0] if found else None


def upsert_stock_note(conn: pyodbc.Connection, market: str, ticker: str, note_text: str | None) -> None:
    """Upsert the per-ticker free-text note (input for the AI workflow)."""
    t = tables_for(market)
    table = t["note"]
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker) AS src
        ON tgt.Ticker = src.Ticker
        WHEN MATCHED THEN UPDATE SET NoteText = ?, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT (Ticker, NoteText) VALUES (?, ?);
    """
    cursor = conn.cursor()
    cursor.execute(merge, [ticker, note_text, ticker, note_text])
    conn.commit()


def get_stock_signals(conn: pyodbc.Connection, market: str, ticker: str) -> dict[str, Any] | None:
    """Return the saved auto-signals snapshot for a ticker, or None."""
    t = tables_for(market)
    table = t["signals"]
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT CapexCwip, CapexCwipPrevQ, CapexChangePct, CapexTrend, CapexAsOf,
                   NewsJson, SignalsText, UpdatedAt
            FROM dbo.{table} WHERE Ticker = ?""",
        [ticker],
    )
    r = cursor.fetchone()
    if not r:
        return None
    return {
        "capex_cwip": r[0], "capex_cwip_prev_q": r[1], "capex_change_pct": r[2],
        "capex_trend": r[3], "capex_as_of": r[4], "news_json": r[5],
        "signals_text": r[6], "updated_at": r[7],
    }


def upsert_stock_signals(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert the per-ticker auto-detected signals snapshot (PK = Ticker).

    Holds CWIP/capex trend + recent news headlines + a compact, token-friendly
    ``SignalsText`` that is fed (alongside the user's StockNote) to the AI workflow.
    """
    t = tables_for(market)
    table = t["signals"]
    cols = ("CapexCwip", "CapexCwipPrevQ", "CapexChangePct", "CapexTrend",
            "CapexAsOf", "NewsJson", "SignalsText")
    keys = ("capex_cwip", "capex_cwip_prev_q", "capex_change_pct", "capex_trend",
            "capex_as_of", "news_json", "signals_text")
    set_clause = ", ".join(f"{c} = ?" for c in cols) + ", UpdatedAt = GETUTCDATE()"
    insert_cols = "Ticker, " + ", ".join(cols)
    insert_ph = "?, " + ", ".join("?" for _ in cols)
    merge = f"""
        MERGE dbo.{table} AS tgt
        USING (SELECT ? AS Ticker) AS src
        ON tgt.Ticker = src.Ticker
        WHEN MATCHED THEN UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_ph});
    """
    vals = [_clean(row.get(k)) for k in keys]
    params = [row["ticker"], *vals, row["ticker"], *vals]
    cursor = conn.cursor()
    cursor.execute(merge, params)
    conn.commit()


def prune_old_bars(conn: pyodbc.Connection, market: str, cutoff: date) -> int:
    """Delete bars older than ``cutoff`` so storage holds a rolling window only.

    Returns the number of rows deleted. Makes repeated ingests idempotent: the stored
    history never grows beyond the configured lookback regardless of prior runs.
    """
    t = tables_for(market)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM dbo.{t['bars1d']} WHERE BarDate < ?", [cutoff])
    deleted = cursor.rowcount
    conn.commit()
    if deleted:
        logger.info("Pruned %s bars older than %s from %s", deleted, cutoff, t["bars1d"])
    return deleted


def refresh_bars_available(conn: pyodbc.Connection, market: str) -> None:
    """Set BarsAvailable on the ticker master from the Bars1D row counts."""
    t = tables_for(market)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        UPDATE tk SET BarsAvailable = c.Cnt, UpdatedAt = GETUTCDATE()
        FROM dbo.{t['tickers']} tk
        OUTER APPLY (
            SELECT COUNT(*) AS Cnt FROM dbo.{t['bars1d']} b WHERE b.Ticker = tk.Ticker
        ) c;
        """
    )
    conn.commit()


def get_recent_bars(
    conn: pyodbc.Connection, market: str, ticker: str, lookback: int = 400
) -> list[dict[str, Any]]:
    """Return up to ``lookback`` most-recent daily bars for a ticker, oldest-first."""
    t = tables_for(market)
    rows = conn.cursor().execute(
        f"""
        SELECT BarDate, [Open], High, Low, [Close]
        FROM (
            SELECT TOP (?) BarDate, [Open], High, Low, [Close]
            FROM dbo.{t['bars1d']}
            WHERE Ticker = ?
            ORDER BY BarDate DESC
        ) q
        ORDER BY BarDate ASC
        """,
        [lookback, ticker],
    ).fetchall()
    return [
        {
            "bar_date": r.BarDate,
            "open": float(r.Open) if r.Open is not None else None,
            "high": float(r.High) if r.High is not None else None,
            "low": float(r.Low) if r.Low is not None else None,
            "close": float(r.Close) if r.Close is not None else None,
        }
        for r in rows
    ]

