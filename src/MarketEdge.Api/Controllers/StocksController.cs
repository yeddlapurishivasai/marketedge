using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/stocks")]
public class StocksController : ControllerBase
{
    private readonly IStockService _stockService;
    public StocksController(IStockService stockService) => _stockService = stockService;

    [HttpGet]
    public async Task<IActionResult> SearchStocks(string market, [FromQuery] string? q, [FromQuery] int? sectorId, [FromQuery] int page = 1, [FromQuery] int pageSize = 50)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var result = await _stockService.SearchStocksAsync(market, q, sectorId, page, pageSize);
        return Ok(result);
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> GetStock(string market, int id)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var stock = await _stockService.GetStockByIdAsync(market, id);
        return stock == null ? NotFound() : Ok(stock);
    }

    [HttpPost]
    public async Task<IActionResult> CreateStock(string market, [FromBody] CreateStockRequest request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var stock = await _stockService.CreateStockAsync(market, request);
        return CreatedAtAction(nameof(GetStock), new { market, id = stock.Id }, stock);
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> UpdateStock(string market, int id, [FromBody] UpdateStockRequest request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var result = await _stockService.UpdateStockAsync(market, id, request);
        return result ? NoContent() : NotFound();
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> DeleteStock(string market, int id)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var result = await _stockService.DeleteStockAsync(market, id);
        return result ? NoContent() : NotFound();
    }

    [HttpPost("move")]
    public async Task<IActionResult> MoveStocks(string market, [FromBody] MoveStocksRequest request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var count = await _stockService.MoveStocksAsync(market, request);
        return Ok(new { moved = count });
    }

    private static bool IsValidMarket(string market) => market is "india" or "us";
}
