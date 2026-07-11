"""Unit tests for the market-regime pure math helpers (feature 013).

Script-style (matches ``test_e2e_worker.py``); no pytest, DB, or network required.

Usage:
    python test_market_regime.py

Exits non-zero on the first failed assertion.
"""
from __future__ import annotations

import sys

import pandas as pd

from market_regime_runner import (
    combine_regime,
    compute_benchmark_context,
    compute_breadth,
    compute_condition,
    compute_participation,
)


def _approx(a, b, tol=1e-4) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def test_participation_per_sma_denominator_and_min_bars() -> None:
    """Each SMA % uses only stocks with enough history; short series are excluded entirely."""
    closes = {
        # 210 strictly increasing closes -> last is above every SMA; SMA20>SMA50>SMA200.
        "AAA": [float(x) for x in range(1, 211)],
        # 210 strictly decreasing closes -> last below every SMA; SMA ordering inverted.
        "BBB": [float(x) for x in range(210, 0, -1)],
        # 30 increasing closes -> qualifies for SMA10/20 only (not 50/200).
        "DDD": [float(x) for x in range(1, 31)],
        # 15 closes -> below _MIN_VALID_BARS (20); must be ignored completely.
        "CCC": [float(x) for x in range(1, 16)],
    }
    p = compute_participation(closes)

    assert p["evaluated_count"] == 3, p["evaluated_count"]  # CCC excluded
    # SMA10/20 denominators include AAA, BBB, DDD; positives = AAA + DDD = 2/3.
    assert _approx(p["pct_above_sma10"], 66.67, 0.01), p["pct_above_sma10"]
    assert _approx(p["pct_above_sma20"], 66.67, 0.01), p["pct_above_sma20"]
    # SMA50/200 denominators include only AAA, BBB (DDD too short); positives = AAA = 1/2.
    assert _approx(p["pct_above_sma50"], 50.0), p["pct_above_sma50"]
    assert _approx(p["pct_above_sma200"], 50.0), p["pct_above_sma200"]
    assert _approx(p["pct_sma20_above_sma50"], 50.0), p["pct_sma20_above_sma50"]
    assert _approx(p["pct_sma50_above_sma200"], 50.0), p["pct_sma50_above_sma200"]


def test_participation_empty() -> None:
    p = compute_participation({})
    assert p["evaluated_count"] == 0
    for k in ("pct_above_sma10", "pct_above_sma20", "pct_above_sma50", "pct_above_sma200",
              "pct_sma20_above_sma50", "pct_sma50_above_sma200"):
        assert p[k] is None, (k, p[k])


def test_participation_all_below_short_history_ignored_for_long_sma() -> None:
    """A single short-history name counts nowhere it lacks history, but is still evaluated."""
    closes = {"S": [float(x) for x in range(1, 41)]}  # 40 closes: has SMA10/20, not SMA50/200
    p = compute_participation(closes)
    assert p["evaluated_count"] == 1
    assert p["pct_above_sma10"] == 100.0  # rising series, last above SMA10
    assert p["pct_above_sma50"] is None   # no stock had 50 bars
    assert p["pct_above_sma200"] is None


def test_benchmark_context_known_values() -> None:
    idx = pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-31"])
    frame = pd.DataFrame({"Close": [100.0, 150.0, 200.0], "High": [100.0, 160.0, 200.0]}, index=idx)
    ctx = compute_benchmark_context(frame)

    assert _approx(ctx["benchmark_ytd_pct"], 100.0), ctx["benchmark_ytd_pct"]
    assert _approx(ctx["benchmark_1w_pct"], 33.3333), ctx["benchmark_1w_pct"]
    assert _approx(ctx["benchmark_1m_pct"], 33.3333), ctx["benchmark_1m_pct"]
    # 2024-01-01 sits exactly 365 days before 2024-12-31 (leap year) -> base 100.
    assert _approx(ctx["benchmark_1y_pct"], 100.0), ctx["benchmark_1y_pct"]
    assert _approx(ctx["benchmark_pct_from_52w_high"], 0.0), ctx["benchmark_pct_from_52w_high"]


