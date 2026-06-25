CREATE TABLE [dbo].[IndianBars1D]
(
    [Ticker]     NVARCHAR(30)  NOT NULL,
    [BarDate]    DATE          NOT NULL,
    [Open]       DECIMAL(18,4) NULL,
    [High]       DECIMAL(18,4) NULL,
    [Low]        DECIMAL(18,4) NULL,
    [Close]      DECIMAL(18,4) NULL,
    [Volume]     BIGINT        NULL,
    [AdjClose]   DECIMAL(18,4) NULL,
    CONSTRAINT [PK_IndianBars1D] PRIMARY KEY CLUSTERED ([Ticker], [BarDate]),
    CONSTRAINT [FK_IndianBars1D_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker]),
    INDEX [IX_IndianBars1D_BarDate] NONCLUSTERED ([BarDate])
);
