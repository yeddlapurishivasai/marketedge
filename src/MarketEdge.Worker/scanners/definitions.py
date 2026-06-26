"""Scanner family definitions (feature 011).

Each scanner is a pure function ``fn(meta, series) -> dict | None`` that returns a dict of
trigger details when the symbol matches, or ``None`` to reject. The runner supplies a
``meta`` dict (market_cap, has_options, rs_rating, company, sector, industry) and a
:class:`BarSeries` loaded once per symbol; result rows (close/day-change/volume/rvol/rs) are
assembled by the runner from the snapshot, so each scanner only encodes its filter logic and
trigger payload.

Logic mirrors the user-supplied reference scanner specification. India variants use the
``NSE_`` prefix and US variants use ``US_``; shared families are parameterised by thresholds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from .indicators import (
    BarSeries,
    IndicatorSnapshot,
    check_no_breakdown,
    day_change_pct,
    round_or_none,
)

ScannerFn = Callable[[dict, BarSeries], "dict | None"]


@dataclass
class ScannerDef:
    name: str
    market: str  # 'india' | 'us'
    label: str
    fn: ScannerFn
    needs_options: bool = False


SCANNERS: dict[str, ScannerDef] = {}


def _register(name: str, market: str, label: str, fn: ScannerFn, needs_options: bool = False) -> None:
    SCANNERS[name] = ScannerDef(name, market, label, fn, needs_options)


def scanners_for(market: str) -> list[ScannerDef]:
    mk = market.lower()
    return [d for d in SCANNERS.values() if d.market == mk]


def _nz(x: float) -> bool:
    return x is not None and not math.isnan(x) and not math.isinf(x)


# --------------------------------------------------------------------------------------
# Weekly resampling (for WEEKLY_SETUP and POCKET_PIVOT 52-week windows)
# --------------------------------------------------------------------------------------
def to_weekly(s: BarSeries) -> BarSeries | None:
    """Resample the daily series to completed weekly bars (week ending Friday)."""
    if s.n == 0:
        return None
    idx = pd.to_datetime([d for d in s.dates])
    df = pd.DataFrame(
        {"Open": s.open, "High": s.high, "Low": s.low, "Close": s.close, "Volume": s.volume},
        index=idx,
    )
    w = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    if w.empty:
        return None
    return BarSeries(
        dates=[d.date() for d in w.index],
        open=w["Open"].to_numpy(float),
        high=w["High"].to_numpy(float),
        low=w["Low"].to_numpy(float),
        close=w["Close"].to_numpy(float),
        volume=w["Volume"].to_numpy(float),
    )


# ======================================================================================
# CONTRACTION
# ======================================================================================
def _contraction(meta: dict, s: BarSeries, *, market: str) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid:
        return None
    close = snap.close
    if not (close > snap.sma(50)):
        return None
    if not check_no_breakdown(s, 20):
        return None
    if not (close > snap.sma(100) or close > snap.sma(200)):
        return None
    if not snap.sma200_trending_up_60:
        return None

    if market == "us":
        if not (snap.natr(50) > 2.0):
            return None
        if snap.pgo(50) >= 2.5 or snap.pgo(20) >= 2.5:
            return None
        if not (snap.rsi_prev1(7) < 60):
            return None
        if not (close > snap.sma(200)):
            return None
        strong_ratio, inside_ratio = 0.5, 0.75
        inside = snap.is_inside_bar
    else:
        if not (snap.avg_turnover(20) > 100_000_000 and snap.avg_turnover(100) > 100_000_000):
            return None
        if not (snap.natr(50) > 3.0 or meta.get("has_options")):
            return None
        if snap.pgo(50) >= 2.5 and snap.pgo(20) >= 2.5:
            return None
        if not (snap.rsi_prev1(7) < 60):
            return None
        if not (close > snap.ema(200)):
            return None
        strong_ratio, inside_ratio = 0.6, 0.85
        if s.last >= 1:
            inside = close < s.high[s.last - 1] and close > s.low[s.last - 1]
        else:
            inside = False

    atr1 = snap.atr1
    strong = (atr1 < snap.atr(5) * strong_ratio or atr1 < snap.atr(20) * strong_ratio
              or atr1 < snap.atr(50) * strong_ratio)
    inside_contraction = inside and (atr1 < snap.atr(5) * inside_ratio or atr1 < snap.atr(20) * inside_ratio
                                     or atr1 < snap.atr(50) * inside_ratio)
    if not (strong or inside_contraction):
        return None

    atr20 = snap.atr(20)
    return {
        "natr50": round_or_none(snap.natr(50), 2),
        "pgo50": round_or_none(snap.pgo(50), 2),
        "rsi7_prev": round_or_none(snap.rsi_prev1(7), 2),
        "atr_ratio": round_or_none(atr1 / atr20 if atr20 > 0 else None, 3),
        "is_inside_bar": bool(inside),
    }


_register("US_CONTRACTION", "us", "US Contraction", lambda m, s: _contraction(m, s, market="us"))
_register("NSE_CONTRACTION", "india", "NSE Contraction", lambda m, s: _contraction(m, s, market="india"))


# ======================================================================================
# EXTREME CONTRACTION
# ======================================================================================
def _extreme_contraction(meta: dict, s: BarSeries, *, min_pct: float = -1.0, max_pct: float = 1.0) -> dict | None:
    if s.last < 1:
        return None
    snap = IndicatorSnapshot(s)
    close = snap.close
    prev = s.close[s.last - 1]
    if prev == 0:
        return None
    if not (close > snap.ema(50) and close > snap.ema(200)):
        return None
    rs = meta.get("rs_rating")
    if rs is None or rs < 70:
        return None
    close_pct = round(((close - prev) / prev) * 100.0, 2)
    if not (min_pct <= close_pct <= max_pct):
        return None
    return {
        "previous_close": round_or_none(prev, 4),
        "close_pct": close_pct,
        "min_pct": min_pct,
        "max_pct": max_pct,
        "ema50": round_or_none(snap.ema(50), 4),
        "ema200": round_or_none(snap.ema(200), 4),
    }


_register("US_EXTREME_CONTRACTION", "us", "US Extreme Contraction", _extreme_contraction)
_register("NSE_EXTREME_CONTRACTION", "india", "NSE Extreme Contraction", _extreme_contraction)


# ======================================================================================
# BULL SNORT
# ======================================================================================
def _bull_snort(meta: dict, s: BarSeries, *, market: str) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid:
        return None
    if market == "us":
        if not (snap.rvol20 >= 3.0):
            return None
        if not (snap.volume >= 500_000):
            return None
        if not (snap.close > snap.sma(200)):
            return None
        if not snap.gap_up:
            return None
        if not (snap.close > snap.open):
            return None
        cvo = ((snap.close - snap.open) / snap.open * 100.0) if snap.open > 0 else 0.0
        return {
            "rvol20": round_or_none(snap.rvol20, 2),
            "volume": int(snap.volume),
            "close_vs_open": round_or_none(cvo, 2),
        }
    # NSE
    mc = meta.get("market_cap")
    if mc is None or mc <= 10_000_000_000:
        return None
    if s.last < 1:
        return None
    prev = s.close[s.last - 1]
    if not (snap.rvol20 >= 3.0):
        return None
    if not (snap.avg_turnover(20) > 50_000_000):
        return None
    if not (snap.close > snap.sma(200) or snap.close > snap.ema(200)):
        return None
    if not snap.gap_up:
        return None
    if not (snap.close > prev):
        return None
    return {
        "rvol20": round_or_none(snap.rvol20, 2),
        "avg_turnover_20": round_or_none(snap.avg_turnover(20), 2),
        "market_cap_cr": round_or_none(mc / 10_000_000.0, 2),
    }


_register("US_BULL_SNORT", "us", "US Bull Snort", lambda m, s: _bull_snort(m, s, market="us"))
_register("NSE_BULL_SNORT", "india", "NSE Bull Snort", lambda m, s: _bull_snort(m, s, market="india"))


# ======================================================================================
# HIGH / LOW TIGHT FLAG (shared)
# ======================================================================================
@dataclass
class _FlagCfg:
    market_cap_min: float
    cap_divisor: float
    cap_label: str
    price_min: float
    volume_min: float
    turnover_min: float
    ratio40_min: float
    ratio40_max: float | None
    high52_ratio_min: float


def _tight_flag(meta: dict, s: BarSeries, *, cfg: _FlagCfg) -> dict | None:
    mc = meta.get("market_cap")
    if mc is None:
        return None
    snap = IndicatorSnapshot(s)
    if not snap.is_valid:
        return None
    last = s.last
    if last < 251:
        return None
    close = snap.close
    close40 = s.close[last - 40]
    start52 = max(0, last - 252 + 1)
    high252 = float(np.max(s.high[start52:last + 1]))
    cur_vol = snap.volume
    cap_units = mc / cfg.cap_divisor
    ratio40 = close / close40 if close40 != 0 else float("nan")
    ratio52 = close / high252 if high252 != 0 else float("nan")

    if not (cap_units >= cfg.market_cap_min):
        return None
    if not (close >= cfg.price_min):
        return None
    if not (cur_vol >= cfg.volume_min):
        return None
    if not (close * cur_vol >= cfg.turnover_min):
        return None
    if not (ratio40 >= cfg.ratio40_min):
        return None
    if cfg.ratio40_max is not None and not (ratio40 <= cfg.ratio40_max):
        return None
    if not (ratio52 >= cfg.high52_ratio_min):
        return None
    return {
        cfg.cap_label: round_or_none(cap_units, 2),
        "close_vs_40d": round_or_none(ratio40, 2),
        "close_vs_52w_high": round_or_none(ratio52, 2),
    }


_US_HTF = _FlagCfg(300.0, 1_000_000, "market_cap_millions", 10.0, 200_000, 5_000_000, 1.8, None, 0.90)
_NSE_HTF = _FlagCfg(1000.0, 10_000_000, "market_cap_cr", 100.0, 200_000, 100_000_000, 1.8, None, 0.90)
_US_LTF = _FlagCfg(300.0, 1_000_000, "market_cap_millions", 10.0, 200_000, 5_000_000, 1.6, 1.8, 0.90)
_NSE_LTF = _FlagCfg(1000.0, 10_000_000, "market_cap_cr", 100.0, 200_000, 100_000_000, 1.6, 1.8, 0.90)

_register("US_HIGH_TIGHT_FLAG", "us", "US High Tight Flag", lambda m, s: _tight_flag(m, s, cfg=_US_HTF))
_register("NSE_HIGH_TIGHT_FLAG", "india", "NSE High Tight Flag", lambda m, s: _tight_flag(m, s, cfg=_NSE_HTF))
_register("US_LOW_TIGHT_FLAG", "us", "US Low Tight Flag", lambda m, s: _tight_flag(m, s, cfg=_US_LTF))
_register("NSE_LOW_TIGHT_FLAG", "india", "NSE Low Tight Flag", lambda m, s: _tight_flag(m, s, cfg=_NSE_LTF))


# ======================================================================================
# POCKET PIVOT
# ======================================================================================
def _pocket_pivot(meta: dict, s: BarSeries, *, market: str) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid:
        return None
    if meta.get("market_cap") is None:
        return None
    last = s.last
    if last < 30:
        return None
    close = snap.close
    cur_vol = snap.volume
    mc_millions = meta["market_cap"] / 1_000_000

    weekly = to_weekly(s)
    if weekly is None or weekly.n < 52:
        return None
    wk_close = weekly.close[-52:]
    wk_low = weekly.low[-52:]
    min_weekly_low_52 = float(np.min(wk_low))
    max_weekly_close_52 = float(np.max(wk_close))
    if math.isnan(min_weekly_low_52) or math.isnan(max_weekly_close_52):
        return None

    avg_vol30 = snap.avg_vol(30)
    ema20_vol = float(pd.Series(s.volume).ewm(span=20, adjust=False).mean().to_numpy()[last])
    higher_than_prev10 = last >= 10 and all(cur_vol > s.volume[i] for i in range(last - 10, last))
    volume_above_ema20 = cur_vol > ema20_vol
    # default config: require previous-ten OR ema20
    volume_rule = higher_than_prev10 or volume_above_ema20

    checks = [
        close > 30.0,
        mc_millions > 1000,
        close > snap.sma(50),
        close > (snap.high + snap.low) / 2,
        avg_vol30 > 10000,
        close > snap.sma(20),
        close > s.high[last - 10],
        close > snap.ema(150),
        close > snap.ema(200),
        snap.ema(150) > snap.ema(200),
        s.ema(200)[last] > s.ema(200)[last - 21],
        snap.ema(50) > snap.ema(150),
        snap.ema(50) > snap.ema(200),
        volume_rule,
        min_weekly_low_52 * 1.3 < close,
        close <= max_weekly_close_52 * 1.25,
    ]
    if not all(bool(c) for c in checks):
        return None
    return {
        "volume_millions": round_or_none(cur_vol / 1_000_000, 2),
        "avg_volume30_shares": round_or_none(avg_vol30, 0),
        "market_cap_millions": round_or_none(mc_millions, 2),
        "weekly_min_low_52": round_or_none(min_weekly_low_52, 2),
        "weekly_max_close_52": round_or_none(max_weekly_close_52, 2),
    }


_register("US_POCKET_PIVOT", "us", "US Pocket Pivot", lambda m, s: _pocket_pivot(m, s, market="us"))
_register("NSE_POCKET_PIVOT", "india", "NSE Pocket Pivot", lambda m, s: _pocket_pivot(m, s, market="india"))


# ======================================================================================
# SETUP
# ======================================================================================
def _us_setup(meta: dict, s: BarSeries) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid:
        return None
    last = s.last
    if last < 60:
        return None
    close = snap.close
    prev = s.close[last - 1]
    adv_turnover30_m = snap.avg_turnover(30) / 1_000_000

    ma_structure = snap.sma(20) >= snap.sma(50)
    advol = adv_turnover30_m > 3.0
    breakdown = close >= snap.sma(50) or s.sma(50)[last] >= s.sma(50)[last - 20]
    above_sma50 = close > snap.sma(50)
    above_long = close > snap.sma(100) or close > snap.sma(200)
    trend20 = snap.trend_ratio(s.sma(200), 20) >= 0.9 or snap.trend_ratio(s.sma(100), 20) >= 0.9
    trend60 = snap.trend_ratio(s.sma(200), 60) >= 0.9
    price = close > 10.0
    atr20, atr50, atr1 = snap.atr(20), snap.atr(50), snap.atr1
    atr_expansion = (atr20 > 0 and atr1 > atr20 * 0.65) or (atr50 > 0 and atr1 > atr50 * 0.65)
    candle = (close > prev and snap.candle_close_position > 0.5) or snap.candle_close_position > 0.7
    pgo = snap.pgo(50) < 2.5 or snap.pgo(20) < 2.5
    atr50_floor = atr50 > 2.0

    if not all([ma_structure, advol, breakdown, above_sma50, above_long, trend20, trend60,
                price, atr_expansion, candle, pgo, atr50_floor]):
        return None
    return {
        "pgo50": round_or_none(snap.pgo(50), 2),
        "pgo20": round_or_none(snap.pgo(20), 2),
        "atr20_ratio": round_or_none(atr1 / atr20 if atr20 > 0 else None, 2),
        "atr50_ratio": round_or_none(atr1 / atr50 if atr50 > 0 else None, 2),
        "close_position": round_or_none(snap.candle_close_position, 2),
        "avg_turnover30_millions": round_or_none(adv_turnover30_m, 2),
    }


def _nse_setup(meta: dict, s: BarSeries) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or s.last < 1:
        return None
    last = s.last
    close = snap.close
    prev = s.close[last - 1]
    if not (snap.avg_turnover(20) > 50_000_000 and snap.avg_turnover(50) > 50_000_000):
        return None

    new_listing = snap.bar_count < 80
    short_tol = 0.001

    above_short = close >= snap.sma(10) * (1 - short_tol) and close >= snap.sma(20) * (1 - short_tol)
    short_stack = snap.sma(10) > snap.sma(20)
    close_up = close > prev * (1 - 0.001)
    atr20, atr1 = snap.atr(20), snap.atr1
    atr_expansion = atr20 > 0 and atr1 >= atr20 * 0.5
    close_pos = snap.candle_close_position >= 0.3

    if new_listing:
        sma20_prev = s.sma(20)[last - 1] if last >= 1 else float("nan")
        trend_ok = (snap.sma(20) >= s.sma(20)[last - 20]) if last >= 20 else (snap.sma(20) >= sma20_prev)
    else:
        trend_ok = snap.sma20_above_sma50
        if not (close >= snap.sma(50) or s.sma(50)[last] >= s.sma(50)[last - 20]):
            return None

    if not all([trend_ok, above_short, short_stack, close_up, atr_expansion, close_pos]):
        return None
    return {
        "branch": "new_listing" if new_listing else "established",
        "bar_count": snap.bar_count,
        "close_position": round_or_none(snap.candle_close_position, 2),
        "atr_ratio": round_or_none(atr1 / atr20 if atr20 > 0 else None, 2),
        "avg_turnover_20": round_or_none(snap.avg_turnover(20), 2),
        "avg_turnover_50": round_or_none(snap.avg_turnover(50), 2),
    }


_register("US_SETUP", "us", "US Setup", _us_setup)
_register("NSE_SETUP", "india", "NSE Setup", _nse_setup)


# ======================================================================================
# WEEKLY SETUP
# ======================================================================================
def _weekly_setup(meta: dict, s: BarSeries, *, market: str) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or s.last < 50:
        return None
    advol20 = snap.avg_turnover(20)
    advol50 = snap.avg_turnover(50)
    if not (advol20 > 50_000_000 and advol50 > 50_000_000):
        return None
    w = to_weekly(s)
    if w is None or w.n < 21:
        return None
    wsnap = IndicatorSnapshot(w)
    wlast = w.last
    wclose = wsnap.close
    wprev = w.close[wlast - 1]
    wsma10, wsma20, wsma50 = wsnap.sma(10), wsnap.sma(20), wsnap.sma(50)
    watr20 = wsnap.atr(20)
    wtr = w._tr[wlast]
    weekly_atr_ratio = wtr / watr20 if watr20 and watr20 > 0 else float("nan")
    rng = wsnap.high - wsnap.low
    weekly_close_pos = (wclose - wsnap.low) / rng if rng != 0 else 0.0

    breakdown = wclose >= wsma50 or wlast < 20 or w.sma(50)[wlast] >= w.sma(50)[wlast - 20]
    checks = [
        wsma20 >= wsma50,
        breakdown,
        wclose > wsma10,
        wclose > wsma20,
        wsma10 > wsma20,
        wclose > wprev,
        weekly_atr_ratio > 0.6,
        weekly_close_pos > 0.4,
    ]
    if not all(bool(c) for c in checks):
        return None
    return {
        "advol20": round_or_none(advol20, 2),
        "advol50": round_or_none(advol50, 2),
        "weekly_atr_ratio": round_or_none(weekly_atr_ratio, 2),
        "weekly_close_position": round_or_none(weekly_close_pos, 2),
    }


_register("US_WEEKLY_SETUP", "us", "US Weekly Setup", lambda m, s: _weekly_setup(m, s, market="us"))
_register("NSE_WEEKLY_SETUP", "india", "NSE Weekly Setup", lambda m, s: _weekly_setup(m, s, market="india"))


# ======================================================================================
# SHORT CANDIDATES (NSE_CSS) - NSE only, requires F&O
# ======================================================================================
def _short_candidates(meta: dict, s: BarSeries) -> dict | None:
    if not meta.get("has_options"):
        return None
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or snap.bar_count < 50:
        return None
    last = s.last
    close = snap.close
    sma20, sma50, sma200 = snap.sma(20), snap.sma(50), snap.sma(200)
    rsi5 = snap.rsi(5)
    atr50 = snap.atr(50)

    def strictly_rising(arr: np.ndarray) -> bool:
        if last < 20:
            return False
        return all(arr[i] > arr[i - 1] for i in range(last - 20 + 1, last + 1))

    sma20_up = strictly_rising(s.sma(20))
    sma50_up = strictly_rising(s.sma(50))
    if sma20_up and sma50_up:
        return None
    if not (rsi5 > 30.0):
        return None
    if close < sma50 - atr50 and close < sma20 - atr50:
        return None
    # above all major averages in last 15 (requires >=200 bars)
    above_all_15 = False
    if last >= 199:
        sma200_arr = s.sma(200)
        above_all_15 = all(
            s.close[i] > s.sma(20)[i] and s.close[i] > s.sma(50)[i] and s.close[i] > sma200_arr[i]
            for i in range(max(199, last - 15 + 1), last + 1)
        )
    if above_all_15:
        return None
    return {
        "fno": True,
        "rsi5": round_or_none(rsi5, 2),
        "sma20_trend_up": bool(sma20_up),
        "sma50_trend_up": bool(sma50_up),
    }


_register("NSE_CSS", "india", "NSE Short Candidates", _short_candidates, needs_options=True)


# ======================================================================================
# WEEKEND SCAN
# ======================================================================================
def _weekend_scan(meta: dict, s: BarSeries, *, market_cap_min: float, require_ema200: bool, cap_divisor: float, cap_label: str) -> dict | None:
    mc = meta.get("market_cap")
    if mc is None or mc <= market_cap_min:
        return None
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or s.last < 1:
        return None
    prev = s.close[s.last - 1]
    if not (snap.close > prev):
        return None
    ema200 = snap.ema(200)
    if require_ema200 and not (_nz(ema200) and snap.close > ema200):
        return None
    return {
        "close": round_or_none(snap.close, 4),
        "prev_close": round_or_none(prev, 4),
        "ema200": round_or_none(ema200, 4),
        cap_label: round_or_none(mc / cap_divisor, 2),
    }


_register("US_WEEKEND_SCAN", "us", "US Weekend Scan",
          lambda m, s: _weekend_scan(m, s, market_cap_min=10_000_000_000, require_ema200=True,
                                     cap_divisor=1_000_000, cap_label="market_cap_millions"))
_register("NSE_WEEKEND_SCAN", "india", "NSE Weekend Scan",
          lambda m, s: _weekend_scan(m, s, market_cap_min=100_000_000_000, require_ema200=False,
                                     cap_divisor=10_000_000, cap_label="market_cap_cr"))


# ======================================================================================
# HIGHEST VOLUME
# ======================================================================================
def _highest_volume(meta: dict, s: BarSeries, *, market: str) -> dict | None:
    mc = meta.get("market_cap")
    floor = 1_000_000_000 if market == "us" else 5_000_000_000
    if mc is None or mc <= floor:
        return None
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or not snap.is_highest_volume_250:
        return None
    if day_change_pct(s) <= 0:
        return None
    cur_vol = snap.volume
    start = max(0, s.last - 250)
    prev_max = float(np.max(s.volume[start:s.last])) if s.last > 0 else 0.0

    if market == "us":
        ratio = cur_vol / prev_max if prev_max > 0 else float("nan")
        return {
            "volume": int(cur_vol),
            "prev_max_volume": int(prev_max),
            "vol_vs_max_ratio": round_or_none(ratio, 2),
            "market_cap": int(mc),
        }
    # NSE reversal-watch
    if s.last < 1 or not (snap.close > s.close[s.last - 1]):
        return None
    ema20, ema200 = snap.ema(20), snap.ema(200)
    if not (snap.close > ema20):
        return None
    if not (snap.close < ema200):
        return None
    return {
        "volume": int(cur_vol),
        "ema20_distance": round_or_none((snap.close - ema20) / ema20 * 100 if ema20 > 0 else None, 2),
        "ema200_distance": round_or_none((snap.close - ema200) / ema200 * 100 if ema200 > 0 else None, 2),
        "market_cap_cr": round_or_none(mc / 10_000_000.0, 2),
        "reversal_watch": True,
        "prev_max_volume": int(prev_max),
    }


_register("US_HIGHEST_VOLUME", "us", "US Highest Volume", lambda m, s: _highest_volume(m, s, market="us"))
_register("NSE_HIGHEST_VOLUME", "india", "NSE Highest Volume", lambda m, s: _highest_volume(m, s, market="india"))


# ======================================================================================
# SHOWING STRENGTH
# ======================================================================================
def _showing_strength(meta: dict, s: BarSeries, *, market_cap_min: float, cap_divisor: float, cap_label: str) -> dict | None:
    mc = meta.get("market_cap")
    if mc is None or mc <= market_cap_min:
        return None
    snap = IndicatorSnapshot(s)
    if s.last < 1:
        return None
    prev = s.close[s.last - 1]
    if not (snap.close > prev):
        return None
    return {
        "close": round_or_none(snap.close, 4),
        "previous_close": round_or_none(prev, 4),
        "market_cap": int(mc),
        cap_label: round_or_none(mc / cap_divisor, 2),
    }


_register("SHOWING_STRENGTH_US", "us", "US Showing Strength",
          lambda m, s: _showing_strength(m, s, market_cap_min=10_000_000_000, cap_divisor=1_000_000, cap_label="market_cap_millions"))
_register("SHOWING_STRENGTH_NSE", "india", "NSE Showing Strength",
          lambda m, s: _showing_strength(m, s, market_cap_min=100_000_000_000, cap_divisor=10_000_000, cap_label="market_cap_cr"))


# ======================================================================================
# DOUBLERS (1M / 3M / 6M)
# ======================================================================================
def _doublers(meta: dict, s: BarSeries, *, lookback_days: int, window: str, price_floor: float) -> dict | None:
    snap = IndicatorSnapshot(s)
    if not snap.is_valid or snap.close < price_floor:
        return None
    cur_close = snap.close
    target = s.dates[s.last] - timedelta(days=lookback_days)
    base_idx = None
    for i in range(s.last, -1, -1):
        if s.dates[i] <= target:
            base_idx = i
            break
    if base_idx is None:
        return None
    base_close = s.close[base_idx]
    if base_close <= 0:
        return None
    return_pct = round(((cur_close - base_close) / base_close) * 100.0, 2)
    if not (return_pct >= 100.0):
        return None
    return {
        "window": window,
        "lookback_days": lookback_days,
        "return_pct": return_pct,
        "base_date": str(s.dates[base_idx]),
        "base_close": round_or_none(base_close, 4),
        "current_close": round_or_none(cur_close, 4),
    }


for _win, _days in (("1M", 30), ("3M", 90), ("6M", 180)):
    _register(f"US_DOUBLERS_{_win}", "us", f"US Doublers {_win}",
              lambda m, s, d=_days, w=_win: _doublers(m, s, lookback_days=d, window=w, price_floor=10.0))
    _register(f"NSE_DOUBLERS_{_win}", "india", f"NSE Doublers {_win}",
              lambda m, s, d=_days, w=_win: _doublers(m, s, lookback_days=d, window=w, price_floor=50.0))
