using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/{market}/sectors")]
public class SectorsController : ControllerBase
{
    private readonly ISectorService _sectorService;
    public SectorsController(ISectorService sectorService) => _sectorService = sectorService;

    [HttpGet]
    public async Task<IActionResult> GetSectors(string market, [FromQuery] bool testSampleOnly = false)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        return Ok(await _sectorService.GetSectorsAsync(market, testSampleOnly));
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> GetSector(string market, int id)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var sector = await _sectorService.GetSectorByIdAsync(market, id);
        return sector == null ? NotFound() : Ok(sector);
    }

    [HttpPost]
    public async Task<IActionResult> CreateSector(string market, [FromBody] CreateSectorRequest request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var sector = await _sectorService.CreateSectorAsync(market, request);
        return CreatedAtAction(nameof(GetSector), new { market, id = sector.Id }, sector);
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> RenameSector(string market, int id, [FromBody] CreateSectorRequest request)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var result = await _sectorService.RenameSectorAsync(market, id, request.SectorName);
        return result ? NoContent() : NotFound();
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> DeleteSector(string market, int id)
    {
        if (!IsValidMarket(market)) return BadRequest("Market must be 'india' or 'us'");
        var result = await _sectorService.DeleteSectorAsync(market, id);
        if (!result) return BadRequest("Sector has stocks or does not exist. Move stocks before deleting.");
        return NoContent();
    }

    private static bool IsValidMarket(string market) => market is "india" or "us";
}
