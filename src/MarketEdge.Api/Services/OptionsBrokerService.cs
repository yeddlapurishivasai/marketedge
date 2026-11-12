using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public sealed class OptionsBrokerService(MarketEdgeDbContext db, HttpClient http, OptionsSettings settings, OptionsTokenCipher cipher)
{
    public async Task<BrokerConnection?> GetAsync(string user, CancellationToken ct)
    {
        var row = await db.OptionsConnections.SingleOrDefaultAsync(x => x.UserKey == user, ct);
        if (row?.Status == "connected" && (row.ExpiresAt is null || row.ExpiresAt <= DateTime.UtcNow || row.EncryptedToken is null))
            await InvalidateAsync(row, "relogin-required", "Broker session expired; log in again.", ct);
        return row;
    }

    public async Task ValidateAsync(string user, CancellationToken ct)
    {
        if (!settings.Configured) throw new InvalidOperationException("Options Kite credentials, redirect URL and 32-byte encryption key must be configured.");
        var row = await GetAsync(user, ct);
        if (row?.Status != "connected" || row.EncryptedToken is null)
            throw new ArgumentException("Connect to Zerodha or log in again before requesting a forecast.");
        using var request = new HttpRequestMessage(HttpMethod.Get, "https://api.kite.trade/user/profile");
        request.Headers.Add("X-Kite-Version", "3");
        request.Headers.TryAddWithoutValidation("Authorization", $"token {settings.KiteApiKey}:{cipher.Decrypt(user, row.EncryptedToken)}");
        using var response = await http.SendAsync(request, ct);
        var authenticationRejected = (int)response.StatusCode == 401;
        if ((int)response.StatusCode == 403)
        {
            try
            {
                var error = await response.Content.ReadAsStringAsync(ct);
                if (error.Length <= 16000)
                {
                    using var json = JsonDocument.Parse(error);
                    authenticationRejected = json.RootElement.ValueKind == JsonValueKind.Object &&
                        json.RootElement.TryGetProperty("error_type", out var type) &&
                        type.ValueKind == JsonValueKind.String && type.GetString() == "TokenException";
                }
            }
            catch (JsonException) { }
        }
        if (authenticationRejected)
        {
            await InvalidateAsync(row, "relogin-required", "Broker rejected this session; log in again.", ct);
            throw new ArgumentException("Broker session was invalidated; log in again.");
        }
        if ((int)response.StatusCode == 403)
            throw new InvalidOperationException("Broker permission denied. Check Kite API permissions or entitlements; the saved session was not invalidated.");
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Broker session verification is temporarily unavailable.");
    }

    public async Task ConnectAsync(string user, string requestToken, CancellationToken ct)
    {
        if (!settings.Configured) throw new InvalidOperationException("Options broker configuration is incomplete.");
        var checksum = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(settings.KiteApiKey + requestToken + settings.KiteApiSecret))).ToLowerInvariant();
        using var request = new HttpRequestMessage(HttpMethod.Post, "https://api.kite.trade/session/token")
        {
            Content = new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["api_key"] = settings.KiteApiKey, ["request_token"] = requestToken, ["checksum"] = checksum
            })
        };
        request.Headers.Add("X-Kite-Version", "3");
        using var response = await http.SendAsync(request, ct);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException("Broker token exchange failed; start a new login.");
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
        var data = json.RootElement.GetProperty("data");
        var token = data.GetProperty("access_token").GetString();
        var brokerUser = data.GetProperty("user_id").GetString();
        if (string.IsNullOrWhiteSpace(token) || string.IsNullOrWhiteSpace(brokerUser))
            throw new InvalidOperationException("Broker returned an incomplete session.");
        var row = await db.OptionsConnections.SingleOrDefaultAsync(x => x.UserKey == user, ct);
        if (row is null) { row = new BrokerConnection { UserKey = user }; db.OptionsConnections.Add(row); }
        row.BrokerUserId = brokerUser;
        row.EncryptedToken = cipher.Encrypt(user, token);
        row.LoginAt = row.UpdatedAt = DateTime.UtcNow;
        row.ExpiresAt = OptionsTokenCipher.ExpectedExpiry(row.LoginAt.Value);
        row.Status = "connected";
        row.LastError = null;
        await db.SaveChangesAsync(ct);
    }

    public async Task DisconnectAsync(string user, CancellationToken ct)
    {
        var row = await GetAsync(user, ct);
        if (row is not null) await InvalidateAsync(row, "disconnected", null, ct);
    }

    private async Task InvalidateAsync(BrokerConnection row, string status, string? error, CancellationToken ct)
    {
        row.Status = status;
        row.LastError = error;
        row.EncryptedToken = null;
        row.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
    }
}
