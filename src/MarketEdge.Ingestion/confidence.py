"""Confidence scoring for fundamental ideas (Wilson lower-bound, age-decayed).

Every fundamental metric on an idea is turned into a 0..100 *confidence* score:

1. The raw metric is normalised to a 0..1 strength ``phat`` via a saturating curve
   (e.g. a 25%+ EPS beat = 1.0; a miss = 0.0).
2. ``phat`` and an evidence count ``n`` (how many metrics the stock actually has data
   for) feed a Wilson score lower bound. More available metrics -> larger ``n`` ->
   tighter interval -> higher confidence; a lone metric is penalised.
3. The Wilson ``z`` widens with the age of the underlying signal,
   ``z = z0 * (1 + days / halflife)``. A fresh result keeps ``z`` near ``z0``; as the
   result (or analyst update) ages, ``z`` grows, the interval widens and confidence
   decays smoothly over weeks rather than collapsing.

The per-metric confidences are blended with fixed weights into a single
``FundamentalConfidence``. ``TechnicalConfidence`` is the Wilson lower bound of the
stock's realised paper-trade win rate (falling back to its triggering scanner's record
when the stock itself has too few trades). ``OverallConfidence`` blends the two.

All functions here are pure and unit-testable; DB reads live in ``db.py``.
"""
from __future__ import annotations

import json
import math
from typing import Any

# --- tunables ----------------------------------------------------------------
Z0 = 1.28          # ~90% one-sided base z (matches the stock scoring engine)
HALFLIFE = 30.0    # days; age at which z has grown by one z0-equivalent step

# Saturation thresholds: the metric value that maps to a full 1.0 strength.
EPS_BEAT_FULL_PCT = 25.0       # +25% reported-EPS surprise = full marks
OPM_EXPANSION_FULL_PP = 10.0   # +10 pp OPM expansion (YoY) = full marks
OP_EXPANSION_FULL_PCT = 50.0   # +50% operating-profit expansion (YoY) = full marks
TARGET_UPSIDE_FULL_PCT = 30.0  # +30% upside to mean target = full marks

# Fixed blend weights for the fundamental confidence (only applied metrics count).
FUND_WEIGHTS = {
    "epsBeat": 0.30,
    "opExpansion": 0.20,
    "opmExpansion": 0.20,
    "rating": 0.15,
    "targetUpside": 0.15,
}

# Overall = fundamental + technical blend (renormalised when one side is missing).
OVERALL_FUND_WEIGHT = 0.60
OVERALL_TECH_WEIGHT = 0.40

# Minimum trades of a stock's own before we trust its win rate over the scanner's.
MIN_OWN_TRADES = 3

_BUY_RATINGS = {"buy", "strong buy", "outperform", "overweight", "accumulate", "add"}
_HOLD_RATINGS = {"hold", "neutral", "equal-weight", "equalweight", "equal weight",
                 "in-line", "market perform", "sector perform", "peer perform"}
_SELL_RATINGS = {"sell", "strong sell", "underperform", "underweight", "reduce"}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def wilson_lb(phat: float, n: float, z: float) -> float:
    """Wilson score interval lower bound for proportion ``phat`` over ``n`` trials."""
    if n <= 0:
        return 0.0
    phat = _clamp01(phat)
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def aged_z(days: int | float | None) -> float:
    """Base z widened by the age of the signal. Older signal -> larger z -> lower bound."""
    d = 0.0 if days is None or days < 0 else float(days)
    return Z0 * (1.0 + d / HALFLIFE)


# --- metric normalisers (raw value -> phat in 0..1, or None when no data) -----
def norm_eps_beat(pct: float | None) -> float | None:
    return None if pct is None else _clamp01(pct / EPS_BEAT_FULL_PCT)


def norm_opm_expansion(pp: float | None) -> float | None:
    return None if pp is None else _clamp01(pp / OPM_EXPANSION_FULL_PP)


def norm_op_expansion(pct: float | None) -> float | None:
    return None if pct is None else _clamp01(pct / OP_EXPANSION_FULL_PCT)


def norm_rating(grade: str | None) -> float | None:
    if not grade:
        return None
    g = grade.strip().lower()
    if g in _BUY_RATINGS:
        return 1.0
    if g in _SELL_RATINGS:
        return 0.0
    if g in _HOLD_RATINGS:
        return 0.5
    # Substring fallbacks for compound grades ("Moderate Buy", "Sector Underperform").
    if "buy" in g or "outperform" in g or "overweight" in g:
        return 1.0
    if "sell" in g or "underperform" in g or "underweight" in g:
        return 0.0
    if "hold" in g or "neutral" in g or "perform" in g or "equal" in g:
        return 0.5
    return None


def norm_target_upside(mean_target: float | None, close: float | None) -> tuple[float | None, float | None]:
    """Return (phat, upside_pct). Needs a positive close and target."""
    if not mean_target or not close or close <= 0:
        return None, None
    upside = (mean_target / close - 1.0) * 100.0
    return _clamp01(upside / TARGET_UPSIDE_FULL_PCT), upside


