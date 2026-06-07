using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public class SectorServiceTests
{
    [Fact]
    public async Task GetSectorsAsync_ReturnsEmptyList_WhenNoSectors()
    {
        using var db = DbContextFactory.Create();
        var service = new SectorService(db);

        var result = await service.GetSectorsAsync("india");

        result.Should().BeEmpty();
    }

    [Fact]
    public async Task GetSectorsAsync_ReturnsSortedSectors_WithStockCounts()
    {
        using var db = DbContextFactory.Create();
        var sectorA = new IndianSector { SectorName = "Zebra Sector", CreatedAt = DateTime.UtcNow };
        var sectorB = new IndianSector { SectorName = "Alpha Sector", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.AddRange(sectorA, sectorB);
        db.IndianStocks.Add(new IndianStock { Symbol = "TEST", CompanyName = "Test Co", Sector = sectorA, CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.GetSectorsAsync("india");

        result.Should().HaveCount(2);
        result[0].SectorName.Should().Be("Alpha Sector");
        result[0].StockCount.Should().Be(0);
        result[1].SectorName.Should().Be("Zebra Sector");
        result[1].StockCount.Should().Be(1);
    }

    [Fact]
    public async Task GetSectorsAsync_UsMarket_ReturnsUSSectors()
    {
        using var db = DbContextFactory.Create();
        db.USSectors.Add(new USSector { SectorName = "Tech", CreatedAt = DateTime.UtcNow });
        db.IndianSectors.Add(new IndianSector { SectorName = "Pharma", CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.GetSectorsAsync("us");

        result.Should().HaveCount(1);
        result[0].SectorName.Should().Be("Tech");
    }

    [Fact]
    public async Task CreateSectorAsync_CreatesSectorAndReturnsDto()
    {
        using var db = DbContextFactory.Create();
        var service = new SectorService(db);

        var result = await service.CreateSectorAsync("india", new CreateSectorRequest { SectorName = "New Sector" });

        result.SectorName.Should().Be("New Sector");
        result.StockCount.Should().Be(0);
        result.Id.Should().BeGreaterThan(0);
        db.IndianSectors.Should().HaveCount(1);
    }

    [Fact]
    public async Task CreateSectorAsync_Us_CreatesUsSector()
    {
        using var db = DbContextFactory.Create();
        var service = new SectorService(db);

        var result = await service.CreateSectorAsync("us", new CreateSectorRequest { SectorName = "Fintech" });

        result.SectorName.Should().Be("Fintech");
        db.USSectors.Should().HaveCount(1);
    }

    [Fact]
    public async Task RenameSectorAsync_RenamesSector_ReturnsTrue()
    {
        using var db = DbContextFactory.Create();
        db.IndianSectors.Add(new IndianSector { Id = 1, SectorName = "Old Name", CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.RenameSectorAsync("india", 1, "New Name");

        result.Should().BeTrue();
        (await db.IndianSectors.FindAsync(1))!.SectorName.Should().Be("New Name");
    }

    [Fact]
    public async Task RenameSectorAsync_NonExistentId_ReturnsFalse()
    {
        using var db = DbContextFactory.Create();
        var service = new SectorService(db);

        var result = await service.RenameSectorAsync("india", 999, "Name");

        result.Should().BeFalse();
    }

    [Fact]
    public async Task DeleteSectorAsync_EmptySector_DeletesAndReturnsTrue()
    {
        using var db = DbContextFactory.Create();
        db.IndianSectors.Add(new IndianSector { Id = 1, SectorName = "Empty", CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.DeleteSectorAsync("india", 1);

        result.Should().BeTrue();
        db.IndianSectors.Should().BeEmpty();
    }

    [Fact]
    public async Task DeleteSectorAsync_SectorWithStocks_ReturnsFalse()
    {
        using var db = DbContextFactory.Create();
        var sector = new IndianSector { Id = 1, SectorName = "Has Stocks", CreatedAt = DateTime.UtcNow };
        db.IndianSectors.Add(sector);
        db.IndianStocks.Add(new IndianStock { Symbol = "X", CompanyName = "X Corp", SectorId = 1, CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.DeleteSectorAsync("india", 1);

        result.Should().BeFalse();
        db.IndianSectors.Should().HaveCount(1);
    }

    [Fact]
    public async Task GetSectorByIdAsync_ExistingSector_ReturnsDto()
    {
        using var db = DbContextFactory.Create();
        db.IndianSectors.Add(new IndianSector { Id = 1, SectorName = "Test", CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var service = new SectorService(db);
        var result = await service.GetSectorByIdAsync("india", 1);

        result.Should().NotBeNull();
        result!.SectorName.Should().Be("Test");
    }

    [Fact]
    public async Task GetSectorByIdAsync_NonExistent_ReturnsNull()
    {
        using var db = DbContextFactory.Create();
        var service = new SectorService(db);

        var result = await service.GetSectorByIdAsync("india", 999);

        result.Should().BeNull();
    }
}
