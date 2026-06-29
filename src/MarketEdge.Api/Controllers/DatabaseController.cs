using System.Diagnostics;
using Azure.Storage.Blobs;
using Azure.Storage.Sas;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.SqlClient;

namespace MarketEdge.Api.Controllers;

// Database snapshot tooling: export the configured (prod) database to a .bacpac (schema + data)
// and import a .bacpac into a fresh local database, so the whole dataset can be moved down for
// local testing. Backed by the SqlPackage CLI. Local-admin only.
[ApiController]
[Route("api/admin/database")]
public class DatabaseController : ControllerBase
{
    private readonly IConfiguration _config;
    private readonly ILogger<DatabaseController> _logger;

    public DatabaseController(IConfiguration config, ILogger<DatabaseController> logger)
    {
        _config = config;
        _logger = logger;
    }

    private string Conn => _config.GetConnectionString("MarketEdge")
        ?? throw new InvalidOperationException("MarketEdge connection string is not configured.");

    private string? StorageConn => _config.GetValue<string>("AzureStorage:ConnectionString");
    private string BackupContainer => _config.GetValue<string>("AzureStorage:BackupContainer") ?? "db-backups";

    [HttpGet("info")]
    public IActionResult Info()
    {
        var b = new SqlConnectionStringBuilder(Conn);
        return Ok(new
        {
            server = b.DataSource,
            database = b.InitialCatalog,
            sqlPackageAvailable = FindSqlPackage() != null,
            storageConfigured = !string.IsNullOrWhiteSpace(StorageConn),
        });
    }

    // Export current DB -> .bacpac, upload to the storage account, and return a time-limited SAS download URL.
    [HttpPost("export")]
    public async Task<IActionResult> Export()
    {
        var sp = FindSqlPackage();
        if (sp == null) return Problem("SqlPackage CLI not found. Install with: dotnet tool install -g microsoft.sqlpackage");
        if (string.IsNullOrWhiteSpace(StorageConn)) return Problem("AzureStorage:ConnectionString is not configured.");

        var b = new SqlConnectionStringBuilder(Conn);
        var fileName = $"{Sanitize(b.InitialCatalog)}_{DateTime.Now:yyyyMMdd_HHmmss}.bacpac";
        var file = Path.Combine(Path.GetTempPath(), fileName);

        var args = new List<string>
        {
            "/Action:Export",
            $"/SourceServerName:{b.DataSource}",
            $"/SourceDatabaseName:{b.InitialCatalog}",
            $"/TargetFile:{file}",
            "/SourceTrustServerCertificate:True",
        };
        AddAuth(args, b, "Source");

        var (ok, log) = await RunSqlPackage(sp, args);
        if (!ok || !System.IO.File.Exists(file)) return Problem("Export failed:\n" + log);

        try
        {
            var opts = new BlobClientOptions(BlobClientOptions.ServiceVersion.V2024_08_04);
            var container = new BlobServiceClient(StorageConn, opts).GetBlobContainerClient(BackupContainer);
            await container.CreateIfNotExistsAsync();
            var blob = container.GetBlobClient(fileName);
            await using (var fs = System.IO.File.OpenRead(file)) await blob.UploadAsync(fs, overwrite: true);

            var size = new FileInfo(file).Length;
            if (!blob.CanGenerateSasUri)
                return Problem("Uploaded but cannot generate a SAS URL — storage connection string has no account key.");

            var expires = DateTimeOffset.UtcNow.AddDays(7);
            var sas = blob.GenerateSasUri(BlobSasPermissions.Read, expires);
            return Ok(new { fileName, sizeBytes = size, url = sas.ToString(), expiresUtc = expires });
        }
        finally
        {
            try { System.IO.File.Delete(file); } catch { /* best effort */ }
        }
    }

    // Import an uploaded .bacpac into a NEW local database (never overwrites the source).
    [HttpPost("import")]
    [DisableRequestSizeLimit]
    public async Task<IActionResult> Import([FromForm] IFormFile file, [FromForm] string? targetDatabase = null)
    {
        if (file == null || file.Length == 0) return BadRequest("No .bacpac uploaded.");
        var sp = FindSqlPackage();
        if (sp == null) return Problem("SqlPackage CLI not found. Install with: dotnet tool install -g microsoft.sqlpackage");

        var b = new SqlConnectionStringBuilder(Conn);
        var target = string.IsNullOrWhiteSpace(targetDatabase)
            ? $"{b.InitialCatalog}_Import_{DateTime.Now:yyyyMMdd_HHmmss}"
            : Sanitize(targetDatabase);

        var tmp = Path.Combine(Path.GetTempPath(), $"import_{Guid.NewGuid():N}.bacpac");
        await using (var fs = System.IO.File.Create(tmp)) await file.CopyToAsync(fs);

        try
        {
            var args = new List<string>
            {
                "/Action:Import",
                $"/SourceFile:{tmp}",
                $"/TargetServerName:{b.DataSource}",
                $"/TargetDatabaseName:{target}",
                "/TargetTrustServerCertificate:True",
            };
            AddAuth(args, b, "Target");
            var (ok, log) = await RunSqlPackage(sp, args);
            if (!ok) return Problem("Import failed:\n" + log);
            return Ok(new { database = target, server = b.DataSource, log });
        }
        finally
        {
            try { System.IO.File.Delete(tmp); } catch { /* best effort */ }
        }
    }

    private static void AddAuth(List<string> args, SqlConnectionStringBuilder b, string side)
    {
        if (!string.IsNullOrEmpty(b.UserID))
        {
            args.Add($"/{side}User:{b.UserID}");
            args.Add($"/{side}Password:{b.Password}");
        }
        // else integrated/Windows auth -> SqlPackage uses it by default
    }

    private async Task<(bool ok, string log)> RunSqlPackage(string exe, List<string> args)
    {
        var psi = new ProcessStartInfo
        {
            FileName = exe, RedirectStandardOutput = true, RedirectStandardError = true,
            UseShellExecute = false, CreateNoWindow = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);
        _logger.LogInformation("Running sqlpackage {Args}", string.Join(' ', args.Where(a => !a.Contains("Password"))));
        using var p = Process.Start(psi)!;
        var so = await p.StandardOutput.ReadToEndAsync();
        var se = await p.StandardError.ReadToEndAsync();
        await p.WaitForExitAsync();
        return (p.ExitCode == 0, so + se);
    }

    private static string Sanitize(string s) => new(s.Where(c => char.IsLetterOrDigit(c) || c is '_' or '-').ToArray());

    private static string? FindSqlPackage()
    {
        if (OperatingSystem.IsWindows())
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            var tool = Path.Combine(home, ".dotnet", "tools", "sqlpackage.exe");
            if (System.IO.File.Exists(tool)) return tool;
        }
        var path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var dir in path.Split(Path.PathSeparator))
        {
            foreach (var name in new[] { "sqlpackage.exe", "sqlpackage" })
            {
                try { var f = Path.Combine(dir, name); if (System.IO.File.Exists(f)) return f; } catch { }
            }
        }
        return null;
    }
}
