using MarketEdge.Api.Models;
using Microsoft.EntityFrameworkCore;

namespace MarketEdge.Api.Data;

public class MarketEdgeDbContext : DbContext
{
    public MarketEdgeDbContext(DbContextOptions<MarketEdgeDbContext> options) : base(options) { }

    public DbSet<IndianSector> IndianSectors => Set<IndianSector>();
    public DbSet<IndianStock> IndianStocks => Set<IndianStock>();
    public DbSet<USSector> USSectors => Set<USSector>();
    public DbSet<USStock> USStocks => Set<USStock>();
    public DbSet<IndianStockFundamentals> IndianStockFundamentals => Set<IndianStockFundamentals>();
    public DbSet<USStockFundamentals> USStockFundamentals => Set<USStockFundamentals>();
    public DbSet<JobRun> JobRuns => Set<JobRun>();
    public DbSet<IndianStageAnalysisResult> IndianStageAnalysisResults => Set<IndianStageAnalysisResult>();
    public DbSet<USStageAnalysisResult> USStageAnalysisResults => Set<USStageAnalysisResult>();

    // Stock Lookup (query-only) sets
    public DbSet<IndianTicker> IndianTickers => Set<IndianTicker>();
    public DbSet<USTicker> USTickers => Set<USTicker>();
    public DbSet<IndianTickerTechnical> IndianTickerTechnical => Set<IndianTickerTechnical>();
    public DbSet<USTickerTechnical> USTickerTechnical => Set<USTickerTechnical>();
    public DbSet<IndianAnalystSnapshot> IndianAnalystSnapshots => Set<IndianAnalystSnapshot>();
    public DbSet<USAnalystSnapshot> USAnalystSnapshots => Set<USAnalystSnapshot>();
    public DbSet<IndianEpsForecast> IndianEpsForecasts => Set<IndianEpsForecast>();
    public DbSet<USEpsForecast> USEpsForecasts => Set<USEpsForecast>();
    public DbSet<IndianBar1D> IndianBars1D => Set<IndianBar1D>();
    public DbSet<USBar1D> USBars1D => Set<USBar1D>();

    // Technical Scanners (feature 011)
    public DbSet<IndianTechnicalScannerResult> IndianTechnicalScannerResults => Set<IndianTechnicalScannerResult>();
    public DbSet<USTechnicalScannerResult> USTechnicalScannerResults => Set<USTechnicalScannerResult>();
    public DbSet<ScannerSchedule> ScannerSchedules => Set<ScannerSchedule>();
    public DbSet<FundamentalsSchedule> FundamentalsSchedules => Set<FundamentalsSchedule>();
    public DbSet<Stage2Schedule> Stage2Schedules => Set<Stage2Schedule>();
    // Fundamental Scanners (earnings fundamentals + per-stock note)
    public DbSet<IndianEarningsFundamentals> IndianEarningsFundamentals => Set<IndianEarningsFundamentals>();
    public DbSet<USEarningsFundamentals> USEarningsFundamentals => Set<USEarningsFundamentals>();
    public DbSet<IndianFundamentalIdea> IndianFundamentalIdeas => Set<IndianFundamentalIdea>();
    public DbSet<USFundamentalIdea> USFundamentalIdeas => Set<USFundamentalIdea>();
    public DbSet<IndianStockNote> IndianStockNotes => Set<IndianStockNote>();
    public DbSet<USStockNote> USStockNotes => Set<USStockNote>();
    public DbSet<IndianStockSignals> IndianStockSignals => Set<IndianStockSignals>();
    public DbSet<USStockSignals> USStockSignals => Set<USStockSignals>();
    public DbSet<IndianBreakout> IndianBreakouts => Set<IndianBreakout>();
    public DbSet<USBreakout> USBreakouts => Set<USBreakout>();
    public DbSet<IndianNearPivot> IndianNearPivots => Set<IndianNearPivot>();
    public DbSet<USNearPivot> USNearPivots => Set<USNearPivot>();
    public DbSet<ScoringWeight> ScoringWeights => Set<ScoringWeight>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<IndianSector>()
            .HasMany(s => s.Stocks)
            .WithOne(st => st.Sector)
            .HasForeignKey(st => st.SectorId);

