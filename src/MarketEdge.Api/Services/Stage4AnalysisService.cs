using System.Text;
using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IStage4AnalysisService
{
    Task<int> TriggerAsync(string market, TriggerStage4Request? request = null);
    Task<Stage4SummaryDto?> GetSummaryAsync(string market, int? runId = null);
    Task<List<Stage4AnalysisResultDto>?> GetStocksAsync(string market, int runId,
        string? classification = null, int? sectorId = null, decimal? minMarketCap = null);
    Task<List<Stage4HistoryDto>> GetHistoryAsync(string market, int maxRuns = 10, int? runId = null);
    Task<List<SectorRotationDto>?> GetSectorRotationAsync(string market, int runId);
    Task<JobScheduleDto> GetScheduleAsync(string market);
    Task<JobScheduleDto> UpdateScheduleAsync(string market, UpdateJobScheduleRequest request);
    Task<bool> EnqueueScheduledAsync(string market, DateTimeOffset nowUtc);
}

public sealed class Stage4EnqueueException(int runId, Exception inner)
    : Exception($"Stage 4 run {runId} could not be enqueued. The run has been marked failed.", inner)
{
    public int RunId { get; } = runId;
}

public sealed class Stage4ActiveRunException(int runId)
    : InvalidOperationException($"Stage 4 run {runId} is already active for this week with a different scope.");

public sealed class Stage4SnapshotNotFoundException()
    : Exception("No matching completed Stage 4 snapshot found.");

