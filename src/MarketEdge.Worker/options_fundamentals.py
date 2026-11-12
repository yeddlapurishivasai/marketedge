"""Use existing MarketEdge financials; never fetch or invent valuation inputs."""
from __future__ import annotations

import math
import json
import statistics
from datetime import date, timedelta

from options_data import rows


def canonical_symbol(symbol: str) -> str:
    return symbol.upper().removesuffix(".NS").removesuffix(".BO")


def load_constituents(conn, index_code: str, as_of: date) -> list[dict]:
    records = rows(conn.cursor().execute(
        """SELECT s.Id StockId,s.Symbol,s.CompanyName,s.BroadSector,w.WeightPct,w.Source,w.EffectiveDate WeightEffectiveDate,
            f.LatestQuarterEnd,f.RevenueGrowthYoyPct,f.EarningsGrowthYoyPct,f.Opm,f.OpmYoyQ,
            f.TrailingPe,f.ReportedSource,f.ReportedFetchedAt,f.UpdatedAt,f.LastEarningsDate,
            f.EpsQ1Date,f.EpsQ1Actual,f.EpsQ2Date,f.EpsQ2Actual,
            f.EpsQ3Date,f.EpsQ3Actual,f.EpsQ4Date,f.EpsQ4Actual,
            ai.AiAsOf,ai.AiModel,ai.AiQuarterEnd,ai.AiFairLow,ai.AiFairHigh,ai.AiPriceScenarios,
            analyst.AsOfDate AnalystAsOf,analyst.NumAnalysts,analyst.TargetLowPrice,
            analyst.TargetMeanPrice,analyst.TargetHighPrice
        FROM [options].[IndexConstituentHistory] w
        JOIN dbo.IndianStocks s ON s.Id=w.StockId
        LEFT JOIN dbo.IndianEarningsFundamentals f
            ON REPLACE(UPPER(f.Ticker),'.NS','')=REPLACE(UPPER(s.Symbol),'.NS','')
        OUTER APPLY (
            SELECT TOP 1 a.UpdatedAt AiAsOf,a.Model AiModel,
                JSON_VALUE(a.InputJson,'$.Fundamentals.LatestQuarterEnd') AiQuarterEnd,
                TRY_CONVERT(float,JSON_VALUE(a.AnalysisJson,'$.valuation.intrinsicValueLow')) AiFairLow,
                TRY_CONVERT(float,JSON_VALUE(a.AnalysisJson,'$.valuation.intrinsicValueHigh')) AiFairHigh,
                JSON_QUERY(a.AnalysisJson,'$.priceScenarios') AiPriceScenarios
            FROM dbo.AiStockAnalysisResults a
            WHERE a.Market='india' AND REPLACE(UPPER(a.Ticker),'.NS','')=REPLACE(UPPER(s.Symbol),'.NS','')
              AND a.UpdatedAt<=GETUTCDATE() AND a.UpdatedAt>=DATEADD(day,-62,?)
              AND ISJSON(a.InputJson)=1 AND ISJSON(a.AnalysisJson)=1
            ORDER BY a.UpdatedAt DESC,a.Id DESC
        ) ai
        OUTER APPLY (
            SELECT TOP 1 a.AsOfDate,a.NumAnalysts,a.TargetLowPrice,a.TargetMeanPrice,a.TargetHighPrice
            FROM dbo.IndianAnalystSnapshot a
            WHERE REPLACE(UPPER(a.Ticker),'.NS','')=REPLACE(UPPER(s.Symbol),'.NS','')
              AND a.AsOfDate<=? AND a.AsOfDate>=DATEADD(day,-62,?)
            ORDER BY a.AsOfDate DESC
        ) analyst
        WHERE w.IndexCode=? AND w.EffectiveDate=(
            SELECT MAX(EffectiveDate) FROM [options].[IndexConstituentHistory]
            WHERE IndexCode=? AND EffectiveDate<=?) AND w.EffectiveDate>=?""",
        as_of, as_of, as_of, index_code, index_code, as_of, as_of - timedelta(days=62),
    ))
    return records


def _number(value):
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _trailing_eps(record: dict, as_of: date) -> float | None:
    quarters = [(record.get(f"EpsQ{i}Date"), _number(record.get(f"EpsQ{i}Actual"))) for i in range(1, 5)]
    if any(day is None or day > as_of or value is None for day, value in quarters):
        return None
    quarters.sort(key=lambda pair: pair[0])
    gaps = [(quarters[i][0] - quarters[i - 1][0]).days for i in range(1, 4)]
    if (any(gap < 45 or gap > 140 for gap in gaps)
            or not 210 <= (quarters[-1][0] - quarters[0][0]).days <= 420
            or (as_of - quarters[-1][0]).days > 200):
        return None
    result = sum(pair[1] for pair in quarters)
    return result if result > 0 else None


