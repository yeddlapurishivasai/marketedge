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
    Task<Stage2SummaryDto?> GetLatestStage2SummaryAsync(string market);
    Task<List<StageAnalysisResultDto>> GetStage2StocksAsync(int runId, string? classification = null, int? sectorId = null);
    Task<List<SectorRotationDto>> GetSectorRotationAsync(int runId);
    Task<List<Stage2HistoryDto>> GetStage2HistoryAsync(string market, int maxRuns = 10);
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
        var parameters = new Dictionary<string, object?>();
        if (request?.MinMarketCap != null) parameters["minMarketCap"] = request.MinMarketCap;
        if (request?.MaxMarketCap != null) parameters["maxMarketCap"] = request.MaxMarketCap;
        if (request?.SectorIds != null && request.SectorIds.Count > 0) parameters["sectorIds"] = request.SectorIds;
        if (request?.Limit != null) parameters["limit"] = request.Limit;

        var job = new JobRun
        {
            JobType = "stage2_analysis",
            Market = market,
            Status = "queued",
            Progress = 0,
            Parameters = parameters.Count > 0 ? JsonSerializer.Serialize(parameters) : null,
            CreatedAt = DateTime.UtcNow
        };

        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        var message = JsonSerializer.Serialize(new
        {
            market,
            runId = job.Id,
            triggeredBy = "manual",
            minMarketCap = request?.MinMarketCap,
            maxMarketCap = request?.MaxMarketCap,
            sectorIds = request?.SectorIds,
            limit = request?.Limit,
            timestamp = DateTime.UtcNow
        });

        await _queueClient.CreateIfNotExistsAsync();
        await _queueClient.SendMessageAsync(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(message)));

        return job.Id;
    }

    public async Task<Stage2SummaryDto?> GetLatestStage2SummaryAsync(string market)
    {
        var latestRun = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis" && j.Market == market && j.Status == "completed")
            .OrderByDescending(j => j.CompletedAt)
            .FirstOrDefaultAsync();

        if (latestRun == null) return null;

        var results = market == "india"
            ? await _db.IndianStageAnalysisResults.Where(r => r.RunId == latestRun.Id).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => r.RunId == latestRun.Id).ToListAsync<StageAnalysisResultBase>();

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
            ? _db.IndianStageAnalysisResults.Where(r => r.RunId == runId)
            : _db.USStageAnalysisResults.Where(r => r.RunId == runId);

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
            ? await _db.IndianStageAnalysisResults.Where(r => r.RunId == runId && r.RSScore != null && r.RSMomentum != null).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => r.RunId == runId && r.RSScore != null && r.RSMomentum != null).ToListAsync<StageAnalysisResultBase>();

        return results
            .GroupBy(r => new { r.SectorId, r.SectorName })
            .Select(g => new SectorRotationDto
            {
                SectorId = g.Key.SectorId,
                SectorName = g.Key.SectorName,
                AvgRSScore = g.Average(r => r.RSScore ?? 0),
                AvgRSMomentum = g.Average(r => r.RSMomentum ?? 0),
                Quadrant = GetQuadrant(g.Average(r => r.RSScore ?? 0), g.Average(r => r.RSMomentum ?? 0)),
                StockCount = g.Count(),
                AccumulatingCount = g.Count(r => r.ADClassification == "accumulating"),
                DistributingCount = g.Count(r => r.ADClassification == "distributing")
            })
            .OrderByDescending(s => s.AvgRSScore)
            .ToList();
    }

    public async Task<List<Stage2HistoryDto>> GetStage2HistoryAsync(string market, int maxRuns = 10)
    {
        var runs = await _db.JobRuns
            .Where(j => j.JobType == "stage2_analysis" && j.Market == market && j.Status == "completed")
            .OrderByDescending(j => j.CompletedAt)
            .Take(maxRuns)
            .ToListAsync();

        var runIds = runs.Select(r => r.Id).ToList();
        var allResults = market == "india"
            ? await _db.IndianStageAnalysisResults.Where(r => runIds.Contains(r.RunId) && r.IsStage2).ToListAsync<StageAnalysisResultBase>()
            : await _db.USStageAnalysisResults.Where(r => runIds.Contains(r.RunId) && r.IsStage2).ToListAsync<StageAnalysisResultBase>();

        return runs.Select(run => new Stage2HistoryDto
        {
            RunId = run.Id,
            RunDate = run.CompletedAt ?? run.CreatedAt,
            TotalStage2 = allResults.Count(r => r.RunId == run.Id),
            BySector = allResults
                .Where(r => r.RunId == run.Id)
                .GroupBy(r => r.SectorName)
                .Select(g => new SectorStage2CountDto
                {
                    SectorName = g.Key,
                    Stage2Count = g.Count()
                })
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
            RSScore = r.RSScore,
            RSRank = r.RSRank,
            RSMomentum = r.RSMomentum,
            MomentumScore = r.MomentumScore,
            ROC12w = r.ROC12w,
            ROC26w = r.ROC26w,
            ROC52w = r.ROC52w,
            Quadrant = r.Quadrant,
            ADRatio = r.ADRatio,
            ADClassification = r.ADClassification
        };
    }
}
