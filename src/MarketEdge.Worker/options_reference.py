"""Automatic public exchange calendars and explicitly labelled index weight proxies."""
from __future__ import annotations

import copy
import csv
import io
import json
import logging
import re
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from lxml import html
from openpyxl.utils.exceptions import InvalidFileException

from options_reference_store import persist_sessions, persist_weights

logger = logging.getLogger(__name__)
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
NSE_HOLIDAYS = "https://www.nseindia.com/api/holiday-master?type=trading"
NSE_HOURS = "https://www.nseindia.com/static/market-data/market-timings"
BSE_HOLIDAYS = "https://beta.bseindia.com/static/markets/marketinfo/listholi.aspx"
BSE_HOURS = "https://beta.bseindia.com/downloads/UploadDocs/Notices/20260801-1/20260801-1.pdf"
MEMBERS = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
MEMBERS_ARCHIVE = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
ETF_BASE = "https://mf.nipponindiaim.com/InvestorServices/FactsheetsDocuments/"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SPECIAL_SESSIONS = {
    "NIFTY50": {
        date(2026, 11, 8): ("14:20", "15:30",
            "https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf"),
    },
    "SENSEX": {
        date(2026, 11, 8): ("14:20", "15:30",
            "https://beta.bseindia.com/downloads/UploadDocs/Notices/20261019-9/20261019-9.pdf"),
    },
}
_lock = threading.Lock()
_status_lock = threading.Lock()
_attempts = {}
_status = {}


class PublicReferenceError(RuntimeError):
    pass


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _text(page):
    parser = _PageText()
    parser.feed(page)
    return re.sub(r"\s+", " ", " ".join(parser.parts))


def _download(url):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "*/*",
        "Referer": "https://www.nseindia.com/" if "nse" in url else url,
    })
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read(10_000_001)
    except HTTPError as exc:
        raise PublicReferenceError(f"Public reference download returned HTTP {exc.code}: {url}") from None
    except (URLError, TimeoutError) as exc:
        raise PublicReferenceError(f"Public reference download unavailable: {url}") from exc
    if not data or len(data) > 10_000_000:
        raise PublicReferenceError(f"Public reference payload is empty or too large: {url}")
    return data


def _nse_calendar(download):
    payload = json.loads(download(NSE_HOLIDAYS))
    records = payload.get("CM") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise PublicReferenceError("NSE capital-market holiday calendar is missing.")
    holidays = {}
    for row in records:
        day = datetime.strptime(row["tradingDate"], "%d-%b-%Y").date()
        if day in holidays:
            raise PublicReferenceError("NSE calendar contains a duplicate holiday.")
        holidays[day] = str(row.get("description", ""))
    page = _text(download(NSE_HOURS).decode("utf-8-sig"))
    pattern = r"Normal\s*(?:/\s*Odd.?lot\s*)?Market\s+{}\s*:?\s*(\d{{2}}:\d{{2}})"
    opening = re.search(pattern.format("Open"), page, re.I)
    closing = re.search(pattern.format("Close"), page, re.I)
    if not opening or not closing:
        raise PublicReferenceError("NSE normal cash-session hours could not be verified.")
    return holidays, opening[1], closing[1], NSE_HOLIDAYS + ";cash-hours=" + NSE_HOURS


def _bse_calendar(download):
    page = download(BSE_HOLIDAYS).decode("utf-8-sig")
    heading = re.search(r"Trading\s+Holidays\s+for\s+(\d{4})\s*[^A-Za-z]*Equity", _text(page), re.I)
    if not heading:
        raise PublicReferenceError("BSE calendar does not identify its equity-calendar year.")
    year = int(heading[1])
    root = html.fromstring(page)
    section = root.xpath("//*[@id='eq24' or @name='eq24']")
    if not section:
        raise PublicReferenceError("BSE equity-calendar section is missing.")
    tables = section[0].xpath(".//table") or section[0].xpath("following::table[1]")
    holidays = {}
    for table in tables:
        for row in table.xpath(".//tr"):
            cells = [" ".join(cell.itertext()).strip() for cell in row.xpath("./td")]
            if len(cells) < 4:
                continue
            match = next((re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}", cell) for cell in cells
                          if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}", cell)), None)
            if not match:
                continue
            day = datetime.strptime(match[0], "%d-%b-%Y" if len(match[0].split("-")[-1]) == 4 else "%d-%b-%y").date()
            if day.year != year:
                raise PublicReferenceError("BSE holiday row differs from its declared calendar year.")
            holidays[day] = cells[1]
    evidence = download(BSE_HOURS)
    if not evidence.startswith(b"%PDF"):
        raise PublicReferenceError("BSE cash-session hours notice is not a PDF.")
    # BSE notice 20260801-1, effective 3 August 2026: regular T+1 cash session.
    return holidays, "09:15", "15:30", BSE_HOLIDAYS + ";cash-hours=" + BSE_HOURS


