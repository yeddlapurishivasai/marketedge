using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public interface IAiAnalysisCandidateService
{
    Task<AiAnalysisCandidateResponse?> GetTopCandidatesAsync(
    string market, int top = 20, bool excludeAnalyzed = true, decimal? minMarketCap = null);
}

public class AiAnalysisCandidateService : IAiAnalysisCandidateService
{
    private const int MaxPerSector = 4;
    private const decimal IndiaDefaultMinimumMarketCap = 10_000_000_000m;
    private const decimal UsDefaultMinimumMarketCap = 10_000_000_000m;
    private readonly MarketEdgeDbContext _db;

    public AiAnalysisCandidateService(MarketEdgeDbContext db) => _db = db;

    public async Task<AiAnalysisCandidateResponse?> GetTopCandidatesAsync(
        string market, int top = 20, bool excludeAnalyzed = true, decimal? minMarketCap = null)
    {
        var requested = Math.Clamp(top, 1, 100);
        var minimumMarketCap = minMarketCap
            ?? (market == "us" ? UsDefaultMinimumMarketCap : IndiaDefaultMinimumMarketCap);
        if (minimumMarketCap < 0)
            throw new ArgumentOutOfRangeException(nameof(minMarketCap), "Minimum market cap cannot be negative.");
        var run = await _db.JobRuns.AsNoTracking()
            .Where(row => row.JobType == "stage2_analysis"
                && row.Market == market && row.Status == "completed")
            .OrderByDescending(row => row.CompletedAt ?? row.CreatedAt)
            .FirstOrDefaultAsync();
        if (run == null) return null;

        var completedAt = run.CompletedAt ?? run.CreatedAt;
        var analysisWeek = GetIsoWeekNumber(DateTime.UtcNow);
        if (completedAt < DateTime.UtcNow.AddDays(-10))
            throw new InvalidOperationException(
                $"Latest Stage 2 snapshot for {market} is stale ({completedAt:O}).");

        var allStageRows = await LoadStageRowsAsync(market, run.WeekNumber);
        var stage2 = allStageRows.Where(row => row.IsStage2).ToList();
        var marketCapEligible = stage2
            .Where(row => row.MarketCap.HasValue && row.MarketCap >= minimumMarketCap)
            .ToList();
        var available = marketCapEligible;
        if (excludeAnalyzed)
        {
            var analyzed = (await _db.AiStockAnalysisResults.AsNoTracking()
                .Where(row => row.Market == market && row.WeekNumber == analysisWeek)
                .Select(row => row.Ticker.ToUpper()).ToListAsync()).ToHashSet();
            available = marketCapEligible
                .Where(row => !analyzed.Contains(row.Symbol.ToUpperInvariant()))
                .ToList();
        }

        var symbols = available.Select(row => row.Symbol.ToUpperInvariant()).Distinct().ToList();
        var earningsRows = await LoadEarningsAsync(market, symbols);
        var signalRows = await LoadSignalsAsync(market, symbols);
        var pivotRows = await LoadNearPivotsAsync(market, symbols);
        var bars = await LoadBarsAsync(market, symbols);

        var pivots = pivotRows
            .Where(row => row.Direction == "long" && Math.Abs(row.DistancePct) <= 5m)
            .GroupBy(row => row.Ticker.ToUpperInvariant())
            .ToDictionary(group => group.Key, group => group.OrderByDescending(row => row.ScanDate).First());
        var setups = available.ToDictionary(
            row => row.Symbol.ToUpperInvariant(),
            row => BuildTechnicalSetup(row.Symbol, pivots.GetValueOrDefault(row.Symbol.ToUpperInvariant()), bars));
        var setupCount = setups.Values.Count(setup => TechnicalSetupScore(setup) > 0);

        var earnings = earningsRows.ToDictionary(row => row.Ticker.ToUpperInvariant());
        var signals = signalRows.ToDictionary(row => row.Ticker.ToUpperInvariant());
        var yoyRanks = Percentiles(earningsRows.Select(row => (row.Ticker, row.EarningsGrowthYoyPct)));
        var qoqRanks = Percentiles(earningsRows.Select(row => (row.Ticker, row.EarningsGrowthQoqPct)));
        var marginRanks = Percentiles(earningsRows.Select(row =>
            (row.Ticker, row.Opm.HasValue && row.OpmYoyQ.HasValue ? row.Opm - row.OpmYoyQ : null)));
        var capexRanks = Percentiles(signalRows.Select(row => (row.Ticker, row.CapexChangePct)));
        var sectors = BuildSectorStrength(allStageRows);

        var scored = available.Select(stage => Score(
                stage,
                earnings.GetValueOrDefault(stage.Symbol.ToUpperInvariant()),
                signals.GetValueOrDefault(stage.Symbol.ToUpperInvariant()),
                sectors[stage.SectorName],
                setups[stage.Symbol.ToUpperInvariant()],
                yoyRanks, qoqRanks, marginRanks, capexRanks))
            .OrderByDescending(row => row.Score)
            .ThenByDescending(row => row.Stage.RSRank)
            .ThenBy(row => row.Stage.Symbol)
            .ToList();

        var selected = ApplySectorCap(scored, requested);
        var candidates = selected.Select((candidate, index) => MapCandidate(candidate, index + 1)).ToList();
        return new AiAnalysisCandidateResponse(
            market, run.Id, run.WeekNumber, analysisWeek, completedAt, requested, minimumMarketCap,
            stage2.Count, marketCapEligible.Count, available.Count,
            setupCount, candidates);
    }

