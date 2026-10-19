using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using MarketEdge.Api.Models;
using Microsoft.Extensions.Options;

namespace MarketEdge.Api.Services;

public record FoundryResponse(string OutputText, AiInferenceUsage Usage);

public interface IAzureAiFoundryClient
{
    Task<FoundryResponse> CreateResponseAsync(
        string instructions,
        string input,
        bool enableWebSearch,
        int maxOutputTokens,
        CancellationToken cancellationToken = default);
}

public class AzureAiFoundryClient : IAzureAiFoundryClient
{
    private readonly HttpClient _httpClient;
    private readonly AzureAiFoundryOptions _options;

    public AzureAiFoundryClient(HttpClient httpClient, IOptions<AzureAiFoundryOptions> options)
    {
        _httpClient = httpClient;
        _options = options.Value;
    }

    public async Task<FoundryResponse> CreateResponseAsync(
        string instructions,
        string input,
        bool enableWebSearch,
        int maxOutputTokens,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(_options.Endpoint)
            || string.IsNullOrWhiteSpace(_options.ApiKey)
            || string.IsNullOrWhiteSpace(_options.Model))
            throw new InvalidOperationException("Azure AI Foundry endpoint, API key, and model must be configured.");

        var body = new JsonObject
        {
            ["model"] = _options.Model,
            ["instructions"] = instructions,
            ["input"] = input,
            ["max_output_tokens"] = maxOutputTokens,
            ["reasoning"] = new JsonObject { ["effort"] = _options.ReasoningEffort }
        };
        if (enableWebSearch)
            body["tools"] = new JsonArray(new JsonObject { ["type"] = "web_search_preview" });

        using var request = new HttpRequestMessage(HttpMethod.Post, _options.Endpoint)
        {
            Content = JsonContent.Create(body)
        };
        request.Headers.Add("api-key", _options.ApiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken);
        var responseText = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode)
            throw new HttpRequestException(
                $"Azure AI Foundry returned {(int)response.StatusCode}: {SanitizeError(responseText)}");

        using var document = JsonDocument.Parse(responseText);
        var root = document.RootElement;
        var status = root.TryGetProperty("status", out var statusElement)
            ? statusElement.GetString()
            : null;
        var output = ExtractOutputText(root);
        if (!string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase)
            || string.IsNullOrWhiteSpace(output))
            throw new InvalidOperationException(
                $"Azure AI Foundry response did not complete with text output (status: {status ?? "unknown"}).");

        var usage = root.GetProperty("usage");
        var inputTokens = usage.GetProperty("input_tokens").GetInt64();
        var outputTokens = usage.GetProperty("output_tokens").GetInt64();
        var cachedTokens = usage.TryGetProperty("input_tokens_details", out var details)
            && details.TryGetProperty("cached_tokens", out var cached)
                ? cached.GetInt64()
                : 0;
        return new FoundryResponse(output, new AiInferenceUsage(inputTokens, outputTokens, cachedTokens));
    }

    private static string ExtractOutputText(JsonElement root)
    {
        if (!root.TryGetProperty("output", out var outputItems)) return string.Empty;
        foreach (var outputItem in outputItems.EnumerateArray())
        {
            if (!outputItem.TryGetProperty("content", out var contentItems)) continue;
            foreach (var contentItem in contentItems.EnumerateArray())
            {
                if (contentItem.TryGetProperty("type", out var type)
                    && type.GetString() == "output_text"
                    && contentItem.TryGetProperty("text", out var text))
                    return text.GetString() ?? string.Empty;
            }
        }
        return string.Empty;
    }

    private static string SanitizeError(string responseText) =>
        responseText.Length <= 1000 ? responseText : responseText[..1000];
}