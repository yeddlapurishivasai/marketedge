using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class BreakoutsController : ControllerBase
{
    private readonly IBreakoutsService _breakouts;

    public BreakoutsController(IBreakoutsService breakouts) => _breakouts = breakouts;

    private static bool ValidMarket(string market) => market is "india" or "us";

    [HttpGet("api/{market}/breakouts")]
    public async Task<IActionResult> GetBreakouts(string market, [FromQuery] string? status = null,
        [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _breakouts.GetBreakoutsAsync(market, status, tradeType));
    }

    [HttpGet("api/{market}/breakouts/stats")]
    public async Task<IActionResult> GetBreakoutStats(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _breakouts.GetBreakoutStatsAsync(market));
    }

    [HttpGet("api/{market}/breakouts/pnl")]
    public async Task<IActionResult> GetBreakoutPnl(string market, [FromQuery] DateTime from,
        [FromQuery] DateTime to, [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        if (to <= from) return BadRequest("'to' must be after 'from'");
        tradeType = NormalizeTradeType(tradeType);
        return Ok(await _breakouts.GetBreakoutPnlAsync(market, from, to, tradeType));
    }

    [HttpGet("api/{market}/breakouts/day")]
    public async Task<IActionResult> GetBreakoutsByDay(string market, [FromQuery] DateTime date,
        [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        tradeType = NormalizeTradeType(tradeType);
        return Ok(await _breakouts.GetBreakoutsByDayAsync(market, date, tradeType));
    }

    [HttpGet("api/{market}/breakouts/near-pivot")]
    public async Task<IActionResult> GetNearPivots(string market, [FromQuery] string? tradeType = null,
        [FromQuery] decimal maxDistancePct = 5m)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        tradeType = NormalizeTradeType(tradeType);
        maxDistancePct = Math.Clamp(maxDistancePct, 0m, 100m);
        return Ok(await _breakouts.GetNearPivotsAsync(market, tradeType, maxDistancePct));
    }

    // Empty/"all" means no trade-type filter.
    private static string? NormalizeTradeType(string? tradeType)
    {
        if (string.IsNullOrWhiteSpace(tradeType)) return null;
        var t = tradeType.ToLowerInvariant();
        return t is "swing" or "positional" ? t : null;
    }

    [HttpGet("api/{market}/scanners/performance")]
    public async Task<IActionResult> GetScannerPerformance(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _breakouts.GetScannerPerformanceAsync(market));
    }

    [HttpGet("api/{market}/scoring/weights")]
    public async Task<IActionResult> GetScoringWeights(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _breakouts.GetScoringWeightsAsync(market));
    }

    [HttpPut("api/{market}/scoring/weights/{id:int}")]
    public async Task<IActionResult> UpdateScoringWeight(string market, int id,
        [FromBody] Models.ScoringWeightUpdateDto update)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var updated = await _breakouts.UpdateScoringWeightAsync(market, id, update);
        return updated == null ? NotFound() : Ok(updated);
    }
}
