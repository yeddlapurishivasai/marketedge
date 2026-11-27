using System.Text.RegularExpressions;
using FluentAssertions;

namespace MarketEdge.Api.UnitTests.Services;

public class Stage4SchemaContractTests
{
    [Theory]
    [InlineData("IndianStage4AnalysisResults")]
    [InlineData("USStage4AnalysisResults")]
    public void ResultTable_ExactlyMatchesWorkerPersistenceColumns(string table)
    {
        var root = RepositoryRoot();
        var worker = File.ReadAllText(Path.Combine(root, "src", "MarketEdge.Worker", "stage4_store.py"));
        var mapping = Regex.Match(worker, @"_COLUMNS\s*=\s*\{(?<columns>.*?)\}", RegexOptions.Singleline);
        mapping.Success.Should().BeTrue();
        var workerColumns = Regex.Matches(mapping.Groups["columns"].Value, "\"(?<name>[A-Za-z0-9]+)\"\\s*:")
            .Select(m => m.Groups["name"].Value).ToList();
        workerColumns.Should().HaveCount(29);

        var sql = File.ReadAllText(Path.Combine(root, "src", "MarketEdge.Database", "Tables", table + ".sql"));
        var sqlColumns = Regex.Matches(sql,
                @"^\s*\[(?<name>\w+)\]\s+(?:INT|NVARCHAR|DECIMAL|BIT|DATE|DATETIME2)\b",
                RegexOptions.Multiline | RegexOptions.IgnoreCase)
            .Select(m => m.Groups["name"].Value).ToList();
        sqlColumns.Should().Equal(new[] { "Id" }.Concat(workerColumns).Append("CreatedAt"),
            "only the identity and default-created timestamp are managed outside the worker INSERT contract");
        sql.Should().Contain("[Id] INT IDENTITY(1,1) NOT NULL");
        sql.Should().Contain("[CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE()");
        sql.Should().Contain("UNIQUE NONCLUSTERED ([RunId], [Symbol])");
        sql.Should().Contain("[IsStage4] BIT NULL");
        sql.Should().Contain("[PriceAsOfDate] DATE NULL");
        sql.Should().Contain("[EligibilityCapturedAt] DATETIME2 NOT NULL");
        foreach (var column in new[] { "RSScore", "RSDelta1w", "RSDelta2w", "RSDelta3w",
            "MomentumScore", "ROC1w", "ROC2w", "ROC3w", "ADRatio" })
            sql.Should().Contain($"[{column}] DECIMAL(20,8) NULL");
    }

    private static string RepositoryRoot()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
            if (Directory.Exists(Path.Combine(directory.FullName, "src", "MarketEdge.Database", "Tables")))
                return directory.FullName;
        throw new DirectoryNotFoundException("Cannot locate repository source for the Stage 4 schema contract test.");
    }
}
