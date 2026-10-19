using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public class AiAnalysisCandidateServiceTests
{
    [Fact]
    public async Task GetTopCandidatesAsync_UsesTenBillionDefaultForUs()
    {
        using var db = DbContextFactory.Create();
        var now = DateTime.UtcNow;
        db.JobRuns.Add(new JobRun
        {
            Id = 1, JobType = "stage2_analysis", Market = "us", WeekNumber = "2026-W43",
            Status = "completed", CreatedAt = now.AddHours(-1), CompletedAt = now
        });
        db.USStageAnalysisResults.AddRange(
            UsStage("SMALL", 5_000_000_000m, now),
            UsStage("LARGE", 15_000_000_000m, now));
        foreach (var symbol in new[] { "SMALL", "LARGE" })
        {
            db.USNearPivots.Add(new USNearPivot
            {
                Ticker = symbol, TradeType = "positional", Direction = "long",
                LastClose = 100, PivotPrice = 102, DistancePct = 2,
                ScanDate = now, CreatedAt = now, UpdatedAt = now
            });
        }
        await db.SaveChangesAsync();

        var result = await new AiAnalysisCandidateService(db).GetTopCandidatesAsync("us");

        result!.MinimumMarketCap.Should().Be(10_000_000_000m);
        result.EligibleStage2Count.Should().Be(2);
        result.MarketCapEligibleCount.Should().Be(1);
        result.Candidates.Should().ContainSingle().Which.Symbol.Should().Be("LARGE");
    }

    [Fact]
    public async Task GetTopCandidatesAsync_AppliesStage2SetupAnalyzedAndSectorGates()
    {
        using var db = DbContextFactory.Create();
        var now = DateTime.UtcNow;
        db.JobRuns.Add(new JobRun
        {
            Id = 1,
            JobType = "stage2_analysis",
            Market = "india",
            WeekNumber = "2026-W43",
            Status = "completed",
            CreatedAt = now.AddHours(-1),
            CompletedAt = now
        });
        db.IndianStageAnalysisResults.AddRange(
            Stage("A", "Software", true, now),
            Stage("B", "Software", true, now),
            Stage("C", "Software", true, now),
            Stage("D", "Software", true, now),
            Stage("E", "Software", true, now),
            Stage("F", "Capital Goods", true, now),
            Stage("LOWCAP", "Capital Goods", true, now, marketCap: 5_000_000_000m),
            Stage("NOSETUP", "Capital Goods", true, now),
            Stage("NOTSTAGE2", "Capital Goods", false, now));
        foreach (var symbol in new[] { "A", "B", "C", "D", "E", "F", "LOWCAP" })
        {
            db.IndianNearPivots.Add(new IndianNearPivot
            {
                Ticker = symbol,
                TradeType = "positional",
                Direction = "long",
                LastClose = 100,
                PivotPrice = 102,
                DistancePct = 2,
                ScanDate = now,
                CreatedAt = now,
                UpdatedAt = now
            });
            db.IndianEarningsFundamentals.Add(new IndianEarningsFundamentals
            {
                Ticker = symbol,
                AsOfDate = DateOnly.FromDateTime(now),
                EarningsGrowthYoyPct = 20,
                EarningsGrowthQoqPct = 10,
                Opm = 25,
                OpmYoyQ = 20,
                UpdatedAt = now
            });
        }
        db.AiStockAnalysisResults.Add(new AiStockAnalysisResult
        {
            Market = "india",
            Ticker = "A",
            WeekNumber = "2026-W43",
            SchemaVersion = "v1",
            SkillVersion = "v1",
            Model = "test",
            InputJson = "{}",
            EarningsCallJson = "{}",
            CorporateFilingsJson = "{}",
            AnalysisJson = "{}",
            CreatedAt = now,
            UpdatedAt = now
        });
        await db.SaveChangesAsync();

        var service = new AiAnalysisCandidateService(db);
        var result = await service.GetTopCandidatesAsync("india", top: 20);

        result.Should().NotBeNull();
        result!.MinimumMarketCap.Should().Be(10_000_000_000m);
        result.EligibleStage2Count.Should().Be(8);
        result.MarketCapEligibleCount.Should().Be(7);
        result.AvailableAfterAnalyzedExclusion.Should().Be(6);
        result.TechnicalSetupCount.Should().Be(5);
        result.Candidates.Should().HaveCount(6);
        result.Candidates.Should().NotContain(candidate =>
            candidate.Symbol is "A" or "LOWCAP" or "NOTSTAGE2");
        result.Candidates.Count(candidate => candidate.SectorName == "Software").Should().Be(4);
        result.Candidates.Should().OnlyContain(candidate => candidate.ScoreBreakdown.SectorStrength > 0);
        result.Candidates.Single(candidate => candidate.Symbol == "NOSETUP")
            .ScoreBreakdown.TechnicalTiming.Should().Be(0);
    }

    private static IndianStageAnalysisResult Stage(
        string symbol, string sector, bool isStage2, DateTime now,
        decimal marketCap = 20_000_000_000m) => new()
    {
        RunId = 1,
        WeekNumber = "2026-W43",
        Symbol = symbol,
        CompanyName = symbol,
        SectorId = sector == "Software" ? 1 : 2,
        SectorName = sector,
        IsStage2 = isStage2,
        Classification = "continuing",
        MarketCap = marketCap,
        RSScore = sector == "Software" ? 80 : 60,
        RSRank = sector == "Software" ? 90 : 70,
        RSDelta2w = sector == "Software" ? 5 : 2,
        MomentumScore = 3,
        ADClassification = "accumulating",
        CreatedAt = now
    };

    private static USStageAnalysisResult UsStage(string symbol, decimal marketCap, DateTime now) => new()
    {
        RunId = 1, WeekNumber = "2026-W43", Symbol = symbol, CompanyName = symbol,
        SectorId = 1, SectorName = "Technology", IsStage2 = true,
        Classification = "continuing", MarketCap = marketCap,
        RSScore = 80, RSRank = 90, RSDelta2w = 5, MomentumScore = 3,
        ADClassification = "accumulating", CreatedAt = now
    };
}
