using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

[Table("JobRuns")]
public class JobRun
{
    [Key]
    public int Id { get; set; }
    public string JobType { get; set; } = string.Empty;
    public string Market { get; set; } = string.Empty;
    public string Status { get; set; } = "queued";
    public int Progress { get; set; }
    public string? Parameters { get; set; }
    public string? Metrics { get; set; }
    public string? ErrorMessage { get; set; }
    public DateTime? StartedAt { get; set; }
    public DateTime? CompletedAt { get; set; }
    public DateTime CreatedAt { get; set; }

    public ICollection<IndianStageAnalysisResult> IndianStageAnalysisResults { get; set; } = new List<IndianStageAnalysisResult>();
    public ICollection<USStageAnalysisResult> USStageAnalysisResults { get; set; } = new List<USStageAnalysisResult>();
}

public abstract class StageAnalysisResultBase
{
    [Key]
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

    public decimal? RSScore { get; set; }
    public int? RSRank { get; set; }
    public decimal? RSMomentum { get; set; }

    public decimal? MomentumScore { get; set; }
    public decimal? ROC12w { get; set; }
    public decimal? ROC26w { get; set; }
    public decimal? ROC52w { get; set; }

    public string? Quadrant { get; set; }

    public decimal? ADRatio { get; set; }
    public string? ADClassification { get; set; }

    public DateTime CreatedAt { get; set; }
}

[Table("IndianStageAnalysisResults")]
public class IndianStageAnalysisResult : StageAnalysisResultBase
{
    [ForeignKey(nameof(RunId))]
    public JobRun JobRun { get; set; } = null!;
}

[Table("USStageAnalysisResults")]
public class USStageAnalysisResult : StageAnalysisResultBase
{
    [ForeignKey(nameof(RunId))]
    public JobRun JobRun { get; set; } = null!;
}
