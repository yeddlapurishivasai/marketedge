using System.Diagnostics;
using System.Text;
using System.Text.Json;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public record TriggerIngestionRequest(bool TestSample = false, int? Limit = null);

public interface IIngestionService
{
    Task<int> TriggerAsync(string market, TriggerIngestionRequest request);
}

/// <summary>
/// Runs the Python data-ingestion CLI (<c>src/MarketEdge.Ingestion</c>) out-of-process and
/// tracks each invocation as a <c>data_ingestion</c> <see cref="JobRun"/>. The HTTP request
/// returns as soon as the run row exists; the process runs on a background task that updates
/// the row's terminal state.
///
/// A single trigger runs the whole pipeline in order: <c>ingest bars</c> (which also seeds
/// the ticker universe), then <c>ingest technical</c>, then <c>ingest fundamentals</c>.
/// </summary>
public class IngestionService : IIngestionService
{
    public const string JobType = "data_ingestion";

    // Pipeline stage -> CLI argument list (market + shared flags appended at runtime).
    // The bars stage seeds the ticker universe internally, so there is no separate seed step.
    private static readonly (string Name, string[] Args)[] Pipeline =
    {
        ("bars", new[] { "ingest", "bars" }),
        ("technical", new[] { "ingest", "technical" }),
        ("fundamentals", new[] { "ingest", "fundamentals" }),
    };

    private readonly MarketEdgeDbContext _db;
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly IConfiguration _config;
    private readonly IWebHostEnvironment _env;
    private readonly ILogger<IngestionService> _logger;

    public IngestionService(
        MarketEdgeDbContext db,
        IServiceScopeFactory scopeFactory,
        IConfiguration config,
        IWebHostEnvironment env,
        ILogger<IngestionService> logger)
    {
        _db = db;
        _scopeFactory = scopeFactory;
        _config = config;
        _env = env;
        _logger = logger;
    }

    public async Task<int> TriggerAsync(string market, TriggerIngestionRequest request)
    {
        if (market != "india" && market != "us")
            throw new ArgumentException("Market must be 'india' or 'us'.");

        if (request.Limit is < 1)
            throw new ArgumentException("Limit must be a positive integer.");

        // One in-flight run per market: return the existing run if present.
        var existing = await _db.JobRuns
            .Where(j => j.JobType == JobType && j.Market == market && j.Status == "running")
            .OrderByDescending(j => j.CreatedAt)
            .FirstOrDefaultAsync();
        if (existing != null)
            return existing.Id;

        var parameters = new Dictionary<string, object?>
        {
            ["market"] = market,
            ["testSample"] = request.TestSample,
        };
        if (request.Limit is int lim) parameters["limit"] = lim;

        var now = DateTime.UtcNow;
        var job = new JobRun
        {
            JobType = JobType,
            Market = market,
            WeekNumber = GetIsoWeekNumber(now),
            Status = "running",
            Progress = 0,
            Parameters = JsonSerializer.Serialize(parameters),
            CreatedAt = now,
            StartedAt = now,
        };
        _db.JobRuns.Add(job);
        await _db.SaveChangesAsync();

        // Snapshot primitives for the background task (don't capture the scoped DbContext).
        var runId = job.Id;
        var testSample = request.TestSample;
        var limit = request.Limit;
        _ = Task.Run(() => RunPipelineAsync(runId, market, testSample, limit));

        return runId;
    }

    private async Task RunPipelineAsync(int runId, string market, bool testSample, int? limit)
    {
        var output = new StringBuilder();
        var startedAt = DateTime.UtcNow;
        var failed = false;

        for (var i = 0; i < Pipeline.Length; i++)
        {
            var (name, args) = Pipeline[i];
            output.AppendLine($"=== {name} ({market}) ===");
            int exitCode;
            try
            {
                exitCode = await RunProcessAsync(market, args, testSample, limit, output);
            }
            catch (Exception ex)
            {
                output.AppendLine($"[launch error] {ex.Message}");
                exitCode = -1;
            }

            await UpdateProgressAsync(runId, (int)Math.Round((i + 1) * 100.0 / Pipeline.Length));

            if (exitCode != 0)
            {
                failed = true;
                output.AppendLine($"[step '{name}' exited {exitCode}]");
                break;
            }
        }

        await FinalizeAsync(runId, failed, startedAt, output.ToString());
    }

