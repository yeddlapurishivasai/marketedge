using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

public abstract class ScannerResultBase
{
    [Key]
    public int Id { get; set; }
    public int RunId { get; set; }
    public string ScannerName { get; set; } = string.Empty;
    public DateTime ScanDate { get; set; }
    public string Symbol { get; set; } = string.Empty;
    public string? CompanyName { get; set; }
    public string? SectorName { get; set; }
    public string? Industry { get; set; }

    public decimal? ClosePrice { get; set; }
    public decimal? DayChangePct { get; set; }
    public long? Volume { get; set; }
    public decimal? RelVolume { get; set; }
    public int? RsRating { get; set; }

    public string? TriggerDetails { get; set; }
    public DateTime CreatedAt { get; set; }
}

[Table("IndianScannerResults")]
public class IndianScannerResult : ScannerResultBase { }

[Table("USScannerResults")]
public class USScannerResult : ScannerResultBase { }

[Table("ScannerSchedules")]
public class ScannerSchedule
{
    [Key]
    public string Market { get; set; } = string.Empty;
    public bool Enabled { get; set; }
    public int IntervalMinutes { get; set; } = 15;
    public DateTime? LastEnqueuedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
