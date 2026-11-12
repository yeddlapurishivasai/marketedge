"""Focused offline contract, calendar, pricing, risk and reconciliation tests."""

import copy
from datetime import datetime, timedelta, timezone
import json
import math
import unittest

import options_engine as engine


UTC = timezone.utc
NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)  # 11:30 IST
RATE, DIVIDEND, COST = 0.06, 0.01, 2.0
SPOT = 25000.0


def session(day, trading=True, weekly=False, monthly=False, close="15:30"):
    return {"sessionDate": day, "isTradingDay": trading, "openTime": "09:15",
            "closeTime": close, "isWeeklyExpiry": weekly, "isMonthlyExpiry": monthly}


def fixture():
    sessions = []
    for offset in range(24):
        day = (NOW + timedelta(days=offset)).date()
        sessions.append(session(day.isoformat(), day.weekday() < 5,
                                weekly=offset in (1, 8, 15, 22), monthly=offset == 22))
    instruments = []
    for expiry in ("2026-11-09", "2026-11-10", "2026-11-17", "2026-12-01"):
        for kind in ("CE", "PE"):
            for strike in range(24000, 26001, 250):
                instruments.append({"expiry": expiry, "strike": float(strike),
                                    "instrumentType": kind, "exchange": "NFO",
                                    "tradingSymbol": ("NIFTY26DEC" if expiry == "2026-12-01"
                                                      else "NIFTY26N" + expiry[-2:]) + str(strike) + kind,
                                    "instrumentToken": len(instruments) + 1, "lotSize": 75, "tickSize": 0.05})
    bars = []
    for offset in range(240):
        close = 24800 + 45 * math.sin(offset * 0.55) + offset * 0.35
        bars.append({"date": (NOW - timedelta(days=240 - offset)).date().isoformat(),
                     "open": close - 2, "high": close + 180, "low": close - 180,
                     "close": close, "volume": 0})
    horizon = engine.resolve_horizon(NOW, "daily", sessions, instruments)
    quotes = []
    for instrument in instruments:
        expiry_at = horizon["expiryCloseAt"].get(instrument["expiry"])
        if not expiry_at:
            continue
        years = (engine._aware(expiry_at) - NOW).total_seconds() / engine.YEAR_SECONDS
        theoretical = engine.european_price(SPOT, instrument["strike"], years, RATE, DIVIDEND,
                                            0.35, instrument["instrumentType"])
        quote = dict(instrument, lastPrice=theoretical, bid=theoretical * 0.99,
                     ask=theoretical * 1.01, observedAt=engine._iso(NOW), volume=500,
                     openInterest=1000)
        quotes.append(quote)
    return sessions, instruments, bars, quotes, horizon


def analysis(bars=None, quotes=None, horizon=None, **kwargs):
    _, _, b, q, h = fixture()
    return engine.build_analysis(SPOT, b if bars is None else bars,
                                 q if quotes is None else quotes,
                                 h if horizon is None else horizon, NOW,
                                 kwargs.get("rate", RATE), kwargs.get("dividend", DIVIDEND),
                                 kwargs.get("cost", COST), kwargs.get("extra_dimensions"))


def reprice_quotes(quotes, horizon, now, volatility):
    result = []
    for q in quotes:
        expiry_at = horizon["expiryCloseAt"].get(q["expiry"])
        if expiry_at:
            years = (engine._aware(expiry_at) - now).total_seconds() / engine.YEAR_SECONDS
            price = engine.european_price(SPOT, q["strike"], years, RATE, DIVIDEND,
                                          volatility, q["instrumentType"])
            result.append(dict(q, bid=price * .99, ask=price * 1.01, lastPrice=price,
                               observedAt=engine._iso(now)))
    return result


