using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
using MarketEdge.Api.Models;

namespace MarketEdge.Api.E2ETests;

public class StocksEndpointTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly HttpClient _client;

    public StocksEndpointTests(TestWebApplicationFactory factory)
    {
        _client = factory.CreateClient();
    }

    private async Task<SectorDto> CreateSectorAsync(string market, string name)
    {
        var res = await _client.PostAsJsonAsync($"/api/{market}/sectors", new { sectorName = name });
        res.IsSuccessStatusCode.Should().BeTrue();
        return (await res.Content.ReadFromJsonAsync<SectorDto>())!;
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task GetStocks_ReturnsPagedResults(string market)
    {
        var sector = await CreateSectorAsync(market, $"Paged_{market}");

        for (int i = 0; i < 3; i++)
            await _client.PostAsJsonAsync($"/api/{market}/stocks", new
            {
                symbol = $"PG{market}{i}",
                companyName = $"Paged Co {i}",
                sectorId = sector.Id
            });

        var response = await _client.GetAsync($"/api/{market}/stocks?page=1&pageSize=2");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var result = await response.Content.ReadFromJsonAsync<PagedResult<StockDto>>();
        result.Should().NotBeNull();
        result!.Items.Should().HaveCountLessOrEqualTo(2);
        result.TotalCount.Should().BeGreaterOrEqualTo(3);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task CreateStock_ReturnsCreatedStock(string market)
    {
        var sector = await CreateSectorAsync(market, $"Create_{market}");

        var response = await _client.PostAsJsonAsync($"/api/{market}/stocks", new
        {
            symbol = $"NEW{market}",
            companyName = "New Corp",
            sectorId = sector.Id,
            broadSector = "Technology"
        });

        response.IsSuccessStatusCode.Should().BeTrue();
        var stock = await response.Content.ReadFromJsonAsync<StockDto>();
        stock.Should().NotBeNull();
        stock!.Symbol.Should().Be($"NEW{market}");
        stock.SectorId.Should().Be(sector.Id);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task SearchStocks_FiltersByQuery(string market)
    {
        var sector = await CreateSectorAsync(market, $"Search_{market}");
        await _client.PostAsJsonAsync($"/api/{market}/stocks", new { symbol = $"FIND{market}", companyName = "Findable Corp", sectorId = sector.Id });
        await _client.PostAsJsonAsync($"/api/{market}/stocks", new { symbol = $"HIDE{market}", companyName = "Hidden Corp", sectorId = sector.Id });

        var response = await _client.GetAsync($"/api/{market}/stocks?q=Findable");
        var result = await response.Content.ReadFromJsonAsync<PagedResult<StockDto>>();

        result!.Items.Should().Contain(s => s.Symbol == $"FIND{market}");
        result.Items.Should().NotContain(s => s.Symbol == $"HIDE{market}");
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task MoveStocks_MovesToTargetSector(string market)
    {
        var source = await CreateSectorAsync(market, $"Source_{market}");
        var target = await CreateSectorAsync(market, $"Target_{market}");

        var createRes = await _client.PostAsJsonAsync($"/api/{market}/stocks", new
        {
            symbol = $"MOV{market}",
            companyName = "Movable Corp",
            sectorId = source.Id
        });
        var stock = await createRes.Content.ReadFromJsonAsync<StockDto>();

        var moveRes = await _client.PostAsJsonAsync($"/api/{market}/stocks/move", new
        {
            stockIds = new[] { stock!.Id },
            targetSectorId = target.Id
        });
        moveRes.StatusCode.Should().Be(HttpStatusCode.OK);

        // Verify stock is now in target sector
        var getRes = await _client.GetAsync($"/api/{market}/stocks?sectorId={target.Id}");
        var result = await getRes.Content.ReadFromJsonAsync<PagedResult<StockDto>>();
        result!.Items.Should().Contain(s => s.Id == stock.Id);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task DeleteStock_ExistingStock_ReturnsNoContent(string market)
    {
        var sector = await CreateSectorAsync(market, $"Del_{market}");
        var createRes = await _client.PostAsJsonAsync($"/api/{market}/stocks", new
        {
            symbol = $"DEL{market}",
            companyName = "Delete Me",
            sectorId = sector.Id
        });
        var stock = await createRes.Content.ReadFromJsonAsync<StockDto>();

        var deleteRes = await _client.DeleteAsync($"/api/{market}/stocks/{stock!.Id}");
        deleteRes.StatusCode.Should().Be(HttpStatusCode.NoContent);
    }

    [Theory]
    [InlineData("india")]
    [InlineData("us")]
    public async Task UpdateStock_UpdatesFields(string market)
    {
        var sector = await CreateSectorAsync(market, $"Upd_{market}");
        var createRes = await _client.PostAsJsonAsync($"/api/{market}/stocks", new
        {
            symbol = $"UPD{market}",
            companyName = "Old Name",
            sectorId = sector.Id
        });
        var stock = await createRes.Content.ReadFromJsonAsync<StockDto>();

        var updateRes = await _client.PutAsJsonAsync($"/api/{market}/stocks/{stock!.Id}", new { companyName = "Updated Name" });
        updateRes.StatusCode.Should().Be(HttpStatusCode.NoContent);
    }
}
