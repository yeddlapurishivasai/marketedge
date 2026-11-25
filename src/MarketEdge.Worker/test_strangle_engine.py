"""Offline acceptance fixtures for advisory strangles and cash-flow attribution."""
from __future__ import annotations

import copy
import math
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from options_engine import european_price
from strangle_engine import (
    IST, YEAR, aggregate_greeks, attributed_ledger, build_adjustment, build_entry, build_position_review,
    common_atm, completed_candles, contract, expiry_close, intraday_context,
    modeled_legs, payoff_bounds, point_increment, qualified_quote, revalidate_completion,
    select_expiry, select_legs, session_window, short_strikes, stress_x, reconcile_baseline, reconcile_trade_ids,
    nifty_regime, premium_balance, exit_policy, entry_spec_choices, timestamp, straddle_specs, _validate_protection,
)

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=IST)
RATE, DIVIDEND = .06, .01


def fixture(index="NIFTY50", lot=75, tick=.05):
    spot = 24500 if index == "NIFTY50" else 80000
    exchange = "NFO" if index == "NIFTY50" else "BFO"
    expiry = "2026-12-01"
    sessions = [
        {"sessionDate": "2026-11-17", "isTradingDay": True, "openTime": "09:15",
         "closeTime": "15:30", "source": "verified-fixture"},
        {"sessionDate": expiry, "isTradingDay": True, "openTime": "09:15",
         "closeTime": "15:30", "source": "verified-fixture"},
    ]
    instruments, quotes = [], {}
    years = (expiry_close(expiry, sessions)-NOW).total_seconds()/YEAR
    for strike in range(spot-5000, spot+5100, 100):
        for kind in ("CE", "PE"):
            symbol = f"{index}{strike}{kind}"
            inst = {"exchange": exchange, "tradingSymbol": symbol, "instrumentToken": strike*2+(kind == "CE"),
                    "underlying": index, "expiry": expiry, "strike": strike,
                    "instrumentType": kind, "lotSize": lot, "tickSize": tick}
            instruments.append(inst)
            mid = european_price(spot, strike, years, RATE, DIVIDEND, .18, kind)
            if strike == spot:
                mid = 110
            mid = max(2*tick, round(mid, 2))
            quotes[exchange+":"+symbol] = {
                "last_price": mid, "timestamp": NOW.isoformat(), "volume": 10000,
                "depth": {"buy": [{"price": max(tick, mid-tick), "quantity": 100000}],
                          "sell": [{"price": mid+tick, "quantity": 100000}]},
            }
    candles = []
    for minute in range(45):
        close = spot-5+minute*.1
        candles.append({"date": (NOW.replace(hour=9, minute=15)+timedelta(minutes=minute)).isoformat(),
                        "open": close-.1, "high": close+.02, "low": close-.12, "close": close, "volume": 0})
    request = {"requestId": "fixture-request", "indexCode": index, "action": "entry",
               "wingWidth": 1000 if index == "NIFTY50" else 3000, "xOverride": None, "strategyId": None}
    snapshot = {"sessions": sessions, "instruments": instruments, "quotes": quotes, "candles": candles,
                "spotSymbol": "NSE:NIFTY 50" if index == "NIFTY50" else "BSE:SENSEX",
                "spotQuote": {"last_price": spot, "timestamp": NOW.isoformat(), "instrument_token": 1},
                "positions": {"net": [], "day": []},
                "regimeEvidence": {"fresh": True, "niftyChangePct": .1, "sensexChangePct": .1, "vixChangePct": -.1,
                                   "niftyChangePctObservedAt": NOW.isoformat(), "sensexChangePctObservedAt": NOW.isoformat(),
                                   "vixChangePctObservedAt": NOW.isoformat()}}
    snapshot["niftySessions"] = sessions
    snapshot["niftySpotQuote"] = {"last_price": 24500, "timestamp": NOW.isoformat(), "instrument_token": 2}
    snapshot["niftyCandles"] = [{**c, **{k: c[k]-spot+24500 for k in ("open", "high", "low", "close")}}
                               for c in candles] if index == "SENSEX" else candles
    return request, snapshot


def entry(request=None, snapshot=None, now=NOW, **model):
    default_request, default_snapshot = fixture()
    return build_entry(request or default_request, snapshot or default_snapshot, now,
                       rate=model.get("rate", RATE), dividend=model.get("dividend", DIVIDEND))


def linked(quantity=None, *, index="NIFTY50"):
    request, snapshot = fixture(index, lot=75 if index == "NIFTY50" else 20)
    result = entry(request, snapshot)
    if result["blockers"]:
        raise AssertionError(result["blockers"])
    qty = quantity or result["quantity"]
    legs, trades, net = [], [], []
    for n, leg in enumerate(result["legs"]):
        signed = qty*(1 if leg["direction"] == "buy" else -1)
        legs.append({"exchange": leg["exchange"], "tradingSymbol": leg["tradingSymbol"],
                     "product": "MIS", "signedQuantity": signed, "entryPrice": leg["entryPrice"]})
        trades.append({"exchange": leg["exchange"], "tradingsymbol": leg["tradingSymbol"], "product": "MIS",
                       "trade_id": str(n), "quantity": qty, "transaction_type": "BUY" if signed > 0 else "SELL",
                       "average_price": leg["entryPrice"], "fill_timestamp": (NOW-timedelta(minutes=1)).isoformat()})
        net.append({"exchange": leg["exchange"], "tradingsymbol": leg["tradingSymbol"], "product": "MIS",
                    "quantity": signed, "overnight_quantity": 0, "average_price": 999999})
    baseline = {"legs": legs, "quantity": qty, "sessionDate": "2026-11-17", "xPoints": 100,
                "originalRiskBudget": 100*qty, "originalMaxLoss": 2000*qty}
    request.update(action="adjustment", strategyId=5)
    snapshot.update(positions={"net": net, "day": copy.deepcopy(net)}, trades=trades)
    return request, snapshot, baseline


def adjustment(request, snapshot, baseline, now=NOW):
    return build_adjustment(request, snapshot, baseline, now, rate=RATE, dividend=DIVIDEND)


def fill_ledger(trades):
    return [{
        "exchange": t["exchange"], "tradingSymbol": t["tradingsymbol"], "product": t["product"],
        "signedQuantity": t["quantity"]*(1 if t["transaction_type"] == "BUY" else -1),
        "price": t["average_price"], "tradeId": t["trade_id"],
    } for t in trades]


def closed_roll_cycle():
    r, s, b = linked()
    old = s["trades"][0]
    new = contract(s["instruments"], "2026-12-01", 24100, "PE")
    changes = [
        ("old-close", old["tradingsymbol"], "BUY", old["average_price"]+10),
        ("new-open", new["tradingSymbol"], "SELL", old["average_price"]-10),
        ("new-close", new["tradingSymbol"], "BUY", old["average_price"]),
        ("old-reopen", old["tradingsymbol"], "SELL", old["average_price"]),
    ]
    for n, (trade_id, symbol, direction, price) in enumerate(changes):
        s["trades"].append({**old, "trade_id": trade_id, "tradingsymbol": symbol,
                            "transaction_type": direction, "average_price": price,
                            "fill_timestamp": (NOW-timedelta(seconds=30-n*5)).isoformat()})
    s["positions"]["net"].append({**s["positions"]["net"][0], "tradingsymbol": new["tradingSymbol"], "quantity": 0})
    return r, s, b


def chart_fixture(snapshot, index="NIFTY50", signal="breakout"):
    cases = {"breakout": (24501, 24512, 24500, 24510),
             "breakdown": (24499, 24500, 24488, 24490),
             "support-rejection": (24496, 24502, 24494, 24501),
             "resistance-rejection": (24504, 24506, 24498, 24499),
             "inside-range": (24500, 24504, 24496, 24500),
             "equal-resistance": (24500, 24506, 24499, 24505)}
    minutes = []
    for bar in range(9):
        opened, high, low, closed = cases[signal] if bar == 8 else (24500, 24505, 24495, 24500)
        for minute in range(5):
            o = opened+(closed-opened)*minute/5
            c = opened+(closed-opened)*(minute+1)/5
            minutes.append({"date": (NOW.replace(hour=9, minute=15)+timedelta(minutes=bar*5+minute)).isoformat(),
                            "open": o, "high": high if minute == 0 else max(o, c),
                            "low": low if minute == 0 else min(o, c), "close": c})
    snapshot["candles" if index == "NIFTY50" else "niftyCandles"] = minutes
    return minutes


def range_defence_fixture(index="NIFTY50", *, rate=RATE, dividend=DIVIDEND):
    request, snapshot, baseline = linked(index=index)
    request.update(strategyId=None, riskFreeRate=rate, dividendYield=dividend)
    chart_fixture(snapshot, index, "inside-range")
    expiry, spot = snapshot["instruments"][0]["expiry"], snapshot["spotQuote"]["last_price"]
    strike = common_atm(snapshot["instruments"], expiry, spot)
    years = (expiry_close(expiry, snapshot["sessions"])-NOW).total_seconds()/YEAR
    for kind in ("CE", "PE"):
        inst = contract(snapshot["instruments"], expiry, strike, kind)
        mid = european_price(spot, strike, years, rate, dividend, .18, kind)
        quote = snapshot["quotes"][inst["exchange"]+":"+inst["tradingSymbol"]]
        quote["depth"]["buy"][0]["price"] = mid-.05
        quote["depth"]["sell"][0]["price"] = mid+.05
    return request, snapshot, baseline


