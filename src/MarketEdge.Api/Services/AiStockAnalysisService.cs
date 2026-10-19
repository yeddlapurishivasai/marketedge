using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IAiStockAnalysisService
{
    Task<AiStockAnalysisDto?> GetAsync(string market, string symbol);
}

public class AiStockAnalysisService : IAiStockAnalysisService
{
    private static readonly string[] TechnicalColumns =
    {
        "asOfDate", "high52w", "from52wHighPct", "marketCap",
        "rs", "rs1d", "rs1w", "rs1m", "rs3m", "rs6m", "rsType", "rsDate",
        "scannerHits", "lastScannerHit", "updatedAt"
    };

    private static readonly string[] CandleColumns =
    {
        "date", "open", "high", "low", "close", "volume", "adjClose"
    };

    private readonly MarketEdgeDbContext _db;
    public AiStockAnalysisService(MarketEdgeDbContext db) => _db = db;

    private static bool IsUs(string market) => market == "us";

    public async Task<AiStockAnalysisDto?> GetAsync(string market, string symbol)
    {
        var upper = symbol.Trim().ToUpperInvariant();
        var stock = await LoadStockAsync(market, upper);
        if (stock == null) return null;

        var allDaily = await LoadDailyCandlesAsync(market, upper);
        var candleEnd = allDaily.Count > 0
            ? allDaily.Max(b => b.Date)
            : DateOnly.FromDateTime(DateTime.UtcNow);
        var dailyStart = candleEnd.AddMonths(-6);
        var weeklyStart = candleEnd.AddYears(-1);
        var daily = allDaily.Where(b => b.Date >= dailyStart && b.Date <= candleEnd).ToList();
        var weeklySource = allDaily.Where(b => b.Date >= weeklyStart && b.Date <= candleEnd).ToList();

        return new AiStockAnalysisDto(
            DateTime.UtcNow,
            market,
            stock,
            await LoadFundamentalsAsync(market, upper),
            TechnicalColumns,
            await LoadTechnicalRowsAsync(market, upper),
            await LoadAnalystHistoryAsync(market, upper),
            await LoadEpsForecastsAsync(market, upper),
            await LoadSignalsAsync(market, upper),
            await LoadNoteAsync(market, upper),
            CandleColumns,
            ToCompactCandleRows(daily),
            ToCompactCandleRows(AggregateWeekly(weeklySource)),
            new AiDataAvailabilityDto(false, "Concall data is not persisted in MarketEdge."));
    }

    private async Task<AiStockMetadataDto?> LoadStockAsync(string market, string upper)
    {
        if (IsUs(market))
        {
            var stock = await _db.USStocks.AsNoTracking().Include(s => s.Sector).Include(s => s.Fundamentals)
                .FirstOrDefaultAsync(s => s.Symbol.ToUpper() == upper);
            if (stock == null) return null;
            var ticker = await _db.USTickers.AsNoTracking().FirstOrDefaultAsync(t => t.Ticker.ToUpper() == upper);
            return new AiStockMetadataDto(
                stock.Id, stock.Symbol, stock.CompanyName, stock.SectorId, stock.Sector.SectorName,
                stock.BroadSector, stock.IsFno, stock.IsTestSample, stock.CreatedAt,
                ticker?.Exchange, ticker?.Active, ticker?.IsFno, ticker?.BarsAvailable,
                stock.Fundamentals?.MarketCap, stock.Fundamentals?.UpdatedAt, stock.Fundamentals?.CreatedAt);
        }

        var indianStock = await _db.IndianStocks.AsNoTracking().Include(s => s.Sector).Include(s => s.Fundamentals)
            .FirstOrDefaultAsync(s => s.Symbol.ToUpper() == upper);
        if (indianStock == null) return null;
        var indianTicker = await _db.IndianTickers.AsNoTracking().FirstOrDefaultAsync(t => t.Ticker.ToUpper() == upper);
        return new AiStockMetadataDto(
            indianStock.Id, indianStock.Symbol, indianStock.CompanyName,
            indianStock.SectorId, indianStock.Sector.SectorName,
            indianStock.BroadSector, indianStock.IsFno, indianStock.IsTestSample, indianStock.CreatedAt,
            indianTicker?.Exchange, indianTicker?.Active, indianTicker?.IsFno, indianTicker?.BarsAvailable,
            indianStock.Fundamentals?.MarketCap,
            indianStock.Fundamentals?.UpdatedAt, indianStock.Fundamentals?.CreatedAt);
    }

