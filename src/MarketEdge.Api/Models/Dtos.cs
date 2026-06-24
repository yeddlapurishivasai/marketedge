namespace MarketEdge.Api.Models;

public class SectorDto
{
    public int Id { get; set; }
    public string SectorName { get; set; } = string.Empty;
    public int StockCount { get; set; }
}

public class StockDto
{
    public int Id { get; set; }
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string? SectorName { get; set; }
    public string? BroadSector { get; set; }
    public decimal? MarketCap { get; set; }
    public bool IsFno { get; set; }
}

public class CreateSectorRequest
{
    public string SectorName { get; set; } = string.Empty;
}

public class CreateStockRequest
{
    public string Symbol { get; set; } = string.Empty;
    public string CompanyName { get; set; } = string.Empty;
    public int SectorId { get; set; }
    public string? BroadSector { get; set; }
    public bool IsFno { get; set; }
}

public class UpdateStockRequest
{
    public string? CompanyName { get; set; }
    public int? SectorId { get; set; }
    public string? BroadSector { get; set; }
    public bool? IsFno { get; set; }
}

public class MoveStocksRequest
{
    public List<int> StockIds { get; set; } = new();
    public int TargetSectorId { get; set; }
}

public class PagedResult<T>
{
    public List<T> Items { get; set; } = new();
    public int TotalCount { get; set; }
    public int Page { get; set; }
    public int PageSize { get; set; }
}
