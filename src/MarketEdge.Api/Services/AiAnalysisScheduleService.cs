using Microsoft.Extensions.Options;

namespace MarketEdge.Api.Services;

public sealed class AiAnalysisScheduleOptions
{
    public const string SectionName = "AiAnalysisSchedule";
    public bool Enabled { get; set; }
    public int Top { get; set; } = 20;
    public int IndiaHourLocal { get; set; } = 5;
    public int UsHourLocal { get; set; } = 5;
}

public class AiAnalysisScheduleService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly AiAnalysisScheduleOptions _options;
    private readonly ILogger<AiAnalysisScheduleService> _logger;

    public AiAnalysisScheduleService(
        IServiceScopeFactory scopeFactory,
        IOptions<AiAnalysisScheduleOptions> options,
        ILogger<AiAnalysisScheduleService> logger)
    {
        _scopeFactory = scopeFactory;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            if (_options.Enabled)
            {
                await TryScheduleAsync("india", IndiaTimeZone(), _options.IndiaHourLocal);
                await TryScheduleAsync("us", UsEasternTimeZone(), _options.UsHourLocal);
            }
            await Task.Delay(TimeSpan.FromMinutes(5), stoppingToken);
        }
    }

    private async Task TryScheduleAsync(string market, TimeZoneInfo timeZone, int hourLocal)
    {
        var localNow = TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, timeZone);
        if (localNow.DayOfWeek != DayOfWeek.Monday || localNow.Hour < hourLocal) return;

        try
        {
            using var scope = _scopeFactory.CreateScope();
            var batch = scope.ServiceProvider.GetRequiredService<IAiAnalysisBatchService>();
            var result = await batch.TriggerAsync(market, _options.Top, "weekly-schedule");
            if (!result.AlreadyExists)
                _logger.LogInformation(
                    "Scheduled weekly AI analysis run {RunId} for {Market}/{Week}",
                    result.RunId, market, result.WeekNumber);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to schedule weekly AI analysis for {Market}", market);
        }
    }

    private static TimeZoneInfo IndiaTimeZone() => FindTimeZone("Asia/Kolkata", "India Standard Time");
    private static TimeZoneInfo UsEasternTimeZone() => FindTimeZone("America/New_York", "Eastern Standard Time");

    private static TimeZoneInfo FindTimeZone(string ianaId, string windowsId)
    {
        try { return TimeZoneInfo.FindSystemTimeZoneById(ianaId); }
        catch (TimeZoneNotFoundException) { return TimeZoneInfo.FindSystemTimeZoneById(windowsId); }
    }
}
