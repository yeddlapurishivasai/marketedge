namespace MarketEdge.Api.Models;

// Wire shapes for the breakout and scoring-weight APIs.

public record BreakoutDto(
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

public record BreakoutStatsDto(
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

// Realized PnL = breakouts closed (by ExitAt) within [From, To). Unrealized PnL = the live
// snapshot of all currently-open positions, which is period-independent.
public record BreakoutPnlSummaryDto(
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

public record BreakoutDayDto(
    DateTime Date,
    string? TradeType,
    List<BreakoutDto> Entries,
    List<BreakoutDto> Exits);

public record NearPivotDto(
    int Id,
    string Ticker,
    string? CompanyName,
    string TradeType,
    string Direction,
    List<string> FlaggedScanners,
    int ScannerHitCount,
    decimal LastClose,
    decimal PivotPrice,
    decimal DistancePct,
    decimal? RelVolume,
    bool VolumeConfirmed,
    DateTime ScanDate,
    DateTime UpdatedAt);

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

