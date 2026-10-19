using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.Extensions.Options;

namespace MarketEdge.Api.UnitTests.Services;

public class AiAnalysisCostCalculatorTests
{
    [Fact]
    public void Calculate_AggregatesRoundsAndProjectsOneThousandTickers()
    {
        var options = Options.Create(new AzureAiFoundryOptions
        {
            InputUsdPerMillionTokens = 2m,
            CachedInputUsdPerMillionTokens = 0.5m,
            OutputUsdPerMillionTokens = 8m,
            EarningsCallToolCostUsd = 0.01m,
            CorporateFilingsToolCostUsd = 0.02m
        });
        var calculator = new AiAnalysisCostCalculator(options);

        var result = calculator.Calculate(new[]
        {
            new AiInferenceUsage(InputTokens: 60_000, OutputTokens: 4_000, CachedInputTokens: 10_000),
            new AiInferenceUsage(InputTokens: 40_000, OutputTokens: 6_000, CachedInputTokens: 10_000)
        });

        result.InputTokens.Should().Be(100_000);
        result.CachedInputTokens.Should().Be(20_000);
        result.OutputTokens.Should().Be(10_000);
        result.InputCostUsd.Should().Be(0.17m);
        result.OutputCostUsd.Should().Be(0.08m);
        result.ToolCallsCostUsd.Should().Be(0.03m);
        result.TotalCostUsd.Should().Be(0.28m);
        result.Projected1000TickersCostUsd.Should().Be(280m);
    }

    [Fact]
    public void Calculate_RejectsCachedTokensAboveInputTokens()
    {
        var calculator = new AiAnalysisCostCalculator(Options.Create(new AzureAiFoundryOptions()));

        var action = () => calculator.Calculate(new[] { new AiInferenceUsage(10, 1, 11) });

        action.Should().Throw<ArgumentOutOfRangeException>();
    }
}