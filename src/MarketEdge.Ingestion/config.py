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


class Config:
    SQL_CONNECTION_STRING = os.getenv(
        "SQL_CONNECTION_STRING",
        DEFAULT_SQL_CONNECTION_STRING,
    )

    # yfinance fetch / throttle settings (carried over from the worker's config).
    YFINANCE_BATCH_SIZE = _get_int("YFINANCE_BATCH_SIZE", 50)
    YFINANCE_BATCH_DELAY = _get_float("YFINANCE_BATCH_DELAY", 2.0)
    YFINANCE_MAX_RETRIES = _get_int("YFINANCE_MAX_RETRIES", 3)

    # Parallel threads used inside a single yfinance batch download / market-cap chunk.
    YFINANCE_THREADS = _get_int("YFINANCE_THREADS", 10)

    # Daily-bar history window fetched per ticker (yfinance period string).
    DAILY_LOOKBACK_PERIOD = os.getenv("DAILY_LOOKBACK_PERIOD", "2y")
    DAILY_INTERVAL = "1d"
