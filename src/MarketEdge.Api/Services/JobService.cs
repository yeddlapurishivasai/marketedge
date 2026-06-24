using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IJobService
{
    Task<List<JobRunDto>> GetRunsAsync(string? market = null, string? jobType = null, int page = 1, int pageSize = 20);
    Task<JobRunDto?> GetRunByIdAsync(int id);
    Task<int> TriggerStageAnalysisAsync(string market, TriggerAnalysisRequest? request);
    Task<bool> CancelRunAsync(int id);
    Task<Stage2SummaryDto?> GetLatestStage2SummaryAsync(string market);
    Task<List<StageAnalysisResultDto>> GetStage2StocksAsync(int runId, string? classification = null, int? sectorId = null);
    Task<List<SectorRotationDto>> GetSectorRotationAsync(int runId);
    Task<List<Stage2HistoryDto>> GetStage2HistoryAsync(string market, int maxRuns = 10);
    Task<List<SectorRotationHistoryDto>> GetSectorRotationHistoryAsync(string market, int maxRuns = 12);
}

public class JobService : IJobService
{
    private readonly MarketEdgeDbContext _db;
    private readonly QueueClient _queueClient;

    public JobService(MarketEdgeDbContext db, QueueClient queueClient)
    {
        _db = db;
        _queueClient = queueClient;
    }

    public async Task<List<JobRunDto>> GetRunsAsync(string? market = null, string? jobType = null, int page = 1, int pageSize = 20)
    {
        var query = _db.JobRuns.AsQueryable();
        if (!string.IsNullOrEmpty(market))
            query = query.Where(j => j.Market == market);
        if (!string.IsNullOrEmpty(jobType))
            query = query.Where(j => j.JobType == jobType);

        return await query
            .OrderByDescending(j => j.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .Select(j => MapJobRun(j))
            .ToListAsync();
    }

    public async Task<JobRunDto?> GetRunByIdAsync(int id)
    {
        var job = await _db.JobRuns.FindAsync(id);
        return job == null ? null : MapJobRun(job);
    }

    public async Task<int> TriggerStageAnalysisAsync(string market, TriggerAnalysisRequest? request)
    {
        var now = DateTime.UtcNow;
        var weekNumber = GetIsoWeekNumber(now);

        // One in-flight run per (week, market): if a run is already queued or running
        // for this week, return it instead of starting a duplicate. Completed runs are
        // an append-only audit log — they never block a new run, and results are
        // upserted per week rather than deleted.
        var inFlightRun = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis"
                && j.Market == market
                && j.WeekNumber == weekNumber
                && (j.Status == "running" || j.Status == "queued"))
            .OrderByDescending(j => j.CreatedAt)
            .FirstOrDefaultAsync();

        if (inFlightRun != null)
        {
            return inFlightRun.Id;
        }

        var parameters = new Dictionary<string, object?>();
        if (request?.MinMarketCap != null) parameters["minMarketCap"] = request.MinMarketCap;
        if (request?.MaxMarketCap != null) parameters["maxMarketCap"] = request.MaxMarketCap;
        if (request?.SectorIds != null && request.SectorIds.Count > 0)
        {
            // Resolve sector names for display
            List<string> sectorNames;
            int totalSectors;
            if (market == "india")
            {
                sectorNames = await _db.IndianSectors
                    .Where(s => request.SectorIds.Contains(s.Id))
                    .Select(s => s.SectorName).ToListAsync();
                totalSectors = await _db.IndianSectors.CountAsync();
            }
            else
            {
                sectorNames = await _db.USSectors
                    .Where(s => request.SectorIds.Contains(s.Id))
                    .Select(s => s.SectorName).ToListAsync();
                totalSectors = await _db.USSectors.CountAsync();
            }
            if (sectorNames.Count == totalSectors)
                parameters["sectors"] = "All Sectors";
            else
                parameters["sectors"] = string.Join(", ", sectorNames);
        }
        if (request?.Limit != null) parameters["limit"] = request.Limit;
        if (request?.TestSampleOnly == true) parameters["testSampleOnly"] = true;
        if (request?.RetryFailedOnly == true) parameters["retryFailedOnly"] = true;

        var job = new JobRun
        {
            JobType = "stage2_analysis",
            Market = market,
            WeekNumber = weekNumber,
            Status = "queued",
            Progress = 0,
            Parameters = parameters.Count > 0 ? JsonSerializer.Serialize(parameters) : null,
            CreatedAt = DateTime.UtcNow
        };

        _db.JobRuns.Add(job);
        try
        {
            await _db.SaveChangesAsync();
        }
        catch (DbUpdateException ex) when (IsUniqueViolation(ex))
        {
            // Race: another request created the in-flight run for this week first.
            // The UX_JobRuns_ActiveWeek filtered unique index blocked this duplicate;
            // fall back to returning the existing in-flight run for the week.
            _db.Entry(job).State = EntityState.Detached;
            var existing = await _db.JobRuns
                .Where(j => j.JobType == "stage2_analysis"
                    && j.Market == market
                    && j.WeekNumber == weekNumber
                    && (j.Status == "running" || j.Status == "queued"))
                .OrderByDescending(j => j.CreatedAt)
                .FirstOrDefaultAsync();
            if (existing != null) return existing.Id;
            throw;
        }

        var message = JsonSerializer.Serialize(new
        {
            market,
            runId = job.Id,
            weekNumber,
            triggeredBy = "manual",
            minMarketCap = request?.MinMarketCap,
            maxMarketCap = request?.MaxMarketCap,
            sectorIds = request?.SectorIds,
            limit = request?.Limit,
            testSampleOnly = request?.TestSampleOnly,
            retryFailedOnly = request?.RetryFailedOnly,
            timestamp = DateTime.UtcNow
        });

        await _queueClient.CreateIfNotExistsAsync();
        await _queueClient.SendMessageAsync(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(message)));