class SellingRegimeDefenceTests(unittest.TestCase):
    def test_selling_friendly_range_is_risk_on_with_up_down_or_neutral_direction(self):
        for signal, direction in (("inside-range", "neutral"), ("support-rejection", "up"),
                                  ("resistance-rejection", "down")):
            _, snapshot = fixture()
            chart_fixture(snapshot, signal=signal)
            regime = nifty_regime(snapshot, "NIFTY50", NOW)
            self.assertEqual(regime["state"], "risk-on")
            self.assertEqual(regime["chartState"], "risk-on")
            self.assertEqual(regime["direction"], direction)
            self.assertTrue(regime["rangeBounded"])
            self.assertLessEqual(regime["volatilityRatio"], regime["thresholds"]["containedVolatilityRatioMax"])

    def test_expanding_range_and_confirmed_break_are_risk_off_independent_of_direction(self):
        for direction in ("up", "down"):
            _, snapshot = fixture()
            minutes = chart_fixture(snapshot, signal="inside-range")
            for n in range(35, 45):
                close = 24510 if direction == "up" else 24490
                minutes[n] = {**minutes[n], "open": close, "high": close+1, "low": close-1, "close": close}
            regime = nifty_regime(snapshot, "NIFTY50", NOW)
            self.assertTrue(regime["rangeBreakConfirmed"])
            self.assertEqual(regime["state"], "risk-off")
            self.assertEqual(regime["direction"], direction)
        _, snapshot = fixture()
        minutes = chart_fixture(snapshot, signal="inside-range")
        minutes[-1].update(open=24500, high=24520, low=24480, close=24500)
        regime = nifty_regime(snapshot, "NIFTY50", NOW)
        self.assertEqual(regime["direction"], "neutral")
        self.assertFalse(regime["rangeBreakConfirmed"])
        self.assertGreaterEqual(regime["volatilityRatio"], 1.5)
        self.assertEqual(regime["state"], "risk-off")

    def test_same_strike_risk_on_straddle_strictly_defends_sl_with_actual_greeks_for_both_indices(self):
        for index in ("NIFTY50", "SENSEX"):
            request, snapshot, _ = range_defence_fixture(index)
            result = build_position_review(request, snapshot, NOW)
            self.assertEqual(result["regime"]["state"], "risk-on")
            self.assertEqual(result["regime"]["direction"], "neutral")
            self.assertTrue(result["regime"]["exposureRisk"]["acceptable"])
            candidate = next(c for c in result["candidates"] if c.get("structure") == "hedged-straddle")
            self.assertEqual(candidate["action"], "roll")
            self.assertEqual(candidate["purpose"], "sl-defence")
            shorts = [l for l in candidate["legs"] if l["direction"] == "sell"]
            self.assertEqual(len({l["strike"] for l in shorts}), 1)
            _validate_protection(candidate["legs"])
            self.assertEqual({l["quantity"] for l in candidate["legs"]}, {result["quantity"]})
            self.assertEqual({l["expiry"] for l in candidate["legs"]}, {result["expiry"]})
            self.assertLess(candidate["stressLossRupees"], result["riskPlan"]["stressLossRupees"])
            self.assertLessEqual(candidate["maxLoss"], result["payoff"]["maxLossRupees"])
            self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"])
            self.assertLessEqual(candidate["remainingRiskBudget"], result["riskPlan"]["remainingLossRupees"])
            defence = candidate["defenceQualification"]
            self.assertTrue(defence["sameGrid"])
            self.assertGreater(defence["stressImprovementRupees"], defence["minimumStrictImprovementRupees"])
            self.assertEqual(defence["currentStressLossRupees"], result["riskPlan"]["stressLossRupees"])
            self.assertLessEqual(defence["greekStressLossAfterRupees"], candidate["remainingRiskBudget"])
            for key in ("delta", "gamma", "vega", "theta"):
                self.assertAlmostEqual(candidate["greeksBefore"]["quantityWeighted"][key],
                                       result["greeks"]["quantityWeighted"][key])
                actual_after = aggregate_greeks(
                    candidate["legs"], result["spot"], NOW, expiry_close(result["expiry"], snapshot["sessions"]),
                    RATE, DIVIDEND)["quantityWeighted"][key]
                self.assertAlmostEqual(candidate["greeksAfter"]["quantityWeighted"][key], actual_after)
            for key in ("spotShocks", "ivShocks", "elapsedSeconds", "remainingSecondsToExit"):
                self.assertEqual(candidate["xCalculation"][key], result["xCalculation"][key])
            self.assertIn("protective long first", candidate["steps"][0])
            self.assertIn("before selling its replacement", candidate["steps"][1])
            self.assertFalse(result["mandatoryExit"])
            revalidate_completion(result, snapshot, NOW, "adjustment")
            self.assertEqual(result["regime"]["state"], "risk-on")
            self.assertTrue(result["eligible"])
            self.assertTrue(any(c.get("structure") == "hedged-straddle" for c in result["candidates"]))

    def test_equal_or_worse_stress_never_qualifies_even_when_straddle_premiums_balance(self):
        for added_loss in (0, 100):
            request, snapshot, _ = range_defence_fixture()
            baseline = build_position_review(request, snapshot, NOW)
            current = baseline["riskPlan"]["stressLossRupees"]
            def no_improvement(legs, *args, **kwargs):
                shorts = [l for l in legs if l["direction"] == "sell"]
                if len({l["strike"] for l in shorts}) == 1:
                    return (current+added_loss)/legs[0]["quantity"], baseline["xCalculation"]
                return stress_x(legs, *args, **kwargs)
            with patch("strangle_engine.stress_x", side_effect=no_improvement):
                result = build_position_review(request, snapshot, NOW)
            self.assertEqual(result["regime"]["state"], "risk-on")
            self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])

    def test_greek_stress_can_veto_risk_on_even_when_fair_value_stress_fits_sl_budget(self):
        request, snapshot, _ = range_defence_fixture()
        before = build_position_review(request, snapshot, NOW)
        risk = before["regime"]["exposureRisk"]
        desired_budget = (risk["stressLossRupees"]+risk["greekStressLossRupees"])/2
        loss = (before["riskPlan"]["remainingLossRupees"]-desired_budget)/1.5
        old = snapshot["trades"][0]
        for trade_id, direction, price, minutes in (
                ("greek-veto-buy", "BUY", 200, 3), ("greek-veto-sell", "SELL", 200-loss/75, 2)):
            snapshot["trades"].append({**old, "trade_id": trade_id, "product": "NRML",
                                       "transaction_type": direction, "average_price": price,
                                       "fill_timestamp": (NOW-timedelta(minutes=minutes)).isoformat()})
        snapshot["positions"]["net"].append({**snapshot["positions"]["net"][0], "product": "NRML", "quantity": 0})
        result = build_position_review(request, snapshot, NOW)
        regime = result["regime"]
        self.assertEqual(regime["chartState"], "risk-on")
        self.assertEqual(regime["state"], "risk-off")
        self.assertEqual(regime["direction"], "neutral")
        self.assertFalse(regime["exposureRisk"]["acceptable"])
        self.assertLessEqual(result["riskPlan"]["stressLossRupees"], result["remainingRiskBudget"])
        self.assertGreater(regime["exposureRisk"]["greekStressLossRupees"], result["remainingRiskBudget"])
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])
        self.assertFalse(result["mandatoryExit"])
        self.assertIsNone(result["exitPolicy"]["reason"])

    def test_risk_off_and_mixed_never_propose_straddle_tightening(self):
        for chart_signal in ("breakout", "breakdown"):
            request, snapshot, _ = range_defence_fixture()
            chart_fixture(snapshot, signal=chart_signal)
            result = build_position_review(request, snapshot, NOW)
            self.assertEqual(result["regime"]["chartState"], "mixed")
            self.assertFalse(any(c.get("structure") == "hedged-straddle" for c in result["candidates"]))
        request, snapshot, _ = range_defence_fixture()
        snapshot["candles"][-1].update(open=24500, high=24520, low=24480, close=24500)
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(result["regime"]["state"], "risk-off")
        self.assertEqual(result["regime"]["direction"], "neutral")
        self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])

    def test_saved_strategy_straddle_preserves_frozen_stop_and_original_quantity(self):
        request, snapshot, baseline = range_defence_fixture()
        request["strategyId"] = 5
        baseline.update(xPoints=30, originalRiskBudget=2250)
        result = adjustment(request, snapshot, baseline)
        candidate = next(c for c in result["candidates"] if c.get("structure") == "hedged-straddle")
        self.assertEqual(result["exitPolicy"]["stopPnlRupees"], -2250)
        self.assertEqual(result["exitPolicy"]["targetPnlRupees"], 4500)
        self.assertLess(candidate["stressLossRupees"], result["riskPlan"]["stressLossRupees"])
        self.assertEqual(result["currentStressLossRupees"], result["riskPlan"]["stressLossRupees"])
        self.assertLess(candidate["stressLossRupees"], result["currentStressLossRupees"])
        self.assertEqual(candidate["defenceQualification"]["currentStressLossRupees"],
                         result["currentStressLossRupees"])
        self.assertLessEqual(candidate["maxLoss"], result["maxLoss"])
        self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"])

    def test_straddle_reversal_accounts_for_short_buyback_and_new_protective_long(self):
        for index in ("NIFTY50", "SENSEX"):
            for current_positions in (False, True):
                request, snapshot, baseline = range_defence_fixture(index)
                spot = snapshot["spotQuote"]["last_price"]
                quantity = baseline["quantity"]
                for kind, direction, strike in (("PE", "sell", spot-100), ("PE", "buy", spot-1100),
                                                ("CE", "sell", spot+300), ("CE", "buy", spot+1300)):
                    held = next(l for l in baseline["legs"] if l["tradingSymbol"].endswith(kind)
                                and (l["signedQuantity"] < 0) == (direction == "sell"))
                    old_symbol = held["tradingSymbol"]
                    instrument = contract(snapshot["instruments"], "2026-12-01", strike, kind)
                    symbol = instrument["tradingSymbol"]
                    quote = qualified_quote(instrument, snapshot["quotes"], NOW, quantity)
                    price = quote["bid" if direction == "sell" else "ask"]
                    held.update(tradingSymbol=symbol, entryPrice=price)
                    for trade in snapshot["trades"]:
                        if trade["tradingsymbol"] == old_symbol:
                            trade.update(tradingsymbol=symbol, average_price=price)
                    for position in snapshot["positions"]["net"]:
                        if position["tradingsymbol"] == old_symbol:
                            position["tradingsymbol"] = symbol
                baseline.update(xPoints=30, originalRiskBudget=30*quantity)
                request["strategyId"] = None if current_positions else 5
                result = (build_position_review(request, snapshot, NOW) if current_positions else
                          adjustment(request, snapshot, baseline))
                self.assertTrue(any(c.get("structure") == "hedged-straddle" for c in result["candidates"]),
                                (index, current_positions, result["warnings"], result["blockers"]))
                candidate = next(c for c in result["candidates"] if c.get("structure") == "hedged-straddle")
                reversed_symbol = contract(snapshot["instruments"], "2026-12-01", spot-100, "PE")["tradingSymbol"]
                old_short = next(l for l in candidate["closingLegs"] if l["tradingSymbol"] == reversed_symbol)
                new_long = next(l for l in candidate["openingLegs"] if l["tradingSymbol"] == reversed_symbol)
                self.assertEqual(old_short["signedQuantity"], -quantity)
                self.assertEqual(new_long["signedQuantity"], quantity)
                self.assertEqual(-old_short["signedQuantity"]+new_long["signedQuantity"], 2*quantity)
                cash_before = sum(t["quantity"]*t["average_price"]*(1 if t["transaction_type"] == "SELL" else -1)
                                  for t in snapshot["trades"])
                closing_cash = 0
                for held in baseline["legs"]:
                    instrument = next(i for i in snapshot["instruments"] if i["tradingSymbol"] == held["tradingSymbol"])
                    quote = qualified_quote(instrument, snapshot["quotes"], NOW, quantity)
                    closing_cash += held["signedQuantity"]*quote["ask" if held["signedQuantity"] < 0 else "bid"]
                expected_cash = cash_before+closing_cash-sum(l["signedQuantity"]*l["entryPrice"] for l in candidate["legs"])
                expected_pnl = expected_cash+sum(l["signedQuantity"]*l["liquidationPrice"] for l in candidate["legs"])
                self.assertAlmostEqual(candidate["cashAfterRupees"], expected_cash)
                self.assertAlmostEqual(candidate["strategyPnlAfterRupees"], expected_pnl)
                profit, loss = payoff_bounds(candidate["legs"], expected_cash/quantity)
                self.assertAlmostEqual(candidate["maxProfit"], profit*quantity)
                self.assertAlmostEqual(candidate["maxLoss"], loss*quantity)
                self.assertLessEqual(expected_pnl, result["strategyPnlRupees"])
                limit = (min(result["riskPlan"]["suggestedSlRupees"], profit*quantity/2)
                         if current_positions else baseline["originalRiskBudget"])
                self.assertAlmostEqual(candidate["remainingRiskBudget"], limit+expected_pnl)
                snapshot["quotes"][new_long["exchange"]+":"+reversed_symbol]["depth"]["sell"][0]["quantity"] = quantity
                thin = (build_position_review(request, snapshot, NOW) if current_positions else
                        adjustment(request, snapshot, baseline))
                self.assertFalse(any(c.get("structure") == "hedged-straddle" for c in thin["candidates"]))

    def test_equal_shorts_require_strict_outer_wings_and_inverted_shorts_are_rejected(self):
        _, snapshot, _ = range_defence_fixture()
        result = entry()
        specs = straddle_specs(snapshot["instruments"], result["legs"], result["spot"])
        legs = modeled_legs(specs, snapshot["quotes"], result["spot"], NOW,
                            expiry_close(result["expiry"], snapshot["sessions"]), RATE, DIVIDEND)
        _validate_protection(legs)
        broken = copy.deepcopy(legs)
        next(l for l in broken if l["instrumentType"] == "PE" and l["direction"] == "buy")["strike"] = 24500
        with self.assertRaisesRegex(ValueError, "outward"):
            _validate_protection(broken)
        broken = copy.deepcopy(legs)
        next(l for l in broken if l["instrumentType"] == "PE" and l["direction"] == "sell")["strike"] = 24501
        with self.assertRaisesRegex(ValueError, "Inverted"):
            _validate_protection(broken)

    def test_entry_remains_hedged_strangle_not_adjustment_straddle(self):
        request, snapshot = fixture()
        chart_fixture(snapshot, signal="inside-range")
        result = entry(request, snapshot)
        self.assertEqual(result["structure"], "hedged-strangle")
        self.assertLess(result["shortPutStrike"], result["shortCallStrike"])
        self.assertFalse(any(c.get("structure") == "hedged-straddle" for c in result["candidates"]))

    def test_every_directional_or_straddle_roll_is_strict_same_grid_sl_defence(self):
        request, snapshot, baseline = linked()
        range_request, range_snapshot, _ = range_defence_fixture()
        results = (build_position_review(request, snapshot, NOW, rate=RATE, dividend=DIVIDEND),
                   adjustment(request, snapshot, baseline),
                   build_position_review(range_request, range_snapshot, NOW))
        for result in results:
            rolls = [c for c in result["candidates"] if c["action"] == "roll"]
            self.assertTrue(rolls)
            for candidate in rolls:
                defence = candidate["defenceQualification"]
                self.assertEqual(candidate["purpose"], "sl-defence")
                self.assertLess(candidate["stressLossRupees"], result["riskPlan"]["stressLossRupees"])
                self.assertGreater(defence["stressImprovementRupees"], .01)
                self.assertEqual(defence["stressGrid"]["horizonSeconds"],
                                 max(result["xCalculation"]["elapsedSeconds"]))
                self.assertEqual(defence["stressGrid"]["spotShocks"], result["xCalculation"]["spotShocks"])
                self.assertEqual(defence["stressGrid"]["ivShocks"], result["xCalculation"]["ivShocks"])
                self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"])
                self.assertLessEqual(defence["greekStressLossAfterRupees"], candidate["remainingRiskBudget"])
                if result["regime"]["state"] == "risk-off":
                    self.assertEqual(candidate["structure"], "hedged-strangle")

    def test_missing_nifty_chart_cannot_offer_sensex_straddle_even_when_greeks_are_safe(self):
        request, snapshot, _ = range_defence_fixture("SENSEX")
        snapshot["niftyCandles"] = []
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(result["regime"]["state"], "unavailable")
        self.assertEqual(result["regime"]["direction"], "neutral")
        self.assertTrue(result["regime"]["exposureRisk"]["acceptable"])
        self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])

    def test_legacy_current_stress_is_unavailable_without_grid_or_after_failed_completion(self):
        request, snapshot, baseline = linked()
        result = adjustment(request, snapshot, baseline)
        self.assertEqual(result["currentStressLossRupees"], result["suggestedXPoints"]*result["quantity"])
        revalidate_completion(result, snapshot, NOW+timedelta(seconds=31), "adjustment")
        self.assertIsNone(result["currentStressLossRupees"])
        self.assertFalse(result["eligible"])
        snapshot["candles"] = []
        missing = adjustment(request, snapshot, baseline)
        self.assertIsNone(missing["currentStressLossRupees"])
        self.assertFalse(any(c["action"] == "roll" for c in missing["candidates"]))


