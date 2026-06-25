using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface ILookupService
{
    Task<IReadOnlyList<LookupCandidate>> SearchAsync(string market, string q);
    Task<StockLookupDetail?> GetDetailAsync(string market, string symbol);
    Task<IReadOnlyList<LookupBarDto>> GetBarsAsync(string market, string symbol, string timeframe);
}

public class LookupService : ILookupService
{
    private readonly MarketEdgeDbContext _db;
    public LookupService(MarketEdgeDbContext db) => _db = db;

    private static bool IsUs(string market) => market == "us";

    public async Task<IReadOnlyList<LookupCandidate>> SearchAsync(string market, string q)
    {
        q = (q ?? string.Empty).Trim();
        if (q.Length == 0) return Array.Empty<LookupCandidate>();
        var like = q.ToUpperInvariant();

        if (IsUs(market))
        {
            return await _db.USStocks
                .Where(s => s.Symbol.ToUpper().Contains(like) || s.CompanyName.ToUpper().Contains(like))
                .OrderBy(s => s.Symbol.ToUpper().StartsWith(like) ? 0 : 1)
                .ThenBy(s => s.Symbol)
                .Take(20)
                .Select(s => new LookupCandidate(s.Symbol, s.CompanyName, s.Sector.SectorName, s.BroadSector))
                .ToListAsync();
        }

        return await _db.IndianStocks
            .Where(s => s.Symbol.ToUpper().Contains(like) || s.CompanyName.ToUpper().Contains(like))
            .OrderBy(s => s.Symbol.ToUpper().StartsWith(like) ? 0 : 1)
            .ThenBy(s => s.Symbol)
            .Take(20)
            .Select(s => new LookupCandidate(s.Symbol, s.CompanyName, s.Sector.SectorName, s.BroadSector))
            .ToListAsync();
    }

    public async Task<StockLookupDetail?> GetDetailAsync(string market, string symbol)
    {
        symbol = symbol.Trim();
        var upper = symbol.ToUpperInvariant();

        // Header comes from the catalog; if the symbol isn't catalogued there's nothing to show.
        string companyName, broadSector = null!, industry = null!;
        if (IsUs(market))
        {
            var stock = await _db.USStocks
                .Where(s => s.Symbol.ToUpper() == upper)
                .Select(s => new { s.Symbol, s.CompanyName, s.BroadSector, Industry = s.Sector.SectorName })
                .FirstOrDefaultAsync();
            if (stock == null) return null;
            symbol = stock.Symbol; companyName = stock.CompanyName; broadSector = stock.BroadSector!; industry = stock.Industry;
        }
        else
        {
            var stock = await _db.IndianStocks
                .Where(s => s.Symbol.ToUpper() == upper)
                .Select(s => new { s.Symbol, s.CompanyName, s.BroadSector, Industry = s.Sector.SectorName })
                .FirstOrDefaultAsync();
            if (stock == null) return null;
            symbol = stock.Symbol; companyName = stock.CompanyName; broadSector = stock.BroadSector!; industry = stock.Industry;
        }

        TickerBase? ticker = IsUs(market)
            ? await _db.USTickers.FirstOrDefaultAsync(t => t.Ticker.ToUpper() == upper)
            : await _db.IndianTickers.FirstOrDefaultAsync(t => t.Ticker.ToUpper() == upper);

        TickerTechnicalBase? tech = IsUs(market)
            ? await _db.USTickerTechnical.Where(t => t.Ticker.ToUpper() == upper)
                .OrderByDescending(t => t.AsOfDate).FirstOrDefaultAsync()
            : await _db.IndianTickerTechnical.Where(t => t.Ticker.ToUpper() == upper)
                .OrderByDescending(t => t.AsOfDate).FirstOrDefaultAsync();

        AnalystSnapshotBase? analyst = IsUs(market)
            ? await _db.USAnalystSnapshots.Where(a => a.Ticker.ToUpper() == upper)
                .OrderByDescending(a => a.AsOfDate).FirstOrDefaultAsync()
            : await _db.IndianAnalystSnapshots.Where(a => a.Ticker.ToUpper() == upper)
                .OrderByDescending(a => a.AsOfDate).FirstOrDefaultAsync();

        List<EpsForecastBase> eps;
        if (IsUs(market))
            eps = (await _db.USEpsForecasts.Where(e => e.Ticker.ToUpper() == upper).ToListAsync()).Cast<EpsForecastBase>().ToList();
        else
            eps = (await _db.IndianEpsForecasts.Where(e => e.Ticker.ToUpper() == upper).ToListAsync()).Cast<EpsForecastBase>().ToList();

        // Keep only the latest AsOf set, then split by period type.
        if (eps.Count > 0)
        {
            var latest = eps.Max(e => e.AsOfDate);
            eps = eps.Where(e => e.AsOfDate == latest).ToList();
        }
        var quarterly = eps.Where(e => e.PeriodType == "Q").OrderBy(e => e.PeriodEndDate).Select(ToEpsDto).ToList();
        var yearly = eps.Where(e => e.PeriodType == "Y").OrderBy(e => e.PeriodEndDate).Select(ToEpsDto).ToList();

        return new StockLookupDetail(
            symbol, companyName, broadSector, industry, market,
            ticker?.Exchange, ticker?.Active ?? true, ticker?.IsFno ?? false, ticker?.BarsAvailable,
            tech == null ? null : ToTechDto(tech),
            analyst == null ? null : ToAnalystDto(analyst),
            quarterly, yearly);
    }

