"""Configuration for the MarketEdge data-ingestion pipeline.

All values are environment-driven (with sensible local defaults) and mirror the
worker's config conventions. Throttling settings carry over from the worker so the
ingestion pipeline owns the only live yfinance access in the system.
"""
import os

from dotenv import load_dotenv

load_dotenv()


DEFAULT_SQL_CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=MarketEdge;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SQL_CONNECTION_STRING = os.getenv(
        "SQL_CONNECTION_STRING",
        DEFAULT_SQL_CONNECTION_STRING,
    )

    # yfinance fetch / throttle settings (carried over from the worker's config).
    YFINANCE_BATCH_SIZE = _get_int("YFINANCE_BATCH_SIZE", 50)
    YFINANCE_BATCH_DELAY = _get_float("YFINANCE_BATCH_DELAY", 2.0)
    YFINANCE_MAX_RETRIES = _get_int("YFINANCE_MAX_RETRIES", 4)

    # Base delay (seconds) for exponential backoff when a per-ticker fundamentals call hits a
    # Yahoo throttle ("Too Many Requests") or "Invalid Crumb" auth error. Attempt N sleeps
    # roughly YFINANCE_RETRY_BASE_DELAY * 2**N (+ jitter) before retrying.
    YFINANCE_RETRY_BASE_DELAY = _get_float("YFINANCE_RETRY_BASE_DELAY", 1.5)

    # Parallel threads used inside a single yfinance batch download / market-cap chunk.
    YFINANCE_THREADS = _get_int("YFINANCE_THREADS", 10)

    # Parallel worker threads for the per-symbol fundamentals loop (analyst / EPS / earnings
    # / signals). Each worker uses its own DB connection. Kept low (4) so the per-IP request
    # rate to Yahoo stays under its throttle; raise only if you stop seeing rate-limit errors,
    # set 1 to force the old sequential behaviour.
    FUNDAMENTALS_THREADS = _get_int("FUNDAMENTALS_THREADS", 4)

    # Sleep (seconds) between per-ticker fundamentals/signals calls. Adds steady-state pacing
    # on top of the backoff retries; raise if Yahoo still rate-limits the fundamentals loop.
    YFINANCE_TICKER_DELAY = _get_float("YFINANCE_TICKER_DELAY", 0.0)

    # Daily-bar history window fetched per ticker (yfinance period string).
    DAILY_LOOKBACK_PERIOD = os.getenv("DAILY_LOOKBACK_PERIOD", "1y")
    DAILY_INTERVAL = "1d"

    # --- Screener.in (India-only reported-fundamentals source) --------------------------
    # Screener supplies REPORTED financials (sales / operating profit / OPM / net profit /
    # reported EPS) only. It publishes no analyst estimates, ratings or targets, so it is
    # wired as a field-level primary for those reported fields with yfinance as the
    # fallback — never as a whole-record replacement. See screener.py.
    SCREENER_ENABLED = _get_bool("SCREENER_ENABLED", True)

    # Process-wide outbound ceiling. Screener has no published limit and starts issuing
    # 429s well under 1 req/s; 10/min is a deliberately conservative steady state. This is
    # enforced by ONE shared token bucket, so it is the aggregate across all
    # FUNDAMENTALS_THREADS workers, not a per-thread rate.
    SCREENER_RATE_PER_MIN = _get_float("SCREENER_RATE_PER_MIN", 10.0)
    SCREENER_BURST = _get_int("SCREENER_BURST", 3)

    # Hard ceiling on Screener calls in a single run, so a full-universe job can never hang
    # for hours behind the rate limit. Once spent, the rest of the run uses yfinance and the
    # remaining symbols are picked up by the next run. 0 disables the cap.
    SCREENER_MAX_CALLS_PER_RUN = _get_int("SCREENER_MAX_CALLS_PER_RUN", 600)

    # Circuit breaker: after this many consecutive 429/403/503s, stop calling Screener for
    # COOLDOWN seconds (doubling per re-trip) and let the rest of the run fall back to
    # yfinance instead of burning the job on retries.
    SCREENER_BREAKER_THRESHOLD = _get_int("SCREENER_BREAKER_THRESHOLD", 5)
    SCREENER_BREAKER_COOLDOWN = _get_float("SCREENER_BREAKER_COOLDOWN", 300.0)

    # Skip Screener for a symbol whose stored reported financials are already newer than
    # its last earnings announcement. Reported numbers only move when results are
    # announced, so after the first backfill a nightly run touches only what just reported.
    SCREENER_CACHE_DAYS = _get_int("SCREENER_CACHE_DAYS", 45)

    SCREENER_TIMEOUT = _get_float("SCREENER_TIMEOUT", 15.0)
    SCREENER_MAX_RETRIES = _get_int("SCREENER_MAX_RETRIES", 3)
    SCREENER_RETRY_BASE_DELAY = _get_float("SCREENER_RETRY_BASE_DELAY", 5.0)
    SCREENER_USER_AGENT = os.getenv(
        "SCREENER_USER_AGENT",
        "MarketEdge/1.0 (personal stock research; contact via repository)",
    )

    # Hard cap on stored history: bars older than this many days are never staged
    # and are pruned on every run, keeping a strict rolling 1-year window.
    DAILY_LOOKBACK_DAYS = _get_int("DAILY_LOOKBACK_DAYS", 365)
