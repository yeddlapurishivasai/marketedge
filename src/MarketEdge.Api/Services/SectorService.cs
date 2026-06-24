using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface ISectorService
{
    Task<List<SectorDto>> GetSectorsAsync(string market, bool testSampleOnly = false);
    Task<SectorDto?> GetSectorByIdAsync(string market, int id);
    Task<SectorDto> CreateSectorAsync(string market, CreateSectorRequest request);
    Task<bool> RenameSectorAsync(string market, int id, string newName);
    Task<bool> DeleteSectorAsync(string market, int id);
}

public class SectorService : ISectorService
{
    private readonly MarketEdgeDbContext _db;
    public SectorService(MarketEdgeDbContext db) => _db = db;

    public async Task<List<SectorDto>> GetSectorsAsync(string market, bool testSampleOnly = false)
    {
        if (market == "us")
        {
            var query = _db.USSectors.AsQueryable();
            if (testSampleOnly) query = query.Where(s => s.Stocks.Any(st => st.IsTestSample));
            return await query
                .Select(s => new SectorDto
                {
                    Id = s.Id,
                    SectorName = s.SectorName,
                    StockCount = testSampleOnly ? s.Stocks.Count(st => st.IsTestSample) : s.Stocks.Count
                })
                .OrderBy(s => s.SectorName)
                .ToListAsync();
        }

        var inQuery = _db.IndianSectors.AsQueryable();
        if (testSampleOnly) inQuery = inQuery.Where(s => s.Stocks.Any(st => st.IsTestSample));
        return await inQuery
            .Select(s => new SectorDto
            {
                Id = s.Id,
                SectorName = s.SectorName,
                StockCount = testSampleOnly ? s.Stocks.Count(st => st.IsTestSample) : s.Stocks.Count
            })
            .OrderBy(s => s.SectorName)
            .ToListAsync();
    }

    public async Task<SectorDto?> GetSectorByIdAsync(string market, int id)
    {
        if (market == "us")
        {
            return await _db.USSectors
                .Where(s => s.Id == id)
                .Select(s => new SectorDto { Id = s.Id, SectorName = s.SectorName, StockCount = s.Stocks.Count })
                .FirstOrDefaultAsync();
        }

        return await _db.IndianSectors
            .Where(s => s.Id == id)
            .Select(s => new SectorDto { Id = s.Id, SectorName = s.SectorName, StockCount = s.Stocks.Count })
            .FirstOrDefaultAsync();
    }

    public async Task<SectorDto> CreateSectorAsync(string market, CreateSectorRequest request)
    {
        if (market == "us")
        {
            var sector = new USSector { SectorName = request.SectorName, CreatedAt = DateTime.UtcNow };
            _db.USSectors.Add(sector);
            await _db.SaveChangesAsync();
            return new SectorDto { Id = sector.Id, SectorName = sector.SectorName, StockCount = 0 };
        }
        else
        {
            var sector = new IndianSector { SectorName = request.SectorName, CreatedAt = DateTime.UtcNow };
            _db.IndianSectors.Add(sector);
            await _db.SaveChangesAsync();
            return new SectorDto { Id = sector.Id, SectorName = sector.SectorName, StockCount = 0 };
        }
    }

    public async Task<bool> RenameSectorAsync(string market, int id, string newName)
    {
        if (market == "us")
        {
            var sector = await _db.USSectors.FindAsync(id);
            if (sector == null) return false;
            sector.SectorName = newName;
        }
        else
        {
            var sector = await _db.IndianSectors.FindAsync(id);
            if (sector == null) return false;
            sector.SectorName = newName;
        }
        await _db.SaveChangesAsync();
        return true;
    }

    public async Task<bool> DeleteSectorAsync(string market, int id)
    {
        if (market == "us")
        {
            var hasStocks = await _db.USStocks.AnyAsync(s => s.SectorId == id);
            if (hasStocks) return false;
            var sector = await _db.USSectors.FindAsync(id);
            if (sector == null) return false;
            _db.USSectors.Remove(sector);
        }
        else
        {
            var hasStocks = await _db.IndianStocks.AnyAsync(s => s.SectorId == id);
            if (hasStocks) return false;
            var sector = await _db.IndianSectors.FindAsync(id);
            if (sector == null) return false;
            _db.IndianSectors.Remove(sector);
        }
        await _db.SaveChangesAsync();
        return true;
    }
}