    public async Task<IReadOnlyList<LookupBarDto>> GetBarsAsync(string market, string symbol, string timeframe)
    {
        var upper = symbol.Trim().ToUpperInvariant();

        List<LookupBarDto> daily = IsUs(market)
            ? await _db.USBars1D.Where(b => b.Ticker.ToUpper() == upper)
                .OrderBy(b => b.BarDate)
                .Select(b => new LookupBarDto(b.BarDate, b.Open, b.High, b.Low, b.Close, b.Volume))
                .ToListAsync()
            : await _db.IndianBars1D.Where(b => b.Ticker.ToUpper() == upper)
                .OrderBy(b => b.BarDate)
                .Select(b => new LookupBarDto(b.BarDate, b.Open, b.High, b.Low, b.Close, b.Volume))
                .ToListAsync();

        if (!string.Equals(timeframe, "weekly", StringComparison.OrdinalIgnoreCase))
            return daily;

        return AggregateWeekly(daily);
    }

    private static IReadOnlyList<LookupBarDto> AggregateWeekly(List<LookupBarDto> daily)
    {
        var cal = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var groups = daily
            .GroupBy(b =>
            {
                var dt = b.Date.ToDateTime(TimeOnly.MinValue);
                var week = cal.GetWeekOfYear(dt, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
                var year = dt.Year;
                if (week >= 52 && dt.Month == 1) year--;
                if (week == 1 && dt.Month == 12) year++;
                return (year, week);
            })
            .OrderBy(g => g.Min(b => b.Date));

        var result = new List<LookupBarDto>();
        foreach (var g in groups)
        {
            var ordered = g.OrderBy(b => b.Date).ToList();
            result.Add(new LookupBarDto(
                ordered[^1].Date,
                ordered[0].Open,
                ordered.Max(b => b.High),
                ordered.Min(b => b.Low),
                ordered[^1].Close,
                ordered.Sum(b => b.Volume ?? 0)));
        }
        return result;
    }

    private static LookupTechnicalDto ToTechDto(TickerTechnicalBase t) => new(
        t.AsOfDate, t.Close, t.DayPct, t.Open, t.High, t.Low, t.High52w, t.From52wHigh, t.MarketCap,
        t.Rs, t.Rs1d, t.Rs1w, t.Rs1m, t.Rs3m, t.Rs6m, t.RsType, t.RsDate, t.ScannerHits, t.LastScannerHit);

    private static LookupAnalystDto ToAnalystDto(AnalystSnapshotBase a) => new(
        a.AsOfDate, a.ConsensusRating, a.NumAnalysts,
        a.CurrentQuarterEps, a.NextQuarterEps, a.CurrentYearEps, a.NextYearEps);

    private static LookupEpsForecastDto ToEpsDto(EpsForecastBase e) => new(
        e.PeriodType, e.PeriodEndDate, e.ConsensusEps, e.HighEps, e.LowEps,
        e.NumEstimates, e.RevisionsUp, e.RevisionsDown);
}
