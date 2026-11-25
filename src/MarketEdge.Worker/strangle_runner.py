"""Account-scoped queue capture for read-only strangle advice."""
from __future__ import annotations

import json
import logging
import os

from db import get_connection
from options_data import (KiteClient, KiteSessionExpired, dumps, load_instruments, load_sessions,
                          rows, sqltime, utcnow)
from options_reference import ensure_reference_data
from strangle_engine import (build_adjustment, build_entry, build_position_review, identity, number,
                             common_atm, contract, revalidate_completion, select_expiry,
                             select_legs, entry_spec_choices, straddle_specs, validate_spot, reconcile_trade_ids, selected_index_option,
                             invalidate_review)

logger = logging.getLogger(__name__)


class StrangleCancelled(RuntimeError):
    pass


def _read_state(conn, run_id, job_id, user_key, *, locked=False):
    hint = " WITH (UPDLOCK,HOLDLOCK)" if locked else ""
    # All API/worker lifecycle transactions acquire the job before the run.
    jobs = rows(conn.cursor().execute(
        "SELECT Status AS JobStatus FROM dbo.JobRuns" + hint +
        " WHERE Id=? AND JobType='options_strangle' AND Market='india'", job_id))
    if len(jobs) != 1:
        return None
    runs = rows(conn.cursor().execute(
        "SELECT Status AS RunStatus FROM [options].[StrangleRuns]" + hint +
        " WHERE Id=? AND UserKey=? AND JobRunId=?", run_id, user_key, job_id))
    if len(runs) != 1:
        return None
    return {"JobStatus": jobs[0]["JobStatus"], "RunStatus": runs[0]["RunStatus"]}


def _check_cancelled(conn, run_id, job_id, user_key):
    state = _read_state(conn, run_id, job_id, user_key)
    if not state or state["RunStatus"] == "cancelled" or state["JobStatus"] == "cancelled":
        raise StrangleCancelled()


def _model_input(name):
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if number(value) else None


def _position_fingerprint(positions):
    net = positions.get("net")
    if not isinstance(net, list):
        raise ValueError("Broker net positions unavailable.")
    # Account-wide net changes invalidate advice, including trades sharing contracts.
    return sorted((identity(p), p.get("quantity"), p.get("overnight_quantity"),
                   p.get("buy_quantity"), p.get("sell_quantity"),
                   p.get("buy_value"), p.get("sell_value")) for p in net)


def _trade_fingerprint(trades):
    if not isinstance(trades, list):
        raise ValueError("Broker fill ledger unavailable.")
    return sorted((str(t.get("trade_id")), identity(t), t.get("quantity"),
                   t.get("transaction_type"), t.get("average_price"),
                   str(t.get("fill_timestamp") or t.get("exchange_timestamp"))) for t in trades)


def _regime_evidence(quotes, now):
    evidence = {"fresh": True, "asOf": now.isoformat(),
                "priceBases": "Index changes from current-session open; India VIX change from previous close."}
    for symbol, field in (("NSE:NIFTY 50", "niftyChangePct"), ("BSE:SENSEX", "sensexChangePct"),
                          ("NSE:INDIA VIX", "vixChangePct")):
        try:
            price, at = validate_spot(quotes.get(symbol), now)
            basis = quotes[symbol].get("ohlc", {}).get("close" if symbol == "NSE:INDIA VIX" else "open")
            if not number(basis) or basis <= 0:
                raise ValueError("Required context price basis unavailable.")
            evidence[field] = (price/basis - 1)*100
            evidence[field + "ObservedAt"] = at.isoformat()
        except (ValueError, KeyError, TypeError):
            evidence["fresh"] = False
            evidence[field] = None
    return evidence


