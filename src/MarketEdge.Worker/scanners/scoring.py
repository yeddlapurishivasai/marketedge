"""Shared scanner-reliability + Wilson helpers."""
from __future__ import annotations

import math
from typing import Any

_Z = 1.28  # ~90% one-sided lower bound (less punitive than 1.64 so strong, well-evidenced setups score higher)

# Table maps -------------------------------------------------------------------
_EARN = {"india": "IndianEarningsFundamentals", "us": "USEarningsFundamentals"}
_BREAKOUTS = {"india": "IndianBreakouts", "us": "USBreakouts"}


def _t(mapping: dict, market: str) -> str:
    t = mapping.get(market.lower())
    if t is None:
        raise ValueError(f"Unsupported market: {market}")
    return t


def _wilson_lb(passes: float, n: float) -> float:
    """Weighted Wilson score lower bound. ``passes`` is phat*n, ``n`` is trial count."""
    if n <= 0:
        return 0.0
    phat = passes / n
    z = _Z
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _fin(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _scanner_reliability(conn, market: str) -> dict[str, dict[str, Any]]:
    """Per-scanner breakout success keyed by EntryScanner.

    ``wilson`` remains available for API compatibility. ``reliability`` is the
    Beta-smoothed realised rate with an equal prior (alpha=beta=2), so new scanners
    start at 0.5 and move toward their observed breakout win rate as results close.
    """
    table = _t(_BREAKOUTS, market)
    cur = conn.cursor()
    rows = cur.execute(
        f"""
        SELECT EntryScanner,
               SUM(CASE WHEN Status='closed' THEN 1 ELSE 0 END) AS Closed,
               SUM(CASE WHEN Status='closed' AND PnLPct > 0 THEN 1 ELSE 0 END) AS ClosedWins,
               SUM(CASE WHEN (Status='closed' AND PnLPct > 0)
                          OR (Status='active' AND PnLPct > 0) THEN 1 ELSE 0 END) AS Wins,
               SUM(CASE WHEN Status='closed' OR (Status='active' AND PnLPct IS NOT NULL)
                        THEN 1 ELSE 0 END) AS Total,
               AVG(CAST(PnLPct AS FLOAT)) AS AvgPnL
        FROM dbo.{table}
        WHERE EntryScanner IS NOT NULL
        GROUP BY EntryScanner
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        total = int(r.Total or 0)
        wins = int(r.Wins or 0)
        wilson = _wilson_lb(float(wins), float(total)) if total > 0 else 0.0
        smoothed = (wins + 2.0) / (total + 4.0)
        out[r.EntryScanner] = {
            "closed": int(r.Closed or 0),
            "closedWins": int(r.ClosedWins or 0),
            "wins": wins,
            "total": total,
            "winRate": round(wins / total, 4) if total > 0 else None,
            "wilson": round(wilson, 4),
            "reliability": round(smoothed, 4),
            "avgPnLPct": round(float(r.AvgPnL), 2) if r.AvgPnL is not None else None,
        }
    return out
