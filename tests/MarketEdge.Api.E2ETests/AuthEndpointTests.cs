using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using FluentAssertions;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;

namespace MarketEdge.Api.E2ETests;

public class AuthEndpointTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly HttpClient _client;

    public AuthEndpointTests(TestWebApplicationFactory factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task AuthConfig_WhenAuthDisabled_ReturnsEnabledFalse()
    {
        var response = await _client.GetAsync("/api/auth/config");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var cfg = await response.Content.ReadFromJsonAsync<JsonElement>();
        cfg.GetProperty("enabled").GetBoolean().Should().BeFalse();
    }

    [Fact]
    public async Task Me_WhenAuthDisabled_ReturnsAnonymous()
    {
        var response = await _client.GetAsync("/api/auth/me");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var me = await response.Content.ReadFromJsonAsync<JsonElement>();
        me.GetProperty("authenticated").GetBoolean().Should().BeFalse();
    }

    [Fact]
    public async Task ProtectedEndpoint_WhenAuthDisabled_IsAccessibleAnonymously()
    {
        var response = await _client.GetAsync("/api/india/sectors");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }
}

public class SpaFallbackRouteTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly TestWebApplicationFactory _factory;

    public SpaFallbackRouteTests(TestWebApplicationFactory factory)
    {
        _factory = factory;
    }

    // Regression guard for the production SPA lockout bug. When auth is enabled the global
    // FallbackPolicy (RequireAuthenticatedUser) protects every endpoint that lacks explicit
    // auth metadata — including MapFallbackToFile("index.html"). If that route isn't marked
    // AllowAnonymous, "/" and every client-side route return 401, the SPA shell never loads,
    // MSAL never starts, and no one can sign in. We assert the metadata directly so the guard
    // holds regardless of whether auth happens to be enabled in a given environment.
    [Fact]
    public async Task SpaFallbackRoute_IsMarkedAllowAnonymous()
    {
        // Warm up the pipeline so the endpoint data sources are fully populated.
        using var client = _factory.CreateClient();
        await client.GetAsync("/api/auth/config");

        var endpoints = _factory.Services
            .GetServices<EndpointDataSource>()
            .SelectMany(source => source.Endpoints);

        var fallback = endpoints.FirstOrDefault(endpoint =>
            endpoint.DisplayName is not null &&
            endpoint.DisplayName.Contains("Fallback", StringComparison.OrdinalIgnoreCase));

        fallback.Should().NotBeNull("the SPA MapFallbackToFile route must be registered");
        fallback!.Metadata.GetMetadata<IAllowAnonymous>().Should().NotBeNull(
            "the SPA shell must load anonymously or the auth FallbackPolicy locks users out of login");
    }
}
