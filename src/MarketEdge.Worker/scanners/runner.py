"""Scanner run orchestration (feature 011).

A scanner job:

1. Resolves the symbol universe (``stage2`` default, or the full active ``universe``).
2. Refreshes *today's* daily bar for that universe from yfinance (so a scan run any time
   during market hours reflects the latest price), upserting into ``{Market}Bars1D``.
3. Runs one scanner (``scannerName``) or all scanners for the market, loading each symbol's
   daily bars once and evaluating every selected scanner against it.
4. Persists results idempotently per ``(ScannerName, ScanDate)`` and records hit counts on
   the ``JobRun``.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from config import Config
from db import get_connection, update_job_status

from .definitions import SCANNERS, ScannerDef, scanners_for
from .indicators import IndicatorSnapshot, day_change_pct, load_bars, round_or_none

logger = logging.getLogger(__name__)

_STOCKS = {"india": "IndianStocks", "us": "USStocks"}
_SECTORS = {"india": "IndianSectors", "us": "USSectors"}
_TECH = {"india": "IndianTickerTechnical", "us": "USTickerTechnical"}
_STAGE = {"india": "IndianStageAnalysisResults", "us": "USStageAnalysisResults"}
_RESULTS = {"india": "IndianTechnicalScannerResults", "us": "USTechnicalScannerResults"}

# One generous load covers every daily scanner (max lookback ~520 for weekly aggregation);
# indicator values at the last bar are unaffected by extra leading history.
_MAX_BARS = 520


def _table(mapping: dict, market: str) -> str:
    t = mapping.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


def _to_yf(symbol: str, market: str) -> str:
    if symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS" if market.lower() == "india" else symbol


def load_universe(conn, market: str, universe: str) -> list[dict[str, Any]]:
    """Return universe metadata rows: symbol, company, sector, industry, market_cap, has_options, rs_rating."""
    stocks = _table(_STOCKS, market)
    sectors = _table(_SECTORS, market)
    tech = _table(_TECH, market)

    where = ""
    params: list[Any] = []
    if universe == "stage2":
        stage = _table(_STAGE, market)
        where = f"""
            WHERE s.Symbol IN (
                SELECT r.Symbol FROM dbo.{stage} r
                WHERE r.IsStage2 = 1
                  AND r.WeekNumber = (SELECT MAX(WeekNumber) FROM dbo.{stage} WHERE IsStage2 = 1)
            )
        """

    sql = f"""
        SELECT s.Symbol, s.CompanyName, s.BroadSector, s.IsFno, sec.SectorName,
               t.MarketCap, t.Rs
        FROM dbo.{stocks} s
        JOIN dbo.{sectors} sec ON s.SectorId = sec.Id
        OUTER APPLY (
            SELECT TOP 1 MarketCap, Rs FROM dbo.{tech}
            WHERE Ticker = s.Symbol ORDER BY AsOfDate DESC
        ) t
        {where}
        ORDER BY s.Symbol
    """
    rows = conn.cursor().execute(sql, params).fetchall()
    out = []
    for r in rows:
        out.append({
            "symbol": r.Symbol,
            "company": r.CompanyName,
            "sector": r.SectorName,
            "industry": r.BroadSector,
            "market_cap": int(r.MarketCap) if r.MarketCap is not None else None,
            "has_options": bool(r.IsFno),
            "rs_rating": int(r.Rs) if r.Rs is not None else None,
        })
    return out


def refresh_today_bars(conn, market: str, symbols: list[str]) -> int:
    """Fetch the latest daily bar for each symbol and upsert into {Market}Bars1D.

    Uses a short 5-day window per batch (cheap) and writes only the most recent bar.
    """
    if not symbols:
        return 0
    bars_table = _table({"india": "IndianBars1D", "us": "USBars1D"}, market)
    batch_size = max(Config.YFINANCE_BATCH_SIZE, 1)
    total_batches = math.ceil(len(symbols) / batch_size)
    staged: list[tuple] = []

    for bi in range(total_batches):
        batch = symbols[bi * batch_size:(bi + 1) * batch_size]
        yf_syms = [_to_yf(s, market) for s in batch]
        try:
            raw = yf.download(
                tickers=yf_syms, period="5d", interval="1d", group_by="ticker",
                auto_adjust=False, progress=False, threads=False,
            )
        except Exception as exc:  # noqa: BLE001 - skip batch, keep going
            logger.warning("Today-bar refresh batch %s/%s failed: %s", bi + 1, total_batches, exc)
            continue
        for original, yf_sym in zip(batch, yf_syms):
            frame = _extract_frame(raw, yf_sym)
            if frame is None or frame.empty:
                continue
            last = frame.dropna(subset=["Close"]).tail(1)
            if last.empty:
                continue
            ts = last.index[-1]
            row = last.iloc[-1]
            staged.append((
                original,
                pd.Timestamp(ts).date(),
                _f(row.get("Open")), _f(row.get("High")), _f(row.get("Low")),
                _f(row.get("Close")),
                int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
                _f(row.get("Adj Close")),
            ))
        if bi < total_batches - 1:
            time.sleep(Config.YFINANCE_BATCH_DELAY)

    if staged:
        _upsert_bars(conn, bars_table, staged)
    return len(staged)


def _extract_frame(raw: pd.DataFrame, yf_symbol: str):
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        if yf_symbol in set(lvl0):
            return raw[yf_symbol]
        try:
            return raw.xs(yf_symbol, axis=1, level=1)
        except Exception:
            return None
    return raw


def _f(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 4)


def _upsert_bars(conn, table: str, rows: list[tuple]) -> None:
    cur = conn.cursor()
    cur.execute("IF OBJECT_ID('tempdb..#TodayBars') IS NOT NULL DROP TABLE #TodayBars;")
    cur.execute(
        """
        CREATE TABLE #TodayBars (
            Ticker NVARCHAR(30) NOT NULL, BarDate DATE NOT NULL,
            [Open] DECIMAL(18,4) NULL, High DECIMAL(18,4) NULL, Low DECIMAL(18,4) NULL,
            [Close] DECIMAL(18,4) NULL, Volume BIGINT NULL, AdjClose DECIMAL(18,4) NULL);
        """
    )
    cur.fast_executemany = True
    cur.executemany(
        "INSERT INTO #TodayBars (Ticker, BarDate, [Open], High, Low, [Close], Volume, AdjClose) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute(
        f"""
        MERGE dbo.{table} AS tgt USING #TodayBars AS src
        ON tgt.Ticker = src.Ticker AND tgt.BarDate = src.BarDate
        WHEN MATCHED THEN UPDATE SET [Open]=src.[Open], High=src.High, Low=src.Low,
            [Close]=src.[Close], Volume=src.Volume, AdjClose=src.AdjClose
        WHEN NOT MATCHED THEN INSERT (Ticker, BarDate, [Open], High, Low, [Close], Volume, AdjClose)
            VALUES (src.Ticker, src.BarDate, src.[Open], src.High, src.Low, src.[Close], src.Volume, src.AdjClose);
        """
    )
    cur.execute("DROP TABLE #TodayBars;")
    conn.commit()


def _build_row(meta: dict, s, triggers: dict) -> dict:
    snap = IndicatorSnapshot(s)
    return {
        "symbol": meta["symbol"],
        "company": meta.get("company"),
        "sector": meta.get("sector"),
        "industry": meta.get("industry"),
        "close_price": round_or_none(snap.close, 4),
        "day_change_pct": day_change_pct(s),
        "volume": int(snap.volume) if not math.isnan(snap.volume) else None,
        "rel_volume": round_or_none(snap.rvol20, 2),
        "rs_rating": meta.get("rs_rating"),
        "triggers": triggers,
    }


def persist_results(conn, market: str, scanner_name: str, scan_date: date, run_id: int, rows: list[dict]) -> int:
    """Idempotent write: delete existing rows for (scanner, scan_date) then insert."""
    table = _table(_RESULTS, market)
    cur = conn.cursor()
    cur.execute(
        f"DELETE FROM dbo.{table} WHERE ScannerName = ? AND ScanDate = ?",
        scanner_name, scan_date,
    )
    if rows:
        cur.fast_executemany = True
        cur.executemany(
            f"""INSERT INTO dbo.{table}
                (RunId, ScannerName, ScanDate, Symbol, CompanyName, SectorName, Industry,
                 ClosePrice, DayChangePct, Volume, RelVolume, RsRating, TriggerDetails)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id, scanner_name, scan_date, r["symbol"], r.get("company"),
                    r.get("sector"), r.get("industry"), r.get("close_price"),
                    r.get("day_change_pct"), r.get("volume"), r.get("rel_volume"),
                    r.get("rs_rating"), json.dumps(r.get("triggers"), default=str),
                )
                for r in rows
            ],
        )
    conn.commit()
    return len(rows)


