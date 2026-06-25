namespace MarketEdge.Api.Services;

/// <summary>Exchange trading-hours helper. India (NSE) 09:15–15:30 IST, US 09:30–16:00 ET,
/// weekdays only. Holiday calendars are out of scope (weekday + hours window only).</summary>
public static class MarketHours
{
    private static readonly Dictionary<string, (string TzId, TimeSpan Open, TimeSpan Close)> Windows = new()
    {
        ["india"] = ("India Standard Time", new TimeSpan(9, 15, 0), new TimeSpan(15, 30, 0)),
        ["us"] = ("Eastern Standard Time", new TimeSpan(9, 30, 0), new TimeSpan(16, 0, 0)),
    };

    public static bool IsOpen(string market, DateTimeOffset? nowUtc = null)
    {
        if (!Windows.TryGetValue(market.ToLowerInvariant(), out var w)) return false;
        TimeZoneInfo tz;
        try
        {
            tz = TimeZoneInfo.FindSystemTimeZoneById(w.TzId);
        }
        catch (TimeZoneNotFoundException)
        {
            return false;
        }
        var local = TimeZoneInfo.ConvertTime(nowUtc ?? DateTimeOffset.UtcNow, tz);
        if (local.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) return false;
        var t = local.TimeOfDay;
        return t >= w.Open && t <= w.Close;
    }
}
