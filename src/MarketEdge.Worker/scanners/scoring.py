"""Stock scoring engine (Wilson lower-bound).

Computes two probability scores per ticker from the same evidence groups but with
different weight profiles:

* **swing** -- technical evidence takes precedence (Tech 0.65).
* **positional** -- fundamentals weigh exactly 50% (Fund 0.50).

Each evidence group is a set of pass/fail checks. The weighted pass fraction is
``phat`` and the count of *applicable* checks is ``n``; both feed the Wilson lower
bound so that missing data (smaller ``n``) widens the interval and lowers the score.
Fundamental checks are multiplied by an earnings-freshness decay
``0.5**(days_since_earnings/30)`` so stale reporters (e.g. names that stopped
filing) fall back to a technical judgement automatically.

A bear score is computed by inverting the applicable checks; F&O names whose bear
score is high enough are flagged as short candidates.

The realised paper-trade track record (see ``scanners.trades``) is folded in as its
own evidence group every run, which is the daily feedback loop: scores drift toward
setups and tickers that have actually paid off.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

_Z = 1.64  # ~90% one-sided

# Table maps -------------------------------------------------------------------
_EARN = {"india": "IndianEarningsFundamentals", "us": "USEarningsFundamentals"}
_SIG = {"india": "IndianStockSignals", "us": "USStockSignals"}
_ANALYST = {"india": "IndianAnalystSnapshot", "us": "USAnalystSnapshot"}
_TECH = {"india": "IndianTickerTechnical", "us": "USTickerTechnical"}
_STAGE = {"india": "IndianStageAnalysisResults", "us": "USStageAnalysisResults"}
_SCORES = {"india": "IndianStockScores", "us": "USStockScores"}
_TRADES = {"india": "IndianTrades", "us": "USTrades"}
_TICKERS = {"india": "IndianTickers", "us": "USTickers"}

# Weight profiles: group -> weight (sum ~ 1.0). Within a group all checks share weight.
_PROFILES = {
    "swing": {"tech": 0.65, "catalyst": 0.15, "track": 0.10, "fund": 0.05, "est": 0.05},
    "positional": {"fund": 0.50, "tech": 0.30, "est": 0.10, "track": 0.05, "catalyst": 0.05},
}

_POSITIVE_TAGS = {"NEW-BIZ", "M&A", "POLICY", "DEMAND-PRICING", "SPINOFF"}
_BUY_RATINGS = {"buy", "strong buy", "outperform", "overweight"}


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


def _rows_by_symbol(conn, table: str, symbols: list[str], cols: str, key: str = "Ticker") -> dict[str, Any]:
    """Return latest row per symbol for ``table`` keyed by symbol. Handles IN batching."""
    out: dict[str, Any] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"SELECT {cols} FROM dbo.{table} WHERE {key} IN ({ph})", batch
        ).fetchall()
        for r in rows:
            out[getattr(r, key)] = r
    return out


def _latest_stage(conn, market: str, symbols: list[str]) -> dict[str, Any]:
    """Latest stage-analysis row per symbol (Symbol key, most recent week)."""
    table = _t(_STAGE, market)
    out: dict[str, Any] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"""
            SELECT r.Symbol, r.IsStage2, r.RSScore, r.MomentumScore, r.Quadrant, r.ADClassification
            FROM dbo.{table} r
            JOIN (
                SELECT Symbol, MAX(WeekNumber) AS WeekNumber FROM dbo.{table}
                WHERE Symbol IN ({ph}) GROUP BY Symbol
            ) m ON m.Symbol = r.Symbol AND m.WeekNumber = r.WeekNumber
            """,
            batch,
        ).fetchall()
        for r in rows:
            out[r.Symbol] = r
    return out


def _track_records(conn, market: str) -> dict[str, dict[str, int]]:
    """Per-ticker win/total over closed trades + active-in-profit trades (feedback loop)."""
    table = _t(_TRADES, market)
    cur = conn.cursor()
    rows = cur.execute(
        f"""
        SELECT Ticker,
               SUM(CASE WHEN (Status='closed' AND PnLPct > 0)
                          OR (Status='active' AND PnLPct > 0) THEN 1 ELSE 0 END) AS Wins,
               SUM(CASE WHEN Status='closed' OR (Status='active' AND PnLPct IS NOT NULL) THEN 1 ELSE 0 END) AS Total
        FROM dbo.{table}
        GROUP BY Ticker
        """
    ).fetchall()
    return {r.Ticker: {"wins": int(r.Wins or 0), "total": int(r.Total or 0)} for r in rows}


# --- check construction -------------------------------------------------------
def _bool(v: Any) -> bool:
    return bool(v) if v is not None else False


def _build_checks(sym: str, *, tech, earn, sig, analyst, stage, series, track) -> list[dict]:
    """Return a list of checks: {group, weight(intra), applicable, bull}."""
    checks: list[dict] = []

    def add(group: str, weight: float, applicable: bool, bull: bool) -> None:
        checks.append({"group": group, "weight": weight, "applicable": bool(applicable), "bull": bool(bull)})

    close = _fin(getattr(tech, "Close", None)) if tech else None
    ema50 = ema200 = sma10 = ema20 = None
    if series is not None and series.n >= 1:
        last = series.last
        close = float(series.close[last]) if close is None else close
        if series.n >= 50:
            ema50 = float(series.ema(50)[last])
        if series.n >= 200:
            ema200 = float(series.ema(200)[last])

    # --- TECHNICAL ---
    add("tech", 1.0, stage is not None, _bool(getattr(stage, "IsStage2", None)) if stage else False)
    add("tech", 1.0, close is not None and ema50 is not None, bool(close and ema50 and close > ema50))
    add("tech", 1.0, close is not None and ema200 is not None, bool(close and ema200 and close > ema200))
    rs = _fin(getattr(tech, "Rs", None)) if tech else None
    add("tech", 1.0, rs is not None, bool(rs is not None and rs >= 70))
    mom = _fin(getattr(stage, "MomentumScore", None)) if stage else None
    add("tech", 1.0, mom is not None, bool(mom is not None and mom > 0))
    f52 = _fin(getattr(tech, "From52wHigh", None)) if tech else None
    add("tech", 1.0, f52 is not None, bool(f52 is not None and f52 >= -15))
    hits = _fin(getattr(tech, "ScannerHits", None)) if tech else None
    add("tech", 1.5, hits is not None, bool(hits is not None and hits >= 2))  # scanner-hit-count evidence
    quad = (getattr(stage, "Quadrant", None) or "") if stage else ""
    add("tech", 0.5, stage is not None, quad in ("leading", "improving"))
    adc = (getattr(stage, "ADClassification", None) or "") if stage else ""
    add("tech", 0.5, stage is not None, adc == "accumulating")

    # --- FUNDAMENTAL ---
    if earn is not None:
        eg = _fin(earn.EarningsGrowthYoyPct)
        add("fund", 1.0, eg is not None, bool(eg is not None and eg > 0))
        eq = _fin(earn.EarningsGrowthQoqPct)
        add("fund", 1.0, eq is not None, bool(eq is not None and eq > 0))
        rg = _fin(earn.RevenueGrowthYoyPct)
        add("fund", 1.0, rg is not None, bool(rg is not None and rg > 0))
        add("fund", 0.8, earn.OpmTrend is not None, (earn.OpmTrend or "") == "expanding")
        sp = _fin(earn.LastEpsSurprisePct)
        add("fund", 0.8, sp is not None, bool(sp is not None and sp > 0))
        add("fund", 1.0, earn.EarningsIncreasing is not None, _bool(earn.EarningsIncreasing))

    # --- CATALYST ---
    if sig is not None:
        add("catalyst", 1.0, sig.CapexTrend is not None, (sig.CapexTrend or "") == "rising")
        tags: set[str] = set()
        try:
            for item in json.loads(sig.NewsJson or "[]"):
                for tg in (item.get("tags") or []):
                    tags.add(str(tg).upper())
        except (ValueError, TypeError, AttributeError):
            pass
        add("catalyst", 1.0, sig.NewsJson is not None, bool(tags & _POSITIVE_TAGS))

    # --- ESTIMATE ---
    if analyst is not None:
        rating = (analyst.ConsensusRating or "").lower()
        add("est", 1.0, analyst.ConsensusRating is not None, rating in _BUY_RATINGS)
        cy = _fin(analyst.CurrentYearEps)
        ny = _fin(analyst.NextYearEps)
        add("est", 1.0, cy is not None and ny is not None, bool(cy and ny and cy > 0 and ny > cy))

    # --- TRACK RECORD ---
    if track and track.get("total", 0) >= 1:
        wins, total = track["wins"], track["total"]
        # represent as a graded check: passes if win-rate > 0.5
        add("track", float(min(total, 5)), True, (wins / total) > 0.5)

    return checks


def _score_profile(checks: list[dict], profile: str, freshness: float) -> dict[str, int]:
    """Compute bull/bear Wilson scores for one weight profile."""
    weights = _PROFILES[profile]
    bull_passes = bull_n = 0.0
    bear_passes = bear_n = 0.0
    for c in checks:
        if not c["applicable"]:
            continue
        gw = weights.get(c["group"], 0.0)
        if gw <= 0:
            continue
        decay = freshness if c["group"] == "fund" else 1.0
        w = gw * c["weight"] * decay
        if w <= 0:
            continue
        bull_n += w
        bear_n += w
        if c["bull"]:
            bull_passes += w
        else:
            bear_passes += w
    bull = round(100 * _wilson_lb(bull_passes, bull_n))
    bear = round(100 * _wilson_lb(bear_passes, bear_n))
    return {"bull": bull, "bear": bear}


def _side(bull: int, bear: int, is_fno: bool) -> str:
    if bull >= 60 and bull > bear:
        return "long"
    if bear >= 65 and is_fno:
        return "short"
    return "none"


def score_universe(conn, market: str, symbols: list[str], scan_date: date,
                   series_cache: dict[str, Any] | None = None) -> int:
    """Score every symbol and upsert into the {Market}StockScores table. Returns count."""
    if not symbols:
        return 0
    series_cache = series_cache or {}

    tech = _latest_tech(conn, market, symbols)
    earn = _rows_by_symbol(conn, _t(_EARN, market), symbols,
                           "Ticker, EarningsGrowthYoyPct, EarningsGrowthQoqPct, RevenueGrowthYoyPct, "
                           "OpmTrend, LastEpsSurprisePct, EarningsIncreasing, LastEarningsDate")
    sig = _rows_by_symbol(conn, _t(_SIG, market), symbols, "Ticker, CapexTrend, NewsJson")
    analyst = _latest_analyst(conn, market, symbols)
    stage = _latest_stage(conn, market, symbols)
    track = _track_records(conn, market)
    fno = _fno_set(conn, market, symbols)

    rows: list[tuple] = []
    for sym in symbols:
        t = tech.get(sym)
        e = earn.get(sym)
        s = sig.get(sym)
        a = analyst.get(sym)
        st = stage.get(sym)
        series = series_cache.get(sym)
        tr = track.get(sym)
        is_fno = sym in fno

        days_since = None
        freshness = 1.0
        if e is not None and e.LastEarningsDate is not None:
            days_since = (scan_date - e.LastEarningsDate).days
            if days_since >= 0:
                freshness = 0.5 ** (days_since / 30.0)

        checks = _build_checks(sym, tech=t, earn=e, sig=s, analyst=a, stage=st, series=series, track=tr)
        if not any(c["applicable"] for c in checks):
            continue

        swing = _score_profile(checks, "swing", freshness)
        pos = _score_profile(checks, "positional", freshness)

        # deterministic upside from projected EPS (yearly)
        upside_eps = None
        if a is not None:
            cy, ny = _fin(a.CurrentYearEps), _fin(a.NextYearEps)
            if cy and ny and cy > 0:
                upside_eps = round((ny / cy - 1) * 100, 2)

        hits = None
        if t is not None:
            hits = int(t.ScannerHits) if t.ScannerHits is not None else None

        comp = {
            "groups": _group_summary(checks),
            "freshness": round(freshness, 3),
        }

        rows.append((
            sym, scan_date, upside_eps,
            swing["bull"], _side(swing["bull"], swing["bear"], is_fno), swing["bull"], swing["bear"],
            pos["bull"], _side(pos["bull"], pos["bear"], is_fno), pos["bull"], pos["bear"],
            round(freshness, 6), days_since, hits, int(is_fno),
            json.dumps(comp, default=str),
        ))

    _upsert_scores(conn, market, rows)
    return len(rows)


def _latest_tech(conn, market: str, symbols: list[str]) -> dict[str, Any]:
    table = _t(_TECH, market)
    out: dict[str, Any] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"""
            SELECT t.Ticker, t.[Close], t.Rs, t.From52wHigh, t.High52w, t.ScannerHits
            FROM dbo.{table} t
            JOIN (SELECT Ticker, MAX(AsOfDate) AS AsOfDate FROM dbo.{table}
                  WHERE Ticker IN ({ph}) GROUP BY Ticker) m
              ON m.Ticker = t.Ticker AND m.AsOfDate = t.AsOfDate
            """,
            batch,
        ).fetchall()
        for r in rows:
            out[r.Ticker] = r
    return out


def _latest_analyst(conn, market: str, symbols: list[str]) -> dict[str, Any]:
    table = _t(_ANALYST, market)
    out: dict[str, Any] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"""
            SELECT a.Ticker, a.ConsensusRating, a.CurrentYearEps, a.NextYearEps,
                   a.CurrentQuarterEps, a.NextQuarterEps
            FROM dbo.{table} a
            JOIN (SELECT Ticker, MAX(AsOfDate) AS AsOfDate FROM dbo.{table}
                  WHERE Ticker IN ({ph}) GROUP BY Ticker) m
              ON m.Ticker = a.Ticker AND m.AsOfDate = a.AsOfDate
            """,
            batch,
        ).fetchall()
        for r in rows:
            out[r.Ticker] = r
    return out


def _fno_set(conn, market: str, symbols: list[str]) -> set[str]:
    table = _t(_TICKERS, market)
    out: set[str] = set()
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"SELECT Ticker FROM dbo.{table} WHERE IsFno = 1 AND Ticker IN ({ph})", batch
        ).fetchall()
        out.update(r.Ticker for r in rows)
    return out


def _group_summary(checks: list[dict]) -> dict[str, str]:
    agg: dict[str, list[int]] = {}
    for c in checks:
        if not c["applicable"]:
            continue
        a = agg.setdefault(c["group"], [0, 0])
        a[1] += 1
        if c["bull"]:
            a[0] += 1
    return {g: f"{p}/{n}" for g, (p, n) in agg.items()}


def _upsert_scores(conn, market: str, rows: list[tuple]) -> None:
    if not rows:
        return
    table = _t(_SCORES, market)
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            f"""
            MERGE dbo.{table} AS tgt
            USING (SELECT ? AS Ticker) AS src ON tgt.Ticker = src.Ticker
            WHEN MATCHED THEN UPDATE SET
                AsOfDate=?, UpsideEpsPct=?,
                SwingScore=?, SwingSide=?, SwingBull=?, SwingBear=?,
                PositionalScore=?, PositionalSide=?, PositionalBull=?, PositionalBear=?,
                FundFreshnessDecay=?, DaysSinceEarnings=?, ScannerHits=?, IsFno=?,
                ComponentsJson=?, ScoredAt=GETUTCDATE()
            WHEN NOT MATCHED THEN INSERT
                (Ticker, AsOfDate, UpsideEpsPct,
                 SwingScore, SwingSide, SwingBull, SwingBear,
                 PositionalScore, PositionalSide, PositionalBull, PositionalBear,
                 FundFreshnessDecay, DaysSinceEarnings, ScannerHits, IsFno, ComponentsJson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            # USING
            r[0],
            # UPDATE (skip Ticker)
            *r[1:],
            # INSERT (full)
            *r,
        )
    conn.commit()
