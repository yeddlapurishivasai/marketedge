using System.Text;
using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public record TriggerIngestionRequest(
    bool TestSample = false,
    int? Limit = null,
    string[]? Steps = null,
    bool MissingOnly = false);

public interface IIngestionService
{
    Task<int> TriggerAsync(string market, TriggerIngestionRequest request);
    Task<int> RefreshStockAsync(string market, string symbol);
    Task<int> TriggerFundamentalsAsync(string market, string triggeredBy = "manual");
    Task<JobScheduleDto> GetFundamentalsScheduleAsync(string market);
    Task<JobScheduleDto> UpdateFundamentalsScheduleAsync(string market, UpdateJobScheduleRequest request);
}

/// <summary>
/// Enqueues data-ingestion work onto the shared Azure Storage queue and tracks each
/// invocation as a <c>data_ingestion</c> <see cref="JobRun"/>. The Python worker
/// (<c>src/MarketEdge.Worker</c>) consumes the message and runs the bundled ingestion
/// CLI (<c>src/MarketEdge.Ingestion</c>), updating the run row to its terminal state.
///
/// The worker runs the requested steps in pipeline order: <c>ingest bars</c> (which also
/// seeds the ticker universe), then <c>ingest technical</c>, then <c>ingest fundamentals</c>.
/// Running ingestion on the worker (the Python host with yfinance + ODBC already installed)
/// is what makes it work on cloud, where the .NET API has no Python runtime.
/// </summary>
public class IngestionService : IIngestionService
{
    public const string JobType = "data_ingestion";
    public const string StockRefreshJobType = "stock_refresh";
    public const string FundamentalsJobType = "fundamentals";

    // Pipeline stage names in execution order. The bars stage seeds the ticker universe
    // internally, so there is no separate seed step.
    private static readonly string[] PipelineSteps = { "bars", "technical", "fundamentals" };

    // A job is "active" (dedupe target) while it is queued or running.
    private static readonly string[] ActiveStatuses = { "queued", "running" };

    private readonly MarketEdgeDbContext _db;
    private readonly QueueClient _queueClient;
    private readonly ILogger<IngestionService> _logger;

    public IngestionService(
        MarketEdgeDbContext db,
        QueueClient queueClient,
        ILogger<IngestionService> logger)
    {
        _db = db;
        _queueClient = queueClient;
        _logger = logger;
    }

    public async Task<int> TriggerAsync(string market, TriggerIngestionRequest request)
    {
        if (market != "india" && market != "us")
            throw new ArgumentException("Market must be 'india' or 'us'.");

        if (request.Limit is < 1)
            throw new ArgumentException("Limit must be a positive integer.");

        // Resolve the requested steps (default: the whole pipeline), preserving pipeline order.
        var validSteps = PipelineSteps.ToHashSet(StringComparer.OrdinalIgnoreCase);
        string[] steps;
        if (request.Steps == null || request.Steps.Length == 0)
        {
            steps = PipelineSteps.ToArray();
        }
        else
        {
            var invalid = request.Steps.Where(s => !validSteps.Contains(s)).ToArray();
            if (invalid.Length > 0)
                throw new ArgumentException($"Unknown step(s): {string.Join(", ", invalid)}. Valid: {string.Join(", ", validSteps)}.");
            steps = PipelineSteps.Where(n => request.Steps.Contains(n, StringComparer.OrdinalIgnoreCase)).ToArray();
        }

        // One in-flight run per market: return the existing run if present.
        var existing = await _db.JobRuns
            .Where(j => j.JobType == JobType && j.Market == market && ActiveStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAt)
            .FirstOrDefaultAsync();
        if (existing != null)
            return existing.Id;

        var parameters = new Dictionary<string, object?>
        {
            ["market"] = market,
            ["testSample"] = request.TestSample,
            ["steps"] = steps,
            ["missingOnly"] = request.MissingOnly,
        };
        if (request.Limit is int lim) parameters["limit"] = lim;

        var now = DateTime.UtcNow;
        var job = new JobRun
        {
            JobType = JobType,
            Market = market,
            WeekNumber = GetIsoWeekNumber(now),
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(parameters),
            CreatedAt = now,
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        await EnqueueAsync(new
        {
            jobType = "ingestion",
            market,
            runId = job.Id,
            steps,
            testSample = request.TestSample,
            limit = request.Limit,
            missingOnly = request.MissingOnly,
            triggeredBy = "manual",
            timestamp = now,
        });

        return job.Id;
    }

    public async Task<int> RefreshStockAsync(string market, string symbol)
    {
        if (market != "india" && market != "us")
            throw new ArgumentException("Market must be 'india' or 'us'.");
        if (string.IsNullOrWhiteSpace(symbol))
            throw new ArgumentException("Symbol is required.");

        symbol = symbol.Trim().ToUpperInvariant();

        // One in-flight refresh per (market, symbol): return the existing run if present.
        var existing = await _db.JobRuns
            .Where(j => j.JobType == StockRefreshJobType && j.Market == market && ActiveStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAt)
            .ToListAsync();
        var inflight = existing.FirstOrDefault(j =>
            (j.Parameters ?? string.Empty).Contains($"\"symbol\":\"{symbol}\""));
        if (inflight != null)
            return inflight.Id;

        var now = DateTime.UtcNow;
        var job = new JobRun
        {
            JobType = StockRefreshJobType,
            Market = market,
            WeekNumber = GetIsoWeekNumber(now),
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["market"] = market,
                ["symbol"] = symbol,
                ["steps"] = PipelineSteps,
            }),
            CreatedAt = now,
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        // The worker re-ingests every pipeline step for this one symbol, then recomputes
        // its score (jobType "stock_refresh").
        await EnqueueAsync(new
        {
            jobType = "stock_refresh",
            market,
            runId = job.Id,
            steps = PipelineSteps,
            symbols = new[] { symbol },
            testSample = false,
            missingOnly = false,
            triggeredBy = "stock-refresh",
            timestamp = now,
        });

        return job.Id;
    }