        return job.Id;
    }

    public async Task<bool> CancelRunAsync(int id)
    {
        var job = await _db.JobRuns.FindAsync(id);
        if (job == null) return false;
        if (job.Status is "completed" or "failed" or "cancelled") return false;

        job.Status = "cancelled";
        job.ErrorMessage = "Cancelled by user";
        job.CompletedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync();
        return true;
    }

    public async Task<Stage2SummaryDto?> GetLatestStage2SummaryAsync(string market)
    {
        var latestRun = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis" && j.Market == market && j.Status == "completed")
            .OrderByDescending(j => j.CompletedAt)
            .FirstOrDefaultAsync();

        if (latestRun == null) return null;

        var week = latestRun.WeekNumber;
        var results = market == "india"
            ? await _db.IndianStageAnalysisResults.Where(r => r.WeekNumber == week).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => r.WeekNumber == week).ToListAsync<StageAnalysisResultBase>();

        var stage2 = results.Where(r => r.IsStage2).ToList();

        var summary = new Stage2SummaryDto
        {
            TotalStocks = results.Count,
            Stage2Count = stage2.Count,
            NewAdditions = results.Count(r => r.Classification == "new"),
            ReEntries = results.Count(r => r.Classification == "reentry"),
            Continuing = results.Count(r => r.Classification == "continuing"),
            Removed = results.Count(r => r.Classification == "removed"),
            BySector = stage2
                .GroupBy(r => r.SectorName)
                .Select(g => new SectorStage2CountDto
                {
                    SectorName = g.Key,
                    Stage2Count = g.Count(),
                    TotalCount = results.Count(r => r.SectorName == g.Key)
                })
                .OrderByDescending(s => s.Stage2Count)
                .ToList(),
            Top25 = stage2
                .OrderByDescending(r => r.RSScore ?? 0)
                .ThenByDescending(r => r.MomentumScore ?? 0)
                .Take(25)
                .Select(MapResult)
                .ToList()
        };

        return summary;
    }

    public async Task<List<StageAnalysisResultDto>> GetStage2StocksAsync(int runId, string? classification = null, int? sectorId = null)
    {
        // Determine market from the job run
        var job = await _db.JobRuns.FindAsync(runId);
        if (job == null) return new List<StageAnalysisResultDto>();

        IQueryable<StageAnalysisResultBase> query = job.Market == "india"
            ? _db.IndianStageAnalysisResults.Where(r => r.WeekNumber == job.WeekNumber)
            : _db.USStageAnalysisResults.Where(r => r.WeekNumber == job.WeekNumber);

        if (!string.IsNullOrEmpty(classification))
        {
            if (classification == "removed")
                query = query.Where(r => r.Classification == "removed");
            else
                query = query.Where(r => r.IsStage2 && r.Classification == classification);
        }
        else
        {
            query = query.Where(r => r.IsStage2);
        }

        if (sectorId.HasValue)
            query = query.Where(r => r.SectorId == sectorId.Value);

        return await query
            .OrderByDescending(r => r.RSScore)
            .ThenByDescending(r => r.MomentumScore)
            .Select(r => MapResult(r))
            .ToListAsync();
    }

    public async Task<List<SectorRotationDto>> GetSectorRotationAsync(int runId)
    {
        var job = await _db.JobRuns.FindAsync(runId);
        if (job == null) return new List<SectorRotationDto>();

        var results = job.Market == "india"
            ? await _db.IndianStageAnalysisResults.Where(r => r.WeekNumber == job.WeekNumber && r.RSScore != null && r.RSDelta2w != null).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => r.WeekNumber == job.WeekNumber && r.RSScore != null && r.RSDelta2w != null).ToListAsync<StageAnalysisResultBase>();

        return results
            .GroupBy(r => new { r.SectorId, r.SectorName })
            .Select(g => new SectorRotationDto
            {
                SectorId = g.Key.SectorId,
                SectorName = g.Key.SectorName,
                AvgRSScore = g.Average(r => r.RSScore ?? 0),
                AvgRSDelta2w = g.Average(r => r.RSDelta2w ?? 0),
                Quadrant = GetQuadrant(g.Average(r => r.RSScore ?? 0), g.Average(r => r.RSDelta2w ?? 0)),
                StockCount = g.Count(),
                AccumulatingCount = g.Count(r => r.ADClassification == "accumulating"),
                DistributingCount = g.Count(r => r.ADClassification == "distributing")
            })
            .OrderByDescending(s => s.AvgRSScore)
            .ToList();
    }

    public async Task<List<Stage2HistoryDto>> GetStage2HistoryAsync(string market, int maxRuns = 10)
    {
        var completed = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis" && j.Market == market && j.Status == "completed")
            .OrderByDescending(j => j.CompletedAt)
            .ToListAsync();

        // Results are upserted per week (one snapshot per week, last writer owns it).
        // Collapse the audit log to the latest completed run per week so history shows
        // one entry per week rather than one per trigger.
        var runs = completed
            .GroupBy(j => j.WeekNumber)
            .Select(g => g.OrderByDescending(j => j.CompletedAt).First())
            .OrderByDescending(j => j.CompletedAt)
            .Take(maxRuns)
            .ToList();

        var weeks = runs.Select(r => r.WeekNumber).ToList();
        var allResults = market == "india"
            ? await _db.IndianStageAnalysisResults.Where(r => weeks.Contains(r.WeekNumber) && r.IsStage2).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => weeks.Contains(r.WeekNumber) && r.IsStage2).ToListAsync<StageAnalysisResultBase>();

        return runs.Select(run => new Stage2HistoryDto
        {
            RunId = run.Id,
            RunDate = run.CompletedAt ?? run.CreatedAt,
            TotalStage2 = allResults.Count(r => r.WeekNumber == run.WeekNumber),
            BySector = allResults
                .Where(r => r.WeekNumber == run.WeekNumber)
                .GroupBy(r => r.SectorName)
                .Select(g => new SectorStage2CountDto
                {
                    SectorName = g.Key,
                    Stage2Count = g.Count()
                })
                .ToList()
        }).OrderBy(h => h.RunDate).ToList();
    }

    public async Task<List<SectorRotationHistoryDto>> GetSectorRotationHistoryAsync(string market, int maxRuns = 12)
    {
        var completed = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis" && j.Market == market && j.Status == "completed")
            .OrderByDescending(j => j.CompletedAt)
            .ToListAsync();

        // One entry per week: latest completed run owns the week's upserted snapshot.
        var runs = completed
            .GroupBy(j => j.WeekNumber)
            .Select(g => g.OrderByDescending(j => j.CompletedAt).First())
            .OrderByDescending(j => j.CompletedAt)
            .Take(maxRuns)
            .ToList();

        if (!runs.Any()) return new List<SectorRotationHistoryDto>();

        var weeks = runs.Select(r => r.WeekNumber).ToList();
        var allResults = market == "india"
            ? await _db.IndianStageAnalysisResults
                .Where(r => weeks.Contains(r.WeekNumber) && r.RSScore != null && r.RSDelta2w != null)
                .ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults
                .Where(r => weeks.Contains(r.WeekNumber) && r.RSScore != null && r.RSDelta2w != null)
                .ToListAsync<StageAnalysisResultBase>();

        return runs.Select(run => new SectorRotationHistoryDto
        {
            RunId = run.Id,
            RunDate = run.CompletedAt ?? run.CreatedAt,
            Sectors = allResults
                .Where(r => r.WeekNumber == run.WeekNumber)
                .GroupBy(r => new { r.SectorId, r.SectorName })
                .Select(g => new SectorRotationDto
                {
                    SectorId = g.Key.SectorId,
                    SectorName = g.Key.SectorName,
                    AvgRSScore = g.Average(r => r.RSScore ?? 0),
                    AvgRSDelta2w = g.Average(r => r.RSDelta2w ?? 0),
                    Quadrant = GetQuadrant(g.Average(r => r.RSScore ?? 0), g.Average(r => r.RSDelta2w ?? 0)),
                    StockCount = g.Count(),
                    AccumulatingCount = g.Count(r => r.ADClassification == "accumulating"),
                    DistributingCount = g.Count(r => r.ADClassification == "distributing")
                })
                .OrderByDescending(s => s.AvgRSScore)
                .ToList()
        }).OrderBy(h => h.RunDate).ToList();
    }

    private static string GetQuadrant(decimal rsScore, decimal rsMomentum)
    {
        return (rsScore > 0, rsMomentum > 0) switch
        {
            (true, true) => "leading",
            (true, false) => "weakening",
            (false, false) => "lagging",
            (false, true) => "improving"
        };
    }

    private static JobRunDto MapJobRun(JobRun j)
    {
        return new JobRunDto
        {
            Id = j.Id,
            JobType = j.JobType,
            Market = j.Market,
            WeekNumber = j.WeekNumber,
            Status = j.Status,
            Progress = j.Progress,
            Parameters = j.Parameters != null ? JsonSerializer.Deserialize<Dictionary<string, object>>(j.Parameters) : null,
            Metrics = j.Metrics != null ? JsonSerializer.Deserialize<Dictionary<string, object>>(j.Metrics) : null,
            ErrorMessage = j.ErrorMessage,
            StartedAt = j.StartedAt,
            CompletedAt = j.CompletedAt,
            CreatedAt = j.CreatedAt,
            DurationSeconds = j.StartedAt.HasValue && j.CompletedAt.HasValue
                ? (j.CompletedAt.Value - j.StartedAt.Value).TotalSeconds
                : null
        };
    }

    private static StageAnalysisResultDto MapResult(StageAnalysisResultBase r)
    {
        return new StageAnalysisResultDto
        {
            Id = r.Id,
            RunId = r.RunId,
            Symbol = r.Symbol,
            CompanyName = r.CompanyName,
            SectorId = r.SectorId,
            SectorName = r.SectorName,
            ClosePrice = r.ClosePrice,
            MA10 = r.MA10,
            MA30 = r.MA30,
            MarketCap = r.MarketCap,
            IsStage2 = r.IsStage2,
            Classification = r.Classification,
            WeeksInStage2 = r.WeeksInStage2,
            RSScore = r.RSScore,
            RSRank = r.RSRank,
            RS1w = r.RS1w,
            RS2w = r.RS2w,
            RS3w = r.RS3w,
            RSDelta1w = r.RSDelta1w,
            RSDelta2w = r.RSDelta2w,
            RSDelta3w = r.RSDelta3w,
            MomentumScore = r.MomentumScore,
            ROC1w = r.ROC1w,
            ROC2w = r.ROC2w,
            ROC3w = r.ROC3w,
            Quadrant = r.Quadrant,
            ADRatio = r.ADRatio,
            ADClassification = r.ADClassification
        };
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

    private static bool IsUniqueViolation(DbUpdateException ex)
    {
        // SQL Server unique index/constraint violations: 2601 (duplicate key row) / 2627 (unique constraint)
        return ex.InnerException is Microsoft.Data.SqlClient.SqlException sql
            && sql.Errors.Cast<Microsoft.Data.SqlClient.SqlError>().Any(e => e.Number is 2601 or 2627);
    }
}
