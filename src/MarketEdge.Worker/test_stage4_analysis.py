"""Offline Stage 4 qualification and Stage 2 regression tests."""
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from db import get_stocks
from stage_analysis import (
    calculate_stage2, calculate_stage4, fetch_benchmark_weekly_from_daily,
    load_weekly_price_from_db,
)
from stage4_runner import classify_results, closed_week_end, latest_closed_week, symbol_batches


def weekly_frame(closes, end="2026-11-20"):
    index = pd.date_range(end=end, periods=len(closes), freq="W-FRI")
    frame = pd.DataFrame({
        "Open": np.array(closes) + 1, "High": np.array(closes) + 2,
        "Low": np.array(closes) - 1, "Close": closes, "Volume": 1000.,
    }, index=index)
    frame.attrs["last_bar_date"] = index[-1].date()
    return frame


class Stage4MathTests(unittest.TestCase):
    def setUp(self):
        self.stock = weekly_frame(np.arange(260., 200., -1))
        self.benchmark = weekly_frame(np.full(60, 100.))

    def test_downtrend_qualifies_and_keeps_bearish_indicator_meanings(self):
        result = calculate_stage4(self.stock, self.benchmark)
        self.assertTrue(result["is_stage4"])
        self.assertEqual(result["ma10"], 205.5)
        self.assertEqual(result["ma30"], 215.5)
        self.assertEqual(result["ma30_four_weeks_ago"], 219.5)
        self.assertLess(result["rs_score"], 0)
        self.assertLess(result["momentum_score"], 0)
        self.assertEqual(result["quadrant"], "lagging")
        self.assertEqual(result["ad_classification"], "distributing")
        self.assertNotIn("is_stage2", result)

    def test_each_strict_condition_is_required_including_equality(self):
        base = {
            "close_price": 80., "ma10": 90., "ma30": 100.,
            "ma30_four_weeks_ago": 110., "rs_score": -10.,
        }
        cases = [
            {"close_price": 100.}, {"close_price": 101.},
            {"ma10": 80.}, {"ma10": 79.},
            {"ma10": 100.}, {"ma10": 101.},
            {"ma30_four_weeks_ago": 100.}, {"ma30_four_weeks_ago": 99.},
            {"rs_score": 0.}, {"rs_score": 1.},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), patch(
                "stage_analysis._calculate_weekly_indicators", return_value={**base, **overrides}
            ):
                self.assertFalse(calculate_stage4(self.stock, self.benchmark)["is_stage4"])

    def test_52_aligned_weeks_required(self):
        self.assertIsNone(calculate_stage4(self.stock.tail(51), self.benchmark))
        self.assertIsNone(calculate_stage4(self.stock, self.benchmark.tail(51)))
        shifted = self.benchmark.copy()
        shifted.index -= pd.Timedelta(weeks=10)
        self.assertIsNone(calculate_stage4(self.stock, shifted))
        self.assertIsNotNone(calculate_stage4(self.stock.tail(52), self.benchmark))

    def test_stale_missing_duplicate_and_nonfinite_data_do_not_mean_not_stage4(self):
        for value in (np.nan, np.inf, -np.inf, 0., -1.):
            for column in ("Close",):
                broken = self.stock.copy()
                broken.loc[broken.index[-1], column] = value
                with self.subTest(value=value):
                    self.assertIsNone(calculate_stage4(broken, self.benchmark))
        for column in ("Open", "Volume"):
            broken = self.stock.copy()
            broken.loc[broken.index[-1], column] = np.nan
            self.assertIsNone(calculate_stage4(broken, self.benchmark))
        self.assertIsNone(calculate_stage4(self.stock, self.benchmark.iloc[:-1]))
        self.assertIsNone(calculate_stage4(pd.concat([self.stock, self.stock.tail(1)]), self.benchmark))
        self.assertIsNone(calculate_stage4(None, self.benchmark))

    def test_stage2_original_output_and_formula_regression(self):
        stock = weekly_frame(np.arange(41., 101.))
        result = calculate_stage2(stock, self.benchmark)
        self.assertTrue(result["is_stage2"])
        self.assertEqual(result["close_price"], 100.)
        self.assertEqual(result["ma10"], 95.5)
        self.assertEqual(result["ma30"], 85.5)
        self.assertAlmostEqual(result["rs_score"], (100 / 74.5 - 1) * 100)
        self.assertAlmostEqual(result["rs_1w"], .99)
        self.assertAlmostEqual(result["rs_2w"], .98)
        self.assertAlmostEqual(result["rs_3w"], .97)
        self.assertEqual(result["quadrant"], "leading")
        self.assertEqual(set(result), {
            "close_price", "ma10", "ma30", "is_stage2", "rs_score", "rs_1w", "rs_2w", "rs_3w",
            "rs_delta_1w", "rs_delta_2w", "rs_delta_3w", "momentum_score",
            "roc_1w", "roc_2w", "roc_3w", "quadrant", "ad_ratio", "ad_classification",
        })
        self.assertFalse(calculate_stage2(self.stock, self.benchmark)["is_stage2"])
        self.assertIsNone(calculate_stage2(stock.tail(29), self.benchmark))
        self.assertIsNone(calculate_stage2(stock.tail(30), self.benchmark)["rs_score"])

    def test_stage2_flat_prices_and_multiindex_still_supported(self):
        self.assertFalse(calculate_stage2(self.benchmark, self.benchmark)["is_stage2"])
        stock = self.stock.copy()
        stock.columns = pd.MultiIndex.from_product([stock.columns, ["TEST"]], names=["Price", "Ticker"])
        self.assertEqual(
            calculate_stage2(stock, self.benchmark), calculate_stage2(self.stock, self.benchmark)
        )


