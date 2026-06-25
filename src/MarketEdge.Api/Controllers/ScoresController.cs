using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class ScoresController : ControllerBase
{
    private readonly IScoresService _scores;

    public ScoresController(IScoresService scores) => _scores = scores;

    private static bool ValidMarket(string market) => market is "india" or "us";

    [HttpGet("api/{market}/scores")]
    public async Task<IActionResult> GetScores(string market, [FromQuery] string profile = "swing",
        [FromQuery] string? side = null, [FromQuery] int take = 100)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        profile = profile.ToLowerInvariant();
        if (profile != "swing" && profile != "positional") return BadRequest("profile must be 'swing' or 'positional'");
        return Ok(await _scores.GetScoresAsync(market, profile, side, take));
    }

    [HttpGet("api/{market}/scores/{ticker}")]
    public async Task<IActionResult> GetScore(string market, string ticker)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var score = await _scores.GetScoreAsync(market, ticker);
        return score == null ? NotFound() : Ok(score);
    }

    [HttpGet("api/{market}/trades")]
    public async Task<IActionResult> GetTrades(string market, [FromQuery] string? status = null,
        [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scores.GetTradesAsync(market, status, tradeType));
    }

    [HttpGet("api/{market}/trades/stats")]
    public async Task<IActionResult> GetTradeStats(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scores.GetTradeStatsAsync(market));
    }

    [HttpGet("api/{market}/scanners/performance")]
    public async Task<IActionResult> GetScannerPerformance(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scores.GetScannerPerformanceAsync(market));
    }
}
