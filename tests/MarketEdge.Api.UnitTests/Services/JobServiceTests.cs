using Azure.Storage.Queues;
using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Moq;
using System.Text.Json;

namespace MarketEdge.Api.UnitTests.Services;

public class JobServiceTests
{
    [Theory]
    [InlineData("stage4_analysis")]
    [InlineData("stage2_analysis")]
    public async Task GetRunsAndDetail_StripOnlyStage4FrozenUniverseArrays(string jobType)
    {
        using var db = DbContextFactory.Create();
        const string parameters = """
            {"scope":"filtered","minMarketCap":100,"maxMarketCap":1000,"sectorIds":[9],"limit":20,
            "testSampleOnly":false,"eligibilityCapturedAt":"2026-11-14T00:00:00","retrySourceRunId":4,
            "universeSymbols":["FIRST","SECOND"],"universeSnapshot":[
                {"symbol":"FIRST","company_name":"First","sector_id":9},
                {"symbol":"SECOND","company_name":"Second","sector_id":9}
            ]}
            """;
        var job = new JobRun
        {
            JobType = jobType, Market = "india", WeekNumber = "2026-W46", Status = "completed",
            Parameters = parameters, CreatedAt = DateTime.UtcNow
        };
        db.JobRuns.Add(job);
        await db.SaveChangesAsync();
        var service = new JobService(db, new Mock<QueueClient>().Object);
        var listed = (await service.GetRunsAsync(jobType: jobType)).Single();
        var detail = (await service.GetRunByIdAsync(job.Id))!;
        foreach (var dto in new[] { listed, detail })
        {
            dto.Parameters.Should().ContainKeys("scope", "minMarketCap", "maxMarketCap", "sectorIds", "limit",
                "testSampleOnly", "eligibilityCapturedAt", "retrySourceRunId");
            if (jobType == "stage4_analysis")
            {
                dto.Parameters.Should().NotContainKeys("universeSymbols", "universeSnapshot");
                dto.Parameters!["universeCount"].Should().Be(2);
            }
            else
            {
                dto.Parameters.Should().ContainKeys("universeSymbols", "universeSnapshot");
                dto.Parameters.Should().NotContainKey("universeCount");
                ((JsonElement)dto.Parameters!["universeSymbols"]).GetArrayLength().Should().Be(2);
            }
        }
        db.JobRuns.Single().Parameters.Should().Be(parameters, "frozen retry metadata must remain persisted unchanged");
    }

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