class PlanningTests(unittest.TestCase):
    def test_completed_session_candle_contributes_only_with_verified_cutoff(self):
        sessions, instruments, bars, quotes, _ = fixture()
        after = NOW.replace(hour=14)
        today = {"date": NOW.date().isoformat(), "open": 29900.0, "high": 30100.0,
                 "low": 29800.0, "close": 30000.0, "volume": 0}
        bars.append(today)
        tomorrow = dict(today, date=(NOW + timedelta(days=1)).date().isoformat(), close=29950.0)
        bars.append(tomorrow)
        horizon = engine.resolve_horizon(after, "weekly", sessions, instruments)
        result = engine.build_analysis(SPOT, bars, quotes, horizon, after, None, None, None,
                                       completed_through=after.date())
        technicals = next(d for d in result["dimensions"] if d["key"] == "technicals")
        sma20 = next(i["value"] for i in technicals["indicators"] if i["key"] == "sma20")
        self.assertAlmostEqual(sma20, sum(b["close"] for b in bars[-21:-1]) / 20)
        # Without verified completion, today's candle remains excluded even at night.
        prior = engine._technicals(SPOT, bars, after)[0]
        self.assertAlmostEqual(next(i["value"] for i in prior["indicators"] if i["key"] == "sma20"),
                               sum(b["close"] for b in bars[-22:-2]) / 20)
        with self.assertRaisesRegex(ValueError, "future"):
            engine._technicals(SPOT, bars, after, after.date() + timedelta(days=1))

    def test_individual_signal_contributions_reconcile_without_double_counting_averages(self):
        result = analysis(extra_dimensions=[
            {"key": "macro", "score": 60, "coverage": 1,
             "indicators": [{"key": "crude", "value": -10, "direction": "supportive", "contribution": 10}]},
        ])
        indicators = [i for d in result["dimensions"] for i in d["indicators"]]
        total = sum(i["signalContribution"] for i in indicators if i["signalContribution"] is not None)
        self.assertAlmostEqual(total, sum(d["contribution"] for d in result["directionContributions"]))
        for key in ("crude", "adx14", "ema20", "realizedVol20", "atr14", "validQuotes"):
            indicator = next(i for i in indicators if i["key"] == key)
            self.assertIsNone(indicator["signalContribution"])
            self.assertEqual(indicator["influenceRole"], "context")
        averages = [i for i in indicators if i["key"] in ("sma20", "sma50", "sma200")]
        self.assertAlmostEqual(sum(i["scoreContribution"] for i in averages),
                               50 * sum(i["contribution"] for i in averages) / len(averages) / 3)

    def planned(self, mode="weekly", **quote_changes):
        sessions, instruments, bars, quotes, _ = fixture()
        now = NOW.replace(hour=19)
        close = NOW.replace(hour=10)
        policy = engine.snapshot_policy(now, sessions, close, mode)
        horizon = engine.resolve_horizon(now, mode, sessions, instruments)
        quotes = [dict(q, observedAt=engine._iso(close), lastTradeAt=engine._iso(close), **quote_changes)
                  for q in quotes]
        result = engine.build_analysis(SPOT, bars, quotes, horizon, now, None, None, None,
                                       quote_policy=policy)
        return result, policy, horizon, quotes

    def test_daily_and_weekly_have_ranges_and_non_executable_plans_after_close(self):
        for mode in ("daily", "weekly"):
            result, policy, _, _ = self.planned(mode)
            self.assertEqual(policy["executionStatus"], "planning-only")
            self.assertTrue(result["candidates"])
            self.assertEqual(len(result["scenarios"]), 3)
            for scenario in result["scenarios"]:
                self.assertTrue(0 < scenario["low"] < scenario["high"])
                self.assertIsNone(scenario["probability"])
            for candidate in result["candidates"]:
                self.assertEqual(candidate["executionStatus"], "planning-only")
                self.assertTrue(candidate["requiresLiveRevalidation"])
                self.assertTrue(all(leg["quoteObservedAt"].endswith("10:00:00Z") for leg in candidate["legs"]))
                self.assertTrue(all(leg["impliedVolatility"] is None for leg in candidate["legs"]))
                if candidate["side"] == "selling":
                    self.assertGreaterEqual(len(candidate["legs"]), 2)
                    stop = candidate["technicalStop"]
                    self.assertTrue(stop["lower"] is not None or stop["upper"] is not None)
                    if stop["lower"] is not None:
                        self.assertLess(stop["lower"], SPOT)
                    if stop["upper"] is not None:
                        self.assertGreater(stop["upper"], SPOT)

    def test_missing_books_use_explicit_last_trade_references_not_liquidity(self):
        result, _, _, _ = self.planned(bid=0, ask=0)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(leg["priceBasis"] == "last-traded-reference"
                            for c in result["candidates"] for leg in c["legs"]))
        liquidity = next(d for d in result["dimensions"] if d["key"] == "liquidity")
        self.assertGreater(liquidity["coverage"], 0)
        self.assertEqual(liquidity["score"], 0)
        self.assertIn("not live", liquidity["scale"])

    def test_old_last_trade_cannot_be_made_fresh_by_quote_timestamp(self):
        result, policy, horizon, quotes = self.planned(bid=0, ask=0)
        self.assertTrue(result["candidates"])
        changed = [dict(q, lastTradeAt=engine._iso(NOW)) for q in quotes]
        models, rejected = engine._quote_models(SPOT, changed, horizon, NOW.replace(hour=19), None, None, policy)
        self.assertEqual(models, [])
        self.assertTrue(any("last trade" in reason for reason in rejected))

    def test_last_trade_references_are_not_misdiagnosed_as_executable_book_arbitrage(self):
        _, policy, horizon, quotes = self.planned(bid=0, ask=0)
        changed = [dict(q, lastPrice=1000 if q["strike"] == SPOT else 100) for q in quotes]
        models, rejected = engine._quote_models(SPOT, changed, horizon, NOW.replace(hour=19), None, None, policy)
        self.assertTrue(models)
        self.assertFalse(any("executable arbitrage" in reason for reason in rejected))

    def test_last_session_reference_is_not_a_hypothetical_entry(self):
        result, _, _, _ = self.planned()
        for candidate in result["candidates"]:
            outcome = engine.reconcile_strategy(candidate, [], engine._aware(candidate["plannedExitAt"]))
            self.assertEqual(outcome["status"], "unavailable")
            self.assertNotIn("pnl", outcome)

    def test_live_staleness_and_intraday_cutoffs_are_not_relaxed(self):
        sessions, _, _, _, _ = fixture()
        stale = engine.snapshot_policy(NOW, sessions, NOW - timedelta(seconds=31), "daily")
        self.assertEqual(stale["executionStatus"], "unavailable")
        self.assertFalse(engine.quote_in_policy(NOW, NOW, stale))
        after_close = engine.snapshot_policy(NOW.replace(hour=19), sessions, NOW.replace(hour=10), "expiry-intraday")
        self.assertEqual(after_close["executionStatus"], "unavailable")

    def test_planning_requires_latest_completed_session_and_contiguous_calendar(self):
        sessions, _, _, _, _ = fixture()
        now = NOW + timedelta(days=2, hours=13)
        old = engine.snapshot_policy(now, sessions, NOW.replace(hour=10), "weekly")
        self.assertEqual(old["executionStatus"], "unavailable")
        sessions = [s for s in sessions if s["sessionDate"] != "2026-11-10"]
        missing_day = engine.snapshot_policy(NOW + timedelta(days=1, hours=-3), sessions,
                                             NOW.replace(hour=10), "weekly")
        self.assertEqual(missing_day["executionStatus"], "unavailable")

    def test_plan_cannot_accept_future_or_nonclosing_option_observations(self):
        _, policy, horizon, quotes = self.planned()
        for timestamp in (NOW, NOW.replace(hour=23)):
            changed = [dict(q, observedAt=engine._iso(timestamp)) for q in quotes]
            models, _ = engine._quote_models(SPOT, changed, horizon, NOW.replace(hour=19), None, None, policy)
            self.assertEqual(models, [])

    def test_dated_post_close_index_updates_are_references_not_official_close_prints(self):
        sessions, _, _, _, _ = fixture()
        observed, now = NOW.replace(hour=12, minute=5, second=6), NOW.replace(hour=19)
        policy = engine.snapshot_policy(now, sessions, observed, "weekly")
        self.assertEqual(policy["executionStatus"], "planning-only")
        self.assertTrue(engine.quote_in_policy(observed, now, policy))
        self.assertIn("not a closing print", policy["reason"])
        self.assertLess(engine._aware(policy["tradeWindowEnd"]), observed)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.sessions, self.instruments, _, _, _ = fixture()

    def resolve(self, now=NOW, mode="daily", sessions=None, instruments=None):
        return engine.resolve_horizon(now, mode, self.sessions if sessions is None else sessions,
                                      self.instruments if instruments is None else instruments)

    def test_daily_cutoff_is_exact_and_same_day_buying_never_allowed(self):
        before = self.resolve(datetime(2026, 11, 9, 8, 29, 59, tzinfo=UTC))
        after = self.resolve(datetime(2026, 11, 9, 8, 30, tzinfo=UTC))
        self.assertEqual(before["targetAt"], "2026-11-09T10:00:00Z")
        self.assertEqual(after["targetAt"], "2026-11-10T10:00:00Z")
        self.assertIn("2026-11-09", before["eligibleExpiries"])
        self.assertNotIn("2026-11-09", before["buyingExpiries"])
        self.assertNotIn("2026-11-09", after["eligibleExpiries"])
        self.assertEqual(before["buyingExpiries"], ["2026-11-10", "2026-11-17"])

    def test_holiday_and_exceptional_session_are_imported_not_guessed(self):
        sessions = copy.deepcopy(self.sessions)
        sessions[1]["isTradingDay"] = False
        result = self.resolve(datetime(2026, 11, 9, 9, tzinfo=UTC), sessions=sessions)
        self.assertEqual(result["targetAt"], "2026-11-11T10:00:00Z")
        sessions[1]["isTradingDay"] = True
        sessions[0]["closeTime"] = "13:00"
        result = self.resolve(mode="expiry-intraday", sessions=sessions)
        self.assertEqual(result["entryCutoffAt"], "2026-11-09T07:00:00Z")
        self.assertEqual(result["plannedExitAt"], "2026-11-09T07:15:00Z")

    def test_intraday_after_14_until_close_relative_cutoff(self):
        result = self.resolve(datetime(2026, 11, 9, 9, 29, 59, tzinfo=UTC), "expiry-intraday")
        self.assertTrue(result["canSuggest"])
        self.assertEqual(result["entryCutoffAt"], "2026-11-09T09:30:00Z")
        self.assertEqual(result["plannedExitAt"], "2026-11-09T09:45:00Z")
        self.assertFalse(self.resolve(datetime(2026, 11, 9, 9, 30, tzinfo=UTC), "expiry-intraday")["canSuggest"])
        self.assertFalse(self.resolve(datetime(2026, 11, 9, 9, 46, tzinfo=UTC), "expiry-intraday")["canSuggest"])

    def test_weekly_monthly_use_broker_contracts_not_calendar_labels(self):
        self.assertEqual(self.resolve(mode="weekly")["targetAt"], "2026-11-10T10:00:00Z")
        self.assertEqual(self.resolve(mode="monthly")["targetAt"], "2026-12-01T10:00:00Z")
        without_first = [i for i in self.instruments if i["expiry"] != "2026-11-10"]
        self.assertEqual(self.resolve(mode="weekly", instruments=without_first)["targetAt"],
                         "2026-11-17T10:00:00Z")
        sessions = copy.deepcopy(self.sessions)
        for s in sessions:
            s["isWeeklyExpiry"] = False
            s["isMonthlyExpiry"] = False
        self.assertEqual(self.resolve(mode="weekly", sessions=sessions)["targetAt"],
                         "2026-11-10T10:00:00Z")
        self.assertEqual(self.resolve(mode="monthly", sessions=sessions)["targetAt"],
                         "2026-12-01T10:00:00Z")

    def test_weekly_does_not_skip_a_broker_expiry_with_missing_session_data(self):
        sessions = [s for s in self.sessions if s["sessionDate"] != "2026-11-10"]
        with self.assertRaisesRegex(ValueError, "broker-listed expiry 2026-11-10"):
            self.resolve(mode="weekly", sessions=sessions)
        sessions = copy.deepcopy(self.sessions)
        sessions[1]["isTradingDay"] = False
        with self.assertRaisesRegex(ValueError, "not a trading session"):
            self.resolve(mode="weekly", sessions=sessions)

    def test_monthly_is_not_simply_the_last_listed_date_of_the_month(self):
        instruments = [
            {"expiry": "2026-11-17", "tradingSymbol": "SENSEX26NOV80000CE",
             "exchange": "BFO", "instrumentType": "CE", "strike": 80000.0},
            {"expiry": "2026-11-24", "tradingSymbol": "SENSEX26N2480000CE",
             "exchange": "BFO", "instrumentType": "CE", "strike": 80000.0},
        ]
        self.assertEqual(self.resolve(mode="monthly", instruments=instruments)["targetAt"],
                         "2026-11-17T10:00:00Z")

    def test_monthly_symbol_must_match_the_actual_contract_metadata(self):
        base = {"expiry": "2026-11-17", "tradingSymbol": "NIFTY26NOV25000CE",
                "exchange": "NFO", "instrumentType": "CE", "strike": 25000.0}
        self.assertEqual(self.resolve(mode="monthly", instruments=[base])["targetAt"],
                         "2026-11-17T10:00:00Z")
        for invalid in (
            {"tradingSymbol": "NIFTY26DEC25000CE"}, {"tradingSymbol": "NIFTY25NOV25000CE"},
            {"tradingSymbol": "NIFTY26NOV25000PE"}, {"exchange": "BFO"},
            {"strike": 24000.0}, {"tradingSymbol": "UNRECOGNISED"},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "broker-listed.*monthly"):
                self.resolve(mode="monthly", instruments=[dict(base, **invalid)])

    def test_monthly_excludes_today_even_when_a_monthly_contract_is_listed(self):
        instruments = [
            {"expiry": "2026-11-09", "tradingSymbol": "NIFTY26NOV25000CE",
             "exchange": "NFO", "instrumentType": "CE", "strike": 25000.0},
            {"expiry": "2026-12-01", "tradingSymbol": "NIFTY26DEC25000CE",
             "exchange": "NFO", "instrumentType": "CE", "strike": 25000.0},
        ]
        self.assertEqual(self.resolve(mode="monthly", instruments=instruments)["targetAt"],
                         "2026-12-01T10:00:00Z")

    def test_missing_coverage_or_product_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.resolve(sessions=self.sessions[1:])
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.resolve(mode="monthly", sessions=self.sessions[:2])
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.resolve(instruments=[])
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.resolve(mode="monthly", sessions=self.sessions[:2] + self.sessions[3:])

    def test_no_new_entries_outside_actual_open_session(self):
        self.assertFalse(self.resolve(datetime(2026, 11, 9, 3, 0, tzinfo=UTC))["canSuggest"])
        saturday = datetime(2026, 11, 14, 6, tzinfo=UTC)
        self.assertFalse(self.resolve(saturday)["canSuggest"])
        with self.assertRaisesRegex(ValueError, "aware"):
            self.resolve(NOW.replace(tzinfo=None))

    def test_missing_first_future_expiry_close_is_not_silently_substituted(self):
        sessions = [s for s in self.sessions if s["sessionDate"] != "2026-11-10"]
        with self.assertRaisesRegex(ValueError, "future calendar coverage"):
            self.resolve(sessions=sessions)

    def test_weekly_does_not_allow_same_day_selling_even_before_14(self):
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["isWeeklyExpiry"] = True
        result = self.resolve(mode="weekly", sessions=sessions)
        self.assertEqual(result["targetAt"], "2026-11-10T10:00:00Z")
        self.assertNotIn("2026-11-09", result["eligibleExpiries"])


