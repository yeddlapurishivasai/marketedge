CREATE TABLE [dbo].[IndianTickerTechnical]
(
    [Ticker]          NVARCHAR(30)  NOT NULL,
    [AsOfDate]        DATE          NOT NULL,
    [Close]           DECIMAL(18,4) NULL,      -- INR
    [DayPct]          DECIMAL(8,4)  NULL,
    [Open]            DECIMAL(18,4) NULL,
    [High]            DECIMAL(18,4) NULL,
    [Low]             DECIMAL(18,4) NULL,
    [High52w]         DECIMAL(18,4) NULL,
    [From52wHigh]     DECIMAL(8,4)  NULL,
    [MarketCap]       BIGINT        NULL,      -- INR
    [Rs]              INT           NULL,
    [Rs1d]            INT           NULL,
    [Rs1w]            INT           NULL,
    [Rs1m]            INT           NULL,
    [Rs3m]            INT           NULL,
    [Rs6m]            INT           NULL,
    [RsType]          NVARCHAR(20)  NULL,
    [RsDate]          DATE          NULL,
    [ScannerHits]     INT           NULL,
    [LastScannerHit]  DATE          NULL,
    [UpdatedAt]       DATETIME2     NOT NULL CONSTRAINT [DF_IndianTickerTechnical_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianTickerTechnical] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_IndianTickerTechnical_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker]),
    INDEX [IX_IndianTickerTechnical_AsOfDate] NONCLUSTERED ([AsOfDate])
);
