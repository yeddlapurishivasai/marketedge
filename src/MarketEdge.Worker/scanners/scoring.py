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

_Z = 1.28  # ~90% one-sided lower bound (less punitive than 1.64 so strong, well-evidenced setups score higher)

# Table maps -------------------------------------------------------------------
_EARN = {"india": "IndianEarningsFundamentals", "us": "USEarningsFundamentals"}
_SIG = {"india": "IndianStockSignals", "us": "USStockSignals"}
_ANALYST = {"india": "IndianAnalystSnapshot", "us": "USAnalystSnapshot"}
_TECH = {"india": "IndianTickerTechnical", "us": "USTickerTechnical"}
_STAGE = {"india": "IndianStageAnalysisResults", "us": "USStageAnalysisResults"}
_SCORES = {"india": "IndianStockScores", "us": "USStockScores"}
_TRADES = {"india": "IndianTrades", "us": "USTrades"}
_TICKERS = {"india": "IndianTickers", "us": "USTickers"}
_RESULTS = {"india": "IndianTechnicalScannerResults", "us": "USTechnicalScannerResults"}

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


def _scanner_reliability(conn, market: str) -> dict[str, dict[str, Any]]:
    """Per-scanner paper-trade success keyed by EntryScanner.

    A scanner's reliability is the Wilson lower bound of its realised win rate
    (closed trades), with active-in-profit trades counted as provisional wins so a
    young scanner isn't stuck at zero. This is the per-scanner feedback loop: setups
    from scanners that have actually paid off carry more weight in the stock score.
    """
    table = _t(_TRADES, market)
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
        out[r.EntryScanner] = {
            "closed": int(r.Closed or 0),
            "closedWins": int(r.ClosedWins or 0),
            "wins": wins,
            "total": total,
            "winRate": round(wins / total, 4) if total > 0 else None,
            "wilson": round(wilson, 4),
            "avgPnLPct": round(float(r.AvgPnL), 2) if r.AvgPnL is not None else None,
        }
    return out


def _scanner_flags(conn, market: str, symbols: list[str], scan_date: date) -> dict[str, list[str]]:
    """Distinct scanners that flagged each symbol on ``scan_date``."""
    table = _t(_RESULTS, market)
    out: dict[str, list[str]] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"""
            SELECT DISTINCT Symbol, ScannerName
            FROM dbo.{table}
            WHERE ScanDate = ? AND Symbol IN ({ph})
            """,
            [scan_date, *batch],
        ).fetchall()
        for r in rows:
            out.setdefault(r.Symbol, []).append(r.ScannerName)
    return out


# --- check construction -------------------------------------------------------
def _bool(v: Any) -> bool:
    return bool(v) if v is not None else False