def _margin(client, result):
    if not result["legs"]:
        return
    try:
        before = client.basket_margin(result["legs"])
        shorts = [l for l in result["legs"] if l["direction"] == "sell"]
        unhedged = client.basket_margin(shorts)
        if any(not isinstance(estimate, dict) or not number((estimate.get("final") or {}).get("total"))
               for estimate in (before, unhedged)):
            raise ValueError("Invalid broker margin response.")
        result["margin"] = {"status": "available", "hedged": before, "unhedged": unhedged,
                            "product": result["legs"][0]["product"], "considerPositions": False,
                            "asOf": utcnow().isoformat(),
                            "meaning": "Broker read-only basket estimates; not maximum loss or a guarantee."}
    except (RuntimeError, ValueError, KeyError, TypeError):
        result["warnings"].append("Broker same-product basket margin comparison unavailable; no margin was invented.")
    validated = []
    for candidate in result["candidates"]:
        candidate["marginBefore"] = result["margin"]
        if candidate["action"] == "roll" and result["margin"]["status"] != "available":
            result["warnings"].append("Roll excluded: comparable current broker margin unavailable.")
            continue
        if candidate["action"] in ("close", "hold"):
            candidate["marginAfter"] = ({"status": "not-required", "reason": "Fully closed basket."}
                                         if candidate["action"] == "close" else result["margin"])
            validated.append(candidate)
            continue
        try:
            after = client.basket_margin(candidate["legs"])
            if not isinstance(after, dict) or not number((after.get("final") or {}).get("total")):
                raise ValueError("Invalid margin response.")
            candidate["marginAfter"] = {"status": "available", "estimate": after}
            validated.append(candidate)
        except (RuntimeError, ValueError, KeyError, TypeError):
            if candidate["action"] != "roll":
                candidate["marginAfter"] = {"status": "unavailable"}
                validated.append(candidate)
            else:
                result["warnings"].append("Roll excluded: current broker basket margin unavailable.")
    result["candidates"] = validated


def _basket_key(legs):
    return tuple(sorted((identity(l), l["direction"], l["quantity"]) for l in legs))


def _required_symbols(snapshot, request, baseline):
    instruments = snapshot["instruments"]
    context = [snapshot["spotSymbol"], "NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"]
    selected = []
    review = request["action"] == "adjustment" and request.get("strategyId") is None
    if review:
        baseline = {"legs": [p for p in snapshot["positions"].get("net", [])
                             if p.get("quantity") and selected_index_option(p, instruments, request["indexCode"])]}
    if baseline:
        keys = {identity(l)[:2] for l in baseline["legs"]}
        keys.update(identity(f)[:2] for f in baseline.get("cashFlows", []))
        net = snapshot["positions"].get("net", [])
        open_keys = {identity(p)[:2] for p in net if p.get("quantity")}
        selected = [i for i in instruments if identity(i)[:2] in keys & open_keys]
        current = [(i, p["quantity"]) for p in net for i in selected
                   if identity(i)[:2] == identity(p)[:2] and p.get("quantity")]
        try:
            held = [{**i, "quantity": abs(qty), "direction": "buy" if qty > 0 else "sell"} for i, qty in current]
            spot = (snapshot.get("spotQuote") or {}).get("last_price")
            if number(spot) and spot > 0:
                selected.extend(i for i, _ in straddle_specs(instruments, held, spot))
        except (ValueError, KeyError, TypeError):
            pass
        for inst, signed in current:
            opposite = [i for i, qty in current if i["expiry"] == inst["expiry"] and
                        i["instrumentType"] == inst["instrumentType"] and qty*signed < 0]
            if len(opposite) != 1:
                continue
            other = opposite[0]
            if signed < 0:
                possible = [i for i in instruments if i["exchange"] == inst["exchange"] and
                            i["expiry"] == inst["expiry"] and i["instrumentType"] == inst["instrumentType"]
                            and min(inst["strike"], other["strike"]) < i["strike"] <
                            max(inst["strike"], other["strike"])]
                selected.extend(sorted(possible, key=lambda i: abs(i["strike"]-inst["strike"]))[:2])
            else:
                possible = [i for i in instruments if i["exchange"] == inst["exchange"] and
                            i["expiry"] == inst["expiry"] and i["instrumentType"] == inst["instrumentType"]
                            and min(inst["strike"], other["strike"]) < i["strike"] <
                            max(inst["strike"], other["strike"])]
                if possible:
                    selected.append(min(possible, key=lambda i: abs(i["strike"]-inst["strike"])))
    else:
        quote = snapshot.get("spotQuote") or {}
        spot = quote.get("last_price")
        if not number(spot) or spot <= 0:
            return context
        expiry = select_expiry(instruments, utcnow())
        atm = common_atm(instruments, expiry, spot)
        atm_legs = [contract(instruments, expiry, atm, kind) for kind in ("CE", "PE")]
        selected.extend(atm_legs)
        try:
            premium = 0
            for inst in atm_legs:
                q = snapshot["quotes"][inst["exchange"]+":"+inst["tradingSymbol"]]
                bid, ask = q["depth"]["buy"][0]["price"], q["depth"]["sell"][0]["price"]
                if not all(number(v) and v > 0 for v in (bid, ask)):
                    raise ValueError("ATM depth unavailable.")
                premium += (bid+ask)/2
            for specs in entry_spec_choices(instruments, expiry, spot, premium, request["wingWidth"]):
                selected.extend(i for i, _ in specs)
        except (ValueError, KeyError, TypeError, IndexError):
            # Fetch the ATM even if initial depth was absent; the engine retains
            # all quote/model gates and never treats this selection hint as advice.
            pass
    held = ([p["exchange"]+":"+identity(p)[1] for p in baseline["legs"]] if review else [])
    return list(dict.fromkeys(context+held+[i["exchange"]+":"+i["tradingSymbol"] for i in selected]))