        modelBuilder.Entity<USSector>()
            .HasMany(s => s.Stocks)
            .WithOne(st => st.Sector)
            .HasForeignKey(st => st.SectorId);

        modelBuilder.Entity<JobRun>()
            .HasMany(j => j.IndianStageAnalysisResults)
            .WithOne(r => r.JobRun)
            .HasForeignKey(r => r.RunId);

        modelBuilder.Entity<JobRun>()
            .HasMany(j => j.USStageAnalysisResults)
            .WithOne(r => r.JobRun)
            .HasForeignKey(r => r.RunId);

        modelBuilder.Entity<IndianStock>()
            .HasOne(s => s.Fundamentals)
            .WithOne(m => m.Stock)
            .HasForeignKey<IndianStockFundamentals>(m => m.StockId);

        modelBuilder.Entity<USStock>()
            .HasOne(s => s.Fundamentals)
            .WithOne(m => m.Stock)
            .HasForeignKey<USStockFundamentals>(m => m.StockId);

        modelBuilder.Entity<IndianStockFundamentals>().Property(m => m.MarketCap).HasColumnType("decimal(20,2)");
        modelBuilder.Entity<USStockFundamentals>().Property(m => m.MarketCap).HasColumnType("decimal(20,2)");

        ConfigureDecimalProperties<IndianStageAnalysisResult>(modelBuilder);
        ConfigureDecimalProperties<USStageAnalysisResult>(modelBuilder);

