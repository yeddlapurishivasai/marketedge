"""Paper-breakout engine driven by scanner breakouts.

Runs as part of every scanner job (the 15-minute scheduled run). On each run it:

1. **Manages** every open paper breakout against the latest daily bar:
   * updates ``LastPrice`` / ``PnLPct`` and the MFE/MAE excursions,
   * for **swing** trades: once price moves ``+1R`` the stop jumps to breakeven and
     thereafter trails the 10-period SMA,
   * for **positional** trades: the initial stop is the *further* of the 20-EMA
     and a 10%-below-entry floor, so the trade always starts with >=10% of room;
     the stop only tightens to (and trails) the 20-EMA once the EMA has risen
     above the entry (the trade is in profit). It exits on a close below the
     current stop,
   * closes a trade (capturing realised PnL%) when price violates the current stop.
2. **Opens** new trades for symbols flagged by a scanner *this run* **only once price
   actually breaks the pivot** -- above the prior resistance (highest high) for longs.
   A scanner hit alone is just a setup. One swing (10-bar pivot, 6% stop) and one
   positional (20-bar base, 10%-floor stop that later trails the 20-EMA) per qualifying
   breakout -- except a *scored* setup whose blended ConfidenceScore is below the floor
   (< _MIN_CONFIDENCE) is rejected; unscored setups (no fundamental idea yet) still open so
   they surface for review. Breakouts are long-only; short "breakdown" setups are a future feature.
   The blotter starts from a clean slate and only fills with genuine forward breakouts.
3. **Tags** every scanner that flagged an already-active trade onto that trade
   (``FlaggedScannersJson`` + ``ScannerHitCount``) without opening a duplicate entry.
4. **Records** every flagged-but-not-yet-broken setup sitting within ``_NEAR_PIVOT_BAND_PCT``
   of its pivot as a near-pivot watch candidate (``{Market}NearPivots``), refreshed each run,
   so the UI can show what's closing in on a breakout (filterable by distance).

Realised + open-in-profit trades feed back into :mod:`scanners.scoring` as the
track-record evidence group.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Any

import numpy as np

from .indicators import load_bars
from . import weights as wmod

logger = logging.getLogger(__name__)

_BREAKOUTS = {"india": "IndianBreakouts", "us": "USBreakouts"}
_NEAR_PIVOTS = {"india": "IndianNearPivots", "us": "USNearPivots"}

_SWING_STOP_PCT = 0.06      # initial swing stop
_POS_STOP_FALLBACK = 0.10   # positional floor: trade always gets >= this much room
_TRAIL_MA = 10             # swing trail SMA period
_POS_EMA = 20              # positional stop / exit EMA
_MAX_BARS = 260

# Notional capital allocated per paper position -> drives Qty and the absolute
# ("pure") profit. Round, market-appropriate sizes.
_NOTIONAL = {"india": 100_000.0, "us": 1_000.0}

# Breakout confirmation: a scanner hit is only a *setup*. A trade is opened
# only once price actually breaks the pivot -- resistance (prior highest high)
# for longs, support (prior lowest low) for shorts -- AND the breakout bar trades
# on above-average volume (a break on thin volume is not a trade). Swing trades
# break a shorter pivot; positional trades break a longer base.
_SWING_BREAKOUT_LOOKBACK = 10
_POS_BREAKOUT_LOOKBACK = 20
_VOL_AVG = 20             # average-volume lookback for breakout confirmation
_VOL_MULT = 1.5           # breakout bar volume must be >= this x average volume

# Quality floor: a *scored* setup is only tradeable if its blended confidence is at least
# this -- a scored break below it is rejected. Unscored setups (no canonical fundamental
# idea, confidence None) are still opened so they surface for fundamental review.
_MIN_CONFIDENCE = 60.0

# Near-pivot capture band: a flagged setup whose close is within this %% *below* the pivot
# (resistance for longs, support for shorts) but hasn't broken yet is recorded as a near-pivot
# candidate. We capture a wide band so the UI can filter to a tighter threshold on demand.
_NEAR_PIVOT_BAND_PCT = 15.0

# Scanners that express a bearish/short setup.
_SHORT_SCANNERS = {"NSE_CSS"}


def _t(market: str) -> str:
    t = _BREAKOUTS.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


def _npt(market: str) -> str:
    t = _NEAR_PIVOTS.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_short_scanner(name: str) -> bool:
    return name in _SHORT_SCANNERS or "SHORT" in name.upper() or name.upper().endswith("_CSS")


def _is_breakout(series, direction: str, lookback: int, idx: int | None = None) -> bool:
    """True only when the bar at ``idx`` (default latest) closes past the prior pivot.

    Long  -> close breaks **above** the highest high of the prior ``lookback`` bars
             (resistance breakout).
    Short -> close breaks **below** the lowest low of the prior ``lookback`` bars
             (support breakdown).

    The pivot window excludes the tested bar so we register a genuine break, not the
    bar making the extreme itself.
    """
    if series is None:
        return False
    if idx is None:
        idx = series.last
    if idx < lookback:
        return False
    close = float(series.close[idx])
    if math.isnan(close):
        return False
    if direction == "long":
        resistance = float(np.nanmax(series.high[idx - lookback:idx]))
        broke = not math.isnan(resistance) and close > resistance
    else:
        support = float(np.nanmin(series.low[idx - lookback:idx]))
        broke = not math.isnan(support) and close < support
    return broke and _volume_confirmed(series, idx)


def _volume_confirmed(series, idx: int) -> bool:
    """Breakout bar must trade on >= ``_VOL_MULT`` x the average volume.

    If volume history is missing/insufficient we don't block the entry.
    """
    rv = _breakout_rel_vol(series, idx)
    return True if rv is None else rv >= _VOL_MULT


def _breakout_rel_vol(series, idx: int | None = None) -> float | None:
    """Relative volume (bar volume / 20-day average volume) at ``idx`` (default latest).

    ``None`` when there isn't enough history or the data is unusable -- the breakout
    volume score then drops out of the breakout-confidence blend.
    """
    if series is None or series.n < _VOL_AVG:
        return None
    if idx is None:
        idx = series.last
    try:
        avg = float(series.avg_vol(_VOL_AVG)[idx])
        vol = float(series.volume[idx])
    except (IndexError, ValueError, TypeError):
        return None
    if math.isnan(avg) or avg <= 0 or math.isnan(vol):
        return None
    return vol / avg


def _base_accumulation(series, lookback: int) -> float | None:
    """0..1 share of base volume on up-close days over the prior ``lookback`` bars.

    >0.5 means green-day volume dominates (accumulation), <0.5 means red-day volume
    dominates (distribution). ``None`` when there isn't enough history/volume.
    """
    if series is None or series.n < lookback + 1:
        return None
    last = series.last
    up = down = 0.0
    for i in range(last - lookback + 1, last + 1):
        try:
            c = float(series.close[i]); p = float(series.close[i - 1]); v = float(series.volume[i])
        except (IndexError, ValueError, TypeError):
            continue
        if math.isnan(c) or math.isnan(p) or math.isnan(v) or v <= 0:
            continue
        if c >= p:
            up += v
        else:
            down += v
    tot = up + down
    return up / tot if tot > 0 else None


def _pivot_distance(series, direction: str, lookback: int) -> float | None:
    """Percent the latest close sits *short of* the breakout pivot, or None.

    Long  -> pivot is the prior resistance (highest high); distance = (pivot - close)/close.
    Short -> pivot is the prior support (lowest low);   distance = (close - support)/support.
    Returns the pivot and the >=0 distance only when price is still on the setup side of the
    pivot (not yet broken). A negative distance means it already broke -> not a near-pivot.
    """
    if series is None:
        return None
    idx = series.last
    if idx < lookback:
        return None
    close = float(series.close[idx])
    if math.isnan(close) or close <= 0:
        return None
    if direction == "long":
        pivot = float(np.nanmax(series.high[idx - lookback:idx]))
        if math.isnan(pivot) or pivot <= 0:
            return None
        dist = (pivot - close) / close * 100.0
    else:
        pivot = float(np.nanmin(series.low[idx - lookback:idx]))
        if math.isnan(pivot) or pivot <= 0:
            return None
        dist = (close - pivot) / pivot * 100.0
    return None if dist < 0 else (pivot, dist)


def _refresh_near_pivots(conn, market: str, scan_date: date, candidates: list[tuple]) -> int:
    """Replace the near-pivot list for ``market`` with this run's candidates (delete + insert)."""
    table = _npt(market)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM dbo.{table}")
    if candidates:
        cur.fast_executemany = True
        cur.executemany(
            f"""INSERT INTO dbo.{table}
                (Ticker, CompanyName, TradeType, Direction, FlaggedScannersJson, ScannerHitCount,
                 LastClose, PivotPrice, DistancePct, RelVolume, VolumeConfirmed, ScanDate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            candidates,
        )
    conn.commit()
    return len(candidates)


def _get_active_trades(conn, market: str) -> list[dict]:
    table = _t(market)
    rows = conn.cursor().execute(
        f"""SELECT Id, Ticker, TradeType, Direction, EntryPrice, Qty, InitialStop, CurrentStop,
                   StopBasis, RiskPerShare, MovedToBe, MfePct, MaePct, FlaggedScannersJson,
                   ScannerHitCount, EntryScanner
            FROM dbo.{table} WHERE Status = 'active'"""
    ).fetchall()
    return [
        {
            "id": r.Id, "ticker": r.Ticker, "trade_type": r.TradeType, "direction": r.Direction,
            "entry": float(r.EntryPrice), "qty": int(r.Qty) if r.Qty is not None else 0,
            "initial_stop": float(r.InitialStop) if r.InitialStop is not None else None,
            "current_stop": float(r.CurrentStop) if r.CurrentStop is not None else None,
            "stop_basis": r.StopBasis, "risk": float(r.RiskPerShare) if r.RiskPerShare is not None else None,
            "moved_to_be": bool(r.MovedToBe),
            "mfe": float(r.MfePct) if r.MfePct is not None else None,
            "mae": float(r.MaePct) if r.MaePct is not None else None,
            "flagged": _load_list(r.FlaggedScannersJson),
            "hit_count": int(r.ScannerHitCount or 0),
            "entry_scanner": r.EntryScanner,
        }
        for r in rows
    ]


def _load_list(raw: Any) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _series_metrics(series) -> dict[str, float | None]:
    """Latest close plus SMA10 / EMA20 at the last bar; ``close`` is None when no usable bars."""
    if series is None or getattr(series, "n", 0) < 1:
        return {"close": None, "sma10": None, "ema20": None}
    try:
        last = series.last
        sma10 = float(series.sma(_TRAIL_MA)[last]) if series.n >= _TRAIL_MA else None
        ema20 = float(series.ema(_POS_EMA)[last]) if series.n >= _POS_EMA else None
        sma10 = None if (sma10 is not None and math.isnan(sma10)) else sma10
        ema20 = None if (ema20 is not None and math.isnan(ema20)) else ema20
        return {"close": float(series.close[last]), "sma10": sma10, "ema20": ema20}
    except (IndexError, TypeError, ValueError):
        return {"close": None, "sma10": None, "ema20": None}


def _manage_trade(conn, market: str, t: dict, series, flagged_now: list[str]) -> None:
    table = _t(market)
    m = _series_metrics(series)
    last = m["close"]
    if last is None:
        return

    sign = 1.0 if t["direction"] == "long" else -1.0
    entry = t["entry"]
    pnl = (last - entry) / entry * 100.0 * sign
    qty = t.get("qty") or 0
    pnl_amt = (last - entry) * sign * qty

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
    else:  # positional: hold a >=10% floor, then trail the 20-EMA once in profit
        ema20 = m["ema20"]
        if ema20 is not None:
            if t["direction"] == "long":
                # Only tighten to the 20-EMA after the EMA has risen above entry
                # (trade in profit); until then the >=10% floor stop stands.
                if ema20 > entry and ema20 > (stop or 0):
                    stop, basis = ema20, "ema20"
            else:
                if ema20 < entry and (stop is None or ema20 < stop):
                    stop, basis = ema20, "ema20"
        if stop is not None:
            hit = last <= stop if t["direction"] == "long" else last >= stop
            if hit:
                exit_reason = "trail" if basis == "ema20" else "sl_hit"

    # Tag any scanners that flagged this active trade again (no new entry).
    new_flagged = sorted(set(t["flagged"]) | set(flagged_now))
    hit_count = t["hit_count"] + len([s for s in flagged_now if s not in t["flagged"]])

    cur = conn.cursor()
    if exit_reason is not None:
        cur.execute(
            f"""UPDATE dbo.{table} SET Status='closed', ExitAt=?, ExitPrice=?, ExitReason=?,
                    LastPrice=?, PnLPct=?, PnLAmount=?, CurrentStop=?, StopBasis=?, MovedToBe=?,
                    MfePct=?, MaePct=?, FlaggedScannersJson=?, ScannerHitCount=?, UpdatedAt=GETUTCDATE()
                WHERE Id=?""",
            _now(), round(last, 4), exit_reason, round(last, 4), round(pnl, 4), round(pnl_amt, 4),
            round(stop, 4) if stop is not None else None, basis, int(moved),
            round(mfe, 4), round(mae, 4), json.dumps(new_flagged), hit_count, t["id"],
        )
        logger.info("Closed %s %s %s @ %.2f pnl=%.2f%% (%s)",
                    market, t["trade_type"], t["ticker"], last, pnl, exit_reason)
        conn.commit()
        # No weight nudging: a scanner's reliability is its Wilson lower bound over the
        # paper-breakout record, recomputed every run -- this closed trade now feeds it.
        return
    else:
        cur.execute(
            f"""UPDATE dbo.{table} SET LastPrice=?, PnLPct=?, PnLAmount=?, CurrentStop=?, StopBasis=?, MovedToBe=?,
                    MfePct=?, MaePct=?, FlaggedScannersJson=?, ScannerHitCount=?, UpdatedAt=GETUTCDATE()
                WHERE Id=?""",
            round(last, 4), round(pnl, 4), round(pnl_amt, 4), round(stop, 4) if stop is not None else None, basis,
            int(moved), round(mfe, 4), round(mae, 4), json.dumps(new_flagged), hit_count, t["id"],
        )
    conn.commit()


def _open_trade(conn, market: str, ticker: str, company: str | None, trade_type: str,
                direction: str, entry: float, series, scanners: list[str],
                weights: dict[str, Any] | None = None,
                reliability: dict[str, Any] | None = None) -> dict | None:
    table = _t(market)
    m = _series_metrics(series)

    if trade_type == "swing":
        if direction == "long":
            initial = entry * (1 - _SWING_STOP_PCT)
        else:
            initial = entry * (1 + _SWING_STOP_PCT)
        basis = "pct6"
    else:  # positional
        # Give the trade a true >=10% of room: the stop is the *further* of the
        # 20-EMA and a 10%-below-entry floor. The stop only tightens to the 20-EMA
        # later, once the EMA has risen above the entry (i.e. the trade is in
        # profit) -- see _manage. This stops positional from being tighter than
        # swing when price breaks out hugging its 20-EMA.
        ema20 = m["ema20"]
        if direction == "long":
            floor = entry * (1 - _POS_STOP_FALLBACK)
            initial = min(ema20, floor) if ema20 is not None else floor
        else:
            floor = entry * (1 + _POS_STOP_FALLBACK)
            initial = max(ema20, floor) if ema20 is not None else floor
        basis = "ema20" if (ema20 is not None and initial == ema20) else "pct10"

    risk = abs(entry - initial)
    qty = max(1, int(_NOTIONAL.get(market.lower(), 100_000.0) // entry)) if entry > 0 else 0
    entry_scanner = scanners[0] if scanners else None

    # Breakout-confidence: blend the setup reliability (Wilson LB of the flagging
    # scanners, noisy-OR'd), the symbol's direction-aware fundamentals, and the
    # breakout bar's volume strength, per the profile's mix weights.
    confidence = None
    rationale_json = None
    if weights is not None:
        try:
            fund_score = wmod.symbol_fundamentals(conn, market, ticker)
            lookback = _SWING_BREAKOUT_LOOKBACK if trade_type == "swing" else _POS_BREAKOUT_LOOKBACK
            accum = _base_accumulation(series, lookback)
            vol_score = wmod.breakout_volume_score(_breakout_rel_vol(series), accum)
            confidence, rationale = wmod.breakout_confidence(
                weights, trade_type, direction, scanners, reliability, fund_score, vol_score, accum)
            # No canonical fundamental score -> no confidence at all (and no rationale shown).
            rationale_json = json.dumps(rationale, default=str) if confidence is not None else None
        except Exception:  # noqa: BLE001 - confidence is advisory, never block an entry
            logger.exception("Breakout-confidence computation failed for %s", ticker)

    # Quality floor: reject genuinely low-conviction breakouts -- a *scored* setup whose
    # blended confidence is below _MIN_CONFIDENCE. Unscored setups (confidence None, i.e. no
    # canonical fundamental idea yet) are deliberately allowed through so they surface in the
    # blotter as a worklist for fundamental review/backfill rather than being silently dropped.
    # Gated only when weights loaded; a weight-load failure (weights None) never blocks entries.
    if weights is not None and confidence is not None and confidence < _MIN_CONFIDENCE:
        logger.info("Rejected %s %s %s @ %.2f conf=%.2f < floor %.0f",
                    market, trade_type, ticker, entry, confidence, _MIN_CONFIDENCE)
        return None

    row = conn.cursor().execute(
        f"""INSERT INTO dbo.{table}
            (Ticker, CompanyName, TradeType, Direction, Status, EntryScanner, FlaggedScannersJson,
             ScannerHitCount, EntryAt, EntryPrice, Qty, InitialStop, CurrentStop, StopBasis, RiskPerShare,
             MovedToBe, LastPrice, PnLPct, PnLAmount, MfePct, MaePct, ConfidenceScore,
             ConfidenceRationaleJson, CreatedAt, UpdatedAt)
            OUTPUT INSERTED.Id
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, 0, ?, ?, GETUTCDATE(), GETUTCDATE())""",
        ticker, company, trade_type, direction, entry_scanner, json.dumps(sorted(set(scanners))),
        len(set(scanners)), _now(), round(entry, 4), qty, round(initial, 4), round(initial, 4),
        basis, round(risk, 4), round(entry, 4), confidence, rationale_json,
    ).fetchone()
    conn.commit()
    logger.info("Opened %s %s %s @ %.2f stop=%.2f qty=%d (%s) conf=%s",
                market, trade_type, ticker, entry, initial, qty, entry_scanner, confidence)
    new_id = int(row.Id) if row else None
    return {
        "id": new_id, "ticker": ticker, "trade_type": trade_type, "direction": direction,
        "entry": entry, "qty": qty, "initial_stop": initial, "current_stop": initial,
        "stop_basis": basis, "risk": risk, "moved_to_be": False, "mfe": None, "mae": None,
        "flagged": sorted(set(scanners)), "hit_count": len(set(scanners)),
    }


def run_breakout_engine(conn, market: str, scan_date: date,
                     flagged: dict[str, dict], series_cache: dict[str, Any],
                     manage_trades: bool = True) -> dict[str, int]:
    """Manage open trades and open new ones for this run's breakouts.

    ``flagged`` maps symbol -> {"scanners": [names], "company": str, "is_fno": bool}.
    The blotter starts empty and only fills with genuine forward breakouts. Returns counts.
    When ``manage_trades`` is False, the paper-breakout blotter is left untouched and only
    the near-pivot watchlist is refreshed (intraday/single-scanner runs).
    """
    active = _get_active_trades(conn, market)
    # tickers that were active at the START of this run -> never re-enter same (ticker, type) this run
    active_keys = {(t["ticker"], t["trade_type"]) for t in active}

    # Seed the editable per-profile blend weights, then load them + the per-scanner
    # reliability (Wilson LB of each scanner's paper-breakout record) once for this run.
    try:
        wmod.seed_weights(conn, market)
    except Exception:  # noqa: BLE001 - seeding must never abort the run
        logger.exception("Weight seeding failed for %s", market)
    try:
        weights = wmod.get_weights(conn, market)
    except Exception:  # noqa: BLE001
        logger.exception("Weight load failed for %s", market)
        weights = None
    try:
        from .scoring import _scanner_reliability
        reliability = _scanner_reliability(conn, market)
    except Exception:  # noqa: BLE001 - reliability is advisory for confidence only
        logger.exception("Scanner reliability load failed for %s", market)
        reliability = None

    def _series_for(sym: str):
        s = series_cache.get(sym)
        if s is None:
            s = load_bars(conn, market, sym, _MAX_BARS, end_date=scan_date)
            series_cache[sym] = s
        return s

    managed = closed_before = 0
    if manage_trades:
        for t in active:
            flagged_now = flagged.get(t["ticker"], {}).get("scanners", [])
            _manage_trade(conn, market, t, _series_for(t["ticker"]), flagged_now)
            managed += 1

    # count closes after management
    closed_now = conn.cursor().execute(
        f"SELECT COUNT(*) AS c FROM dbo.{_t(market)} WHERE Status='closed' AND CAST(UpdatedAt AS DATE)=CAST(GETUTCDATE() AS DATE)"
    ).fetchone()

    opened = setups = skipped_low_conf = 0
    near_pivots: list[tuple] = []
    for sym, info in flagged.items():
        scanners = info.get("scanners", [])
        if not scanners:
            continue
        series = _series_for(sym)
        m = _series_metrics(series)
        if m["close"] is None:
            continue
        entry = m["close"]
        # Breakouts are long-only. Short setups will become a separate "breakdowns"
        # feature later, so names flagged only by short scanners are skipped here.
        if not any(not _is_short_scanner(s) for s in scanners):
            continue
        direction = "long"

        scanner_list = sorted(set(scanners))
        rel_vol = _breakout_rel_vol(series)
        for trade_type in ("swing", "positional"):
            if (sym, trade_type) in active_keys:
                continue  # already active -> tagged during management, no new entry
            lookback = _SWING_BREAKOUT_LOOKBACK if trade_type == "swing" else _POS_BREAKOUT_LOOKBACK

            if not _is_breakout(series, direction, lookback):
                setups += 1  # flagged but no confirmed break yet -> wait
                pivot = _pivot_distance(series, direction, lookback)
                if pivot is not None and pivot[1] <= _NEAR_PIVOT_BAND_PCT:
                    near_pivots.append((
                        sym, info.get("company"), trade_type, direction,
                        json.dumps(scanner_list), len(scanner_list),
                        round(entry, 4), round(pivot[0], 4), round(pivot[1], 4),
                        round(rel_vol, 4) if rel_vol is not None else None,
                        1 if (rel_vol is not None and rel_vol >= _VOL_MULT) else 0, scan_date,
                    ))
                continue
            if manage_trades:
                trade = _open_trade(conn, market, sym, info.get("company"), trade_type, direction,
                            entry, series, scanners, weights=weights, reliability=reliability)
                if trade is not None:
                    opened += 1
                else:
                    skipped_low_conf += 1  # confirmed break but scored below the floor (rejected)

    near = _refresh_near_pivots(conn, market, scan_date, near_pivots)

    return {
        "managed": managed,
        "opened": opened,
        "nearPivots": near,
        "setupsWaiting": setups,
        "skippedLowConfidence": skipped_low_conf,
        "closedToday": int(closed_now.c) if closed_now else 0,
    }
