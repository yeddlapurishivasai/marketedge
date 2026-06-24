using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace MarketEdge.Api.Models;

[Table("IndianSectors")]
public class IndianSector
{
    [Key]
    public int Id { get; set; }
    public string SectorName { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public ICollection<IndianStock> Stocks { get; set; } = new List<IndianStock>();
}

[Table("IndianStocks")]
public class IndianStock
{
    [Key]
    public int Id { get; set; }
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string? BroadSector { get; set; }
    public decimal? MarketCap { get; set; }
    public bool IsFno { get; set; }
    public DateTime CreatedAt { get; set; }

    [ForeignKey(nameof(SectorId))]
    public IndianSector Sector { get; set; } = null!;
}

[Table("USSectors")]
public class USSector
{
    [Key]
    public int Id { get; set; }
    public string SectorName { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public ICollection<USStock> Stocks { get; set; } = new List<USStock>();
}

[Table("USStocks")]
public class USStock
{
    [Key]
    public int Id { get; set; }
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string? BroadSector { get; set; }
    public decimal? MarketCap { get; set; }
    public bool IsFno { get; set; }
    public DateTime CreatedAt { get; set; }

    [ForeignKey(nameof(SectorId))]
    public USSector Sector { get; set; } = null!;
}
