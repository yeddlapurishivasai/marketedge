using MarketEdge.Api.Authentication;
using MarketEdge.Api.Controllers;
using MarketEdge.Api.Services;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;
using Moq;

namespace MarketEdge.Api.UnitTests.Services;

public sealed class OptionsCallbackTests
{
    [Fact]
    public async Task RejectedCallbackLogsWarningWithoutSensitivePayload()
    {
        var environment = new Mock<IWebHostEnvironment>();
        environment.SetupGet(x => x.ContentRootPath).Returns(Directory.GetCurrentDirectory());
        var correlation = new OptionsLoginCorrelation(new EphemeralDataProtectionProvider(), environment.Object);
        var logger = new Mock<ILogger<OptionsController>>();
        var controller = new OptionsController(null!, null!, correlation, new OptionsSettings(), new AuthOptions(), logger.Object)
        {
            ControllerContext = new ControllerContext { HttpContext = new DefaultHttpContext() }
        };
        var result = await controller.Callback("invalid-state", "sensitive-request-token", default);
        Assert.Equal("/india/options?connection=error", Assert.IsType<RedirectResult>(result).Url);
        logger.Verify(x => x.Log(
            LogLevel.Warning, It.IsAny<EventId>(),
            It.Is<It.IsAnyType>((state, _) => Convert.ToString(state)!.Contains("CryptographicException") &&
                !Convert.ToString(state)!.Contains("sensitive-request-token") && !Convert.ToString(state)!.Contains("invalid-state")),
            It.IsAny<Exception?>(), It.IsAny<Func<It.IsAnyType, Exception?, string>>()), Times.Once);
    }
}
