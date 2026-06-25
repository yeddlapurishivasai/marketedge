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
    string? Note);

public record SaveNoteRequest(string? NoteText);
