using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using System.Text.Json;

namespace MarketEdge.Api.UnitTests.Services;

public class AiStockAnalysisServiceTests
{
    [Fact]
    public async Task GetAsync_ReturnsAllRawDataWithoutConfidenceScores()
    {
        using var db = DbContextFactory.Create();
        var now = DateTime.UtcNow;
        var sector = new IndianSector { SectorName = "Software", CreatedAt = now };
        db.IndianSectors.Add(sector);
        await db.SaveChangesAsync();
        var stock = new IndianStock
        {
            Symbol = "E2E", CompanyName = "E2E Networks", SectorId = sector.Id,
            BroadSector = "Technology", IsFno = false, IsTestSample = true, CreatedAt = now
        };
        db.IndianStocks.Add(stock);
        await db.SaveChangesAsync();
        db.IndianStockFundamentals.Add(new IndianStockFundamentals
        {
            StockId = stock.Id, MarketCap = 135_000_000_000m, CreatedAt = now, UpdatedAt = now
        });
        db.IndianTickers.Add(new IndianTicker
        {
            Ticker = "E2E", Exchange = "NSE", Active = true, IsFno = false, BarsAvailable = 3
        });
        db.IndianEarningsFundamentals.Add(new IndianEarningsFundamentals
        {
            Ticker = "E2E", AsOfDate = new DateOnly(2026, 10, 19),
            LatestQuarterEnd = new DateOnly(2026, 9, 30),
            Revenue = 224m, RevenuePrevQ = 157m, RevenueYoyQ = 44m,
            OperatingProfit = 171m, NetProfit = 73m,
            ReportedSource = "screener", ReportedFetchedAt = now, UpdatedAt = now
        });
        db.IndianTickerTechnical.AddRange(
            new IndianTickerTechnical
            {
                Ticker = "E2E", AsOfDate = new DateOnly(2026, 10, 19),
                Close = 660.15m, Rs = 98, UpdatedAt = now
            },
            new IndianTickerTechnical
            {
                Ticker = "E2E", AsOfDate = new DateOnly(2026, 10, 18),
                Close = 694.85m, UpdatedAt = now.AddDays(-1)
            });
        db.IndianAnalystSnapshots.Add(new IndianAnalystSnapshot
        {
            Ticker = "E2E", AsOfDate = new DateOnly(2026, 10, 19),
            CurrentYearEps = 5.61m, RecommendationsJson = "[]", UpdatedAt = now
        });
        db.IndianEpsForecasts.Add(new IndianEpsForecast
        {
            Ticker = "E2E", AsOfDate = new DateOnly(2026, 10, 19), PeriodType = "Y",
            PeriodEndDate = new DateOnly(2027, 3, 31), ConsensusEps = 8.25m,
            RevisionsUp = 1, RevisionsDown = 0, UpdatedAt = now
        });
        db.IndianStockSignals.Add(new IndianStockSignals
        {
            Ticker = "E2E", CapexCwip = 533m, NewsJson = "[]",
            SignalsText = "CAPEX(CWIP): 533M", UpdatedAt = now
        });
        db.IndianStockNotes.Add(new IndianStockNote
        {
            Ticker = "E2E", NoteText = "Raw operator note", UpdatedAt = now
        });
        var latestDate = new DateOnly(2026, 10, 19);
        db.IndianBars1D.AddRange(
            Candle(latestDate.AddYears(-1).AddDays(-1), 1m),
            Candle(latestDate.AddYears(-1), 2m),
            Candle(latestDate.AddMonths(-6).AddDays(-1), 3m),
            Candle(latestDate.AddMonths(-6), 4m),
            Candle(latestDate, 5m));
        await db.SaveChangesAsync();

        var service = new AiStockAnalysisService(db);
        var result = await service.GetAsync("india", "E2E");

        result.Should().NotBeNull();
        result!.Stock.Symbol.Should().Be("E2E");
        result.Stock.MarketCap.Should().Be(135_000_000_000m);
        result.Fundamentals!.RevenuePrevQ.Should().Be(157m);
        result.Fundamentals.ReportedSource.Should().Be("screener");
        result.TechnicalColumns.Should().Equal(
            "asOfDate", "high52w", "from52wHighPct", "marketCap",
            "rs", "rs1d", "rs1w", "rs1m", "rs3m", "rs6m", "rsType", "rsDate",
            "scannerHits", "lastScannerHit", "updatedAt");
        result.TechnicalRows.Should().HaveCount(2);
        result.AnalystHistory.Should().ContainSingle().Which.CurrentYearEps.Should().Be(5.61m);
        result.EpsForecasts.Should().ContainSingle().Which.ConsensusEps.Should().Be(8.25m);
        result.Signals!.NewsJson.Should().Be("[]");
        result.Note!.NoteText.Should().Be("Raw operator note");
        result.CandleColumns.Should().Equal("date", "open", "high", "low", "close", "volume", "adjClose");
        result.DailyCandles.Select(row => (DateOnly)row[0]!).Should()
            .Equal(latestDate.AddMonths(-6), latestDate);
        result.DailyCandles.Last()[6].Should().Be(5.1m);
        result.WeeklyCandles.Select(row => (DateOnly)row[0]!).Should()
            .Contain(latestDate.AddYears(-1))
            .And.Contain(latestDate);
        result.ConcallData.Available.Should().BeFalse();

        var json = JsonSerializer.Serialize(result);
        json.ToLowerInvariant().Should().NotContain("confidence");
    }

    private static IndianBar1D Candle(DateOnly date, decimal close) => new()
    {
        Ticker = "E2E", BarDate = date,
        Open = close - 0.5m, High = close + 0.5m, Low = close - 1m,
        Close = close, AdjClose = close + 0.1m, Volume = 100
    };
}