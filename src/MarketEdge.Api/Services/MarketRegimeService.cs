using System.Text;
using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IMarketRegimeService
{
    Task<MarketRegimeDto> GetRegimeAsync(string market, CancellationToken ct = default);
    Task<int> TriggerRefreshAsync(string market);
    Task<RegimeScheduleDto> GetScheduleAsync(string market);
    Task<RegimeScheduleDto> UpdateScheduleAsync(string market, UpdateRegimeScheduleRequest request);
}

/// <summary>
/// Thin reader over the fully-computed regime snapshot the worker persists (all §3.1 / §3.2 / §4
/// business logic lives in the <c>market_regime</c> worker job). This service only maps the latest
/// <c>{Market}RegimeSnapshots</c> row to a DTO, derives the read-time freshness flag (§8), and
/// orchestrates refresh/schedule — it computes no labels, scores, or combinations.
/// </summary>
public class MarketRegimeService : IMarketRegimeService
{
    private readonly MarketEdgeDbContext _db;
    private readonly QueueClient _queueClient;

    // A regime is "stale" when its newest as-of date is older than this many calendar days.
    private const int StaleAfterDays = 4;

    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web);

    public MarketRegimeService(MarketEdgeDbContext db, QueueClient queueClient)
    {
        _db = db;
        _queueClient = queueClient;
    }

    public async Task<MarketRegimeDto> GetRegimeAsync(string market, CancellationToken ct = default)
    {
        market = market.ToLowerInvariant();
        RegimeSnapshotBase? row = market switch
        {
            "india" => await _db.IndianRegimeSnapshots.AsNoTracking()
                .OrderByDescending(s => s.AsOfDate).FirstOrDefaultAsync(ct),
            "us" => await _db.USRegimeSnapshots.AsNoTracking()
                .OrderByDescending(s => s.AsOfDate).FirstOrDefaultAsync(ct),
            _ => null,
        };

        if (row == null)
            return Unavailable(market, "No regime snapshot computed yet — trigger a refresh.");

        var condition = new MarketConditionDto(
            row.ConditionLabel, row.ConditionTone, row.ConditionExplanation ?? string.Empty,
            row.BenchmarkSymbol, row.ConditionAsOfDate,
            row.ConditionClose, row.ConditionSma20, row.ConditionSma50, row.ConditionSma200,
            row.ConditionCloseVsSma20Pct, row.ConditionCloseVsSma50Pct, row.ConditionCloseVsSma200Pct,
            row.ConditionVolumeVsAvgPct, row.ConditionAvailable);

        var breadth = new MarketBreadthDto(
            row.BreadthLabel, row.BreadthTone, row.BreadthScore,
            row.BreadthPositiveSignals, row.BreadthAvailableSignals, row.EvaluatedCount,
            row.BreadthAsOfDate, row.BenchmarkSymbol, row.VolatilitySymbol,
            DeserializeSignals(row.SignalsJson), row.BreadthAvailable);

        var (stale, staleReason) = Freshness(row);

        return new MarketRegimeDto(
            market, row.Regime, row.RegimeLabel, row.RegimeTone, row.Posture ?? string.Empty,
            condition, breadth, row.AsOfDate, row.Available, stale, staleReason, row.IsIntraday);
    }

    private static MarketRegimeDto Unavailable(string market, string reason)
    {
        var condition = new MarketConditionDto(
            "Unavailable", "grey", reason, null, null,
            null, null, null, null, null, null, null, null, false);
        var breadth = new MarketBreadthDto(
            "Unavailable", "grey", null, 0, 0, 0, null, null, null,
            Array.Empty<BreadthSignalDto>(), false);
        return new MarketRegimeDto(
            market, "Unavailable", "Unavailable", "grey",
            "Insufficient data to determine market context.",
            condition, breadth, null, false, true, reason, false);
    }

    private static IReadOnlyList<BreadthSignalDto> DeserializeSignals(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return Array.Empty<BreadthSignalDto>();
        try
        {
            return JsonSerializer.Deserialize<List<BreadthSignalDto>>(json, JsonOptions)
                   ?? new List<BreadthSignalDto>();
        }
        catch (JsonException)
        {
            return Array.Empty<BreadthSignalDto>();
        }
    }

    /// <summary>Read-time freshness (§8). The worker cannot know the read-time "now", so the
    /// staleness age is the only thing derived here; availability/lag reasons come from the row.</summary>
    private static (bool Stale, string? Reason) Freshness(RegimeSnapshotBase row)
    {
        var reasons = new List<string>();
        if (!row.ConditionAvailable) reasons.Add("benchmark condition unavailable");
        if (!row.BreadthAvailable) reasons.Add("breadth unavailable");

        if (row.ConditionAsOfDate is DateOnly c && row.BreadthAsOfDate is DateOnly b && b < c)
            reasons.Add($"breadth snapshot ({b:yyyy-MM-dd}) is behind benchmark ({c:yyyy-MM-dd})");

        var ageDays = DateOnly.FromDateTime(DateTime.UtcNow).DayNumber - row.AsOfDate.DayNumber;
        if (ageDays > StaleAfterDays)
            reasons.Add($"newest data is {ageDays} days old");

        return reasons.Count > 0 ? (true, string.Join("; ", reasons)) : (false, null);
    }

    public async Task<int> TriggerRefreshAsync(string market)
    {
        var job = new JobRun
        {
            JobType = "market_regime",
            Market = market,
            WeekNumber = string.Empty,
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(new { triggeredBy = "manual" }),
            CreatedAt = DateTime.UtcNow
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        var message = JsonSerializer.Serialize(new
        {
            jobType = "market_regime",
            market,
            runId = job.Id,
            triggeredBy = "manual",
            timestamp = DateTime.UtcNow
        });
        await _queueClient.CreateIfNotExistsAsync();
        await _queueClient.SendMessageAsync(Convert.ToBase64String(Encoding.UTF8.GetBytes(message)));

        return job.Id;
    }

    public async Task<RegimeScheduleDto> GetScheduleAsync(string market)
    {
        market = market.ToLowerInvariant();
        var s = await _db.RegimeSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            s = new RegimeSchedule { Market = market, Enabled = true, HourLocal = 20, UpdatedAt = DateTime.UtcNow };
            _db.RegimeSchedules.Add(s);
            await _db.SaveChangesAsync();
        }
        return await ToDtoAsync(s);
    }

    public async Task<RegimeScheduleDto> UpdateScheduleAsync(string market, UpdateRegimeScheduleRequest request)
    {
        market = market.ToLowerInvariant();
        var s = await _db.RegimeSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            s = new RegimeSchedule { Market = market };
            _db.RegimeSchedules.Add(s);
        }
        s.Enabled = request.Enabled;
        if (request.HourLocal is int h && h is >= 0 and <= 23) s.HourLocal = h;
        s.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync();
        return await ToDtoAsync(s);
    }

    private async Task<RegimeScheduleDto> ToDtoAsync(RegimeSchedule s)
    {
        var lastRun = await _db.JobRuns
            .Where(j => j.JobType == "market_regime" && j.Market == s.Market)
            .OrderByDescending(j => j.Id)
            .Select(j => (DateTime?)(j.CompletedAt ?? j.StartedAt ?? j.CreatedAt))
            .FirstOrDefaultAsync();
        return new RegimeScheduleDto(s.Market, s.Enabled, s.HourLocal, s.LastEnqueuedAt, s.UpdatedAt, lastRun);
    }
}
