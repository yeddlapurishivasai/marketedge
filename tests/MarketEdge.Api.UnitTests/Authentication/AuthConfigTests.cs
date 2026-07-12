using System.Collections.Generic;
using FluentAssertions;
using MarketEdge.Api.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace MarketEdge.Api.UnitTests.Authentication;

public class AuthConfigTests
{
    private static SpaAuthConfig BuildSpaConfig(Dictionary<string, string?> settings)
    {
        var builder = WebApplication.CreateBuilder();
        builder.Configuration.AddInMemoryCollection(settings);
        builder.AddMarketEdgeAuth();
        using var app = builder.Build();
        return app.Services.GetRequiredService<SpaAuthConfig>();
    }

    [Fact]
    public void SpaConfig_WhenEnabled_MapsAuthorityAndFullyQualifiedScopes()
    {
        var spa = BuildSpaConfig(new Dictionary<string, string?>
        {
            ["AzureAd:Enabled"] = "true",
            ["AzureAd:TenantId"] = "tenant-abc",
            ["AzureAd:ClientId"] = "api-client-id",
            ["AzureAd:SpaClientId"] = "spa-client-id",
            ["AzureAd:Scopes"] = "access_as_user",
            ["AzureAd:AppRoles"] = "Api.Access",
        });

        spa.Enabled.Should().BeTrue();
        spa.ClientId.Should().Be("spa-client-id");
        spa.Authority.Should().Be("https://login.microsoftonline.com/tenant-abc");
        spa.Scopes.Should().ContainSingle().Which.Should().Be("api://api-client-id/access_as_user");
    }

    [Fact]
    public void SpaConfig_WhenDisabled_IsEmpty()
    {
        var spa = BuildSpaConfig(new Dictionary<string, string?>
        {
            ["AzureAd:Enabled"] = "false",
            ["AzureAd:ClientId"] = "api-client-id",
            ["AzureAd:SpaClientId"] = "spa-client-id",
        });

        spa.Enabled.Should().BeFalse();
        spa.ClientId.Should().BeNull();
        spa.Authority.Should().BeNull();
        spa.Scopes.Should().BeEmpty();
    }
}
