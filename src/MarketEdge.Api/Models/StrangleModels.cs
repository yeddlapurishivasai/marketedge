using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Models;

public sealed record StrangleRunRequest(Guid RequestId, string IndexCode, string Action, decimal WingWidth,
    decimal? XOverride, int? StrategyId, List<string>? SelectedTradeIds = null,
    decimal? RiskFreeRate = null, decimal? DividendYield = null);
public sealed record StrangleBaselineLeg(string Exchange, string TradingSymbol, string Product, int Quantity, decimal EntryPrice);
public sealed record StrangleConfirmRequest(int EntryRunId, List<StrangleBaselineLeg> Legs, decimal XPoints);
public sealed record StrangleRunDto(int Id, int JobRunId, string IndexCode, string Action, string Status,
    DateTime GeneratedAt, string? ErrorMessage, JsonElement? Result, JsonElement? Ai);

[Table("StrangleRuns", Schema = "options")]
[Index(nameof(UserKey), nameof(RequestId), IsUnique = true)]
public sealed class StrangleRun
{
    public int Id { get; set; }
    [MaxLength(200)] public string UserKey { get; set; } = "";
    public Guid RequestId { get; set; }
    [MaxLength(16)] public string IndexCode { get; set; } = "";
    [MaxLength(16)] public string Action { get; set; } = "";
    public int? StrategyId { get; set; }
    public int JobRunId { get; set; }
    [MaxLength(16)] public string Status { get; set; } = "queued";
    public string RequestJson { get; set; } = "{}";
    public string? ResultJson { get; set; }
    public string? AiJson { get; set; }
    public DateTime GeneratedAt { get; set; }
    public DateTime? CompletedAt { get; set; }
    [MaxLength(2000)] public string? ErrorMessage { get; set; }
}

[Table("StrangleStrategies", Schema = "options")]
[Index(nameof(UserKey), nameof(EntryRunId), IsUnique = true)]
public sealed class StrangleStrategy
{
    public int Id { get; set; }
    [MaxLength(200)] public string UserKey { get; set; } = "";
    public int EntryRunId { get; set; }
    [MaxLength(16)] public string IndexCode { get; set; } = "";
    public DateOnly SessionDate { get; set; }
    public string BaselineJson { get; set; } = "{}";
    public DateTime CreatedAt { get; set; }
}
