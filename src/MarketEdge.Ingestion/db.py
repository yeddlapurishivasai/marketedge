"""SQL Server DML for the ingestion pipeline (pyodbc).

DML only — the schema is owned by the ``src/MarketEdge.Database`` dacpac. India and US
are handled symmetrically via the ``MARKET_TABLES`` lookup. All writes are idempotent
upserts (MERGE) on the natural key.
"""
import logging
import math
from datetime import date
from typing import Any

import pyodbc

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
            NextQuarterEps = ?, CurrentYearEps = ?, NextYearEps = ?, UpdatedAt = GETUTCDATE()
        WHEN NOT MATCHED THEN INSERT
            (Ticker, AsOfDate, ConsensusRating, NumAnalysts, CurrentQuarterEps,
             NextQuarterEps, CurrentYearEps, NextYearEps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """
    common = (
        row.get("consensus_rating"), _clean(row.get("num_analysts")),
        _clean(row.get("current_quarter_eps")), _clean(row.get("next_quarter_eps")),
        _clean(row.get("current_year_eps")), _clean(row.get("next_year_eps")),
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
        "LastEarningsDate", "PrevEarningsDate", "LastReportedEps", "LastEpsSurprisePct",
    )
    keys = (
        "as_of_date", "latest_quarter_end",
        "revenue", "revenue_prev_q", "revenue_yoy_q", "revenue_growth_yoy_pct",
        "operating_profit", "operating_profit_prev_q", "operating_profit_yoy_q",
        "opm", "opm_prev_q", "opm_yoy_q",
        "net_profit", "net_profit_prev_q", "net_profit_yoy_q", "net_margin_pct",
        "earnings_growth_yoy_pct", "earnings_growth_qoq_pct",
        "earnings_increasing", "operating_profit_trend", "opm_trend",
        "last_earnings_date", "prev_earnings_date", "last_reported_eps", "last_eps_surprise_pct",
    )
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
        f"""SELECT CapexCwip, CapexCwipPrevQ, CapexChangePct, CapexTrend,
                   NewsJson, SignalsText, UpdatedAt
            FROM dbo.{table} WHERE Ticker = ?""",
        [ticker],
    )
    r = cursor.fetchone()
    if not r:
        return None
    return {
        "capex_cwip": r[0], "capex_cwip_prev_q": r[1], "capex_change_pct": r[2],
        "capex_trend": r[3], "news_json": r[4], "signals_text": r[5], "updated_at": r[6],
    }


def upsert_stock_signals(conn: pyodbc.Connection, market: str, row: dict[str, Any]) -> None:
    """Upsert the per-ticker auto-detected signals snapshot (PK = Ticker).

    Holds CWIP/capex trend + recent news headlines + a compact, token-friendly
    ``SignalsText`` that is fed (alongside the user's StockNote) to the AI workflow.
    """
    t = tables_for(market)
    table = t["signals"]
    cols = ("CapexCwip", "CapexCwipPrevQ", "CapexChangePct", "CapexTrend",
            "NewsJson", "SignalsText")
    keys = ("capex_cwip", "capex_cwip_prev_q", "capex_change_pct", "capex_trend",
            "news_json", "signals_text")
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

