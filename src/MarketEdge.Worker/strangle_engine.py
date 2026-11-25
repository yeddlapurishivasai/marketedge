"""Versioned, deterministic advisory rules. No broker or persistence side effects."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import copy
import re
from datetime import datetime, timedelta
from decimal import Decimal
from functools import reduce
from zoneinfo import ZoneInfo

from options_engine import european_greeks, european_price, implied_volatility

IST = ZoneInfo("Asia/Kolkata")
YEAR = 365 * 86400
VERSION = "intraday-strangle-v1"


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def model_inputs(request, rate, dividend):
    values, sources = [], []
    for key, configured, lower in (("riskFreeRate", rate, -.05), ("dividendYield", dividend, 0)):
        supplied = request.get(key) is not None
        value = request[key] if supplied else configured
        values.append(value if number(value) and lower <= value <= 1 else None)
        sources.append("request" if supplied else "configuration" if configured is not None else "unavailable")
    return values[0], values[1], {
        "riskFreeRate": values[0], "dividendYield": values[1],
        "source": sources[0] if sources[0] == sources[1] else "mixed",
        "riskFreeRateSource": sources[0], "dividendYieldSource": sources[1],
    }


def selected_index_option(item, instruments, index):
    """Prefer exact metadata; fallback requires the exact index root followed by a digit."""
    key = identity(item)[:2]
    found = [i for i in instruments if identity(i)[:2] == key]
    if found:
        return (len(found) == 1 and found[0].get("underlying") == index and
                found[0].get("instrumentType") in ("CE", "PE"))
    exchange, root = ("NFO", "NIFTY") if index == "NIFTY50" else ("BFO", "SENSEX")
    return key[0] == exchange and bool(re.fullmatch(root + r"\d[A-Z0-9]*(?:CE|PE)", str(key[1])))


def timestamp(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(at, datetime):
        raise ValueError("Missing provider timestamp.")
    return at.replace(tzinfo=IST) if at.tzinfo is None else at


def identity(item):
    return (item["exchange"], item.get("tradingSymbol", item.get("tradingsymbol")), item.get("product", "MIS"))


def session_window(now, sessions):
    today = now.astimezone(IST).date().isoformat()
    session = next((s for s in sessions if s["sessionDate"] == today), None)
    if not session or not session.get("isTradingDay") or not session.get("source"):
        raise ValueError("Verified exchange session unavailable; reference-only.")
    opened = timestamp(today + "T" + session["openTime"])
    closed = timestamp(today + "T" + session["closeTime"])
    deadline = min(timestamp(today + "T15:15:00"), closed - timedelta(minutes=15))
    if closed <= opened or deadline <= opened:
        raise ValueError("Invalid exchange session.")
    return opened, closed, deadline, deadline - timedelta(minutes=15)


def select_expiry(instruments, now):
    today = now.astimezone(IST).date().isoformat()
    expiries = sorted({i["expiry"] for i in instruments
                       if i["instrumentType"] in ("CE", "PE") and i["expiry"] >= today})
    if not expiries:
        raise ValueError("Nearest listed unexpired expiry unavailable.")
    return expiries[0]


def common_atm(instruments, expiry, spot):
    calls = {i["strike"] for i in instruments if i["expiry"] == expiry and i["instrumentType"] == "CE"}
    puts = {i["strike"] for i in instruments if i["expiry"] == expiry and i["instrumentType"] == "PE"}
    if not calls & puts:
        raise ValueError("Listed common ATM call/put unavailable.")
    return min(calls & puts, key=lambda s: (abs(s - spot), s))


def contract(instruments, expiry, strike, kind):
    found = [i for i in instruments if i["expiry"] == expiry and i["strike"] == strike
             and i["instrumentType"] == kind]
    if len(found) != 1:
        raise ValueError(f"Exact listed {kind} {strike:g} unavailable or ambiguous.")
    return found[0]


def short_strikes(spot, midpoint_sum):
    return math.floor((spot - midpoint_sum) / 100) * 100, math.ceil((spot + midpoint_sum) / 100) * 100


def select_legs(instruments, expiry, spot, premium, width):
    put, call = short_strikes(spot, premium)
    return _protected_specs(instruments, expiry, put, call, width)


def _protected_specs(instruments, expiry, put, call, width):
    hedges_put = [i["strike"] for i in instruments if i["expiry"] == expiry
                  and i["instrumentType"] == "PE" and i["strike"] <= put - width]
    hedges_call = [i["strike"] for i in instruments if i["expiry"] == expiry
                   and i["instrumentType"] == "CE" and i["strike"] >= call + width]
    if not hedges_put or not hedges_call:
        raise ValueError("Listed outward protective wings unavailable; wings cannot be narrowed.")
    return [(contract(instruments, expiry, put, "PE"), "sell"),
            (contract(instruments, expiry, call, "CE"), "sell"),
            (contract(instruments, expiry, max(hedges_put), "PE"), "buy"),
            (contract(instruments, expiry, min(hedges_call), "CE"), "buy")]


def entry_spec_choices(instruments, expiry, spot, premium, width):
    """Anchor plus one listed inward/outward move on either side: at most five baskets."""
    put, call = short_strikes(spot, premium)
    pairs = [(put, call)]
    for kind, anchor in (("PE", put), ("CE", call)):
        listed = {i["strike"] for i in instruments if i["expiry"] == expiry and i["instrumentType"] == kind}
        for outward in (True, False):
            sign = (-1 if kind == "PE" else 1) * (1 if outward else -1)
            neighbors = [s for s in listed if (s-anchor)*sign > 0 and
                         (s < spot if kind == "PE" else s > spot)]
            if neighbors:
                replacement = min(neighbors, key=lambda s: abs(s-anchor))
                pairs.append((replacement, call) if kind == "PE" else (put, replacement))
    choices = []
    for p, c in dict.fromkeys(pairs):
        try:
            choices.append(_protected_specs(instruments, expiry, p, c, width))
        except ValueError:
            continue
    return choices


def premium_balance(legs=(), *, directional=False):
    shorts = {kind: [l for l in legs if l.get("direction") == "sell" and l.get("instrumentType") == kind]
              for kind in ("PE", "CE")}
    put = shorts["PE"][0].get("bid") if len(shorts["PE"]) == 1 else None
    call = shorts["CE"][0].get("bid") if len(shorts["CE"]) == 1 else None
    valid = all(number(p) and p > 0 for p in (put, call))
    ratio = abs(put-call)/max(put, call) if valid else None
    preferred = valid and ratio <= .20
    reason = ("Fresh executable short bids are within the soft 20% similarity band." if preferred else
              "NIFTY 5-minute support/resistance directional protection overrides the soft premium preference."
              if valid and directional else
              "Risk, stress and liquidity gates take priority over premium similarity; equality is not mandatory."
              if valid else "Two individually qualified short CE/PE bid premiums are unavailable.")
    return {"putPremium": put, "callPremium": call, "imbalanceRatio": ratio,
            "preferred": bool(preferred), "reason": reason}


def _premium_score(balance):
    return (not balance["preferred"], balance["imbalanceRatio"] if balance["imbalanceRatio"] is not None else 2)


def exit_policy(result, now, stop=None, target=None, pnl=None, *, active=True):
    previous = result.get("exitPolicy") or {}
    policy = {"version": "sl-target-time-v1", "reason": None,
              "stopPnlRupees": stop if number(stop) else None,
              "targetPnlRupees": target if number(target) else None}
    if active:
        policy["reason"] = ("time" if result.get("exitAt") and now >= timestamp(result["exitAt"]) else
                            "stop-loss" if number(pnl) and number(stop) and pnl <= stop else
                            "target" if number(pnl) and number(target) and pnl >= target else None)
        if (policy["reason"] is None and result.get("mandatoryExit") and
                previous.get("reason") in ("stop-loss", "target", "time")):
            policy = dict(previous)
    result["exitPolicy"] = policy
    if policy["reason"]:
        result.update(mandatoryExit=True, eligible=False, executionStatus="exit-required", candidates=[])
    return policy


def qualified_quote(instrument, quotes, now, quantity):
    key = instrument["exchange"] + ":" + instrument["tradingSymbol"]
    q = quotes.get(key)
    if not q:
        raise ValueError(key + ": quote unavailable.")
    observed = timestamp(q.get("timestamp"))
    age = (now - observed).total_seconds()
    if age < -2 or age > 60:
        raise ValueError(key + ": stale/future option quote.")
    depth = q.get("depth") or {}
    buys, sells = depth.get("buy") or [], depth.get("sell") or []
    if not buys or not sells:
        raise ValueError(key + ": two-sided depth unavailable.")
    bid, ask = buys[0].get("price"), sells[0].get("price")
    if not all(number(v) and v > 0 for v in (bid, ask)) or bid > ask:
        raise ValueError(key + ": missing/crossed executable price.")
    if (ask - bid) / ((ask + bid) / 2) > .20:
        raise ValueError(key + ": relative spread exceeds 20%.")
    if any(not number(side[0].get("quantity")) or side[0]["quantity"] < quantity for side in (buys, sells)):
        raise ValueError(key + ": insufficient displayed lot depth.")
    tick = instrument.get("tickSize")
    if not number(tick) or tick <= 0 or min(bid, ask) < tick:
        raise ValueError(key + ": invalid tick size/premium.")
    if not number(q.get("volume")) or q["volume"] <= 0:
        raise ValueError(key + ": traded liquidity unavailable.")
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "bidQuantity": buys[0]["quantity"], "askQuantity": sells[0]["quantity"],
            "relativeSpread": (ask-bid)/((ask+bid)/2), "volume": q["volume"],
            "observedAt": observed.isoformat()}


def validate_spot(quote, now):
    if not quote or not number(quote.get("last_price")) or quote["last_price"] <= 0:
        raise ValueError("Underlying quote unavailable.")
    observed = timestamp(quote.get("timestamp"))
    if not -2 <= (now - observed).total_seconds() <= 30:
        raise ValueError("Underlying quote stale/future.")
    return quote["last_price"], observed


def completed_candles(candles, now, opened):
    selected = {}
    for c in candles:
        at = timestamp(c["date"])
        if opened <= at and at + timedelta(minutes=1) <= now:
            if at in selected:
                raise ValueError("Duplicate minute candles.")
            if not all(number(c.get(k)) and c[k] > 0 for k in ("open", "high", "low", "close")):
                raise ValueError("Invalid minute OHLC.")
            if c["high"] < max(c["open"], c["close"], c["low"]) or c["low"] > min(c["open"], c["close"]):
                raise ValueError("Inconsistent minute OHLC.")
            selected[at] = c
    ordered = sorted(selected.items())
    if len(ordered) < 16:
        raise ValueError("X unavailable: at least 15 completed current-session minute returns required.")
    if any((b[0] - a[0]).total_seconds() != 60 for a, b in zip(ordered, ordered[1:])):
        raise ValueError("X unavailable: minute history has gaps.")
    if (now - ordered[-1][0]).total_seconds() > 120:
        raise ValueError("X unavailable: completed minute history stale.")
    return [c for _, c in ordered]


def intraday_context(candles, spot, evidence=None, opened=None):
    closes = [c["close"] for c in candles]
    def ema(period):
        if len(closes) < period:
            return None
        value = sum(closes[:period]) / period
        for close in closes[period:]:
            value += 2 / (period + 1) * (close - value)
        return value
    fast, slow = ema(9), ema(21)
    direction = "unknown" if slow is None else ("up" if fast > slow and spot > fast
                                               else "down" if fast < slow and spot < fast else "mixed")
    opening = candles[:15]
    opening_complete = (len(opening) == 15 and
                        (opened is None or timestamp(opening[0]["date"]) == opened))
    recent = max(c["high"] for c in candles[-5:]) - min(c["low"] for c in candles[-5:])
    previous = max(c["high"] for c in candles[-10:-5]) - min(c["low"] for c in candles[-10:-5])
    trend = {"version": VERSION, "direction": direction, "ema9": fast, "ema21": slow,
             "completedMinutes": len(candles),
             "sampleStart": candles[0]["date"], "sampleEnd": candles[-1]["date"],
             "openingRangeSampleComplete": opening_complete,
             "openingRangeHigh": max(c["high"] for c in opening) if opening_complete else None,
             "openingRangeLow": min(c["low"] for c in opening) if opening_complete else None, "recentRange": recent,
             "rangeExpansion": recent / previous if previous else None,
             "recentReturn": closes[-1] / closes[-6] - 1}
    regime = {"version": "macro-context-only-v1", "state": "unavailable", "score": None,
              "macroEvidence": evidence or {}, "reason": "Macro index/VIX changes do not authorize a directional roll."}
    return trend, regime


def nifty_regime(snapshot, index, now):
    result = {"version": "nifty-selling-regime-v2", "status": "unavailable", "state": "unavailable",
              "chartState": "unavailable", "direction": "neutral",
              "score": None, "sourceIndexCode": "NIFTY50", "timeframe": "5minute",
              "signal": "unavailable", "support": None, "resistance": None,
              "openingRangeHigh": None, "openingRangeLow": None, "signalBar": None,
              "signalAt": None, "signalClose": None, "lastClose": None, "levelsAsOf": None, "observedAt": None,
              "completedBars": 0, "priorBarCount": 0, "reason": None,
              "rangeBounded": None, "rangeWidthPct": None, "rangeBreakConfirmed": False,
              "chartReason": None,
              "confirmationSupport": None, "confirmationResistance": None,
              "trueRangePoints": None, "referenceTrueRangePoints": None, "volatilityRatio": None,
              "thresholds": {"containedVolatilityRatioMax": 1.2, "expandingVolatilityRatioMin": 1.5,
                             "maxRangeWidthPct": .6, "rangeBreakConfirmBars": 2, "volatilityLookbackBars": 6,
                             "stressBudgetRatioMax": 1.0, "greekStressBudgetRatioMax": 1.0},
              "exposureRisk": {"status": "unavailable", "acceptable": None, "stressLossRupees": None,
                               "greekStressLossRupees": None, "remainingRiskBudgetRupees": None,
                               "stressBudgetRatio": None, "greekStressBudgetRatio": None,
                               "reason": "Portfolio Greek/stress qualification has not been calculated."},
              "basis": ("Verified NIFTY minute OHLC aggregated into complete 5-minute bars aligned 09:15 IST; "
                        "levels use prior up-to-six bars plus the first three-bar opening range, never the signal bar. "
                        "Risk-on means a bounded selling-friendly range with contained true range, not bullishness. "
                        "Two closes beyond prior confirmation levels or expanding true range mean risk-off. "
                        "Price direction is separate. Portfolio stress/Greek budget failure vetoes risk-on; "
                        "thresholds are disclosed heuristics, not probabilities."),
              "macroEvidence": snapshot.get("regimeEvidence") or {}}
    try:
        sessions = snapshot.get("sessions") if index == "NIFTY50" else snapshot.get("niftySessions")
        quote = snapshot.get("spotQuote") if index == "NIFTY50" else snapshot.get("niftySpotQuote")
        candles = snapshot.get("candles") if index == "NIFTY50" else snapshot.get("niftyCandles")
        opened, _, _, _ = session_window(now, sessions or [])
        if (opened.hour, opened.minute) != (9, 15):
            raise ValueError("NIFTY 09:15-aligned verified session unavailable.")
        _, observed = validate_spot(quote, now)
        minutes = completed_candles(candles or [], now, opened)
        if timestamp(minutes[0]["date"]) != opened:
            raise ValueError("NIFTY minute history does not start at the verified session open.")
        bars = []
        for offset in range(0, len(minutes)-4, 5):
            group = minutes[offset:offset+5]
            start = timestamp(group[0]["date"])
            if start+timedelta(minutes=5) > now:
                continue
            bars.append({"startAt": start.isoformat(), "endAt": (start+timedelta(minutes=5)).isoformat(),
                         "open": group[0]["open"], "high": max(c["high"] for c in group),
                         "low": min(c["low"] for c in group), "close": group[-1]["close"]})
        if len(bars) < 4:
            raise ValueError("At least four complete NIFTY 5-minute bars required; opening range excludes the signal.")
        signal, prior, opening = bars[-1], bars[max(0, len(bars)-7):-1], bars[:3]
        support = min([b["low"] for b in prior]+[b["low"] for b in opening])
        resistance = max([b["high"] for b in prior]+[b["high"] for b in opening])
        if signal["close"] > resistance and signal["close"] > signal["open"]:
            name, direction = "breakout", "up"
        elif signal["close"] < support and signal["close"] < signal["open"]:
            name, direction = "breakdown", "down"
        elif signal["low"] <= support < signal["close"] and signal["close"] > signal["open"]:
            name, direction = "support-rejection", "up"
        elif signal["high"] >= resistance > signal["close"] and signal["close"] < signal["open"]:
            name, direction = "resistance-rejection", "down"
        else:
            name, direction = "inside-range", "neutral"
        confirm_support, confirm_resistance, confirmed = None, None, False
        if len(bars) >= 5:
            history = bars[max(0, len(bars)-8):-2]
            confirm_support = min([b["low"] for b in history]+[b["low"] for b in opening])
            confirm_resistance = max([b["high"] for b in history]+[b["high"] for b in opening])
            if all(b["close"] > confirm_resistance for b in bars[-2:]):
                confirmed, direction = True, "up"
            elif all(b["close"] < confirm_support for b in bars[-2:]):
                confirmed, direction = True, "down"
        ranges = [max(b["high"]-b["low"],
                      abs(b["high"]-bars[n-1]["close"]) if n else 0,
                      abs(b["low"]-bars[n-1]["close"]) if n else 0) for n, b in enumerate(bars)]
        reference = statistics.median(ranges[max(0, len(ranges)-7):-1])
        ratio = ranges[-1]/reference if reference > 0 else None
        width_pct = (resistance-support)/signal["close"]*100
        bounded = support <= signal["close"] <= resistance and width_pct <= .6
        state = ("risk-off" if confirmed or (ratio is not None and ratio >= 1.5) else
                 "risk-on" if bounded and ratio is not None and ratio <= 1.2 else "mixed")
        chart_reason = ("Two completed closes confirm a break of prior NIFTY range levels." if confirmed else
                        "Latest NIFTY true range is at least 1.5 times the prior median." if ratio is not None and ratio >= 1.5 else
                        "NIFTY remains inside bounded prior levels with contained true range." if state == "risk-on" else
                        "NIFTY is neither a qualified contained range nor a confirmed/expanding-volatility break.")
        result.update(status="available", state=state, score={"risk-on": 1, "risk-off": -1, "mixed": 0}[state],
                      signal=name, support=support, resistance=resistance,
                      openingRangeHigh=max(b["high"] for b in opening),
                      openingRangeLow=min(b["low"] for b in opening), signalBar=signal,
                      signalAt=signal["endAt"], signalClose=signal["close"], lastClose=signal["close"],
                      levelsAsOf=prior[-1]["endAt"],
                      observedAt=observed.isoformat(), completedBars=len(bars), priorBarCount=len(prior),
                      chartState=state, direction=direction, rangeBounded=bounded, rangeWidthPct=width_pct,
                      chartReason=chart_reason,
                      rangeBreakConfirmed=confirmed, confirmationSupport=confirm_support,
                      confirmationResistance=confirm_resistance, trueRangePoints=ranges[-1],
                      referenceTrueRangePoints=reference, volatilityRatio=ratio)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["reason"] = str(exc)
    return result


def point_increment(legs):
    ticks = [Decimal(str(l["tickSize"])) for l in legs]
    scale = 10 ** max(-t.as_tuple().exponent for t in ticks)
    return reduce(math.lcm, [int(t * scale) for t in ticks]) / scale


def aggregate_greeks(legs, spot, now, expiry_at, rate, dividend):
    totals = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
    weighted = totals.copy()
    for leg in legs:
        g = european_greeks(spot, leg["strike"], (expiry_at - now).total_seconds() / YEAR,
                            rate, dividend, leg["iv"], leg["instrumentType"])
        if g is None:
            raise ValueError("Finite signed Greeks unavailable.")
        for k in totals:
            signed = (1 if leg["direction"] == "buy" else -1) * g[k]
            totals[k] += signed
            weighted[k] += signed * leg["quantity"]
    return {**totals, "quantityWeighted": weighted,
            "units": {"delta": "points per underlying point", "gamma": "points per underlying point squared",
                      "vega": "points per 1 absolute volatility percentage point", "theta": "points per calendar day"},
            "quantityWeightedUnits": "INR using matched underlying quantity, not number of legs"}


def stress_x(legs, candles, spot, now, expiry_at, deadline, rate, dividend):
    returns = [math.log(b["close"] / a["close"]) for a, b in zip(candles, candles[1:])]
    realized = statistics.stdev(returns) * math.sqrt(5) * spot
    recent = candles[-5:]
    last_range = max(c["high"] for c in recent) - min(c["low"] for c in recent)
    true_range = max(last_range, abs(max(c["high"] for c in recent) - candles[-6]["close"]),
                     abs(min(c["low"] for c in recent) - candles[-6]["close"]))
    move = max(realized, true_range)
    horizon = max(0, min(300, (deadline - now).total_seconds()))
    times = sorted({0, min(60, horizon), min(180, horizon), horizon})
    def fair(underlying, elapsed, shock):
        years = max(0, (expiry_at - now).total_seconds() - elapsed) / YEAR
        return sum((1 if l["direction"] == "buy" else -1) *
                   european_price(underlying, l["strike"], years, rate, dividend,
                                  max(.000001, l["iv"] + shock), l["instrumentType"]) for l in legs)
    initial = fair(spot, 0, 0)
    grid = [{"spotShift": shift, "elapsedSeconds": t, "ivShift": shock,
             "fairValuePnlPoints": fair(spot + shift, t, shock)-initial}
            for shift in (-move, 0, move) for t in times for shock in (-.01, 0, .01)]
    worst_scenario = min(grid, key=lambda row: row["fairValuePnlPoints"])
    worst = max(0, -worst_scenario["fairValuePnlPoints"])
    spread = sum(l["ask"] - l["bid"] for l in legs)
    increment = point_increment(legs)
    x = float((Decimal(str(worst + spread)) / Decimal(str(increment))).to_integral_value(rounding="ROUND_CEILING")
              * Decimal(str(increment)))
    if not number(x) or x <= 0:
        raise ValueError("Positive modeled X unavailable.")
    greeks = aggregate_greeks(legs, spot, now, expiry_at, rate, dividend)
    attribution = {
        "deltaPnlPoints": greeks["delta"]*worst_scenario["spotShift"],
        "gammaPnlPoints": .5*greeks["gamma"]*worst_scenario["spotShift"]**2,
        "vegaPnlPoints": greeks["vega"]*worst_scenario["ivShift"]*100,
        "thetaPnlPoints": greeks["theta"]*worst_scenario["elapsedSeconds"]/86400,
    }
    return x, {"version": VERSION, "returnCount": len(returns), "realizedFiveMinuteMove": realized,
               "latestFiveMinuteTrueRange": true_range, "stressMove": move,
               "ivShocks": [-.01, 0, .01], "elapsedSeconds": times, "spotShocks": [-move, 0, move],
               "ivFloor": .000001, "minimumCompletedReturns": 15, "maximumHorizonSeconds": 300,
               "worstFairValueLossPoints": worst, "roundTripSpreadAllowancePoints": spread,
               "pointIncrement": increment, "suggestedXPoints": x,
               "worstScenario": worst_scenario, "greekAttribution": attribution,
               "approximateGreekPnlPoints": sum(attribution.values()),
               "bestModeledFiveMinuteFairValuePnlPoints": max(row["fairValuePnlPoints"] for row in grid),
               "approximateRemainingThetaPoints": greeks["theta"]*max(0, (deadline-now).total_seconds())/86400,
               "grid": grid, "remainingSecondsToExit": max(0, (deadline-now).total_seconds()),
               "formula": "ceil_to_increment(max_positive_fair_value_grid_loss + sum(ask-bid))",
               "fees": "unknown; all P&L, target and risk values are gross before charges"}


def greek_stress_loss(greeks, calculation, quantity):
    """Conservative adverse Greek components across the same finite stress grid, in INR."""
    weighted = greeks.get("quantityWeighted") or {}
    if not all(number(weighted.get(k)) for k in ("delta", "gamma", "vega", "theta")):
        raise ValueError("Complete finite quantity-weighted Greeks required for SL defence.")
    grid = calculation.get("grid")
    if not isinstance(grid, list) or not grid:
        raise ValueError("Actual common stress grid unavailable for Greek qualification.")
    def adverse(row):
        pieces = (weighted["delta"]*row["spotShift"],
                  .5*weighted["gamma"]*row["spotShift"]**2,
                  weighted["vega"]*row["ivShift"]*100,
                  weighted["theta"]*row["elapsedSeconds"]/86400)
        return sum(max(0, -p) for p in pieces)
    loss = max(adverse(row) for row in grid)+calculation["roundTripSpreadAllowancePoints"]*quantity
    if not number(loss):
        raise ValueError("Finite Greek stress qualification unavailable.")
    return loss


def exposure_qualification(greeks, calculation, quantity, stress_rupees, budget):
    result = {"status": "unavailable", "acceptable": None, "stressLossRupees": stress_rupees,
              "greekStressLossRupees": None, "remainingRiskBudgetRupees": budget,
              "stressBudgetRatio": None, "greekStressBudgetRatio": None, "reason": None}
    try:
        if not number(stress_rupees) or stress_rupees < 0 or not number(budget) or budget <= 0:
            raise ValueError("Positive remaining SL budget and finite stress required.")
        loss = greek_stress_loss(greeks, calculation, quantity)
        acceptable = stress_rupees <= budget and loss <= budget
        result.update(status="available", acceptable=acceptable, greekStressLossRupees=loss,
                      stressBudgetRatio=stress_rupees/budget, greekStressBudgetRatio=loss/budget,
                      reason=None if acceptable else "Fair-value or adverse-component Greek stress exceeds the remaining SL budget.")
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["reason"] = str(exc)
    return result


def apply_exposure_regime(regime, qualification):
    regime["exposureRisk"] = qualification
    chart = regime["chartState"]
    regime["state"] = (chart if chart == "unavailable" else
                       "risk-off" if qualification.get("acceptable") is False else
                       "mixed" if chart == "risk-on" and qualification.get("acceptable") is not True else chart)
    regime["score"] = {"risk-on": 1, "risk-off": -1, "mixed": 0, "unavailable": None}[regime["state"]]
    return regime


def _veto_unqualified_regime(result):
    regime = result.get("regime")
    if regime and ((result.get("payoff") or {}).get("lossUnbounded") or
                   ("originalRiskBudget" in result and not result["positionsConfirmed"])):
        qualification = exposure_qualification({}, {}, 0, None, None)
        if (result.get("payoff") or {}).get("lossUnbounded"):
            qualification.update(acceptable=False, reason="Exact payoff has unbounded loss; risk-on exposure is vetoed.")
        apply_exposure_regime(regime, qualification)
    if (regime and regime.get("chartState") == "risk-on" and
            regime["exposureRisk"]["acceptable"] is not True and
            (result["legs"] or any(p.get("quantity") for p in result["positions"]))):
        apply_exposure_regime(regime, regime["exposureRisk"])


def payoff_bounds(legs, cash_points):
    strikes = sorted({0, *(l["strike"] for l in legs)})
    def payoff(s):
        return cash_points + sum((1 if l["direction"] == "buy" else -1) *
                                 max(0, s - l["strike"] if l["instrumentType"] == "CE" else l["strike"] - s)
                                 for l in legs)
    values = [payoff(s) for s in strikes]
    if sum((1 if l["direction"] == "buy" else -1) for l in legs if l["instrumentType"] == "CE") != 0:
        raise ValueError("Unbounded/unmatched call exposure.")
    return max(values), max(0, -min(values))


def modeled_legs(specs, quotes, spot, now, expiry_at, rate, dividend, quantity=None, *, actual_quantity=False):
    lots = {i["lotSize"] for i, _ in specs}
    exchanges = {i["exchange"] for i, _ in specs}
    if len(lots) != 1 or len(exchanges) != 1:
        raise ValueError("Mixed lot sizes or exchanges.")
    lot = next(iter(lots))
    if not isinstance(lot, int) or isinstance(lot, bool) or lot <= 0:
        raise ValueError("Invalid broker lot size.")
    quantity = lot if quantity is None else quantity
    if (not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or
            (not actual_quantity and quantity % lot)):
        raise ValueError("Quantity must be a positive number of complete lots.")
    legs = []
    for inst, direction in specs:
        q = qualified_quote(inst, quotes, now, quantity)
        years = (expiry_at - now).total_seconds() / YEAR
        if years <= 0:
            raise ValueError("Expired option; model unavailable.")
        lower = european_price(spot, inst["strike"], years, rate, dividend, 0, inst["instrumentType"])
        upper = (spot * math.exp(-dividend * years) if inst["instrumentType"] == "CE"
                 else inst["strike"] * math.exp(-rate * years))
        if q["bid"] <= lower or q["ask"] >= upper:
            raise ValueError("Arbitrage-invalid executable quote.")
        iv = implied_volatility(q["mid"], spot, inst["strike"], years,
                                rate, dividend, inst["instrumentType"])
        if iv is None:
            raise ValueError("Explicit finite IV/model inputs unavailable for " + inst["tradingSymbol"])
        legs.append({**inst, **q, "direction": direction, "quantity": quantity, "product": "MIS",
                     "signedQuantity": quantity if direction == "buy" else -quantity,
                     "optionType": inst["instrumentType"],
                     "entryPrice": q["ask"] if direction == "buy" else q["bid"],
                     "liquidationPrice": q["bid"] if direction == "buy" else q["ask"],
                     "priceBasis": "buy-at-ask/sell-at-bid", "iv": iv})
    return legs


def check_skew(legs, spot_at):
    times = [timestamp(l["observedAt"]) for l in legs] + [spot_at]
    if (max(times) - min(times)).total_seconds() > 5:
        raise ValueError("Cross-leg/underlying quote skew exceeds 5 seconds.")


def expiry_close(expiry, sessions):
    s = next((s for s in sessions if s["sessionDate"] == expiry and s.get("isTradingDay") and s.get("source")), None)
    if not s or not s.get("closeTime"):
        raise ValueError("Verified expiry session close unavailable; no model default.")
    return timestamp(expiry + "T" + s["closeTime"])


def unavailable(index_code, now):
    return {"formulaVersion": VERSION, "asOf": now.isoformat(), "indexCode": index_code,
            "sessionDate": now.astimezone(IST).date().isoformat(), "eligible": False, "mandatoryExit": False,
            "positionsConfirmed": False,
            "exitPolicy": {"version": "sl-target-time-v1", "reason": None,
                           "stopPnlRupees": None, "targetPnlRupees": None},
            "premiumBalance": premium_balance(),
            **dict.fromkeys(("expiry", "spot", "atmStrike", "atmPremiumSum", "shortPutStrike", "shortCallStrike",
                             "netCreditPoints", "quantity", "lotSize", "suggestedXPoints", "xPoints", "targetPoints",
                             "stopPoints", "greeks", "maxProfit", "maxLoss", "trend", "regime", "xCalculation",
                             "strategyPnlPoints", "strategyPnlRupees", "exitAt", "entryCutoffAt")),
            "legs": [], "positions": [], "candidates": [], "warnings": [
                "Advisory only: no orders or automatic monitoring/exit. Manage risk manually at the broker.",
                "All P&L and payoff values are gross before unknown fees; margin is not maximum loss."],
            "blockers": [], "margin": {"status": "unavailable", "reason": "Broker margin estimate not captured."},
            "executionStatus": "unavailable"}


def _with_status(result):
    _veto_unqualified_regime(result)
    result["mandatoryExit"] = result["executionStatus"] == "exit-required"
    result["eligible"] = result["executionStatus"] == "advisory-only" and not result["blockers"]
    return result


def _entry_selection(request, snapshot, now, spot, spot_at, expiry, expiry_at, premium, width,
                     candles, deadline, rate, dividend, regime):
    override = request.get("xOverride")
    if override is not None and (not number(override) or override <= 0):
        raise ValueError("X override must be finite and positive.")
    anchor_put, anchor_call = short_strikes(spot, premium)
    choices = entry_spec_choices(snapshot["instruments"], expiry, spot, premium, width)
    qualified, errors = [], []
    for specs in choices:
        try:
            legs = modeled_legs(specs, snapshot["quotes"], spot, now, expiry_at, rate, dividend)
            check_skew(legs, spot_at)
            credit = sum((1 if l["direction"] == "sell" else -1)*l["entryPrice"] for l in legs)
            profit, loss = payoff_bounds(legs, credit)
            stress, calculation = stress_x(legs, candles, spot, now, expiry_at, deadline, rate, dividend)
            x = stress if override is None else override
            if credit <= 0:
                raise ValueError("Basket is not a positive-credit strangle.")
            if override is not None and x <= calculation["roundTripSpreadAllowancePoints"]+1e-9:
                raise ValueError("X override is at or below initial executable round-trip spread loss; the stop is already hit on immediate liquidation.")
            if 2*x > profit:
                raise ValueError("2X target exceeds the theoretical gross profit cap.")
            qualified.append({"legs": legs, "credit": credit, "profit": profit, "loss": loss,
                              "stress": stress, "x": x, "calculation": calculation,
                              "put": specs[0][0]["strike"], "call": specs[1][0]["strike"],
                              "balance": premium_balance(legs, directional=regime["direction"] in ("up", "down"))})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            errors.append(str(exc))
    if not qualified:
        raise ValueError(errors[0] if errors else "No bounded listed anchor/neighbor entry basket available.")
    anchor = next((q for q in qualified if q["put"] == anchor_put and q["call"] == anchor_call), None)
    safe = [q for q in qualified if anchor is None or
            (q["loss"] <= anchor["loss"]+1e-9 and q["stress"] <= anchor["stress"]+1e-9)]
    def score(q):
        against_chart = ((regime["direction"] == "up" and q["call"] < anchor_call) or
                         (regime["direction"] == "down" and q["put"] > anchor_put))
        inward = q["put"] > anchor_put or q["call"] < anchor_call
        balance = q["balance"]
        return (against_chart, not balance["preferred"],
                0 if balance["preferred"] else balance["imbalanceRatio"], inward,
                abs(q["put"]-anchor_put)+abs(q["call"]-anchor_call), q["loss"], q["put"], q["call"])
    chosen = min(safe, key=score)
    chosen["selection"] = {"version": "bounded-premium-soft-v1", "evaluatedBaskets": len(choices),
                           "qualifiedBaskets": len(qualified), "nonIncreasingRiskBaskets": len(safe),
                           "anchorPutStrike": anchor_put, "anchorCallStrike": anchor_call,
                           "anchorMaxLossRupees": anchor["loss"]*anchor["legs"][0]["quantity"] if anchor else None,
                           "anchorStressLossRupees": anchor["stress"]*anchor["legs"][0]["quantity"] if anchor else None,
                           "anchorPremiumBalance": anchor["balance"] if anchor else premium_balance(),
                           "basis": ("At most five anchor/one-neighbor baskets; prefer similar short bids and outward strikes. "
                                     "Never increase qualified anchor contractual loss or modeled stress for balance; "
                                     "NIFTY chart bias may override similarity. Positive credit and 2X cap remain mandatory.")}
    return chosen


def build_entry(request, snapshot, now, *, rate=None, dividend=None):
    index = request["indexCode"]
    result = unavailable(index, now)
    rate, dividend, result["modelInputs"] = model_inputs(request, rate, dividend)
    result["regime"] = nifty_regime(snapshot, index, now)
    try:
        if index not in ("NIFTY50", "SENSEX"):
            raise ValueError("Unsupported index.")
        opened, _, deadline, cutoff = session_window(now, snapshot["sessions"])
        result.update(exitAt=deadline.isoformat(), entryCutoffAt=cutoff.isoformat())
        if now < opened or now >= deadline:
            result["executionStatus"] = "reference-only"
            raise ValueError("Outside verified live session/deadline; no current entry.")
        if now >= cutoff:
            raise ValueError("Entry cutoff reached; no new entry.")
        if not all(number(v) for v in (rate, dividend)):
            raise ValueError("Explicit risk-free rate and dividend yield required; model/X unavailable.")
        width = request.get("wingWidth")
        if not number(width) or width <= 0:
            raise ValueError("Positive explicit wing width required.")
        spot, spot_at = validate_spot(snapshot["spotQuote"], now)
        instruments = snapshot["instruments"]
        supported = {(i["exchange"], i["tradingSymbol"]) for i in instruments}
        result["positions"] = [p for p in snapshot.get("positions", {}).get("net", [])
                               if identity(p)[:2] in supported]
        expiry = select_expiry(instruments, now)
        expiry_at = expiry_close(expiry, snapshot["sessions"])
        atm = common_atm(instruments, expiry, spot)
        atm_quotes = [qualified_quote(contract(instruments, expiry, atm, k), snapshot["quotes"], now,
                                      contract(instruments, expiry, atm, k)["lotSize"]) for k in ("CE", "PE")]
        premium = sum(q["mid"] for q in atm_quotes)
        candles = completed_candles(snapshot.get("candles", []), now, opened)
        trend, _ = intraday_context(candles, spot, snapshot.get("regimeEvidence"), opened)
        regime = result["regime"]
        choice = _entry_selection(request, snapshot, now, spot, spot_at, expiry, expiry_at, premium, width,
                                  candles, deadline, rate, dividend, regime)
        legs = choice["legs"]
        check_skew(legs + atm_quotes, spot_at)
        credit, profit, loss = choice["credit"], choice["profit"], choice["loss"]
        qty = legs[0]["quantity"]
        result.update(expiry=expiry, spot=spot, atmStrike=atm, atmPremiumSum=premium,
                      atmPriceBasis="qualified-bid-ask-midpoint", shortPutStrike=choice["put"],
                      shortCallStrike=choice["call"], legs=legs, netCreditPoints=credit, quantity=qty,
                      premiumBalance=choice["balance"], premiumSelection=choice["selection"],
                      structure="hedged-strangle",
                      lotSize=legs[0]["lotSize"], maxProfit=profit*qty, maxLoss=loss*qty,
                      maxProfitPoints=profit, maxLossPoints=loss,
                      greeks=aggregate_greeks(legs, spot, now, expiry_at, rate, dividend))
        suggested, calculation = choice["stress"], choice["calculation"]
        override = request.get("xOverride")
        if override is not None and (not number(override) or override <= 0):
            raise ValueError("X override must be finite and positive.")
        x = choice["x"]
        result.update(suggestedXPoints=suggested, xPoints=x, targetPoints=2*x,
                      stopPoints=-x,
                      stopRiskRupees=x*qty, targetRupees=2*x*qty, hedgeCostPoints=sum(
                          l["entryPrice"] for l in legs if l["direction"] == "buy"),
                      trend=trend, regime=regime, xCalculation={**calculation, "xOverride": override,
                                                               "rate": rate, "dividendYield": dividend},
                      executionStatus="advisory-only")
        exit_policy(result, now, -x*qty, 2*x*qty, active=False)
        apply_exposure_regime(result["regime"], exposure_qualification(
            result["greeks"], calculation, qty, suggested*qty, x*qty))
        result["warnings"].append("X is a disclosed five-minute stress heuristic, not a backtested stop/probability.")
        result["warnings"].append("Remaining theta/time and favorable modeled scenarios do not establish probability of reaching 2X.")
        if calculation["bestModeledFiveMinuteFairValuePnlPoints"] - calculation["roundTripSpreadAllowancePoints"] < 2*x:
            result["warnings"].append("None of the disclosed five-minute stress scenarios reaches 2X after measured spreads; this is not a probability estimate.")
        if credit <= 0:
            result["blockers"].append("Basket is not a positive-credit strangle.")
        if override is not None and x <= calculation["roundTripSpreadAllowancePoints"] + 1e-9:
            result["blockers"].append("X override is at or below initial executable round-trip spread loss; the stop is already hit on immediate liquidation.")
        if 2*x > profit:
            result["blockers"].append("2X target exceeds the theoretical gross profit cap.")
        if result["blockers"]:
            result["executionStatus"] = "unavailable"
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["blockers"].append(str(exc))
    return _with_status(result)


def _structure(legs):
    shorts = {kind: [l for l in legs if l.get("instrumentType") == kind and l.get("direction") == "sell"]
              for kind in ("CE", "PE")}
    if len(legs) == 4 and len(shorts["CE"]) == len(shorts["PE"]) == 1:
        if shorts["PE"][0]["strike"] > shorts["CE"][0]["strike"]:
            return None
        return "hedged-straddle" if shorts["CE"][0]["strike"] == shorts["PE"][0]["strike"] else "hedged-strangle"
    return None


def _candidate(action, legs, reason, **details):
    stable = json.dumps({"action": action, "legs": legs}, sort_keys=True, separators=(",", ":"), default=str)
    structure = _structure(legs)
    return {"id": hashlib.sha256(stable.encode()).hexdigest()[:20], "action": action,
            "reason": reason, "legs": legs, "eligible": True, "premiumBalance": premium_balance(legs),
            **({"structure": structure} if structure else {}), **details}


def reconcile_baseline(baseline, cash_flows, instruments):
    """Validate a broker-derived ledger without modifying the frozen baseline."""
    if not isinstance(cash_flows, list) or not 1 <= len(cash_flows) <= 256:
        raise ValueError("A reconciliation requires 1..256 complete actual fill rows.")
    original = baseline["legs"]
    products = {l.get("product") for l in original}
    if len(products) != 1 or next(iter(products)) not in ("MIS", "NRML"):
        raise ValueError("A reconciliation requires one explicit confirmed product.")
    contracts = {(i["exchange"], i["tradingSymbol"]): i for i in instruments
                 if i["instrumentType"] in ("CE", "PE")}
    try:
        expiries = {contracts[identity(l)[:2]]["expiry"] for l in original}
        exchanges = {l["exchange"] for l in original}
    except KeyError:
        raise ValueError("Original baseline contracts unavailable from the current broker master.") from None
    if len(expiries) != 1 or len(exchanges) != 1:
        raise ValueError("Reconciliation must preserve one listed expiry/exchange.")
    verified, seen = [], set()
    for flow in cash_flows:
        if not isinstance(flow, dict):
            raise ValueError("Each actual fill must be an object.")
        trade_id = flow.get("tradeId")
        signed, price = flow.get("signedQuantity"), flow.get("price")
        if not isinstance(trade_id, str) or not trade_id.strip() or len(trade_id) > 100 or trade_id in seen:
            raise ValueError("Reconciliation trade IDs must be unique nonempty broker IDs.")
        if not isinstance(signed, int) or isinstance(signed, bool) or signed == 0 or not number(price) or price <= 0:
            raise ValueError("Reconciliation requires nonzero signed quantities and positive finite actual prices.")
        inst = contracts.get((flow.get("exchange"), flow.get("tradingSymbol")))
        if (not inst or inst["expiry"] not in expiries or flow.get("exchange") not in exchanges or
                flow.get("product") not in products):
            raise ValueError("Reconciliation fills must be listed same-index, same-expiry, same-product contracts.")
        seen.add(trade_id)
        verified.append({key: flow[key] for key in
                         ("tradeId", "exchange", "tradingSymbol", "product", "signedQuantity", "price")})
    reconciled = copy.deepcopy(baseline)
    reconciled["cashFlows"] = verified
    reconciled["ledgerSource"] = baseline.get("ledgerSource", "broker-fills")
    return reconciled


def reconcile_trade_ids(baseline, selected_ids, trades, instruments, now, *, prior=None):
    """Derive a full ledger from explicitly selected account broker fills."""
    prior = prior or {}
    prior_ids = set(prior.get("selectedTradeIds", []))
    reused_prior = selected_ids is None and bool(prior_ids)
    if reused_prior:
        selected_ids = sorted(prior_ids)
    if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 200 or
            any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in selected_ids) or
            len(set(selected_ids)) != len(selected_ids)):
        raise ValueError("Select 1..200 unique nonempty actual broker trade IDs.")
    wanted = set(selected_ids)
    if not prior_ids.issubset(wanted):
        raise ValueError("Selected IDs omit previously verified strategy fills; realized cash flows cannot be reset.")
    selected = [trade for trade in trades if str(trade.get("trade_id")) in wanted]
    if len(selected) != len(wanted) or {str(t.get("trade_id")) for t in selected} != wanted:
        raise ValueError("Selected broker trade IDs are missing or ambiguous in this account.")
    ordered = sorted(selected, key=lambda t: timestamp(t.get("fill_timestamp") or t.get("exchange_timestamp")))
    flows = []
    for trade in ordered:
        at = timestamp(trade.get("fill_timestamp") or trade.get("exchange_timestamp"))
        if (at.astimezone(IST).date().isoformat() != baseline["sessionDate"] or at > now or
                baseline["sessionDate"] != now.astimezone(IST).date().isoformat()):
            raise ValueError("Selected fills must belong to the confirmed current strategy session.")
        qty, price = trade.get("quantity"), trade.get("average_price")
        if (trade.get("transaction_type") not in ("BUY", "SELL") or
                not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0 or
                not number(price) or price <= 0):
            raise ValueError("Selected actual broker fill quantity/direction/price unavailable.")
        flows.append({
            "tradeId": str(trade["trade_id"]), "exchange": trade["exchange"],
            "tradingSymbol": trade["tradingsymbol"], "product": trade["product"],
            "signedQuantity": qty*(1 if trade["transaction_type"] == "BUY" else -1), "price": price,
        })
    reconciled = reconcile_baseline(baseline, flows, instruments)
    actual_by_id = {flow["tradeId"]: flow for flow in flows}
    for previous in prior.get("cashFlows", []):
        current = actual_by_id.get(previous["tradeId"])
        if (not current or identity(current) != identity(previous) or
                current["signedQuantity"] != previous["signedQuantity"] or
                abs(current["price"]-previous["price"]) > .000001):
            raise ValueError("A previously broker-verified fill changed; original realized cash flows cannot be reset.")
    reconciled["ledgerSource"] = "prior-verified-ledger" if reused_prior else "selected-trade-ids"
    reconciled["selectedTradeIds"] = sorted(wanted)
    reconciled["priorVerifiedTradeIds"] = sorted(prior_ids)
    return reconciled


def attributed_ledger(baseline, positions, trades, now):
    """Reconstruct cash from fills, never broker average prices after a roll."""
    if baseline.get("sessionDate") != now.astimezone(IST).date().isoformat():
        raise ValueError("Same-session confirmed baseline required; overnight strategies unsupported.")
    original = baseline["legs"]
    if len(original) != 4 or len({identity(l) for l in original}) != 4:
        raise ValueError("Four unambiguous baseline legs required.")
    if len({l.get("product") for l in original}) != 1 or original[0].get("product") not in ("MIS", "NRML"):
        raise ValueError("A confirmed strategy requires one explicit supported broker product.")
    qty = baseline["quantity"]
    if not isinstance(qty, int) or qty <= 0:
        raise ValueError("Invalid frozen baseline quantity.")
    flows = baseline.get("cashFlows")
    if flows is None:
        flows = [{"exchange": l["exchange"], "tradingSymbol": l["tradingSymbol"], "product": l["product"],
                  "signedQuantity": l.get("signedQuantity", l.get("quantity")), "price": l["entryPrice"]}
                 for l in original]
    if any(f.get("product") != original[0]["product"] for f in flows):
        raise ValueError("Cash-flow products must match the explicitly confirmed strategy product.")
    keys = {identity(f) for f in flows}
    selected = [t for t in trades if identity(t) in keys]
    if not selected:
        raise ValueError("Actual trade-fill cash-flow attribution unavailable; no invented strategy P&L/roll.")
    if any(identity(t)[:2] in {k[:2] for k in keys} and identity(t) not in keys for t in trades):
        raise ValueError("Mixed-product fills make strategy attribution ambiguous.")
    actual, expected, actual_cash, expected_cash = {}, {}, 0.0, 0.0
    trade_ids = [str(t.get("trade_id")) for t in selected]
    if len(set(trade_ids)) != len(trade_ids) or "None" in trade_ids:
        raise ValueError("Unique broker trade IDs required.")
    if baseline.get("cashFlows") is not None:
        flow_ids = [str(f.get("tradeId")) for f in flows]
        if len(set(flow_ids)) != len(flow_ids) or set(flow_ids) != set(trade_ids):
            raise ValueError("Roll ledger trade IDs incomplete or ambiguous.")
        by_id = {str(t["trade_id"]): t for t in selected}
        for f in flows:
            t = by_id[str(f["tradeId"])]
            signed = t["quantity"] * (1 if t["transaction_type"] == "BUY" else -1)
            if identity(f) != identity(t) or f["signedQuantity"] != signed or abs(f["price"] - t["average_price"]) > .000001:
                raise ValueError("Confirmed cash-flow ledger differs from broker fills.")
    else:
        directions = {identity(f): "BUY" if f["signedQuantity"] > 0 else "SELL" for f in flows}
        if any(t.get("transaction_type") != directions[identity(t)] for t in selected):
            raise ValueError("Realized rolls/shared round-trips require explicit trade-ID cash-flow attribution.")
    for t in selected:
        if t.get("product") != original[0]["product"]:
            raise ValueError("Broker fill product unavailable or inconsistent.")
        if t.get("transaction_type") not in ("BUY", "SELL"):
            raise ValueError("Invalid fill direction.")
        at = timestamp(t.get("fill_timestamp") or t.get("exchange_timestamp"))
        if at.astimezone(IST).date().isoformat() != baseline["sessionDate"] or at > now:
            raise ValueError("Fill outside confirmed strategy session.")
        if not isinstance(t.get("quantity"), int) or t["quantity"] <= 0 or not number(t.get("average_price")) or t["average_price"] <= 0:
            raise ValueError("Invalid actual fill quantity/price.")
        signed = t["quantity"] * (1 if t["transaction_type"] == "BUY" else -1)
        key = identity(t)
        actual[key] = actual.get(key, 0) + signed
        actual_cash -= signed * t["average_price"]
    if baseline.get("cashFlows") is not None:
        ordered = sorted(selected, key=lambda t: timestamp(t.get("fill_timestamp") or t.get("exchange_timestamp")))
        for leg in original:
            original_signed = leg.get("signedQuantity", leg.get("quantity"))
            if not isinstance(original_signed, int) or abs(original_signed) != qty:
                raise ValueError("Frozen original entry quantities are inconsistent.")
            filled, entry_cash = 0, 0.0
            for trade in ordered:
                if identity(trade) != identity(leg) or filled == qty:
                    continue
                direction = 1 if trade["transaction_type"] == "BUY" else -1
                if direction*original_signed <= 0 or filled+trade["quantity"] > qty:
                    raise ValueError("Actual original-entry fill attribution is ambiguous or incomplete.")
                filled += trade["quantity"]
                entry_cash += trade["quantity"]*trade["average_price"]
            if filled != qty or abs(entry_cash-qty*leg["entryPrice"]) > .01:
                raise ValueError("Actual original-entry cash flows differ from the frozen baseline; risk cannot be reset.")
    for f in flows:
        signed = f["signedQuantity"]
        if not isinstance(signed, int) or isinstance(signed, bool) or signed == 0 or not number(f["price"]) or f["price"] <= 0:
            raise ValueError("Invalid frozen cash-flow ledger.")
        key = identity(f)
        expected[key] = expected.get(key, 0) + signed
        expected_cash -= signed * f["price"]
    if actual != expected or abs(actual_cash - expected_cash) > .01:
        raise ValueError("Actual fills include partial fills/shared trades or unconfirmed rolls; attribution ambiguous.")
    net = positions.get("net")
    if not isinstance(net, list):
        raise ValueError("Broker net positions unavailable; day activity is not net exposure.")
    matching = [p for p in net if identity(p) in keys]
    if len({identity(p) for p in matching}) != len(matching):
        raise ValueError("Ambiguous duplicate net positions.")
    exposure = {identity(p): p["quantity"] for p in matching}
    for p in matching:
        if p.get("product") != original[0]["product"]:
            raise ValueError("Broker position product unavailable or inconsistent.")
        if p.get("overnight_quantity") != 0:
            raise ValueError("Overnight position exposure cannot be attributed to this intraday baseline.")
    if any(exposure.get(k, 0) != v for k, v in expected.items()):
        raise ValueError("Broker positions changed/partial fills; baseline requires explicit reconfirmation.")
    return {k: v for k, v in expected.items() if v}, actual_cash, matching


SESSION_SOURCE = "Broker today fills; FIFO realized gross ordered by fill timestamp then trade ID; never position average prices"
SESSION_SCOPE = "Authenticated account, today IST, selected-index options, all products; net only, never day plus net"
PAYOFF_BASIS = ("Exact piecewise expiry intrinsic payoff for S>=0 plus ENTIRE verified session cash flow, "
                "including closed trades; gross INR, not an intraday guaranteed target.")
RISK_BASIS = ("Recalculated session planning policy: positive finite expiry maximum profit / 2 (2:1); "
              "target = twice displayed session SL; only SL/target/time exits. "
              "Not an immutable original stop, guaranteed fill, or maximum contractual loss.")


def session_ledger(positions, trades, instruments, index, now):
    """Reconcile the entire index session before assigning any realized cash."""
    if not isinstance(trades, list):
        raise ValueError("Today broker trade ledger unavailable.")
    net = [p for p in positions["net"] if selected_index_option(p, instruments, index)]
    exposure = {}
    for p in net:
        key, qty = identity(p), p.get("quantity")
        if key in exposure or not isinstance(qty, int) or isinstance(qty, bool):
            raise ValueError("Duplicate or invalid selected-index net quantity.")
        if not isinstance(p.get("product"), str) or not p["product"]:
            raise ValueError("Explicit broker position product required.")
        if p.get("overnight_quantity") != 0:
            raise ValueError("Zero overnight exposure required for session accounting.")
        exposure[key] = qty
    today = now.astimezone(IST).date()
    fills, ids = [], set()
    for t in trades:
        if not selected_index_option(t, instruments, index):
            continue
        at = timestamp(t.get("fill_timestamp") or t.get("exchange_timestamp"))
        if at.astimezone(IST).date() < today:
            continue
        if at.astimezone(IST).date() != today or at > now:
            raise ValueError("Future/out-of-session trade timestamp.")
        trade_id = t.get("trade_id")
        if trade_id is None or not str(trade_id).strip() or str(trade_id) in ids:
            raise ValueError("Unique actual broker fill IDs required.")
        ids.add(str(trade_id))
        qty, price = t.get("quantity"), t.get("average_price")
        if (not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0 or
                not number(price) or price <= 0 or t.get("transaction_type") not in ("BUY", "SELL") or
                not isinstance(t.get("product"), str) or not t["product"]):
            raise ValueError("Invalid actual fill quantity, price, direction or product.")
        fills.append((at, str(trade_id), t))
    if not fills:
        raise ValueError("No usable today selected-index fill ledger; session totals unavailable.")
    inventory, actual, realized, cash = {}, {}, 0.0, 0.0
    for _, _, t in sorted(fills, key=lambda row: (row[0], row[1])):
        key = identity(t)
        signed = t["quantity"] * (1 if t["transaction_type"] == "BUY" else -1)
        price = t["average_price"]
        actual[key] = actual.get(key, 0) + signed
        cash -= signed * price
        queue = inventory.setdefault(key, [])
        remaining = signed
        while queue and queue[0][0] * remaining < 0:
            old_qty, old_price = queue[0]
            matched = min(abs(remaining), abs(old_qty))
            realized += matched * (price-old_price) * (1 if old_qty > 0 else -1)
            sign = 1 if remaining > 0 else -1
            remaining -= sign*matched
            old_qty += sign*matched
            if old_qty:
                queue[0] = (old_qty, old_price)
            else:
                queue.pop(0)
        if remaining:
            queue.append((remaining, price))
    if (not actual.keys() <= exposure.keys() or
            any(actual.get(k, 0) != exposure.get(k, 0) for k in actual.keys() | exposure.keys())):
        raise ValueError("Today fills do not match current net exposure, including closed rows; no assumed opening inventory.")
    if not all(number(v) for v in (realized, cash)):
        raise ValueError("Non-finite session cash accounting.")
    return {"cash": cash, "realized": realized, "tradeCount": len(fills),
            "inventory": inventory, "exposure": exposure}


def session_payoff(legs, cash, total=None):
    if not number(cash) or any(not number(l.get("strike")) or l["strike"] < 0 for l in legs):
        raise ValueError("Finite cash and nonnegative option strikes required.")
    expiries = {l["expiry"] for l in legs}
    if len(expiries) > 1:
        raise ValueError("Different open expiries cannot be treated as one expiry payoff.")
    strikes = sorted({0, *(l["strike"] for l in legs)})
    def value(spot):
        return cash + sum(l["signedQuantity"] * max(
            0, spot-l["strike"] if l["instrumentType"] == "CE" else l["strike"]-spot) for l in legs)
    values = [value(s) for s in strikes]
    if not all(number(v) for v in values):
        raise ValueError("Non-finite expiry payoff.")
    tail = sum(l["signedQuantity"] for l in legs if l["instrumentType"] == "CE")
    profit = None if tail > 0 else max(values)
    loss = None if tail < 0 else max(0, -min(values))
    return {"status": "available", "maxProfitRupees": profit, "maxLossRupees": loss,
            "profitUnbounded": tail > 0, "lossUnbounded": tail < 0,
            "remainingUpsideRupees": max(0, profit-total) if profit is not None and total is not None else None,
            "expiry": next(iter(expiries)) if expiries else None, "basis": PAYOFF_BASIS}


def invalidate_review(result, reason):
    result["eligible"] = False
    result["positionsConfirmed"] = False
    result["candidates"] = []
    result["greeks"] = None
    result["premiumBalance"] = {**premium_balance(), "reason": reason}
    qualification = exposure_qualification({}, {}, 0, None, None)
    qualification["reason"] = reason
    apply_exposure_regime(result["regime"], qualification)
    for risk in result.get("positionRisk", []):
        risk.pop("greeks", None)
        risk["unavailableReason"] = reason
    result["sessionPnl"].update(status="unavailable", realizedRupees=None, unrealizedRupees=None,
                                totalRupees=None, cashFlowRupees=None, closeValueRupees=None, reason=reason)
    result["payoff"].update(status="unavailable", maxProfitRupees=None, maxLossRupees=None,
                            remainingUpsideRupees=None, profitUnbounded=False, lossUnbounded=False, reason=reason)
    result["riskPlan"].update(status="unavailable", suggestedSlRupees=None, stopPnlRupees=None,
                              targetPnlRupees=None, remainingLossRupees=None, stressLossRupees=None, reason=reason)
    if not result["mandatoryExit"]:
        result["exitPolicy"].update(stopPnlRupees=None, targetPnlRupees=None)
    result.update(strategyPnlRupees=None, maxProfit=None, maxLoss=None, remainingRiskBudget=None)
    if not result["mandatoryExit"]:
        result["executionStatus"] = "unavailable"
    result["blockers"].append(reason)


def _with_review_status(result):
    _veto_unqualified_regime(result)
    candidates = result["candidates"]
    result["eligible"] = bool(
        result["executionStatus"] == "review-only" and not result["mandatoryExit"] and
        not result["blockers"] and result["positionsConfirmed"] and
        result["sessionPnl"]["status"] == "verified" and
        result["riskPlan"]["status"] == "available" and candidates and
        all(c.get("eligible") is True and c["action"] in ("hold", "roll")
            for c in candidates))
    return result


def _review_fallback(result, reason):
    result["warnings"].append(reason + " No safe hold/protective roll; review only, not an early-exit instruction.")
    if not result["mandatoryExit"] and result["positions"]:
        result["candidates"] = [{"id": "review-current-positions", "action": "review", "eligible": True,
                                 "legs": [], "reason": reason, "premiumBalance": result["premiumBalance"]}]
    else:
        result["candidates"] = []


def _rank_adjustments(result):
    result["candidates"].sort(key=lambda c: (c.get("stressLossRupees", math.inf), c.get("maxLoss", math.inf),
                                            *_premium_score(c["premiumBalance"]),
                                            c["action"] != "hold", c["id"]))
    for rank, candidate in enumerate(result["candidates"], 1):
        candidate["preferenceRank"] = rank
    result["warnings"].append("SL defence ranks by stress then contractual loss; premium similarity only breaks remaining ties. No added lots or wider risk.")


def build_position_review(request, snapshot, now, *, rate=None, dividend=None):
    result = unavailable(request["indexCode"], now)
    rate, dividend, result["modelInputs"] = model_inputs(request, rate, dividend)
    result["regime"] = nifty_regime(snapshot, request["indexCode"], now)
    standard_deadline = timestamp(now.astimezone(IST).date().isoformat() + "T15:15:00")
    result.update(analysisMode="current-positions", executionStatus="review-only",
                  asOf=snapshot.get("positionsObservedAt", now.isoformat()), positionRisk=[],
                  exitAt=standard_deadline.isoformat(),
                  sessionPnl={"status": "unavailable", "realizedRupees": None, "unrealizedRupees": None,
                              "totalRupees": None, "cashFlowRupees": None, "closeValueRupees": None,
                              "tradeCount": 0, "source": SESSION_SOURCE, "scope": SESSION_SCOPE},
                  payoff={"status": "unavailable", "maxProfitRupees": None, "maxLossRupees": None,
                          "profitUnbounded": False, "lossUnbounded": False, "remainingUpsideRupees": None,
                          "basis": PAYOFF_BASIS},
                  riskPlan={"status": "unavailable", "suggestedSlRupees": None, "stopPnlRupees": None,
                            "targetPnlRupees": None, "remainingLossRupees": None, "basis": RISK_BASIS})
    result["warnings"].append(RISK_BASIS + " No saved baseline or assumed original strategy is used.")
    metadata = snapshot.get("instruments", [])
    instruments = {identity(i)[:2]: i for i in metadata}
    net = snapshot.get("positions", {}).get("net")
    if not isinstance(net, list):
        reason = "Broker net positions unavailable; day activity is not net exposure."
        invalidate_review(result, reason)
        return _with_review_status(result)
    result["positions"] = [p for p in net if p.get("quantity") and
                           selected_index_option(p, metadata, request["indexCode"])]
    opened, deadline, cutoff, candles = None, standard_deadline, None, None
    try:
        opened, _, deadline, cutoff = session_window(now, snapshot["sessions"])
        result.update(exitAt=deadline.isoformat(), entryCutoffAt=cutoff.isoformat())
        if now < opened:
            raise ValueError("Before verified session open; reference-only.")
    except (ValueError, KeyError, TypeError) as exc:
        result["warnings"].append(str(exc))
        opened = None
    if result["positions"] and now >= deadline:
        result.update(executionStatus="exit-required", mandatoryExit=True)
        result["warnings"].append("MANDATORY MANUAL EXIT: session deadline reached; AI cannot override.")
    try:
        spot, spot_at = validate_spot(snapshot.get("spotQuote"), now)
        result["spot"] = spot
        if opened is None:
            raise ValueError("Verified session open unavailable.")
        candles = completed_candles(snapshot.get("candles", []), now, opened)
        result["trend"], _ = intraday_context(
            candles, spot, snapshot.get("regimeEvidence"), opened)
    except (ValueError, KeyError, TypeError) as exc:
        result["warnings"].append("Intraday context unavailable: " + str(exc))
    totals = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
    modeled, quoted, payoff_legs = [], [], []
    for p in result["positions"]:
        key, signed = identity(p)[:2], p["quantity"]
        inst = instruments.get(key)
        risk = {"exchange": key[0], "tradingSymbol": key[1], "product": p.get("product"),
                "signedQuantity": signed, "quantity": abs(signed) if number(signed) else None, "brokerPnlRupees": p.get("pnl"),
                "overnightQuantity": p.get("overnight_quantity")}
        result["positionRisk"].append(risk)
        try:
            if not inst or inst["instrumentType"] not in ("CE", "PE"):
                raise ValueError("Listed option metadata unavailable.")
            if not isinstance(signed, int) or isinstance(signed, bool) or not signed:
                raise ValueError("Invalid actual signed position quantity.")
            risk.update(strike=inst["strike"], expiry=inst["expiry"], optionType=inst["instrumentType"],
                        lotSize=inst["lotSize"], tickSize=inst["tickSize"])
            base = {**inst, "product": p["product"], "quantity": abs(signed), "signedQuantity": signed,
                    "direction": "buy" if signed > 0 else "sell"}
            payoff_legs.append(base)
            q = qualified_quote(inst, snapshot.get("quotes", {}), now, abs(signed))
            quoted.append({**base, **q, "liquidationPrice": q["bid"] if signed > 0 else q["ask"]})
            risk.update(bid=q["bid"], ask=q["ask"], quoteObservedAt=q["observedAt"])
            if not all(number(v) for v in (rate, dividend)):
                raise ValueError("Explicit supported risk-free rate and dividend yield required; Greeks unavailable.")
            if result["spot"] is None:
                raise ValueError("Fresh underlying quote unavailable; Greeks unavailable.")
            expiry_at = expiry_close(inst["expiry"], snapshot["sessions"])
            legs = modeled_legs([(inst, base["direction"])], snapshot["quotes"], result["spot"], now,
                                expiry_at, rate, dividend, abs(signed), actual_quantity=True)
            legs[0]["product"] = p["product"]
            check_skew(legs, spot_at)
            risk["greeks"] = aggregate_greeks(legs, result["spot"], now, expiry_at, rate, dividend)
            modeled.extend(legs)
            for name in totals:
                totals[name] += risk["greeks"]["quantityWeighted"][name]
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            risk["unavailableReason"] = str(exc)
    if len(modeled) == len(result["positions"]) and modeled:
        result["greeks"] = {"quantityWeighted": totals,
                            "quantityWeightedUnits": "INR across actual signed quantities; per-contract Greeks in positionRisk"}
        result["legs"] = modeled
        if _structure(modeled):
            result["structure"] = _structure(modeled)
    elif result["positions"]:
        result["warnings"].append("Portfolio Greeks unavailable: no partial portfolio totals are shown.")
    result["premiumBalance"] = premium_balance(quoted, directional=result["regime"]["direction"] in ("up", "down"))
    ledger = None
    try:
        ledger = session_ledger(snapshot["positions"], snapshot.get("trades"), metadata, request["indexCode"], now)
        result["positionsConfirmed"] = True
        pnl = result["sessionPnl"]
        pnl.update(status="partial", realizedRupees=ledger["realized"], cashFlowRupees=ledger["cash"],
                   tradeCount=ledger["tradeCount"])
        if len(quoted) != len(result["positions"]):
            raise ValueError("Qualified liquidation quotes unavailable for at least one current leg.")
        if quoted:
            observations = [timestamp(l["observedAt"]) for l in quoted]
            if (max(observations)-min(observations)).total_seconds() > 5:
                raise ValueError("Cross-leg liquidation quote skew exceeds 5 seconds.")
        close_value = sum(l["signedQuantity"]*l["liquidationPrice"] for l in quoted)
        total = ledger["cash"]+close_value
        pnl.update(status="verified", unrealizedRupees=total-ledger["realized"],
                   totalRupees=total, closeValueRupees=close_value)
        result["strategyPnlRupees"] = total
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["sessionPnl"]["reason"] = str(exc)
        result["warnings"].append("Session P&L: " + str(exc))
    try:
        if ledger is None:
            raise ValueError("Verified session cash ledger unavailable.")
        if len(payoff_legs) != len(result["positions"]):
            raise ValueError("Remaining option contract metadata unavailable.")
        result["payoff"] = session_payoff(payoff_legs, ledger["cash"], result["sessionPnl"]["totalRupees"])
        result.update(maxProfit=result["payoff"]["maxProfitRupees"], maxLoss=result["payoff"]["maxLossRupees"],
                      expiry=result["payoff"]["expiry"])
    except (ValueError, KeyError, TypeError) as exc:
        result["payoff"]["reason"] = str(exc)
    cap, total = result["payoff"]["maxProfitRupees"], result["sessionPnl"]["totalRupees"]
    if number(cap) and cap > 0 and result["payoff"]["status"] == "available":
        sl = cap/2
        result["riskPlan"].update(suggestedSlRupees=sl, stopPnlRupees=-sl, targetPnlRupees=2*sl)
        if number(total):
            result["riskPlan"].update(status="available", remainingLossRupees=max(0, sl+total))
            result["remainingRiskBudget"] = max(0, sl+total)
        else:
            result["riskPlan"]["reason"] = "Expiry-based planning SL known; current session liquidation P&L and remaining loss budget unavailable."
    else:
        result["riskPlan"]["reason"] = "Positive finite expiry maximum profit and verified current session P&L required."
    exit_policy(result, now, result["riskPlan"]["stopPnlRupees"], result["riskPlan"]["targetPnlRupees"],
                total, active=bool(result["positions"]))
    if not result["positions"]:
        result["warnings"].append("No open selected-index positions; no paid AI candidates.")
        return _with_review_status(result)
    if result["mandatoryExit"]:
        return _with_review_status(result)
    if (result["sessionPnl"]["status"] != "verified" or not result["greeks"] or opened is None or
            result["riskPlan"]["status"] != "available" or candles is None):
        _review_fallback(result, "Fresh verified session risk/model/candle data insufficient for a non-exit action.")
        return _with_review_status(result)
    try:
        if (len(modeled) not in (2, 4) or len({l["quantity"] for l in modeled}) != 1 or
                len({l["product"] for l in modeled}) != 1 or len({l["lotSize"] for l in modeled}) != 1 or
                len({l["exchange"] for l in modeled}) != 1 or any(l["quantity"] % l["lotSize"] for l in modeled)):
            raise ValueError("Matched same-product complete-lot protective spreads required for hold; four legs for rolls.")
        _validate_protection(modeled)
        if result["payoff"]["lossUnbounded"] or result["payoff"]["profitUnbounded"]:
            raise ValueError("Bounded same-expiry payoff required.")
        expiry_at = expiry_close(result["expiry"], snapshot["sessions"])
        qty = modeled[0]["quantity"]
        result.update(quantity=qty, lotSize=modeled[0]["lotSize"])
        stress, calculation = stress_x(modeled, candles, spot, now, expiry_at, deadline, rate, dividend)
        result["riskPlan"]["stressLossRupees"] = stress*qty
        result.update(suggestedXPoints=stress, xCalculation=calculation)
        budget = result["remainingRiskBudget"]
        apply_exposure_regime(result["regime"], exposure_qualification(
            result["greeks"], calculation, qty, stress*qty, budget))
        if stress*qty <= budget and result["regime"]["exposureRisk"]["acceptable"] is True:
            result["candidates"].append(_candidate(
                "hold", modeled, "Stay with bounded current positions within the recalculated session stress budget.",
                greeks=result["greeks"], greeksAfter=result["greeks"], strategyPnlRupees=total,
                maxProfit=cap, maxLoss=result["maxLoss"], remainingRiskBudget=budget, stressLossRupees=stress*qty))
        if len(modeled) == 4 and now < cutoff:
            # Reuse the roll safety gates with explicitly transient session limits, not an original baseline.
            limits = {"originalRiskBudget": cap/2, "originalMaxLoss": result["maxLoss"],
                      "quantity": qty, "xPoints": cap/(2*qty)}
            rolls = _rolls(modeled, snapshot, spot, spot_at, now, expiry_at, deadline, candles, rate, dividend,
                           ledger["cash"], total, limits, result["trend"], result["regime"],
                           current_stress=stress*qty, current_calculation=calculation, session_policy=True)
            for candidate in rolls:
                candidate.update(reason="Strict same-grid SL-defensive "+candidate["structure"]+
                                 " replacement within transient current-session limits; not a premium-seeking adjustment.",
                                 riskLimitBasis=RISK_BASIS, greeksAfter=candidate["greeks"],
                                 strategyPnlRupees=candidate["strategyPnlAfterRupees"])
                candidate["steps"] = [s.replace("reconfirm baseline", "refresh current-session review")
                                      for s in candidate["steps"]]
            result["candidates"].extend(rolls)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["warnings"].append("Non-exit qualification: " + str(exc))
    if not result["candidates"]:
        _review_fallback(result, "No action passes bounded protection, contractual loss and session stress budgets.")
    else:
        _rank_adjustments(result)
    return _with_review_status(result)


def build_adjustment(request, snapshot, baseline, now, *, rate=None, dividend=None):
    result = unavailable(request["indexCode"], now)
    result["currentStressLossRupees"] = None
    rate, dividend, result["modelInputs"] = model_inputs(request, rate, dividend)
    result["regime"] = nifty_regime(snapshot, request["indexCode"], now)
    result["riskPlan"] = {"status": "unavailable", "suggestedSlRupees": None, "stopPnlRupees": None,
                          "targetPnlRupees": None, "remainingLossRupees": None, "stressLossRupees": None,
                          "basis": "Frozen saved-strategy X times original quantity; not a recalculated session cap."}
    try:
        opened, _, deadline, cutoff = session_window(now, snapshot["sessions"])
        result.update(exitAt=deadline.isoformat(), entryCutoffAt=cutoff.isoformat())
        supported = {(i["exchange"], i["tradingSymbol"]) for i in snapshot["instruments"]}
        result["positions"] = [p for p in snapshot["positions"].get("net", [])
                               if identity(p)[:2] in supported]
        exit_policy(result, now, active=any(p.get("quantity") for p in result["positions"]))
        if now >= deadline:
            result["executionStatus"] = "exit-required"
            result["warnings"].append("MANDATORY MANUAL EXIT: session deadline reached, even if valuation is unavailable.")
        if now < opened:
            raise ValueError("Outside verified session; positions are reference-only.")
        for key in ("xPoints", "originalRiskBudget", "originalMaxLoss"):
            if not number(baseline.get(key)) or baseline[key] <= 0:
                raise ValueError("Frozen original X/risk limits unavailable.")
        original_qty, x = baseline["quantity"], baseline["xPoints"]
        if abs(baseline["originalRiskBudget"] - original_qty*x) > .01:
            raise ValueError("Frozen original risk budget inconsistent with baseline X/quantity.")
        exit_policy(result, now, -x*original_qty, 2*x*original_qty,
                    active=any(p.get("quantity") for p in result["positions"]))
        if baseline.get("cashFlows") is not None:
            ledger_source = baseline.get("ledgerSource", "confirmed-baseline-ledger")
            baseline = reconcile_baseline(baseline, baseline["cashFlows"], snapshot["instruments"])
            baseline["ledgerSource"] = ledger_source
        exposure, cash, positions = attributed_ledger(baseline, snapshot["positions"], snapshot.get("trades", []), now)
        result["positions"] = positions
        result["positionsConfirmed"] = True
        if baseline.get("cashFlows") is None:
            keys = {identity(leg) for leg in baseline["legs"]}
            actual_ids = [str(t["trade_id"]) for t in snapshot.get("trades", []) if identity(t) in keys]
            baseline = reconcile_trade_ids(baseline, actual_ids, snapshot["trades"], snapshot["instruments"], now)
            baseline["ledgerSource"] = "unchanged-baseline-broker-fills"
        result["reconciliation"] = {
            "source": baseline.get("ledgerSource", "broker-fills"),
            "verified": True, "cashFlowCount": len(baseline["cashFlows"]),
            "tradeIds": [f["tradeId"] for f in baseline["cashFlows"]],
            "selectedTradeIds": baseline.get("selectedTradeIds", [f["tradeId"] for f in baseline["cashFlows"]]),
            "priorVerifiedTradeIds": baseline.get("priorVerifiedTradeIds", []),
            "cashFlows": baseline["cashFlows"], "originalRiskLimitsPreserved": True,
        }
        spot, spot_at = validate_spot(snapshot["spotQuote"], now)
        open_sizes = {abs(signed) for signed in exposure.values()}
        if len(open_sizes) > 1:
            raise ValueError("Unmatched/partial open quantities require completed fills and ledger reconfirmation.")
        qty = next(iter(open_sizes)) if open_sizes else original_qty
        if qty > original_qty:
            raise ValueError("Current exposure exceeds the original confirmed quantity.")
        specs = []
        for key, signed in sorted(exposure.items()):
            found = [i for i in snapshot["instruments"] if (i["exchange"], i["tradingSymbol"], key[2]) == key]
            if len(found) != 1 or abs(signed) != qty:
                raise ValueError("Unmatched current contracts/partial lots require baseline reconfirmation.")
            specs.append((found[0], "buy" if signed > 0 else "sell"))
        if not specs:
            result.update(strategyPnlRupees=cash, strategyPnlPoints=cash/original_qty, executionStatus="reference-only",
                          xPoints=x, targetPoints=2*x, stopPoints=-x)
            result["warnings"].append("Confirmed strategy is fully closed.")
            return _with_status(result)
        if len(specs) != 4 or len({i["expiry"] for i, _ in specs}) != 1:
            raise ValueError("Four same-expiry hedged positions required; no naked roll suggestions.")
        if not all(number(v) for v in (rate, dividend)):
            raise ValueError("Explicit pricing model inputs unavailable.")
        expiry = specs[0][0]["expiry"]
        expiry_at = expiry_close(expiry, snapshot["sessions"])
        legs = modeled_legs(specs, snapshot["quotes"], spot, now, expiry_at, rate, dividend, qty)
        for l in legs:
            l["product"] = next(k[2] for k in exposure if k[:2] == identity(l)[:2])
        _validate_protection(legs)
        check_skew(legs, spot_at)
        liquidation = sum((1 if l["direction"] == "buy" else -1)*l["liquidationPrice"]*qty for l in legs)
        pnl = cash + liquidation
        greeks = aggregate_greeks(legs, spot, now, expiry_at, rate, dividend)
        profit, loss = payoff_bounds(legs, cash/qty)
        result.update(expiry=expiry, spot=spot, legs=legs, quantity=qty, lotSize=legs[0]["lotSize"],
                      originalQuantity=original_qty, pnlPointBasis="INR divided by frozen original matched quantity",
                      xPoints=x, targetPoints=2*x, stopPoints=-x, strategyPnlPoints=pnl/original_qty,
                      targetRupees=2*x*original_qty, stopRiskRupees=x*original_qty,
                      strategyPnlRupees=pnl, originalRiskBudget=baseline["originalRiskBudget"],
                      originalMaxLoss=baseline["originalMaxLoss"], remainingRiskBudget=baseline["originalRiskBudget"]+pnl,
                      earlyWarningPolicy={"version": VERSION, "lossFractionOfOriginalX": .8,
                                          "empiricallyValidated": False},
                      maxProfit=profit*qty, maxLoss=loss*qty, greeks=greeks, candidates=[],
                      premiumBalance=premium_balance(legs), structure=_structure(legs),
                      executionStatus="advisory-only")
        exit_policy(result, now, -x*original_qty, 2*x*original_qty, pnl)
        result["riskPlan"].update(status="available", suggestedSlRupees=x*original_qty,
                                  stopPnlRupees=-x*original_qty, targetPnlRupees=2*x*original_qty,
                                  remainingLossRupees=max(0, baseline["originalRiskBudget"]+pnl))
        if qty != original_qty:
            result["warnings"].append("Quantity reduced: strategy P&L/stop/target points retain the original quantity basis; INR limits never reset.")
        if profit*qty < 2*x*original_qty:
            result["warnings"].append("Remaining theoretical profit cap is below the frozen original target; no roll may fabricate target feasibility.")
        if result["mandatoryExit"]:
            result["warnings"].append("MANDATORY MANUAL EXIT: original SL/2X target or session deadline reached; no paid AI.")
            return _with_status(result)
        result["candidates"].append(_candidate("hold", legs, "Keep confirmed fully hedged exposure within original limits.",
                                               greeks=greeks, greeksAfter=greeks, strategyPnlRupees=pnl,
                                               maxLoss=loss*qty, remainingRiskBudget=baseline["originalRiskBudget"]+pnl))
        if pnl <= -.8*baseline["originalRiskBudget"]:
            result["warnings"].append("Versioned early warning: loss reached 80% of original X budget; not an empirical signal.")
        try:
            candles = completed_candles(snapshot.get("candles", []), now, opened)
            result["trend"], _ = intraday_context(candles, spot, snapshot.get("regimeEvidence"), opened)
            result["regime"] = nifty_regime(snapshot, request["indexCode"], now)
            result["premiumBalance"] = premium_balance(legs, directional=result["regime"]["direction"] in ("up", "down"))
            suggested, calculation = stress_x(legs, candles, spot, now, expiry_at, deadline, rate, dividend)
            result.update(suggestedXPoints=suggested, xCalculation=calculation)
            result["riskPlan"]["stressLossRupees"] = suggested*qty
            result["currentStressLossRupees"] = suggested*qty
            apply_exposure_regime(result["regime"], exposure_qualification(
                greeks, calculation, qty, suggested*qty, result["remainingRiskBudget"]))
            for candidate in result["candidates"]:
                candidate.update(stressLossRupees=suggested*qty, xCalculation=calculation,
                                 maxProfit=profit*qty, premiumBalance=result["premiumBalance"])
            result["candidates"] = [
                candidate for candidate in result["candidates"]
                if candidate["stressLossRupees"] <= candidate["remainingRiskBudget"]
                 and candidate["maxLoss"] <= baseline["originalMaxLoss"]
                 and result["regime"]["exposureRisk"]["acceptable"] is True
            ]
            if now < cutoff:
                result["candidates"].extend(_rolls(legs, snapshot, spot, spot_at, now, expiry_at, deadline,
                                                   candles, rate, dividend, cash, pnl, baseline,
                                                   result["trend"], result["regime"],
                                                   current_stress=suggested*qty, current_calculation=calculation))
            else:
                result["warnings"].append("No defensive roll after the entry cutoff.")
        except (ValueError, KeyError, TypeError) as exc:
            result["candidates"] = []
            result["warnings"].append("Non-exit candidates unavailable: " + str(exc))
        if result["candidates"]:
            _rank_adjustments(result)
        else:
            result["executionStatus"] = "review-only"
            _review_fallback(result, "No action passes frozen contractual-loss and remaining SL/stress limits.")
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["blockers"].append(str(exc))
    return _with_status(result)


def _validate_protection(legs):
    puts = [l for l in legs if l["instrumentType"] == "PE" and l["direction"] == "sell"]
    calls = [l for l in legs if l["instrumentType"] == "CE" and l["direction"] == "sell"]
    if puts and calls and max(l["strike"] for l in puts) > min(l["strike"] for l in calls):
        raise ValueError("Inverted short strikes are unsupported; equal strikes require strict outer protective wings.")
    for kind in ("CE", "PE"):
        short = [l for l in legs if l["instrumentType"] == kind and l["direction"] == "sell"]
        long = [l for l in legs if l["instrumentType"] == kind and l["direction"] == "buy"]
        if not short and not long:
            continue
        if len(short) != 1 or len(long) != 1 or short[0]["quantity"] != long[0]["quantity"]:
            raise ValueError("Matched protective coverage required.")
        if (long[0]["strike"] - short[0]["strike"]) * (1 if kind == "CE" else -1) <= 0:
            raise ValueError("Protective wings must remain outward of shorts.")


def straddle_specs(instruments, legs, spot):
    """One bounded ATM replacement with the nearest listed wings inside existing outer protection."""
    if (len(legs) != 4 or len({l["expiry"] for l in legs}) != 1 or
            len({l["exchange"] for l in legs}) != 1 or len({l["quantity"] for l in legs}) != 1):
        raise ValueError("Matched same-expiry four-leg exposure required for a hedged straddle.")
    _validate_protection(legs)
    expiry, exchange = legs[0]["expiry"], legs[0]["exchange"]
    strike = common_atm(instruments, expiry, spot)
    specs = []
    for kind in ("PE", "CE"):
        old_long = next(l for l in legs if l["instrumentType"] == kind and l["direction"] == "buy")
        possible = [i for i in instruments if i["expiry"] == expiry and i["exchange"] == exchange and
                    i["instrumentType"] == kind and
                    (old_long["strike"] <= i["strike"] < strike if kind == "PE"
                     else strike < i["strike"] <= old_long["strike"])]
        if not possible:
            raise ValueError("Strict listed straddle wings inside existing outer protection unavailable.")
        hedge = min(possible, key=lambda i: abs(i["strike"]-strike))
        specs.extend([(contract(instruments, expiry, strike, kind), "sell"), (hedge, "buy")])
    return specs


def _qualify_defensive_roll(legs, specs, snapshot, spot, spot_at, now, expiry_at, deadline,
                           candles, rate, dividend, cash, baseline, regime,
                           current_stress, current_calculation, session_policy):
    qty = legs[0]["quantity"]
    new = modeled_legs(specs, snapshot["quotes"], spot, now, expiry_at, rate, dividend, qty)
    for leg in new:
        leg["product"] = legs[0]["product"]
    _validate_protection(new)
    check_skew(legs+new, spot_at)
    structure = _structure(new)
    if structure == "hedged-straddle" and not (
            regime["state"] == "risk-on" and regime["exposureRisk"]["acceptable"] is True):
        return None
    # Reusing a contract with reversed exposure still requires a close and a new open.
    old_keys = {(identity(l), l["signedQuantity"]) for l in legs}
    new_keys = {(identity(l), l["signedQuantity"]) for l in new}
    closing = [l for l in legs if (identity(l), l["signedQuantity"]) not in new_keys]
    opening = [l for l in new if (identity(l), l["signedQuantity"]) not in old_keys]
    if not closing or not opening:
        return None
    for old_leg in closing:
        for new_leg in opening:
            if identity(old_leg) == identity(new_leg) and old_leg["signedQuantity"]*new_leg["signedQuantity"] < 0:
                qualified_quote(new_leg, snapshot["quotes"], now,
                                abs(old_leg["signedQuantity"])+abs(new_leg["signedQuantity"]))
    cash_after = (cash+sum(l["signedQuantity"]*l["liquidationPrice"] for l in closing)-
                  sum(l["signedQuantity"]*l["entryPrice"] for l in opening))
    profit, loss = payoff_bounds(new, cash_after/qty)
    stress, calculation = stress_x(new, candles, spot, now, expiry_at, deadline, rate, dividend)
    after_stress = stress*qty
    grid_keys = ("spotShocks", "ivShocks", "elapsedSeconds", "remainingSecondsToExit")
    same_grid = all(k in current_calculation and calculation.get(k) == current_calculation[k] for k in grid_keys)
    if not same_grid or not number(current_stress) or current_stress-after_stress <= .01:
        return None
    pnl_after = cash_after+sum(l["signedQuantity"]*l["liquidationPrice"] for l in new)
    risk_limit = min(baseline["originalRiskBudget"], profit*qty/2) if session_policy else baseline["originalRiskBudget"]
    remaining = risk_limit+pnl_after
    _, current_loss = payoff_bounds(legs, cash/qty)
    if (loss*qty > min(baseline["originalMaxLoss"], current_loss*qty) or after_stress > remaining or
            (profit <= 0 if session_policy else profit*qty < 2*baseline["xPoints"]*baseline["quantity"])):
        return None
    before_greeks = aggregate_greeks(legs, spot, now, expiry_at, rate, dividend)
    after_greeks = aggregate_greeks(new, spot, now, expiry_at, rate, dividend)
    qualification = exposure_qualification(after_greeks, calculation, qty, after_stress, remaining)
    if qualification["acceptable"] is not True:
        return None
    before_greek_loss = greek_stress_loss(before_greeks, current_calculation, qty)
    return _candidate(
        "roll", new, "Strict same-grid SL defence; not an adjustment to collect more premium.",
        purpose="sl-defence", closingLegs=closing, openingLegs=opening,
        greeks=after_greeks, greeksBefore=before_greeks, greeksAfter=after_greeks,
        maxLoss=loss*qty, maxProfit=profit*qty, cashAfterRupees=cash_after,
        adjustmentCashFlowRupees=cash_after-cash, strategyPnlAfterRupees=pnl_after,
        strategyPnlRupees=pnl_after, remainingRiskBudget=remaining, stressLossRupees=after_stress,
        xCalculation=calculation, premiumBalance=premium_balance(new, directional=regime["direction"] != "neutral"),
        defenceQualification={"status": "qualified", "sameGrid": True, "quantity": qty, "expiry": legs[0]["expiry"],
                              "currentStressLossRupees": current_stress, "stressLossRupees": after_stress,
                              "stressImprovementRupees": current_stress-after_stress,
                              "minimumStrictImprovementRupees": .01,
                              "stressGrid": {"spotShocks": calculation["spotShocks"],
                                             "ivShocks": calculation["ivShocks"],
                                             "elapsedSeconds": calculation["elapsedSeconds"],
                                             "horizonSeconds": max(calculation["elapsedSeconds"])},
                              "greekStressLossBeforeRupees": before_greek_loss,
                              "greekStressLossAfterRupees": qualification["greekStressLossRupees"],
                              "greekStressBudgetRatioAfter": qualification["greekStressBudgetRatio"],
                              "greekStressBudgetRatioMax": 1.0, "regimeState": regime["state"],
                              "regimeDirection": regime["direction"],
                              "basis": "Same spot/IV shocks, time grid, horizon and matched quantity; fair-value loss plus spreads. "
                                       "All four actual signed Greeks disclosed; no claim that each Greek improves."},
        margin={"status": "unavailable", "reason": "Requires broker basket estimate before exposure."},
        steps=["Buy any replacement protective long first; retain all existing protection until confirmed. "
               "If an existing short becomes a long, buy back the old short AND buy the new long; confirm the final net long quantity.",
               "Buy back each changed short before selling its replacement; never remove existing protection first.",
               "Sell replacement shorts only after their strict outer hedges and old short closes are confirmed; no added lots.",
               "Only then sell replaced old protective longs. Stop on partial fills and reconfirm baseline."])


def _rolls(legs, snapshot, spot, spot_at, now, expiry_at, deadline, candles, rate, dividend,
           cash, pnl, baseline, trend, regime, *, current_stress, current_calculation, session_policy=False):
    if regime.get("status") != "available" or regime["state"] not in ("risk-on", "risk-off"):
        return []
    choices = []
    if regime["direction"] in ("up", "down"):
        kind = "CE" if regime["direction"] == "up" else "PE"
        old = next(l for l in legs if l["instrumentType"] == kind and l["direction"] == "sell")
        hedge = next(l for l in legs if l["instrumentType"] == kind and l["direction"] == "buy")
        outward = 1 if kind == "CE" else -1
        listed = [i for i in snapshot["instruments"] if i["expiry"] == old["expiry"] and
                  i["exchange"] == old["exchange"] and i["instrumentType"] == kind
                  and (i["strike"]-old["strike"])*outward > 0 and (hedge["strike"]-i["strike"])*outward > 0]
        opposite_kind = "PE" if kind == "CE" else "CE"
        opposite_long = next(l for l in legs if l["instrumentType"] == opposite_kind and l["direction"] == "buy")
        opposite_short = next(l for l in legs if l["instrumentType"] == opposite_kind and l["direction"] == "sell")
        tighter = [i for i in snapshot["instruments"] if i["expiry"] == old["expiry"] and
                   i["exchange"] == old["exchange"] and i["instrumentType"] == opposite_kind and
                   min(opposite_long["strike"], opposite_short["strike"]) < i["strike"] <
                   max(opposite_long["strike"], opposite_short["strike"])]
        tightening = min(tighter, key=lambda i: abs(i["strike"]-opposite_long["strike"])) if tighter else None
        for replacement in sorted(listed, key=lambda i: abs(i["strike"]-old["strike"]))[:2]:
            for tighter_hedge in ([None, tightening] if tightening else [None]):
                choices.append([(replacement if l is old else tighter_hedge if l is opposite_long and tighter_hedge else l,
                                 l["direction"]) for l in legs])
    if regime["state"] == "risk-on" and regime["exposureRisk"]["acceptable"] is True:
        try:
            choices.append(straddle_specs(snapshot["instruments"], legs, spot))
        except (ValueError, KeyError, TypeError):
            pass
    candidates = []
    for specs in choices:
        try:
            candidate = _qualify_defensive_roll(
                legs, specs, snapshot, spot, spot_at, now, expiry_at, deadline, candles, rate, dividend,
                cash, baseline, regime, current_stress, current_calculation, session_policy)
            if candidate and candidate["id"] not in {c["id"] for c in candidates}:
                candidates.append(candidate)
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
    candidates.sort(key=lambda c: (c["stressLossRupees"], c["maxLoss"], *_premium_score(c["premiumBalance"]), c["id"]))
    return candidates


def _refresh_chart_context(result, snapshot, now):
    previous = result.get("regime") or {}
    regime = nifty_regime(snapshot, result["indexCode"], now)
    if previous.get("exposureRisk"):
        apply_exposure_regime(regime, previous["exposureRisk"])
    unchanged = (regime["status"] == "available" and regime["signalAt"] == previous.get("signalAt") and
                 regime["state"] == previous.get("state") and regime["direction"] == previous.get("direction"))
    result["regime"] = regime
    if result["legs"] and not result["blockers"]:
        result["premiumBalance"] = premium_balance(result["legs"], directional=regime["direction"] in ("up", "down"))
    for candidate in result["candidates"]:
        if candidate["action"] == "hold":
            candidate["premiumBalance"] = result["premiumBalance"]
    return unchanged


def revalidate_completion(result, snapshot, now, action):
    """Invalidate queued advice instead of relabelling old quotes as fresh."""
    if result.get("analysisMode") == "current-positions":
        if not -2 <= (now - timestamp(result["asOf"])).total_seconds() <= 60:
            invalidate_review(result, "Position capture is stale; request a fresh broker review.")
        fresh_chart = _refresh_chart_context(result, snapshot, now)
        try:
            used = [l for l in result["legs"]]
            for candidate in result["candidates"]:
                used.extend(candidate["legs"])
                used.extend(candidate.get("closingLegs", []))
            if used:
                _, observed = validate_spot(snapshot["spotQuote"], now)
                observations = [qualified_quote(l, snapshot["quotes"], now, l["quantity"]) for l in used]
                check_skew(observations, observed)
            elif result["sessionPnl"]["status"] == "verified" and result["positions"]:
                instruments = {identity(i)[:2]: i for i in snapshot["instruments"]}
                observations = [qualified_quote(instruments[identity(p)[:2]], snapshot["quotes"], now,
                                                abs(p["quantity"])) for p in result["positions"]]
                times = [timestamp(q["observedAt"]) for q in observations]
                if (max(times)-min(times)).total_seconds() > 5:
                    raise ValueError("Cross-leg liquidation quote skew exceeds 5 seconds.")
            if any(c["action"] in ("hold", "roll") for c in result["candidates"]):
                opened, _, _, cutoff = session_window(now, snapshot["sessions"])
                completed_candles(snapshot["candles"], now, opened)
                if now >= cutoff or not fresh_chart:
                    result["candidates"] = [c for c in result["candidates"] if c["action"] != "roll"]
        except (ValueError, KeyError, TypeError) as exc:
            invalidate_review(result, "Completion revalidation: " + str(exc))
        policy = result["exitPolicy"]
        exit_policy(result, now, policy["stopPnlRupees"], policy["targetPnlRupees"],
                    result["sessionPnl"]["totalRupees"], active=bool(result["positions"]))
        if not result["candidates"] and not result["mandatoryExit"] and not result["blockers"]:
            _review_fallback(result, "Completion chart/risk checks left no safe non-exit candidate.")
        return _with_review_status(result)
    fresh_chart = _refresh_chart_context(result, snapshot, now)
    try:
        spot, observed = validate_spot(snapshot["spotQuote"], now)
        used = list(result["legs"])
        if action == "entry" and result.get("atmStrike") is not None:
            for kind in ("CE", "PE"):
                atm = contract(snapshot["instruments"], result["expiry"], result["atmStrike"], kind)
                used.append({**atm, "quantity": atm["lotSize"]})
        for candidate in result["candidates"]:
            used.extend(candidate["legs"])
            used.extend(candidate.get("closingLegs", []))
        observations = [qualified_quote(leg, snapshot["quotes"], now, leg["quantity"]) for leg in used]
        if observations:
            check_skew(observations, observed)
        if not fresh_chart:
            result["candidates"] = [c for c in result["candidates"] if c["action"] != "roll"]
            result["warnings"].append("NIFTY chart unavailable/changed at completion; directional rolls removed.")
        _, _, deadline, cutoff = session_window(now, snapshot["sessions"])
        if action == "entry" and now >= cutoff:
            raise ValueError("Entry cutoff reached during calculation.")
        if now >= deadline and action == "adjustment":
            exit_policy(result, now, result["exitPolicy"]["stopPnlRupees"],
                        result["exitPolicy"]["targetPnlRupees"], result.get("strategyPnlRupees"))
            result["warnings"].append("Manual exit deadline reached during calculation.")
    except (ValueError, KeyError, TypeError) as exc:
        qualification = exposure_qualification({}, {}, 0, None, None)
        qualification["reason"] = "Completion revalidation: "+str(exc)
        apply_exposure_regime(result["regime"], qualification)
        result["premiumBalance"] = {**premium_balance(), "reason": qualification["reason"]}
        if "currentStressLossRupees" in result:
            result["currentStressLossRupees"] = None
        mandatory_exit = (result["executionStatus"] == "exit-required" or
                          (action == "adjustment" and result.get("exitAt") and now >= timestamp(result["exitAt"])))
        result["executionStatus"] = "exit-required" if mandatory_exit else "unavailable"
        result["candidates"] = []
        result["blockers"].append("Completion revalidation: " + str(exc))
    if action == "adjustment":
        policy = result["exitPolicy"]
        exit_policy(result, now, policy["stopPnlRupees"], policy["targetPnlRupees"],
                    result.get("strategyPnlRupees"), active=any(p.get("quantity") for p in result["positions"]))
        if not result["candidates"] and not result["mandatoryExit"] and not result["blockers"] and result["positions"]:
            result["executionStatus"] = "review-only"
            _review_fallback(result, "Completion left no safe hold/protective roll.")
    return _with_status(result)
