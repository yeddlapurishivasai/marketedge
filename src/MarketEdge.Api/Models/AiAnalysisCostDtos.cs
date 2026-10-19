namespace MarketEdge.Api.Models;

public record AiInferenceUsage(long InputTokens, long OutputTokens, long CachedInputTokens = 0);

public record AiAnalysisCostDto(
    long InputTokens,
    long CachedInputTokens,
    long OutputTokens,
    decimal InputCostUsd,
    decimal OutputCostUsd,
    decimal ToolCallsCostUsd,
    decimal TotalCostUsd,
    decimal Projected1000TickersCostUsd);