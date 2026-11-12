import csv
import io
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from options_reference import (
    BSE_HOLIDAYS, BSE_HOURS, MEMBERS, MEMBERS_ARCHIVE, NSE_HOLIDAYS, NSE_HOURS, PublicReferenceError,
    _bse_calendar, _nse_calendar, build_sessions, ensure_reference_data, fetch_etf_weights, parse_etf_weights,
)

NOW = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)


def holidays():
    return {date(2026, month, 10): "Published test holiday" for month in range(1, 13)}


def membership():
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Company Name", "Industry", "Symbol", "Series", "ISIN Code"])
    for i in range(50):
        writer.writerow([f"Company {i}", "Test", f"STOCK{i}", "EQ", f"INE{i:09d}"])
    return stream.getvalue().encode()


def workbook(source_date="October 31,2026", missing=False):
    rows = [
        ["NIPPON INDIA ETF NIFTY 50 BEES", None],
        ["Monthly Portfolio Statement as on " + source_date, None],
        ["ISIN", "% to NAV"],
    ]
    rows.extend([[f"INE{i:09d}", .01999] for i in range(49 if missing else 50)])
    rows.append([None, .0005])
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="NB", index=False, header=False)
    return stream.getvalue()


class PublicReferenceTests(unittest.TestCase):
    def test_calendar_uses_published_holidays_and_known_exception_not_fixed_expiry_days(self):
        closed = holidays()
        closed[date(2026, 11, 8)] = "Diwali Laxmi Pujan"
        sessions, unknown = build_sessions("NIFTY50", closed, "09:15", "15:30", NOW.date())
        lookup = {row["sessionDate"]: row for row in sessions}
        self.assertFalse(lookup["2026-11-10"]["isTradingDay"])
        self.assertFalse(lookup["2026-11-14"]["isTradingDay"])
        self.assertTrue(lookup["2026-11-08"]["isTradingDay"])
        self.assertEqual(lookup["2026-11-08"]["openTime"], "14:20")
        self.assertEqual(lookup["2026-11-08"]["closeTime"], "15:30")
        self.assertEqual(unknown, [])
        self.assertNotIn("isWeeklyExpiry", lookup["2026-11-11"])
        self.assertTrue(all(row["sessionDate"] <= "2026-12-31" for row in sessions))

    def test_unpublished_year_and_unverified_special_hours_are_not_invented(self):
        with self.assertRaisesRegex(PublicReferenceError, "2027"):
            build_sessions("NIFTY50", holidays(), "09:15", "15:30", date(2027, 1, 1))
        closed = holidays()
        closed[date(2026, 11, 20)] = "Special Muhurat session"
        sessions, unknown = build_sessions("NIFTY50", closed, "09:15", "15:30", NOW.date())
        self.assertEqual(unknown, ["2026-11-20"])
        self.assertNotIn("2026-11-20", [row["sessionDate"] for row in sessions])

    def test_nse_parser_requires_holiday_json_and_published_cash_hours(self):
        payload = {"CM": [{"tradingDate": day.strftime("%d-%b-%Y"), "description": label}
                          for day, label in holidays().items()]}
        def download(url):
            return (json.dumps(payload).encode() if url == NSE_HOLIDAYS else
                    b"<h2>Equities</h2>Normal / Odd lot Market Open : 09:15 hrs Normal Market Close : 15:30 hrs")
        closed, opening, closing, source = _nse_calendar(download)
        self.assertEqual(closed, holidays())
        self.assertEqual((opening, closing), ("09:15", "15:30"))
        self.assertIn(NSE_HOURS, source)
        with self.assertRaises(PublicReferenceError):
            _nse_calendar(lambda url: b"{}" if url == NSE_HOLIDAYS else b"Unknown hours")

    def test_bse_calendar_accepts_named_anchor_and_uses_its_own_source(self):
        page = """<html><h2>Trading Holidays for 2026 – Equity Segment, Equity Derivative Segment</h2>
        <a name="eq24"></a><table><tr><th>SI.NO.</th><th>Holidays</th><th>Date</th><th>Day</th></tr>
        <tr><td>1</td><td>Published BSE holiday</td><td>10-Nov-26</td><td>Tuesday</td></tr></table></html>"""
        closed, opening, closing, source = _bse_calendar(
            lambda url: page.encode() if url == BSE_HOLIDAYS else b"%PDF-1.4 test fixture")
        self.assertEqual(closed, {date(2026, 11, 10): "Published BSE holiday"})
        self.assertEqual((opening, closing), ("09:15", "15:30"))
        self.assertIn(BSE_HOURS, source)

    def test_full_proxy_is_joined_by_isin_dated_and_explicitly_normalized(self):
        effective, weights, source, evidence = parse_etf_weights(
            membership(), workbook(), NOW.date(), "https://issuer.example/portfolio.xls")
        self.assertEqual(effective, date(2026, 10, 31))
        self.assertEqual(len(weights), 50)
        self.assertTrue(all(row["weightPct"] == 2 for row in weights))
        self.assertAlmostEqual(evidence["rawEquityNavPct"], 99.95)
        self.assertEqual(evidence["sourceType"], "etf-holdings-proxy")
        self.assertIn("ETF_PROXY", source)
        self.assertIn(MEMBERS, source)

    def test_proxy_rejects_missing_holdings_future_dates_and_stale_dates(self):
        for data in (workbook(missing=True), workbook("November 30,2026"), workbook("July 31,2026")):
            with self.subTest(), self.assertRaises(PublicReferenceError):
                parse_etf_weights(membership(), data, NOW.date(), "https://issuer.example/portfolio.xls")

    def test_membership_uses_official_archive_if_primary_returns_html_instead_of_csv(self):
        calls = []
        data = workbook()
        def download(url):
            calls.append(url)
            return (b"<html>not a CSV</html>" if url == MEMBERS else
                    membership() if url == MEMBERS_ARCHIVE else data)
        effective, weights, _, _ = fetch_etf_weights(NOW.date(), download)
        self.assertEqual(effective, date(2026, 10, 31))
        self.assertEqual(len(weights), 50)
        self.assertEqual(calls[:2], [MEMBERS, MEMBERS_ARCHIVE])

    def test_failed_calendar_does_not_prevent_independent_weight_refresh_or_silently_succeed(self):
        conn = MagicMock()
        evidence = {"sourceDate": "2026-10-31", "sourceType": "etf-holdings-proxy"}
        with patch("options_reference._attempts", {}), patch("options_reference._status", {}), \
                patch("options_reference._nse_calendar", side_effect=PublicReferenceError("source offline")), \
                patch("options_reference.fetch_etf_weights", return_value=(date(2026, 10, 31), [], "test", evidence)), \
                patch("options_reference.persist_weights", return_value={"written": 50}):
            result = ensure_reference_data(conn, "NIFTY50", NOW, force=True)
        self.assertEqual(result["calendar"]["status"], "unavailable")
        self.assertEqual(result["weights"]["status"], "ready")
        self.assertTrue(any("source offline" in warning for warning in result["warnings"]))
        conn.rollback.assert_called_once()

    def test_successful_refresh_is_cached_but_failed_refresh_has_bounded_backoff(self):
        conn = MagicMock()
        with patch("options_reference._attempts", {}), patch("options_reference._status", {}), \
                patch("options_reference._nse_calendar", side_effect=PublicReferenceError("source offline")) as fetch, \
                patch("options_reference.fetch_etf_weights", side_effect=PublicReferenceError("source offline")):
            ensure_reference_data(conn, "NIFTY50", NOW)
            ensure_reference_data(conn, "NIFTY50", NOW)
            self.assertEqual(fetch.call_count, 1)

    def test_calendar_refresh_preserves_separate_exception_provenance_and_daily_cache(self):
        conn = MagicMock()
        evidence = {"sourceDate": "2026-10-31", "sourceType": "etf-holdings-proxy"}
        def persist(_conn, _index, sessions, _source):
            return {"supplied": len(sessions), "written": len(sessions), "preservedManual": 0}
        with patch("options_reference._attempts", {}), patch("options_reference._status", {}), \
                patch("options_reference._nse_calendar", return_value=(holidays(), "09:15", "15:30", "calendar")) as fetch, \
                patch("options_reference.persist_sessions", side_effect=persist) as save, \
                patch("options_reference.fetch_etf_weights", return_value=(date(2026, 10, 31), [], "test", evidence)), \
                patch("options_reference.persist_weights", return_value={"written": 50}):
            first = ensure_reference_data(conn, "NIFTY50", NOW, download=lambda url: b"%PDF-1.4 fixture")
            second = ensure_reference_data(conn, "NIFTY50", NOW)
        self.assertEqual(first["calendar"]["status"], "ready")
        self.assertEqual(first["calendar"], second["calendar"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(save.call_count, 2)
        self.assertEqual(len(first["calendar"]["exceptionSources"]), 1)
        self.assertIn("CMTR76941.pdf", first["calendar"]["exceptionSources"][0])


if __name__ == "__main__":
    unittest.main()
