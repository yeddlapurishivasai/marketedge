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

# Saturation thresholds: the metric value that maps to ~full strength. The three
# *ratio* metrics (epsBeat / epsForecast / opExpansion) use soft_norm() — a smooth
# diminishing curve where FULL maps to ~0.875 strength and there is NO hard ceiling,
# so a 400%-from-a-near-zero-base beat converges near 1 instead of tying a clean 25%
# one. The additive opmExpansion (pp) and the bounded targetUpside keep the linear clamp.
EPS_BEAT_FULL_PCT = 25.0       # +25% reported-EPS surprise (soft) ~= 0.875 strength
EPS_FORECAST_FULL_PCT = 25.0   # +25% expected QoQ growth, next-qtr consensus vs last actual (soft)
OPM_EXPANSION_FULL_PP = 10.0   # +10 pp OPM expansion YoY (linear clamp = full marks)
OP_EXPANSION_FULL_PCT = 50.0   # +50% operating-profit expansion YoY (soft) ~= 0.875 strength
TARGET_UPSIDE_FULL_PCT = 30.0  # +30% upside to mean target (linear clamp = full marks)

# Exp scale for soft_norm: scale = FULL * SOFT_FACTOR. Tuned so a metric's FULL value
# maps to 1 - e^(-1/0.48) ~= 0.875, keeping calibration close to the old linear cap.
SOFT_FACTOR = 0.48

# Fixed blend weights for the fundamental confidence (only applied metrics count).
# THIS IS THE SINGLE SOURCE OF TRUTH for fundamental weighting — the API no longer
# keeps its own copy; it reads the scores/side the worker computes from here.
FUND_WEIGHTS = {
    "epsBeat": 0.18,        # magnitude of the latest reported-EPS surprise
    "epsBeatRate": 0.12,    # consistency: how often it beats (Wilson LB over last N quarters)
    "epsForecast": 0.18,
    "opExpansion": 0.17,
    "opmExpansion": 0.18,
    "rating": 0.07,
    "targetUpside": 0.10,
}

# Weights for the signed long/short *direction* score (a separate classifier from the
# confidence blend; epsForecast/targetUpside don't vote on direction).
DIRECTION_WEIGHTS = {
    "epsBeat": 0.30,
    "opExpansion": 0.20,
    "opmExpansion": 0.20,
    "rating": 0.15,
}
# Dead-band on the signed direction score: |score| <= 20 is Neutral.
LONG_MIN = 20
SHORT_MAX = -20

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


def recency(days: int | float | None) -> float:
    """Recency multiplier in (0, 1]: a fresh signal keeps its full strength and decays
    hyperbolically with age — HALFLIFE / (HALFLIFE + days). At days == HALFLIFE a metric
    is worth half its strength. (Replaces the old Wilson lower-bound penalty, which used
    the metric count as a bogus sample size and capped even a perfect metric near 75.)"""
    d = 0.0 if days is None or days < 0 else float(days)
    return HALFLIFE / (HALFLIFE + d)


def soft_norm(value: float | None, full: float) -> float | None:
    """Diminishing-returns normaliser for *ratio* metrics that can explode from a
    near-zero base (a 400% EPS beat is almost always a ~$0.01-estimate artifact, not
    16x the signal of a clean 25% beat). Smoothly approaches 1 with no hard ceiling:

        strength = 1 - e^(-value / (full * SOFT_FACTOR))

    Larger values always score a little higher but with shrinking marginal reward, so
    low-base monsters converge near 1 instead of dominating the blend. Non-positive
    values -> 0.0 (no signal); SOFT_FACTOR keeps each metric's FULL constant at ~0.875,
    so calibration barely shifts vs the old linear clamp."""
    if value is None:
        return None
    if value <= 0.0:
        return 0.0
    return 1.0 - math.exp(-value / (full * SOFT_FACTOR))


# --- metric normalisers (raw value -> phat in 0..1, or None when no data) -----
def norm_eps_beat(pct: float | None) -> float | None:
    return soft_norm(pct, EPS_BEAT_FULL_PCT)


def norm_eps_forecast(growth_pct: float | None) -> float | None:
    """Next-quarter consensus EPS growth vs the last reported actual (expected QoQ %)."""
    return soft_norm(growth_pct, EPS_FORECAST_FULL_PCT)


def norm_opm_expansion(pp: float | None) -> float | None:
    return None if pp is None else _clamp01(pp / OPM_EXPANSION_FULL_PP)


def norm_op_expansion(pct: float | None) -> float | None:
    return soft_norm(pct, OP_EXPANSION_FULL_PCT)


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


