using System.Security.Claims;
using Azure.Storage.Queues;
using MarketEdge.Api.Authentication;
using MarketEdge.Api.Controllers;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsDeleteTests
{
    private static OptionsWorkspaceService Service(MarketEdgeDbContext db) =>
        new(db, new Mock<QueueClient>().Object, null!, new OptionsSettings());

    private static OptionsController Controller(MarketEdgeDbContext db, string? account = "alice")
    {
        var claims = account is null ? [] : new[] { new Claim("tid", "tenant"), new Claim("oid", account) };
        return new OptionsController(Service(db), null!, null!, new OptionsSettings(),
            new AuthOptions { Enabled = true }, Mock.Of<ILogger<OptionsController>>())
        {
            ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext
                {
                    User = new ClaimsPrincipal(account is null ? new ClaimsIdentity() : new ClaimsIdentity(claims, "test"))
                }
            }
        };
    }

    private static async Task SeedAsync(MarketEdgeDbContext db, string status = "completed",
        string? aiStatus = "completed", string jobStatus = "completed")
    {
        db.OptionsIndices.Add(new OptionsMarketIndex { Code = "NIFTY50", Name = "Nifty 50" });
        db.OptionsConnections.Add(new BrokerConnection { Id = 1, UserKey = "tenant:alice", Status = "connected", EncryptedToken = "keep" });
        db.OptionsConstituents.Add(new IndexConstituentHistory { Id = 1, IndexCode = "NIFTY50", StockId = 1, WeightPct = 100 });
        db.OptionsSessions.Add(new OptionsExchangeSession { Id = 1, IndexCode = "NIFTY50", IsTradingDay = true });
        db.OptionsInstruments.Add(new OptionsDerivativeInstrument { Exchange = "NFO", TradingSymbol = "KEEP", InstrumentToken = 1 });
        db.OptionsSnapshotBatches.Add(new OptionsSnapshotBatch { Id = 1, UserKey = "tenant:alice", IndexCode = "NIFTY50", Status = "completed" });
        db.OptionsQuotes.Add(new OptionsQuoteSnapshot { Id = 1, BatchId = 1, LastPrice = 123 });
        db.JobRuns.AddRange(
            new JobRun { Id = 1, JobType = "options_analysis", Status = jobStatus },
            new JobRun { Id = 2, JobType = "unrelated_feature", Status = "running" });
        db.OptionsForecasts.AddRange(
            new OptionsForecastRun { Id = 1, UserKey = "tenant:alice", Status = status, JobRunId = 1, SnapshotBatchId = 1, IndexCode = "NIFTY50", Mode = "daily", RequestId = Guid.NewGuid() },
            new OptionsForecastRun { Id = 2, UserKey = "tenant:bob", Status = "completed", SnapshotBatchId = 1, IndexCode = "NIFTY50", Mode = "daily", RequestId = Guid.NewGuid() },
            new OptionsForecastRun { Id = 3, UserKey = "tenant:alice", Status = "completed", SnapshotBatchId = 1, IndexCode = "NIFTY50", Mode = "weekly", RequestId = Guid.NewGuid() });
        foreach (var id in new[] { 1, 2, 3 })
        {
            db.OptionsStrategies.Add(new OptionsStrategySuggestion { Id = id, ForecastRunId = id, CandidateKey = $"candidate-{id}" });
            db.OptionsLegs.Add(new OptionsStrategyLeg { Id = id, StrategySuggestionId = id });
            db.OptionsValuations.Add(new OptionsValuationSnapshot { Id = id, ForecastRunId = id, StockId = 1 });
            if (id != 1 || aiStatus is not null)
                db.OptionsAiSelections.Add(new OptionsAiSelection { Id = id, ForecastRunId = id, Status = id == 1 ? aiStatus! : "completed" });
            foreach (var version in new[] { 1, 2 })
            {
                db.OptionsForecastOutcomes.Add(new OptionsForecastOutcome { Id = id * 10 + version, ForecastRunId = id, Version = version });
                db.OptionsStrategyOutcomes.Add(new OptionsStrategyOutcome { Id = id * 10 + version, StrategySuggestionId = id, Version = version });
            }
        }
        await db.SaveChangesAsync();
        db.ChangeTracker.Clear();
    }

    [Theory]
    [InlineData("completed", "completed")]
    [InlineData("failed", "failed")]
    [InlineData("cancelled", "skipped")]
    [InlineData("completed", null)]
    public async Task Delete_TerminalForecastRemovesAllPrivateDependentsButPreservesSharedData(string status, string? ai)
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db, status, ai);

        Assert.IsType<NoContentResult>(await Controller(db).Delete(1, default));
        db.ChangeTracker.Clear();
        Assert.Equal(new[] { 2, 3 }, await db.OptionsForecasts.OrderBy(x => x.Id).Select(x => x.Id).ToArrayAsync());
        Assert.Equal(new[] { 2, 3 }, await db.OptionsStrategies.OrderBy(x => x.Id).Select(x => x.ForecastRunId).ToArrayAsync());
        Assert.Equal(new[] { 2, 3 }, await db.OptionsLegs.OrderBy(x => x.Id).Select(x => x.StrategySuggestionId).ToArrayAsync());
        Assert.Equal(new[] { 2, 3 }, await db.OptionsValuations.OrderBy(x => x.Id).Select(x => x.ForecastRunId).ToArrayAsync());
        Assert.Equal(new[] { 2, 3 }, await db.OptionsAiSelections.OrderBy(x => x.Id).Select(x => x.ForecastRunId).ToArrayAsync());
        Assert.Equal(new[] { 2, 2, 3, 3 }, await db.OptionsForecastOutcomes.OrderBy(x => x.Id).Select(x => x.ForecastRunId).ToArrayAsync());
        Assert.Equal(new[] { 2, 2, 3, 3 }, await db.OptionsStrategyOutcomes.OrderBy(x => x.Id).Select(x => x.StrategySuggestionId).ToArrayAsync());
        Assert.Equal(2, await db.JobRuns.CountAsync());
        Assert.Equal("completed", (await db.JobRuns.FindAsync(1))!.Status);
        Assert.Equal("running", (await db.JobRuns.FindAsync(2))!.Status);
        Assert.Equal(1, await db.OptionsSnapshotBatches.CountAsync());
        Assert.Equal(123m, (await db.OptionsQuotes.SingleAsync()).LastPrice);
        Assert.Equal("keep", (await db.OptionsConnections.SingleAsync()).EncryptedToken);
        Assert.Single(await db.OptionsConstituents.ToListAsync());
        Assert.Single(await db.OptionsSessions.ToListAsync());
        Assert.Single(await db.OptionsIndices.ToListAsync());
        Assert.Single(await db.OptionsInstruments.ToListAsync());
        Assert.IsType<NotFoundResult>(await Controller(db).Delete(1, default));
    }

    [Theory]
    [InlineData(2)]
    [InlineData(99)]
    public async Task Delete_OtherAccountsAndAbsentForecastsAreIndistinguishable(int id)
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db);
        Assert.IsType<NotFoundResult>(await Controller(db).Delete(id, default));
        Assert.Equal(3, await db.OptionsForecasts.CountAsync());
        Assert.Equal(3, await db.OptionsAiSelections.CountAsync());
        Assert.Equal(6, await db.OptionsStrategyOutcomes.CountAsync());
    }

    [Fact]
    public async Task Delete_RequiresAccountIdentityWhenAuthenticationEnabled()
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db);
        await Assert.ThrowsAsync<UnauthorizedAccessException>(() => Controller(db, null).Delete(1, default));
        Assert.Equal(3, await db.OptionsForecasts.CountAsync());
    }

    [Theory]
    [InlineData("queued", "completed", "completed")]
    [InlineData("running", "completed", "completed")]
    [InlineData("unavailable", "completed", "completed")]
    [InlineData("completed", "pending", "completed")]
    [InlineData("completed", "running", "completed")]
    [InlineData("completed", "in-flight", "completed")]
    [InlineData("completed", "completed", "queued")]
    [InlineData("completed", "completed", "running")]
    public async Task Delete_ActiveOrNonTerminalWorkReturnsConflictWithoutDeletingAnything(string forecast, string ai, string job)
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db, forecast, ai, job);
        var result = Assert.IsType<ConflictObjectResult>(await Controller(db).Delete(1, default));
        var problem = Assert.IsType<ProblemDetails>(result.Value);
        Assert.Equal(409, problem.Status);
        Assert.Contains("no active AI selection or linked job", problem.Title);
        Assert.Equal(3, await db.OptionsForecasts.CountAsync());
        Assert.Equal(3, await db.OptionsStrategies.CountAsync());
        Assert.Equal(3, await db.OptionsLegs.CountAsync());
        Assert.Equal(3, await db.OptionsValuations.CountAsync());
        Assert.Equal(3, await db.OptionsAiSelections.CountAsync());
        Assert.Equal(6, await db.OptionsForecastOutcomes.CountAsync());
        Assert.Equal(6, await db.OptionsStrategyOutcomes.CountAsync());
        Assert.Equal(2, await db.JobRuns.CountAsync());
    }

    [Fact]
    public async Task Delete_NoLinkedJobOrAiClaimStillDeletesTerminalForecast()
    {
        using var db = DbContextFactory.Create();
        db.OptionsForecasts.Add(new OptionsForecastRun { Id = 1, UserKey = "tenant:alice", Status = "cancelled" });
        await db.SaveChangesAsync();
        Assert.IsType<NoContentResult>(await Controller(db).Delete(1, default));
        Assert.Empty(await db.OptionsForecasts.ToListAsync());
    }
}