def _refresh_required_quotes(client, snapshot, request, baseline, check):
    """Bounded recapture; every published price is repriced, never re-timestamped."""
    for _ in range(3):
        required = _required_symbols(snapshot, request, baseline)
        response = client.quotes(required)
        if not isinstance(response, dict):
            raise ValueError("Broker targeted quotes unavailable.")
        refreshed = dict(response)
        check()
        for symbol in required:
            snapshot["quotes"].pop(symbol, None)
        snapshot["quotes"].update(refreshed)
        snapshot["spotQuote"] = refreshed.get(snapshot["spotSymbol"])
        snapshot["niftySpotQuote"] = refreshed.get("NSE:NIFTY 50")
        snapshot["regimeEvidence"] = _regime_evidence(snapshot["quotes"], utcnow())
        if set(_required_symbols(snapshot, request, baseline)).issubset(required):
            return
    raise ValueError("Market moved outside the bounded targeted quote selection; request fresh advice.")


def _carry_margins(previous, current):
    """Basket margins are estimates tied to identities/quantities, not old marks."""
    if _basket_key(previous["legs"]) == _basket_key(current["legs"]):
        current["margin"] = previous["margin"]
    prior = {(_basket_key(c["legs"]), c["action"]): c for c in previous["candidates"]}
    validated = []
    for candidate in current["candidates"]:
        old = prior.get((_basket_key(candidate["legs"]), candidate["action"]))
        if old:
            for field in ("marginBefore", "marginAfter"):
                if field in old:
                    candidate[field] = old[field]
        if candidate["action"] == "roll" and (
                not old or current["margin"]["status"] != "available" or
                candidate.get("marginAfter", {}).get("status") != "available"):
            continue
        validated.append(candidate)
    current["candidates"] = validated


