using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

/// <summary>
/// Enqueues a nightly <c>market_regime</c> refresh per market after the exchange-local
/// <c>HourLocal</c> (default 20:00). The worker computes and persists the full regime snapshot;
/// this service only schedules the job (with dedupe + in-flight guards), mirroring
/// <see cref="ScannerScheduleService"/>'s tick/idempotency shape.
/// </summary>
public class MarketRegimeScheduleService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<MarketRegimeScheduleService> _logger;

    private static readonly string[] Markets = { "india", "us" };
    private static readonly TimeSpan EnqueueDedupe = TimeSpan.FromHours(12);

    public MarketRegimeScheduleService(IServiceScopeFactory scopeFactory, ILogger<MarketRegimeScheduleService> logger)
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
                _logger.LogError(ex, "Market regime schedule tick failed");
            }
            await Task.Delay(TimeSpan.FromMinutes(1), stoppingToken);
        }
    }

    private async Task TickAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var regime = scope.ServiceProvider.GetRequiredService<IMarketRegimeService>();

        foreach (var market in Markets)
        {
            var schedule = await db.RegimeSchedules.FirstOrDefaultAsync(s => s.Market == market, ct);
            if (schedule == null)
            {
                schedule = new RegimeSchedule { Market = market, Enabled = true, HourLocal = 20, UpdatedAt = DateTime.UtcNow };
                db.RegimeSchedules.Add(schedule);
                await db.SaveChangesAsync(ct);
            }
            if (!schedule.Enabled) continue;

            var localNow = MarketHours.NowLocal(market);
            if (localNow == null || localNow.Value.Hour < schedule.HourLocal) continue;

            if (schedule.LastEnqueuedAt is DateTime last && DateTime.UtcNow - last < EnqueueDedupe) continue;

            var inFlight = await db.JobRuns.AnyAsync(j =>
                j.JobType == "market_regime" && j.Market == market &&
                (j.Status == "queued" || j.Status == "running"), ct);
            if (inFlight) continue;

            await regime.TriggerRefreshAsync(market);
            schedule.LastEnqueuedAt = DateTime.UtcNow;
            schedule.UpdatedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _logger.LogInformation("Nightly market regime refresh enqueued for {Market}", market);
        }
    }
}