def _beat_miss_counts(surprises: list[float | None] | None) -> tuple[int, int, int]:
    """(#beats, #misses, #quarters-with-data) from reported-EPS surprise %s.

    A beat is surprise > 0, a miss is surprise < 0; an exact meet (0) counts toward the
    sample size but is neither. Used for the EPS-beat-*rate* metric — a genuine k-of-n
    frequency, which is why it (unlike the magnitude metrics) takes a Wilson lower bound.
    """
    vals = [s for s in (surprises or []) if s is not None]
    beats = sum(1 for s in vals if s > 0.0)
    misses = sum(1 for s in vals if s < 0.0)
    return beats, misses, len(vals)


# --- direction (long/short) + bearish short-mirror, computed by the worker so the API
# --- can be a pure reader (no weight/Wilson math lives in C#) -----------------
def _signed(x: float) -> float:
    return -1.0 if x < -1.0 else 1.0 if x > 1.0 else x


def _rating_direction(grade: str | None) -> float | None:
    """+1 buy / 0 hold / -1 sell / None, for the signed direction score."""
    if not grade or not grade.strip():
        return None
    g = grade.strip().lower()
    if "buy" in g or "outperform" in g or "overweight" in g or "accumulate" in g or "add" in g:
        return 1.0
    if "sell" in g or "underperform" in g or "underweight" in g or "reduce" in g:
        return -1.0
    if "hold" in g or "neutral" in g or "equal" in g or "perform" in g or "in-line" in g:
        return 0.0
    return None


def _short_rating_strength(grade: str | None) -> float | None:
    """Bearish rating strength (sell=1, hold=0.5, buy=0) for the mirrored short blend."""
    d = _rating_direction(grade)
    return None if d is None else 0.5 - d / 2.0


def direction_score(eps_beat_pct: float | None, opm_expansion_pp: float | None,
                    op_expansion_pct: float | None, rating_grade: str | None) -> int | None:
    """Signed direction in -100..+100 (None when no metric has data)."""
    wsum = 0.0
    num = 0.0

    def add(w: float, v: float | None) -> None:
        nonlocal wsum, num
        if v is not None:
            wsum += w
            num += w * v

    add(DIRECTION_WEIGHTS["epsBeat"],
        None if eps_beat_pct is None else _signed(eps_beat_pct / EPS_BEAT_FULL_PCT))
    add(DIRECTION_WEIGHTS["opExpansion"],
        None if op_expansion_pct is None else _signed(op_expansion_pct / OP_EXPANSION_FULL_PCT))
    add(DIRECTION_WEIGHTS["opmExpansion"],
        None if opm_expansion_pp is None else _signed(opm_expansion_pp / OPM_EXPANSION_FULL_PP))
    add(DIRECTION_WEIGHTS["rating"], _rating_direction(rating_grade))
    if wsum <= 0:
        return None
    return int(round(100.0 * num / wsum))


def side(score: int | None) -> str | None:
    """long / short / neutral from a signed score (None when score is None)."""
    if score is None:
        return None
    return "long" if score > LONG_MIN else "short" if score < SHORT_MAX else "neutral"


def compute_short_confidence(
    eps_beat_pct: float | None, opm_expansion_pp: float | None,
    op_expansion_pct: float | None, rating_grade: str | None,
    days_since_earnings: int | None, days_since_rating: int | None,
    eps_quarter_surprises: list[float | None] | None = None,
) -> dict[str, Any]:
    """Mirrored bearish confidence: each metric's miss/contraction/sell -> high short
    conviction, blended with the same FUND_WEIGHTS as the long side. Target upside has
    no bearish twin (needs a live price), so it never votes. Returns a rationale block
    that also carries the scalar short confidences the API surfaces."""
    phats: list[tuple[str, float, int | None]] = []
    if eps_beat_pct is not None:
        phats.append(("epsBeat", soft_norm(-eps_beat_pct, EPS_BEAT_FULL_PCT), days_since_earnings))
    if opm_expansion_pp is not None:
        phats.append(("opmExpansion", _clamp01(-opm_expansion_pp / OPM_EXPANSION_FULL_PP), days_since_earnings))
    if op_expansion_pct is not None:
        phats.append(("opExpansion", soft_norm(-op_expansion_pct, OP_EXPANSION_FULL_PCT), days_since_earnings))
    sr = _short_rating_strength(rating_grade)
    if sr is not None:
        phats.append(("rating", sr, days_since_rating))

    n = len(phats)
    conf: dict[str, float] = {}
    metrics: list[dict[str, Any]] = []
    for key, phat, days in phats:
        c = round(100.0 * phat * recency(days), 2)
        conf[key] = c
        metrics.append({
            "metric": key, "phat": round(phat, 4), "n": n,
            "days": days, "recency": round(recency(days), 4), "confidence": c,
        })

    # Bearish twin of the beat-rate: the *miss* frequency over the last reported quarters
    # (a true k-of-n), so it takes the Wilson lower bound and doesn't age.
    _b, misses, brn = _beat_miss_counts(eps_quarter_surprises)
    if brn > 0:
        mr_phat = misses / brn
        mr_conf = round(100.0 * wilson_lb(mr_phat, brn, Z0), 2)
        conf["epsBeatRate"] = mr_conf
        metrics.append({
            "metric": "epsBeatRate", "phat": round(mr_phat, 4), "n": brn,
            "days": None, "recency": 1.0, "confidence": mr_conf,
            "misses": misses, "quarters": brn,
        })

    num = den = 0.0
    for key, c in conf.items():
        w = FUND_WEIGHTS.get(key, 0.0)
        num += w * c
        den += w
    fundamental = round(num / den, 2) if den > 0 else None

    return {
        "n": n,
        "weights": FUND_WEIGHTS,
        "metrics": metrics,
        "targetUpsidePct": None,
        "epsBeat": conf.get("epsBeat"),
        "epsBeatRate": conf.get("epsBeatRate"),
        "opmExpansion": conf.get("opmExpansion"),
        "opExpansion": conf.get("opExpansion"),
        "rating": conf.get("rating"),
        "fundamental": fundamental,
        "technical": None,
        "overall": fundamental,  # technical is null for shorts -> overall = fundamental
        "blend": {"fundamental": OVERALL_FUND_WEIGHT, "technical": OVERALL_TECH_WEIGHT},
        "side": "short",
    }