def _prior_reconciliation(conn, run):
    history = rows(conn.cursor().execute(
        """SELECT ResultJson FROM [options].[StrangleRuns]
        WHERE UserKey=? AND IndexCode=? AND StrategyId=? AND Id<>?
          AND Status='completed' AND ResultJson IS NOT NULL ORDER BY Id""",
        run["UserKey"], run["IndexCode"], run["StrategyId"], run["Id"]))
    ids, cash = set(), {}
    for record in history:
        result = json.loads(record["ResultJson"])
        ledger = result.get("reconciliation") or {}
        if result.get("positionsConfirmed") is not True or ledger.get("verified") is not True:
            continue
        previous_ids = ledger.get("selectedTradeIds", ledger.get("tradeIds"))
        if (not isinstance(previous_ids, list) or not previous_ids or
                any(not isinstance(value, str) or not value for value in previous_ids)):
            raise ValueError("Previously verified reconciliation is incomplete; manual review required.")
        ids.update(previous_ids)
        for flow in ledger.get("cashFlows", []):
            trade_id = flow["tradeId"]
            if trade_id not in previous_ids:
                raise ValueError("Previously verified reconciliation is inconsistent.")
            if trade_id in cash and cash[trade_id] != flow:
                raise ValueError("Previously verified broker fills changed; manual review required.")
            cash[trade_id] = flow
    return {"selectedTradeIds": sorted(ids), "cashFlows": list(cash.values())}


def _nifty_sessions(conn, index, sessions, warnings, check):
    if index == "NIFTY50":
        return sessions
    try:
        references = ensure_reference_data(conn, "NIFTY50", utcnow())
        warnings.extend(references.get("warnings", []))
        check()
        return load_sessions(conn, "NIFTY50", utcnow())
    except StrangleCancelled:
        raise
    except (RuntimeError, ValueError):
        warnings.append("Verified NIFTY calendar unavailable; no NIFTY-chart directional bias.")
        return []


def _capture_chart_candles(client, snapshot, index, check, *, reuse_selected=False):
    warnings = []
    if not reuse_selected:
        try:
            snapshot["candles"] = client.minute_bars(snapshot["spotQuote"]["instrument_token"], utcnow())
        except (RuntimeError, ValueError, KeyError, TypeError):
            snapshot["candles"] = []
            warnings.append("Fresh completed minute candles unavailable; non-exit stress qualification unavailable.")
        check()
    if index == "NIFTY50":
        snapshot.update(niftyCandles=snapshot["candles"], niftySpotQuote=snapshot["spotQuote"],
                        niftySessions=snapshot["sessions"])
    else:
        snapshot["niftySpotQuote"] = snapshot["quotes"].get("NSE:NIFTY 50")
        try:
            snapshot["niftyCandles"] = client.minute_bars(
                snapshot["niftySpotQuote"]["instrument_token"], utcnow())
        except (RuntimeError, ValueError, KeyError, TypeError):
            snapshot["niftyCandles"] = []
            warnings.append("Fresh NIFTY minute candles unavailable; no NIFTY-chart directional roll.")
        check()
    return warnings