def _build_checks(sym: str, *, tech, earn, sig, analyst, stage, series, track,
                  scanner_hits: int | None = None,
                  flag_scanners: list[str] | None = None,
                  scanner_reliability: dict[str, dict] | None = None) -> list[dict]:
    """Return a list of checks: {group, weight(intra), applicable, bull, label}."""
    checks: list[dict] = []

    def add(group: str, weight: float, applicable: bool, bull: bool, label: str) -> None:
        checks.append({"group": group, "weight": weight, "applicable": bool(applicable),
                       "bull": bool(bull), "label": label})

    # --- TECHNICAL (screeners + stage-2 screener only; no raw indicators) ---
    # Raw technical indicators (EMAs, RS, distance-from-high) are intentionally NOT
    # scored here: they are already encoded by the technical scanners and the stage-2
    # screener. Technical evidence is therefore which patterns fired and how stage
    # analysis classifies the name -- "which pattern is performing" drives the score.
    add("tech", 1.0, stage is not None, _bool(getattr(stage, "IsStage2", None)) if stage else False,
        "In Stage-2 uptrend")
    mom = _fin(getattr(stage, "MomentumScore", None)) if stage else None
    add("tech", 1.0, mom is not None, bool(mom is not None and mom > 0), "Positive momentum (stage screen)")
    hits = scanner_hits if scanner_hits is not None else (
        _fin(getattr(tech, "ScannerHits", None)) if tech else None)
    add("tech", 1.5, hits is not None, bool(hits is not None and hits >= 2),
        "Flagged by 2+ scanners")  # scanner-hit-count evidence
    # Scanner *quality*: weight a flagging scanner by its own paper-trade success.
    rel = scanner_reliability or {}
    flags = flag_scanners or []
    rated = [(s, rel[s]) for s in flags if s in rel and rel[s].get("total", 0) >= 3]
    if rated:
        best_name, best = max(rated, key=lambda kv: kv[1]["wilson"])
        win_pct = round((best["winRate"] or 0) * 100)
        # weight scales with the best scanner's reliability (1.0 .. 2.5)
        qweight = 1.0 + 1.5 * best["wilson"]
        add("tech", qweight, True, best["wilson"] >= 0.5,
            f"Triggered by proven scanner ({best_name}: {win_pct}% win, {best['total']} trades)")
    quad = (getattr(stage, "Quadrant", None) or "") if stage else ""
    add("tech", 0.5, stage is not None, quad in ("leading", "improving"), "RRG leading/improving")
    adc = (getattr(stage, "ADClassification", None) or "") if stage else ""
    add("tech", 0.5, stage is not None, adc == "accumulating", "Under accumulation")

    # --- FUNDAMENTAL ---
    if earn is not None:
        eg = _fin(earn.EarningsGrowthYoyPct)
        add("fund", 1.0, eg is not None, bool(eg is not None and eg > 0), "Earnings growth YoY > 0")
        eq = _fin(earn.EarningsGrowthQoqPct)
        add("fund", 1.0, eq is not None, bool(eq is not None and eq > 0), "Earnings growth QoQ > 0")
        rg = _fin(earn.RevenueGrowthYoyPct)
        add("fund", 1.0, rg is not None, bool(rg is not None and rg > 0), "Revenue growth YoY > 0")
        add("fund", 0.8, earn.OpmTrend is not None, (earn.OpmTrend or "") == "expanding",
            "Operating margin expanding")
        sp = _fin(earn.LastEpsSurprisePct)
        add("fund", 0.8, sp is not None, bool(sp is not None and sp > 0), "Positive EPS surprise")
        add("fund", 1.0, earn.EarningsIncreasing is not None, _bool(earn.EarningsIncreasing),
            "Earnings increasing")

    # --- CATALYST ---
    if sig is not None:
        add("catalyst", 1.0, sig.CapexTrend is not None, (sig.CapexTrend or "") == "rising",
            "Capex trend rising")
        tags: set[str] = set()
        try:
            for item in json.loads(sig.NewsJson or "[]"):
                for tg in (item.get("tags") or []):
                    tags.add(str(tg).upper())
        except (ValueError, TypeError, AttributeError):
            pass
        add("catalyst", 1.0, sig.NewsJson is not None, bool(tags & _POSITIVE_TAGS),
            "Positive news catalyst")

    # --- ESTIMATE ---
    if analyst is not None:
        rating = (analyst.ConsensusRating or "").lower()
        add("est", 1.0, analyst.ConsensusRating is not None, rating in _BUY_RATINGS,
            "Analyst consensus = Buy")
        cy = _fin(analyst.CurrentYearEps)
        ny = _fin(analyst.NextYearEps)
        add("est", 1.0, cy is not None and ny is not None, bool(cy and ny and cy > 0 and ny > cy),
            "Forward EPS growth expected")

    # --- TRACK RECORD ---
    if track and track.get("total", 0) >= 1:
        wins, total = track["wins"], track["total"]
        # represent as a graded check: passes if win-rate > 0.5
        add("track", float(min(total, 5)), True, (wins / total) > 0.5,
            f"Paper-trade win rate ({wins}/{total})")

    return checks


def _score_profile(checks: list[dict], profile: str, freshness: float) -> dict[str, Any]:
    """Compute bull/bear Wilson scores for one weight profile, with explainability detail."""
    weights = _PROFILES[profile]
    bull_passes = bull_n = 0.0
    bear_passes = bear_n = 0.0
    contribs: list[dict] = []
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
        contribs.append({
            "label": c["label"],
            "group": c["group"],
            "pass": bool(c["bull"]),
            "weight": round(w, 4),
        })
    bull_phat = (bull_passes / bull_n) if bull_n > 0 else 0.0
    bull = round(100 * _wilson_lb(bull_passes, bull_n))
    bear = round(100 * _wilson_lb(bear_passes, bear_n))
    return {
        "bull": bull,
        "bear": bear,
        "phat": round(bull_phat, 4),
        "n": round(bull_n, 4),
        "z": _Z,
        "contribs": contribs,
    }


