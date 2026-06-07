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
    public DbSet<StageAnalysisResult> StageAnalysisResults => Set<StageAnalysisResult>();

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
            .HasMany(j => j.StageAnalysisResults)
            .WithOne(r => r.JobRun)
            .HasForeignKey(r => r.RunId);

        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.ClosePrice).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.MA10).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.MA30).HasColumnType("decimal(18,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.MarketCap).HasColumnType("decimal(22,2)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.RSScore).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.RSMomentum).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.MomentumScore).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.ROC12w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.ROC26w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.ROC52w).HasColumnType("decimal(10,4)");
        modelBuilder.Entity<StageAnalysisResult>()
            .Property(r => r.ADRatio).HasColumnType("decimal(5,4)");
    }
}
