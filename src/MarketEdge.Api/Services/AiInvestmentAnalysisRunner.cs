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
    private const string SkillVersion = "0.1.0";
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