class ClosedWeekTests(unittest.TestCase):
    def test_midweek_and_india_friday_boundary(self):
        self.assertEqual(latest_closed_week("india", datetime(2026, 11, 25, tzinfo=timezone.utc)), "2026-W47")
        self.assertEqual(latest_closed_week("india", datetime(2026, 11, 27, 9, 59, tzinfo=timezone.utc)), "2026-W47")
        self.assertEqual(latest_closed_week("india", datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)), "2026-W48")

    def test_us_close_respects_dst(self):
        cases = [
            (datetime(2026, 7, 10, 19, 59, tzinfo=timezone.utc), "2026-W27"),
            (datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc), "2026-W28"),
            (datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc), "2026-W44"),
            (datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc), "2026-W45"),
        ]
        for now, expected in cases:
            self.assertEqual(latest_closed_week("us", now), expected)

    def test_iso_year_invalid_and_unfinished_weeks(self):
        now = datetime(2027, 1, 1, 8, tzinfo=timezone.utc)
        self.assertEqual(latest_closed_week("india", now), "2026-W52")
        self.assertEqual(closed_week_end("india", "2026-W52", now), date(2026, 12, 28))
        for week in ("2026-W53", "2026-W54", "2025-W53", "2026-W1", "nonsense"):
            with self.subTest(week=week), self.assertRaises(ValueError):
                closed_week_end("india", week, now)

    def test_stock_loader_bounds_before_resampling_and_preserves_actual_price_date(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchall.return_value = [
            SimpleNamespace(BarDate=date(2026, 11, 19), Open=10, High=11, Low=9, Close=10, Volume=100),
        ]
        frame = load_weekly_price_from_db(conn, "us", "TEST", date(2026, 11, 23))
        sql, params = conn.cursor.return_value.execute.call_args.args
        self.assertIn("BarDate < ?", sql)
        self.assertEqual(params, ["TEST", date(2026, 11, 23)])
        self.assertEqual(frame.index[-1], pd.Timestamp("2026-11-20"))
        self.assertEqual(frame.attrs["last_bar_date"], date(2026, 11, 19))

    def test_benchmark_uses_same_bounded_week_and_holiday_thursday(self):
        daily = weekly_frame([100.], end="2026-11-20")
        daily.index = pd.DatetimeIndex(["2026-11-19"])
        with patch("stage_analysis.yf.download", return_value=daily) as download:
            frame = fetch_benchmark_weekly_from_daily("us", date(2026, 11, 23))
        self.assertEqual(download.call_args.kwargs["end"], "2026-11-23")
        self.assertEqual(frame.index[-1], pd.Timestamp("2026-11-20"))
        self.assertEqual(frame.attrs["last_bar_date"], date(2026, 11, 19))

    def test_missing_friday_close_cannot_claim_friday_freshness(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchall.return_value = [
            SimpleNamespace(BarDate=date(2026, 11, 19), Open=10, High=11, Low=9, Close=10, Volume=100),
            SimpleNamespace(BarDate=date(2026, 11, 20), Open=10, High=11, Low=9, Close=None, Volume=100),
        ]
        frame = load_weekly_price_from_db(conn, "india", "TEST", date(2026, 11, 23))
        self.assertEqual(frame.attrs["last_bar_date"], date(2026, 11, 19))

    def test_fno_predicate_is_in_both_markets_sql_before_limit(self):
        for market, table in (("india", "IndianStocks"), ("us", "USStocks")):
            conn = MagicMock()
            conn.cursor.return_value.execute.return_value.fetchall.return_value = []
            get_stocks(conn, market, sector_ids=[3], limit=2, test_sample_only=True, fno_only=True)
            sql, params = conn.cursor.return_value.execute.call_args.args
            self.assertIn(f"dbo.{table}", sql)
            self.assertIn("TOP 2", sql)
            self.assertIn("st.IsFno = 1", sql)
            self.assertIn("st.SectorId IN (?)", sql)
            self.assertEqual(params, [3])
        get_stocks(conn, "us")
        self.assertNotIn("st.IsFno", conn.cursor.return_value.execute.call_args.args[0])

    def test_symbol_batches_cover_exact_universe_under_argument_limit(self):
        symbols = [f"STOCK{i:05d}" for i in range(3000)]
        batches = symbol_batches(symbols)
        self.assertEqual([s for batch in batches for s in batch], symbols)
        self.assertTrue(all(len(",".join(batch)) <= 6000 for batch in batches))


class Stage4HistoryTests(unittest.TestCase):
    def test_new_reentry_continuing_removed_and_unknown(self):
        previous = {
            "CONTINUE": {"is_stage4": True}, "EXIT": {"is_stage4": True},
            "UNKNOWN": {"is_stage4": True},
        }
        history = {
            "2026-W46": previous,
            "2026-W45": {"CONTINUE": {"is_stage4": True}, "REENTER": {"is_stage4": True}},
        }
        results = [
            {"symbol": symbol, "is_stage4": flag, "analysis_status": status}
            for symbol, flag, status in (
                ("NEW", True, "evaluated"), ("CONTINUE", True, "evaluated"),
                ("REENTER", True, "evaluated"), ("EXIT", False, "evaluated"),
                ("UNKNOWN", None, "stale"),
            )
        ]
        classify_results(results, history, "2026-W47")
        self.assertEqual(
            [r["classification"] for r in results], ["new", "continuing", "reentry", "removed", None]
        )
        self.assertEqual([r["weeks_in_stage4"] for r in results], [1, 3, 1, 0, None])

    def test_missing_week_breaks_streak_and_eligibility_exit_creates_no_removed_row(self):
        results = [{"symbol": "GAP", "is_stage4": True, "analysis_status": "evaluated"}]
        classify_results(results, {"2026-W45": {"GAP": {"is_stage4": True}, "NO_LONGER_FNO": {"is_stage4": True}}}, "2026-W47")
        self.assertEqual(results[0]["classification"], "reentry")
        self.assertEqual(results[0]["weeks_in_stage4"], 1)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
