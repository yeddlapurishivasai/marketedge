"""Centralized OpenTelemetry file logging for MarketEdge Python components.

This module implements the observability contract in
``specs/006-fundamentals/contracts/telemetry.md``:

* All logging flows through an OpenTelemetry ``LoggerProvider`` (the stdlib
  ``logging`` module is bridged to OTel via ``LoggingHandler``), so every record
  carries MarketEdge resource attributes and, inside a span, trace correlation ids.
* Records are written to **files** as newline-delimited JSON (one OTel ``LogRecord``
  per line) under an OS-specific directory:
      Linux  -> /var/log/marketedge
      Windows-> C:\\ProgramData\\MarketEdge\\logs
  overridable with ``MARKETEDGE_LOG_DIR``.
* Files rotate **daily** and retain **7 days** (``MARKETEDGE_LOG_RETENTION_DAYS``).
* Setup is resilient: an unwritable directory falls back to a temp location and a
  single warning instead of crashing the process.

Usage (once, at process/CLI startup):

    from observability import configure_logging
    configure_logging("marketedge-fundamentals", market="us")
"""
from __future__ import annotations

import json
import logging
import os
import platform
import socket
import tempfile
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_DEFAULT_LINUX_DIR = "/var/log/marketedge"
_DEFAULT_WINDOWS_DIR = r"C:\ProgramData\MarketEdge\logs"
_FALLBACK_DIR = os.path.join(tempfile.gettempdir(), "marketedge-logs")

_configured = False


# --------------------------------------------------------------------------- #
# Configuration resolution
# --------------------------------------------------------------------------- #
def _retention_days() -> int:
    try:
        return max(int(os.getenv("MARKETEDGE_LOG_RETENTION_DAYS", "7")), 1)
    except ValueError:
        return 7


def _environment() -> str:
    explicit = os.getenv("MARKETEDGE_ENVIRONMENT")
    if explicit:
        return explicit
    return "local" if platform.system() == "Windows" else "server"


def resolve_log_dir() -> Path:
    """Resolve the OS-specific log directory (env override wins)."""
    override = os.getenv("MARKETEDGE_LOG_DIR")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        return Path(_DEFAULT_WINDOWS_DIR)
    return Path(_DEFAULT_LINUX_DIR)


def _ensure_dir(preferred: Path) -> tuple[Path, str | None]:
    """Create the preferred dir; fall back to a temp dir if it is not writable."""
    for candidate in (preferred, Path(_FALLBACK_DIR)):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.touch()
            probe.unlink(missing_ok=True)
            warning = None if candidate == preferred else (
                f"Log directory '{preferred}' is not writable; "
                f"falling back to '{candidate}'."
            )
            return candidate, warning
        except OSError:
            continue
    # Last resort: current directory (should essentially never happen).
    return Path.cwd(), f"Could not create '{preferred}' or fallback; logging to CWD."


# --------------------------------------------------------------------------- #
# NDJSON serialization
# --------------------------------------------------------------------------- #
def _format_trace_id(value: int | None) -> str | None:
    return f"{value:032x}" if value else None


def _format_span_id(value: int | None) -> str | None:
    return f"{value:016x}" if value else None


def _otel_record_to_ndjson(log_record, default_attrs: dict, resource=None) -> str:
    """Serialize an OpenTelemetry LogRecord to a single JSON line."""
    from datetime import datetime, timezone

    ts_ns = getattr(log_record, "timestamp", None) or getattr(
        log_record, "observed_timestamp", None
    )
    if ts_ns:
        timestamp = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    resource_attrs = {}
    resource = resource or getattr(log_record, "resource", None)
    if resource is not None:
        resource_attrs = dict(getattr(resource, "attributes", {}) or {})

    record_attrs = dict(getattr(log_record, "attributes", {}) or {})
    merged_attrs = {**default_attrs, **record_attrs}

    payload = {
        "timestamp": timestamp,
        "severity": getattr(log_record, "severity_text", None) or "INFO",
        "body": _to_jsonable(getattr(log_record, "body", "")),
        "service.name": resource_attrs.get("service.name"),
        "service.namespace": resource_attrs.get("service.namespace"),
        "deployment.environment": resource_attrs.get("deployment.environment"),
    }
    trace_id = _format_trace_id(getattr(log_record, "trace_id", 0))
    span_id = _format_span_id(getattr(log_record, "span_id", 0))
    if trace_id:
        payload["trace_id"] = trace_id
    if span_id:
        payload["span_id"] = span_id
    if merged_attrs:
        payload["attributes"] = {k: _to_jsonable(v) for k, v in merged_attrs.items()}

    return json.dumps(payload, default=str, ensure_ascii=False)


