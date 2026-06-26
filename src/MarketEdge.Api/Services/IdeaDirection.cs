using System.Text.Json;

namespace MarketEdge.Api.Services;

// Server-side long/short/neutral bucketing for fundamental ideas, plus a *direction-aware*
// confidence so the conviction number always means "how strong is the call for THIS row's
// side": a long row's 80 = strong upside, a short row's 80 = strong downside.
//
// Background: the stored confidences (computed in confidence.py) are strictly bullish — they
// floor every miss/contraction/sell to p̂=0, so a genuinely bad quarter scores ~0. That is
// correct for longs but inverted for shorts. Here we mirror the normalisation for short rows
// (a -40% EPS miss -> high short conviction) using the same Wilson-lower-bound + age-decay math
// as the Python model, so any API consumer gets a correctly-oriented score for both sides.
public static class IdeaDirection
{
    // Mirror of confidence.py tunables.
    private const double Z0 = 1.28;
    private const double Halflife = 30.0;
    private const double EpsBeatFullPct = 25.0;
    private const double OpmExpansionFullPp = 10.0;
    private const double OpExpansionFullPct = 50.0;

    private static readonly Dictionary<string, double> FundWeights = new()
    {
        ["epsBeat"] = 0.30,
        ["opExpansion"] = 0.20,
        ["opmExpansion"] = 0.20,
        ["rating"] = 0.15,
        ["targetUpside"] = 0.15,
    };

    // Dead-band on the signed direction score: |score| <= 20 is Neutral.
    public const int LongMin = 20;
    public const int ShortMax = -20;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private static double Clamp01(double x) => x < 0.0 ? 0.0 : x > 1.0 ? 1.0 : x;
    private static double Signed(double x) => x < -1.0 ? -1.0 : x > 1.0 ? 1.0 : x;

    private static double AgedZ(int? days)
    {
        var d = days is null || days < 0 ? 0.0 : (double)days.Value;
        return Z0 * (1.0 + d / Halflife);
    }

    private static double WilsonLb(double phat, int n, double z)
    {
        if (n <= 0) return 0.0;
        phat = Clamp01(phat);
        var denom = 1.0 + z * z / n;
        var centre = phat + z * z / (2 * n);
        var margin = z * Math.Sqrt((phat * (1 - phat) + z * z / (4 * n)) / n);
        return Math.Max(0.0, (centre - margin) / denom);
    }

    // +1 buy / 0 hold / -1 sell, used for the signed direction score.
    private static double? RatingDirection(string? grade)
    {
        if (string.IsNullOrWhiteSpace(grade)) return null;
        var g = grade.ToLowerInvariant();
        if (g.Contains("buy") || g.Contains("outperform") || g.Contains("overweight") || g.Contains("accumulate") || g.Contains("add")) return 1.0;
        if (g.Contains("sell") || g.Contains("underperform") || g.Contains("underweight") || g.Contains("reduce")) return -1.0;
        if (g.Contains("hold") || g.Contains("neutral") || g.Contains("equal") || g.Contains("perform") || g.Contains("in-line")) return 0.0;
        return null;
    }

    // Bearish rating strength (sell=1, hold=0.5, buy=0) for the mirrored short confidence.
    private static double? ShortRatingStrength(string? grade)
    {
        var d = RatingDirection(grade);
        return d is null ? null : 0.5 - d.Value / 2.0; // +1 -> 0, 0 -> 0.5, -1 -> 1
    }

    /// <summary>Signed direction score in -100..+100 (null when no metric has data).</summary>
    public static int? Score(double? epsBeatPct, double? opmExpansionPp, double? opExpansionPct, string? ratingGrade)
    {
        double wsum = 0, num = 0;
        void Add(double w, double? v) { if (v is not null) { wsum += w; num += w * v.Value; } }
        Add(0.30, epsBeatPct is null ? null : Signed(epsBeatPct.Value / EpsBeatFullPct));
        Add(0.20, opExpansionPct is null ? null : Signed(opExpansionPct.Value / OpExpansionFullPct));
        Add(0.20, opmExpansionPp is null ? null : Signed(opmExpansionPp.Value / OpmExpansionFullPp));
        Add(0.15, RatingDirection(ratingGrade));
        if (wsum <= 0) return null;
        return (int)Math.Round(100.0 * num / wsum);
    }

