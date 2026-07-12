using Azure.Storage.Queues;
using MarketEdge.Api.Authentication;
using MarketEdge.Api.Data;
using MarketEdge.Api.Observability;
using MarketEdge.Api.Services;
using Microsoft.EntityFrameworkCore;
using Microsoft.OpenApi.Models;

var builder = WebApplication.CreateBuilder(args);

// OpenTelemetry file logging (OS-specific dir, daily rotation, 7-day retention)
builder.AddMarketEdgeLogging();

// Azure Entra ID authentication (disabled by default via AzureAd:Enabled)
builder.AddMarketEdgeAuth();

// EF Core (no migrations - schema managed by SQL project)
builder.Services.AddDbContext<MarketEdgeDbContext>(options =>
    options.UseSqlServer(builder.Configuration.GetConnectionString("MarketEdge")));

// Azure Storage Queue
var storageConnectionString = builder.Configuration.GetValue<string>("AzureStorage:ConnectionString")
    ?? "UseDevelopmentStorage=true";
var queueName = builder.Configuration.GetValue<string>("AzureStorage:QueueName")
    ?? "stage-analysis-jobs";
builder.Services.AddSingleton(_ => new QueueClient(storageConnectionString, queueName));

// Services
builder.Services.AddScoped<ISectorService, SectorService>();
builder.Services.AddScoped<IStockService, StockService>();
builder.Services.AddScoped<IJobService, JobService>();
builder.Services.AddScoped<IIngestionService, IngestionService>();
builder.Services.AddScoped<ILookupService, LookupService>();
builder.Services.AddScoped<IScannerService, ScannerService>();
builder.Services.AddScoped<IFundamentalsService, FundamentalsService>();
builder.Services.AddScoped<IBreakoutsService, BreakoutsService>();
builder.Services.AddScoped<IMarketRegimeService, MarketRegimeService>();
builder.Services.AddHostedService<ScannerScheduleService>();
builder.Services.AddHostedService<FundamentalsScheduleService>();
builder.Services.AddHostedService<Stage2ScheduleService>();
builder.Services.AddHostedService<MarketRegimeScheduleService>();

// API
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(options =>
{
    var scheme = new OpenApiSecurityScheme
    {
        Name = "Authorization",
        Type = SecuritySchemeType.Http,
        Scheme = "bearer",
        BearerFormat = "JWT",
        In = ParameterLocation.Header,
        Description = "Paste a JWT access token (user or client-credentials).",
        Reference = new OpenApiReference { Type = ReferenceType.SecurityScheme, Id = "Bearer" }
    };
    options.AddSecurityDefinition("Bearer", scheme);
    options.AddSecurityRequirement(new OpenApiSecurityRequirement { [scheme] = Array.Empty<string>() });
});

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseDefaultFiles();
app.UseStaticFiles();

app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
// The SPA shell must load anonymously so MSAL can run and sign the user in;
// without this the global auth FallbackPolicy returns 401 for '/' and every
// client-side route. Protected data still lives behind /api/* (bearer token).
app.MapFallbackToFile("index.html").AllowAnonymous();

app.Run();

// Make Program accessible to WebApplicationFactory in test projects
public partial class Program { }
