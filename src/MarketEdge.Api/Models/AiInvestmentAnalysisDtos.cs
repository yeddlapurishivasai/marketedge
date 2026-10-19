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
    AiInvestmentAnalysisSummary Summary,
    AiAnalysisCostDto Cost,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public record AiInvestmentAnalysisSummary(
    string? Valuation,
    decimal? BaseReturn3mPct,
    decimal? BaseReturn6mPct,
    decimal? BaseReturn1yPct,
    decimal? BaseReturn2yPct,
    string? ShortTermAction,
    string? LongTermAction);

public record AiInvestmentAnalysisDetail(
    AiInvestmentAnalysisHistoryItem Metadata,
    JsonElement DeterministicInput,
    JsonElement EarningsCallEnrichment,
    JsonElement CorporateFilingsEnrichment,
    JsonElement Analysis);

public record AiAnalysisCandidateResponse(
    string Market,
    int Stage2RunId,
    string WeekNumber,
    string AnalysisWeekNumber,
    DateTime Stage2CompletedAt,
    int RequestedCount,
    decimal MinimumMarketCap,
    int EligibleStage2Count,
    int MarketCapEligibleCount,
    int AvailableAfterAnalyzedExclusion,
    int TechnicalSetupCount,
    IReadOnlyList<AiAnalysisCandidate> Candidates);

public record AiAnalysisCandidate(
    int Rank,
    string Symbol,
    string CompanyName,
    string SectorName,
    string? Classification,
    decimal? MarketCap,
    decimal Score,
    AiCandidateScoreBreakdown ScoreBreakdown,
    AiCandidateSectorStrength SectorStrength,
    AiCandidateTechnicalSetup TechnicalSetup,
    IReadOnlyList<string> Reasons,
    decimal? EarningsGrowthYoyPct,
    decimal? EarningsGrowthQoqPct,
    decimal? MarginExpansionYoyPct,
    DateOnly? LastEarningsDate,
    decimal? CapexChangePct,
    int? RsRank,
    decimal? RsDelta2w,
    decimal? MomentumScore,
    string? AdClassification,
    bool? SqueezeFired);

public record AiCandidateScoreBreakdown(
    decimal EarningsGrowth,
    decimal MarginExpansion,
    decimal RecentEarnings,
    decimal Capex,
    decimal SectorStrength,
    decimal TechnicalTiming,
    decimal DataQuality);

public record AiCandidateSectorStrength(
    decimal? AverageRsScore,
    decimal? AverageRsDelta2w,
    decimal Stage2ParticipationPct);

public record AiCandidateTechnicalSetup(
    bool NearPivot,
    decimal? PivotPrice,
    decimal? PivotDistancePct,
    bool Ema40Bounce,
    decimal? WeeklyEma40,
    decimal? Ema40DistancePct);