def test_benchmark_context_below_high() -> None:
    idx = pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-31"])
    # Peak in the middle, latest below it -> distance from 52w high is negative.
    frame = pd.DataFrame({"Close": [100.0, 200.0, 150.0], "High": [100.0, 200.0, 160.0]}, index=idx)
    ctx = compute_benchmark_context(frame)
    assert ctx["benchmark_pct_from_52w_high"] is not None
    assert ctx["benchmark_pct_from_52w_high"] < 0, ctx["benchmark_pct_from_52w_high"]


def test_benchmark_context_no_year_old_data_yields_none() -> None:
    idx = pd.to_datetime(["2024-11-01", "2024-12-01", "2024-12-31"])
    frame = pd.DataFrame({"Close": [100.0, 110.0, 120.0], "High": [100.0, 110.0, 120.0]}, index=idx)
    ctx = compute_benchmark_context(frame)
    assert ctx["benchmark_1y_pct"] is None, ctx["benchmark_1y_pct"]  # nothing >= 365 days back
    assert ctx["benchmark_ytd_pct"] is not None  # within-year base still resolves


def test_benchmark_context_empty() -> None:
    for frame in (None, pd.DataFrame()):
        ctx = compute_benchmark_context(frame)
        assert all(v is None for v in ctx.values()), ctx


# --------------------------------------------------------------------------- #
# compute_condition (§3.1) — craft close series that resolve to each label.
# --------------------------------------------------------------------------- #
def test_condition_unavailable_when_no_bars() -> None:
    c = compute_condition("^X", None, [], [])
    assert c["condition_label"] == "Unavailable" and c["condition_available"] is False, c


def test_condition_uptrend() -> None:
    closes = [float(x) for x in range(1, 211)]  # steady rise, close modestly above SMA20
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Uptrend", c["condition_label"]
    assert c["condition_tone"] == "green"
    assert c["condition_close_vs_sma20_pct"] > 0, c["condition_close_vs_sma20_pct"]


def test_condition_euphoric() -> None:
    closes = [100.0] * 209 + [130.0]  # last bar 10%+ above SMA20, not below SMA50/200
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Euphoric", c["condition_label"]


def test_condition_bearish() -> None:
    closes = [float(x) for x in range(1, 201)] + [150.0] * 10  # rose, then dip below SMA50
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Bearish", c["condition_label"]


def test_condition_pessimistic() -> None:
    closes = [float(x) for x in range(210, 0, -1)]  # steady decline, 10%+ below SMA200
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Pessimistic", c["condition_label"]


def test_condition_cautious_two_sessions_below_sma20() -> None:
    # Rising series whose last two closes dip below SMA20 but stay above SMA50.
    closes = [float(x) for x in range(1, 209)] + [185.0, 185.0]
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Cautious", c["condition_label"]
    assert c["condition_tone"] == "yellow"


def test_condition_neutral_flat() -> None:
    closes = [100.0] * 210  # perfectly flat: close == every SMA, no dominant signal
    c = compute_condition("^X", None, closes, [None] * 210)
    assert c["condition_label"] == "Neutral", c["condition_label"]


def test_condition_volume_confirmation_prevents_cautious() -> None:
    # Same shape as the cautious case, but a volume spike on the last bar confirms recovery.
    closes = [float(x) for x in range(1, 210)] + [205.0]  # last close just above SMA20
    volumes = [100] * 209 + [10_000]  # heavy volume on the latest session
    c = compute_condition("^X", None, closes, volumes)
    assert c["condition_label"] in ("Uptrend", "Euphoric"), c["condition_label"]
    assert c["condition_volume_vs_avg_pct"] is not None and c["condition_volume_vs_avg_pct"] > 0


