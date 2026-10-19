using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

[Table("AiStockAnalysisResults")]
public class AiStockAnalysisResult
{
    [Key]
    public long Id { get; set; }
    public string Market { get; set; } = string.Empty;
    public string Ticker { get; set; } = string.Empty;
    public string WeekNumber { get; set; } = string.Empty;
    public string SchemaVersion { get; set; } = string.Empty;
    public string SkillVersion { get; set; } = string.Empty;
    public string Model { get; set; } = string.Empty;
    public string InputJson { get; set; } = string.Empty;
    public string EarningsCallJson { get; set; } = string.Empty;
    public string CorporateFilingsJson { get; set; } = string.Empty;
    public string AnalysisJson { get; set; } = string.Empty;
    public long InputTokens { get; set; }
    public long CachedInputTokens { get; set; }
    public long OutputTokens { get; set; }
    public decimal InputCostUsd { get; set; }
    public decimal OutputCostUsd { get; set; }
    public decimal ToolCallsCostUsd { get; set; }
    public decimal TotalCostUsd { get; set; }
    public decimal Projected1000TickersCostUsd { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}