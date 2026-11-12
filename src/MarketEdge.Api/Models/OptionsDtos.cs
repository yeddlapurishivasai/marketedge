namespace MarketEdge.Api.Models;

public sealed record OptionsForecastRequest(string IndexCode, string Mode, Guid RequestId);
public sealed record OptionsConstituentImport(string Symbol, decimal WeightPct);
public sealed record OptionsSessionImport(DateOnly SessionDate, string Exchange, bool IsTradingDay,
    string? OpenTime, string? CloseTime, bool IsWeeklyExpiry, bool IsMonthlyExpiry);
public sealed record OptionsMetadataImport(string IndexCode, DateOnly EffectiveDate, string Source,
    List<OptionsConstituentImport> Constituents, List<OptionsSessionImport> Sessions);
