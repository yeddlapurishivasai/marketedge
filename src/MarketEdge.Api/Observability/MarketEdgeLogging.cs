using System.Text;
using System.Text.Json;
using OpenTelemetry;
using OpenTelemetry.Logs;
using OpenTelemetry.Resources;

namespace MarketEdge.Api.Observability;

/// <summary>
/// Centralized OpenTelemetry file logging for the API, implementing the contract in
/// <c>specs/006-fundamentals/contracts/telemetry.md</c>: all logs flow through an
/// OpenTelemetry <c>LoggerProvider</c> and are written as newline-delimited JSON to an
/// OS-specific directory (Linux <c>/var/log/marketedge</c>, Windows
/// <c>C:\ProgramData\MarketEdge\logs</c>), rotated daily with 7-day retention.
/// </summary>
public static class MarketEdgeLogging
{
    public const string ServiceName = "marketedge-api";
    private const string ServiceNamespace = "marketedge";

    public static WebApplicationBuilder AddMarketEdgeLogging(this WebApplicationBuilder builder)
    {
        var (logDir, warning) = ResolveLogDirectory();
        var retention = GetRetentionDays();
        var environment = GetEnvironment();

        var resource = ResourceBuilder.CreateDefault().AddAttributes(new[]
        {
            new KeyValuePair<string, object>("service.name", ServiceName),
            new KeyValuePair<string, object>("service.namespace", ServiceNamespace),
            new KeyValuePair<string, object>("deployment.environment", environment),
            new KeyValuePair<string, object>("host.name", Environment.MachineName),
        });

        builder.Logging.AddOpenTelemetry(options =>
        {
            options.IncludeScopes = true;
            options.IncludeFormattedMessage = true;
            options.SetResourceBuilder(resource);
            options.AddProcessor(new SimpleLogRecordExportProcessor(
                new NdjsonFileLogExporter(logDir, $"{ServiceName}.log", retention)));
        });

        if (warning is not null)
        {
            // Surfaced once at startup; logging itself must never crash the app.
            Console.Error.WriteLine($"[MarketEdgeLogging] {warning}");
        }

        return builder;
    }

    internal static (string dir, string? warning) ResolveLogDirectory()
    {
        var preferred = Environment.GetEnvironmentVariable("MARKETEDGE_LOG_DIR");
        if (string.IsNullOrWhiteSpace(preferred))
        {
            preferred = OperatingSystem.IsWindows()
                ? @"C:\ProgramData\MarketEdge\logs"
                : "/var/log/marketedge";
        }

        var fallback = Path.Combine(Path.GetTempPath(), "marketedge-logs");
        foreach (var candidate in new[] { preferred!, fallback })
        {
            try
            {
                Directory.CreateDirectory(candidate);
                var probe = Path.Combine(candidate, ".write-test");
                File.WriteAllText(probe, string.Empty);
                File.Delete(probe);
                var warning = candidate == preferred
                    ? null
                    : $"Log directory '{preferred}' is not writable; falling back to '{candidate}'.";
                return (candidate, warning);
            }
            catch (Exception)
            {
                // try next candidate
            }
        }

        return (Directory.GetCurrentDirectory(), $"Could not create '{preferred}' or fallback; logging to CWD.");
    }

    private static int GetRetentionDays()
    {
        var raw = Environment.GetEnvironmentVariable("MARKETEDGE_LOG_RETENTION_DAYS");
        return int.TryParse(raw, out var days) && days > 0 ? days : 7;
    }

    private static string GetEnvironment()
    {
        var explicitEnv = Environment.GetEnvironmentVariable("MARKETEDGE_ENVIRONMENT");
        if (!string.IsNullOrWhiteSpace(explicitEnv))
        {
            return explicitEnv!;
        }
        return OperatingSystem.IsWindows() ? "local" : "server";
    }
}

