using System.Text;
using System.Text.Json;
using Azure;
using Azure.Storage.Queues;
using Azure.Storage.Queues.Models;
using FluentAssertions;
using MarketEdge.Api.Controllers;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public class Stage4AnalysisServiceTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-11-25T18:00:00Z");

    [Theory]
    [InlineData("india", "2026-11-20T09:59:59Z", "2026-W46")]
    [InlineData("india", "2026-11-20T10:00:00Z", "2026-W47")]
    [InlineData("us", "2026-11-20T20:59:59Z", "2026-W46")]
    [InlineData("us", "2026-11-20T21:00:00Z", "2026-W47")]
    [InlineData("us", "2026-03-06T20:59:59Z", "2026-W09")]
    [InlineData("us", "2026-03-06T21:00:00Z", "2026-W10")]
    [InlineData("us", "2026-03-13T19:59:59Z", "2026-W10")]
    [InlineData("us", "2026-03-13T20:00:00Z", "2026-W11")]
    [InlineData("india", "2021-01-01T10:00:00Z", "2020-W53")]
    [InlineData("us", "2021-01-04T15:00:00Z", "2020-W53")]
    public void LatestClosed_UsesExchangeCloseIsoYearAndDst(string market, string utc, string expected) =>
        Stage4Week.LatestClosed(market, DateTimeOffset.Parse(utc)).Should().Be(expected);

    [Theory]
    [InlineData("2025-W53")]
    [InlineData("2026-W00")]
    [InlineData("2026-W54")]
    [InlineData("0000-W01")]
    [InlineData("2026-W48")]
    [InlineData("2027-W01")]
    [InlineData("2026-W1")]
    [InlineData("")]
    public async Task Trigger_RejectsInvalidOrUnfinishedWeeks(string week)
    {
        using var db = DbContextFactory.Create();
        var queue = new RecordingQueue();
        var service = Service(db, queue);
        await FluentActions.Awaiting(() => service.TriggerAsync("india", new() { WeekNumber = week }))
            .Should().ThrowAsync<ArgumentException>();
        db.JobRuns.Should().BeEmpty();
        queue.Messages.Should().BeEmpty();
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task Trigger_QueuesExplicitFnoScopeAndDefaultsToClosedWeek(string market)
    {
        using var db = DbContextFactory.Create();
        var queue = new RecordingQueue();
        var id = await Service(db, queue).TriggerAsync(market);
        var job = await db.JobRuns.SingleAsync();
        job.Id.Should().Be(id);
        job.JobType.Should().Be("stage4_analysis");
        job.WeekNumber.Should().Be("2026-W47");
        job.Status.Should().Be("queued");
        using var message = JsonDocument.Parse(queue.Messages.Single());
        var payload = message.RootElement;
        payload.GetProperty("jobType").GetString().Should().Be("stage4_analysis");
        payload.GetProperty("market").GetString().Should().Be(market);
        payload.GetProperty("runId").GetInt32().Should().Be(id);
        payload.GetProperty("weekNumber").GetString().Should().Be("2026-W47");
        payload.GetProperty("fnoOnly").GetBoolean().Should().BeTrue();
        payload.GetProperty("scope").GetString().Should().Be("full");
        foreach (var key in new[] { "minMarketCap", "maxMarketCap", "sectorIds", "limit", "testSampleOnly" })
            payload.TryGetProperty(key, out _).Should().BeTrue();
        job.Parameters.Should().NotContain("universeSymbols", "the worker freezes the universe at start");
    }

    [Fact]
    public async Task Trigger_ValidatesMarketCapsLimitsAndSectorMembership()
    {
        using var db = DbContextFactory.Create();
        db.IndianSectors.Add(new IndianSector { Id = 1, SectorName = "India only" });
        await db.SaveChangesAsync();
        var service = Service(db);
        foreach (var request in new TriggerStage4Request[]
        {
            new() { Limit = 0 }, new() { Limit = -1 }, new() { MinMarketCap = -1 },
            new() { MaxMarketCap = -1 }, new() { MinMarketCap = 10, MaxMarketCap = 1 },
            new() { SectorIds = [0] }, new() { SectorIds = [2] }, new() { RetrySourceRunId = 0 }
        })
            await FluentActions.Awaiting(() => service.TriggerAsync("india", request)).Should().ThrowAsync<ArgumentException>();
        await FluentActions.Awaiting(() => service.TriggerAsync("us", new() { SectorIds = [1] }))
            .Should().ThrowAsync<ArgumentException>();
        await FluentActions.Awaiting(() => service.TriggerAsync("uk")).Should().ThrowAsync<ArgumentException>();
        db.JobRuns.Should().BeEmpty();
    }

    [Fact]
    public async Task Trigger_UnknownFnoToggleCannotDisableMandatoryEligibility()
    {
        using var db = DbContextFactory.Create();
        var queue = new RecordingQueue();
        var request = JsonSerializer.Deserialize<TriggerStage4Request>("""{"fnoOnly":false,"onlyFno":false}""");
        await Service(db, queue).TriggerAsync("india", request);
        using var payload = JsonDocument.Parse(queue.Messages.Single());
        payload.RootElement.GetProperty("fnoOnly").GetBoolean().Should().BeTrue();
    }

    [Theory]
    [InlineData(true, null, null, "sample")]
    [InlineData(false, 10, null, "filtered")]
    [InlineData(false, null, 100, "filtered")]
    [InlineData(false, null, null, "full")]
    public async Task Trigger_PersistsScope(bool sample, int? limit, int? cap, string expected)
    {
        using var db = DbContextFactory.Create();
        await Service(db).TriggerAsync("india", new() { TestSampleOnly = sample, Limit = limit, MinMarketCap = cap });
        using var parameters = JsonDocument.Parse(db.JobRuns.Single().Parameters!);
        parameters.RootElement.GetProperty("scope").GetString().Should().Be(expected);
    }

    [Fact]
    public async Task Trigger_DedupesStage4IndependentlyAndRejectsDifferentActiveScope()
    {
        using var db = DbContextFactory.Create();
        db.JobRuns.Add(Run("2026-W47", jobType: "stage2_analysis", status: "running"));
        await db.SaveChangesAsync();
        var queue = new RecordingQueue();
        var service = Service(db, queue);
        var id = await service.TriggerAsync("india");
        (await service.TriggerAsync("india", new() { TestSampleOnly = false, SectorIds = [] })).Should().Be(id);
        await FluentActions.Awaiting(() => service.TriggerAsync("india", new() { Limit = 10 }))
            .Should().ThrowAsync<InvalidOperationException>().WithMessage("*different scope*");
        queue.Messages.Should().HaveCount(1);
        db.JobRuns.Should().HaveCount(2);
    }

    [Fact]
    public async Task Trigger_QueueFailurePersistsTerminalRunAndReturns503WithoutTransportDetails()
    {
        using var db = DbContextFactory.Create();
        var queue = new RecordingQueue { Fail = true };
        var controller = new Stage4AnalysisController(Service(db, queue));
        var result = (await controller.Trigger("india")).Should().BeOfType<ObjectResult>().Subject;
        result.StatusCode.Should().Be(503);
        JsonSerializer.Serialize(result.Value).Should().NotContain("transport detail");
        var job = db.JobRuns.Single();
        job.Status.Should().Be("failed");
        job.CompletedAt.Should().NotBeNull();
        job.ErrorMessage.Should().Contain("queue submission failed");
        queue.Fail = false;
        (await Service(db, queue).TriggerAsync("india")).Should().NotBe(job.Id);
    }

    [Fact]
    public async Task Retry_ReusesFrozenUniverseAndScopeForFailedSourceAndRetainsLinkage()
    {
        using var db = DbContextFactory.Create();
        var source = Run("2026-W46", status: "failed", parameters: FrozenParameters());
        db.JobRuns.Add(source);
        await db.SaveChangesAsync();
        var queue = new RecordingQueue();
        var id = await Service(db, queue).TriggerAsync("india", new() { RetrySourceRunId = source.Id });
        var retry = await db.JobRuns.SingleAsync(j => j.Id == id);
        retry.WeekNumber.Should().Be("2026-W46");
        using var parameters = JsonDocument.Parse(retry.Parameters!);
        parameters.RootElement.GetProperty("retrySourceRunId").GetInt32().Should().Be(source.Id);
        parameters.RootElement.GetProperty("universeSymbols").EnumerateArray().Select(v => v.GetString())
            .Should().Equal("OLD-FNO", "PENDING");
        parameters.RootElement.GetProperty("scope").GetString().Should().Be("filtered");
        using var payload = JsonDocument.Parse(queue.Messages.Single());
        payload.RootElement.GetProperty("minMarketCap").GetDecimal().Should().Be(100);
        payload.RootElement.GetProperty("sectorIds")[0].GetInt32().Should().Be(9);
        payload.RootElement.TryGetProperty("eligibilityCapturedAt", out _).Should().BeFalse();
        payload.RootElement.TryGetProperty("universeSymbols", out _).Should().BeFalse();
        payload.RootElement.TryGetProperty("universeSnapshot", out _).Should().BeFalse();
        payload.RootElement.GetProperty("retrySourceRunId").GetInt32().Should().Be(source.Id);
        parameters.RootElement.GetProperty("eligibilityCapturedAt").GetDateTime().Should().Be(new DateTime(2026, 11, 14));
        parameters.RootElement.GetProperty("universeSnapshot")[0].GetProperty("sector_name").GetString().Should().Be("Frozen sector");
        parameters.RootElement.GetProperty("universeSnapshot").GetArrayLength().Should().Be(2);
    }

    [Theory]
    [InlineData("india", 250)]
    [InlineData("us", 1000)]
    public async Task Retry_LargeFrozenUniverseStaysInSqlAndQueueMessageFits64KiB(string market, int count)
    {
        using var db = DbContextFactory.Create();
        var snapshot = Enumerable.Range(0, count).Select(i => new
        {
            symbol = $"FNO{i:D4}", company_name = new string('N', 100),
            sector_id = 1, sector_name = "Frozen technology sector", market_cap = 1_000_000_000
        }).ToArray();
        var frozen = JsonSerializer.Serialize(new
        {
            scope = "full", fnoOnly = true, universeSymbols = snapshot.Select(s => s.symbol),
            universeSnapshot = snapshot, eligibilityCapturedAt = Now.AddDays(-7).UtcDateTime
        });
        Convert.ToBase64String(Encoding.UTF8.GetBytes(frozen)).Length.Should().BeGreaterThan(64 * 1024);
        var source = Run("2026-W46", market: market, status: "failed", parameters: frozen);
        db.JobRuns.Add(source);
        await db.SaveChangesAsync();
        var queue = new RecordingQueue();
        var id = await Service(db, queue).TriggerAsync(market, new() { RetrySourceRunId = source.Id });
        var retry = await db.JobRuns.SingleAsync(j => j.Id == id);
        using var parameters = JsonDocument.Parse(retry.Parameters!);
        parameters.RootElement.GetProperty("universeSnapshot").GetArrayLength().Should().Be(count);
        using var payload = JsonDocument.Parse(queue.Messages.Single());
        payload.RootElement.TryGetProperty("universeSymbols", out _).Should().BeFalse();
        payload.RootElement.TryGetProperty("universeSnapshot", out _).Should().BeFalse();
        payload.RootElement.GetProperty("retrySourceRunId").GetInt32().Should().Be(source.Id);
        Convert.ToBase64String(Encoding.UTF8.GetBytes(queue.Messages.Single())).Length.Should().BeLessThan(64 * 1024);
        retry.Status.Should().Be("queued");
    }

    [Theory]
    [InlineData("""{"universeSymbols":["OLD-FNO"],"eligibilityCapturedAt":"2026-11-14T00:00:00"}""")]
    [InlineData("""{"universeSymbols":["OLD-FNO"],"universeSnapshot":[{"symbol":"OTHER"}],"eligibilityCapturedAt":"2026-11-14T00:00:00"}""")]
    [InlineData("""{"universeSymbols":[],"universeSnapshot":[],"eligibilityCapturedAt":"2026-11-14T00:00:00"}""")]
    public async Task Retry_RejectsMissingEmptyOrMismatchedMetadataSnapshot(string parameters)
    {
        using var db = DbContextFactory.Create();
        var source = Run("2026-W46", status: "failed", parameters: parameters);
        db.JobRuns.Add(source);
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => Service(db).TriggerAsync("india", new() { RetrySourceRunId = source.Id }))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*universeSnapshot*");
        db.JobRuns.Should().HaveCount(1);
    }

    [Fact]
    public async Task Retry_RejectsWrongMarketTypeWeekConflictingFiltersAndUnfrozenSources()
    {
        using var db = DbContextFactory.Create();
        var source = Run("2026-W46", parameters: FrozenParameters());
        db.JobRuns.Add(source);
        await db.SaveChangesAsync();
        var service = Service(db);
        await FluentActions.Awaiting(() => service.TriggerAsync("us", new() { RetrySourceRunId = source.Id }))
            .Should().ThrowAsync<ArgumentException>();
        foreach (var request in new TriggerStage4Request[]
        {
            new() { RetrySourceRunId = source.Id, WeekNumber = "2026-W47" },
            new() { RetrySourceRunId = source.Id, MinMarketCap = 101 },
            new() { RetrySourceRunId = source.Id, SectorIds = [] },
            new() { RetrySourceRunId = source.Id, TestSampleOnly = true },
            new() { RetrySourceRunId = source.Id, Limit = 5 }
        })
            await FluentActions.Awaiting(() => service.TriggerAsync("india", request)).Should().ThrowAsync<ArgumentException>();
        source.JobType = "stage2_analysis";
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => service.TriggerAsync("india", new() { RetrySourceRunId = source.Id }))
            .Should().ThrowAsync<ArgumentException>();
        source.JobType = "stage4_analysis";
        source.Status = "running";
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => service.TriggerAsync("india", new() { RetrySourceRunId = source.Id }))
            .Should().ThrowAsync<ArgumentException>();
        source.Status = "completed";
        source.Parameters = """{"scope":"full"}""";
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => service.TriggerAsync("india", new() { RetrySourceRunId = source.Id }))
            .Should().ThrowAsync<ArgumentException>();
        db.JobRuns.Should().HaveCount(1);
    }

    [Fact]
    public async Task Summary_SelectsLatestClosedFullWeekThenNewestSuccessfulRerun()
    {
        using var db = DbContextFactory.Create();
        var previous = Run("2026-W45");
        var first = Run("2026-W46", completedAt: Now.AddDays(-4).UtcDateTime);
        var newest = Run("2026-W46", completedAt: Now.AddDays(-3).UtcDateTime);
        var backfill = Run("2026-W44", completedAt: Now.UtcDateTime);
        var sample = Run("2026-W47", parameters: """{"scope":"sample","limit":1}""");
        var filtered = Run("2026-W47", parameters: """{"scope":"filtered","sectorIds":[1]}""");
        var failed = Run("2026-W47", status: "failed");
        var running = Run("2026-W47", status: "running");
        db.JobRuns.AddRange(previous, first, newest, backfill, sample, filtered, failed, running);
        await db.SaveChangesAsync();
        db.IndianStage4AnalysisResults.AddRange(Result(first.Id, "OLD"), Result(newest.Id, "CURRENT"),
            Result(failed.Id, "UNPUBLISHED"), Result(sample.Id, "SAMPLE"));
        await db.SaveChangesAsync();
        var service = Service(db);
        var summary = await service.GetSummaryAsync("india");
        summary!.RunId.Should().Be(newest.Id);
        summary.WeekNumber.Should().Be("2026-W46");
        summary.Top25.Should().ContainSingle().Which.Symbol.Should().Be("CURRENT");
        (await service.GetSummaryAsync("india", sample.Id))!.Scope.Should().Be("sample");
        (await service.GetSummaryAsync("india", running.Id)).Should().BeNull();
        (await service.GetSummaryAsync("india", failed.Id)).Should().BeNull();
        var history = await service.GetHistoryAsync("india");
        history.Select(h => h.WeekNumber).Should().Equal("2026-W44", "2026-W45", "2026-W46");
        history.Last().RunId.Should().Be(newest.Id);
    }

    [Fact]
    public async Task Summary_ReportsPartialCoverageRanksWeakestAndDoesNotJoinMutableCatalog()
    {
        using var db = DbContextFactory.Create();
        var run = Run("2026-W47");
        run.Metrics = """{"eligibleStocks":7,"evaluatedStocks":4,"skippedStocks":2,"errorStocks":1,"warnings":["Some history unavailable"]}""";
        db.JobRuns.Add(run);
        await db.SaveChangesAsync();
        db.IndianStage4AnalysisResults.AddRange(
            Result(run.Id, "ZZZ", rs: -8, momentum: -5, classification: "new"),
            Result(run.Id, "AAA", rs: -8, momentum: -5, classification: "reentry"),
            Result(run.Id, "WEAKER-MOMENTUM", rs: -8, momentum: -6, classification: "continuing"),
            Result(run.Id, "REMOVED", stage4: false, classification: "removed"),
            Result(run.Id, "INSUFFICIENT", stage4: null, status: "insufficient"),
            Result(run.Id, "STALE", stage4: null, status: "stale"),
            Result(run.Id, "ERROR", stage4: null, status: "error"));
        await db.SaveChangesAsync();
        var summary = (await Service(db).GetSummaryAsync("india"))!;
        summary.TotalStocks.Should().Be(7);
        summary.EvaluatedStocks.Should().Be(4);
        summary.SkippedStocks.Should().Be(2);
        summary.ErrorStocks.Should().Be(1);
        summary.Stage4Count.Should().Be(3);
        summary.NewAdditions.Should().Be(1);
        summary.ReEntries.Should().Be(1);
        summary.Continuing.Should().Be(1);
        summary.Removed.Should().Be(1);
        summary.Warnings.Should().Equal("Some history unavailable");
        summary.Top25.Select(r => r.Symbol).Should().Equal("WEAKER-MOMENTUM", "AAA", "ZZZ");
        summary.Top25.Should().OnlyContain(r => r.IsFno);
        summary.BySector.Should().ContainSingle().Which.Should().Be(new SectorStage4CountDto(1, "Frozen sector", 3, 7));
    }

    [Fact]
    public async Task History_SelectedRunPinsSnapshotAndMatchesFilteredScopeWithoutMixingFullOrSample()
    {
        using var db = DbContextFactory.Create();
        var prior = Run("2026-W45", parameters: """{"scope":"filtered","minMarketCap":100}""");
        var selected = Run("2026-W46", parameters: """{"scope":"filtered","minMarketCap":100}""",
            completedAt: Now.AddDays(-4).UtcDateTime);
        var laterRerun = Run("2026-W46", parameters: """{"scope":"filtered","minMarketCap":100}""");
        var differentFilters = Run("2026-W45", parameters: """{"scope":"filtered","minMarketCap":200}""");
        var full = Run("2026-W45");
        var newer = Run("2026-W47", parameters: """{"scope":"filtered","minMarketCap":100}""");
        db.JobRuns.AddRange(prior, selected, laterRerun, differentFilters, full, newer);
        await db.SaveChangesAsync();
        var service = Service(db);
        var history = await service.GetHistoryAsync("india", runId: selected.Id);
        history.Select(h => h.RunId).Should().Equal(prior.Id, selected.Id);
        history.Should().OnlyContain(h => h.Scope == "filtered");
        await FluentActions.Awaiting(() => service.GetHistoryAsync("us", runId: selected.Id))
            .Should().ThrowAsync<Stage4SnapshotNotFoundException>();
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task SnapshotReads_EnforceMarketJobTypeAndCompletion(string market)
    {
        using var db = DbContextFactory.Create();
        var otherMarket = Run("2026-W47", market: market == "india" ? "us" : "india");
        var stage2 = Run("2026-W47", market: market, jobType: "stage2_analysis");
        var running = Run("2026-W47", market: market, status: "running");
        db.JobRuns.AddRange(otherMarket, stage2, running);
        await db.SaveChangesAsync();
        var service = Service(db);
        foreach (var run in new[] { otherMarket, stage2, running })
        {
            (await service.GetSummaryAsync(market, run.Id)).Should().BeNull();
            (await service.GetStocksAsync(market, run.Id)).Should().BeNull();
            (await service.GetSectorRotationAsync(market, run.Id)).Should().BeNull();
        }
    }

    [Fact]
    public async Task Stocks_FiltersStoredClassificationsSectorAndMarketCapWithCoverageOptIn()
    {
        using var db = DbContextFactory.Create();
        var run = Run("2026-W47");
        db.JobRuns.Add(run);
        await db.SaveChangesAsync();
        var large = Result(run.Id, "LARGE");
        large.MarketCap = 100;
        var small = Result(run.Id, "SMALL");
        small.MarketCap = 10;
        db.IndianStage4AnalysisResults.AddRange(large, small, Result(run.Id, "EXIT", stage4: false, classification: "removed"),
            Result(run.Id, "UNKNOWN", stage4: null, status: "error"));
        await db.SaveChangesAsync();
        var service = Service(db);
        (await service.GetStocksAsync("india", run.Id))!.Should().HaveCount(4);
        (await service.GetStocksAsync("india", run.Id, "continuing"))!.Should().HaveCount(2);
        (await service.GetStocksAsync("india", run.Id, minMarketCap: 50))!.Should().ContainSingle()
            .Which.Symbol.Should().Be("LARGE");
        (await service.GetStocksAsync("india", run.Id, "removed"))!.Should().ContainSingle().Which.Symbol.Should().Be("EXIT");
        (await service.GetStocksAsync("india", run.Id, "all"))!.Should().HaveCount(4);
        (await service.GetStocksAsync("india", run.Id, sectorId: 99))!.Should().BeEmpty();
        await FluentActions.Awaiting(() => service.GetStocksAsync("india", run.Id, "invalid"))
            .Should().ThrowAsync<ArgumentException>();
    }

    [Fact]
    public async Task UsSnapshots_UseUsTableAndRotationPreservesIndicatorMeanings()
    {
        using var db = DbContextFactory.Create();
        var run = Run("2026-W47", market: "us");
        db.JobRuns.Add(run);
        await db.SaveChangesAsync();
        db.USStage4AnalysisResults.Add(new()
        {
            RunId = run.Id, WeekNumber = run.WeekNumber, Symbol = "US", SectorId = 8, SectorName = "US sector",
            AnalysisStatus = "evaluated", IsStage4 = true, RSScore = -5, RSDelta2w = 2,
            ADClassification = "distributing", EligibilityCapturedAt = Now.UtcDateTime
        });
        await db.SaveChangesAsync();
        var service = Service(db);
        (await service.GetSummaryAsync("us"))!.Stage4Count.Should().Be(1);
        (await service.GetStocksAsync("us", run.Id))!.Should().ContainSingle().Which.Symbol.Should().Be("US");
        var rotation = (await service.GetSectorRotationAsync("us", run.Id))!.Single();
        rotation.Quadrant.Should().Be("improving");
        rotation.AvgRSScore.Should().Be(-5);
        rotation.AvgRSDelta2w.Should().Be(2);
        rotation.DistributingCount.Should().Be(1);
    }

    [Fact]
    public async Task GenericJobs_ListAndCancelStage4WithoutSpecialIntegration()
    {
        using var db = DbContextFactory.Create();
        var queue = new RecordingQueue();
        var id = await Service(db, queue).TriggerAsync("india");
        var jobs = new JobService(db, queue);
        (await jobs.GetRunsAsync(jobType: "stage4_analysis")).Should().ContainSingle().Which.Id.Should().Be(id);
        (await jobs.GetRunByIdAsync(id))!.JobType.Should().Be("stage4_analysis");
        (await jobs.CancelRunAsync(id)).Should().BeTrue();
        db.JobRuns.Single().Status.Should().Be("cancelled");
    }

    [Fact]
    public async Task Schedule_DefaultsAreIndependentEnabledSaturday20AndValidateHours()
    {
        using var db = DbContextFactory.Create();
        var service = Service(db);
        var india = await service.GetScheduleAsync("india");
        var us = await service.GetScheduleAsync("us");
        india.Enabled.Should().BeTrue();
        us.HourLocal.Should().Be(20);
        await service.UpdateScheduleAsync("india", new() { Enabled = false, HourLocal = 21 });
        (await service.GetScheduleAsync("us")).Enabled.Should().BeTrue();
        db.Stage2Schedules.Should().BeEmpty();
        await FluentActions.Awaiting(() => service.UpdateScheduleAsync("us", new() { Enabled = true, HourLocal = 24 }))
            .Should().ThrowAsync<ArgumentException>();
        (await service.GetScheduleAsync("us")).HourLocal.Should().Be(20);
    }

    [Theory]
    [InlineData("india", "2026-11-21T14:29:59Z", false)]
    [InlineData("india", "2026-11-21T14:30:00Z", true)]
    [InlineData("us", "2026-11-22T00:59:59Z", false)]
    [InlineData("us", "2026-11-22T01:00:00Z", true)]
    [InlineData("india", "2026-11-22T14:30:00Z", false)]
    public async Task Schedule_UsesLocalSaturdayAndOnlyEnqueuesOnce(string market, string utc, bool expected)
    {
        using var db = DbContextFactory.Create();
        var now = DateTimeOffset.Parse(utc);
        var queue = new RecordingQueue();
        var service = Service(db, queue, now);
        (await service.EnqueueScheduledAsync(market, now)).Should().Be(expected);
        foreach (var job in db.JobRuns) job.Status = "completed";
        await db.SaveChangesAsync();
        (await service.EnqueueScheduledAsync(market, now)).Should().BeFalse();
        queue.Messages.Should().HaveCount(expected ? 1 : 0);
    }

    [Fact]
    public async Task Schedule_QueueFailureReleasesClaimAndDisabledOrActiveRunsDoNotEnqueue()
    {
        using var db = DbContextFactory.Create();
        var now = DateTimeOffset.Parse("2026-11-21T14:30:00Z");
        var queue = new RecordingQueue { Fail = true };
        var service = Service(db, queue, now);
        await FluentActions.Awaiting(() => service.EnqueueScheduledAsync("india", now))
            .Should().ThrowAsync<Stage4EnqueueException>();
        db.Stage4Schedules.Single().LastEnqueuedAt.Should().BeNull();
        queue.Fail = false;
        (await service.EnqueueScheduledAsync("india", now)).Should().BeTrue();
        (await service.EnqueueScheduledAsync("india", now.AddMinutes(1))).Should().BeFalse();
        await service.UpdateScheduleAsync("us", new() { Enabled = false });
        (await service.EnqueueScheduledAsync("us", DateTimeOffset.Parse("2026-11-22T01:00:00Z"))).Should().BeFalse();
    }

    [Fact]
    public void Persistence_HasRunScopedKeysAndStage4OnlyActiveUniqueness()
    {
        using var db = DbContextFactory.Create();
        foreach (var type in new[] { typeof(IndianStage4AnalysisResult), typeof(USStage4AnalysisResult) })
        {
            var entity = db.Model.FindEntityType(type)!;
            entity.GetIndexes().Should().Contain(i => i.IsUnique &&
                i.Properties.Select(p => p.Name).SequenceEqual(new[] { "RunId", "Symbol" }));
            entity.FindProperty("PriceAsOfDate")!.ClrType.Should().Be(typeof(DateOnly?));
            entity.FindProperty("IsStage4")!.IsNullable.Should().BeTrue();
            entity.FindProperty("RSScore")!.FindAnnotation("Relational:ColumnType")!.Value.Should().Be("decimal(20,8)");
            entity.FindProperty("ADRatio")!.FindAnnotation("Relational:ColumnType")!.Value.Should().Be("decimal(20,8)");
        }
        db.Model.FindEntityType(typeof(JobRun))!.GetIndexes().Should().Contain(i => i.IsUnique &&
            i.GetFilter() == "[JobType] = 'stage4_analysis' AND [Status] IN ('queued', 'running')");
    }

    [Fact]
    public void ResultJson_UsesStableFrontendIndicatorNames()
    {
        var dto = new Stage4AnalysisResultDto
        {
            MA10 = 10, MA30 = 30, MA30FourWeeksAgo = 31, ROC1w = -1, ROC2w = -2, ROC3w = -3,
            RSScore = -4, RSDelta1w = -5, ADRatio = 0.5m
        };
        using var json = JsonDocument.Parse(JsonSerializer.Serialize(dto, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
        foreach (var name in new[] { "ma10", "ma30", "ma30FourWeeksAgo", "roc1w", "roc2w", "roc3w", "rsScore", "rsDelta1w", "adRatio" })
            json.RootElement.TryGetProperty(name, out _).Should().BeTrue($"the Stage 4 client expects {name}");
        foreach (var name in new[] { "mA10", "mA30", "mA30FourWeeksAgo", "roC1w", "roC2w", "roC3w" })
            json.RootElement.TryGetProperty(name, out _).Should().BeFalse();
    }

    private static Stage4AnalysisService Service(MarketEdgeDbContext db, RecordingQueue? queue = null, DateTimeOffset? now = null) =>
        new(db, queue ?? new RecordingQueue(), new FixedClock(now ?? Now));

    private sealed class FixedClock(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    private sealed class RecordingQueue : QueueClient
    {
        public List<string> Messages { get; } = [];
        public bool Fail { get; set; }
        public override Task<Response> CreateIfNotExistsAsync(IDictionary<string, string>? metadata = null,
            CancellationToken cancellationToken = default) => Task.FromResult(Mock.Of<Response>());
        public override Task<Response<SendReceipt>> SendMessageAsync(string messageText, CancellationToken cancellationToken = default)
        {
            Messages.Add(Encoding.UTF8.GetString(Convert.FromBase64String(messageText)));
            if (Fail) throw new HttpRequestException("transport detail must not be exposed");
            return Task.FromResult(Mock.Of<Response<SendReceipt>>());
        }
    }

    private static JobRun Run(string week, string market = "india", string jobType = "stage4_analysis",
        string status = "completed", string? parameters = null, DateTime? completedAt = null) => new()
    {
        JobType = jobType, Market = market, WeekNumber = week, Status = status, Parameters = parameters ?? """{"scope":"full"}""",
        CreatedAt = Now.AddDays(-7).UtcDateTime, CompletedAt = completedAt ?? Now.AddDays(-1).UtcDateTime
    };

    private static IndianStage4AnalysisResult Result(int runId, string symbol, bool? stage4 = true, string status = "evaluated",
        decimal rs = -2, decimal momentum = -1, string classification = "continuing") => new()
    {
        RunId = runId, WeekNumber = "2026-W47", Symbol = symbol, CompanyName = symbol,
        SectorId = 1, SectorName = "Frozen sector", AnalysisStatus = status, IsStage4 = stage4,
        Classification = stage4.HasValue ? classification : null, RSScore = rs, MomentumScore = momentum,
        EligibilityCapturedAt = Now.UtcDateTime, CreatedAt = Now.UtcDateTime
    };

    private static string FrozenParameters() => """
        {"scope":"filtered","fnoOnly":true,"sectorIds":[9],"minMarketCap":100,"maxMarketCap":null,
        "limit":null,"testSampleOnly":false,"universeSymbols":["OLD-FNO","PENDING"],
        "universeSnapshot":[
            {"symbol":"OLD-FNO","company_name":"Frozen company","sector_id":9,"sector_name":"Frozen sector","market_cap":1000},
            {"symbol":"PENDING","company_name":"Pending company","sector_id":9,"sector_name":"Frozen sector","market_cap":2000}
        ],
        "eligibilityCapturedAt":"2026-11-14T00:00:00"}
        """;
}
