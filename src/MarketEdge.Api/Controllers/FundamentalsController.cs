using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/fundamentals")]
public class FundamentalsController : ControllerBase
{
    private readonly IFundamentalsService _fundamentals;
    private readonly IIngestionService _ingestion;
    public FundamentalsController(IFundamentalsService fundamentals, IIngestionService ingestion)
    {
        _fundamentals = fundamentals;
        _ingestion = ingestion;
    }

    /// <summary>
    /// Manually enqueues a fundamentals refresh (the screener's data source). Choose the
    /// scope with <c>universe</c> ("stage2" — fast, default; or "all" — every ticker).
    /// Pass <c>force = true</c> to bypass the earnings-window filter. Idempotent: returns
    /// the in-flight run if one is already active for the market.
    /// </summary>
    [HttpPost("trigger")]
    public async Task<IActionResult> Trigger(string market, [FromBody] TriggerFundamentalsRequest? request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        try
        {
            var runId = await _ingestion.TriggerFundamentalsAsync(
                market,
                triggeredBy: "manual",
                force: request?.Force ?? false,
                universe: request?.Universe ?? "stage2",
                missingOnly: request?.MissingOnly ?? false);
            return Ok(new { runId });
        }
        catch (ArgumentException ex)
        {
            return BadRequest(ex.Message);
        }
    }

    [HttpGet]
    public async Task<IActionResult> List(string market, [FromQuery] string? scanner)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var rows = await _fundamentals.ListAsync(market, scanner);
        return Ok(rows);
    }

    [HttpGet("ideas")]
    public async Task<IActionResult> Ideas(string market, [FromQuery] string? side)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var rows = await _fundamentals.ListIdeasAsync(market, side);
        return Ok(rows);
    }

    [HttpGet("{symbol}")]
    public async Task<IActionResult> Get(string market, string symbol)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var detail = await _fundamentals.GetAsync(market, symbol);
        return detail == null ? NotFound() : Ok(detail);
    }

    [HttpPut("{symbol}/note")]
    public async Task<IActionResult> SaveNote(string market, string symbol, [FromBody] SaveNoteRequest body)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var ok = await _fundamentals.SaveNoteAsync(market, symbol, body?.NoteText);
        return ok ? NoContent() : NotFound();
    }

    private static bool IsValidMarket(string market) => market is "india" or "us";
}
