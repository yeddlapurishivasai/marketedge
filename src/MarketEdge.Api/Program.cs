using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Services;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

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