def _capture_positions(conn, client, run, index, instruments, check):
    captured = utcnow()
    warnings, quotes, candles = [], {}, []
    trades, sessions, references = None, [], {}
    try:
        references = ensure_reference_data(conn, run["IndexCode"], captured)
        warnings.extend(references.get("warnings", []))
    except (RuntimeError, ValueError):
        warnings.append("Exchange reference refresh unavailable; positions remain visible.")
    check()
    try:
        sessions = load_sessions(conn, run["IndexCode"], utcnow())
    except (RuntimeError, ValueError):
        warnings.append("Verified session/expiry calendar unavailable; model data unavailable.")
    nifty_sessions = _nifty_sessions(conn, run["IndexCode"], sessions, warnings, check)
    check()
    positions = client.positions()
    captured = utcnow()
    check()
    if not isinstance(positions.get("net"), list):
        raise ValueError("Broker net positions unavailable.")
    try:
        trades = client.trades()
        if not isinstance(trades, list):
            raise ValueError("Trade ledger unavailable.")
    except (RuntimeError, ValueError):
        trades = None
        warnings.append("Fresh account trade fills unavailable; session P&L/payoff/risk are not invented.")
    check()
    snapshot = {"instruments": instruments, "sessions": sessions, "quotes": quotes, "spotQuote": None,
                "spotSymbol": index["SpotSymbol"], "candles": candles, "positions": positions, "trades": trades,
                "positionsObservedAt": captured.isoformat(), "referenceData": references,
                "niftySessions": nifty_sessions}
    symbols = _required_symbols(snapshot, {"action": "adjustment", "strategyId": None,
                                         "indexCode": run["IndexCode"]}, None)
    try:
        quotes = client.quotes(symbols)
        if not isinstance(quotes, dict):
            quotes = {}
            raise ValueError("Broker quotes unavailable.")
    except (RuntimeError, ValueError):
        warnings.append("Fresh quote capture unavailable; actual positions remain visible without model metrics.")
    check()
    snapshot.update(quotes=quotes, spotQuote=quotes.get(index["SpotSymbol"]))
    if snapshot["spotQuote"]:
        try:
            _refresh_required_quotes(client, snapshot, {"action": "adjustment", "strategyId": None,
                                                       "indexCode": run["IndexCode"]}, None, check)
        except StrangleCancelled:
            raise
        except (RuntimeError, ValueError):
            warnings.append("Bounded replacement quotes unavailable; no unqualified straddle/roll is inferred.")
    quotes, spot_quote = snapshot["quotes"], snapshot["spotQuote"]
    try:
        candles = client.minute_bars(spot_quote["instrument_token"], captured)
    except (RuntimeError, ValueError, TypeError, KeyError):
        warnings.append("Completed minute candles unavailable; intraday trend is not inferred.")
    check()
    snapshot.update(quotes=quotes, spotQuote=spot_quote, candles=candles,
                    regimeEvidence=_regime_evidence(quotes, utcnow()))
    warnings.extend(_capture_chart_candles(client, snapshot, run["IndexCode"], check, reuse_selected=True))
    return snapshot, None, warnings


def _recapture_review(client, snapshot, request, check):
    """Capture actual account data after margin latency; never renew an old capture timestamp."""
    changed, warnings = False, []
    try:
        positions = client.positions()
        observed = utcnow()
        changed = _position_fingerprint(snapshot["positions"]) != _position_fingerprint(positions)
        snapshot.update(positions=positions, positionsObservedAt=observed.isoformat())
    except (RuntimeError, ValueError):
        changed = True
        warnings.append("Fresh completion positions unavailable; prior observed positions remain reference-only.")
    check()
    try:
        trades = client.trades()
        if not isinstance(trades, list):
            raise ValueError("Trade ledger unavailable.")
        if snapshot.get("trades") is not None:
            changed = changed or _trade_fingerprint(snapshot["trades"]) != _trade_fingerprint(trades)
        snapshot["trades"] = trades
    except (RuntimeError, ValueError):
        changed = changed or snapshot.get("trades") is not None
        snapshot["trades"] = None
        warnings.append("Fresh completion fills unavailable; session accounting cannot be verified.")
    check()
    try:
        _refresh_required_quotes(client, snapshot, request, None, check)
    except StrangleCancelled:
        raise
    except (RuntimeError, ValueError):
        snapshot.update(quotes={}, spotQuote=None)
        warnings.append("Completion quote recapture unavailable; positions preserved without invented current totals.")
    warnings.extend(_capture_chart_candles(client, snapshot, request["indexCode"], check))
    return changed, warnings


