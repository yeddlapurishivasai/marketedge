namespace MarketEdge.Api.Models;

public class TriggerStage4Request
{
    public string? WeekNumber { get; set; }
    public decimal? MinMarketCap { get; set; }
    public decimal? MaxMarketCap { get; set; }
    public List<int>? SectorIds { get; set; }
    public int? Limit { get; set; }
    public bool? TestSampleOnly { get; set; }
    public int? RetrySourceRunId { get; set; }
}

public class Stage4AnalysisResultDto : Stage4AnalysisResultBase
{
    public bool IsFno => true;
}

public record SectorStage4CountDto(int SectorId, string SectorName, int Stage4Count, int TotalCount);

public class Stage4SummaryDto
{
    public int RunId { get; set; }
    public string WeekNumber { get; set; } = string.Empty;
    public string Scope { get; set; } = "full";
    public Dictionary<string, object> Metrics { get; set; } = new();
    public int TotalStocks { get; set; }
    public int EvaluatedStocks { get; set; }
    public int SkippedStocks { get; set; }
    public int ErrorStocks { get; set; }
    public int Stage4Count { get; set; }
    public int NewAdditions { get; set; }
    public int ReEntries { get; set; }
    public int Continuing { get; set; }
    public int Removed { get; set; }
    public List<SectorStage4CountDto> BySector { get; set; } = new();
    public List<Stage4AnalysisResultDto> Top25 { get; set; } = new();
    public List<string> Warnings { get; set; } = new();
}

public record Stage4HistoryDto(
    int RunId, string WeekNumber, DateTime RunDate, string Scope,
    int TotalStage4, List<SectorStage4CountDto> BySector);