def valuation(record: dict, peers: list[dict], live_quote: dict | None, as_of: date) -> dict:
    price = _number((live_quote or {}).get("last_price"))
    result = {
        "stockId": record["StockId"], "symbol": canonical_symbol(record["Symbol"]),
        "companyName": record["CompanyName"], "weightPct": _number(record["WeightPct"]),
        "sector": record.get("BroadSector"), "livePrice": price,
        "livePriceAt": (live_quote or {}).get("timestamp"),
        "fairLow": None, "fairBase": None, "fairHigh": None,
        "lowMovePct": None, "baseMovePct": None, "highMovePct": None,
        "method": "unavailable", "latestQuarterEnd": record.get("LatestQuarterEnd"),
        "source": record.get("ReportedSource"), "reason": None,
        "weightSource": record.get("Source"), "weightAsOf": record.get("WeightEffectiveDate"),
        "earningsGrowthYoyPct": _number(record.get("EarningsGrowthYoyPct")),
        "revenueGrowthYoyPct": _number(record.get("RevenueGrowthYoyPct")),
        "aiPriceForecasts": [],
    }
    quarter = record.get("LatestQuarterEnd")
    if not price or price <= 0:
        result["reason"] = "Live constituent price unavailable."
    elif not quarter or not 0 <= (as_of - quarter).days <= 200:
        result["reason"] = "Financial reporting period missing or older than 200 days."
    else:
        ai_as_of = record.get("AiAsOf")
        ai_quarter = record.get("AiQuarterEnd")
        ai_low, ai_high = _number(record.get("AiFairLow")), _number(record.get("AiFairHigh"))
        ai_valid = (ai_as_of is not None and 0 <= (as_of - ai_as_of.date()).days <= 62
                    and ai_as_of.date() >= max(quarter, record.get("LastEarningsDate") or quarter)
                    and str(ai_quarter) == quarter.isoformat()
                    and ai_low is not None and ai_high is not None and 0 < ai_low <= ai_high)
        if ai_valid:
            result.update({
                "fairLow": ai_low, "fairBase": (ai_low + ai_high) / 2, "fairHigh": ai_high,
                "method": "stored-stock-AI-valuation", "valuationSource": "existing-ai",
                "valuationAsOf": ai_as_of, "aiModel": record.get("AiModel"),
                "reason": "Reused stored MarketEdge AI intrinsic-value bounds for the same latest reporting quarter; "
                          "base is their deterministic midpoint, not a new inference or a short-term target.",
            })
            scenarios = json.loads(record.get("AiPriceScenarios") or "[]")
            for scenario in scenarios if isinstance(scenarios, list) else []:
                if not isinstance(scenario, dict):
                    continue
                targets = [_number(scenario[case].get("target")) if isinstance(scenario.get(case), dict) else None
                           for case in ("bear", "base", "bull")]
                if (scenario.get("horizon") in ("3m", "6m", "1y", "2y")
                        and all(value is not None and value > 0 for value in targets)
                        and targets[0] <= targets[1] <= targets[2]):
                    result["aiPriceForecasts"].append(dict(zip(("horizon", "low", "base", "high"),
                                                              (scenario["horizon"], *targets))))
        else:
            analyst_date, earnings_date = record.get("AnalystAsOf"), record.get("LastEarningsDate")
            targets = [_number(record.get(field)) for field in ("TargetLowPrice", "TargetMeanPrice", "TargetHighPrice")]
            if (analyst_date and earnings_date and quarter <= earnings_date <= analyst_date <= as_of
                    and (as_of - analyst_date).days <= 62 and (_number(record.get("NumAnalysts")) or 0) > 0
                    and all(value is not None and value > 0 for value in targets)
                    and targets[0] <= targets[1] <= targets[2]):
                result.update({
                    "fairLow": targets[0], "fairBase": targets[1], "fairHigh": targets[2],
                    "method": "stored-analyst-12m-targets", "valuationSource": "analyst-consensus",
                    "valuationAsOf": analyst_date, "analystCount": int(record["NumAnalysts"]),
                    "reason": "Existing analyst targets captured after the stored results announcement. "
                              "Twelve-month context, not a Daily/Weekly/Monthly price target.",
                })
        if result["fairBase"] is not None:
            for label in ("Low", "Base", "High"):
                result[label.lower() + "MovePct"] = round((result["fair" + label] / price - 1) * 100, 2)
            return result
    if result["reason"] is not None:
        return result
    if any(term in (record.get("BroadSector") or "").lower()
             for term in ("bank", "financial", "insurance")):
        result["reason"] = "Sector requires book value/ROE or another specialised model; inputs not stored."
    else:
        eps = _trailing_eps(record, as_of)
        sector = record.get("BroadSector")
        multiples = [
            float(peer["TrailingPe"]) for peer in peers
            if sector and peer.get("BroadSector") == sector
            and peer["StockId"] != record["StockId"] and _number(peer.get("TrailingPe")) is not None
            and 0 < float(peer["TrailingPe"]) <= 100
            and peer.get("LatestQuarterEnd") and 0 <= (as_of - peer["LatestQuarterEnd"]).days <= 200
        ]
        if eps is None:
            result["reason"] = "Four distinct valid reported EPS quarters with positive trailing earnings are required."
        elif len(multiples) < 3:
            result["reason"] = "At least three valid same-sector peer P/E observations are required."
        else:
            quantiles = statistics.quantiles(multiples, n=4, method="inclusive")
            for label, multiple in zip(("Low", "Base", "High"), (quantiles[0], statistics.median(multiples), quantiles[2])):
                fair = eps * multiple
                result["fair" + label] = round(fair, 2)
                result[label.lower() + "MovePct"] = round((fair / price - 1) * 100, 2)
            result.update({
                "method": "trailing-EPS/sector-peer-PE",
                "valuationSource": "reported-EPS/peer-PE", "valuationAsOf": record.get("ReportedFetchedAt"),
                "trailingEps": eps, "peerCount": len(multiples),
                "reason": "Reported EPS is unadjusted: exceptional-item normalisation is unavailable. "
                          "Peer P/E is the stored latest snapshot, not historical point-in-time evidence.",
            })
    return result


