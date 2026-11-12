"""Queue orchestration and prospective reconciliation for the options workspace."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_connection
from options_data import (
    KiteClient, KiteSessionExpired, derivatives_context, dumps, flattened_quotes, load_instruments,
    load_sessions, observed_time, rows, save_snapshot, sqltime, utcnow, last_session_snapshot,
)
from options_fundamentals import canonical_symbol, constituent_context, fundamental_index_range, load_constituents
from options_reference import ensure_reference_data, reference_status
from options_context import capture_context

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
_collector_lock = threading.Lock()
_collector_started = False
_collector_status = {"state": "disabled", "lastError": None, "lastCapturedAt": None}
_collector_status_lock = threading.Lock()
_last_pruned_date = None


class OptionsCancelled(RuntimeError):
    pass


def _configured_number(name: str) -> float | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    import math

    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _timestamp(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _resolve_horizon(now, mode, sessions, instruments):
    from options_engine import resolve_horizon

    return resolve_horizon(
        now, mode, sessions, instruments,
        entry_minutes_before_close=int(os.environ.get("MARKETEDGE_OPTIONS_ENTRY_MINUTES_BEFORE_CLOSE", "30")),
        exit_minutes_before_close=int(os.environ.get("MARKETEDGE_OPTIONS_EXIT_MINUTES_BEFORE_CLOSE", "15")),
    )


def _completed_bar_cutoff(now, sessions):
    local = now.astimezone(IST)
    today = local.date()
    session = next((item for item in sessions if item["sessionDate"] == today.isoformat()), None)
    if session and session["isTradingDay"]:
        close = datetime.fromisoformat(today.isoformat() + "T" + session["closeTime"]).replace(tzinfo=IST)
        if local >= close + timedelta(minutes=15):
            return today
    return today - timedelta(days=1)


def _weight_snapshot_evidence(records, reference):
    if not records:
        return reference
    selected_date = records[0].get("WeightEffectiveDate")
    selected_date = selected_date.isoformat() if selected_date else None
    source = records[0].get("Source") or ""
    is_proxy = all("ETF_PROXY" in (row.get("Source") or "") for row in records)
    if is_proxy and reference.get("sourceDate") == selected_date:
        return reference
    return {
        "status": "ready", "sourceDate": selected_date, "source": source, "constituents": len(records),
        "sourceType": "etf-holdings-proxy" if is_proxy else "effective-dated-constituent-snapshot",
        "sourceUrl": next((part for part in source.split(";") if part.startswith("https://")), None),
        "method": ("Persisted equity-normalized ETF holdings proxy; not official or live index weights."
                   if is_proxy else "Selected persisted constituent snapshot; see each weight source and effective date."),
    }


def _index(conn, index_code: str) -> dict:
    records = rows(conn.cursor().execute(
        "SELECT Code,SpotSymbol,DerivativeExchange FROM [options].[MarketIndices] WHERE Code=?", index_code,
    ))
    if not records:
        raise ValueError("Index reference data missing; publish the options dacpac and reference seed.")
    return records[0]


def _chain_window(instruments: list[dict], spot: float, expiries: list[str]) -> list[dict]:
    selected = []
    for expiry in sorted(set(expiries))[:4]:
        contracts = [item for item in instruments if item["expiry"] == expiry]
        strikes = sorted({item["strike"] for item in contracts if item["instrumentType"] in ("CE", "PE")},
                         key=lambda strike: abs(strike - spot))[:31]
        selected.extend(item for item in contracts
                        if item["strike"] in strikes or item["instrumentType"] == "FUT")
    return selected


def _progress(conn, job_id: int, forecast_id: int, progress: int) -> None:
    state = conn.cursor().execute("SELECT Status FROM dbo.JobRuns WHERE Id=?", job_id).fetchone()
    if not state or state.Status == "cancelled":
        raise OptionsCancelled("Options forecast was cancelled.")
    conn.cursor().execute("UPDATE dbo.JobRuns SET Progress=? WHERE Id=?", progress, job_id)
    conn.commit()


def run_options_job(payload: dict) -> None:
    from options_engine import build_analysis, snapshot_policy

    forecast_id, job_id = int(payload["forecastRunId"]), int(payload["runId"])
    conn = get_connection()
    try:
        records = rows(conn.cursor().execute(
            """SELECT Id,UserKey,IndexCode,Mode,Status FROM [options].[ForecastRuns]
            WHERE Id=? AND UserKey=? AND JobRunId=?""", forecast_id, payload["userKey"], job_id,
        ))
        if not records:
            existing = conn.cursor().execute(
                "SELECT Id FROM [options].[ForecastRuns] WHERE Id=?", forecast_id,
            ).fetchone()
            job = conn.cursor().execute("SELECT Status FROM dbo.JobRuns WHERE Id=?", job_id).fetchone()
            if not existing and job and job.Status in ("completed", "failed", "cancelled"):
                logger.info("Skipping late delivery for deleted forecast %s with terminal job %s", forecast_id, job_id)
                return
            raise ValueError("Forecast/account/job association does not match.")
        forecast = records[0]
        if forecast["Status"] in ("running", "completed", "failed", "cancelled"):
            return
        job_state = conn.cursor().execute("SELECT Status FROM dbo.JobRuns WHERE Id=?", job_id).fetchone()
        if not job_state or job_state.Status == "cancelled":
            conn.cursor().execute("UPDATE [options].[ForecastRuns] SET Status='cancelled' WHERE Id=?", forecast_id)
            conn.commit()
            return
        claim = conn.cursor().execute(
            "UPDATE [options].[ForecastRuns] SET Status='running' WHERE Id=? AND Status='queued'", forecast_id,
        )
        if claim.rowcount != 1:
            conn.rollback()
            return
        conn.cursor().execute(
            "UPDATE dbo.JobRuns SET Status='running',StartedAt=?,Progress=5 WHERE Id=?", sqltime(utcnow()), job_id,
        )
        conn.commit()
        client = KiteClient(conn, forecast["UserKey"])
        index = _index(conn, forecast["IndexCode"])
        instruments = load_instruments(conn, client, forecast["IndexCode"])
        now = utcnow()
        references = ensure_reference_data(conn, forecast["IndexCode"], now)
        sessions = load_sessions(conn, forecast["IndexCode"], now)
        horizon = _resolve_horizon(now, forecast["Mode"], sessions, instruments)
        initial = client.quotes([index["SpotSymbol"]])
        quote = initial.get(index["SpotSymbol"])
        if not quote or not quote.get("last_price"):
            initial_reference = last_session_snapshot(
                conn, forecast["UserKey"], forecast["IndexCode"], index["SpotSymbol"], sessions, now, forecast["Mode"])
            if initial_reference is None:
                raise RuntimeError("Broker underlying price is missing and no verified last-session snapshot is available.")
            quote = initial_reference["quotes"][index["SpotSymbol"]]
        spot = float(quote["last_price"])
        contracts = _chain_window(instruments, spot, horizon["eligibleExpiries"] + horizon["buyingExpiries"])
        constituents = load_constituents(conn, forecast["IndexCode"], now.astimezone(IST).date())
        if constituents:
            references["weights"] = _weight_snapshot_evidence(constituents, references["weights"])
            if references["weights"]["sourceType"] != "etf-holdings-proxy":
                references["warnings"] = [warning for warning in references["warnings"]
                                          if not warning.startswith("NIFTY50 weights use a dated equity-normalized ETF proxy")]
        symbols = [index["SpotSymbol"], "NSE:INDIA VIX"] + [
            "NSE:" + canonical_symbol(item["Symbol"]) for item in constituents
        ] + [item["exchange"] + ":" + item["tradingSymbol"] for item in contracts]
        market_context = capture_context(client, utcnow(), refresh=True)
        quotes = client.quotes(list(dict.fromkeys(symbols)))
        _progress(conn, job_id, forecast_id, 35)
        now = utcnow()
        # Network work can cross the daily/intraday cutoff; resolve against actual computation time.
        horizon = _resolve_horizon(now, forecast["Mode"], sessions, instruments)
        quote = quotes.get(index["SpotSymbol"])
        observed = observed_time(quote) if quote else None
        policy = snapshot_policy(now, sessions, observed, forecast["Mode"])
        reference = None
        if (not quote or not quote.get("last_price") or policy["executionStatus"] == "unavailable"
                or not any(observed_time(quotes.get(c["exchange"] + ":" + c["tradingSymbol"], {}))
                           for c in contracts)):
            reference = last_session_snapshot(
                conn, forecast["UserKey"], forecast["IndexCode"], index["SpotSymbol"], sessions, now, forecast["Mode"])
            if reference is not None:
                quotes = reference["quotes"]
                quote = quotes[index["SpotSymbol"]]
        if not quote or not quote.get("last_price") or observed_time(quote) is None:
            raise RuntimeError("Broker returned an unset underlying timestamp; no verified last-session snapshot is available. Recheck during a trading session.")
        spot = float(quote["last_price"])
        contracts = _chain_window(instruments, spot, horizon["eligibleExpiries"] + horizon["buyingExpiries"])
        batch_id = reference["batchId"] if reference else save_snapshot(conn, forecast["UserKey"], forecast["IndexCode"], quotes)
        completed_through = _completed_bar_cutoff(now, sessions)
        bars = client.bars(quote["instrument_token"], now, completed_through=completed_through)
        now = utcnow()
        horizon = _resolve_horizon(now, forecast["Mode"], sessions, instruments)
        policy = snapshot_policy(now, sessions, observed_time(quote), forecast["Mode"])
        valuations, dimensions = constituent_context(constituents, quotes, now.astimezone(IST).date())
        fundamental_range = fundamental_index_range(spot, valuations)
        dimensions.extend([
            derivatives_context(spot, contracts, quotes, now, policy),
            market_context["dimension"],
        ])
        assumptions = {
            "riskFreeRate": _configured_number("MARKETEDGE_OPTIONS_RISK_FREE_RATE"),
            "dividendYield": _configured_number("MARKETEDGE_OPTIONS_DIVIDEND_YIELD"),
            "roundTripCostPerLeg": _configured_number("MARKETEDGE_OPTIONS_COST_PER_LEG"),
        }
        analysis = build_analysis(
            spot, bars, flattened_quotes(contracts, quotes), horizon, now,
            assumptions["riskFreeRate"], assumptions["dividendYield"], assumptions["roundTripCostPerLeg"],
            extra_dimensions=dimensions,
            quote_policy=policy,
            completed_through=completed_through,
        )
        analysis.update({
            "schemaVersion": "options.v1", "constituents": valuations, "asOf": now.isoformat(),
            "spotObservedAt": observed_time(quote).isoformat(),
            "referenceData": references,
            "marketContext": market_context,
            "fundamentalRange": fundamental_range,
            "snapshotEvidence": ({key: value for key, value in reference.items() if key != "quotes"}
                                 if reference else {"source": "broker-response", "batchId": batch_id, "capturedAt": now.isoformat()}),
            "directionNeutralBandPct": 0.1,
            "inputEvidence": {
                "completedDailyBars": bars, "financialSnapshots": constituents,
                "pricingAssumptions": assumptions, "horizon": horizon,
                "referenceData": references, "exchangeSessions": sessions,
            },
        })
        analysis["warnings"].extend([
            "Quote capture is sampled REST data, not a continuous tick/order execution record.",
        ])
        analysis["warnings"].extend(market_context["warnings"])
        analysis["warnings"].extend(references["warnings"])
        if reference:
            analysis["warnings"].append(reference["reason"])
        completed_cutoff = completed_through.isoformat()
        expected_bar = max((item["sessionDate"] for item in sessions
                            if item["isTradingDay"] and item["sessionDate"] <= completed_cutoff), default=None)
        latest_bar = max((bar["date"] for bar in bars), default=None)
        if expected_bar and (latest_bar is None or latest_bar < expected_bar):
            analysis["warnings"].append(
                f"Broker daily history is complete only through {latest_bar or 'unavailable'}; "
                f"the completed session {expected_bar} is not available. Technical ranges use the disclosed older history."
            )
        if not constituents:
            analysis["warnings"].append("Current complete index weights unavailable; constituent analysis unavailable.")
        if policy["executionStatus"] != "live-ready":
            analysis["warnings"].append(policy["reason"])
        vix = quotes.get("NSE:INDIA VIX")
        if vix and vix.get("last_price"):
            analysis["vix"] = {
                "value": vix["last_price"], "observedAt": vix.get("timestamp"),
                "meaning": "NIFTY-derived expected annualised volatility over approximately 30 calendar days.",
            }
            volatility = next((dimension for dimension in analysis["dimensions"] if dimension["key"] == "volatility"), None)
            if volatility:
                volatility["indicators"].append({
                    "key": "india-vix", "label": "India VIX", "value": vix["last_price"], "unit": "%",
                    "direction": "context", "contribution": 0,
                    "reason": "NIFTY option-derived 30-calendar-day annualised volatility; not direction or SENSEX-specific IV.",
                })
        _progress(conn, job_id, forecast_id, 80)
        cursor = conn.cursor()
        for value in valuations:
            cursor.execute(
                """INSERT INTO [options].[ConstituentValuationSnapshots]
                (ForecastRunId,StockId,PayloadJson) VALUES(?,?,?)""",
                forecast_id, value["stockId"], dumps(value),
            )
        for candidate in analysis["candidates"]:
            suggestion_id = cursor.execute(
                """INSERT INTO [options].[StrategySuggestions]
                (ForecastRunId,CandidateKey,Side,RiskTier,Strategy,Expiry,PayloadJson)
                OUTPUT INSERTED.Id VALUES(?,?,?,?,?,?,?)""",
                forecast_id, candidate["id"], candidate["side"], candidate["riskTier"],
                candidate["strategy"], candidate["expiry"], dumps(candidate),
            ).fetchone()[0]
            for leg in candidate["legs"]:
                cursor.execute(
                    """INSERT INTO [options].[StrategyLegs]
                    (StrategySuggestionId,Exchange,TradingSymbol,InstrumentToken,Direction,Quantity,Strike,OptionType,EntryPrice)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    suggestion_id, leg["exchange"], leg["tradingSymbol"], leg["instrumentToken"],
                    leg["direction"], leg["quantity"], leg["strike"], leg["optionType"], leg["entryPrice"],
                )
        serialised = dumps(analysis)
        cursor.execute(
            """UPDATE [options].[ForecastRuns] SET Status='completed',SnapshotBatchId=?,TargetAt=?,
            InputCutoffAt=?,InputHash=?,AnalysisJson=? WHERE Id=?""",
            batch_id, sqltime(_timestamp(horizon["targetAt"])), sqltime(now),
            hashlib.sha256(serialised.encode()).hexdigest(), serialised, forecast_id,
        )
        cursor.execute(
            "UPDATE dbo.JobRuns SET Status='completed',Progress=100,CompletedAt=? WHERE Id=?",
            sqltime(utcnow()), job_id,
        )
        conn.commit()
    except OptionsCancelled:
        conn.rollback()
        conn.cursor().execute("UPDATE [options].[ForecastRuns] SET Status='cancelled' WHERE Id=?", forecast_id)
        conn.cursor().execute(
            "UPDATE dbo.JobRuns SET Status='cancelled',CompletedAt=? WHERE Id=?", sqltime(utcnow()), job_id,
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        # Error strings from the transport are deliberately credential-free.
        logger.exception("Options forecast %s failed", forecast_id)
        message = str(exc)[:1800]
        conn.cursor().execute(
            "UPDATE [options].[ForecastRuns] SET Status='failed',ErrorMessage=? WHERE Id=?",
            message, forecast_id,
        )
        conn.cursor().execute(
            "UPDATE dbo.JobRuns SET Status='failed',ErrorMessage=?,CompletedAt=? WHERE Id=?",
            message, sqltime(utcnow()), job_id,
        )
        conn.commit()
        raise
    finally:
        conn.close()


def strategy_outcome(candidate: dict, observations: list[dict], target: datetime, now: datetime) -> dict:
    from options_engine import reconcile_strategy

    expiry = min(target, _timestamp(candidate["plannedExitAt"]))
    outcome = reconcile_strategy(candidate, [
        {"timestamp": item["at"], "spot": item.get("spot"), "quotes": item["quotes"]}
        for item in observations if _timestamp(item["at"]) <= expiry + timedelta(seconds=60)
    ], target)
    result = {
        **outcome, "coverageType": "sampled-quotes", "eventOrderCertain": False,
        "netHypotheticalPnl": None, "grossHypotheticalPnl": None,
    }
    if outcome["status"] == "completed":
        result.update({
            "status": "partial", "exitAt": outcome["at"],
            "reason": {"risk": "technical-stop", "target": "premium-target", "time": "time-exit"}[outcome["trigger"]],
            "netHypotheticalPnl": round(outcome["pnl"], 2) if outcome.get("pnl") is not None else None,
            "grossHypotheticalPnl": round(outcome["grossPnl"], 2) if outcome.get("grossPnl") is not None else None,
            "warning": "First observed event only; an earlier stop/target between samples cannot be excluded. "
                       "This is an estimated hypothetical outcome, not a trade fill.",
        })
    elif outcome["status"] == "pending" and now >= expiry:
        result.update(status="unavailable", reason="No valid full-leg observation at the exit/event.")
    return result


def _observations(conn, forecast: dict, spot_symbol: str) -> list[dict]:
    target = forecast["TargetAt"].replace(tzinfo=UTC)
    records = rows(conn.cursor().execute(
        """SELECT b.Id,b.CapturedAt,q.Exchange,q.TradingSymbol,q.LastPrice,q.Bid,q.Ask,q.ObservedAt
        FROM [options].[MarketSnapshotBatches] b
        JOIN [options].[InstrumentQuoteSnapshots] q ON q.BatchId=b.Id
        WHERE b.UserKey=? AND b.IndexCode=? AND b.CapturedAt>=? AND b.CapturedAt<=?
        ORDER BY b.CapturedAt,b.Id""",
        forecast["UserKey"], forecast["IndexCode"], forecast["InputCutoffAt"],
        sqltime(target + timedelta(minutes=2)),
    ))
    grouped = {}
    for record in records:
        observation = grouped.setdefault(record["Id"], {
            "at": record["CapturedAt"].replace(tzinfo=UTC).isoformat(), "spot": None, "quotes": {},
        })
        symbol = record["Exchange"] + ":" + record["TradingSymbol"]
        observation["quotes"][symbol] = {
            "bid": float(record["Bid"]) if record["Bid"] is not None else None,
            "ask": float(record["Ask"]) if record["Ask"] is not None else None,
            "observedAt": record["ObservedAt"].replace(tzinfo=UTC).isoformat(),
        }
        if symbol == spot_symbol and abs((record["CapturedAt"] - record["ObservedAt"]).total_seconds()) <= 60:
            observation["spot"] = float(record["LastPrice"])
    return list(grouped.values())


def _save_outcome(conn, table: str, column: str, item_id: int, result: dict) -> None:
    if (table, column) not in (("ForecastOutcomes", "ForecastRunId"), ("StrategyOutcomes", "StrategySuggestionId")):
        raise ValueError("Unsupported outcome storage.")
    payload = dumps(result)
    cursor = conn.cursor()
    forecast_id = item_id
    if table == "StrategyOutcomes":
        suggestion = cursor.execute(
            "SELECT ForecastRunId FROM [options].[StrategySuggestions] WHERE Id=?", item_id,
        ).fetchone()
        if not suggestion:
            logger.info("Skipping reconciliation for deleted strategy %s", item_id)
            return
        forecast_id = suggestion.ForecastRunId
    parent = cursor.execute(
        "SELECT Id FROM [options].[ForecastRuns] WITH (UPDLOCK,HOLDLOCK) WHERE Id=?", forecast_id,
    ).fetchone()
    if not parent:
        logger.info("Skipping reconciliation for deleted forecast %s", forecast_id)
        return
    if table == "StrategyOutcomes" and not cursor.execute(
            "SELECT Id FROM [options].[StrategySuggestions] WITH (UPDLOCK,HOLDLOCK) WHERE Id=? AND ForecastRunId=?",
            item_id, forecast_id).fetchone():
        logger.info("Skipping reconciliation for deleted strategy %s", item_id)
        return
    # Row-level update locks serialise version allocation across worker processes.
    previous = cursor.execute(
        f"""SELECT TOP 1 Version,PayloadJson FROM [options].[{table}] WITH (UPDLOCK,HOLDLOCK)
        WHERE [{column}]=? ORDER BY Version DESC""", item_id,
    ).fetchone()
    if previous and previous.PayloadJson == payload:
        return
    version = previous.Version + 1 if previous else 1
    cursor.execute(
        f"""INSERT INTO [options].[{table}] ([{column}],Version,Status,PayloadJson,EvaluatedAt)
        VALUES(?,?,?,?,?)""", item_id, version, result["status"], payload, sqltime(utcnow()),
    )


def reconcile_forecast(conn, forecast: dict, now: datetime, official_close: float | None = None) -> None:
    if official_close is None:
        previous = conn.cursor().execute(
            "SELECT TOP 1 PayloadJson FROM [options].[ForecastOutcomes] WHERE ForecastRunId=? ORDER BY Version DESC",
            forecast["Id"],
        ).fetchone()
        if previous:
            cached = json.loads(previous.PayloadJson)
            if cached.get("terminalSource") == "kite-daily-candle":
                official_close = cached.get("actualClose")
    index = _index(conn, forecast["IndexCode"])
    observations = _observations(conn, forecast, index["SpotSymbol"])
    analysis = json.loads(forecast["AnalysisJson"])
    target = forecast["TargetAt"].replace(tzinfo=UTC)
    path = [point for point in observations if point["spot"] is not None and _timestamp(point["at"]) <= target]
    terminal = next((point for point in observations if point["spot"] is not None
                     and target <= _timestamp(point["at"]) <= target + timedelta(seconds=60)), None)
    result = {
        "status": "pending" if now < target else "completed" if official_close is not None else "partial" if terminal else "unavailable",
        "predictedBias": analysis["bias"], "baselineSpot": analysis["spot"],
        "targetAt": target.isoformat(), "actualClose": official_close if official_close is not None else terminal["spot"] if terminal else None,
        "observedHigh": max((p["spot"] for p in path), default=None),
        "observedLow": min((p["spot"] for p in path), default=None),
        "coverageType": "sampled-quotes",
        "terminalSource": "kite-daily-candle" if official_close is not None else "sampled-quote",
        "pathStatus": "partial",
        "warning": "Path high/low coverage is sampled, not continuous. "
                   + ("Terminal value uses the broker's daily candle." if official_close is not None
                      else "Target quote is not necessarily the official closing value."),
        "scenarios": [],
    }
    if result["actualClose"] is not None:
        actual = result["actualClose"]
        result["actualReturnPct"] = round((actual / analysis["spot"] - 1) * 100, 4)
        neutral_band = analysis.get("directionNeutralBandPct")
        movement = result["actualReturnPct"]
        result["actualDirection"] = (
            "neutral" if neutral_band is not None and abs(movement) <= neutral_band
            else "bullish" if movement > 0 else "bearish" if movement < 0 else "neutral"
        )
        result["directionNeutralBandPct"] = neutral_band
        result["directionCorrect"] = (
            result["actualDirection"] == analysis["bias"]
            if analysis["bias"] in ("bullish", "neutral", "bearish") else None
        )
        result["scenarios"] = [{
            "id": scenario["id"], "terminalCovered": (
                scenario["low"] <= actual <= scenario["high"]
                if scenario["low"] is not None and scenario["high"] is not None else None
            ),
            "low": scenario["low"], "high": scenario["high"],
            "reason": None if scenario["low"] is not None and scenario["high"] is not None
            else "No valid interval was available in the original forecast.",
        } for scenario in analysis["scenarios"]]
    _save_outcome(conn, "ForecastOutcomes", "ForecastRunId", forecast["Id"], result)
    suggestions = rows(conn.cursor().execute(
        "SELECT Id,PayloadJson FROM [options].[StrategySuggestions] WHERE ForecastRunId=?", forecast["Id"],
    ))
    for suggestion in suggestions:
        outcome = strategy_outcome(json.loads(suggestion["PayloadJson"]), observations, target, now)
        _save_outcome(conn, "StrategyOutcomes", "StrategySuggestionId", suggestion["Id"], outcome)
    conn.commit()


def _official_close(conn, forecast: dict, client: KiteClient, now: datetime) -> float | None:
    target = forecast["TargetAt"].replace(tzinfo=UTC)
    if now < target + timedelta(minutes=15) or forecast["Mode"] == "expiry-intraday":
        return None
    local_target = target.astimezone(IST)
    session = conn.cursor().execute(
        "SELECT CloseTime FROM [options].[ExchangeSessions] WHERE IndexCode=? AND SessionDate=? AND IsTradingDay=1",
        forecast["IndexCode"], local_target.date(),
    ).fetchone()
    if not session or str(session.CloseTime)[:5] != local_target.strftime("%H:%M"):
        return None
    previous = conn.cursor().execute(
        "SELECT TOP 1 PayloadJson FROM [options].[ForecastOutcomes] WHERE ForecastRunId=? ORDER BY Version DESC",
        forecast["Id"],
    ).fetchone()
    if previous:
        outcome = json.loads(previous.PayloadJson)
        if outcome.get("terminalSource") == "kite-daily-candle":
            return outcome.get("actualClose")
    index = _index(conn, forecast["IndexCode"])
    exchange, symbol = index["SpotSymbol"].split(":", 1)
    token = conn.cursor().execute(
        """SELECT TOP 1 q.InstrumentToken FROM [options].[InstrumentQuoteSnapshots] q
        JOIN [options].[ForecastRuns] f ON f.SnapshotBatchId=q.BatchId
        WHERE f.Id=? AND q.Exchange=? AND q.TradingSymbol=?""", forecast["Id"], exchange, symbol,
    ).fetchone()
    if not token:
        return None
    data = client.get(f"/instruments/historical/{token.InstrumentToken}/day", [
        ("from", local_target.strftime("%Y-%m-%d 00:00:00")),
        ("to", local_target.strftime("%Y-%m-%d %H:%M:%S")),
    ])
    candle = next((item for item in data["candles"] if item[0][:10] == local_target.date().isoformat()), None)
    return float(candle[4]) if candle and candle[4] > 0 else None


def collector_status() -> dict:
    from options_engine import CONFIG_VERSION

    with _collector_status_lock:
        status = dict(_collector_status)
    status["configVersion"] = CONFIG_VERSION
    status["referenceData"] = reference_status()
    return status


def _set_collector_status(**updates) -> None:
    with _collector_status_lock:
        _collector_status.update(updates)


def _collector_cycle() -> None:
    now = utcnow()
    conn = get_connection()
    try:
        for index_code in ("NIFTY50", "SENSEX"):
            ensure_reference_data(conn, index_code, now)
        forecasts = rows(conn.cursor().execute(
            """SELECT Id,UserKey,IndexCode,Mode,TargetAt,InputCutoffAt,AnalysisJson
            FROM [options].[ForecastRuns] WHERE Status='completed'
            AND TargetAt>=? AND InputCutoffAt IS NOT NULL""", sqltime(now - timedelta(days=2)),
        ))
        groups = {}
        for forecast in forecasts:
            groups.setdefault((forecast["UserKey"], forecast["IndexCode"]), []).append(forecast)
        for (user_key, index_code), group in groups.items():
            index = _index(conn, index_code)
            sessions = load_sessions(conn, index_code, now)
            local = now.astimezone(IST)
            today = next((s for s in sessions if s["sessionDate"] == local.date().isoformat()), None)
            is_open = today and today["isTradingDay"] and today["openTime"] <= local.strftime("%H:%M") <= today["closeTime"]
            client = None
            if is_open:
                try:
                    client = KiteClient(conn, user_key)
                    symbols = {index["SpotSymbol"]}
                    for forecast in group:
                        if forecast["TargetAt"].replace(tzinfo=UTC) + timedelta(minutes=2) < now:
                            continue
                        for candidate in json.loads(forecast["AnalysisJson"])["candidates"]:
                            symbols.update(leg["exchange"] + ":" + leg["tradingSymbol"] for leg in candidate["legs"])
                    save_snapshot(conn, user_key, index_code, client.quotes(sorted(symbols)))
                    _set_collector_status(lastCapturedAt=now.isoformat())
                except KiteSessionExpired as exc:
                    logger.warning("Options collection paused: %s", exc)
                    _set_collector_status(lastError=str(exc))
            for forecast in group:
                close = None
                if now >= forecast["TargetAt"].replace(tzinfo=UTC) + timedelta(minutes=15):
                    try:
                        client = client or KiteClient(conn, user_key)
                        close = _official_close(conn, forecast, client, now)
                    except KiteSessionExpired:
                        logger.warning("Official close backfill requires Zerodha re-login.")
                reconcile_forecast(conn, forecast, now, close)
        _prune_unlinked_snapshots(conn, now)
    finally:
        conn.close()


def _prune_unlinked_snapshots(conn, now: datetime) -> None:
    global _last_pruned_date
    if _last_pruned_date == now.date():
        return
    retention = int(os.environ.get("MARKETEDGE_OPTIONS_RETENTION_DAYS", "30"))
    if retention < 7:
        raise ValueError("MARKETEDGE_OPTIONS_RETENTION_DAYS must be at least seven.")
    cursor = conn.cursor()
    # Forecast evidence is retained; only unlinked captures expire automatically.
    cursor.execute(
        """DELETE q FROM [options].[InstrumentQuoteSnapshots] q
        JOIN [options].[MarketSnapshotBatches] b ON b.Id=q.BatchId
        WHERE b.CapturedAt<? AND NOT EXISTS (
            SELECT 1 FROM [options].[ForecastRuns] f
            WHERE f.UserKey=b.UserKey AND f.IndexCode=b.IndexCode AND
                (f.SnapshotBatchId=b.Id OR (b.CapturedAt>=f.InputCutoffAt
                AND b.CapturedAt<=DATEADD(minute,2,f.TargetAt))))""",
        sqltime(now - timedelta(days=retention)),
    )
    cursor.execute(
        """DELETE b FROM [options].[MarketSnapshotBatches] b WHERE b.CapturedAt<?
        AND NOT EXISTS(SELECT 1 FROM [options].[InstrumentQuoteSnapshots] q WHERE q.BatchId=b.Id)
        AND NOT EXISTS(SELECT 1 FROM [options].[ForecastRuns] f WHERE f.SnapshotBatchId=b.Id)""",
        sqltime(now - timedelta(days=retention)),
    )
    conn.commit()
    _last_pruned_date = now.date()


def start_options_collector() -> None:
    global _collector_started
    if os.environ.get("MARKETEDGE_OPTIONS_ENABLED", "").lower() not in ("1", "true"):
        logger.info("Options collector disabled; set MARKETEDGE_OPTIONS_ENABLED=true after dacpac deployment.")
        return
    with _collector_lock:
        if _collector_started:
            return
        _collector_started = True

    def run():
        while True:
            try:
                _set_collector_status(state="running", lastError=None)
                _collector_cycle()
            except Exception:
                # Supervision boundary: expose failure and retry the next cycle, never report healthy.
                logger.exception("Options capture/reconciliation cycle failed")
                _set_collector_status(state="error", lastError="Capture/reconciliation cycle failed; inspect worker logs.")
            threading.Event().wait(20)

    threading.Thread(target=run, daemon=True, name="options-collector").start()
