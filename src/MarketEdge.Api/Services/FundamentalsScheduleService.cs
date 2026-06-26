using MarketEdge.Api.Data;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

/// <summary>
/// Enqueues a nightly fundamentals-only refresh (stage2 universe) once per exchange-local
/// calendar day for any market whose <see cref="Models.FundamentalsSchedule"/> is enabled.
/// Fires once the exchange-local time has passed the configured <c>HourLocal</c> (default 20:00,
/// i.e. after the close), on weekdays only. The worker resolves the stage2 universe itself and
/// runs just the <c>fundamentals</c> ingestion step. Idempotent: <c>LastEnqueuedAt</c> (persisted)
/// prevents a second enqueue the same local day, and an in-flight check avoids piling up.
/// </summary>
public class FundamentalsScheduleService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<FundamentalsScheduleService> _logger;

    private static readonly string[] Markets = { "india", "us" };

    public FundamentalsScheduleService(IServiceScopeFactory scopeFactory, ILogger<FundamentalsScheduleService> logger)
    {
        _scopeFactory = scopeFactory;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await TickAsync(stoppingToken);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Fundamentals schedule tick failed");
            }
            await Task.Delay(TimeSpan.FromMinutes(1), stoppingToken);
        }
    }

    private async Task TickAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var ingestion = scope.ServiceProvider.GetRequiredService<IIngestionService>();

        foreach (var market in Markets)
        {
            var schedule = await db.FundamentalsSchedules.FirstOrDefaultAsync(s => s.Market == market, ct);
            if (schedule is not { Enabled: true }) continue;

            var local = MarketHours.NowLocal(market);
            if (local is not DateTimeOffset now) continue;

            // Weekdays only, and only once the exchange-local hour has passed.
            if (now.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) continue;
            if (now.Hour < schedule.HourLocal) continue;

            // Once per exchange-local calendar day: compare last enqueue in the same local tz.
            if (schedule.LastEnqueuedAt is DateTime lastUtc)
            {
                var lastLocal = MarketHours.NowLocal(market, new DateTimeOffset(lastUtc, TimeSpan.Zero));
                if (lastLocal is DateTimeOffset ll && ll.Date == now.Date) continue;
            }

            // Idempotency: don't pile up if a fundamentals run is still in flight for this market.
            var inFlight = await db.JobRuns.AnyAsync(j =>
                j.JobType == IngestionService.FundamentalsJobType && j.Market == market &&
                (j.Status == "queued" || j.Status == "running"), ct);
            if (inFlight) continue;

            await ingestion.TriggerFundamentalsAsync(market, triggeredBy: "nightly");
            schedule.LastEnqueuedAt = DateTime.UtcNow;
            schedule.UpdatedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _logger.LogInformation("Scheduled nightly fundamentals refresh enqueued for {Market}", market);
        }
    }
}
