using System.Text.Json;

namespace MarketEdge.Api.Models;

public record SaveAiInvestmentAnalysis(
    string Market,
    string Ticker,
    string WeekNumber,
    string SchemaVersion,
    string SkillVersion,
    string Model,
    string InputJson,
    string EarningsCallJson,
    string CorporateFilingsJson,
    string AnalysisJson,
    AiAnalysisCostDto Cost);

public record AiInvestmentAnalysisHistoryItem(
    long Id,
    string Market,
    string Ticker,
    string WeekNumber,
    string SchemaVersion,
    string SkillVersion,
    string Model,
    AiAnalysisCostDto Cost,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public record AiInvestmentAnalysisDetail(
    AiInvestmentAnalysisHistoryItem Metadata,
    JsonElement DeterministicInput,
    JsonElement EarningsCallEnrichment,
    JsonElement CorporateFilingsEnrichment,
    JsonElement Analysis);