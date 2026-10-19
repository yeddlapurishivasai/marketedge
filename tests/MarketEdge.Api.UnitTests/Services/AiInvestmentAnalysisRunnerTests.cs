using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Options;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public class AiInvestmentAnalysisRunnerTests
{
    [Fact]
    public async Task RunAsync_UsesWebResearchInternallyAndPersistsOnlyFinalReportUsage()
    {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString());
        Directory.CreateDirectory(Path.Combine(root, "Ai"));
        await File.WriteAllTextAsync(
            Path.Combine(root, "Ai", "stock-investment-analysis.skill.md"),
            "Return the marketedge.ai-analysis.v1 JSON report.");
        try
        {
            var foundry = new Mock<IAzureAiFoundryClient>();
            foundry.Setup(client => client.CreateResponseAsync(
                    It.IsAny<string>(), It.IsAny<string>(), true,
                    It.IsAny<int>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(new FoundryResponse(
                    "{\"schemaVersion\":\"marketedge.ai-analysis.v1\",\"enrichment\":{\"earningsCall\":{\"status\":\"available\"},\"corporateFilings\":{\"status\":\"available\"}}}",
                    new AiInferenceUsage(300, 30)));
            var options = Options.Create(new AzureAiFoundryOptions
            {
                Model = "test-model",
                InputUsdPerMillionTokens = 2m,
                OutputUsdPerMillionTokens = 8m,
                EarningsCallToolCostUsd = 0.01m,
                CorporateFilingsToolCostUsd = 0.02m
            });
            var costCalculator = new AiAnalysisCostCalculator(options);
            SaveAiInvestmentAnalysis? saved = null;
            var store = new Mock<IAiInvestmentAnalysisStore>();
            store.Setup(service => service.SaveAsync(It.IsAny<SaveAiInvestmentAnalysis>()))
                .Callback<SaveAiInvestmentAnalysis>(value => saved = value)
                .ReturnsAsync((SaveAiInvestmentAnalysis value) => Result(value));
            var environment = new Mock<IWebHostEnvironment>();
            environment.SetupGet(value => value.ContentRootPath).Returns(root);
            var runner = new AiInvestmentAnalysisRunner(
                foundry.Object, costCalculator, store.Object, options, environment.Object);

            var result = await runner.RunAsync("us", "TSM", Input());

            foundry.Verify(client => client.CreateResponseAsync(
                It.IsAny<string>(), It.IsAny<string>(), true,
                It.IsAny<int>(), It.IsAny<CancellationToken>()), Times.Once);
            saved.Should().NotBeNull();
            saved!.Cost.InputTokens.Should().Be(300);
            saved.Cost.OutputTokens.Should().Be(30);
            saved.Cost.TotalCostUsd.Should().Be(0.03084m);
            saved.Cost.Projected1000TickersCostUsd.Should().Be(30.84m);
            saved.EarningsCallJson.Should().Contain("available");
            saved.CorporateFilingsJson.Should().Contain("available");
            result.Metadata.Model.Should().Be("test-model");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private static AiStockAnalysisDto Input() => new(
        DateTime.UtcNow,
        "us",
        new AiStockMetadataDto(
            1, "TSM", "Taiwan Semiconductor", 1, "Semiconductors", "Technology",
            false, false, DateTime.UtcNow, "NYSE", true, false, 250,
            1_000_000m, DateTime.UtcNow, DateTime.UtcNow),
        null,
        Array.Empty<string>(),
        Array.Empty<IReadOnlyList<object?>>(),
        Array.Empty<AiAnalystSnapshotDto>(),
        Array.Empty<AiEpsForecastDto>(),
        null,
        null,
        Array.Empty<string>(),
        Array.Empty<IReadOnlyList<object?>>(),
        Array.Empty<IReadOnlyList<object?>>(),
        new AiDataAvailabilityDto(false, "Unavailable"));

    private static AiInvestmentAnalysisDetail Result(SaveAiInvestmentAnalysis value)
    {
        var metadata = new AiInvestmentAnalysisHistoryItem(
            1, value.Market, value.Ticker, value.WeekNumber,
            value.SchemaVersion, value.SkillVersion, value.Model,
            value.Cost, DateTime.UtcNow, DateTime.UtcNow);
        return new AiInvestmentAnalysisDetail(
            metadata,
            System.Text.Json.JsonSerializer.SerializeToElement(new { }),
            System.Text.Json.JsonSerializer.SerializeToElement(new { }),
            System.Text.Json.JsonSerializer.SerializeToElement(new { }),
            System.Text.Json.JsonSerializer.SerializeToElement(new { }));
    }
}