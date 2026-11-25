using System.Security.Cryptography;
using System.Globalization;
using System.Data;
using System.Text;
using System.Text.Json;
using Azure.Storage.Queues;
using MarketEdge.Api.Data;
using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Services;

public sealed class StrangleService(MarketEdgeDbContext db, QueueClient queue, OptionsBrokerService broker)
{
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    public static DateOnly Today => DateOnly.FromDateTime(DateTime.UtcNow.AddMinutes(330));
    public static void Validate(StrangleRunRequest request)
    {
        OptionsWorkspaceService.ValidateIndex(request.IndexCode);
        if (request.RequestId == Guid.Empty || request.Action is not ("entry" or "adjustment") ||
            request.WingWidth <= 0 || request.WingWidth > 100000 || request.XOverride is <= 0 or > 100000 ||
            request.Action == "entry" && request.StrategyId is not null ||
            request.StrategyId is <= 0)
            throw new ArgumentException("Supply a requestId, supported action, positive bounded wingWidth/X and an optional strategyId only for adjustments.");
        if (request.RiskFreeRate is < -0.05m or > 1m || request.DividendYield is < 0 or > 1m)
            throw new ArgumentException("Model interest rate must be between -5% and 100%, and dividend yield between 0% and 100%, expressed as annualized decimals.");
        if (request.SelectedTradeIds is { } tradeIds && (request.Action != "adjustment" || request.StrategyId is null || tradeIds.Count is < 1 or > 200 ||
            tradeIds.Any(x => string.IsNullOrWhiteSpace(x) || x.Length > 100 || x.Any(char.IsControl) || x != x.Trim()) ||
            tradeIds.Distinct(StringComparer.Ordinal).Count() != tradeIds.Count))
            throw new ArgumentException("selectedTradeIds requires 1–200 distinct broker fill IDs on an adjustment only.");
    }