def _metric_conf(phat: float, n: int, days: int | None) -> float:
    z = aged_z(days)
    return round(100.0 * wilson_lb(phat, n, z), 2)


def technical_confidence(own_wins: int, own_total: int,
                         scanner_wins: int, scanner_total: int,
                         scanner_name: str | None) -> tuple[float | None, dict[str, Any]]:
    """Wilson lower bound of realised trade win rate (0..100), scanner-fallback aware.

    Uses the stock's own closed trades when it has at least ``MIN_OWN_TRADES``; otherwise
    falls back to the triggering scanner's aggregate record. Returns (confidence, detail).
    """
    detail: dict[str, Any] = {
        "ownWins": own_wins, "ownTotal": own_total,
        "scanner": scanner_name, "scannerWins": scanner_wins, "scannerTotal": scanner_total,
    }
    if own_total >= MIN_OWN_TRADES:
        conf = round(100.0 * wilson_lb(own_wins / own_total, own_total, Z0), 2)
        detail.update(source="own", wins=own_wins, total=own_total, confidence=conf)
        return conf, detail
    if scanner_total > 0:
        conf = round(100.0 * wilson_lb(scanner_wins / scanner_total, scanner_total, Z0), 2)
        detail.update(source="scanner", wins=scanner_wins, total=scanner_total, confidence=conf)
        return conf, detail
    if own_total > 0:
        conf = round(100.0 * wilson_lb(own_wins / own_total, own_total, Z0), 2)
        detail.update(source="own", wins=own_wins, total=own_total, confidence=conf)
        return conf, detail
    detail.update(source="none", confidence=None)
    return None, detail


def compute_confidence(
    *,
    eps_beat_pct: float | None,
    opm_expansion_pp: float | None,
    op_expansion_pct: float | None,
    rating_grade: str | None,
    target_mean: float | None,
    close: float | None,
    days_since_earnings: int | None,
    days_since_rating: int | None,
    technical: float | None,
    technical_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all confidence scores + rationale for one idea row.

    Earnings-based metrics age with ``days_since_earnings``; analyst rating and target
    upside age with ``days_since_rating``. ``n`` is the number of fundamental metrics
    that have data, so more complete coverage yields tighter (higher) Wilson bounds.
    """
    phats: dict[str, float] = {}
    upside_pct: float | None = None

    p = norm_eps_beat(eps_beat_pct)
    if p is not None:
        phats["epsBeat"] = p
    p = norm_opm_expansion(opm_expansion_pp)
    if p is not None:
        phats["opmExpansion"] = p
    p = norm_op_expansion(op_expansion_pct)
    if p is not None:
        phats["opExpansion"] = p
    p = norm_rating(rating_grade)
    if p is not None:
        phats["rating"] = p
    p, upside_pct = norm_target_upside(target_mean, close)
    if p is not None:
        phats["targetUpside"] = p

    n = len(phats)
    earnings_keys = {"epsBeat", "opmExpansion", "opExpansion"}

    metric_conf: dict[str, float] = {}
    rationale_metrics: list[dict[str, Any]] = []
    for key, phat in phats.items():
        days = days_since_earnings if key in earnings_keys else days_since_rating
        conf = _metric_conf(phat, n, days)
        metric_conf[key] = conf
        rationale_metrics.append({
            "metric": key,
            "phat": round(phat, 4),
            "n": n,
            "days": days,
            "z": round(aged_z(days), 4),
            "confidence": conf,
        })

    # Fundamental confidence = weighted blend over the metrics that applied.
    num = den = 0.0
    for key, conf in metric_conf.items():
        w = FUND_WEIGHTS.get(key, 0.0)
        num += w * conf
        den += w
    fundamental = round(num / den, 2) if den > 0 else None

    # Overall = fundamental + technical blend, renormalised when one side is missing.
    onum = oden = 0.0
    if fundamental is not None:
        onum += OVERALL_FUND_WEIGHT * fundamental
        oden += OVERALL_FUND_WEIGHT
    if technical is not None:
        onum += OVERALL_TECH_WEIGHT * technical
        oden += OVERALL_TECH_WEIGHT
    overall = round(onum / oden, 2) if oden > 0 else None

    rationale = {
        "n": n,
        "weights": FUND_WEIGHTS,
        "metrics": rationale_metrics,
        "targetUpsidePct": round(upside_pct, 2) if upside_pct is not None else None,
        "fundamental": fundamental,
        "technical": technical,
        "technicalDetail": technical_detail,
        "overall": overall,
        "blend": {"fundamental": OVERALL_FUND_WEIGHT, "technical": OVERALL_TECH_WEIGHT},
    }

    return {
        "eps_beat_confidence": metric_conf.get("epsBeat"),
        "opm_expansion_confidence": metric_conf.get("opmExpansion"),
        "operating_profit_expansion_confidence": metric_conf.get("opExpansion"),
        "analyst_rating_confidence": metric_conf.get("rating"),
        "target_upside_confidence": metric_conf.get("targetUpside"),
        "fundamental_confidence": fundamental,
        "technical_confidence": technical,
        "overall_confidence": overall,
        "rationale_json": json.dumps(rationale, default=str),
    }