def build_sessions(index_code, holidays, opening, closing, today):
    if not opening < closing:
        raise PublicReferenceError("Published cash-session hours are invalid.")
    years = {day.year for day in holidays}
    if today.year not in years:
        raise PublicReferenceError(f"No published {today.year} exchange holiday calendar is available.")
    for year in years:
        if sum(day.year == year for day in holidays) < 10:
            raise PublicReferenceError(f"Public holiday calendar for {year} appears incomplete.")
    result, unknown_special = [], []
    start = today - timedelta(days=14)
    for year in sorted(years):
        if year < start.year:
            continue
        day = max(date(year, 1, 1), start)
        while day.year == year:
            special = SPECIAL_SESSIONS[index_code].get(day)
            description = holidays.get(day, "")
            if special:
                is_open, start_at, end_at = True, special[0], special[1]
            elif re.search(r"laxmi|lakshmi|muhurat", description, re.I):
                unknown_special.append(day.isoformat())
                day += timedelta(days=1)
                continue
            else:
                is_open = day.weekday() < 5 and day not in holidays
                start_at, end_at = (opening, closing) if is_open else (None, None)
            result.append({"sessionDate": day.isoformat(), "isTradingDay": is_open,
                           "openTime": start_at, "closeTime": end_at})
            day += timedelta(days=1)
    return result, unknown_special


