"""Scoped extended-history ingestion must not change existing refresh defaults."""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

import cli
import fetch
from config import Config


class Stage4HistoryIngestionTests(unittest.TestCase):
    def test_fetch_period_override_is_optional_and_default_remains_unchanged(self):
        frame = pd.DataFrame({"Close": [10.]}, index=pd.DatetimeIndex([date.today()]))
        for period in (None, "2y"):
            with patch("fetch.yf.download", return_value=frame) as download:
                fetch.fetch_daily_bars(["BEAR"], "us", period=period)
            self.assertEqual(download.call_args.kwargs["period"], period or Config.DAILY_LOOKBACK_PERIOD)

    def test_scoped_two_year_ingest_retains_old_bars_and_does_not_prune_other_stocks(self):
        day = date.today() - timedelta(days=500)
        frame = pd.DataFrame({"Open": [10.], "High": [11.], "Low": [9.], "Close": [10.],
                              "Volume": [100], "Adj Close": [10.]}, index=pd.DatetimeIndex([day]))
        args = SimpleNamespace(market="us", limit=None, test_sample=False, sectors=None,
                               symbols="BEAR", history_days=760, missing=False, force=False)
        with patch("cli.db.get_connection", return_value=MagicMock()), patch(
            "cli._resolve_universe", return_value=[{"symbol": "BEAR"}]
        ), patch("cli._seed"), patch("cli.fetch.fetch_daily_bars", return_value={"BEAR": frame}) as download, patch(
            "cli.db.upsert_bars", return_value=1
        ) as upsert, patch("cli.db.prune_old_bars") as prune, patch(
            "cli.db.refresh_bars_available"
        ), patch("cli._progress_reporter"):
            self.assertEqual(cli.cmd_ingest_bars(args), 0)
        download.assert_called_once_with(["^GSPC", "BEAR"], "us", period="2y")
        self.assertEqual(upsert.call_args.args[2][0][1], day)
        prune.assert_not_called()

    def test_legacy_ingest_keeps_existing_fetch_and_retention_behavior(self):
        args = SimpleNamespace(market="us", limit=None, test_sample=False, sectors=None,
                               symbols="BEAR", missing=False, force=False)
        with patch("cli.db.get_connection", return_value=MagicMock()), patch(
            "cli._resolve_universe", return_value=[{"symbol": "BEAR"}]
        ), patch("cli._seed"), patch("cli.fetch.fetch_daily_bars", return_value={}) as download, patch(
            "cli.db.prune_old_bars", return_value=0
        ) as prune, patch("cli.db.refresh_bars_available"), patch("cli._progress_reporter"):
            self.assertEqual(cli.cmd_ingest_bars(args), 0)
        download.assert_called_once_with(["^GSPC", "BEAR"], "us")
        self.assertEqual(prune.call_args.args[2], date.today() - timedelta(days=Config.DAILY_LOOKBACK_DAYS))

    def test_cli_accepts_history_override_only_on_bars(self):
        args = cli.build_parser().parse_args(["ingest", "bars", "--market", "india", "--history-days", "760"])
        self.assertEqual(args.history_days, 760)
        self.assertIsNone(cli.build_parser().parse_args(["ingest", "bars", "--market", "india"]).history_days)


if __name__ == "__main__":
    unittest.main()