def _capture(conn, client, run, request, check):
    index = rows(conn.cursor().execute(
        "SELECT Code,SpotSymbol,DerivativeExchange FROM [options].[MarketIndices] WHERE Code=?", run["IndexCode"]))
    if len(index) != 1:
        raise ValueError("Index reference data unavailable.")
    review = run["Action"] == "adjustment" and run["StrategyId"] is None
    try:
        instruments = load_instruments(conn, client, run["IndexCode"])
    except (RuntimeError, ValueError):
        if not review:
            raise
        instruments = []
    if review:
        captured, baseline, warnings = _capture_positions(conn, client, run, index[0], instruments, check)
        if not instruments:
            warnings.append("Option instrument reference unavailable; exact-root broker positions remain visible without model data.")
        return captured, baseline, warnings
    now = utcnow()
    references = ensure_reference_data(conn, run["IndexCode"], now)
    check()
    sessions = load_sessions(conn, run["IndexCode"], now)
    capture_warnings = list(references.get("warnings", []))
    nifty_sessions = _nifty_sessions(conn, run["IndexCode"], sessions, capture_warnings, check)
    expiry = select_expiry(instruments, now)
    baseline = None
    prior = {"selectedTradeIds": [], "cashFlows": []}
    if run["Action"] == "adjustment":
        selected = rows(conn.cursor().execute(
            """SELECT BaselineJson FROM [options].[StrangleStrategies]
            WHERE Id=? AND UserKey=? AND IndexCode=?""",
            run["StrategyId"], run["UserKey"], run["IndexCode"]))
        if len(selected) != 1:
            raise ValueError("Confirmed account/index strategy baseline unavailable.")
        baseline = json.loads(selected[0]["BaselineJson"])
        prior = _prior_reconciliation(conn, run)
    check()
    positions = client.positions()
    check()
    context = list(dict.fromkeys([index[0]["SpotSymbol"], "NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"]))
    quotes = client.quotes(context)
    check()
    if baseline or quotes.get(index[0]["SpotSymbol"]):
        targeted = {"instruments": instruments, "quotes": quotes, "spotSymbol": index[0]["SpotSymbol"],
                    "spotQuote": quotes.get(index[0]["SpotSymbol"]), "positions": positions}
        _refresh_required_quotes(client, targeted, request, baseline, check)
        quotes = targeted["quotes"]
    captured = utcnow()
    spot_quote = quotes.get(index[0]["SpotSymbol"])
    candles = []
    if not any(s["sessionDate"] == expiry and s.get("isTradingDay") and s.get("source") for s in sessions):
        capture_warnings.append("Verified nearest-expiry session unavailable after reference refresh; model advice remains unavailable.")
    try:
        token = spot_quote["instrument_token"]
        candles = client.minute_bars(token, captured)
    except (RuntimeError, ValueError, TypeError, KeyError):
        capture_warnings.append("Completed minute candles unavailable; suggested X/defensive rolls unavailable.")
    check()
    trades = []
    if baseline:
        try:
            trades = client.trades()
        except (RuntimeError, ValueError):
            capture_warnings.append("Trade fills unavailable; strategy P&L attribution/rolls unavailable.")
        check()
        if request.get("selectedTradeIds") is not None or prior["selectedTradeIds"]:
            baseline = reconcile_trade_ids(baseline, request.get("selectedTradeIds"), trades, instruments, utcnow(),
                                           prior=prior)
    snapshot = {"instruments": instruments, "sessions": sessions, "quotes": quotes, "spotQuote": spot_quote,
                "spotSymbol": index[0]["SpotSymbol"], "referenceData": references,
                "niftySessions": nifty_sessions,
                "candles": candles, "positions": positions, "trades": trades,
                "regimeEvidence": _regime_evidence(quotes, captured)}
    capture_warnings.extend(_capture_chart_candles(client, snapshot, run["IndexCode"], check, reuse_selected=True))
    return snapshot, baseline, capture_warnings