def _metric_conf(phat: float, days: int | None) -> float:
    """Metric confidence (0..100): raw 0..1 strength decayed by the signal's age."""
    return round(100.0 * phat * recency(days), 2)


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
    eps_forecast_growth_pct: float | None = None,
    eps_quarter_surprises: list[float | None] | None = None,
) -> dict[str, Any]:
    """Compute all confidence scores + rationale for one idea row.

    Earnings-based metrics age with ``days_since_earnings``; analyst rating and target
    upside age with ``days_since_rating``. Each metric's 0..1 strength is decayed by a
    recency multiplier (see ``recency``); ``n`` is just how many metrics had data.
    """
    phats: dict[str, float] = {}
    upside_pct: float | None = None

    p = norm_eps_beat(eps_beat_pct)
    if p is not None:
        phats["epsBeat"] = p
    p = norm_eps_forecast(eps_forecast_growth_pct)
    if p is not None:
        phats["epsForecast"] = p
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
    earnings_keys = {"epsBeat", "epsForecast", "opmExpansion", "opExpansion"}

    metric_conf: dict[str, float] = {}
    rationale_metrics: list[dict[str, Any]] = []
    for key, phat in phats.items():
        days = days_since_earnings if key in earnings_keys else days_since_rating
        conf = _metric_conf(phat, days)
        metric_conf[key] = conf
        rationale_metrics.append({
            "metric": key,
            "phat": round(phat, 4),
            "n": n,
            "days": days,
            "recency": round(recency(days), 4),
            "confidence": conf,
        })

    # EPS-beat *consistency*: a genuine k-of-n frequency (beats over the last reported
    # quarters), so it gets the Wilson lower bound (small samples stay humble) instead of
    # the magnitude path above. It's a track record, so it doesn't age (recency = 1).
    beats, _misses, br_n = _beat_miss_counts(eps_quarter_surprises)
    if br_n > 0:
        br_phat = beats / br_n
        br_conf = round(100.0 * wilson_lb(br_phat, br_n, Z0), 2)
        metric_conf["epsBeatRate"] = br_conf
        rationale_metrics.append({
            "metric": "epsBeatRate",
            "phat": round(br_phat, 4),
            "n": br_n,
            "days": None,
            "recency": 1.0,
            "confidence": br_conf,
            "beats": beats,
            "quarters": br_n,
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

    # Worker computes long/short direction + the bearish mirror once, here, and embeds
    # them in the rationale so the API reads them straight back (no scoring in C#).
    direction = direction_score(eps_beat_pct, opm_expansion_pp, op_expansion_pct, rating_grade)
    rationale["direction"] = direction
    rationale["side"] = side(direction)
    rationale["short"] = compute_short_confidence(
        eps_beat_pct, opm_expansion_pp, op_expansion_pct, rating_grade,
        days_since_earnings, days_since_rating, eps_quarter_surprises,
    )

    return {
        "eps_beat_confidence": metric_conf.get("epsBeat"),
        "eps_forecast_confidence": metric_conf.get("epsForecast"),
        "opm_expansion_confidence": metric_conf.get("opmExpansion"),
        "operating_profit_expansion_confidence": metric_conf.get("opExpansion"),
        "analyst_rating_confidence": metric_conf.get("rating"),
        "target_upside_confidence": metric_conf.get("targetUpside"),
        "fundamental_confidence": fundamental,
        "technical_confidence": technical,
        "overall_confidence": overall,
        "rationale_json": json.dumps(rationale, default=str),
    }
