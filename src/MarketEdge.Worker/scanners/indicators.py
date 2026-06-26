"""Indicator engine for scanners.

Loads daily bars from ``{Market}Bars1D`` and exposes a :class:`BarSeries` with cached
indicator arrays (SMA/EMA/ATR/RSI/turnover/volume) plus an :class:`IndicatorSnapshot` of
last-bar scalars and the windowed boolean flags the reference scanner spec relies on.

Indicator close uses ``AdjClose`` when present, otherwise ``Close`` (matching the reference
series construction); open/high/low fall back to the indicator close when null; volume null
becomes 0. Bars whose close is NaN/<=0 are dropped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

_BARS_TABLES = {"india": "IndianBars1D", "us": "USBars1D"}


def bars_table(market: str) -> str:
    table = _BARS_TABLES.get(market.lower())
    if table is None:
        raise ValueError(f"Unsupported market: {market}")
    return table


def load_bars(conn, market: str, symbol: str, max_bars: int, end_date: date | None = None) -> "BarSeries | None":
    """Load up to ``max_bars`` daily bars (ascending) for ``symbol`` as of ``end_date``."""
    table = bars_table(market)
    where = ["Ticker = ?"]
    params: list[Any] = [symbol]
    if end_date is not None:
        where.append("BarDate <= ?")
        params.append(end_date)
    sql = (
        f"SELECT TOP {int(max_bars)} BarDate, [Open], [High], [Low], [Close], [Volume], [AdjClose] "
        f"FROM dbo.{table} WHERE {' AND '.join(where)} ORDER BY BarDate DESC"
    )
    rows = conn.cursor().execute(sql, params).fetchall()
    if not rows:
        return None
    rows = rows[::-1]  # ascending chronological

    dates: list[date] = []
    o: list[float] = []
    h: list[float] = []
    lo: list[float] = []
    c: list[float] = []
    v: list[float] = []
    for r in rows:
        close = r.AdjClose if r.AdjClose is not None else r.Close
        if close is None:
            continue
        close = float(close)
        if math.isnan(close) or close <= 0:
            continue
        dates.append(r.BarDate)
        c.append(close)
        o.append(float(r.Open) if r.Open is not None else close)
        h.append(float(r.High) if r.High is not None else close)
        lo.append(float(r.Low) if r.Low is not None else close)
        v.append(float(r.Volume) if r.Volume is not None else 0.0)
    if not c:
        return None
    return BarSeries(
        dates=dates,
        open=np.asarray(o, dtype=float),
        high=np.asarray(h, dtype=float),
        low=np.asarray(lo, dtype=float),
        close=np.asarray(c, dtype=float),
        volume=np.asarray(v, dtype=float),
    )


def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(arr).rolling(n).mean().to_numpy()


def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(arr).ewm(span=n, adjust=False).mean().to_numpy()


def _wilder(arr: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(arr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


class BarSeries:
    """A validated ascending OHLCV series with cached indicator arrays."""

    def __init__(self, dates, open, high, low, close, volume):  # noqa: A002 - mirror OHLC names
        self.dates = dates
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.n = len(close)
        self.last = self.n - 1
        self._turnover = close * volume
        self._tr = self._true_range()
        self._cache: dict[str, np.ndarray] = {}

    def _true_range(self) -> np.ndarray:
        high, low, close = self.high, self.low, self.close
        prev_close = np.concatenate(([close[0]], close[:-1]))
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        return tr

    def _c(self, key: str, fn) -> np.ndarray:
        arr = self._cache.get(key)
        if arr is None:
            arr = fn()
            self._cache[key] = arr
        return arr

    # --- indicator arrays (cached) ---
    def sma(self, n: int) -> np.ndarray:
        return self._c(f"sma{n}", lambda: _sma(self.close, n))

    def ema(self, n: int) -> np.ndarray:
        return self._c(f"ema{n}", lambda: _ema(self.close, n))

    def atr(self, n: int) -> np.ndarray:
        return self._c(f"atr{n}", lambda: _wilder(self._tr, n))

    def rsi(self, n: int) -> np.ndarray:
        return self._c(f"rsi{n}", lambda: _rsi(self.close, n))

    def avg_vol(self, n: int) -> np.ndarray:
        return self._c(f"avgvol{n}", lambda: _sma(self.volume, n))

    def avg_turnover(self, n: int) -> np.ndarray:
        return self._c(f"turn{n}", lambda: _sma(self._turnover, n))

    # --- scalar helpers at the last bar ---
    @staticmethod
    def _v(arr: np.ndarray, i: int) -> float:
        if i < 0 or i >= len(arr):
            return float("nan")
        return float(arr[i])

    def lastv(self, arr: np.ndarray) -> float:
        return self._v(arr, self.last)


def _rsi(close: np.ndarray, n: int) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    avg_loss = pd.Series(loss).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.inf), where=avg_loss != 0)
    return 100.0 - (100.0 / (1.0 + rs))


def _finite(x: float) -> bool:
    return x is not None and not math.isnan(x) and not math.isinf(x)


@dataclass
class IndicatorSnapshot:
    """Last-bar scalar view over a :class:`BarSeries`, with the reference flag set."""

    s: BarSeries

    # cached scalars
    def _at(self, arr: np.ndarray, offset: int = 0) -> float:
        return BarSeries._v(arr, self.s.last - offset)

    @property
    def bar_count(self) -> int:
        return self.s.n

    @property
    def close(self) -> float:
        return float(self.s.close[self.s.last])

    @property
    def open(self) -> float:
        return float(self.s.open[self.s.last])

    @property
    def high(self) -> float:
        return float(self.s.high[self.s.last])

    @property
    def low(self) -> float:
        return float(self.s.low[self.s.last])

    @property
    def volume(self) -> float:
        return float(self.s.volume[self.s.last])

    @property
    def prev_close(self) -> float:
        return self._at(self.s.close, 1)

    def sma(self, n: int) -> float:
        return self._at(self.s.sma(n))

    def ema(self, n: int) -> float:
        return self._at(self.s.ema(n))

    def atr(self, n: int) -> float:
        return self._at(self.s.atr(n))

    @property
    def atr1(self) -> float:
        return float(self.s._tr[self.s.last])

    def rsi(self, n: int) -> float:
        return self._at(self.s.rsi(n))

    def rsi_prev1(self, n: int) -> float:
        return self._at(self.s.rsi(n), 1)

    def avg_vol(self, n: int) -> float:
        return self._at(self.s.avg_vol(n))

    def avg_turnover(self, n: int) -> float:
        return self._at(self.s.avg_turnover(n))

    @property
    def rvol20(self) -> float:
        av = self.avg_vol(20)
        if not _finite(av) or av == 0:
            return 0.0
        return self.volume / av

    def natr(self, n: int) -> float:
        c = self.close
        a = self.atr(n)
        if c == 0 or not _finite(a):
            return 0.0
        return a / c * 100.0

    def pgo(self, n: int) -> float:
        a = self.atr(n)
        if not _finite(a) or a == 0:
            return 0.0
        return (self.close - self.sma(n)) / a

    @property
    def is_valid(self) -> bool:
        c = self.close
        return _finite(c) and c > 0

    # --- candle / structure ---
    @property
    def candle_range(self) -> float:
        return self.high - self.low

    @property
    def candle_close_position(self) -> float:
        rng = self.high - self.low
        if rng == 0:
            return 0.5
        return (self.close - self.low) / rng

    @property
    def is_inside_bar(self) -> bool:
        if self.s.last < 1:
            return False
        return self.high <= self.s.high[self.s.last - 1] and self.low >= self.s.low[self.s.last - 1]

    @property
    def gap_up(self) -> bool:
        if self.s.last < 1:
            return False
        return self.open > self.s.close[self.s.last - 1]

    # --- trend flags ---
    @property
    def sma20_above_sma50(self) -> bool:
        return self.sma(20) > self.sma(50)

    @property
    def sma100_above_sma200(self) -> bool:
        return self.sma(100) > self.sma(200)

    def sma_was_above_all_of(self, window: int) -> bool:
        """SMA20 >= SMA50 for every one of the last ``window`` bars."""
        s, last = self.s, self.s.last
        a, b = s.sma(20), s.sma(50)
        start = max(0, last - window + 1)
        for i in range(start, last + 1):
            if math.isnan(a[i]) or math.isnan(b[i]) or a[i] < b[i]:
                return False
        return True

    def _trending_up(self, arr: np.ndarray, window: int) -> bool:
        last = self.s.last
        start = max(1, last - window + 1)
        for i in range(start, last + 1):
            prev = arr[i - 1]
            if math.isnan(arr[i]) or math.isnan(prev) or prev == 0:
                return False
            change = (arr[i] - prev) / prev
            if change <= 0.001:
                return False
        return True

    @property
    def sma200_trending_up_60(self) -> bool:
        return self._trending_up(self.s.sma(200), 60)

    @property
    def sma100_trending_up_40(self) -> bool:
        return self._trending_up(self.s.sma(100), 40)

    @property
    def ema200_trending_up(self) -> bool:
        if self.s.last < 1:
            return False
        a = self.s.ema(200)
        return _finite(a[self.s.last]) and _finite(a[self.s.last - 1]) and a[self.s.last] > a[self.s.last - 1]

    @property
    def is_highest_volume_250(self) -> bool:
        last = self.s.last
        start = max(0, last - 249)
        window = self.s.volume[start:last + 1]
        if len(window) == 0:
            return False
        return self.volume == float(np.max(window))

    def trend_ratio(self, arr: np.ndarray, window: int) -> float:
        """Fraction of the last ``window`` day-to-day MA slopes that are positive."""
        last = self.s.last
        start = max(1, last - window + 1)
        pos = 0
        total = 0
        for i in range(start, last + 1):
            prev, cur = arr[i - 1], arr[i]
            if math.isnan(prev) or math.isnan(cur):
                continue
            total += 1
            if cur > prev:
                pos += 1
        if total == 0:
            return 0.0
        return pos / total


def check_no_breakdown(s: BarSeries, window: int) -> bool:
    """Reference ``checkNoBreakdown``: reject if a bar closes below a falling SMA50."""
    if s.last < 1:
        return True
    sma50 = s.sma(50)
    start = max(1, s.last - window + 1)
    for i in range(start, s.last + 1):
        if math.isnan(sma50[i]) or math.isnan(sma50[i - 1]):
            continue
        if s.close[i] < sma50[i] and sma50[i] < sma50[i - 1]:
            return False
    return True


def day_change_pct(s: BarSeries) -> float:
    if s.last < 1:
        return 0.0
    prev = s.close[s.last - 1]
    if prev == 0:
        return 0.0
    return round(((s.close[s.last] - prev) / prev) * 100.0, 2)


def round_or_none(value: float | None, digits: int) -> float | None:
    if value is None or not _finite(float(value)):
        return None
    return round(float(value), digits)
