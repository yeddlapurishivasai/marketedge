using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IAiInvestmentAnalysisStore
{
    Task<AiInvestmentAnalysisDetail> SaveAsync(SaveAiInvestmentAnalysis analysis);
    Task<IReadOnlyList<AiInvestmentAnalysisHistoryItem>> GetHistoryAsync(string market, string ticker);
    Task<IReadOnlyList<AiInvestmentAnalysisHistoryItem>> GetMarketHistoryAsync(string market, int maxRows);
    Task<AiInvestmentAnalysisDetail?> GetAsync(string market, string ticker, string weekNumber);
}

public class AiInvestmentAnalysisStore : IAiInvestmentAnalysisStore
{
    private const int RetainedWeeks = 10;
    private readonly MarketEdgeDbContext _db;

    public AiInvestmentAnalysisStore(MarketEdgeDbContext db) => _db = db;

    public async Task<AiInvestmentAnalysisDetail> SaveAsync(SaveAiInvestmentAnalysis analysis)
    {
        ValidateJson(analysis.InputJson);
        ValidateJson(analysis.EarningsCallJson);
        ValidateJson(analysis.CorporateFilingsJson);
        ValidateJson(analysis.AnalysisJson);

        var market = analysis.Market.Trim().ToLowerInvariant();
        var ticker = analysis.Ticker.Trim().ToUpperInvariant();
        var now = DateTime.UtcNow;
        var entity = await _db.AiStockAnalysisResults.FirstOrDefaultAsync(row =>
            row.Market == market && row.Ticker == ticker && row.WeekNumber == analysis.WeekNumber);

        if (entity == null)
        {
            entity = new AiStockAnalysisResult
            {
                Market = market,
                Ticker = ticker,
                WeekNumber = analysis.WeekNumber,
                CreatedAt = now
            };
            _db.AiStockAnalysisResults.Add(entity);
        }

        entity.SchemaVersion = analysis.SchemaVersion;
        entity.SkillVersion = analysis.SkillVersion;
        entity.Model = analysis.Model;
        entity.InputJson = analysis.InputJson;
        entity.EarningsCallJson = analysis.EarningsCallJson;
        entity.CorporateFilingsJson = analysis.CorporateFilingsJson;
        entity.AnalysisJson = analysis.AnalysisJson;
        entity.InputTokens = analysis.Cost.InputTokens;
        entity.CachedInputTokens = analysis.Cost.CachedInputTokens;
        entity.OutputTokens = analysis.Cost.OutputTokens;
        entity.InputCostUsd = analysis.Cost.InputCostUsd;
        entity.OutputCostUsd = analysis.Cost.OutputCostUsd;
        entity.ToolCallsCostUsd = analysis.Cost.ToolCallsCostUsd;
        entity.TotalCostUsd = Money(
            entity.InputCostUsd + entity.OutputCostUsd + entity.ToolCallsCostUsd);
        entity.Projected1000TickersCostUsd = Money(entity.TotalCostUsd * 1000m);
        entity.UpdatedAt = now;
        await _db.SaveChangesAsync();

        var obsolete = await _db.AiStockAnalysisResults
            .Where(row => row.Market == market && row.Ticker == ticker)
            .OrderByDescending(row => row.WeekNumber)
            .Skip(RetainedWeeks)
            .ToListAsync();
        if (obsolete.Count > 0)
        {
            _db.AiStockAnalysisResults.RemoveRange(obsolete);
            await _db.SaveChangesAsync();
        }

        return ToDetail(entity);
    }

    public async Task<IReadOnlyList<AiInvestmentAnalysisHistoryItem>> GetHistoryAsync(
        string market, string ticker)
    {
        var rows = await _db.AiStockAnalysisResults.AsNoTracking()
            .Where(row => row.Market == market.ToLower() && row.Ticker == ticker.ToUpper())
            .OrderByDescending(row => row.WeekNumber)
            .Take(RetainedWeeks)
            .ToListAsync();
        return rows.Select(ToHistoryItem).ToList();
    }

