"""Offline contract, source-date, currency, fallback and cache tests."""
from __future__ import annotations

import io
import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import pandas as pd

import options_context as context
from options_data import KiteSessionExpired

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
NSE_BODY = context._nse_body
MASTER = """exchange,name,instrument_type,expiry,tradingsymbol
MCX,CRUDEOIL,FUT,2026-11-20,CRUDEOIL26NOVFUT
MCX,CRUDEOIL,FUT,2026-12-18,CRUDEOIL26DECFUT
MCX,CRUDEOIL,FUT,2026-11-09,EXPIRINGTODAY
MCX,CRUDEOIL,FUT,2026-10-19,EXPIRED
MCX,CRUDEOILM,FUT,2026-11-10,MINICONTRACT
MCX,CRUDEOIL,CE,2026-11-10,OPTIONCONTRACT
NFO,CRUDEOIL,FUT,2026-11-10,WRONGEXCHANGE
"""


def feed(items=None):
    if items is None:
        items = [
            (f"RBI release {i}", f"https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid={i}",
             f"Mon, 09 Nov 2026 {16 - i:02}:00:00")
            for i in range(7)
        ]
    return ("<rss><channel>" + "".join(
        f"<item><title>{title}</title><link>{link}</link><pubDate>{day}</pubDate></item>"
        for title, link, day in items
    ) + "</channel></rss>").encode()


def history(values=(98.0, 100.0, 999.0), dates=("2026-11-05", "2026-11-06", "2026-11-09")):
    return pd.DataFrame({"Close": values},
                        index=pd.DatetimeIndex(dates, tz="America/New_York"))


def broker(price=7000, previous=6800, timestamp=None):
    client = Mock()
    client.get.return_value = MASTER
    client.quotes.return_value = {"MCX:CRUDEOIL26NOVFUT": {
        "last_price": price, "ohlc": {"close": previous},
        "timestamp": timestamp or NOW.isoformat(),
    }}
    return client


def cash_csv(rows=None, unit="₹ Crores"):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["CATEGORY \n", "DATE \n", f"BUY VALUE \n({unit})",
                     f"SELL VALUE \n({unit})", f"NET VALUE \n({unit})"])
    writer.writerows(rows if rows is not None else [
        ["DII", "09-Nov-2026", "12,764.97", "19,937.50", "-7,172.53"],
        ["FII/FPI", "09-Nov-2026", "20,310.99", "13,098.77", "7,212.22"],
    ])
    return output.getvalue().encode("utf-8-sig")