    /// <summary>long / short / neutral from a signed score (null when score is null).</summary>
    public static string? Side(int? score) =>
        score is null ? null : score > LongMin ? "long" : score < ShortMax ? "short" : "neutral";

    public record ShortConfidence(
        decimal? EpsBeat,
        decimal? OpmExpansion,
        decimal? OpExpansion,
        decimal? Rating,
        decimal? Fundamental,
        decimal? Overall,
        string RationaleJson);

    /// <summary>
    /// Mirrored bearish confidence for a short row: each metric's miss/contraction/sell maps to a
    /// high short-conviction p̂, blended exactly like the bullish model. Target upside is omitted
    /// (the idea row carries no live close to compute downside from).
    /// </summary>
    public static ShortConfidence ComputeShortConfidence(
        double? epsBeatPct, double? opmExpansionPp, double? opExpansionPct, string? ratingGrade,
        int? daysSinceEarnings, int? daysSinceRating)
    {
        var phats = new List<(string Key, double Phat, int? Days)>();
        if (epsBeatPct is not null) phats.Add(("epsBeat", Clamp01(-epsBeatPct.Value / EpsBeatFullPct), daysSinceEarnings));
        if (opmExpansionPp is not null) phats.Add(("opmExpansion", Clamp01(-opmExpansionPp.Value / OpmExpansionFullPp), daysSinceEarnings));
        if (opExpansionPct is not null) phats.Add(("opExpansion", Clamp01(-opExpansionPct.Value / OpExpansionFullPct), daysSinceEarnings));
        var sr = ShortRatingStrength(ratingGrade);
        if (sr is not null) phats.Add(("rating", sr.Value, daysSinceRating));

        var n = phats.Count;
        var conf = new Dictionary<string, double>();
        var rationaleMetrics = new List<object>();
        foreach (var (key, phat, days) in phats)
        {
            var z = AgedZ(days);
            var c = Math.Round(100.0 * WilsonLb(phat, n, z), 2);
            conf[key] = c;
            rationaleMetrics.Add(new
            {
                metric = key,
                phat = Math.Round(phat, 4),
                n,
                days,
                z = Math.Round(z, 4),
                confidence = c,
            });
        }

        double bnum = 0, bden = 0;
        foreach (var (key, c) in conf)
        {
            var w = FundWeights.GetValueOrDefault(key, 0.0);
            bnum += w * c;
            bden += w;
        }
        double? fundamental = bden > 0 ? Math.Round(bnum / bden, 2) : null;
        // Technical is null for every idea today, so Overall = Fundamental for shorts too.
        var overall = fundamental;

        var rationale = new
        {
            n,
            weights = FundWeights,
            metrics = rationaleMetrics,
            targetUpsidePct = (double?)null,
            fundamental,
            technical = (double?)null,
            technicalDetail = (object?)null,
            overall,
            blend = new { fundamental = 0.60, technical = 0.40 },
            side = "short",
        };

        return new ShortConfidence(
            conf.TryGetValue("epsBeat", out var e) ? (decimal)e : null,
            conf.TryGetValue("opmExpansion", out var om) ? (decimal)om : null,
            conf.TryGetValue("opExpansion", out var op) ? (decimal)op : null,
            conf.TryGetValue("rating", out var ra) ? (decimal)ra : null,
            fundamental is null ? null : (decimal)fundamental.Value,
            overall is null ? null : (decimal)overall.Value,
            JsonSerializer.Serialize(rationale, JsonOpts));
    }
}
