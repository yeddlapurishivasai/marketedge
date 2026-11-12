"""Kite transport and SQL persistence for immutable options snapshots."""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from db import get_connection

logger = logging.getLogger(__name__)
UTC = timezone.utc
_transport_lock = threading.Lock()
_last_request = 0.0
_instrument_lock = threading.Lock()


class KiteSessionExpired(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def sqltime(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def dumps(value) -> str:
    return json.dumps(value, default=json_default, allow_nan=False, separators=(",", ":"))


def rows(cursor) -> list[dict]:
    names = [col[0] for col in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def token_for(conn, user_key: str) -> str:
    row = conn.cursor().execute(
        "SELECT EncryptedToken, ExpiresAt, Status FROM [options].[BrokerConnections] WHERE UserKey = ?",
        user_key,
    ).fetchone()
    if not row or row.Status != "connected" or not row.EncryptedToken:
        raise KiteSessionExpired("Connect or re-login to Zerodha before requesting live data.")
    if not row.ExpiresAt or row.ExpiresAt.replace(tzinfo=UTC) <= utcnow():
        invalidate_session(conn, user_key, "Zerodha session expired; re-login required.")
        raise KiteSessionExpired("Zerodha session expired; re-login required.")
    key_text = os.environ.get("MARKETEDGE_OPTIONS_TOKEN_KEY", "")
    try:
        key = base64.b64decode(key_text, validate=True)
    except ValueError as exc:
        raise RuntimeError("MARKETEDGE_OPTIONS_TOKEN_KEY must be a base64 AES-256 key.") from exc
    if len(key) != 32:
        raise RuntimeError("MARKETEDGE_OPTIONS_TOKEN_KEY must decode to exactly 32 bytes.")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    encrypted = base64.b64decode(row.EncryptedToken, validate=True)
    if len(encrypted) < 29:
        raise RuntimeError("Stored broker credential is invalid; reconnect Zerodha.")
    nonce, tag, cipher = encrypted[:12], encrypted[12:28], encrypted[28:]
    try:
        return AESGCM(key).decrypt(nonce, cipher + tag, user_key.encode()).decode()
    except InvalidTag:
        raise RuntimeError("Broker credential could not be decrypted for this account; check the shared key and reconnect.") from None


def invalidate_session(conn, user_key: str, reason: str) -> None:
    conn.cursor().execute(
        """UPDATE [options].[BrokerConnections]
        SET Status = 'relogin-required', EncryptedToken = NULL, LastError = ?, UpdatedAt = ?
        WHERE UserKey = ?""",
        reason, sqltime(utcnow()), user_key,
    )
    conn.commit()


class KiteClient:
    def __init__(self, conn, user_key: str):
        self.conn = conn
        self.user_key = user_key
        self.api_key = os.environ.get("MARKETEDGE_KITE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("MARKETEDGE_KITE_API_KEY is required for the options worker.")
        self.token = token_for(conn, user_key)

    def get(self, path: str, params: list[tuple[str, str]] | None = None, text=False):
        global _last_request
        url = "https://api.kite.trade" + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={
            "Authorization": f"token {self.api_key}:{self.token}",
            "X-Kite-Version": "3",
        })
        with _transport_lock:
            delay = 1.1 - (time.monotonic() - _last_request)
            if delay > 0:
                time.sleep(delay)
            _last_request = time.monotonic()
            try:
                with urlopen(request, timeout=30) as response:
                    body = response.read().decode("utf-8")
            except HTTPError as exc:
                error_type = None
                try:
                    error_type = json.loads(exc.read().decode("utf-8")).get("error_type")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    logger.warning("Kite error response was not valid JSON (HTTP %s)", exc.code)
                if exc.code == 401 or error_type == "TokenException":
                    invalidate_session(self.conn, self.user_key, "Broker rejected session; re-login required.")
                    raise KiteSessionExpired("Broker rejected session; re-login required.") from None
                raise RuntimeError(f"Kite request failed with HTTP {exc.code}; no snapshot was fabricated.") from None
            except URLError:
                raise RuntimeError("Kite could not be reached; check connectivity and retry explicitly.") from None
        if text:
            return body
        payload = json.loads(body)
        if payload.get("status") != "success":
            raise RuntimeError("Kite returned an unsuccessful response; snapshot unavailable.")
        return payload["data"]

    def quotes(self, instruments: list[str]) -> dict:
        result = {}
        for start in range(0, len(instruments), 400):
            result.update(self.get("/quote", [("i", symbol) for symbol in instruments[start:start + 400]]))
        return result

    def bars(self, token: int, now: datetime, *, completed_through: date | None = None) -> list[dict]:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo("Asia/Kolkata"))
        cutoff = completed_through or local.date() - timedelta(days=1)
        if cutoff > local.date():
            raise ValueError("Completed candle cutoff cannot be in the future.")
        data = self.get(f"/instruments/historical/{token}/day", [
            ("from", (local - timedelta(days=550)).strftime("%Y-%m-%d 00:00:00")),
            ("to", local.strftime("%Y-%m-%d %H:%M:%S")),
        ])
        return [
            {"date": candle[0][:10], "open": candle[1], "high": candle[2],
             "low": candle[3], "close": candle[4], "volume": candle[5]}
            for candle in data["candles"] if candle[0][:10] <= cutoff.isoformat()
        ]


def load_instruments(conn, client: KiteClient, index_code: str) -> list[dict]:
    """Keep contracts by natural key because exchanges recycle numeric tokens."""
    with _instrument_lock:
        newest = conn.cursor().execute(
            "SELECT MAX(UpdatedAt) FROM [options].[DerivativeInstruments] WHERE Underlying = ?", index_code,
        ).fetchone()[0]
        if newest is None or newest.date() < utcnow().date():
            master = csv.DictReader(io.StringIO(client.get("/instruments", text=True)))
            wanted_name = "NIFTY" if index_code == "NIFTY50" else "SENSEX"
            records = []
            updated = sqltime(utcnow())
            for item in master:
                if item["exchange"] not in ("NFO", "BFO") or item["name"] != wanted_name:
                    continue
                records.append((
                    item["exchange"], item["tradingsymbol"], int(item["instrument_token"]),
                    index_code, item["expiry"] or None, float(item["strike"] or 0),
                    item["instrument_type"], int(item["lot_size"]), float(item["tick_size"]), updated,
                ))
            if not records:
                raise RuntimeError(f"No {index_code} derivative contracts are available from the broker.")
            conn.cursor().executemany(
                """MERGE [options].[DerivativeInstruments] AS t
                USING (SELECT ? Exchange, ? TradingSymbol, ? InstrumentToken, ? Underlying,
                    CAST(? AS date) Expiry, ? Strike, ? InstrumentType, ? LotSize, ? TickSize,
                    CAST(? AS datetime2) UpdatedAt) AS s
                ON t.Exchange=s.Exchange AND t.TradingSymbol=s.TradingSymbol
                WHEN MATCHED THEN UPDATE SET InstrumentToken=s.InstrumentToken,
                    Expiry=s.Expiry, Strike=s.Strike, InstrumentType=s.InstrumentType,
                    UpdatedAt=s.UpdatedAt, LotSize=s.LotSize, TickSize=s.TickSize
                WHEN NOT MATCHED THEN INSERT
                    (Exchange,TradingSymbol,InstrumentToken,Underlying,Expiry,Strike,InstrumentType,LotSize,TickSize,UpdatedAt)
                    VALUES(s.Exchange,s.TradingSymbol,s.InstrumentToken,s.Underlying,s.Expiry,s.Strike,
                        s.InstrumentType,s.LotSize,s.TickSize,s.UpdatedAt);""", records,
            )
            conn.commit()
    result = rows(conn.cursor().execute(
        """SELECT InstrumentToken,Exchange,TradingSymbol,Underlying,Expiry,Strike,InstrumentType,LotSize,TickSize
        FROM [options].[DerivativeInstruments] WHERE Underlying=? AND Expiry>=CAST(GETUTCDATE() AS date)
        AND UpdatedAt=(SELECT MAX(UpdatedAt) FROM [options].[DerivativeInstruments] WHERE Underlying=?)""",
        index_code, index_code,
    ))
    return [{
        "instrumentToken": item["InstrumentToken"], "exchange": item["Exchange"],
        "tradingSymbol": item["TradingSymbol"], "underlying": item["Underlying"],
        "expiry": item["Expiry"].isoformat(), "strike": float(item["Strike"]),
        "instrumentType": item["InstrumentType"], "lotSize": item["LotSize"],
        "tickSize": float(item["TickSize"]),
    } for item in result]


def load_sessions(conn, index_code: str, now: datetime) -> list[dict]:
    records = rows(conn.cursor().execute(
        """SELECT SessionDate,IsTradingDay,OpenTime,CloseTime,IsWeeklyExpiry,IsMonthlyExpiry,Source
        FROM [options].[ExchangeSessions] WHERE IndexCode=? AND SessionDate>=?
        ORDER BY SessionDate""", index_code, (now - timedelta(days=14)).date(),
    ))
    return [{
        "sessionDate": record["SessionDate"].isoformat(), "isTradingDay": bool(record["IsTradingDay"]),
        "openTime": str(record["OpenTime"])[:5] if record["OpenTime"] else None,
        "closeTime": str(record["CloseTime"])[:5] if record["CloseTime"] else None,
        "isWeeklyExpiry": bool(record["IsWeeklyExpiry"]),
        "isMonthlyExpiry": bool(record["IsMonthlyExpiry"]),
        "source": record["Source"],
    } for record in records]


def observed_time(quote: dict) -> datetime | None:
    text = quote.get("timestamp")
    if not text:
        return None
    from zoneinfo import ZoneInfo

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return parsed.astimezone(UTC) if parsed.year > 1970 else None


def last_session_snapshot(conn, user_key, index_code, spot_symbol, sessions, now, mode):
    from options_engine import _aware, last_session_reference_window, snapshot_policy

    window = last_session_reference_window(now, sessions)
    if window is None or mode == "expiry-intraday":
        return None
    # During an open session an old snapshot must never become a planning entry.
    if snapshot_policy(now, sessions, _aware(window["windowStart"]), mode)["executionStatus"] != "planning-only":
        return None
    start, end = _aware(window["windowStart"]), _aware(window["windowEnd"])
    batches = rows(conn.cursor().execute(
        """SELECT TOP 1 b.Id,b.CapturedAt FROM [options].[MarketSnapshotBatches] b
        JOIN [options].[InstrumentQuoteSnapshots] q ON q.BatchId=b.Id
        WHERE b.UserKey=? AND b.IndexCode=? AND b.Status IN ('complete','partial')
          AND q.Exchange+':'+q.TradingSymbol=? AND q.ObservedAt>=? AND q.ObservedAt<=?
          AND b.CapturedAt<=? ORDER BY b.CapturedAt DESC,b.Id DESC""",
        user_key, index_code, spot_symbol, sqltime(start), sqltime(end), sqltime(now),
    ))
    if not batches:
        return None
    batch = batches[0]
    records = rows(conn.cursor().execute(
        """SELECT Exchange,TradingSymbol,PayloadJson FROM [options].[InstrumentQuoteSnapshots]
        WHERE BatchId=? AND ObservedAt>=? AND ObservedAt<=?""",
        batch["Id"], sqltime(start), sqltime(end),
    ))
    quotes = {}
    for record in records:
        payload = json.loads(record["PayloadJson"])
        observed = observed_time(payload)
        if observed is not None and start <= observed <= end:
            quotes[record["Exchange"] + ":" + record["TradingSymbol"]] = payload
    spot = quotes.get(spot_symbol)
    if not spot or not spot.get("last_price") or snapshot_policy(
            now, sessions, observed_time(spot), mode)["executionStatus"] != "planning-only":
        raise ValueError("Persisted last-session snapshot is inconsistent with its source timestamps.")
    return {"quotes": quotes, "batchId": batch["Id"], "capturedAt": batch["CapturedAt"],
            "source": "persisted-broker-snapshot",
            "reason": "Broker supplied missing or unusable timestamps; using an actual same-account last-session snapshot for planning only."}


def save_snapshot(conn, user_key: str, index_code: str, quotes: dict) -> int:
    now = utcnow()
    cursor = conn.cursor()
    batch_id = cursor.execute(
        """INSERT INTO [options].[MarketSnapshotBatches]
        (UserKey,IndexCode,CapturedAt,Status,CoverageJson,Source)
        OUTPUT INSERTED.Id VALUES (?,?,?,'collecting',?,'kite')""",
        user_key, index_code, sqltime(now), dumps({"received": len(quotes)}),
    ).fetchone()[0]
    records = []
    for symbol, quote in quotes.items():
        observed = observed_time(quote)
        if observed is None or not quote.get("last_price"):
            logger.warning("Skipping quote without provider timestamp/price for %s", symbol)
            continue
        exchange, trading_symbol = symbol.split(":", 1)
        depth = quote.get("depth") or {}
        buy = depth.get("buy") or []
        sell = depth.get("sell") or []
        records.append((
            batch_id, quote["instrument_token"], exchange, trading_symbol, sqltime(observed),
            quote["last_price"], buy[0]["price"] if buy else None, sell[0]["price"] if sell else None,
            quote.get("volume"), quote.get("oi"), dumps(quote),
        ))
    if records:
        cursor.executemany(
            """INSERT INTO [options].[InstrumentQuoteSnapshots]
            (BatchId,InstrumentToken,Exchange,TradingSymbol,ObservedAt,LastPrice,Bid,Ask,Volume,OpenInterest,PayloadJson)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", records,
        )
    cursor.execute(
        "UPDATE [options].[MarketSnapshotBatches] SET Status=?,CoverageJson=? WHERE Id=?",
        "complete" if len(records) == len(quotes) and records else "partial",
        dumps({"received": len(quotes), "persisted": len(records)}), batch_id,
    )
    conn.commit()
    return batch_id


def flattened_quotes(instruments: list[dict], quotes: dict) -> list[dict]:
    output = []
    for instrument in instruments:
        quote = quotes.get(instrument["exchange"] + ":" + instrument["tradingSymbol"])
        if not quote or observed_time(quote) is None:
            continue
        depth = quote.get("depth") or {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        traded_at = observed_time({"timestamp": quote.get("last_trade_time")})
        output.append({
            **instrument, "lastPrice": quote["last_price"],
            "bid": buys[0]["price"] if buys else None,
            "ask": sells[0]["price"] if sells else None,
            "observedAt": observed_time(quote).isoformat(),
            "lastTradeAt": traded_at.isoformat() if traded_at is not None else None,
            "volume": quote.get("volume"), "openInterest": quote.get("oi"),
        })
    return output


def derivatives_context(spot: float, contracts: list[dict], quotes: dict, now: datetime,
                        quote_policy: dict | None = None) -> dict:
    from options_engine import quote_in_policy

    call_oi = put_oi = 0
    indicators = []
    expiries = sorted({item["expiry"] for item in contracts if item["instrumentType"] in ("CE", "PE")})
    nearest = expiries[0] if expiries else None
    covered = 0
    for instrument in contracts:
        quote = quotes.get(instrument["exchange"] + ":" + instrument["tradingSymbol"])
        observed = observed_time(quote) if quote else None
        if observed is None or not quote_in_policy(observed, now, quote_policy):
            continue
        if instrument["instrumentType"] == "FUT" and quote.get("last_price", 0) > 0:
            indicators.append({
                "key": "basis-" + instrument["expiry"], "label": "Futures basis " + instrument["expiry"],
                "value": round((quote["last_price"] / spot - 1) * 100, 4), "unit": "%",
                "direction": "context", "contribution": 0,
                "reason": "Futures/spot premium includes carry and is not an isolated directional signal.",
            })
        if instrument["expiry"] != nearest or instrument["instrumentType"] not in ("CE", "PE"):
            continue
        oi = quote.get("oi")
        if not isinstance(oi, (int, float)) or oi < 0:
            continue
        covered += 1
        if instrument["instrumentType"] == "CE":
            call_oi += oi
        else:
            put_oi += oi
    total = call_oi + put_oi
    indicators.append({
        "key": "window-oi-pcr", "label": "Nearest-expiry sampled-strike OI put/call ratio",
        "value": round(put_oi / call_oi, 4) if call_oi else None, "unit": "ratio",
        "direction": "context", "contribution": 0,
        "reason": "Bounded captured strike window, not the full exchange chain. OI does not identify buyers, writers or dealer direction.",
    })
    return {
        "key": "derivatives", "label": "Derivatives positioning",
        "asOf": quote_policy.get("quoteAsOf") if quote_policy else now.isoformat(),
        "observationBasis": ("last-session" if quote_policy and quote_policy.get("executionStatus") == "planning-only"
                             else "unavailable" if quote_policy and quote_policy.get("executionStatus") == "unavailable" else "live"),
        "scale": "Call-heavy to put-heavy OI (not bearish to bullish)",
        "score": round(put_oi / total * 100, 2) if total else None,
        "coverage": covered / max(1, sum(1 for item in contracts
                                      if item["expiry"] == nearest and item["instrumentType"] in ("CE", "PE"))),
        "indicators": indicators,
    }
