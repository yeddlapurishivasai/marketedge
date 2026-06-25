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