    private async Task<List<StageAnalysisResultBase>> LoadStageRowsAsync(string market, string week) =>
        market == "us"
            ? (await _db.USStageAnalysisResults.AsNoTracking().Where(row => row.WeekNumber == week)
                .ToListAsync()).Cast<StageAnalysisResultBase>().ToList()
            : (await _db.IndianStageAnalysisResults.AsNoTracking().Where(row => row.WeekNumber == week)
                .ToListAsync()).Cast<StageAnalysisResultBase>().ToList();

    private async Task<List<EarningsFundamentalsBase>> LoadEarningsAsync(string market, List<string> symbols) =>
        market == "us"
            ? (await _db.USEarningsFundamentals.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<EarningsFundamentalsBase>().ToList()
            : (await _db.IndianEarningsFundamentals.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<EarningsFundamentalsBase>().ToList();

    private async Task<List<StockSignalsBase>> LoadSignalsAsync(string market, List<string> symbols) =>
        market == "us"
            ? (await _db.USStockSignals.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<StockSignalsBase>().ToList()
            : (await _db.IndianStockSignals.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<StockSignalsBase>().ToList();

    private async Task<List<NearPivotBase>> LoadNearPivotsAsync(string market, List<string> symbols) =>
        market == "us"
            ? (await _db.USNearPivots.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<NearPivotBase>().ToList()
            : (await _db.IndianNearPivots.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .ToListAsync()).Cast<NearPivotBase>().ToList();

    private async Task<List<Bar1DBase>> LoadBarsAsync(string market, List<string> symbols) =>
        market == "us"
            ? (await _db.USBars1D.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .OrderBy(row => row.BarDate).ToListAsync()).Cast<Bar1DBase>().ToList()
            : (await _db.IndianBars1D.AsNoTracking().Where(row => symbols.Contains(row.Ticker.ToUpper()))
                .OrderBy(row => row.BarDate).ToListAsync()).Cast<Bar1DBase>().ToList();

    private static TechnicalSetupWork BuildTechnicalSetup(
        string symbol, NearPivotBase? pivot, List<Bar1DBase> allBars)
    {
        var bars = allBars.Where(row => row.Ticker.Equals(symbol, StringComparison.OrdinalIgnoreCase))
            .OrderBy(row => row.BarDate).ToList();
        var calendar = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var weeks = bars.GroupBy(row =>
            {
                var date = row.BarDate.ToDateTime(TimeOnly.MinValue);
                var week = calendar.GetWeekOfYear(date,
                    System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
                var year = date.Year;
                if (week >= 52 && date.Month == 1) year--;
                if (week == 1 && date.Month == 12) year++;
                return (year, week);
            })
            .OrderBy(group => group.Min(row => row.BarDate))
            .Select(group => new WeeklyBar(
                group.Max(row => row.BarDate),
                group.Min(row => row.Low),
                group.OrderBy(row => row.BarDate).Last().Close))
            .Where(row => row.Close.HasValue)
            .ToList();

        decimal? ema40 = null;
        if (weeks.Count >= 40)
        {
            var multiplier = 2m / 41m;
            ema40 = weeks[0].Close!.Value;
            foreach (var week in weeks.Skip(1))
                ema40 = (week.Close!.Value - ema40.Value) * multiplier + ema40.Value;
        }

        var latest = weeks.LastOrDefault();
        var previous = weeks.Count > 1 ? weeks[^2] : null;
        decimal? distance = ema40.HasValue && latest?.Close is not null
            ? (latest.Close.Value - ema40.Value) / ema40.Value * 100m
            : null;
        var bounced = ema40.HasValue && latest?.Close is not null && latest.Low is not null
            && latest.Low <= ema40.Value * 1.02m
            && latest.Close >= ema40.Value
            && previous?.Close is not null
            && latest.Close >= previous.Close;

        return new TechnicalSetupWork(
            pivot is not null,
            pivot?.PivotPrice,
            pivot?.DistancePct,
            bounced,
            ema40.HasValue ? Round(ema40.Value) : null,
            distance.HasValue ? Round(distance.Value) : null);
    }

    private static CandidateWork Score(
        StageAnalysisResultBase stage, EarningsFundamentalsBase? earnings, StockSignalsBase? signals,
        SectorWork sector, TechnicalSetupWork setup,
        IReadOnlyDictionary<string, decimal> yoyRanks,
        IReadOnlyDictionary<string, decimal> qoqRanks,
        IReadOnlyDictionary<string, decimal> marginRanks,
        IReadOnlyDictionary<string, decimal> capexRanks)
    {
        var symbol = stage.Symbol.ToUpperInvariant();
        var growth = new List<decimal>();
        if (earnings?.EarningsGrowthYoyPct > 0 && yoyRanks.TryGetValue(symbol, out var yoy)) growth.Add(yoy);
        if (earnings?.EarningsGrowthQoqPct > 0 && qoqRanks.TryGetValue(symbol, out var qoq)) growth.Add(qoq);
        var earningsScore = growth.Count == 0 ? 0 : growth.Average() * 0.25m;

        var marginExpansion = earnings?.Opm is not null && earnings.OpmYoyQ is not null
            ? earnings.Opm - earnings.OpmYoyQ : null;
        var marginScore = marginExpansion > 0 && marginRanks.TryGetValue(symbol, out var margin)
            ? margin * 0.15m : 0;
        var recent = earnings?.LastEarningsDate is not null
            && earnings.LastEarningsDate >= DateOnly.FromDateTime(DateTime.UtcNow.AddDays(-14));
        var recentScore = recent ? 10m : 0m;
        var capexRank = signals?.CapexChangePct > 0 && capexRanks.TryGetValue(symbol, out var capex)
            ? capex : string.Equals(signals?.CapexTrend, "rising", StringComparison.OrdinalIgnoreCase) ? 70m : 0m;
        var capexScore = capexRank * 0.10m;

        var technicalScore = TechnicalSetupScore(setup);

        var freshness = 0m;
        if (stage.CreatedAt >= DateTime.UtcNow.AddDays(-7)) freshness += 2m;
        if (earnings?.UpdatedAt >= DateTime.UtcNow.AddDays(-7)) freshness += 2m;
        if (signals?.UpdatedAt >= DateTime.UtcNow.AddDays(-14)) freshness += 1m;
        var completeness = new object?[] { earnings?.EarningsGrowthYoyPct, marginExpansion,
            signals?.CapexTrend, stage.RSDelta2w, stage.MomentumScore }.Count(value => value is not null);
        var qualityScore = freshness + completeness;

        var reasons = new List<string> { $"Stage 2 {stage.Classification ?? "active"}" };
        if (setup.NearPivot) reasons.Add($"Within {Math.Abs(setup.PivotDistancePct ?? 0):0.##}% of breakout pivot");
        if (setup.Ema40Bounce) reasons.Add("Confirmed bounce from 40-week EMA");
        else if (Math.Abs(setup.Ema40DistancePct ?? decimal.MaxValue) <= 3m)
            reasons.Add("Trading near 40-week EMA without bounce confirmation");
        if (sector.Score >= 14m) reasons.Add("Strong sector trend and Stage 2 participation");
        if (recent) reasons.Add("Earnings announced in the last 14 days");
        if (earnings?.EarningsGrowthYoyPct > 0) reasons.Add("Positive year-over-year earnings growth");
        if (earnings?.EarningsGrowthQoqPct > 0) reasons.Add("Positive quarter-over-quarter earnings growth");
        if (marginExpansion > 0) reasons.Add("Operating margin expansion");
        if (capexRank > 0) reasons.Add("Increasing capex/CWIP");
        if (stage.RSDelta2w > 0) reasons.Add("Improving relative strength");
        if (stage.ADClassification == "accumulating") reasons.Add("Accumulation");
        if (stage.SqueezeFired == true) reasons.Add("Squeeze fired");

        var breakdown = new AiCandidateScoreBreakdown(
            Round(earningsScore), Round(marginScore), recentScore, Round(capexScore),
            Round(sector.Score), Round(technicalScore), Round(qualityScore));
        return new CandidateWork(stage, earnings, signals, sector, setup,
            Round(earningsScore + marginScore + recentScore + capexScore + sector.Score
                + technicalScore + qualityScore), breakdown, reasons, marginExpansion);
    }

    private static decimal TechnicalSetupScore(TechnicalSetupWork setup)
    {
        var pivotDistance = Math.Abs(setup.PivotDistancePct ?? decimal.MaxValue);
        var pivotScore = setup.NearPivot
            ? pivotDistance <= 1m ? 10m : pivotDistance <= 3m ? 8m : 5m
            : 0m;
        var emaDistance = Math.Abs(setup.Ema40DistancePct ?? decimal.MaxValue);
        var emaScore = setup.Ema40Bounce ? 8m : emaDistance <= 3m ? 4m : 0m;
        return Math.Max(pivotScore, emaScore);
    }

    private static Dictionary<string, SectorWork> BuildSectorStrength(List<StageAnalysisResultBase> rows)
    {
        var raw = rows.GroupBy(row => row.SectorName).Select(group => new SectorWork(
            group.Key, Average(group.Select(row => row.RSScore)), Average(group.Select(row => row.RSDelta2w)),
            group.Count(row => row.IsStage2) * 100m / group.Count(), 0)).ToList();
        var rsRanks = Percentiles(raw.Select(row => (row.SectorName, row.AverageRsScore)));
        var deltaRanks = Percentiles(raw.Select(row => (row.SectorName, row.AverageRsDelta2w)));
        var participationRanks = Percentiles(raw.Select(row => (row.SectorName, (decimal?)row.Stage2ParticipationPct)));
        return raw.ToDictionary(row => row.SectorName, row => row with
        {
            Score = Round(rsRanks.GetValueOrDefault(row.SectorName) * 0.10m
                + (row.AverageRsDelta2w > 0 ? deltaRanks.GetValueOrDefault(row.SectorName) * 0.06m : 0)
                + participationRanks.GetValueOrDefault(row.SectorName) * 0.04m)
        }, StringComparer.OrdinalIgnoreCase);
    }

    private static List<CandidateWork> ApplySectorCap(List<CandidateWork> scored, int requested)
    {
        var selected = new List<CandidateWork>();
        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var candidate in scored)
        {
            var count = counts.GetValueOrDefault(candidate.Stage.SectorName);
            if (count >= MaxPerSector) continue;
            selected.Add(candidate);
            counts[candidate.Stage.SectorName] = count + 1;
            if (selected.Count == requested) break;
        }
        return selected;
    }

    private static AiAnalysisCandidate MapCandidate(CandidateWork candidate, int rank) => new(
        rank, candidate.Stage.Symbol, candidate.Stage.CompanyName, candidate.Stage.SectorName,
        candidate.Stage.Classification, candidate.Stage.MarketCap, candidate.Score, candidate.Breakdown,
        new AiCandidateSectorStrength(candidate.Sector.AverageRsScore,
            candidate.Sector.AverageRsDelta2w, candidate.Sector.Stage2ParticipationPct),
        new AiCandidateTechnicalSetup(candidate.Setup.NearPivot, candidate.Setup.PivotPrice,
            candidate.Setup.PivotDistancePct, candidate.Setup.Ema40Bounce,
            candidate.Setup.WeeklyEma40, candidate.Setup.Ema40DistancePct),
        candidate.Reasons, candidate.Earnings?.EarningsGrowthYoyPct,
        candidate.Earnings?.EarningsGrowthQoqPct, candidate.MarginExpansion,
        candidate.Earnings?.LastEarningsDate, candidate.Signals?.CapexChangePct,
        candidate.Stage.RSRank, candidate.Stage.RSDelta2w, candidate.Stage.MomentumScore,
        candidate.Stage.ADClassification, candidate.Stage.SqueezeFired);

    private static Dictionary<string, decimal> Percentiles(IEnumerable<(string Key, decimal? Value)> source)
    {
        var values = source.Where(item => item.Value.HasValue)
            .Select(item => (Key: item.Key.ToUpperInvariant(), Value: item.Value!.Value)).ToList();
        var result = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in values)
            result[item.Key] = values.Count(other => other.Value <= item.Value) * 100m / values.Count;
        return result;
    }

    private static decimal? Average(IEnumerable<decimal?> values)
    {
        var available = values.Where(value => value.HasValue).Select(value => value!.Value).ToList();
        return available.Count == 0 ? null : available.Average();
    }

    private static decimal Round(decimal value) => decimal.Round(value, 2, MidpointRounding.AwayFromZero);

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

    private sealed record WeeklyBar(DateOnly Date, decimal? Low, decimal? Close);
    private sealed record TechnicalSetupWork(bool NearPivot, decimal? PivotPrice, decimal? PivotDistancePct,
        bool Ema40Bounce, decimal? WeeklyEma40, decimal? Ema40DistancePct);
    private sealed record SectorWork(string SectorName, decimal? AverageRsScore,
        decimal? AverageRsDelta2w, decimal Stage2ParticipationPct, decimal Score);
    private sealed record CandidateWork(StageAnalysisResultBase Stage, EarningsFundamentalsBase? Earnings,
        StockSignalsBase? Signals, SectorWork Sector, TechnicalSetupWork Setup, decimal Score,
        AiCandidateScoreBreakdown Breakdown, IReadOnlyList<string> Reasons, decimal? MarginExpansion);
}