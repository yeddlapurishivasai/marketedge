using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;
using System.Text.Json.Serialization;

namespace MarketEdge.Api.Models;

public abstract class Stage4AnalysisResultBase
{
    [Key]
    public int Id { get; set; }
    public int RunId { get; set; }
    public string WeekNumber { get; set; } = string.Empty;
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string SectorName { get; set; } = string.Empty;
    public decimal? ClosePrice { get; set; }
    [JsonPropertyName("ma10")]
    public decimal? MA10 { get; set; }
    [JsonPropertyName("ma30")]
    public decimal? MA30 { get; set; }
    [JsonPropertyName("ma30FourWeeksAgo")]
    public decimal? MA30FourWeeksAgo { get; set; }
    public decimal? MarketCap { get; set; }
    public bool? IsStage4 { get; set; }
    public string AnalysisStatus { get; set; } = "insufficient";
    public string? ErrorMessage { get; set; }
    public DateOnly? PriceAsOfDate { get; set; }
    public DateTime EligibilityCapturedAt { get; set; }
    public string? Classification { get; set; }
    public int? WeeksInStage4 { get; set; }
    public decimal? RSScore { get; set; }
    public decimal? RSDelta1w { get; set; }
    public decimal? RSDelta2w { get; set; }
    public decimal? RSDelta3w { get; set; }
    public decimal? MomentumScore { get; set; }
    [JsonPropertyName("roc1w")]
    public decimal? ROC1w { get; set; }
    [JsonPropertyName("roc2w")]
    public decimal? ROC2w { get; set; }
    [JsonPropertyName("roc3w")]
    public decimal? ROC3w { get; set; }
    public string? Quadrant { get; set; }
    public decimal? ADRatio { get; set; }
    public string? ADClassification { get; set; }
    public DateTime CreatedAt { get; set; }
}

[Table("IndianStage4AnalysisResults")]
public class IndianStage4AnalysisResult : Stage4AnalysisResultBase;

[Table("USStage4AnalysisResults")]
public class USStage4AnalysisResult : Stage4AnalysisResultBase;

[Table("Stage4Schedules")]
public class Stage4Schedule
{
    [Key]
    public string Market { get; set; } = string.Empty;
    public bool Enabled { get; set; } = true;
    public int HourLocal { get; set; } = 20;
    public DateTime? LastEnqueuedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
