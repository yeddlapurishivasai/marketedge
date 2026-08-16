"""Unit tests for the Squeeze Momentum indicator used by stage 2 analysis.

Run: python test_squeeze.py
"""

import numpy as np
import pandas as pd

from stage_analysis import calculate_squeeze


def _frame(closes: list[float], spread: float | list[float] = 0.5) -> pd.DataFrame:
    spreads = [spread] * len(closes) if isinstance(spread, (int, float)) else spread
    index = pd.date_range("2023-01-06", periods=len(closes), freq="W-FRI")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + s for c, s in zip(closes, spreads)],
            "Low": [c - s for c, s in zip(closes, spreads)],
            "Close": closes,
            "Volume": [1000] * len(closes),
        },
        index=index,
    )


def test_insufficient_history_returns_nulls():
    result = calculate_squeeze(_frame([100.0] * 10))
    assert result == {"squeeze_on": None, "squeeze_fired": None, "squeeze_momentum": None}, result


def test_tight_range_is_in_squeeze():
    rng = np.random.default_rng(7)
    closes = [100 + float(rng.normal(0, 0.05)) for _ in range(60)]
    result = calculate_squeeze(_frame(closes, spread=1.5))
    assert result["squeeze_on"] is True, result
    assert result["squeeze_fired"] is False, result
    assert result["squeeze_momentum"] is not None


def test_expansion_after_squeeze_fires():
    rng = np.random.default_rng(11)
    quiet = [100 + float(rng.normal(0, 0.05)) for _ in range(40)]
    # A single large upward bar expands the Bollinger Bands outside the Keltner channel.
    breakout = quiet + [140.0]
    result = calculate_squeeze(_frame(breakout, spread=[1.5] * 40 + [1.5]))
    assert result["squeeze_on"] is False, result
    assert result["squeeze_fired"] is True, result
    assert result["squeeze_momentum"] > 0, result


def test_trending_wide_range_is_not_squeezed():
    # A drifting random walk expands the Bollinger Bands well outside the Keltner channel.
    rng = np.random.default_rng(3)
    closes = list(100 + np.cumsum(rng.normal(1.0, 5.0, 60)))
    result = calculate_squeeze(_frame([float(c) for c in closes], spread=0.5))
    assert result["squeeze_on"] is False, result


def test_momentum_sign_follows_trend():
    up = calculate_squeeze(_frame([100 + i for i in range(60)]))
    down = calculate_squeeze(_frame([100 - i for i in range(60)]))
    assert up["squeeze_momentum"] > 0, up
    assert down["squeeze_momentum"] < 0, down


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All squeeze tests passed")
