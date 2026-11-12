using System.Text.Json;
using System.Text.Json.Nodes;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsShortlistTests
{
    [Fact]
    public void CompactIdsResolveLosslesslyAndCachedCanonicalSelectionsRemainValid()
    {
        const string suffix = "0123456789abcdef01234567";
        const string id = "put-credit-spread:2026-11-17:" + suffix;
        var candidate = Candidate(id, "put-credit-spread", "2026-11-17");
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "weekly",
            AnalysisJson = """{"scenarios":[{"id":"base","low":24000,"high":26000}]}"""
        }, [candidate]);
        var output = JsonSerializer.Serialize(new
        {
            scenarioId = "base", scenarioReason = "Range reflects available evidence",
            recommendations = from side in new[] { "selling", "buying" }
                              from riskTier in new[] { "low", "medium", "high" }
                              select new
                              {
                                  side, riskTier,
                                  candidateId = side == "selling" && riskTier == "low" ? "~" + suffix : null,
                                  reason = "Closing reference requires live checks"
                              }
        });
        var normalized = OptionsAiValidation.ValidateOutput(output, input);
        Assert.Contains(id, normalized);
        Assert.Equal(normalized, OptionsAiValidation.ValidateOutput(normalized, input));
    }

    private static OptionsStrategySuggestion Candidate(string id, string strategy, string expiry) => new()
    {
        CandidateKey = id, Side = "selling", RiskTier = "low", Strategy = strategy,
        PayloadJson = JsonSerializer.Serialize(new
        {
            id, side = "selling", riskTier = "low", strategy, expiry, scenarioIds = new[] { "base" }
        })
    };

    private static string[] Selected(string bias, params OptionsStrategySuggestion[] candidates)
    {
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "daily",
            AnalysisJson = JsonSerializer.Serialize(new { bias, scenarios = new[] { new { id = "base" } } })
        }, candidates);
        using var root = JsonDocument.Parse(input);
        return root.RootElement.GetProperty("c").EnumerateArray().Select(x => x[0].GetString()!).ToArray();
    }

    [Fact]
    public void ShortlistPrefersBiasFitThenDistinctExpiriesOverLexicalIds()
    {
        var selected = Selected("bullish",
            Candidate("a-call", "call-credit-spread", "2026-11-10"),
            Candidate("z-put-a", "put-credit-spread", "2026-11-10"),
            Candidate("z-put-b", "put-credit-spread", "2026-11-10"),
            Candidate("z-put-c", "put-credit-spread", "2026-11-17"));
        Assert.Equal(new[] { "z-put-a", "z-put-c" }, selected);
    }

    [Fact]
    public void ShortlistRotatesStrategiesBeforeMoreOfTheSameStructure()
    {
        var selected = Selected("unavailable",
            Candidate("a-call-a", "call-credit-spread", "2026-11-10"),
            Candidate("a-call-b", "call-credit-spread", "2026-11-17"),
            Candidate("a-call-c", "call-credit-spread", "2026-11-10"),
            Candidate("z-put-a", "put-credit-spread", "2026-11-10"));
        Assert.Equal(new[] { "a-call-a", "z-put-a" }, selected);
    }

    [Fact]
    public void QuoteOnlyCandidatePreservesUnknownNetAndModelMetricsInSelectorInput()
    {
        var candidate = Candidate("quote-only", "put-credit-spread", "2026-11-10");
        candidate.RiskTier = "high";
        var payload = JsonNode.Parse(candidate.PayloadJson)!.AsObject();
        payload["riskTier"] = "high";
        foreach (var key in new[] { "maxProfit", "maxLoss", "capitalBasis", "costEstimate",
                     "estimatedStopLoss", "probabilityOfProfit" })
            payload[key] = null;
        payload["grossMaxLoss"] = 6500;
        payload["grossMaxProfit"] = 1000;
        payload["probabilityType"] = "unavailable";
        payload["warnings"] = new JsonArray("Charges unavailable: gross before charges only; net values unavailable");
        candidate.PayloadJson = payload.ToJsonString();
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "daily",
            AnalysisJson = """{"scenarios":[{"id":"base","probability":null,"probabilityType":"unavailable"}]}"""
        }, [candidate]);
        using var root = JsonDocument.Parse(input);
        var fields = root.RootElement.GetProperty("C").GetString()!.Split(',');
        var row = root.RootElement.GetProperty("c")[0];
        foreach (var field in new[] { "maxProfit", "maxLoss", "capital", "cost", "estStopLoss", "pop" })
            Assert.Equal(JsonValueKind.Null, row[Array.IndexOf(fields, field)].ValueKind);
        Assert.Contains("unknown fees:no net/cap", OptionsAiValidation.Instructions);
        Assert.Contains("No model:no metrics", OptionsAiValidation.Instructions);
        Assert.Contains("long-call unbounded iff fees known", OptionsAiValidation.Instructions);
    }
}
