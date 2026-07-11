using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Observability;
using MarketEdge.Api.Services;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// OpenTelemetry file logging (OS-specific dir, daily rotation, 7-day retention)
builder.AddMarketEdgeLogging();

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
builder.Services.AddSwaggerGen();

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseDefaultFiles();
app.UseStaticFiles();

app.MapControllers();
app.MapFallbackToFile("index.html");

app.Run();

// Make Program accessible to WebApplicationFactory in test projects
public partial class Program { }