        ConfigureLookupEntities(modelBuilder);
        ConfigureScannerEntities(modelBuilder);
        ConfigureFundamentalEntities(modelBuilder);
    }

    private static void ConfigureFundamentalEntities(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<IndianEarningsFundamentals>().HasKey(e => e.Ticker);
        modelBuilder.Entity<USEarningsFundamentals>().HasKey(e => e.Ticker);
        modelBuilder.Entity<IndianStockNote>().HasKey(n => n.Ticker);
        modelBuilder.Entity<USStockNote>().HasKey(n => n.Ticker);

        modelBuilder.Entity<IndianStockSignals>().HasKey(s => s.Ticker);
        modelBuilder.Entity<USStockSignals>().HasKey(s => s.Ticker);
        foreach (var t in new[] { typeof(IndianStockSignals), typeof(USStockSignals) })
        {
            var e = modelBuilder.Entity(t);
            e.Property(nameof(StockSignalsBase.CapexCwip)).HasColumnType("decimal(20,2)");
            e.Property(nameof(StockSignalsBase.CapexCwipPrevQ)).HasColumnType("decimal(20,2)");
            e.Property(nameof(StockSignalsBase.CapexChangePct)).HasColumnType("decimal(12,4)");
        }
        modelBuilder.Entity<IndianBreakout>().HasKey(t => t.Id);
        modelBuilder.Entity<USBreakout>().HasKey(t => t.Id);
        foreach (var t in new[] { typeof(IndianBreakout), typeof(USBreakout) })
        {
            var e = modelBuilder.Entity(t);
            foreach (var p in new[] { nameof(BreakoutBase.EntryPrice), nameof(BreakoutBase.InitialStop),
                nameof(BreakoutBase.CurrentStop), nameof(BreakoutBase.RiskPerShare), nameof(BreakoutBase.LastPrice),
                nameof(BreakoutBase.PnLAmount), nameof(BreakoutBase.ExitPrice) })
                e.Property(p).HasColumnType("decimal(18,4)");
            foreach (var p in new[] { nameof(BreakoutBase.PnLPct), nameof(BreakoutBase.MfePct), nameof(BreakoutBase.MaePct) })
                e.Property(p).HasColumnType("decimal(12,4)");
            e.Property(nameof(BreakoutBase.ConfidenceScore)).HasColumnType("decimal(5,2)");
        }

        modelBuilder.Entity<IndianNearPivot>().HasKey(t => t.Id);
        modelBuilder.Entity<USNearPivot>().HasKey(t => t.Id);
        foreach (var t in new[] { typeof(IndianNearPivot), typeof(USNearPivot) })
        {
            var e = modelBuilder.Entity(t);
            e.Property(nameof(NearPivotBase.LastClose)).HasColumnType("decimal(18,4)");
            e.Property(nameof(NearPivotBase.PivotPrice)).HasColumnType("decimal(18,4)");
            e.Property(nameof(NearPivotBase.DistancePct)).HasColumnType("decimal(9,4)");
            e.Property(nameof(NearPivotBase.RelVolume)).HasColumnType("decimal(12,4)");
        }

        modelBuilder.Entity<ScoringWeight>().HasKey(w => w.Id);
        modelBuilder.Entity<ScoringWeight>().Property(w => w.Weight).HasColumnType("decimal(9,4)");
        modelBuilder.Entity<ScoringWeight>().Property(w => w.SeedWeight).HasColumnType("decimal(9,4)");

        foreach (var t in new[] { typeof(IndianEarningsFundamentals), typeof(USEarningsFundamentals) })
        {
            var e = modelBuilder.Entity(t);
            foreach (var p in new[] { nameof(EarningsFundamentalsBase.Revenue), nameof(EarningsFundamentalsBase.RevenuePrevQ),
                nameof(EarningsFundamentalsBase.RevenueYoyQ), nameof(EarningsFundamentalsBase.OperatingProfit),
                nameof(EarningsFundamentalsBase.OperatingProfitPrevQ), nameof(EarningsFundamentalsBase.OperatingProfitYoyQ),
                nameof(EarningsFundamentalsBase.NetProfit), nameof(EarningsFundamentalsBase.NetProfitPrevQ),
                nameof(EarningsFundamentalsBase.NetProfitYoyQ) })
                e.Property(p).HasColumnType("decimal(20,2)");
            foreach (var p in new[] { nameof(EarningsFundamentalsBase.RevenueGrowthYoyPct),
                nameof(EarningsFundamentalsBase.EarningsGrowthYoyPct), nameof(EarningsFundamentalsBase.EarningsGrowthQoqPct) })
                e.Property(p).HasColumnType("decimal(12,4)");
            foreach (var p in new[] { nameof(EarningsFundamentalsBase.Opm), nameof(EarningsFundamentalsBase.OpmPrevQ),
                nameof(EarningsFundamentalsBase.OpmYoyQ), nameof(EarningsFundamentalsBase.NetMarginPct) })
                e.Property(p).HasColumnType("decimal(9,4)");
            foreach (var p in new[] { nameof(EarningsFundamentalsBase.LastReportedEps),
                nameof(EarningsFundamentalsBase.LastEpsSurprisePct),
                nameof(EarningsFundamentalsBase.EpsQ1Estimate), nameof(EarningsFundamentalsBase.EpsQ1Actual), nameof(EarningsFundamentalsBase.EpsQ1SurprisePct),
                nameof(EarningsFundamentalsBase.EpsQ2Estimate), nameof(EarningsFundamentalsBase.EpsQ2Actual), nameof(EarningsFundamentalsBase.EpsQ2SurprisePct),
                nameof(EarningsFundamentalsBase.EpsQ3Estimate), nameof(EarningsFundamentalsBase.EpsQ3Actual), nameof(EarningsFundamentalsBase.EpsQ3SurprisePct),
                nameof(EarningsFundamentalsBase.EpsQ4Estimate), nameof(EarningsFundamentalsBase.EpsQ4Actual), nameof(EarningsFundamentalsBase.EpsQ4SurprisePct) })
                e.Property(p).HasColumnType("decimal(12,4)");
            foreach (var p in new[] { nameof(EarningsFundamentalsBase.TrailingPe), nameof(EarningsFundamentalsBase.ForwardPe) })
                e.Property(p).HasColumnType("decimal(18,4)");
        }

        foreach (var t in new[] { typeof(IndianFundamentalIdea), typeof(USFundamentalIdea) })
        {
            var e = modelBuilder.Entity(t);
            e.HasKey(nameof(FundamentalIdeaBase.Ticker), nameof(FundamentalIdeaBase.EarningsDate));
            foreach (var p in new[] { nameof(FundamentalIdeaBase.EpsBeatPct),
                nameof(FundamentalIdeaBase.OpmExpansionYoyPct),
                nameof(FundamentalIdeaBase.OperatingProfitExpansionYoyPct) })
                e.Property(p).HasColumnType("decimal(12,4)");
            foreach (var p in new[] { nameof(FundamentalIdeaBase.TargetLowPrice),
                nameof(FundamentalIdeaBase.TargetMeanPrice), nameof(FundamentalIdeaBase.TargetHighPrice) })
                e.Property(p).HasColumnType("decimal(18,4)");
            foreach (var p in new[] { nameof(FundamentalIdeaBase.EpsBeatConfidence),
                nameof(FundamentalIdeaBase.OpmExpansionConfidence),
                nameof(FundamentalIdeaBase.OperatingProfitExpansionConfidence),
                nameof(FundamentalIdeaBase.AnalystRatingConfidence),
                nameof(FundamentalIdeaBase.TargetUpsideConfidence),
                nameof(FundamentalIdeaBase.FundamentalConfidence),
                nameof(FundamentalIdeaBase.TechnicalConfidence),
                nameof(FundamentalIdeaBase.OverallConfidence) })
                e.Property(p).HasColumnType("decimal(6,2)");
        }
    }

    private static void ConfigureScannerEntities(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<ScannerSchedule>().HasKey(s => s.Market);
        modelBuilder.Entity<FundamentalsSchedule>().HasKey(s => s.Market);
        modelBuilder.Entity<Stage2Schedule>().HasKey(s => s.Market);
        foreach (var t in new[] { typeof(IndianTechnicalScannerResult), typeof(USTechnicalScannerResult) })
        {
            var e = modelBuilder.Entity(t);
            e.Property(nameof(TechnicalScannerResultBase.ClosePrice)).HasColumnType("decimal(18,4)");
            e.Property(nameof(TechnicalScannerResultBase.DayChangePct)).HasColumnType("decimal(10,4)");
            e.Property(nameof(TechnicalScannerResultBase.RelVolume)).HasColumnType("decimal(12,4)");
        }
    }

    private static void ConfigureLookupEntities(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<IndianTicker>().HasKey(t => t.Ticker);
        modelBuilder.Entity<USTicker>().HasKey(t => t.Ticker);

        modelBuilder.Entity<IndianTickerTechnical>().HasKey(t => new { t.Ticker, t.AsOfDate });
        modelBuilder.Entity<USTickerTechnical>().HasKey(t => new { t.Ticker, t.AsOfDate });

        modelBuilder.Entity<IndianAnalystSnapshot>().HasKey(a => new { a.Ticker, a.AsOfDate });
        modelBuilder.Entity<USAnalystSnapshot>().HasKey(a => new { a.Ticker, a.AsOfDate });

        modelBuilder.Entity<IndianEpsForecast>().HasKey(e => new { e.Ticker, e.AsOfDate, e.PeriodType, e.PeriodEndDate });
        modelBuilder.Entity<USEpsForecast>().HasKey(e => new { e.Ticker, e.AsOfDate, e.PeriodType, e.PeriodEndDate });

        modelBuilder.Entity<IndianBar1D>().HasKey(b => new { b.Ticker, b.BarDate });
        modelBuilder.Entity<USBar1D>().HasKey(b => new { b.Ticker, b.BarDate });
    }

    private static void ConfigureDecimalProperties<T>(ModelBuilder modelBuilder) where T : StageAnalysisResultBase
    {
        modelBuilder.Entity<T>().Property(r => r.ClosePrice).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<T>().Property(r => r.MA10).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<T>().Property(r => r.MA30).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<T>().Property(r => r.MarketCap).HasColumnType("decimal(22,2)");
        modelBuilder.Entity<T>().Property(r => r.RSScore).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RS1w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RS2w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RS3w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RSDelta1w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RSDelta2w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.RSDelta3w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.MomentumScore).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.ROC1w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.ROC2w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.ROC3w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<T>().Property(r => r.ADRatio).HasColumnType("decimal(5,4)");
    }
}
