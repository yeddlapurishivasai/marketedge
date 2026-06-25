# MarketEdge.Ingestion

The data-ingestion pipeline for MarketEdge. This is the **only** component that performs
live yfinance calls. It fetches daily bars, technical snapshots, and (best-effort) analyst
/ EPS fundamentals into SQL Server, where the Stage 2 sector-rotation worker reads them as
base data instead of fetching from yfinance itself.

See `specs/005-data-ingestion/` for the full specification and `specs/002-stage2-analysis-worker/`
for how the worker consumes the ingested tables.

## Tables (dacpac)

Defined in `src/MarketEdge.Database/Tables/` (built/published via the SQL project):

| Concern            | India table             | US table            |
| ------------------ | ----------------------- | ------------------- |
| Ticker master      | `IndianTickers`         | `USTickers`         |
| Daily technicals   | `IndianTickerTechnical` | `USTickerTechnical` |
| Analyst snapshot   | `IndianAnalystSnapshot` | `USAnalystSnapshot` |
| EPS forecasts      | `IndianEpsForecasts`    | `USEpsForecasts`    |
| Daily OHLCV bars   | `IndianBars1D`          | `USBars1D`          |

## Setup

```powershell
cd src/MarketEdge.Ingestion
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then edit SQL_CONNECTION_STRING if needed
```

Requires the Microsoft ODBC Driver for SQL Server and the MarketEdge database published
from `src/MarketEdge.Database/` (with the catalog tables `IndianStocks` / `USStocks`
already seeded).

## Usage

```powershell
# Seed the ticker master (plus the market benchmark) from the catalog.
python cli.py seed tickers --market india --limit 200

# Ingest daily bars — the Stage 2 base data (critical path).
python cli.py ingest bars --market india --limit 200

# Compute & store the latest daily technical snapshot + market caps.
python cli.py ingest technical --market india --limit 200

# Best-effort analyst / EPS forecast ingestion.
python cli.py ingest fundamentals --market india --limit 200
```

### Universe selectors (all subcommands)

| Flag            | Meaning                                            |
| --------------- | -------------------------------------------------- |
| `--market`      | `india` or `us` (required).                        |
| `--limit N`     | Cap the number of tickers (ordered by symbol).     |
| `--test-sample` | Restrict to catalog rows flagged `IsTestSample=1`. |
| `--sectors a,b` | Restrict to the given `SectorId` values.           |

## 200-sample test

```powershell
python cli.py ingest bars --market india --limit 200
```

This resolves 200 catalog symbols, seeds them (plus `^NSEI`) into `IndianTickers`,
fetches the last 1 year of daily bars in throttled batches, bulk-upserts into
`IndianBars1D`, prunes anything older than the rolling 1-year window
(`DAILY_LOOKBACK_DAYS`, default 365), and refreshes each ticker's `BarsAvailable` count.
Swap `--market us` (benchmark `^GSPC`) for the US universe.

## Throttling

The throttle settings carry over from the worker (`YFINANCE_BATCH_SIZE`,
`YFINANCE_BATCH_DELAY`, `YFINANCE_MAX_RETRIES`) plus `YFINANCE_THREADS` for in-batch
parallelism. Fetching is **batched** (one HTTP request per batch of tickers, threaded by
yfinance) rather than per-stock, which is what makes a full run finish in minutes.
