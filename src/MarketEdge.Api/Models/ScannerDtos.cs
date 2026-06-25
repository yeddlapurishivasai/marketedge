namespace MarketEdge.Api.Models;

/// <summary>One scanner in the catalog. Mirrors the worker's scanner registry so the UI can
/// render a section per scanner even before any results exist.</summary>
public record ScannerCatalogEntry(string Name, string Market, string Label, string Family, bool ComingSoon = false);

public static class ScannerCatalog
{
    // Keep names in sync with src/MarketEdge.Worker/scanners/definitions.py.
    public static readonly IReadOnlyList<ScannerCatalogEntry> All = new List<ScannerCatalogEntry>
    {
        // US
        new("US_SETUP", "us", "US Setup", "Setup"),
        new("US_WEEKLY_SETUP", "us", "US Weekly Setup", "Weekly Setup"),
        new("US_CONTRACTION", "us", "US Contraction", "Contraction"),
        new("US_EXTREME_CONTRACTION", "us", "US Extreme Contraction", "Extreme Contraction"),
        new("US_BULL_SNORT", "us", "US Bull Snort", "Bull Snort"),
        new("US_HIGH_TIGHT_FLAG", "us", "US High Tight Flag", "High Tight Flag"),
        new("US_LOW_TIGHT_FLAG", "us", "US Low Tight Flag", "Low Tight Flag"),
        new("US_POCKET_PIVOT", "us", "US Pocket Pivot", "Pocket Pivot"),
        new("US_WEEKEND_SCAN", "us", "US Weekend Scan", "Weekend Scan"),
        new("US_HIGHEST_VOLUME", "us", "US Highest Volume", "Highest Volume"),
        new("SHOWING_STRENGTH_US", "us", "US Showing Strength", "Showing Strength"),
        new("US_DOUBLERS_1M", "us", "US Doublers 1M", "Doublers"),
        new("US_DOUBLERS_3M", "us", "US Doublers 3M", "Doublers"),
        new("US_DOUBLERS_6M", "us", "US Doublers 6M", "Doublers"),
        new("US_EPISODIC_PIVOT", "us", "US Episodic Pivot", "Episodic Pivot", ComingSoon: true),

        // India (NSE)
        new("NSE_SETUP", "india", "NSE Setup", "Setup"),
        new("NSE_WEEKLY_SETUP", "india", "NSE Weekly Setup", "Weekly Setup"),
        new("NSE_CONTRACTION", "india", "NSE Contraction", "Contraction"),
        new("NSE_EXTREME_CONTRACTION", "india", "NSE Extreme Contraction", "Extreme Contraction"),
        new("NSE_BULL_SNORT", "india", "NSE Bull Snort", "Bull Snort"),
        new("NSE_HIGH_TIGHT_FLAG", "india", "NSE High Tight Flag", "High Tight Flag"),
        new("NSE_LOW_TIGHT_FLAG", "india", "NSE Low Tight Flag", "Low Tight Flag"),
        new("NSE_POCKET_PIVOT", "india", "NSE Pocket Pivot", "Pocket Pivot"),
        new("NSE_WEEKEND_SCAN", "india", "NSE Weekend Scan", "Weekend Scan"),
        new("NSE_HIGHEST_VOLUME", "india", "NSE Highest Volume", "Highest Volume"),
        new("SHOWING_STRENGTH_NSE", "india", "NSE Showing Strength", "Showing Strength"),
        new("NSE_CSS", "india", "NSE Short Candidates", "Short Candidates"),
        new("NSE_DOUBLERS_1M", "india", "NSE Doublers 1M", "Doublers"),
        new("NSE_DOUBLERS_3M", "india", "NSE Doublers 3M", "Doublers"),
        new("NSE_DOUBLERS_6M", "india", "NSE Doublers 6M", "Doublers"),
        new("NSE_EPISODIC_PIVOT", "india", "NSE Episodic Pivot", "Episodic Pivot", ComingSoon: true),
    };

    public static IEnumerable<ScannerCatalogEntry> ForMarket(string market) =>
        All.Where(s => s.Market == market);

    public static bool IsKnown(string market, string name) =>
        All.Any(s => s.Market == market && s.Name == name && !s.ComingSoon);
}

public record ScannerInfoDto(string Name, string Label, string Family, bool ComingSoon, int LatestHits, DateTime? LatestScanDate);

public record ScannerResultDto(
    string Symbol, string? CompanyName, string? SectorName, string? Industry,
    decimal? ClosePrice, decimal? DayChangePct, long? Volume, decimal? RelVolume,
    int? RsRating, string? TriggerDetails);

public record ScannerScheduleDto(string Market, bool Enabled, int IntervalMinutes, DateTime? LastEnqueuedAt, DateTime UpdatedAt, bool IsMarketOpen, DateTime? LastRunAt);

public class TriggerScannerRequest
{
    /// <summary>Single scanner to run. Null/empty runs all scanners for the market (pre-close scan).</summary>
    public string? ScannerName { get; set; }
    /// <summary>"stage2" (default) or "all".</summary>
    public string? Universe { get; set; }
    /// <summary>Replay the last few days' breakouts into the paper-trade blotter (seed/backfill).</summary>
    public bool Backfill { get; set; }
}

public class UpdateScannerScheduleRequest
{
    public bool Enabled { get; set; }
    public int? IntervalMinutes { get; set; }
}
