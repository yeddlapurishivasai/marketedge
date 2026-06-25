"""Reusable workflow step: compute IBD-style RS ratings from ingested daily bars.

This step reads only already-ingested data (``{Market}Bars1D``) — it makes no network
calls — and writes percentile RS ratings (1-99) onto the latest ``{Market}TickerTechnical``
row for each symbol. It can be run on its own (worker ``/steps/compute-rs`` endpoint) or as
part of a Stage 2 analysis run.
"""

import logging
from datetime import date
from typing import Any

import pandas as pd
import pyodbc

logger = logging.getLogger(__name__)

TICKER_TABLES = {
    "india": ("IndianBars1D", "IndianTickerTechnical", "IndianStocks"),
    "us": ("USBars1D", "USTickerTechnical", "USStocks"),
}

# Window label -> trailing trading-day offset.
WINDOWS: dict[str, int] = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
}

# Composite blend weights (renormalised over the windows a symbol actually has).
COMPOSITE_WEIGHTS: dict[str, float] = {
    "1w": 0.15,
    "1m": 0.25,
    "3m": 0.35,
    "6m": 0.25,
}

# Enough history for the longest window plus a small cushion.
_MAX_LOOKBACK_BARS = max(WINDOWS.values()) + 10


def _resolve_tables(market: str) -> tuple[str, str, str]:
    key = market.lower()
    if key not in TICKER_TABLES:
        raise ValueError(f"Unsupported market: {market}")
    return TICKER_TABLES[key]


def _load_bars(
    conn: pyodbc.Connection,
    bars_table: str,
    stocks_table: str,
    test_sample_only: bool,
    symbols: list[str] | None,
) -> pd.DataFrame:
    """Load recent (Ticker, BarDate, Close) rows for the evaluated universe."""
    where: list[str] = []
    params: list[Any] = []

    if symbols:
        placeholders = ",".join("?" * len(symbols))
        where.append(f"b.Ticker IN ({placeholders})")
        params.extend(symbols)

    if test_sample_only:
        where.append(
            f"b.Ticker IN (SELECT Symbol FROM dbo.{stocks_table} WHERE IsTestSample = 1)"
        )

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    query = f"""
        SELECT b.Ticker, b.BarDate, b.[Close]
        FROM dbo.{bars_table} b
        {where_sql}
        ORDER BY b.Ticker, b.BarDate
    """
    cursor = conn.cursor()
    rows = cursor.execute(query, params).fetchall()
    if not rows:
        return pd.DataFrame(columns=["ticker", "bar_date", "close"])
    return pd.DataFrame(
        [(r.Ticker, r.BarDate, float(r.Close) if r.Close is not None else None) for r in rows],
        columns=["ticker", "bar_date", "close"],
    )


def _window_return(closes: list[float], offset: int) -> float | None:
    if len(closes) <= offset:
        return None
    prior = closes[-1 - offset]
    latest = closes[-1]
    if prior is None or latest is None or prior == 0:
        return None
    return latest / prior - 1.0


def _composite_return(window_returns: dict[str, float | None]) -> float | None:
    num = 0.0
    den = 0.0
    for window, weight in COMPOSITE_WEIGHTS.items():
        value = window_returns.get(window)
        if value is not None:
            num += weight * value
            den += weight
    if den == 0:
        return None
    return num / den


def _percentile_rank(series: pd.Series) -> pd.Series:
    """Rank a numeric series to 1-99 (higher value -> higher rank)."""
    valid = series.dropna()
    if valid.empty:
        return pd.Series(index=series.index, dtype="float64")
    if len(valid) == 1:
        ranks = pd.Series([99.0], index=valid.index)
    else:
        ranks = (
            (valid.rank(method="average") - 1) / (len(valid) - 1) * 98 + 1
        ).round()
    return ranks.reindex(series.index)


