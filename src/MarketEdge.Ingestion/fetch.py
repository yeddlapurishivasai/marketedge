"""yfinance fetching for the ingestion pipeline.

This is the ONLY place in MarketEdge that performs live yfinance calls. It fetches in
batches (one HTTP request per batch of tickers, parallelised by yfinance threads),
retries with exponential backoff, and throttles between batches to respect rate
limits. A single ticker failing never aborts the run.
"""
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from config import Config

logger = logging.getLogger(__name__)

BENCHMARKS = {"india": "^NSEI", "us": "^GSPC"}


def to_yfinance_symbol(symbol: str, market: str) -> str:
    """Map a catalog symbol to the yfinance ticker (``.NS`` suffix for India)."""
    if symbol.startswith("^"):
        return symbol  # benchmark / index symbols are passed through unchanged
    return f"{symbol}.NS" if market.lower() == "india" else symbol


def benchmark_symbol(market: str) -> str:
    key = market.lower()
    if key not in BENCHMARKS:
        raise ValueError(f"Unsupported market: {market}")
    return BENCHMARKS[key]


def _normalize_frame(raw: pd.DataFrame, yf_symbol: str) -> pd.DataFrame:
    """Extract a single ticker's OHLCV frame from a (possibly MultiIndex) download."""
    if raw is None or raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        level0 = raw.columns.get_level_values(0)
        if yf_symbol in level0:
            frame = raw[yf_symbol].copy()
        else:
            # Single-ticker downloads sometimes nest the ticker on level 1.
            try:
                frame = raw.xs(yf_symbol, axis=1, level=-1).copy()
            except (KeyError, ValueError):
                return pd.DataFrame()
    else:
        frame = raw.copy()

    frame = frame.dropna(how="all")
    return frame


def fetch_daily_bars(symbols: list[str], market: str) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for ``symbols`` (catalog symbols) in throttled batches.

    Returns a dict mapping the ORIGINAL catalog symbol to a DataFrame indexed by date
    with columns Open/High/Low/Close/Volume/Adj Close. Symbols with no data are omitted.
    """
    results: dict[str, pd.DataFrame] = {}
    if not symbols:
        return results

    batch_size = max(Config.YFINANCE_BATCH_SIZE, 1)
    total_batches = math.ceil(len(symbols) / batch_size)

    for batch_index in range(total_batches):
        batch = symbols[batch_index * batch_size : (batch_index + 1) * batch_size]
        yf_symbols = [to_yfinance_symbol(s, market) for s in batch]

        for attempt in range(Config.YFINANCE_MAX_RETRIES):
            try:
                logger.info(
                    "Fetching daily bars: batch %s/%s (%s symbols)",
                    batch_index + 1, total_batches, len(batch),
                )
                raw = yf.download(
                    tickers=yf_symbols,
                    period=Config.DAILY_LOOKBACK_PERIOD,
                    interval=Config.DAILY_INTERVAL,
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=Config.YFINANCE_THREADS,
                )
                for original, yf_symbol in zip(batch, yf_symbols):
                    frame = _normalize_frame(raw, yf_symbol)
                    if frame.empty or "Close" not in frame.columns:
                        logger.warning("No daily data returned for %s", original)
                        continue
                    results[original] = frame
                break
            except Exception as exc:  # noqa: BLE001 - skip-and-continue by design
                logger.warning(
                    "Daily batch %s/%s attempt %s/%s failed: %s",
                    batch_index + 1, total_batches, attempt + 1,
                    Config.YFINANCE_MAX_RETRIES, exc,
                )
                if attempt == Config.YFINANCE_MAX_RETRIES - 1:
                    logger.error("Skipping batch %s after exhausting retries", batch_index + 1)
                else:
                    time.sleep(Config.YFINANCE_BATCH_DELAY * (2 ** attempt))

        if batch_index < total_batches - 1:
            time.sleep(Config.YFINANCE_BATCH_DELAY)

    return results


def fetch_benchmark_bars(market: str) -> pd.DataFrame:
    """Fetch the benchmark's daily OHLCV; raises if unavailable."""
    symbol = benchmark_symbol(market)
    frames = fetch_daily_bars([symbol], market)
    frame = frames.get(symbol, pd.DataFrame())
    if frame.empty or "Close" not in frame.columns:
        raise ValueError(f"Unable to fetch benchmark data for market {market} ({symbol})")
    return frame


def fetch_market_caps(symbols: list[str], market: str) -> dict[str, int | None]:
    """Fetch market caps via ``fast_info`` in parallel chunks (best-effort)."""
    results: dict[str, int | None] = {}
    if not symbols:
        return results

    workers = max(Config.YFINANCE_THREADS, 1)
    total_chunks = math.ceil(len(symbols) / workers)

    def load(symbol: str) -> tuple[str, int | None]:
        yf_symbol = to_yfinance_symbol(symbol, market)
        try:
            fast_info = yf.Ticker(yf_symbol).fast_info
            mc = fast_info.get("market_cap") or fast_info.get("marketCap")
            return symbol, int(mc) if mc is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed market cap for %s: %s", symbol, exc)
            return symbol, None

    for index in range(0, len(symbols), workers):
        chunk = symbols[index : index + workers]
        chunk_no = index // workers + 1
        if chunk_no == 1 or chunk_no % 25 == 0:
            logger.info("Market cap progress: chunk %s/%s", chunk_no, total_chunks)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(load, s): s for s in chunk}
            for future in as_completed(futures):
                symbol, mc = future.result()
                results[symbol] = mc
        if index + workers < len(symbols):
            time.sleep(Config.YFINANCE_BATCH_DELAY)

    return results
