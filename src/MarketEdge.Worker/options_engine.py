"""Pure, deterministic index-options analytics. No fetching, storage or execution.

European Black--Scholes with continuous carry; actual imported session closes
define expiry. Probabilities are risk-neutral model outputs, never calibration.
All money amounts are rupees for one matched exchange lot. Gross expiry
payoffs remain available without model inputs; net values require known costs.
Risk tiers use loss/notional plus same-day gamma and low-delta OTM floors.
Conditional profit probabilities share the unconditional grid, only when the
scenario and planned-exit timestamps coincide; no cross-time inference is made.
"""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from math import exp, isfinite, log, pi, sqrt
from statistics import NormalDist, mean, stdev
import re


MODEL_VERSION = "options-european-v1"
CONFIG_VERSION = "options-rules-v5"
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
MAX_QUOTE_AGE_SECONDS = 60
MAX_QUOTE_SKEW_SECONDS = 5
MAX_FUTURE_QUOTE_SECONDS = 2
MAX_RELATIVE_SPREAD = 0.20
MAX_CANDIDATES = 40
MAX_STRIKES_PER_TYPE = 9
PROBABILITY_GRID_POINTS = 1001
TARGET_CREDIT_RETENTION = 0.50
TARGET_PREMIUM_GAIN = 0.50
LOW_RISK_EXPOSURE_RATIO = 0.005
MEDIUM_RISK_EXPOSURE_RATIO = 0.015
LOW_DELTA_THRESHOLD = 0.25
DIRECTION_WEIGHTS = {"technicals": 0.50, "fundamentals": 0.35, "breadth": 0.15}
YEAR_SECONDS = 365.0 * 86400
NORMAL = NormalDist()


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    return value.astimezone(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _session_time(session, field):
    return datetime.combine(date.fromisoformat(session["sessionDate"]),
                            time.fromisoformat(session[field]), IST).astimezone(UTC)


def last_session_reference_window(now, sessions):
    now = _aware(now)
    today = now.astimezone(IST).date()
    completed = [s for s in sessions if s["isTradingDay"] and _session_time(s, "closeTime") <= now]
    if not completed:
        return None
    last = max(completed, key=lambda s: s["sessionDate"])
    days = {s["sessionDate"] for s in sessions}
    day = date.fromisoformat(last["sessionDate"])
    while day <= today:
        if day.isoformat() not in days:
            return None
        day += timedelta(days=1)
    close = _session_time(last, "closeTime")
    start = close - timedelta(minutes=15)
    end = min(now, datetime.combine(date.fromisoformat(last["sessionDate"]), time.max, IST).astimezone(UTC))
    return {"sessionDate": last["sessionDate"], "windowStart": _iso(start), "windowEnd": _iso(end),
            "tradeWindowEnd": _iso(close + timedelta(minutes=15))}


def snapshot_policy(now, sessions, spot_observed_at, mode):
    """Keep live entry gates separate from a bounded last-session reference."""
    now = _aware(now)
    observed = _aware(spot_observed_at) if spot_observed_at is not None else None
    today = now.astimezone(IST).date()
    current = next((s for s in sessions if s["sessionDate"] == today.isoformat()), None)
    market_open = bool(current and current["isTradingDay"]
                       and _session_time(current, "openTime") <= now < _session_time(current, "closeTime"))
    if market_open:
        fresh = observed is not None and -MAX_FUTURE_QUOTE_SECONDS <= (now - observed).total_seconds() <= 30
        return {"executionStatus": "live-ready" if fresh else "unavailable",
                "quoteAsOf": _iso(observed) if observed else None,
                "reason": "Live entry still requires contract, cutoff and risk checks." if fresh
                else "Underlying timestamp is missing, stale or future-dated during the live session."}
    result = {"executionStatus": "unavailable", "quoteAsOf": _iso(observed) if observed else None,
              "reason": "No verified last-session reference; live entry is unavailable."}
    if mode == "expiry-intraday" or observed is None or observed > now + timedelta(seconds=MAX_FUTURE_QUOTE_SECONDS):
        return result
    window = last_session_reference_window(now, sessions)
    if window is None or not _aware(window["windowStart"]) <= observed <= _aware(window["windowEnd"]):
        return result
    return {"executionStatus": "planning-only", "quoteAsOf": _iso(observed),
            **window,
            "reason": "Last-session reference (including dated post-close updates), not a closing print or entry. Recheck live prices, legs and risk next session."}


def quote_in_policy(observed_at, now, policy=None):
    observed_at, now = _aware(observed_at), _aware(now)
    if policy and policy.get("executionStatus") == "unavailable":
        return False
    if policy and policy.get("executionStatus") == "planning-only":
        return _aware(policy["windowStart"]) <= observed_at <= _aware(policy["windowEnd"])
    return -MAX_FUTURE_QUOTE_SECONDS <= (now - observed_at).total_seconds() <= MAX_QUOTE_AGE_SECONDS


def _monthly_option_expiries(instruments):
    month_codes = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    expiries = set()
    for instrument in instruments:
        if instrument.get("instrumentType") not in ("CE", "PE") or not instrument.get("expiry"):
            continue
        symbol = instrument.get("tradingSymbol")
        if not isinstance(symbol, str):
            continue
        match = re.fullmatch(r"(NIFTY|SENSEX)(\d{2})([A-Z]{3})(\d+(?:\.\d+)?)(CE|PE)", symbol)
        if not match:
            continue
        expiry = date.fromisoformat(instrument["expiry"])
        expected_exchange = "NFO" if match[1] == "NIFTY" else "BFO"
        if (instrument.get("exchange") == expected_exchange
                and match[2] == f"{expiry.year % 100:02d}"
                and match[3] == month_codes[expiry.month - 1]
                and match[5] == instrument["instrumentType"]
                and _number(instrument.get("strike"))
                and float(match[4]) == instrument["strike"]):
            expiries.add(expiry)
    return expiries


def resolve_horizon(now: datetime, mode: str, sessions: list[dict],
                    instruments: list[dict], *, entry_minutes_before_close: int = 30,
                    exit_minutes_before_close: int = 15) -> dict:
    """Resolve expiries from broker contracts and session times from the calendar.

    Coverage must be contiguous up to the target. Missing future contracts or
    calendar rows are errors, never inferred weekdays/holidays.
    """
    now = _aware(now)
    if (not isinstance(entry_minutes_before_close, int) or isinstance(entry_minutes_before_close, bool)
            or not isinstance(exit_minutes_before_close, int) or isinstance(exit_minutes_before_close, bool)
            or not 0 < exit_minutes_before_close < entry_minutes_before_close):
        raise ValueError("Intraday cutoffs require 0 < exit minutes < entry minutes before close")
    if mode not in ("daily", "weekly", "monthly", "expiry-intraday"):
        raise ValueError("Unsupported horizon mode")
    calendar = {}
    for session in sessions:
        day = date.fromisoformat(session["sessionDate"])
        if day in calendar:
            raise ValueError("Duplicate calendar session")
        if session["isTradingDay"]:
            opening, closing = _session_time(session, "openTime"), _session_time(session, "closeTime")
            if opening >= closing:
                raise ValueError("Invalid calendar session times")
        calendar[day] = session
    today = now.astimezone(IST).date()
    if today not in calendar:
        raise ValueError("Missing calendar coverage for current date")
    expiries = sorted({date.fromisoformat(i["expiry"]) for i in instruments
                       if i.get("expiry") and i.get("instrumentType") in ("CE", "PE")})
    trading = sorted(d for d, s in calendar.items() if d >= today and s["isTradingDay"])
    cutoff = None
    warnings = []
    if mode == "daily":
        today_session = calendar[today]
        daily_cutoff = datetime.combine(today, time(14), IST).astimezone(UTC)
        if today_session["isTradingDay"] and now < min(daily_cutoff, _session_time(today_session, "closeTime")):
            target_day = today
        else:
            target_day = next((d for d in trading if d > today), None)
        if target_day is None:
            raise ValueError("Missing future calendar coverage for daily target")
        target = _session_time(calendar[target_day], "closeTime")
        exit_at = target
        cutoff = min(datetime.combine(target_day, time(14), IST).astimezone(UTC), target)
    elif mode == "expiry-intraday":
        if not calendar[today]["isTradingDay"] or today not in expiries:
            raise ValueError("Today is not an imported trading session with a listed expiry")
        target_day = today
        target = _session_time(calendar[today], "closeTime")
        cutoff = target - timedelta(minutes=entry_minutes_before_close)
        exit_at = target - timedelta(minutes=exit_minutes_before_close)
        if cutoff <= _session_time(calendar[today], "openTime"):
            raise ValueError("Expiry session too short for configured entry/exit windows")
        warnings.append("Late-session gamma/liquidity risk; hypothetical exit, not an automatic order")
    else:
        listed = set(expiries) if mode == "weekly" else _monthly_option_expiries(instruments)
        target_day = next((d for d in expiries if d > today and d in listed), None)
        if target_day is None:
            raise ValueError("Missing broker-listed option contract coverage for " + mode)
        if target_day not in calendar:
            raise ValueError("Missing calendar coverage for broker-listed expiry " + target_day.isoformat())
        if not calendar[target_day]["isTradingDay"]:
            raise ValueError("Broker-listed option expiry is not a trading session: " + target_day.isoformat())
        target = exit_at = _session_time(calendar[target_day], "closeTime")
    day = today
    while day <= target_day:
        if day not in calendar:
            raise ValueError("Missing calendar coverage for " + day.isoformat())
        day += timedelta(days=1)
    same_day_allowed = mode == "expiry-intraday" or (
        mode == "daily" and target_day == today and
        now < datetime.combine(today, time(14), IST).astimezone(UTC))
    eligible = []
    closes = {}
    future_count = 0
    for expiry in expiries:
        if expiry < target_day or (expiry == today and not same_day_allowed):
            continue
        if expiry not in calendar:
            if future_count < 2:
                raise ValueError("Missing future calendar coverage for listed expiry " + expiry.isoformat())
            continue
        session = calendar[expiry]
        if not session["isTradingDay"]:
            raise ValueError("Listed contract expiry is not an imported trading session: " + expiry.isoformat())
        if session["isTradingDay"] and _session_time(session, "closeTime") >= exit_at:
            eligible.append(expiry.isoformat())
            closes[expiry.isoformat()] = _iso(_session_time(session, "closeTime"))
            if expiry > today:
                future_count += 1
    buying = [d for d in eligible if d > today.isoformat()][:2]
    if not eligible:
        raise ValueError("Missing calendar/listed contract coverage for eligible expiry")
    if not buying:
        warnings.append("No covered future buying expiry; same-day buying is prohibited")
    elif len(buying) < 2:
        warnings.append("Only one covered future buying expiry is available")
    current = calendar[today]
    market_open = (current["isTradingDay"] and _session_time(current, "openTime") <= now
                   < _session_time(current, "closeTime"))
    can_suggest = market_open and now < exit_at and (cutoff is None or now < cutoff)
    if not can_suggest:
        warnings.append("Outside the live entry window; last-session planning requires a verified closing reference")
    return {"targetAt": _iso(target), "plannedExitAt": _iso(exit_at),
            "entryCutoffAt": _iso(cutoff) if cutoff else None,
            "eligibleExpiries": eligible, "buyingExpiries": buying,
            "canSuggest": can_suggest, "warnings": warnings, "expiryCloseAt": closes}


def european_price(spot, strike, years, rate, dividend, volatility, option_type):
    """Black--Scholes price with explicit continuous rate/carry inputs."""
    if option_type not in ("CE", "PE"):
        raise ValueError("Option type must be CE or PE")
    if not all(_number(v) for v in (spot, strike, years, rate, dividend, volatility)):
        raise ValueError("Pricing requires finite explicit inputs")
    if spot <= 0 or strike <= 0 or years < 0 or volatility < 0:
        raise ValueError("Invalid pricing inputs")
    if years == 0:
        return max(0.0, spot - strike if option_type == "CE" else strike - spot)
    forward_spot, discounted_strike = spot * exp(-dividend * years), strike * exp(-rate * years)
    if volatility == 0:
        return max(0.0, forward_spot - discounted_strike if option_type == "CE"
                   else discounted_strike - forward_spot)
    d1 = (log(spot / strike) + (rate - dividend + volatility ** 2 / 2) * years) / (volatility * sqrt(years))
    d2 = d1 - volatility * sqrt(years)
    if option_type == "CE":
        return forward_spot * NORMAL.cdf(d1) - discounted_strike * NORMAL.cdf(d2)
    return discounted_strike * NORMAL.cdf(-d2) - forward_spot * NORMAL.cdf(-d1)


def implied_volatility(price, spot, strike, years, rate, dividend, option_type):
    if years <= 0 or not _number(price) or price <= 0:
        return None
    lower = european_price(spot, strike, years, rate, dividend, 0, option_type)
    upper = spot * exp(-dividend * years) if option_type == "CE" else strike * exp(-rate * years)
    if not lower < price < upper:
        return None
    lo, hi = 0.000001, 8.0
    if european_price(spot, strike, years, rate, dividend, hi, option_type) < price:
        return None
    for _ in range(90):
        mid = (lo + hi) / 2
        if european_price(spot, strike, years, rate, dividend, mid, option_type) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def european_greeks(spot, strike, years, rate, dividend, volatility, option_type):
    if years <= 0 or volatility <= 0:
        return None
    d1 = (log(spot / strike) + (rate - dividend + volatility ** 2 / 2) * years) / (volatility * sqrt(years))
    d2 = d1 - volatility * sqrt(years)
    density = exp(-d1 * d1 / 2) / sqrt(2 * pi)
    carry = exp(-dividend * years)
    call = option_type == "CE"
    delta = carry * (NORMAL.cdf(d1) if call else NORMAL.cdf(d1) - 1)
    theta = -spot * carry * density * volatility / (2 * sqrt(years))
    if call:
        theta += dividend * spot * carry * NORMAL.cdf(d1) - rate * strike * exp(-rate * years) * NORMAL.cdf(d2)
    else:
        theta += -dividend * spot * carry * NORMAL.cdf(-d1) + rate * strike * exp(-rate * years) * NORMAL.cdf(-d2)
    return {"delta": delta, "gamma": carry * density / (spot * volatility * sqrt(years)),
            "vega": spot * carry * density * sqrt(years) / 100,
            "theta": theta / 365,
            "rho": ((strike * years * exp(-rate * years) * NORMAL.cdf(d2)) if call
                    else (-strike * years * exp(-rate * years) * NORMAL.cdf(-d2))) / 100,
            "units": "delta per point; gamma per point squared; vega/rho per percentage point; theta per calendar day"}


def _ema(values, period):
    if len(values) < period:
        return []
    output = [mean(values[:period])]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def _technicals(spot, bars, now, completed_through=None):
    """Include today's candle only with a verified completed-session cutoff."""
    today = now.astimezone(IST).date()
    cutoff = completed_through if completed_through is not None else today - timedelta(days=1)
    if cutoff > today:
        raise ValueError("Completed daily bar cutoff cannot be in the future")
    cutoff = cutoff.isoformat()
    valid, seen = [], set()
    for bar in sorted(bars, key=lambda b: b.get("date", "")):
        day = str(bar.get("date", ""))[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        values = [bar.get(k) for k in ("open", "high", "low", "close")]
        if (day > cutoff or day in seen or not all(_number(v) and v > 0 for v in values)
                or bar["low"] > min(bar["open"], bar["close"])
                or bar["high"] < max(bar["open"], bar["close"])):
            continue
        seen.add(day)
        valid.append(bar)
    closes = [b["close"] for b in valid]
    indicators = []
    votes = []

    def add(key, label, value, unit, vote=None, reason=""):
        contribution = None if vote is None else vote
        indicators.append({"key": key, "label": label, "value": value, "unit": unit,
                           "direction": ("unavailable" if value is None else
                                         "neutral" if not vote else "bullish" if vote > 0 else "bearish"),
                           "contribution": contribution, "reason": reason,
                           "timeframe": "completed daily", "factorRole": "trend/momentum"})

    # One trend vote avoids counting each correlated moving average separately.
    for period in (20, 50, 200):
        value = mean(closes[-period:]) if len(closes) >= period else None
        add("sma" + str(period), "SMA " + str(period), value, "points",
            None if value is None else (1 if spot > value else -1 if spot < value else 0),
            "Price versus completed daily average; averages share one trend vote")
    averages = [i["contribution"] for i in indicators if i["value"] is not None]
    if averages:
        votes.append(mean(averages))
    ema = _ema(closes, 20)
    add("ema20", "EMA 20", ema[-1] if ema else None, "points", reason="SMA-seeded exponential average; contextual, not another trend vote")
    rsi = atr = rv = macd = signal = adx = None
    if len(closes) >= 15:
        differences = [b - a for a, b in zip(closes, closes[1:])]
        gain = mean([max(d, 0) for d in differences[:14]])
        loss = mean([max(-d, 0) for d in differences[:14]])
        for change in differences[14:]:
            gain = (13 * gain + max(change, 0)) / 14
            loss = (13 * loss + max(-change, 0)) / 14
        rsi = 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
        true_ranges, plus_dm, minus_dm = [], [], []
        for prior, current in zip(valid, valid[1:]):
            true_ranges.append(max(current["high"] - current["low"],
                                   abs(current["high"] - prior["close"]),
                                   abs(current["low"] - prior["close"])))
            up, down = current["high"] - prior["high"], prior["low"] - current["low"]
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
        atr = mean(true_ranges[:14])
        tr, plus, minus = sum(true_ranges[:14]), sum(plus_dm[:14]), sum(minus_dm[:14])
        dxs = []
        for index in range(14, len(true_ranges) + 1):
            if index > 14:
                tr = tr - tr / 14 + true_ranges[index - 1]
                plus = plus - plus / 14 + plus_dm[index - 1]
                minus = minus - minus / 14 + minus_dm[index - 1]
                atr = (13 * atr + true_ranges[index - 1]) / 14
            pdi, mdi = (100 * plus / tr, 100 * minus / tr) if tr else (0, 0)
            dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0)
        if len(dxs) >= 14:
            adx = mean(dxs[:14])
            for dx in dxs[14:]:
                adx = (13 * adx + dx) / 14
    if len(closes) >= 21:
        returns = [log(b / a) for a, b in zip(closes[-21:], closes[-20:])]
        rv = stdev(returns) * sqrt(252)
    if len(closes) >= 26:
        fast, slow = _ema(closes, 12), _ema(closes, 26)
        macds = [a - b for a, b in zip(fast[14:], slow)]
        macd = macds[-1]
        signals = _ema(macds, 9)
        signal = signals[-1] if signals else None
    rsi_vote = None if rsi is None else (1 if rsi > 55 else -1 if rsi < 45 else 0)
    add("rsi14", "RSI 14", rsi, "oscillator", rsi_vote, "Wilder smoothing; bullish >55, bearish <45; not a probability")
    if rsi_vote is not None:
        votes.append(rsi_vote)
    macd_vote = None if signal is None else (1 if macd > signal else -1 if macd < signal else 0)
    add("macd", "MACD 12/26", macd, "points", macd_vote, "MACD relative to 9-period signal")
    add("macdSignal", "MACD signal 9", signal, "points")
    if macd_vote is not None:
        votes.append(macd_vote)
    for indicator in indicators:
        vote = indicator["contribution"]
        if indicator["key"] in ("sma20", "sma50", "sma200") and vote is not None:
            indicator["scoreContribution"] = 50 * vote / (len(averages) * len(votes))
        elif indicator["key"] in ("rsi14", "macd") and vote is not None:
            indicator["scoreContribution"] = 50 * vote / len(votes)
        else:
            indicator["scoreContribution"] = None
    add("adx14", "ADX 14", adx, "strength", reason="Non-directional Wilder trend strength; no directional vote")
    score = 50 + 50 * mean(votes) if votes else None
    dimension = {"key": "technicals", "label": "Technicals", "score": score,
                 "scale": "Bearish to bullish", "coverage": min(len(closes) / 200, 1),
                 "indicators": indicators}
    volatility = {"key": "volatility", "label": "Volatility", "scale": "Low to high",
                  "score": None if rv is None else min(100, rv / 0.5 * 100),
                  "coverage": 1 if rv is not None else 0,
                  "indicators": [
                      {"key": "realizedVol20", "label": "20-session realised volatility", "value": rv,
                       "unit": "annualised fraction", "reason": "Sample log-return deviation x sqrt(252); score anchors 0%-50%"},
                      {"key": "atr14", "label": "ATR 14", "value": atr, "unit": "points",
                       "reason": "Wilder-smoothed true range"}]}
    levels = []
    for index in range(2, len(valid) - 2):
        window = valid[index - 2:index + 3]
        if valid[index]["low"] == min(b["low"] for b in window):
            levels.append(valid[index]["low"])
        if valid[index]["high"] == max(b["high"] for b in window):
            levels.append(valid[index]["high"])
    if valid:
        # Prior session and 20-session extrema are explicit fallback levels.
        recent = valid[-20:]
        prior = valid[-1]
        pivot = (prior["high"] + prior["low"] + prior["close"]) / 3
        levels += [prior["low"], prior["high"], min(b["low"] for b in recent),
                   max(b["high"] for b in recent), 2 * pivot - prior["high"],
                   2 * pivot - prior["low"]]
    lower = max((x for x in levels if 0 < x < spot), default=None)
    upper = min((x for x in levels if x > spot), default=None)
    return dimension, volatility, {"support": lower, "resistance": upper,
                                 "method": "Confirmed five-bar swings, prior-session pivots/high/low and 20-session extrema; nearest valid level",
                                 "intradayCoverage": "unavailable: daily bars only"}, rv


def _quote_models(spot, quotes, horizon, now, rate, dividend, quote_policy=None):
    valid, rejected = [], []
    model_available = all(_number(v) for v in (rate, dividend))
    eligible_quotes = [q for q in quotes if q.get("instrumentType") in ("CE", "PE")
                       and q.get("expiry") in horizon["eligibleExpiries"]]
    identities = {(q.get("exchange"), q.get("underlying") or
                   re.split(r"\d", q.get("tradingSymbol", ""), maxsplit=1)[0])
                  for q in eligible_quotes}
    if len(identities) > 1:
        return [], ["Mixed underlying/exchange chain rejected"]
    keys = [(q.get("exchange"), q.get("tradingSymbol")) for q in eligible_quotes]
    if len(set(keys)) != len(keys):
        return [], ["Duplicate instrument quotes: ambiguous snapshot rejected"]
    seen = set()
    for quote in sorted(quotes, key=lambda q: (q.get("exchange", ""), q.get("tradingSymbol", ""))):
        key = quote.get("exchange", "") + ":" + quote.get("tradingSymbol", "")
        try:
            if quote.get("instrumentType") not in ("CE", "PE"):
                continue
            if quote.get("expiry") not in horizon["eligibleExpiries"]:
                continue
            if key in seen:
                raise ValueError("duplicate instrument")
            seen.add(key)
            bid, ask = quote.get("bid"), quote.get("ask")
            price_basis = "captured-bid-ask"
            price_observed = None
            planning = quote_policy and quote_policy.get("executionStatus") == "planning-only"
            if planning and not all(_number(x) and x > 0 for x in (bid, ask)):
                reference = quote.get("lastPrice")
                if not _number(reference) or reference <= 0:
                    raise ValueError("missing closing price reference")
                traded_at = _aware(quote["lastTradeAt"])
                if (not quote_in_policy(traded_at, now, quote_policy)
                        or traded_at > _aware(quote_policy["tradeWindowEnd"])
                        or traded_at > _aware(quote["observedAt"]) + timedelta(seconds=MAX_FUTURE_QUOTE_SECONDS)):
                    raise ValueError("last trade is outside the verified closing reference window")
                bid = ask = reference
                price_basis = "last-traded-reference"
                price_observed = traded_at
            if not all(_number(x) and x > 0 for x in (bid, ask)):
                raise ValueError("zero/missing executable quote")
            if "lastPrice" in quote and (not _number(quote["lastPrice"]) or quote["lastPrice"] <= 0):
                raise ValueError("zero/missing last price")
            if bid > ask:
                raise ValueError("crossed quote")
            if (ask - bid) / ((ask + bid) / 2) > MAX_RELATIVE_SPREAD:
                raise ValueError("wide spread")
            observed = _aware(quote["observedAt"])
            if not quote_in_policy(observed, now, quote_policy):
                raise ValueError("stale/future quote")
            strike, lot = quote["strike"], quote["lotSize"]
            if not _number(strike) or strike <= 0 or not isinstance(lot, int) or isinstance(lot, bool) or lot <= 0:
                raise ValueError("invalid strike/lot size")
            if not _number(quote.get("tickSize")) or quote["tickSize"] <= 0:
                raise ValueError("invalid tick size")
            if bid < quote["tickSize"] or ask < quote["tickSize"]:
                raise ValueError("executable premium below minimum tick")
            expiry_at = _aware(horizon["expiryCloseAt"][quote["expiry"]])
            years = (expiry_at - now).total_seconds() / YEAR_SECONDS
            if years <= 0:
                raise ValueError("expired contract")
            iv = greeks = None
            if model_available:
                lower = european_price(spot, strike, years, rate, dividend, 0, quote["instrumentType"])
                upper = spot * exp(-dividend * years) if quote["instrumentType"] == "CE" else strike * exp(-rate * years)
                if bid <= lower or ask >= upper:
                    raise ValueError("arbitrage-invalid executable quote")
                iv = implied_volatility((bid + ask) / 2, spot, strike, years, rate, dividend, quote["instrumentType"])
                if iv is None:
                    raise ValueError("no finite implied volatility solution")
                greeks = european_greeks(spot, strike, years, rate, dividend, iv, quote["instrumentType"])
            q = dict(quote)
            q.update({"bid": bid, "ask": ask, "priceBasis": price_basis,
                      "iv": iv, "_observed": price_observed or observed, "_expiryAt": expiry_at,
                      "_underlying": quote.get("underlying") or
                      re.split(r"\d", quote["tradingSymbol"], maxsplit=1)[0],
                      "greeks": greeks})
            valid.append(q)
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            rejected.append(key + ": " + str(error))
    # Executable vertical, butterfly and put/call parity checks. Never compare
    # asynchronous prices to diagnose arbitrage.
    invalid_expiries = set()
    for expiry in sorted({q["expiry"] for q in valid}):
        chain = [q for q in valid if q["expiry"] == expiry]
        years = (chain[0]["_expiryAt"] - now).total_seconds() / YEAR_SECONDS
        discount = exp(-rate * years) if _number(rate) else None

        def synchronous(items):
            return (all(q["priceBasis"] == "captured-bid-ask" for q in items)
                    and (max(q["_observed"] for q in items) - min(q["_observed"] for q in items)).total_seconds() <= MAX_QUOTE_SKEW_SECONDS)

        def fail(reason):
            invalid_expiries.add(expiry)
            rejected.append(expiry + ": executable arbitrage violation (" + reason + ")")

        for kind in ("CE", "PE"):
            options = sorted((q for q in chain if q["instrumentType"] == kind), key=lambda q: q["strike"])
            for i, low in enumerate(options):
                for high in options[i + 1:]:
                    if not synchronous([low, high]):
                        continue
                    width = (high["strike"] - low["strike"]) * discount if discount is not None else None
                    if kind == "CE":
                        invalid = high["bid"] > low["ask"] + 1e-8 or (width is not None and low["bid"] - high["ask"] > width + 1e-8)
                    else:
                        invalid = low["bid"] > high["ask"] + 1e-8 or (width is not None and high["bid"] - low["ask"] > width + 1e-8)
                    if invalid:
                        fail("vertical")
            for low, middle, high in zip(options, options[1:], options[2:]):
                if synchronous([low, middle, high]) and high["strike"] > low["strike"]:
                    weight = (high["strike"] - middle["strike"]) / (high["strike"] - low["strike"])
                    if middle["bid"] > weight * low["ask"] + (1 - weight) * high["ask"] + 1e-8:
                        fail("butterfly convexity")
        for call in (q for q in chain if q["instrumentType"] == "CE"):
            for put in (q for q in chain if q["instrumentType"] == "PE" and q["strike"] == call["strike"]):
                if synchronous([call, put]) and model_available:
                    parity = spot * exp(-dividend * years) - call["strike"] * discount
                    if call["bid"] - put["ask"] > parity + 1e-8 or call["ask"] - put["bid"] < parity - 1e-8:
                        fail("put-call parity")
    valid = [q for q in valid if q["expiry"] not in invalid_expiries]
    return valid, rejected


def _payoff(legs, terminal, costs):
    return sum((1 if l["direction"] == "buy" else -1) * l["quantity"] *
               (max(0, terminal - l["strike"] if l["optionType"] == "CE"
                    else l["strike"] - terminal) - l["entryPrice"]) for l in legs) - costs


def _payoff_bounds(legs, costs):
    values = [_payoff(legs, p, costs) for p in {0.0, *(l["strike"] for l in legs)}]
    tail_slope = sum((1 if l["direction"] == "buy" else -1) * l["quantity"]
                     for l in legs if l["optionType"] == "CE")
    # Put losses are bounded because the underlying cannot fall below zero.
    max_loss = None if tail_slope < 0 else max(0.0, -min(values))
    max_profit = None if tail_slope > 0 else max(values)
    return max_loss, max_profit


def _mark_pnl(legs, models, underlying, at, rate, dividend, costs, iv_shift=0):
    value = 0.0
    for leg, quote in zip(legs, models):
        years = max(0, (quote["_expiryAt"] - at).total_seconds() / YEAR_SECONDS)
        premium = european_price(underlying, leg["strike"], years, rate, dividend,
                                 max(0.000001, quote["iv"] + iv_shift), leg["optionType"])
        value += (1 if leg["direction"] == "buy" else -1) * leg["quantity"] * (premium - leg["entryPrice"])
    return value - costs


def _make_candidate(strategy, specification, spot, levels, horizon, now, rate, dividend, cost, scenarios, bias):
    models = [q for q, _ in specification]
    if len({(q["expiry"], q["lotSize"], q["exchange"], q["_underlying"]) for q in models}) != 1:
        return None
    if len({q["tradingSymbol"] for q in models}) != len(models):
        return None
    if (max(q["_observed"] for q in models) - min(q["_observed"] for q in models)).total_seconds() > MAX_QUOTE_SKEW_SECONDS:
        return None
    legs = [{"exchange": q["exchange"], "tradingSymbol": q["tradingSymbol"],
             "instrumentToken": q.get("instrumentToken"), "direction": direction,
             "quantity": q["lotSize"], "strike": q["strike"], "optionType": q["instrumentType"],
             "entryPrice": q["ask"] if direction == "buy" else q["bid"],
             "priceBasis": q["priceBasis"], "quoteObservedAt": _iso(q["_observed"]),
             "impliedVolatility": q["iv"], "greeks": q["greeks"]}
            for q, direction in specification]
    fees_available = _number(cost) and cost >= 0
    model_available = all(_number(v) for v in (rate, dividend)) and all(_number(q["iv"]) for q in models)
    costs = len(legs) * cost if fees_available else None
    gross_loss, gross_profit = _payoff_bounds(legs, 0)
    # Zero here computes explicitly gross payoff, never an assumed charge.
    max_loss, max_profit = _payoff_bounds(legs, costs) if fees_available else (gross_loss, gross_profit)
    if max_loss is None or max_loss <= 0 or (max_profit is not None and max_profit <= 0):
        return None
    selling = strategy in ("put-credit-spread", "call-credit-spread", "iron-condor", "iron-fly")
    net = -sum((1 if l["direction"] == "buy" else -1) * l["entryPrice"] * l["quantity"] for l in legs)
    if (selling and net <= (costs if fees_available else 0)) or (not selling and net >= 0):
        return None
    bullish = strategy in ("put-credit-spread", "call-debit-spread", "long-call")
    bearish = strategy in ("call-credit-spread", "put-debit-spread", "long-put")
    lower = levels["support"] if bullish or not bearish else None
    upper = levels["resistance"] if bearish or not bullish else None
    if (bullish and lower is None) or (bearish and upper is None) or (not bullish and not bearish and (lower is None or upper is None)):
        return None
    lot = models[0]["lotSize"]
    ratio = max_loss / (spot * lot)
    tier = "low" if ratio <= LOW_RISK_EXPOSURE_RATIO else "medium" if ratio <= MEDIUM_RISK_EXPOSURE_RATIO else "high"
    risk_reasons = ["Base tier from one-lot maximum loss divided by index notional"]
    if not fees_available:
        tier = "high"
        risk_reasons = ["Charges unavailable: high-risk uncertainty floor; gross expiry bound is not a fee-inclusive loss cap"]
    same_day = models[0]["expiry"] == now.astimezone(IST).date().isoformat()
    if same_day:
        if now.astimezone(IST).time().replace(tzinfo=None) >= time(14):
            tier = "high"
            risk_reasons.append("Same-day expiry at/after 14:00 IST: high-risk gamma floor")
        else:
            if tier == "low":
                tier = "medium"
            risk_reasons.append("Same-day expiry before 14:00 IST: at least medium-risk gamma floor")
    if (len(legs) == 1 and models[0].get("_moneyness") == "OTM"
            and (not model_available or abs(models[0]["greeks"]["delta"]) < LOW_DELTA_THRESHOLD)):
        tier = "high"
        risk_reasons.append("Outright OTM with unavailable delta or absolute delta below 0.25: high-risk decay/directional hurdle floor")
    planned_exit = _aware(horizon["plannedExitAt"])
    stop_losses = ([-_mark_pnl(legs, models, level, now, rate, dividend, costs)
                    for level in (lower, upper) if level is not None]
                   if model_available and fees_available else [])
    warning = ["Hypothetical one-lot trade; no margin estimate or fill guarantee",
               "Stop estimate assumes immediate level hit and sticky-strike IV; gaps/time/IV and spreads can worsen loss",
               "Target is a fixed configured premium rule, not an optimal exit"]
    if not stop_losses:
        warning.remove("Stop estimate assumes immediate level hit and sticky-strike IV; gaps/time/IV and spreads can worsen loss")
    if bullish and bias != "bullish" or bearish and bias != "bearish" or not bullish and not bearish and bias != "neutral":
        warning.append("Counter-bias alternative: weaker technical fit, not a recommendation")
    if strategy in ("long-call", "long-put"):
        warning.append("Entire premium and costs can be lost; OTM is not inherently safer")
    if same_day:
        warning.append("Same-day expiry: high gamma, liquidity and settlement risk")
    if not model_available:
        warning.append("Rate/dividend yield unavailable: IV, Greeks, model probabilities, repricing and estimated stop loss unavailable; quoted expiry payoff arithmetic only")
    if not fees_available:
        warning.append("Charges unavailable: gross before charges only; net profit, capital requirement and fee-inclusive maximum loss unavailable")
    target_gross = net * TARGET_CREDIT_RETENTION if selling else -net * TARGET_PREMIUM_GAIN
    target_profit = target_gross - costs if fees_available else None
    # Do not show a target that cannot pay the stated costs or exceeds payoff.
    if fees_available and (target_profit <= 0 or max_profit is not None and target_profit > max_profit):
        return None
    target_value = (net * (1 - TARGET_CREDIT_RETENTION) if selling
                    else -net * (1 + TARGET_PREMIUM_GAIN))
    target_years = (planned_exit - now).total_seconds() / YEAR_SECONDS
    probability = None
    probability_reason = "Explicit rate/dividend, finite IV, known charges and positive target time required; no net probability is inferred"
    scenario_probabilities = {}
    conditional_counts = {}
    for scenario in scenarios:
        matching_time = _aware(scenario["targetAt"]) == planned_exit
        valid_band = (all(_number(scenario.get(k)) for k in ("low", "high")) and
                      scenario["low"] < scenario["high"])
        supported = matching_time and valid_band and target_years > 0 and model_available and fees_available
        conditional_counts[scenario["id"]] = [0, 0, supported]
        scenario_probabilities[scenario["id"]] = {
            "probability": None, "probabilityType": "unavailable",
            "condition": {"low": scenario.get("low"), "high": scenario.get("high"),
                          "at": scenario["targetAt"]},
            "gridPoints": PROBABILITY_GRID_POINTS, "matchedGridPoints": 0,
            "reason": ("Scenario target differs from planned exit; single-terminal grid cannot establish cross-time conditional probability"
                       if not matching_time else "Model inputs, charges, valid interval and positive time required; no conditional probability")}
    reference_iv = mean(q["iv"] for q in models) if model_available else None
    if target_years > 0 and model_available and fees_available:
        profitable = 0
        for i in range(PROBABILITY_GRID_POINTS):
            z = NORMAL.inv_cdf((i + 0.5) / PROBABILITY_GRID_POINTS)
            future_spot = spot * exp((rate - dividend - reference_iv ** 2 / 2) * target_years
                                     + reference_iv * sqrt(target_years) * z)
            is_profitable = _mark_pnl(legs, models, future_spot, planned_exit, rate, dividend, costs) > 0
            if is_profitable:
                profitable += 1
            for scenario in scenarios:
                counts = conditional_counts[scenario["id"]]
                if counts[2] and scenario["low"] <= future_spot <= scenario["high"]:
                    counts[0] += 1
                    counts[1] += int(is_profitable)
        probability = profitable / PROBABILITY_GRID_POINTS
        probability_reason = ("Risk-neutral lognormal terminal grid, equal-probability midpoint quadrature; "
                              "mean leg IV, sticky-strike IV; mark-to-model at planned exit including costs; "
                              "not first-hit probability or historically calibrated")
        for scenario_id, (matched, wins, supported) in conditional_counts.items():
            item = scenario_probabilities[scenario_id]
            item["matchedGridPoints"] = matched
            if supported and matched:
                item.update({"probability": wins / matched, "probabilityType": "model-implied",
                             "reason": "Conditional P(net mark-to-model profit > 0 | terminal spot in stated scenario interval); same risk-neutral midpoint grid/mean leg IV as unconditional probability; not calibrated or first-hit probability"})
            elif supported:
                item["reason"] = "No midpoint-grid mass resolves this interval; conditional probability unavailable"
    repricing = []
    for scenario in scenarios:
        if scenario["low"] is None or not model_available or not fees_available:
            continue
        for shift in (-0.05, 0.0, 0.05):
            repricing.append({"scenarioId": scenario["id"], "ivShift": shift,
                              "at": horizon["plannedExitAt"],
                              "lowPnl": _mark_pnl(legs, models, scenario["low"], planned_exit, rate, dividend, costs, shift),
                              "highPnl": _mark_pnl(legs, models, scenario["high"], planned_exit, rate, dividend, costs, shift),
                              "reason": "Endpoint repricing, not extrema over scenario interval"})
    moneyness = None
    if len(legs) == 1:
        # ATM defined by the chain's nearest strike, provided by the generator.
        moneyness = models[0].get("_moneyness")
    identity = "|".join(l["direction"] + ":" + l["exchange"] + ":" + l["tradingSymbol"] for l in legs)
    candidate_id = strategy + ":" + models[0]["expiry"] + ":" + sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {"id": candidate_id,
            "side": "selling" if selling else "buying", "riskTier": tier, "strategy": strategy,
            "expiry": models[0]["expiry"], "lotSize": lot, "legs": legs,
            "netPremium": net, "maxProfit": max_profit if fees_available else None,
            "maxLoss": max_loss if fees_available else None,
            "grossMaxProfit": gross_profit, "grossMaxLoss": gross_loss,
            "payoffBasis": "net-after-configured-charges" if fees_available else "gross-before-charges",
            "modelMetricsReason": None if model_available else "Explicit rate and dividend yield unavailable; no IV/Greeks or future model marks inferred",
            "costEstimate": costs, "capitalBasis": max_loss if fees_available else None,
            "riskExposureRatio": ratio if fees_available else None,
            "riskTierRule": "Base one-lot net maxLoss/(spot*lot): low<=0.5%, medium<=1.5%, otherwise high; unknown charges high; same-day before14IST at least medium, at/after14IST high; outright OTM unavailable delta or absolute delta<0.25 high; floors never lower a base tier",
            "riskTierReasons": risk_reasons,
            "rewardRisk": None if max_profit is None or not fees_available else max_profit / max_loss,
            "probabilityOfProfit": probability,
            "probabilityType": "model-implied" if probability is not None else "unavailable",
            "probabilityReason": probability_reason, "probabilityGridPoints": PROBABILITY_GRID_POINTS,
            "scenarioProfitProbabilities": scenario_probabilities,
            "scenarioIds": [s["id"] for s in scenarios], "scenarioRepricing": repricing,
            "moneyness": moneyness,
            "technicalStop": {"lower": lower, "upper": upper, "trigger": "first index quote at/beyond level"},
            "estimatedStopLoss": max(0, max(stop_losses)) if stop_losses else None,
            "estimatedStopLossReason": None if stop_losses else "Model inputs and known charges required; technical trigger levels remain valid",
            "targetPremium": target_value / lot, "targetProfit": target_profit, "grossTargetProfit": target_gross,
            "targetRule": "50% credit retention" if selling else "50% premium gain",
            "asOf": _iso(now),
            "plannedExitAt": horizon["plannedExitAt"], "entryCutoffAt": horizon.get("entryCutoffAt"),
            "warnings": warning}


def _generate(spot, models, levels, horizon, now, rate, dividend, costs, scenarios, bias):
    candidates = []
    selling_expiry = horizon["eligibleExpiries"][:1]
    buying_expiries = horizon["buyingExpiries"][:2]
    for expiry in sorted(set(selling_expiry + buying_expiries)):
        chain = [q for q in models if q["expiry"] == expiry]
        calls = sorted((q for q in chain if q["instrumentType"] == "CE"),
                       key=lambda q: (abs(q["strike"] - spot), q["strike"]))[:MAX_STRIKES_PER_TYPE]
        puts = sorted((q for q in chain if q["instrumentType"] == "PE"),
                      key=lambda q: (abs(q["strike"] - spot), q["strike"]))[:MAX_STRIKES_PER_TYPE]
        calls.sort(key=lambda q: q["strike"])
        puts.sort(key=lambda q: q["strike"])

        def append(name, specs):
            if len(candidates) >= MAX_CANDIDATES:
                return
            candidate = _make_candidate(name, specs, spot, levels, horizon, now, rate, dividend, costs, scenarios, bias)
            if candidate is not None:
                candidates.append(candidate)

        if expiry in buying_expiries:
            for kind, options in (("CE", calls), ("PE", puts)):
                if not options:
                    continue
                nearest = min(options, key=lambda q: (abs(q["strike"] - spot), q["strike"]))
                groups = {"ATM": nearest,
                          "ITM": next(iter(sorted((q for q in options if q != nearest and
                                                  (q["strike"] < spot if kind == "CE" else q["strike"] > spot)),
                                                 key=lambda q: abs(q["strike"] - spot))), None),
                          "OTM": next(iter(sorted((q for q in options if q != nearest and
                                                  (q["strike"] > spot if kind == "CE" else q["strike"] < spot)),
                                                 key=lambda q: abs(q["strike"] - spot))), None)}
                for label in ("ITM", "ATM", "OTM"):
                    if groups[label] is not None:
                        quote = dict(groups[label], _moneyness=label)
                        append("long-call" if kind == "CE" else "long-put", [(quote, "buy")])
            call_low = next((q for q in reversed(calls) if q["strike"] <= spot), None)
            call_high = next((q for q in calls if q["strike"] > spot), None)
            put_low = next((q for q in reversed(puts) if q["strike"] < spot), None)
            put_high = next((q for q in puts if q["strike"] >= spot), None)
            if call_low and call_high:
                append("call-debit-spread", [(call_low, "buy"), (call_high, "sell")])
            if put_low and put_high:
                append("put-debit-spread", [(put_high, "buy"), (put_low, "sell")])
        if expiry in selling_expiry:
            otm_puts = [q for q in puts if q["strike"] < spot]
            otm_calls = [q for q in calls if q["strike"] > spot]
            put_pair = [(otm_puts[-1], "sell"), (otm_puts[-2], "buy")] if len(otm_puts) >= 2 else None
            call_pair = [(otm_calls[0], "sell"), (otm_calls[1], "buy")] if len(otm_calls) >= 2 else None
            if put_pair:
                append("put-credit-spread", put_pair)
            if call_pair:
                append("call-credit-spread", call_pair)
            if put_pair and call_pair:
                append("iron-condor", put_pair + call_pair)
            common = sorted({q["strike"] for q in puts} & {q["strike"] for q in calls},
                            key=lambda k: (abs(k - spot), k))
            if common:
                center = common[0]
                lower = next((q for q in reversed(puts) if q["strike"] < center), None)
                upper = next((q for q in calls if q["strike"] > center), None)
                if lower and upper:
                    append("iron-fly", [(next(q for q in puts if q["strike"] == center), "sell"),
                                        (lower, "buy"),
                                        (next(q for q in calls if q["strike"] == center), "sell"),
                                        (upper, "buy")])
    return candidates


def _direction(dimensions):
    """Coverage-adjusted directional weights; quality/volatility never vote."""
    contributions = []
    for dimension in dimensions:
        key = dimension["key"]
        if key not in DIRECTION_WEIGHTS:
            continue
        score, coverage = dimension.get("score"), dimension.get("coverage", 0)
        if not _number(coverage) or not 0 <= coverage <= 1:
            raise ValueError("Dimension coverage must be a finite fraction: " + key)
        if score is not None and (not _number(score) or not 0 <= score <= 100):
            raise ValueError("Dimension score must be null or a finite 0-100 value: " + key)
        weight = DIRECTION_WEIGHTS[key] * coverage if score is not None else 0
        contributions.append({"key": key, "score": score, "coverage": coverage,
                              "configuredWeight": DIRECTION_WEIGHTS[key],
                              "coverageAdjustedWeight": weight})
    total = sum(item["coverageAdjustedWeight"] for item in contributions)
    signed_direction = 0
    for item in contributions:
        weight = item["coverageAdjustedWeight"] / total if total else 0
        contribution = weight * (item["score"] - 50) / 50 if weight else 0
        item.update({"effectiveWeight": weight, "contribution": contribution,
                     "reason": "Configured weight x available coverage, renormalised over directional dimensions only"})
        signed_direction += contribution
    bias = ("unavailable" if not total else "bullish" if signed_direction >= .20 - 1e-12
            else "bearish" if signed_direction <= -.20 + 1e-12 else "neutral")
    return bias, contributions


def build_analysis(spot: float, bars: list[dict], quotes: list[dict], horizon: dict,
                   now: datetime, risk_free_rate: float | None,
                   dividend_yield: float | None, cost_per_leg: float | None,
                   extra_dimensions: list[dict] | None = None,
                   quote_policy: dict | None = None,
                   completed_through: date | None = None) -> dict:
    now = _aware(now)
    if not _number(spot) or spot <= 0:
        raise ValueError("A finite positive spot is required")
    technicals, volatility, levels, rv = _technicals(spot, bars, now, completed_through)
    dimensions = [technicals, volatility]
    reserved = {"technicals", "volatility", "liquidity"}
    for dimension in extra_dimensions or []:
        key = dimension.get("key")
        if not isinstance(key, str) or not key or key in reserved:
            raise ValueError("Extra dimension must have a distinct non-reserved key")
        reserved.add(key)
        dimensions.append(deepcopy(dimension))
    bias, direction_contributions = _direction(dimensions)
    warnings = list(horizon.get("warnings", []))
    warnings += ["European continuous-carry model; sticky-strike IV and lognormal assumptions are not calibrated forecasts",
                 "Daily-only technicals: opening range and intraday VWAP unavailable"]
    for key in ("fundamentals", "breadth"):
        if key not in reserved:
            warnings.append(key + " unavailable: not supplied by parent")
    planning = bool(quote_policy and quote_policy.get("executionStatus") == "planning-only")
    if planning:
        risk_free_rate = dividend_yield = None
    model_inputs = all(_number(v) for v in (risk_free_rate, dividend_yield))
    fees_available = _number(cost_per_leg) and cost_per_leg >= 0
    if not model_inputs:
        warnings.append("Rate/dividend yield unavailable: descriptive historical-volatility bands and quote-based expiry payoffs only; no model IV/Greeks/probabilities")
    if not fees_available:
        warnings.append("Round-trip charges unavailable: gross before charges only; net profit and fee-inclusive maximum loss unavailable")
    target = _aware(horizon["targetAt"])
    years = max(0, (target - now).total_seconds() / YEAR_SECONDS)
    scenarios = []
    for key, label, z in (("narrow", "Narrow", 0.6744897501960817),
                          ("base", "Baseline", 1.2815515655446004),
                          ("wide", "Wide", 1.959963984540054)):
        available = rv is not None and rv > 0 and years > 0
        probability_available = available and model_inputs
        drift = (risk_free_rate - dividend_yield - rv ** 2 / 2) * years if probability_available else 0
        width = z * rv * sqrt(years) if available else None
        scenarios.append({"id": key, "label": label,
                          "low": spot * exp(drift - width) if available else None,
                          "high": spot * exp(drift + width) if available else None,
                          "probability": 2 * NORMAL.cdf(z) - 1 if probability_available else None,
                          "probabilityType": "model-implied" if probability_available else "unavailable",
                          "intervalType": "terminal-price central lognormal interval" if probability_available else "descriptive volatility band",
                          "reason": "Risk-neutral drift with 20-session realised volatility proxy; not calibrated or path coverage" if probability_available else "Descriptive historical-volatility band centred on captured spot, not assumed carry drift; explicit model inputs/history/positive volatility/time required for probability",
                          "targetAt": horizon["targetAt"]})
    models, rejections = _quote_models(spot, quotes, horizon, now, risk_free_rate, dividend_yield, quote_policy)
    warnings.extend(rejections)
    book_count = sum(q["priceBasis"] == "captured-bid-ask" for q in models)
    liquidity = {"key": "liquidity", "label": "Last-session book coverage" if planning else "Liquidity/data quality",
                 "scale": "Retained usable books (not live liquidity)" if planning else "Poor to strong",
                 "score": 100 * book_count / len(quotes) if quotes else None,
                 "coverage": len(models) / len(quotes) if quotes else 0,
                 "indicators": [
                     {"key": "validQuotes", "label": "Closing price references" if planning else "Valid executable quotes",
                      "value": len(models), "unit": "contracts", "reason": "Gate only, not directional evidence"},
                     {"key": "validBooks", "label": "Retained usable bid/ask books" if planning else "Usable bid/ask books",
                      "value": book_count, "unit": "contracts",
                      "reason": "Last-traded references do not establish executable liquidity."}],
                 "asOf": quote_policy.get("quoteAsOf") if quote_policy else _iso(now)}
    for dimension in dimensions + [liquidity]:
        influence = next((item for item in direction_contributions if item["key"] == dimension["key"]), None)
        for indicator in dimension.get("indicators", []):
            points = indicator.get("scoreContribution") if dimension["key"] == "technicals" else indicator.get("contribution")
            directional = (influence is not None and _number(points) and
                           indicator.get("direction") in ("bullish", "bearish", "neutral"))
            indicator["signalContribution"] = influence["effectiveWeight"] * points / 50 if directional else None
            indicator["influenceRole"] = "directional" if directional else "context"
            indicator["contributionBasis"] = "Coverage-adjusted directional signal, not index points or probability" if directional else "Context for range/risk selection; no calibrated numeric direction weight"
    candidates = []
    cutoff_ok = horizon.get("entryCutoffAt") is None or now < _aware(horizon["entryCutoffAt"])
    entry_allowed = (horizon.get("canSuggest") or planning) and (not quote_policy or quote_policy["executionStatus"] != "unavailable")
    if entry_allowed and cutoff_ok and now < _aware(horizon["plannedExitAt"]) and (not model_inputs or not fees_available or rv is not None and rv > 0):
        candidates = _generate(spot, models, levels, horizon, now, risk_free_rate,
                               dividend_yield, cost_per_leg, scenarios, bias)
    for candidate in candidates:
        candidate["executionStatus"] = "planning-only" if planning else "live-ready"
        candidate["requiresLiveRevalidation"] = planning
        candidate["quoteAsOf"] = min(leg["quoteObservedAt"] for leg in candidate["legs"])
        if planning:
            candidate["modelMetricsReason"] = "Closing references, not live model marks; no live IV, Greeks or stop repricing inferred."
            candidate["warnings"].append("Planning only: reference premiums are not entry fills. Revalidate live quotes, levels, charges and risk next session.")
    if not candidates:
        warnings.append("No defensible live-entry or last-session planning alternative under the available price, level and risk evidence")
    return {"schemaVersion": "options.v1", "modelVersion": MODEL_VERSION,
            "configVersion": CONFIG_VERSION, "spot": spot, "asOf": _iso(now), "bias": bias,
            "dimensions": dimensions + [liquidity],
            "directionContributions": direction_contributions, "scenarios": scenarios,
            "levels": levels, "candidates": candidates, "warnings": warnings,
            "executionStatus": "planning-only" if planning else "live-ready"
            if horizon.get("canSuggest") and (not quote_policy or quote_policy["executionStatus"] == "live-ready") else "unavailable",
            "snapshotPolicy": deepcopy(quote_policy),
            "entryCutoffAt": horizon.get("entryCutoffAt"), "plannedExitAt": horizon["plannedExitAt"],
            "modelInputs": {"riskFreeRate": risk_free_rate if _number(risk_free_rate) else None,
                            "dividendYield": dividend_yield if _number(dividend_yield) else None,
                            "roundTripCostPerLeg": cost_per_leg if fees_available else None},
            "quotePolicy": {"version": CONFIG_VERSION, "maxAgeSeconds": MAX_QUOTE_AGE_SECONDS,
                            "maxSkewSeconds": MAX_QUOTE_SKEW_SECONDS,
                            "maxFutureSeconds": MAX_FUTURE_QUOTE_SECONDS,
                            "maxRelativeSpread": MAX_RELATIVE_SPREAD}}


def reconcile_strategy(candidate: dict, observations: list[dict], target_at: datetime) -> dict:
    """First *observed* stop/target/time, not an inferred intratick first hit.

    Observations: {timestamp, spot, quotes: {"exchange:symbol": {bid, ask,
    observedAt}}}. A stop observed without executable legs ends as unavailable;
    it must not be retroactively filled at a later favourable observation.
    """
    if candidate.get("executionStatus") == "planning-only":
        return {"status": "unavailable", "reason": "Planning reference only; no observed live entry or hypothetical entry P&L is asserted."}
    target_at = min(_aware(target_at), _aware(candidate["plannedExitAt"]))
    if not candidate.get("asOf"):
        return {"status": "unavailable", "reason": "Candidate entry/asOf timestamp required; cannot exclude pre-entry observations"}
    entered_at = _aware(candidate["asOf"])
    ordered = sorted((o for o in observations if _aware(o["timestamp"]) >= entered_at),
                     key=lambda o: _aware(o["timestamp"]))
    timestamps = [_aware(o["timestamp"]) for o in ordered]
    if len(set(timestamps)) != len(timestamps):
        return {"status": "ambiguous", "reason": "Duplicate observation timestamps; ordering unknown"}
    for observation in ordered:
        at = _aware(observation["timestamp"])
        if at < entered_at:
            continue
        stop = candidate["technicalStop"]
        spot = observation.get("spot")
        risk = _number(spot) and ((stop.get("lower") is not None and spot <= stop["lower"]) or
                                  (stop.get("upper") is not None and spot >= stop["upper"]))
        executable = True
        liquidation, times = 0.0, []
        for leg in candidate["legs"]:
            quote = observation.get("quotes", {}).get(leg["exchange"] + ":" + leg["tradingSymbol"])
            try:
                if quote is None:
                    raise ValueError("missing quote")
                bid, ask = quote["bid"], quote["ask"]
                observed_at = _aware(quote["observedAt"])
                age = (at - observed_at).total_seconds()
                if (not all(_number(v) and v > 0 for v in (bid, ask)) or bid > ask or
                        age > MAX_QUOTE_AGE_SECONDS or age < -MAX_FUTURE_QUOTE_SECONDS or
                        (ask - bid) / ((ask + bid) / 2) > MAX_RELATIVE_SPREAD):
                    raise ValueError("invalid executable quote")
                times.append(observed_at)
                liquidation += ((1 if leg["direction"] == "buy" else -1) * leg["quantity"] *
                                ((bid if leg["direction"] == "buy" else ask) - leg["entryPrice"]))
            except (ValueError, KeyError, TypeError):
                executable = False
        if times and (max(times) - min(times)).total_seconds() > MAX_QUOTE_SKEW_SECONDS:
            executable = False
        costs = candidate.get("costEstimate")
        fees_available = _number(costs) and costs >= 0
        gross_pnl = liquidation if executable else None
        pnl = liquidation - costs if executable and fees_available else None
        target = candidate.get("targetProfit") if fees_available else candidate.get("grossTargetProfit")
        target_pnl = pnl if fees_available else gross_pnl
        if not fees_available and not _number(target):
            return {"status": "unavailable", "at": _iso(at),
                    "reason": "Unknown charges and no explicit gross target; first target ordering cannot be established"}
        premium_target = executable and _number(target) and target_pnl >= target
        due = at >= target_at
        if not _number(spot):
            return {"status": "unavailable", "at": _iso(at),
                    "reason": "Missing index observation: first risk/target ordering cannot be established"}
        if not executable and not risk and not due:
            return {"status": "unavailable", "at": _iso(at),
                    "reason": "Missing/invalid leg quotes: an earlier premium target cannot be excluded"}
        if risk or premium_target or due:
            if risk and premium_target:
                return {"status": "ambiguous", "at": _iso(at),
                        "reason": "Risk and premium target first observed together; no intratick ordering"}
            if due and (risk or premium_target) and at > target_at:
                return {"status": "ambiguous", "at": _iso(at),
                        "reason": "Price trigger first observed after scheduled exit; prior ordering unknown"}
            reason = "risk" if risk else "time" if due else "target"
            if not executable:
                return {"status": "unavailable", "at": _iso(at), "trigger": reason,
                        "reason": "Trigger observed but synchronous executable leg quotes missing/invalid"}
            return {"status": "completed", "at": _iso(at), "trigger": reason, "pnl": pnl,
                    "grossPnl": gross_pnl,
                    "payoffBasis": "net-after-configured-charges" if fees_available else "gross-before-charges",
                    "netPnlReason": None if fees_available else "Charges unavailable; net hypothetical P&L unavailable",
                    "hypothetical": True, "scheduledExitAt": _iso(target_at),
                    "exitDelaySeconds": max(0, (at - target_at).total_seconds()) if due else 0,
                    "reason": "First observed trigger only; gaps and intratick paths unobserved"}
    return {"status": "pending", "reason": "No observed trigger/time exit yet; unobserved path is unknown"}