    private async Task<int> RunProcessAsync(string market, string[] stepArgs, bool testSample, int? limit, StringBuilder output)
    {
        var (python, workingDir) = ResolvePaths();
        var args = new List<string> { "cli.py" };
        args.AddRange(stepArgs);
        args.Add("--market");
        args.Add(market);
        if (testSample) args.Add("--test-sample");
        if (limit is int n) { args.Add("--limit"); args.Add(n.ToString()); }

        var psi = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = workingDir,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);

        _logger.LogInformation("Starting ingestion: {Python} {Args} (cwd {Cwd})",
            python, string.Join(' ', args), workingDir);

        using var proc = new Process { StartInfo = psi };
        var sink = new object();
        proc.OutputDataReceived += (_, e) => { if (e.Data != null) lock (sink) output.AppendLine(e.Data); };
        proc.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (sink) output.AppendLine(e.Data); };
        proc.Start();
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        await proc.WaitForExitAsync();
        return proc.ExitCode;
    }

    private (string python, string workingDir) ResolvePaths()
    {
        // Content root is .../src/MarketEdge.Api; repo root is two levels up.
        var repoRoot = Path.GetFullPath(Path.Combine(_env.ContentRootPath, "..", ".."));

        var python = _config.GetValue<string>("Ingestion:PythonPath");
        if (string.IsNullOrWhiteSpace(python))
        {
            python = OperatingSystem.IsWindows()
                ? Path.Combine(repoRoot, ".local", "worker-venv", "Scripts", "python.exe")
                : Path.Combine(repoRoot, ".local", "worker-venv", "bin", "python");
        }
        if (!File.Exists(python))
            python = OperatingSystem.IsWindows() ? "python" : "python3"; // PATH fallback

        var workingDir = _config.GetValue<string>("Ingestion:WorkingDirectory");
        if (string.IsNullOrWhiteSpace(workingDir))
            workingDir = Path.Combine(repoRoot, "src", "MarketEdge.Ingestion");

        return (python, workingDir);
    }

    private async Task UpdateProgressAsync(int runId, int progress)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var job = await db.JobRuns.FindAsync(runId);
        if (job == null || job.Status != "running") return;
        job.Progress = Math.Clamp(progress, 0, 100);
        await db.SaveChangesAsync();
    }

    private async Task FinalizeAsync(int runId, bool failed, DateTime startedAt, string output)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MarketEdgeDbContext>();
        var job = await db.JobRuns.FindAsync(runId);
        if (job == null) return;

        var completedAt = DateTime.UtcNow;
        job.Status = failed ? "failed" : "completed";
        job.Progress = failed ? job.Progress : 100;
        job.CompletedAt = completedAt;
        job.Metrics = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["durationSeconds"] = Math.Round((completedAt - startedAt).TotalSeconds, 1),
        });
        // Keep the last ~4000 chars of output for diagnostics.
        var tail = output.Length > 4000 ? output[^4000..] : output;
        if (failed) job.ErrorMessage = tail;
        else job.ErrorMessage = null;

        await db.SaveChangesAsync();
        _logger.LogInformation("Ingestion run {RunId} {Status}", runId, job.Status);
    }

    private static string GetIsoWeekNumber(DateTime date)
    {
        var cal = System.Globalization.CultureInfo.InvariantCulture.Calendar;
        var week = cal.GetWeekOfYear(date, System.Globalization.CalendarWeekRule.FirstFourDayWeek, DayOfWeek.Monday);
        var year = date.Year;
        if (week >= 52 && date.Month == 1) year--;
        if (week == 1 && date.Month == 12) year++;
        return $"{year}-W{week:D2}";
    }
}
