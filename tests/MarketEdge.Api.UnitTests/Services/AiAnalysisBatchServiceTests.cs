using FluentAssertions;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;

namespace MarketEdge.Api.UnitTests.Services;

public class AiAnalysisBatchServiceTests
{
    [Fact]
    public async Task TriggerAsync_ReturnsExistingRunForSameMarketAndWeek()
    {
        using var db = DbContextFactory.Create();
        var service = new AiAnalysisBatchService(db);

        var first = await service.TriggerAsync("india", top: 20);
        var second = await service.TriggerAsync("india", top: 20);

        first.AlreadyExists.Should().BeFalse();
        second.AlreadyExists.Should().BeTrue();
        second.RunId.Should().Be(first.RunId);
        second.WeekNumber.Should().Be(first.WeekNumber);
        db.JobRuns.Should().ContainSingle(job => job.JobType == AiAnalysisBatchService.JobType);
        db.JobRuns.Single().Status.Should().Be("queued");
    }

    [Fact]
    public async Task TriggerAsync_DoesNotRepeatCompletedRunForWeek()
    {
        using var db = DbContextFactory.Create();
        var week = AiAnalysisBatchService.GetIsoWeekNumber(DateTime.UtcNow);
        db.JobRuns.Add(new JobRun
        {
            JobType = AiAnalysisBatchService.JobType,
            Market = "us",
            WeekNumber = week,
            Status = "completed",
            Progress = 100,
            CreatedAt = DateTime.UtcNow.AddHours(-2),
            CompletedAt = DateTime.UtcNow.AddHours(-1)
        });
        await db.SaveChangesAsync();

        var result = await new AiAnalysisBatchService(db).TriggerAsync("us", top: 20);

        result.AlreadyExists.Should().BeTrue();
        db.JobRuns.Should().ContainSingle();
    }
}
