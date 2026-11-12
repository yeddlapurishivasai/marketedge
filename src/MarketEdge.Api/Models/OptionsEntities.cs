using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Models;

[Table("BrokerConnections", Schema = "options")]
[Index(nameof(UserKey), IsUnique = true)]
public sealed class BrokerConnection
{
    public int Id { get; set; }
    [MaxLength(200)] public string UserKey { get; set; } = "";
    public string? BrokerUserId { get; set; }
    public string? EncryptedToken { get; set; }
    public DateTime? LoginAt { get; set; }
    public DateTime? ExpiresAt { get; set; }
    public string Status { get; set; } = "disconnected";
    public string? LastError { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("MarketIndices", Schema = "options")]
public sealed class OptionsMarketIndex
{
    [Key] public string Code { get; set; } = "";
    public string Name { get; set; } = "";
    public string SpotSymbol { get; set; } = "";
    public string DerivativeExchange { get; set; } = "";
}

[Table("IndexConstituentHistory", Schema = "options")]
[Index(nameof(IndexCode), nameof(StockId), nameof(EffectiveDate), IsUnique = true)]
public sealed class IndexConstituentHistory
{
    public int Id { get; set; }
    public string IndexCode { get; set; } = "";
    public int StockId { get; set; }
    public DateOnly EffectiveDate { get; set; }
    [Precision(9, 6)] public decimal WeightPct { get; set; }
    public string Source { get; set; } = "";
}

[Table("ExchangeSessions", Schema = "options")]
[Index(nameof(IndexCode), nameof(SessionDate), IsUnique = true)]
public sealed class OptionsExchangeSession
{
    public int Id { get; set; }
    public string IndexCode { get; set; } = "";
    public DateOnly SessionDate { get; set; }
    public string Exchange { get; set; } = "";
    public bool IsTradingDay { get; set; }
    public TimeOnly? OpenTime { get; set; }
    public TimeOnly? CloseTime { get; set; }
    public bool IsWeeklyExpiry { get; set; }
    public bool IsMonthlyExpiry { get; set; }
    public string Source { get; set; } = "";
}

[Table("DerivativeInstruments", Schema = "options")]
[PrimaryKey(nameof(Exchange), nameof(TradingSymbol))]
public sealed class OptionsDerivativeInstrument
{
    public long InstrumentToken { get; set; }
    public string Exchange { get; set; } = "";
    public string TradingSymbol { get; set; } = "";
    public string Underlying { get; set; } = "";
    public DateOnly? Expiry { get; set; }
    [Precision(20, 6)] public decimal Strike { get; set; }
    public string InstrumentType { get; set; } = "";
    public int LotSize { get; set; }
    [Precision(12, 6)] public decimal TickSize { get; set; }
    public DateTime UpdatedAt { get; set; }
}

[Table("MarketSnapshotBatches", Schema = "options")]
public sealed class OptionsSnapshotBatch
{
    public int Id { get; set; }
    public string UserKey { get; set; } = "";
    public string IndexCode { get; set; } = "";
    public DateTime CapturedAt { get; set; }
    public string Status { get; set; } = "";
    public string CoverageJson { get; set; } = "{}";
    public string Source { get; set; } = "";
}

[Table("InstrumentQuoteSnapshots", Schema = "options")]
public sealed class OptionsQuoteSnapshot
{
    public long Id { get; set; }
    public int BatchId { get; set; }
    public long InstrumentToken { get; set; }
    public string Exchange { get; set; } = "";
    public string TradingSymbol { get; set; } = "";
    public DateTime ObservedAt { get; set; }
    [Precision(20, 6)] public decimal LastPrice { get; set; }
    [Precision(20, 6)] public decimal? Bid { get; set; }
    [Precision(20, 6)] public decimal? Ask { get; set; }
    public long? Volume { get; set; }
    public long? OpenInterest { get; set; }
    public string PayloadJson { get; set; } = "{}";
}

[Table("ForecastRuns", Schema = "options")]
[Index(nameof(UserKey), nameof(RequestId), IsUnique = true)]
public sealed class OptionsForecastRun
{
    public int Id { get; set; }
    public string UserKey { get; set; } = "";
    public Guid RequestId { get; set; }
    public string IndexCode { get; set; } = "";
    public string Mode { get; set; } = "";
    public string Status { get; set; } = "queued";
    public int? JobRunId { get; set; }
    public int? SnapshotBatchId { get; set; }
    public DateTime GeneratedAt { get; set; }
    public DateTime? TargetAt { get; set; }
    public DateTime? InputCutoffAt { get; set; }
    public string ModelVersion { get; set; } = "options.v1";
    public string? InputHash { get; set; }
    public string? AnalysisJson { get; set; }
    public string? ErrorMessage { get; set; }
}

[Table("ConstituentValuationSnapshots", Schema = "options")]
public sealed class OptionsValuationSnapshot
{
    public int Id { get; set; }
    public int ForecastRunId { get; set; }
    public int StockId { get; set; }
    public string PayloadJson { get; set; } = "{}";
}

[Table("StrategySuggestions", Schema = "options")]
public sealed class OptionsStrategySuggestion
{
    public int Id { get; set; }
    public int ForecastRunId { get; set; }
    public string CandidateKey { get; set; } = "";
    public string Side { get; set; } = "";
    public string RiskTier { get; set; } = "";
    public string Strategy { get; set; } = "";
    public DateOnly Expiry { get; set; }
    public string PayloadJson { get; set; } = "{}";
}

[Table("StrategyLegs", Schema = "options")]
public sealed class OptionsStrategyLeg
{
    public int Id { get; set; }
    public int StrategySuggestionId { get; set; }
    public string Exchange { get; set; } = "";
    public string TradingSymbol { get; set; } = "";
    public long InstrumentToken { get; set; }
    public string Direction { get; set; } = "";
    public int Quantity { get; set; }
    [Precision(20, 6)] public decimal Strike { get; set; }
    public string OptionType { get; set; } = "";
    [Precision(20, 6)] public decimal EntryPrice { get; set; }
}

[Table("AiSelections", Schema = "options")]
[Index(nameof(ForecastRunId), IsUnique = true)]
public sealed class OptionsAiSelection
{
    public int Id { get; set; }
    public int ForecastRunId { get; set; }
    public string InputHash { get; set; } = "";
    public string Status { get; set; } = "pending";
    public string Model { get; set; } = "";
    public string PromptVersion { get; set; } = "options-selector.v2";
    public string InputJson { get; set; } = "{}";
    public string? OutputJson { get; set; }
    public long InputTokens { get; set; }
    public long OutputTokens { get; set; }
    public long CachedInputTokens { get; set; }
    [Precision(20, 8)] public decimal? CostUsd { get; set; }
    public string? ErrorMessage { get; set; }
    public DateTime CreatedAt { get; set; }
}

[Table("ForecastOutcomes", Schema = "options")]
public sealed class OptionsForecastOutcome
{
    public int Id { get; set; }
    public int ForecastRunId { get; set; }
    public int Version { get; set; }
    public string Status { get; set; } = "";
    public string PayloadJson { get; set; } = "{}";
    public DateTime EvaluatedAt { get; set; }
}

[Table("StrategyOutcomes", Schema = "options")]
public sealed class OptionsStrategyOutcome
{
    public int Id { get; set; }
    public int StrategySuggestionId { get; set; }
    public int Version { get; set; }
    public string Status { get; set; } = "";
    public string PayloadJson { get; set; } = "{}";
    public DateTime EvaluatedAt { get; set; }
}
