using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IBreakoutsService
{
    Task<List<BreakoutDto>> GetBreakoutsAsync(string market, string? status, string? tradeType);
    Task<BreakoutStatsDto> GetBreakoutStatsAsync(string market);
    Task<BreakoutPnlSummaryDto> GetBreakoutPnlAsync(string market, DateTime from, DateTime to, string? tradeType);
    Task<BreakoutDayDto> GetBreakoutsByDayAsync(string market, DateTime date, string? tradeType);
    Task<List<NearPivotDto>> GetNearPivotsAsync(string market, string? tradeType, decimal maxDistancePct);
    Task<List<ScannerPerformanceDto>> GetScannerPerformanceAsync(string market);
    Task<List<ScoringWeightDto>> GetScoringWeightsAsync(string market);
    Task<ScoringWeightDto?> UpdateScoringWeightAsync(string market, int id, ScoringWeightUpdateDto update);
}

public class BreakoutsService : IBreakoutsService
{
    private readonly MarketEdgeDbContext _db;

    public BreakoutsService(MarketEdgeDbContext db) => _db = db;
    private IQueryable<T> BreakoutSet<T>() where T : BreakoutBase
        => typeof(T) == typeof(IndianBreakout)
            ? (IQueryable<T>)_db.IndianBreakouts
            : (IQueryable<T>)_db.USBreakouts;
    private IQueryable<T> NearPivotSet<T>() where T : NearPivotBase
        => typeof(T) == typeof(IndianNearPivot)
            ? (IQueryable<T>)_db.IndianNearPivots
            : (IQueryable<T>)_db.USNearPivots;

    public Task<List<BreakoutDto>> GetBreakoutsAsync(string market, string? status, string? tradeType)
        => market == "india" ? GetBreakouts<IndianBreakout>(status, tradeType) : GetBreakouts<USBreakout>(status, tradeType);

    private async Task<List<BreakoutDto>> GetBreakouts<T>(string? status, string? tradeType) where T : BreakoutBase
    {
        var q = BreakoutSet<T>().AsQueryable();
        if (!string.IsNullOrWhiteSpace(status)) q = q.Where(t => t.Status == status);
        if (!string.IsNullOrWhiteSpace(tradeType)) q = q.Where(t => t.TradeType == tradeType);
        var rows = await q.OrderByDescending(t => t.UpdatedAt).Take(500).ToListAsync();
        return rows.Select(ToBreakoutDto).ToList();
    }

    public Task<BreakoutStatsDto> GetBreakoutStatsAsync(string market)
        => market == "india" ? GetStats<IndianBreakout>() : GetStats<USBreakout>();

    private async Task<BreakoutStatsDto> GetStats<T>() where T : BreakoutBase
    {
        var rows = await BreakoutSet<T>()
            .Select(t => new { t.Status, t.TradeType, t.PnLPct, t.PnLAmount })
            .ToListAsync();
        var active = rows.Count(r => r.Status == "active");
        var closed = rows.Where(r => r.Status == "closed").ToList();
        var wins = closed.Count(r => r.PnLPct > 0);
        var losses = closed.Count(r => r.PnLPct <= 0);
        decimal? winRate = closed.Count > 0 ? Math.Round(100m * wins / closed.Count, 2) : null;
        var withPnl = rows.Where(r => r.PnLPct.HasValue).Select(r => r.PnLPct!.Value).ToList();
        decimal? avgPnl = withPnl.Count > 0 ? Math.Round(withPnl.Average(), 2) : null;

        decimal Sum(string status, string? type) => Math.Round(
            rows.Where(r => r.Status == status && (type == null || r.TradeType == type) && r.PnLAmount.HasValue)
                .Select(r => r.PnLAmount!.Value).DefaultIfEmpty(0).Sum(), 2);

        return new BreakoutStatsDto(active, closed.Count, wins, losses, winRate, avgPnl,
            Sum("closed", null), Sum("active", null),
            Sum("active", "swing"), Sum("closed", "swing"),
            Sum("active", "positional"), Sum("closed", "positional"));
    }

    public Task<BreakoutPnlSummaryDto> GetBreakoutPnlAsync(string market, DateTime from, DateTime to, string? tradeType)
        => market == "india"
            ? GetBreakoutPnl<IndianBreakout>(from, to, tradeType)
            : GetBreakoutPnl<USBreakout>(from, to, tradeType);

