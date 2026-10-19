using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public class AiInvestmentAnalysisStoreTests
{
    [Fact]
    public async Task SaveAsync_UpsertsWeekAndRetainsNewestTenWeeks()
    {
        using var db = DbContextFactory.Create();
        var store = new AiInvestmentAnalysisStore(db);

        for (var week = 1; week <= 12; week++)
            await store.SaveAsync(CreateAnalysis($"2026-W{week:D2}", week));

        await store.SaveAsync(CreateAnalysis("2026-W12", 99));

        var history = await store.GetHistoryAsync("india", "E2E");
        history.Should().HaveCount(10);
        history.Select(item => item.WeekNumber).Should().Equal(
            "2026-W12", "2026-W11", "2026-W10", "2026-W09", "2026-W08",
            "2026-W07", "2026-W06", "2026-W05", "2026-W04", "2026-W03");
        history[0].Cost.TotalCostUsd.Should().Be(99m);
        history[0].Cost.Projected1000TickersCostUsd.Should().Be(99_000m);

        var detail = await store.GetAsync("india", "E2E", "2026-W12");
        detail.Should().NotBeNull();
        detail!.DeterministicInput.GetProperty("week").GetInt32().Should().Be(99);

        var marketHistory = await store.GetMarketHistoryAsync("india", 4);
        marketHistory.Should().HaveCount(4);
        marketHistory.Select(item => item.WeekNumber).Should()
            .Equal("2026-W12", "2026-W11", "2026-W10", "2026-W09");
    }

    private static SaveAiInvestmentAnalysis CreateAnalysis(string weekNumber, decimal cost) => new(
        "india",
        "E2E",
        weekNumber,
        "marketedge.ai-analysis.v1",
        "0.1.0",
        "test-model",
        $$"{"week":{{cost}}}",
        "{\"status\":\"available\"}",
        "{\"status\":\"available\"}",
        "{\"valuation\":\"fairly_valued\"}",
        new AiAnalysisCostDto(100, 0, 20, cost, 0, 0, 999m, 1m));
}