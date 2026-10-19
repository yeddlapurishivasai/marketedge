namespace MarketEdge.Api.Models;

public record AiStockAnalysisDto(
    DateTime GeneratedAt,
    string Market,
    AiStockMetadataDto Stock,
    AiEarningsFundamentalsDto? Fundamentals,
    IReadOnlyList<string> TechnicalColumns,
    IReadOnlyList<IReadOnlyList<object?>> TechnicalRows,
    IReadOnlyList<AiAnalystSnapshotDto> AnalystHistory,
    IReadOnlyList<AiEpsForecastDto> EpsForecasts,
    AiStockSignalsDto? Signals,
    AiStockNoteDto? Note,
    IReadOnlyList<string> CandleColumns,
    IReadOnlyList<IReadOnlyList<object?>> DailyCandles,
    IReadOnlyList<IReadOnlyList<object?>> WeeklyCandles,
    AiDataAvailabilityDto ConcallData);

public record AiDataAvailabilityDto(bool Available, string Reason);

public record AiStockMetadataDto(
    int Id, string Symbol, string CompanyName,
    int SectorId, string SectorName, string? BroadSector,
    bool CatalogIsFno, bool IsTestSample, DateTime CatalogCreatedAt,
    string? Exchange, bool? Active, bool? TickerIsFno, int? BarsAvailable,
    decimal? MarketCap, DateTime? MarketCapUpdatedAt, DateTime? MarketCapCreatedAt);

public record AiEarningsFundamentalsDto(
    string Ticker, DateOnly AsOfDate, DateOnly? LatestQuarterEnd,
    decimal? Revenue, decimal? RevenuePrevQ, decimal? RevenueYoyQ, decimal? RevenueGrowthYoyPct,
    decimal? OperatingProfit, decimal? OperatingProfitPrevQ, decimal? OperatingProfitYoyQ,
    decimal? Opm, decimal? OpmPrevQ, decimal? OpmYoyQ,
    decimal? NetProfit, decimal? NetProfitPrevQ, decimal? NetProfitYoyQ,
    decimal? NetMarginPct, decimal? EarningsGrowthYoyPct, decimal? EarningsGrowthQoqPct,
    bool? EarningsIncreasing, string? OperatingProfitTrend, string? OpmTrend,
    DateOnly? LastEarningsDate, DateOnly? PrevEarningsDate, DateOnly? NextEarningsDate,
    decimal? LastReportedEps, decimal? LastEpsSurprisePct,
    DateOnly? EpsQ1Date, decimal? EpsQ1Estimate, decimal? EpsQ1Actual, decimal? EpsQ1SurprisePct,
    DateOnly? EpsQ2Date, decimal? EpsQ2Estimate, decimal? EpsQ2Actual, decimal? EpsQ2SurprisePct,
    DateOnly? EpsQ3Date, decimal? EpsQ3Estimate, decimal? EpsQ3Actual, decimal? EpsQ3SurprisePct,
    DateOnly? EpsQ4Date, decimal? EpsQ4Estimate, decimal? EpsQ4Actual, decimal? EpsQ4SurprisePct,
    decimal? TrailingPe, decimal? ForwardPe,
    string? ReportedSource, DateTime? ReportedFetchedAt, DateTime UpdatedAt);

public record AiAnalystSnapshotDto(
    string Ticker, DateOnly AsOfDate, string? ConsensusRating, int? NumAnalysts,
    decimal? CurrentQuarterEps, decimal? NextQuarterEps,
    decimal? CurrentYearEps, decimal? NextYearEps,
    decimal? TargetLowPrice, decimal? TargetMeanPrice, decimal? TargetHighPrice,
    string? RecommendationsJson,
    string? LatestRatingFirm, string? LatestRatingGrade,
    string? LatestRatingAction, DateOnly? LatestRatingDate,
    DateTime UpdatedAt);

public record AiEpsForecastDto(
    string Ticker, DateOnly AsOfDate, string PeriodType, DateOnly PeriodEndDate,
    decimal? ConsensusEps, decimal? HighEps, decimal? LowEps,
    int? NumEstimates, int RevisionsUp, int RevisionsDown, DateTime UpdatedAt);

public record AiStockSignalsDto(
    string Ticker, decimal? CapexCwip, decimal? CapexCwipPrevQ,
    decimal? CapexChangePct, string? CapexTrend, DateOnly? CapexAsOf,
    string? NewsJson, string? SignalsText, DateTime UpdatedAt);

public record AiStockNoteDto(string Ticker, string? NoteText, DateTime UpdatedAt);

public record AiCandleDto(
    DateOnly Date, decimal? Open, decimal? High, decimal? Low,
    decimal? Close, long? Volume, decimal? AdjClose);