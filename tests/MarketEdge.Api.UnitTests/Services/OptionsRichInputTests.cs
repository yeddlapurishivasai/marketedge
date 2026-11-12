using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsRichInputTests
{
    [Fact]
    public void IndividualMetricsRemainSeparateAndContextIsNotAssignedADirectionalVote()
    {
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "weekly",
            AnalysisJson = """
                {"scenarios":[{"id":"base","low":24000,"high":26000}],
                 "dimensions":[{"key":"technicals","score":60,"coverage":1,"indicators":[
                    {"key":"rsi14","value":62,"signalContribution":0.1},
                    {"key":"macd","value":-4,"signalContribution":-0.05},
                    {"key":"adx14","value":34,"signalContribution":null}]}]}
                """
        }, []);
        using var root = JsonDocument.Parse(input);
        var rows = root.RootElement.GetProperty("i");
        Assert.Equal(3, rows.GetArrayLength());
        Assert.Equal("rsi14", rows[0][0].GetString());
        Assert.Equal(.1, rows[0][2].GetDouble());
        Assert.Equal("macd", rows[1][0].GetString());
        Assert.Equal(-.05, rows[1][2].GetDouble());
        Assert.Equal(JsonValueKind.Null, rows[2][2].ValueKind);
        Assert.True(OptionsAiValidation.CountInputTokens(input) <= 2372);
    }

    [Fact]
    public void NoTradeStillSelectsAnAvailableRangeWithoutInventingAnEntry()
    {
        static string Output(string? scenarioId) => JsonSerializer.Serialize(new
        {
            scenarioId,
            scenarioReason = "Range reflects the captured evidence",
            recommendations = from side in new[] { "selling", "buying" }
                              from riskTier in new[] { "low", "medium", "high" }
                              select new { side, riskTier, candidateId = (string?)null, reason = "No supported entry" }
        });
        const string input = """{"scenarios":[{"id":"base","low":24000,"high":26000}],"candidates":[]}""";
        Assert.Equal(Output("base"), OptionsAiValidation.ValidateOutput(Output("base"), input));
        Assert.Throws<InvalidOperationException>(() => OptionsAiValidation.ValidateOutput(Output(null), input));
        const string unavailable = """{"scenarios":[{"id":"base","low":null,"high":null}],"candidates":[]}""";
        Assert.Equal(Output(null), OptionsAiValidation.ValidateOutput(Output(null), unavailable));
        Assert.Throws<InvalidOperationException>(() => OptionsAiValidation.ValidateOutput(Output("base"), unavailable));
    }

    [Fact]
    public void EngineRepresentativeFitsWithEveryAvailableBucketAndThreeScenarios()
    {
        var data = new[]
        {
            ("call-credit-spread:2026-11-09:9cda2ef5dd97fed9bbecd680", "selling", "medium", "call-credit-spread", "2026-11-09", (string?)null, (double?)576.9694051080081, 18173.030594891992, 4d, 204.43968340835067, 0.9150849150849151, 1d),
            ("long-call:2026-11-10:3c3edd27388db025d6e4c645", "buying", "medium", "long-call", "2026-11-10", "ATM", (double?)null, 15101.41765591039, 2d, 9263.920935211136, 0.4305694305694306, 0d),
            ("call-debit-spread:2026-11-10:b4d07482b8be9fe27c6f9777", "buying", "low", "call-debit-spread", "2026-11-10", (string?)null, (double?)11006.707167840928, 7743.292832159071, 4d, 4246.861446598521, 0.44955044955044954, 0d),
            ("long-call:2026-11-17:5e6821aa0fef5dc96ee6ce32", "buying", "high", "long-call", "2026-11-17", "ATM", (double?)null, 40586.76629569858, 2d, 11614.018797674034, 0.45454545454545453, 0d)
        };
        var analysis = JsonSerializer.Serialize(new
        {
            bias = "neutral",
            spot = 25044.65,
            executionStatus = "planning-only",
            levels = new { support = 24900, resistance = 25200 },
            vix = new { value = 14.8 },
            fundamentalRange = new { low = 21868.264, @base = 26156.513, high = 32569.975, coverage = .876238,
                aiCoverage = .023312, analystCoverage = .762781, mathematicalCoverage = .090145 },
            marketContext = new
            {
                evidence = new
                {
                    crude = new { name = "MCX CRUDEOIL futures", price = 6412.123456, currency = "INR", changePct = -0.812345, asOf = "2026-11-09T10:00:00Z" },
                    flows = new { sessionDate = "2026-11-09", fii = new { net = 7212.22 }, dii = new { net = -7172.53 } }
                },
                news = Enumerable.Range(1, 3).Select(_ => new
                {
                    publishedAt = "2026-11-09T09:30:00Z", source = "Reserve Bank of India",
                    title = "Dated central bank release supplied as untrusted context, not a scheduled event"
                })
            },
            dimensions = new[]
            {
                new { key = "technicals", score = (double?)50, coverage = 1d },
                new { key = "volatility", score = (double?)2.1328605219407737, coverage = 1d },
                new { key = "fundamentals", score = (double?)60, coverage = 0.7d },
                new { key = "breadth", score = (double?)60, coverage = 1d },
                new { key = "derivatives", score = (double?)50, coverage = 1d },
                new { key = "macro", score = (double?)null, coverage = 0d },
                new { key = "liquidity", score = (double?)83.33333333333333, coverage = 0.8333333333333334 }
            },
            scenarios = new[]
            {
                new { id = "narrow", low = 24996.72773610365, high = 25004.413121790298, probability = 0.5, probabilityType = "model-implied" },
                new { id = "base", low = 24993.269963417555, high = 25007.87243611175, probability = 0.7999999999999998, probabilityType = "model-implied" },
                new { id = "wide", low = 24989.406349673805, high = 25011.738904898324, probability = 0.9499999999999997, probabilityType = "model-implied" }
            }
        });
        var candidates = new List<OptionsStrategySuggestion>();
        foreach (var row in data)
        for (var alternative = 0; alternative < 5; alternative++)
        {
            var id = row.Item1 + (alternative == 0 ? "" : $":{alternative}");
            var warnings = new List<string>
            {
                "Hypothetical one-lot trade; no margin estimate or fill guarantee",
                "Stop estimate assumes immediate level hit and sticky-strike IV; gaps/time/IV and spreads can worsen loss",
                "Target is a fixed configured premium rule, not an optimal exit",
                "Counter-bias alternative: weaker technical fit, not a recommendation"
            };
            if (row.Item2 == "selling") warnings.Add("Same-day expiry: high gamma, liquidity and settlement risk");
            if (row.Item4 == "long-call") warnings.Add("Entire premium and costs can be lost; OTM is not inherently safer");
            var probabilities = new[] { "narrow", "base", "wide" }.ToDictionary(key => key, key => new
            {
                probability = row.Item12, probabilityType = "model-implied", gridPoints = 1001,
                reason = "Conditional P(net mark-to-model profit > 0 | terminal spot in stated scenario interval); same risk-neutral midpoint grid/mean leg IV as unconditional probability; not calibrated or first-hit probability"
            });
            candidates.Add(new OptionsStrategySuggestion
            {
                CandidateKey = id, Side = row.Item2, RiskTier = row.Item3,
                PayloadJson = JsonSerializer.Serialize(new
                {
                    id, side = row.Item2, riskTier = row.Item3, strategy = row.Item4, expiry = row.Item5, moneyness = row.Item6,
                    maxProfit = row.Item7, maxLoss = row.Item8, capitalBasis = row.Item8, costEstimate = row.Item9,
                    estimatedStopLoss = row.Item10, probabilityOfProfit = row.Item11, probabilityType = "model-implied",
                    scenarioProfitProbabilities = probabilities, scenarioIds = new[] { "narrow", "base", "wide" },
                    technicalStop = new
                    {
                        lower = row.Item2 == "buying" ? (double?)24682.198715188184 : null,
                        upper = row.Item2 == "selling" ? (double?)25025.908926280426 : null,
                        trigger = "first index quote at/beyond level"
                    },
                    plannedExitAt = "2026-11-09T10:00:00Z", warnings
                })
            });
        }
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "daily", AnalysisJson = analysis
        }, candidates);
        using var compact = JsonDocument.Parse(input);
        Assert.Equal(3, compact.RootElement.GetProperty("s").GetArrayLength());
        var buckets = compact.RootElement.GetProperty("c").EnumerateArray()
            .Select(x => $"{x[1].GetString()}:{x[2].GetString()}").Distinct().Order().ToArray();
        Assert.Equal(new[] { "buying:high", "buying:low", "buying:medium", "selling:medium" }, buckets);
        Assert.True(OptionsAiValidation.CountInputTokens(input) <= 2372);
        Assert.Contains("maxLoss", compact.RootElement.GetProperty("C").GetString());
        Assert.Contains("estStopLoss", compact.RootElement.GetProperty("C").GetString());
        Assert.Equal(800, OptionsAiValidation.MaxOutputTokens);
        Assert.True(compact.RootElement.GetProperty("news").GetArrayLength() > 0);
        Assert.Equal(6412.1235, compact.RootElement.GetProperty("oil")[0][1].GetDouble());
        Assert.Equal(21868.264, compact.RootElement.GetProperty("f")[0][0].GetDouble());
        Assert.Equal(7212.22, compact.RootElement.GetProperty("m")[5].GetDouble());
        Assert.Equal(-7172.53, compact.RootElement.GetProperty("m")[6].GetDouble());
        Assert.Contains("Choose a known numeric range even with no trades", OptionsAiValidation.Instructions);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void MissingModelAndNullableChargesFitWithoutInventingMetrics(bool missingCharges)
    {
        var candidates = new List<OptionsStrategySuggestion>();
        foreach (var side in new[] { "buying", "selling" })
        {
            var id = side + ":quote-only:2026-11-10:9cda2ef5dd97fed9bbecd680";
            var payload = new JsonObject
            {
                ["id"] = id, ["side"] = side, ["riskTier"] = "high",
                ["strategy"] = side == "buying" ? "long-call" : "put-credit-spread",
                ["expiry"] = "2026-11-10", ["maxProfit"] = null,
                ["maxLoss"] = missingCharges ? null : 6500,
                ["capitalBasis"] = missingCharges ? null : 6500,
                ["costEstimate"] = missingCharges ? null : 4,
                ["estimatedStopLoss"] = null, ["probabilityOfProfit"] = null,
                ["probabilityType"] = "unavailable",
                ["scenarioIds"] = new JsonArray("narrow", "base", "wide"),
                ["technicalStop"] = new JsonObject { ["lower"] = 24900, ["upper"] = null, ["trigger"] = "first index quote at/beyond level" },
                ["plannedExitAt"] = "2026-11-10T10:00:00Z",
                ["warnings"] = new JsonArray(
                    "Hypothetical one-lot trade; no margin estimate or fill guarantee",
                    "Rate/dividend yield unavailable: IV, Greeks, model probabilities, repricing and estimated stop loss unavailable; quoted expiry payoff arithmetic only",
                    "Charges unavailable: gross before charges only; net profit, capital requirement and fee-inclusive maximum loss unavailable")
            };
            if (!missingCharges) payload["warnings"]!.AsArray().RemoveAt(2);
            payload["scenarioProfitProbabilities"] = new JsonObject(new[] { "narrow", "base", "wide" }
                .Select(key => KeyValuePair.Create<string, JsonNode?>(key, new JsonObject
                {
                    ["probability"] = null, ["probabilityType"] = "unavailable",
                    ["reason"] = "Model inputs, charges, valid interval and positive time required; no conditional probability"
                })));
            candidates.Add(new OptionsStrategySuggestion
            {
                CandidateKey = id, Side = side, RiskTier = "high", PayloadJson = payload.ToJsonString()
            });
        }
        var input = OptionsAiValidation.BuildInput(new OptionsForecastRun
        {
            IndexCode = "NIFTY50", Mode = "daily",
            AnalysisJson = """{"scenarios":[{"id":"narrow","low":24800,"high":25200,"probability":null,"probabilityType":"unavailable"},{"id":"base","low":24700,"high":25300,"probability":null,"probabilityType":"unavailable"},{"id":"wide","low":24600,"high":25400,"probability":null,"probabilityType":"unavailable"}]}"""
        }, candidates);
        using var root = JsonDocument.Parse(input);
        Assert.Equal(2, root.RootElement.GetProperty("c").GetArrayLength());
        Assert.True(OptionsAiValidation.CountInputTokens(input) <= 2372);
    }
}
