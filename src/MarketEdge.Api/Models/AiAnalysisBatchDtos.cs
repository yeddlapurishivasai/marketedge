namespace MarketEdge.Api.Models;

public record AiAnalysisBatchTriggerResponse(
    int RunId,
    string WeekNumber,
    bool AlreadyExists);
