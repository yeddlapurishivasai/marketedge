using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

// Query-only mappings of the earnings-fundamentals snapshot tables produced by the
// ingestion pipeline (yfinance reported quarterly financials). Schema is owned by the
// SQL project; these are never used for migrations.

public abstract class EarningsFundamentalsBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly AsOfDate { get; set; }
    public DateOnly? LatestQuarterEnd { get; set; }

    public decimal? Revenue { get; set; }
    public decimal? RevenuePrevQ { get; set; }
    public decimal? RevenueYoyQ { get; set; }
    public decimal? RevenueGrowthYoyPct { get; set; }

    public decimal? OperatingProfit { get; set; }
    public decimal? OperatingProfitPrevQ { get; set; }
    public decimal? OperatingProfitYoyQ { get; set; }
    public decimal? Opm { get; set; }
    public decimal? OpmPrevQ { get; set; }
    public decimal? OpmYoyQ { get; set; }

    public decimal? NetProfit { get; set; }
    public decimal? NetProfitPrevQ { get; set; }
    public decimal? NetProfitYoyQ { get; set; }
    public decimal? NetMarginPct { get; set; }
    public decimal? EarningsGrowthYoyPct { get; set; }
    public decimal? EarningsGrowthQoqPct { get; set; }

    public bool? EarningsIncreasing { get; set; }
    public string? OperatingProfitTrend { get; set; }
    public string? OpmTrend { get; set; }

    public DateOnly? LastEarningsDate { get; set; }
    public DateOnly? PrevEarningsDate { get; set; }
    public decimal? LastReportedEps { get; set; }
    public decimal? LastEpsSurprisePct { get; set; }

    public DateTime UpdatedAt { get; set; }
}

[Table("IndianEarningsFundamentals")] public class IndianEarningsFundamentals : EarningsFundamentalsBase { }
[Table("USEarningsFundamentals")] public class USEarningsFundamentals : EarningsFundamentalsBase { }

// Per-stock free-text note: additional fundamental context captured by the user, used
// as an input for a downstream AI workflow. Writable from the API.
public abstract class StockNoteBase
{
    public string Ticker { get; set; } = string.Empty;
    public string? NoteText { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("IndianStockNote")] public class IndianStockNote : StockNoteBase { }
[Table("USStockNote")] public class USStockNote : StockNoteBase { }

// Per-stock auto-detected catalyst signals (CWIP/capex trend + recent news headlines),
// scraped daily during fundamentals ingestion. Read-only from the API. Kept SEPARATE from
// StockNote: both feed the downstream AI workflow as distinct inputs.
public abstract class StockSignalsBase
{
    public string Ticker { get; set; } = string.Empty;
    public decimal? CapexCwip { get; set; }
    public decimal? CapexCwipPrevQ { get; set; }
    public decimal? CapexChangePct { get; set; }
    public string? CapexTrend { get; set; }
    public DateOnly? CapexAsOf { get; set; }
    public string? NewsJson { get; set; }
    public string? SignalsText { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("IndianStockSignals")] public class IndianStockSignals : StockSignalsBase { }
[Table("USStockSignals")] public class USStockSignals : StockSignalsBase { }

// Per-stock Wilson lower-bound scores (swing + positional) plus deterministic upside,
// produced by the worker scoring engine each scanner run. Read-only from the API.
public abstract class StockScoresBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly? AsOfDate { get; set; }

    public decimal? UpsideEpsPct { get; set; }
    public decimal? UpsideAnalystPct { get; set; }
    public decimal? TargetPrice { get; set; }

    public decimal? AiUpsidePct { get; set; }
    public decimal? AiDownsidePct { get; set; }
    public string? AiRationale { get; set; }

    public int? SwingScore { get; set; }
    public string? SwingSide { get; set; }
    public int? SwingBull { get; set; }
    public int? SwingBear { get; set; }

    public int? PositionalScore { get; set; }
    public string? PositionalSide { get; set; }
    public int? PositionalBull { get; set; }
    public int? PositionalBear { get; set; }

    public decimal? FundFreshnessDecay { get; set; }
    public int? DaysSinceEarnings { get; set; }
    public int? ScannerHits { get; set; }
    public bool? IsFno { get; set; }
    public string? ComponentsJson { get; set; }

    public DateTime ScoredAt { get; set; }
}

[Table("IndianStockScores")] public class IndianStockScores : StockScoresBase { }
[Table("USStockScores")] public class USStockScores : StockScoresBase { }

// Paper trades opened/managed by the worker trade engine on scanner breakouts.
public abstract class TradeBase
{
    public int Id { get; set; }
    public string Ticker { get; set; } = string.Empty;
    public string? CompanyName { get; set; }
    public string TradeType { get; set; } = string.Empty;   // swing / positional
    public string Direction { get; set; } = string.Empty;   // long / short
    public string Status { get; set; } = string.Empty;      // active / closed

    public string? EntryScanner { get; set; }
    public string? FlaggedScannersJson { get; set; }
    public int ScannerHitCount { get; set; }

    public DateTime EntryAt { get; set; }
    public decimal EntryPrice { get; set; }
    public int? Qty { get; set; }

    public decimal? InitialStop { get; set; }
    public decimal? CurrentStop { get; set; }
    public string? StopBasis { get; set; }
    public decimal? RiskPerShare { get; set; }
    public bool MovedToBe { get; set; }

    public decimal? LastPrice { get; set; }
    public decimal? PnLPct { get; set; }
    public decimal? PnLAmount { get; set; }
    public decimal? MfePct { get; set; }
    public decimal? MaePct { get; set; }

    public DateTime? ExitAt { get; set; }
    public decimal? ExitPrice { get; set; }
    public string? ExitReason { get; set; }

    public decimal? ConfidenceScore { get; set; }
    public string? ConfidenceRationaleJson { get; set; }

    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("ScoringWeights")]
public class ScoringWeight
{
    public int Id { get; set; }
    public string Market { get; set; } = string.Empty;       // india / us
    public string Category { get; set; } = string.Empty;     // pattern / mix
    public string ComponentKey { get; set; } = string.Empty; // scanner name OR '{profile}:{component}'
    public decimal Weight { get; set; }
    public decimal SeedWeight { get; set; }
    public int Wins { get; set; }
    public int Losses { get; set; }
    public bool ManualOverride { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("IndianTrades")] public class IndianTrade : TradeBase { }
[Table("USTrades")] public class USTrade : TradeBase { }
