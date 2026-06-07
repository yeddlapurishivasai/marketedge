using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IStockService
{
    Task<PagedResult<StockDto>> SearchStocksAsync(string market, string? query, int? sectorId, int page, int pageSize);
    Task<StockDto?> GetStockByIdAsync(string market, int id);
    Task<StockDto> CreateStockAsync(string market, CreateStockRequest request);
    Task<bool> UpdateStockAsync(string market, int id, UpdateStockRequest request);
    Task<bool> DeleteStockAsync(string market, int id);
    Task<int> MoveStocksAsync(string market, MoveStocksRequest request);
}

public class StockService : IStockService
{
    private readonly MarketEdgeDbContext _db;
    public StockService(MarketEdgeDbContext db) => _db = db;

    public async Task<PagedResult<StockDto>> SearchStocksAsync(string market, string? query, int? sectorId, int page, int pageSize)
    {
        IQueryable<StockDto> stocks;

        if (market == "us")
        {
            var q = _db.USStocks.AsQueryable();
            if (!string.IsNullOrEmpty(query))
                q = q.Where(s => s.Symbol.Contains(query) || s.CompanyName.Contains(query));
            if (sectorId.HasValue)
                q = q.Where(s => s.SectorId == sectorId.Value);

            stocks = q.Select(s => new StockDto
            {
                Id = s.Id, Symbol = s.Symbol, CompanyName = s.CompanyName,
                SectorId = s.SectorId, SectorName = s.Sector.SectorName, BroadSector = s.BroadSector
            });
        }
        else
        {
            var q = _db.IndianStocks.AsQueryable();
            if (!string.IsNullOrEmpty(query))
                q = q.Where(s => s.Symbol.Contains(query) || s.CompanyName.Contains(query));
            if (sectorId.HasValue)
                q = q.Where(s => s.SectorId == sectorId.Value);

            stocks = q.Select(s => new StockDto
            {
                Id = s.Id, Symbol = s.Symbol, CompanyName = s.CompanyName,
                SectorId = s.SectorId, SectorName = s.Sector.SectorName, BroadSector = s.BroadSector
            });
        }

        var totalCount = await stocks.CountAsync();
        var items = await stocks
            .OrderBy(s => s.CompanyName)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync();

        return new PagedResult<StockDto> { Items = items, TotalCount = totalCount, Page = page, PageSize = pageSize };
    }

    public async Task<StockDto?> GetStockByIdAsync(string market, int id)
    {
        if (market == "us")
        {
            return await _db.USStocks.Where(s => s.Id == id)
                .Select(s => new StockDto
                {
                    Id = s.Id, Symbol = s.Symbol, CompanyName = s.CompanyName,
                    SectorId = s.SectorId, SectorName = s.Sector.SectorName, BroadSector = s.BroadSector
                }).FirstOrDefaultAsync();
        }

        return await _db.IndianStocks.Where(s => s.Id == id)
            .Select(s => new StockDto
            {
                Id = s.Id, Symbol = s.Symbol, CompanyName = s.CompanyName,
                SectorId = s.SectorId, SectorName = s.Sector.SectorName, BroadSector = s.BroadSector
            }).FirstOrDefaultAsync();
    }

    public async Task<StockDto> CreateStockAsync(string market, CreateStockRequest request)
    {
        if (market == "us")
        {
            var stock = new USStock
            {
                Symbol = request.Symbol, CompanyName = request.CompanyName,
                SectorId = request.SectorId, BroadSector = request.BroadSector, CreatedAt = DateTime.UtcNow
            };
            _db.USStocks.Add(stock);
            await _db.SaveChangesAsync();
            return (await GetStockByIdAsync(market, stock.Id))!;
        }
        else
        {
            var stock = new IndianStock
            {
                Symbol = request.Symbol, CompanyName = request.CompanyName,
                SectorId = request.SectorId, BroadSector = request.BroadSector, CreatedAt = DateTime.UtcNow
            };
            _db.IndianStocks.Add(stock);
            await _db.SaveChangesAsync();
            return (await GetStockByIdAsync(market, stock.Id))!;
        }
    }

    public async Task<bool> UpdateStockAsync(string market, int id, UpdateStockRequest request)
    {
        if (market == "us")
        {
            var stock = await _db.USStocks.FindAsync(id);
            if (stock == null) return false;
            if (request.CompanyName != null) stock.CompanyName = request.CompanyName;
            if (request.SectorId.HasValue) stock.SectorId = request.SectorId.Value;
            if (request.BroadSector != null) stock.BroadSector = request.BroadSector;
        }
        else
        {
            var stock = await _db.IndianStocks.FindAsync(id);
            if (stock == null) return false;
            if (request.CompanyName != null) stock.CompanyName = request.CompanyName;
            if (request.SectorId.HasValue) stock.SectorId = request.SectorId.Value;
            if (request.BroadSector != null) stock.BroadSector = request.BroadSector;
        }
        await _db.SaveChangesAsync();
        return true;
    }

    public async Task<bool> DeleteStockAsync(string market, int id)
    {
        if (market == "us")
        {
            var stock = await _db.USStocks.FindAsync(id);
            if (stock == null) return false;
            _db.USStocks.Remove(stock);
        }
        else
        {
            var stock = await _db.IndianStocks.FindAsync(id);
            if (stock == null) return false;
            _db.IndianStocks.Remove(stock);
        }
        await _db.SaveChangesAsync();
        return true;
    }

    public async Task<int> MoveStocksAsync(string market, MoveStocksRequest request)
    {
        if (market == "us")
        {
            return await _db.USStocks
                .Where(s => request.StockIds.Contains(s.Id))
                .ExecuteUpdateAsync(s => s.SetProperty(x => x.SectorId, request.TargetSectorId));
        }

        return await _db.IndianStocks
            .Where(s => request.StockIds.Contains(s.Id))
            .ExecuteUpdateAsync(s => s.SetProperty(x => x.SectorId, request.TargetSectorId));
    }
}
