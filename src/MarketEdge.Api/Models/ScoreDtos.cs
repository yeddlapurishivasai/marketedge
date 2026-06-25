namespace MarketEdge.Api.Models;

// Wire shapes for the scoring & paper-trade engine feature.

public record StockScoreDto(
    string Ticker,
    DateOnly? AsOfDate,
    decimal? UpsideEpsPct,
    decimal? UpsideAnalystPct,
    decimal? TargetPrice,
    decimal? AiUpsidePct,
    decimal? AiDownsidePct,
    string? AiRationale,
    int? SwingScore,
    string? SwingSide,
    int? SwingBull,
    int? SwingBear,
    int? PositionalScore,
    string? PositionalSide,
    int? PositionalBull,
    int? PositionalBear,
    decimal? FundFreshnessDecay,
    int? DaysSinceEarnings,
    int? ScannerHits,
    bool? IsFno,
    string? ComponentsJson,
    DateTime ScoredAt);

public record TradeDto(
    int Id,
    string Ticker,
    string? CompanyName,
    string TradeType,
    string Direction,
    string Status,
    string? EntryScanner,
    List<string> FlaggedScanners,
    int ScannerHitCount,
    DateTime EntryAt,
    decimal EntryPrice,
    int? Qty,
    decimal? InitialStop,
    decimal? CurrentStop,
    string? StopBasis,
    decimal? RiskPerShare,
    bool MovedToBe,
    decimal? LastPrice,
    decimal? PnLPct,
    decimal? PnLAmount,
    decimal? MfePct,
    decimal? MaePct,
    DateTime? ExitAt,
    decimal? ExitPrice,
    string? ExitReason,
    decimal? ConfidenceScore,
    string? ConfidenceRationaleJson,
    DateTime UpdatedAt);

public record TradeStatsDto(
    int ActiveCount,
    int ClosedCount,
    int Wins,
    int Losses,
    decimal? WinRatePct,
    decimal? AvgPnLPct,
    decimal? RealizedPnLAmount,
    decimal? OpenPnLAmount,
    decimal? SwingOpenPnLAmount,
    decimal? SwingRealizedPnLAmount,
    decimal? PositionalOpenPnLAmount,
    decimal? PositionalRealizedPnLAmount);

// Realized PnL = trades closed (by ExitAt) within [From, To). Unrealized PnL = the live
// snapshot of all currently-open positions, which is period-independent.
public record TradePnlSummaryDto(
    DateTime From,
    DateTime To,
    string? TradeType,
    int RealizedCount,
    int Wins,
    int Losses,
    decimal? WinRatePct,
    decimal RealizedPnLAmount,
    decimal? AvgRealizedPnLPct,
    int OpenCount,
    decimal OpenPnLAmount);

public record TradeDayDto(
    DateTime Date,
    string? TradeType,
    List<TradeDto> Entries,
    List<TradeDto> Exits);

public record ScannerPerformanceDto(
    string Scanner,
    int Trades,
    int Closed,
    int OpenCount,
    int Wins,
    int Losses,
    decimal? WinRatePct,
    decimal ReliabilityScore,
    decimal? AvgPnLPct,
    decimal? RealizedPnLAmount,
    decimal? OpenPnLAmount);

public record ScoringWeightDto(
    int Id,
    string Market,
    string Category,
    string ComponentKey,
    decimal Weight,
    decimal SeedWeight,
    int Wins,
    int Losses,
    bool ManualOverride,
    DateTime UpdatedAt);

public record ScoringWeightUpdateDto(
    decimal? Weight,
    bool? ManualOverride);

