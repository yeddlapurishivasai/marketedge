using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class IngestionController : ControllerBase
{
    private readonly IIngestionService _ingestionService;

    public IngestionController(IIngestionService ingestionService) => _ingestionService = ingestionService;

    [HttpPost("api/{market}/ingestion/trigger")]
    public async Task<IActionResult> Trigger(string market, [FromBody] TriggerIngestionRequest request)
    {
        if (request == null)
            return BadRequest("Request body is required.");

        int runId;
        try
        {
            runId = await _ingestionService.TriggerAsync(market, request);
        }
        catch (ArgumentException ex)
        {
            return BadRequest(ex.Message);
        }
        return Ok(new { runId });
    }
}
