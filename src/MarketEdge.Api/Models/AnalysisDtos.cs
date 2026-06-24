namespace MarketEdge.Api.Models;

// --- Job Runs ---

public class JobRunDto
{
    public int Id { get; set; }
    public string JobType { get; set; } = string.Empty;
    public string Market { get; set; } = string.Empty;
    public string Status { get; set; } = string.Empty;
    public int Progress { get; set; }
    public Dictionary<string, object>? Parameters { get; set; }
    public Dictionary<string, object>? Metrics { get; set; }
    public string? ErrorMessage { get; set; }
    public DateTime? StartedAt { get; set; }
    public DateTime? CompletedAt { get; set; }
    public DateTime CreatedAt { get; set; }
    public double? DurationSeconds { get; set; }
}

public class TriggerAnalysisRequest
{
    public decimal? MinMarketCap { get; set; }
    public decimal? MaxMarketCap { get; set; }
    public List<int>? SectorIds { get; set; }
    public int? Limit { get; set; }
    /// <summary>
    /// When true, restricts the run to stocks flagged IsTestSample (200 India + 200 US)
    /// for fast local/e2e runs.
    /// </summary>
    public bool? TestSampleOnly { get; set; }
    /// <summary>
    /// When true, forces a new run even if one already exists for this week.
    /// The previous same-week run will be superseded.
    /// </summary>
    public bool Force { get; set; }
}

// --- Stage Analysis Results ---

public class StageAnalysisResultDto
{
    public int Id { get; set; }
    public int RunId { get; set; }
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string SectorName { get; set; } = string.Empty;
    public decimal? ClosePrice { get; set; }
    public decimal? MA10 { get; set; }
    public decimal? MA30 { get; set; }
    public decimal? MarketCap { get; set; }
    public bool IsStage2 { get; set; }
    public string? Classification { get; set; }
    public int? WeeksInStage2 { get; set; }
    public decimal? RSScore { get; set; }
    public int? RSRank { get; set; }
    public decimal? RS1w { get; set; }
    public decimal? RS2w { get; set; }
    public decimal? RS3w { get; set; }
    public decimal? RSDelta1w { get; set; }
    public decimal? RSDelta2w { get; set; }
    public decimal? RSDelta3w { get; set; }
    public decimal? MomentumScore { get; set; }
    public decimal? ROC1w { get; set; }
    public decimal? ROC2w { get; set; }
    public decimal? ROC3w { get; set; }
    public string? Quadrant { get; set; }
    public decimal? ADRatio { get; set; }
    public string? ADClassification { get; set; }
}

// --- Sector Rotation ---

public class SectorRotationDto
{
    public string SectorName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public decimal AvgRSScore { get; set; }
    public decimal AvgRSDelta2w { get; set; }
    public string Quadrant { get; set; } = string.Empty;
    public int StockCount { get; set; }
    public int AccumulatingCount { get; set; }
    public int DistributingCount { get; set; }
}

// --- Stage 2 Summary ---

public class Stage2SummaryDto
{
    public int TotalStocks { get; set; }
    public int Stage2Count { get; set; }
    public int NewAdditions { get; set; }
    public int ReEntries { get; set; }
    public int Continuing { get; set; }
    public int Removed { get; set; }
    public List<SectorStage2CountDto> BySector { get; set; } = new();
    public List<StageAnalysisResultDto> Top25 { get; set; } = new();
}

public class SectorStage2CountDto
{
    public string SectorName { get; set; } = string.Empty;
    public int Stage2Count { get; set; }
    public int TotalCount { get; set; }
}

// --- Stage 2 History for line charts ---

public class Stage2HistoryDto
{
    public int RunId { get; set; }
    public DateTime RunDate { get; set; }
    public int TotalStage2 { get; set; }
    public List<SectorStage2CountDto> BySector { get; set; } = new();
}

// --- Sector Rotation History (for animated timeline) ---

public class SectorRotationHistoryDto
{
    public int RunId { get; set; }
    public DateTime RunDate { get; set; }
    public List<SectorRotationDto> Sectors { get; set; } = new();
}
