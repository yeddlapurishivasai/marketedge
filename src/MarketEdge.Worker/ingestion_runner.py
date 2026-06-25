"""Data-ingestion job runner (queue-driven).

The .NET API enqueues an ``ingestion`` job; this module runs the bundled ingestion CLI
(``src/MarketEdge.Ingestion/cli.py``) on the worker — the Python host that already has
yfinance + pyodbc installed. Running ingestion here (instead of spawning Python from the
.NET API) is what makes ingestion work on cloud, where the API App Service has no Python
runtime or ingestion code.

Each step (``bars`` -> ``technical`` -> ``fundamentals``) is run as a subprocess with the
worker's interpreter; the ingestion CLI reads ``SQL_CONNECTION_STRING`` from the inherited
environment, so it talks to the same database as the worker. Progress and terminal state
are written to the run's ``JobRun`` row.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

from db import get_connection, update_job_status

logger = logging.getLogger(__name__)

# Steps in execution order; the bars step seeds the ticker universe internally.
_PIPELINE = ("bars", "technical", "fundamentals")

# Keep the tail of subprocess output for diagnostics (mirrors the old API behaviour).
_OUTPUT_TAIL = 4000


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_cli() -> str:
    """Locate the ingestion ``cli.py``.

    Deployment bundles the ingestion project alongside the worker as ``ingestion/``; the
    repo layout keeps it as a sibling ``../MarketEdge.Ingestion``. ``INGESTION_DIR`` wins
    if set.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    env_dir = os.getenv("INGESTION_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append(os.path.join(here, "ingestion"))
    candidates.append(os.path.join(here, "..", "MarketEdge.Ingestion"))
    for d in candidates:
        cli = os.path.join(d, "cli.py")
        if os.path.isfile(cli):
            return cli
    raise FileNotFoundError(
        "Ingestion cli.py not found. Looked in: " + ", ".join(candidates)
    )


def _ordered_steps(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(_PIPELINE)
    wanted = {str(s).lower() for s in requested}
    return [s for s in _PIPELINE if s in wanted]


def _build_args(cli: str, step: str, market: str, payload: dict) -> list[str]:
    args = [sys.executable, cli, "ingest", step, "--market", market]
    if payload.get("testSample"):
        args.append("--test-sample")
    limit = payload.get("limit")
    if limit is not None:
        args += ["--limit", str(int(limit))]
    if payload.get("missingOnly"):
        args.append("--missing")
    symbols = payload.get("symbols")
    if symbols:
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join(str(s) for s in symbols)
        args += ["--symbols", str(symbols)]
    return args


def run_steps_inline(market: str, symbols: list[str] | None, steps: list[str]) -> tuple[bool, str]:
    """Run ingestion steps as subprocesses WITHOUT creating/updating a JobRun.

    Used by other jobs that need to reuse ingestion logic mid-run — e.g. the pre-close scan
    refreshing the ``TickerTechnical`` snapshot (prices/52W + market cap) for the scanned
    universe so scoring and lookups see today's data. Returns ``(failed, output_tail)``.
    """
    cli = _resolve_cli()
    cli_dir = os.path.dirname(cli)
    market = (market or "").lower()
    payload = {"symbols": symbols} if symbols else {}
    output: list[str] = []
    failed = False
    for step in _ordered_steps(steps):
        args = _build_args(cli, step, market, payload)
        try:
            proc = subprocess.run(
                args, cwd=cli_dir, env=os.environ.copy(),
                capture_output=True, text=True,
            )
            if proc.stdout:
                output.append(proc.stdout)
            if proc.stderr:
                output.append(proc.stderr)
            exit_code = proc.returncode
        except Exception as exc:  # noqa: BLE001 - record launch failure, stop
            output.append(f"[launch error] {exc}")
            exit_code = -1
        if exit_code != 0:
            failed = True
            output.append(f"[step '{step}' exited {exit_code}]")
            break
    full = "\n".join(output)
    return failed, (full[-_OUTPUT_TAIL:] if len(full) > _OUTPUT_TAIL else full)


def run_ingestion_job(payload: dict) -> None:
    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    steps = _ordered_steps(payload.get("steps"))
    missing = bool(payload.get("missingOnly"))

    cli = _resolve_cli()
    cli_dir = os.path.dirname(cli)
    output: list[str] = []

    conn = get_connection()
    try:
        update_job_status(conn, run_id, "running", progress=0, started_at=_now())
        logger.info(
            "Ingestion run %s: market=%s steps=%s missing=%s cli=%s",
            run_id, market, steps, missing, cli,
        )

        failed = _run_steps(conn, run_id, market, steps, payload, cli, cli_dir, output)

        full = "\n".join(output)
        tail = full[-_OUTPUT_TAIL:] if len(full) > _OUTPUT_TAIL else full
        if failed:
            update_job_status(conn, run_id, "failed", error=tail, completed_at=_now())
            logger.error("Ingestion run %s failed", run_id)
        else:
            metrics = {"market": market, "steps": steps, "output": tail}
            update_job_status(conn, run_id, "completed", progress=100,
                              metrics=metrics, completed_at=_now())
            logger.info("Ingestion run %s completed (%s steps)", run_id, len(steps))
    except Exception as exc:
        logger.exception("Ingestion run %s crashed", run_id)
        try:
            update_job_status(conn, run_id, "failed", error=str(exc), completed_at=_now())
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _run_steps(conn, run_id: int, market: str, steps: list[str], payload: dict,
               cli: str, cli_dir: str, output: list[str],
               progress_ceiling: int = 100) -> bool:
    """Run the requested ingestion steps as subprocesses in pipeline order.

    Appends each step's output to ``output`` and advances the run's progress up to
    ``progress_ceiling``. Returns ``True`` if any step failed (and the pipeline stopped).
    """
    missing = bool(payload.get("missingOnly"))
    failed = False
    for i, step in enumerate(steps):
        header = f"=== {step} ({market}){' [missing-only]' if missing else ''} ==="
        output.append(header)
        args = _build_args(cli, step, market, payload)
        try:
            proc = subprocess.run(
                args,
                cwd=cli_dir,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
            )
            if proc.stdout:
                output.append(proc.stdout)
            if proc.stderr:
                output.append(proc.stderr)
            exit_code = proc.returncode
        except Exception as exc:  # noqa: BLE001 - record launch failure, stop pipeline
            output.append(f"[launch error] {exc}")
            exit_code = -1

        # Progress advances one slot per completed step, scaled to the ceiling.
        update_job_status(conn, run_id, "running",
                          progress=int((i + 1) * progress_ceiling / len(steps)))

        if exit_code != 0:
            failed = True
            output.append(f"[step '{step}' exited {exit_code}]")
            break
    return failed


def run_stock_refresh_job(payload: dict) -> None:
    """Re-ingest all pipeline steps for a single symbol, then recompute its score.

    Backs the Stock Lookup "Refresh & Rescore" button: runs ``ingest bars`` ->
    ``ingest technical`` -> ``ingest fundamentals`` scoped to one symbol, then scores that
    symbol into ``{Market}StockScores`` so the lookup reflects fresh data and a fresh score.
    """
    market = str(payload["market"]).lower()
    run_id = int(payload["runId"])
    syms = payload.get("symbols")
    if syms and isinstance(syms, (list, tuple)):
        symbol = str(syms[0]).upper()
    else:
        symbol = str(payload.get("symbol", "")).upper()
    if not symbol:
        raise ValueError("stock_refresh requires a symbol")

    steps = _ordered_steps(payload.get("steps"))
    # Ensure the single symbol is passed to every ingestion step.
    payload = {**payload, "symbols": [symbol]}

    cli = _resolve_cli()
    cli_dir = os.path.dirname(cli)
    output: list[str] = []

    conn = get_connection()
    try:
        update_job_status(conn, run_id, "running", progress=0, started_at=_now())
        logger.info("Stock refresh run %s: market=%s symbol=%s steps=%s",
                    run_id, market, symbol, steps)

        # Phase 1: ingest the symbol's data (reserve the last 20% for scoring).
        failed = _run_steps(conn, run_id, market, steps, payload, cli, cli_dir, output,
                            progress_ceiling=80)

        full = "\n".join(output)
        tail = full[-_OUTPUT_TAIL:] if len(full) > _OUTPUT_TAIL else full
        if failed:
            update_job_status(conn, run_id, "failed", error=tail, completed_at=_now())
            logger.error("Stock refresh run %s failed during ingestion", run_id)
            return

        # Phase 2: recompute the symbol's score from the freshly ingested data.
        scored = 0
        try:
            from datetime import date
            from scanners.scoring import score_universe
            from scanners.indicators import load_bars
            scan_date = date.today()
            series = load_bars(conn, market, symbol, 520, end_date=scan_date)
            series_cache = {symbol: series} if series is not None else {}
            scored = score_universe(conn, market, [symbol], scan_date, series_cache)
            logger.info("Stock refresh run %s: scored %s (symbol=%s)", run_id, scored, symbol)
        except Exception:  # noqa: BLE001 - scoring failure shouldn't drop the ingested data
            logger.exception("Stock refresh run %s: scoring failed", run_id)
            output.append("[scoring failed — see worker log]")
            tail = ("\n".join(output))[-_OUTPUT_TAIL:]

        metrics = {"market": market, "symbol": symbol, "steps": steps,
                   "scored": scored, "output": tail}
        update_job_status(conn, run_id, "completed", progress=100,
                          metrics=metrics, completed_at=_now())
        logger.info("Stock refresh run %s completed (symbol=%s)", run_id, symbol)
    except Exception as exc:
        logger.exception("Stock refresh run %s crashed", run_id)
        try:
            update_job_status(conn, run_id, "failed", error=str(exc), completed_at=_now())
        except Exception:
            pass
        raise
    finally:
        conn.close()
