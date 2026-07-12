using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.Identity.Web;

namespace MarketEdge.Api.Authentication;

/// <summary>Strongly-typed view over the <c>AzureAd</c> configuration section.</summary>
public sealed class AuthOptions
{
    public const string SectionName = "AzureAd";

    /// <summary>Master switch. When false, the API and SPA run with no authentication.</summary>
    public bool Enabled { get; set; }

    public string Instance { get; set; } = "https://login.microsoftonline.com/";
    public string? TenantId { get; set; }

    /// <summary>Client id of the API app registration (the token audience).</summary>
    public string? ClientId { get; set; }

    /// <summary>Client id of the SPA app registration (handed to the browser).</summary>
    public string? SpaClientId { get; set; }

    /// <summary>Space/comma separated delegated scopes required for user access.</summary>
    public string Scopes { get; set; } = "access_as_user";

    /// <summary>Space/comma separated app roles required for client-credentials access.</summary>
    public string AppRoles { get; set; } = "Api.Access";

    public string Authority => $"{Instance.TrimEnd('/')}/{TenantId}";

    public string[] ScopeList => Split(Scopes);
    public string[] AppRoleList => Split(AppRoles);

    private static string[] Split(string? value) =>
        (value ?? string.Empty).Split([' ', ','],
            StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}

/// <summary>Auth configuration served to the SPA at <c>/api/auth/config</c>.</summary>
public sealed record SpaAuthConfig(bool Enabled, string? ClientId, string? Authority, string[] Scopes);

public static class AuthenticationExtensions
{
    public const string ApiAccessPolicy = "ApiAccess";

    public static AuthOptions GetAuthOptions(this IConfiguration configuration)
    {
        var options = new AuthOptions();
        configuration.GetSection(AuthOptions.SectionName).Bind(options);
        return options;
    }

    public static WebApplicationBuilder AddMarketEdgeAuth(this WebApplicationBuilder builder)
    {
        var auth = builder.Configuration.GetAuthOptions();

        builder.Services.AddSingleton(auth);
        builder.Services.AddSingleton(BuildSpaConfig(auth));

        if (auth.Enabled)
        {
            builder.Services
                .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
                .AddMicrosoftIdentityWebApi(builder.Configuration.GetSection(AuthOptions.SectionName));

            builder.Services.AddSingleton<IAuthorizationHandler, ScopeOrAppRoleHandler>();
            builder.Services.AddAuthorization(options =>
            {
                var policy = new AuthorizationPolicyBuilder()
                    .RequireAuthenticatedUser()
                    .AddRequirements(new ScopeOrAppRoleRequirement(auth.ScopeList, auth.AppRoleList))
                    .Build();

                options.AddPolicy(ApiAccessPolicy, policy);
                // Protect every endpoint by default; opt out with [AllowAnonymous].
                options.FallbackPolicy = policy;
            });
        }
        else
        {
            // Register core authentication services (with no schemes) so that
            // app.UseAuthentication() is a safe no-op, and leave authorization open.
            builder.Services.AddAuthentication();
            builder.Services.AddAuthorization();
        }

        return builder;
    }

    private static SpaAuthConfig BuildSpaConfig(AuthOptions auth)
    {
        if (!auth.Enabled || string.IsNullOrWhiteSpace(auth.ClientId))
        {
            return new SpaAuthConfig(false, null, null, []);
        }

        var scopes = auth.ScopeList
            .Select(scope => $"api://{auth.ClientId}/{scope}")
            .ToArray();

        return new SpaAuthConfig(true, auth.SpaClientId, auth.Authority, scopes);
    }
}
