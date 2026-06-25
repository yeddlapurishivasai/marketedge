namespace MarketEdge.Api.Models;

public record LookupCandidate(string Symbol, string CompanyName, string? Industry, string? BroadSector);

public record LookupTechnicalDto(
    DateOnly? AsOfDate,
    decimal? Close, decimal? DayPct, decimal? Open, decimal? High, decimal? Low,
    decimal? High52w, decimal? From52wHigh, long? MarketCap,
    int? Rs, int? Rs1d, int? Rs1w, int? Rs1m, int? Rs3m, int? Rs6m,
    string? RsType, DateOnly? RsDate, int? ScannerHits, DateOnly? LastScannerHit);

public record LookupAnalystDto(
    DateOnly? AsOfDate, string? ConsensusRating, int? NumAnalysts,
    decimal? CurrentQuarterEps, decimal? NextQuarterEps,
    decimal? CurrentYearEps, decimal? NextYearEps);

public record LookupEpsForecastDto(
    string PeriodType, DateOnly PeriodEndDate,
    decimal? ConsensusEps, decimal? HighEps, decimal? LowEps,
    int? NumEstimates, int RevisionsUp, int RevisionsDown);

public record StockLookupDetail(
    string Symbol, string CompanyName, string? BroadSector, string? Industry, string Market,
    string? Exchange, bool Active, bool IsFno, int? BarsAvailable,
    LookupTechnicalDto? Technical, LookupAnalystDto? Analyst,
    IReadOnlyList<LookupEpsForecastDto> QuarterlyEps,
    IReadOnlyList<LookupEpsForecastDto> YearlyEps);

public record LookupBarDto(
    DateOnly Date, decimal? Open, decimal? High, decimal? Low, decimal? Close, long? Volume);
