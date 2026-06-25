using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/lookup")]
public class LookupController : ControllerBase
{
    private readonly ILookupService _lookup;
    private readonly IIngestionService _ingestion;

    public LookupController(ILookupService lookup, IIngestionService ingestion)
    {
        _lookup = lookup;
        _ingestion = ingestion;
    }

    [HttpGet("search")]
    public async Task<IActionResult> Search(string market, [FromQuery] string? q)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var results = await _lookup.SearchAsync(market, q ?? string.Empty);
        return Ok(results);
    }

    [HttpGet("{symbol}")]
    public async Task<IActionResult> GetDetail(string market, string symbol)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var detail = await _lookup.GetDetailAsync(market, symbol);
        return detail == null ? NotFound() : Ok(detail);
    }

    [HttpGet("{symbol}/bars")]
    public async Task<IActionResult> GetBars(string market, string symbol, [FromQuery] string timeframe = "daily")
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var bars = await _lookup.GetBarsAsync(market, symbol, timeframe);
        return Ok(bars);
    }

    [HttpPost("{symbol}/refresh-stock")]
    public async Task<IActionResult> RefreshStock(string market, string symbol)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var detail = await _lookup.GetDetailAsync(market, symbol);
        if (detail == null) return NotFound();
        try
        {
            var runId = await _ingestion.RefreshStockAsync(market, symbol);
            return Ok(new { runId });
        }
        catch (ArgumentException ex)
        {
            return BadRequest(ex.Message);
        }
    }

    private static bool IsValidMarket(string market) => market is "india" or "us";
}