    public async Task<int> TriggerFundamentalsAsync(string market, string triggeredBy = "manual")
    {
        if (market != "india" && market != "us")
            throw new ArgumentException("Market must be 'india' or 'us'.");

        // One in-flight fundamentals run per market: return the existing run if present.
        var existing = await _db.JobRuns
            .Where(j => j.JobType == FundamentalsJobType && j.Market == market && ActiveStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAt)
            .FirstOrDefaultAsync();
        if (existing != null)
            return existing.Id;

        var now = DateTime.UtcNow;
        var job = new JobRun
        {
            JobType = FundamentalsJobType,
            Market = market,
            WeekNumber = GetIsoWeekNumber(now),
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["market"] = market,
                ["universe"] = "stage2",
                ["steps"] = new[] { "fundamentals" },
                ["triggeredBy"] = triggeredBy,
            }),
            CreatedAt = now,
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        // The worker resolves the stage2 universe itself and runs only the fundamentals step.
        await EnqueueAsync(new
        {
            jobType = "fundamentals",
            market,
            runId = job.Id,
            universe = "stage2",
            triggeredBy,
            timestamp = now,
        });

        return job.Id;
    }

    private async Task EnqueueAsync(object message)
    {
        var json = JsonSerializer.Serialize(message);
        await _queueClient.CreateIfNotExistsAsync();
        await _queueClient.SendMessageAsync(Convert.ToBase64String(Encoding.UTF8.GetBytes(json)));
        _logger.LogInformation("Enqueued ingestion message: {Message}", json);
    }

    public async Task<JobScheduleDto> GetFundamentalsScheduleAsync(string market)
    {
        var s = await _db.FundamentalsSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            s = new FundamentalsSchedule { Market = market, Enabled = true, HourLocal = 20, UpdatedAt = DateTime.UtcNow };
            _db.FundamentalsSchedules.Add(s);
            await _db.SaveChangesAsync();
        }
        return new JobScheduleDto(s.Market, s.Enabled, s.HourLocal, s.LastEnqueuedAt, s.UpdatedAt,
            await GetLastRunAtAsync(FundamentalsJobType, market));
    }

    public async Task<JobScheduleDto> UpdateFundamentalsScheduleAsync(string market, UpdateJobScheduleRequest request)
    {
        var s = await _db.FundamentalsSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            s = new FundamentalsSchedule { Market = market };
            _db.FundamentalsSchedules.Add(s);
        }
        s.Enabled = request.Enabled;
        if (request.HourLocal is int h && h is >= 0 and <= 23) s.HourLocal = h;
        s.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync();
        return new JobScheduleDto(s.Market, s.Enabled, s.HourLocal, s.LastEnqueuedAt, s.UpdatedAt,
            await GetLastRunAtAsync(FundamentalsJobType, market));
    }

    private async Task<DateTime?> GetLastRunAtAsync(string jobType, string market)
    {
        var job = await _db.JobRuns
            .Where(j => j.JobType == jobType && j.Market == market)
            .OrderByDescending(j => j.Id)
            .Select(j => new { j.CompletedAt, j.StartedAt, j.CreatedAt })
            .FirstOrDefaultAsync();
        if (job == null) return null;
        return job.CompletedAt ?? job.StartedAt ?? job.CreatedAt;
    }

    private static string GetIsoWeekNumber(DateTime date)
    {
        var cal = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var week = cal.GetWeekOfYear(date, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
        var year = date.Year;
        if (week >= 52 && date.Month == 1) year--;
        if (week == 1 && date.Month == 12) year++;
        return $"{year}-W{week:D2}";
    }
}