class ChartPremiumAndExitTests(unittest.TestCase):
    def test_nifty_completed_five_minute_breakout_and_rejection_levels_for_both_indices(self):
        for index in ("NIFTY50", "SENSEX"):
            for signal, state, direction in (("breakout", "mixed", "up"), ("breakdown", "mixed", "down"),
                                             ("support-rejection", "risk-on", "up"),
                                             ("resistance-rejection", "risk-on", "down"),
                                             ("inside-range", "risk-on", "neutral"),
                                             ("equal-resistance", "risk-on", "neutral")):
                r, s = fixture(index)
                chart_fixture(s, index, signal)
                result = nifty_regime(s, index, NOW)
                with self.subTest(index=index, signal=signal):
                    self.assertEqual(result["status"], "available")
                    self.assertEqual(result["state"], state)
                    self.assertEqual(result["direction"], direction)
                    self.assertEqual(result["version"], "nifty-selling-regime-v2")
                    self.assertEqual(result["signal"], "inside-range" if signal == "equal-resistance" else signal)
                    self.assertEqual(result["sourceIndexCode"], "NIFTY50")
                    self.assertEqual(result["timeframe"], "5minute")
                    self.assertEqual(result["support"], 24495)
                    self.assertEqual(result["resistance"], 24505)
                    self.assertEqual(result["levelsAsOf"], NOW.replace(hour=9, minute=55).isoformat())
                    self.assertEqual(result["signalAt"], NOW.isoformat())
                    self.assertEqual(result["signalBar"]["close"], result["signalClose"])
                    self.assertEqual(result["lastClose"], result["signalClose"])

    def test_partial_signal_bar_and_extreme_partial_bar_do_not_enter_levels(self):
        _, s = fixture()
        minutes = chart_fixture(s)
        before = nifty_regime(s, "NIFTY50", NOW-timedelta(seconds=1))
        self.assertEqual(before["completedBars"], 8)
        self.assertEqual(before["state"], "risk-on")
        self.assertEqual(before["direction"], "neutral")
        self.assertEqual(nifty_regime(s, "NIFTY50", NOW)["state"], "mixed")
        minutes.append({"date": NOW.isoformat(), "open": 24510, "high": 30000, "low": 20000, "close": 21000})
        s["spotQuote"]["timestamp"] = (NOW+timedelta(minutes=1)).isoformat()
        result = nifty_regime(s, "NIFTY50", NOW+timedelta(minutes=1))
        self.assertEqual(result["completedBars"], 9)
        self.assertEqual(result["signalAt"], NOW.isoformat())
        self.assertEqual((result["support"], result["resistance"]), (24495, 24505))

    def test_chart_gaps_leading_missing_stale_calendar_and_short_history_never_use_macro(self):
        for invalid in ("gap", "leading", "empty", "stale", "calendar", "opening", "few"):
            _, s = fixture("SENSEX")
            chart_fixture(s, "SENSEX")
            if invalid == "gap":
                s["niftyCandles"].pop(12)
            elif invalid == "leading":
                s["niftyCandles"] = s["niftyCandles"][-20:]
            elif invalid == "empty":
                s["niftyCandles"] = []
            elif invalid == "stale":
                s["niftySpotQuote"]["timestamp"] = (NOW-timedelta(seconds=31)).isoformat()
            elif invalid == "calendar":
                s["niftySessions"] = []
            elif invalid == "opening":
                s["niftySessions"] = copy.deepcopy(s["sessions"])
                s["niftySessions"][0]["openTime"] = "09:00"
            else:
                s["niftyCandles"] = s["niftyCandles"][:15]
            with self.subTest(invalid=invalid):
                result = nifty_regime(s, "SENSEX", NOW)
                self.assertEqual(result["state"], "unavailable")
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["signalBar"])
                self.assertIsNone(result["lastClose"])
                self.assertTrue(result["reason"])
                self.assertEqual(result["macroEvidence"]["niftyChangePct"], .1)

    def test_both_indices_actual_rolls_use_nifty_support_resistance_not_selected_index_or_macro(self):
        for index in ("NIFTY50", "SENSEX"):
            r, s, _ = linked(index=index)
            chart_fixture(s, index, "breakdown")
            minutes = s["candles" if index == "NIFTY50" else "niftyCandles"]
            for n in range(35, 40):
                minutes[n] = {**minutes[n], "open": 24499, "high": 24500, "low": 24492, "close": 24493}
            result = build_position_review(r, s, NOW, rate=RATE, dividend=DIVIDEND)
            self.assertEqual(result["regime"]["state"], "risk-off")
            self.assertEqual(result["regime"]["direction"], "down")
            rolls = [c for c in result["candidates"] if c["action"] == "roll"]
            self.assertTrue(rolls)
            if index == "NIFTY50":
                self.assertTrue(any(not c["premiumBalance"]["preferred"] for c in rolls))
            for roll in rolls:
                self.assertEqual(roll["closingLegs"][0]["instrumentType"], "PE")
                self.assertLessEqual(roll["maxLoss"], result["maxLoss"])
                if not roll["premiumBalance"]["preferred"]:
                    self.assertIn("NIFTY", roll["premiumBalance"]["reason"])

    def test_entry_premium_soft_preference_improves_safe_skew_without_increasing_anchor_risk(self):
        r, s = fixture()
        years = (expiry_close("2026-12-01", s["sessions"])-NOW).total_seconds()/YEAR
        for inst in s["instruments"]:
            if inst["strike"] == 24500:
                continue
            mid = european_price(24500, inst["strike"], years, RATE, DIVIDEND,
                                 .15 if inst["instrumentType"] == "PE" else .23, inst["instrumentType"])
            q = s["quotes"][inst["exchange"]+":"+inst["tradingSymbol"]]
            q["depth"]["buy"][0]["price"] = max(.05, mid-.05)
            q["depth"]["sell"][0]["price"] = max(.15, mid+.05)
        result = entry(r, s)
        self.assertTrue(result["eligible"])
        trace = result["premiumSelection"]
        self.assertLess(result["premiumBalance"]["imbalanceRatio"], trace["anchorPremiumBalance"]["imbalanceRatio"])
        self.assertFalse(result["premiumBalance"]["preferred"])
        self.assertIn("directional", result["premiumBalance"]["reason"])
        self.assertEqual((result["shortPutStrike"], result["shortCallStrike"]), (24300, 24800))
        specs = select_legs(s["instruments"], result["expiry"], result["spot"], result["atmPremiumSum"], r["wingWidth"])
        anchor = modeled_legs(specs, s["quotes"], 24500, NOW, expiry_close(result["expiry"], s["sessions"]), RATE, DIVIDEND)
        cash = sum(l["entryPrice"]*(1 if l["direction"] == "sell" else -1) for l in anchor)
        _, loss = payoff_bounds(anchor, cash)
        stress, _ = stress_x(anchor, s["candles"], 24500, NOW, expiry_close(result["expiry"], s["sessions"]),
                             timestamp(result["exitAt"]), RATE, DIVIDEND)
        self.assertLessEqual(result["maxLoss"], loss*75)
        self.assertLessEqual(result["suggestedXPoints"], stress)
        self.assertLessEqual(trace["evaluatedBaskets"], 5)

    def test_roll_soft_preference_ranks_improved_executable_premiums_without_adding_risk(self):
        r, s, _ = linked()
        result = build_position_review(r, s, NOW, rate=RATE, dividend=DIVIDEND)
        best = result["candidates"][0]
        self.assertEqual(best["action"], "roll")
        self.assertLess(best["premiumBalance"]["imbalanceRatio"], result["premiumBalance"]["imbalanceRatio"])
        self.assertEqual(best["preferenceRank"], 1)
        self.assertLessEqual(best["maxLoss"], result["maxLoss"])
        for leg in best["legs"]:
            self.assertEqual(leg["quantity"], 75)
        shorts = {l["instrumentType"]: l for l in best["legs"] if l["direction"] == "sell"}
        self.assertEqual(best["premiumBalance"]["putPremium"], shorts["PE"]["bid"])
        self.assertEqual(best["premiumBalance"]["callPremium"], shorts["CE"]["bid"])

    def test_exact_exit_boundaries_no_intermediate_exits_and_entry_initial_policy(self):
        for pnl, reason in ((-100.01, "stop-loss"), (-100, "stop-loss"), (-99.99, None),
                            (199.99, None), (200, "target"), (200.01, "target")):
            result = {"exitAt": (NOW+timedelta(hours=1)).isoformat(), "candidates": [],
                      "mandatoryExit": False, "eligible": True, "executionStatus": "review-only"}
            exit_policy(result, NOW, -100, 200, pnl)
            self.assertEqual(result["exitPolicy"]["reason"], reason)
            self.assertEqual(result["mandatoryExit"], reason is not None)
        result = entry()
        self.assertEqual(result["exitPolicy"]["version"], "sl-target-time-v1")
        self.assertEqual(result["exitPolicy"]["stopPnlRupees"], -result["xPoints"]*result["quantity"])
        self.assertEqual(result["exitPolicy"]["targetPnlRupees"], 2*result["xPoints"]*result["quantity"])
        self.assertIsNone(result["exitPolicy"]["reason"])
        self.assertFalse(result["mandatoryExit"])
        r, s, b = linked(quantity=150)
        for result in (adjustment(r, s, b), build_position_review(r, s, NOW, rate=RATE, dividend=DIVIDEND)):
            self.assertTrue(all(c["action"] in ("hold", "roll") for c in result["candidates"]))
            self.assertIsNone(result["exitPolicy"]["reason"])
            self.assertFalse(result["mandatoryExit"])

    def test_current_position_target_exit_uses_verified_session_total_and_displayed_sl(self):
        r, s, _ = linked()
        r.update(riskFreeRate=-.05, dividendYield=0)
        s["positions"]["net"], s["trades"] = [], []
        years = (expiry_close("2026-12-01", s["sessions"])-NOW).total_seconds()/YEAR
        for n, (strike, direction) in enumerate(((20000, "BUY"), (20100, "SELL"))):
            inst = contract(s["instruments"], "2026-12-01", strike, "CE")
            mid = european_price(24500, strike, years, -.05, 0, .18, "CE")
            q = s["quotes"]["NFO:"+inst["tradingSymbol"]]
            q["depth"]["buy"][0]["price"], q["depth"]["sell"][0]["price"] = mid-.05, mid+.05
            s["positions"]["net"].append({"exchange": "NFO", "tradingsymbol": inst["tradingSymbol"],
                                          "product": "MIS", "quantity": 75*(1 if direction == "BUY" else -1),
                                          "overnight_quantity": 0})
            s["trades"].append({"exchange": "NFO", "tradingsymbol": inst["tradingSymbol"], "product": "MIS",
                                "trade_id": str(n), "quantity": 75, "transaction_type": direction,
                                "average_price": mid+(.05 if direction == "BUY" else -.05),
                                "fill_timestamp": (NOW-timedelta(minutes=1)).isoformat()})
        old = s["trades"][0]
        for trade_id, direction, price, seconds in (("profit-buy", "BUY", 10, 40), ("profit-sell", "SELL", 210, 30)):
            s["trades"].append({**old, "trade_id": trade_id, "product": "NRML", "transaction_type": direction,
                                "average_price": price, "fill_timestamp": (NOW-timedelta(seconds=seconds)).isoformat()})
        s["positions"]["net"].append({**s["positions"]["net"][0], "product": "NRML", "quantity": 0})
        result = build_position_review(r, s, NOW)
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertGreaterEqual(result["sessionPnl"]["totalRupees"], result["riskPlan"]["targetPnlRupees"])
        self.assertEqual(result["riskPlan"]["targetPnlRupees"], 2*result["riskPlan"]["suggestedSlRupees"])
        self.assertEqual(result["exitPolicy"]["reason"], "target")
        self.assertTrue(result["mandatoryExit"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["candidates"], [])
        s["quotes"] = {}
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertTrue(result["mandatoryExit"])
        self.assertEqual(result["exitPolicy"]["reason"], "target")
        self.assertFalse(result["eligible"])

    def test_time_and_earlier_session_exit_survive_unavailable_quotes_for_both_adjustment_modes(self):
        r, s, b = linked()
        s["sessions"][0]["closeTime"] = "10:15"
        s.update(quotes={}, spotQuote=None)
        for result in (adjustment(r, s, b), build_position_review(r, s, NOW, rate=RATE, dividend=DIVIDEND)):
            self.assertTrue(result["mandatoryExit"])
            self.assertEqual(result["exitPolicy"]["reason"], "time")
            self.assertEqual(result["executionStatus"], "exit-required")
            self.assertEqual(result["candidates"], [])

    def test_sensex_completion_stale_nifty_chart_drops_only_biased_rolls_without_early_exit(self):
        r, s, _ = linked(index="SENSEX")
        result = build_position_review(r, s, NOW, rate=RATE, dividend=DIVIDEND)
        self.assertTrue(any(c["action"] == "roll" for c in result["candidates"]))
        s["niftySpotQuote"]["timestamp"] = (NOW-timedelta(seconds=31)).isoformat()
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertEqual(result["regime"]["status"], "unavailable")
        self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])
        self.assertTrue(result["eligible"])
        self.assertFalse(result["mandatoryExit"])
        self.assertIsNone(result["exitPolicy"]["reason"])

    def test_signal_bar_change_at_five_minute_boundary_cannot_reuse_old_directional_rolls(self):
        r, s, _ = linked()
        minutes = chart_fixture(s, signal="breakdown")
        # The prior complete 09:50 bar broke resistance; the incomplete 09:55 bar reverses it.
        for n in range(35, 40):
            minutes[n] = {**minutes[n], "open": 24501+n-35, "high": 24520,
                          "low": 24500, "close": 24506+n-35}
        before = NOW-timedelta(seconds=1)
        result = build_position_review(r, s, before, rate=RATE, dividend=DIVIDEND)
        self.assertEqual(result["regime"]["state"], "risk-off")
        self.assertEqual(result["regime"]["direction"], "up")
        self.assertTrue(any(c["action"] == "roll" for c in result["candidates"]))
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertEqual(result["regime"]["state"], "risk-off")
        self.assertEqual(result["regime"]["direction"], "down")
        self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])
        self.assertFalse(result["mandatoryExit"])