def compute_rs_ratings(
    conn: pyodbc.Connection,
    market: str,
    test_sample_only: bool = False,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Compute and persist RS ratings for ``market`` from ingested bars.

    Returns a summary ``{market, evaluated, updated, as_of}``.
    """
    bars_table, technical_table, stocks_table = _resolve_tables(market)
    logger.info(
        "Compute RS ratings: market=%s test_sample_only=%s symbols=%s",
        market, test_sample_only, len(symbols) if symbols else "all",
    )

    bars = _load_bars(conn, bars_table, stocks_table, test_sample_only, symbols)
    if bars.empty:
        logger.warning("Compute RS ratings: no ingested bars for market %s — nothing to do", market)
        return {"market": market, "evaluated": 0, "updated": 0, "as_of": None}

    as_of: date = bars["bar_date"].max()

    rows: list[dict[str, Any]] = []
    for ticker, group in bars.groupby("ticker", sort=False):
        closes = group["close"].tolist()[-_MAX_LOOKBACK_BARS:]
        window_returns = {w: _window_return(closes, off) for w, off in WINDOWS.items()}
        record: dict[str, Any] = {"ticker": ticker}
        for window in WINDOWS:
            record[f"ret_{window}"] = window_returns[window]
        record["ret_composite"] = _composite_return(window_returns)
        rows.append(record)

    frame = pd.DataFrame(rows).set_index("ticker")

    rating_cols: dict[str, str] = {}
    for window in WINDOWS:
        col = f"rs_{window}"
        frame[col] = _percentile_rank(frame[f"ret_{window}"])
        rating_cols[window] = col
    frame["rs"] = _percentile_rank(frame["ret_composite"])

    updated = _persist(conn, technical_table, frame, rating_cols, as_of)
    logger.info(
        "Compute RS ratings: market=%s evaluated=%s updated=%s as_of=%s",
        market, len(frame), updated, as_of,
    )
    return {"market": market, "evaluated": int(len(frame)), "updated": updated, "as_of": str(as_of)}


def _to_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _persist(
    conn: pyodbc.Connection,
    technical_table: str,
    frame: pd.DataFrame,
    rating_cols: dict[str, str],
    as_of: date,
) -> int:
    """Upsert RS ratings onto each symbol's latest TickerTechnical row."""
    cursor = conn.cursor()
    update_sql = f"""
        UPDATE dbo.{technical_table}
        SET Rs = ?, Rs1d = ?, Rs1w = ?, Rs1m = ?, Rs3m = ?, Rs6m = ?,
            RsType = 'Full', RsDate = ?
        WHERE Ticker = ?
          AND AsOfDate = (SELECT MAX(AsOfDate) FROM dbo.{technical_table} WHERE Ticker = ?)
    """
    insert_sql = f"""
        INSERT INTO dbo.{technical_table} (Ticker, AsOfDate, Rs, Rs1d, Rs1w, Rs1m, Rs3m, Rs6m, RsType, RsDate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Full', ?)
    """

    updated = 0
    for ticker, row in frame.iterrows():
        rs = _to_int(row.get("rs"))
        rs1d = _to_int(row.get(rating_cols["1d"]))
        rs1w = _to_int(row.get(rating_cols["1w"]))
        rs1m = _to_int(row.get(rating_cols["1m"]))
        rs3m = _to_int(row.get(rating_cols["3m"]))
        rs6m = _to_int(row.get(rating_cols["6m"]))

        cursor.execute(update_sql, rs, rs1d, rs1w, rs1m, rs3m, rs6m, as_of, ticker, ticker)
        if cursor.rowcount == 0:
            try:
                cursor.execute(insert_sql, ticker, as_of, rs, rs1d, rs1w, rs1m, rs3m, rs6m, as_of)
            except pyodbc.Error as exc:
                logger.debug("Compute RS ratings: skipped insert for %s (%s)", ticker, exc)
                continue
        updated += 1

    conn.commit()
    return updated
