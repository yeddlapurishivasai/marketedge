"""Exchange trading-hours helper for the worker (mirrors the API's ``MarketHours``).

India (NSE) 09:15–15:30 IST, US 09:30–16:00 ET, weekdays only. Holiday calendars are out of
scope (weekday + hours window only), matching the .NET side. Uses IANA zone ids via
``zoneinfo`` (the ``tzdata`` package guarantees availability on Windows / minimal Linux).
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# market -> (IANA tz id, open, close)
_WINDOWS: dict[str, tuple[str, time, time]] = {
    "india": ("Asia/Kolkata", time(9, 15), time(15, 30)),
    "us": ("America/New_York", time(9, 30), time(16, 0)),
}


def _zone(market: str) -> ZoneInfo | None:
    w = _WINDOWS.get(market.lower())
    if w is None:
        return None
    try:
        return ZoneInfo(w[0])
    except Exception:  # noqa: BLE001 - unknown/unavailable tz => treat as closed
        return None


def market_local_now(market: str, now_utc: datetime | None = None) -> datetime | None:
    """Current exchange-local time for the market, or ``None`` if the tz is unknown."""
    tz = _zone(market)
    if tz is None:
        return None
    base = now_utc or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(tz)


def is_market_open(market: str, now_utc: datetime | None = None) -> bool:
    """Whether the exchange is within its weekday trading window right now."""
    w = _WINDOWS.get(market.lower())
    if w is None:
        return False
    local = market_local_now(market, now_utc)
    if local is None:
        return False
    if local.weekday() >= 5:  # Saturday/Sunday
        return False
    return w[1] <= local.time() <= w[2]
