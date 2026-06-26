namespace MarketEdge.Api.Models;

// Wire shapes for the Fundamental Scanners feature.

public record FundamentalRow(
    string Symbol,
    string CompanyName,
    string? BroadSector,
    string? Industry,
    DateOnly AsOfDate,
    DateOnly? LatestQuarterEnd,
    decimal? Revenue,
    decimal? RevenueGrowthYoyPct,
    decimal? OperatingProfit,
    decimal? Opm,
    decimal? OpmPrevQ,
    decimal? OpmYoyQ,
    string? OpmTrend,
    decimal? NetProfit,
    decimal? NetMarginPct,
    decimal? EarningsGrowthYoyPct,
    decimal? EarningsGrowthQoqPct,
    bool? EarningsIncreasing,
    string? OperatingProfitTrend,
    DateOnly? LastEarningsDate,
    DateOnly? PrevEarningsDate,
    DateOnly? NextEarningsDate,
    decimal? LastReportedEps,
    decimal? LastEpsSurprisePct,
    decimal? TrailingPe,
    decimal? ForwardPe,
    bool EarningsAnnouncedRecent,
    IReadOnlyList<EpsQuarter> EpsHistory);

// One reported quarter of EPS: estimate vs actual. Beat $ = Actual - Estimate (derived
// in the UI); SurprisePct is yfinance's reported beat percentage.
public record EpsQuarter(
    DateOnly? Date,
    decimal? Estimate,
    decimal? Actual,
    decimal? SurprisePct);

public record FundamentalDetail(
    FundamentalRow Row,
    string? Note,
    FundamentalSignals? Signals);

// Auto-detected catalyst signals (read-only). SignalsText is the compact, token-friendly
// blob fed to the AI workflow; News is the structured headline list for UI display.
public record FundamentalSignals(
    decimal? CapexCwip,
    decimal? CapexCwipPrevQ,
    decimal? CapexChangePct,
    string? CapexTrend,
    DateOnly? CapexAsOf,
    IReadOnlyList<string> Detected,
    IReadOnlyList<SignalNewsItem> News,
    string? SignalsText,
    DateTime UpdatedAt);

public record SignalNewsItem(string Title, string? Publisher, string? Date, string? Link, IReadOnlyList<string>? Tags);

public record SaveNoteRequest(string? NoteText);

// Reimagined fundamental-screener "idea": earnings-based metrics (stamped to the result)
// plus daily-detected analyst rating change and price targets. Only non-stale rows are
// surfaced; superseded results are hidden pending a purge job.
public record FundamentalIdeaRow(
    string Symbol,
    string CompanyName,
    string? BroadSector,
    string? Industry,
    DateOnly EarningsDate,
    decimal? EpsBeatPct,
    decimal? OpmExpansionYoyPct,
    decimal? OperatingProfitExpansionYoyPct,
    string? LatestRatingFirm,
    string? LatestRatingGrade,
    string? LatestRatingAction,
    DateOnly? LatestRatingDate,
    decimal? TargetLowPrice,
    decimal? TargetMeanPrice,
    decimal? TargetHighPrice,
    decimal? EpsBeatConfidence,
    decimal? OpmExpansionConfidence,
    decimal? OperatingProfitExpansionConfidence,
    decimal? AnalystRatingConfidence,
    decimal? TargetUpsideConfidence,
    decimal? FundamentalConfidence,
    decimal? TechnicalConfidence,
    decimal? OverallConfidence,
    int? DaysSinceEarnings,
    int? DaysSinceRating,
    string? ConfidenceRationaleJson,
    bool? IsStage2,
    int? DirectionScore,
    string? Side,
    // Short-side (bearish, mirrored) confidence for every score. The existing fields
    // above are the long-side (bullish) set; a consumer picks the set matching the
    // idea's side so a confidence number always means "conviction for that direction".
    decimal? EpsBeatConfidenceShort,
    decimal? OpmExpansionConfidenceShort,
    decimal? OperatingProfitExpansionConfidenceShort,
    decimal? AnalystRatingConfidenceShort,
    decimal? FundamentalConfidenceShort,
    decimal? OverallConfidenceShort,
    string? ConfidenceRationaleShortJson,
    DateTime UpdatedAt);
