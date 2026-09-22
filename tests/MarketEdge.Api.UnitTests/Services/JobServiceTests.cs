using Azure.Storage.Queues;
using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public class JobServiceTests
{
    [Fact]
    public async Task GetStage2StocksAsync_PopulatesLatestNonStaleFundamentalScore()
    {
        using var db = DbContextFactory.Create();
        var job = new JobRun
        {
            JobType = "stage2_analysis",
            Market = "india",
            WeekNumber = "2026-W38",
            Status = "completed",
            CreatedAt = DateTime.UtcNow
        };
        db.JobRuns.Add(job);
        await db.SaveChangesAsync();

        db.IndianStageAnalysisResults.AddRange(
            CreateResult(job.Id, "SCORED"),
            CreateResult(job.Id, "MISSING"));
        db.IndianFundamentalIdeas.AddRange(
            CreateIdea("SCORED", new DateOnly(2026, 1, 1), 55m),
            CreateIdea("SCORED", new DateOnly(2026, 4, 1), 82.5m),
            CreateIdea("SCORED", new DateOnly(2026, 7, 1), 99m, isStale: true));
        await db.SaveChangesAsync();

        var service = new JobService(db, new Mock<QueueClient>().Object);
        var results = await service.GetStage2StocksAsync(job.Id);

        results.Single(r => r.Symbol == "SCORED").FundamentalScore.Should().Be(82.5m);
        results.Single(r => r.Symbol == "MISSING").FundamentalScore.Should().BeNull();
    }

    [Fact]
    public async Task GetStage2StocksAsync_FiltersSqueezeWithoutFnoRestriction()
    {
        using var db = DbContextFactory.Create();
        var job = new JobRun
        {
            JobType = "stage2_analysis",
            Market = "india",
            WeekNumber = "2026-W38",
            Status = "completed",
            CreatedAt = DateTime.UtcNow
        };
        db.JobRuns.Add(job);
        await db.SaveChangesAsync();

        var squeezed = CreateResult(job.Id, "SQUEEZED");
        squeezed.SqueezeOn = true;
        db.IndianStageAnalysisResults.AddRange(squeezed, CreateResult(job.Id, "OPEN"));
        await db.SaveChangesAsync();

        var service = new JobService(db, new Mock<QueueClient>().Object);
        var results = await service.GetStage2StocksAsync(job.Id, squeeze: "on");

        results.Should().ContainSingle().Which.Symbol.Should().Be("SQUEEZED");
    }

    [Fact]
    public async Task GetStage2StocksAsync_FiltersByMinimumMarketCap()
    {
        using var db = DbContextFactory.Create();
        var job = new JobRun
        {
            JobType = "stage2_analysis",
            Market = "india",
            WeekNumber = "2026-W38",
            Status = "completed",
            CreatedAt = DateTime.UtcNow
        };
        db.JobRuns.Add(job);
        await db.SaveChangesAsync();

        var largeCap = CreateResult(job.Id, "LARGE");
        largeCap.MarketCap = 50_000_000_000m;
        var smallCap = CreateResult(job.Id, "SMALL");
        smallCap.MarketCap = 10_000_000_000m;
        db.IndianStageAnalysisResults.AddRange(largeCap, smallCap, CreateResult(job.Id, "UNKNOWN"));
        await db.SaveChangesAsync();

        var service = new JobService(db, new Mock<QueueClient>().Object);
        var results = await service.GetStage2StocksAsync(job.Id, minMarketCap: 25_000_000_000m);

        results.Should().ContainSingle().Which.Symbol.Should().Be("LARGE");
    }

    private static IndianStageAnalysisResult CreateResult(int runId, string symbol) => new()
    {
        RunId = runId,
        WeekNumber = "2026-W38",
        Symbol = symbol,
        CompanyName = symbol,
        SectorId = 1,
        SectorName = "Technology",
        IsStage2 = true,
        CreatedAt = DateTime.UtcNow
    };

    private static IndianFundamentalIdea CreateIdea(
        string ticker,
        DateOnly earningsDate,
        decimal fundamentalConfidence,
        bool isStale = false) => new()
    {
        Ticker = ticker,
        EarningsDate = earningsDate,
        FundamentalConfidence = fundamentalConfidence,
        IsStale = isStale,
        CapturedAt = DateTime.UtcNow,
        UpdatedAt = DateTime.UtcNow
    };
}
