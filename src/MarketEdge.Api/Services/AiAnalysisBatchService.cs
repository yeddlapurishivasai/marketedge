using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IAiAnalysisBatchService
{
    Task<AiAnalysisBatchTriggerResponse> TriggerAsync(
        string market,
        int top = 20,
        string triggeredBy = "manual");
}

public class AiAnalysisBatchService : IAiAnalysisBatchService
{
    public const string JobType = "ai_analysis";
    private readonly MarketEdgeDbContext _db;

    public AiAnalysisBatchService(MarketEdgeDbContext db) => _db = db;

    public async Task<AiAnalysisBatchTriggerResponse> TriggerAsync(
        string market,
        int top = 20,
        string triggeredBy = "manual")
    {
        if (market is not ("india" or "us"))
            throw new ArgumentException("Market must be 'india' or 'us'.");

        var week = GetIsoWeekNumber(DateTime.UtcNow);
        var existing = await _db.JobRuns
            .Where(job => job.JobType == JobType
                && job.Market == market
                && job.WeekNumber == week
                && (job.Status == "queued" || job.Status == "running" || job.Status == "completed"))
            .OrderByDescending(job => job.Id)
            .FirstOrDefaultAsync();
        if (existing != null)
            return new AiAnalysisBatchTriggerResponse(existing.Id, week, true);

        var now = DateTime.UtcNow;
        var job = new JobRun
        {
            JobType = JobType,
            Market = market,
            WeekNumber = week,
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(new BatchParameters
            {
                Top = Math.Clamp(top, 1, 20),
                TriggeredBy = triggeredBy
            }),
            Stages = JsonSerializer.Serialize(new[]
            {
                new JobStageDto { Key = "selection", Label = "Select candidates", Status = "pending" },
                new JobStageDto { Key = "analysis", Label = "Run AI analyses", Status = "pending" }
            }),
            CreatedAt = now
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();
        return new AiAnalysisBatchTriggerResponse(job.Id, week, false);
    }

    internal static string GetIsoWeekNumber(DateTime date)
    {
        var calendar = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var week = calendar.GetWeekOfYear(
            date, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
        var year = date.Year;
        if (week >= 52 && date.Month == 1) year--;
        if (week == 1 && date.Month == 12) year++;
        return $"{year}-W{week:D2}";
    }

    internal sealed class BatchParameters
    {
        public int Top { get; set; } = 20;
        public string TriggeredBy { get; set; } = "manual";
        public List<string>? Symbols { get; set; }
    }
}

public class AiAnalysisBatchProcessor : BackgroundService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<AiAnalysisBatchProcessor> _logger;

    public AiAnalysisBatchProcessor(
        IServiceScopeFactory scopeFactory,
        ILogger<AiAnalysisBatchProcessor> logger)
    {
        _scopeFactory = scopeFactory;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await RequeueInterruptedJobsAsync(stoppingToken);
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var processed = await ProcessNextAsync(stoppingToken);
                if (!processed)
                    await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "AI batch processor loop failed");
                await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken);
            }
        }
    }

    private async Task<bool> ProcessNextAsync(CancellationToken cancellationToken)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var job = await db.JobRuns
            .Where(row => row.JobType == AiAnalysisBatchService.JobType && row.Status == "queued")
            .OrderBy(row => row.CreatedAt)
            .FirstOrDefaultAsync(cancellationToken);
        if (job == null) return false;

        job.Status = "running";
        job.StartedAt ??= DateTime.UtcNow;
        job.ErrorMessage = null;
        await db.SaveChangesAsync(cancellationToken);

        try
        {
            var parameters = JsonSerializer.Deserialize<AiAnalysisBatchService.BatchParameters>(
                job.Parameters ?? "{}", JsonOptions) ?? new AiAnalysisBatchService.BatchParameters();
            var symbols = parameters.Symbols;
            if (symbols == null)
            {
                SetStages(job, selectionStatus: "running", analysisStatus: "pending");
                await db.SaveChangesAsync(cancellationToken);
                var selector = scope.ServiceProvider.GetRequiredService<IAiAnalysisCandidateService>();
                var selection = await selector.GetTopCandidatesAsync(job.Market, parameters.Top, excludeAnalyzed: true)
                    ?? throw new InvalidOperationException("No completed Stage 2 analysis is available.");
                symbols = selection.Candidates.Select(candidate => candidate.Symbol).ToList();
                parameters.Symbols = symbols;
                job.Parameters = JsonSerializer.Serialize(parameters);
                SetStages(job, selectionStatus: "completed", analysisStatus: "running",
                    selectionDetail: $"Selected {symbols.Count} of {selection.AvailableAfterAnalyzedExclusion} eligible stocks");
                await db.SaveChangesAsync(cancellationToken);
            }

            var inputService = scope.ServiceProvider.GetRequiredService<IAiStockAnalysisService>();
            var runner = scope.ServiceProvider.GetRequiredService<IAiInvestmentAnalysisRunner>();
            var store = scope.ServiceProvider.GetRequiredService<IAiInvestmentAnalysisStore>();
            var completed = 0;
            var skipped = 0;
            var failures = new List<object>();

            for (var index = 0; index < symbols.Count; index++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                await db.Entry(job).ReloadAsync(cancellationToken);
                if (job.Status == "cancelled") return true;

                var symbol = symbols[index];
                if (await store.GetAsync(job.Market, symbol, job.WeekNumber) != null)
                {
                    skipped++;
                }
                else
                {
                    try
                    {
                        var input = await inputService.GetAsync(job.Market, symbol)
                            ?? throw new InvalidOperationException("Deterministic stock input is unavailable.");
                        await runner.RunAsync(job.Market, symbol, input, cancellationToken);
                        completed++;
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        failures.Add(new { symbol, error = ex.Message });
                        _logger.LogWarning(ex, "AI analysis failed for {Market}/{Symbol}", job.Market, symbol);
                    }
                }

                job.Progress = symbols.Count == 0 ? 100 : (index + 1) * 100 / symbols.Count;
                job.Metrics = JsonSerializer.Serialize(new
                {
                    selected = symbols.Count,
                    completed,
                    skipped,
                    failed = failures.Count,
                    failures
                });
                SetStages(job, "completed", "running",
                    selectionDetail: $"Selected {symbols.Count} stocks",
                    analysisProgress: job.Progress,
                    analysisDetail: $"{index + 1}/{symbols.Count}: {symbol}");
                await db.SaveChangesAsync(cancellationToken);
            }

            job.Status = "completed";
            job.Progress = 100;
            job.CompletedAt = DateTime.UtcNow;
            SetStages(job, "completed", "completed",
                selectionDetail: $"Selected {symbols.Count} stocks",
                analysisProgress: 100,
                analysisDetail: $"Completed {completed}, skipped {skipped}, failed {failures.Count}");
            await db.SaveChangesAsync(cancellationToken);
            return true;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            job.Status = "queued";
            job.ErrorMessage = "Interrupted by application shutdown; queued for resume.";
            await db.SaveChangesAsync(CancellationToken.None);
            throw;
        }
        catch (Exception ex)
        {
            job.Status = "failed";
            job.ErrorMessage = ex.Message;
            job.CompletedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(CancellationToken.None);
            _logger.LogError(ex, "AI batch job {RunId} failed", job.Id);
            return true;
        }
    }

    private async Task RequeueInterruptedJobsAsync(CancellationToken cancellationToken)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        await db.JobRuns
            .Where(job => job.JobType == AiAnalysisBatchService.JobType && job.Status == "running")
            .ExecuteUpdateAsync(update => update
                .SetProperty(job => job.Status, "queued")
                .SetProperty(job => job.ErrorMessage, "Recovered after application restart."),
                cancellationToken);
    }

    private static void SetStages(
        JobRun job,
        string selectionStatus,
        string analysisStatus,
        string? selectionDetail = null,
        int analysisProgress = 0,
        string? analysisDetail = null)
    {
        job.Stages = JsonSerializer.Serialize(new[]
        {
            new JobStageDto
            {
                Key = "selection", Label = "Select candidates", Status = selectionStatus,
                Progress = selectionStatus == "completed" ? 100 : 0, Detail = selectionDetail
            },
            new JobStageDto
            {
                Key = "analysis", Label = "Run AI analyses", Status = analysisStatus,
                Progress = analysisProgress, Detail = analysisDetail
            }
        });
    }
}
