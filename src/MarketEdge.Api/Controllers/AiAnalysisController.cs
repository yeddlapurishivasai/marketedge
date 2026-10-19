using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/ai-analysis")]
public class AiAnalysisController : ControllerBase
{
    private readonly IAiStockAnalysisService _analysis;
    private readonly IAiInvestmentAnalysisStore _store;
    private readonly IAiInvestmentAnalysisRunner _runner;

    public AiAnalysisController(
        IAiStockAnalysisService analysis,
        IAiInvestmentAnalysisStore store,
        IAiInvestmentAnalysisRunner runner)
    {
        _analysis = analysis;
        _store = store;
        _runner = runner;
    }

    [HttpGet("{symbol}")]
    public async Task<IActionResult> Get(string market, string symbol)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        var result = await _analysis.GetAsync(market, symbol);
        return result == null ? NotFound() : Ok(result);
    }

    [HttpPost("{symbol}/run")]
    public async Task<IActionResult> Run(
        string market,
        string symbol,
        [FromQuery] bool force = false,
        CancellationToken cancellationToken = default)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        var input = await _analysis.GetAsync(market, symbol);
        if (input == null) return NotFound();

        if (!force)
        {
            var existing = await _store.GetAsync(
                market, input.Stock.Symbol, AiAnalysisBatchService.GetIsoWeekNumber(DateTime.UtcNow));
            if (existing != null) return Ok(existing);
        }

        return await RunAnalysisAsync(market, input.Stock.Symbol, input, cancellationToken);
    }

    [HttpPost("run")]
    public async Task<IActionResult> RunInput(
        string market,
        [FromBody] AiStockAnalysisDto input,
        CancellationToken cancellationToken)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");
        if (!string.Equals(input.Market, market, StringComparison.OrdinalIgnoreCase))
            return BadRequest("Input market must match the route market.");

        var existing = await _store.GetAsync(
            market, input.Stock.Symbol, AiAnalysisBatchService.GetIsoWeekNumber(DateTime.UtcNow));
        if (existing != null) return Ok(existing);

        return await RunAnalysisAsync(market, input.Stock.Symbol, input, cancellationToken);
    }

    private async Task<IActionResult> RunAnalysisAsync(
        string market,
        string symbol,
        AiStockAnalysisDto input,
        CancellationToken cancellationToken)
    {
        try
        {
            return Ok(await _runner.RunAsync(market, symbol, input, cancellationToken));
        }
        catch (InvalidOperationException ex)
        {
            return Problem(ex.Message, statusCode: StatusCodes.Status503ServiceUnavailable);
        }
        catch (HttpRequestException ex)
        {
            return Problem(ex.Message, statusCode: StatusCodes.Status502BadGateway);
        }
    }

    [HttpGet("{symbol}/history")]
    public async Task<IActionResult> GetHistory(string market, string symbol)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        return Ok(await _store.GetHistoryAsync(market, symbol));
    }

    [HttpGet("history")]
    public async Task<IActionResult> GetMarketHistory(
        string market,
        [FromQuery] int maxRows = 500)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        return Ok(await _store.GetMarketHistoryAsync(market, maxRows));
    }

    [HttpGet("{symbol}/history/{weekNumber}")]
    public async Task<IActionResult> GetStored(string market, string symbol, string weekNumber)
    {
        if (market is not ("india" or "us"))
            return BadRequest("Market must be 'india' or 'us'");

        var result = await _store.GetAsync(market, symbol, weekNumber);
        return result == null ? NotFound() : Ok(result);
    }
}