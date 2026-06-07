import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

WEEKLY_LOOKBACK_PERIOD = "2y"
WEEKLY_INTERVAL = "1wk"
MAX_MARKET_CAP_WORKERS = 5


def _to_yfinance_symbol(symbol: str, market: str) -> str:
    return f"{symbol}.NS" if market.lower() == "india" else symbol


def _extract_symbol_frame(raw_data: pd.DataFrame, ticker: str, batch_size: int) -> pd.DataFrame:
    if raw_data.empty:
        return pd.DataFrame()

    if isinstance(raw_data.columns, pd.MultiIndex):
        if ticker not in raw_data.columns.get_level_values(0):
            return pd.DataFrame()
        frame = raw_data[ticker].copy()
    elif batch_size == 1:
        frame = raw_data.copy()
    else:
        return pd.DataFrame()

    return frame.dropna(how="all").tail(60)


def fetch_price_data(
    symbols: list[str],
    market: str,
    batch_size: int = 50,
    batch_delay: float = 2.0,
    max_retries: int = 3,
) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    if not symbols:
        return results

    total_batches = math.ceil(len(symbols) / batch_size)

    for batch_index in range(total_batches):
        batch = symbols[batch_index * batch_size : (batch_index + 1) * batch_size]
        yf_symbols = [_to_yfinance_symbol(symbol, market) for symbol in batch]

        for attempt in range(max_retries):
            try:
                logger.info(
                    "Fetching weekly price data for batch %s/%s (%s symbols)",
                    batch_index + 1,
                    total_batches,
                    len(batch),
                )
                raw_data = yf.download(
                    tickers=yf_symbols,
                    period=WEEKLY_LOOKBACK_PERIOD,
                    interval=WEEKLY_INTERVAL,
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )

                for original_symbol, yf_symbol in zip(batch, yf_symbols):
                    symbol_frame = _extract_symbol_frame(raw_data, yf_symbol, len(batch))
                    if symbol_frame.empty or "Close" not in symbol_frame.columns:
                        logger.warning("No weekly data returned for %s", original_symbol)
                        continue
                    results[original_symbol] = symbol_frame

                break
            except Exception as exc:
                logger.warning(
                    "Failed to fetch batch %s/%s on attempt %s/%s: %s",
                    batch_index + 1,
                    total_batches,
                    attempt + 1,
                    max_retries,
                    exc,
                )
                if attempt == max_retries - 1:
                    logger.exception("Skipping batch %s after exhausting retries", batch_index + 1)
                else:
                    time.sleep(batch_delay * (2**attempt))
        if batch_index < total_batches - 1:
            time.sleep(batch_delay)

    return results


