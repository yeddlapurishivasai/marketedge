using MarketEdge.Api.Data;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.UnitTests;

public static class DbContextFactory
{
    public static MarketEdgeDbContext Create(string? dbName = null)
    {
        var options = new DbContextOptionsBuilder<MarketEdgeDbContext>()
            .UseInMemoryDatabase(dbName ?? Guid.NewGuid().ToString())
            .Options;

        return new MarketEdgeDbContext(options);
    }
}
