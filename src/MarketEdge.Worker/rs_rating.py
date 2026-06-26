"""Reusable workflow step: compute IBD-style RS ratings from ingested daily bars.

This step reads only already-ingested data (``{Market}Bars1D``) — it makes no network
calls — and writes percentile RS ratings (1-99) onto the latest ``{Market}TickerTechnical``
row for each symbol. It can be run on its own (worker ``/steps/compute-rs`` endpoint) or as
part of a Stage 2 analysis run.

RS model
--------
The base rating is an IBD-style weighted price-performance score computed over trailing
quarters (63 trading days each), most-recent quarter double-weighted::

    raw = (2*Q1 + Q2 + Q3 + Q4) / (2 + 1 + 1 + 1)

where ``Qk`` is the simple return of quarter ``k`` back from the as-of bar. Weights are
renormalised over whatever quarters have enough history (the ingested window is ~1 year, so
older snapshots naturally use fewer quarters). The raw scores are percentile-ranked across
the universe to 1-99.

The ``Rs*`` columns are the *same* rating snapshotted back through time, not different return
horizons:

* ``Rs``   — rating as of the latest bar
* ``Rs1d`` — rating as of 1 trading day ago
* ``Rs1w`` — 5 trading days ago
* ``Rs1m`` — 21 trading days ago
* ``Rs3m`` — 63 trading days ago
* ``Rs6m`` — 126 trading days ago
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

# Snapshot column -> how many trading days back the rating is computed as-of.
SNAPSHOT_OFFSETS: dict[str, int] = {
    "rs": 0,
    "rs1d": 1,
    "rs1w": 5,
    "rs1m": 21,
    "rs3m": 63,
    "rs6m": 126,
}

_QUARTER_BARS = 63
_QUARTER_WEIGHTS = (2.0, 1.0, 1.0, 1.0)  # most-recent quarter double-weighted (IBD-style)

# Longest as-of offset plus the deepest quarter lookback we may try to use.
_MAX_LOOKBACK_BARS = max(SNAPSHOT_OFFSETS.values()) + _QUARTER_BARS * len(_QUARTER_WEIGHTS) + 5


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
    """Load recent (Ticker, BarDate, Close) rows for the evaluated universe.

    The symbol filter is chunked: SQL Server caps a statement at 2100 parameters,
    so a single ``Ticker IN (?,?,...)`` over the full universe (>2100 symbols)
    fails with a 07002 error. We batch the IN list and concatenate the results.
    """
    sample_clause = ""
    if test_sample_only:
        sample_clause = (
            f"b.Ticker IN (SELECT Symbol FROM dbo.{stocks_table} WHERE IsTestSample = 1)"
        )

    cursor = conn.cursor()
    rows: list[Any] = []

    def _run(where_sql: str, params: list[Any]) -> None:
        query = f"""
            SELECT b.Ticker, b.BarDate, b.[Close]
            FROM dbo.{bars_table} b
            {where_sql}
            ORDER BY b.Ticker, b.BarDate
        """
        rows.extend(cursor.execute(query, params).fetchall())

    if symbols:
        # Keep well under the 2100-parameter ceiling.
        batch_size = 1000
        for start in range(0, len(symbols), batch_size):
            chunk = symbols[start:start + batch_size]
            placeholders = ",".join("?" * len(chunk))
            # Skip partial/placeholder bars with no close so offset-0 snapshots
            # use the latest *valid* close rather than breaking on a NULL.
            where_sql = f"WHERE b.[Close] IS NOT NULL AND b.Ticker IN ({placeholders})"
            if sample_clause:
                where_sql += f" AND {sample_clause}"
            _run(where_sql, list(chunk))
    else:
        where_sql = "WHERE b.[Close] IS NOT NULL"
        if sample_clause:
            where_sql += f" AND {sample_clause}"
        _run(where_sql, [])

    if not rows:
        return pd.DataFrame(columns=["ticker", "bar_date", "close"])
    return pd.DataFrame(
        [(r.Ticker, r.BarDate, float(r.Close) if r.Close is not None else None) for r in rows],
        columns=["ticker", "bar_date", "close"],
    )


def _ibd_raw(closes: list[float], as_of_index: int) -> float | None:
    """IBD-style weighted quarterly return ending at ``as_of_index``.

    Uses whatever trailing quarters have data, renormalising the weights. Returns ``None``
    when not even the most recent quarter is available.
    """
    if as_of_index < _QUARTER_BARS:
        return None
    num = 0.0
    den = 0.0
    for k, weight in enumerate(_QUARTER_WEIGHTS):
        end = as_of_index - k * _QUARTER_BARS
        start = as_of_index - (k + 1) * _QUARTER_BARS
        if start < 0:
            break
        c_end = closes[end]
        c_start = closes[start]
        if c_end is None or c_start is None or c_start == 0:
            break
        num += weight * (c_end / c_start - 1.0)
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
        ranks = ((valid.rank(method="average") - 1) / (len(valid) - 1) * 98 + 1).round()
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

    # ticker -> chronological close series (trimmed to the deepest lookback we need).
    closes_by_ticker: dict[str, list[float]] = {}
    for ticker, group in bars.groupby("ticker", sort=False):
        closes_by_ticker[ticker] = group["close"].tolist()[-_MAX_LOOKBACK_BARS:]

    frame = pd.DataFrame(index=pd.Index(list(closes_by_ticker.keys()), name="ticker"))

    for column, offset in SNAPSHOT_OFFSETS.items():
        raw: dict[str, float | None] = {}
        for ticker, closes in closes_by_ticker.items():
            as_of_index = len(closes) - 1 - offset
            raw[ticker] = _ibd_raw(closes, as_of_index) if as_of_index >= 0 else None
        frame[column] = _percentile_rank(pd.Series(raw))

    updated = _persist(conn, technical_table, frame, as_of)
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
        rs1d = _to_int(row.get("rs1d"))
        rs1w = _to_int(row.get("rs1w"))
        rs1m = _to_int(row.get("rs1m"))
        rs3m = _to_int(row.get("rs3m"))
        rs6m = _to_int(row.get("rs6m"))

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
