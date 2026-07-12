using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;

namespace MarketEdge.Api.Authentication;

/// <summary>
/// Authorization requirement that is satisfied when the caller's token contains
/// EITHER one of the required delegated scopes (user sign-in via the SPA)
/// OR one of the required application roles (client-credentials / daemon access).
/// </summary>
public sealed class ScopeOrAppRoleRequirement : IAuthorizationRequirement
{
    public ScopeOrAppRoleRequirement(string[] scopes, string[] appRoles)
    {
        Scopes = scopes ?? [];
        AppRoles = appRoles ?? [];
    }

    /// <summary>Accepted delegated scopes (the <c>scp</c> claim).</summary>
    public string[] Scopes { get; }

    /// <summary>Accepted application roles (the <c>roles</c> claim).</summary>
    public string[] AppRoles { get; }
}

public sealed class ScopeOrAppRoleHandler : AuthorizationHandler<ScopeOrAppRoleRequirement>
{
    private static readonly string[] ScopeClaimTypes =
        ["scp", "http://schemas.microsoft.com/identity/claims/scope"];

    private static readonly string[] RoleClaimTypes =
        ["roles", "role", ClaimTypes.Role];

    protected override Task HandleRequirementAsync(
        AuthorizationHandlerContext context,
        ScopeOrAppRoleRequirement requirement)
    {
        // No scope/role configured => an authenticated caller is sufficient.
        if (requirement.Scopes.Length == 0 && requirement.AppRoles.Length == 0)
        {
            context.Succeed(requirement);
            return Task.CompletedTask;
        }

        if (requirement.Scopes.Length > 0)
        {
            var tokenScopes = ScopeClaimTypes
                .SelectMany(t => context.User.FindAll(t))
                .SelectMany(c => c.Value.Split(' ', StringSplitOptions.RemoveEmptyEntries))
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            if (requirement.Scopes.Any(tokenScopes.Contains))
            {
                context.Succeed(requirement);
                return Task.CompletedTask;
            }
        }

        if (requirement.AppRoles.Length > 0)
        {
            var tokenRoles = RoleClaimTypes
                .SelectMany(t => context.User.FindAll(t))
                .Select(c => c.Value)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            if (requirement.AppRoles.Any(tokenRoles.Contains))
            {
                context.Succeed(requirement);
            }
        }

        return Task.CompletedTask;
    }
}