class PricingTests(unittest.TestCase):
    def test_put_call_parity_and_iv_roundtrip(self):
        for years in (1 / 365, 0.1, 1):
            for strike in (23000, 25000, 27000):
                call = engine.european_price(SPOT, strike, years, RATE, DIVIDEND, .3, "CE")
                put = engine.european_price(SPOT, strike, years, RATE, DIVIDEND, .3, "PE")
                self.assertAlmostEqual(call - put, SPOT * math.exp(-DIVIDEND * years) -
                                       strike * math.exp(-RATE * years), places=8)
                self.assertAlmostEqual(engine.implied_volatility(call, SPOT, strike, years,
                                                                 RATE, DIVIDEND, "CE"), .3, places=7)

    def test_greeks_against_finite_differences(self):
        args = (SPOT, 25000, .2, RATE, DIVIDEND, .2, "CE")
        g = engine.european_greeks(*args)
        p = engine.european_price(*args)
        up = engine.european_price(SPOT + 1, *args[1:])
        down = engine.european_price(SPOT - 1, *args[1:])
        self.assertAlmostEqual(g["delta"], (up - down) / 2, places=6)
        self.assertAlmostEqual(g["gamma"], up - 2 * p + down, places=8)
        self.assertGreater(g["vega"], 0)

    def test_expiry_and_arbitrage_bounds(self):
        self.assertEqual(engine.european_price(100, 90, 0, .05, 0, .2, "CE"), 10)
        self.assertIsNone(engine.implied_volatility(110, 100, 100, 1, .05, 0, "CE"))
        with self.assertRaises(ValueError):
            engine.european_price(-1, 100, 1, .05, 0, .2, "CE")


