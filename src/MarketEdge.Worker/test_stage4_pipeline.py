"""Stage 4 orchestration tests without SQL Server, queues, or market-data requests."""
import base64
import json
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from db import update_job_status
from ingestion_runner import _build_args, run_bars_refresh_inline
from job_stages import StageTracker
from stage4_runner import _freeze_universe, run_stage4_job
from stage4_store import completed_history, fail_expired_claim, save_result, selection_signature
from test_stage4_analysis import weekly_frame

WEEK = "2026-W47"
CAPTURED = datetime(2026, 11, 21, 20)
STOCK = {"symbol": "BEAR", "company_name": "Bear", "sector_id": 1, "sector_name": "Tech", "market_cap": 500}


class Stage4PipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        self.conn = MagicMock()
        self.conn.cursor.return_value.execute.return_value.fetchone.return_value = SimpleNamespace(Status="running")
        for name in (
            "get_connection", "load_run", "claim_run", "save_parameters", "get_stocks",
            "load_market_cap_from_db", "latest_closed_week", "run_bars_refresh_inline",
            "fetch_benchmark_weekly_from_daily", "load_weekly_price_from_db",
            "completed_history", "save_result", "save_classifications",
            "load_results", "StageTracker", "update_job_status", "fail_expired_claim",
        ):
            self.mocks[name] = self.stack.enter_context(patch(f"stage4_runner.{name}"))
        self.run = {"id": 10, "job_type": "stage4_analysis", "market": "india", "week_number": WEEK,
                    "status": "queued", "parameters": {}}
        self.mocks["get_connection"].return_value = self.conn
        self.mocks["load_run"].return_value = self.run
        self.mocks["claim_run"].return_value = True
        self.mocks["fail_expired_claim"].return_value = False
        self.mocks["get_stocks"].return_value = [dict(STOCK)]
        self.mocks["load_market_cap_from_db"].return_value = 500
        self.mocks["latest_closed_week"].return_value = WEEK
        self.mocks["run_bars_refresh_inline"].return_value = (False, "")
        self.benchmark = weekly_frame(np.full(60, 100.))
        self.prices = weekly_frame(np.arange(260., 200., -1))
        self.mocks["fetch_benchmark_weekly_from_daily"].return_value = self.benchmark
        self.mocks["load_weekly_price_from_db"].return_value = self.prices
        self.mocks["completed_history"].return_value = {}
        self.tracker = self.mocks["StageTracker"].return_value

    def run_job(self):
        return run_stage4_job({"jobType": "stage4_analysis", "runId": 10,
                               "market": self.run["market"], "weekNumber": WEEK})

    def test_both_markets_use_frozen_fno_universe_and_bounded_prices(self):
        for market in ("india", "us"):
            with self.subTest(market=market):
                self.run["market"] = market
                self.run_job()
                self.assertTrue(self.mocks["get_stocks"].call_args.kwargs["fno_only"])
                args = self.mocks["run_bars_refresh_inline"].call_args
                self.assertEqual(args.args, (market,))
                self.assertEqual(args.kwargs["symbols"], ["BEAR"])
                self.assertEqual(args.kwargs["history_days"], 760)
                self.assertGreater(args.kwargs["timeout_seconds"], 0)
                self.assertLessEqual(args.kwargs["timeout_seconds"], 14400)
                self.mocks["load_weekly_price_from_db"].assert_called_with(
                    self.conn, market, "BEAR", end_date=date(2026, 11, 23)
                )
                self.mocks["fetch_benchmark_weekly_from_daily"].assert_called_with(
                    market, end_date=date(2026, 11, 23)
                )
                saved = self.mocks["save_result"].call_args.args[2]
                self.assertTrue(saved["is_stage4"])
                self.assertNotIn("is_stage2", saved)
                self.assertEqual(self.run["parameters"]["universeSymbols"], ["BEAR"])
                self.assertEqual(self.tracker.finish.call_args.kwargs["metrics"]["scope"], "full")

    def test_completed_and_duplicate_deliveries_do_not_refresh_or_write(self):
        for status in ("running", "completed", "failed", "cancelled"):
            self.run["status"] = status
            self.run_job()
        self.mocks["run_bars_refresh_inline"].assert_not_called()
        self.mocks["save_result"].assert_not_called()

    def test_atomic_claim_loss_does_not_process(self):
        self.mocks["claim_run"].return_value = False
        self.run_job()
        self.mocks["get_stocks"].assert_not_called()
        self.tracker.finish.assert_not_called()

    def test_redelivered_abandoned_run_is_failed_without_overwriting_saved_outcomes(self):
        self.run["status"] = "running"
        self.mocks["fail_expired_claim"].return_value = True
        self.run_job()
        conn, run_id, market, now, cutoff = self.mocks["fail_expired_claim"].call_args.args
        self.assertIs(conn, self.conn)
        self.assertEqual((run_id, market), (10, "india"))
        self.assertEqual(now - cutoff, timedelta(seconds=14400))
        self.mocks["claim_run"].assert_not_called()
        self.mocks["save_result"].assert_not_called()
        self.tracker.finish.assert_not_called()

    def test_live_duplicate_claim_is_not_failed_or_restarted(self):
        self.run["status"] = "running"
        self.run_job()
        self.mocks["fail_expired_claim"].assert_called_once()
        self.mocks["claim_run"].assert_not_called()
        self.mocks["update_job_status"].assert_not_called()

    def test_wrong_market_or_week_is_rejected(self):
        with self.assertRaises(ValueError):
            run_stage4_job({"runId": 10, "market": "us", "weekNumber": WEEK})
        with self.assertRaises(ValueError):
            run_stage4_job({"runId": 10, "market": "india", "weekNumber": "2026-W46"})
        self.mocks["claim_run"].assert_not_called()

    def test_zero_eligible_universe_is_explicit_failure(self):
        self.mocks["get_stocks"].return_value = []
        with self.assertRaisesRegex(ValueError, "No F&O stocks"):
            self.run_job()
        self.mocks["update_job_status"].assert_called_once()
        self.assertEqual(self.mocks["update_job_status"].call_args.args[2], "failed")
        self.tracker.finish.assert_not_called()

    def test_missing_benchmark_fails_without_publishing(self):
        self.mocks["fetch_benchmark_weekly_from_daily"].return_value = pd.DataFrame()
        with self.assertRaisesRegex(ValueError, "Benchmark"):
            self.run_job()
        self.tracker.finish.assert_not_called()
        self.mocks["save_result"].assert_not_called()

    def test_refresh_failure_uses_only_valid_existing_data_and_exposes_warning(self):
        self.mocks["run_bars_refresh_inline"].return_value = (True, "Provider throttled")
        self.run_job()
        metrics = self.tracker.finish.call_args.kwargs["metrics"]
        self.assertIn("Provider throttled", metrics["warnings"][0])
        self.assertEqual(metrics["evaluatedStocks"], 1)

    def test_all_stale_or_insufficient_data_is_failure_not_empty_success(self):
        self.mocks["load_weekly_price_from_db"].return_value = self.prices.tail(20)
        with self.assertRaisesRegex(ValueError, "No F&O stocks had"):
            self.run_job()
        result = self.mocks["save_result"].call_args.args[2]
        self.assertIsNone(result["is_stage4"])
        self.assertEqual(result["analysis_status"], "insufficient")
        self.tracker.finish.assert_not_called()

    def test_stock_older_than_benchmark_is_explicitly_stale(self):
        self.prices.attrs["last_bar_date"] = date(2026, 11, 18)
        with self.assertRaisesRegex(ValueError, "No F&O stocks had"):
            self.run_job()
        self.assertEqual(self.mocks["save_result"].call_args.args[2]["analysis_status"], "stale")

    def test_holiday_shortened_week_uses_actual_last_session_date(self):
        for frame in (self.prices, self.benchmark):
            frame.attrs["last_bar_date"] = date(2026, 11, 19)
        self.run_job()
        result = self.mocks["save_result"].call_args.args[2]
        self.assertEqual(result["analysis_status"], "evaluated")
        self.assertEqual(result["price_as_of_date"], date(2026, 11, 19))

    def test_historical_run_does_not_refresh_prices(self):
        self.mocks["latest_closed_week"].return_value = "2026-W48"
        self.run_job()
        self.mocks["run_bars_refresh_inline"].assert_not_called()
        self.tracker.finish.assert_called_once()

    def test_retry_carries_valid_outcomes_and_never_uses_changed_catalog(self):
        self.run["parameters"] = {"retrySourceRunId": 9}
        source = {
            **self.run, "id": 9, "status": "failed",
            "parameters": {"universeSnapshot": [dict(STOCK)],
                           "universeSymbols": ["BEAR"], "eligibilityCapturedAt": CAPTURED.isoformat()},
        }
        carried = {
            **STOCK, "run_id": 9, "week_number": WEEK, "is_stage4": True,
            "analysis_status": "evaluated", "eligibility_captured_at": CAPTURED,
        }
        self.mocks["load_run"].side_effect = [self.run, source]
        self.mocks["load_results"].return_value = [carried]
        self.run_job()
        self.mocks["get_stocks"].assert_not_called()
        self.mocks["load_weekly_price_from_db"].assert_not_called()
        self.mocks["run_bars_refresh_inline"].assert_not_called()
        result = self.mocks["save_result"].call_args.args[2]
        self.assertEqual(result["run_id"], 10)
        self.assertEqual(result["eligibility_captured_at"], CAPTURED)

    def test_retry_unresolved_symbols_are_reevaluated_in_original_scope(self):
        self.run["parameters"] = {"retrySourceRunId": 9}
        source = {
            **self.run, "id": 9, "status": "completed",
            "parameters": {"universeSnapshot": [dict(STOCK)], "eligibilityCapturedAt": CAPTURED.isoformat()},
        }
        self.mocks["load_run"].side_effect = [self.run, source]
        self.mocks["load_results"].return_value = [{"symbol": "BEAR", "analysis_status": "error"}]
        self.run_job()
        self.mocks["get_stocks"].assert_not_called()
        self.mocks["load_weekly_price_from_db"].assert_called_once()

    def test_cancellation_cannot_publish_completed_results(self):
        self.conn.cursor.return_value.execute.return_value.fetchone.return_value = SimpleNamespace(Status="cancelled")
        self.run_job()
        self.tracker.finish.assert_not_called()
        self.mocks["update_job_status"].assert_not_called()

    def test_partial_coverage_finishes_with_unknowns_not_confirmed_exits(self):
        self.mocks["get_stocks"].return_value = [dict(STOCK), {**STOCK, "symbol": "STALE"}]
        self.mocks["load_weekly_price_from_db"].side_effect = [self.prices, self.prices.iloc[:-1]]
        self.run_job()
        metrics = self.tracker.finish.call_args.kwargs["metrics"]
        self.assertEqual(metrics["eligibleStocks"], 2)
        self.assertEqual(metrics["evaluatedStocks"], 1)
        self.assertEqual(metrics["skippedStocks"], 1)
        self.assertTrue(metrics["warnings"])

    def test_market_cap_filter_precedes_limit(self):
        self.mocks["get_stocks"].return_value = [{**STOCK, "symbol": s} for s in ("SMALL", "BIG")]
        self.mocks["load_market_cap_from_db"].side_effect = [100, 500]
        selected = _freeze_universe(self.conn, "us", {"limit": 1, "minMarketCap": 400})
        self.assertEqual([s["symbol"] for s in selected], ["BIG"])
        self.assertIsNone(self.mocks["get_stocks"].call_args.kwargs["limit"])

    def test_missing_market_caps_are_visible_when_filtering_the_universe(self):
        self.mocks["get_stocks"].return_value = [dict(STOCK), {**STOCK, "symbol": "MISSING"}]
        self.mocks["load_market_cap_from_db"].side_effect = [500, None]
        warnings = []
        selected = _freeze_universe(self.conn, "us", {"minMarketCap": 400}, warnings)
        self.assertEqual(len(selected), 1)
        self.assertIn("1 F&O stocks excluded", warnings[0])