class PositionReviewTests(unittest.TestCase):
    def review(self, request, snapshot, **model):
        return build_position_review(request, snapshot, NOW,
                                     rate=model.get("rate", RATE), dividend=model.get("dividend", DIVIDEND))

    def test_fifo_realized_roll_profit_and_entire_cash_in_payoff(self):
        r, s, _ = linked()
        old = s["trades"][0]
        qty = old["quantity"]
        rolled = contract(s["instruments"], "2026-12-01", 24100, "PE")
        for trade_id, symbol, direction, price, seconds in (
                ("roll-close", old["tradingsymbol"], "BUY", old["average_price"]-20, 40),
                ("roll-open", rolled["tradingSymbol"], "SELL", 120, 30)):
            s["trades"].append({**old, "trade_id": trade_id, "tradingsymbol": symbol,
                                "transaction_type": direction, "average_price": price,
                                "fill_timestamp": (NOW-timedelta(seconds=seconds)).isoformat()})
        s["positions"]["net"][0]["quantity"] = 0
        s["positions"]["net"].append({**s["positions"]["net"][0], "tradingsymbol": rolled["tradingSymbol"],
                                     "quantity": -qty})
        result = self.review(r, s)
        pnl = result["sessionPnl"]
        self.assertEqual(pnl["status"], "verified")
        self.assertEqual(pnl["realizedRupees"], 20*qty)
        self.assertEqual(pnl["tradeCount"], 6)
        self.assertEqual(len(result["positions"]), 4)
        cash = sum(t["quantity"]*t["average_price"]*(1 if t["transaction_type"] == "SELL" else -1)
                   for t in s["trades"])
        self.assertAlmostEqual(pnl["cashFlowRupees"], cash)
        self.assertAlmostEqual(pnl["totalRupees"], cash+pnl["closeValueRupees"])
        self.assertAlmostEqual(pnl["totalRupees"], pnl["realizedRupees"]+pnl["unrealizedRupees"])
        self.assertIn("FIFO", pnl["source"])
        self.assertAlmostEqual(result["payoff"]["maxProfitRupees"], cash)
        self.assertAlmostEqual(result["riskPlan"]["suggestedSlRupees"], cash/2)
        self.assertNotIn("originalRiskBudget", result)

    def test_closed_round_trip_all_products_retained_and_day_never_doublecounted(self):
        r, s, _ = closed_roll_cycle()
        result = self.review(r, s)
        self.assertEqual(result["sessionPnl"]["realizedRupees"], -20*75)
        old = s["trades"][0]
        for direction, price, trade_id in (("BUY", 10, "nrml-buy"), ("SELL", 30, "nrml-sell")):
            s["trades"].append({**old, "product": "NRML", "transaction_type": direction,
                                "average_price": price, "trade_id": trade_id})
        s["positions"]["net"].append({**s["positions"]["net"][0], "product": "NRML", "quantity": 0})
        again = self.review(r, s)
        self.assertAlmostEqual(again["sessionPnl"]["realizedRupees"], 0)
        self.assertAlmostEqual(again["sessionPnl"]["totalRupees"]-result["sessionPnl"]["totalRupees"], 20*75)
        self.assertEqual(again["sessionPnl"]["tradeCount"], 10)

    def test_mismatch_duplicates_overnight_and_missing_ledger_fail_closed(self):
        for invalid in ("mismatch", "duplicate", "overnight", "missing", "closed-missing", "future"):
            r, s, _ = closed_roll_cycle()
            if invalid == "mismatch":
                s["positions"]["net"][0]["quantity"] -= 1
            elif invalid == "duplicate":
                s["trades"].append(copy.deepcopy(s["trades"][0]))
            elif invalid == "overnight":
                s["positions"]["net"][-1]["overnight_quantity"] = 75
            elif invalid == "missing":
                s["trades"] = None
            elif invalid == "closed-missing":
                s["positions"]["net"].pop()
            else:
                s["trades"][0]["fill_timestamp"] = (NOW+timedelta(seconds=1)).isoformat()
            with self.subTest(invalid=invalid):
                result = self.review(r, s)
                self.assertEqual(result["sessionPnl"]["status"], "unavailable")
                self.assertIsNone(result["sessionPnl"]["totalRupees"])
                self.assertEqual(result["payoff"]["status"], "unavailable")
                self.assertFalse(result["positionsConfirmed"])
                self.assertEqual(result["candidates"][0]["action"], "review")
                self.assertTrue(result["positions"])

    def test_partial_realized_available_when_current_liquidation_quote_missing(self):
        r, s, _ = closed_roll_cycle()
        s["quotes"] = {}
        result = self.review(r, s)
        self.assertEqual(result["sessionPnl"]["status"], "partial")
        self.assertEqual(result["sessionPnl"]["realizedRupees"], -1500)
        self.assertIsNone(result["sessionPnl"]["totalRupees"])
        self.assertIsNone(result["sessionPnl"]["unrealizedRupees"])
        self.assertEqual(result["payoff"]["status"], "available")
        self.assertEqual(result["riskPlan"]["status"], "unavailable")
        self.assertAlmostEqual(result["riskPlan"]["suggestedSlRupees"], result["payoff"]["maxProfitRupees"]/2)
        self.assertIsNone(result["riskPlan"]["remainingLossRupees"])
        self.assertEqual(len(result["positions"]), 4)

    def test_fully_closed_shows_realized_without_candidates_or_model(self):
        r, s, _ = linked()
        for t in list(s["trades"]):
            s["trades"].append({**t, "trade_id": "close-"+t["trade_id"],
                                "transaction_type": "SELL" if t["transaction_type"] == "BUY" else "BUY",
                                "average_price": t["average_price"]+(10 if t["transaction_type"] == "BUY" else -10),
                                "fill_timestamp": (NOW-timedelta(seconds=20)).isoformat()})
        for p in s["positions"]["net"]:
            p["quantity"] = 0
        s.update(quotes={}, spotQuote=None, sessions=[])
        result = build_position_review(r, s, NOW)
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertAlmostEqual(result["sessionPnl"]["totalRupees"], 3000)
        self.assertAlmostEqual(result["sessionPnl"]["unrealizedRupees"], 0)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["candidates"], [])

    def test_exact_index_filter_does_not_include_other_nifty_indices(self):
        r, s, _ = linked()
        for symbol in ("BANKNIFTY26NOV24500CE", "FINNIFTY26NOV24500CE", "NIFTYNXT5026NOV24500CE"):
            s["positions"]["net"].append({**s["positions"]["net"][0], "tradingsymbol": symbol})
            s["trades"].append({**s["trades"][0], "tradingsymbol": symbol})
        result = self.review(r, s)
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(result["sessionPnl"]["tradeCount"], 4)
        self.assertEqual(result["sessionPnl"]["status"], "verified")

    def test_mixed_expiry_payoff_explicitly_unavailable_but_greeks_and_pnl_remain(self):
        r, s, _ = linked()
        symbol = s["positions"]["net"][0]["tradingsymbol"]
        inst = next(i for i in s["instruments"] if i["tradingSymbol"] == symbol)
        inst["expiry"] = "2026-12-02"
        s["sessions"].append({**s["sessions"][-1], "sessionDate": inst["expiry"]})
        result = self.review(r, s)
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertIsNotNone(result["greeks"])
        self.assertEqual(result["payoff"]["status"], "unavailable")
        self.assertIn("Different open expiries", result["payoff"]["reason"])
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])

    def test_naked_tail_unbounded_and_no_hold(self):
        for kind, direction in (("CE", "SELL"), ("CE", "BUY"), ("PE", "SELL")):
            r, s, _ = linked()
            t = next(t for t in s["trades"] if t["tradingsymbol"].endswith(kind))
            p = next(p for p in s["positions"]["net"] if p["tradingsymbol"] == t["tradingsymbol"])
            t["transaction_type"] = direction
            p["quantity"] = t["quantity"]*(1 if direction == "BUY" else -1)
            s["trades"], s["positions"]["net"] = [t], [p]
            result = self.review(r, s)
            payoff = result["payoff"]
            self.assertEqual(payoff["lossUnbounded"], kind == "CE" and direction == "SELL")
            self.assertEqual(payoff["profitUnbounded"], kind == "CE" and direction == "BUY")
            self.assertNotIn("hold", [c["action"] for c in result["candidates"]])
            self.assertEqual(result["candidates"][0]["action"], "review")
            if kind == "PE":
                self.assertAlmostEqual(payoff["maxLossRupees"],
                                       p["quantity"]*-1*(result["positionRisk"][0]["strike"]-t["average_price"]))

    def test_actual_partial_fill_quantities_have_complete_weighted_greeks(self):
        r, s, _ = linked()
        for n, (p, t) in enumerate(zip(s["positions"]["net"], s["trades"])):
            p["quantity"] = (n+1)*13*(1 if p["quantity"] > 0 else -1)
            t["quantity"] = abs(p["quantity"])
        result = self.review(r, s)
        self.assertIsNotNone(result["greeks"])
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        for greek, value in result["greeks"]["quantityWeighted"].items():
            self.assertAlmostEqual(value, sum(p["greeks"]["quantityWeighted"][greek] for p in result["positionRisk"]))
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])

    def test_request_model_overrides_configuration_zero_yield_and_supported_ranges(self):
        r, s, _ = linked()
        r.update(riskFreeRate=.06, dividendYield=0)
        result = self.review(r, s, rate=.9, dividend=.8)
        self.assertEqual(result["modelInputs"]["source"], "request")
        self.assertEqual(result["modelInputs"]["dividendYield"], 0)
        self.assertIsNotNone(result["greeks"])
        self.assertEqual(entry(r, s, rate=.9, dividend=.8)["modelInputs"]["riskFreeRate"], .06)
        for key, value in (("riskFreeRate", -.051), ("riskFreeRate", 1.01),
                           ("riskFreeRate", float("nan")), ("dividendYield", -.001),
                           ("dividendYield", float("inf")), ("dividendYield", True)):
            bad = {**r, key: value}
            actual = self.review(bad, s)
            self.assertIsNone(actual["greeks"])
            self.assertIsNone(actual["modelInputs"][key])
        del r["dividendYield"]
        self.assertEqual(self.review(r, s)["modelInputs"]["source"], "mixed")
        self.assertIsNone(build_position_review(r, s, NOW)["modelInputs"]["dividendYield"])

    def test_bounded_risk_budget_and_mandatory_stop_have_no_invented_target(self):
        r, s, _ = linked(quantity=150)
        result = self.review(r, s)
        risk = result["riskPlan"]
        self.assertEqual(risk["status"], "available")
        self.assertAlmostEqual(risk["suggestedSlRupees"], result["payoff"]["maxProfitRupees"]/2)
        self.assertAlmostEqual(risk["remainingLossRupees"],
                               risk["suggestedSlRupees"]+result["sessionPnl"]["totalRupees"])
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(risk["targetPnlRupees"], 2*risk["suggestedSlRupees"])
        self.assertTrue(all(c["action"] in ("hold", "roll") for c in result["candidates"]))
        for c in result["candidates"]:
            if c["action"] != "close":
                self.assertLessEqual(c["stressLossRupees"], c["remainingRiskBudget"])
                self.assertLessEqual(c["maxLoss"], result["maxLoss"])
        # A low-remaining-premium bounded basket has a small session max profit but substantial mark loss.
        for t in s["trades"]:
            if t["transaction_type"] == "SELL":
                t["average_price"] -= 100
        stopped = self.review(r, s)
        self.assertTrue(stopped["mandatoryExit"])
        self.assertEqual(stopped["executionStatus"], "exit-required")
        self.assertEqual(stopped["candidates"], [])
        self.assertEqual(stopped["exitPolicy"]["reason"], "stop-loss")

    def test_rolls_preserve_current_loss_limit_and_both_current_and_repriced_session_sl(self):
        r, s, _ = linked()
        result = self.review(r, s)
        rolls = [c for c in result["candidates"] if c["action"] == "roll"]
        self.assertTrue(rolls)
        for c in rolls:
            self.assertLessEqual(c["maxLoss"], result["payoff"]["maxLossRupees"])
            self.assertLessEqual(c["stressLossRupees"], c["remainingRiskBudget"])
            self.assertLessEqual(c["remainingRiskBudget"],
                                 result["riskPlan"]["suggestedSlRupees"]+c["strategyPnlRupees"])
            self.assertLessEqual(c["remainingRiskBudget"], c["maxProfit"]/2+c["strategyPnlRupees"])
            self.assertEqual({l["quantity"] for l in c["legs"]}, {75})
            self.assertIn("protective long first", c["steps"][0])
            self.assertIn("before selling its replacement", c["steps"][1])
            self.assertIn("current-session", c["reason"])
            self.assertNotIn("baseline", " ".join(c["steps"]))

    def test_action_candidates_satisfy_parent_api_numerical_guards(self):
        for r, s, _ in (linked(), linked(quantity=150), closed_roll_cycle()):
            r.update(strategyId=None, riskFreeRate=.06, dividendYield=0)
            result = self.review(r, s)
            candidates = result["candidates"]
            self.assertEqual(len({c["id"] for c in candidates}), len(candidates))
            self.assertTrue(all(c["action"] in ("hold", "roll") for c in candidates))
            self.assertEqual(result["executionStatus"], "review-only")
            self.assertTrue(result["eligible"])
            for candidate in candidates:
                self.assertTrue(candidate["eligible"])
                self.assertEqual(result["sessionPnl"]["status"], "verified")
                self.assertEqual(result["riskPlan"]["status"], "available")
                self.assertGreater(result["riskPlan"]["suggestedSlRupees"], 0)
                self.assertGreater(result["riskPlan"]["remainingLossRupees"], 0)
                self.assertEqual(result["payoff"]["status"], "available")
                self.assertTrue(math.isfinite(result["payoff"]["maxLossRupees"]))
                self.assertTrue(all(math.isfinite(result["greeks"]["quantityWeighted"][g])
                                    for g in ("delta", "gamma", "vega", "theta")))
                self.assertGreaterEqual(candidate["maxLoss"], 0)
                self.assertLessEqual(candidate["maxLoss"], result["payoff"]["maxLossRupees"]+.01)
                self.assertGreaterEqual(candidate["stressLossRupees"], 0)
                self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"]+.01)
                self.assertLessEqual(candidate["remainingRiskBudget"], result["riskPlan"]["remainingLossRupees"]+.01)

    def test_review_eligibility_survives_completion_without_advisory_only_status(self):
        r, s, _ = linked()
        result = self.review(r, s)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["executionStatus"], "review-only")
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["executionStatus"], "review-only")
        revalidate_completion(result, s, NOW+timedelta(seconds=61), "adjustment")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["executionStatus"], "unavailable")

    def test_fallback_closed_and_mandatory_review_results_are_not_actionable(self):
        r, s, _ = linked()
        fallback = build_position_review(r, s, NOW)
        self.assertFalse(fallback["eligible"])
        self.assertEqual(fallback["candidates"][0]["action"], "review")
        revalidate_completion(fallback, s, NOW, "adjustment")
        self.assertFalse(fallback["eligible"])
        deadline = self.review(r, s)
        deadline["exitAt"] = (NOW+timedelta(seconds=10)).isoformat()
        revalidate_completion(deadline, s, NOW+timedelta(seconds=11), "adjustment")
        self.assertFalse(deadline["eligible"])
        self.assertTrue(deadline["mandatoryExit"])
        self.assertEqual(deadline["candidates"], [])
        self.assertEqual(deadline["exitPolicy"]["reason"], "time")
        for t in s["trades"]:
            if t["transaction_type"] == "SELL":
                t["average_price"] -= 100
        stopped = self.review(r, s)
        self.assertTrue(stopped["mandatoryExit"])
        self.assertFalse(stopped["eligible"])
        r, s = fixture()
        self.assertFalse(self.review(r, s)["eligible"])

    def test_existing_two_leg_hedged_spread_can_stay_without_assuming_four_leg_entry(self):
        r, s, _ = linked()
        s["positions"]["net"] = [p for p in s["positions"]["net"] if p["tradingsymbol"].endswith("CE")]
        s["trades"] = [t for t in s["trades"] if t["tradingsymbol"].endswith("CE")]
        result = self.review(r, s)
        self.assertEqual(len(result["positions"]), 2)
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertIn("hold", [c["action"] for c in result["candidates"]])
        self.assertNotIn("roll", [c["action"] for c in result["candidates"]])
        self.assertFalse(result["payoff"]["lossUnbounded"])

    def test_missing_regime_only_blocks_directional_rolls_not_hedged_stay(self):
        r, s, _ = linked()
        s["candles"] = s["candles"][-16:]
        result = self.review(r, s)
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertIn("hold", [c["action"] for c in result["candidates"]])
        self.assertNotIn("roll", [c["action"] for c in result["candidates"]])

    def test_stress_budget_exceeded_excludes_hold_reduce_and_roll(self):
        r, s, _ = linked(quantity=150)
        with patch("strangle_engine.stress_x", return_value=(100000, {})):
            result = self.review(r, s)
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])
        self.assertFalse(result["mandatoryExit"])
        self.assertGreater(result["riskPlan"]["stressLossRupees"], result["riskPlan"]["remainingLossRupees"])

    def test_closed_adjustment_profit_shifts_session_payoff_and_sl_not_open_greeks(self):
        r, s, _ = linked()
        before = self.review(r, s)
        t = s["trades"][0]
        for trade_id, direction, price, seconds in (
                ("profit-open", "SELL", 120, 40), ("profit-close", "BUY", 20, 30)):
            s["trades"].append({**t, "trade_id": trade_id, "transaction_type": direction,
                                "product": "NRML", "average_price": price,
                                "fill_timestamp": (NOW-timedelta(seconds=seconds)).isoformat()})
        s["positions"]["net"].append({**s["positions"]["net"][0], "product": "NRML", "quantity": 0})
        after = self.review(r, s)
        self.assertAlmostEqual(after["sessionPnl"]["realizedRupees"], 7500)
        self.assertAlmostEqual(after["sessionPnl"]["totalRupees"]-before["sessionPnl"]["totalRupees"], 7500)
        self.assertAlmostEqual(after["payoff"]["maxProfitRupees"]-before["payoff"]["maxProfitRupees"], 7500)
        self.assertAlmostEqual(before["payoff"]["maxLossRupees"]-after["payoff"]["maxLossRupees"], 7500)
        self.assertAlmostEqual(after["riskPlan"]["suggestedSlRupees"]-before["riskPlan"]["suggestedSlRupees"], 3750)
        self.assertEqual(after["greeks"], before["greeks"])

    def test_completion_invalidates_stale_quotes_and_deadline_retains_only_close(self):
        r, s, _ = linked()
        result = self.review(r, s)
        s["quotes"][next(iter(s["quotes"]))]["timestamp"] = (NOW-timedelta(seconds=61)).isoformat()
        # Stale data for a used contract, not an unrelated chain member.
        used = result["legs"][0]
        s["quotes"][used["exchange"]+":"+used["tradingSymbol"]]["timestamp"] = (NOW-timedelta(seconds=61)).isoformat()
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertTrue(result["positions"])
        self.assertEqual(result["sessionPnl"]["status"], "unavailable")
        r, s, _ = linked()
        result = self.review(r, s)
        result["exitAt"] = (NOW+timedelta(seconds=10)).isoformat()
        revalidate_completion(result, s, NOW+timedelta(seconds=11), "adjustment")
        self.assertEqual(result["executionStatus"], "exit-required")
        self.assertTrue(result["mandatoryExit"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["exitPolicy"]["reason"], "time")

    def test_reads_actual_net_without_baseline_or_model_and_never_uses_day_as_net(self):
        request, snapshot, _ = linked()
        request["strategyId"] = None
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(len(result["positionRisk"]), 4)
        self.assertIsNone(result["greeks"])
        self.assertIsNone(result["xPoints"])
        self.assertNotIn("originalRiskBudget", result)
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])
        self.assertTrue(all("risk-free rate" in p["unavailableReason"] for p in result["positionRisk"]))

    def test_arbitrary_actual_quantities_weight_each_leg_not_one_matched_lot(self):
        request, snapshot, _ = linked()
        snapshot["positions"]["net"][0]["quantity"] *= 2
        result = build_position_review(request, snapshot, NOW, rate=RATE, dividend=DIVIDEND)
        self.assertEqual(result["blockers"], [])
        totals = result["greeks"]["quantityWeighted"]
        for greek in ("delta", "gamma", "vega", "theta"):
            self.assertAlmostEqual(totals[greek], sum(p["greeks"]["quantityWeighted"][greek] for p in result["positionRisk"]))
        self.assertNotIn("delta", result["greeks"])

    def test_naked_existing_position_can_be_reviewed_without_enabling_roll(self):
        request, snapshot, _ = linked()
        snapshot["positions"]["net"] = snapshot["positions"]["net"][:1]
        result = build_position_review(request, snapshot, NOW, rate=RATE, dividend=DIVIDEND)
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual([c["id"] for c in result["candidates"]], ["review-current-positions"])

    def test_no_open_positions_has_no_ai_candidates(self):
        request, snapshot = fixture()
        snapshot["positions"]["day"] = [{"exchange": "NFO", "tradingsymbol": "CLOSED", "quantity": 100}]
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["candidates"], [])

    def test_missing_net_is_explicitly_unavailable(self):
        request, snapshot = fixture()
        snapshot["positions"] = {"day": []}
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertIn("net positions unavailable", result["blockers"][0])

    def test_missing_quote_context_still_returns_actual_positions(self):
        request, snapshot, _ = linked()
        snapshot.update(spotQuote=None, quotes={}, candles=[], sessions=[])
        result = build_position_review(request, snapshot, NOW)
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertIsNone(result["trend"])

    def test_stale_capture_and_exit_deadline_cannot_reach_ai(self):
        request, snapshot, _ = linked()
        result = build_position_review(request, snapshot, NOW)
        revalidate_completion(result, snapshot, NOW + timedelta(seconds=61), "adjustment")
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertFalse(result["candidates"])
        result = build_position_review(request, snapshot, NOW.replace(hour=15, minute=16))
        self.assertTrue(result["mandatoryExit"])
        revalidate_completion(result, snapshot, NOW.replace(hour=15, minute=16), "adjustment")
        self.assertFalse(result["candidates"])


