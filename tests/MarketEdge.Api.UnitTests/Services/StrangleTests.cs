using System.Text;
using System.Text.Json;
using Azure;
using Azure.Storage.Queues;
using Azure.Storage.Queues.Models;
using FluentAssertions;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class StrangleTests
{
    private static OptionsSettings Settings(bool ai = true) => new()
    {
        AiEnabled = ai,
        KiteApiKey = "test-key", KiteApiSecret = "test-secret",
        KiteRedirectUrl = "http://localhost/api/india/options/connection/callback",
        TokenEncryptionKey = Convert.ToBase64String(Enumerable.Range(0, 32).Select(x => (byte)x).ToArray())
    };
    private static StrangleService Service(MarketEdgeDbContext db, QueueClient? queue = null) =>
        new(db, queue ?? new Mock<QueueClient>().Object,
            new OptionsBrokerService(db, new HttpClient(), Settings(), new OptionsTokenCipher(Settings())));
    private static StrangleRunRequest Request() => new(Guid.NewGuid(), "NIFTY50", "entry", 1000, null, null);
    [Fact]
    public async Task LatestRun_IsAccountIndexScopedIncludesFailedLatestAndDoesNotDelete()
    {
        using var db = DbContextFactory.Create();
        var old = Run();
        var latest = Run(Request() with { Action = "adjustment" });
        latest.Status = "failed";
        db.StrangleRuns.AddRange(old, latest, Run(user: "bob"), Run(Request() with { IndexCode = "SENSEX" }));
        await db.SaveChangesAsync();
        var service = Service(db);
        var result = JsonSerializer.SerializeToElement(await service.ListAsync("alice", "NIFTY50", default, 1), StrangleService.Json);
        result.GetProperty("items").GetArrayLength().Should().Be(1);
        result.GetProperty("items")[0].GetProperty("id").GetInt32().Should().Be(latest.Id);
        result.GetProperty("items")[0].GetProperty("status").GetString().Should().Be("failed");
        (await db.StrangleRuns.CountAsync()).Should().Be(4);
        foreach (var invalid in new[] { 0, 101 })
            await FluentActions.Awaiting(() => service.ListAsync("alice", "NIFTY50", default, invalid))
                .Should().ThrowAsync<ArgumentException>();
    }

    private static StrangleRun Run(StrangleRunRequest? request = null, string user = "alice")
    {
        request ??= Request();
        return new StrangleRun
        {
            UserKey = user, RequestId = request.RequestId, IndexCode = request.IndexCode, Action = request.Action,
            StrategyId = request.StrategyId,
            RequestJson = JsonSerializer.Serialize(request, StrangleService.Json), Status = "completed", GeneratedAt = DateTime.UtcNow
        };
    }

    [Fact]
    public void Requests_RejectUnboundedOrUnlinkedActions()
    {
        var request = Request();
        StrangleService.Validate(request);
        foreach (var invalid in new[]
        {
            request with { RequestId = Guid.Empty }, request with { IndexCode = "OTHER" }, request with { Action = "order" },
            request with { WingWidth = 0 }, request with { WingWidth = 100001 }, request with { XOverride = -1 },
            request with { StrategyId = 1 }, request with { Action = "adjustment", StrategyId = 0 }
        })
            FluentActions.Invoking(() => StrangleService.Validate(invalid)).Should().Throw<ArgumentException>();
        StrangleService.Validate(request with { Action = "adjustment", StrategyId = 1 });
        StrangleService.Validate(request with { Action = "adjustment", StrategyId = null });
        FluentActions.Invoking(() => StrangleService.Validate(request with
            { Action = "adjustment", StrategyId = null, SelectedTradeIds = ["fill"] })).Should().Throw<ArgumentException>();
    }

    [Fact]
    public async Task Idempotency_UsesAccountAndAllRequestParametersWithoutRevalidatingBroker()
    {
        using var db = DbContextFactory.Create();
        var request = Request();
        var row = Run(request);
        db.StrangleRuns.Add(row);
        await db.SaveChangesAsync();
        var service = Service(db);
        (await service.CreateAsync("alice", request, default)).Id.Should().Be(row.Id);
        (await service.CreateAsync("alice", request with { WingWidth = 1000.00m }, default)).Id.Should().Be(row.Id);
        await FluentActions.Awaiting(() => service.CreateAsync("alice", request with { WingWidth = 2000 }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*different parameters*");
        (await service.DetailAsync("bob", row.Id, default)).Should().BeNull();
        await FluentActions.Awaiting(() => service.CreateAsync("bob", request, default)).Should().ThrowAsync<ArgumentException>();
    }

    [Fact]
    public async Task ModelInputs_AreExplicitBoundedAndPartOfRequestIdentity()
    {
        var request = Request() with { RiskFreeRate = .06m, DividendYield = 0m };
        StrangleService.Validate(request);
        StrangleService.Validate(request with { RiskFreeRate = -.05m, DividendYield = 1m });
        foreach (var invalid in new[]
        {
            request with { RiskFreeRate = 6 }, request with { RiskFreeRate = -.051m },
            request with { DividendYield = -.01m }, request with { DividendYield = 1.01m }
        })
            FluentActions.Invoking(() => StrangleService.Validate(invalid)).Should().Throw<ArgumentException>();
        using var db = DbContextFactory.Create();
        var row = Run(request);
        db.StrangleRuns.Add(row);
        await db.SaveChangesAsync();
        var service = Service(db);
        (await service.CreateAsync("alice", request, default)).Id.Should().Be(row.Id);
        await FluentActions.Awaiting(() => service.CreateAsync("alice",
            request with { DividendYield = .01m }, default)).Should().ThrowAsync<ArgumentException>()
            .WithMessage("*different parameters*");
        row.RequestJson.Should().Contain("\"riskFreeRate\":0.06").And.Contain("\"dividendYield\":0");
    }

    [Fact]
    public async Task Reconciliation_TradeIdsAreBoundedAdjustmentOnlyAndPersistedRequestIdentity()
    {
        var request = Request() with { Action = "adjustment", StrategyId = 7, SelectedTradeIds = ["fill-b", "fill-a"] };
        StrangleService.Validate(request);
        foreach (var invalid in new[]
        {
            request with { Action = "entry", StrategyId = null },
            request with { SelectedTradeIds = [] }, request with { SelectedTradeIds = ["same", "same"] },
            request with { SelectedTradeIds = ["  ambiguous "] },
            request with { SelectedTradeIds = Enumerable.Range(0, 201).Select(x => x.ToString()).ToList() }
        })
            FluentActions.Invoking(() => StrangleService.Validate(invalid)).Should().Throw<ArgumentException>();
        using var db = DbContextFactory.Create();
        var run = Run(request);
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var service = Service(db);
        (await service.CreateAsync("alice", request with { SelectedTradeIds = ["fill-a", "fill-b"] }, default)).Id.Should().Be(run.Id);
        await FluentActions.Awaiting(() => service.CreateAsync("alice", request with { SelectedTradeIds = ["fill-c"] }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*different parameters*");
        db.StrangleRuns.Single().RequestJson.Should().Contain("fill-a").And.Contain("fill-b");
    }

    [Fact]
    public async Task Reconciliation_CannotClaimAnotherStrategySessionFills()
    {
        using var db = DbContextFactory.Create();
        var oldRequest = Request() with { Action = "adjustment", StrategyId = 7, SelectedTradeIds = ["fill-original"] };
        db.StrangleRuns.Add(Run(oldRequest));
        db.StrangleStrategies.Add(new StrangleStrategy
        {
            Id = 8, UserKey = "alice", IndexCode = "NIFTY50", SessionDate = StrangleService.Today
        });
        var settings = Settings();
        var cipher = new OptionsTokenCipher(settings);
        db.OptionsConnections.Add(new BrokerConnection
        {
            UserKey = "alice", Status = "connected", EncryptedToken = cipher.Encrypt("alice", "broker-secret"),
            ExpiresAt = DateTime.UtcNow.AddHours(1)
        });
        await db.SaveChangesAsync();
        var queue = new FailingQueue();
        var service = new StrangleService(db, queue,
            new OptionsBrokerService(db, new HttpClient(new ProfileHandler()), settings, cipher));
        await FluentActions.Awaiting(() => service.CreateAsync("alice",
                oldRequest with { RequestId = Guid.NewGuid(), StrategyId = 8 }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*another strategy*");
        queue.Payload.Should().BeNull();
        db.JobRuns.Should().BeEmpty();
        db.StrangleRuns.Should().HaveCount(1);
    }

    [Fact]
    public async Task ActiveScope_OnlyBlocksSameAccountIndexAction()
    {
        using var db = DbContextFactory.Create();
        var row = Run();
        row.Status = "running";
        db.StrangleRuns.Add(row);
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => Service(db).CreateAsync("alice", Request(), default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*active run*");
        await FluentActions.Awaiting(() => Service(db).CreateAsync("bob", Request(), default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*Connect*");
    }

    [Fact]
    public async Task Adjustment_RejectsOtherAccountsOldSessionsAndWrongIndex()
    {
        using var db = DbContextFactory.Create();
        db.StrangleStrategies.Add(new StrangleStrategy { Id = 3, UserKey = "bob", IndexCode = "NIFTY50", SessionDate = StrangleService.Today });
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => Service(db).CreateAsync("alice", Request() with { Action = "adjustment", StrategyId = 3 }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*same-session*");
    }

    private sealed class ProfileHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            request.Method.Should().Be(HttpMethod.Get);
            request.RequestUri!.AbsolutePath.Should().Be("/user/profile");
            return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK));
        }
    }
    private sealed class FailingQueue : QueueClient
    {
        public string? Payload { get; private set; }
        public override Task<Response> CreateIfNotExistsAsync(IDictionary<string, string>? metadata = null, CancellationToken cancellationToken = default) =>
            Task.FromResult(Mock.Of<Response>());
        public override Task<Response<SendReceipt>> SendMessageAsync(string messageText, CancellationToken cancellationToken = default)
        {
            Payload = Encoding.UTF8.GetString(Convert.FromBase64String(messageText));
            throw new HttpRequestException("queue secret details");
        }
    }

    [Fact]
    public async Task QueueFailure_PersistsBothTerminalRecordsAndExactWorkerEnvelope()
    {
        using var db = DbContextFactory.Create();
        var settings = Settings();
        var cipher = new OptionsTokenCipher(settings);
        db.OptionsConnections.Add(new BrokerConnection
        {
            UserKey = "alice", Status = "connected", EncryptedToken = cipher.Encrypt("alice", "broker-secret"),
            ExpiresAt = DateTime.UtcNow.AddHours(1)
        });
        await db.SaveChangesAsync();
        var queue = new FailingQueue();
        var broker = new OptionsBrokerService(db, new HttpClient(new ProfileHandler()), settings, cipher);
        var service = new StrangleService(db, queue, broker);
        var request = Request();
        await FluentActions.Awaiting(() => service.CreateAsync("alice", request, default)).Should().ThrowAsync<InvalidOperationException>();
        var run = db.StrangleRuns.Single();
        var job = db.JobRuns.Single();
        run.Status.Should().Be("failed");
        job.Status.Should().Be("failed");
        run.CompletedAt.Should().NotBeNull();
        run.JobRunId.Should().Be(job.Id);
        run.ErrorMessage.Should().NotContain("secret");
        var payload = JsonSerializer.Deserialize<JsonElement>(queue.Payload!);
        payload.GetProperty("runId").GetInt32().Should().Be(job.Id);
        payload.GetProperty("strangleRunId").GetInt32().Should().Be(run.Id);
        payload.GetProperty("userKey").GetString().Should().Be("alice");
        payload.GetProperty("jobType").GetString().Should().Be("options_strangle");
        (await service.CreateAsync("alice", request, default)).Status.Should().Be("failed");
        db.JobRuns.Should().HaveCount(1);
    }

    [Fact]
    public async Task InterruptedPublicationOrJob_IsTerminalWithoutAutomaticRedispatch()
    {
        using var db = DbContextFactory.Create();
        db.JobRuns.Add(new JobRun { Id = 7, JobType = "options_strangle", Status = "queued", CreatedAt = DateTime.UtcNow.AddMinutes(-6) });
        var run = Run();
        run.Status = "queued";
        run.JobRunId = 7;
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        await Service(db).ReconcileInterruptedJobsAsync(default);
        run.Status.Should().Be("failed");
        db.JobRuns.Single().Status.Should().Be("failed");
        run.ErrorMessage.Should().Contain("fresh request");
    }

    private static async Task<(StrangleRun Run, StrangleConfirmRequest Request)> SeedEntry(MarketEdgeDbContext db)
    {
        var legs = new List<StrangleBaselineLeg>
        {
            new("NFO", "SHORTPE", "MIS", -50, 180), new("NFO", "SHORTCE", "MIS", -50, 180),
            new("NFO", "LONGPE", "MIS", 50, 30), new("NFO", "LONGCE", "MIS", 50, 30)
        };
        var run = Run();
        run.ResultJson = JsonSerializer.Serialize(new
        {
            executionStatus = "advisory-only", asOf = DateTimeOffset.UtcNow, blockers = Array.Empty<string>(), lotSize = 50,
            legs = legs.Select(x => new
            {
                x.Exchange, x.TradingSymbol, x.Product, quantity = Math.Abs(x.Quantity),
                direction = x.Quantity < 0 ? "sell" : "buy", x.EntryPrice
            })
        }, StrangleService.Json);
        db.StrangleRuns.Add(run);
        var strikes = new[] { 24000m, 25000m, 23000m, 26000m };
        for (var i = 0; i < legs.Count; i++)
            db.OptionsInstruments.Add(new OptionsDerivativeInstrument
            {
                Exchange = "NFO", TradingSymbol = legs[i].TradingSymbol, Underlying = "NIFTY50", Strike = strikes[i],
                InstrumentType = i % 2 == 0 ? "PE" : "CE", LotSize = 50, Expiry = StrangleService.Today.AddDays(1)
            });
        await db.SaveChangesAsync();
        return (run, new StrangleConfirmRequest(run.Id, legs, 100));
    }

    [Fact]
    public async Task Baseline_FreezesActualCashFlowsOriginalRupeeBudgetAndFingerprint()
    {
        using var db = DbContextFactory.Create();
        var (_, request) = await SeedEntry(db);
        var service = Service(db);
        await service.ConfirmAsync("alice", request, default);
        var row = db.StrangleStrategies.Single();
        var baseline = JsonSerializer.Deserialize<JsonElement>(row.BaselineJson);
        baseline.GetProperty("originalRiskBudget").GetDecimal().Should().Be(5000);
        baseline.GetProperty("originalMaxLoss").GetDecimal().Should().Be(35000);
        baseline.GetProperty("quantity").GetInt32().Should().Be(50);
        baseline.GetProperty("positionsConfirmed").GetBoolean().Should().BeFalse();
        baseline.GetProperty("legs").GetArrayLength().Should().Be(4);
        baseline.GetProperty("fingerprint").GetString().Should().HaveLength(64);
        var original = row.BaselineJson;
        await service.ConfirmAsync("alice", request, default);
        db.StrangleStrategies.Should().HaveCount(1);
        await FluentActions.Awaiting(() => service.ConfirmAsync("alice", request with { XPoints = 110 }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*immutable*");
        row.BaselineJson.Should().Be(original);
        (await service.StrategyAsync("bob", row.Id, default)).Should().BeNull();
    }

    [Fact]
    public async Task Baseline_AcceptsWorkerAdvisoryContractAndConvertsDirectionsToSignedQuantities()
    {
        using var db = DbContextFactory.Create();
        var (run, request) = await SeedEntry(db);
        run.ResultJson = JsonSerializer.Serialize(new
        {
            executionStatus = "advisory-only", asOf = DateTimeOffset.UtcNow, blockers = Array.Empty<string>(), lotSize = 50,
            legs = request.Legs.Select(x => new
            {
                x.Exchange, x.TradingSymbol, x.Product, quantity = Math.Abs(x.Quantity),
                direction = x.Quantity < 0 ? "sell" : "buy", x.EntryPrice
            })
        }, StrangleService.Json);
        await db.SaveChangesAsync();
        await Service(db).ConfirmAsync("alice", request, default);
        db.StrangleStrategies.Should().HaveCount(1);
    }

    [Fact]
    public async Task Baseline_RejectsWrongOwnershipUnattainableXAndInventedLegs()
    {
        using var db = DbContextFactory.Create();
        var (_, request) = await SeedEntry(db);
        var service = Service(db);
        await FluentActions.Awaiting(() => service.ConfirmAsync("bob", request, default)).Should().ThrowAsync<ArgumentException>();
        await FluentActions.Awaiting(() => service.ConfirmAsync("alice", request with { XPoints = 151 }, default)).Should().ThrowAsync<ArgumentException>();
        var altered = request.Legs.Select((x, i) => i == 0 ? x with { TradingSymbol = "INVENTED" } : x).ToList();
        await FluentActions.Awaiting(() => service.ConfirmAsync("alice", request with { Legs = altered }, default)).Should().ThrowAsync<ArgumentException>();
        db.StrangleStrategies.Should().BeEmpty();
    }

    [Theory]
    [InlineData(100, true)]
    [InlineData(75, false)]
    public async Task Baseline_AcceptsActualCompleteLotMultiplesButNeverPartialLots(int actualQuantity, bool valid)
    {
        using var db = DbContextFactory.Create();
        var (_, request) = await SeedEntry(db);
        request = request with { Legs = request.Legs.Select(x => x with { Quantity = Math.Sign(x.Quantity) * actualQuantity }).ToList() };
        var service = Service(db);
        if (!valid)
        {
            await FluentActions.Awaiting(() => service.ConfirmAsync("alice", request, default))
                .Should().ThrowAsync<ArgumentException>().WithMessage("*complete-lot*");
            db.StrangleStrategies.Should().BeEmpty();
            return;
        }
        await service.ConfirmAsync("alice", request, default);
        var baseline = JsonSerializer.Deserialize<JsonElement>(db.StrangleStrategies.Single().BaselineJson);
        baseline.GetProperty("quantity").GetInt32().Should().Be(100);
        baseline.GetProperty("originalRiskBudget").GetDecimal().Should().Be(10000);
        baseline.GetProperty("originalMaxLoss").GetDecimal().Should().Be(70000);
        baseline.GetProperty("legs").EnumerateArray().Select(x => Math.Abs(x.GetProperty("quantity").GetInt32()))
            .Should().OnlyContain(x => x == 100);
    }

    [Fact]
    public async Task Baseline_NewSnapshotCannotResetBudgetForOverlappingSessionExposure()
    {
        using var db = DbContextFactory.Create();
        var (entry, request) = await SeedEntry(db);
        var service = Service(db);
        await service.ConfirmAsync("alice", request, default);
        var original = db.StrangleStrategies.Single().BaselineJson;
        var freshEntry = Run();
        freshEntry.ResultJson = entry.ResultJson;
        db.StrangleRuns.Add(freshEntry);
        await db.SaveChangesAsync();
        await FluentActions.Awaiting(() => service.ConfirmAsync("alice",
                request with { EntryRunId = freshEntry.Id, XPoints = 120 }, default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*overlapping exposure*");
        db.StrangleStrategies.Should().HaveCount(1);
        db.StrangleStrategies.Single().BaselineJson.Should().Be(original);
    }

    [Fact]
    public async Task GenericJobs_HidePrivateStranglesAndCancelBothRecordsOnlyForOwner()
    {
        using var db = DbContextFactory.Create();
        db.JobRuns.AddRange(new JobRun { Id = 1, JobType = "options_strangle", Status = "running" },
            new JobRun { Id = 2, JobType = "stage2_analysis", Status = "running" });
        var run = Run();
        run.JobRunId = 1;
        run.Status = "running";
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var service = new JobService(db, new Mock<QueueClient>().Object);
        (await service.GetRunsAsync(userKey: "bob")).Select(x => x.Id).Should().Equal(2);
        (await service.GetRunsAsync()).Select(x => x.Id).Should().Equal(2);
        (await service.GetRunByIdAsync(1, "bob")).Should().BeNull();
        (await service.CancelRunAsync(1, "bob")).Should().BeFalse();
        (await service.CancelRunAsync(1, "alice")).Should().BeTrue();
        run.Status.Should().Be("cancelled");
        run.CompletedAt.Should().NotBeNull();
    }

    private static StrangleRun Adjustment()
    {
        var run = Run(Request() with { Action = "adjustment", StrategyId = 1 });
        run.ResultJson = JsonSerializer.Serialize(new
        {
            executionStatus = "advisory-only", blockers = Array.Empty<string>(), strategyPnlRupees = 0,
            exitPolicy = ExitPolicy(), candidates = new[] { new { id = "hold", action = "hold" } },
            context = new { regime = "mixed" }
        }, StrangleService.Json);
        return run;
    }

    private static object ExitPolicy() => new
    {
        version = "sl-target-time-v1", reason = (string?)null, stopPnlRupees = -5000, targetPnlRupees = 10000
    };

    private static StrangleRun PositionReview()
    {
        var run = Run(Request() with { Action = "adjustment" });
        run.ResultJson = JsonSerializer.Serialize(new
        {
            analysisMode = "current-positions", executionStatus = "review-only", asOf = DateTimeOffset.UtcNow,
            blockers = Array.Empty<string>(), positions = new[] { new { quantity = -75 } },
            positionRisk = new[] { new { exchange = "NFO", tradingSymbol = "HELDCE", signedQuantity = -75,
                unavailableReason = "Explicit model inputs unavailable" } },
            candidates = new[] { new { id = "review-current-positions", action = "review" } }
        }, StrangleService.Json);
        return run;
    }

    private static StrangleRun CurrentPositionsWithActions()
    {
        var run = PositionReview();
        var greeks = new { quantityWeighted = new { delta = -10, gamma = -.1m, vega = -50, theta = 100 } };
        run.ResultJson = JsonSerializer.Serialize(new
        {
            analysisMode = "current-positions", executionStatus = "review-only", asOf = DateTimeOffset.UtcNow,
            exitAt = DateTimeOffset.UtcNow.AddMinutes(10), mandatoryExit = false,
            blockers = Array.Empty<string>(), positions = new[] { new { quantity = -75 } },
            positionRisk = new[] { new { exchange = "NFO", tradingSymbol = "HELDCE", signedQuantity = -75 } },
            greeks,
            sessionPnl = new { status = "verified", realizedRupees = 2000, unrealizedRupees = -1000, totalRupees = 1000 },
            payoff = new { status = "available", maxProfitRupees = 10000, maxLossRupees = 20000 },
            riskPlan = new { status = "available", suggestedSlRupees = 5000, remainingLossRupees = 6000,
                stopPnlRupees = -5000, targetPnlRupees = 10000, stressLossRupees = 2000 },
            exitPolicy = ExitPolicy(),
            regime = new { state = "risk-on", sourceIndexCode = "NIFTY50", timeframe = "5minute", direction = "neutral" },
            premiumBalance = new { putPremium = 100, callPremium = 102, imbalanceRatio = .02m, preferred = true,
                reason = "Similar premiums within risk bounds." },
            modelInputs = new { riskFreeRate = .06m, dividendYield = 0 },
            candidates = new[]
            {
                new { id = "stay-current", action = "hold", eligible = true, maxLoss = 20000,
                    stressLossRupees = 1000, remainingRiskBudget = 6000, greeksAfter = greeks, structure = "hedged-strangle" }
            }
        }, StrangleService.Json);
        return run;
    }

    [Fact]
    public void Ai_CurrentActionInputIncludesRealizedAdjustmentProfitsAndRupeeLimits()
    {
        var run = CurrentPositionsWithActions();
        var input = StrangleAiValidation.BuildInput(run);
        input.Should().Contain("\"realizedRupees\":2000").And.Contain("\"unrealizedRupees\":-1000")
            .And.Contain("\"totalRupees\":1000").And.Contain("\"suggestedSlRupees\":5000")
            .And.Contain("\"maxProfitRupees\":10000").And.Contain("\"dividendYield\":0")
            .And.Contain("\"delta\":-10").And.Contain("\"targetPnlRupees\":10000")
            .And.Contain("\"premiumBalance\"").And.Contain("\"sourceIndexCode\":\"NIFTY50\"");
        StrangleAiValidation.ValidateOutput("""{"candidateId":"stay-current","reason":"Stay hedged while monitoring the observed risk headroom."}""", input)
            .GetProperty("candidateId").GetString().Should().Be("stay-current");
        FluentActions.Invoking(() => StrangleAiValidation.ValidateOutput(
            """{"candidateId":"new-contract","reason":"Switch exposure"}""", input)).Should().Throw<InvalidOperationException>();
    }

    [Theory]
    [InlineData("\"stressLossRupees\":1000", "\"stressLossRupees\":6001")]
    [InlineData("\"maxLoss\":20000", "\"maxLoss\":20001")]
    [InlineData("\"suggestedSlRupees\":5000", "\"suggestedSlRupees\":6000")]
    [InlineData("\"totalRupees\":1000", "\"totalRupees\":-5000")]
    [InlineData("\"delta\":-10", "\"delta\":null")]
    [InlineData("\"mandatoryExit\":false", "\"mandatoryExit\":true")]
    [InlineData("\"targetPnlRupees\":10000", "\"targetPnlRupees\":11000")]
    [InlineData("\"totalRupees\":1000", "\"totalRupees\":10000")]
    [InlineData("\"version\":\"sl-target-time-v1\"", "\"version\":\"old-policy\"")]
    [InlineData("\"reason\":null", "\"reason\":\"target\"")]
    [InlineData("\"action\":\"hold\"", "\"action\":\"close\"")]
    [InlineData("\"action\":\"hold\"", "\"action\":\"reduce\"")]
    public void Ai_CurrentActionsCannotExpandSessionRiskOrBypassMissingGreeks(string before, string after)
    {
        var run = CurrentPositionsWithActions();
        run.ResultJson = run.ResultJson!.Replace(before, after);
        FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void Ai_HedgedStraddleRequiresRiskOnAndStrictGreekStressImprovement()
    {
        var run = CurrentPositionsWithActions();
        run.ResultJson = run.ResultJson!.Replace("\"action\":\"hold\"", "\"action\":\"roll\"")
            .Replace("\"structure\":\"hedged-strangle\"", "\"structure\":\"hedged-straddle\"");
        var valid = run.ResultJson;
        StrangleAiValidation.BuildInput(run).Should().Contain("hedged-straddle");
        foreach (var (before, after) in new[]
        {
            ("\"state\":\"risk-on\"", "\"state\":\"risk-off\""),
            ("\"stressLossRupees\":1000", "\"stressLossRupees\":2000"),
            ("\"stressLossRupees\":1000", "\"stressLossRupees\":2001"),
            ("\"sourceIndexCode\":\"NIFTY50\"", "\"sourceIndexCode\":\"SENSEX\""),
            ("\"timeframe\":\"5minute\"", "\"timeframe\":\"minute\""),
            ("\"gamma\":-0.1", "\"gamma\":null")
        })
        {
            run.ResultJson = valid.Replace(before, after);
            FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
        }
    }

    [Theory]
    [InlineData("risk-on", "down")]
    [InlineData("risk-off", "up")]
    public void Ai_SellingRegimeIsSeparateFromDirectionAndPremiumEquality(string regime, string direction)
    {
        var run = CurrentPositionsWithActions();
        run.ResultJson = run.ResultJson!.Replace("\"action\":\"hold\"", "\"action\":\"roll\"")
            .Replace("\"state\":\"risk-on\"", $"\"state\":\"{regime}\"")
            .Replace("\"direction\":\"neutral\"", $"\"direction\":\"{direction}\"")
            .Replace("\"callPremium\":102", "\"callPremium\":200")
            .Replace("\"imbalanceRatio\":0.02", "\"imbalanceRatio\":0.6667")
            .Replace("\"preferred\":true", "\"preferred\":false");
        StrangleAiValidation.BuildInput(run).Should().Contain("\"callPremium\":200")
            .And.Contain($"\"state\":\"{regime}\"").And.Contain($"\"direction\":\"{direction}\"");
    }

    [Theory]
    [InlineData("stop-loss", -5000)]
    [InlineData("target", 10000)]
    [InlineData("time", 1000)]
    public async Task Ai_RequiredExitNeverCallsPaidProvider(string reason, int pnl)
    {
        using var db = DbContextFactory.Create();
        var run = CurrentPositionsWithActions();
        run.ResultJson = run.ResultJson!.Replace("\"reason\":null", $"\"reason\":\"{reason}\"")
            .Replace("\"totalRupees\":1000", $"\"totalRupees\":{pnl}")
            .Replace("\"mandatoryExit\":false", "\"mandatoryExit\":true");
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var client = new Mock<IAzureAiFoundryClient>(MockBehavior.Strict);
        await Selector(db, client).ProcessAsync(run.Id, default);
        run.AiJson.Should().Contain("\"status\":\"skipped\"");
        client.VerifyNoOtherCalls();
    }

    [Fact]
    public void Ai_PositionReviewNeedsNoBaselineButNeverAdmitsRollCandidates()
    {
        var run = PositionReview();
        var input = StrangleAiValidation.BuildInput(run);
        input.Should().Contain("current-positions").And.Contain("HELDCE").And.Contain("unavailableReason");
        StrangleAiValidation.ValidateOutput(
            """{"candidateId":"review-current-positions","reason":"Short exposure needs monitoring; model Greeks are unavailable."}""", input)
            .GetProperty("candidateId").GetString().Should().Be("review-current-positions");
        FluentActions.Invoking(() => StrangleAiValidation.ValidateOutput(
            """{"candidateId":"roll","reason":"Roll the short"}""", input)).Should().Throw<InvalidOperationException>();
        run.ResultJson = run.ResultJson!.Replace("\"action\":\"review\"", "\"action\":\"roll\"");
        FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public async Task Ai_EmptyCurrentPositionsSkipPaidProvider()
    {
        using var db = DbContextFactory.Create();
        var run = PositionReview();
        run.ResultJson = run.ResultJson!.Replace("\"positions\":[{\"quantity\":-75}]", "\"positions\":[]");
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var client = new Mock<IAzureAiFoundryClient>(MockBehavior.Strict);
        await Selector(db, client).ProcessAsync(run.Id, default);
        var ai = JsonSerializer.Deserialize<JsonElement>(run.AiJson!);
        ai.GetProperty("status").GetString().Should().Be("skipped");
        client.VerifyNoOtherCalls();
    }

    [Fact]
    public async Task Ai_CurrentPositionsReachConfiguredProviderWithoutSavedStrategy()
    {
        using var db = DbContextFactory.Create();
        var run = PositionReview();
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var client = new Mock<IAzureAiFoundryClient>();
        client.Setup(x => x.CreateResponseAsync(It.IsAny<string>(),
                It.Is<string>(input => input.Contains("current-positions") && input.Contains("HELDCE")),
                false, 400, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new FoundryResponse(
                """{"candidateId":"review-current-positions","reason":"Short exposure needs monitoring; model Greeks are unavailable."}""",
                new AiInferenceUsage(350, 50, 0)));
        var selector = Selector(db, client);
        await selector.ProcessAsync(run.Id, default);
        await selector.ProcessAsync(run.Id, default);
        var ai = JsonSerializer.Deserialize<JsonElement>(run.AiJson!);
        ai.GetProperty("status").GetString().Should().Be("completed");
        ai.GetProperty("output").GetProperty("candidateId").GetString().Should().Be("review-current-positions");
        db.StrangleStrategies.Should().BeEmpty();
        client.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400,
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public void CompletedAdjustment_ReportsPendingAiUntilHostedClaimIsPersisted()
    {
        var run = Adjustment();
        StrangleService.Dto(run).Ai!.Value.GetProperty("status").GetString().Should().Be("pending");
        StrangleService.Dto(Run()).Ai.Should().BeNull();
        run.AiJson = """{"status":"failed"}""";
        StrangleService.Dto(run).Ai!.Value.GetProperty("status").GetString().Should().Be("failed");
    }
    private static StrangleAiSelector Selector(MarketEdgeDbContext db, Mock<IAzureAiFoundryClient> client, bool enabled = true)
    {
        var options = Options.Create(new AzureAiFoundryOptions { Model = "test" });
        return new(db, client.Object, options, new AiAnalysisCostCalculator(options), Settings(enabled));
    }

    [Theory]
    [InlineData("""{"candidateId":"unknown","reason":"Stay hedged"}""")]
    [InlineData("""{"candidateId":"hold","reason":"Buy 99"}""")]
    [InlineData("""{"candidateId":"hold","reason":"Stay hedged","price":23}""")]
    [InlineData("""{"candidateId":"hold"}""")]
    [InlineData("not json")]
    public void Ai_RejectsUnknownOrUnboundedOutputs(string output)
    {
        var input = StrangleAiValidation.BuildInput(Adjustment());
        FluentActions.Invoking(() => StrangleAiValidation.ValidateOutput(output, input)).Should().Throw<Exception>();
    }

    [Fact]
    public void Ai_StopOrDiscretionaryExitCannotReachProvider()
    {
        var run = Adjustment();
        run.ResultJson = run.ResultJson!.Replace("\"executionStatus\":\"advisory-only\"", "\"executionStatus\":\"exit-required\"");
        FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
        run = Adjustment();
        run.ResultJson = run.ResultJson!.Replace("\"action\":\"hold\"", "\"action\":\"close\"");
        FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void Ai_WorkerContractSupportsOnlyFreshValidatedAdvisoryCandidates()
    {
        var run = Adjustment();
        run.ResultJson = JsonSerializer.Serialize(new
        {
            executionStatus = "advisory-only", asOf = DateTimeOffset.UtcNow, exitAt = DateTimeOffset.UtcNow.AddMinutes(2),
            blockers = Array.Empty<string>(), candidates = new[] { new { id = "hold", action = "hold" } },
            originalRiskBudget = 5000, strategyPnlRupees = 0, exitPolicy = ExitPolicy()
        }, StrangleService.Json);
        StrangleAiValidation.BuildInput(run).Should().Contain("originalRiskBudget");
        var result = JsonSerializer.Deserialize<JsonElement>(run.ResultJson);
        FluentActions.Invoking(() => StrangleAiValidation.ValidateFreshness(result, DateTimeOffset.UtcNow.AddSeconds(61)))
            .Should().Throw<InvalidOperationException>();
        run.ResultJson = run.ResultJson.Replace("advisory-only", "exit-required");
        FluentActions.Invoking(() => StrangleAiValidation.BuildInput(run)).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public async Task Ai_ClaimsOnceNoToolsAndPersistsUsageWithoutChangingDeterministicResult()
    {
        using var db = DbContextFactory.Create();
        var run = Adjustment();
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var result = run.ResultJson;
        var client = new Mock<IAzureAiFoundryClient>();
        client.Setup(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new FoundryResponse("""{"candidateId":"hold","reason":"Preserve remaining risk budget"}""", new AiInferenceUsage(400, 40, 10)));
        var selector = Selector(db, client);
        await selector.ProcessAsync(run.Id, default);
        await selector.ProcessAsync(run.Id, default);
        client.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()), Times.Once);
        var ai = JsonSerializer.Deserialize<JsonElement>(run.AiJson!);
        ai.GetProperty("status").GetString().Should().Be("completed");
        ai.GetProperty("inputTokens").GetInt64().Should().Be(400);
        ai.GetProperty("output").GetProperty("candidateId").GetString().Should().Be("hold");
        run.ResultJson.Should().Be(result);
    }

    [Fact]
    public async Task Ai_ProviderFailureOrInterruptedClaimNeverCausesPaidRetry()
    {
        using var db = DbContextFactory.Create();
        var run = Adjustment();
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var client = new Mock<IAzureAiFoundryClient>();
        client.Setup(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("secret provider payload"));
        var selector = Selector(db, client);
        await selector.ProcessAsync(run.Id, default);
        await selector.ProcessAsync(run.Id, default);
        run.AiJson.Should().Contain("\"status\":\"failed\"").And.NotContain("secret provider");
        client.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()), Times.Once);
        run.AiJson = JsonSerializer.Serialize(new { status = "running", startedAt = DateTime.UtcNow.AddMinutes(-10) });
        await db.SaveChangesAsync();
        await selector.ExpireClaimsAsync(default);
        run.AiJson.Should().Contain("interrupted");
        await selector.ProcessAsync(run.Id, default);
        client.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()), Times.Once);
    }

    private sealed class MutableClock(DateTimeOffset now) : TimeProvider
    {
        public DateTimeOffset Now { get; set; } = now;
        public override DateTimeOffset GetUtcNow() => Now;
    }

    [Theory]
    [InlineData(70, 120)]
    [InlineData(10, 5)]
    public async Task Ai_InferenceCrossingFreshnessOrDeadlineKeepsUsageButPublishesNoRecommendation(int inferenceSeconds, int deadlineSeconds)
    {
        using var db = DbContextFactory.Create();
        var clock = new MutableClock(DateTimeOffset.UtcNow);
        var run = Adjustment();
        run.ResultJson = JsonSerializer.Serialize(new
        {
            executionStatus = "advisory-only", blockers = Array.Empty<string>(), asOf = clock.Now,
            exitAt = clock.Now.AddSeconds(deadlineSeconds), candidates = new[] { new { id = "hold", action = "hold" } },
            strategyPnlRupees = 0, exitPolicy = ExitPolicy()
        }, StrangleService.Json);
        db.StrangleRuns.Add(run);
        await db.SaveChangesAsync();
        var historicalResult = run.ResultJson;
        var client = new Mock<IAzureAiFoundryClient>();
        client.Setup(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()))
            .Callback(() => clock.Now = clock.Now.AddSeconds(inferenceSeconds))
            .ReturnsAsync(new FoundryResponse("""{"candidateId":"hold","reason":"Preserve remaining risk budget"}""", new AiInferenceUsage(400, 40, 10)));
        var options = Options.Create(new AzureAiFoundryOptions { Model = "test" });
        var selector = new StrangleAiSelector(db, client.Object, options, new AiAnalysisCostCalculator(options), Settings(), clock);
        await selector.ProcessAsync(run.Id, default);
        await selector.ProcessAsync(run.Id, default);
        var ai = JsonSerializer.Deserialize<JsonElement>(run.AiJson!);
        ai.GetProperty("status").GetString().Should().Be("failed");
        ai.GetProperty("output").ValueKind.Should().Be(JsonValueKind.Null);
        ai.GetProperty("inputTokens").GetInt64().Should().Be(400);
        ai.GetProperty("outputTokens").GetInt64().Should().Be(40);
        ai.GetProperty("costUsd").ValueKind.Should().Be(JsonValueKind.Number);
        ai.GetProperty("errorMessage").GetString().Should().Contain("AI unavailable");
        run.ResultJson.Should().Be(historicalResult);
        client.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 400, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Ai_NeverRunsForEntryAndDisabledSelectionIsExplicit()
    {
        using var db = DbContextFactory.Create();
        var entry = Run();
        var adjustment = Adjustment();
        db.StrangleRuns.AddRange(entry, adjustment);
        await db.SaveChangesAsync();
        var client = new Mock<IAzureAiFoundryClient>(MockBehavior.Strict);
        var selector = Selector(db, client, false);
        await selector.ProcessAsync(entry.Id, default);
        await selector.ProcessAsync(adjustment.Id, default);
        entry.AiJson.Should().BeNull();
        adjustment.AiJson.Should().Contain("\"status\":\"skipped\"");
    }
}
