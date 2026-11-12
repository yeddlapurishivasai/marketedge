"""Offline tests for options credentials, existing fundamentals and sampled outcomes."""
from __future__ import annotations

import base64
import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from options_data import KiteClient, KiteSessionExpired, derivatives_context, flattened_quotes, load_instruments, token_for, last_session_snapshot, observed_time
from options_fundamentals import constituent_context, fundamental_index_range, valuation
from options_runner import _chain_window, _completed_bar_cutoff, _resolve_horizon, _weight_snapshot_evidence, reconcile_forecast, run_options_job, strategy_outcome
from options_runner import _save_outcome

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)


class FakeConnection:
    def __init__(self, record):
        self.record = record
        self.commands = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.rowcount = 0

    def cursor(self):
        return self

    def execute(self, sql, *params):
        self.commands.append((sql, params))
        return self

    def fetchone(self):
        return self.record

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def financial(stock_id=1, **overrides):
    record = {
        "StockId": stock_id, "Symbol": f"STOCK{stock_id}", "CompanyName": f"Stock {stock_id}",
        "BroadSector": "Industrials", "WeightPct": 20, "LatestQuarterEnd": date(2026, 9, 30),
        "EarningsGrowthYoyPct": 15, "TrailingPe": 20,
    }
    for i, day in enumerate((date(2026, 10, 20), date(2026, 7, 20), date(2026, 4, 20), date(2026, 1, 20)), 1):
        record[f"EpsQ{i}Date"] = day
        record[f"EpsQ{i}Actual"] = 2
    return {**record, **overrides}


class LastSessionSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 2, tzinfo=timezone.utc)
        self.sessions = [
            {"sessionDate": "2026-11-09", "isTradingDay": True, "openTime": "09:15", "closeTime": "15:30"},
            {"sessionDate": "2026-11-10", "isTradingDay": False, "openTime": None, "closeTime": None},
        ]
        self.conn = MagicMock()
        self.spot = {"instrument_token": 1, "last_price": 25000, "timestamp": "2026-11-09T12:05:06Z"}

    def test_epoch_placeholder_is_missing_not_a_real_observation(self):
        for timestamp in ("1970-01-01T05:30:00+05:30", "1970-01-01T00:00:00Z", None):
            self.assertIsNone(observed_time({"timestamp": timestamp}))
        self.assertIsNotNone(observed_time(self.spot))
        quote = dict(self.spot, last_trade_time="1970-01-01T00:00:00Z")
        flattened = flattened_quotes([{"exchange": "NFO", "tradingSymbol": "CALL"}], {"NFO:CALL": quote})
        self.assertIsNone(flattened[0]["lastTradeAt"])

    def test_restore_one_scoped_batch_and_preserve_original_observation(self):
        records = [{"Exchange": "NSE", "TradingSymbol": "NIFTY 50", "PayloadJson": json.dumps(self.spot)},
                   {"Exchange": "NFO", "TradingSymbol": "INVALID", "PayloadJson": '{"timestamp":"1970-01-01T00:00:00Z","last_price":10}'}]
        with patch("options_data.rows", side_effect=[
                [{"Id": 7, "CapturedAt": datetime(2026, 11, 9, 19)}], records]):
            result = last_session_snapshot(self.conn, "alice", "NIFTY50", "NSE:NIFTY 50",
                                           self.sessions, self.now, "weekly")
        self.assertEqual(result["batchId"], 7)
        self.assertEqual(result["quotes"], {"NSE:NIFTY 50": self.spot})
        sql, *parameters = self.conn.cursor.return_value.execute.call_args_list[0].args
        self.assertIn("b.UserKey=? AND b.IndexCode=?", sql)
        self.assertEqual(parameters[:3], ["alice", "NIFTY50", "NSE:NIFTY 50"])
        self.assertIn("q.ObservedAt>=? AND q.ObservedAt<=?", sql)

    def test_never_restore_for_live_entry_intraday_or_calendar_gap(self):
        live = self.sessions + [{"sessionDate": "2026-11-11", "isTradingDay": True,
                                 "openTime": "09:15", "closeTime": "15:30"}]
        for sessions, now, mode in (
                (live, datetime(2026, 11, 11, 8, tzinfo=timezone.utc), "weekly"),
                (self.sessions, self.now, "expiry-intraday"),
                (self.sessions[:1], self.now, "weekly")):
            self.assertIsNone(last_session_snapshot(self.conn, "alice", "NIFTY50", "NSE:NIFTY 50",
                                                    sessions, now, mode))
        self.conn.cursor.assert_not_called()

    def test_no_batch_is_explicit_and_corrupt_source_timestamps_are_rejected(self):
        with patch("options_data.rows", return_value=[]):
            self.assertIsNone(last_session_snapshot(self.conn, "alice", "NIFTY50", "NSE:NIFTY 50",
                                                    self.sessions, self.now, "daily"))
        with patch("options_data.rows", side_effect=[
                [{"Id": 7, "CapturedAt": datetime(2026, 11, 9, 19)}],
                [{"Exchange": "NSE", "TradingSymbol": "NIFTY 50", "PayloadJson": '{"last_price":25000,"timestamp":"1970-01-01T00:00:00Z"}'}]]):
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                last_session_snapshot(self.conn, "alice", "NIFTY50", "NSE:NIFTY 50",
                                      self.sessions, self.now, "daily")