class SelectionTests(unittest.TestCase):
    def test_exact_24500_220_outward_example_and_decimal_premiums(self):
        self.assertEqual(short_strikes(24500, 220), (24200, 24800))
        self.assertEqual(short_strikes(24500, 200.01), (24200, 24800))
        self.assertEqual(short_strikes(24500, 200), (24300, 24700))

    def test_atm_common_tie_chooses_lower(self):
        _, s = fixture()
        self.assertEqual(common_atm(s["instruments"], "2026-12-01", 24550), 24500)
        removed = [i for i in s["instruments"] if not (i["strike"] == 24500 and i["instrumentType"] == "PE")]
        self.assertEqual(common_atm(removed, "2026-12-01", 24500), 24400)

    def test_exact_short_contract_cannot_be_substituted(self):
        _, s = fixture()
        missing = [i for i in s["instruments"] if not (i["strike"] == 24200 and i["instrumentType"] == "PE")]
        with self.assertRaisesRegex(ValueError, "Exact listed"):
            select_legs(missing, "2026-12-01", 24500, 220, 1000)

    def test_hedges_round_outward_and_never_narrow(self):
        _, s = fixture()
        specs = select_legs(s["instruments"], "2026-12-01", 24500, 220, 1050)
        self.assertEqual([i["strike"] for i, _ in specs], [24200, 24800, 23100, 25900])
        with self.assertRaisesRegex(ValueError, "outward"):
            select_legs(s["instruments"], "2026-12-01", 24500, 220, 10000)

    def test_nearest_expiry_including_today_not_inferred(self):
        _, s = fixture()
        today = [{**i, "expiry": "2026-11-17"} for i in s["instruments"]]
        self.assertEqual(select_expiry(s["instruments"]+today, NOW), "2026-11-17")
        self.assertEqual(select_expiry(s["instruments"]+today, NOW+timedelta(days=1)), "2026-12-01")

    def test_both_exchanges_dynamic_lots_ticks_and_identical_results(self):
        for index, lot, tick in (("NIFTY50", 65, .05), ("SENSEX", 20, .1)):
            r, s = fixture(index, lot, tick)
            first = entry(r, s)
            self.assertEqual(first["blockers"], [])
            self.assertTrue(first["eligible"])
            self.assertFalse(first["mandatoryExit"])
            self.assertEqual(first["sessionDate"], "2026-11-17")
            self.assertEqual(first, entry(r, s))
            self.assertEqual(first["quantity"], lot)
            self.assertEqual(first["lotSize"], lot)
            self.assertEqual(len(first["legs"]), 4)
            self.assertEqual(first["atmPremiumSum"], 220)
            self.assertTrue(all(l["quantity"] == lot for l in first["legs"]))
            self.assertTrue(all(l["signedQuantity"] == lot*(1 if l["direction"] == "buy" else -1)
                                for l in first["legs"]))

    def test_missing_model_never_defaults_even_with_override(self):
        r, s = fixture()
        r["xOverride"] = 10
        for model in ({"rate": None}, {"dividend": None}, {"rate": math.nan}):
            result = entry(r, s, **model)
            self.assertIsNone(result["suggestedXPoints"])
            self.assertEqual(result["executionStatus"], "unavailable")

    def test_explicit_zero_rate_and_yield_are_valid_not_missing(self):
        self.assertEqual(entry(rate=0, dividend=0)["blockers"], [])

    def test_invalid_x_and_impossible_target_block(self):
        for x in (0, -1, math.nan, True, 100000):
            r, s = fixture()
            r["xOverride"] = x
            result = entry(r, s)
            self.assertTrue(result["blockers"])
            self.assertEqual(result["executionStatus"], "unavailable")

    def test_override_at_or_below_executable_spread_is_non_actionable(self):
        baseline = entry()
        spread = baseline["xCalculation"]["roundTripSpreadAllowancePoints"]
        for override in (spread/2, spread):
            r, s = fixture()
            r["xOverride"] = override
            result = entry(r, s)
            self.assertFalse(result["eligible"])
            self.assertEqual(result["executionStatus"], "unavailable")
            self.assertIn("stop is already hit", " ".join(result["blockers"]))
        r, s = fixture()
        r["xOverride"] = spread+.05
        self.assertTrue(entry(r, s)["eligible"])


