using System.Globalization;
using System.Text.RegularExpressions;

namespace MarketEdge.Api.Services;

/// <summary>Weekday/exchange-close boundaries; exchange holidays and early closes are not modeled.</summary>
public static partial class Stage4Week
{
    public static string LatestClosed(string market, DateTimeOffset nowUtc)
    {
        ValidateMarket(market);
        var local = MarketHours.NowLocal(market, nowUtc)
            ?? throw new InvalidOperationException("The exchange timezone is unavailable.");
        var friday = local.Date.AddDays(-((int)local.DayOfWeek - (int)DayOfWeek.Friday + 7) % 7);
        var close = market == "india" ? new TimeSpan(15, 30, 0) : new TimeSpan(16, 0, 0);
        if (local.Date == friday && local.TimeOfDay < close)
            friday = friday.AddDays(-7);
        return $"{ISOWeek.GetYear(friday):D4}-W{ISOWeek.GetWeekOfYear(friday):D2}";
    }

    public static string Resolve(string market, string? requested, DateTimeOffset nowUtc)
    {
        var latest = LatestClosed(market, nowUtc);
        if (requested is null) return latest;
        var week = requested.Trim();
        if (!WeekPattern().IsMatch(week) ||
            !int.TryParse(week.AsSpan(0, 4), out var year) || year is < 1 or > 9999 ||
            !int.TryParse(week.AsSpan(6, 2), out var number) ||
            number < 1 || number > ISOWeek.GetWeeksInYear(year))
            throw new ArgumentException("WeekNumber must be a real ISO week in YYYY-Www format.");
        if (string.CompareOrdinal(week, latest) > 0)
            throw new ArgumentException($"Week '{week}' is unfinished or in the future. Latest closed week is {latest}.");
        return week;
    }

    public static void ValidateMarket(string market)
    {
        if (market is not ("india" or "us"))
            throw new ArgumentException("Market must be 'india' or 'us'.");
    }

    [GeneratedRegex(@"^\d{4}-W\d{2}$", RegexOptions.CultureInvariant)]
    private static partial Regex WeekPattern();
}