def fetch_benchmark_data(market: str) -> pd.DataFrame:
    benchmark_symbol = "^NSEI" if market.lower() == "india" else "^GSPC"
    logger.info("Fetching benchmark data for %s using %s", market, benchmark_symbol)
    benchmark_data = yf.download(
        tickers=benchmark_symbol,
        period=WEEKLY_LOOKBACK_PERIOD,
        interval=WEEKLY_INTERVAL,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    benchmark_data = benchmark_data.dropna(how="all").tail(60)

    if benchmark_data.empty:
        raise ValueError(f"Unable to fetch benchmark data for market {market}")

    # yfinance may return MultiIndex columns even for single ticker
    if isinstance(benchmark_data.columns, pd.MultiIndex):
        benchmark_data = benchmark_data.droplevel("Ticker", axis=1)

    if "Close" not in benchmark_data.columns:
        raise ValueError(f"Unable to fetch benchmark data for market {market}")

    return benchmark_data


def fetch_market_caps(symbols: list[str], market: str, batch_delay: float = 2.0) -> dict[str, int | None]:
    results: dict[str, int | None] = {}
    if not symbols:
        return results

    def load_market_cap(symbol: str) -> tuple[str, int | None]:
        yf_symbol = _to_yfinance_symbol(symbol, market)
        try:
            fast_info = yf.Ticker(yf_symbol).fast_info
            market_cap = fast_info.get("market_cap")
            if market_cap is None:
                market_cap = fast_info.get("marketCap")
            return symbol, market_cap
        except Exception as exc:
            logger.warning("Failed to fetch market cap for %s: %s", symbol, exc)
            return symbol, None

    for index in range(0, len(symbols), MAX_MARKET_CAP_WORKERS):
        chunk = symbols[index : index + MAX_MARKET_CAP_WORKERS]
        with ThreadPoolExecutor(max_workers=MAX_MARKET_CAP_WORKERS) as executor:
            futures = {executor.submit(load_market_cap, symbol): symbol for symbol in chunk}
            for future in as_completed(futures):
                symbol, market_cap = future.result()
                results[symbol] = market_cap

        if index + MAX_MARKET_CAP_WORKERS < len(symbols):
            time.sleep(batch_delay)

    return results


def calculate_stage2(stock_data: pd.DataFrame, benchmark_data: pd.DataFrame) -> dict | None:
    if stock_data is None or stock_data.empty or len(stock_data) < 30:
        return None

    stock_frame = stock_data.sort_index().copy()
    benchmark_frame = benchmark_data.sort_index().copy()

    # Ensure columns are flat (not MultiIndex)
    if isinstance(stock_frame.columns, pd.MultiIndex):
        stock_frame.columns = stock_frame.columns.droplevel(0)
    if isinstance(benchmark_frame.columns, pd.MultiIndex):
        benchmark_frame.columns = benchmark_frame.columns.droplevel(0)

    close = stock_frame["Close"].squeeze()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = close.astype(float)

    open_price = stock_frame["Open"].squeeze().astype(float) if "Open" in stock_frame.columns else close
    if isinstance(open_price, pd.DataFrame):
        open_price = open_price.iloc[:, 0]

    volume = stock_frame["Volume"].fillna(0).squeeze().astype(float) if "Volume" in stock_frame.columns else pd.Series(0, index=stock_frame.index, dtype=float)
    if isinstance(volume, pd.DataFrame):
        volume = volume.iloc[:, 0]

    ma10_series = close.rolling(10).mean()
    ma30_series = close.rolling(30).mean()
    ma10 = float(ma10_series.iloc[-1]) if pd.notna(ma10_series.iloc[-1]) else None
    ma30 = float(ma30_series.iloc[-1]) if pd.notna(ma30_series.iloc[-1]) else None

    benchmark_close = benchmark_frame["Close"].squeeze()
    if isinstance(benchmark_close, pd.DataFrame):
        benchmark_close = benchmark_close.iloc[:, 0]
    benchmark_close = benchmark_close.astype(float)

    aligned_close, aligned_benchmark = close.align(benchmark_close, join="inner")
    # Deduplicate index to prevent multi-value iloc issues
    if aligned_close.index.duplicated().any():
        mask = ~aligned_close.index.duplicated(keep="last")
        aligned_close = aligned_close[mask]
        aligned_benchmark = aligned_benchmark[mask]

    rs_score = None
    rs_1w = None
    rs_2w = None
    rs_3w = None
    rs_delta_1w = None
    rs_delta_2w = None
    rs_delta_3w = None
    quadrant = None

    if not aligned_close.empty and len(aligned_close) >= 5:
        rs_line = aligned_close / aligned_benchmark
        if len(rs_line) >= 52:
            rs_line_sma52 = rs_line.rolling(52).mean()
            latest_sma52 = float(rs_line_sma52.iloc[-1])
            latest_rs = float(rs_line.iloc[-1])
            if not pd.isna(latest_sma52) and latest_sma52 != 0:
                rs_score = float(((latest_rs / latest_sma52) - 1) * 100)

        # RS values at 1w, 2w, 3w ago (raw RS line values as Mansfield scores)
        rs_val_now = float(rs_line.iloc[-1])
        if len(rs_line) >= 2:
            rs_1w = float(rs_line.iloc[-2])  # RS value 1 week ago
            rs_delta_1w = float(((rs_val_now / rs_1w) - 1) * 100) if rs_1w != 0 else None
        if len(rs_line) >= 3:
            rs_2w = float(rs_line.iloc[-3])  # RS value 2 weeks ago
            rs_delta_2w = float(((rs_val_now / rs_2w) - 1) * 100) if rs_2w != 0 else None
        if len(rs_line) >= 4:
            rs_3w = float(rs_line.iloc[-4])  # RS value 3 weeks ago
            rs_delta_3w = float(((rs_val_now / rs_3w) - 1) * 100) if rs_3w != 0 else None

    # Price momentum: 1w, 2w, 3w ROC (suited for swing & positional)
    roc_1w = float(((close.iloc[-1] / close.iloc[-2]) - 1) * 100) if len(close) >= 2 and close.iloc[-2] != 0 else None
    roc_2w = float(((close.iloc[-1] / close.iloc[-3]) - 1) * 100) if len(close) >= 3 and close.iloc[-3] != 0 else None
    roc_3w = float(((close.iloc[-1] / close.iloc[-4]) - 1) * 100) if len(close) >= 4 and close.iloc[-4] != 0 else None

    momentum_score = None
    if roc_1w is not None and roc_2w is not None and roc_3w is not None:
        momentum_score = float((0.4 * roc_1w) + (0.3 * roc_2w) + (0.3 * roc_3w))

    # Quadrant based on RS Score (X) and RS Delta 2w (Y) for faster signal
    if rs_score is not None and rs_delta_2w is not None:
        if rs_score > 0 and rs_delta_2w > 0:
            quadrant = "leading"
        elif rs_score > 0 and rs_delta_2w <= 0:
            quadrant = "weakening"
        elif rs_score <= 0 and rs_delta_2w <= 0:
            quadrant = "lagging"
        else:
            quadrant = "improving"

    last_ten = stock_frame.tail(10)
    acc_vol = 0.0
    dist_vol = 0.0
    for _, row in last_ten.iterrows():
        week_open = float(row.get("Open", row["Close"]))
        week_close = float(row["Close"])
        week_volume = float(row.get("Volume", 0) or 0)
        if week_close > week_open:
            acc_vol += week_volume
        elif week_close < week_open:
            dist_vol += week_volume

    ad_total = acc_vol + dist_vol
    ad_ratio = float(acc_vol / ad_total) if ad_total > 0 else 0.5
    if ad_ratio > 0.6:
        ad_classification = "accumulating"
    elif ad_ratio < 0.4:
        ad_classification = "distributing"
    else:
        ad_classification = "neutral"

    sma30_rising = False
    if len(ma30_series.dropna()) >= 5:
        sma30_rising = bool(ma30_series.iloc[-1] > ma30_series.iloc[-5])

    is_stage2 = bool(
        ma30 is not None
        and ma10 is not None
        and close.iloc[-1] > ma30
        and sma30_rising
        and close.iloc[-1] > ma10
        and ma10 > ma30
        and rs_score is not None
        and rs_score > 0
    )

    return {
        "close_price": float(close.iloc[-1]),
        "ma10": ma10,
        "ma30": ma30,
        "is_stage2": is_stage2,
        "rs_score": rs_score,
        "rs_1w": rs_1w,
        "rs_2w": rs_2w,
        "rs_3w": rs_3w,
        "rs_delta_1w": rs_delta_1w,
        "rs_delta_2w": rs_delta_2w,
        "rs_delta_3w": rs_delta_3w,
        "momentum_score": momentum_score,
        "roc_1w": roc_1w,
        "roc_2w": roc_2w,
        "roc_3w": roc_3w,
        "quadrant": quadrant,
        "ad_ratio": ad_ratio,
        "ad_classification": ad_classification,
    }


def classify_stocks(
    current_stage2_symbols: set[str],
    previous_stage2_symbols: set[str],
    ever_stage2_symbols: set[str],
) -> dict[str, str]:
    classifications: dict[str, str] = {}

    for symbol in current_stage2_symbols:
        if symbol in previous_stage2_symbols:
            classifications[symbol] = "continuing"
        elif symbol not in ever_stage2_symbols:
            classifications[symbol] = "new"
        else:
            classifications[symbol] = "reentry"

    for symbol in previous_stage2_symbols - current_stage2_symbols:
        classifications[symbol] = "removed"

    return classifications


def compute_rs_ranks(results: list[dict]) -> list[dict]:
    scored_results = [result for result in results if result.get("rs_score") is not None]
    if not scored_results:
        return results

    scores = pd.Series([result["rs_score"] for result in scored_results], dtype="float64")
    if len(scores) == 1:
        ranks = [99]
    else:
        percentile_ranks = ((scores.rank(method="average") - 1) / (len(scores) - 1) * 99).round().astype(int)
        ranks = percentile_ranks.tolist()

    for result, rs_rank in zip(scored_results, ranks):
        result["rs_rank"] = int(rs_rank)

    for result in results:
        result.setdefault("rs_rank", None)

    return results
