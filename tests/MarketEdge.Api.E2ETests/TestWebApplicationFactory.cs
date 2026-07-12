using MarketEdge.Api.Data;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace MarketEdge.Api.E2ETests;

public class TestWebApplicationFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    static TestWebApplicationFactory()
    {
        // AddMarketEdgeAuth() reads AzureAd:Enabled eagerly while the host is being
        // built (before Build()), so WebApplicationFactory's ConfigureAppConfiguration
        // overrides land too late to influence it. An environment variable is picked up
        // by WebApplication.CreateBuilder's AddEnvironmentVariables and therefore wins.
        //
        // Force auth OFF for the whole suite so tests never depend on a developer's local
        // appsettings.Development.json (which may enable auth for manual login testing).
        // The value is uniform, so this is safe under xUnit's parallel test execution.
        Environment.SetEnvironmentVariable("AzureAd__Enabled", "false");
    }

    private readonly string _dbName = $"MarketEdge_Test_{Guid.NewGuid():N}";

    public string ConnectionString =>
        $"Server=localhost;Database={_dbName};Trusted_Connection=True;TrustServerCertificate=True;";

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            // Remove the existing DbContext registration
            var descriptor = services.SingleOrDefault(
                d => d.ServiceType == typeof(DbContextOptions<MarketEdgeDbContext>));
            if (descriptor != null) services.Remove(descriptor);

            // Add DbContext with test database
            services.AddDbContext<MarketEdgeDbContext>(options =>
                options.UseSqlServer(ConnectionString));
        });
    }

    public async Task InitializeAsync()
    {
        using var scope = Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        await db.Database.EnsureCreatedAsync();
    }

    async Task IAsyncLifetime.DisposeAsync()
    {
        using var scope = Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        await db.Database.EnsureDeletedAsync();
    }
}
