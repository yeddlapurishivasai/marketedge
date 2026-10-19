using System.Text.Json;
using MarketEdge.Api.Models;
using Microsoft.Extensions.Options;

namespace MarketEdge.Api.Services;

public interface IAiInvestmentAnalysisRunner
{
    Task<AiInvestmentAnalysisDetail> RunAsync(
        string market,
        string symbol,
        AiStockAnalysisDto deterministicInput,
        CancellationToken cancellationToken = default);
}

public class AiInvestmentAnalysisRunner : IAiInvestmentAnalysisRunner
{
    private const string SchemaVersion = "marketedge.ai-analysis.v1";
    private const string SkillVersion = "0.4.0";
    private readonly IAzureAiFoundryClient _foundry;
    private readonly IAiAnalysisCostCalculator _costCalculator;
    private readonly IAiInvestmentAnalysisStore _store;
    private readonly AzureAiFoundryOptions _options;
    private readonly string _skillPath;

    public AiInvestmentAnalysisRunner(
        IAzureAiFoundryClient foundry,
        IAiAnalysisCostCalculator costCalculator,
        IAiInvestmentAnalysisStore store,
        IOptions<AzureAiFoundryOptions> options,
        IWebHostEnvironment environment)
    {
        _foundry = foundry;
        _costCalculator = costCalculator;
        _store = store;
        _options = options.Value;
        _skillPath = Path.Combine(environment.ContentRootPath, "Ai", "stock-investment-analysis.skill.md");
    }

    public async Task<AiInvestmentAnalysisDetail> RunAsync(
        string market,
        string symbol,
        AiStockAnalysisDto deterministicInput,
        CancellationToken cancellationToken = default)
    {
        var retrievedAt = DateTime.UtcNow;
        var company = deterministicInput.Stock.CompanyName;
        var asOfDate = deterministicInput.GeneratedAt.ToString("O");
        var inputJson = JsonSerializer.Serialize(deterministicInput);
        var skill = await File.ReadAllTextAsync(_skillPath, cancellationToken);
        var finalInput = $$"""
            Market: {{market}}
            Symbol: {{symbol}}
            Company: {{company}}
            Deterministic data as of: {{asOfDate}}
            Retrieval timestamp: {{retrievedAt:O}}

            DETERMINISTIC_INPUT_JSON:
            {{inputJson}}

            Perform two distinct web-research passes before writing the report:
            1. Earnings-call transcripts and management commentary, including prior-quarter comparison.
            2. Corporate filings, exchange disclosures, earnings releases, and investor presentations
               from the last 365 days.

            Use the research only as evidence for the final report. Do not output a research scratchpad,
            duplicate source descriptions, or intermediate tool results. Produce only the concise final
            JSON report defined by the skill, including its enrichment and sources sections.
            """;
        var analysis = await _foundry.CreateResponseAsync(
            skill + ResearchInstructions,
            finalInput,
            enableWebSearch: true,
            _options.AnalysisMaxOutputTokens,
            cancellationToken);
        var analysisJson = NormalizeJson(analysis.OutputText);
        ValidateRequiredValuation(analysisJson);
        ValidateRequiredProjections(analysisJson);
        var earningsJson = ExtractEnrichment(analysisJson, "earningsCall");
        var filingsJson = ExtractEnrichment(analysisJson, "corporateFilings");
        var cost = _costCalculator.Calculate(new[] { analysis.Usage });

        return await _store.SaveAsync(new SaveAiInvestmentAnalysis(
            market,
            symbol,
            GetIsoWeekNumber(retrievedAt),
            SchemaVersion,
            SkillVersion,
            _options.Model,
            inputJson,
            earningsJson,
            filingsJson,
            analysisJson,
            cost));
    }

    private static string NormalizeJson(string output)
    {
        var value = output.Trim();
        if (value.StartsWith("```", StringComparison.Ordinal))
        {
            var firstNewline = value.IndexOf('\n');
            var lastFence = value.LastIndexOf("```", StringComparison.Ordinal);
            if (firstNewline >= 0 && lastFence > firstNewline)
                value = value[(firstNewline + 1)..lastFence].Trim();
        }

        using var document = JsonDocument.Parse(value);
        return document.RootElement.GetRawText();
    }