class Stage4PersistenceTests(unittest.TestCase):
    def test_expired_claim_failure_is_atomic_and_preserves_live_or_finished_runs(self):
        now = datetime(2026, 11, 21, 20)
        cutoff = now - timedelta(hours=4)
        for changed in (False, True):
            conn = MagicMock()
            conn.cursor.return_value.execute.return_value.fetchone.return_value = SimpleNamespace(Id=10) if changed else None
            self.assertEqual(fail_expired_claim(conn, 10, "us", now, cutoff), changed)
            sql, *parameters = conn.cursor.return_value.execute.call_args.args
            self.assertIn("Status = 'failed'", sql)
            self.assertIn("Status = 'running'", sql)
            self.assertIn("JobType = 'stage4_analysis'", sql)
            self.assertIn("StartedAt IS NULL OR StartedAt <= ?", sql)
            self.assertEqual(parameters[0], now)
            self.assertEqual(parameters[2:], [10, "us", cutoff])
            conn.commit.assert_called_once()

    def test_equivalent_selection_defaults_and_sector_order_match(self):
        self.assertEqual(selection_signature({}), selection_signature({"sectorIds": [], "testSampleOnly": False}))
        self.assertEqual(selection_signature({"sectorIds": [2, 1, 2]}), selection_signature({"sectorIds": [1, 2]}))

    def test_new_result_table_and_all_unknowns_remain_nullable(self):
        conn = MagicMock()
        save_result(conn, "us", {
            "run_id": 1, "week_number": WEEK, **STOCK, "analysis_status": "stale",
            "is_stage4": None, "eligibility_captured_at": CAPTURED,
        })
        sql, *params = conn.cursor.return_value.execute.call_args.args
        self.assertIn("INSERT INTO dbo.USStage4AnalysisResults", sql)
        self.assertIn("[MA30FourWeeksAgo]", sql)
        self.assertIn(None, params)
        with self.assertRaises(ValueError):
            save_result(conn, "invalid", {"symbol": "BEAR"})

    def test_history_does_not_mix_different_filters_or_newer_failed_runs(self):
        conn = MagicMock()
        params = {"scope": "filtered", "sectorIds": [1]}
        conn.cursor.return_value.execute.return_value.fetchall.return_value = [
            SimpleNamespace(Id=8, WeekNumber="2026-W46", Parameters=json.dumps({"scope": "filtered", "sectorIds": [2]})),
            SimpleNamespace(Id=7, WeekNumber="2026-W46", Parameters=json.dumps(params)),
            SimpleNamespace(Id=6, WeekNumber="2026-W46", Parameters=json.dumps(params)),
        ]
        with patch("stage4_store.load_results", return_value=[{"symbol": "BEAR", "is_stage4": True}]) as results:
            history = completed_history(conn, "us", WEEK, params)
        self.assertEqual(list(history), ["2026-W46"])
        results.assert_called_once_with(conn, "us", 7)
        self.assertIn("Status = 'completed'", conn.cursor.return_value.execute.call_args.args[0])

    def test_stage4_progress_guards_terminal_job_status_without_changing_legacy_default(self):
        conn = MagicMock()
        update_job_status(conn, 10, "running", active_only=True)
        self.assertIn("AND Status IN ('queued', 'running')", conn.cursor.return_value.execute.call_args.args[0])
        update_job_status(conn, 10, "running")
        self.assertNotIn("AND Status", conn.cursor.return_value.execute.call_args.args[0])
        with patch("job_stages.update_job_status") as update:
            StageTracker(conn, 10, [("analyze",)], preserve_terminal_status=True).finish()
        self.assertTrue(update.call_args.kwargs["active_only"])


