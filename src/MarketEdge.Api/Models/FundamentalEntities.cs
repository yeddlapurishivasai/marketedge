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
    public DateOnly? NextEarningsDate { get; set; }
    public decimal? LastReportedEps { get; set; }
    public decimal? LastEpsSurprisePct { get; set; }

    // Reported EPS history: last 4 reported quarters (Q1 = most recent).
    public DateOnly? EpsQ1Date { get; set; }
    public decimal? EpsQ1Estimate { get; set; }
    public decimal? EpsQ1Actual { get; set; }
    public decimal? EpsQ1SurprisePct { get; set; }
    public DateOnly? EpsQ2Date { get; set; }
    public decimal? EpsQ2Estimate { get; set; }
    public decimal? EpsQ2Actual { get; set; }
    public decimal? EpsQ2SurprisePct { get; set; }
    public DateOnly? EpsQ3Date { get; set; }
    public decimal? EpsQ3Estimate { get; set; }
    public decimal? EpsQ3Actual { get; set; }
    public decimal? EpsQ3SurprisePct { get; set; }
    public DateOnly? EpsQ4Date { get; set; }
    public decimal? EpsQ4Estimate { get; set; }
    public decimal? EpsQ4Actual { get; set; }
    public decimal? EpsQ4SurprisePct { get; set; }

    // Valuation multiples (from yfinance ticker.info)
    public decimal? TrailingPe { get; set; }
    public decimal? ForwardPe { get; set; }

    public DateTime UpdatedAt { get; set; }
}

[Table("IndianEarningsFundamentals")] public class IndianEarningsFundamentals : EarningsFundamentalsBase { }
[Table("USEarningsFundamentals")] public class USEarningsFundamentals : EarningsFundamentalsBase { }

// Reimagined fundamental screener "idea": one snapshot per ticker per earnings result.
// Earnings metrics are stamped to the reported result; analyst rating/targets refresh daily.
// Superseded results are flagged IsStale and hidden in the UI. Schema owned by SQL project.
public abstract class FundamentalIdeaBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly EarningsDate { get; set; }

    public decimal? EpsBeatPct { get; set; }
    public decimal? OpmExpansionYoyPct { get; set; }
    public decimal? OperatingProfitExpansionYoyPct { get; set; }

    public string? LatestRatingFirm { get; set; }
    public string? LatestRatingGrade { get; set; }
    public string? LatestRatingAction { get; set; }
    public DateOnly? LatestRatingDate { get; set; }
    public decimal? TargetLowPrice { get; set; }
    public decimal? TargetMeanPrice { get; set; }
    public decimal? TargetHighPrice { get; set; }

    public decimal? EpsBeatConfidence { get; set; }
    public decimal? OpmExpansionConfidence { get; set; }
    public decimal? OperatingProfitExpansionConfidence { get; set; }
    public decimal? AnalystRatingConfidence { get; set; }
    public decimal? TargetUpsideConfidence { get; set; }
    public decimal? FundamentalConfidence { get; set; }
    public decimal? TechnicalConfidence { get; set; }
    public decimal? OverallConfidence { get; set; }
    public int? DaysSinceEarnings { get; set; }
    public int? DaysSinceRating { get; set; }
    public string? ConfidenceRationaleJson { get; set; }

    public bool IsStale { get; set; }
    public DateTime CapturedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("IndianFundamentalIdeas")] public class IndianFundamentalIdea : FundamentalIdeaBase { }
[Table("USFundamentalIdeas")] public class USFundamentalIdea : FundamentalIdeaBase { }

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


// Breakouts opened/managed by the worker engine on scanner signals.
public abstract class BreakoutBase
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

[Table("IndianBreakouts")] public class IndianBreakout : BreakoutBase { }
[Table("USBreakouts")] public class USBreakout : BreakoutBase { }
