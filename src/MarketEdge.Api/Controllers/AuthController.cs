using System.Security.Claims;
using MarketEdge.Api.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace MarketEdge.Api.Controllers;

[ApiController]
[Route("api/auth")]
public class AuthController : ControllerBase
{
    private readonly SpaAuthConfig _spaConfig;

    public AuthController(SpaAuthConfig spaConfig) => _spaConfig = spaConfig;

    /// <summary>
    /// Public endpoint the SPA calls at startup to discover whether auth is enabled
    /// and, if so, how to configure MSAL. Always anonymous so the app can bootstrap.
    /// </summary>
    [AllowAnonymous]
    [HttpGet("config")]
    public ActionResult<SpaAuthConfig> GetConfig() => Ok(_spaConfig);

    /// <summary>
    /// Returns information about the current caller. Protected by the global fallback
    /// policy when auth is enabled; when auth is disabled it reports an anonymous caller.
    /// </summary>
    [HttpGet("me")]
    public IActionResult Me()
    {
        if (User.Identity?.IsAuthenticated != true)
        {
            return Ok(new { authenticated = false });
        }

        var name = User.FindFirstValue("name")
            ?? User.FindFirstValue(ClaimTypes.Name)
            ?? User.FindFirstValue("preferred_username");

        var roles = User.FindAll("roles").Select(c => c.Value).ToArray();
        var scopes = (User.FindFirstValue("scp") ?? string.Empty)
            .Split(' ', StringSplitOptions.RemoveEmptyEntries);

        var isApplication = string.Equals(User.FindFirstValue("idtyp"), "app", StringComparison.OrdinalIgnoreCase)
            || (scopes.Length == 0 && roles.Length > 0);

        return Ok(new
        {
            authenticated = true,
            name,
            clientType = isApplication ? "application" : "user",
            roles,
            scopes
        });
    }
}
