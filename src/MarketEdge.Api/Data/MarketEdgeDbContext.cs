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
    public DbSet<JobRun> JobRuns => Set<JobRun>();
    public DbSet<IndianStageAnalysisResult> IndianStageAnalysisResults => Set<IndianStageAnalysisResult>();
    public DbSet<USStageAnalysisResult> USStageAnalysisResults => Set<USStageAnalysisResult>();

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

        modelBuilder.Entity<IndianStock>().Property(s => s.MarketCap).HasColumnType("decimal(20,2)");
        modelBuilder.Entity<USStock>().Property(s => s.MarketCap).HasColumnType("decimal(20,2)");

        ConfigureDecimalProperties<IndianStageAnalysisResult>(modelBuilder);
        ConfigureDecimalProperties<USStageAnalysisResult>(modelBuilder);
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
