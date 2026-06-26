using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

// Query-only mappings of the ingestion tables consumed by the Stock Lookup page.
// Schema is owned by the SQL project; these are never used for migrations.

public abstract class TickerBase
{
    public string Ticker { get; set; } = string.Empty;
    public string? Exchange { get; set; }
    public bool Active { get; set; }
    public bool IsFno { get; set; }
    public int? BarsAvailable { get; set; }
}

[Table("IndianTickers")] public class IndianTicker : TickerBase { }
[Table("USTickers")] public class USTicker : TickerBase { }

public abstract class TickerTechnicalBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly AsOfDate { get; set; }
    public decimal? Close { get; set; }
    public decimal? DayPct { get; set; }
    public decimal? Open { get; set; }
    public decimal? High { get; set; }
    public decimal? Low { get; set; }
    public decimal? High52w { get; set; }
    public decimal? From52wHigh { get; set; }
    public long? MarketCap { get; set; }
    public int? Rs { get; set; }
    public int? Rs1d { get; set; }
    public int? Rs1w { get; set; }
    public int? Rs1m { get; set; }
    public int? Rs3m { get; set; }
    public int? Rs6m { get; set; }
    public string? RsType { get; set; }
    public DateOnly? RsDate { get; set; }
    public int? ScannerHits { get; set; }
    public DateOnly? LastScannerHit { get; set; }
}

[Table("IndianTickerTechnical")] public class IndianTickerTechnical : TickerTechnicalBase { }
[Table("USTickerTechnical")] public class USTickerTechnical : TickerTechnicalBase { }

public abstract class AnalystSnapshotBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly AsOfDate { get; set; }
    public string? ConsensusRating { get; set; }
    public int? NumAnalysts { get; set; }
    public decimal? CurrentQuarterEps { get; set; }
    public decimal? NextQuarterEps { get; set; }
    public decimal? CurrentYearEps { get; set; }
    public decimal? NextYearEps { get; set; }
    public decimal? TargetLowPrice { get; set; }
    public decimal? TargetMeanPrice { get; set; }
    public decimal? TargetHighPrice { get; set; }
}

[Table("IndianAnalystSnapshot")] public class IndianAnalystSnapshot : AnalystSnapshotBase { }
[Table("USAnalystSnapshot")] public class USAnalystSnapshot : AnalystSnapshotBase { }

public abstract class EpsForecastBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly AsOfDate { get; set; }
    public string PeriodType { get; set; } = string.Empty;
    public DateOnly PeriodEndDate { get; set; }
    public decimal? ConsensusEps { get; set; }
    public decimal? HighEps { get; set; }
    public decimal? LowEps { get; set; }
    public int? NumEstimates { get; set; }
    public int RevisionsUp { get; set; }
    public int RevisionsDown { get; set; }
}

[Table("IndianEpsForecasts")] public class IndianEpsForecast : EpsForecastBase { }
[Table("USEpsForecasts")] public class USEpsForecast : EpsForecastBase { }

public abstract class Bar1DBase
{
    public string Ticker { get; set; } = string.Empty;
    public DateOnly BarDate { get; set; }
    public decimal? Open { get; set; }
    public decimal? High { get; set; }
    public decimal? Low { get; set; }
    public decimal? Close { get; set; }
    public long? Volume { get; set; }
    public decimal? AdjClose { get; set; }
}

[Table("IndianBars1D")] public class IndianBar1D : Bar1DBase { }
[Table("USBars1D")] public class USBar1D : Bar1DBase { }
