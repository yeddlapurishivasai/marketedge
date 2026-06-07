using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
using MarketEdge.Api.Models;

namespace MarketEdge.Api.E2ETests;

public class SectorsEndpointTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly HttpClient _client;

    public SectorsEndpointTests(TestWebApplicationFactory factory)
    {
        _client = factory.CreateClient();
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task GetSectors_ReturnsOk(string market)
    {
        var response = await _client.GetAsync($"/api/{market}/sectors");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var sectors = await response.Content.ReadFromJsonAsync<List<SectorDto>>();
        sectors.Should().NotBeNull();
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task CreateSector_ReturnsCreatedSector(string market)
    {
        var response = await _client.PostAsJsonAsync($"/api/{market}/sectors", new { sectorName = "Technology" });

        response.IsSuccessStatusCode.Should().BeTrue();
        var sector = await response.Content.ReadFromJsonAsync<SectorDto>();
        sector.Should().NotBeNull();
        sector!.SectorName.Should().Be("Technology");
        sector.Id.Should().BeGreaterThan(0);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task RenameSector_RenamesSuccessfully(string market)
    {
        // Create
        var createRes = await _client.PostAsJsonAsync($"/api/{market}/sectors", new { sectorName = "OldName" });
        var created = await createRes.Content.ReadFromJsonAsync<SectorDto>();

        // Rename
        var renameRes = await _client.PutAsJsonAsync($"/api/{market}/sectors/{created!.Id}", new { sectorName = "NewName" });
        renameRes.StatusCode.Should().Be(HttpStatusCode.NoContent);

        // Verify
        var getRes = await _client.GetFromJsonAsync<List<SectorDto>>($"/api/{market}/sectors");
        getRes.Should().Contain(s => s.SectorName == "NewName");
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task DeleteSector_EmptySector_Succeeds(string market)
    {
        var createRes = await _client.PostAsJsonAsync($"/api/{market}/sectors", new { sectorName = "ToDelete" });
        var created = await createRes.Content.ReadFromJsonAsync<SectorDto>();

        var deleteRes = await _client.DeleteAsync($"/api/{market}/sectors/{created!.Id}");
        deleteRes.StatusCode.Should().Be(HttpStatusCode.NoContent);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task DeleteSector_WithStocks_ReturnsBadRequest(string market)
    {
        // Create sector
        var sectorRes = await _client.PostAsJsonAsync($"/api/{market}/sectors", new { sectorName = "HasStocks" });
        var sector = await sectorRes.Content.ReadFromJsonAsync<SectorDto>();

        // Create stock in sector
        await _client.PostAsJsonAsync($"/api/{market}/stocks", new
        {
            symbol = "TEST",
            companyName = "Test Corp",
            sectorId = sector!.Id
        });

        // Try delete
        var deleteRes = await _client.DeleteAsync($"/api/{market}/sectors/{sector.Id}");
        deleteRes.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }
}