class QuoteAndSessionTests(unittest.TestCase):
    def test_quote_gates(self):
        _, s = fixture()
        i = contract(s["instruments"], "2026-12-01", 24200, "PE")
        key = i["exchange"]+":"+i["tradingSymbol"]
        variations = [
            {"timestamp": (NOW-timedelta(seconds=61)).isoformat()},
            {"timestamp": (NOW+timedelta(seconds=3)).isoformat()},
            {"timestamp": None}, {"depth": {}}, {"volume": 0},
            {"depth": {"buy": [{"price": 11, "quantity": 75}], "sell": [{"price": 10, "quantity": 75}]}},
            {"depth": {"buy": [{"price": 1, "quantity": 75}], "sell": [{"price": 2, "quantity": 75}]}},
            {"depth": {"buy": [{"price": 10, "quantity": 74}], "sell": [{"price": 10.1, "quantity": 75}]}},
        ]
        for change in variations:
            with self.subTest(change=change):
                quotes = copy.deepcopy(s["quotes"])
                quotes[key].update(change)
                with self.assertRaises((ValueError, TypeError)):
                    qualified_quote(i, quotes, NOW, 75)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            qualified_quote(i, {}, NOW, 75)

    def test_spot_30_second_future_and_cross_leg_skew_gates(self):
        for seconds in (-31, 3):
            r, s = fixture()
            s["spotQuote"]["timestamp"] = (NOW+timedelta(seconds=seconds)).isoformat()
            self.assertTrue(entry(r, s)["blockers"])
        r, s = fixture()
        s["quotes"]["NFO:NIFTY5024200PE"]["timestamp"] = (NOW-timedelta(seconds=6)).isoformat()
        result = entry(r, s)
        self.assertEqual(result["blockers"], [])
        self.assertNotEqual(result["shortPutStrike"], 24200)

    def test_missing_calendar_source_and_short_session_deadlines(self):
        r, s = fixture()
        s["sessions"] = []
        self.assertTrue(entry(r, s)["blockers"])
        r, s = fixture()
        s["sessions"][0]["source"] = ""
        self.assertTrue(entry(r, s)["blockers"])
        _, s = fixture()
        s["sessions"][0]["closeTime"] = "12:00"
        _, _, deadline, cutoff = session_window(NOW, s["sessions"])
        self.assertEqual(deadline.strftime("%H:%M"), "11:45")
        self.assertEqual(cutoff.strftime("%H:%M"), "11:30")

    def test_entry_cutoff_exact_and_closed_session_reference_only(self):
        r, s = fixture()
        self.assertIn("cutoff", " ".join(entry(r, s, NOW.replace(hour=15))["blockers"]))
        self.assertEqual(entry(r, s, NOW.replace(hour=15, minute=15))["executionStatus"], "reference-only")
        self.assertEqual(entry(r, s, NOW.replace(hour=8))["executionStatus"], "reference-only")

    def test_expiry_day_is_supported_with_verified_close(self):
        r, s = fixture()
        s["sessions"][1]["sessionDate"] = "2026-11-17"
        for i in s["instruments"]:
            i["expiry"] = "2026-11-17"
        result = entry(r, s)
        self.assertEqual(result["expiry"], "2026-11-17")
        self.assertEqual(result["blockers"], [])

    def test_completion_cannot_publish_stale_advice(self):
        r, s = fixture()
        result = entry(r, s)
        revalidate_completion(result, s, NOW+timedelta(seconds=31), "entry")
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertIn("Completion revalidation", " ".join(result["blockers"]))

    def test_completion_also_revalidates_replacement_contracts(self):
        r, s, b = linked()
        result = adjustment(r, s, b)
        old_symbols = {l["tradingSymbol"] for l in result["legs"]}
        roll = next(c for c in result["candidates"] if c["action"] == "roll")
        new = next(l for l in roll["legs"] if l["tradingSymbol"] not in old_symbols)
        s["quotes"][new["exchange"]+":"+new["tradingSymbol"]]["timestamp"] = (NOW-timedelta(seconds=61)).isoformat()
        revalidate_completion(result, s, NOW, "adjustment")
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertEqual(result["candidates"], [])

    def test_stale_valuation_does_not_clear_mandatory_manual_exit(self):
        r, s, b = linked()
        result = adjustment(r, s, b)
        result["executionStatus"] = "exit-required"
        revalidate_completion(result, s, NOW+timedelta(seconds=31), "adjustment")
        self.assertEqual(result["executionStatus"], "exit-required")
        self.assertEqual(result["candidates"], [])


