using System.Security.Claims;
using Azure.Storage.Queues;
using FluentAssertions;
using MarketEdge.Api.Authentication;
using MarketEdge.Api.Controllers;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsJobIsolationTests
{
    private static async Task SeedAsync(MarketEdgeDbContext db)
    {
        db.JobRuns.AddRange(
            new JobRun { Id = 1, JobType = "options_analysis", Status = "running", Parameters = """{"userKey":"tenant:alice"}""" },
            new JobRun { Id = 2, JobType = "options_analysis", Status = "running", Parameters = """{"userKey":"tenant:bob"}""" },
            new JobRun { Id = 3, JobType = "stage2_analysis", Status = "running" },
            new JobRun { Id = 4, JobType = "options_analysis", Status = "completed" });
        db.OptionsForecasts.AddRange(
            new OptionsForecastRun { Id = 1, JobRunId = 1, UserKey = "tenant:alice", IndexCode = "NIFTY50", Mode = "daily", RequestId = Guid.NewGuid() },
            new OptionsForecastRun { Id = 2, JobRunId = 2, UserKey = "tenant:bob", IndexCode = "NIFTY50", Mode = "daily", RequestId = Guid.NewGuid() });
        await db.SaveChangesAsync();
    }

    [Theory]
    [InlineData(null, 0)]
    [InlineData("tenant:alice", 1)]
    [InlineData("tenant:bob", 2)]
    public async Task GenericJobs_FilterOptionsByForecastOwnershipWithoutChangingSharedJobs(string? userKey, int ownId)
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db);
        var service = new JobService(db, new Mock<QueueClient>().Object);
        var all = await service.GetRunsAsync(userKey: userKey);
        all.Select(x => x.Id).Should().BeEquivalentTo(ownId == 0 ? new[] { 3 } : new[] { 3, ownId });
        var options = await service.GetRunsAsync(jobType: "options_analysis", userKey: userKey);
        options.Select(x => x.Id).Should().BeEquivalentTo(ownId == 0 ? [] : new[] { ownId });
        foreach (var id in new[] { 1, 2, 4 }.Where(id => id != ownId))
        {
            (await service.GetRunByIdAsync(id, userKey)).Should().BeNull();
            (await service.CancelRunAsync(id, userKey)).Should().BeFalse();
        }
        db.JobRuns.Single(x => x.Id == 1).Status.Should().Be("running");
        db.JobRuns.Single(x => x.Id == 2).Status.Should().Be("running");
        (await service.GetRunByIdAsync(3, userKey)).Should().NotBeNull();
        (await service.CancelRunAsync(3, userKey)).Should().BeTrue();
        if (ownId != 0)
        {
            (await service.GetRunByIdAsync(ownId, userKey)).Should().NotBeNull();
            (await service.CancelRunAsync(ownId, userKey)).Should().BeTrue();
        }
    }

    [Fact]
    public async Task GenericJobEndpoints_UseTheSignedInAccountNotRequestParameters()
    {
        using var db = DbContextFactory.Create();
        await SeedAsync(db);
        var controller = new JobsController(new JobService(db, new Mock<QueueClient>().Object), new AuthOptions { Enabled = true })
        {
            ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext
                {
                    User = new ClaimsPrincipal(new ClaimsIdentity(
                        [new Claim("tid", "tenant"), new Claim("oid", "alice")], "test"))
                }
            }
        };
        var result = (OkObjectResult)await controller.GetRuns(jobType: "options_analysis");
        ((List<JobRunDto>)result.Value!).Select(x => x.Id).Should().Equal(1);
        (await controller.GetRun(2)).Should().BeOfType<NotFoundResult>();
        (await controller.CancelRun(2)).Should().BeOfType<NotFoundObjectResult>();
        db.JobRuns.Single(x => x.Id == 2).Status.Should().Be("running");
        (await controller.GetRun(1)).Should().BeOfType<OkObjectResult>();
        (await controller.CancelRun(1)).Should().BeOfType<OkObjectResult>();
    }
}
