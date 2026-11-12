using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Net;
using Azure.Storage.Queues;
using FluentAssertions;
using MarketEdge.Api.Authentication;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Hosting;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsWorkspaceTests
{
    private static OptionsSettings Settings() => new() { TokenEncryptionKey = Convert.ToBase64String(Enumerable.Range(0, 32).Select(x => (byte)x).ToArray()) };

    [Fact]
    public void Crypto_UsesSharedNonceTagCipherFormatAndUserAad()
    {
        var settings = Settings();
        var cipher = new OptionsTokenCipher(settings);
        var encrypted = cipher.Encrypt("tenant:alice", "broker-secret");
        cipher.Decrypt("tenant:alice", encrypted).Should().Be("broker-secret");
        var packed = Convert.FromBase64String(encrypted);
        packed.Length.Should().Be(28 + Encoding.UTF8.GetByteCount("broker-secret"));
        var plain = new byte[packed.Length - 28];
        using var aes = new AesGcm(Convert.FromBase64String(settings.TokenEncryptionKey), 16);
        aes.Decrypt(packed.AsSpan(0, 12), packed.AsSpan(28), packed.AsSpan(12, 16), plain, Encoding.UTF8.GetBytes("tenant:alice"));
        Encoding.UTF8.GetString(plain).Should().Be("broker-secret");
        FluentActions.Invoking(() => cipher.Decrypt("tenant:bob", encrypted)).Should().Throw<CryptographicException>();
        packed[^1] ^= 1;
        FluentActions.Invoking(() => cipher.Decrypt("tenant:alice", Convert.ToBase64String(packed))).Should().Throw<CryptographicException>();
        cipher.Encrypt("tenant:alice", "broker-secret").Should().NotBe(encrypted);
    }

    [Theory]
    [InlineData("2026-11-09T00:00:00Z", "2026-11-10T00:30:00Z")]
    [InlineData("2026-11-09T20:00:00Z", "2026-11-11T00:30:00Z")]
    public void Expiry_IsNextCalendarDaySixIst(string now, string expected) =>
        OptionsTokenCipher.ExpectedExpiry(DateTime.Parse(now).ToUniversalTime()).Should().Be(DateTime.Parse(expected).ToUniversalTime());

    [Fact]
    public void Identity_IsTenantBoundAndLocalModeStable()
    {
        OptionsIdentity.UserKey(new ClaimsPrincipal(), new AuthOptions()).Should().Be("local-development");
        var user = new ClaimsPrincipal(new ClaimsIdentity(new[] { new Claim("oid", "alice"), new Claim("tid", "tenant") }, "test"));
        OptionsIdentity.UserKey(user, new AuthOptions { Enabled = true }).Should().Be("tenant:alice");
        FluentActions.Invoking(() => OptionsIdentity.UserKey(new ClaimsPrincipal(), new AuthOptions { Enabled = true })).Should().Throw<UnauthorizedAccessException>();
    }

    [Fact]
    public void MetadataPermissions_SeparateGlobalWritesFromReadAccess()
    {
        var auth = new AuthOptions { Enabled = true };
        var settings = Settings();
        var reader = new ClaimsPrincipal(new ClaimsIdentity(new[] { new Claim("roles", "Api.Access") }, "test"));
        OptionsIdentity.CanImportMetadata(reader, auth, settings).Should().BeFalse();
        var administrator = new ClaimsPrincipal(new ClaimsIdentity(new[] { new Claim("roles", "Options.MetadataAdmin") }, "test"));
        OptionsIdentity.CanImportMetadata(administrator, auth, settings).Should().BeTrue();
        OptionsIdentity.CanImportMetadata(new ClaimsPrincipal(), auth, settings).Should().BeFalse();
        OptionsIdentity.CanImportMetadata(new ClaimsPrincipal(), new AuthOptions(), settings).Should().BeTrue();
    }

    [Fact]
    public void LoginCorrelation_IsBrowserBoundAndOneTime()
    {
        var root = Path.Combine(Directory.GetCurrentDirectory(), ".local", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var environment = new Mock<IWebHostEnvironment>();
            environment.SetupGet(x => x.ContentRootPath).Returns(root);
            var correlation = new OptionsLoginCorrelation(new EphemeralDataProtectionProvider(), environment.Object);
            var first = correlation.Create("tenant:alice");
            var other = correlation.Create("tenant:bob");
            FluentActions.Invoking(() => correlation.Consume(first.State, other.Cookie)).Should().Throw<CryptographicException>();
            correlation.Consume(first.State, first.Cookie).Should().Be("tenant:alice");
            FluentActions.Invoking(() => correlation.Consume(first.State, first.Cookie)).Should().Throw<IOException>();
            FluentActions.Invoking(() => correlation.Consume("..\\escape", first.Cookie)).Should().Throw<CryptographicException>();
        }
        finally { Directory.Delete(root, true); }
    }

    private static OptionsMetadataImport Metadata() => new("NIFTY50", new DateOnly(2026, 11, 9), "Official imported source",
        [new("ONE", 40), new("TWO", 60)],
        [new(new DateOnly(2026, 11, 9), "NSE", true, "09:15", "15:30", true, false)]);

    [Fact]
    public void Metadata_ValidatesWeightsSessionsAndExplicitDates()
    {
        var valid = Metadata();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid)).Should().NotThrow();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { Constituents = [new("ONE", 99)] })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { Constituents = [new("ONE", 50), new("one", 50)] })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { Sessions = [valid.Sessions[0] with { CloseTime = "08:00" }] })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { Sessions = [valid.Sessions[0] with { IsTradingDay = false }] })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { Sessions = [valid.Sessions[0] with { Exchange = "BSE" }] })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { EffectiveDate = default })).Should().Throw<ArgumentException>();
        FluentActions.Invoking(() => OptionsWorkspaceService.ValidateMetadata(valid with { IndexCode = "NASDAQ" })).Should().Throw<ArgumentException>();
    }

    [Fact]
    public async Task Broker_ExpiresAndClearsCredentialsBeforeAnyNetworkCall()
    {
        using var db = DbContextFactory.Create();
        db.OptionsConnections.Add(new BrokerConnection { UserKey = "alice", Status = "connected", EncryptedToken = "secret", ExpiresAt = DateTime.UtcNow.AddSeconds(-1) });
        await db.SaveChangesAsync();
        var service = new OptionsBrokerService(db, new HttpClient(), Settings(), new OptionsTokenCipher(Settings()));
        var row = await service.GetAsync("alice", default);
        row!.Status.Should().Be("relogin-required");
        row.EncryptedToken.Should().BeNull();
        (await service.GetAsync("bob", default)).Should().BeNull();
    }

    [Fact]
    public async Task Forecasts_ActiveScopeIsAccountIndexAndModeButRequestRetriesAreIdempotent()
    {
        using var db = DbContextFactory.Create();
        using var http = new HttpClient();
        var existing = new OptionsForecastRun
        {
            UserKey = "alice", IndexCode = "NIFTY50", Mode = "daily",
            Status = "queued", RequestId = Guid.NewGuid()
        };
        db.OptionsForecasts.Add(existing);
        await db.SaveChangesAsync();
        var broker = new OptionsBrokerService(db, http, Settings(), new OptionsTokenCipher(Settings()));
        var service = new OptionsWorkspaceService(db, new Mock<QueueClient>().Object, broker, Settings());
        var retry = await service.CreateAsync("alice",
            new OptionsForecastRequest("NIFTY50", "daily", existing.RequestId), default);
        retry.Id.Should().Be(existing.Id);
        await FluentActions.Awaiting(() => service.CreateAsync("alice",
            new OptionsForecastRequest("NIFTY50", "daily", Guid.NewGuid()), default))
            .Should().ThrowAsync<ArgumentException>().WithMessage("*already active*");
        foreach (var request in new[]
                 {
                     ("bob", "NIFTY50", "daily"),
                     ("alice", "SENSEX", "daily"),
                     ("alice", "NIFTY50", "monthly")
                 })
        {
            // These scopes pass concurrency validation and reach the disconnected-broker gate.
            await FluentActions.Awaiting(() => service.CreateAsync(request.Item1,
                new OptionsForecastRequest(request.Item2, request.Item3, Guid.NewGuid()), default))
                .Should().ThrowAsync<InvalidOperationException>();
        }
    }

    [Fact]
    public void Forecasts_ActiveUniquenessUsesOptionsScopeNotAnIsoWeek()
    {
        var settings = new DbContextOptionsBuilder<MarketEdgeDbContext>()
            .UseSqlServer("Server=unused;Database=unused;Trusted_Connection=True;TrustServerCertificate=True").Options;
        using var db = new MarketEdgeDbContext(settings);
        var index = db.Model.FindEntityType(typeof(OptionsForecastRun))!.GetIndexes()
            .Single(x => x.GetDatabaseName() == "UX_Options_ForecastRuns_Active");
        index.IsUnique.Should().BeTrue();
        index.Properties.Select(x => x.Name).Should().Equal("UserKey", "IndexCode", "Mode");
        index.GetFilter().Should().Be("[Status] IN (N'queued', N'running')");
    }

    [Fact]
    public async Task Broker_EarlyAuthenticationRejectionClearsToken()
    {
        using var db = DbContextFactory.Create();
        var settings = Settings();
        settings.KiteApiKey = "api";
        settings.KiteApiSecret = "secret";
        settings.KiteRedirectUrl = "https://example.test/api/india/options/connection/callback";
        var cipher = new OptionsTokenCipher(settings);
        db.OptionsConnections.Add(new BrokerConnection
        {
            UserKey = "alice", Status = "connected", ExpiresAt = DateTime.UtcNow.AddHours(1),
            EncryptedToken = cipher.Encrypt("alice", "broker-secret")
        });
        await db.SaveChangesAsync();
        using var http = new HttpClient(new RejectedBroker("TokenException"));
        var service = new OptionsBrokerService(db, http, settings, cipher);
        await FluentActions.Awaiting(() => service.ValidateAsync("alice", default)).Should().ThrowAsync<ArgumentException>();
        var row = await service.GetAsync("alice", default);
        row!.Status.Should().Be("relogin-required");
        row.EncryptedToken.Should().BeNull();
    }

    [Fact]
    public async Task Broker_PermissionRejectionPreservesSavedSession()
    {
        using var db = DbContextFactory.Create();
        var settings = Settings();
        settings.KiteApiKey = "api";
        settings.KiteApiSecret = "secret";
        settings.KiteRedirectUrl = "https://example.test/api/india/options/connection/callback";
        var cipher = new OptionsTokenCipher(settings);
        var encrypted = cipher.Encrypt("alice", "broker-secret");
        db.OptionsConnections.Add(new BrokerConnection
        {
            UserKey = "alice", Status = "connected", ExpiresAt = DateTime.UtcNow.AddHours(1), EncryptedToken = encrypted
        });
        await db.SaveChangesAsync();
        using var http = new HttpClient(new RejectedBroker("PermissionException"));
        var service = new OptionsBrokerService(db, http, settings, cipher);
        await FluentActions.Awaiting(() => service.ValidateAsync("alice", default)).Should().ThrowAsync<InvalidOperationException>();
        var row = await service.GetAsync("alice", default);
        row!.Status.Should().Be("connected");
        row.EncryptedToken.Should().Be(encrypted);
    }

    private sealed class RejectedBroker(string errorType) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.Forbidden)
            {
                Content = new StringContent(JsonSerializer.Serialize(new { status = "error", error_type = errorType }))
            });
    }

    [Fact]
    public async Task ForecastRequest_DeduplicatesBeforeBrokerOrQueueAndRejectsChangedParameters()
    {
        using var db = DbContextFactory.Create();
        var request = new OptionsForecastRequest("NIFTY50", "daily", Guid.NewGuid());
        var run = new OptionsForecastRun
        {
            UserKey = "alice", RequestId = request.RequestId, IndexCode = request.IndexCode,
            Mode = request.Mode, GeneratedAt = DateTime.UtcNow
        };
        db.OptionsForecasts.Add(run);
        await db.SaveChangesAsync();
        var service = new OptionsWorkspaceService(db, new Mock<QueueClient>(MockBehavior.Strict).Object,
            new OptionsBrokerService(db, new HttpClient(), Settings(), new OptionsTokenCipher(Settings())), Settings());
        (await service.CreateAsync("alice", request, default)).Id.Should().Be(run.Id);
        await FluentActions.Awaiting(() => service.CreateAsync("alice", request with { Mode = "monthly" }, default))
            .Should().ThrowAsync<ArgumentException>();
        await FluentActions.Awaiting(() => service.CreateAsync("bob", request, default))
            .Should().ThrowAsync<InvalidOperationException>();
    }

    [Fact]
    public async Task ForecastReads_AreOwnUserOnlyAndMappingsUseDedicatedSchema()
    {
        using var db = DbContextFactory.Create();
        var run = new OptionsForecastRun { UserKey = "alice", RequestId = Guid.NewGuid(), IndexCode = "NIFTY50", Mode = "daily", GeneratedAt = DateTime.UtcNow };
        db.OptionsForecasts.Add(run);
        await db.SaveChangesAsync();
        var service = new OptionsWorkspaceService(db, new Mock<QueueClient>().Object,
            new OptionsBrokerService(db, new HttpClient(), Settings(), new OptionsTokenCipher(Settings())), Settings());
        (await service.DetailAsync("bob", run.Id, default)).Should().BeNull();
        (await service.DetailAsync("alice", run.Id, default)).Should().NotBeNull();
        var tables = db.Model.GetEntityTypes().Where(x => x.GetSchema() == "options").ToList();
        tables.Should().HaveCount(14);
        db.Model.FindEntityType(typeof(JobRun))!.GetSchema().Should().NotBe("options");
    }

    [Fact]
    public void SqlModel_UsesExactTablesCompositeInstrumentIdentityAndUniqueAiClaim()
    {
        using var db = new MarketEdgeDbContext(new DbContextOptionsBuilder<MarketEdgeDbContext>()
            .UseSqlServer("Server=unused;Database=unused;Trusted_Connection=True;TrustServerCertificate=True").Options);
        db.Model.GetEntityTypes().Count(x => x.GetSchema() == "options").Should().Be(14);
        db.Model.FindEntityType(typeof(OptionsDerivativeInstrument))!.FindPrimaryKey()!.Properties
            .Select(x => x.Name).Should().Equal("Exchange", "TradingSymbol");
        db.Model.FindEntityType(typeof(OptionsAiSelection))!.GetIndexes().Should()
            .Contain(x => x.IsUnique && x.Properties.Single().Name == "ForecastRunId");
        db.Model.FindEntityType(typeof(OptionsForecastRun))!.GetIndexes().Should()
            .Contain(x => x.IsUnique && x.Properties.Select(p => p.Name).SequenceEqual(new[] { "UserKey", "RequestId" }));
    }

    private static string Input() => """{"scenarios":[{"id":"base","low":1,"high":2}],"candidates":[{"id":"eligible","side":"buying","riskTier":"low","scenarioIds":["base"]}]}""";
    private static string Output(string? candidate = null) => JsonSerializer.Serialize(new
    {
        scenarioId = "base",
        scenarioReason = "Range reflects the captured evidence",
        recommendations = (from side in new[] { "selling", "buying" }
                           from riskTier in new[] { "low", "medium", "high" }
                           select new { side, riskTier, candidateId = side == "buying" && riskTier == "low" ? candidate : null, reason = "No suitable trade" })
    });

    [Fact]
    public void AiOutput_RequiresExistingEligibleIdsAndSixExactBucketsWithoutNumericClaims()
    {
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output("eligible"), Input())).Should().NotThrow();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output(), Input())).Should().NotThrow();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output("invented"), Input())).Should().Throw<InvalidOperationException>();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output("eligible").Replace("\"base\"", "\"invented\""), Input())).Should().Throw<InvalidOperationException>();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output().Replace("No suitable trade", "Gain 50 percent"), Input())).Should().Throw<InvalidOperationException>();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput("""{"scenarioId":"base","recommendations":[]}""", Input())).Should().Throw<InvalidOperationException>();
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output().Replace("\"reason\":", "\"extra\":0,\"reason\":"), Input())).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void AiInput_IsBoundedAndIncludesBothSidesAndAllTiers()
    {
        var run = new OptionsForecastRun { IndexCode = "NIFTY50", Mode = "daily", AnalysisJson = """{"bias":"neutral","scenarios":[{"id":"base","low":1,"high":2}]}""" };
        var candidates = (from side in new[] { "selling", "buying" }
                          from tier in new[] { "low", "medium", "high" }
                          from number in Enumerable.Range(1, 5)
                          let id = $"{side}-{tier}-{number}"
                          select new OptionsStrategySuggestion
                          {
                              CandidateKey = id, Side = side, RiskTier = tier,
                              PayloadJson = JsonSerializer.Serialize(new
                              {
                                  id, side, riskTier = tier, strategy = side == "buying" ? "long-call" : "put-credit-spread",
                                  expiry = "2026-11-12", maxLoss = 100, capitalBasis = 100, estimatedStopLoss = 80,
                                  scenarioIds = new[] { "base" }, warnings = new[] { "Relative tier does not imply safety" },
                                  scenarioProfitProbabilities = new Dictionary<string, object>
                                  {
                                      ["base"] = new { probability = 0.6m, probabilityType = "model-implied", reason = "Conditional risk neutral grid estimate", gridPoints = 1001 }
                                  }
                              })
                          }).ToList();
        using var input = JsonDocument.Parse(OptionsAiValidation.BuildInput(run, candidates));
        input.RootElement.GetProperty("c").GetArrayLength().Should().BeInRange(6, 12);
        input.RootElement.TryGetProperty("v", out _).Should().BeTrue();
        OptionsAiValidation.CountInputTokens(input.RootElement.GetRawText())
            .Should().BeLessOrEqualTo(2372);
        FluentActions.Invoking(() => OptionsAiValidation.ValidateOutput(Output("buying-low-1"), input.RootElement.GetRawText()))
            .Should().NotThrow();
        OptionsAiValidation.MaxOutputTokens.Should().Be(800);
        run.AnalysisJson = """{"scenarios":[{"id":"a"},{"id":"b"},{"id":"c"},{"id":"d"}]}""";
        FluentActions.Invoking(() => OptionsAiValidation.BuildInput(run, candidates)).Should().Throw<InvalidOperationException>();
        run.AnalysisJson = JsonSerializer.Serialize(new { scenarios = new[] { new { id = "base", label = new string('x', 13000) } } });
        FluentActions.Invoking(() => OptionsAiValidation.BuildInput(run, candidates)).Should().NotThrow();
        var oversizedRisk = System.Text.Json.Nodes.JsonNode.Parse(candidates[0].PayloadJson)!;
        oversizedRisk["warnings"] = new System.Text.Json.Nodes.JsonArray(string.Join(" ", Enumerable.Range(0, 5000).Select(i => $"risk{i}")));
        candidates[0].PayloadJson = oversizedRisk.ToJsonString();
        FluentActions.Invoking(() => OptionsAiValidation.BuildInput(run, candidates)).Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public async Task AiSelector_ClaimsOnceDisablesToolsAndReusesOwnCache()
    {
        using var db = DbContextFactory.Create();
        var first = new OptionsForecastRun { UserKey = "alice", Status = "completed", IndexCode = "NIFTY50", Mode = "daily", AnalysisJson = """{"scenarios":[{"id":"base","low":24000,"high":26000}]}""" };
        db.OptionsForecasts.Add(first);
        await db.SaveChangesAsync();
        var foundry = new Mock<IAzureAiFoundryClient>();
        foundry.Setup(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 800, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new FoundryResponse(Output(), new AiInferenceUsage(100, 50, 0)));
        var options = Options.Create(new AzureAiFoundryOptions { Model = "model", EarningsCallToolCostUsd = 5, InputUsdPerMillionTokens = 1 });
        var selector = new OptionsAiSelector(db, foundry.Object, options, new AiAnalysisCostCalculator(options), Settings());
        await selector.ProcessAsync(first.Id, default);
        await selector.ProcessAsync(first.Id, default);
        var second = new OptionsForecastRun { UserKey = "alice", Status = "completed", IndexCode = first.IndexCode, Mode = first.Mode, AnalysisJson = first.AnalysisJson };
        db.OptionsForecasts.Add(second);
        await db.SaveChangesAsync();
        await selector.ProcessAsync(second.Id, default);
        foundry.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 800, It.IsAny<CancellationToken>()), Times.Once);
        var selections = await db.OptionsAiSelections.OrderBy(x => x.Id).ToListAsync();
        selections.Select(x => x.Status).Should().OnlyContain(x => x == "completed");
        selections[0].CostUsd.Should().Be(0.0001m);
        selections[1].CostUsd.Should().Be(0);
    }

    [Fact]
    public async Task AiSelector_InvalidResponseIsTerminalAndStillRecordsUsage()
    {
        using var db = DbContextFactory.Create();
        var run = new OptionsForecastRun { UserKey = "alice", Status = "completed", IndexCode = "NIFTY50", Mode = "daily", AnalysisJson = """{"scenarios":[{"id":"base"}]}""" };
        db.OptionsForecasts.Add(run);
        await db.SaveChangesAsync();
        var foundry = new Mock<IAzureAiFoundryClient>();
        foundry.Setup(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 800, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new FoundryResponse("""{"scenarioId":"invented","recommendations":[]}""", new AiInferenceUsage(100, 50, 0)));
        var options = Options.Create(new AzureAiFoundryOptions { Model = "model", InputUsdPerMillionTokens = 1 });
        var selector = new OptionsAiSelector(db, foundry.Object, options, new AiAnalysisCostCalculator(options), Settings());
        await selector.ProcessAsync(run.Id, default);
        await selector.ProcessAsync(run.Id, default);
        foundry.Verify(x => x.CreateResponseAsync(It.IsAny<string>(), It.IsAny<string>(), false, 800, It.IsAny<CancellationToken>()), Times.Once);
        var selection = await db.OptionsAiSelections.SingleAsync();
        selection.Status.Should().Be("failed");
        selection.OutputJson.Should().BeNull();
        selection.InputTokens.Should().Be(100);
        selection.CostUsd.Should().Be(0.0001m);
    }

    [Fact]
    public async Task DisabledAi_IsTerminalBeforeAndAfterProcessingAndIsNeverRetried()
    {
        using var db = DbContextFactory.Create();
        var run = new OptionsForecastRun
        {
            UserKey = "alice", Status = "completed", IndexCode = "NIFTY50", Mode = "daily",
            RequestId = Guid.NewGuid()
        };
        db.OptionsForecasts.Add(run);
        await db.SaveChangesAsync();
        var settings = Settings();
        settings.AiEnabled = false;
        var workspace = new OptionsWorkspaceService(db, new Mock<QueueClient>().Object, null!, settings);
        var detail = JsonSerializer.SerializeToElement(await workspace.DetailAsync("alice", run.Id, default));
        detail.GetProperty("ai").GetProperty("status").GetString().Should().Be("skipped");
        detail.GetProperty("ai").GetProperty("errorMessage").GetString().Should().Contain("disabled");

        var foundry = new Mock<IAzureAiFoundryClient>(MockBehavior.Strict);
        var options = Options.Create(new AzureAiFoundryOptions());
        var selector = new OptionsAiSelector(db, foundry.Object, options, new AiAnalysisCostCalculator(options), settings);
        await selector.ProcessAsync(run.Id, default);
        settings.AiEnabled = true;
        await selector.ProcessAsync(run.Id, default);

        var selection = await db.OptionsAiSelections.SingleAsync();
        selection.Status.Should().Be("skipped");
        selection.InputTokens.Should().Be(0);
        selection.OutputTokens.Should().Be(0);
        foundry.VerifyNoOtherCalls();
        detail = JsonSerializer.SerializeToElement(await workspace.DetailAsync("alice", run.Id, default));
        detail.GetProperty("ai").GetProperty("status").GetString().Should().Be("skipped");
        (await workspace.DeleteAsync("alice", run.Id, default)).Should().Be(OptionsForecastDeleteResult.Deleted);
    }

    [Fact]
    public void NoToolCost_PreservesLegacyToolAccounting()
    {
        var calculator = new AiAnalysisCostCalculator(Options.Create(new AzureAiFoundryOptions { EarningsCallToolCostUsd = 1, CorporateFilingsToolCostUsd = 2 }));
        calculator.Calculate([new AiInferenceUsage(100, 100, 0)]).ToolCallsCostUsd.Should().Be(3);
        calculator.CalculateWithoutTools([new AiInferenceUsage(100, 100, 0)]).ToolCallsCostUsd.Should().Be(0);
    }
}
