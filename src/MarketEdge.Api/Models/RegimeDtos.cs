namespace MarketEdge.Api.Models;

// Feature 013: Market Regime — API boundary DTOs (§3.1 condition, §3.2 breadth, §4 regime, §8 freshness).

/// <summary>Benchmark condition signal (§3.1): index trend + volume state.</summary>
public record MarketConditionDto(
    string Label,           // Pessimistic | Bearish | Cautious | Euphoric | Uptrend | Neutral | Unavailable
    string Tone,            // red | yellow | green | grey
    string Explanation,
    string? BenchmarkSymbol,
    DateOnly? AsOfDate,
    decimal? Close,
    decimal? Sma20,
    decimal? Sma50,
    decimal? Sma200,
    decimal? CloseVsSma20Pct,
    decimal? CloseVsSma50Pct,
    decimal? CloseVsSma200Pct,
    decimal? VolumeVsAvgPct,
    bool Available);

/// <summary>One positive/negative breadth signal with its raw value and threshold (§3.2).</summary>
public record BreadthSignalDto(string Key, string Label, decimal? Value, string Threshold, bool? Positive);

/// <summary>Breadth composite (§3.2): participation score + label.</summary>
public record MarketBreadthDto(
    string Label,           // Bullish | Positive | Neutral | Negative | Bearish | Unavailable
    string Tone,
    int? Score,             // 0..100 (percent of positive signals among available)
    int PositiveSignals,
    int AvailableSignals,
    int EvaluatedCount,
    DateOnly? AsOfDate,
    string? BenchmarkSymbol,
    string? VolatilitySymbol,
    IReadOnlyList<BreadthSignalDto> Signals,
    bool Available);

/// <summary>Combined effective regime + posture (§4), with freshness metadata (§8).</summary>
public record MarketRegimeDto(
    string Market,
    string Regime,          // RiskOn | SelectiveRiskOn | Caution | RiskOff | Mixed | Unavailable
    string RegimeLabel,     // human-friendly, e.g. "Selective Risk On"
    string Tone,
    string Posture,
    MarketConditionDto Condition,
    MarketBreadthDto Breadth,
    DateOnly? AsOfDate,
    bool Available,
    bool Stale,
    string? StaleReason);

public record RegimeScheduleDto(
    string Market, bool Enabled, int HourLocal, DateTime? LastEnqueuedAt, DateTime UpdatedAt,
    DateTime? LastRunAt);

public class UpdateRegimeScheduleRequest
{
    public bool Enabled { get; set; }
    public int? HourLocal { get; set; }
}
