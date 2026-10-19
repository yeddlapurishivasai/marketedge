using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/ai-batch")]
public class AiBatchController : ControllerBase
{
    private readonly IAiAnalysisBatchService _batch;

    public AiBatchController(IAiAnalysisBatchService batch) => _batch = batch;

    [HttpPost("run")]
    public async Task<IActionResult> Run(string market)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        return Ok(await _batch.TriggerAsync(market, top: 20, triggeredBy: "manual"));
    }
}
