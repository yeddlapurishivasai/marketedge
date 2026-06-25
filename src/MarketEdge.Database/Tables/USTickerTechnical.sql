CREATE TABLE [dbo].[USTickerTechnical]
(
    [Ticker]          NVARCHAR(20)  NOT NULL,
    [AsOfDate]        DATE          NOT NULL,
    [Close]           DECIMAL(18,4) NULL,      -- USD
    [DayPct]          DECIMAL(8,4)  NULL,
    [Open]            DECIMAL(18,4) NULL,
    [High]            DECIMAL(18,4) NULL,
    [Low]             DECIMAL(18,4) NULL,
    [High52w]         DECIMAL(18,4) NULL,
    [From52wHigh]     DECIMAL(8,4)  NULL,
    [MarketCap]       BIGINT        NULL,      -- USD
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
    [UpdatedAt]       DATETIME2     NOT NULL CONSTRAINT [DF_USTickerTechnical_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USTickerTechnical] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_USTickerTechnical_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USTickerTechnical_AsOfDate] NONCLUSTERED ([AsOfDate])
);
