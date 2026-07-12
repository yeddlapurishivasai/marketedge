using System.Security.Claims;
using FluentAssertions;
using MarketEdge.Api.Authentication;
using Microsoft.AspNetCore.Authorization;

namespace MarketEdge.Api.UnitTests.Authentication;

public class ScopeOrAppRoleHandlerTests
{
    private static readonly string[] Scopes = ["access_as_user"];
    private static readonly string[] Roles = ["Api.Access"];

    private static async Task<bool> Evaluate(ScopeOrAppRoleRequirement requirement, params Claim[] claims)
    {
        var user = new ClaimsPrincipal(new ClaimsIdentity(claims, "test"));
        var context = new AuthorizationHandlerContext([requirement], user, resource: null);
        await new ScopeOrAppRoleHandler().HandleAsync(context);
        return context.HasSucceeded;
    }

    [Fact]
    public async Task Succeeds_WhenDelegatedScopePresent()
    {
        var requirement = new ScopeOrAppRoleRequirement(Scopes, Roles);

        var result = await Evaluate(requirement, new Claim("scp", "openid access_as_user"));

        result.Should().BeTrue();
    }

    [Fact]
    public async Task Succeeds_WhenAppRolePresent()
    {
        var requirement = new ScopeOrAppRoleRequirement(Scopes, Roles);

        var result = await Evaluate(requirement, new Claim("roles", "Api.Access"));

        result.Should().BeTrue();
    }

    [Fact]
    public async Task Fails_WhenNeitherScopeNorRolePresent()
    {
        var requirement = new ScopeOrAppRoleRequirement(Scopes, Roles);

        var result = await Evaluate(requirement, new Claim("scp", "some.other.scope"));

        result.Should().BeFalse();
    }

    [Fact]
    public async Task Succeeds_WhenNoScopesOrRolesConfigured()
    {
        var requirement = new ScopeOrAppRoleRequirement([], []);

        var result = await Evaluate(requirement, new Claim("sub", "abc"));

        result.Should().BeTrue();
    }
}
