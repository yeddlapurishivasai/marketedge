using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
public class JobsController : ControllerBase
{
    private readonly IJobService _jobService;

    public JobsController(IJobService jobService) => _jobService = jobService;

    // ── Job Runs (generic, all job types) ──

    [HttpGet("api/jobs")]
    public async Task<IActionResult> GetRuns(
        [FromQuery] string? market = null,
        [FromQuery] string? jobType = null,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 20)
    {
        var runs = await _jobService.GetRunsAsync(market, jobType, page, pageSize);
        return Ok(runs);
    }

    [HttpGet("api/jobs/{id}")]
    public async Task<IActionResult> GetRun(int id)
    {
        var run = await _jobService.GetRunByIdAsync(id);
        return run == null ? NotFound() : Ok(run);
    }

    [HttpPost("api/jobs/{id}/cancel")]
    public async Task<IActionResult> CancelRun(int id)
    {
        var cancelled = await _jobService.CancelRunAsync(id);
        if (!cancelled) return NotFound("Run not found or already finished");
        return Ok(new { cancelled = true });
    }

    // ── Stage 2 Analysis ──

    [HttpPost("api/{market}/analysis/trigger")]
    public async Task<IActionResult> TriggerAnalysis(
        string market,
        [FromBody] TriggerAnalysisRequest? request = null)
    {
        if (market != "india" && market != "us")
            return BadRequest("Market must be 'india' or 'us'");

        var runId = await _jobService.TriggerStageAnalysisAsync(market, request);
        return Ok(new { runId });
    }

    [HttpGet("api/{market}/analysis/summary")]
    public async Task<IActionResult> GetSummary(string market)
    {
        if (market != "india" && market != "us")
            return BadRequest("Market must be 'india' or 'us'");

        var summary = await _jobService.GetLatestStage2SummaryAsync(market);
        return summary == null ? NotFound("No completed analysis runs found") : Ok(summary);
    }

    [HttpGet("api/{market}/analysis/runs/{runId}/stocks")]
    public async Task<IActionResult> GetStage2Stocks(
        string market,
        int runId,
        [FromQuery] string? classification = null,
        [FromQuery] int? sectorId = null)
    {
        var stocks = await _jobService.GetStage2StocksAsync(runId, classification, sectorId);
        return Ok(stocks);
    }

    [HttpGet("api/{market}/analysis/runs/{runId}/sector-rotation")]
    public async Task<IActionResult> GetSectorRotation(string market, int runId)
    {
        var rotation = await _jobService.GetSectorRotationAsync(runId);
        return Ok(rotation);
    }

    [HttpGet("api/{market}/analysis/history")]
    public async Task<IActionResult> GetHistory(string market, [FromQuery] int maxRuns = 10)
    {
        if (market != "india" && market != "us")
            return BadRequest("Market must be 'india' or 'us'");

        var history = await _jobService.GetStage2HistoryAsync(market, maxRuns);
        return Ok(history);
    }

    [HttpGet("api/{market}/analysis/rotation-history")]
    public async Task<IActionResult> GetRotationHistory(string market, [FromQuery] int maxRuns = 12)
    {
        if (market != "india" && market != "us")
            return BadRequest("Market must be 'india' or 'us'");

        var history = await _jobService.GetSectorRotationHistoryAsync(market, maxRuns);
        return Ok(history);
    }
}
