using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/fundamentals")]
public class FundamentalsController : ControllerBase
{
    private readonly IFundamentalsService _fundamentals;
    public FundamentalsController(IFundamentalsService fundamentals) => _fundamentals = fundamentals;

    [HttpGet]
    public async Task<IActionResult> List(string market, [FromQuery] string? scanner)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var rows = await _fundamentals.ListAsync(market, scanner);
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
