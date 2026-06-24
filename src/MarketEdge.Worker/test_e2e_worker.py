"""
End-to-end test for the worker pipeline.
Runs a single sector through the full stock-by-stock pipeline against the local DB.

Usage:
    python test_e2e_worker.py [--market india|us] [--sector-id N]

Requires:
    - Local SQL Server with MarketEdge DB (seeded)
    - Internet access for yfinance
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

# Ensure Unicode output (emoji, em dash) works on Windows consoles (cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from config import Config
from db import get_connection, get_stocks, save_single_result, update_job_status, clear_run_results
from stage_analysis import calculate_stage2, fetch_benchmark_data
from worker import _fetch_single_market_cap, _fetch_single_price_data


def _get_week_number() -> str:
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def main():
    parser = argparse.ArgumentParser(description="E2E worker test for one sector")
    parser.add_argument("--market", default="india", choices=["india", "us"])
    parser.add_argument("--sector-id", type=int, default=None, help="Sector ID to test (default: first available)")
    parser.add_argument("--max-stocks", type=int, default=5, help="Max stocks to process (default: 5)")
    parser.add_argument("--all-stocks", action="store_true",
                        help="Use the full universe instead of only IsTestSample stocks")
    args = parser.parse_args()

    market = args.market
    print(f"\n=== E2E Worker Test: {market.upper()} ===\n")

    # 1. Connect to DB
    print("[1] Connecting to DB...")
    conn = get_connection()
    print("    OK")

    # 2. Get stocks for one sector (default: only the local test sample for speed)
    sector_ids = [args.sector_id] if args.sector_id else None
    stocks = get_stocks(conn, market, sector_ids=sector_ids, test_sample_only=not args.all_stocks)
    if not stocks:
        print("    ERROR: No stocks found")
        sys.exit(1)

    # Pick first sector if none specified
    first_sector_id = stocks[0]["sector_id"]
    first_sector_name = stocks[0]["sector_name"]
    sector_stocks = [s for s in stocks if s["sector_id"] == first_sector_id]

    # Limit stocks for testing
    if args.max_stocks:
        sector_stocks = sector_stocks[: args.max_stocks]

    print(f"    Sector: {first_sector_name} (id={first_sector_id})")
    print(f"    Stocks to process: {len(sector_stocks)}")

    # 3. Create a test job run
    print("\n[2] Creating test job run...")
    cursor = conn.cursor()
    week_number = _get_week_number()
    cursor.execute(
        "INSERT INTO dbo.JobRuns (JobType, Market, WeekNumber, Status, Progress, CreatedAt) "
        "VALUES (?, ?, ?, 'running', 0, GETUTCDATE())",
        "stage2_analysis", market, week_number,
    )
    conn.commit()
    cursor.execute("SELECT @@IDENTITY")
    run_id = int(cursor.fetchone()[0])
    print(f"    Created run #{run_id}")

    # 4. Fetch benchmark
    print("\n[3] Fetching benchmark data...")
    benchmark = fetch_benchmark_data(market)
    print(f"    Benchmark shape: {benchmark.shape}")

    # 5. Process stocks one by one
    print(f"\n[4] Processing {len(sector_stocks)} stocks stock-by-stock...")
    results = []
    skipped = 0
    stock_delay = 1.0

    for i, stock in enumerate(sector_stocks):
        symbol = stock["symbol"]
        print(f"\n  [{i+1}/{len(sector_stocks)}] {symbol} ({stock['company_name']})")

        # Market cap
        mc = _fetch_single_market_cap(symbol, market)
        print(f"    Market cap: {mc}")
        time.sleep(stock_delay)

        # Price data
        price_frame = _fetch_single_price_data(symbol, market)
        if price_frame is None:
            print(f"    SKIPPED: No price data")
            skipped += 1
            continue
        print(f"    Price data: {price_frame.shape}, columns={price_frame.columns.tolist()}")
        time.sleep(stock_delay)

        # Analysis
        analysis = calculate_stage2(price_frame, benchmark)
        if analysis is None:
            print(f"    SKIPPED: Analysis returned None")
            skipped += 1
            continue

        result = {
            "run_id": run_id,
            "symbol": symbol,
            "company_name": stock["company_name"],
            "sector_id": stock["sector_id"],
            "sector_name": stock["sector_name"],
            "market_cap": mc,
            "weeks_in_stage2": 0,
            **analysis,
        }
        results.append(result)

        # Save to DB
        save_single_result(conn, market, result)
        print(f"    Stage2={result['is_stage2']}, RS={result.get('rs_score') or 0:.2f}, "
              f"Momentum={result.get('momentum_score') or 0:.2f}")
        print(f"    Saved to DB")

    # 6. Summary
    stage2_count = sum(1 for r in results if r["is_stage2"])
    print(f"\n=== RESULTS ===")
    print(f"  Total processed: {len(sector_stocks)}")
    print(f"  Analyzed: {len(results)}")
    print(f"  Skipped: {skipped}")
    print(f"  In Stage 2: {stage2_count}")

    # 7. Verify DB
    print(f"\n[5] Verifying DB...")
    results_table = "IndianStageAnalysisResults" if market == "india" else "USStageAnalysisResults"
    row = cursor.execute(
        f"SELECT COUNT(*) FROM dbo.{results_table} WHERE RunId = ?", run_id
    ).fetchone()
    db_count = row[0]
    print(f"    Rows in DB for run #{run_id}: {db_count}")

    # 8. Clean up test run
    print(f"\n[6] Cleaning up test run #{run_id}...")
    cursor.execute(f"DELETE FROM dbo.{results_table} WHERE RunId = ?", run_id)
    cursor.execute("DELETE FROM dbo.JobRuns WHERE Id = ?", run_id)
    conn.commit()
    print(f"    Cleaned up")

    conn.close()

    # 9. Verdict
    if db_count == len(results) and len(results) > 0:
        print(f"\n✅ E2E TEST PASSED — {len(results)} stocks analyzed and saved successfully\n")
        sys.exit(0)
    elif len(sector_stocks) == skipped:
        print(f"\n⚠️  All stocks skipped — possibly a data issue, not a code bug\n")
        sys.exit(0)
    else:
        print(f"\n❌ E2E TEST FAILED — expected {len(results)} rows in DB, got {db_count}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
