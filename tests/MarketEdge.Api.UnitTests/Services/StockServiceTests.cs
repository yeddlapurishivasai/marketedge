using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public class StockServiceTests
{
    [Fact]
    public async Task SearchStocksAsync_ReturnsPagedResults()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        for (int i = 1; i <= 10; i++)
            db.IndianStocks.Add(new IndianStock { Symbol = $"STK{i}", CompanyName = $"Company {i}", Sector = sector, CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.SearchStocksAsync("india", null, null, 1, 5);

        result.TotalCount.Should().Be(10);
        result.Items.Should().HaveCount(5);
        result.Page.Should().Be(1);
        result.PageSize.Should().Be(5);
    }

    [Fact]
    public async Task SearchStocksAsync_FiltersByQuery()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        db.IndianStocks.AddRange(
            new IndianStock { Symbol = "TCS", CompanyName = "Tata Consultancy", Sector = sector, CreatedAt = DateTime.UtcNow },
            new IndianStock { Symbol = "INFY", CompanyName = "Infosys", Sector = sector, CreatedAt = DateTime.UtcNow }
        );
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.SearchStocksAsync("india", "Tata", null, 1, 50);

        result.TotalCount.Should().Be(1);
        result.Items[0].Symbol.Should().Be("TCS");
    }

    [Fact]
    public async Task SearchStocksAsync_FiltersBySectorId()
    {
        using var db = DbContextFactory.Create();
        var sector1 = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        var sector2 = new IndianSector { SectorName = "Pharma", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.AddRange(sector1, sector2);
        db.IndianStocks.AddRange(
            new IndianStock { Symbol = "TCS", CompanyName = "TCS", Sector = sector1, CreatedAt = DateTime.UtcNow },
            new IndianStock { Symbol = "SUN", CompanyName = "Sun Pharma", Sector = sector2, CreatedAt = DateTime.UtcNow }
        );
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.SearchStocksAsync("india", null, sector1.Id, 1, 50);

        result.TotalCount.Should().Be(1);
        result.Items[0].Symbol.Should().Be("TCS");
    }

    [Fact]
    public async Task CreateStockAsync_CreatesAndReturnsDto()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.CreateStockAsync("india", new CreateStockRequest
        {
            Symbol = "NEW",
            CompanyName = "New Corp",
            SectorId = sector.Id,
            BroadSector = "Technology"
        });

        result.Symbol.Should().Be("NEW");
        result.CompanyName.Should().Be("New Corp");
        result.SectorId.Should().Be(sector.Id);
    }

    [Fact]
    public async Task UpdateStockAsync_UpdatesFields_ReturnsTrue()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        var stock = new IndianStock { Symbol = "OLD", CompanyName = "Old Corp", Sector = sector, CreatedAt = DateTime.UtcNow };
        db.IndianStocks.Add(stock);
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.UpdateStockAsync("india", stock.Id, new UpdateStockRequest { CompanyName = "New Corp" });

        result.Should().BeTrue();
        (await db.IndianStocks.FindAsync(stock.Id))!.CompanyName.Should().Be("New Corp");
    }

    [Fact]
    public async Task UpdateStockAsync_NonExistent_ReturnsFalse()
    {
        using var db = DbContextFactory.Create();
        var service = new StockService(db);

        var result = await service.UpdateStockAsync("india", 999, new UpdateStockRequest { CompanyName = "X" });

        result.Should().BeFalse();
    }

    [Fact]
    public async Task DeleteStockAsync_ExistingStock_DeletesAndReturnsTrue()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        var stock = new IndianStock { Symbol = "DEL", CompanyName = "Delete Me", Sector = sector, CreatedAt = DateTime.UtcNow };
        db.IndianStocks.Add(stock);
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.DeleteStockAsync("india", stock.Id);

        result.Should().BeTrue();
        db.IndianStocks.Should().BeEmpty();
    }

    [Fact]
    public async Task DeleteStockAsync_NonExistent_ReturnsFalse()
    {
        using var db = DbContextFactory.Create();
        var service = new StockService(db);

        var result = await service.DeleteStockAsync("india", 999);

        result.Should().BeFalse();
    }

    [Fact(Skip = "ExecuteUpdate not supported by InMemory provider - tested in E2E")]
    public async Task MoveStocksAsync_MovesStocksToTargetSector()
    {
        using var db = DbContextFactory.Create();
        var sector1 = new IndianSector { SectorName = "Source", CreatedAt = DateTime.UtcNow };
        var sector2 = new IndianSector { SectorName = "Target", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.AddRange(sector1, sector2);
        var stock1 = new IndianStock { Symbol = "A", CompanyName = "A Corp", Sector = sector1, CreatedAt = DateTime.UtcNow };
        var stock2 = new IndianStock { Symbol = "B", CompanyName = "B Corp", Sector = sector1, CreatedAt = DateTime.UtcNow };
        db.IndianStocks.AddRange(stock1, stock2);
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var moved = await service.MoveStocksAsync("india", new MoveStocksRequest
        {
            StockIds = [stock1.Id, stock2.Id],
            TargetSectorId = sector2.Id
        });

        moved.Should().Be(2);
    }

    [Fact]
    public async Task SearchStocksAsync_UsMarket_ReturnsUsStocks()
    {
        using var db = DbContextFactory.Create();
        var sector = new USSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow };
        db.USSectors.Add(sector);
        db.USStocks.Add(new USStock { Symbol = "AAPL", CompanyName = "Apple Inc", Sector = sector, CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new StockService(db);
        var result = await service.SearchStocksAsync("us", null, null, 1, 50);

        result.TotalCount.Should().Be(1);
        result.Items[0].Symbol.Should().Be("AAPL");
    }
}
