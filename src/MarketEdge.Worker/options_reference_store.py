"""Validated, bounded SQL persistence for explicitly sourced reference records.

Call at a transaction-safe boundary with autocommit disabled. Each call owns its
commit/rollback; a manual constituent snapshot is never merged with automation.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Number

logger = logging.getLogger(__name__)
MAX_SESSIONS = 3660
MAX_CONSTITUENTS = 1000
EXCHANGES = {"NIFTY50": "NSE", "SENSEX": "BSE"}


def _source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Reference source provenance is required.")
    source = source.strip()
    if not source.startswith("auto:"):
        source = "auto:" + source
    if not source[5:].strip() or len(source) > 500 or any(ord(c) < 32 for c in source):
        raise ValueError("Automatic source must contain provenance and fit 500 characters.")
    return source


def _date(value, field):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must be an explicit ISO date.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO date.") from exc


def _symbol(value):
    if not isinstance(value, str):
        raise ValueError("Constituent symbol must be a string.")
    value = value.strip().upper()
    if value.endswith((".NS", ".BO")):
        value = value[:-3]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&_.-]{0,99}", value):
        raise ValueError("Constituent symbol is invalid.")
    return value


def _list(records, maximum, label):
    if not isinstance(records, (list, tuple)) or not 1 <= len(records) <= maximum:
        raise ValueError(f"{label} must contain between 1 and {maximum} explicit records.")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"Every {label} record must be a dictionary.")


def _transactional(conn):
    if getattr(conn, "autocommit", False):
        raise ValueError("Reference persistence requires autocommit disabled.")


def _rows(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def persist_sessions(conn, index_code, sessions, source):
    """Persist only supplied dates; return supplied/written/preservedManual counts."""
    if index_code not in EXCHANGES:
        raise ValueError("Sessions support only NIFTY50 (NSE) and SENSEX (BSE).")
    provenance = _source(source)
    _list(sessions, MAX_SESSIONS, "sessions")
    validated, seen = [], set()
    for session in sessions:
        day = _date(session.get("sessionDate"), "sessionDate")
        if day in seen:
            raise ValueError(f"Duplicate sessionDate: {day}.")
        seen.add(day)
        trading = session.get("isTradingDay")
        if type(trading) is not bool:
            raise ValueError("isTradingDay must be an explicit boolean.")
        opening, closing = session.get("openTime"), session.get("closeTime")
        if trading:
            for value in (opening, closing):
                if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise ValueError("Trading sessions require valid HH:mm openTime and closeTime.")
            if opening >= closing:
                raise ValueError("Session openTime must precede closeTime.")
        elif opening is not None or closing is not None:
            raise ValueError("Non-trading sessions must have null openTime and closeTime.")
        validated.append((day, trading, opening, closing))
    _transactional(conn)
    written = preserved = 0
    try:
        cursor = conn.cursor()
        for day, trading, opening, closing in sorted(validated):
            existing = cursor.execute(
                """SELECT Source FROM [options].[ExchangeSessions] WITH (UPDLOCK,HOLDLOCK)
                WHERE IndexCode=? AND SessionDate=?""", index_code, day,
            ).fetchone()
            if existing is not None:
                if not isinstance(existing[0], str) or not existing[0].startswith("auto:"):
                    preserved += 1
                    continue
                cursor.execute(
                    """UPDATE [options].[ExchangeSessions]
                    SET Exchange=?,IsTradingDay=?,OpenTime=?,CloseTime=?,
                        IsWeeklyExpiry=0,IsMonthlyExpiry=0,Source=?
                    WHERE IndexCode=? AND SessionDate=? AND Source LIKE 'auto:%'""",
                    EXCHANGES[index_code], trading, opening, closing, provenance, index_code, day,
                )
            else:
                cursor.execute(
                    """INSERT INTO [options].[ExchangeSessions]
                    (IndexCode,SessionDate,Exchange,IsTradingDay,OpenTime,CloseTime,
                     IsWeeklyExpiry,IsMonthlyExpiry,Source) VALUES (?,?,?,?,?,?,0,0,?)""",
                    index_code, day, EXCHANGES[index_code], trading, opening, closing, provenance,
                )
            written += 1
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Automatic session persistence failed for %s; transaction rolled back.", index_code)
        raise
    return {"supplied": len(validated), "written": written, "preservedManual": preserved}


def persist_weights(conn, index_code, effective_date, weights, source, *, today=None, authoritative_count=50):
    """Replace only this source's dated snapshot after validating every mapping.

    An explicit authoritative_count permits a provider-declared constituent count
    other than 50. Other automatic sources on the same date are rejected rather
    than combined. Dates outside this snapshot are never modified.
    """
    if index_code != "NIFTY50":
        raise ValueError("Automatic constituent weights currently support only NIFTY50.")
    provenance = _source(source)
    effective = _date(effective_date, "effective_date")
    current = date.today() if today is None else _date(today, "today")
    if effective > current:
        raise ValueError("Constituent effective_date cannot be in the future.")
    if type(authoritative_count) is not int or not 1 <= authoritative_count <= MAX_CONSTITUENTS:
        raise ValueError(f"authoritative_count must be between 1 and {MAX_CONSTITUENTS}.")
    _list(weights, MAX_CONSTITUENTS, "weights")
    if len(weights) != authoritative_count:
        raise ValueError(f"Expected exactly {authoritative_count} constituents.")
    validated, seen = [], set()
    for entry in weights:
        symbol = _symbol(entry.get("symbol"))
        if symbol in seen:
            raise ValueError(f"Duplicate canonical constituent symbol: {symbol}.")
        seen.add(symbol)
        value = entry.get("weightPct")
        if isinstance(value, bool) or not isinstance(value, (Number, Decimal)):
            raise ValueError(f"weightPct must be numeric for {symbol}.")
        try:
            weight = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"Invalid weightPct for {symbol}.") from exc
        if not weight.is_finite() or not 0 < weight <= 100:
            raise ValueError(f"weightPct must be finite, positive and at most 100 for {symbol}.")
        try:
            rounded = weight.quantize(Decimal("0.000001"))
        except InvalidOperation as exc:
            raise ValueError(f"weightPct supports at most six decimal places for {symbol}.") from exc
        if weight != rounded:
            raise ValueError(f"weightPct supports at most six decimal places for {symbol}.")
        validated.append((symbol, weight))
    if abs(sum((weight for _, weight in validated), Decimal(0)) - 100) > Decimal("0.01"):
        raise ValueError("Constituent weights must total 100 within 0.01 percentage points.")
    _transactional(conn)
    try:
        cursor = conn.cursor()
        catalogue = _rows(cursor.execute("SELECT Id,Symbol FROM dbo.IndianStocks"))
        mapping = {}
        for stock in catalogue:
            try:
                symbol = _symbol(stock["Symbol"])
            except ValueError:
                continue
            if symbol in seen:
                mapping.setdefault(symbol, set()).add(stock["Id"])
        for symbol, _ in validated:
            if not mapping.get(symbol):
                raise ValueError(f"No existing IndianStocks entry for constituent {symbol}.")
            if len(mapping[symbol]) != 1:
                raise ValueError(f"Ambiguous IndianStocks mapping for constituent {symbol}.")
        existing = _rows(cursor.execute(
            """SELECT StockId,Source FROM [options].[IndexConstituentHistory] WITH (UPDLOCK,HOLDLOCK)
            WHERE IndexCode=? AND EffectiveDate=?""", index_code, effective,
        ))
        manual = any(not isinstance(row["Source"], str) or not row["Source"].startswith("auto:")
                     for row in existing)
        if manual:
            conn.commit()
            return {"supplied": len(validated), "written": 0, "preservedManual": len(existing)}
        if any(row["Source"] != provenance for row in existing):
            raise ValueError("Another automatic source owns this dated constituent snapshot.")
        cursor.execute(
            """DELETE FROM [options].[IndexConstituentHistory]
            WHERE IndexCode=? AND EffectiveDate=? AND Source=? AND Source LIKE 'auto:%'""",
            index_code, effective, provenance,
        )
        for symbol, weight in validated:
            cursor.execute(
                """INSERT INTO [options].[IndexConstituentHistory]
                (IndexCode,StockId,EffectiveDate,WeightPct,Source) VALUES (?,?,?,?,?)""",
                index_code, next(iter(mapping[symbol])), effective, weight, provenance,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Automatic constituent persistence failed for %s; transaction rolled back.", index_code)
        raise
    return {"supplied": len(validated), "written": len(validated), "preservedManual": 0}