def fundamental_index_range(spot: float, valuations: list[dict]) -> dict:
    """Covered-weight revaluation sensitivity, without extrapolating missing stocks."""
    result = {
        "status": "unavailable", "low": None, "base": None, "high": None, "coverage": 0.0,
        "aiCoverage": 0.0, "analystCoverage": 0.0, "mathematicalCoverage": 0.0,
        "uncoveredWeightPct": 100.0,
        "horizon": "structural-not-run-target", "sensitivityOnly": True,
        "method": "spot * (1 + sum(weight * (constituent value / captured constituent price - 1)))",
        "reason": "No covered current-quarter valuation inputs; missing values are not invented.",
    }
    if not valuations:
        return result
    weights = [_number(value.get("weightPct")) for value in valuations]
    if (any(weight is None or weight <= 0 for weight in weights)
            or abs(sum(weights) - 100) > 0.05
            or len({value["stockId"] for value in valuations}) != len(valuations)):
        raise ValueError("A complete unique constituent weight snapshot totalling 100% is required.")
    contributions = {key: 0.0 for key in ("low", "base", "high")}
    for value, weight in zip(valuations, weights):
        bounds = [_number(value.get("fair" + label)) for label in ("Low", "Base", "High")]
        price = _number(value.get("livePrice"))
        if not price or price <= 0 or any(bound is None or bound <= 0 for bound in bounds):
            continue
        if not bounds[0] <= bounds[1] <= bounds[2]:
            raise ValueError("Constituent valuation bounds must be ordered.")
        fraction = weight / 100
        result["coverage"] += fraction
        source_key = {"stored-stock-AI-valuation": "aiCoverage",
                      "stored-analyst-12m-targets": "analystCoverage"}.get(value["method"], "mathematicalCoverage")
        result[source_key] += fraction
        value["indexContributions"] = {}
        for key, bound in zip(contributions, bounds):
            impact = spot * fraction * (bound / price - 1)
            contributions[key] += impact
            value["indexContributions"][key] = impact
    if result["coverage"]:
        result.update({key: spot + impact for key, impact in contributions.items()})
        result["status"] = "available" if result["coverage"] >= 0.9999 else "partial"
        result["reason"] = ("Structural covered-weight sensitivity only, not a forecast for the run target. "
                            "Uncovered constituents are held at captured prices, not assigned a neutral valuation; "
                            "no long-horizon target is compressed into the next trading day or expiry.")
    result["uncoveredWeightPct"] = max(0, 100 * (1 - result["coverage"]))
    return result


