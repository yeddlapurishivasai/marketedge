"""Self-adjusting trade-confidence weights persisted in ``dbo.ScoringWeights``.

Two kinds of weights live in the table (see the SQL definition):

* **pattern** -- one adaptive weight (0..1) per technical scanner. It starts at a
  neutral seed and, with no backtracking, drifts up when that scanner's paper
  trades win and down when they lose. This is the "which pattern is performing"
  signal expressed as a single editable number.
* **mix** -- per-profile blend weights describing how much each evidence
  component (pattern / fundamental / eps / ai) contributes to a trade's
  confidence. Swing leans on the pattern; positional leans on fundamentals.

When a paper trade is *opened* we read these weights and compute a 0..100
confidence plus a JSON rationale (each component's weight, score and
contribution). When a trade *closes* we nudge the triggering scanner's pattern
weight toward the outcome. Any row flagged ``ManualOverride`` is frozen so a
human edit is never overwritten by the auto-adapter.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .scoring import _EARN, _fin, _t, _upside_eps, _ANALYST

logger = logging.getLogger(__name__)

# Online learning rate for the pattern weights. Small so a single trade only
# nudges the weight; the value converges toward the realised win rate over time.
_LR = 0.08

# Neutral seed for a brand-new scanner's pattern weight.
_PATTERN_SEED = 0.50

# Per-profile blend seeds. Swing trades are technical -> the pattern dominates;
# positional trades are held longer -> fundamentals carry half the weight.
_MIX_SEEDS: dict[str, dict[str, float]] = {
    "swing": {"pattern": 0.60, "fundamental": 0.15, "eps": 0.15, "ai": 0.10},
    "positional": {"pattern": 0.25, "fundamental": 0.50, "eps": 0.15, "ai": 0.10},
}

# EPS upside (%) that maps to a full-marks eps component score of 1.0.
_EPS_FULL_PCT = 25.0

# Neutral score for the AI component until a real AI signal feeds in.
_AI_NEUTRAL = 0.50

_COMPONENTS = ("pattern", "fundamental", "eps", "ai")


def seed_weights(conn, market: str, scanner_names: list[str]) -> None:
    """Insert any missing pattern + mix rows for ``market`` (idempotent)."""
    mk = market.lower()
    cur = conn.cursor()
    for name in sorted(set(scanner_names)):
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM dbo.ScoringWeights
                           WHERE Market=? AND Category='pattern' AND ComponentKey=?)
            INSERT INTO dbo.ScoringWeights (Market, Category, ComponentKey, Weight, SeedWeight)
            VALUES (?, 'pattern', ?, ?, ?)
            """,
            mk, name, mk, name, _PATTERN_SEED, _PATTERN_SEED,
        )
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
    """Load all weights for ``market`` -> {"pattern": {...}, "mix": {profile: {...}}}."""
    mk = market.lower()
    rows = conn.cursor().execute(
        "SELECT Category, ComponentKey, Weight FROM dbo.ScoringWeights WHERE Market=?",
        mk,
    ).fetchall()
    pattern: dict[str, float] = {}
    mix: dict[str, dict[str, float]] = {p: dict(s) for p, s in _MIX_SEEDS.items()}
    for r in rows:
        w = float(r.Weight)
        if r.Category == "pattern":
            pattern[r.ComponentKey] = w
        elif r.Category == "mix" and ":" in r.ComponentKey:
            profile, component = r.ComponentKey.split(":", 1)
            mix.setdefault(profile, {})[component] = w
    return {"pattern": pattern, "mix": mix}


def adapt_pattern_weight(conn, market: str, scanner: str | None, won: bool) -> None:
    """Nudge a scanner's pattern weight toward a closed trade's outcome.

    win  -> W += LR * (1 - W)   (rewards, capped below 1)
    loss -> W -= LR * W         (penalises, capped above 0)

    Wins/Losses counters are bumped for transparency. Rows the user has pinned
    (``ManualOverride=1``) keep their counters updated but their weight frozen.
    """
    if not scanner:
        return
    mk = market.lower()
    cur = conn.cursor()
    row = cur.execute(
        """SELECT Id, Weight, ManualOverride FROM dbo.ScoringWeights
           WHERE Market=? AND Category='pattern' AND ComponentKey=?""",
        mk, scanner,
    ).fetchone()
    if row is None:
        # Unknown scanner (not seeded yet) -> create it at the seed then adapt.
        seed_weights(conn, market, [scanner])
        row = cur.execute(
            """SELECT Id, Weight, ManualOverride FROM dbo.ScoringWeights
               WHERE Market=? AND Category='pattern' AND ComponentKey=?""",
            mk, scanner,
        ).fetchone()
        if row is None:
            return
    w = float(row.Weight)
    new_w = w + _LR * (1.0 - w) if won else w - _LR * w
    new_w = max(0.0, min(1.0, new_w))
    if row.ManualOverride:
        cur.execute(
            """UPDATE dbo.ScoringWeights
               SET Wins = Wins + ?, Losses = Losses + ?, UpdatedAt = GETUTCDATE()
               WHERE Id = ?""",
            1 if won else 0, 0 if won else 1, row.Id,
        )
    else:
        cur.execute(
            """UPDATE dbo.ScoringWeights
               SET Weight = ?, Wins = Wins + ?, Losses = Losses + ?, UpdatedAt = GETUTCDATE()
               WHERE Id = ?""",
            round(new_w, 4), 1 if won else 0, 0 if won else 1, row.Id,
        )
    conn.commit()
    logger.info("Adapted %s pattern weight %s: %.4f -> %.4f (%s)",
                market, scanner, w, new_w, "win" if won else "loss")


