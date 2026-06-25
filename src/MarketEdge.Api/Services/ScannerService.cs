using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IScannerService
{
    Task<int> TriggerScannerAsync(string market, TriggerScannerRequest request);
    Task<List<ScannerInfoDto>> GetScannersAsync(string market);
    Task<List<DateTime>> GetScanDatesAsync(string market, string scannerName);
    Task<List<ScannerResultDto>> GetResultsAsync(string market, string scannerName, DateTime? scanDate);
    Task<ScannerScheduleDto> GetScheduleAsync(string market);
    Task<ScannerScheduleDto> UpdateScheduleAsync(string market, UpdateScannerScheduleRequest request);
}

public class ScannerService : IScannerService
{
    private readonly MarketEdgeDbContext _db;
    private readonly QueueClient _queueClient;

    public ScannerService(MarketEdgeDbContext db, QueueClient queueClient)
    {
        _db = db;
        _queueClient = queueClient;
    }

    private IQueryable<T> ResultSet<T>() where T : TechnicalScannerResultBase
        => typeof(T) == typeof(IndianTechnicalScannerResult)
            ? (IQueryable<T>)_db.IndianTechnicalScannerResults
            : (IQueryable<T>)_db.USTechnicalScannerResults;

    public async Task<int> TriggerScannerAsync(string market, TriggerScannerRequest request)
    {
        var universe = (request.Universe ?? "stage2").ToLowerInvariant();
        if (universe != "stage2" && universe != "all")
            throw new ArgumentException("Universe must be 'stage2' or 'all'");

        var scannerName = string.IsNullOrWhiteSpace(request.ScannerName) ? null : request.ScannerName.Trim();
        if (scannerName != null && !ScannerCatalog.IsKnown(market, scannerName))
            throw new ArgumentException($"Unknown scanner '{scannerName}' for market '{market}'");

        var job = new JobRun
        {
            JobType = "scanner",
            Market = market,
            WeekNumber = string.Empty,
            Status = "queued",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(new { scannerName, universe, backfill = request.Backfill }),
            CreatedAt = DateTime.UtcNow
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        var message = JsonSerializer.Serialize(new
        {
            jobType = "scanner",
            market,
            runId = job.Id,
            scannerName,
            universe,
            backfill = request.Backfill,
            triggeredBy = "manual",
            timestamp = DateTime.UtcNow
        });
        await _queueClient.CreateIfNotExistsAsync();
        await _queueClient.SendMessageAsync(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(message)));

        return job.Id;
    }

    public async Task<List<ScannerInfoDto>> GetScannersAsync(string market)
        => market == "india" ? await GetScanners<IndianTechnicalScannerResult>(market) : await GetScanners<USTechnicalScannerResult>(market);

    private async Task<List<ScannerInfoDto>> GetScanners<T>(string market) where T : TechnicalScannerResultBase
    {
        var grouped = await ResultSet<T>()
            .GroupBy(r => new { r.ScannerName, r.ScanDate })
            .Select(g => new { g.Key.ScannerName, g.Key.ScanDate, Count = g.Count() })
            .ToListAsync();

        var latest = grouped
            .GroupBy(g => g.ScannerName)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(x => x.ScanDate).First());

        return ScannerCatalog.ForMarket(market)
            .Select(c =>
            {
                latest.TryGetValue(c.Name, out var l);
                return new ScannerInfoDto(c.Name, c.Label, c.Family, c.ComingSoon,
                    l?.Count ?? 0, l?.ScanDate);
            })
            .ToList();
    }

    public async Task<List<DateTime>> GetScanDatesAsync(string market, string scannerName)
        => market == "india" ? await GetScanDates<IndianTechnicalScannerResult>(scannerName) : await GetScanDates<USTechnicalScannerResult>(scannerName);

    private async Task<List<DateTime>> GetScanDates<T>(string scannerName) where T : TechnicalScannerResultBase
    {
        return await ResultSet<T>()
            .Where(r => r.ScannerName == scannerName)
            .Select(r => r.ScanDate)
            .Distinct()
            .OrderByDescending(d => d)
            .Take(60)
            .ToListAsync();
    }

    public async Task<List<ScannerResultDto>> GetResultsAsync(string market, string scannerName, DateTime? scanDate)
        => market == "india" ? await GetResults<IndianTechnicalScannerResult>(scannerName, scanDate) : await GetResults<USTechnicalScannerResult>(scannerName, scanDate);

    private async Task<List<ScannerResultDto>> GetResults<T>(string scannerName, DateTime? scanDate) where T : TechnicalScannerResultBase
    {
        var q = ResultSet<T>().Where(r => r.ScannerName == scannerName);
        if (scanDate.HasValue)
        {
            q = q.Where(r => r.ScanDate == scanDate.Value.Date);
        }
        else
        {
            var max = await ResultSet<T>()
                .Where(r => r.ScannerName == scannerName)
                .MaxAsync(r => (DateTime?)r.ScanDate);
            if (max == null) return new List<ScannerResultDto>();
            q = q.Where(r => r.ScanDate == max.Value);
        }

        return await q
            .OrderByDescending(r => r.RsRating)
            .Select(r => new ScannerResultDto(
                r.Symbol, r.CompanyName, r.SectorName, r.Industry,
                r.ClosePrice, r.DayChangePct, r.Volume, r.RelVolume,
                r.RsRating, r.TriggerDetails))
            .ToListAsync();
    }

    public async Task<ScannerScheduleDto> GetScheduleAsync(string market)
    {
        var s = await _db.ScannerSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            // Default: enabled (the scheduler only fires during market hours anyway).
            s = new ScannerSchedule { Market = market, Enabled = true, IntervalMinutes = 15, UpdatedAt = DateTime.UtcNow };
            _db.ScannerSchedules.Add(s);
            await _db.SaveChangesAsync();
        }
        var lastRun = await GetLastRunAtAsync(market);
        return new ScannerScheduleDto(s.Market, s.Enabled, s.IntervalMinutes, s.LastEnqueuedAt, s.UpdatedAt, MarketHours.IsOpen(market), lastRun);
    }

    public async Task<ScannerScheduleDto> UpdateScheduleAsync(string market, UpdateScannerScheduleRequest request)
    {
        var s = await _db.ScannerSchedules.FirstOrDefaultAsync(x => x.Market == market);
        if (s == null)
        {
            s = new ScannerSchedule { Market = market };
            _db.ScannerSchedules.Add(s);
        }
        s.Enabled = request.Enabled;
        if (request.IntervalMinutes is int iv && iv > 0) s.IntervalMinutes = iv;
        s.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync();
        var lastRun = await GetLastRunAtAsync(market);
        return new ScannerScheduleDto(s.Market, s.Enabled, s.IntervalMinutes, s.LastEnqueuedAt, s.UpdatedAt, MarketHours.IsOpen(market), lastRun);
    }

    private async Task<DateTime?> GetLastRunAtAsync(string market)
    {
        var job = await _db.JobRuns
            .Where(j => j.JobType == "scanner" && j.Market == market)
            .OrderByDescending(j => j.Id)
            .Select(j => new { j.CompletedAt, j.StartedAt, j.CreatedAt })
            .FirstOrDefaultAsync();
        if (job == null) return null;
        return job.CompletedAt ?? job.StartedAt ?? job.CreatedAt;
    }
}

