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
    decimal? CurrentYearEps, decimal? NextYearEps,
    decimal? TargetLowPrice, decimal? TargetMeanPrice, decimal? TargetHighPrice);

public record LookupEpsForecastDto(
    string PeriodType, DateOnly PeriodEndDate,
    decimal? ConsensusEps, decimal? HighEps, decimal? LowEps,
    int? NumEstimates, int RevisionsUp, int RevisionsDown);

// One scenario of an EPS-driven upside at constant P/E: the projected EPS, the implied
// % price move vs the base EPS, and the implied stock price.
public record UpsideCaseDto(decimal? Eps, decimal? UpsidePct, decimal? ImpliedPrice);

// Best/base/worst upside for a horizon. Source distinguishes the method:
//   "deterministic" – EPS at constant P/E (horizon quarter/year)
//   "analyst"       – yfinance analyst 12-month price targets (low/mean/high)
//   "ai"            – AI-predicted scenarios (placeholder until a model is wired in)
public record UpsideProjectionDto(
    string Horizon, string Source,
    decimal? CurrentPrice, decimal? BaseEps,
    UpsideCaseDto? Bear, UpsideCaseDto? Base, UpsideCaseDto? Bull);

public record StockLookupDetail(
    string Symbol, string CompanyName, string? BroadSector, string? Industry, string Market,
    string? Exchange, bool Active, bool IsFno, int? BarsAvailable,
    LookupTechnicalDto? Technical, LookupAnalystDto? Analyst,
    IReadOnlyList<LookupEpsForecastDto> QuarterlyEps,
    IReadOnlyList<LookupEpsForecastDto> YearlyEps,
    UpsideProjectionDto? QuarterUpside, UpsideProjectionDto? YearUpside,
    UpsideProjectionDto? AnalystUpside, UpsideProjectionDto? AiUpside);

public record LookupBarDto(
    DateOnly Date, decimal? Open, decimal? High, decimal? Low, decimal? Close, long? Volume);