    private static string ExtractEnrichment(string analysisJson, string section)
    {
        using var document = JsonDocument.Parse(analysisJson);
        if (document.RootElement.TryGetProperty("enrichment", out var enrichment)
            && enrichment.TryGetProperty(section, out var value))
            return value.GetRawText();
        return "{\"status\":\"unavailable\",\"findings\":[],\"sources\":[]}";
    }

    private static void ValidateRequiredProjections(string analysisJson)
    {
        using var document = JsonDocument.Parse(analysisJson);
        if (!document.RootElement.TryGetProperty("priceScenarios", out var scenarios)
            || scenarios.ValueKind != JsonValueKind.Array)
            throw new InvalidOperationException("AI analysis is missing required price scenarios.");

        foreach (var horizon in new[] { "3m", "6m", "1y", "2y" })
        {
            var scenario = scenarios.EnumerateArray().FirstOrDefault(item =>
                item.TryGetProperty("horizon", out var value) && value.GetString() == horizon);
            if (scenario.ValueKind == JsonValueKind.Undefined)
                throw new InvalidOperationException($"AI analysis is missing the {horizon} price scenario.");
            if (!TryGetPositiveNumber(scenario, "referencePrice", out var referencePrice)
                || !HasNonEmptyString(scenario, "method")
                || !HasNonEmptyString(scenario, "calculation")
                || !HasNonEmptyString(scenario, "revenueAssumption")
                || !HasNonEmptyString(scenario, "epsAssumption")
                || !HasNonEmptyString(scenario, "valuationMultiple"))
                throw new InvalidOperationException(
                    $"AI analysis {horizon} scenario must include its reference price and valuation methodology.");

            foreach (var caseName in new[] { "bear", "base", "bull" })
            {
                if (!scenario.TryGetProperty(caseName, out var priceCase)
                    || !TryGetPositiveNumber(priceCase, "target", out var target)
                    || !TryGetNumber(priceCase, "returnPct", out var returnPct)
                    || !HasNonEmptyArray(priceCase, "assumptions"))
                    throw new InvalidOperationException(
                        $"AI analysis {horizon} {caseName} scenario must contain a target, returnPct, and assumptions.");

                var calculatedReturn = (target / referencePrice - 1m) * 100m;
                if (Math.Abs(calculatedReturn - returnPct) > 0.25m)
                    throw new InvalidOperationException(
                        $"AI analysis {horizon} {caseName} returnPct does not match its target and reference price.");
            }
        }
    }

    private static bool HasNonEmptyString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
        && !string.IsNullOrWhiteSpace(value.GetString());

    private static bool HasNonEmptyArray(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Array
        && value.GetArrayLength() > 0;

    private static bool TryGetNumber(JsonElement element, string property, out decimal result)
    {
        result = 0;
        return element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetDecimal(out result);
    }

    private static void ValidateRequiredValuation(string analysisJson)
    {
        using var document = JsonDocument.Parse(analysisJson);
        if (!document.RootElement.TryGetProperty("valuation", out var valuation)
            || !valuation.TryGetProperty("assessment", out var assessment)
            || assessment.GetString() is not ("undervalued" or "fairly_valued" or "overvalued")
            || !TryGetPositiveNumber(valuation, "intrinsicValueLow", out var low)
            || !TryGetPositiveNumber(valuation, "intrinsicValueHigh", out var high)
            || low > high)
            throw new InvalidOperationException(
                "AI analysis must contain a concrete valuation and a valid positive intrinsic-value range.");
    }

    private static bool TryGetPositiveNumber(JsonElement element, string property, out decimal result)
    {
        result = 0;
        return element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetDecimal(out result)
            && result > 0;
    }

    private static string GetIsoWeekNumber(DateTime date)
    {
        var calendar = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var week = calendar.GetWeekOfYear(
            date, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
        var year = date.Year;
        if (week >= 52 && date.Month == 1) year--;
        if (week == 1 && date.Month == 12) year++;
        return $"{year}-W{week:D2}";
    }

    private const string ResearchInstructions = """

        Runtime research rule: use hosted web search for the two required research passes. Prioritize
        company investor relations and official exchange or regulator sources. Cross-check material
        claims where possible. The final answer must be JSON only and must not contain tool narration,
        search logs, or duplicated evidence. Reference compact source entries by URL.
        """;
}