using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class ScannersController : ControllerBase
{
    private readonly IScannerService _scannerService;

    public ScannersController(IScannerService scannerService) => _scannerService = scannerService;

    private static bool ValidMarket(string market) => market is "india" or "us";

    [HttpGet("api/{market}/scanners")]
    public async Task<IActionResult> GetScanners(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scannerService.GetScannersAsync(market));
    }

    [HttpPost("api/{market}/scanners/trigger")]
    public async Task<IActionResult> Trigger(string market, [FromBody] TriggerScannerRequest? request = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        try
        {
            var runId = await _scannerService.TriggerScannerAsync(market, request ?? new TriggerScannerRequest());
            return Ok(new { runId });
        }
        catch (ArgumentException ex)
        {
            return BadRequest(ex.Message);
        }
    }

    [HttpGet("api/{market}/scanners/{scannerName}/dates")]
    public async Task<IActionResult> GetDates(string market, string scannerName)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scannerService.GetScanDatesAsync(market, scannerName));
    }

    [HttpGet("api/{market}/scanners/{scannerName}/results")]
    public async Task<IActionResult> GetResults(string market, string scannerName, [FromQuery] DateTime? date = null)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scannerService.GetResultsAsync(market, scannerName, date));
    }

    [HttpGet("api/{market}/scanners/schedule")]
    public async Task<IActionResult> GetSchedule(string market)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scannerService.GetScheduleAsync(market));
    }

    [HttpPut("api/{market}/scanners/schedule")]
    public async Task<IActionResult> UpdateSchedule(string market, [FromBody] UpdateScannerScheduleRequest request)
    {
        if (!ValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _scannerService.UpdateScheduleAsync(market, request));
    }
}
