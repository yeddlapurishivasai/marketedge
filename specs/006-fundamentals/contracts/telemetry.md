# Contract: OpenTelemetry File Logging

This contract defines the **mandatory** logging behavior for every MarketEdge
component (API, Worker, Ingestion). It is the authoritative reference for the
`OBS-*` requirements in `../spec.md`.

## 1. Pipeline

```
app code ──▶ native logging ──▶ OpenTelemetry LoggerProvider ──▶ file log exporter ──▶ rotating JSON-line file
            (logging / ILogger)   (+ Resource attributes)         (NDJSON serializer)   (per-service, 7-day retention)
```

- **Python** (`Worker`, `Ingestion`): configure an OpenTelemetry `LoggerProvider`,
  attach it via `opentelemetry.sdk._logs.LoggingHandler` to the stdlib root logger so
  every `logging.getLogger(...)` call flows through OTel. Modules keep using
  `logging.getLogger(__name__)` — no call-site changes beyond the central setup.
- **.NET** (`Api`): add the OpenTelemetry logging provider to `ILoggingBuilder`
  (`builder.Logging.AddOpenTelemetry(...)`) with the same `Resource`. Controllers /
  services keep using `ILogger<T>`.

## 2. Resource attributes (every record)

| Attribute                 | Value                                              |
| ------------------------- | -------------------------------------------------- |
| `service.name`            | `marketedge-<component>` (e.g. `marketedge-fundamentals`, `marketedge-worker`, `marketedge-api`) |
| `service.namespace`       | `marketedge`                                        |
| `deployment.environment`  | `server` (Linux) / `local` (Windows); override `MARKETEDGE_ENVIRONMENT` |
| `host.name`               | machine host name                                  |

Per-record attributes additionally include `market` (`india`/`us`) for market-scoped
operations and `trace_id` / `span_id` when emitted inside an active span.

## 3. Log directory resolution (OBS-003)

```
if env MARKETEDGE_LOG_DIR set        -> use it
elif OS == Linux                     -> /var/log/marketedge
elif OS == Windows                   -> C:\ProgramData\MarketEdge\logs   (= %ProgramData%\MarketEdge\logs)
else                                 -> <system temp>/marketedge-logs
```

The resolved directory is created on startup (`mkdir -p` semantics). If it cannot be
created or is not writable, fall back to `<system temp>/marketedge-logs`, log one
startup `WARNING`, and continue (OBS-008). A logging failure never crashes the app.

## 4. File naming & rotation (OBS-004, OBS-005)

- File name: `marketedge-<component>.log` (matches `service.name`).
- Rotation: **daily**, at local midnight.
- Retention: **7 days** — keep 7 rotated backups; older files deleted automatically.
  Configurable via `MARKETEDGE_LOG_RETENTION_DAYS` (default `7`).
- Rotated files use a dated suffix, e.g. `marketedge-fundamentals.log.2026-06-24`.

Reference implementations:
- Python: `logging.handlers.TimedRotatingFileHandler(path, when="midnight",
  backupCount=MARKETEDGE_LOG_RETENTION_DAYS, utc=False, encoding="utf-8")` wired as the
  emit target of the OTel file exporter / handler.
- .NET: a rolling file sink configured for daily rolling and a 7-file retention limit,
  fed by the OpenTelemetry log exporter.

## 5. Record format (OBS-002, OBS-006)

One JSON object per line (NDJSON). Minimum fields:

```json
{
  "timestamp": "2026-06-25T05:14:09.123Z",
  "severity": "INFO",
  "body": "Fundamentals run complete",
  "service.name": "marketedge-fundamentals",
  "service.namespace": "marketedge",
  "deployment.environment": "local",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "attributes": { "market": "us", "succeeded": 47, "skipped": 2, "failed": 1 }
}
```

- `timestamp` is UTC ISO-8601 (OBS-006).
- `trace_id` / `span_id` present only when inside an active span.
- No secrets: connection strings/credentials/tokens are redacted to host/database
  only (OBS-009).

## 6. Environment variables (OBS-007)

| Variable                        | Default                        | Purpose                              |
| ------------------------------- | ------------------------------ | ------------------------------------ |
| `MARKETEDGE_LOG_DIR`            | OS default (§3)                | Override the log directory (any OS). |
| `MARKETEDGE_LOG_RETENTION_DAYS` | `7`                            | Days of daily files to retain.       |
| `MARKETEDGE_ENVIRONMENT`        | `server` (Linux)/`local` (Win) | `deployment.environment` value.      |
| `OTEL_SERVICE_NAME`             | `marketedge-<component>`       | `service.name`.                      |
| `OTEL_LOG_LEVEL`                | `INFO`                         | Minimum severity emitted.            |
| `OTEL_EXPORTER_OTLP_ENDPOINT`   | _(unset)_                      | Optional extra OTLP export (file sink stays mandatory). |

## 7. Centralized setup

- Python: a single `observability.py` (shared pattern) exposing
  `configure_logging(service_name, market=None)`; called once at process/CLI startup
  (e.g. in `cli.py` / `app.py`) before any logging.
- .NET: a single extension (e.g. `AddMarketEdgeLogging`) called in `Program.cs`.

All components MUST go through these helpers; no component configures handlers ad hoc
or logs via `print` / `Console.WriteLine` (OBS-001, SC-006).
