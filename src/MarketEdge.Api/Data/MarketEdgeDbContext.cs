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
    }

    private static void ConfigureScannerEntities(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<ScannerSchedule>().HasKey(s => s.Market);
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