def constituent_context(records: list[dict], quotes: dict, as_of: date) -> tuple[list[dict], list[dict]]:
    valuations = []
    weighted_growth = []
    weighted_revenue = []
    weighted_margin = []
    advances = []
    for record in records:
        symbol = canonical_symbol(record["Symbol"])
        quote = quotes.get("NSE:" + symbol)
        valuations.append(valuation(record, records, quote, as_of))
        weight = float(record["WeightPct"]) / 100
        quarter = record.get("LatestQuarterEnd")
        if quarter and 0 <= (as_of - quarter).days <= 200:
            growth = _number(record.get("EarningsGrowthYoyPct"))
            if growth is not None:
                weighted_growth.append((weight, growth))
            revenue = _number(record.get("RevenueGrowthYoyPct"))
            if revenue is not None:
                weighted_revenue.append((weight, revenue))
            margin, previous_margin = _number(record.get("Opm")), _number(record.get("OpmYoyQ"))
            is_financial = any(term in (record.get("BroadSector") or "").lower()
                               for term in ("bank", "financial", "insurance"))
            if margin is not None and previous_margin is not None and not is_financial:
                weighted_margin.append((weight, margin - previous_margin))
        previous = _number((quote or {}).get("ohlc", {}).get("close"))
        price = _number((quote or {}).get("last_price"))
        if previous and price:
            advances.append((weight, (price / previous - 1) * 100, symbol))

    fundamentals = {
        "key": "fundamentals", "label": "Constituent fundamentals", "scale": "Bearish to bullish",
        "score": None, "coverage": round(sum(w for w, _ in weighted_growth), 4), "indicators": [],
    }
    metrics = [
        ("weighted-earnings-growth", "Covered-weight earnings growth YoY", "%", 0.5, 30, weighted_growth),
        ("weighted-revenue-growth", "Covered-weight revenue growth YoY", "%", 0.3, 20, weighted_revenue),
        ("weighted-margin-expansion", "Covered non-financial operating-margin change YoY", "percentage points",
         0.2, 5, weighted_margin),
    ]
    available_weight = sum(factor_weight * sum(w for w, _ in observations)
                           for _, _, _, factor_weight, _, observations in metrics)
    if available_weight:
        contribution_sum = 0.0
        for key, label, unit, factor_weight, scale, observations in metrics:
            coverage = sum(w for w, _ in observations)
            if not coverage:
                continue
            value = sum(w * number for w, number in observations) / coverage
            contribution = factor_weight * coverage / available_weight * 50 * math.tanh(value / scale)
            contribution_sum += contribution
            fundamentals["indicators"].append({
                "key": key, "label": label, "value": round(value, 2), "unit": unit,
                "direction": "bullish" if value > 0 else "bearish" if value < 0 else "neutral",
                "contribution": round(contribution, 2), "coverage": round(coverage, 4),
                "reason": "Latest valid stored results; available factor weights are coverage-adjusted and renormalised.",
            })
        fundamentals["score"] = round(50 + contribution_sum, 2)
        fundamentals["coverage"] = round(available_weight, 4)
    else:
        fundamentals["reason"] = "No current covered-weight earnings observations."

    breadth = {
        "key": "breadth", "label": "Index breadth", "scale": "Bearish to bullish",
        "score": None, "coverage": round(sum(w for w, _, _ in advances), 4), "indicators": [],
    }
    if advances:
        coverage = sum(w for w, _, _ in advances)
        advancing = sum(w for w, change, _ in advances if change > 0) / coverage * 100
        breadth["score"] = round(advancing, 2)
        breadth["indicators"] = [{
            "key": "weighted-advances", "label": "Covered advancing index weight",
            "value": round(advancing, 2), "unit": "%", "direction": "bullish" if advancing > 55 else "bearish" if advancing < 45 else "neutral",
            "contribution": round(advancing - 50, 2),
            "reason": "Compared with previous close using effective-dated weights.",
        }, {
            "key": "weighted-return", "label": "Approximate covered index return contribution",
            "value": round(sum(w * change for w, change, _ in advances), 3), "unit": "%",
            "direction": "context", "contribution": 0,
            "reason": "Approximation from supplied weight snapshot; not exact index replication.",
        }]
    else:
        breadth["reason"] = "Official weights or constituent quotes unavailable."
    return valuations, [fundamentals, breadth]
