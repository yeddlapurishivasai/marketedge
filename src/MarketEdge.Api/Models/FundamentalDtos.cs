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
    decimal? LastReportedEps,
    decimal? LastEpsSurprisePct,
    bool EarningsAnnouncedRecent);

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