class StressAndGreekTests(unittest.TestCase):
    def test_at_least_fifteen_returns_completed_current_session_only(self):
        _, s = fixture()
        opened, *_ = session_window(NOW, s["sessions"])
        self.assertEqual(len(completed_candles(s["candles"][-16:], NOW, opened)), 16)
        with self.assertRaisesRegex(ValueError, "15 completed"):
            completed_candles(s["candles"][-15:], NOW, opened)
        unfinished = {**s["candles"][-1], "date": NOW.isoformat()}
        overnight = {**s["candles"][0], "date": (NOW-timedelta(days=1)).isoformat()}
        self.assertEqual(len(completed_candles(s["candles"]+[unfinished, overnight], NOW, opened)), 45)
        with self.assertRaisesRegex(ValueError, "gaps"):
            completed_candles(s["candles"][:20]+s["candles"][21:], NOW, opened)

    def test_missing_candles_no_override_bypass(self):
        r, s = fixture()
        r["xOverride"] = 10
        s["candles"] = []
        result = entry(r, s)
        self.assertIsNone(result["suggestedXPoints"])
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertEqual(len(result["legs"]), 0)
        self.assertIsNone(result["greeks"])

    def test_stress_grid_fair_value_plus_spread_exactly_once(self):
        r, s = fixture()
        result = entry(r, s)
        calc = result["xCalculation"]
        increment = calc["pointIncrement"]
        raw = calc["worstFairValueLossPoints"]+calc["roundTripSpreadAllowancePoints"]
        self.assertAlmostEqual(result["suggestedXPoints"], math.ceil(raw/increment-1e-10)*increment)
        self.assertAlmostEqual(calc["roundTripSpreadAllowancePoints"],
                               sum(l["ask"]-l["bid"] for l in result["legs"]))
        self.assertEqual(calc["ivShocks"], [-.01, 0, .01])
        self.assertEqual(calc["elapsedSeconds"], [0, 60, 180, 300])
        self.assertEqual(calc["returnCount"], 44)
        self.assertIn("gross", calc["fees"])
        self.assertEqual(len(calc["grid"]), 36)
        self.assertAlmostEqual(calc["worstFairValueLossPoints"],
                               max(0, -min(row["fairValuePnlPoints"] for row in calc["grid"])))
        self.assertAlmostEqual(calc["approximateGreekPnlPoints"], sum(calc["greekAttribution"].values()))
        self.assertEqual(point_increment([{"tickSize": .05}, {"tickSize": .1}]), .1)

    def test_stress_time_grid_capped_by_exit_deadline(self):
        r, s = fixture()
        result = entry(r, s)
        _, calculation = stress_x(result["legs"], s["candles"], result["spot"], NOW,
                                  expiry_close(result["expiry"], s["sessions"]),
                                  NOW+timedelta(seconds=90), RATE, DIVIDEND)
        self.assertEqual(calculation["elapsedSeconds"], [0, 60, 90])

    def test_signed_greeks_match_finite_differences_and_quantity_units(self):
        r, s = fixture()
        result = entry(r, s)
        legs = result["legs"]
        expiry_at = expiry_close(result["expiry"], s["sessions"])
        years = (expiry_at-NOW).total_seconds()/YEAR
        def fair(spot=24500, shock=0, elapsed=0):
            return sum((1 if l["direction"] == "buy" else -1)*european_price(
                spot, l["strike"], years-elapsed/YEAR, RATE, DIVIDEND, l["iv"]+shock, l["instrumentType"]) for l in legs)
        g = result["greeks"]
        self.assertAlmostEqual(g["delta"], (fair(24500+.1)-fair(24500-.1))/.2, places=6)
        self.assertAlmostEqual(g["gamma"], (fair(24500+.1)-2*fair()+fair(24500-.1))/.01, places=6)
        self.assertAlmostEqual(g["vega"], (fair(shock=.00001)-fair(shock=-.00001))/.002, places=4)
        self.assertAlmostEqual(g["theta"], (fair(elapsed=1)-fair(elapsed=-1))*43200, places=4)
        for k in ("delta", "gamma", "vega", "theta"):
            self.assertAlmostEqual(g["quantityWeighted"][k], g[k]*75)

    def test_intraday_not_daily_or_cash_vwap_and_unknown_regime(self):
        _, s = fixture()
        trend, regime = intraday_context(s["candles"], 24500)
        self.assertEqual(trend["direction"], "up")
        self.assertEqual(regime["state"], "unavailable")
        self.assertNotIn("vwap", trend)
        for values, expected in (((1, 2, -1), "risk-on"), ((-1, -2, 1), "risk-off"),
                                 ((1, -1, 1), "mixed")):
            evidence = dict(zip(("niftyChangePct", "sensexChangePct", "vixChangePct"), values))
            evidence["fresh"] = True
            self.assertEqual(intraday_context(s["candles"], 24500, evidence)[1]["state"], "unavailable")


