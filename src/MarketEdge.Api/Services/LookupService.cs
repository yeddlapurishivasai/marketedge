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

        // The RS step and the price/technical step write separate dated rows (RS-only today,
        // prices on the last bar date). Coalesce the most recent rows so every field is filled.
        List<TickerTechnicalBase> techRows = IsUs(market)
            ? (await _db.USTickerTechnical.Where(t => t.Ticker.ToUpper() == upper)
                .OrderByDescending(t => t.AsOfDate).Take(5).ToListAsync()).Cast<TickerTechnicalBase>().ToList()
            : (await _db.IndianTickerTechnical.Where(t => t.Ticker.ToUpper() == upper)
                .OrderByDescending(t => t.AsOfDate).Take(5).ToListAsync()).Cast<TickerTechnicalBase>().ToList();
        LookupTechnicalDto? tech = techRows.Count == 0 ? null : ToTechDtoCoalesced(techRows);

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

        var analystDto = analyst == null ? null : ToAnalystDto(analyst);
        decimal? currentPrice = tech?.Close;
        var yearUpside = BuildProjection("year", currentPrice, analystDto?.CurrentYearEps, yearly);
        var quarterUpside = BuildProjection("quarter", currentPrice, analystDto?.CurrentQuarterEps, quarterly);

        return new StockLookupDetail(
            symbol, companyName, broadSector, industry, market,
            ticker?.Exchange, ticker?.Active ?? true, ticker?.IsFno ?? false, ticker?.BarsAvailable,
            tech,
            analystDto,
            quarterly, yearly,
            quarterUpside, yearUpside);
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

    private static LookupTechnicalDto ToTechDtoCoalesced(List<TickerTechnicalBase> rows)
    {
        // rows are newest-first; pick the newest non-null value for each field so a fresh
        // RS-only row doesn't blank out prices written on an earlier dated row.
        decimal? D(Func<TickerTechnicalBase, decimal?> f) => rows.Select(f).FirstOrDefault(v => v.HasValue);
        long? L(Func<TickerTechnicalBase, long?> f) => rows.Select(f).FirstOrDefault(v => v.HasValue);
        int? I(Func<TickerTechnicalBase, int?> f) => rows.Select(f).FirstOrDefault(v => v.HasValue);
        DateOnly? Dt(Func<TickerTechnicalBase, DateOnly?> f) => rows.Select(f).FirstOrDefault(v => v.HasValue);
        string? S(Func<TickerTechnicalBase, string?> f) => rows.Select(f).FirstOrDefault(v => !string.IsNullOrEmpty(v));
        return new LookupTechnicalDto(
            rows[0].AsOfDate,
            D(t => t.Close), D(t => t.DayPct), D(t => t.Open), D(t => t.High), D(t => t.Low),
            D(t => t.High52w), D(t => t.From52wHigh), L(t => t.MarketCap),
            I(t => t.Rs), I(t => t.Rs1d), I(t => t.Rs1w), I(t => t.Rs1m), I(t => t.Rs3m), I(t => t.Rs6m),
            S(t => t.RsType), Dt(t => t.RsDate), I(t => t.ScannerHits), Dt(t => t.LastScannerHit));
    }

    // Best/base/worst EPS upside at constant P/E for a horizon. Base EPS is the trailing
    // figure (analyst current quarter/year EPS); if absent, the current-period forecast
    // consensus is used. The projection target is the forward-most forecast row, whose
    // Low / Consensus / High estimates give the bear / base / bull cases.
    private static UpsideProjectionDto? BuildProjection(
        string horizon, decimal? currentPrice, decimal? baseEpsFromAnalyst,
        IReadOnlyList<LookupEpsForecastDto> forecasts)
    {
        if (forecasts.Count == 0) return null;
        var proj = forecasts[^1];

        decimal? baseEps = baseEpsFromAnalyst;
        if ((baseEps is null || baseEps <= 0) && forecasts.Count >= 2)
            baseEps = forecasts[0].ConsensusEps;
        if (baseEps is null || baseEps <= 0)
            return new UpsideProjectionDto(horizon, "deterministic", currentPrice, baseEps, null, null, null);

        UpsideCaseDto? Case(decimal? eps)
        {
            if (eps is null) return null;
            var ratio = eps.Value / baseEps.Value;
            var pct = decimal.Round((ratio - 1m) * 100m, 2);
            decimal? price = currentPrice.HasValue ? decimal.Round(currentPrice.Value * ratio, 2) : null;
            return new UpsideCaseDto(eps, pct, price);
        }

        return new UpsideProjectionDto(
            horizon, "deterministic", currentPrice, baseEps,
            Case(proj.LowEps), Case(proj.ConsensusEps), Case(proj.HighEps));
    }

    private static LookupAnalystDto ToAnalystDto(AnalystSnapshotBase a) => new(
        a.AsOfDate, a.ConsensusRating, a.NumAnalysts,
        a.CurrentQuarterEps, a.NextQuarterEps, a.CurrentYearEps, a.NextYearEps);

    private static LookupEpsForecastDto ToEpsDto(EpsForecastBase e) => new(
        e.PeriodType, e.PeriodEndDate, e.ConsensusEps, e.HighEps, e.LowEps,
        e.NumEstimates, e.RevisionsUp, e.RevisionsDown);
}
