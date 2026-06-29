using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace MarketEdge.Api.Services;

public interface IFundamentalsService
{
    Task<IReadOnlyList<FundamentalRow>> ListAsync(string market, string? scanner);
    Task<IReadOnlyList<FundamentalIdeaRow>> ListIdeasAsync(string market, string? side = null);
    Task<FundamentalDetail?> GetAsync(string market, string symbol);
    Task<bool> SaveNoteAsync(string market, string symbol, string? noteText);
}

public class FundamentalsService : IFundamentalsService
{
    private readonly MarketEdgeDbContext _db;
    public FundamentalsService(MarketEdgeDbContext db) => _db = db;

    private static bool IsUs(string market) => market == "us";

    public async Task<IReadOnlyList<FundamentalRow>> ListAsync(string market, string? scanner)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow);

        List<EarningsFundamentalsBase> rows = IsUs(market)
            ? (await _db.USEarningsFundamentals.ToListAsync()).Cast<EarningsFundamentalsBase>().ToList()
            : (await _db.IndianEarningsFundamentals.ToListAsync()).Cast<EarningsFundamentalsBase>().ToList();

        rows = ApplyScanner(rows, scanner, today).ToList();

        // Join to catalog for company / sector context.
        var catalog = await LoadCatalogAsync(market);

        var result = rows
            .Select(r =>
            {
                catalog.TryGetValue(r.Ticker.ToUpperInvariant(), out var meta);
                return ToRow(r, meta, today);
            })
            .OrderBy(r => r.LastEarningsDate == null ? 1 : 0)
            .ThenByDescending(r => r.LastEarningsDate)
            .ThenBy(r => r.Symbol)
            .ToList();

        return result;
    }

    public async Task<IReadOnlyList<FundamentalIdeaRow>> ListIdeasAsync(string market, string? side = null)
    {
        List<FundamentalIdeaBase> rows = IsUs(market)
            ? (await _db.USFundamentalIdeas.Where(i => !i.IsStale).ToListAsync())
                .Cast<FundamentalIdeaBase>().ToList()
            : (await _db.IndianFundamentalIdeas.Where(i => !i.IsStale).ToListAsync())
                .Cast<FundamentalIdeaBase>().ToList();

        var catalog = await LoadCatalogAsync(market);
        var stage2 = await LoadStage2Async(market);
        var want = string.IsNullOrWhiteSpace(side) ? null : side.Trim().ToLowerInvariant();

        var mapped = rows
            .Select(r =>
            {
                catalog.TryGetValue(r.Ticker.ToUpperInvariant(), out var meta);
                var sym = meta?.Symbol ?? r.Ticker;
                bool? isStage2 = stage2.TryGetValue(sym.ToUpperInvariant(), out var s2) ? s2 : (bool?)null;

                // Direction/side + the bearish short mirror are computed once by the worker
                // (confidence.py) and embedded in ConfidenceRationaleJson; the API only reads
                // them back here — no fundamental weights or scoring math live in C#.
                var pj = ParseIdeaRationale(r.ConfidenceRationaleJson);

                return new FundamentalIdeaRow(
                    meta?.Symbol ?? r.Ticker,
                    meta?.CompanyName ?? r.Ticker,
                    meta?.BroadSector,
                    meta?.Industry,
                    r.EarningsDate,
                    r.EpsBeatPct,
                    r.OpmExpansionYoyPct,
                    r.OperatingProfitExpansionYoyPct,
                    r.LatestRatingFirm,
                    r.LatestRatingGrade,
                    r.LatestRatingAction,
                    r.LatestRatingDate,
                    r.TargetLowPrice,
                    r.TargetMeanPrice,
                    r.TargetHighPrice,
                    r.EpsBeatConfidence,
                    r.OpmExpansionConfidence,
                    r.OperatingProfitExpansionConfidence,
                    r.AnalystRatingConfidence,
                    r.TargetUpsideConfidence,
                    r.FundamentalConfidence,
                    r.TechnicalConfidence,
                    r.OverallConfidence,
                    r.DaysSinceEarnings,
                    r.DaysSinceRating,
                    r.ConfidenceRationaleJson,
                    isStage2,
                    pj.Direction,
                    pj.Side,
                    pj.EpsBeatShort,
                    pj.OpmExpansionShort,
                    pj.OpExpansionShort,
                    pj.RatingShort,
                    pj.FundamentalShort,
                    pj.OverallShort,
                    pj.ShortJson,
                    r.UpdatedAt);
            })
            .Where(r => want is null || want == "all" || r.Side == want)
            .OrderByDescending(r => r.EarningsDate)
            .ThenBy(r => r.Symbol)
            .ToList();

        return mapped;
    }

    // Latest-run Stage-2 flag per symbol, used to tag fundamental ideas for the Long
    // (Stage-2) filter. Keyed by the catalog symbol (upper-cased).
    private async Task<Dictionary<string, bool>> LoadStage2Async(string market)
    {
        if (IsUs(market))
        {
            var maxRun = await _db.USStageAnalysisResults.MaxAsync(s => (int?)s.RunId);
            if (maxRun == null) return new();
            var rows = await _db.USStageAnalysisResults.Where(s => s.RunId == maxRun)
                .Select(s => new { s.Symbol, s.IsStage2 }).ToListAsync();
            return rows.GroupBy(s => s.Symbol.ToUpperInvariant())
                       .ToDictionary(g => g.Key, g => g.Any(x => x.IsStage2));
        }
        else
        {
            var maxRun = await _db.IndianStageAnalysisResults.MaxAsync(s => (int?)s.RunId);
            if (maxRun == null) return new();
            var rows = await _db.IndianStageAnalysisResults.Where(s => s.RunId == maxRun)
                .Select(s => new { s.Symbol, s.IsStage2 }).ToListAsync();
            return rows.GroupBy(s => s.Symbol.ToUpperInvariant())
                       .ToDictionary(g => g.Key, g => g.Any(x => x.IsStage2));
        }
    }

    public async Task<FundamentalDetail?> GetAsync(string market, string symbol)
    {
        var upper = symbol.Trim().ToUpperInvariant();
        var today = DateOnly.FromDateTime(DateTime.UtcNow);

        EarningsFundamentalsBase? row = IsUs(market)
            ? await _db.USEarningsFundamentals.FirstOrDefaultAsync(e => e.Ticker.ToUpper() == upper)
            : await _db.IndianEarningsFundamentals.FirstOrDefaultAsync(e => e.Ticker.ToUpper() == upper);

        var catalog = await LoadCatalogAsync(market, upper);
        catalog.TryGetValue(upper, out var meta);

        // A stock can have a note even without an ingested earnings row; still allow detail.
        if (row == null && meta == null) return null;

        StockNoteBase? note = IsUs(market)
            ? await _db.USStockNotes.FirstOrDefaultAsync(n => n.Ticker.ToUpper() == upper)
            : await _db.IndianStockNotes.FirstOrDefaultAsync(n => n.Ticker.ToUpper() == upper);

        StockSignalsBase? signalsRow = IsUs(market)
            ? await _db.USStockSignals.FirstOrDefaultAsync(s => s.Ticker.ToUpper() == upper)
            : await _db.IndianStockSignals.FirstOrDefaultAsync(s => s.Ticker.ToUpper() == upper);

        var dtoRow = row != null
            ? ToRow(row, meta, today)
            : new FundamentalRow(
                meta!.Symbol, meta.CompanyName, meta.BroadSector, meta.Industry,
                today, null, null, null, null, null, null, null, null, null, null, null,
                null, null, null, null, null, null, null, null, null, null, false, Array.Empty<EpsQuarter>());

        return new FundamentalDetail(dtoRow, note?.NoteText, ToSignals(signalsRow));
    }

    private static FundamentalSignals? ToSignals(StockSignalsBase? s)
    {
        if (s == null) return null;
        IReadOnlyList<SignalNewsItem> news = Array.Empty<SignalNewsItem>();
        if (!string.IsNullOrWhiteSpace(s.NewsJson))
        {
            try
            {
                news = System.Text.Json.JsonSerializer.Deserialize<List<SignalNewsItem>>(
                    s.NewsJson,
                    new System.Text.Json.JsonSerializerOptions
                    {
                        PropertyNameCaseInsensitive = true,
                    }) ?? new List<SignalNewsItem>();
            }
            catch (System.Text.Json.JsonException)
            {
                news = Array.Empty<SignalNewsItem>();
            }
        }
        return new FundamentalSignals(
            s.CapexCwip, s.CapexCwipPrevQ, s.CapexChangePct, s.CapexTrend, s.CapexAsOf,
            news.SelectMany(n => n.Tags ?? Array.Empty<string>()).Distinct().OrderBy(t => t).ToList(),
            news, s.SignalsText, s.UpdatedAt);
    }

    public async Task<bool> SaveNoteAsync(string market, string symbol, string? noteText)
    {
        var upper = symbol.Trim().ToUpperInvariant();

        // Only allow notes for catalogued symbols (FK-backed by the Tickers table).
        var exists = IsUs(market)
            ? await _db.USTickers.AnyAsync(t => t.Ticker.ToUpper() == upper)
            : await _db.IndianTickers.AnyAsync(t => t.Ticker.ToUpper() == upper);
        if (!exists) return false;

        if (IsUs(market))
        {
            var note = await _db.USStockNotes.FirstOrDefaultAsync(n => n.Ticker.ToUpper() == upper);
            if (note == null)
            {
                var ticker = await _db.USTickers.Where(t => t.Ticker.ToUpper() == upper)
                    .Select(t => t.Ticker).FirstAsync();
                _db.USStockNotes.Add(new USStockNote { Ticker = ticker, NoteText = noteText, UpdatedAt = DateTime.UtcNow });
            }
            else
            {
                note.NoteText = noteText;
                note.UpdatedAt = DateTime.UtcNow;
            }
        }
        else
        {
            var note = await _db.IndianStockNotes.FirstOrDefaultAsync(n => n.Ticker.ToUpper() == upper);
            if (note == null)
            {
                var ticker = await _db.IndianTickers.Where(t => t.Ticker.ToUpper() == upper)
                    .Select(t => t.Ticker).FirstAsync();
                _db.IndianStockNotes.Add(new IndianStockNote { Ticker = ticker, NoteText = noteText, UpdatedAt = DateTime.UtcNow });
            }
            else
            {
                note.NoteText = noteText;
                note.UpdatedAt = DateTime.UtcNow;
            }
        }

        await _db.SaveChangesAsync();
        return true;
    }

    private static IEnumerable<EarningsFundamentalsBase> ApplyScanner(
        IEnumerable<EarningsFundamentalsBase> rows, string? scanner, DateOnly today)
    {
        return (scanner ?? string.Empty).Trim().ToLowerInvariant() switch
        {
            "earnings_increasing" => rows.Where(r => r.EarningsIncreasing == true),
            "margin_expanding" => rows.Where(r => r.OpmTrend == "expanding"),
            "operating_profit_expanding" => rows.Where(r => r.OperatingProfitTrend == "expanding"),
            "recently_announced" => rows.Where(r =>
                r.LastEarningsDate != null && r.LastEarningsDate >= today.AddDays(-7)),
            _ => rows,
        };
    }

    private async Task<Dictionary<string, StockMeta>> LoadCatalogAsync(string market, string? onlyUpper = null)
    {
        if (IsUs(market))
        {
            var q = _db.USStocks.AsQueryable();
            if (onlyUpper != null) q = q.Where(s => s.Symbol.ToUpper() == onlyUpper);
            var list = await q
                .Select(s => new StockMeta(s.Symbol, s.CompanyName, s.BroadSector, s.Sector.SectorName))
                .ToListAsync();
            return list.ToDictionary(m => m.Symbol.ToUpperInvariant(), m => m);
        }
        else
        {
            var q = _db.IndianStocks.AsQueryable();
            if (onlyUpper != null) q = q.Where(s => s.Symbol.ToUpper() == onlyUpper);
            var list = await q
                .Select(s => new StockMeta(s.Symbol, s.CompanyName, s.BroadSector, s.Sector.SectorName))
                .ToListAsync();
            return list.ToDictionary(m => m.Symbol.ToUpperInvariant(), m => m);
        }
    }

    private static FundamentalRow ToRow(EarningsFundamentalsBase r, StockMeta? meta, DateOnly today)
    {
        var announcedRecent = r.LastEarningsDate != null && r.LastEarningsDate >= today.AddDays(-7);

        var epsHistory = new List<EpsQuarter>();
        void AddQuarter(DateOnly? date, decimal? est, decimal? act, decimal? surprise)
        {
            if (date != null || act != null || est != null)
                epsHistory.Add(new EpsQuarter(date, est, act, surprise));
        }
        AddQuarter(r.EpsQ1Date, r.EpsQ1Estimate, r.EpsQ1Actual, r.EpsQ1SurprisePct);
        AddQuarter(r.EpsQ2Date, r.EpsQ2Estimate, r.EpsQ2Actual, r.EpsQ2SurprisePct);
        AddQuarter(r.EpsQ3Date, r.EpsQ3Estimate, r.EpsQ3Actual, r.EpsQ3SurprisePct);
        AddQuarter(r.EpsQ4Date, r.EpsQ4Estimate, r.EpsQ4Actual, r.EpsQ4SurprisePct);

        return new FundamentalRow(
            meta?.Symbol ?? r.Ticker,
            meta?.CompanyName ?? r.Ticker,
            meta?.BroadSector,
            meta?.Industry,
            r.AsOfDate,
            r.LatestQuarterEnd,
            r.Revenue,
            r.RevenueGrowthYoyPct,
            r.OperatingProfit,
            r.Opm,
            r.OpmPrevQ,
            r.OpmYoyQ,
            r.OpmTrend,
            r.NetProfit,
            r.NetMarginPct,
            r.EarningsGrowthYoyPct,
            r.EarningsGrowthQoqPct,
            r.EarningsIncreasing,
            r.OperatingProfitTrend,
            r.LastEarningsDate,
            r.PrevEarningsDate,
            r.NextEarningsDate,
            r.LastReportedEps,
            r.LastEpsSurprisePct,
            r.TrailingPe,
            r.ForwardPe,
            announcedRecent,
            epsHistory);
    }

    private record StockMeta(string Symbol, string CompanyName, string? BroadSector, string Industry);

    // Direction/side + bearish short-mirror scores read straight from the worker-produced
    // rationale JSON (confidence.py). Old rows lacking these keys parse to all-null, which
    // simply hides them from the long/short tabs until the next fundamentals refresh.
    private readonly record struct IdeaRationale(
        int? Direction, string? Side,
        decimal? EpsBeatShort, decimal? OpmExpansionShort, decimal? OpExpansionShort,
        decimal? RatingShort, decimal? FundamentalShort, decimal? OverallShort,
        string? ShortJson);

    private static IdeaRationale ParseIdeaRationale(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return default;
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            int? dir = ReadInt(root, "direction");
            string? side = root.TryGetProperty("side", out var s) && s.ValueKind == JsonValueKind.String
                ? s.GetString() : null;
            if (!root.TryGetProperty("short", out var sh) || sh.ValueKind != JsonValueKind.Object)
                return new IdeaRationale(dir, side, null, null, null, null, null, null, null);
            return new IdeaRationale(
                dir, side,
                ReadDec(sh, "epsBeat"), ReadDec(sh, "opmExpansion"), ReadDec(sh, "opExpansion"),
                ReadDec(sh, "rating"), ReadDec(sh, "fundamental"), ReadDec(sh, "overall"),
                sh.GetRawText());
        }
        catch (JsonException)
        {
            return default;
        }
    }

    private static int? ReadInt(JsonElement el, string name) =>
        el.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : (int?)null;

    private static decimal? ReadDec(JsonElement el, string name) =>
        el.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : (decimal?)null;
}
