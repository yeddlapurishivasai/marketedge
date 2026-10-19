using MarketEdge.Api.Models;
using Microsoft.Extensions.Options;

namespace MarketEdge.Api.Services;

public sealed class AzureAiFoundryOptions
{
    public const string SectionName = "AzureAiFoundry";

    public string Endpoint { get; set; } = string.Empty;
    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = string.Empty;
    public string ReasoningEffort { get; set; } = "low";
    public int AnalysisMaxOutputTokens { get; set; } = 12000;
    public decimal InputUsdPerMillionTokens { get; set; }
    public decimal CachedInputUsdPerMillionTokens { get; set; }
    public decimal OutputUsdPerMillionTokens { get; set; }
    public decimal EarningsCallToolCostUsd { get; set; }
    public decimal CorporateFilingsToolCostUsd { get; set; }
}

public interface IAiAnalysisCostCalculator
{
    AiAnalysisCostDto Calculate(IEnumerable<AiInferenceUsage> inferenceRounds);
}

public class AiAnalysisCostCalculator : IAiAnalysisCostCalculator
{
    private const decimal TokensPerMillion = 1_000_000m;
    private readonly AzureAiFoundryOptions _options;

    public AiAnalysisCostCalculator(IOptions<AzureAiFoundryOptions> options) =>
        _options = options.Value;

    public AiAnalysisCostDto Calculate(IEnumerable<AiInferenceUsage> inferenceRounds)
    {
        var rounds = inferenceRounds.ToList();
        var inputTokens = rounds.Sum(round => round.InputTokens);
        var outputTokens = rounds.Sum(round => round.OutputTokens);
        var cachedInputTokens = rounds.Sum(round => round.CachedInputTokens);

        if (inputTokens < 0 || outputTokens < 0 || cachedInputTokens < 0 || cachedInputTokens > inputTokens)
            throw new ArgumentOutOfRangeException(nameof(inferenceRounds), "Token usage must be non-negative and cached input cannot exceed total input.");

        var uncachedInputTokens = inputTokens - cachedInputTokens;
        var cachedRate = _options.CachedInputUsdPerMillionTokens > 0
            ? _options.CachedInputUsdPerMillionTokens
            : _options.InputUsdPerMillionTokens;
        var inputCost = TokenCost(uncachedInputTokens, _options.InputUsdPerMillionTokens)
            + TokenCost(cachedInputTokens, cachedRate);
        var outputCost = TokenCost(outputTokens, _options.OutputUsdPerMillionTokens);
        var toolCost = Money(_options.EarningsCallToolCostUsd + _options.CorporateFilingsToolCostUsd);
        var totalCost = Money(inputCost + outputCost + toolCost);

        return new AiAnalysisCostDto(
            inputTokens,
            cachedInputTokens,
            outputTokens,
            Money(inputCost),
            outputCost,
            toolCost,
            totalCost,
            Money(totalCost * 1000m));
    }

    private static decimal TokenCost(long tokens, decimal ratePerMillion) =>
        Money(tokens / TokensPerMillion * ratePerMillion);

    private static decimal Money(decimal amount) =>
        decimal.Round(amount, 8, MidpointRounding.AwayFromZero);
}