    public async Task<StrangleRunDto> CreateAsync(string user, StrangleRunRequest request, CancellationToken ct)
    {
        Validate(request);
        if (request.SelectedTradeIds is { } ids)
            request = request with { SelectedTradeIds = ids.Order(StringComparer.Ordinal).ToList() };
        var requestJson = JsonSerializer.Serialize(request, Json);
        var existing = await db.StrangleRuns.AsNoTracking().SingleOrDefaultAsync(x => x.UserKey == user && x.RequestId == request.RequestId, ct);
        if (existing is not null) return Retry(existing, requestJson);
        if (request.StrategyId is { } strategyId && !await db.StrangleStrategies.AnyAsync(x =>
                x.Id == strategyId && x.UserKey == user && x.IndexCode == request.IndexCode && x.SessionDate == Today, ct))
            throw new ArgumentException("A confirmed same-session strategy for this account and index is required.");
        await EnsureNoActiveAsync(user, request, ct);
        await broker.ValidateAsync(user, ct);
        var job = new JobRun { JobType = "options_strangle", Market = "india", CreatedAt = DateTime.UtcNow };
        var run = new StrangleRun
        {
            UserKey = user, RequestId = request.RequestId, IndexCode = request.IndexCode, Action = request.Action,
            StrategyId = request.StrategyId, RequestJson = requestJson, GeneratedAt = DateTime.UtcNow
        };
        await using var tx = db.Database.IsRelational()
            ? await db.Database.BeginTransactionAsync(request.SelectedTradeIds is null ? IsolationLevel.ReadCommitted : IsolationLevel.Serializable, ct) : null;
        try
        {
            if (request.SelectedTradeIds is { } selectedIds)
                await ValidateTradeClaimsAsync(user, request.StrategyId!.Value, selectedIds, ct);
            db.JobRuns.Add(job);
            await db.SaveChangesAsync(ct);
            run.JobRunId = job.Id;
            db.StrangleRuns.Add(run);
            await db.SaveChangesAsync(ct);
            job.Parameters = JsonSerializer.Serialize(new { strangleRunId = run.Id, userKey = user }, Json);
            await db.SaveChangesAsync(ct);
            if (tx is not null) await tx.CommitAsync(ct);
        }
        catch (DbUpdateException)
        {
            if (tx is not null) await tx.RollbackAsync(CancellationToken.None);
            db.ChangeTracker.Clear();
            existing = await db.StrangleRuns.AsNoTracking().SingleOrDefaultAsync(x => x.UserKey == user && x.RequestId == request.RequestId, ct);
            if (existing is not null) return Retry(existing, requestJson);
            await EnsureNoActiveAsync(user, request, ct);
            throw;
        }
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await queue.CreateIfNotExistsAsync(cancellationToken: timeout.Token);
            var payload = JsonSerializer.Serialize(new { jobType = "options_strangle", market = "india", runId = job.Id, strangleRunId = run.Id, userKey = user }, Json);
            await queue.SendMessageAsync(Convert.ToBase64String(Encoding.UTF8.GetBytes(payload)), cancellationToken: timeout.Token);
        }
        catch (Exception ex)
        {
            if (db.Database.IsRelational())
            {
                await using var failureTx = await db.Database.BeginTransactionAsync(CancellationToken.None);
                await LockJobAsync(job.Id, CancellationToken.None);
                var failedAt = DateTime.UtcNow;
                const string error = "Queue publication failed; create a new request after restoring queue connectivity.";
                var changed = await db.JobRuns.Where(x => x.Id == job.Id && x.Status == "queued")
                    .ExecuteUpdateAsync(s => s.SetProperty(x => x.Status, "failed")
                        .SetProperty(x => x.CompletedAt, failedAt).SetProperty(x => x.ErrorMessage, error), CancellationToken.None);
                if (changed > 0)
                    await db.StrangleRuns.Where(x => x.Id == run.Id && x.Status == "queued")
                        .ExecuteUpdateAsync(s => s.SetProperty(x => x.Status, "failed")
                            .SetProperty(x => x.CompletedAt, failedAt).SetProperty(x => x.ErrorMessage, error), CancellationToken.None);
                await failureTx.CommitAsync(CancellationToken.None);
                // A failed acknowledgement may follow successful delivery. Never overwrite a worker claim.
                if (changed == 0)
                    return Dto(await db.StrangleRuns.AsNoTracking().SingleAsync(x => x.Id == run.Id, CancellationToken.None));
                throw new InvalidOperationException(error, ex);
            }
            run.Status = job.Status = "failed";
            run.CompletedAt = job.CompletedAt = DateTime.UtcNow;
            run.ErrorMessage = job.ErrorMessage = "Queue publication failed; create a new request after restoring queue connectivity.";
            await db.SaveChangesAsync(CancellationToken.None);
            throw new InvalidOperationException(run.ErrorMessage, ex);
        }
        return Dto(run);
    }

    private async Task ValidateTradeClaimsAsync(string user, int strategyId, IReadOnlyList<string> selectedIds, CancellationToken ct)
    {
        var start = Today.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc).AddMinutes(-330);
        var end = start.AddDays(1);
        var history = db.Database.IsSqlServer()
            ? await db.StrangleRuns.FromSqlInterpolated(
                    $"SELECT * FROM [options].[StrangleRuns] WITH (UPDLOCK, HOLDLOCK, INDEX(IX_StrangleRuns_UserHistory)) WHERE [UserKey] = {user} AND [GeneratedAt] >= {start} AND [GeneratedAt] < {end}")
                .AsNoTracking().ToListAsync(ct)
            : await db.StrangleRuns.AsNoTracking().Where(x => x.UserKey == user && x.GeneratedAt >= start && x.GeneratedAt < end).ToListAsync(ct);
        var selected = selectedIds.ToHashSet(StringComparer.Ordinal);
        if (history.Where(x => x.StrategyId != null && x.StrategyId != strategyId).Any(x =>
                (JsonSerializer.Deserialize<StrangleRunRequest>(x.RequestJson, Json)?.SelectedTradeIds ?? []).Any(selected.Contains)))
            throw new ArgumentException("Selected broker fill IDs were already claimed by another strategy in this account/session. Ambiguous reassignment is unavailable.");
    }

    private async Task EnsureNoActiveAsync(string user, StrangleRunRequest request, CancellationToken ct)
    {
        if (await db.StrangleRuns.AnyAsync(x => x.UserKey == user && x.IndexCode == request.IndexCode && x.Action == request.Action &&
                (x.Status == "queued" || x.Status == "running"), ct))
            throw new ArgumentException("An active run already exists for this account, index and action.");
    }

    private static StrangleRunDto Retry(StrangleRun run, string json)
    {
        var stored = JsonSerializer.Deserialize<StrangleRunRequest>(run.RequestJson, Json)!;
        var requested = JsonSerializer.Deserialize<StrangleRunRequest>(json, Json)!;
        if (stored with { SelectedTradeIds = null } != requested with { SelectedTradeIds = null } ||
            !(stored.SelectedTradeIds ?? []).Order(StringComparer.Ordinal).SequenceEqual(
                (requested.SelectedTradeIds ?? []).Order(StringComparer.Ordinal), StringComparer.Ordinal))
            throw new ArgumentException("requestId was already used with different parameters.");
        return Dto(run);
    }
    public static JsonElement? Parse(string? json) => json is null ? null : JsonSerializer.Deserialize<JsonElement>(json);
    public static bool Qualified(JsonElement result)
    {
        if (result.TryGetProperty("eligible", out var qualification) && qualification.ValueKind != JsonValueKind.True) return false;
        if (!result.TryGetProperty("blockers", out var blockers) ||
            blockers.ValueKind != JsonValueKind.Array || blockers.GetArrayLength() > 0) return false;
        if (result.TryGetProperty("executionStatus", out var status))
            return status.ValueKind == JsonValueKind.String && status.GetString() == "advisory-only";
        return false;
    }

    private static int? SignedProposalQuantity(JsonElement leg)
    {
        if (leg.TryGetProperty("signedQuantity", out var signed) && signed.ValueKind == JsonValueKind.Number &&
            signed.TryGetInt32(out var signedValue)) return signedValue;
        if (!leg.TryGetProperty("quantity", out var quantity) || quantity.ValueKind != JsonValueKind.Number ||
            !quantity.TryGetInt32(out var value)) return null;
        if (leg.TryGetProperty("direction", out var direction))
            return direction.GetString() switch { "sell" => -Math.Abs(value), "buy" => Math.Abs(value), _ => null };
        return value;
    }
    public static StrangleRunDto Dto(StrangleRun run) => new(run.Id, run.JobRunId, run.IndexCode, run.Action, run.Status,
        DateTime.SpecifyKind(run.GeneratedAt, DateTimeKind.Utc), run.ErrorMessage, Parse(run.ResultJson),
        Parse(run.AiJson ?? (run.Action == "adjustment" && run.Status == "completed" ? "{\"status\":\"pending\"}" : null)));
    public async Task<object> ListAsync(string user, string? index, CancellationToken ct, int limit = 100)
    {
        if (limit is < 1 or > 100) throw new ArgumentException("Run limit must be between 1 and 100.");
        if (index is not null) OptionsWorkspaceService.ValidateIndex(index);
        var rows = await db.StrangleRuns.AsNoTracking().Where(x => x.UserKey == user && (index == null || x.IndexCode == index))
            .OrderByDescending(x => x.Id).Take(limit).ToListAsync(ct);
        return new { items = rows.Select(Dto) };
    }
    public async Task<StrangleRunDto?> DetailAsync(string user, int id, CancellationToken ct)
    {
        var row = await db.StrangleRuns.AsNoTracking().SingleOrDefaultAsync(x => x.UserKey == user && x.Id == id, ct);
        return row is null ? null : Dto(row);
    }
    public static object StrategyDto(StrangleStrategy row) => new { row.Id, row.EntryRunId, row.IndexCode, row.SessionDate, baseline = Parse(row.BaselineJson), row.CreatedAt };
    public async Task<object> StrategiesAsync(string user, CancellationToken ct) => new
    {
        items = (await db.StrangleStrategies.AsNoTracking().Where(x => x.UserKey == user).OrderByDescending(x => x.Id).Take(100).ToListAsync(ct)).Select(StrategyDto)
    };
    public async Task<object?> StrategyAsync(string user, int id, CancellationToken ct)
    {
        var row = await db.StrangleStrategies.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id && x.UserKey == user, ct);
        return row is null ? null : StrategyDto(row);
    }

    public async Task<object> ConfirmAsync(string user, StrangleConfirmRequest request, CancellationToken ct)
    {
        if (request.EntryRunId <= 0 || request.XPoints <= 0 || request.XPoints > 100000 ||
            request.Legs is not { Count: 4 } || request.Legs.Any(x => x is null || string.IsNullOrWhiteSpace(x.Exchange) ||
                string.IsNullOrWhiteSpace(x.TradingSymbol) || x.Product is not ("MIS" or "NRML") || x.Quantity == 0 ||
                x.Exchange.Length > 10 || x.TradingSymbol.Length > 100 || x.Quantity is < -100000000 or > 100000000 ||
                x.EntryPrice is <= 0 or > 10000000) ||
            request.Legs.Select(x => (x.Exchange, x.TradingSymbol, x.Product)).Distinct().Count() != 4 ||
            request.Legs.Select(x => x.Product).Distinct().Count() != 1)
            throw new ArgumentException("Confirm four distinct, matched signed legs with actual entry prices and positive X.");
        var run = await db.StrangleRuns.AsNoTracking().SingleOrDefaultAsync(x => x.Id == request.EntryRunId && x.UserKey == user, ct);
        if (run is null || run.Action != "entry" || run.Status != "completed" || run.ResultJson is null)
            throw new ArgumentException("A completed entry run owned by this account is required.");
        var result = JsonSerializer.Deserialize<JsonElement>(run.ResultJson);
        DateOnly date = default;
        if (result.TryGetProperty("asOf", out var asOf) && asOf.ValueKind == JsonValueKind.String &&
                 DateTimeOffset.TryParse(asOf.GetString(), out var capture))
            date = DateOnly.FromDateTime(capture.ToOffset(TimeSpan.FromMinutes(330)).DateTime);
        if (!Qualified(result) || date != Today)
            throw new ArgumentException("Only a qualified current-session entry can be confirmed.");
        if (!result.TryGetProperty("legs", out var proposedLegs) || proposedLegs.ValueKind != JsonValueKind.Array)
            throw new ArgumentException("The entry proposal has no confirmable legs.");
        var proposed = proposedLegs.EnumerateArray().ToArray();
        if (proposed.Length != 4 || request.Legs.Any(leg => !proposed.Any(p =>
                p.TryGetProperty("exchange", out var exchange) && exchange.ValueKind == JsonValueKind.String && exchange.GetString() == leg.Exchange &&
                p.TryGetProperty("tradingSymbol", out var symbol) && symbol.ValueKind == JsonValueKind.String && symbol.GetString() == leg.TradingSymbol &&
                SignedProposalQuantity(p) is { } proposedQuantity && proposedQuantity != 0 &&
                Math.Sign(proposedQuantity) == Math.Sign(leg.Quantity))))
            throw new ArgumentException("Confirmed contracts and leg directions must match the entry proposal.");
        var quantity = request.Legs.Select(x => Math.Abs(x.Quantity)).Distinct().ToArray();
        if (quantity.Length != 1 || request.Legs.Count(x => x.Quantity > 0) != 2)
            throw new ArgumentException("A matched four-leg hedged position is required.");
        if (!result.TryGetProperty("lotSize", out var proposalLot) || proposalLot.ValueKind != JsonValueKind.Number ||
            !proposalLot.TryGetInt32(out var lotSize) || lotSize <= 0 || quantity[0] % lotSize != 0)
            throw new ArgumentException("Actual matched quantity must be a positive complete-lot multiple of the entry lot size.");
        var instruments = new List<(StrangleBaselineLeg Leg, OptionsDerivativeInstrument Instrument)>();
        foreach (var leg in request.Legs)
        {
            var instrument = await db.OptionsInstruments.AsNoTracking().SingleOrDefaultAsync(x => x.Exchange == leg.Exchange && x.TradingSymbol == leg.TradingSymbol, ct);
            if (instrument is null || instrument.Expiry < date || instrument.Expiry is null ||
                instrument.LotSize != lotSize ||
                instrument.Underlying != run.IndexCode ||
                instrument.InstrumentType is not ("CE" or "PE"))
                throw new ArgumentException("Confirmed legs require valid listed index option metadata.");
            instruments.Add((leg, instrument));
        }
        if (instruments.Select(x => x.Instrument.Expiry).Distinct().Count() != 1 ||
            instruments.Select(x => x.Leg.Exchange).Distinct().Count() != 1)
            throw new ArgumentException("All legs must share an exchange and expiry.");
        decimal width = 0;
        foreach (var type in new[] { "CE", "PE" })
        {
            var pair = instruments.Where(x => x.Instrument.InstrumentType == type).ToArray();
            if (pair.Length != 2 || pair.Count(x => x.Leg.Quantity > 0) != 1)
                throw new ArgumentException("Each short must have one protective option.");
            var shortStrike = pair.Single(x => x.Leg.Quantity < 0).Instrument.Strike;
            var longStrike = pair.Single(x => x.Leg.Quantity > 0).Instrument.Strike;
            if (type == "CE" ? longStrike <= shortStrike : longStrike >= shortStrike)
                throw new ArgumentException("Protection must lie outside the shorts.");
            width = Math.Max(width, Math.Abs(longStrike - shortStrike));
        }
        var credit = -request.Legs.Sum(x => x.Quantity * x.EntryPrice);
        if (instruments.Single(x => x.Instrument.InstrumentType == "PE" && x.Leg.Quantity < 0).Instrument.Strike >=
            instruments.Single(x => x.Instrument.InstrumentType == "CE" && x.Leg.Quantity < 0).Instrument.Strike)
            throw new ArgumentException("The short put must lie below the short call.");
        var maxLoss = width * quantity[0] - credit;
        var budget = request.XPoints * quantity[0];
        if (credit <= 0 || maxLoss <= 0 || budget > maxLoss || 2 * budget > credit)
            throw new ArgumentException("X must fit the actual risk budget and attainable gross profit cap.");
        var sorted = request.Legs.OrderBy(x => x.Exchange, StringComparer.Ordinal).ThenBy(x => x.TradingSymbol, StringComparer.Ordinal).ToArray();
        var fingerprint = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new
        {
            legs = sorted.Select(x => new { x.Exchange, x.TradingSymbol, x.Product, x.Quantity, entryPrice = x.EntryPrice.ToString("G29", CultureInfo.InvariantCulture) }),
            xPoints = request.XPoints.ToString("G29", CultureInfo.InvariantCulture), sessionDate = date
        }, Json)))).ToLowerInvariant();
        await using var tx = db.Database.IsRelational()
            ? await db.Database.BeginTransactionAsync(IsolationLevel.Serializable, ct) : null;
        // Lock the entire account/index/session range, including the empty range, before overlap checks.
        var sessionBaselines = db.Database.IsSqlServer()
            ? await db.StrangleStrategies.FromSqlInterpolated(
                $"SELECT * FROM [options].[StrangleStrategies] WITH (UPDLOCK, HOLDLOCK, INDEX(IX_StrangleStrategies_Session)) WHERE [UserKey] = {user} AND [IndexCode] = {run.IndexCode} AND [SessionDate] = {date}")
                .ToListAsync(ct)
            : await db.StrangleStrategies.Where(x => x.UserKey == user && x.IndexCode == run.IndexCode && x.SessionDate == date).ToListAsync(ct);
        var existing = sessionBaselines.SingleOrDefault(x => x.EntryRunId == run.Id);
        if (existing is not null) return ConfirmRetry(existing, fingerprint);
        var identities = sorted.Select(x => (x.Exchange, x.TradingSymbol, x.Product)).ToHashSet();
        if (sessionBaselines.Any(x => JsonSerializer.Deserialize<JsonElement>(x.BaselineJson).GetProperty("legs")
                .EnumerateArray().Any(leg => identities.Contains((
                    leg.GetProperty("exchange").GetString()!, leg.GetProperty("tradingSymbol").GetString()!,
                    leg.GetProperty("product").GetString()!)))))
            throw new ArgumentException("These session contracts already belong to an immutable baseline. Rebaselining overlapping exposure, including re-entry without unique fill attribution, is unavailable.");
        var row = new StrangleStrategy { UserKey = user, EntryRunId = run.Id, IndexCode = run.IndexCode, SessionDate = date, CreatedAt = DateTime.UtcNow };
        row.BaselineJson = JsonSerializer.Serialize(new
        {
            baselineId = Guid.NewGuid(), sessionDate = date, legs = sorted, xPoints = request.XPoints,
            originalRiskBudget = budget, originalMaxLoss = maxLoss, quantity = quantity[0], fingerprint,
            positionsConfirmed = false, formulaVersion = "strangle-baseline.v1"
        }, Json);
        db.StrangleStrategies.Add(row);
        try
        {
            await db.SaveChangesAsync(ct);
            if (tx is not null) await tx.CommitAsync(ct);
        }
        catch (DbUpdateException)
        {
            if (tx is not null) await tx.RollbackAsync(CancellationToken.None);
            db.Entry(row).State = EntityState.Detached;
            existing = await db.StrangleStrategies.SingleOrDefaultAsync(x => x.UserKey == user && x.EntryRunId == run.Id, ct);
            if (existing is null) throw;
            return ConfirmRetry(existing, fingerprint);
        }
        return StrategyDto(row);
    }
    private static object ConfirmRetry(StrangleStrategy row, string fingerprint) =>
        JsonSerializer.Deserialize<JsonElement>(row.BaselineJson).GetProperty("fingerprint").GetString() == fingerprint
            ? StrategyDto(row) : throw new ArgumentException("This entry already has an immutable baseline; it cannot be reset.");

    public async Task ReconcileInterruptedJobsAsync(CancellationToken ct)
    {
        var queuedCutoff = DateTime.UtcNow.AddMinutes(-5);
        var runningCutoff = DateTime.UtcNow.AddMinutes(-15);
        var rows = await db.StrangleRuns.AsNoTracking().Where(x => (x.Status == "queued" || x.Status == "running") &&
                db.JobRuns.Any(j => j.Id == x.JobRunId && (j.Status == "failed" || j.Status == "cancelled" ||
                    j.Status == "queued" && j.CreatedAt < queuedCutoff || j.Status == "running" && j.StartedAt < runningCutoff)))
            .OrderBy(x => x.Id).Take(100).ToListAsync(ct);
        foreach (var row in rows)
        {
            await using var tx = db.Database.IsRelational() ? await db.Database.BeginTransactionAsync(ct) : null;
            var job = await LockJobAsync(row.JobRunId, ct);
            var status = job.Status == "cancelled" ? "cancelled" : "failed";
            var now = DateTime.UtcNow;
            const string error = "Job cancelled, interrupted or not delivered before its intraday timeout. Start a fresh request.";
            if (db.Database.IsRelational())
            {
                var owned = await db.JobRuns.Where(x => x.Id == job.Id && (x.Status == "failed" || x.Status == "cancelled" ||
                        x.Status == "queued" && x.CreatedAt < queuedCutoff || x.Status == "running" && x.StartedAt < runningCutoff))
                    .ExecuteUpdateAsync(s => s.SetProperty(x => x.Status, status).SetProperty(x => x.CompletedAt, now)
                        .SetProperty(x => x.ErrorMessage, error), ct);
                if (owned > 0)
                    await db.StrangleRuns.Where(x => x.Id == row.Id && (x.Status == "queued" || x.Status == "running"))
                        .ExecuteUpdateAsync(s => s.SetProperty(x => x.Status, status).SetProperty(x => x.CompletedAt, now)
                            .SetProperty(x => x.ErrorMessage, error), ct);
                await tx!.CommitAsync(ct);
            }
            else
            {
                var trackedJob = await db.JobRuns.SingleAsync(x => x.Id == job.Id, ct);
                var trackedRun = await db.StrangleRuns.SingleAsync(x => x.Id == row.Id, ct);
                trackedJob.Status = trackedRun.Status = status;
                trackedJob.CompletedAt = trackedRun.CompletedAt = now;
                trackedJob.ErrorMessage = trackedRun.ErrorMessage = error;
                await db.SaveChangesAsync(ct);
            }
        }

    }

    private Task<JobRun> LockJobAsync(int id, CancellationToken ct) => db.Database.IsSqlServer()
        ? db.JobRuns.FromSqlInterpolated(
                $"SELECT * FROM [dbo].[JobRuns] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = {id}")
            .AsNoTracking().SingleAsync(ct)
        : db.JobRuns.AsNoTracking().SingleAsync(x => x.Id == id, ct);
}
