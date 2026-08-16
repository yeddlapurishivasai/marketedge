"""Unit tests for the Squeeze Momentum indicator used by stage 2 analysis.

Run: python test_squeeze.py
"""

import numpy as np
import pandas as pd

from stage_analysis import calculate_squeeze, squeeze_state


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


def test_array_and_frame_inputs_agree():
    # The scanner refresh feeds numpy arrays straight from BarSeries; the stage-2 path
    # feeds a DataFrame. Both must produce identical state.
    rng = np.random.default_rng(23)
    closes = [100 + float(c) for c in np.cumsum(rng.normal(0, 0.8, 80))]
    frame = _frame(closes, spread=0.9)
    from_frame = calculate_squeeze(frame)
    from_arrays = squeeze_state(
        frame["High"].to_numpy(), frame["Low"].to_numpy(), frame["Close"].to_numpy()
    )
    assert from_frame == from_arrays, (from_frame, from_arrays)


def test_mismatched_series_lengths_return_nulls():
    result = squeeze_state(np.arange(30.0), np.arange(29.0), np.arange(30.0))
    assert result == {"squeeze_on": None, "squeeze_fired": None, "squeeze_momentum": None}, result


def test_bar_series_scales_high_low_to_adjusted_close():
    # Bars are stored unadjusted except Close/AdjClose, so highs/lows must be put on the
    # adjusted scale before they are mixed with the adjusted close (true range).
    from scanners.indicators import BarSeries

    raw_close = np.array([100.0, 100.0, 50.0])
    adj_close = np.array([50.0, 50.0, 50.0])  # a 2:1 split on the last bar
    s = BarSeries(
        dates=[None, None, None],
        open=raw_close.copy(),
        high=np.array([110.0, 110.0, 55.0]),
        low=np.array([90.0, 90.0, 45.0]),
        close=adj_close,
        volume=np.array([1.0, 1.0, 1.0]),
        raw_close=raw_close,
    )
    assert list(s.adj_high()) == [55.0, 55.0, 55.0], list(s.adj_high())
    assert list(s.adj_low()) == [45.0, 45.0, 45.0], list(s.adj_low())


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("All squeeze tests passed")