def run_strangle_job(payload):
    run_id, job_id = int(payload["strangleRunId"]), int(payload["runId"])
    user_key = str(payload["userKey"])
    conn, claimed = get_connection(), False
    try:
        jobs = rows(conn.cursor().execute(
            """SELECT Status AS JobStatus FROM dbo.JobRuns
            WHERE Id=? AND JobType='options_strangle' AND Market='india'""", job_id))
        if len(jobs) != 1:
            logger.warning("Rejected strangle queue job association.")
            return
        runs = rows(conn.cursor().execute(
            """SELECT Id,UserKey,RequestId,IndexCode,Action,StrategyId,Status,RequestJson
            FROM [options].[StrangleRuns] WHERE Id=? AND UserKey=? AND JobRunId=?""",
            run_id, user_key, job_id))
        if len(runs) != 1:
            logger.warning("Rejected strangle queue account/job association.")
            return
        run = runs[0]
        if run["Status"] == "queued" and jobs[0]["JobStatus"] == "cancelled":
            _terminal(conn, run_id, job_id, user_key, "cancelled", None)
            return
        if run["Status"] != "queued":
            return
        state = _read_state(conn, run_id, job_id, user_key, locked=True)
        if not state or state["RunStatus"] != "queued" or state["JobStatus"] != "queued":
            conn.rollback()
            return
        job_claim = conn.cursor().execute(
            "UPDATE dbo.JobRuns SET Status='running',StartedAt=?,Progress=5 WHERE Id=? AND Status='queued'",
            sqltime(utcnow()), job_id)
        if job_claim.rowcount != 1:
            conn.rollback()
            return
        run_claim = conn.cursor().execute(
            """UPDATE [options].[StrangleRuns] SET Status='running'
            WHERE Id=? AND UserKey=? AND JobRunId=? AND Status='queued'""", run_id, user_key, job_id)
        if run_claim.rowcount != 1:
            conn.rollback()
            return
        conn.commit()
        claimed = True
        check = lambda: _check_cancelled(conn, run_id, job_id, user_key)
        check()
        request = json.loads(run["RequestJson"])
        if (request.get("indexCode") != run["IndexCode"] or request.get("action") != run["Action"] or
                str(request.get("requestId")).lower() != str(run["RequestId"]).lower() or
                request.get("strategyId") != run["StrategyId"] or run["Action"] not in ("entry", "adjustment")):
            raise ValueError("Persisted strangle request association invalid.")
        if "cashFlows" in request:
            raise ValueError("Request cash flows are unsupported; select actual broker trade IDs instead.")
        if request.get("selectedTradeIds") is not None and (run["Action"] != "adjustment" or run["StrategyId"] is None):
            raise ValueError("Selected trade IDs require a confirmed strategy adjustment.")
        client = KiteClient(conn, user_key)
        snapshot, baseline, warnings = _capture(conn, client, run, request, check)
        review = run["Action"] == "adjustment" and run["StrategyId"] is None
        if not review:
            _refresh_required_quotes(client, snapshot, request, baseline, check)
        model = {"rate": _model_input("MARKETEDGE_STRANGLE_RISK_FREE_RATE"),
                 "dividend": _model_input("MARKETEDGE_STRANGLE_DIVIDEND_YIELD")}
        result = (build_position_review(request, snapshot, utcnow(), **model) if review else
                  build_entry(request, snapshot, utcnow(), **model) if run["Action"] == "entry" else
                  build_adjustment(request, snapshot, baseline, utcnow(), **model))
        check()
        _margin(client, result)
        check()
        capture_changed = False
        if review or result["legs"]:
            previous = result
            if review:
                capture_changed, recapture_warnings = _recapture_review(client, snapshot, request, check)
                warnings.extend(recapture_warnings)
            else:
                _refresh_required_quotes(client, snapshot, request, baseline, check)
                warnings.extend(_capture_chart_candles(client, snapshot, request["indexCode"], check))
            result = (build_position_review(request, snapshot, utcnow(), **model) if review else
                      build_entry(request, snapshot, utcnow(), **model) if run["Action"] == "entry" else
                      build_adjustment(request, snapshot, baseline, utcnow(), **model))
            _carry_margins(previous, result)
            result["warnings"].extend(w for w in previous["warnings"] if w not in result["warnings"])
        result["warnings"].extend(warnings)
        result["requestId"] = request["requestId"]
        result["strategyId"] = run["StrategyId"]
        try:
            current_positions = client.positions()
            changed = capture_changed or _position_fingerprint(snapshot["positions"]) != _position_fingerprint(current_positions)
        except (RuntimeError, ValueError):
            changed = True
        if baseline or review:
            try:
                current_trades = client.trades()
                changed = changed or _trade_fingerprint(snapshot["trades"]) != _trade_fingerprint(current_trades)
            except (RuntimeError, ValueError):
                # Missing fills on both captures cannot verify a ledger, but must not hide live exposure.
                changed = changed or snapshot.get("trades") is not None
        if changed:
            if review:
                invalidate_review(result, "Broker positions or fills changed/unverifiable during calculation; request fresh review.")
            result["positionsConfirmed"] = False
            if result.get("reconciliation"):
                result["reconciliation"]["verified"] = False
            if result["executionStatus"] != "exit-required":
                result["executionStatus"] = "unavailable"
            result["candidates"] = []
            result["blockers"].append("Broker positions changed during calculation; request fresh advice.")
        revalidate_completion(result, snapshot, utcnow(), run["Action"])
        # Locks serialize completion against API cancellation of either row.
        state = _read_state(conn, run_id, job_id, user_key, locked=True)
        if not state or state["RunStatus"] != "running" or state["JobStatus"] != "running":
            raise StrangleCancelled()
        completed = sqltime(utcnow())
        conn.cursor().execute(
            "UPDATE dbo.JobRuns SET Status='completed',Progress=100,CompletedAt=? WHERE Id=? AND Status='running'",
            completed, job_id)
        conn.cursor().execute(
            """UPDATE [options].[StrangleRuns] SET Status='completed',ResultJson=?,CompletedAt=?,ErrorMessage=NULL
            WHERE Id=? AND UserKey=? AND JobRunId=? AND Status='running'""",
            dumps(result), completed, run_id, user_key, job_id)
        conn.commit()
    except StrangleCancelled:
        conn.rollback()
        if claimed:
            _terminal(conn, run_id, job_id, user_key, "cancelled", None)
    except Exception as exc:
        conn.rollback()
        if claimed:
            # Never persist raw broker/DB exception text, URLs, credentials or position payloads.
            message = ("Broker session unavailable; reconnect Zerodha and request fresh advice."
                       if isinstance(exc, KiteSessionExpired) else
                       "Strangle analysis unavailable: verify broker data, confirmed strategy and worker configuration, then retry explicitly.")
            logger.warning("Strangle run %s failed (%s).", run_id, type(exc).__name__)
            _terminal(conn, run_id, job_id, user_key, "failed", message)
    finally:
        conn.close()


def _terminal(conn, run_id, job_id, user_key, status, message):
    # Never overwrite a concurrent API cancellation with failure or mutate an unrelated job.
    state = _read_state(conn, run_id, job_id, user_key, locked=True)
    if not state:
        conn.rollback()
        return
    if state["RunStatus"] == "cancelled" or state["JobStatus"] == "cancelled":
        status, message = "cancelled", None
    conn.cursor().execute(
        """UPDATE dbo.JobRuns SET Status=?,ErrorMessage=?,CompletedAt=?
        WHERE Id=? AND Status IN ('queued','running')""",
        status, message, sqltime(utcnow()), job_id)
    conn.cursor().execute(
        """UPDATE [options].[StrangleRuns] SET Status=?,ErrorMessage=?,CompletedAt=?
        WHERE Id=? AND UserKey=? AND JobRunId=? AND Status IN ('queued','running')""",
        status, message, sqltime(utcnow()), run_id, user_key, job_id)
    conn.commit()
