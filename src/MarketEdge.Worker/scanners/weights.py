"""Breakout-confidence scoring for paper breakouts (setup + fundamental + volume blend).

A new paper breakout's 0..100 confidence is the conviction that the setup will work,
blended from three independent, count-or-data-driven components:

* **setup** -- how reliable are the scanner(s) that flagged this symbol. Each
  technical scanner carries a Beta-smoothed realised paper-breakout win
  rate (computed in :mod:`scanners.scoring`); when several scanners flag the same
  name we combine their reliabilities with a *noisy-OR* (``1 - Π(1 - wᵢ)``) so
  corroborating signals raise -- never lower -- the setup score.
* **fundamental** -- the symbol's fundamental score for the breakout's *direction*
  (long uses the bullish pass-fraction; short uses its complement).
* **volume** -- how strong the breakout bar's volume is versus its 20-day average.

The per-profile blend weights live (editable) in ``dbo.ScoringWeights`` under the
``mix`` category: swing leans on setup + breakout volume, positional leans on
fundamentals. Unlike the previous design there is **no online "pattern weight" to
learn** -- a scanner's performance is its smoothed realised rate, which already
self-updates every run as paper breakouts close, with no learning-rate hyperparameter.
"""
from __future__ import annotations

import logging
from typing import Any

from .scoring import _fin, _t

logger = logging.getLogger(__name__)

# Fundamental-ideas tables (canonical FundamentalConfidence lives here, keyed by Ticker +
# EarningsDate with IsStale flagging superseded rows).
_IDEAS = {"india": "IndianFundamentalIdeas", "us": "USFundamentalIdeas"}

# Per-profile blend of the three confidence components. Swing is a momentum/technical
# play (setup + breakout volume dominate); positional is held for the fundamental
# thesis (fundamentals dominate). Editable per-market via dbo.ScoringWeights(mix).
_MIX_SEEDS: dict[str, dict[str, float]] = {
    "swing": {"setup": 0.55, "fundamental": 0.20, "volume": 0.25},
    "positional": {"setup": 0.20, "fundamental": 0.70, "volume": 0.10},
}

_COMPONENTS = ("setup", "fundamental", "volume")

_SCANNER_PRIOR = 0.5

# Breakout volume (as a multiple of the 20-day average) that earns full marks (1.0).
# The 1.5x breakout-confirmation floor therefore maps to ~0.25.
_VOL_FULL_MULT = 3.0
# Base accumulation: share of base volume on up days. 0.40 = neutral floor (0.0),
# 0.70+ (green volume dominates) earns full marks; below 0.40 (distribution) scores 0.
_ACCUM_FLOOR = 0.40
_ACCUM_FULL = 0.70
# Breakout-bar strength vs base accumulation blend for the volume component.
_BAR_WEIGHT = 0.60
_ACCUM_WEIGHT = 0.40


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def seed_weights(conn, market: str) -> None:
    """Insert any missing per-profile ``mix`` blend rows for ``market`` (idempotent)."""
    mk = market.lower()
    cur = conn.cursor()
    for profile, seeds in _MIX_SEEDS.items():
        for component, w in seeds.items():
            key = f"{profile}:{component}"
            cur.execute(
                """
                IF NOT EXISTS (SELECT 1 FROM dbo.ScoringWeights
                               WHERE Market=? AND Category='mix' AND ComponentKey=?)
                INSERT INTO dbo.ScoringWeights (Market, Category, ComponentKey, Weight, SeedWeight)
                VALUES (?, 'mix', ?, ?, ?)
                """,
                mk, key, mk, key, w, w,
            )
    conn.commit()


def get_weights(conn, market: str) -> dict[str, Any]:
    """Load the editable per-profile ``mix`` weights -> {"mix": {profile: {...}}}."""
    mk = market.lower()
    rows = conn.cursor().execute(
        "SELECT ComponentKey, Weight FROM dbo.ScoringWeights WHERE Market=? AND Category='mix'",
        mk,
    ).fetchall()
    mix: dict[str, dict[str, float]] = {p: dict(s) for p, s in _MIX_SEEDS.items()}
    for r in rows:
        if ":" in r.ComponentKey:
            profile, component = r.ComponentKey.split(":", 1)
            mix.setdefault(profile, {})[component] = float(r.Weight)
    return {"mix": mix}


def setup_score(reliability: dict[str, dict[str, Any]] | None,
                scanners: list[str]) -> tuple[float, dict[str, Any]]:
    """Noisy-OR combine the flagging scanners' smoothed reliabilities into 0..1.

    ``reliability`` is ``EntryScanner -> {reliability, wilson, wins, total, ...}``
    from :func:`scanners.scoring._scanner_reliability`. Missing/unproven scanners use
    the equal 0.5 prior. Noisy-OR means each extra corroborating scanner can only
    raise the score.
    """
    parts: list[dict[str, Any]] = []
    values: list[float] = []
    for s in sorted(set(scanners)):
        rel = reliability.get(s) if reliability else None
        total = int(rel.get("total", 0)) if rel else 0
        wl = float(rel.get("wilson", 0.0)) if rel else 0.0
        raw = rel.get("reliability") if rel else None
        if raw is None:
            raw = rel.get("wilson") if rel else None
        smoothed = _clamp01(float(raw)) if raw is not None else _SCANNER_PRIOR
        values.append(smoothed)
        parts.append({
            "scanner": s,
            "wilson": round(wl, 4),
            "reliability": round(smoothed, 4),
            "total": total,
        })

    prod = 1.0
    for value in values:
        prod *= (1.0 - value)
    score = 1.0 - prod if values else 0.0

    score = _clamp01(score)
    return score, {"method": "noisy-or", "prior": _SCANNER_PRIOR,
                   "scanners": parts, "score": round(score, 4)}


