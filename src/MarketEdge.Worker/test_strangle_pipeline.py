"""Transport, isolation, atomic claim and completion lifecycle fixtures."""
from __future__ import annotations

import base64
import copy
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from options_data import KiteClient
from strangle_engine import build_entry, build_position_review, reconcile_trade_ids, entry_spec_choices
from strangle_runner import (_capture, _margin, _position_fingerprint, _regime_evidence,
                            _refresh_required_quotes, _required_symbols, _carry_margins,
                            _read_state, _terminal, _prior_reconciliation, StrangleCancelled, run_strangle_job)
from test_strangle_engine import (DIVIDEND, NOW, RATE, fixture, linked, fill_ledger, closed_roll_cycle,
                                  range_defence_fixture, adjustment)


class ReadOnlyTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = KiteClient.__new__(KiteClient)
        self.client.conn = MagicMock()
        self.client.user_key = "account"
        self.client.api_key = "fixture-key"
        self.client.token = "fixture-token"

    def test_positions_and_trade_fills_use_read_endpoints(self):
        with patch.object(self.client, "get", return_value={"net": [], "day": []}) as get:
            self.client.positions()
            get.assert_called_once_with("/portfolio/positions")
        with patch.object(self.client, "get", return_value=[]) as get:
            self.client.trades()
            get.assert_called_once_with("/trades")

    def test_minute_history_excludes_unfinished_and_overnight(self):
        candles = [
            ["2026-11-16T15:29:00+05:30", 1, 2, 1, 2, 0],
            ["2026-11-17T09:59:00+05:30", 1, 2, 1, 2, 0],
            ["2026-11-17T10:00:00+05:30", 1, 2, 1, 2, 0],
        ]
        with patch.object(self.client, "get", return_value={"candles": candles}) as get:
            bars = self.client.minute_bars(12, NOW)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0]["date"], candles[1][0])
            self.assertEqual(get.call_args.args[0], "/instruments/historical/12/minute")

    def test_margin_posts_only_read_only_basket_without_execution_endpoint(self):
        r, s = fixture()
        legs = build_entry(r, s, NOW, rate=RATE, dividend=DIVIDEND)["legs"]
        response = MagicMock()
        response.read.return_value = b'{"status":"success","data":{"final":{"total":1000}}}'
        response.__enter__.return_value = response
        with patch("options_data.urlopen", return_value=response) as urlopen, patch("options_data._last_request", 0):
            estimate = self.client.basket_margin(legs)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, "https://api.kite.trade/margins/basket?consider_positions=false")
        basket = json.loads(request.data)
        self.assertEqual(len(basket), 4)
        self.assertEqual(basket[0]["quantity"], 75)
        self.assertEqual(basket[0]["product"], "MIS")
        self.assertEqual(estimate["final"]["total"], 1000)
        self.assertFalse(any(name in dir(self.client) for name in ("place_order", "modify_order", "cancel_order")))

    def test_existing_get_keeps_method_and_query_behavior(self):
        with patch.object(self.client, "_request", return_value={"ok": True}) as request:
            self.assertEqual(self.client.get("/quote", [("i", "NSE:NIFTY 50")]), {"ok": True})
            request.assert_called_once_with("/quote", params=[("i", "NSE:NIFTY 50")], text=False)

    def test_worker_dispatches_dedicated_job_without_changing_options_forecasts(self):
        from worker import process_message
        for kind, function in (("options_strangle", "strangle_runner.run_strangle_job"),
                               ("options_analysis", "options_runner.run_options_job")):
            payload = {"jobType": kind, "runId": 2, "strangleRunId": 1, "userKey": "alice"}
            encoded = base64.b64encode(json.dumps(payload).encode()).decode()
            with patch(function) as run:
                process_message(encoded)
                run.assert_called_once_with(payload)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.r, self.s = fixture()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.conn
        self.conn.execute.return_value = self.conn
        self.conn.rowcount = 1
        self.run = {"Id": 10, "UserKey": "alice", "RequestId": self.r["requestId"],
                    "IndexCode": "NIFTY50", "Action": "entry", "StrategyId": None,
                    "Status": "queued", "JobStatus": "queued", "RequestJson": json.dumps(self.r)}
        self.payload = {"jobType": "options_strangle", "runId": 20, "strangleRunId": 10, "userKey": "alice"}
        self.client = MagicMock()
        self.client.positions.return_value = self.s["positions"]
        self.rows_calls = 0
        self.running = {"RunStatus": "running", "JobStatus": "running"}

    def rows(self, _):
        self.rows_calls += 1
        sql = self.conn.execute.call_args.args[0]
        claimed = any("UPDATE [options].[StrangleRuns] SET Status='running'" in command[0]
                      for command in self.commands())
        if "SELECT Status AS JobStatus" in sql:
            return [{"JobStatus": self.running["JobStatus"] if claimed else self.run["JobStatus"]}]
        if "SELECT Status AS RunStatus" in sql:
            return [{"RunStatus": self.running["RunStatus"] if claimed else self.run["Status"]}]
        return [self.run]

    def execute(self, *, rows=None, capture=None, check=None, margin=None, refresh=None, clock=None):
        if isinstance(self.client.minute_bars.return_value, MagicMock):
            self.client.minute_bars.return_value = self.s["candles"]
        with patch("strangle_runner.get_connection", return_value=self.conn), \
             patch("strangle_runner.rows", side_effect=rows or self.rows), \
             patch("strangle_runner.KiteClient", return_value=self.client), \
             patch("strangle_runner._capture", side_effect=capture or (lambda *_: (self.s, None, []))), \
             patch("strangle_runner._refresh_required_quotes", side_effect=refresh), \
             patch("strangle_runner._margin", side_effect=margin), \
             patch("strangle_runner.utcnow", side_effect=clock or (lambda: NOW)), \
             patch.dict(os.environ, {"MARKETEDGE_STRANGLE_RISK_FREE_RATE": str(RATE),
                                     "MARKETEDGE_STRANGLE_DIVIDEND_YIELD": str(DIVIDEND)}):
            if check:
                with patch("strangle_runner._check_cancelled", side_effect=check):
                    run_strangle_job(self.payload)
            else:
                run_strangle_job(self.payload)

    def commands(self):
        return [call.args for call in self.conn.execute.call_args_list]

    def persisted_result(self):
        for command in self.commands():
            if "ResultJson=?" in command[0]:
                return json.loads(command[1])
        return None

    def test_atomic_claim_association_and_success(self):
        self.execute()
        sql = "\n".join(c[0] for c in self.commands())
        self.assertIn("JobType='options_strangle'", sql)
        self.assertIn("UserKey=? AND JobRunId=?", sql)
        self.assertIn("Id=? AND Status='queued'", sql)
        self.assertIn("UPDLOCK,HOLDLOCK", sql)
        self.assertEqual(self.persisted_result()["executionStatus"], "advisory-only")
        self.assertEqual(self.persisted_result()["requestId"], self.r["requestId"])
        self.assertEqual(self.client.positions.call_count, 1)
        self.conn.close.assert_called_once()

    def test_baseline_free_adjustment_reads_current_positions_and_persists_review(self):
        request, snapshot, _ = linked()
        request["strategyId"] = None
        self.r, self.s = request, snapshot
        self.client.positions.return_value = snapshot["positions"]
        self.client.trades.return_value = snapshot["trades"]
        self.run.update(Action="adjustment", StrategyId=None, RequestJson=json.dumps(request))
        self.execute()
        result = self.persisted_result()
        self.assertEqual(result["analysisMode"], "current-positions")
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(self.client.trades.call_count, 2)
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertIn("hold", [c["action"] for c in result["candidates"]])

    def test_baseline_free_capture_reads_session_fills_not_saved_strategy(self):
        request, snapshot, _ = linked()
        request["strategyId"] = None
        self.client.positions.return_value = snapshot["positions"]
        self.client.quotes.return_value = snapshot["quotes"]
        self.client.minute_bars.return_value = snapshot["candles"]
        self.client.trades.return_value = snapshot["trades"]
        index = {"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}
        with patch("strangle_runner.rows", return_value=[index]), \
             patch("strangle_runner.load_instruments", return_value=snapshot["instruments"]), \
             patch("strangle_runner.load_sessions", return_value=snapshot["sessions"]), \
             patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}) as references, \
             patch("strangle_runner.select_expiry", side_effect=AssertionError("Review does not require nearest expiry")), \
             patch("strangle_runner.utcnow", return_value=NOW):
            captured, baseline, _ = _capture(self.conn, self.client,
                {**self.run, "Action": "adjustment", "StrategyId": None}, request, MagicMock())
        self.assertIsNone(baseline)
        self.assertEqual(captured["positions"], snapshot["positions"])
        self.assertFalse(any("StrangleStrategies" in command[0] for command in self.commands()))
        self.client.trades.assert_called_once()
        references.assert_called_once()
        self.assertEqual(captured["trades"], snapshot["trades"])
        self.assertGreater(len(self.client.quotes.call_args.args[0]), 8)
        for position in snapshot["positions"]["net"]:
            self.assertIn(position["exchange"] + ":" + position["tradingsymbol"], self.client.quotes.call_args.args[0])

    def test_selected_index_and_nifty_candles_capture_separately_only_for_sensex(self):
        for index_code in ("NIFTY50", "SENSEX"):
            r, s, _ = linked(index=index_code)
            r.update(strategyId=None, riskFreeRate=.06, dividendYield=0)
            run = {**self.run, "IndexCode": index_code, "Action": "adjustment", "StrategyId": None}
            index = {"Code": index_code, "SpotSymbol": s["spotSymbol"],
                     "DerivativeExchange": "NFO" if index_code == "NIFTY50" else "BFO"}
            self.client.reset_mock()
            self.client.positions.return_value = s["positions"]
            self.client.trades.return_value = s["trades"]
            server = {**s["quotes"], s["spotSymbol"]: s["spotQuote"]}
            server["NSE:NIFTY 50"] = s["spotQuote"] if index_code == "NIFTY50" else s["niftySpotQuote"]
            self.client.quotes.side_effect = lambda symbols: {k: server[k] for k in symbols if k in server}
            self.client.minute_bars.side_effect = lambda token, at: s["candles"] if token == 1 else s["niftyCandles"]
            with patch("strangle_runner.rows", return_value=[index]), \
                 patch("strangle_runner.load_instruments", return_value=s["instruments"]), \
                 patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}) as references, \
                 patch("strangle_runner.load_sessions", return_value=s["sessions"]), \
                 patch("strangle_runner.utcnow", return_value=NOW):
                captured, baseline, _ = _capture(self.conn, self.client, run, r, MagicMock())
            self.assertIsNone(baseline)
            self.assertEqual(self.client.minute_bars.call_count, 1 if index_code == "NIFTY50" else 2)
            self.assertEqual([call.args[1] for call in references.call_args_list],
                             ["NIFTY50"] if index_code == "NIFTY50" else ["SENSEX", "NIFTY50"])
            if index_code == "NIFTY50":
                self.assertIs(captured["niftyCandles"], captured["candles"])
            else:
                self.assertEqual(captured["niftyCandles"], s["niftyCandles"])
                self.assertEqual([call.args[0] for call in self.client.minute_bars.call_args_list], [1, 2])
            result = build_position_review(r, captured, NOW)
            self.assertEqual(result["regime"]["sourceIndexCode"], "NIFTY50")
            self.assertEqual(result["regime"]["status"], "available")
            self.assertTrue(all(len(call.args[0]) < 20 for call in self.client.quotes.call_args_list))

    def test_entry_initial_capture_and_requotes_cover_all_five_baskets_without_full_chain(self):
        r, s = fixture()
        index = {"Code": "NIFTY50", "SpotSymbol": s["spotSymbol"], "DerivativeExchange": "NFO"}
        server = {**s["quotes"], s["spotSymbol"]: s["spotQuote"]}
        self.client.quotes.side_effect = lambda symbols: {k: server[k] for k in symbols if k in server}
        self.client.minute_bars.return_value = s["candles"]
        with patch("strangle_runner.rows", return_value=[index]), \
             patch("strangle_runner.load_instruments", return_value=s["instruments"]), \
             patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}), \
             patch("strangle_runner.load_sessions", return_value=s["sessions"]), \
             patch("strangle_runner.utcnow", return_value=NOW):
            captured, _, _ = _capture(self.conn, self.client, self.run, r, MagicMock())
        self.assertGreater(self.client.quotes.call_count, 1)
        self.assertTrue(all(len(call.args[0]) < 20 for call in self.client.quotes.call_args_list))
        choices = entry_spec_choices(s["instruments"], "2026-12-01", 24500, 220, 1000)
        self.assertEqual(len(choices), 5)
        needed = {i["exchange"]+":"+i["tradingSymbol"] for specs in choices for i, _ in specs}
        self.assertTrue(needed <= captured["quotes"].keys())
        result = build_entry(r, captured, NOW, rate=RATE, dividend=DIVIDEND)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["premiumSelection"]["evaluatedBaskets"], 5)
        self.assertIsNone(result["exitPolicy"]["reason"])

    def test_cold_review_captures_straddle_quotes_before_margin_then_reprices_strict_defence(self):
        request, source, _ = range_defence_fixture(dividend=0)
        self.r, self.s = request, source
        self.run.update(Action="adjustment", StrategyId=None, RequestJson=json.dumps(request))
        self.client.positions.return_value = source["positions"]
        self.client.trades.return_value = source["trades"]
        self.client.minute_bars.return_value = source["candles"]
        self.client.basket_margin.return_value = {"final": {"total": 1000}}
        server = {**source["quotes"], source["spotSymbol"]: source["spotQuote"]}
        self.client.quotes.side_effect = lambda symbols: {k: server[k] for k in symbols if k in server}
        with patch("strangle_runner.rows", return_value=[
                {"Code": "NIFTY50", "SpotSymbol": source["spotSymbol"], "DerivativeExchange": "NFO"}]), \
             patch("strangle_runner.load_instruments", return_value=source["instruments"]), \
             patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}), \
             patch("strangle_runner.load_sessions", return_value=source["sessions"]), \
             patch("strangle_runner.utcnow", return_value=NOW):
            captured, baseline, warnings = _capture(self.conn, self.client, self.run, request, MagicMock())
        self.assertIsNone(baseline)
        self.assertGreater(self.client.quotes.call_count, 1)
        initial = build_position_review(request, captured, NOW)
        self.assertTrue(any(c.get("structure") == "hedged-straddle" for c in initial["candidates"]))
        self.execute(capture=lambda *_: (captured, None, warnings),
                     margin=lambda client, result: _margin(client, result), refresh=_refresh_required_quotes)
        result = self.persisted_result()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(result["regime"]["state"], "risk-on")
        self.assertEqual(result["regime"]["direction"], "neutral")
        straddle = next(c for c in result["candidates"] if c.get("structure") == "hedged-straddle")
        self.assertEqual(straddle["marginAfter"]["status"], "available")
        self.assertLess(straddle["stressLossRupees"], result["riskPlan"]["stressLossRupees"])
        self.assertEqual(straddle["defenceQualification"]["currentStressLossRupees"],
                         result["riskPlan"]["stressLossRupees"])
        self.assertTrue(all(len(call.args[0]) < 20 for call in self.client.quotes.call_args_list))
        self.assertFalse(any(c["action"] in ("close", "reduce") for c in result["candidates"]))
        self.assertEqual(result["modelInputs"]["dividendYield"], 0)

    def test_straddle_without_comparable_margin_is_removed_not_relabelled_as_hold_or_exit(self):
        self.r, self.s, _ = range_defence_fixture()
        self.run.update(Action="adjustment", StrategyId=None, RequestJson=json.dumps(self.r))
        self.client.positions.return_value = self.s["positions"]
        self.client.trades.return_value = self.s["trades"]
        self.client.minute_bars.return_value = self.s["candles"]
        self.client.basket_margin.side_effect = RuntimeError("no comparable margin")
        self.execute(margin=lambda client, result: _margin(client, result))
        result = self.persisted_result()
        self.assertEqual([c["action"] for c in result["candidates"]], ["hold"])
        self.assertEqual(result["candidates"][0]["structure"], "hedged-strangle")
        self.assertFalse(result["mandatoryExit"])
        self.assertIsNone(result["exitPolicy"]["reason"])

    def configure_review(self):
        self.r, self.s, _ = linked()
        self.r.update(strategyId=None, riskFreeRate=.06, dividendYield=0)
        self.run.update(Action="adjustment", StrategyId=None, RequestJson=json.dumps(self.r))
        self.client.positions.return_value = self.s["positions"]
        self.client.trades.return_value = self.s["trades"]
        self.client.minute_bars.return_value = self.s["candles"]

    def test_slow_margin_precedes_genuinely_fresh_account_and_quote_capture(self):
        self.configure_review()
        self.s["positionsObservedAt"] = NOW.isoformat()
        current, events = [NOW], []
        def margin(client, result):
            events.append("margin")
            current[0] = NOW+timedelta(seconds=120)
        def positions():
            events.append("positions")
            return copy.deepcopy(self.s["positions"])
        def trades():
            events.append("trades")
            return copy.deepcopy(self.s["trades"])
        def refresh(client, snapshot, request, baseline, check):
            events.append("quotes")
            snapshot["spotQuote"]["timestamp"] = current[0].isoformat()
            for quote in snapshot["quotes"].values():
                quote["timestamp"] = current[0].isoformat()
            for key in ("niftyChangePct", "sensexChangePct", "vixChangePct"):
                snapshot["regimeEvidence"][key+"ObservedAt"] = current[0].isoformat()
        candles = copy.deepcopy(self.s["candles"])
        for minute in range(2):
            candles.append({**candles[-1], "date": (NOW+timedelta(minutes=minute)).isoformat()})
        def minute_bars(*_):
            events.append("candles")
            return candles
        self.client.positions.side_effect = positions
        self.client.trades.side_effect = trades
        self.client.minute_bars.side_effect = minute_bars
        self.execute(margin=margin, refresh=refresh, clock=lambda: current[0])
        result = self.persisted_result()
        self.assertEqual(events, ["margin", "positions", "trades", "quotes", "candles", "positions", "trades"])
        self.assertEqual(result["asOf"], current[0].isoformat())
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(result["sessionPnl"]["status"], "verified")
        self.assertIn("hold", [c["action"] for c in result["candidates"]])

    def test_failed_final_position_capture_never_renews_previous_timestamp(self):
        self.configure_review()
        self.s["positionsObservedAt"] = NOW.isoformat()
        self.client.positions.side_effect = RuntimeError("position provider unavailable")
        self.execute()
        result = self.persisted_result()
        self.assertEqual(result["asOf"], NOW.isoformat())
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["sessionPnl"]["totalRupees"])

    def test_deadline_manual_exit_survives_position_and_quote_provider_failure(self):
        self.configure_review()
        self.s["positionsObservedAt"] = NOW.isoformat()
        self.client.positions.side_effect = RuntimeError("position provider unavailable")
        self.execute(clock=lambda: NOW.replace(hour=15, minute=16),
                     refresh=RuntimeError("quotes unavailable"))
        result = self.persisted_result()
        self.assertEqual(result["executionStatus"], "exit-required")
        self.assertEqual(result["exitPolicy"]["reason"], "time")
        self.assertTrue(result["mandatoryExit"])
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["sessionPnl"]["totalRupees"])

    def test_reference_latency_precedes_initial_actual_position_capture(self):
        self.configure_review()
        current, events = [NOW], []
        index = {"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}
        def references(*_):
            events.append("references")
            current[0] += timedelta(seconds=120)
            return {"warnings": []}
        def positions():
            events.append("positions")
            return self.s["positions"]
        def trades():
            events.append("trades")
            return self.s["trades"]
        self.client.positions.side_effect = positions
        self.client.trades.side_effect = trades
        self.client.quotes.return_value = self.s["quotes"]
        with patch("strangle_runner.rows", return_value=[index]), \
             patch("strangle_runner.load_instruments", return_value=self.s["instruments"]), \
             patch("strangle_runner.ensure_reference_data", side_effect=references), \
             patch("strangle_runner.load_sessions", return_value=self.s["sessions"]), \
             patch("strangle_runner.utcnow", side_effect=lambda: current[0]):
            captured, _, _ = _capture(self.conn, self.client, self.run, self.r, MagicMock())
        self.assertEqual(events, ["references", "positions", "trades"])
        self.assertEqual(captured["positionsObservedAt"], (NOW+timedelta(seconds=120)).isoformat())

    def test_review_completion_changed_trade_price_timestamp_or_closed_row_invalidates_totals(self):
        for field in ("average_price", "fill_timestamp", "closed-row"):
            self.setUp()
            self.configure_review()
            if field == "closed-row":
                changed = copy.deepcopy(self.s["positions"])
                changed["net"].append({**changed["net"][0], "tradingsymbol": "NIFTY26NOV24000CE",
                                       "quantity": 0, "buy_quantity": 75, "sell_quantity": 75})
                self.client.positions.return_value = changed
            else:
                changed = copy.deepcopy(self.s["trades"])
                changed[0][field] = (changed[0][field]+1 if field == "average_price"
                                     else (NOW-timedelta(seconds=15)).isoformat())
                self.client.trades.return_value = changed
            self.execute()
            with self.subTest(field=field):
                result = self.persisted_result()
                self.assertEqual(len(result["positions"]), 4)
                self.assertEqual(result["executionStatus"], "unavailable")
                self.assertFalse(result["positionsConfirmed"])
                self.assertFalse(result["eligible"])
                self.assertEqual(result["candidates"], [])
                self.assertIsNone(result["sessionPnl"]["totalRupees"])
                self.assertIsNone(result["sessionPnl"]["realizedRupees"])
                self.assertEqual(result["payoff"]["status"], "unavailable")

    def test_review_quote_recapture_dispatches_review_and_preserves_positions_on_failure(self):
        self.configure_review()
        self.execute(refresh=RuntimeError("quotes unavailable"))
        result = self.persisted_result()
        self.assertEqual(result["analysisMode"], "current-positions")
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(len(result["positions"]), 4)
        self.assertEqual(result["sessionPnl"]["status"], "partial")
        self.assertEqual(result["sessionPnl"]["realizedRupees"], 0)
        self.assertIsNone(result["sessionPnl"]["totalRupees"])
        self.assertEqual(result["candidates"][0]["action"], "review")

    def test_review_populates_margin_legs_and_reprices_without_legacy_adjustment(self):
        self.configure_review()
        self.client.basket_margin.return_value = {"final": {"total": 1000}}
        def reprice(client, snapshot, request, baseline, check):
            quote = snapshot["quotes"]["NFO:NIFTY5024200PE"]
            quote["depth"]["buy"][0]["price"] += .01
            quote["depth"]["sell"][0]["price"] += .01
        self.execute(margin=lambda client, result: _margin(client, result), refresh=reprice)
        result = self.persisted_result()
        self.assertEqual(result["analysisMode"], "current-positions")
        self.assertEqual(result["executionStatus"], "review-only")
        self.assertEqual(result["modelInputs"]["source"], "request")
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["exitPolicy"]["reason"])
        self.assertAlmostEqual(result["riskPlan"]["targetPnlRupees"], 2*result["riskPlan"]["suggestedSlRupees"])
        self.assertTrue(all(c["action"] in ("hold", "roll") for c in result["candidates"]))
        self.assertEqual(result["modelInputs"]["dividendYield"], 0)
        self.assertEqual(result["margin"]["status"], "available")
        self.assertGreater(self.client.basket_margin.call_count, 1)
        self.assertTrue(any(c["action"] == "roll" for c in result["candidates"]))
        self.assertAlmostEqual(result["sessionPnl"]["totalRupees"], -30.75)
        self.assertTrue(all(c["marginAfter"]["status"] == "available"
                            for c in result["candidates"] if c["action"] == "roll"))

    def test_review_missing_margin_filters_rolls_including_after_reprice(self):
        self.configure_review()
        self.client.basket_margin.side_effect = RuntimeError("no margin")
        self.execute(margin=lambda client, result: _margin(client, result))
        result = self.persisted_result()
        self.assertTrue(result["legs"])
        self.assertFalse(any(c["action"] == "roll" for c in result["candidates"]))
        self.assertIn("hold", [c["action"] for c in result["candidates"]])
        self.assertEqual(result["executionStatus"], "review-only")

    def test_review_capture_reference_and_trade_failure_still_returns_live_positions(self):
        self.configure_review()
        self.client.trades.side_effect = RuntimeError("no fills")
        self.client.quotes.side_effect = RuntimeError("no quotes")
        with patch("strangle_runner.rows", return_value=[
                {"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}]), \
             patch("strangle_runner.load_instruments", return_value=self.s["instruments"]), \
             patch("strangle_runner.load_sessions", return_value=[]), \
             patch("strangle_runner.ensure_reference_data", side_effect=RuntimeError("no references")), \
             patch("strangle_runner.utcnow", return_value=NOW):
            captured, baseline, warnings = _capture(self.conn, self.client, self.run, self.r, MagicMock())
        self.assertIsNone(baseline)
        self.assertEqual(captured["positions"], self.s["positions"])
        self.assertIsNone(captured["trades"])
        self.assertEqual(captured["quotes"], {})
        self.assertTrue(warnings)

    def test_repeated_delivery_running_or_terminal_is_noop(self):
        for state in ("running", "completed", "failed", "cancelled"):
            self.setUp()
            self.run["Status"] = state
            self.execute()
            self.assertEqual(len(self.commands()), 2)
            self.client.positions.assert_not_called()

    def test_failed_atomic_claim_has_no_side_effects(self):
        self.conn.rowcount = 0
        self.execute()
        self.assertIsNone(self.persisted_result())
        self.conn.rollback.assert_called_once()
        self.client.positions.assert_not_called()

    def test_wrong_account_job_association_does_not_update_either_row(self):
        self.execute(rows=lambda _: [])
        self.assertEqual(len(self.commands()), 1)
        self.conn.commit.assert_not_called()

    def test_all_lifecycle_locks_and_writes_are_explicit_job_then_run(self):
        self.execute()
        locked = [command[0] for command in self.commands() if "WITH (UPDLOCK,HOLDLOCK)" in command[0]]
        self.assertEqual(len(locked), 4)
        for job, run in zip(locked[::2], locked[1::2]):
            self.assertIn("FROM dbo.JobRuns", job)
            self.assertIn("FROM [options].[StrangleRuns]", run)
            self.assertNotIn("JOIN", job+run)
        writes = [command[0] for command in self.commands() if command[0].startswith("UPDATE")]
        self.assertEqual(len(writes), 4)
        for job, run in zip(writes[::2], writes[1::2]):
            self.assertTrue(job.startswith("UPDATE dbo.JobRuns"))
            self.assertTrue(run.startswith("UPDATE [options].[StrangleRuns]"))

    def test_terminal_failure_preserves_cancellation_and_same_lock_order(self):
        self.run.update(Status="cancelled", JobStatus="running")
        with patch("strangle_runner.rows", side_effect=self.rows):
            _terminal(self.conn, 10, 20, "alice", "failed", "Must not override cancellation")
        writes = [command for command in self.commands() if command[0].startswith("UPDATE")]
        self.assertTrue(writes[0][0].startswith("UPDATE dbo.JobRuns"))
        self.assertTrue(writes[1][0].startswith("UPDATE [options].[StrangleRuns]"))
        self.assertTrue(all(command[1:3] == ("cancelled", None) for command in writes))

    def test_cancellation_prevents_completion(self):
        self.execute(check=[None, StrangleCancelled()])
        self.assertIsNone(self.persisted_result())
        self.assertTrue(any("SET Status=?" in c[0] and c[1] == "cancelled" for c in self.commands()))

    def test_queued_cancelled_job_cancels_without_broker_capture(self):
        self.run["JobStatus"] = "cancelled"
        self.execute()
        self.assertIsNone(self.persisted_result())
        self.client.positions.assert_not_called()
        self.assertTrue(any("SET Status=?" in c[0] and c[1] == "cancelled" for c in self.commands()))

    def test_error_persistence_never_contains_raw_secret_or_broker_payload(self):
        self.execute(capture=RuntimeError("SECRET_TOKEN account=999 broker-body"))
        serialized = str(self.commands())
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("broker-body", serialized)
        self.assertTrue(any("SET Status=?" in c[0] and c[1] == "failed" for c in self.commands()))
        self.assertIsNone(self.persisted_result())

    def test_persisted_request_cannot_change_account_index_action_or_strategy(self):
        for key, value in (("indexCode", "SENSEX"), ("action", "adjustment"),
                           ("strategyId", 1), ("requestId", "another")):
            self.setUp()
            r = {**self.r, key: value}
            self.run["RequestJson"] = json.dumps(r)
            self.execute()
            self.assertIsNone(self.persisted_result())
            self.client.positions.assert_not_called()

    def test_positions_changed_at_completion_invalidates_result(self):
        self.client.positions.return_value = {"net": [{"exchange": "NFO", "tradingsymbol": "NEW",
                                                       "product": "MIS", "quantity": 75}]}
        self.execute()
        result = self.persisted_result()
        self.assertEqual(result["executionStatus"], "unavailable")
        self.assertIn("positions changed", " ".join(result["blockers"]))

    def test_age_revalidated_after_slow_margin(self):
        def delayed(*_):
            self.s["spotQuote"]["timestamp"] = (NOW-timedelta(seconds=31)).isoformat()
        self.execute(margin=delayed)
        self.assertEqual(self.persisted_result()["executionStatus"], "unavailable")

    def test_adjustment_rereads_actual_positions_and_fill_ledger(self):
        r, s, b = linked()
        self.r, self.s = r, s
        self.run.update(Action="adjustment", StrategyId=5, RequestJson=json.dumps(r))
        self.client.positions.return_value = s["positions"]
        self.client.trades.return_value = s["trades"]
        self.execute(capture=lambda *_: (s, b, []))
        self.assertEqual(self.client.trades.call_count, 1)
        self.assertIsNotNone(self.persisted_result()["strategyPnlRupees"])

    def test_selected_id_reconciliation_persists_only_in_run_result(self):
        r, s, baseline = linked()
        r["selectedTradeIds"] = [t["trade_id"] for t in s["trades"]]
        frozen = copy.deepcopy(baseline)
        self.r, self.s = r, s
        self.run.update(Action="adjustment", StrategyId=5, RequestJson=json.dumps(r))
        self.client.positions.return_value = s["positions"]
        self.client.trades.return_value = s["trades"]
        self.execute(capture=lambda *_: (
            s, reconcile_trade_ids(baseline, r["selectedTradeIds"], s["trades"], s["instruments"], NOW), []))
        result = self.persisted_result()
        self.assertTrue(result["reconciliation"]["verified"])
        self.assertEqual(result["reconciliation"]["selectedTradeIds"], sorted(r["selectedTradeIds"]))
        self.assertEqual(result["reconciliation"]["cashFlows"], fill_ledger(s["trades"]))
        self.assertEqual(baseline, frozen)
        self.assertFalse(any("UPDATE [options].[StrangleStrategies]" in command[0] or
                             "INSERT INTO [options].[StrangleStrategies]" in command[0]
                             for command in self.commands()))

    def test_regime_missing_context_is_unknown_not_invented(self):
        self.assertFalse(_regime_evidence({}, NOW)["fresh"])

    def test_net_and_day_are_not_added_or_average_prices_used(self):
        position = {"exchange": "NFO", "tradingsymbol": "A", "product": "MIS", "quantity": 75}
        self.assertEqual(_position_fingerprint({"net": [position], "day": [position]}),
                         _position_fingerprint({"net": [position], "day": []}))

    def test_roll_not_exposed_without_broker_margin(self):
        result = build_entry(self.r, self.s, NOW, rate=RATE, dividend=DIVIDEND)
        result["candidates"] = [{"action": "roll", "legs": result["legs"]},
                                {"action": "close", "legs": result["legs"]}]
        self.client.basket_margin.side_effect = RuntimeError("unavailable")
        _margin(self.client, result)
        self.assertEqual([c["action"] for c in result["candidates"]], ["close"])

    def test_both_actions_capture_bounded_fresh_far_wings_and_account_exposure(self):
        for action in ("entry", "adjustment"):
            self.setUp()
            r, s, b = linked()
            run = {**self.run, "Action": action, "StrategyId": 5 if action == "adjustment" else None}
            broker_quotes = {**s["quotes"], "NSE:NIFTY 50": s["spotQuote"]}
            self.client.quotes.return_value = broker_quotes
            self.client.minute_bars.return_value = s["candles"]
            self.client.positions.return_value = s["positions"]
            self.client.trades.return_value = s["trades"]
            records = [[{"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}]]
            if action == "adjustment":
                records.append([{"BaselineJson": json.dumps(b)}])
                records.append([])
            events = []
            with patch("strangle_runner.rows", side_effect=records), \
                 patch("strangle_runner.load_instruments", return_value=s["instruments"]), \
                 patch("strangle_runner.ensure_reference_data", side_effect=lambda *_: events.append("references") or {"warnings": []}) as references, \
                 patch("strangle_runner.load_sessions", side_effect=lambda *_: events.append("sessions") or s["sessions"]), \
                 patch("strangle_runner.utcnow", return_value=NOW):
                capture, baseline, warnings = _capture(self.conn, self.client, run, r, MagicMock())
            captured_symbols = self.client.quotes.call_args.args[0]
            self.assertIn("NFO:NIFTY5023200PE", captured_symbols)
            self.assertIn("NFO:NIFTY5025800CE", captured_symbols)
            self.assertLess(len(captured_symbols), 20)
            self.assertTrue(all(len(call.args[0]) < 20 for call in self.client.quotes.call_args_list))
            self.client.positions.assert_called_once()
            self.client.minute_bars.assert_called_once_with(1, NOW)
            self.assertEqual(capture["spotQuote"], s["spotQuote"])
            self.assertEqual(self.client.trades.call_count, int(action == "adjustment"))
            references.assert_called_once_with(self.conn, "NIFTY50", NOW)
            self.assertEqual(events, ["references", "sessions"])

    def test_manual_request_cash_flows_are_rejected(self):
        r, s, _ = linked()
        r["cashFlows"] = fill_ledger(s["trades"])
        self.run.update(Action="adjustment", StrategyId=5, RequestJson=json.dumps(r))
        self.execute()
        self.assertIsNone(self.persisted_result())
        self.client.positions.assert_not_called()
        self.assertTrue(any("SET Status=?" in c[0] and c[1] == "failed" for c in self.commands()))

    def test_entry_selected_trade_ids_are_rejected(self):
        self.run["RequestJson"] = json.dumps({**self.r, "selectedTradeIds": ["actual-id"]})
        self.execute()
        self.assertIsNone(self.persisted_result())
        self.client.positions.assert_not_called()

    def test_capture_derives_selected_id_ledger_from_account_actual_trade_fills(self):
        r, s, b = linked()
        r["selectedTradeIds"] = [t["trade_id"] for t in s["trades"]]
        self.client.quotes.return_value = {**s["quotes"], "NSE:NIFTY 50": s["spotQuote"]}
        self.client.minute_bars.return_value = s["candles"]
        self.client.positions.return_value = s["positions"]
        self.client.trades.return_value = s["trades"]
        baseline_json = json.dumps(b)
        with patch("strangle_runner.rows", side_effect=[
                [{"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}],
                [{"BaselineJson": baseline_json}], []]), \
             patch("strangle_runner.load_instruments", return_value=s["instruments"]), \
             patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}), \
             patch("strangle_runner.load_sessions", return_value=s["sessions"]), \
             patch("strangle_runner.utcnow", return_value=NOW):
            captured, reconciled, _ = _capture(self.conn, self.client,
                                              {**self.run, "Action": "adjustment", "StrategyId": 5}, r, MagicMock())
        from test_strangle_engine import adjustment
        result = adjustment(r, captured, reconciled)
        self.assertTrue(result["eligible"])
        self.assertTrue(result["reconciliation"]["verified"])
        self.assertEqual(result["reconciliation"]["selectedTradeIds"], sorted(r["selectedTradeIds"]))
        self.assertEqual(result["reconciliation"]["cashFlows"], fill_ledger(s["trades"]))
        self.assertNotIn("cashFlows", json.loads(baseline_json))
        self.assertNotIn("selectedTradeIds", json.loads(baseline_json))

    def test_prior_verified_history_is_account_strategy_scoped_and_unioned(self):
        _, s, _ = linked()
        flows = fill_ledger(s["trades"])
        records = [
            {"ResultJson": json.dumps({"positionsConfirmed": True, "reconciliation": {
                "verified": True, "selectedTradeIds": ["0", "1"], "cashFlows": flows[:2]}})},
            {"ResultJson": json.dumps({"positionsConfirmed": True, "reconciliation": {
                "verified": True, "selectedTradeIds": ["1", "2"], "cashFlows": flows[1:3]}})},
            {"ResultJson": json.dumps({"positionsConfirmed": False, "reconciliation": {
                "verified": True, "selectedTradeIds": ["unverified"], "cashFlows": []}})},
        ]
        with patch("strangle_runner.rows", return_value=records):
            prior = _prior_reconciliation(self.conn, {**self.run, "StrategyId": 5})
        self.assertEqual(prior["selectedTradeIds"], ["0", "1", "2"])
        self.assertEqual({f["tradeId"] for f in prior["cashFlows"]}, {"0", "1", "2"})
        sql, *params = self.commands()[-1]
        self.assertIn("Status='completed'", sql)
        self.assertEqual(params, ["alice", "NIFTY50", 5, 10])

    def test_capture_blocks_history_omission_and_null_ids_reuse_all_verified_losses(self):
        r, s, b = closed_roll_cycle()
        ids = [t["trade_id"] for t in s["trades"]]
        previous = adjustment(r, s, reconcile_trade_ids(b, ids, s["trades"], s["instruments"], NOW))
        omitted = [trade_id for trade_id in ids if trade_id not in ("new-open", "new-close")]
        self.client.quotes.return_value = {**s["quotes"], "NSE:NIFTY 50": s["spotQuote"]}
        self.client.minute_bars.return_value = s["candles"]
        self.client.positions.return_value = s["positions"]
        self.client.trades.return_value = s["trades"]
        for selected_ids in (omitted, None):
            with patch("strangle_runner.rows", side_effect=[
                    [{"Code": "NIFTY50", "SpotSymbol": "NSE:NIFTY 50", "DerivativeExchange": "NFO"}],
                    [{"BaselineJson": json.dumps(b)}], [{"ResultJson": json.dumps(previous)}]]), \
                 patch("strangle_runner.load_instruments", return_value=s["instruments"]), \
                 patch("strangle_runner.ensure_reference_data", return_value={"warnings": []}), \
                 patch("strangle_runner.load_sessions", return_value=s["sessions"]), \
                 patch("strangle_runner.utcnow", return_value=NOW):
                request = {**r, "selectedTradeIds": selected_ids}
                run = {**self.run, "Action": "adjustment", "StrategyId": 5}
                if selected_ids is not None:
                    with self.assertRaisesRegex(ValueError, "omit previously verified"):
                        _capture(self.conn, self.client, run, request, MagicMock())
                else:
                    captured, reconciled, _ = _capture(self.conn, self.client, run, request, MagicMock())
                    current = adjustment(request, captured, reconciled)
                    self.assertAlmostEqual(current["strategyPnlRupees"], -1530)
                    self.assertEqual(current["reconciliation"]["source"], "prior-verified-ledger")
                    self.assertEqual(current["originalRiskBudget"], b["originalRiskBudget"])

    def test_targeted_refresh_recovers_aged_full_chain_and_reprices_current_marks(self):
        before = build_entry(self.r, self.s, NOW, rate=RATE, dividend=DIVIDEND)
        server = copy.deepcopy(self.s["quotes"])
        spot = copy.deepcopy(self.s["spotQuote"])
        server[self.s["spotSymbol"]] = spot
        short = before["legs"][0]
        key = short["exchange"]+":"+short["tradingSymbol"]
        server[key]["depth"]["buy"][0]["price"] += 1
        server[key]["depth"]["sell"][0]["price"] += 1
        for q in self.s["quotes"].values():
            q["timestamp"] = (NOW-timedelta(seconds=65)).isoformat()
        self.s["spotQuote"]["timestamp"] = (NOW-timedelta(seconds=35)).isoformat()
        self.client.quotes.side_effect = lambda symbols: {symbol: copy.deepcopy(server[symbol])
                                                        for symbol in symbols if symbol in server}
        with patch("strangle_runner.utcnow", return_value=NOW):
            _refresh_required_quotes(self.client, self.s, self.r, None, MagicMock())
        result = build_entry(self.r, self.s, NOW, rate=RATE, dividend=DIVIDEND)
        self.assertTrue(result["eligible"])
        self.assertAlmostEqual(result["netCreditPoints"], before["netCreditPoints"]+1)
        self.assertLess(len(self.client.quotes.call_args.args[0]), 20)
        self.assertEqual(self.s["spotQuote"]["timestamp"], NOW.isoformat())
        self.assertEqual(result["legs"][0]["observedAt"], NOW.isoformat())

    def test_targeted_adjustment_refresh_includes_actual_roll_neighborhood(self):
        r, s, b = linked()
        with patch("strangle_runner.utcnow", return_value=NOW):
            symbols = _required_symbols(s, r, b)
        self.assertIn("NFO:NIFTY5024900CE", symbols)
        self.assertIn("NFO:NIFTY5025000CE", symbols)
        self.assertIn("NFO:NIFTY5023300PE", symbols)
        self.assertLess(len(symbols), 20)

    def test_margin_reuse_requires_exact_basket_not_old_prices_or_candidate_id(self):
        r, s, b = linked()
        from test_strangle_engine import adjustment
        before = adjustment(r, s, b)
        self.client.basket_margin.return_value = {"final": {"total": 1000}}
        _margin(self.client, before)
        q = s["quotes"]["NFO:NIFTY5024200PE"]
        q["depth"]["buy"][0]["price"] += .01
        q["depth"]["sell"][0]["price"] += .01
        after = adjustment(r, s, b)
        _carry_margins(before, after)
        self.assertNotEqual(before["strategyPnlRupees"], after["strategyPnlRupees"])
        self.assertEqual(after["margin"]["status"], "available")
        self.assertTrue(any(c["action"] == "roll" for c in after["candidates"]))
        changed = copy.deepcopy(after)
        changed["legs"][0]["quantity"] *= 2
        changed["margin"] = {"status": "unavailable"}
        _carry_margins(before, changed)
        self.assertEqual(changed["margin"]["status"], "unavailable")
        self.assertFalse(any(c["action"] == "roll" for c in changed["candidates"]))


if __name__ == "__main__":
    unittest.main()