def _side(bull: int, bear: int, is_fno: bool) -> str:
    if bull >= 60 and bull > bear:
        return "long"
    if bear >= 65 and is_fno:
        return "short"
    return "none"


def score_universe(conn, market: str, symbols: list[str], scan_date: date,
                   series_cache: dict[str, Any] | None = None,
                   scanner_hits: dict[str, int] | None = None) -> int:
    """Score every symbol and upsert into the {Market}StockScores table. Returns count."""
    if not symbols:
        return 0
    series_cache = series_cache or {}
    if scanner_hits is None:
        scanner_hits = _scanner_hit_counts(conn, market, symbols, scan_date)
    scanner_reliability = _scanner_reliability(conn, market)
    scanner_flags = _scanner_flags(conn, market, symbols, scan_date)

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
        hits = scanner_hits.get(sym)
        flags = scanner_flags.get(sym, [])

        days_since = None
        freshness = 1.0
        if e is not None and e.LastEarningsDate is not None:
            days_since = (scan_date - e.LastEarningsDate).days
            if days_since >= 0:
                freshness = 0.5 ** (days_since / 30.0)

        checks = _build_checks(sym, tech=t, earn=e, sig=s, analyst=a, stage=st, series=series,
                               track=tr, scanner_hits=hits, flag_scanners=flags,
                               scanner_reliability=scanner_reliability)
        if not any(c["applicable"] for c in checks):
            continue

        swing = _score_profile(checks, "swing", freshness)
        pos = _score_profile(checks, "positional", freshness)

        # deterministic upside from projected EPS (yearly), with earnings-growth fallback
        upside_eps, upside_src = _upside_eps(a, e)

        comp = {
            "groups": _group_summary(checks),
            "freshness": round(freshness, 3),
            "daysSinceEarnings": days_since,
            "scannerHits": hits,
            "upsideSource": upside_src,
            "scanners": [
                {
                    "name": s,
                    "winRate": scanner_reliability.get(s, {}).get("winRate"),
                    "wilson": scanner_reliability.get(s, {}).get("wilson"),
                    "trades": scanner_reliability.get(s, {}).get("total", 0),
                }
                for s in flags
            ],
            "swing": {
                "bull": swing["bull"], "bear": swing["bear"],
                "phat": swing["phat"], "n": swing["n"], "z": swing["z"],
                "contribs": swing["contribs"],
            },
            "positional": {
                "bull": pos["bull"], "bear": pos["bear"],
                "phat": pos["phat"], "n": pos["n"], "z": pos["z"],
                "contribs": pos["contribs"],
            },
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


def _upside_eps(analyst, earn) -> tuple[float | None, str | None]:
    """Possible upside % from projected EPS.

    Prefers analyst forward EPS (next FY vs current FY); falls back to the most recent
    reported earnings growth (YoY) when forward analyst estimates are unavailable, which
    is the common case for NSE names where yfinance does not return forward EPS.
    """
    if analyst is not None:
        cy, ny = _fin(analyst.CurrentYearEps), _fin(analyst.NextYearEps)
        if cy and ny and cy > 0:
            return round((ny / cy - 1) * 100, 2), "forward_eps"
    if earn is not None:
        eg = _fin(earn.EarningsGrowthYoyPct)
        if eg is not None:
            return round(eg, 2), "earnings_growth_yoy"
    return None, None


def _scanner_hit_counts(conn, market: str, symbols: list[str], scan_date: date) -> dict[str, int]:
    """Distinct scanners that flagged each symbol on ``scan_date`` (the day's hit count)."""
    table = _t(_RESULTS, market)
    out: dict[str, int] = {}
    if not symbols:
        return out
    cur = conn.cursor()
    for i in range(0, len(symbols), 1000):
        batch = symbols[i:i + 1000]
        ph = ",".join("?" * len(batch))
        rows = cur.execute(
            f"""
            SELECT Symbol, COUNT(DISTINCT ScannerName) AS Hits
            FROM dbo.{table}
            WHERE ScanDate = ? AND Symbol IN ({ph})
            GROUP BY Symbol
            """,
            [scan_date, *batch],
        ).fetchall()
        for r in rows:
            out[r.Symbol] = int(r.Hits or 0)
    return out


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
