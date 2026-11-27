namespace MarketEdge.Api.Services;

public class Stage4ScheduleService(
    IServiceScopeFactory scopeFactory, ILogger<Stage4ScheduleService> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            foreach (var market in new[] { "india", "us" })
            {
                try
                {
                    using var scope = scopeFactory.CreateScope();
                    var service = scope.ServiceProvider.GetRequiredService<IStage4AnalysisService>();
                    if (await service.EnqueueScheduledAsync(market, DateTimeOffset.UtcNow))
                        logger.LogInformation("Scheduled Saturday Stage 4 analysis enqueued for {Market}", market);
                }
                catch (Exception ex)
                {
                    logger.LogError(ex, "Stage 4 schedule tick failed for {Market}", market);
                }
            }
            await Task.Delay(TimeSpan.FromMinutes(1), stoppingToken);
        }
    }
}
