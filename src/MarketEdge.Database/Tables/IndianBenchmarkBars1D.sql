CREATE TABLE [dbo].[IndianBenchmarkBars1D]
(
    [Symbol]     NVARCHAR(30)  NOT NULL,
    [BarDate]    DATE          NOT NULL,
    [Open]       DECIMAL(18,4) NULL,
    [High]       DECIMAL(18,4) NULL,
    [Low]        DECIMAL(18,4) NULL,
    [Close]      DECIMAL(18,4) NULL,
    [Volume]     BIGINT        NULL,
    [AdjClose]   DECIMAL(18,4) NULL,
    CONSTRAINT [PK_IndianBenchmarkBars1D] PRIMARY KEY CLUSTERED ([Symbol], [BarDate]),
    INDEX [IX_IndianBenchmarkBars1D_BarDate] NONCLUSTERED ([BarDate])
);
