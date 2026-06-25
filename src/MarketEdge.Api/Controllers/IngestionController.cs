using MarketEdge.Api.Models;
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

    private static bool ValidMarket(string market) => market is "india" or "us";

    [HttpGet("api/{market}/ingestion/fundamentals-schedule")]
    public async Task<IActionResult> GetFundamentalsSchedule(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _ingestionService.GetFundamentalsScheduleAsync(market));
    }

    [HttpPut("api/{market}/ingestion/fundamentals-schedule")]
    public async Task<IActionResult> UpdateFundamentalsSchedule(string market, [FromBody] UpdateJobScheduleRequest request)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _ingestionService.UpdateFundamentalsScheduleAsync(market, request));
    }
}