class AdjustmentTests(unittest.TestCase):
    def test_broker_ledger_is_separate_from_immutable_original_baseline(self):
        r, s, b = linked()
        frozen = copy.deepcopy(b)
        flows = fill_ledger(s["trades"])
        reconciled = reconcile_baseline(b, flows, s["instruments"])
        result = adjustment(r, s, reconciled)
        self.assertEqual(b, frozen)
        for field in ("legs", "xPoints", "originalRiskBudget", "originalMaxLoss", "quantity", "sessionDate"):
            self.assertEqual(reconciled[field], frozen[field])
        self.assertTrue(result["positionsConfirmed"])
        self.assertTrue(result["reconciliation"]["verified"])
        self.assertEqual(result["reconciliation"]["source"], "broker-fills")
        self.assertEqual(result["reconciliation"]["tradeIds"], [t["trade_id"] for t in s["trades"]])

    def test_broker_ledger_cannot_replace_original_actual_entry_cash(self):
        r, s, b = linked()
        b["legs"][0]["entryPrice"] += 1
        reconciled = reconcile_baseline(b, fill_ledger(s["trades"]), s["instruments"])
        result = adjustment(r, s, reconciled)
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["strategyPnlRupees"])
        self.assertIn("frozen baseline", " ".join(result["blockers"]))

    def test_selected_trade_ids_derive_actual_fills_not_position_average(self):
        r, s, b = linked()
        frozen = copy.deepcopy(b)
        ids = [t["trade_id"] for t in s["trades"]]
        reconciled = reconcile_trade_ids(b, list(reversed(ids)), s["trades"], s["instruments"], NOW)
        result = adjustment(r, s, reconciled)
        self.assertTrue(result["eligible"])
        self.assertEqual(b, frozen)
        self.assertEqual(result["reconciliation"]["source"], "selected-trade-ids")
        self.assertEqual(result["reconciliation"]["selectedTradeIds"], sorted(ids))
        self.assertEqual(result["reconciliation"]["cashFlows"], fill_ledger(s["trades"]))
        self.assertAlmostEqual(result["strategyPnlRupees"], -30)

    def test_unchanged_initial_verification_records_all_actual_fill_ids_and_cash(self):
        r, s, b = linked()
        result = adjustment(r, s, b)
        self.assertTrue(result["positionsConfirmed"])
        self.assertTrue(result["reconciliation"]["verified"])
        self.assertEqual(result["reconciliation"]["source"], "unchanged-baseline-broker-fills")
        self.assertEqual(result["reconciliation"]["selectedTradeIds"], sorted(t["trade_id"] for t in s["trades"]))
        self.assertEqual(result["reconciliation"]["cashFlows"], fill_ledger(s["trades"]))

    def test_omitting_previously_verified_closed_roll_loss_ids_is_blocked(self):
        r, s, b = closed_roll_cycle()
        ids = [t["trade_id"] for t in s["trades"]]
        full = reconcile_trade_ids(b, ids, s["trades"], s["instruments"], NOW)
        previous = adjustment(r, s, full)
        self.assertTrue(previous["positionsConfirmed"])
        self.assertAlmostEqual(previous["strategyPnlRupees"], -1530)
        omitted = [trade_id for trade_id in ids if trade_id not in ("new-open", "new-close")]
        with self.assertRaisesRegex(ValueError, "omit previously verified"):
            reconcile_trade_ids(b, omitted, s["trades"], s["instruments"], NOW,
                                prior=previous["reconciliation"])

    def test_null_ids_reuse_verified_roll_ledger_instead_of_resetting_cash_losses(self):
        r, s, b = closed_roll_cycle()
        full = reconcile_trade_ids(b, [t["trade_id"] for t in s["trades"]], s["trades"], s["instruments"], NOW)
        previous = adjustment(r, s, full)
        reused = reconcile_trade_ids(b, None, s["trades"], s["instruments"], NOW,
                                     prior=previous["reconciliation"])
        current = adjustment(r, s, reused)
        self.assertEqual(current["strategyPnlRupees"], previous["strategyPnlRupees"])
        self.assertAlmostEqual(current["strategyPnlRupees"], -1530)
        self.assertEqual(current["reconciliation"]["source"], "prior-verified-ledger")
        self.assertEqual(current["reconciliation"]["priorVerifiedTradeIds"],
                         previous["reconciliation"]["selectedTradeIds"])
        self.assertEqual(current["originalRiskBudget"], b["originalRiskBudget"])

    def test_selected_ids_missing_duplicate_other_session_and_incomplete_entry_are_blocked(self):
        r, s, b = linked()
        ids = [t["trade_id"] for t in s["trades"]]
        for invalid in ([], ids+["unknown"], ["0", "0"], list(range(4))):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reconcile_trade_ids(b, invalid, s["trades"], s["instruments"], NOW)
        trades = copy.deepcopy(s["trades"])
        trades.append(copy.deepcopy(trades[0]))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            reconcile_trade_ids(b, ids, trades, s["instruments"], NOW)
        trades = copy.deepcopy(s["trades"])
        trades[0]["fill_timestamp"] = (NOW-timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "session"):
            reconcile_trade_ids(b, ids, trades, s["instruments"], NOW)
        incomplete = reconcile_trade_ids(b, ids[:-1], s["trades"], s["instruments"], NOW)
        result = adjustment(r, s, incomplete)
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["strategyPnlRupees"])

    def test_broker_ledger_rejects_duplicate_ids_unknown_contracts_and_invalid_values(self):
        for change in (
                {"tradeId": ""}, {"tradeId": "1"}, {"signedQuantity": 0}, {"signedQuantity": True},
                {"price": 0}, {"price": math.nan}, {"product": "NRML"}, {"exchange": "BFO"},
                {"tradingSymbol": "INVENTED"}):
            _, s, b = linked()
            flows = fill_ledger(s["trades"])
            flows[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                reconcile_baseline(b, flows, s["instruments"])

    def test_signed_cash_and_liquidation_include_all_hedges_ignore_day_and_average(self):
        r, s, b = linked()
        result = adjustment(r, s, b)
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["positionsConfirmed"])
        self.assertAlmostEqual(result["strategyPnlPoints"], -.4)
        self.assertAlmostEqual(result["strategyPnlRupees"], -.4*75)
        self.assertEqual(result["xPoints"], 100)
        self.assertEqual(result["originalRiskBudget"], 7500)
        self.assertTrue(any(c["action"] == "hold" for c in result["candidates"]))
        self.assertEqual(result["candidates"], adjustment(r, s, b)["candidates"])

    def test_missing_fills_partial_fills_overnight_and_mixed_product_are_blocked(self):
        mutations = [
            lambda s, b: s.update(trades=[]),
            lambda s, b: s["trades"][0].update(quantity=50),
            lambda s, b: s["positions"]["net"][0].update(overnight_quantity=75),
            lambda s, b: s["positions"]["net"][0].update(quantity=-50),
            lambda s, b: s["trades"].append({**s["trades"][0], "product": "NRML", "trade_id": "extra"}),
            lambda s, b: s["positions"]["net"].append(s["positions"]["net"][0]),
            lambda s, b: b.update(sessionDate="2026-11-16"),
        ]
        for mutate in mutations:
            r, s, b = linked()
            mutate(s, b)
            result = adjustment(r, s, b)
            self.assertIsNone(result["strategyPnlRupees"])
            self.assertEqual(result["candidates"], [])
            self.assertTrue(result["blockers"])

    def test_ambiguous_roundtrip_cannot_be_hidden_by_net_or_cash(self):
        r, s, b = linked()
        trade = s["trades"][0]
        s["trades"].extend([{**trade, "trade_id": "close", "transaction_type": "BUY"},
                            {**trade, "trade_id": "reopen", "transaction_type": "SELL"}])
        # A matching round-trip is still ambiguous without explicit fill IDs.
        result = adjustment(r, s, b)
        self.assertTrue(result["blockers"])

    def test_freeze_risk_no_override_reset_stop_and_target_precede_rolls(self):
        for offset, expected in ((101, "exit-required"), (-201, "exit-required")):
            r, s, b = linked()
            r["xOverride"] = 100000
            for leg in b["legs"]:
                if leg["signedQuantity"] < 0:
                    leg["entryPrice"] -= offset/2
                    if leg["entryPrice"] <= 0:
                        self.fail("Fixture entry premium too small")
            for trade, leg in zip(s["trades"], b["legs"]):
                trade["average_price"] = leg["entryPrice"]
            result = adjustment(r, s, b)
            self.assertEqual(result["executionStatus"], expected)
            self.assertTrue(result["mandatoryExit"])
            self.assertFalse(result["eligible"])
            self.assertEqual(result["xPoints"], 100)
            self.assertEqual(result["candidates"], [])
            self.assertIn(result["exitPolicy"]["reason"], ("stop-loss", "target"))

    def test_deadline_exit_even_when_quotes_unavailable(self):
        r, s, b = linked()
        result = adjustment(r, s, b, NOW.replace(hour=15, minute=15))
        self.assertEqual(result["executionStatus"], "exit-required")
        self.assertIn("MANDATORY", " ".join(result["warnings"]))

    def test_no_discretionary_reduction_or_lot_increase_or_hedge_removal(self):
        r, s, b = linked(150)
        result = adjustment(r, s, b)
        self.assertFalse(any(c["action"] in ("close", "reduce") for c in result["candidates"]))
        for candidate in result["candidates"]:
            self.assertEqual(len(candidate["legs"]), 4)
            self.assertTrue(all(l["quantity"] <= 150 for l in candidate["legs"]))
            if candidate["action"] == "roll":
                self.assertLessEqual(candidate["maxLoss"], b["originalMaxLoss"])
                self.assertLessEqual(candidate["stressLossRupees"], result["remainingRiskBudget"])

    def test_defensive_roll_validates_realized_cash_stress_and_contractual_risk(self):
        r, s, b = linked()
        result = adjustment(r, s, b)
        rolls = [c for c in result["candidates"] if c["action"] == "roll"]
        self.assertTrue(rolls)
        for candidate in rolls:
            self.assertLessEqual(candidate["maxLoss"], result["maxLoss"])
            self.assertLessEqual(candidate["maxLoss"], b["originalMaxLoss"])
            self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"])
            self.assertAlmostEqual(candidate["remainingRiskBudget"],
                                   b["originalRiskBudget"]+candidate["strategyPnlAfterRupees"])
            self.assertEqual({l["quantity"] for l in candidate["legs"]}, {b["quantity"]})
            self.assertIn("protective long first", candidate["steps"][0])

    def test_1000_point_wings_30_point_stop_allow_only_non_increasing_contractual_risk(self):
        r, s, b = linked()
        b.update(xPoints=30, originalRiskBudget=2250)
        result = adjustment(r, s, b)
        rolls = [c for c in result["candidates"] if c["action"] == "roll"]
        self.assertTrue(rolls)
        self.assertGreater(result["maxLoss"], b["originalRiskBudget"])
        for candidate in rolls:
            self.assertGreater(candidate["maxLoss"], b["originalRiskBudget"])
            self.assertLessEqual(candidate["maxLoss"], result["maxLoss"])
            self.assertLessEqual(candidate["stressLossRupees"], candidate["remainingRiskBudget"])
            self.assertLess(candidate["remainingRiskBudget"], result["remainingRiskBudget"])
        # Moving only the threatened short outward spends credit and increases
        # the opposite wing's maximum loss. It must not be a valid candidate.
        old = next(l for l in result["legs"] if l["instrumentType"] == "CE" and l["direction"] == "sell")
        replacement = contract(s["instruments"], result["expiry"], old["strike"]+100, "CE")
        unsafe = modeled_legs([(replacement if l is old else l, l["direction"]) for l in result["legs"]],
                              s["quotes"], result["spot"], NOW, expiry_close(result["expiry"], s["sessions"]),
                              RATE, DIVIDEND, b["quantity"])
        _, cash, _ = attributed_ledger(b, s["positions"], s["trades"], NOW)
        new_short = next(l for l in unsafe if l["tradingSymbol"] == replacement["tradingSymbol"])
        cash_after = cash+b["quantity"]*(new_short["bid"]-old["ask"])
        _, unsafe_loss = payoff_bounds(unsafe, cash_after/b["quantity"])
        self.assertGreater(unsafe_loss*b["quantity"], result["maxLoss"])
        unsafe_symbols = {l["tradingSymbol"] for l in unsafe}
        self.assertFalse(any({l["tradingSymbol"] for l in c["legs"]} == unsafe_symbols for c in rolls))

    def test_roll_costs_cannot_sneak_through_using_pre_roll_remaining_budget(self):
        r, s, b = linked()
        valid = adjustment(r, s, b)
        roll = min((c for c in valid["candidates"] if c["action"] == "roll"),
                   key=lambda c: c["stressLossRupees"])
        stress = roll["stressLossRupees"]
        pnl_before, pnl_after = valid["strategyPnlRupees"], roll["strategyPnlAfterRupees"]
        self.assertLess(pnl_after, pnl_before)
        budget = stress-(pnl_before+pnl_after)/2
        b.update(xPoints=budget/b["quantity"], originalRiskBudget=budget)
        self.assertLess(stress, budget+pnl_before)
        self.assertGreater(stress, budget+pnl_after)
        result = adjustment(r, s, b)
        self.assertFalse(any(c["action"] == "roll" for c in result["candidates"]))

    def test_directional_rolls_require_nifty_chart_not_macro_fallback(self):
        for missing in ("leading", "gap"):
            r, s, b = linked()
            if missing == "leading":
                s["candles"] = s["candles"][-16:]
            else:
                s["candles"].pop(10)
            result = adjustment(r, s, b)
            self.assertFalse(any(c["action"] == "roll" for c in result["candidates"]))

    def test_smaller_original_maximum_loss_does_not_invent_early_exit(self):
        r, s, b = linked()
        b["originalMaxLoss"] = 1
        result = adjustment(r, s, b)
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertFalse(result["mandatoryExit"])
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])

    def test_hold_and_reduce_excluded_when_stress_exceeds_remaining_original_budget(self):
        r, s, b = linked(150)
        b.update(xPoints=10, originalRiskBudget=1500)
        result = adjustment(r, s, b)
        self.assertEqual(result["blockers"], [])
        self.assertGreater(result["suggestedXPoints"]*75, result["remainingRiskBudget"])
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])

    def test_actual_lot_reduction_preserves_original_point_basis_and_loss_budget(self):
        r, s, b = linked(150)
        for original_trade, position in zip(list(s["trades"]), s["positions"]["net"]):
            close = {**original_trade, "quantity": 75, "trade_id": "reduced-"+original_trade["trade_id"],
                     "transaction_type": "SELL" if original_trade["transaction_type"] == "BUY" else "BUY"}
            q = s["quotes"][original_trade["exchange"]+":"+original_trade["tradingsymbol"]]
            close["average_price"] = (q["depth"]["buy"][0]["price"] if close["transaction_type"] == "SELL"
                                       else q["depth"]["sell"][0]["price"])
            s["trades"].append(close)
            position["quantity"] //= 2
        b["cashFlows"] = [{
            "exchange": t["exchange"], "tradingSymbol": t["tradingsymbol"], "product": t["product"],
            "signedQuantity": t["quantity"]*(1 if t["transaction_type"] == "BUY" else -1),
            "price": t["average_price"], "tradeId": t["trade_id"],
        } for t in s["trades"]]
        result = adjustment(r, s, b)
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["quantity"], 75)
        self.assertEqual(result["originalQuantity"], 150)
        self.assertEqual(result["currentStressLossRupees"], result["suggestedXPoints"]*75)
        self.assertEqual(result["originalRiskBudget"], 15000)
        self.assertAlmostEqual(result["strategyPnlRupees"], -60)
        self.assertAlmostEqual(result["strategyPnlPoints"], -.4)
        self.assertEqual(result["xPoints"], 100)
        original = {key: value for key, value in b.items() if key != "cashFlows"}
        selected = reconcile_trade_ids(original, [t["trade_id"] for t in s["trades"]],
                                       s["trades"], s["instruments"], NOW)
        selected_result = adjustment(r, s, selected)
        self.assertEqual(selected_result["quantity"], 75)
        self.assertEqual(selected_result["originalQuantity"], 150)
        self.assertEqual(selected_result["originalRiskBudget"], 15000)
        self.assertAlmostEqual(selected_result["strategyPnlRupees"], -60)

    def test_missing_stress_history_keeps_exit_available_without_inventing_x(self):
        r, s, b = linked()
        s["candles"] = []
        result = adjustment(r, s, b)
        self.assertIsNone(result["suggestedXPoints"])
        self.assertFalse(any(c["action"] == "roll" for c in result["candidates"]))
        self.assertEqual([c["action"] for c in result["candidates"]], ["review"])

    def test_closed_strategy_cash_ledger_does_not_invent_new_exposure(self):
        r, s, b = linked()
        closing = [{**t, "trade_id": "closed-"+t["trade_id"],
                    "transaction_type": "SELL" if t["transaction_type"] == "BUY" else "BUY"}
                   for t in s["trades"]]
        s["trades"].extend(closing)
        for p in s["positions"]["net"]:
            p["quantity"] = 0
        b["cashFlows"] = [{
            "exchange": t["exchange"], "tradingSymbol": t["tradingsymbol"], "product": t["product"],
            "signedQuantity": t["quantity"]*(1 if t["transaction_type"] == "BUY" else -1),
            "price": t["average_price"], "tradeId": t["trade_id"],
        } for t in s["trades"]]
        result = adjustment(r, s, b)
        self.assertEqual(result["blockers"], [])
        self.assertAlmostEqual(result["strategyPnlRupees"], 0)
        self.assertEqual(result["executionStatus"], "reference-only")
        self.assertEqual(result["candidates"], [])

    def test_cash_flow_ledger_includes_realized_roll_and_closed_legs(self):
        r, s, b = linked()
        original = b["legs"][0]
        original_key = (original["exchange"], original["tradingSymbol"], "MIS")
        qty = b["quantity"]
        new_inst = contract(s["instruments"], "2026-12-01", 24100, "PE")
        new_symbol = new_inst["tradingSymbol"]
        old_trade = s["trades"][0]
        close_price = old_trade["average_price"]+10
        new_price = close_price-20
        s["trades"] += [
            {**old_trade, "trade_id": "roll-close", "transaction_type": "BUY", "average_price": close_price},
            {**old_trade, "trade_id": "roll-open", "tradingsymbol": new_symbol, "average_price": new_price},
        ]
        s["positions"]["net"][0]["quantity"] = 0
        s["positions"]["net"].append({**s["positions"]["net"][0], "tradingsymbol": new_symbol, "quantity": -qty})
        b["cashFlows"] = [{
            "exchange": t["exchange"], "tradingSymbol": t["tradingsymbol"], "product": t["product"],
            "signedQuantity": t["quantity"]*(1 if t["transaction_type"] == "BUY" else -1),
            "price": t["average_price"], "tradeId": t["trade_id"],
        } for t in s["trades"]]
        exposures, cash, _ = attributed_ledger(b, s["positions"], s["trades"], NOW)
        expected = sum(-f["signedQuantity"]*f["price"] for f in b["cashFlows"])
        self.assertAlmostEqual(cash, expected)
        self.assertNotIn(original_key, exposures)
        self.assertEqual(exposures[(new_inst["exchange"], new_symbol, "MIS")], -qty)
        result = adjustment(r, s, b)
        self.assertEqual(result["blockers"], [])
        liquidation = sum((1 if l["direction"] == "buy" else -1)*l["liquidationPrice"]*qty
                          for l in result["legs"])
        self.assertAlmostEqual(result["strategyPnlRupees"], expected+liquidation)
        self.assertEqual(result["xPoints"], b["xPoints"])
        self.assertEqual(result["originalRiskBudget"], b["originalRiskBudget"])
        # A persisted adjustment selects broker IDs, never browser fill prices.
        original = {key: value for key, value in b.items() if key != "cashFlows"}
        selected = reconcile_trade_ids(original, [t["trade_id"] for t in s["trades"]],
                                       s["trades"], s["instruments"], NOW)
        selected_result = adjustment(r, s, selected)
        self.assertEqual(selected_result["strategyPnlRupees"], result["strategyPnlRupees"])
        self.assertEqual(selected_result["originalRiskBudget"], original["originalRiskBudget"])
        self.assertEqual(selected_result["reconciliation"]["cashFlows"], b["cashFlows"])
        self.assertNotIn("cashFlows", original)
        b["cashFlows"][0]["tradeId"] = "missing"
        self.assertTrue(adjustment(r, s, b)["blockers"])


if __name__ == "__main__":
    unittest.main()