class OptionsContextTests(unittest.TestCase):
    def setUp(self):
        context._master_cache = context._crude_cache = context._news_cache = None
        context._flows_cache = None
        self.nse = patch.object(context, "_nse_body", side_effect=URLError("offline test")).start()
        self.open = patch.object(context, "urlopen", side_effect=lambda *a, **k: io.BytesIO(feed())).start()
        self.ticker = patch.object(context.yf, "Ticker").start()
        self.ticker.return_value.history.return_value = history()
        self.addCleanup(patch.stopall)

    def test_selects_actual_front_master_contract_and_observed_time(self):
        client = broker(timestamp="2026-11-09 17:29:00")
        result = context.capture_context(client, NOW)
        client.get.assert_called_once_with("/instruments/MCX", text=True)
        client.quotes.assert_called_once_with(["MCX:CRUDEOIL26NOVFUT"])
        crude = result["evidence"]["crude"]
        self.assertEqual(crude["currency"], "INR")
        self.assertEqual(crude["unit"], "INR/barrel")
        self.assertEqual(crude["asOf"], "2026-11-09T11:59:00+00:00")
        self.assertEqual(crude["quoteType"], "live")
        self.assertEqual(crude["contractExpiry"], "2026-11-20")
        self.assertAlmostEqual(crude["changePct"], 100 * (7000 / 6800 - 1))
        dimension = result["dimension"]
        self.assertGreaterEqual(dimension["score"], 0)
        self.assertLess(dimension["score"], 50)
        self.assertEqual(dimension["scale"], "Adverse to supportive oil context")
        self.assertAlmostEqual(dimension["score"], 50 - crude["changePct"])
        self.assertAlmostEqual(dimension["indicators"][0]["contribution"], -crude["changePct"])
        self.assertEqual(dimension["indicators"][0]["contributionUnit"], "oil-context score points")
        self.assertEqual(dimension["coverage"], 0.25)
        self.assertTrue(dimension["indicators"][0]["contextOnly"])
        self.assertIn("not a calibrated", dimension["reason"])
        self.assertFalse(result["evidence"]["scheduledCalendar"])
        self.ticker.assert_not_called()
        json.dumps(result, allow_nan=False)

    def test_falling_crude_supportive_and_delayed_quote_is_not_live(self):
        result = context.capture_context(broker(6500, 6800, (NOW - timedelta(hours=12)).isoformat()), NOW)
        self.assertGreater(result["dimension"]["score"], 50)
        self.assertLessEqual(result["dimension"]["score"], 100)
        self.assertEqual(result["evidence"]["crude"]["quoteType"], "delayed-observation")
        self.assertTrue(any("not a live quote" in item for item in result["warnings"]))

    def test_brent_permission_fallback_is_usd_completed_close_not_today(self):
        client = broker()
        client.get.side_effect = RuntimeError("SECRET broker response")
        result = context.capture_context(client, NOW)
        crude = result["evidence"]["crude"]
        self.assertEqual(crude["instrument"], "BZ=F")
        self.assertEqual(crude["currency"], "USD")
        self.assertEqual(crude["unit"], "USD/barrel")
        self.assertEqual(crude["price"], 100)
        self.assertEqual(crude["previousClose"], 98)
        self.assertEqual(crude["sessionDate"], "2026-11-06")
        self.assertEqual(crude["previousCloseAsOf"], "2026-11-05")
        self.assertEqual(crude["quoteType"], "last-session")
        self.assertEqual(crude["asOfPrecision"], "session-date")
        self.assertLess(datetime.fromisoformat(crude["asOf"]), NOW)
        self.assertTrue(crude["isAlternative"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.ticker.assert_called_once_with("BZ=F")
        self.ticker.return_value.history.assert_called_once_with(
            period="1mo", interval="1d", auto_adjust=False, raise_errors=True, timeout=15)
        client.quotes.assert_not_called()

    def test_expired_broker_session_propagates_without_fallback(self):
        client = broker()
        client.quotes.side_effect = KiteSessionExpired("sensitive provider error")
        with self.assertRaises(KiteSessionExpired):
            context.capture_context(client, NOW)
        self.ticker.assert_not_called()
        self.open.assert_not_called()
        self.assertIsNone(context._crude_cache)
        context._master_cache = None
        client.get.side_effect = KiteSessionExpired("re-login required")
        with self.assertRaises(KiteSessionExpired):
            context.capture_context(client, NOW)
        self.ticker.assert_not_called()
        self.open.assert_not_called()

    def test_context_score_has_neutral_midpoint_and_bounded_extremes(self):
        for price, previous, expected in [(100, 100, 50), (1000, 100, 0), (1, 100, 100)]:
            with self.subTest(price=price):
                context._crude_cache = None
                result = context.capture_context(broker(price, previous), NOW)
                self.assertEqual(result["dimension"]["score"], expected)
                indicator = result["dimension"]["indicators"][0]
                self.assertEqual(indicator["contribution"], expected - 50)
                self.assertTrue(indicator["contextOnly"])

    def test_invalid_mcx_prices_and_dates_trigger_real_fallback(self):
        cases = [
            broker(float("nan")), broker(float("inf")), broker(0), broker(-1),
            broker(previous=0), broker(previous=float("nan")),
            broker(timestamp=(NOW + timedelta(seconds=1)).isoformat()),
            broker(timestamp=(NOW - timedelta(days=8)).isoformat()),
            broker(timestamp="bad-date"), broker(1e308, 1e-308),
        ]
        for client in cases:
            with self.subTest(quote=client.quotes.return_value):
                context._master_cache = context._crude_cache = None
                result = context.capture_context(client, NOW)
                self.assertTrue(result["evidence"]["crude"]["isAlternative"])
                json.dumps(result, allow_nan=False)

    def test_invalid_master_never_constructs_symbol(self):
        for master in ("bad,csv\n1,2", MASTER.replace("CRUDEOIL,FUT", "OTHER,FUT")):
            with self.subTest(master=master):
                context._master_cache = context._crude_cache = None
                client = broker()
                client.get.return_value = master
                result = context.capture_context(client, NOW)
                client.quotes.assert_not_called()
                self.assertEqual(result["evidence"]["crude"]["instrument"], "BZ=F")

    def test_both_providers_unavailable_preserve_null_score_and_no_retry(self):
        client = broker()
        client.get.side_effect = RuntimeError("restricted")
        self.ticker.return_value.history.side_effect = context.YFException("provider error")
        result = context.capture_context(client, NOW)
        self.assertIsNone(result["dimension"]["score"])
        self.assertEqual(result["dimension"]["coverage"], 0)
        self.assertEqual(result["dimension"]["indicators"], [])
        self.assertIsNone(result["evidence"]["crude"])
        self.assertEqual(len(result["news"]), 5)
        client.get.assert_called_once()
        self.ticker.return_value.history.assert_called_once()
        self.assertTrue(any("Brent" in message and "unavailable" in message for message in result["warnings"]))

    def test_brent_rejects_missing_stale_nonfinite_and_nonpositive_data(self):
        bad_histories = [
            pd.DataFrame(),
            history((0, -1, 200)),
            history((float("nan"), float("inf"), 200)),
            history((100,), ("2026-11-06",)),
            history((90, 100), ("2026-10-01", "2026-10-02")),
            history((90, 100), ("2026-11-09", "2026-11-10")),
        ]
        for data in bad_histories:
            with self.subTest(data=data):
                context._crude_cache = None
                self.ticker.return_value.history.return_value = data
                client = broker()
                client.get.side_effect = RuntimeError("MCX unavailable")
                result = context.capture_context(client, NOW)
                self.assertIsNone(result["evidence"]["crude"])
                json.dumps(result, allow_nan=False)

    def test_rbi_dated_headlines_are_bounded_and_untrusted(self):
        result = context.capture_context(broker(), NOW)
        self.assertEqual(len(result["news"]), 5)
        self.assertEqual(result["news"][0]["publishedAt"], "2026-11-09T10:30:00+00:00")
        for item in result["news"]:
            self.assertTrue(item["untrustedText"])
            self.assertEqual(item["kind"], "news")
            self.assertLessEqual(len(item["title"]), 120)
            self.assertLessEqual(datetime.fromisoformat(item["publishedAt"]), NOW)
            self.assertEqual(item["sourceUrl"], context.RBI_RSS)
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual([call.args[0].full_url for call in self.open.call_args_list],
                         [context.RBI_RSS, context.MARKET_RSS])
        self.assertEqual(len(result["evidence"]["newsSources"]), 2)

    def test_feed_rejects_future_stale_undated_unsafe_links_and_deduplicates(self):
        url = "https://www.rbi.org.in/scripts/release.aspx?id=1"
        body = feed([
            ("&lt;b&gt;" + "Headline " * 30 + "&lt;/b&gt;", url, "Mon, 09 Nov 2026 16:00:00"),
            ("duplicate", url, "Mon, 09 Nov 2026 16:01:00"),
            ("future", url + "2", "Tue, 10 Nov 2026 16:00:00"),
            ("old", url + "3", "Mon, 19 Oct 2026 16:00:00"),
            ("undated", url + "4", ""),
            ("unsafe", "javascript:alert(1)", "Mon, 09 Nov 2026 16:00:00"),
            ("foreign", "https://evil.example/a", "Mon, 09 Nov 2026 16:00:00"),
        ])
        self.open.side_effect = lambda *a, **k: io.BytesIO(body)
        result = context.capture_context(broker(), NOW)
        self.assertEqual(len(result["news"]), 1)
        self.assertNotIn("<b>", result["news"][0]["title"])
        self.assertLessEqual(len(result["news"][0]["title"]), 120)
        self.assertTrue(any("Fewer than three" in warning for warning in result["warnings"]))

    def test_et_fallback_has_explicit_offset_not_calendar(self):
        body = feed([("Market headline", "https://economictimes.indiatimes.com/markets/news/a.cms",
                      "Mon, 09 Nov 2026 17:00:00 +0530")])
        self.open.side_effect = [URLError("RBI down"), io.BytesIO(body)]
        result = context.capture_context(broker(), NOW)
        self.assertEqual(result["news"][0]["publishedAt"], "2026-11-09T11:30:00+00:00")
        self.assertEqual(result["news"][0]["source"], "The Economic Times Markets")
        self.assertEqual(result["evidence"]["newsSources"][0]["status"], "unavailable")
        self.assertFalse(result["evidence"]["scheduledCalendar"])
        self.assertEqual(self.open.call_count, 2)

    def test_mixed_news_reserves_two_rbi_and_three_market_then_sorts_newest(self):
        market = feed([
            (f"Market story {i}", f"https://economictimes.indiatimes.com/markets/news/{i}.cms",
             f"Mon, 09 Nov 2026 {17 - i:02}:00:00 +0530") for i in range(7)
        ])
        self.open.side_effect = [io.BytesIO(feed()), io.BytesIO(market)]
        result = context.capture_context(broker(), NOW)
        news = result["news"]
        self.assertEqual(len(news), 5)
        self.assertEqual(sum(item["sourceUrl"] == context.RBI_RSS for item in news), 2)
        self.assertEqual(sum(item["sourceUrl"] == context.MARKET_RSS for item in news), 3)
        self.assertEqual(news[0]["title"], "Market story 0")
        self.assertEqual(news[0]["publishedAt"], "2026-11-09T11:30:00+00:00")
        self.assertEqual([item["publishedAt"] for item in news],
                         sorted((item["publishedAt"] for item in news), reverse=True))
        self.assertEqual([item["status"] for item in result["evidence"]["newsSources"]],
                         ["available", "available"])

    def test_mixed_news_deduplicates_titles_and_urls_and_fills_capacity(self):
        market = feed([
            ("rbi RELEASE 0", "https://economictimes.indiatimes.com/markets/news/shared.cms",
             "Mon, 09 Nov 2026 17:00:00 +0530"),
            ("Market unique", "https://economictimes.indiatimes.com/markets/news/unique.cms",
             "Mon, 09 Nov 2026 16:00:00 +0530"),
            ("Repeated URL", "https://economictimes.indiatimes.com/markets/news/unique.cms",
             "Mon, 09 Nov 2026 15:00:00 +0530"),
        ])
        self.open.side_effect = [io.BytesIO(feed()), io.BytesIO(market)]
        result = context.capture_context(broker(), NOW)
        news = result["news"]
        self.assertEqual(len(news), 5)
        self.assertEqual(len({item["url"] for item in news}), 5)
        self.assertEqual(len({item["title"].casefold() for item in news}), 5)
        self.assertEqual(sum(item["sourceUrl"] == context.RBI_RSS for item in news), 4)
        self.assertEqual(sum(item["sourceUrl"] == context.MARKET_RSS for item in news), 1)
        self.assertNotIn("Repeated URL", [item["title"] for item in news])

    def test_failed_market_feed_keeps_rbi_and_exposes_both_attempts(self):
        self.open.side_effect = [io.BytesIO(feed()), URLError("ET down")]
        result = context.capture_context(broker(), NOW)
        self.assertEqual(len(result["news"]), 5)
        self.assertTrue(all(item["sourceUrl"] == context.RBI_RSS for item in result["news"]))
        self.assertEqual([item["status"] for item in result["evidence"]["newsSources"]],
                         ["available", "unavailable"])
        self.assertTrue(any("The Economic Times Markets RSS unavailable" in w for w in result["warnings"]))

    def test_bad_xml_entities_size_and_network_fail_explicitly_without_retry(self):
        for bad in (b"<rss>", b"<!DOCTYPE rss [<!ENTITY x 'boom'>]><rss/>",
                    b"x" * (context.MAX_FEED_BYTES + 1), b"<html>not an RSS feed</html>"):
            with self.subTest(body=bad[:50]):
                context._news_cache = None
                self.open.reset_mock()
                self.open.side_effect = lambda *a, **k: io.BytesIO(bad)
                result = context.capture_context(broker(), NOW)
                self.assertEqual(result["news"], [])
                self.assertEqual(self.open.call_count, 2)
                self.assertEqual(len(result["evidence"]["newsSources"]), 2)
        context._news_cache = None
        self.open.side_effect = URLError("network down")
        result = context.capture_context(broker(), NOW)
        self.assertEqual(result["news"], [])
        self.assertTrue(any("no headlines fabricated" in w for w in result["warnings"]))

    def test_cache_ttls_daily_master_and_return_mutation_isolation(self):
        client = broker()
        initial = context.capture_context(client, NOW)
        initial["evidence"]["crude"]["price"] = -100
        initial["news"][0]["title"] = "mutated"
        cached = context.capture_context(client, NOW + timedelta(minutes=4))
        self.assertEqual(cached["evidence"]["crude"]["price"], 7000)
        self.assertNotEqual(cached["news"][0]["title"], "mutated")
        self.assertTrue(cached["evidence"]["newsCached"])
        self.assertTrue(cached["evidence"]["crudeCached"])
        context.capture_context(client, NOW + timedelta(minutes=5))
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.quotes.call_count, 2)
        self.assertEqual(self.open.call_count, 2)
        context.capture_context(client, NOW + timedelta(hours=1))
        self.assertEqual(self.open.call_count, 4)
        context.capture_context(client, NOW + timedelta(days=1))
        self.assertEqual(client.get.call_count, 2)

    def test_cached_live_observation_becomes_delayed_and_failed_sources_are_cached(self):
        result = context.capture_context(broker(timestamp=(NOW - timedelta(minutes=4)).isoformat()), NOW)
        self.assertEqual(result["evidence"]["crude"]["quoteType"], "live")
        result = context.capture_context(broker(), NOW + timedelta(minutes=2))
        self.assertEqual(result["evidence"]["crude"]["quoteType"], "delayed-observation")
        context._master_cache = context._crude_cache = None
        client = broker()
        client.get.side_effect = RuntimeError("unavailable")
        self.ticker.return_value.history.side_effect = context.YFException("down")
        context.capture_context(client, NOW)
        context.capture_context(client, NOW + timedelta(minutes=1))
        client.get.assert_called_once()

    def test_rejects_naive_now_without_provider_calls(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            context.capture_context(broker(), NOW.replace(tzinfo=None))
        self.open.assert_not_called()
        self.ticker.assert_not_called()

    def test_unexpected_programming_errors_are_not_swallowed(self):
        client = broker()
        client.quotes.side_effect = AssertionError("programming error")
        with self.assertRaisesRegex(AssertionError, "programming error"):
            context.capture_context(client, NOW)

    def test_official_cash_flows_dates_units_and_raw_indicators(self):
        self.nse.side_effect = None
        self.nse.return_value = (cash_csv(), False)
        result = context.capture_context(broker(), NOW)
        flows = result["evidence"]["flows"]
        self.assertEqual(flows["status"], "available")
        self.assertEqual(flows["sessionDate"], "2026-11-09")
        self.assertEqual(flows["asOf"], "2026-11-09")
        self.assertEqual(flows["capturedAt"], NOW.isoformat())
        self.assertEqual(flows["unit"], "INR crore")
        self.assertEqual(flows["sourceUrl"], context.NSE_FLOWS_URL)
        self.assertEqual(flows["fii"], {"buy": 20310.99, "sell": 13098.77, "net": 7212.22})
        self.assertEqual(flows["dii"], {"buy": 12764.97, "sell": 19937.5, "net": -7172.53})
        self.assertEqual(flows["quoteType"], "last-reported-end-of-day")
        self.assertEqual(result["dimension"]["coverage"], 0.5)
        crude = result["evidence"]["crude"]
        self.assertAlmostEqual(result["dimension"]["score"], 50 - crude["changePct"])
        for indicator in result["dimension"]["indicators"][1:]:
            self.assertIsNone(indicator["contribution"])
            self.assertTrue(indicator["contextOnly"])
            self.assertEqual(indicator["unit"], "INR crore")
            self.assertIn("no calibrated", indicator["reason"])
        json.dumps(result, allow_nan=False)

    def test_cash_partial_missing_invalid_and_future_categories(self):
        for bad in (None, ["DII", "09-Nov-2026", "nan", "2", "1"],
                    ["DII", "10-Nov-2026", "3", "2", "1"],
                    ["DII", "09-Nov-2026", "3", "2", "99"]):
            with self.subTest(bad=bad):
                context._flows_cache = None
                rows = [["FII/FPI", "09-Nov-2026", "100", "80", "20"]]
                if bad is not None:
                    rows.append(bad)
                self.nse.side_effect = None
                self.nse.return_value = (cash_csv(rows), False)
                result = context.capture_context(broker(), NOW)
                flows = result["evidence"]["flows"]
                self.assertEqual(flows["status"], "partial")
                self.assertIsNone(flows["dii"])
                self.assertEqual(flows["fii"]["net"], 20)
                self.assertEqual(len(result["dimension"]["indicators"]), 2)
                self.assertTrue(any("partial" in w for w in result["warnings"]))

    def test_cash_rejects_bad_units_categories_dates_and_values(self):
        cases = [
            cash_csv(unit="USD million"),
            cash_csv([["F&O", "09-Nov-2026", "100", "80", "20"]]),
            cash_csv([["FII/FPI", "09-Nov-2026", "100", "80", "20"],
                      ["DII", "06-Nov-2026", "100", "80", "20"]]),
            cash_csv([["FII/FPI", "10-Nov-2026", "100", "80", "20"]]),
            cash_csv([["DII", "01-Nov-2026", "100", "80", "20"]]),
            cash_csv([["DII", "bad-date", "100", "80", "20"]]),
            cash_csv([["DII", "09-Nov-2026", "inf", "80", "20"]]),
            cash_csv([["DII", "09-Nov-2026", "-100", "80", "-180"]]),
            cash_csv([["DII", "09-Nov-2026", "100", "80", "999"]]),
            cash_csv([["DII", "09-Nov-2026", "1,00", "80", "20"]]),
            cash_csv([["DII", "09-Nov-2026", "100", "80", "20"],
                      ["DII", "09-Nov-2026", "100", "80", "20"]]),
            b"<html>blocked</html>",
            cash_csv([]),
        ]
        for body in cases:
            with self.subTest(body=body[:90]):
                context._flows_cache = None
                self.nse.side_effect = None
                self.nse.return_value = (body, False)
                result = context.capture_context(broker(), NOW)
                flows = result["evidence"]["flows"]
                self.assertEqual(flows["status"], "unavailable")
                self.assertIsNone(flows["asOf"])
                self.assertIsNone(flows["fii"])
                self.assertIsNone(flows["dii"])
                self.assertTrue(flows["reason"])
                self.assertEqual(len(result["dimension"]["indicators"]), 1)

    def test_cash_end_of_day_not_fabricated_intraday_and_last_report_remains_valid(self):
        self.nse.side_effect = None
        self.nse.return_value = (cash_csv(), False)
        before_close = NOW - timedelta(hours=3)
        result = context.capture_context(broker(timestamp=before_close.isoformat()), before_close)
        self.assertEqual(result["evidence"]["flows"]["status"], "unavailable")
        self.nse.return_value = (cash_csv([
            ["FII/FPI", "06-Nov-2026", "100", "80", "20"],
            ["DII", "06-Nov-2026", "80", "100", "-20"],
        ]), False)
        result = context.capture_context(broker(timestamp=before_close.isoformat()), before_close, refresh=True)
        self.assertEqual(result["evidence"]["flows"]["status"], "available")
        self.assertEqual(result["evidence"]["flows"]["sessionDate"], "2026-11-06")

    def test_refresh_bypasses_crude_news_flows_but_not_daily_master(self):
        client = broker()
        context.capture_context(client, NOW)
        context.capture_context(client, NOW + timedelta(seconds=1))
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.quotes.call_count, 1)
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual(self.nse.call_count, 1)
        result = context.capture_context(client, NOW + timedelta(seconds=2), refresh=True)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.quotes.call_count, 2)
        self.assertEqual(self.open.call_count, 4)
        self.assertEqual(self.nse.call_count, 2)
        self.assertFalse(result["evidence"]["crudeCached"])
        self.assertFalse(result["evidence"]["newsCached"])
        self.assertFalse(result["evidence"]["flowsCached"])

    def test_nse_transport_bounded_direct_or_one_explicit_warmup(self):
        with patch.object(context, "build_opener") as factory:
            opener = factory.return_value
            opener.open.return_value = io.BytesIO(cash_csv())
            body, warmed = NSE_BODY()
            self.assertFalse(warmed)
            self.assertEqual(body, cash_csv())
            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
            opener.open.reset_mock()
            opener.open.side_effect = [
                HTTPError(context.NSE_FLOWS_URL, 403, "Restricted", {}, None),
                io.BytesIO(b"<html>session</html>"), io.BytesIO(cash_csv()),
            ]
            body, warmed = NSE_BODY()
            self.assertTrue(warmed)
            self.assertEqual(body, cash_csv())
            self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list],
                             [context.NSE_FLOWS_URL, context.NSE_FLOWS_PAGE, context.NSE_FLOWS_URL])
            opener.open.reset_mock()
            opener.open.side_effect = HTTPError(context.NSE_FLOWS_URL, 500, "Down", {}, None)
            with self.assertRaises(HTTPError):
                NSE_BODY()
            self.assertEqual(opener.open.call_count, 1)
            opener.open.side_effect = [io.BytesIO(b"x" * (context.MAX_FEED_BYTES + 1))]
            with self.assertRaisesRegex(ValueError, "bounded size"):
                NSE_BODY()


if __name__ == "__main__":
    unittest.main()
