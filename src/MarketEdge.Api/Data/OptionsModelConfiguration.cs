using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Storage.ValueConversion;

namespace MarketEdge.Api.Data;

public static class OptionsModelConfiguration
{
    public static void ConfigureOptions(this ModelBuilder builder)
    {
        builder.Entity<BrokerConnection>().ToTable("BrokerConnections", "options");
        builder.Entity<OptionsMarketIndex>().ToTable("MarketIndices", "options");
        builder.Entity<IndexConstituentHistory>().ToTable("IndexConstituentHistory", "options");
        builder.Entity<OptionsExchangeSession>().ToTable("ExchangeSessions", "options");
        builder.Entity<OptionsDerivativeInstrument>().ToTable("DerivativeInstruments", "options");
        builder.Entity<OptionsSnapshotBatch>().ToTable("MarketSnapshotBatches", "options");
        builder.Entity<OptionsQuoteSnapshot>().ToTable("InstrumentQuoteSnapshots", "options");
        builder.Entity<OptionsForecastRun>().ToTable("ForecastRuns", "options");
        builder.Entity<OptionsValuationSnapshot>().ToTable("ConstituentValuationSnapshots", "options");
        builder.Entity<OptionsStrategySuggestion>().ToTable("StrategySuggestions", "options");
        builder.Entity<OptionsStrategyLeg>().ToTable("StrategyLegs", "options");
        builder.Entity<OptionsAiSelection>().ToTable("AiSelections", "options");
        builder.Entity<OptionsForecastOutcome>().ToTable("ForecastOutcomes", "options");
        builder.Entity<OptionsStrategyOutcome>().ToTable("StrategyOutcomes", "options");
        builder.Entity<OptionsMarketIndex>().Property(x => x.Code).HasMaxLength(20);
        builder.Entity<OptionsDerivativeInstrument>().Property(x => x.Exchange).HasMaxLength(10);
        builder.Entity<OptionsDerivativeInstrument>().Property(x => x.TradingSymbol).HasMaxLength(100);
        builder.Entity<OptionsDerivativeInstrument>().HasIndex(x => x.InstrumentToken);
        builder.Entity<IndexConstituentHistory>().HasOne<OptionsMarketIndex>().WithMany().HasForeignKey(x => x.IndexCode).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<IndexConstituentHistory>().HasOne<IndianStock>().WithMany().HasForeignKey(x => x.StockId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsExchangeSession>().HasOne<OptionsMarketIndex>().WithMany().HasForeignKey(x => x.IndexCode).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsSnapshotBatch>().HasOne<OptionsMarketIndex>().WithMany().HasForeignKey(x => x.IndexCode).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsQuoteSnapshot>().HasOne<OptionsSnapshotBatch>().WithMany().HasForeignKey(x => x.BatchId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastRun>().HasOne<OptionsMarketIndex>().WithMany().HasForeignKey(x => x.IndexCode).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastRun>().HasOne<JobRun>().WithMany().HasForeignKey(x => x.JobRunId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastRun>().HasOne<OptionsSnapshotBatch>().WithMany().HasForeignKey(x => x.SnapshotBatchId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastRun>().HasIndex(x => new { x.UserKey, x.IndexCode, x.Mode })
            .IsUnique().HasDatabaseName("UX_Options_ForecastRuns_Active")
            .HasFilter("[Status] IN (N'queued', N'running')");
        builder.Entity<OptionsValuationSnapshot>().HasOne<OptionsForecastRun>().WithMany().HasForeignKey(x => x.ForecastRunId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsValuationSnapshot>().HasOne<IndianStock>().WithMany().HasForeignKey(x => x.StockId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsValuationSnapshot>().HasIndex(x => new { x.ForecastRunId, x.StockId }).IsUnique();
        builder.Entity<OptionsStrategySuggestion>().HasOne<OptionsForecastRun>().WithMany().HasForeignKey(x => x.ForecastRunId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsStrategySuggestion>().HasIndex(x => new { x.ForecastRunId, x.CandidateKey }).IsUnique();
        builder.Entity<OptionsStrategySuggestion>().Property(x => x.CandidateKey).HasMaxLength(100);
        builder.Entity<OptionsStrategyLeg>().HasOne<OptionsStrategySuggestion>().WithMany().HasForeignKey(x => x.StrategySuggestionId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsAiSelection>().HasOne<OptionsForecastRun>().WithMany().HasForeignKey(x => x.ForecastRunId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastOutcome>().HasOne<OptionsForecastRun>().WithMany().HasForeignKey(x => x.ForecastRunId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsForecastOutcome>().HasIndex(x => new { x.ForecastRunId, x.Version }).IsUnique();
        builder.Entity<OptionsStrategyOutcome>().HasOne<OptionsStrategySuggestion>().WithMany().HasForeignKey(x => x.StrategySuggestionId).OnDelete(DeleteBehavior.NoAction);
        builder.Entity<OptionsStrategyOutcome>().HasIndex(x => new { x.StrategySuggestionId, x.Version }).IsUnique();
        var utc = new ValueConverter<DateTime, DateTime>(value => value, value => DateTime.SpecifyKind(value, DateTimeKind.Utc));
        var nullableUtc = new ValueConverter<DateTime?, DateTime?>(value => value,
            value => value.HasValue ? DateTime.SpecifyKind(value.Value, DateTimeKind.Utc) : null);
        foreach (var entity in builder.Model.GetEntityTypes().Where(x => x.GetSchema() == "options"))
        foreach (var property in entity.GetProperties())
        {
            if (property.Name == "UserKey") property.SetMaxLength(200);
            if (property.ClrType == typeof(DateTime)) property.SetValueConverter(utc);
            if (property.ClrType == typeof(DateTime?)) property.SetValueConverter(nullableUtc);
        }
    }
}
