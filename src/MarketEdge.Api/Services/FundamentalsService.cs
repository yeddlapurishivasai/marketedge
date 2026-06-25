using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IFundamentalsService
{
    Task<IReadOnlyList<FundamentalRow>> ListAsync(string market, string? scanner);
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
                null, null, null, null, null, null, null, false);

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
            s.CapexCwip, s.CapexCwipPrevQ, s.CapexChangePct, s.CapexTrend,
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
            r.LastReportedEps,
            r.LastEpsSurprisePct,
            announcedRecent);
    }

    private record StockMeta(string Symbol, string CompanyName, string? BroadSector, string Industry);
}
