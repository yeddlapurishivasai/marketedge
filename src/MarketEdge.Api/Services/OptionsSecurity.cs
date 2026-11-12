using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using MarketEdge.Api.Authentication;
using Microsoft.AspNetCore.DataProtection;

namespace MarketEdge.Api.Services;

public sealed class OptionsSettings
{
    public string KiteApiKey { get; set; } = "";
    public string KiteApiSecret { get; set; } = "";
    public string KiteRedirectUrl { get; set; } = "";
    public string TokenEncryptionKey { get; set; } = "";
    public bool AiEnabled { get; set; } = true;
    public string MetadataAdminRole { get; set; } = "Options.MetadataAdmin";
    public bool Configured => !string.IsNullOrWhiteSpace(KiteApiKey) &&
        !string.IsNullOrWhiteSpace(KiteApiSecret) && ValidRedirect(KiteRedirectUrl)
        && ValidKey(TokenEncryptionKey);

    public static OptionsSettings Read(IConfiguration config)
    {
        var result = new OptionsSettings();
        config.GetSection("Options").Bind(result);
        result.KiteApiKey = Environment.GetEnvironmentVariable("MARKETEDGE_KITE_API_KEY") ?? result.KiteApiKey;
        result.TokenEncryptionKey = Environment.GetEnvironmentVariable("MARKETEDGE_OPTIONS_TOKEN_KEY") ?? result.TokenEncryptionKey;
        return result;
    }

    private static bool ValidKey(string key)
    {
        try { return Convert.FromBase64String(key).Length == 32; }
        catch (FormatException) { return false; }
    }

    private static bool ValidRedirect(string value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) &&
        (uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback) &&
        uri.AbsolutePath == "/api/india/options/connection/callback" &&
        string.IsNullOrEmpty(uri.UserInfo) && string.IsNullOrEmpty(uri.Query) && string.IsNullOrEmpty(uri.Fragment);
}

public static class OptionsIdentity
{
    public static bool CanImportMetadata(ClaimsPrincipal user, AuthOptions auth, OptionsSettings settings) =>
        !auth.Enabled || user.Identity?.IsAuthenticated == true && !string.IsNullOrWhiteSpace(settings.MetadataAdminRole) &&
        user.Claims.Any(x => (x.Type == "roles" || x.Type == "role" || x.Type == ClaimTypes.Role) &&
            x.Value.Equals(settings.MetadataAdminRole, StringComparison.Ordinal));

    public static string UserKey(ClaimsPrincipal user, AuthOptions auth)
    {
        if (!auth.Enabled) return "local-development";
        if (user.Identity?.IsAuthenticated != true) throw new UnauthorizedAccessException("Application sign-in is required.");
        var oid = user.FindFirstValue("oid") ?? user.FindFirstValue("http://schemas.microsoft.com/identity/claims/objectidentifier")
            ?? user.FindFirstValue(ClaimTypes.NameIdentifier);
        var tid = user.FindFirstValue("tid") ?? user.FindFirstValue("http://schemas.microsoft.com/identity/claims/tenantid");
        if (string.IsNullOrWhiteSpace(oid) || string.IsNullOrWhiteSpace(tid))
            throw new UnauthorizedAccessException("A tenant-bound user identity is required.");
        var key = $"{tid}:{oid}";
        if (key.Length > 200) throw new UnauthorizedAccessException("User identity is too long.");
        return key;
    }
}

public sealed class OptionsTokenCipher(OptionsSettings settings)
{
    public string Encrypt(string userKey, string token)
    {
        var bytes = Encoding.UTF8.GetBytes(token);
        var result = new byte[28 + bytes.Length];
        RandomNumberGenerator.Fill(result.AsSpan(0, 12));
        using var aes = new AesGcm(Convert.FromBase64String(settings.TokenEncryptionKey), 16);
        aes.Encrypt(result.AsSpan(0, 12), bytes, result.AsSpan(28), result.AsSpan(12, 16), Encoding.UTF8.GetBytes(userKey));
        return Convert.ToBase64String(result);
    }

    public string Decrypt(string userKey, string encrypted)
    {
        var bytes = Convert.FromBase64String(encrypted);
        if (bytes.Length < 29) throw new CryptographicException("Invalid broker credential.");
        var plain = new byte[bytes.Length - 28];
        using var aes = new AesGcm(Convert.FromBase64String(settings.TokenEncryptionKey), 16);
        aes.Decrypt(bytes.AsSpan(0, 12), bytes.AsSpan(28), bytes.AsSpan(12, 16), plain, Encoding.UTF8.GetBytes(userKey));
        return Encoding.UTF8.GetString(plain);
    }

    public static DateTime ExpectedExpiry(DateTime utcNow)
    {
        var ist = new DateTimeOffset(DateTime.SpecifyKind(utcNow, DateTimeKind.Utc)).ToOffset(TimeSpan.FromMinutes(330));
        return new DateTimeOffset(ist.Date.AddDays(1).AddHours(6), TimeSpan.FromMinutes(330)).UtcDateTime;
    }
}

public sealed record OptionsLoginState(string UserKey, string BrowserSecret, DateTime ExpiresAt);

public sealed class OptionsLoginCorrelation
{
    public const string CookieName = "marketedge-options-login";
    private readonly IDataProtector _protector;
    private readonly string _directory;
    public OptionsLoginCorrelation(IDataProtectionProvider provider, IWebHostEnvironment environment)
    {
        _protector = provider.CreateProtector("MarketEdge.Options.KiteLogin.v1");
        _directory = Path.Combine(environment.ContentRootPath, ".local", "options-login");
    }

    public (string State, string Cookie) Create(string userKey)
    {
        Directory.CreateDirectory(_directory);
        foreach (var old in Directory.EnumerateFiles(_directory, "*.state"))
            if (File.GetLastWriteTimeUtc(old) < DateTime.UtcNow.AddMinutes(-15)) File.Delete(old);
        var state = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        var secret = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        File.WriteAllText(Path.Combine(_directory, state + ".state"),
            _protector.Protect(JsonSerializer.Serialize(new OptionsLoginState(userKey, secret, DateTime.UtcNow.AddMinutes(10)))));
        return (state, _protector.Protect(state + ":" + secret));
    }

    public string Consume(string state, string cookie)
    {
        if (state.Length != 64 || !state.All(Uri.IsHexDigit)) throw new CryptographicException("Invalid login state.");
        var binding = _protector.Unprotect(cookie).Split(':');
        if (binding.Length != 2 || binding[0] != state) throw new CryptographicException("Login browser mismatch.");
        var path = Path.Combine(_directory, state + ".state");
        var consumed = path + ".consumed";
        File.Move(path, consumed, false); // Atomic across API instances sharing this directory.
        try
        {
            var value = JsonSerializer.Deserialize<OptionsLoginState>(_protector.Unprotect(File.ReadAllText(consumed)))!;
            if (value.ExpiresAt <= DateTime.UtcNow || value.BrowserSecret != binding[1])
                throw new CryptographicException("Expired or invalid login.");
            return value.UserKey;
        }
        finally { File.Delete(consumed); }
    }
}
