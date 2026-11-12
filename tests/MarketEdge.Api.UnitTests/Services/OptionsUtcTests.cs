using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.EntityFrameworkCore;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsUtcTests
{
    [Theory]
    [InlineData(typeof(OptionsForecastRun), "GeneratedAt")]
    [InlineData(typeof(OptionsForecastRun), "TargetAt")]
    [InlineData(typeof(OptionsForecastRun), "InputCutoffAt")]
    [InlineData(typeof(BrokerConnection), "LoginAt")]
    [InlineData(typeof(BrokerConnection), "ExpiresAt")]
    [InlineData(typeof(BrokerConnection), "UpdatedAt")]
    [InlineData(typeof(OptionsForecastOutcome), "EvaluatedAt")]
    [InlineData(typeof(OptionsStrategyOutcome), "EvaluatedAt")]
    [InlineData(typeof(OptionsAiSelection), "CreatedAt")]
    [InlineData(typeof(OptionsSnapshotBatch), "CapturedAt")]
    [InlineData(typeof(OptionsQuoteSnapshot), "ObservedAt")]
    [InlineData(typeof(OptionsDerivativeInstrument), "UpdatedAt")]
    public void SqlDateTime2MaterializationMarksOnlyOptionsAuditsAsUtc(Type entity, string property)
    {
        using var db = new MarketEdgeDbContext(new DbContextOptionsBuilder<MarketEdgeDbContext>()
            .UseSqlServer("Server=unused;Database=unused;Trusted_Connection=True;TrustServerCertificate=True").Options);
        var converter = db.Model.FindEntityType(entity)!.FindProperty(property)!.GetValueConverter();
        Assert.NotNull(converter);
        var sqlDateTime2 = new DateTime(2026, 11, 9, 10, 0, 0, DateTimeKind.Unspecified);
        var utc = Assert.IsType<DateTime>(converter.ConvertFromProvider(sqlDateTime2));
        Assert.Equal(DateTimeKind.Utc, utc.Kind);
        Assert.Equal(sqlDateTime2.Ticks, utc.Ticks);
        Assert.EndsWith("Z\"", JsonSerializer.Serialize(utc));
        Assert.Null(db.Model.FindEntityType(typeof(JobRun))!.FindProperty(nameof(JobRun.CreatedAt))!.GetValueConverter());
    }

    [Fact]
    public async Task HistoryAndDetailSerializeUtcAuditTimestampsWithZ()
    {
        using var db = DbContextFactory.Create();
        var utcAudit = new DateTime(2026, 11, 9, 10, 0, 0, DateTimeKind.Unspecified);
        var run = new OptionsForecastRun
        {
            UserKey = "alice", RequestId = Guid.NewGuid(), IndexCode = "NIFTY50", Mode = "daily",
            GeneratedAt = utcAudit, TargetAt = utcAudit.AddHours(1), InputCutoffAt = utcAudit
        };
        db.OptionsForecasts.Add(run);
        await db.SaveChangesAsync();
        var strategy = new OptionsStrategySuggestion { ForecastRunId = run.Id, CandidateKey = "candidate", PayloadJson = "{}" };
        db.OptionsStrategies.Add(strategy);
        await db.SaveChangesAsync();
        db.OptionsStrategyOutcomes.Add(new OptionsStrategyOutcome
        {
            StrategySuggestionId = strategy.Id, Version = 1, Status = "partial", EvaluatedAt = utcAudit
        });
        await db.SaveChangesAsync();
        var settings = new OptionsSettings();
        var service = new OptionsWorkspaceService(db, new Mock<QueueClient>().Object,
            new OptionsBrokerService(db, new HttpClient(), settings, new OptionsTokenCipher(settings)), settings);
        var web = new JsonSerializerOptions(JsonSerializerDefaults.Web);
        using var detail = JsonDocument.Parse(JsonSerializer.Serialize(await service.DetailAsync("alice", run.Id, default), web));
        foreach (var name in new[] { "generatedAt", "targetAt", "inputCutoffAt" })
            Assert.EndsWith("Z", detail.RootElement.GetProperty(name).GetString());
        Assert.EndsWith("Z", detail.RootElement.GetProperty("strategyOutcomes")[0].GetProperty("evaluatedAt").GetString());
        using var history = JsonDocument.Parse(JsonSerializer.Serialize(await service.ListAsync("alice", null, null, default), web));
        foreach (var name in new[] { "generatedAt", "targetAt", "inputCutoffAt" })
            Assert.EndsWith("Z", history.RootElement.GetProperty("items")[0].GetProperty(name).GetString());
    }
}
