using MarketEdge.Api.Authentication;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/india/options/strangles")]
[ServiceFilter(typeof(OptionsExceptionFilter))]
public sealed class StrangleController(StrangleService service, AuthOptions auth) : ControllerBase
{
    private string UserKey => OptionsIdentity.UserKey(User, auth);
    [HttpPost("runs")]
    public async Task<IActionResult> Create(StrangleRunRequest request, CancellationToken ct) => Ok(await service.CreateAsync(UserKey, request, ct));
    [HttpGet("runs")]
    public async Task<IActionResult> List([FromQuery] string? indexCode, CancellationToken ct, [FromQuery] int limit = 100) =>
        Ok(await service.ListAsync(UserKey, indexCode, ct, limit));
    [HttpGet("runs/{id:int}")]
    public async Task<IActionResult> Detail(int id, CancellationToken ct) =>
        await service.DetailAsync(UserKey, id, ct) is { } row ? Ok(row) : NotFound();
    [HttpPost("strategies")]
    public async Task<IActionResult> Confirm(StrangleConfirmRequest request, CancellationToken ct) => Ok(await service.ConfirmAsync(UserKey, request, ct));
    [HttpGet("strategies")]
    public async Task<IActionResult> Strategies(CancellationToken ct) => Ok(await service.StrategiesAsync(UserKey, ct));
    [HttpGet("strategies/{id:int}")]
    public async Task<IActionResult> Strategy(int id, CancellationToken ct) =>
        await service.StrategyAsync(UserKey, id, ct) is { } row ? Ok(row) : NotFound();
}