/// <summary>
/// OpenTelemetry log exporter that serializes each <see cref="LogRecord"/> to one JSON
/// line in a daily-rotated file, retaining a bounded number of days of history.
/// </summary>
internal sealed class NdjsonFileLogExporter : BaseExporter<LogRecord>
{
    private readonly string _dir;
    private readonly string _baseName;
    private readonly int _retentionDays;
    private readonly object _gate = new();

    private DateOnly _currentDate;
    private StreamWriter? _writer;

    public NdjsonFileLogExporter(string dir, string baseName, int retentionDays)
    {
        _dir = dir;
        _baseName = baseName;
        _retentionDays = retentionDays;
    }

    public override ExportResult Export(in Batch<LogRecord> batch)
    {
        try
        {
            lock (_gate)
            {
                EnsureWriter();
                foreach (var record in batch)
                {
                    _writer!.WriteLine(Serialize(record));
                }
                _writer!.Flush();
            }
            return ExportResult.Success;
        }
        catch (Exception)
        {
            // A logging failure must never crash the app.
            return ExportResult.Failure;
        }
    }

    private void EnsureWriter()
    {
        var today = DateOnly.FromDateTime(DateTime.Now);
        if (_writer is not null && today == _currentDate)
        {
            return;
        }

        var previousDate = _currentDate;
        _writer?.Flush();
        _writer?.Dispose();

        var basePath = Path.Combine(_dir, _baseName);

        // On a new day, archive the prior day's file as <name>.<yyyy-MM-dd> then prune.
        if (File.Exists(basePath) && previousDate != default && today != previousDate)
        {
            var archived = $"{basePath}.{previousDate:yyyy-MM-dd}";
            try
            {
                if (File.Exists(archived)) File.Delete(archived);
                File.Move(basePath, archived);
            }
            catch (Exception) { /* best effort */ }
        }

        _currentDate = today;
        _writer = new StreamWriter(
            new FileStream(basePath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite),
            Encoding.UTF8);
        PruneOldFiles();
    }

    private void PruneOldFiles()
    {
        try
        {
            var cutoff = DateTime.Now.Date.AddDays(-_retentionDays);
            foreach (var file in Directory.GetFiles(_dir, $"{_baseName}.*"))
            {
                if (File.GetLastWriteTime(file).Date < cutoff)
                {
                    try { File.Delete(file); } catch (Exception) { /* best effort */ }
                }
            }
        }
        catch (Exception) { /* best effort */ }
    }

    private string Serialize(LogRecord record)
    {
        var payload = new Dictionary<string, object?>
        {
            ["timestamp"] = record.Timestamp.ToUniversalTime().ToString("o"),
            ["severity"] = record.LogLevel.ToString().ToUpperInvariant(),
            ["body"] = record.FormattedMessage ?? record.Body,
            ["service.name"] = MarketEdgeLogging.ServiceName,
            ["service.namespace"] = "marketedge",
        };

        foreach (var attr in ParentProvider.GetResource().Attributes)
        {
            if (attr.Key is "deployment.environment" or "host.name")
            {
                payload[attr.Key] = attr.Value;
            }
        }

        if (record.TraceId != default)
        {
            payload["trace_id"] = record.TraceId.ToHexString();
            payload["span_id"] = record.SpanId.ToHexString();
        }

        if (record.Attributes is { Count: > 0 })
        {
            var attrs = new Dictionary<string, object?>();
            foreach (var kvp in record.Attributes)
            {
                attrs[kvp.Key] = kvp.Value;
            }
            payload["attributes"] = attrs;
        }

        if (record.Exception is not null)
        {
            payload["exception.type"] = record.Exception.GetType().FullName;
            payload["exception.message"] = record.Exception.Message;
        }

        return JsonSerializer.Serialize(payload);
    }

    protected override bool OnShutdown(int timeoutMilliseconds)
    {
        lock (_gate)
        {
            _writer?.Flush();
            _writer?.Dispose();
            _writer = null;
        }
        return true;
    }
}