# --------------------------------------------------------------------------- #
# compute_breadth (§3.2) — bands + NULL-signal exclusion (§8).
# --------------------------------------------------------------------------- #
def _all_positive_facts() -> dict:
    return {
        "pct_above_sma10": 100.0, "pct_above_sma20": 100.0, "pct_above_sma50": 100.0,
        "pct_above_sma200": 100.0, "pct_sma20_above_sma50": 100.0, "pct_sma50_above_sma200": 100.0,
        "benchmark_ytd_pct": 10.0, "benchmark_1w_pct": 5.0, "benchmark_1m_pct": 10.0,
        "benchmark_1y_pct": 30.0, "benchmark_pct_from_52w_high": -1.0, "volatility_close": 10.0,
    }


def test_breadth_all_positive_bullish() -> None:
    b = compute_breadth(_all_positive_facts())
    assert b["breadth_score"] == 100, b["breadth_score"]
    assert b["breadth_label"] == "Bullish" and b["breadth_tone"] == "green", b
    assert b["breadth_available_signals"] == 12 and b["breadth_positive_signals"] == 12


def test_breadth_null_excluded_from_denominator() -> None:
    # Only two signals present: one positive, one negative -> score 50 (Neutral), denom 2.
    facts = {
        "pct_above_sma10": 100.0,  # positive
        "pct_above_sma20": 0.0,    # negative
        # everything else absent (None)
    }
    b = compute_breadth(facts)
    assert b["breadth_available_signals"] == 2, b["breadth_available_signals"]
    assert b["breadth_positive_signals"] == 1, b["breadth_positive_signals"]
    assert b["breadth_score"] == 50 and b["breadth_label"] == "Neutral", b


def test_breadth_all_null_unavailable() -> None:
    b = compute_breadth({})
    assert b["breadth_score"] is None and b["breadth_label"] == "Unavailable", b
    assert b["breadth_available"] is False


def test_breadth_negative_band() -> None:
    # 2 of 6 positive -> score 33 -> Negative (>20 and <=40).
    facts = {
        "pct_above_sma10": 100.0, "pct_above_sma20": 100.0,  # positive
        "pct_above_sma50": 0.0, "pct_above_sma200": 0.0,     # negative
        "pct_sma20_above_sma50": 0.0, "pct_sma50_above_sma200": 0.0,  # negative
    }
    b = compute_breadth(facts)
    assert b["breadth_score"] == 33, b["breadth_score"]
    assert b["breadth_label"] == "Negative" and b["breadth_tone"] == "yellow", b


# --------------------------------------------------------------------------- #
# combine_regime (§4) — the full condition x breadth matrix + unavailability.
# --------------------------------------------------------------------------- #
def test_combine_regime_matrix() -> None:
    cases = {
        ("Uptrend", "Bullish"): "RiskOn",
        ("Euphoric", "Neutral"): "SelectiveRiskOn",
        ("Neutral", "Positive"): "SelectiveRiskOn",
        ("Neutral", "Neutral"): "SelectiveRiskOn",
        ("Cautious", "Bullish"): "SelectiveRiskOn",
        ("Uptrend", "Negative"): "Mixed",
        ("Neutral", "Bearish"): "Mixed",
        ("Pessimistic", "Positive"): "Mixed",
        ("Cautious", "Neutral"): "Caution",
        ("Cautious", "Bearish"): "Caution",
        ("Bearish", "Neutral"): "Caution",
        ("Pessimistic", "Bearish"): "RiskOff",
    }
    for (cond, breadth), expected in cases.items():
        r = combine_regime(cond, breadth)
        assert r["regime"] == expected, (cond, breadth, r["regime"], expected)
        assert r["regime_label"] and r["regime_tone"] and r["posture"], r


def test_combine_regime_unavailable_either_side() -> None:
    for pair in (("Unavailable", "Bullish"), ("Uptrend", "Unavailable"),
                 ("Unavailable", "Unavailable")):
        r = combine_regime(*pair)
        assert r["regime"] == "Unavailable" and r["regime_tone"] == "grey", (pair, r)


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