class Stage4IngestionWiringTests(unittest.TestCase):
    def test_scoped_history_argument_does_not_change_legacy_invocation(self):
        standard = _build_args("cli.py", "bars", "us", {"symbols": ["BEAR"]})
        self.assertNotIn("--history-days", standard)
        extended = _build_args("cli.py", "bars", "us", {"symbols": ["BEAR"], "historyDays": 760})
        self.assertEqual(extended[-2:], ["--history-days", "760"])
        with patch("ingestion_runner._resolve_cli", return_value="C:\\code\\cli.py"), patch(
            "ingestion_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
        ) as process:
            failed, _ = run_bars_refresh_inline("us", symbols=["BEAR"], history_days=760)
        self.assertFalse(failed)
        args = process.call_args.args[0]
        self.assertEqual(args[args.index("--symbols") + 1], "BEAR")
        self.assertEqual(args[args.index("--history-days") + 1], "760")

    def test_refresh_timeout_respects_remaining_job_budget(self):
        with patch("ingestion_runner._resolve_cli", return_value="C:\\code\\cli.py"), patch(
            "ingestion_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
        ) as process:
            run_bars_refresh_inline("us", symbols=["BEAR"], history_days=760, timeout_seconds=30)
        self.assertEqual(process.call_args.kwargs["timeout"], 30)

    def test_explicit_queue_dispatch_cannot_fall_into_stage2(self):
        from worker import process_message
        payload = {"jobType": "stage4_analysis", "runId": 10, "market": "us", "weekNumber": WEEK}
        with patch("stage4_runner.run_stage4_job") as runner, patch("worker.get_connection") as stage2_db:
            process_message(base64.b64encode(json.dumps(payload).encode()).decode())
        runner.assert_called_once_with(payload)
        stage2_db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