    public async Task<IReadOnlyList<AiInvestmentAnalysisHistoryItem>> GetMarketHistoryAsync(
        string market, int maxRows)
    {
        var take = Math.Clamp(maxRows, 1, 1000);
        var normalizedMarket = market.Trim().ToLowerInvariant();
        var rows = await _db.AiStockAnalysisResults.AsNoTracking()
            .Where(row => row.Market == normalizedMarket)
            .OrderByDescending(row => row.WeekNumber)
            .ThenBy(row => row.Ticker)
            .Take(take)
            .ToListAsync();
        return rows.Select(ToHistoryItem).ToList();
    }

    public async Task<AiInvestmentAnalysisDetail?> GetAsync(
        string market, string ticker, string weekNumber)
    {
        var entity = await _db.AiStockAnalysisResults.AsNoTracking().FirstOrDefaultAsync(row =>
            row.Market == market.ToLower() && row.Ticker == ticker.ToUpper()
                && row.WeekNumber == weekNumber);
        return entity == null ? null : ToDetail(entity);
    }

    private static AiInvestmentAnalysisDetail ToDetail(AiStockAnalysisResult row) => new(
        ToHistoryItem(row),
        ParseJson(row.InputJson),
        ParseJson(row.EarningsCallJson),
        ParseJson(row.CorporateFilingsJson),
        ParseJson(row.AnalysisJson));

    private static AiInvestmentAnalysisHistoryItem ToHistoryItem(AiStockAnalysisResult row) => new(
        row.Id, row.Market, row.Ticker, row.WeekNumber,
        row.SchemaVersion, row.SkillVersion, row.Model,
        ParseSummary(row.AnalysisJson),
        new AiAnalysisCostDto(
            row.InputTokens, row.CachedInputTokens, row.OutputTokens,
            row.InputCostUsd, row.OutputCostUsd, row.ToolCallsCostUsd,
            row.TotalCostUsd, row.Projected1000TickersCostUsd),
        row.CreatedAt, row.UpdatedAt);

    private static AiInvestmentAnalysisSummary ParseSummary(string analysisJson)
    {
        try
        {
            using var document = JsonDocument.Parse(analysisJson);
            var root = document.RootElement;
            var valuation = ReadString(root, "valuation", "assessment");
            var shortTerm = ReadString(root, "actions", "shortTerm", "action");
            var longTerm = ReadString(root, "actions", "longTerm", "action");

            decimal? ReturnFor(string horizon)
            {
                if (!root.TryGetProperty("priceScenarios", out var scenarios)
                    || scenarios.ValueKind != JsonValueKind.Array)
                    return null;
                foreach (var scenario in scenarios.EnumerateArray())
                {
                    if (scenario.TryGetProperty("horizon", out var value)
                        && value.GetString() == horizon
                        && scenario.TryGetProperty("base", out var baseCase)
                        && baseCase.TryGetProperty("returnPct", out var returnPct)
                        && returnPct.ValueKind == JsonValueKind.Number
                        && returnPct.TryGetDecimal(out var result))
                        return result;
                }
                return null;
            }

            return new AiInvestmentAnalysisSummary(
                valuation,
                ReturnFor("3m"),
                ReturnFor("6m"),
                ReturnFor("1y"),
                ReturnFor("2y"),
                shortTerm,
                longTerm);
        }
        catch (JsonException)
        {
            return new AiInvestmentAnalysisSummary(null, null, null, null, null, null, null);
        }
    }

    private static string? ReadString(JsonElement element, params string[] path)
    {
        var current = element;
        foreach (var segment in path)
        {
            if (current.ValueKind != JsonValueKind.Object
                || !current.TryGetProperty(segment, out current))
                return null;
        }
        return current.ValueKind == JsonValueKind.String ? current.GetString() : null;
    }

    private static void ValidateJson(string json)
    {
        using var _ = JsonDocument.Parse(json);
    }

    private static JsonElement ParseJson(string json)
    {
        using var document = JsonDocument.Parse(json);
        return document.RootElement.Clone();
    }

    private static decimal Money(decimal amount) =>
        decimal.Round(amount, 8, MidpointRounding.AwayFromZero);
}