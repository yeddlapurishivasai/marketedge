using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

// Feature 013: Market Regime — query-only EF entities mapping tables owned by the SQL project.

/// <summary>Daily OHLCV for index-class symbols (benchmark index + volatility proxy).</summary>
public abstract class BenchmarkBar1DBase
{
    public string Symbol { get; set; } = string.Empty;
    public DateOnly BarDate { get; set; }
    public decimal? Open { get; set; }
    public decimal? High { get; set; }
    public decimal? Low { get; set; }
    public decimal? Close { get; set; }
    public long? Volume { get; set; }
    public decimal? AdjClose { get; set; }
}

[Table("IndianBenchmarkBars1D")] public class IndianBenchmarkBar1D : BenchmarkBar1DBase { }
[Table("USBenchmarkBars1D")] public class USBenchmarkBar1D : BenchmarkBar1DBase { }

/// <summary>One fully-computed market regime snapshot per as-of date. The worker computes the
/// benchmark condition (§3.1), breadth composite (§3.2), and combined regime (§4) and persists
/// them here; the API only reads the latest row. Column names mirror the SQL project 1:1.</summary>
public abstract class RegimeSnapshotBase
{
    public DateOnly AsOfDate { get; set; }
    public DateOnly? ConditionAsOfDate { get; set; }
    public DateOnly? BreadthAsOfDate { get; set; }
    public string? BenchmarkSymbol { get; set; }
    public string? VolatilitySymbol { get; set; }
    public int EvaluatedCount { get; set; }

    // Effective regime (§4)
    public string Regime { get; set; } = string.Empty;
    public string RegimeLabel { get; set; } = string.Empty;
    public string RegimeTone { get; set; } = string.Empty;
    public string? Posture { get; set; }
    public bool Available { get; set; }

    // Benchmark condition (§3.1)
    public string ConditionLabel { get; set; } = string.Empty;
    public string ConditionTone { get; set; } = string.Empty;
    public string? ConditionExplanation { get; set; }
    public bool ConditionAvailable { get; set; }
    public decimal? ConditionClose { get; set; }
    public decimal? ConditionSma20 { get; set; }
    public decimal? ConditionSma50 { get; set; }
    public decimal? ConditionSma200 { get; set; }
    public decimal? ConditionCloseVsSma20Pct { get; set; }
    public decimal? ConditionCloseVsSma50Pct { get; set; }
    public decimal? ConditionCloseVsSma200Pct { get; set; }
    public decimal? ConditionVolumeVsAvgPct { get; set; }

    // Breadth composite (§3.2)
    public string BreadthLabel { get; set; } = string.Empty;
    public string BreadthTone { get; set; } = string.Empty;
    public int? BreadthScore { get; set; }
    public int BreadthPositiveSignals { get; set; }
    public int BreadthAvailableSignals { get; set; }
    public bool BreadthAvailable { get; set; }
    public string? SignalsJson { get; set; }

    // Raw participation facts
    public decimal? PctAboveSma10 { get; set; }
    public decimal? PctAboveSma20 { get; set; }
    public decimal? PctAboveSma50 { get; set; }
    public decimal? PctAboveSma200 { get; set; }
    public decimal? PctSma20AboveSma50 { get; set; }
    public decimal? PctSma50AboveSma200 { get; set; }

    // Benchmark / volatility context
    public decimal? BenchmarkYtdPct { get; set; }
    public decimal? Benchmark1wPct { get; set; }
    public decimal? Benchmark1mPct { get; set; }
    public decimal? Benchmark1yPct { get; set; }
    public decimal? BenchmarkPctFrom52wHigh { get; set; }
    public decimal? VolatilityClose { get; set; }

    // True when this snapshot reflects a live intraday index price (computed during market hours).
    public bool IsIntraday { get; set; }

    public DateTime CreatedAt { get; set; }
}

[Table("IndianRegimeSnapshots")] public class IndianRegimeSnapshot : RegimeSnapshotBase { }
[Table("USRegimeSnapshots")] public class USRegimeSnapshot : RegimeSnapshotBase { }

[Table("RegimeSchedules")]
public class RegimeSchedule
{
    [Key]
    public string Market { get; set; } = string.Empty;
    public bool Enabled { get; set; } = true;
    public int HourLocal { get; set; } = 20;
    public DateTime? LastEnqueuedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
