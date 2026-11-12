"""Dated oil and public headlines, not a calendar or calibrated index forecast.

capture_context(client, now, refresh=False) returns dimension/news/evidence/warnings.
Only the crude dimension has a numeric context score: 50 + clamp(-changePct,
-50, +50), on the repository's 0..100 scale with 50 neutral. The signed
contribution is oil-context score points, not an index return. This is an
explicitly uncalibrated India oil-importer heuristic. Headlines are untrusted
source text, never instructions or numeric model inputs.
Official NSE combined-exchange capital-market cash flows are raw dated INR crore
observations only, with no numeric score contribution. refresh=True bypasses
oil/news/flow caches for each deliberate forecast; the MCX master remains daily.
"""
from __future__ import annotations

import copy
import csv
import html
import io
import math
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

import yfinance as yf
from curl_cffi.requests.exceptions import RequestException
from yfinance.exceptions import YFException

from options_data import KiteSessionExpired, observed_time

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
BRENT_ZONE = ZoneInfo("America/New_York")
RBI_RSS = "https://www.rbi.org.in/pressreleases_rss.xml"
MARKET_RSS = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
BRENT_URL = "https://finance.yahoo.com/quote/BZ=F/history/"
NSE_FLOWS_URL = "https://www.nseindia.com/api/fiidiiTradeReact?csv=true"
NSE_FLOWS_PAGE = "https://www.nseindia.com/reports/fii-dii"
MAX_NEWS = 5
MAX_FEED_BYTES = 512 * 1024
MAX_AGE = timedelta(days=7)
_lock = threading.Lock()
_master_cache = None
_crude_cache = None
_news_cache = None
_flows_cache = None


def _positive(value):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _valid_cache(cache, now, lifetime):
    return cache is not None and timedelta(0) <= now - cache[0] < lifetime


