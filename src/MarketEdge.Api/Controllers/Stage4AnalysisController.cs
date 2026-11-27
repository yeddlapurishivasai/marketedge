using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/stage4")]
public class Stage4AnalysisController(IStage4AnalysisService service) : ControllerBase
{
    [HttpPost("trigger")]
    public Task<IActionResult> Trigger(string market, [FromBody] TriggerStage4Request? request = null) =>
        Execute(async () => Ok(new { runId = await service.TriggerAsync(market, request) }));

    [HttpGet("summary")]
    public Task<IActionResult> Summary(string market, [FromQuery] int? runId = null) =>
        Execute(async () => await service.GetSummaryAsync(market, runId) is { } summary
            ? Ok(summary) : NotFound("No matching completed Stage 4 snapshot found."));

    [HttpGet("runs/{runId:int}/stocks")]
    public Task<IActionResult> Stocks(string market, int runId, [FromQuery] string? classification = null,
        [FromQuery] int? sectorId = null, [FromQuery] decimal? minMarketCap = null) =>
        Execute(async () => await service.GetStocksAsync(market, runId, classification, sectorId, minMarketCap) is { } stocks
            ? Ok(stocks) : NotFound("No matching completed Stage 4 snapshot found."));

    [HttpGet("history")]
    public Task<IActionResult> History(string market, [FromQuery] int maxRuns = 10, [FromQuery] int? runId = null) =>
        Execute(async () => Ok(await service.GetHistoryAsync(market, maxRuns, runId)));

    [HttpGet("runs/{runId:int}/sector-rotation")]
    public Task<IActionResult> SectorRotation(string market, int runId) =>
        Execute(async () => await service.GetSectorRotationAsync(market, runId) is { } sectors
            ? Ok(sectors) : NotFound("No matching completed Stage 4 snapshot found."));

    [HttpGet("schedule")]
    public Task<IActionResult> Schedule(string market) =>
        Execute(async () => Ok(await service.GetScheduleAsync(market)));

    [HttpPut("schedule")]
    public Task<IActionResult> UpdateSchedule(string market, [FromBody] UpdateJobScheduleRequest request) =>
        Execute(async () => Ok(await service.UpdateScheduleAsync(market, request)));

    private async Task<IActionResult> Execute(Func<Task<IActionResult>> action)
    {
        try { return await action(); }
        catch (ArgumentException ex) { return BadRequest(new { error = ex.Message }); }
        catch (Stage4EnqueueException ex)
        {
            return StatusCode(StatusCodes.Status503ServiceUnavailable, new { error = ex.Message, runId = ex.RunId });
        }
        catch (Stage4ActiveRunException ex) { return Conflict(new { error = ex.Message }); }
        catch (Stage4SnapshotNotFoundException ex) { return NotFound(new { error = ex.Message }); }
    }
}