def _to_jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def configure_logging(service_name: str, market: str | None = None, level: int | None = None):
    """Configure OpenTelemetry-backed file logging for this process.

    Safe to call more than once (subsequent calls are no-ops). Always succeeds:
    if OpenTelemetry or the log directory is unavailable it degrades gracefully so a
    logging misconfiguration never crashes the component.
    """
    global _configured
    if _configured:
        return logging.getLogger()

    if level is None:
        level = getattr(logging, os.getenv("OTEL_LOG_LEVEL", "INFO").upper(), logging.INFO)

    service_name = os.getenv("OTEL_SERVICE_NAME", service_name)
    log_dir, dir_warning = _ensure_dir(resolve_log_dir())
    log_path = log_dir / f"{service_name}.log"
    backup = _retention_days()

    # Daily-rotating file with 7-day retention; this is the durable NDJSON sink.
    file_handler = TimedRotatingFileHandler(
        str(log_path), when="midnight", backupCount=backup, encoding="utf-8", utc=False
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    # A private logger that only writes finished NDJSON lines to the file, with no
    # propagation back to root (avoids feeding OTel-exported lines into OTel again).
    file_logger = logging.getLogger("marketedge._otel_file")
    file_logger.handlers.clear()
    file_logger.addHandler(file_handler)
    file_logger.setLevel(logging.INFO)
    file_logger.propagate = False

    default_attrs = {"market": market} if market else {}

    root = logging.getLogger()
    root.setLevel(level)

    otel_configured = _try_configure_otel(
        service_name=service_name,
        environment=_environment(),
        level=level,
        file_logger=file_logger,
        default_attrs=default_attrs,
    )

    if not otel_configured:
        # Resilient fallback: log straight to the same NDJSON file via stdlib only.
        _configure_plain_fallback(root, file_logger, service_name, default_attrs, level)

    # Console output (through logging, never print) for local visibility.
    if os.getenv("MARKETEDGE_LOG_CONSOLE", "1") != "0":
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(console)

    _configured = True
    logging.getLogger(__name__).info(
        "Logging configured", extra={"log_dir": str(log_dir), "otel": otel_configured}
    )
    if dir_warning:
        logging.getLogger(__name__).warning(dir_warning)
    return root


def _try_configure_otel(service_name, environment, level, file_logger, default_attrs) -> bool:
    """Wire the OpenTelemetry logging pipeline. Returns False if OTel is unavailable."""
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import (
            LogExporter,
            LogExportResult,
            SimpleLogRecordProcessor,
        )
        from opentelemetry.sdk.resources import Resource
    except Exception:  # noqa: BLE001 - OTel not installed / incompatible
        return False

    class _NdjsonFileExporter(LogExporter):
        def __init__(self, sink_logger, attrs):
            self._sink = sink_logger
            self._attrs = attrs

        def export(self, batch):
            try:
                for item in batch:
                    record = getattr(item, "log_record", item)
                    resource = getattr(item, "resource", None) or getattr(
                        record, "resource", None
                    )
                    self._sink.info(
                        _otel_record_to_ndjson(record, self._attrs, resource)
                    )
                return LogExportResult.SUCCESS
            except Exception:  # noqa: BLE001 - never let logging crash the app
                return LogExportResult.FAILURE

        def shutdown(self):
            for handler in self._sink.handlers:
                try:
                    handler.flush()
                except Exception:  # noqa: BLE001
                    pass

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "marketedge",
            "deployment.environment": environment,
            "host.name": socket.gethostname(),
        }
    )
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        SimpleLogRecordProcessor(_NdjsonFileExporter(file_logger, default_attrs))
    )
    set_logger_provider(provider)

    otel_handler = LoggingHandler(level=level, logger_provider=provider)
    logging.getLogger().addHandler(otel_handler)
    return True


def _configure_plain_fallback(root, file_logger, service_name, default_attrs, level):
    """Stdlib-only NDJSON file logging when OpenTelemetry is unavailable."""
    from datetime import datetime, timezone

    sink = file_logger

    class _NdjsonHandler(logging.Handler):
        def emit(self, record):
            try:
                payload = {
                    "timestamp": datetime.fromtimestamp(
                        record.created, tz=timezone.utc
                    ).isoformat(),
                    "severity": record.levelname,
                    "body": record.getMessage(),
                    "service.name": service_name,
                    "service.namespace": "marketedge",
                    "deployment.environment": _environment(),
                }
                if default_attrs:
                    payload["attributes"] = default_attrs
                sink.info(json.dumps(payload, default=str, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass

    handler = _NdjsonHandler(level=level)
    root.addHandler(handler)
