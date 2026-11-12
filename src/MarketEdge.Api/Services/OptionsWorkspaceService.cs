using System.Data;
using System.Globalization;
using System.Text;
using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public enum OptionsForecastDeleteResult { Deleted, NotFound, Conflict }

public sealed class OptionsWorkspaceService(MarketEdgeDbContext db, QueueClient queue, OptionsBrokerService broker,
    OptionsSettings settings)
{
    public static readonly string[] Indices = ["NIFTY50", "SENSEX"];
    public static readonly string[] Modes = ["daily", "weekly", "monthly", "expiry-intraday"];
    public static void ValidateIndex(string index)
    {
        if (!Indices.Contains(index)) throw new ArgumentException("Only NIFTY50 and SENSEX are supported.");
    }

    public async Task<OptionsForecastRun> CreateAsync(string user, OptionsForecastRequest request, CancellationToken ct)
    {
        ValidateIndex(request.IndexCode);
        if (!Modes.Contains(request.Mode) || request.RequestId == Guid.Empty)
            throw new ArgumentException("A valid mode and non-empty requestId UUID are required.");
        var existing = await db.OptionsForecasts.SingleOrDefaultAsync(x => x.UserKey == user && x.RequestId == request.RequestId, ct);
        if (existing is not null)
        {
            if (existing.IndexCode != request.IndexCode || existing.Mode != request.Mode)
                throw new ArgumentException("requestId was already used with different parameters.");
            return existing;
        }
        var active = await db.OptionsForecasts.AsNoTracking().FirstOrDefaultAsync(x =>
            x.UserKey == user && x.IndexCode == request.IndexCode && x.Mode == request.Mode &&
            (x.Status == "queued" || x.Status == "running"), ct);
        if (active is not null)
            throw new ArgumentException($"Forecast #{active.Id} is already active for this index and mode. Wait for it to finish.");
        await broker.ValidateAsync(user, ct);
        var job = new JobRun { JobType = "options_analysis", Market = "india", CreatedAt = DateTime.UtcNow };
        var forecast = new OptionsForecastRun
        {
            UserKey = user, IndexCode = request.IndexCode, Mode = request.Mode,
            RequestId = request.RequestId, GeneratedAt = DateTime.UtcNow
        };
        await using var tx = await db.Database.BeginTransactionAsync(ct);
        try
        {
            db.JobRuns.Add(job);
            await db.SaveChangesAsync(ct);
            forecast.JobRunId = job.Id;
            db.OptionsForecasts.Add(forecast);
            await db.SaveChangesAsync(ct);
            job.Parameters = JsonSerializer.Serialize(new { forecastRunId = forecast.Id, userKey = user });
            await db.SaveChangesAsync(ct);
            await tx.CommitAsync(ct);
        }
        catch (DbUpdateException)
        {
            await tx.RollbackAsync(ct);
            db.ChangeTracker.Clear();
            var duplicate = await db.OptionsForecasts.SingleOrDefaultAsync(x => x.UserKey == user && x.RequestId == request.RequestId, ct);
            if (duplicate is null)
            {
                var concurrent = await db.OptionsForecasts.AsNoTracking().FirstOrDefaultAsync(x =>
                    x.UserKey == user && x.IndexCode == request.IndexCode && x.Mode == request.Mode &&
                    (x.Status == "queued" || x.Status == "running"), ct);
                if (concurrent is not null)
                    throw new ArgumentException($"Forecast #{concurrent.Id} is already active for this index and mode. Wait for it to finish.");
                throw;
            }
            if (duplicate.IndexCode != request.IndexCode || duplicate.Mode != request.Mode)
                throw new ArgumentException("requestId was already used with different parameters.");
            return duplicate;
        }
        var payload = JsonSerializer.Serialize(new
        {
            jobType = "options_analysis", market = "india", runId = job.Id, forecastRunId = forecast.Id, userKey = user
        });
        try
        {
            // Once committed, a disconnected browser must not abandon publication.
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await queue.CreateIfNotExistsAsync(cancellationToken: timeout.Token);
            await queue.SendMessageAsync(Convert.ToBase64String(Encoding.UTF8.GetBytes(payload)), cancellationToken: timeout.Token);
        }
        catch (Exception ex)
        {
            forecast.Status = job.Status = "failed";
            forecast.ErrorMessage = job.ErrorMessage = "Queue submission failed. Start a new forecast after restoring queue connectivity.";
            job.CompletedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(CancellationToken.None);
            throw new InvalidOperationException(forecast.ErrorMessage, ex);
        }
        return forecast;
    }

    public async Task<object> ListAsync(string user, string? index, string? mode, CancellationToken ct)
    {
        if (index is not null) ValidateIndex(index);
        if (mode is not null && !Modes.Contains(mode)) throw new ArgumentException("Unsupported forecast mode.");
        var rows = await db.OptionsForecasts.AsNoTracking().Where(x => x.UserKey == user &&
            (index == null || x.IndexCode == index) && (mode == null || x.Mode == mode))
            .OrderByDescending(x => x.Id).Take(100).ToListAsync(ct);
        return new { items = rows.Select(Summary) };
    }

    public static object Summary(OptionsForecastRun x) => new
    {
        x.Id, x.JobRunId, x.IndexCode, x.Mode, x.Status, generatedAt = Utc(x.GeneratedAt),
        targetAt = Utc(x.TargetAt), inputCutoffAt = Utc(x.InputCutoffAt), x.ModelVersion, x.ErrorMessage
    };

    public async Task<OptionsForecastDeleteResult> DeleteAsync(string user, int id, CancellationToken ct)
    {
        await using var tx = db.Database.IsRelational()
            ? await db.Database.BeginTransactionAsync(IsolationLevel.Serializable, ct)
            : null;
        // An exclusive parent lock also blocks the FK check on a new AI claim until deletion commits.
        var forecasts = db.Database.IsSqlServer()
            ? db.OptionsForecasts.FromSqlInterpolated(
                $"SELECT * FROM [options].[ForecastRuns] WITH (XLOCK, HOLDLOCK) WHERE [Id] = {id} AND [UserKey] = {user}")
            : db.OptionsForecasts.Where(x => x.Id == id && x.UserKey == user);
        var row = await forecasts.SingleOrDefaultAsync(ct);
        if (row is null) return OptionsForecastDeleteResult.NotFound;
        if (row.Status is not ("completed" or "failed" or "cancelled"))
            return OptionsForecastDeleteResult.Conflict;

        var ai = await db.OptionsAiSelections.SingleOrDefaultAsync(x => x.ForecastRunId == id, ct);
        if (ai is not null && ai.Status is not ("completed" or "failed" or "skipped"))
            return OptionsForecastDeleteResult.Conflict;
        if (row.JobRunId is { } jobId)
        {
            var jobs = db.Database.IsSqlServer()
                ? db.JobRuns.FromSqlInterpolated(
                    $"SELECT * FROM [dbo].[JobRuns] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = {jobId}")
                : db.JobRuns.Where(x => x.Id == jobId);
            var job = await jobs.SingleOrDefaultAsync(ct);
            if (job?.Status is "queued" or "running")
                return OptionsForecastDeleteResult.Conflict;
        }

        var strategies = db.OptionsStrategies.Where(x => x.ForecastRunId == id);
        db.OptionsStrategyOutcomes.RemoveRange(await db.OptionsStrategyOutcomes
            .Where(x => strategies.Any(s => s.Id == x.StrategySuggestionId)).ToListAsync(ct));
        db.OptionsLegs.RemoveRange(await db.OptionsLegs
            .Where(x => strategies.Any(s => s.Id == x.StrategySuggestionId)).ToListAsync(ct));
        db.OptionsForecastOutcomes.RemoveRange(await db.OptionsForecastOutcomes.Where(x => x.ForecastRunId == id).ToListAsync(ct));
        db.OptionsValuations.RemoveRange(await db.OptionsValuations.Where(x => x.ForecastRunId == id).ToListAsync(ct));
        if (ai is not null) db.OptionsAiSelections.Remove(ai);
        db.OptionsStrategies.RemoveRange(await strategies.ToListAsync(ct));
        db.OptionsForecasts.Remove(row);
        // EF orders these explicit deletes by the configured NoAction foreign keys.
        await db.SaveChangesAsync(ct);
        if (tx is not null) await tx.CommitAsync(ct);
        return OptionsForecastDeleteResult.Deleted;
    }

    public async Task<object?> DetailAsync(string user, int id, CancellationToken ct)
    {
        var row = await db.OptionsForecasts.AsNoTracking().SingleOrDefaultAsync(x => x.UserKey == user && x.Id == id, ct);
        if (row is null) return null;
        var candidates = await db.OptionsStrategies.AsNoTracking().Where(x => x.ForecastRunId == id).OrderBy(x => x.Id).ToListAsync(ct);
        var valuations = await db.OptionsValuations.AsNoTracking().Where(x => x.ForecastRunId == id).OrderBy(x => x.Id).ToListAsync(ct);
        var ai = await db.OptionsAiSelections.AsNoTracking().SingleOrDefaultAsync(x => x.ForecastRunId == id, ct);
        var outcome = await db.OptionsForecastOutcomes.AsNoTracking().Where(x => x.ForecastRunId == id)
            .OrderByDescending(x => x.Version).FirstOrDefaultAsync(ct);
        var strategyOutcomes = await db.OptionsStrategyOutcomes.AsNoTracking()
            .Where(x => db.OptionsStrategies.Any(s => s.Id == x.StrategySuggestionId && s.ForecastRunId == id))
            .OrderByDescending(x => x.Version).ToListAsync(ct);
        return new
        {
            row.Id, row.JobRunId, row.IndexCode, row.Mode, row.Status, generatedAt = Utc(row.GeneratedAt),
            targetAt = Utc(row.TargetAt), inputCutoffAt = Utc(row.InputCutoffAt), row.ModelVersion, row.ErrorMessage,
            analysis = Json(row.AnalysisJson),
            candidates = candidates.Select(x => Json(x.PayloadJson)),
            valuations = valuations.Select(x => Json(x.PayloadJson)),
            ai = new
            {
                status = ai?.Status ?? (settings.AiEnabled ? "pending" : "skipped"), selection = Json(ai?.OutputJson),
                inputTokens = ai?.InputTokens ?? 0, outputTokens = ai?.OutputTokens ?? 0,
                cachedInputTokens = ai?.CachedInputTokens ?? 0, costUsd = ai?.CostUsd,
                errorMessage = ai?.ErrorMessage ?? (ai is null && !settings.AiEnabled ? "AI selection is disabled by configuration." : null)
            },
            outcome = new { status = outcome?.Status ?? "pending", payload = Json(outcome?.PayloadJson) },
            strategyOutcomes = strategyOutcomes.GroupBy(x => x.StrategySuggestionId).Select(g => g.First()).Select(x => new
            {
                x.StrategySuggestionId, x.Version, x.Status, payload = Json(x.PayloadJson), evaluatedAt = Utc(x.EvaluatedAt)
            })
        };
    }

    public static JsonElement? Json(string? json) => json is null ? null : JsonSerializer.Deserialize<JsonElement>(json);
    private static DateTime? Utc(DateTime? date) => date.HasValue ? DateTime.SpecifyKind(date.Value, DateTimeKind.Utc) : null;

    public static void ValidateMetadata(OptionsMetadataImport request)
    {
        ValidateIndex(request.IndexCode);
        if (request.EffectiveDate == default || string.IsNullOrWhiteSpace(request.Source) || request.Source.Length > 500)
            throw new ArgumentException("An effective date and source (up to 500 characters) are required.");
        if (request.Constituents is null || request.Constituents.Count is < 1 or > 100 ||
            request.Constituents.Any(x => x is null || string.IsNullOrWhiteSpace(x.Symbol) || x.WeightPct <= 0 || x.WeightPct > 100 ||
                decimal.Round(x.WeightPct, 6) != x.WeightPct) ||
            request.Constituents.Select(x => x.Symbol.Trim().ToUpperInvariant()).Distinct().Count() != request.Constituents.Count ||
            Math.Abs(request.Constituents.Sum(x => x.WeightPct) - 100m) > 0.01m)
            throw new ArgumentException("Distinct constituents with positive six-decimal weights summing to 100% (tolerance 0.01%) are required.");
        if (request.Sessions is null || request.Sessions.Count is < 1 or > 2000 ||
            request.Sessions.Any(x => x is null) ||
            request.Sessions.Select(x => x.SessionDate).Distinct().Count() != request.Sessions.Count)
            throw new ArgumentException("Provide 1–2000 distinct authoritative session dates.");
        var exchange = request.IndexCode == "NIFTY50" ? "NSE" : "BSE";
        foreach (var session in request.Sessions)
        {
            if (session.SessionDate == default || session.Exchange != exchange)
                throw new ArgumentException("Session date and matching spot exchange (NSE/BSE) are required.");
            if (!session.IsTradingDay)
            {
                if (session.OpenTime is not null || session.CloseTime is not null || session.IsWeeklyExpiry || session.IsMonthlyExpiry)
                    throw new ArgumentException("Closed sessions cannot contain trading times or expiry flags.");
            }
            else if (!TimeOnly.TryParseExact(session.OpenTime, "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var open) ||
                !TimeOnly.TryParseExact(session.CloseTime, "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var close) || open >= close)
                throw new ArgumentException("Trading sessions require valid HH:mm IST times with open before close.");
        }
    }

    public async Task ImportAsync(OptionsMetadataImport request, CancellationToken ct)
    {
        ValidateMetadata(request);
        var symbols = request.Constituents.Select(x => x.Symbol.Trim().ToUpperInvariant()).ToArray();
        var stocks = await db.IndianStocks.Where(x => symbols.Contains(x.Symbol)).Select(x => new { x.Id, x.Symbol }).ToListAsync(ct);
        if (stocks.Count != symbols.Length) throw new ArgumentException("Every constituent must match exactly one existing Indian stock symbol.");
        if (!await db.OptionsIndices.AnyAsync(x => x.Code == request.IndexCode, ct))
            throw new InvalidOperationException("Options index definitions are missing. Publish and seed the options SQL schema.");
        await using var tx = await db.Database.BeginTransactionAsync(ct);
        var oldWeights = await db.OptionsConstituents.Where(x => x.IndexCode == request.IndexCode && x.EffectiveDate == request.EffectiveDate).ToListAsync(ct);
        db.OptionsConstituents.RemoveRange(oldWeights);
        var dates = request.Sessions.Select(x => x.SessionDate).ToArray();
        db.OptionsSessions.RemoveRange(await db.OptionsSessions.Where(x => x.IndexCode == request.IndexCode && dates.Contains(x.SessionDate)).ToListAsync(ct));
        await db.SaveChangesAsync(ct);
        db.OptionsConstituents.AddRange(request.Constituents.Select(x => new IndexConstituentHistory
        {
            IndexCode = request.IndexCode, EffectiveDate = request.EffectiveDate, Source = request.Source,
            StockId = stocks.Single(s => s.Symbol.Equals(x.Symbol.Trim(), StringComparison.OrdinalIgnoreCase)).Id, WeightPct = x.WeightPct
        }));
        db.OptionsSessions.AddRange(request.Sessions.Select(x => new OptionsExchangeSession
        {
            IndexCode = request.IndexCode, SessionDate = x.SessionDate, Exchange = x.Exchange,
            IsTradingDay = x.IsTradingDay, IsWeeklyExpiry = x.IsWeeklyExpiry, IsMonthlyExpiry = x.IsMonthlyExpiry,
            OpenTime = x.OpenTime is null ? null : TimeOnly.ParseExact(x.OpenTime, "HH:mm", CultureInfo.InvariantCulture),
            CloseTime = x.CloseTime is null ? null : TimeOnly.ParseExact(x.CloseTime, "HH:mm", CultureInfo.InvariantCulture), Source = request.Source
        }));
        await db.SaveChangesAsync(ct);
        await tx.CommitAsync(ct);
    }

    public async Task<object> MetadataAsync(CancellationToken ct)
    {
        var indices = await db.OptionsIndices.AsNoTracking().ToListAsync(ct);
        if (indices.Count != 2) throw new InvalidOperationException("Options index definitions are missing. Publish and seed the options SQL schema.");
        var weights = await db.OptionsConstituents.GroupBy(x => x.IndexCode)
            .Select(g => new { indexCode = g.Key, effectiveDate = g.Max(x => x.EffectiveDate) }).ToListAsync(ct);
        var coverage = await db.OptionsSessions.GroupBy(x => x.IndexCode)
            .Select(g => new { indexCode = g.Key, from = g.Min(x => x.SessionDate), to = g.Max(x => x.SessionDate), count = g.Count() }).ToListAsync(ct);
        var firstDate = DateOnly.FromDateTime(DateTime.UtcNow.AddDays(-1));
        var lastDate = firstDate.AddDays(61);
        var sessions = await db.OptionsSessions.AsNoTracking().Where(x => x.SessionDate >= firstDate && x.SessionDate <= lastDate)
            .OrderBy(x => x.SessionDate).ThenBy(x => x.IndexCode)
            .Select(x => new
            {
                x.IndexCode, x.SessionDate, x.Exchange, x.IsTradingDay, x.OpenTime, x.CloseTime,
                x.IsWeeklyExpiry, x.IsMonthlyExpiry, x.Source
            }).ToListAsync(ct);
        return new { indices, constituentDates = weights, calendarCoverage = coverage, sessions };
    }
}
