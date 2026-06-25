namespace MarketEdge.Api.Models;

/// <summary>A nightly/weekend job schedule (fundamentals refresh, weekend stage2 analysis).
/// Fires once per exchange-local day after <see cref="HourLocal"/>.</summary>
public record JobScheduleDto(
    string Market, bool Enabled, int HourLocal,
    DateTime? LastEnqueuedAt, DateTime UpdatedAt, DateTime? LastRunAt);

public class UpdateJobScheduleRequest
{
    public bool Enabled { get; set; }
    public int? HourLocal { get; set; }
}