public class Stage4AnalysisService(
    MarketEdgeDbContext db, QueueClient queueClient, TimeProvider? timeProvider = null) : IStage4AnalysisService
{
    public const string JobType = "stage4_analysis";
    private readonly TimeProvider _clock = timeProvider ?? TimeProvider.System;
    private static readonly string[] FilterKeys = ["minMarketCap", "maxMarketCap", "sectorIds", "limit", "testSampleOnly"];

    public async Task<int> TriggerAsync(string market, TriggerStage4Request? request = null)
    {
        Stage4Week.ValidateMarket(market);
        request ??= new TriggerStage4Request();
        ValidateFilters(request);
        var now = _clock.GetUtcNow();
        Dictionary<string, JsonElement> parameters;
        string week;
        if (request.RetrySourceRunId is int sourceId)
        {
            var source = await db.JobRuns.AsNoTracking().SingleOrDefaultAsync(j =>
                j.Id == sourceId && j.Market == market && j.JobType == JobType);
            if (source is null) throw new ArgumentException("Retry source must be a Stage 4 run in the same market.");
            if (source.Status is not ("completed" or "failed"))
                throw new ArgumentException("Retry source must be completed or failed, not queued, running, or cancelled.");
            week = Stage4Week.Resolve(market, request.WeekNumber ?? source.WeekNumber, now);
            if (week != source.WeekNumber) throw new ArgumentException("Retry source must have the same target week.");
            parameters = Parse(source.Parameters);
            if (!HasFrozenUniverse(parameters))
                throw new ArgumentException("Retry source requires matching frozen universeSymbols and universeSnapshot metadata, and a valid eligibilityCapturedAt.");
            ValidateRetryFilters(request, parameters);
            parameters["retrySourceRunId"] = JsonSerializer.SerializeToElement(sourceId);
        }
        else
        {
            week = Stage4Week.Resolve(market, request.WeekNumber, now);
            if (request.SectorIds is { Count: > 0 } ids)
            {
                var unique = ids.Distinct().ToList();
                var count = market == "india"
                    ? await db.IndianSectors.CountAsync(s => unique.Contains(s.Id))
                    : await db.USSectors.CountAsync(s => unique.Contains(s.Id));
                if (count != unique.Count) throw new ArgumentException("One or more sectorIds do not exist in this market.");
            }
            parameters = CreateParameters(request);
        }
        foreach (var key in FilterKeys)
            parameters.TryAdd(key, JsonSerializer.SerializeToElement<object?>(key == "testSampleOnly" ? false : null));
        parameters["scope"] = JsonSerializer.SerializeToElement(GetScope(parameters));
        parameters["fnoOnly"] = JsonSerializer.SerializeToElement(true);

        var active = await ActiveRun(market, week).FirstOrDefaultAsync();
        if (active is not null) return MatchActive(active, parameters);

        var job = new JobRun
        {
            JobType = JobType, Market = market, WeekNumber = week, Status = "queued",
            Parameters = JsonSerializer.Serialize(parameters), CreatedAt = now.UtcDateTime
        };
        db.JobRuns.Add(job);
        try
        {
            await db.SaveChangesAsync();
        }
        catch (DbUpdateException ex) when (IsUniqueViolation(ex))
        {
            db.Entry(job).State = EntityState.Detached;
            active = await ActiveRun(market, week).FirstOrDefaultAsync();
            if (active is null) throw;
            return MatchActive(active, parameters);
        }

        // Frozen universe metadata stays in SQL; queue messages must remain below 64 KiB.
        var payload = new Dictionary<string, JsonElement>
        {
            ["jobType"] = JsonSerializer.SerializeToElement(JobType),
            ["market"] = JsonSerializer.SerializeToElement(market),
            ["runId"] = JsonSerializer.SerializeToElement(job.Id),
            ["weekNumber"] = JsonSerializer.SerializeToElement(week),
            ["scope"] = parameters["scope"],
            ["fnoOnly"] = parameters["fnoOnly"]
        };
        foreach (var key in FilterKeys)
            payload[key] = parameters[key];
        if (parameters.TryGetValue("retrySourceRunId", out var retrySource))
            payload["retrySourceRunId"] = retrySource;
        try
        {
            await queueClient.CreateIfNotExistsAsync();
            var message = Convert.ToBase64String(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
            await queueClient.SendMessageAsync(message, CancellationToken.None);
        }
        catch (Exception ex)
        {
            job.Status = "failed";
            job.ErrorMessage = "Stage 4 queue submission failed. Retry by starting a new run.";
            job.CompletedAt = _clock.GetUtcNow().UtcDateTime;
            await db.SaveChangesAsync();
            throw new Stage4EnqueueException(job.Id, ex);
        }
        return job.Id;
    }

    public async Task<Stage4SummaryDto?> GetSummaryAsync(string market, int? runId = null)
    {
        Stage4Week.ValidateMarket(market);
        var run = runId.HasValue
            ? await CompletedRun(market, runId.Value)
            : (await PublishedRuns(market)).FirstOrDefault();
        if (run is null) return null;
        var results = await Results(market, run.Id);
        var evaluated = results.Where(IsEvaluated).ToList();
        var qualified = evaluated.Where(r => r.IsStage4 == true).ToList();
        var metrics = Parse(run.Metrics);
        return new Stage4SummaryDto
        {
            RunId = run.Id, WeekNumber = run.WeekNumber, Scope = GetScope(Parse(run.Parameters)),
            Metrics = metrics.ToDictionary(p => p.Key, p => (object)p.Value),
            TotalStocks = MetricCount(metrics, "eligibleStocks", results.Count),
            EvaluatedStocks = MetricCount(metrics, "evaluatedStocks", evaluated.Count),
            SkippedStocks = MetricCount(metrics, "skippedStocks", results.Count(r => r.AnalysisStatus is "insufficient" or "stale")),
            ErrorStocks = MetricCount(metrics, "errorStocks", results.Count(r => r.AnalysisStatus == "error")),
            Stage4Count = qualified.Count,
            NewAdditions = qualified.Count(r => r.Classification == "new"),
            ReEntries = qualified.Count(r => r.Classification == "reentry"),
            Continuing = qualified.Count(r => r.Classification == "continuing"),
            Removed = evaluated.Count(r => r.IsStage4 == false && r.Classification == "removed"),
            BySector = SectorCounts(results), Top25 = Ranked(qualified).Take(25).Select(Map).ToList(),
            Warnings = metrics.TryGetValue("warnings", out var warnings) && warnings.ValueKind == JsonValueKind.Array
                ? warnings.EnumerateArray().Where(w => w.ValueKind == JsonValueKind.String).Select(w => w.GetString()!).ToList()
                : []
        };
    }

    public async Task<List<Stage4AnalysisResultDto>?> GetStocksAsync(string market, int runId,
        string? classification = null, int? sectorId = null, decimal? minMarketCap = null)
    {
        Stage4Week.ValidateMarket(market);
        if (classification is not (null or "all" or "new" or "reentry" or "continuing" or "removed"))
            throw new ArgumentException("Classification must be all, new, reentry, continuing, or removed.");
        if (sectorId is <= 0 || minMarketCap is < 0)
            throw new ArgumentException("SectorId must be positive and minMarketCap must be non-negative.");
        if (await CompletedRun(market, runId) is null) return null;
        IEnumerable<Stage4AnalysisResultBase> results = await Results(market, runId);
        if (classification is not (null or "all"))
            results = results.Where(r => IsEvaluated(r) &&
                (classification == "removed" ? r.IsStage4 == false && r.Classification == "removed" : r.IsStage4 == true));
        if (classification is not (null or "all")) results = results.Where(r => r.Classification == classification);
        if (sectorId.HasValue) results = results.Where(r => r.SectorId == sectorId);
        if (minMarketCap.HasValue) results = results.Where(r => r.MarketCap >= minMarketCap);
        return Ranked(results).Select(Map).ToList();
    }

    public async Task<List<Stage4HistoryDto>> GetHistoryAsync(string market, int maxRuns = 10, int? runId = null)
    {
        Stage4Week.ValidateMarket(market);
        if (maxRuns is < 1 or > 100) throw new ArgumentException("MaxRuns must be between 1 and 100.");
        var selected = runId.HasValue ? await CompletedRun(market, runId.Value) : null;
        if (runId.HasValue && selected is null) throw new Stage4SnapshotNotFoundException();
        var runs = (await PublishedRuns(market, selected)).Take(maxRuns).ToList();
        var ids = runs.Select(r => r.Id).ToList();
        var results = await Results(market, ids);
        return runs.AsEnumerable().Reverse().Select(run =>
        {
            var rows = results.Where(r => r.RunId == run.Id).ToList();
            return new Stage4HistoryDto(run.Id, run.WeekNumber, run.CompletedAt ?? run.CreatedAt,
                GetScope(Parse(run.Parameters)), rows.Count(r => IsEvaluated(r) && r.IsStage4 == true), SectorCounts(rows));
        }).ToList();
    }

    public async Task<List<SectorRotationDto>?> GetSectorRotationAsync(string market, int runId)
    {
        Stage4Week.ValidateMarket(market);
        if (await CompletedRun(market, runId) is null) return null;
        var results = await Results(market, runId);
        return results.Where(r => IsEvaluated(r) && r.RSScore.HasValue && r.RSDelta2w.HasValue)
            .GroupBy(r => new { r.SectorId, r.SectorName }).Select(g =>
            {
                var rs = g.Average(r => r.RSScore!.Value);
                var delta = g.Average(r => r.RSDelta2w!.Value);
                return new SectorRotationDto
                {
                    SectorId = g.Key.SectorId, SectorName = g.Key.SectorName, AvgRSScore = rs,
                    AvgRSDelta2w = delta, StockCount = g.Count(),
                    Quadrant = (rs > 0, delta > 0) switch
                    {
                        (true, true) => "leading", (true, false) => "weakening",
                        (false, true) => "improving", _ => "lagging"
                    },
                    AccumulatingCount = g.Count(r => r.ADClassification == "accumulating"),
                    DistributingCount = g.Count(r => r.ADClassification == "distributing")
                };
            }).OrderBy(r => r.AvgRSScore).ThenBy(r => r.SectorName, StringComparer.Ordinal).ToList();
    }

    public async Task<JobScheduleDto> GetScheduleAsync(string market)
    {
        Stage4Week.ValidateMarket(market);
        return await ScheduleDto(await EnsureSchedule(market));
    }

    public async Task<JobScheduleDto> UpdateScheduleAsync(string market, UpdateJobScheduleRequest request)
    {
        Stage4Week.ValidateMarket(market);
        if (request.HourLocal is < 0 or > 23) throw new ArgumentException("HourLocal must be between 0 and 23.");
        var schedule = await EnsureSchedule(market);
        schedule.Enabled = request.Enabled;
        if (request.HourLocal is int hour) schedule.HourLocal = hour;
        schedule.UpdatedAt = _clock.GetUtcNow().UtcDateTime;
        await db.SaveChangesAsync();
        return await ScheduleDto(schedule);
    }

    public async Task<bool> EnqueueScheduledAsync(string market, DateTimeOffset nowUtc)
    {
        Stage4Week.ValidateMarket(market);
        var schedule = await EnsureSchedule(market);
        var local = MarketHours.NowLocal(market, nowUtc);
        if (!schedule.Enabled || local is not DateTimeOffset now ||
            now.DayOfWeek != DayOfWeek.Saturday || now.Hour < schedule.HourLocal) return false;
        if (schedule.LastEnqueuedAt is DateTime last &&
            MarketHours.NowLocal(market, new DateTimeOffset(DateTime.SpecifyKind(last, DateTimeKind.Utc)))?.Date == now.Date)
            return false;
        if (await db.JobRuns.AnyAsync(j => j.JobType == JobType && j.Market == market &&
            (j.Status == "queued" || j.Status == "running"))) return false;

        var previous = schedule.LastEnqueuedAt;
        var claimedAt = nowUtc.UtcDateTime;
        if (db.Database.IsRelational())
        {
            // Compare-and-swap claims the local Saturday across API instances before queue submission.
            var claimed = await db.Stage4Schedules.Where(s => s.Market == market && s.Enabled &&
                s.HourLocal == schedule.HourLocal && s.LastEnqueuedAt == previous)
                .ExecuteUpdateAsync(s => s.SetProperty(x => x.LastEnqueuedAt, claimedAt)
                    .SetProperty(x => x.UpdatedAt, claimedAt));
            if (claimed == 0) return false;
            await db.Entry(schedule).ReloadAsync();
        }
        else
        {
            schedule.LastEnqueuedAt = claimedAt;
            schedule.UpdatedAt = claimedAt;
            await db.SaveChangesAsync();
        }
        try
        {
            await TriggerAsync(market);
            return true;
        }
        catch
        {
            if (db.Database.IsRelational())
            {
                await db.Stage4Schedules.Where(s => s.Market == market && s.LastEnqueuedAt == claimedAt)
                    .ExecuteUpdateAsync(s => s.SetProperty(x => x.LastEnqueuedAt, previous));
                await db.Entry(schedule).ReloadAsync();
            }
            else
            {
                schedule.LastEnqueuedAt = previous;
                await db.SaveChangesAsync();
            }
            throw;
        }
    }

    private async Task<Stage4Schedule> EnsureSchedule(string market)
    {
        var schedule = await db.Stage4Schedules.SingleOrDefaultAsync(s => s.Market == market);
        if (schedule is not null) return schedule;
        schedule = new Stage4Schedule { Market = market, UpdatedAt = _clock.GetUtcNow().UtcDateTime };
        db.Stage4Schedules.Add(schedule);
        try { await db.SaveChangesAsync(); }
        catch (DbUpdateException ex) when (IsUniqueViolation(ex))
        {
            db.Entry(schedule).State = EntityState.Detached;
            return await db.Stage4Schedules.SingleAsync(s => s.Market == market);
        }
        return schedule;
    }

    private async Task<JobScheduleDto> ScheduleDto(Stage4Schedule schedule)
    {
        var lastRun = await db.JobRuns.AsNoTracking().Where(j => j.JobType == JobType && j.Market == schedule.Market)
            .OrderByDescending(j => j.CreatedAt).ThenByDescending(j => j.Id).FirstOrDefaultAsync();
        return new JobScheduleDto(schedule.Market, schedule.Enabled, schedule.HourLocal, schedule.LastEnqueuedAt,
            schedule.UpdatedAt, lastRun?.CompletedAt ?? lastRun?.StartedAt ?? lastRun?.CreatedAt);
    }

    private IQueryable<JobRun> ActiveRun(string market, string week) => db.JobRuns.AsNoTracking().Where(j =>
        j.Market == market && j.JobType == JobType && j.WeekNumber == week && (j.Status == "queued" || j.Status == "running"));

    private Task<JobRun?> CompletedRun(string market, int id) => db.JobRuns.AsNoTracking().SingleOrDefaultAsync(j =>
        j.Id == id && j.Market == market && j.JobType == JobType && j.Status == "completed");

    private async Task<List<JobRun>> PublishedRuns(string market, JobRun? selected = null)
    {
        var runs = await db.JobRuns.AsNoTracking()
            .Where(j => j.Market == market && j.JobType == JobType && j.Status == "completed")
            .OrderByDescending(j => j.WeekNumber).ThenByDescending(j => j.CompletedAt).ThenByDescending(j => j.Id)
            .ToListAsync();
        var scope = selected is null ? null : Parse(selected.Parameters);
        return runs.Where(j =>
            {
                var parameters = Parse(j.Parameters);
                if (scope is null) return GetScope(parameters) == "full";
                if (string.CompareOrdinal(j.WeekNumber, selected!.WeekNumber) > 0) return false;
                // Pin the selected week's exact snapshot instead of silently substituting a later rerun.
                if (j.WeekNumber == selected.WeekNumber) return j.Id == selected.Id;
                return GetScope(parameters) == GetScope(scope) &&
                    FilterKeys.All(key => FilterValue(parameters, key) == FilterValue(scope, key));
            })
            .DistinctBy(j => j.WeekNumber).ToList();
    }

    private Task<List<Stage4AnalysisResultBase>> Results(string market, int runId) => Results(market, [runId]);

    private async Task<List<Stage4AnalysisResultBase>> Results(string market, List<int> runIds) => market == "india"
        ? await db.IndianStage4AnalysisResults.AsNoTracking().Where(r => runIds.Contains(r.RunId)).ToListAsync<Stage4AnalysisResultBase>()
        : await db.USStage4AnalysisResults.AsNoTracking().Where(r => runIds.Contains(r.RunId)).ToListAsync<Stage4AnalysisResultBase>();

    private static void ValidateFilters(TriggerStage4Request request)
    {
        if (request.MinMarketCap is < 0 || request.MaxMarketCap is < 0 ||
            request.MinMarketCap > request.MaxMarketCap)
            throw new ArgumentException("Market cap filters must be non-negative and minMarketCap cannot exceed maxMarketCap.");
        if (request.Limit is <= 0) throw new ArgumentException("Limit must be positive.");
        if (request.SectorIds?.Any(id => id <= 0) == true) throw new ArgumentException("SectorIds must be positive.");
        if (request.RetrySourceRunId is <= 0) throw new ArgumentException("RetrySourceRunId must be positive.");
    }

    private static Dictionary<string, JsonElement> CreateParameters(TriggerStage4Request request) =>
        new()
        {
            ["minMarketCap"] = JsonSerializer.SerializeToElement(request.MinMarketCap),
            ["maxMarketCap"] = JsonSerializer.SerializeToElement(request.MaxMarketCap),
            ["sectorIds"] = JsonSerializer.SerializeToElement(request.SectorIds?.Distinct().Order().ToList()),
            ["limit"] = JsonSerializer.SerializeToElement(request.Limit),
            ["testSampleOnly"] = JsonSerializer.SerializeToElement(request.TestSampleOnly ?? false)
        };

    private static void ValidateRetryFilters(TriggerStage4Request request, Dictionary<string, JsonElement> source)
    {
        var supplied = CreateParameters(request);
        var keys = new List<string>();
        if (request.MinMarketCap.HasValue) keys.Add("minMarketCap");
        if (request.MaxMarketCap.HasValue) keys.Add("maxMarketCap");
        if (request.SectorIds is not null) keys.Add("sectorIds");
        if (request.Limit.HasValue) keys.Add("limit");
        if (request.TestSampleOnly.HasValue) keys.Add("testSampleOnly");
        if (keys.Any(key => FilterValue(source, key) != FilterValue(supplied, key)))
            throw new ArgumentException("Retry filters conflict with the source run. Omit filters to reuse its frozen scope.");
    }

    private static bool HasFrozenUniverse(Dictionary<string, JsonElement> parameters)
    {
        if (!parameters.TryGetValue("universeSymbols", out var universe) || universe.ValueKind != JsonValueKind.Array ||
            universe.GetArrayLength() == 0 ||
            universe.EnumerateArray().Any(s => s.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(s.GetString())) ||
            !parameters.TryGetValue("universeSnapshot", out var snapshot) || snapshot.ValueKind != JsonValueKind.Array ||
            snapshot.GetArrayLength() != universe.GetArrayLength() ||
            snapshot.EnumerateArray().Any(s => s.ValueKind != JsonValueKind.Object ||
                !s.TryGetProperty("symbol", out var symbol) || symbol.ValueKind != JsonValueKind.String ||
                string.IsNullOrWhiteSpace(symbol.GetString())) ||
            !parameters.TryGetValue("eligibilityCapturedAt", out var captured) ||
            captured.ValueKind != JsonValueKind.String || !captured.TryGetDateTime(out _))
            return false;
        var symbols = universe.EnumerateArray().Select(s => s.GetString()!).ToHashSet(StringComparer.Ordinal);
        var metadataSymbols = snapshot.EnumerateArray().Select(s => s.GetProperty("symbol").GetString()!).ToHashSet(StringComparer.Ordinal);
        return symbols.Count == universe.GetArrayLength() && metadataSymbols.Count == snapshot.GetArrayLength() &&
            symbols.SetEquals(metadataSymbols);
    }

    private static int MatchActive(JobRun active, Dictionary<string, JsonElement> requested)
    {
        var current = Parse(active.Parameters);
        if (FilterKeys.Append("retrySourceRunId").Any(key => FilterValue(current, key) != FilterValue(requested, key)))
            throw new Stage4ActiveRunException(active.Id);
        return active.Id;
    }

    private static string FilterValue(Dictionary<string, JsonElement> values, string key)
    {
        if (!values.TryGetValue(key, out var value) || value.ValueKind == JsonValueKind.Null)
            return key == "testSampleOnly" ? "false" : key == "sectorIds" ? "[]" : "null";
        if (key == "sectorIds" && value.ValueKind == JsonValueKind.Array)
            return JsonSerializer.Serialize(value.EnumerateArray().Select(v => v.GetInt32()).Distinct().Order());
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDecimal(out var number))
            return number.ToString("G29", System.Globalization.CultureInfo.InvariantCulture);
        return value.GetRawText();
    }

    private static string GetScope(Dictionary<string, JsonElement> parameters)
    {
        if (parameters.TryGetValue("scope", out var scope) && scope.ValueKind == JsonValueKind.String &&
            scope.GetString() is "full" or "filtered" or "sample") return scope.GetString()!;
        if (FilterValue(parameters, "testSampleOnly") == "true") return "sample";
        return FilterValue(parameters, "sectorIds") != "[]" || FilterValue(parameters, "minMarketCap") != "null" ||
            FilterValue(parameters, "maxMarketCap") != "null" || FilterValue(parameters, "limit") != "null" ? "filtered" : "full";
    }

    private static Dictionary<string, JsonElement> Parse(string? json) => string.IsNullOrWhiteSpace(json)
        ? new() : JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json) ?? new();

    private static bool IsUniqueViolation(DbUpdateException ex) => ex.InnerException is SqlException { Number: 2601 or 2627 };
    private static bool IsEvaluated(Stage4AnalysisResultBase r) => r.AnalysisStatus == "evaluated" && r.IsStage4.HasValue;
    private static int MetricCount(Dictionary<string, JsonElement> metrics, string name, int fallback) =>
        metrics.TryGetValue(name, out var value) && value.ValueKind == JsonValueKind.Number &&
        value.TryGetInt32(out var count) && count >= 0 ? count : fallback;

    private static IOrderedEnumerable<Stage4AnalysisResultBase> Ranked(IEnumerable<Stage4AnalysisResultBase> rows) =>
        rows.OrderBy(r => r.RSScore ?? decimal.MaxValue).ThenBy(r => r.MomentumScore ?? decimal.MaxValue)
            .ThenBy(r => r.Symbol, StringComparer.Ordinal);

    private static List<SectorStage4CountDto> SectorCounts(List<Stage4AnalysisResultBase> rows) =>
        rows.GroupBy(r => new { r.SectorId, r.SectorName }).Select(g => new SectorStage4CountDto(
            g.Key.SectorId, g.Key.SectorName, g.Count(r => IsEvaluated(r) && r.IsStage4 == true), g.Count()))
            .OrderByDescending(s => s.Stage4Count).ThenBy(s => s.SectorName, StringComparer.Ordinal).ToList();

    private static Stage4AnalysisResultDto Map(Stage4AnalysisResultBase r) => new()
    {
        Id = r.Id, RunId = r.RunId, WeekNumber = r.WeekNumber, Symbol = r.Symbol, CompanyName = r.CompanyName,
        SectorId = r.SectorId, SectorName = r.SectorName, ClosePrice = r.ClosePrice, MA10 = r.MA10, MA30 = r.MA30,
        MA30FourWeeksAgo = r.MA30FourWeeksAgo, MarketCap = r.MarketCap, IsStage4 = r.IsStage4,
        AnalysisStatus = r.AnalysisStatus, ErrorMessage = r.ErrorMessage, PriceAsOfDate = r.PriceAsOfDate,
        EligibilityCapturedAt = r.EligibilityCapturedAt, Classification = r.Classification, WeeksInStage4 = r.WeeksInStage4,
        RSScore = r.RSScore, RSDelta1w = r.RSDelta1w, RSDelta2w = r.RSDelta2w, RSDelta3w = r.RSDelta3w,
        MomentumScore = r.MomentumScore, ROC1w = r.ROC1w, ROC2w = r.ROC2w, ROC3w = r.ROC3w,
        Quadrant = r.Quadrant, ADRatio = r.ADRatio, ADClassification = r.ADClassification, CreatedAt = r.CreatedAt
    };
}
