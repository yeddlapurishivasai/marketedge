using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

/// <summary>
/// Enqueues a pre-close scan (all scanners, stage2 universe) every <c>IntervalMinutes</c> while
/// the market is open, for any market whose <see cref="ScannerSchedule"/> is enabled. Auto
/// starts/stops by checking exchange-local trading hours each minute:
/// India 09:15–15:30 IST, US 09:30–16:00 ET, weekdays only. Idempotent: never enqueues while a
/// scanner job for the same market is still queued/running.
/// </summary>
public class ScannerScheduleService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<ScannerScheduleService> _logger;

    private static readonly string[] Markets = { "india", "us" };

    public ScannerScheduleService(IServiceScopeFactory scopeFactory, ILogger<ScannerScheduleService> logger)
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
                _logger.LogError(ex, "Scanner schedule tick failed");
            }
            await Task.Delay(TimeSpan.FromMinutes(1), stoppingToken);
        }
    }

    private async Task TickAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var scanners = scope.ServiceProvider.GetRequiredService<IScannerService>();

        foreach (var market in Markets)
        {
            var schedule = await db.ScannerSchedules.FirstOrDefaultAsync(s => s.Market == market, ct);
            if (schedule is not { Enabled: true }) continue;

            if (!MarketHours.IsOpen(market)) continue;

            var interval = TimeSpan.FromMinutes(Math.Max(schedule.IntervalMinutes, 1));
            if (schedule.LastEnqueuedAt is DateTime last && DateTime.UtcNow - last < interval) continue;

            // Idempotency: don't pile up if a scanner run is still in flight for this market.
            var inFlight = await db.JobRuns.AnyAsync(j =>
                j.JobType == "scanner" && j.Market == market &&
                (j.Status == "queued" || j.Status == "running"), ct);
            if (inFlight) continue;

            await scanners.TriggerScannerAsync(market, new TriggerScannerRequest { ScannerName = null, Universe = "stage2" });
            schedule.LastEnqueuedAt = DateTime.UtcNow;
            schedule.UpdatedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _logger.LogInformation("Scheduled pre-close scan enqueued for {Market}", market);
        }
    }
}