    private async Task<BreakoutPnlSummaryDto> GetBreakoutPnl<T>(DateTime from, DateTime to, string? tradeType) where T : BreakoutBase
    {
        var q = BreakoutSet<T>().AsQueryable();
        if (!string.IsNullOrWhiteSpace(tradeType)) q = q.Where(t => t.TradeType == tradeType);

        // Realized: closed trades whose exit falls in [from, to)
        var realized = await q.Where(t => t.Status == "closed" && t.ExitAt >= from && t.ExitAt < to)
            .Select(t => new { t.PnLPct, t.PnLAmount }).ToListAsync();
        var wins = realized.Count(r => r.PnLPct > 0);
        var losses = realized.Count(r => r.PnLPct <= 0);
        decimal? winRate = realized.Count > 0 ? Math.Round(100m * wins / realized.Count, 2) : null;
        var realizedPnl = Math.Round(
            realized.Where(r => r.PnLAmount.HasValue).Select(r => r.PnLAmount!.Value).DefaultIfEmpty(0).Sum(), 2);
        var pcts = realized.Where(r => r.PnLPct.HasValue).Select(r => r.PnLPct!.Value).ToList();
        decimal? avgPct = pcts.Count > 0 ? Math.Round(pcts.Average(), 2) : null;

        // Unrealized: all currently-open positions (period-independent snapshot)
        var open = await q.Where(t => t.Status == "active").Select(t => t.PnLAmount).ToListAsync();
        var openPnl = Math.Round(open.Where(v => v.HasValue).Select(v => v!.Value).DefaultIfEmpty(0).Sum(), 2);

        return new BreakoutPnlSummaryDto(from, to, tradeType, realized.Count, wins, losses, winRate,
            realizedPnl, avgPct, open.Count, openPnl);
    }

    public Task<BreakoutDayDto> GetBreakoutsByDayAsync(string market, DateTime date, string? tradeType)
        => market == "india"
            ? GetBreakoutsByDay<IndianBreakout>(date, tradeType)
            : GetBreakoutsByDay<USBreakout>(date, tradeType);

    private async Task<BreakoutDayDto> GetBreakoutsByDay<T>(DateTime date, string? tradeType) where T : BreakoutBase
    {
        var dayStart = date.Date;
        var dayEnd = dayStart.AddDays(1);
        var q = BreakoutSet<T>().AsQueryable();
        if (!string.IsNullOrWhiteSpace(tradeType)) q = q.Where(t => t.TradeType == tradeType);

        var entries = await q.Where(t => t.EntryAt >= dayStart && t.EntryAt < dayEnd)
            .OrderByDescending(t => t.EntryAt).ToListAsync();
        var exits = await q.Where(t => t.ExitAt >= dayStart && t.ExitAt < dayEnd)
            .OrderByDescending(t => t.ExitAt).ToListAsync();

        return new BreakoutDayDto(dayStart, tradeType,
            entries.Select(ToBreakoutDto).ToList(), exits.Select(ToBreakoutDto).ToList());
    }

    public Task<List<NearPivotDto>> GetNearPivotsAsync(string market, string? tradeType, decimal maxDistancePct)
        => market == "india" ? GetNearPivots<IndianNearPivot>(tradeType, maxDistancePct) : GetNearPivots<USNearPivot>(tradeType, maxDistancePct);

    private async Task<List<NearPivotDto>> GetNearPivots<T>(string? tradeType, decimal maxDistancePct) where T : NearPivotBase
    {
        var q = NearPivotSet<T>().AsQueryable();
        if (!string.IsNullOrWhiteSpace(tradeType)) q = q.Where(t => t.TradeType == tradeType);
        q = q.Where(t => t.DistancePct <= maxDistancePct);
        var rows = await q.OrderBy(t => t.DistancePct).Take(500).ToListAsync();
        return rows.Select(ToNearPivotDto).ToList();
    }

    private static NearPivotDto ToNearPivotDto(NearPivotBase t)
    {
        List<string> flagged;
        try
        {
            flagged = string.IsNullOrWhiteSpace(t.FlaggedScannersJson)
                ? new List<string>()
                : JsonSerializer.Deserialize<List<string>>(t.FlaggedScannersJson) ?? new List<string>();
        }
        catch { flagged = new List<string>(); }

        return new NearPivotDto(
            t.Id, t.Ticker, t.CompanyName, t.TradeType, t.Direction, flagged, t.ScannerHitCount,
            t.LastClose, t.PivotPrice, t.DistancePct, t.RelVolume, t.VolumeConfirmed, t.ScanDate, t.UpdatedAt);
    }

    public Task<List<ScannerPerformanceDto>> GetScannerPerformanceAsync(string market)
        => market == "india" ? GetScannerPerf<IndianBreakout>() : GetScannerPerf<USBreakout>();

    // Wilson lower bound (z=1.28, matching the worker scoring engine) of the realised win rate.
    private static decimal WilsonLb(int wins, int total)
    {
        if (total <= 0) return 0m;
        const double z = 1.28;
        double phat = (double)wins / total;
        double denom = 1.0 + z * z / total;
        double centre = phat + z * z / (2.0 * total);
        double margin = z * Math.Sqrt((phat * (1 - phat) + z * z / (4.0 * total)) / total);
        return (decimal)Math.Max(0.0, (centre - margin) / denom);
    }