def breakout_volume_score(rel_vol: float | None, accum: float | None = None) -> float | None:
    """0..1 volume strength blending breakout-bar relative volume with base accumulation.

    Bar strength: 1.0x (average) -> 0.0, ``_VOL_FULL_MULT`` x -> 1.0. Accumulation: share of
    base volume on up days, ``_ACCUM_FLOOR`` -> 0.0, ``_ACCUM_FULL`` -> 1.0 (heavy red-day
    volume scores low, heavy green-day volume scores high). Blend = 60% bar + 40% accum;
    whichever is missing, the other stands alone. ``None`` when neither is available.
    """
    bar = None if rel_vol is None else _clamp01((rel_vol - 1.0) / (_VOL_FULL_MULT - 1.0))
    acc = None if accum is None else _clamp01((accum - _ACCUM_FLOOR) / (_ACCUM_FULL - _ACCUM_FLOOR))
    if bar is None and acc is None:
        return None
    if acc is None:
        return bar
    if bar is None:
        return acc
    return _BAR_WEIGHT * bar + _ACCUM_WEIGHT * acc


def symbol_fundamentals(conn, market: str, symbol: str) -> float | None:
    """Bullish (long) fundamental score in 0..1 for ``symbol`` -- or ``None``.

    The canonical ``FundamentalConfidence`` from the fundamental-ideas table (the same
    Wilson-lower-bound score the long fundamental screener shows -- EPS beat + forecast,
    OPM/operating-profit expansion, analyst rating, target upside), normalised 0..100 -> 0..1,
    so a breakout's fundamental component reflects exactly the score we publish for the stock.
    There is deliberately **no fallback**: when a symbol has no computed idea, this returns
    ``None`` and the breakout shows no confidence at all (rather than a guessed value). The
    caller flips it (1 - x) for short trades.
    """
    idea = conn.cursor().execute(
        f"""SELECT TOP 1 FundamentalConfidence
            FROM dbo.{_t(_IDEAS, market)}
            WHERE Ticker = ? AND IsStale = 0 AND FundamentalConfidence IS NOT NULL
            ORDER BY EarningsDate DESC""",
        symbol,
    ).fetchone()
    if idea is None:
        return None
    fc = _fin(idea.FundamentalConfidence)
    return _clamp01(fc / 100.0) if fc is not None else None


def breakout_confidence(weights: dict[str, Any], profile: str, direction: str,
                        scanners: list[str], reliability: dict[str, dict[str, Any]] | None,
                        fund_score: float | None,
                        vol_score: float | None,
                        accum: float | None = None) -> tuple[float | None, dict[str, Any]]:
    """Blend setup + fundamental + volume into a 0..100 confidence + JSON rationale.

    ``confidence = 100 * Σ(mix[c]·score[c]) / Σ mix[c]`` over the components that have
    a score. The fundamental component is direction-aware: long uses the bullish
    score, short uses its complement (1 - bullish), so a high number always means
    "strong conviction in the breakout's own direction".

    The fundamental score is **required**: when ``fund_score is None`` (the symbol has no
    canonical FundamentalConfidence) the confidence is left ``None`` -- the breakout shows no
    confidence rather than a partial blend, per product rule. The rationale is still returned
    (with ``confidence: None``) for diagnostics.
    """
    mix = weights.get("mix", {}).get(profile, _MIX_SEEDS.get(profile, {})) if weights else _MIX_SEEDS.get(profile, {})

    setup_sc, setup_detail = setup_score(reliability, scanners)
    fund_sc = fund_score
    if fund_sc is not None and direction == "short":
        fund_sc = 1.0 - fund_sc

    scores: dict[str, float | None] = {
        "setup": setup_sc,
        "fundamental": fund_sc,
        "volume": vol_score,
    }

    num = den = 0.0
    components: list[dict[str, Any]] = []
    for c in _COMPONENTS:
        sc = scores[c]
        mw = float(mix.get(c, 0.0))
        available = sc is not None and mw > 0
        contribution = (mw * sc) if available else 0.0
        if available:
            num += contribution
            den += mw
        components.append({
            "component": c,
            "weight": round(mw, 4),
            "score": round(sc, 4) if sc is not None else None,
            "available": bool(available),
            "contribution": round(contribution, 4),
        })

    # Fundamental score is mandatory: no canonical FundamentalConfidence -> no confidence.
    confidence = None if fund_sc is None else (round(100.0 * num / den, 2) if den > 0 else 0.0)
    rationale = {
        "profile": profile,
        "direction": direction,
        "confidence": confidence,
        "components": components,
        "setup": setup_detail,
        "notes": {
            "fundamentalScore": round(fund_sc, 4) if fund_sc is not None else None,
            "breakoutVolumeScore": round(vol_score, 4) if vol_score is not None else None,
            "baseAccumulation": round(accum, 4) if accum is not None else None,
        },
    }
    return confidence, rationale