class InstrumentMasterTests(unittest.TestCase):
    def test_persisted_weight_provenance_survives_source_outage_and_manual_overrides(self):
        source = "auto:ETF_PROXY;https://issuer.example/portfolio.xls"
        records = [{"WeightEffectiveDate": date(2026, 10, 31), "Source": source}]
        evidence = _weight_snapshot_evidence(records, {"status": "unavailable"})
        self.assertEqual(evidence["sourceType"], "etf-holdings-proxy")
        self.assertEqual(evidence["sourceDate"], "2026-10-31")
        self.assertEqual(evidence["sourceUrl"], "https://issuer.example/portfolio.xls")
        self.assertIn("not official or live", evidence["method"])
        current = {"sourceDate": "2026-10-31", "sourceType": "etf-holdings-proxy", "rawEquityNavPct": 99.95}
        self.assertEqual(_weight_snapshot_evidence(records, current), current)
        records[0]["Source"] = "Administrator verified snapshot"
        self.assertEqual(_weight_snapshot_evidence(records, current)["sourceType"],
                         "effective-dated-constituent-snapshot")

    def test_completed_bar_cutoff_uses_actual_close_and_publication_grace(self):
        sessions = [{"sessionDate": "2026-11-09", "isTradingDay": True, "closeTime": "15:30"}]
        self.assertEqual(_completed_bar_cutoff(datetime(2026, 11, 9, 10, 14, tzinfo=timezone.utc), sessions),
                         date(2026, 11, 8))
        self.assertEqual(_completed_bar_cutoff(datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc), sessions),
                         date(2026, 11, 9))
        sessions[0]["isTradingDay"] = False
        self.assertEqual(_completed_bar_cutoff(datetime(2026, 11, 9, 16, tzinfo=timezone.utc), sessions),
                         date(2026, 11, 8))

    def test_completed_bars_include_today_only_when_session_completion_is_supplied(self):
        client = MagicMock()
        client.get.return_value = {"candles": [
            ["2026-11-06T00:00:00+0530", 1, 3, 1, 2, 0],
            ["2026-11-09T00:00:00+0530", 2, 4, 1, 3, 0],
        ]}
        self.assertEqual([b["date"] for b in KiteClient.bars(client, 1, NOW)], ["2026-11-06"])
        self.assertEqual([b["date"] for b in KiteClient.bars(
            client, 1, NOW, completed_through=date(2026, 11, 9))], ["2026-11-06", "2026-11-09"])
        with self.assertRaisesRegex(ValueError, "future"):
            KiteClient.bars(client, 1, NOW, completed_through=date(2026, 11, 10))

    def test_daily_refresh_updates_broker_expiry_and_reads_only_latest_master(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.execute.return_value = cursor
        cursor.fetchone.return_value = (None,)
        client = MagicMock()
        client.get.return_value = (
            "exchange,tradingsymbol,instrument_token,name,expiry,strike,instrument_type,lot_size,tick_size\n"
            "NFO,NIFTY26NOV25000CE,1,NIFTY,2026-11-17,25000,CE,75,0.05\n"
        )
        record = {"InstrumentToken": 1, "Exchange": "NFO", "TradingSymbol": "NIFTY26NOV25000CE",
                  "Underlying": "NIFTY50", "Expiry": date(2026, 11, 17), "Strike": 25000,
                  "InstrumentType": "CE", "LotSize": 75, "TickSize": .05}
        with patch("options_data.rows", return_value=[record]), patch("options_data.utcnow", return_value=NOW):
            result = load_instruments(conn, client, "NIFTY50")
        self.assertEqual(result[0]["expiry"], "2026-11-17")
        client.get.assert_called_once_with("/instruments", text=True)
        merge_sql, records = cursor.executemany.call_args.args
        self.assertIn("Expiry=s.Expiry", merge_sql)
        self.assertEqual(records[0][4], "2026-11-17")
        select_sql, *parameters = cursor.execute.call_args.args
        self.assertIn("UpdatedAt=(SELECT MAX(UpdatedAt)", select_sql)
        self.assertEqual(parameters, ["NIFTY50", "NIFTY50"])

    def test_same_day_master_is_reused_without_another_download(self):
        conn = FakeConnection((NOW.replace(tzinfo=None),))
        client = MagicMock()
        with patch("options_data.rows", return_value=[]), patch("options_data.utcnow", return_value=NOW):
            self.assertEqual(load_instruments(conn, client, "SENSEX"), [])
        client.get.assert_not_called()


def candidate():
    return {
        "legs": [
            {"exchange": "NFO", "tradingSymbol": "SHORT", "direction": "sell", "quantity": 10, "entryPrice": 100},
            {"exchange": "NFO", "tradingSymbol": "HEDGE", "direction": "buy", "quantity": 10, "entryPrice": 50},
        ],
        "technicalStop": {"lower": 95, "upper": None}, "costEstimate": 20,
        "targetProfit": 230, "plannedExitAt": (NOW + timedelta(hours=1)).isoformat(), "asOf": NOW.isoformat(),
    }


def observation(seconds=20, spot=100, short=60, hedge=30):
    at = NOW + timedelta(seconds=seconds)
    return {
        "at": at.isoformat(), "spot": spot,
        "quotes": {
            "NFO:SHORT": {"bid": short - 1, "ask": short, "observedAt": at.isoformat()},
            "NFO:HEDGE": {"bid": hedge, "ask": hedge + 1, "observedAt": at.isoformat()},
        },
    }


class CredentialTests(unittest.TestCase):
    def test_shared_dotnet_envelope_and_aad(self):
        key, nonce, user = bytes(range(32)), bytes(range(12)), "test-account"
        encrypted = AESGCM(key).encrypt(nonce, b"example-access-token", user.encode())
        envelope = base64.b64encode(nonce + encrypted[-16:] + encrypted[:-16]).decode()
        conn = FakeConnection(SimpleNamespace(
            EncryptedToken=envelope, ExpiresAt=NOW.replace(tzinfo=None) + timedelta(days=1), Status="connected",
        ))
        with patch.dict(os.environ, {"MARKETEDGE_OPTIONS_TOKEN_KEY": base64.b64encode(key).decode()}), \
                patch("options_data.utcnow", return_value=NOW):
            self.assertEqual(token_for(conn, user), "example-access-token")
            with self.assertRaises(Exception):
                token_for(conn, "different-account")

    def test_expiry_erases_credential_and_requires_login(self):
        conn = FakeConnection(SimpleNamespace(EncryptedToken="not-used", ExpiresAt=NOW.replace(tzinfo=None), Status="connected"))
        with patch("options_data.utcnow", return_value=NOW):
            with self.assertRaises(KiteSessionExpired):
                token_for(conn, "account")
        self.assertIn("relogin-required", conn.commands[-1][0])
        self.assertIn("EncryptedToken = NULL", conn.commands[-1][0])
        self.assertEqual(conn.commits, 1)

    def test_disconnected_has_no_success_fallback(self):
        with self.assertRaises(KiteSessionExpired):
            token_for(FakeConnection(None), "account")


class FundamentalTests(unittest.TestCase):
    def test_current_quarter_stock_ai_is_reused_before_mathematical_fallback(self):
        record = financial(AiAsOf=NOW.replace(tzinfo=None), AiQuarterEnd="2026-09-30",
                           AiFairLow=90, AiFairHigh=130, AiModel="stored-model",
                           AiPriceScenarios='[{"horizon":"3m","bear":{"target":90},"base":{"target":110},"bull":{"target":140}}]')
        result = valuation(record, [], {"last_price": 100}, NOW.date())
        self.assertEqual(result["method"], "stored-stock-AI-valuation")
        self.assertEqual(result["fairBase"], 110)
        self.assertEqual(result["baseMovePct"], 10)
        self.assertEqual(result["aiPriceForecasts"][0]["horizon"], "3m")
        self.assertIn("not a new inference", result["reason"])

    def test_old_quarter_ai_cannot_override_the_current_results_model(self):
        peers = [financial(i, TrailingPe=20) for i in range(1, 5)]
        record = dict(peers[0], AiAsOf=NOW.replace(tzinfo=None), AiQuarterEnd="2026-06-30",
                      AiFairLow=9000, AiFairHigh=10000)
        result = valuation(record, peers, {"last_price": 100}, NOW.date())
        self.assertEqual(result["method"], "trailing-EPS/sector-peer-PE")
        self.assertEqual(result["fairBase"], 160)

    def test_ai_before_results_announcement_cannot_claim_to_include_those_results(self):
        record = financial(AiAsOf=datetime(2026, 10, 19), AiQuarterEnd="2026-09-30",
                           LastEarningsDate=date(2026, 10, 20), AiFairLow=90, AiFairHigh=130)
        result = valuation(record, [], {"last_price": 100}, NOW.date())
        self.assertNotEqual(result["method"], "stored-stock-AI-valuation")
        self.assertIsNone(result["fairBase"])

    def test_banks_can_reuse_dated_post_results_analyst_targets_not_a_fake_pe_model(self):
        record = financial(BroadSector="Banking", LastEarningsDate=date(2026, 10, 20),
                           AnalystAsOf=date(2026, 11, 6), NumAnalysts=12,
                           TargetLowPrice=80, TargetMeanPrice=110, TargetHighPrice=150)
        result = valuation(record, [], {"last_price": 100}, NOW.date())
        self.assertEqual(result["method"], "stored-analyst-12m-targets")
        self.assertEqual(result["analystCount"], 12)
        self.assertIn("Twelve-month", result["reason"])
        record["AnalystAsOf"] = date(2026, 10, 19)
        self.assertIsNone(valuation(record, [], {"last_price": 100}, NOW.date())["fairBase"])

    def test_index_range_sums_individual_weighted_impacts_without_extrapolating_missing_values(self):
        values = [
            {"stockId": 1, "weightPct": 60, "livePrice": 100, "fairLow": 90, "fairBase": 110,
             "fairHigh": 130, "method": "stored-stock-AI-valuation"},
            {"stockId": 2, "weightPct": 40, "livePrice": 200, "fairLow": None, "fairBase": None,
             "fairHigh": None, "method": "unavailable"},
        ]
        result = fundamental_index_range(25000, values)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"], .6)
        self.assertEqual(result["aiCoverage"], .6)
        self.assertEqual(result["uncoveredWeightPct"], 40)
        self.assertAlmostEqual(result["low"], 23500)
        self.assertAlmostEqual(result["base"], 26500)
        self.assertAlmostEqual(result["high"], 29500)
        self.assertAlmostEqual(values[0]["indexContributions"]["base"], 1500)
        self.assertEqual(result["horizon"], "structural-not-run-target")
        self.assertTrue(result["sensitivityOnly"])
        with self.assertRaisesRegex(ValueError, "100%"):
            fundamental_index_range(25000, values[:1])

    def test_sector_peer_range_and_signed_moves(self):
        peers = [financial(i, TrailingPe=10 + i * 5) for i in range(1, 5)]
        result = valuation(peers[0], peers, {"last_price": 400}, NOW.date())
        self.assertEqual(result["method"], "trailing-EPS/sector-peer-PE")
        self.assertEqual(result["fairBase"], 200)
        self.assertEqual(result["baseMovePct"], -50)
        self.assertIn("unadjusted", result["reason"])

    def test_bank_missing_book_value_is_unavailable(self):
        result = valuation(financial(BroadSector="Banking"), [], {"last_price": 100}, NOW.date())
        self.assertIsNone(result["fairBase"])
        self.assertIn("book value", result["reason"])

    def test_duplicate_quarters_and_loss_making_unavailable(self):
        peers = [financial(i) for i in range(1, 5)]
        for invalid in (
            financial(EpsQ4Date=date(2026, 4, 20)),
            financial(EpsQ1Actual=-100),
        ):
            result = valuation(invalid, peers, {"last_price": 100}, NOW.date())
            self.assertIsNone(result["fairBase"])
            self.assertIn("Four distinct", result["reason"])

    def test_missing_data_not_neutral_score(self):
        values, dimensions = constituent_context([], {}, NOW.date())
        self.assertEqual(values, [])
        self.assertTrue(all(dim["score"] is None and dim["coverage"] == 0 for dim in dimensions))

    def test_weighted_coverage_not_count(self):
        records = [financial(1, WeightPct=70), financial(2, WeightPct=30, EarningsGrowthYoyPct=None)]
        quotes = {"NSE:STOCK1": {"last_price": 105, "ohlc": {"close": 100}}}
        _, dimensions = constituent_context(records, quotes, NOW.date())
        self.assertEqual(dimensions[0]["coverage"], 0.35)
        self.assertEqual(dimensions[1]["coverage"], 0.7)
        self.assertGreater(dimensions[0]["score"], 50)
        self.assertNotIn("score", dimensions[0]["indicators"][0])


class ReconciliationTests(unittest.TestCase):
    def test_late_delivery_for_deleted_history_preserves_terminal_job_audit(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.execute.return_value = cursor
        cursor.fetchone.side_effect = [None, SimpleNamespace(Status="completed")]
        with patch("options_runner.get_connection", return_value=conn), \
                patch("options_runner.rows", return_value=[]), \
                patch("options_runner.KiteClient") as broker, \
                self.assertLogs("options_runner", level="INFO") as logs:
            run_options_job({"forecastRunId": 8, "runId": 12, "userKey": "alice"})
        self.assertIn("late delivery", logs.output[0])
        self.assertTrue(all("UPDATE" not in call.args[0] for call in cursor.execute.call_args_list))
        broker.assert_not_called()
        conn.close.assert_called_once()

    def test_outcome_write_locks_parent_before_allocating_version(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.execute.return_value = cursor
        cursor.fetchone.side_effect = [SimpleNamespace(Id=8), None]
        _save_outcome(conn, "ForecastOutcomes", "ForecastRunId", 8, {"status": "pending"})
        commands = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("ForecastRuns] WITH (UPDLOCK,HOLDLOCK)", commands[0])
        self.assertIn("SELECT TOP 1 Version", commands[1])
        self.assertIn("INSERT INTO", commands[2])

    def test_deleted_parent_never_gets_a_new_outcome(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.execute.return_value = cursor
        cursor.fetchone.return_value = None
        with self.assertLogs("options_runner", level="INFO") as logs:
            _save_outcome(conn, "ForecastOutcomes", "ForecastRunId", 8, {"status": "pending"})
        self.assertIn("deleted forecast", logs.output[0])
        self.assertEqual(cursor.execute.call_count, 1)

    def test_gross_reconciliation_is_never_reported_as_net_when_charges_are_unknown(self):
        outcome = {
            "status": "completed", "at": NOW.isoformat(), "trigger": "time",
            "pnl": None, "grossPnl": 123.45, "payoffBasis": "gross-before-charges",
            "netPnlReason": "Charges unavailable",
        }
        with patch("options_engine.reconcile_strategy", return_value=outcome):
            result = strategy_outcome({"plannedExitAt": NOW.isoformat()}, [], NOW, NOW)
        self.assertIsNone(result["netHypotheticalPnl"])
        self.assertEqual(result["grossHypotheticalPnl"], 123.45)
        self.assertEqual(result["payoffBasis"], "gross-before-charges")
        self.assertEqual(result["status"], "partial")

    def test_duplicate_queue_delivery_does_not_restart_running_forecast(self):
        conn = FakeConnection(None)
        with patch("options_runner.get_connection", return_value=conn), \
                patch("options_runner.rows", return_value=[{"Status": "running"}]), \
                patch("options_runner.KiteClient") as broker:
            run_options_job({"forecastRunId": 1, "runId": 2, "userKey": "alice"})
        broker.assert_not_called()
        self.assertTrue(conn.closed)
        self.assertEqual(len(conn.commands), 1)

    def test_only_one_worker_can_claim_a_queued_forecast(self):
        conn = FakeConnection(SimpleNamespace(Status="queued"))
        with patch("options_runner.get_connection", return_value=conn), \
                patch("options_runner.rows", return_value=[{"Status": "queued"}]), \
                patch("options_runner.KiteClient") as broker:
            run_options_job({"forecastRunId": 1, "runId": 2, "userKey": "alice"})
        broker.assert_not_called()
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(any("AND Status='queued'" in sql for sql, _ in conn.commands))
        self.assertFalse(any("INSERT" in sql for sql, _ in conn.commands))

    def test_intraday_cutoffs_are_configurable_and_ordered(self):
        from test_options_engine import NOW as ENGINE_NOW, fixture

        sessions, instruments, _, _, _ = fixture()
        with patch.dict(os.environ, {
            "MARKETEDGE_OPTIONS_ENTRY_MINUTES_BEFORE_CLOSE": "45",
            "MARKETEDGE_OPTIONS_EXIT_MINUTES_BEFORE_CLOSE": "20",
        }):
            horizon = _resolve_horizon(ENGINE_NOW, "expiry-intraday", sessions, instruments)
        self.assertIn("09:15:00", horizon["entryCutoffAt"])
        self.assertIn("09:40:00", horizon["plannedExitAt"])
        with patch.dict(os.environ, {
            "MARKETEDGE_OPTIONS_ENTRY_MINUTES_BEFORE_CLOSE": "15",
            "MARKETEDGE_OPTIONS_EXIT_MINUTES_BEFORE_CLOSE": "30",
        }):
            with self.assertRaises(ValueError):
                _resolve_horizon(ENGINE_NOW, "expiry-intraday", sessions, instruments)

    def test_completed_terminal_close_survives_offline_reconciliation(self):
        import json

        conn = FakeConnection(SimpleNamespace(PayloadJson=json.dumps({
            "terminalSource": "kite-daily-candle", "actualClose": 100.05,
        })))
        analysis = {
            "spot": 100, "bias": "neutral", "directionNeutralBandPct": 0.1,
            "scenarios": [{"id": "base", "low": 99, "high": 101}],
        }
        forecast = {
            "Id": 1, "IndexCode": "NIFTY50", "TargetAt": NOW.replace(tzinfo=None),
            "AnalysisJson": json.dumps(analysis),
        }
        saved = []
        with patch("options_runner._index", return_value={"SpotSymbol": "NSE:NIFTY 50"}), \
                patch("options_runner._observations", return_value=[]), \
                patch("options_runner.rows", return_value=[]), \
                patch("options_runner._save_outcome", side_effect=lambda *args: saved.append(args[-1])):
            reconcile_forecast(conn, forecast, NOW + timedelta(hours=1))
        self.assertEqual(saved[0]["status"], "completed")
        self.assertEqual(saved[0]["actualClose"], 100.05)
        self.assertEqual(saved[0]["actualDirection"], "neutral")
        self.assertTrue(saved[0]["directionCorrect"])
        self.assertEqual(saved[0]["pathStatus"], "partial")

    def test_unavailable_prediction_is_not_counted_as_a_wrong_direction_or_range(self):
        import json

        conn = FakeConnection(None)
        forecast = {
            "Id": 2, "IndexCode": "NIFTY50", "TargetAt": NOW.replace(tzinfo=None),
            "AnalysisJson": json.dumps({
                "spot": 100, "bias": "unavailable",
                "scenarios": [{"id": "base", "low": None, "high": None}],
            }),
        }
        saved = []
        with patch("options_runner._index", return_value={"SpotSymbol": "NSE:NIFTY 50"}), \
                patch("options_runner._observations", return_value=[]), \
                patch("options_runner.rows", return_value=[]), \
                patch("options_runner._save_outcome", side_effect=lambda *args: saved.append(args[-1])):
            reconcile_forecast(conn, forecast, NOW + timedelta(hours=1), official_close=102)
        self.assertIsNone(saved[0]["directionCorrect"])
        self.assertIsNone(saved[0]["scenarios"][0]["terminalCovered"])

    def test_exit_uses_bid_for_long_and_ask_for_short_and_costs(self):
        result = strategy_outcome(candidate(), [observation(spot=94, short=120, hedge=60)],
                                  NOW + timedelta(hours=1), NOW + timedelta(minutes=1))
        self.assertEqual(result["reason"], "technical-stop")
        self.assertEqual(result["netHypotheticalPnl"], -120)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["eventOrderCertain"])

    def test_target_is_observed_not_claimed_fill(self):
        result = strategy_outcome(candidate(), [observation(short=40, hedge=20)],
                                  NOW + timedelta(hours=1), NOW + timedelta(minutes=1))
        self.assertEqual(result["reason"], "premium-target")
        self.assertEqual(result["netHypotheticalPnl"], 280)
        self.assertIn("hypothetical", result["warning"])

    def test_missing_hedge_or_stale_quote_cannot_be_a_win(self):
        point = observation(short=40, hedge=20)
        point["quotes"].pop("NFO:HEDGE")
        result = strategy_outcome(candidate(), [point], NOW + timedelta(hours=1), NOW + timedelta(hours=2))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["netHypotheticalPnl"])
        stale = observation(short=40, hedge=20)
        stale["quotes"]["NFO:HEDGE"]["observedAt"] = (NOW - timedelta(minutes=2)).isoformat()
        self.assertEqual(strategy_outcome(candidate(), [stale], NOW, NOW + timedelta(hours=2))["status"], "unavailable")

    def test_chain_window_preserves_expiry_and_limits_strikes(self):
        instruments = [{
            "expiry": "2026-11-10", "strike": strike, "instrumentType": kind,
        } for strike in range(1000, 10000, 50) for kind in ("CE", "PE")]
        selected = _chain_window(instruments, 5000, ["2026-11-10"])
        self.assertEqual(len(selected), 62)
        self.assertTrue(all(abs(item["strike"] - 5000) <= 750 for item in selected))

    def test_quotes_without_provider_time_are_omitted(self):
        instruments = [{"exchange": "NFO", "tradingSymbol": "CONTRACT"}]
        self.assertEqual(flattened_quotes(instruments, {"NFO:CONTRACT": {"last_price": 10}}), [])

    def test_derivatives_score_is_positioning_not_bullish_probability(self):
        contracts = [
            {"exchange": "NFO", "tradingSymbol": "CALL", "expiry": "2026-11-10", "instrumentType": "CE"},
            {"exchange": "NFO", "tradingSymbol": "PUT", "expiry": "2026-11-10", "instrumentType": "PE"},
        ]
        timestamp = NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")
        dimension = derivatives_context(25000, contracts, {
            "NFO:CALL": {"timestamp": timestamp, "oi": 100},
            "NFO:PUT": {"timestamp": timestamp, "oi": 300},
        }, NOW)
        self.assertEqual(dimension["score"], 75)
        self.assertIn("not bearish to bullish", dimension["scale"])
        self.assertEqual(dimension["indicators"][0]["value"], 3)
        self.assertEqual(dimension["indicators"][0]["contribution"], 0)

    def test_after_hours_oi_retains_last_session_coverage_without_claiming_live_data(self):
        from options_engine import snapshot_policy

        contracts = [
            {"exchange": "NFO", "tradingSymbol": "CALL", "expiry": "2026-11-17", "instrumentType": "CE"},
            {"exchange": "NFO", "tradingSymbol": "PUT", "expiry": "2026-11-17", "instrumentType": "PE"},
        ]
        close, after = NOW.replace(hour=10), NOW.replace(hour=18)
        sessions = [{"sessionDate": "2026-11-09", "isTradingDay": True,
                     "openTime": "09:15", "closeTime": "15:30"}]
        quotes = {"NFO:CALL": {"timestamp": close.isoformat(), "oi": 100},
                  "NFO:PUT": {"timestamp": close.isoformat(), "oi": 300}}
        self.assertEqual(derivatives_context(25000, contracts, quotes, after)["coverage"], 0)
        policy = snapshot_policy(after, sessions, close, "weekly")
        dimension = derivatives_context(25000, contracts, quotes, after, policy)
        self.assertEqual(dimension["coverage"], 1)
        self.assertEqual(dimension["observationBasis"], "last-session")
        self.assertEqual(dimension["score"], 75)
        self.assertEqual(dimension["asOf"], policy["quoteAsOf"])

    def test_planning_outcome_never_reports_gross_or_net_entry_pnl(self):
        plan = dict(candidate(), executionStatus="planning-only")
        result = strategy_outcome(plan, [observation(spot=94, short=120, hedge=60)],
                                  NOW + timedelta(hours=1), NOW + timedelta(hours=2))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["netHypotheticalPnl"])
        self.assertIsNone(result["grossHypotheticalPnl"])


if __name__ == "__main__":
    unittest.main()
