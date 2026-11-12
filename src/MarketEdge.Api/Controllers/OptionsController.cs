using System.Security.Cryptography;
using MarketEdge.Api.Authentication;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/india/options")]
[ServiceFilter(typeof(OptionsExceptionFilter))]
public sealed class OptionsController(OptionsWorkspaceService workspace, OptionsBrokerService broker,
    OptionsLoginCorrelation correlation, OptionsSettings settings, AuthOptions auth,
    ILogger<OptionsController> logger) : ControllerBase
{
    private string UserKey => OptionsIdentity.UserKey(User, auth);

    [HttpGet("connection")]
    public async Task<IActionResult> Connection(CancellationToken ct)
    {
        var row = await broker.GetAsync(UserKey, ct);
        return Ok(new { status = row?.Status ?? "disconnected", row?.BrokerUserId, row?.LoginAt, row?.ExpiresAt, row?.LastError, configured = settings.Configured });
    }

    [HttpPost("connection/login")]
    public IActionResult Login()
    {
        if (!settings.Configured) throw new InvalidOperationException("Configure Options Kite API key, secret, registered redirect URL and 32-byte encryption key.");
        var state = correlation.Create(UserKey);
        Response.Cookies.Append(OptionsLoginCorrelation.CookieName, state.Cookie, new CookieOptions
        {
            HttpOnly = true, Secure = Request.IsHttps || new Uri(settings.KiteRedirectUrl).Scheme == Uri.UriSchemeHttps, SameSite = SameSiteMode.Lax,
            Path = "/api/india/options/connection/callback", MaxAge = TimeSpan.FromMinutes(10), IsEssential = true
        });
        return Ok(new { loginUrl = $"https://kite.zerodha.com/connect/login?v=3&api_key={Uri.EscapeDataString(settings.KiteApiKey)}&redirect_params={Uri.EscapeDataString("state=" + state.State)}" });
    }

    [AllowAnonymous]
    [HttpGet("connection/callback")]
    public async Task<IActionResult> Callback([FromQuery] string? state, [FromQuery(Name = "request_token")] string? requestToken, CancellationToken ct)
    {
        Response.Cookies.Delete(OptionsLoginCorrelation.CookieName, new CookieOptions { Path = "/api/india/options/connection/callback" });
        try
        {
            var user = correlation.Consume(state ?? "", Request.Cookies[OptionsLoginCorrelation.CookieName] ?? "");
            if (string.IsNullOrWhiteSpace(requestToken) || requestToken.Length > 1000)
                return Redirect("/india/options?connection=error");
            await broker.ConnectAsync(user, requestToken, ct);
            return Redirect("/india/options?connection=connected");
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            logger.LogWarning("Options broker callback rejected ({FailureType}); broker payload and credentials omitted.", ex.GetType().Name);
            return Redirect("/india/options?connection=error");
        }
    }

    [HttpDelete("connection")]
    public async Task<IActionResult> Disconnect(CancellationToken ct) { await broker.DisconnectAsync(UserKey, ct); return NoContent(); }

    [HttpPost("forecasts")]
    public async Task<IActionResult> Create(OptionsForecastRequest request, CancellationToken ct)
    {
        var row = await workspace.CreateAsync(UserKey, request, ct);
        return Ok(new { row.Id, row.JobRunId, row.Status });
    }

    [HttpGet("forecasts")]
    public async Task<IActionResult> List([FromQuery] string? indexCode, [FromQuery] string? mode, CancellationToken ct) =>
        Ok(await workspace.ListAsync(UserKey, indexCode, mode, ct));

    [HttpGet("forecasts/{id:int}")]
    public async Task<IActionResult> Detail(int id, CancellationToken ct)
    {
        var result = await workspace.DetailAsync(UserKey, id, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpDelete("forecasts/{id:int}")]
    public async Task<IActionResult> Delete(int id, CancellationToken ct) =>
        await workspace.DeleteAsync(UserKey, id, ct) switch
        {
            OptionsForecastDeleteResult.Deleted => NoContent(),
            OptionsForecastDeleteResult.NotFound => NotFound(),
            _ => Conflict(new ProblemDetails
            {
                Status = 409,
                Title = "Only completed, failed or cancelled forecasts with no active AI selection or linked job can be deleted."
            })
        };

    [HttpPost("metadata")]
    public async Task<IActionResult> Import(OptionsMetadataImport request, CancellationToken ct)
    {
        _ = UserKey;
        if (!OptionsIdentity.CanImportMetadata(User, auth, settings))
            return StatusCode(403, new ProblemDetails { Status = 403, Title = "Options metadata administration permission is required." });
        await workspace.ImportAsync(request, ct);
        return NoContent();
    }

    [HttpGet("metadata")]
    public async Task<IActionResult> Metadata(CancellationToken ct) { _ = UserKey; return Ok(await workspace.MetadataAsync(ct)); }
}

public sealed class OptionsExceptionFilter(ILogger<OptionsExceptionFilter> logger) : IExceptionFilter
{
    public void OnException(ExceptionContext context)
    {
        var exception = context.Exception;
        var (status, message) = exception switch
        {
            UnauthorizedAccessException => (401, exception.Message),
            ArgumentException => (400, exception.Message),
            SqlException or DbUpdateException => (503, "Options database unavailable or schema missing. Publish the options dacpac and check database connectivity."),
            InvalidOperationException => (503, exception.Message),
            HttpRequestException => (503, "The broker or queue service is temporarily unavailable."),
            CryptographicException or FormatException => (503, "Broker credential encryption configuration is invalid. Check the shared key and reconnect."),
            System.Text.Json.JsonException => (503, "Persisted options analysis contains invalid JSON."),
            _ => (500, "Options request failed.")
        };
        logger.LogError(exception, "Options API request failed with status {Status}", status);
        context.Result = new ObjectResult(new ProblemDetails { Status = status, Title = message }) { StatusCode = status };
        context.ExceptionHandled = true;
    }
}
