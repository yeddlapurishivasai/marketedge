using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IScoresService
{
    Task<List<StockScoreDto>> GetScoresAsync(string market, string profile, string? side, int take);
    Task<StockScoreDto?> GetScoreAsync(string market, string ticker);
    Task<List<TradeDto>> GetTradesAsync(string market, string? status, string? tradeType);
    Task<TradeStatsDto> GetTradeStatsAsync(string market);
}

public class ScoresService : IScoresService
{
    private readonly MarketEdgeDbContext _db;

    public ScoresService(MarketEdgeDbContext db) => _db = db;

    private IQueryable<T> ScoreSet<T>() where T : StockScoresBase
        => typeof(T) == typeof(IndianStockScores)
            ? (IQueryable<T>)_db.IndianStockScores
            : (IQueryable<T>)_db.USStockScores;

    private IQueryable<T> TradeSet<T>() where T : TradeBase
        => typeof(T) == typeof(IndianTrade)
            ? (IQueryable<T>)_db.IndianTrades
            : (IQueryable<T>)_db.USTrades;

    public Task<List<StockScoreDto>> GetScoresAsync(string market, string profile, string? side, int take)
        => market == "india"
            ? GetScores<IndianStockScores>(profile, side, take)
            : GetScores<USStockScores>(profile, side, take);

    private async Task<List<StockScoreDto>> GetScores<T>(string profile, string? side, int take) where T : StockScoresBase
    {
        var q = ScoreSet<T>().AsQueryable();
        var positional = profile == "positional";

        if (!string.IsNullOrWhiteSpace(side))
        {
            q = positional ? q.Where(s => s.PositionalSide == side) : q.Where(s => s.SwingSide == side);
        }

        q = positional
            ? q.OrderByDescending(s => s.PositionalScore)
            : q.OrderByDescending(s => s.SwingScore);

        var rows = await q.Take(Math.Clamp(take, 1, 1000)).ToListAsync();
        return rows.Select(ToDto).ToList();
    }

    public Task<StockScoreDto?> GetScoreAsync(string market, string ticker)
        => market == "india" ? GetScore<IndianStockScores>(ticker) : GetScore<USStockScores>(ticker);

    private async Task<StockScoreDto?> GetScore<T>(string ticker) where T : StockScoresBase
    {
        var row = await ScoreSet<T>().FirstOrDefaultAsync(s => s.Ticker == ticker);
        return row == null ? null : ToDto(row);
    }

    public Task<List<TradeDto>> GetTradesAsync(string market, string? status, string? tradeType)
        => market == "india" ? GetTrades<IndianTrade>(status, tradeType) : GetTrades<USTrade>(status, tradeType);

    private async Task<List<TradeDto>> GetTrades<T>(string? status, string? tradeType) where T : TradeBase
    {
        var q = TradeSet<T>().AsQueryable();
        if (!string.IsNullOrWhiteSpace(status)) q = q.Where(t => t.Status == status);
        if (!string.IsNullOrWhiteSpace(tradeType)) q = q.Where(t => t.TradeType == tradeType);
        var rows = await q.OrderByDescending(t => t.UpdatedAt).Take(500).ToListAsync();
        return rows.Select(ToTradeDto).ToList();
    }

    public Task<TradeStatsDto> GetTradeStatsAsync(string market)
        => market == "india" ? GetStats<IndianTrade>() : GetStats<USTrade>();

    private async Task<TradeStatsDto> GetStats<T>() where T : TradeBase
    {
        var rows = await TradeSet<T>()
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

        return new TradeStatsDto(active, closed.Count, wins, losses, winRate, avgPnl,
            Sum("closed", null), Sum("active", null),
            Sum("active", "swing"), Sum("closed", "swing"),
            Sum("active", "positional"), Sum("closed", "positional"));
    }

    private static StockScoreDto ToDto(StockScoresBase s) => new(
        s.Ticker, s.AsOfDate, s.UpsideEpsPct, s.UpsideAnalystPct, s.TargetPrice,
        s.AiUpsidePct, s.AiDownsidePct, s.AiRationale,
        s.SwingScore, s.SwingSide, s.SwingBull, s.SwingBear,
        s.PositionalScore, s.PositionalSide, s.PositionalBull, s.PositionalBear,
        s.FundFreshnessDecay, s.DaysSinceEarnings, s.ScannerHits, s.IsFno, s.ComponentsJson, s.ScoredAt);

    private static TradeDto ToTradeDto(TradeBase t)
    {
        List<string> flagged;
        try
        {
            flagged = string.IsNullOrWhiteSpace(t.FlaggedScannersJson)
                ? new List<string>()
                : JsonSerializer.Deserialize<List<string>>(t.FlaggedScannersJson) ?? new List<string>();
        }
        catch { flagged = new List<string>(); }

        return new TradeDto(
            t.Id, t.Ticker, t.CompanyName, t.TradeType, t.Direction, t.Status,
            t.EntryScanner, flagged, t.ScannerHitCount, t.EntryAt, t.EntryPrice, t.Qty,
            t.InitialStop, t.CurrentStop, t.StopBasis, t.RiskPerShare, t.MovedToBe,
            t.LastPrice, t.PnLPct, t.PnLAmount, t.MfePct, t.MaePct, t.ExitAt, t.ExitPrice, t.ExitReason, t.UpdatedAt);
    }
}
