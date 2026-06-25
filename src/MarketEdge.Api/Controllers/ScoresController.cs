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

    [HttpGet("api/{market}/trades/pnl")]
    public async Task<IActionResult> GetTradePnl(string market, [FromQuery] DateTime from,
        [FromQuery] DateTime to, [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        if (to <= from) return BadRequest("'to' must be after 'from'");
        tradeType = NormalizeTradeType(tradeType);
        return Ok(await _scores.GetTradePnlAsync(market, from, to, tradeType));
    }

    [HttpGet("api/{market}/trades/day")]
    public async Task<IActionResult> GetTradesByDay(string market, [FromQuery] DateTime date,
        [FromQuery] string? tradeType = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        tradeType = NormalizeTradeType(tradeType);
        return Ok(await _scores.GetTradesByDayAsync(market, date, tradeType));
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
        return Ok(await _scores.GetScannerPerformanceAsync(market));
    }

    [HttpGet("api/{market}/scoring/weights")]
    public async Task<IActionResult> GetScoringWeights(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scores.GetScoringWeightsAsync(market));
    }

    [HttpPut("api/{market}/scoring/weights/{id:int}")]
    public async Task<IActionResult> UpdateScoringWeight(string market, int id,
        [FromBody] Models.ScoringWeightUpdateDto update)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var updated = await _scores.UpdateScoringWeightAsync(market, id, update);
        return updated == null ? NotFound() : Ok(updated);
    }
}
