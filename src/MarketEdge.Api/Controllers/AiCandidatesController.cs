using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/ai-candidates")]
public class AiCandidatesController : ControllerBase
{
    private readonly IAiAnalysisCandidateService _candidates;

    public AiCandidatesController(IAiAnalysisCandidateService candidates) =>
        _candidates = candidates;

    [HttpGet]
    public async Task<IActionResult> Get(
        string market,
        [FromQuery] int top = 20,
        [FromQuery] bool excludeAnalyzed = true,
        [FromQuery] decimal? minMarketCap = null)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        try
        {
            var result = await _candidates.GetTopCandidatesAsync(
                market, top, excludeAnalyzed, minMarketCap);
            return result == null
                ? NotFound("No completed Stage 2 analysis is available.")
                : Ok(result);
        }
        catch (InvalidOperationException ex)
        {
            return Conflict(ex.Message);
        }
        catch (ArgumentOutOfRangeException ex)
        {
            return BadRequest(ex.Message);
        }
    }
}