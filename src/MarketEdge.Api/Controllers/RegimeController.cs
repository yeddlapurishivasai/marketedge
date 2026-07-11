using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class RegimeController : ControllerBase
{
    private readonly IMarketRegimeService _regime;

    public RegimeController(IMarketRegimeService regime) => _regime = regime;

    private static bool ValidMarket(string market) => market is "india" or "us";

    [HttpGet("api/{market}/regime")]
    public async Task<IActionResult> GetRegime(string market, CancellationToken ct)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _regime.GetRegimeAsync(market, ct));
    }

    [HttpPost("api/{market}/regime/refresh")]
    public async Task<IActionResult> Refresh(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var runId = await _regime.TriggerRefreshAsync(market);
        return Ok(new { runId });
    }

    [HttpGet("api/{market}/regime/schedule")]
    public async Task<IActionResult> GetSchedule(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _regime.GetScheduleAsync(market));
    }

    [HttpPut("api/{market}/regime/schedule")]
    public async Task<IActionResult> UpdateSchedule(string market, [FromBody] UpdateRegimeScheduleRequest request)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _regime.UpdateScheduleAsync(market, request));
    }
}