    private async Task<List<ScannerPerformanceDto>> GetScannerPerf<T>() where T : BreakoutBase
    {
        var rows = await BreakoutSet<T>()
            .Where(t => t.EntryScanner != null)
            .Select(t => new { t.EntryScanner, t.Status, t.PnLPct, t.PnLAmount })
            .ToListAsync();

        return rows
            .GroupBy(r => r.EntryScanner!)
            .Select(g =>
            {
                var closed = g.Where(r => r.Status == "closed").ToList();
                var open = g.Where(r => r.Status == "active").ToList();
                // wins = closed-in-profit + active-in-profit (provisional), consistent with worker
                int wins = g.Count(r => (r.Status == "closed" || r.Status == "active") && r.PnLPct > 0);
                int losses = closed.Count(r => r.PnLPct <= 0);
                int total = g.Count(r => r.Status == "closed" || (r.Status == "active" && r.PnLPct != null));
                var withPnl = g.Where(r => r.PnLPct.HasValue).Select(r => r.PnLPct!.Value).ToList();
                decimal? avgPnl = withPnl.Count > 0 ? Math.Round(withPnl.Average(), 2) : null;
                decimal Sum(IEnumerable<decimal?> xs) => Math.Round(xs.Where(x => x.HasValue).Select(x => x!.Value).DefaultIfEmpty(0).Sum(), 2);
                return new ScannerPerformanceDto(
                    g.Key, g.Count(), closed.Count, open.Count, wins, losses,
                    total > 0 ? Math.Round(100m * wins / total, 2) : null,
                    Math.Round(100m * WilsonLb(wins, total), 1),
                    avgPnl,
                    Sum(closed.Select(r => r.PnLAmount)),
                    Sum(open.Select(r => r.PnLAmount)));
            })
            .OrderByDescending(d => d.ReliabilityScore)
            .ThenByDescending(d => d.Trades)
            .ToList();
    }
    private static BreakoutDto ToBreakoutDto(BreakoutBase t)
    {
        List<string> flagged;
        try
        {
            flagged = string.IsNullOrWhiteSpace(t.FlaggedScannersJson)
                ? new List<string>()
                : JsonSerializer.Deserialize<List<string>>(t.FlaggedScannersJson) ?? new List<string>();
        }
        catch { flagged = new List<string>(); }

        return new BreakoutDto(
            t.Id, t.Ticker, t.CompanyName, t.TradeType, t.Direction, t.Status,
            t.EntryScanner, flagged, t.ScannerHitCount, t.EntryAt, t.EntryPrice, t.Qty,
            t.InitialStop, t.CurrentStop, t.StopBasis, t.RiskPerShare, t.MovedToBe,
            t.LastPrice, t.PnLPct, t.PnLAmount, t.MfePct, t.MaePct, t.ExitAt, t.ExitPrice, t.ExitReason,
            t.ConfidenceScore, t.ConfidenceRationaleJson, t.UpdatedAt);
    }

    public async Task<List<ScoringWeightDto>> GetScoringWeightsAsync(string market)
    {
        var mk = market.ToLowerInvariant();
        var rows = await _db.ScoringWeights.Where(w => w.Market == mk)
            .OrderBy(w => w.Category).ThenBy(w => w.ComponentKey).ToListAsync();
        return rows.Select(w => new ScoringWeightDto(
            w.Id, w.Market, w.Category, w.ComponentKey, w.Weight, w.SeedWeight,
            w.Wins, w.Losses, w.ManualOverride, w.UpdatedAt)).ToList();
    }

    public async Task<ScoringWeightDto?> UpdateScoringWeightAsync(string market, int id, ScoringWeightUpdateDto update)
    {
        var mk = market.ToLowerInvariant();
        var w = await _db.ScoringWeights.FirstOrDefaultAsync(x => x.Id == id && x.Market == mk);
        if (w == null) return null;
        if (update.Weight.HasValue)
        {
            // Editing a weight pins it: clamp to 0..1 and freeze auto-adaptation.
            w.Weight = Math.Clamp(update.Weight.Value, 0m, 1m);
            w.ManualOverride = true;
        }
        if (update.ManualOverride.HasValue)
            w.ManualOverride = update.ManualOverride.Value;
        w.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync();
        return new ScoringWeightDto(w.Id, w.Market, w.Category, w.ComponentKey, w.Weight, w.SeedWeight,
            w.Wins, w.Losses, w.ManualOverride, w.UpdatedAt);
    }
}
