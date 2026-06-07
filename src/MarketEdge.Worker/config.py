import os

from dotenv import load_dotenv

load_dotenv()


DEFAULT_AZURE_STORAGE_CONNECTION_STRING = "UseDevelopmentStorage=true"
DEFAULT_QUEUE_NAME = "stage-analysis-jobs"
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
    AZURE_STORAGE_CONNECTION_STRING = os.getenv(
        "AZURE_STORAGE_CONNECTION_STRING",
        DEFAULT_AZURE_STORAGE_CONNECTION_STRING,
    )
    QUEUE_NAME = os.getenv("QUEUE_NAME", DEFAULT_QUEUE_NAME)
    SQL_CONNECTION_STRING = os.getenv(
        "SQL_CONNECTION_STRING",
        DEFAULT_SQL_CONNECTION_STRING,
    )
    QUEUE_POLL_INTERVAL = _get_int("QUEUE_POLL_INTERVAL", 10)
    YFINANCE_BATCH_SIZE = _get_int("YFINANCE_BATCH_SIZE", 50)
    YFINANCE_BATCH_DELAY = _get_float("YFINANCE_BATCH_DELAY", 4.0)
    YFINANCE_MAX_RETRIES = _get_int("YFINANCE_MAX_RETRIES", 3)