def symbol_fundamentals(conn, market: str, symbol: str) -> tuple[float | None, float | None]:
    """Return (fund_score 0..1, eps_upside_pct) for ``symbol``.

    ``fund_score`` is the fraction of applicable fundamental checks that pass
    (earnings/revenue growth, margin trend, EPS surprise, earnings increasing).
    ``eps_upside_pct`` reuses the scoring engine's P/E-constant price upside
    (``(forwardEps / trailingEps - 1) * 100``, falling back to reported YoY earnings
    growth). Either may be ``None`` when the data isn't available -- the caller then
    drops that component from the blend.
    """
    cur = conn.cursor()
    earn = cur.execute(
        f"""SELECT TOP 1 EarningsGrowthYoyPct, EarningsGrowthQoqPct, RevenueGrowthYoyPct,
                   OpmTrend, LastEpsSurprisePct, EarningsIncreasing
            FROM dbo.{_t(_EARN, market)} WHERE Ticker = ?""",
        symbol,
    ).fetchone()
    analyst = cur.execute(
        f"""SELECT TOP 1 CurrentYearEps, NextYearEps
            FROM dbo.{_t(_ANALYST, market)} WHERE Ticker = ?
            ORDER BY AsOfDate DESC""",
        symbol,
    ).fetchone()

    fund_score: float | None = None
    if earn is not None:
        checks: list[bool] = []
        eg = _fin(earn.EarningsGrowthYoyPct)
        if eg is not None:
            checks.append(eg > 0)
        eq = _fin(earn.EarningsGrowthQoqPct)
        if eq is not None:
            checks.append(eq > 0)
        rg = _fin(earn.RevenueGrowthYoyPct)
        if rg is not None:
            checks.append(rg > 0)
        if earn.OpmTrend is not None:
            checks.append((earn.OpmTrend or "") == "expanding")
        sp = _fin(earn.LastEpsSurprisePct)
        if sp is not None:
            checks.append(sp > 0)
        if earn.EarningsIncreasing is not None:
            checks.append(bool(earn.EarningsIncreasing))
        if checks:
            fund_score = sum(1 for c in checks if c) / len(checks)

    eps_upside, _src = _upside_eps(analyst, earn)
    return fund_score, eps_upside


def trade_confidence(weights: dict[str, Any], profile: str, scanner: str | None,
                     fund_score: float | None, eps_upside: float | None,
                     ai_score: float | None = None) -> tuple[float, dict[str, Any]]:
    """Blend the component scores into a 0..100 confidence + JSON rationale.

    ``confidence = 100 * Σ(mix[c] * score[c]) / Σ mix[c]`` over the components
    that have a score. ``patternScore`` is the triggering scanner's adaptive
    weight; ``epsScore`` clamps EPS upside into 0..1; ``aiScore`` is a neutral
    placeholder until a real AI signal exists.
    """
    mix = weights.get("mix", {}).get(profile, _MIX_SEEDS.get(profile, {}))
    pattern_w = weights.get("pattern", {})

    pattern_score = float(pattern_w.get(scanner, _PATTERN_SEED)) if scanner else _PATTERN_SEED
    eps_score = None
    if eps_upside is not None:
        eps_score = max(0.0, min(1.0, eps_upside / _EPS_FULL_PCT))
    if ai_score is None:
        ai_score = _AI_NEUTRAL

    scores: dict[str, float | None] = {
        "pattern": pattern_score,
        "fundamental": fund_score,
        "eps": eps_score,
        "ai": ai_score,
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

    confidence = round(100.0 * num / den, 2) if den > 0 else 0.0
    rationale = {
        "profile": profile,
        "scanner": scanner,
        "confidence": confidence,
        "components": components,
        "notes": {
            "epsUpsidePct": eps_upside,
            "patternWeight": round(pattern_score, 4),
        },
    }
    return confidence, rationale
