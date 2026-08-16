"""Unit tests for the breakout base-quality gate (_base_metrics / _has_base).

Script-style (matches ``test_market_regime.py``); no pytest, DB, or network required.

Usage:
    python test_breakouts_base.py

Exits non-zero on the first failed assertion.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np

from scanners.breakouts import (
    _MAX_BASE_DEPTH_PCT,
    _SWING_BREAKOUT_LOOKBACK,
    _SWING_MIN_BASE_BARS,
    _base_metrics,
    _has_base,
    _is_breakout,
)
from scanners.indicators import BarSeries


def _series(highs, lows=None, closes=None, volumes=None) -> BarSeries:
    """Build a BarSeries from bar lists; sensible defaults keep tests terse.

    ``close`` defaults to the bar high (a bar closing on its high) and ``low`` to
    ``high - 0.5``; volume defaults to a flat series (irrelevant while n < 20, where the
    breakout volume check auto-passes).
    """
    highs = np.asarray(highs, dtype=float)
    n = len(highs)
    lows = np.asarray(lows, dtype=float) if lows is not None else highs - 0.5
    closes = np.asarray(closes, dtype=float) if closes is not None else highs.copy()
    volumes = np.asarray(volumes, dtype=float) if volumes is not None else np.ones(n)
    opens = closes.copy()
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return BarSeries(dates=dates, open=opens, high=highs, low=lows, close=closes, volume=volumes)


def _approx(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


# --------------------------------------------------------------------------------------
# _base_metrics
# --------------------------------------------------------------------------------------
def test_base_metrics_long_aged_pivot() -> None:
    # Pivot high (20) printed at the oldest in-window bar -> maximal age; shallow pullback.
    highs = [14, 20, 19, 19.5, 19, 18.5, 19, 18.8, 19.2, 19, 18.9, 20.5]
    lows = [h - 0.8 for h in highs]
    s = _series(highs, lows=lows)
    pivot, age, depth = _base_metrics(s, "long", 10)
    assert _approx(pivot, 20.0), pivot
    assert age == 10, age  # window is bars 1..10; the 20 is the first -> age == lookback
    # trough over the window is 18.5 - 0.8 = 17.7 -> depth = (20 - 17.7)/20 * 100
    assert _approx(depth, (20 - 17.7) / 20 * 100.0, tol=1e-4), depth


def test_base_metrics_long_fresh_high_has_small_age() -> None:
    # New high made 2 bars ago -> pivot_age == 2 (a near-straight run-up, weak base).
    highs = [19, 19, 19.2, 19, 19.1, 19, 19.2, 19, 19.1, 20, 19.9, 20.3]
    s = _series(highs)
    pivot, age, _depth = _base_metrics(s, "long", 10)
    assert _approx(pivot, 20.0), pivot
    assert age == 2, age


def test_base_metrics_repeated_high_uses_oldest_touch() -> None:
    # Resistance at 20 touched twice (bars 2 and 9). Oldest touch defines the age.
    highs = [12, 15, 20, 18, 17, 18, 17, 18, 17, 20, 18, 20.4]
    s = _series(highs)
    _pivot, age, _depth = _base_metrics(s, "long", 10)
    # window bars 1..10 -> the first 20 sits at window position 1 -> age = 10 - 1 = 9
    assert age == 9, age


def test_base_metrics_short_symmetry() -> None:
    lows = [30, 10, 12, 11, 13, 12, 11.5, 12, 11, 12, 13, 9]
    highs = [x + 1 for x in lows]
    s = _series(highs, lows=lows)
    pivot, age, depth = _base_metrics(s, "short", 10)
    assert _approx(pivot, 10.0), pivot      # lowest low in the window
    assert age == 10, age                   # the 10 is the oldest in-window low
    # peak high in window = 13 + 1 = 14 -> depth = (14 - 10)/10 * 100 = 40
    assert _approx(depth, 40.0, tol=1e-4), depth


def test_base_metrics_insufficient_history() -> None:
    s = _series([10, 11, 12, 13, 14])  # n=5 < lookback 10
    assert _base_metrics(s, "long", 10) is None


# --------------------------------------------------------------------------------------
# _has_base
# --------------------------------------------------------------------------------------
def test_has_base_accepts_aged_shallow_base() -> None:
    highs = [14, 20, 19, 19.5, 19, 18.5, 19, 18.8, 19.2, 19, 18.9, 20.5]
    lows = [h - 0.8 for h in highs]
    s = _series(highs, lows=lows)
    assert _has_base(s, "long", 10, min_base_bars=3, max_depth_pct=25.0) is True


def test_has_base_rejects_straight_uptrend() -> None:
    # A fresh N-bar high every day: pivot is yesterday's high -> age 1, no consolidation.
    highs = list(range(10, 22))  # 10,11,...,21 (n=12)
    s = _series(highs)
    _pivot, age, _depth = _base_metrics(s, "long", 10)
    assert age == 1, age
    assert _has_base(s, "long", 10, min_base_bars=3, max_depth_pct=100.0) is False


def test_has_base_age_gate_is_the_discriminator() -> None:
    # Pivot only 2 bars old but pullback shallow: fails at min_base=3, passes at min_base=1.
    highs = [19, 19, 19.2, 19, 19.1, 19, 19.2, 19, 19.1, 20, 19.9, 20.3]
    s = _series(highs)  # lows default to high-0.5 -> depth ~7.5% (well within 25%)
    assert _has_base(s, "long", 10, min_base_bars=3, max_depth_pct=25.0) is False
    assert _has_base(s, "long", 10, min_base_bars=1, max_depth_pct=25.0) is True


def test_has_base_depth_gate_rejects_deep_v() -> None:
    # Aged pivot (age 10) but a deep dip in the base -> depth exceeds the ceiling.
    highs = [14, 20, 19, 19.5, 19, 18.5, 19, 18.8, 19.2, 19, 18.9, 20.5]
    lows = [h - 0.8 for h in highs]
    lows[5] = 14.0  # deep V: (20 - 14)/20 = 30% > 25%
    s = _series(highs, lows=lows)
    assert _has_base(s, "long", 10, min_base_bars=3, max_depth_pct=25.0) is False


def test_has_base_missing_history_is_false() -> None:
    s = _series([10, 11, 12, 13, 14])
    assert _has_base(s, "long", 10, min_base_bars=3, max_depth_pct=25.0) is False


# --------------------------------------------------------------------------------------
# Interaction with _is_breakout (price+volume break) -> the gate adds real filtering
# --------------------------------------------------------------------------------------
def test_straight_uptrend_breaks_but_has_no_base() -> None:
    highs = list(range(10, 22))  # closes at highs -> today closes a fresh 10-bar high
    s = _series(highs)
    # It IS a price/volume breakout (volume auto-confirms while n < 20)...
    assert _is_breakout(s, "long", _SWING_BREAKOUT_LOOKBACK) is True
    # ...but the base gate rejects it (this is the bug the gate fixes).
    assert _has_base(s, "long", _SWING_BREAKOUT_LOOKBACK,
                     _SWING_MIN_BASE_BARS, _MAX_BASE_DEPTH_PCT) is False


def test_based_breakout_passes_both() -> None:
    highs = [14, 20, 19, 19.5, 19, 18.5, 19, 18.8, 19.2, 19, 18.9, 20.5]
    lows = [h - 0.8 for h in highs]
    s = _series(highs, lows=lows)
    assert _is_breakout(s, "long", _SWING_BREAKOUT_LOOKBACK) is True
    assert _has_base(s, "long", _SWING_BREAKOUT_LOOKBACK,
                     _SWING_MIN_BASE_BARS, _MAX_BASE_DEPTH_PCT) is True


def main() -> int:
    tests = [obj for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
