using MarketEdge.Api.Data;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

/// <summary>
/// Enqueues a full stage2 analysis run once per weekend (Saturday night) for any market whose
/// <see cref="Models.Stage2Schedule"/> is enabled. Fires once per exchange-local Saturday after
/// the configured <c>HourLocal</c> (default 20:00). Running after Friday's close keeps the week's
/// stage2 classification fresh while markets are closed, without competing with the intraday
/// pre-close scan or the nightly fundamentals refresh on weekdays.
/// Idempotent: <c>LastEnqueuedAt</c> (persisted) prevents a second enqueue the same local day,
/// and stage2 analysis already dedupes one in-flight run per (week, market).
/// </summary>
public class Stage2ScheduleService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<Stage2ScheduleService> _logger;

    private static readonly string[] Markets = { "india", "us" };

    public Stage2ScheduleService(IServiceScopeFactory scopeFactory, ILogger<Stage2ScheduleService> logger)
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
                _logger.LogError(ex, "Stage2 schedule tick failed");
            }
            await Task.Delay(TimeSpan.FromMinutes(1), stoppingToken);
        }
    }

    private async Task TickAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var jobs = scope.ServiceProvider.GetRequiredService<IJobService>();

        foreach (var market in Markets)
        {
            var schedule = await db.Stage2Schedules.FirstOrDefaultAsync(s => s.Market == market, ct);
            if (schedule is not { Enabled: true }) continue;

            var local = MarketHours.NowLocal(market);
            if (local is not DateTimeOffset now) continue;

            // Once per weekend: Saturday only, and only once the exchange-local hour has passed.
            if (now.DayOfWeek is not DayOfWeek.Saturday) continue;
            if (now.Hour < schedule.HourLocal) continue;

            // Once per exchange-local calendar day: compare last enqueue in the same local tz.
            if (schedule.LastEnqueuedAt is DateTime lastUtc)
            {
                var lastLocal = MarketHours.NowLocal(market, new DateTimeOffset(lastUtc, TimeSpan.Zero));
                if (lastLocal is DateTimeOffset ll && ll.Date == now.Date) continue;
            }

            // Idempotency: don't pile up if a stage2 run is still in flight for this market.
            var inFlight = await db.JobRuns.AnyAsync(j =>
                j.JobType == "stage2_analysis" && j.Market == market &&
                (j.Status == "queued" || j.Status == "running"), ct);
            if (inFlight) continue;

            await jobs.TriggerStageAnalysisAsync(market, null);
            schedule.LastEnqueuedAt = DateTime.UtcNow;
            schedule.UpdatedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _logger.LogInformation("Scheduled Saturday stage2 analysis enqueued for {Market}", market);
        }
    }
}