class AnalysisTests(unittest.TestCase):
    def test_analysis_is_deterministic_json_and_not_mutating_inputs(self):
        _, _, bars, quotes, horizon = fixture()
        original = copy.deepcopy((bars, quotes, horizon))
        result = analysis(bars, quotes, horizon)
        self.assertEqual(result, analysis(bars, quotes, horizon))
        self.assertEqual((bars, quotes, horizon), original)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["schemaVersion"], "options.v1")
        for dimension in result["dimensions"]:
            for indicator in dimension["indicators"]:
                self.assertNotIn("score", indicator)
        self.assertEqual({s["id"] for s in result["scenarios"]}, {"narrow", "base", "wide"})
        self.assertTrue(all(s["probabilityType"] == "model-implied" for s in result["scenarios"]))

    def test_optional_inputs_preserve_quote_alternatives_without_invented_net_or_model_values(self):
        for kwargs in ({"rate": None}, {"dividend": None}, {"cost": None}, {"cost": -1},
                       {"rate": float("nan")}, {"rate": None, "dividend": None, "cost": None}):
            result = analysis(**kwargs)
            self.assertTrue(result["candidates"], result["warnings"])
            model_missing = "rate" in kwargs or "dividend" in kwargs
            fees_missing = "cost" in kwargs
            self.assertTrue(any("unavailable" in w for w in result["warnings"]))
            for candidate in result["candidates"]:
                gross_loss, gross_profit = engine._payoff_bounds(candidate["legs"], 0)
                self.assertEqual((candidate["grossMaxLoss"], candidate["grossMaxProfit"]), (gross_loss, gross_profit))
                if model_missing or fees_missing:
                    self.assertIsNone(candidate["estimatedStopLoss"])
                    self.assertIsNone(candidate["probabilityOfProfit"])
                    self.assertEqual(candidate["scenarioRepricing"], [])
                    self.assertTrue(all(v["probability"] is None for v in candidate["scenarioProfitProbabilities"].values()))
                if model_missing:
                    for leg in candidate["legs"]:
                        self.assertIsNone(leg["impliedVolatility"])
                        self.assertIsNone(leg["greeks"])
                    self.assertTrue(all(s["probability"] is None for s in result["scenarios"]))
                if fees_missing:
                    for field in ("maxLoss", "maxProfit", "costEstimate", "capitalBasis", "rewardRisk", "targetProfit", "riskExposureRatio"):
                        self.assertIsNone(candidate[field], field)
                    self.assertEqual(candidate["payoffBasis"], "gross-before-charges")
                    self.assertEqual(candidate["riskTier"], "high")
                else:
                    self.assertEqual(candidate["costEstimate"], COST * len(candidate["legs"]))
                    self.assertEqual((candidate["maxLoss"], candidate["maxProfit"]),
                                     engine._payoff_bounds(candidate["legs"], candidate["costEstimate"]))
            json.dumps(result, allow_nan=False)

    def test_fees_do_not_gate_scenario_model_but_rate_and_yield_do(self):
        baseline = analysis()["scenarios"]
        self.assertEqual(analysis(cost=None)["scenarios"], baseline)
        self.assertEqual(analysis(cost=-1)["scenarios"], baseline)
        self.assertTrue(all(s["intervalType"] == "descriptive volatility band"
                            for s in analysis(rate=None)["scenarios"]))

    def test_quote_only_mode_keeps_metadata_quote_and_risk_gates(self):
        _, _, _, quotes, _ = fixture()
        original = copy.deepcopy(quotes)
        result = analysis(quotes=quotes, rate=None, dividend=None, cost=None)
        self.assertEqual(quotes, original)
        self.assertTrue(all(c["riskTier"] != "low" for c in result["candidates"]
                            if c["expiry"] == NOW.date().isoformat()))
        otm = [c for c in analysis(rate=None)["candidates"] if c["moneyness"] == "OTM"]
        self.assertTrue(otm)
        self.assertTrue(all(c["riskTier"] == "high" for c in otm))
        for change in ({"bid": 0}, {"lotSize": None}, {"tickSize": None},
                       {"observedAt": engine._iso(NOW - timedelta(minutes=5))}):
            self.assertEqual(analysis(quotes=[dict(q, **change) for q in quotes],
                                      rate=None, cost=None)["candidates"], [])

    def test_daily_and_weekly_quote_only_succeed_for_nifty_and_sensex(self):
        sessions, instruments, bars, quotes, _ = fixture()
        for mode in ("daily", "weekly"):
            for exchange, underlying in (("NFO", "NIFTY"), ("BFO", "SENSEX")):
                contracts = [dict(i, exchange=exchange, tradingSymbol=i["tradingSymbol"].replace("NIFTY", underlying))
                             for i in instruments]
                chain = [dict(q, exchange=exchange, tradingSymbol=q["tradingSymbol"].replace("NIFTY", underlying))
                         for q in quotes]
                horizon = engine.resolve_horizon(NOW, mode, sessions, contracts)
                result = engine.build_analysis(SPOT, bars, chain, horizon, NOW, None, None, None)
                self.assertTrue(result["candidates"], result["warnings"])
                self.assertTrue(all(c["costEstimate"] is None for c in result["candidates"]))

    def test_extra_directional_dimensions_are_coverage_weighted_before_bias(self):
        extra = [{"key": "fundamentals", "score": 0, "coverage": 1, "indicators": []},
                 {"key": "breadth", "score": 0, "coverage": .5, "indicators": []},
                 {"key": "macro", "score": 100, "coverage": 1, "indicators": []}]
        original = copy.deepcopy(extra)
        result = analysis(extra_dimensions=extra)
        self.assertEqual(extra, original)
        self.assertEqual(result["bias"], "bearish")
        contributions = {c["key"]: c for c in result["directionContributions"]}
        self.assertEqual(set(contributions), {"technicals", "fundamentals", "breadth"})
        self.assertAlmostEqual(sum(c["effectiveWeight"] for c in contributions.values()), 1)
        self.assertAlmostEqual(contributions["breadth"]["coverageAdjustedWeight"], .075)
        self.assertAlmostEqual(contributions["fundamentals"]["coverageAdjustedWeight"], .35)
        self.assertEqual([d for d in result["dimensions"] if d["key"] == "macro"], [extra[2]])
        for candidate in result["candidates"]:
            if candidate["strategy"] == "long-call":
                self.assertTrue(any("Counter-bias" in w for w in candidate["warnings"]))
            if candidate["strategy"] == "long-put":
                self.assertFalse(any("Counter-bias" in w for w in candidate["warnings"]))

    def test_unavailable_directional_input_does_not_inject_a_neutral_score(self):
        result = analysis(extra_dimensions=[{"key": "fundamentals", "score": None, "coverage": 0},
                                             {"key": "breadth", "score": 100, "coverage": 0}])
        self.assertEqual(result["bias"], analysis()["bias"])
        self.assertEqual(result["directionContributions"][0]["effectiveWeight"], 1)
        for c in result["directionContributions"][1:]:
            self.assertEqual(c["effectiveWeight"], 0)
        with self.assertRaisesRegex(ValueError, "distinct"):
            analysis(extra_dimensions=[{"key": "volatility", "score": 100, "coverage": 1}])
        with self.assertRaisesRegex(ValueError, "0-100"):
            analysis(extra_dimensions=[{"key": "breadth", "score": 101, "coverage": 1}])

    def test_last_price_is_not_required_for_executable_bid_ask_model(self):
        _, _, _, quotes, _ = fixture()
        quotes = [{k: v for k, v in q.items() if k != "lastPrice"} for q in quotes]
        self.assertTrue(analysis(quotes=quotes)["candidates"])

    def test_missing_history_no_fabricated_trend_probability_or_trades(self):
        result = analysis(bars=[])
        self.assertEqual(result["bias"], "unavailable")
        self.assertIsNone(result["dimensions"][0]["score"])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(all(s["probability"] is None for s in result["scenarios"]))

    def test_current_day_partial_bar_excluded_and_insufficient_long_ma_is_null(self):
        _, _, bars, _, _ = fixture()
        partial = {"date": NOW.date().isoformat(), "open": 90000, "high": 99999,
                   "low": 80000, "close": 95000, "volume": 1}
        self.assertEqual(analysis(bars=bars), analysis(bars=bars + [partial]))
        indicators = analysis(bars=bars[-30:])["dimensions"][0]["indicators"]
        self.assertIsNone(next(i["value"] for i in indicators if i["key"] == "sma50"))
        self.assertIsNotNone(next(i["value"] for i in indicators if i["key"] == "sma20"))

    def test_zero_realised_variance_does_not_fake_central_interval_probability(self):
        _, _, bars, _, _ = fixture()
        flat = [dict(b, open=25000, high=25001, low=24999, close=25000) for b in bars]
        result = analysis(bars=flat)
        self.assertTrue(all(s["probability"] is None for s in result["scenarios"]))
        self.assertEqual(result["candidates"], [])

    def test_bad_quotes_are_rejected(self):
        _, _, _, quotes, _ = fixture()
        for modification, reason in (
                ({"bid": 0}, "zero"),
                ({"bid": 1000, "ask": 100}, "crossed"),
                ({"bid": 1, "ask": 10}, "wide"),
                ({"observedAt": engine._iso(NOW - timedelta(seconds=61))}, "stale"),
                ({"observedAt": engine._iso(NOW + timedelta(seconds=3))}, "future"),
                ({"bid": 100000, "ask": 100001}, "arbitrage")):
            changed = [dict(q, **modification) for q in quotes]
            result = analysis(quotes=changed)
            self.assertEqual(result["candidates"], [])
            self.assertTrue(any(reason in warning for warning in result["warnings"]), reason)

    def test_cross_contract_arbitrage_and_duplicate_snapshots_are_rejected(self):
        _, _, _, quotes, _ = fixture()
        # Each quote remains within its own bounds, but CE and PE violate parity.
        changed = [dict(q, bid=q["bid"] * 1.5, ask=q["ask"] * 1.5)
                   if q["instrumentType"] == "CE" else q for q in quotes]
        result = analysis(quotes=changed)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("arbitrage violation" in w for w in result["warnings"]))
        result = analysis(quotes=quotes + [quotes[0]])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("Duplicate" in w for w in result["warnings"]))

    def test_live_entry_cutoff_is_rechecked_not_trusted_from_old_horizon(self):
        _, _, bars, quotes, horizon = fixture()
        after = datetime(2026, 11, 9, 8, 30, tzinfo=UTC)
        fresh = [dict(q, observedAt=engine._iso(after)) for q in quotes]
        result = engine.build_analysis(SPOT, bars, fresh, horizon, after, RATE, DIVIDEND, COST)
        self.assertEqual(result["candidates"], [])

    def test_generated_structures_are_bounded_hedged_and_have_costed_exact_payoffs(self):
        result = analysis()
        self.assertTrue(result["candidates"], result["warnings"])
        strategies = {c["strategy"] for c in result["candidates"]}
        self.assertEqual(strategies, {"long-call", "long-put", "call-debit-spread", "put-debit-spread",
                                      "put-credit-spread", "call-credit-spread", "iron-condor", "iron-fly"})
        self.assertLessEqual(len(result["candidates"]), engine.MAX_CANDIDATES)
        self.assertEqual(len({c["id"] for c in result["candidates"]}), len(result["candidates"]))
        for candidate in result["candidates"]:
            self.assertLessEqual(len(candidate["id"]), 100)
            self.assertEqual(set(candidate["scenarioIds"]), {s["id"] for s in result["scenarios"]})
            self.assertGreater(candidate["maxLoss"], 0)
            self.assertEqual(candidate["costEstimate"], COST * len(candidate["legs"]))
            loss, profit = engine._payoff_bounds(candidate["legs"], candidate["costEstimate"])
            self.assertEqual((candidate["maxLoss"], candidate["maxProfit"]), (loss, profit))
            for terminal in (0, 10000, 24000, 25000, 26000, 100000):
                pnl = engine._payoff(candidate["legs"], terminal, candidate["costEstimate"])
                self.assertGreaterEqual(pnl + 1e-8, -candidate["maxLoss"])
                if candidate["maxProfit"] is not None:
                    self.assertLessEqual(pnl, candidate["maxProfit"] + 1e-8)
            if candidate["side"] == "selling":
                self.assertGreaterEqual(len(candidate["legs"]), 2)
                self.assertTrue(any(l["direction"] == "buy" for l in candidate["legs"]))
            else:
                self.assertNotEqual(candidate["expiry"], NOW.date().isoformat())
            self.assertEqual(candidate["probabilityType"], "model-implied")
            self.assertTrue(0 <= candidate["probabilityOfProfit"] <= 1)
            self.assertEqual(len(candidate["scenarioRepricing"]), 9)

    def test_itm_atm_otm_both_long_sides_and_two_buying_expiries(self):
        candidates = analysis()["candidates"]
        for strategy in ("long-call", "long-put"):
            for expiry in ("2026-11-10", "2026-11-17"):
                selected = [c for c in candidates if c["strategy"] == strategy and c["expiry"] == expiry]
                self.assertEqual([c["moneyness"] for c in selected], ["ITM", "ATM", "OTM"])
        self.assertTrue(all(c["maxProfit"] is None for c in candidates if c["strategy"] == "long-call"))
        self.assertTrue(all(c["maxProfit"] is not None for c in candidates if c["strategy"] == "long-put"))

    def test_same_day_gamma_floor_is_medium_before14_and_high_at14(self):
        early = [c for c in analysis()["candidates"] if c["expiry"] == NOW.date().isoformat()]
        self.assertTrue(early)
        self.assertTrue(all(c["riskTier"] in ("medium", "high") for c in early))
        low_notional = [c for c in early if c["riskExposureRatio"] <= engine.LOW_RISK_EXPOSURE_RATIO]
        self.assertTrue(low_notional)
        self.assertTrue(all(c["riskTier"] == "medium" for c in low_notional))
        sessions, instruments, bars, quotes, _ = fixture()
        for at in (datetime(2026, 11, 9, 8, 30, tzinfo=UTC),
                   datetime(2026, 11, 9, 9, 0, tzinfo=UTC)):
            horizon = engine.resolve_horizon(at, "expiry-intraday", sessions, instruments)
            fresh = reprice_quotes(quotes, horizon, at, .8)
            result = engine.build_analysis(SPOT, bars, fresh, horizon, at, RATE, DIVIDEND, COST)
            same_day = [c for c in result["candidates"] if c["expiry"] == NOW.date().isoformat()]
            self.assertTrue(same_day)
            self.assertTrue(all(c["riskTier"] == "high" for c in same_day))
            self.assertTrue(all(any("14:00" in r for r in c["riskTierReasons"]) for c in same_day))

    def test_low_delta_outright_otm_is_high_even_with_small_max_loss(self):
        _, _, _, quotes, _ = fixture()
        # Move the first eligible call OTM alternative farther from spot.
        filtered = [q for q in quotes if not (q["instrumentType"] == "CE" and q["strike"] == 25250)]
        result = analysis(quotes=filtered)
        selected = [c for c in result["candidates"] if c["strategy"] == "long-call"
                    and c["expiry"] == "2026-11-10" and c["moneyness"] == "OTM"]
        self.assertEqual(len(selected), 1)
        candidate = selected[0]
        self.assertLess(abs(candidate["legs"][0]["greeks"]["delta"]), engine.LOW_DELTA_THRESHOLD)
        self.assertLess(candidate["riskExposureRatio"], engine.LOW_RISK_EXPOSURE_RATIO)
        self.assertEqual(candidate["riskTier"], "high")
        self.assertTrue(any("absolute delta" in reason for reason in candidate["riskTierReasons"]))

    def test_conditional_scenario_probabilities_use_same_declared_riskneutral_grid(self):
        _, _, _, quotes, horizon = fixture()
        result = analysis()
        candidate = next(c for c in result["candidates"] if c["strategy"] == "long-call")
        valid, _ = engine._quote_models(SPOT, quotes, horizon, NOW, RATE, DIVIDEND)
        lookup = {q["tradingSymbol"]: q for q in valid}
        models = [lookup[l["tradingSymbol"]] for l in candidate["legs"]]
        volatility = sum(q["iv"] for q in models) / len(models)
        at = engine._aware(candidate["plannedExitAt"])
        years = (at - NOW).total_seconds() / engine.YEAR_SECONDS
        samples = []
        for i in range(engine.PROBABILITY_GRID_POINTS):
            z = engine.NORMAL.inv_cdf((i + .5) / engine.PROBABILITY_GRID_POINTS)
            price = SPOT * math.exp((RATE - DIVIDEND - volatility ** 2 / 2) * years
                                    + volatility * math.sqrt(years) * z)
            win = engine._mark_pnl(candidate["legs"], models, price, at, RATE, DIVIDEND,
                                   candidate["costEstimate"]) > 0
            samples.append((price, win))
        for scenario in result["scenarios"]:
            conditional = candidate["scenarioProfitProbabilities"][scenario["id"]]
            matching = [win for price, win in samples if scenario["low"] <= price <= scenario["high"]]
            self.assertTrue(matching)
            self.assertEqual(conditional["matchedGridPoints"], len(matching))
            self.assertEqual(conditional["probability"], sum(matching) / len(matching))
            self.assertEqual(conditional["probabilityType"], "model-implied")
            self.assertIn("Conditional", conditional["reason"])
        self.assertEqual(candidate["probabilityOfProfit"],
                         sum(win for _, win in samples) / engine.PROBABILITY_GRID_POINTS)

    def test_cross_time_intraday_conditionals_are_explicitly_unavailable(self):
        sessions, instruments, bars, quotes, _ = fixture()
        horizon = engine.resolve_horizon(NOW, "expiry-intraday", sessions, instruments)
        result = analysis(bars, quotes, horizon)
        self.assertTrue(result["candidates"])
        for candidate in result["candidates"]:
            for value in candidate["scenarioProfitProbabilities"].values():
                self.assertIsNone(value["probability"])
                self.assertEqual(value["probabilityType"], "unavailable")
                self.assertIn("differs", value["reason"])

    def test_quote_skew_lot_mismatch_and_mixed_underlying(self):
        _, _, _, quotes, horizon = fixture()
        # Every strike timestamp differs by more than permitted skew.
        changed = [dict(q, observedAt=engine._iso(NOW - timedelta(seconds=(int(q["strike"]) % 1000) / 25)))
                   for q in quotes]
        result = analysis(quotes=changed)
        self.assertTrue(all(len(c["legs"]) == 1 for c in result["candidates"]))
        changed = [dict(q, lotSize=int(q["strike"])) for q in quotes]
        result = analysis(quotes=changed)
        self.assertTrue(all(len(c["legs"]) == 1 for c in result["candidates"]))
        changed = [dict(q, underlying="OTHER" if n == 0 else "NIFTY") for n, q in enumerate(quotes)]
        result = analysis(quotes=changed)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("Mixed underlying" in w for w in result["warnings"]))
        horizon["canSuggest"] = False
        self.assertEqual(analysis(horizon=horizon)["candidates"], [])

    def test_exact_costs_long_put_and_unbounded_long_call(self):
        call = [{"direction": "buy", "quantity": 75, "entryPrice": 100, "strike": 25000, "optionType": "CE"}]
        put = [dict(call[0], optionType="PE")]
        self.assertEqual(engine._payoff_bounds(call, 20), (7520, None))
        self.assertEqual(engine._payoff_bounds(put, 20), (7520, 1867480))


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {"legs": [{"exchange": "NFO", "tradingSymbol": "TEST", "direction": "buy",
                                   "quantity": 75, "entryPrice": 100}],
                          "asOf": engine._iso(NOW), "costEstimate": 10, "targetProfit": 3000,
                          "technicalStop": {"lower": 24900, "upper": None},
                          "plannedExitAt": engine._iso(NOW + timedelta(hours=1))}

    def observation(self, minutes, spot=25000, bid=100, ask=101):
        at = NOW + timedelta(minutes=minutes)
        return {"timestamp": engine._iso(at), "spot": spot,
                "quotes": {"NFO:TEST": {"bid": bid, "ask": ask, "observedAt": engine._iso(at)}}}

    def reconcile(self, observations):
        return engine.reconcile_strategy(self.candidate, observations, NOW + timedelta(hours=1))

    def test_first_observed_risk_target_and_time_use_executable_side(self):
        risk = self.reconcile([self.observation(1, spot=24800, bid=80, ask=81), self.observation(2, bid=150, ask=151)])
        self.assertEqual((risk["trigger"], risk["pnl"]), ("risk", -1510))
        target = self.reconcile([self.observation(1, bid=150, ask=151)])
        self.assertEqual((target["trigger"], target["pnl"]), ("target", 3740))
        timed = self.reconcile([self.observation(60, bid=99, ask=100)])
        self.assertEqual((timed["trigger"], timed["pnl"]), ("time", -85))

    def test_missing_quotes_at_trigger_are_not_retrospectively_filled(self):
        missing = self.observation(1, spot=24800)
        missing["quotes"] = {}
        self.assertEqual(self.reconcile([missing, self.observation(2)])["status"], "unavailable")
        self.assertEqual(self.reconcile([])["status"], "pending")

    def test_unknown_charges_keep_gross_separate_from_net_at_each_trigger(self):
        self.candidate.update(costEstimate=None, targetProfit=None, grossTargetProfit=3000)
        for observation, trigger, gross in (
                (self.observation(1, spot=24800, bid=80, ask=81), "risk", -1500),
                (self.observation(1, bid=150, ask=151), "target", 3750),
                (self.observation(60, bid=99, ask=100), "time", -75)):
            result = self.reconcile([observation])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["trigger"], trigger)
            self.assertEqual(result["grossPnl"], gross)
            self.assertIsNone(result["pnl"])
            self.assertEqual(result["payoffBasis"], "gross-before-charges")
            self.assertTrue(result["hypothetical"])
        missing = self.observation(1, spot=24800)
        missing["quotes"] = {}
        self.assertEqual(self.reconcile([missing, self.observation(2)])["status"], "unavailable")

    def test_legacy_unknown_charges_do_not_infer_a_gross_target(self):
        self.candidate.update(costEstimate=None, targetProfit=None)
        self.assertEqual(self.reconcile([self.observation(60)])["status"], "unavailable")

    def test_ambiguous_simultaneous_hits_and_duplicate_times(self):
        self.assertEqual(self.reconcile([self.observation(1, spot=24800, bid=150, ask=151)])["status"], "ambiguous")
        self.assertEqual(self.reconcile([self.observation(1), self.observation(1)])["status"], "ambiguous")

    def test_unobserved_early_target_and_missing_entry_are_explicit(self):
        missing = self.observation(1)
        missing["quotes"] = {}
        self.assertEqual(self.reconcile([missing, self.observation(2, bid=150, ask=151)])["status"],
                         "unavailable")
        self.candidate.pop("asOf")
        self.assertEqual(self.reconcile([self.observation(1)])["status"], "unavailable")

    def test_late_price_hit_cannot_be_claimed_before_time_exit(self):
        result = self.reconcile([self.observation(61, bid=150, ask=151)])
        self.assertEqual(result["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