    private async Task<AiEarningsFundamentalsDto?> LoadFundamentalsAsync(string market, string upper)
    {
        EarningsFundamentalsBase? row = IsUs(market)
            ? await _db.USEarningsFundamentals.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper)
            : await _db.IndianEarningsFundamentals.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper);
        return row == null ? null : new AiEarningsFundamentalsDto(
            row.Ticker, row.AsOfDate, row.LatestQuarterEnd,
            row.Revenue, row.RevenuePrevQ, row.RevenueYoyQ, row.RevenueGrowthYoyPct,
            row.OperatingProfit, row.OperatingProfitPrevQ, row.OperatingProfitYoyQ,
            row.Opm, row.OpmPrevQ, row.OpmYoyQ,
            row.NetProfit, row.NetProfitPrevQ, row.NetProfitYoyQ,
            row.NetMarginPct, row.EarningsGrowthYoyPct, row.EarningsGrowthQoqPct,
            row.EarningsIncreasing, row.OperatingProfitTrend, row.OpmTrend,
            row.LastEarningsDate, row.PrevEarningsDate, row.NextEarningsDate,
            row.LastReportedEps, row.LastEpsSurprisePct,
            row.EpsQ1Date, row.EpsQ1Estimate, row.EpsQ1Actual, row.EpsQ1SurprisePct,
            row.EpsQ2Date, row.EpsQ2Estimate, row.EpsQ2Actual, row.EpsQ2SurprisePct,
            row.EpsQ3Date, row.EpsQ3Estimate, row.EpsQ3Actual, row.EpsQ3SurprisePct,
            row.EpsQ4Date, row.EpsQ4Estimate, row.EpsQ4Actual, row.EpsQ4SurprisePct,
            row.TrailingPe, row.ForwardPe,
            row.ReportedSource, row.ReportedFetchedAt, row.UpdatedAt);
    }

    private async Task<IReadOnlyList<IReadOnlyList<object?>>> LoadTechnicalRowsAsync(
        string market, string upper)
    {
        List<TickerTechnicalBase> rows = IsUs(market)
            ? (await _db.USTickerTechnical.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ToListAsync()).Cast<TickerTechnicalBase>().ToList()
            : (await _db.IndianTickerTechnical.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ToListAsync()).Cast<TickerTechnicalBase>().ToList();
        return rows.Select(r => (IReadOnlyList<object?>)new object?[]
        {
            r.AsOfDate, r.High52w, r.From52wHigh, r.MarketCap,
            r.Rs, r.Rs1d, r.Rs1w, r.Rs1m, r.Rs3m, r.Rs6m, r.RsType, r.RsDate,
            r.ScannerHits, r.LastScannerHit, r.UpdatedAt
        }).ToList();
    }

    private async Task<IReadOnlyList<AiAnalystSnapshotDto>> LoadAnalystHistoryAsync(string market, string upper)
    {
        List<AnalystSnapshotBase> rows = IsUs(market)
            ? (await _db.USAnalystSnapshots.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ToListAsync()).Cast<AnalystSnapshotBase>().ToList()
            : (await _db.IndianAnalystSnapshots.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ToListAsync()).Cast<AnalystSnapshotBase>().ToList();
        return rows.Select(r => new AiAnalystSnapshotDto(
            r.Ticker, r.AsOfDate, r.ConsensusRating, r.NumAnalysts,
            r.CurrentQuarterEps, r.NextQuarterEps, r.CurrentYearEps, r.NextYearEps,
            r.TargetLowPrice, r.TargetMeanPrice, r.TargetHighPrice, r.RecommendationsJson,
            r.LatestRatingFirm, r.LatestRatingGrade, r.LatestRatingAction, r.LatestRatingDate,
            r.UpdatedAt)).ToList();
    }

    private async Task<IReadOnlyList<AiEpsForecastDto>> LoadEpsForecastsAsync(string market, string upper)
    {
        List<EpsForecastBase> rows = IsUs(market)
            ? (await _db.USEpsForecasts.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ThenBy(r => r.PeriodType).ThenBy(r => r.PeriodEndDate)
                .ToListAsync()).Cast<EpsForecastBase>().ToList()
            : (await _db.IndianEpsForecasts.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderByDescending(r => r.AsOfDate).ThenBy(r => r.PeriodType).ThenBy(r => r.PeriodEndDate)
                .ToListAsync()).Cast<EpsForecastBase>().ToList();
        return rows.Select(r => new AiEpsForecastDto(
            r.Ticker, r.AsOfDate, r.PeriodType, r.PeriodEndDate,
            r.ConsensusEps, r.HighEps, r.LowEps, r.NumEstimates,
            r.RevisionsUp, r.RevisionsDown, r.UpdatedAt)).ToList();
    }

    private async Task<AiStockSignalsDto?> LoadSignalsAsync(string market, string upper)
    {
        StockSignalsBase? row = IsUs(market)
            ? await _db.USStockSignals.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper)
            : await _db.IndianStockSignals.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper);
        return row == null ? null : new AiStockSignalsDto(
            row.Ticker, row.CapexCwip, row.CapexCwipPrevQ, row.CapexChangePct,
            row.CapexTrend, row.CapexAsOf, row.NewsJson, row.SignalsText, row.UpdatedAt);
    }

    private async Task<AiStockNoteDto?> LoadNoteAsync(string market, string upper)
    {
        StockNoteBase? row = IsUs(market)
            ? await _db.USStockNotes.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper)
            : await _db.IndianStockNotes.AsNoTracking().FirstOrDefaultAsync(r => r.Ticker.ToUpper() == upper);
        return row == null ? null : new AiStockNoteDto(row.Ticker, row.NoteText, row.UpdatedAt);
    }

    private async Task<List<AiCandleDto>> LoadDailyCandlesAsync(string market, string upper)
    {
        List<Bar1DBase> rows = IsUs(market)
            ? (await _db.USBars1D.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderBy(r => r.BarDate).ToListAsync()).Cast<Bar1DBase>().ToList()
            : (await _db.IndianBars1D.AsNoTracking().Where(r => r.Ticker.ToUpper() == upper)
                .OrderBy(r => r.BarDate).ToListAsync()).Cast<Bar1DBase>().ToList();
        return rows.Select(r => new AiCandleDto(
            r.BarDate, r.Open, r.High, r.Low, r.Close, r.Volume, r.AdjClose)).ToList();
    }

    private static IReadOnlyList<AiCandleDto> AggregateWeekly(IReadOnlyList<AiCandleDto> daily)
    {
        var calendar = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        return daily.GroupBy(candle =>
            {
                var date = candle.Date.ToDateTime(TimeOnly.MinValue);
                var week = calendar.GetWeekOfYear(
                    date, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
                var year = date.Year;
                if (week >= 52 && date.Month == 1) year--;
                if (week == 1 && date.Month == 12) year++;
                return (year, week);
            })
            .OrderBy(group => group.Min(candle => candle.Date))
            .Select(group =>
            {
                var ordered = group.OrderBy(candle => candle.Date).ToList();
                long? volume = ordered.Any(candle => candle.Volume.HasValue)
                    ? ordered.Sum(candle => candle.Volume ?? 0)
                    : null;
                var adjClose = ordered.Select(candle => candle.AdjClose).LastOrDefault(value => value.HasValue);
                return new AiCandleDto(
                    ordered[^1].Date,
                    ordered[0].Open,
                    ordered.Max(candle => candle.High),
                    ordered.Min(candle => candle.Low),
                    ordered[^1].Close,
                    volume,
                    adjClose);
            })
            .ToList();
    }

    private static IReadOnlyList<IReadOnlyList<object?>> ToCompactCandleRows(
        IReadOnlyList<AiCandleDto> candles) => candles
        .Select(candle => (IReadOnlyList<object?>)new object?[]
        {
            candle.Date, candle.Open, candle.High, candle.Low,
            candle.Close, candle.Volume, candle.AdjClose
        })
        .ToList();
}