def run_scanner_job(payload: dict) -> None:
    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    scanner_name = payload.get("scannerName")  # None => all scanners (pre-close scan)
    # Paper breakouts are opened/managed only on the pre-close scan (the daily all-scanner
    # run, scannerName=None). Intraday single-scanner runs and ad-hoc/local test runs
    # evaluate scanners but must NOT touch the paper-breakout blotter.
    is_preclose_scan = scanner_name is None
    universe = (payload.get("universe") or "stage2").lower()
    scan_date = date.today()

    conn = get_connection()
    try:
        update_job_status(conn, run_id, "running", progress=0, started_at=datetime.now(timezone.utc).replace(tzinfo=None))

        if scanner_name:
            defs = [SCANNERS[scanner_name]] if scanner_name in SCANNERS else []
            if not defs:
                raise ValueError(f"Unknown scanner: {scanner_name}")
        else:
            defs = scanners_for(market)

        meta_rows = load_universe(conn, market, universe)
        symbols = [m["symbol"] for m in meta_rows]
        logger.info("Scanner run %s: market=%s universe=%s symbols=%s scanners=%s",
                    run_id, market, universe, len(symbols), [d.name for d in defs])

        refreshed = refresh_today_bars(conn, market, symbols)
        logger.info("Scanner run %s: refreshed %s today-bars", run_id, refreshed)

        # Refresh the TickerTechnical snapshot (prices, 52W, market cap) from the just-updated
        # bars by reusing the ingestion 'technical' step, so scoring and Stock Lookup reflect
        # today's data rather than the last full ingestion. Best-effort: never abort the scan.
        tech_refreshed = False
        try:
            from ingestion_runner import run_steps_inline
            failed, _tail = run_steps_inline(market, symbols, ["technical"])
            tech_refreshed = not failed
            logger.info("Scanner run %s: technical snapshot refresh ok=%s", run_id, tech_refreshed)
        except Exception:  # noqa: BLE001 - technical refresh must never abort the scan
            logger.exception("Scanner run %s: technical snapshot refresh failed", run_id)

        # Load each symbol's series once; evaluate all selected scanners against it.
        results_by_scanner: dict[str, list[dict]] = {d.name: [] for d in defs}
        series_cache: dict[str, Any] = {}
        processed = 0
        for meta in meta_rows:
            series = load_bars(conn, market, meta["symbol"], _MAX_BARS, end_date=scan_date)
            series_cache[meta["symbol"]] = series
            if series is not None and series.n >= 2:
                for d in defs:
                    try:
                        triggers = d.fn(meta, series)
                    except Exception:  # noqa: BLE001 - one symbol/scanner never aborts the run
                        logger.debug("Scanner %s failed on %s", d.name, meta["symbol"], exc_info=True)
                        continue
                    if triggers is not None:
                        results_by_scanner[d.name].append(_build_row(meta, series, triggers))
            processed += 1
            if processed % 200 == 0:
                pct = int(processed / max(len(meta_rows), 1) * 90)
                update_job_status(conn, run_id, "running", progress=pct)

        total_hits = 0
        per_scanner: dict[str, int] = {}
        for d in defs:
            rows = results_by_scanner[d.name]
            persist_results(conn, market, d.name, scan_date, run_id, rows)
            per_scanner[d.name] = len(rows)
            total_hits += len(rows)

        # --- Paper-breakout engine ---
        # Symbols flagged by any scanner this run are breakout candidates.
        meta_by_sym = {m["symbol"]: m for m in meta_rows}
        flagged: dict[str, dict] = {}
        for s_name, rows in results_by_scanner.items():
            for r in rows:
                sym = r["symbol"]
                f = flagged.get(sym)
                if f is None:
                    f = {
                        "scanners": [],
                        "company": r.get("company"),
                        "is_fno": bool(meta_by_sym.get(sym, {}).get("has_options")),
                    }
                    flagged[sym] = f
                f["scanners"].append(s_name)

        breakout_metrics: dict[str, int] = {}
        try:
            from .breakouts import run_breakout_engine
            # Only the pre-close scan opens/manages paper breakouts (see is_preclose_scan).
            if is_preclose_scan:
                breakout_metrics = run_breakout_engine(conn, market, scan_date, flagged, series_cache)
            else:
                breakout_metrics = {"skipped": "not pre-close scan"}
                logger.info("Scanner run %s: breakout engine skipped (single-scanner run '%s')",
                            run_id, scanner_name)
            logger.info("Scanner run %s: breakouts=%s", run_id, breakout_metrics)
        except Exception:  # noqa: BLE001 - breakout handling must never abort the scan
            logger.exception("Scanner run %s: breakout engine failed", run_id)

        metrics = {
            "market": market,
            "universe": universe,
            "symbols": len(symbols),
            "refreshedBars": refreshed,
            "technicalRefreshed": tech_refreshed,
            "scanners": len(defs),
            "totalHits": total_hits,
            "perScanner": per_scanner,
            "scanDate": str(scan_date),
            "breakouts": breakout_metrics,
        }
        update_job_status(conn, run_id, "completed", progress=100, metrics=metrics,
                          completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        logger.info("Scanner run %s completed: %s hits across %s scanners", run_id, total_hits, len(defs))
    except Exception as exc:
        logger.exception("Scanner run %s failed", run_id)
        try:
            update_job_status(conn, run_id, "failed", error=str(exc),
                              completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        except Exception:
            pass
        raise
    finally:
        conn.close()