def _mcx(client, now):
    global _master_cache
    today = now.astimezone(IST).date()
    if (_master_cache is None or _master_cache[0] != today
            or datetime.fromisoformat(_master_cache[2]) > now):
        text = client.get("/instruments/MCX", text=True)
        if not isinstance(text, str) or len(text) > 10 * 1024 * 1024:
            raise ValueError("Invalid MCX master.")
        reader = csv.DictReader(io.StringIO(text))
        required = {"exchange", "name", "instrument_type", "expiry", "tradingsymbol"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Missing MCX master fields.")
        contracts = []
        for row in reader:
            if (row.get("exchange") != "MCX" or row.get("name") != "CRUDEOIL"
                    or row.get("instrument_type") != "FUT"):
                continue
            expiry = datetime.strptime(row["expiry"], "%Y-%m-%d").date()
            symbol = row["tradingsymbol"].strip()
            if expiry > today and symbol and re.fullmatch(r"[A-Z0-9]+", symbol):
                contracts.append((expiry, symbol))
        _master_cache = (today, sorted(contracts), now.isoformat())
    if not _master_cache[1]:
        raise ValueError("No eligible MCX crude future.")
    expiry, symbol = _master_cache[1][0]
    key = "MCX:" + symbol
    quotes = client.quotes([key])
    quote = quotes.get(key) if isinstance(quotes, dict) else None
    if not isinstance(quote, dict):
        raise ValueError("MCX quote missing.")
    as_of = observed_time(quote)
    ohlc = quote.get("ohlc")
    price = _positive(quote.get("last_price"))
    previous = _positive(ohlc.get("close")) if isinstance(ohlc, dict) else None
    if (price is None or previous is None or as_of is None
            or not timedelta(0) <= now - as_of <= MAX_AGE):
        raise ValueError("MCX quote lacks valid dated comparable prices.")
    change = (price / previous - 1) * 100
    if not math.isfinite(change):
        raise ValueError("MCX change is non-finite.")
    return {
        "instrument": key, "name": "MCX CRUDEOIL futures", "benchmark": "MCX CRUDEOIL",
        "currency": "INR", "unit": "INR/barrel", "price": price, "previousClose": previous,
        "changePct": change, "asOf": as_of.isoformat(),
        "previousCloseAsOf": None,
        "comparison": "Same MCX contract's previous session close (Kite ohlc.close).",
        "source": "Zerodha Kite", "sourceUrl": "https://api.kite.trade/quote",
        "masterUrl": "https://api.kite.trade/instruments/MCX",
        "masterFetchedAt": _master_cache[2], "contractExpiry": expiry.isoformat(),
        "contractSelection": "Nearest expiry after the India capture date; expiry-day contracts excluded.",
        "quoteType": "live" if now - as_of <= timedelta(minutes=5) else "delayed-observation",
        "isAlternative": False,
    }


def _brent(now):
    # One explicit history call; no metadata/fast_info calls or application retries.
    history = yf.Ticker("BZ=F").history(
        period="1mo", interval="1d", auto_adjust=False, raise_errors=True, timeout=15,
    )
    if history.empty or "Close" not in history:
        raise ValueError("Brent history missing.")
    today = now.astimezone(BRENT_ZONE).date()
    closes = {}
    for stamp, value in history["Close"].items():
        day = stamp.date()
        price = _positive(value)
        # A daily bar for today may still be forming. Never present it as a close.
        if day < today and price is not None:
            closes[day] = price
    ordered = sorted(closes.items())
    if len(ordered) < 2:
        raise ValueError("Brent needs two completed daily closes.")
    (previous_day, previous), (day, price) = ordered[-2:]
    if (today - previous_day).days > 10 or (today - day).days > 7:
        raise ValueError("Brent completed closes are stale.")
    # A daily source supplies a session date, not the precise settlement instant.
    # End-of-date is a conservative availability bound, explicitly not a quote time.
    available_by = datetime.combine(day, time.max, BRENT_ZONE).astimezone(UTC)
    return {
        "instrument": "BZ=F", "name": "Brent crude futures daily close (MCX alternative)",
        "benchmark": "Brent", "currency": "USD", "unit": "USD/barrel",
        "price": price, "previousClose": previous, "changePct": (price / previous - 1) * 100,
        "asOf": available_by.isoformat(), "asOfPrecision": "session-date",
        "sessionDate": day.isoformat(), "previousCloseAsOf": previous_day.isoformat(),
        "comparison": "Consecutive available completed BZ=F daily closes; continuous futures may roll.",
        "source": "Yahoo Finance via yfinance", "sourceUrl": BRENT_URL,
        "quoteType": "last-session", "isAlternative": True,
        "asOfMeaning": "End of source session date; not an observed quote/settlement timestamp.",
    }


def _capture_crude(client, now):
    warnings = []
    try:
        crude = _mcx(client, now)
    except KiteSessionExpired:
        raise
    except (RuntimeError, URLError, TimeoutError, ValueError, TypeError, csv.Error,
            UnicodeError, OSError, HTTPException):
        # Kite wraps transport/permission failures in RuntimeError. Expired
        # sessions propagate above so the runner can require re-login. Never
        # include broker exception text: it can contain provider response details.
        warnings.append("MCX CRUDEOIL unavailable, restricted or invalid; trying named Brent USD alternative.")
        try:
            crude = _brent(now)
        except (YFException, RequestException, OSError, ValueError, TypeError, KeyError, OverflowError):
            warnings.append("Brent completed daily prices unavailable or invalid; crude context unavailable.")
            return None, warnings
    if not math.isfinite(crude["changePct"]):
        warnings.append("Crude change is non-finite; crude context unavailable.")
        return None, warnings
    if crude["isAlternative"]:
        warnings.append("Brent USD/barrel last-session close is an alternative, not MCX INR/barrel or a live quote.")
    elif crude["quoteType"] != "live":
        warnings.append("MCX crude is a dated delayed observation, not a live quote.")
    return crude, warnings


def _headline(text):
    text = html.unescape(re.sub(r"<[^>]*>", " ", text or ""))
    text = " ".join("".join(c for c in text if c.isprintable()).split())
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


def _feed(url, name, source_zone, now):
    request = Request(url, headers={"User-Agent": "MarketEdge/1.0 (public RSS reader)",
                                    "Accept": "application/rss+xml, application/xml, text/xml"})
    with urlopen(request, timeout=15) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise ValueError("RSS exceeds bounded size.")
    # Do not expand attacker-controlled entities, even in otherwise valid XML.
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("RSS document types/entities are not allowed.")
    root = ET.fromstring(body)
    items = root.findall("./channel/item")
    if root.tag != "rss" or not items:
        raise ValueError("RSS has no items.")
    records, seen, seen_titles = [], set(), set()
    for item in items[:100]:
        title = _headline(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        parts = urlsplit(link)
        # Only publisher-owned links; feed text cannot supply arbitrary schemes.
        host = (parts.hostname or "").lower()
        allowed = ("rbi.org.in", "www.rbi.org.in") if url == RBI_RSS else ("economictimes.indiatimes.com",)
        if (not title or parts.scheme != "https" or host not in allowed
                or parts.username or parts.password or len(link) > 2048):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (ValueError, TypeError, OverflowError):
            continue
        if published.tzinfo is None:
            # Official RBI RSS omits its offset; RBI publishes in India time.
            if source_zone is None:
                continue
            published = published.replace(tzinfo=source_zone)
        published = published.astimezone(UTC)
        title_key = title.casefold()
        if (not timedelta(0) <= now - published <= MAX_AGE
                or link in seen or title_key in seen_titles):
            continue
        seen.add(link)
        seen_titles.add(title_key)
        records.append({
            "title": title, "url": link, "publishedAt": published.isoformat(),
            "source": name, "sourceUrl": url, "kind": "news",
            "timestampBasis": "publisher RSS pubDate (RBI naive timestamps interpreted Asia/Kolkata)"
            if url == RBI_RSS else "publisher RSS pubDate",
            "untrustedText": True,
        })
    records.sort(key=lambda item: item["publishedAt"], reverse=True)
    return records[:MAX_NEWS]


def _capture_news(now):
    warnings = []
    attempts = []
    feeds = []
    for url, name, zone in [(RBI_RSS, "Reserve Bank of India", IST),
                            (MARKET_RSS, "The Economic Times Markets", None)]:
        try:
            records = _feed(url, name, zone, now)
        except (URLError, OSError, HTTPException, ValueError, UnicodeError, ET.ParseError):
            attempts.append({"url": url, "status": "unavailable"})
            warnings.append(f"{name} RSS unavailable or invalid; no headlines fabricated.")
            feeds.append([])
            continue
        attempts.append({"url": url, "status": "available" if records else "no-recent-dated-items"})
        feeds.append(records)
        if not records:
            warnings.append(f"{name} RSS had no valid dated headlines within seven days.")
    selected, urls, titles = [], set(), set()

    def select(records, limit):
        added = 0
        for item in records:
            title = item["title"].casefold()
            if item["url"] in urls or title in titles:
                continue
            selected.append(item)
            urls.add(item["url"])
            titles.add(title)
            added += 1
            if added >= limit:
                break

    # Reserve room for market news instead of letting administrative RBI
    # releases consume the entire bounded context.
    select(feeds[0], 2)
    select(feeds[1], 3)
    if len(selected) < MAX_NEWS:
        remaining = sorted(feeds[0] + feeds[1], key=lambda item: item["publishedAt"], reverse=True)
        select(remaining, MAX_NEWS - len(selected))
    selected.sort(key=lambda item: item["publishedAt"], reverse=True)
    if 0 < len(selected) < 3:
        warnings.append("Fewer than three valid recent headlines available; no filler news added.")
    return selected, attempts, warnings


def _nse_body():
    """One CSV request; only HTTP 401/403 permits one explicit session warmup."""
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,application/json,*/*",
               "Referer": NSE_FLOWS_PAGE}

    def read(url):
        with opener.open(Request(url, headers=headers), timeout=12) as response:
            body = response.read(MAX_FEED_BYTES + 1)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError("NSE response exceeds bounded size.")
        return body

    try:
        return read(NSE_FLOWS_URL), False
    except HTTPError as exc:
        if exc.code not in (401, 403):
            raise
        exc.close()
    read(NSE_FLOWS_PAGE)
    return read(NSE_FLOWS_URL), True


def _flow_number(text, *, signed=False):
    if not isinstance(text, str) or not re.fullmatch(
            r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text.strip()):
        raise ValueError("NSE cash value is missing or not a finite number.")
    number = Decimal(text.strip().replace(",", ""))
    if not number.is_finite() or not math.isfinite(float(number)) or (not signed and number < 0):
        raise ValueError("NSE cash buy/sell values must be finite and nonnegative.")
    return number


def _parse_flows(body, now):
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    fields = reader.fieldnames or []
    normalized = [" ".join(field.split()).upper() for field in fields]
    expected = ["CATEGORY", "DATE", "BUY VALUE (₹ CRORES)", "SELL VALUE (₹ CRORES)", "NET VALUE (₹ CRORES)"]
    if normalized != expected:
        raise ValueError("NSE cash CSV schema or INR crore units are unverified.")
    rows = list(reader)
    if not 1 <= len(rows) <= 10:
        raise ValueError("NSE cash CSV has an invalid number of dated category rows.")
    values, dates, problems, seen = {}, set(), [], set()
    today = now.astimezone(IST).date()
    for row in rows:
        category = (row.get(fields[0]) or "").strip().upper()
        if category not in ("FII/FPI", "DII"):
            raise ValueError("NSE cash CSV contains an unexpected category; not a verified FII/DII cash pair.")
        key = "fii" if category == "FII/FPI" else "dii"
        if key in seen:
            raise ValueError("NSE cash CSV contains duplicate categories.")
        seen.add(key)
        try:
            session = datetime.strptime((row.get(fields[1]) or "").strip(), "%d-%b-%Y").date()
            if not 0 <= (today - session).days <= 7:
                raise ValueError("NSE cash session date is future or older than seven days.")
            if session == today and now.astimezone(IST).time() < time(15, 30):
                raise ValueError("NSE cash end-of-day row cannot be used before the session close.")
            buy = _flow_number(row.get(fields[2]))
            sell = _flow_number(row.get(fields[3]))
            net = _flow_number(row.get(fields[4]), signed=True)
            if abs(buy - sell - net) > Decimal("0.02"):
                raise ValueError("NSE cash net does not agree with buy minus sell.")
        except (ValueError, InvalidOperation) as exc:
            problems.append(f"{category}: {exc}")
            continue
        dates.add(session)
        values[key] = {"buy": float(buy), "sell": float(sell), "net": float(net)}
    if len(dates) > 1:
        raise ValueError("NSE FII and DII cash rows disagree on session date.")
    if not values:
        raise ValueError("No valid NSE cash category rows. " + " ".join(problems))
    missing = [key.upper() for key in ("fii", "dii") if key not in values]
    reason = " ".join(problems + ([f"Missing valid cash rows: {', '.join(missing)}."] if missing else []))
    return values, next(iter(dates)).isoformat(), reason


def _capture_flows(now):
    result = {
        "status": "unavailable", "source": "NSE combined NSE/BSE/MSEI capital-market cash activity",
        "sourceUrl": NSE_FLOWS_URL, "documentationUrl": NSE_FLOWS_PAGE,
        "asOf": None, "sessionDate": None, "capturedAt": now.isoformat(),
        "unit": "INR crore", "fii": None, "dii": None,
        "quoteType": "last-reported-end-of-day", "asOfPrecision": "session-date",
        "unitBasis": "Official CSV BUY/SELL/NET headers explicitly specify ₹ Crores.",
        "reason": None,
    }
    warnings = []
    try:
        body, warmed = _nse_body()
        if warmed:
            warnings.append("NSE cash endpoint required one session warmup and one additional request.")
        values, session, reason = _parse_flows(body, now)
    except (URLError, OSError, HTTPException, csv.Error, UnicodeError, ValueError, InvalidOperation):
        result["reason"] = "Official NSE cash flow source unavailable, restricted, stale or invalid; no F&O substitute used."
        return result, warnings + [result["reason"]]
    result.update({
        "status": "available" if len(values) == 2 else "partial",
        "asOf": session, "sessionDate": session,
        "fii": values.get("fii"), "dii": values.get("dii"),
        "reason": reason or "Dated last-reported cash trading activity; not an intraday flow quote.",
    })
    if result["status"] == "partial":
        warnings.append("NSE cash flows are partial. " + reason)
    return result, warnings


def capture_context(client, now: datetime, *, refresh=False) -> dict:
    """Capture context with five-minute oil and one-hour news/cash-flow caches.

    client is the existing KiteClient interface (get/quotes). now must be aware.
    refresh=True forces fresh crude/news/flows, not a fresh daily MCX master.
    Timestamps are UTC ISO strings; flows.asOf/sessionDate are source date-only.
    No result can be future-dated relative to now. Failures use the same TTLs.
    Cache state is process-local, and never holds credentials or broker errors.
    """
    global _crude_cache, _news_cache, _flows_cache
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Context capture requires a timezone-aware now.")
    now = now.astimezone(UTC)
    with _lock:
        crude_cached = not refresh and _valid_cache(_crude_cache, now, timedelta(minutes=5))
        if not crude_cached:
            _crude_cache = (now, _capture_crude(client, now))
        news_cached = not refresh and _valid_cache(_news_cache, now, timedelta(hours=1))
        if not news_cached:
            _news_cache = (now, _capture_news(now))
        flows_cached = not refresh and _valid_cache(_flows_cache, now, timedelta(hours=1))
        if not flows_cached:
            _flows_cache = (now, _capture_flows(now))
        crude, crude_warnings = copy.deepcopy(_crude_cache[1])
        news, attempts, news_warnings = copy.deepcopy(_news_cache[1])
        flows, flow_warnings = copy.deepcopy(_flows_cache[1])
        crude_fetched_at, news_fetched_at = _crude_cache[0], _news_cache[0]
    news = [item for item in news
            if timedelta(0) <= now - datetime.fromisoformat(item["publishedAt"]) <= MAX_AGE]
    if crude and crude["quoteType"] == "live":
        if now - datetime.fromisoformat(crude["asOf"]) > timedelta(minutes=5):
            crude["quoteType"] = "delayed-observation"
            crude_warnings.append("Cached MCX crude is now a delayed observation, not a live quote.")
    dimension = {
        "key": "macro", "label": "Macro/events", "scale": "Adverse to supportive oil context",
        "score": None, "coverage": 0.0, "indicators": [],
        "reason": "No valid dated crude comparison available. Rates, FX and scheduled calendar are not captured.",
    }
    if crude:
        contribution = max(-50.0, min(50.0, -crude["changePct"]))
        score = 50.0 + contribution
        direction = "adverse" if crude["changePct"] > 0 else "supportive" if crude["changePct"] < 0 else "neutral"
        dimension.update({
            "score": score, "coverage": 0.25,
            "reason": "Oil-only context score; rates and FX unavailable. "
                      "Rising oil is adverse and falling oil supportive for India as an oil importer. "
                      "Score = 50 + clamp(-crude change %, -50, +50), on a 0..100 scale with 50 neutral. "
                      "Signed contribution is oil-context score points; heuristic, not a calibrated index return. "
                      "Headlines carry no numeric impact; these are not a scheduled economic calendar.",
            "indicators": [{
                "key": "crude-change", "label": crude["name"], "value": crude["changePct"],
                "unit": "%", "contribution": contribution,
                "contributionUnit": "oil-context score points", "contextOnly": True,
                "direction": direction, "source": crude["source"], "asOf": crude["asOf"],
                "quote": crude,
            }],
        })
    for key, label in (("fii", "FII/FPI cash net buying"), ("dii", "DII cash net buying")):
        if flows[key] is not None:
            dimension["indicators"].append({
                "key": key + "-cash-net", "label": label, "value": flows[key]["net"],
                "unit": "INR crore", "contribution": None, "contextOnly": True,
                "source": flows["source"], "sourceUrl": flows["sourceUrl"],
                "asOf": flows["asOf"], "sessionDate": flows["sessionDate"],
                "activity": flows[key], "reason": "Raw last-reported end-of-day cash activity; no calibrated direction weight.",
            })
    if flows["status"] in ("available", "partial"):
        dimension["coverage"] += 0.25 if flows["status"] == "available" else 0.125
        dimension["reason"] += " Raw dated FII/DII cash activity is shown without a score contribution."
    else:
        dimension["reason"] += " Official FII/DII cash flows unavailable."
    return {
        "dimension": dimension, "news": news,
        "evidence": {
            "capturedAt": now.isoformat(), "crude": crude,
            "flows": flows, "flowsCached": flows_cached, "flowsCacheSeconds": 3600,
            "crudeFetchedAt": crude_fetched_at.isoformat(), "crudeCached": crude_cached,
            "newsFetchedAt": news_fetched_at.isoformat(), "newsCached": news_cached,
            "newsSources": attempts, "newsMaxAgeDays": 7, "newsLimit": MAX_NEWS,
            "newsTextPolicy": "Untrusted bounded publisher titles only; never instructions or numeric forecast inputs.",
            "scheduledCalendar": False, "crudeCacheSeconds": 300, "newsCacheSeconds": 3600,
        },
        "warnings": crude_warnings + news_warnings + flow_warnings,
    }