def _member_symbols(members_raw):
    try:
        members = list(csv.DictReader(io.StringIO(members_raw.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PublicReferenceError("Public NIFTY50 membership is not a valid CSV.") from exc
    if len(members) != 50:
        raise PublicReferenceError("NIFTY50 public membership must contain all 50 constituents.")
    if any(not isinstance(row.get("ISIN Code"), str) or not isinstance(row.get("Symbol"), str)
           or not row["ISIN Code"].strip() or not row["Symbol"].strip() for row in members):
        raise PublicReferenceError("Public NIFTY50 membership columns are missing or incomplete.")
    by_isin = {row["ISIN Code"].strip(): row["Symbol"].strip().upper() for row in members}
    if len(by_isin) != 50 or len(set(by_isin.values())) != 50:
        raise PublicReferenceError("Public membership contains duplicate ISINs/symbols.")
    return by_isin


def parse_etf_weights(members_raw, workbook_raw, today, source_url):
    by_isin = _member_symbols(members_raw)
    try:
        frame = pd.read_excel(io.BytesIO(workbook_raw), sheet_name="NB", header=None, engine="openpyxl")
    except (ValueError, OSError, zipfile.BadZipFile, InvalidFileException) as exc:
        raise PublicReferenceError("Public ETF portfolio workbook could not be parsed.") from exc
    text = " ".join(str(value) for value in frame.head(5).values.flat)
    if "NIPPON INDIA ETF NIFTY 50 BEES" not in text.upper():
        raise PublicReferenceError("Portfolio sheet is not the Nifty BeES fund.")
    match = re.search(r"as\s+on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", text, re.I)
    if not match:
        raise PublicReferenceError("ETF portfolio source date is missing.")
    effective = datetime.strptime(re.sub(r",\s*", ",", match[1]), "%B %d,%Y").date()
    if not 0 <= (today - effective).days <= 62:
        raise PublicReferenceError("ETF disclosure is future-dated or older than 62 days.")
    header = next((row for _, row in frame.iterrows()
                   if any(str(value).strip().upper() == "ISIN" for value in row)), None)
    if header is None:
        raise PublicReferenceError("ETF holdings header is missing.")
    isin_col = next(column for column, value in header.items() if str(value).strip().upper() == "ISIN")
    nav_col = next((column for column, value in header.items() if "% TO NAV" in str(value).upper()), None)
    if nav_col is None:
        raise PublicReferenceError("ETF holdings NAV percentages are missing.")
    holdings = {}
    for _, row in frame.iterrows():
        isin = str(row[isin_col]).strip()
        if not re.fullmatch(r"INE[A-Z0-9]{9}", isin):
            continue
        if isin in holdings:
            raise PublicReferenceError("ETF holdings contain duplicate equity ISINs.")
        try:
            fraction = Decimal(str(row[nav_col]))
        except DecimalException as exc:
            raise PublicReferenceError("ETF equity NAV fraction is not numeric.") from exc
        if not fraction.is_finite() or fraction <= 0 or fraction > 1:
            raise PublicReferenceError("ETF equity NAV fraction is invalid.")
        holdings[isin] = fraction
    if set(holdings) != set(by_isin):
        raise PublicReferenceError("ETF holdings and current official NIFTY50 membership do not match by ISIN.")
    total = sum(holdings.values())
    if not Decimal(".98") <= total <= Decimal("1.02"):
        raise PublicReferenceError("ETF equity holdings are not sufficiently close to full NAV.")
    weights = [{
        "symbol": by_isin[isin], "isin": isin,
        "rawEtfNavPct": float(fraction * 100),
        "weightPct": float((fraction / total * 100).quantize(Decimal(".000001"))),
    } for isin, fraction in holdings.items()]
    source = f"ETF_PROXY:equity-normalized;asof={effective};{source_url};members={MEMBERS}"
    return effective, weights, source, {
        "sourceType": "etf-holdings-proxy", "sourceDate": effective.isoformat(),
        "sourceUrl": source_url, "membershipUrl": MEMBERS, "constituents": 50,
        "rawEquityNavPct": float(total * 100),
        "method": "Equity NAV percentages normalized to 100%; excludes cash; not official/live index weights.",
    }


def fetch_etf_weights(today, download=_download):
    membership_errors = []
    for membership_url in (MEMBERS, MEMBERS_ARCHIVE):
        try:
            membership = download(membership_url)
            _member_symbols(membership)
            break
        except PublicReferenceError as exc:
            membership_errors.append(str(exc))
    else:
        raise PublicReferenceError("Official NIFTY50 membership unavailable: " + "; ".join(membership_errors))
    month_end = today.replace(day=1) - timedelta(days=1)
    failures = []
    for _ in range(2):
        name = f"NIMF-MONTHLY-PORTFOLIO-{month_end.day:02d}-{MONTHS[month_end.month - 1]}-{month_end.year % 100:02d}.xls"
        url = ETF_BASE + name
        try:
            return parse_etf_weights(membership, download(url), today, url)
        except PublicReferenceError as exc:
            failures.append(str(exc))
        month_end = month_end.replace(day=1) - timedelta(days=1)
    raise PublicReferenceError("No current complete Nifty BeES proxy could be loaded: " + "; ".join(failures))


def reference_status():
    with _status_lock:
        return copy.deepcopy(_status)


def ensure_reference_data(conn, index_code, now, *, force=False, download=_download):
    if index_code not in ("NIFTY50", "SENSEX"):
        raise ValueError("Unsupported reference index.")
    today = now.astimezone(IST).date()
    result = {"warnings": []}
    with _lock:
        for kind in ("calendar", "weights"):
            if kind == "weights" and index_code != "NIFTY50":
                result[kind] = {"status": "unavailable", "reason": "No verified complete public SENSEX weight source."}
                continue
            key = index_code, kind
            previous = _attempts.get(key)
            if previous and not force and (
                previous["success"] and previous["day"] == today
                or not previous["success"] and now - previous["at"] < timedelta(minutes=5)
            ):
                result[kind] = copy.deepcopy(previous["result"])
                if result[kind].get("warning"):
                    result["warnings"].append(result[kind]["warning"])
                continue
            try:
                if kind == "calendar":
                    holidays, opening, closing, source = (_nse_calendar(download) if index_code == "NIFTY50"
                                                         else _bse_calendar(download))
                    sessions, unknown = build_sessions(index_code, holidays, opening, closing, today)
                    groups = {}
                    for session in sessions:
                        special = SPECIAL_SESSIONS[index_code].get(date.fromisoformat(session["sessionDate"]))
                        groups.setdefault(special[2] if special else source, []).append(session)
                    stored = {"supplied": 0, "written": 0, "preservedManual": 0}
                    for session_source, group in groups.items():
                        if session_source != source and not download(session_source).startswith(b"%PDF"):
                            raise PublicReferenceError("Exceptional cash-session notice is not a PDF.")
                        summary = persist_sessions(conn, index_code, group, session_source)
                        for field in stored:
                            stored[field] += summary[field]
                    state = {"status": "ready" if not unknown else "partial",
                             "from": sessions[0]["sessionDate"], "to": sessions[-1]["sessionDate"],
                             "source": source, "spotOpenTime": opening, "spotCloseTime": closing,
                             "exceptionSources": [item for item in groups if item != source],
                             "unknownSpecialSessions": unknown, **stored}
                    if unknown:
                        state["warning"] = "Special-session hours not yet verified for: " + ", ".join(unknown)
                else:
                    effective, weights, source, evidence = fetch_etf_weights(today, download)
                    stored = persist_weights(conn, index_code, effective, weights, source, today=today)
                    state = {"status": "ready", **evidence, **stored}
                    state["warning"] = (f"NIFTY50 weights use a dated equity-normalized ETF proxy as of {effective}; "
                                        "not official/live index weights.")
                success = True
            except (PublicReferenceError, ValueError, KeyError, TypeError, ImportError) as exc:
                conn.rollback()
                state = {"status": "unavailable", "warning": f"Automatic {index_code} {kind} refresh failed: {exc}"}
                logger.warning("%s", state["warning"])
                success = False
            state["lastAttemptAt"] = now.isoformat()
            _attempts[key] = {"at": now, "day": today, "success": success, "result": copy.deepcopy(state)}
            result[kind] = state
            if state.get("warning"):
                result["warnings"].append(state["warning"])
        with _status_lock:
            _status[index_code] = copy.deepcopy(result)
    return result
