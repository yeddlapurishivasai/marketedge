"""Paper-trade engine driven by scanner breakouts.

Runs as part of every scanner job (the 15-minute scheduled run). On each run it:

1. **Manages** every open paper trade against the latest daily bar:
   * updates ``LastPrice`` / ``PnLPct`` and the MFE/MAE excursions,
   * for **swing** trades: once price moves ``+1R`` the stop jumps to breakeven and
     thereafter trails the 10-period SMA,
   * for **positional** trades: the stop rides the 20-EMA and the trade exits on a
     close below it,
   * closes a trade (capturing realised PnL%) when price violates the current stop.
2. **Opens** new trades for symbols flagged by a scanner *this run* that do not already
   have an active trade of that type -- one swing (6% stop) and one positional (20-EMA
   stop) per qualifying breakout. F&O names flagged by a short scanner open short trades.
3. **Tags** every scanner that flagged an already-active trade onto that trade
   (``FlaggedScannersJson`` + ``ScannerHitCount``) without opening a duplicate entry.

Realised + open-in-profit trades feed back into :mod:`scanners.scoring` as the
track-record evidence group.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Any

from .indicators import load_bars

logger = logging.getLogger(__name__)

_TRADES = {"india": "IndianTrades", "us": "USTrades"}

_SWING_STOP_PCT = 0.06      # initial swing stop
_POS_STOP_FALLBACK = 0.08   # if 20-EMA unavailable / above price
_TRAIL_MA = 10             # swing trail SMA period
_POS_EMA = 20              # positional stop / exit EMA
_MAX_BARS = 260

# Scanners that express a bearish/short setup.
_SHORT_SCANNERS = {"NSE_CSS"}


def _t(market: str) -> str:
    t = _TRADES.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_short_scanner(name: str) -> bool:
    return name in _SHORT_SCANNERS or "SHORT" in name.upper() or name.upper().endswith("_CSS")


def _series_metrics(series) -> dict[str, float | None]:
    """Last close plus SMA10 / EMA20 at the last bar."""
    if series is None or series.n < 1:
        return {"close": None, "sma10": None, "ema20": None}
    last = series.last
    sma10 = float(series.sma(_TRAIL_MA)[last]) if series.n >= _TRAIL_MA else None
    ema20 = float(series.ema(_POS_EMA)[last]) if series.n >= _POS_EMA else None
    sma10 = None if (sma10 is not None and math.isnan(sma10)) else sma10
    ema20 = None if (ema20 is not None and math.isnan(ema20)) else ema20
    return {"close": float(series.close[last]), "sma10": sma10, "ema20": ema20}


def _get_active_trades(conn, market: str) -> list[dict]:
    table = _t(market)
    rows = conn.cursor().execute(
        f"""SELECT Id, Ticker, TradeType, Direction, EntryPrice, InitialStop, CurrentStop,
                   StopBasis, RiskPerShare, MovedToBe, MfePct, MaePct, FlaggedScannersJson,
                   ScannerHitCount
            FROM dbo.{table} WHERE Status = 'active'"""
    ).fetchall()
    return [
        {
            "id": r.Id, "ticker": r.Ticker, "trade_type": r.TradeType, "direction": r.Direction,
            "entry": float(r.EntryPrice), "initial_stop": float(r.InitialStop) if r.InitialStop is not None else None,
            "current_stop": float(r.CurrentStop) if r.CurrentStop is not None else None,
            "stop_basis": r.StopBasis, "risk": float(r.RiskPerShare) if r.RiskPerShare is not None else None,
            "moved_to_be": bool(r.MovedToBe),
            "mfe": float(r.MfePct) if r.MfePct is not None else None,
            "mae": float(r.MaePct) if r.MaePct is not None else None,
            "flagged": _load_list(r.FlaggedScannersJson),
            "hit_count": int(r.ScannerHitCount or 0),
        }
        for r in rows
    ]


def _load_list(raw: Any) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _manage_trade(conn, market: str, t: dict, series, flagged_now: list[str]) -> None:
    table = _t(market)
    m = _series_metrics(series)
    last = m["close"]
    if last is None:
        return

    sign = 1.0 if t["direction"] == "long" else -1.0
    entry = t["entry"]
    pnl = (last - entry) / entry * 100.0 * sign

    mfe = max(t["mfe"] if t["mfe"] is not None else pnl, pnl)
    mae = min(t["mae"] if t["mae"] is not None else pnl, pnl)

    stop = t["current_stop"]
    basis = t["stop_basis"]
    moved = t["moved_to_be"]
    risk = t["risk"]
    exit_reason = None

    if t["trade_type"] == "swing":
        # +1R -> breakeven, then trail the 10-SMA
        if not moved and risk:
            target = entry + risk if t["direction"] == "long" else entry - risk
            reached = last >= target if t["direction"] == "long" else last <= target
            if reached:
                stop = entry
                basis = "breakeven"
                moved = True
        if moved and m["sma10"] is not None:
            if t["direction"] == "long" and m["sma10"] > stop:
                stop, basis = m["sma10"], "trail10"
            elif t["direction"] == "short" and m["sma10"] < stop:
                stop, basis = m["sma10"], "trail10"
        if stop is not None:
            hit = last <= stop if t["direction"] == "long" else last >= stop
            if hit:
                exit_reason = "sl_hit" if basis == "pct6" else "trail"
    else:  # positional: stop rides the 20-EMA, exit on close beyond it
        if m["ema20"] is not None:
            if t["direction"] == "long":
                if m["ema20"] > (stop or 0):
                    stop, basis = m["ema20"], "ema20"
                if last < m["ema20"]:
                    exit_reason = "ema_close"
            else:
                if stop is None or m["ema20"] < stop:
                    stop, basis = m["ema20"], "ema20"
                if last > m["ema20"]:
                    exit_reason = "ema_close"
        elif stop is not None:
            hit = last <= stop if t["direction"] == "long" else last >= stop
            if hit:
                exit_reason = "sl_hit"

    # Tag any scanners that flagged this active trade again (no new entry).
    new_flagged = sorted(set(t["flagged"]) | set(flagged_now))
    hit_count = t["hit_count"] + len([s for s in flagged_now if s not in t["flagged"]])

    cur = conn.cursor()
    if exit_reason is not None:
        cur.execute(
            f"""UPDATE dbo.{table} SET Status='closed', ExitAt=?, ExitPrice=?, ExitReason=?,
                    LastPrice=?, PnLPct=?, CurrentStop=?, StopBasis=?, MovedToBe=?,
                    MfePct=?, MaePct=?, FlaggedScannersJson=?, ScannerHitCount=?, UpdatedAt=GETUTCDATE()
                WHERE Id=?""",
            _now(), round(last, 4), exit_reason, round(last, 4), round(pnl, 4),
            round(stop, 4) if stop is not None else None, basis, int(moved),
            round(mfe, 4), round(mae, 4), json.dumps(new_flagged), hit_count, t["id"],
        )
        logger.info("Closed %s %s %s @ %.2f pnl=%.2f%% (%s)",
                    market, t["trade_type"], t["ticker"], last, pnl, exit_reason)
    else:
        cur.execute(
            f"""UPDATE dbo.{table} SET LastPrice=?, PnLPct=?, CurrentStop=?, StopBasis=?, MovedToBe=?,
                    MfePct=?, MaePct=?, FlaggedScannersJson=?, ScannerHitCount=?, UpdatedAt=GETUTCDATE()
                WHERE Id=?""",
            round(last, 4), round(pnl, 4), round(stop, 4) if stop is not None else None, basis,
            int(moved), round(mfe, 4), round(mae, 4), json.dumps(new_flagged), hit_count, t["id"],
        )
    conn.commit()


def _open_trade(conn, market: str, ticker: str, company: str | None, trade_type: str,
                direction: str, entry: float, series, scanners: list[str]) -> None:
    table = _t(market)
    m = _series_metrics(series)

    if trade_type == "swing":
        if direction == "long":
            initial = entry * (1 - _SWING_STOP_PCT)
        else:
            initial = entry * (1 + _SWING_STOP_PCT)
        basis = "pct6"
    else:  # positional
        ema20 = m["ema20"]
        if direction == "long":
            initial = ema20 if (ema20 is not None and ema20 < entry) else entry * (1 - _POS_STOP_FALLBACK)
        else:
            initial = ema20 if (ema20 is not None and ema20 > entry) else entry * (1 + _POS_STOP_FALLBACK)
        basis = "ema20"

    risk = abs(entry - initial)
    entry_scanner = scanners[0] if scanners else None
    conn.cursor().execute(
        f"""INSERT INTO dbo.{table}
            (Ticker, CompanyName, TradeType, Direction, Status, EntryScanner, FlaggedScannersJson,
             ScannerHitCount, EntryAt, EntryPrice, InitialStop, CurrentStop, StopBasis, RiskPerShare,
             MovedToBe, LastPrice, PnLPct, MfePct, MaePct, CreatedAt, UpdatedAt)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, GETUTCDATE(), GETUTCDATE())""",
        ticker, company, trade_type, direction, entry_scanner, json.dumps(sorted(set(scanners))),
        len(set(scanners)), _now(), round(entry, 4), round(initial, 4), round(initial, 4), basis,
        round(risk, 4), round(entry, 4),
    )
    conn.commit()
    logger.info("Opened %s %s %s @ %.2f stop=%.2f (%s)",
                market, trade_type, ticker, entry, initial, entry_scanner)


def run_trade_engine(conn, market: str, scan_date: date,
                     flagged: dict[str, dict], series_cache: dict[str, Any]) -> dict[str, int]:
    """Manage open trades and open new ones for this run's breakouts.

    ``flagged`` maps symbol -> {"scanners": [names], "company": str, "is_fno": bool}.
    Returns counts {managed, opened, closed, tagged}.
    """
    active = _get_active_trades(conn, market)
    # tickers that were active at the START of this run -> never re-enter same (ticker, type) this run
    active_keys = {(t["ticker"], t["trade_type"]) for t in active}

    def _series_for(sym: str):
        s = series_cache.get(sym)
        if s is None:
            s = load_bars(conn, market, sym, _MAX_BARS, end_date=scan_date)
            series_cache[sym] = s
        return s

    managed = closed_before = 0
    for t in active:
        flagged_now = flagged.get(t["ticker"], {}).get("scanners", [])
        _manage_trade(conn, market, t, _series_for(t["ticker"]), flagged_now)
        managed += 1

    # count closes after management
    closed_now = conn.cursor().execute(
        f"SELECT COUNT(*) AS c FROM dbo.{_t(market)} WHERE Status='closed' AND CAST(UpdatedAt AS DATE)=CAST(GETUTCDATE() AS DATE)"
    ).fetchone()

    opened = 0
    for sym, info in flagged.items():
        scanners = info.get("scanners", [])
        if not scanners:
            continue
        series = _series_for(sym)
        m = _series_metrics(series)
        if m["close"] is None:
            continue
        entry = m["close"]
        is_fno = bool(info.get("is_fno"))
        has_short = any(_is_short_scanner(s) for s in scanners)
        has_long = any(not _is_short_scanner(s) for s in scanners)

        # Direction: long setups -> long; short setups -> short only for F&O names.
        plans: list[str] = []
        direction = "long"
        if has_long:
            direction = "long"
        elif has_short and is_fno:
            direction = "short"
        else:
            continue  # short setup on non-F&O -> skip (long-only universe)

        for trade_type in ("swing", "positional"):
            if (sym, trade_type) in active_keys:
                continue  # already active -> tagged during management, no new entry
            _open_trade(conn, market, sym, info.get("company"), trade_type, direction,
                        entry, series, scanners)
            opened += 1

    return {
        "managed": managed,
        "opened": opened,
        "closedToday": int(closed_now.c) if closed_now else 0,
    }
