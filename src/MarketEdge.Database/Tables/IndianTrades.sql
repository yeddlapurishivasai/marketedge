CREATE TABLE [dbo].[IndianTrades]
(
    [Id]                  INT IDENTITY(1,1) NOT NULL,
    [Ticker]              NVARCHAR(30)  NOT NULL,
    [CompanyName]         NVARCHAR(500) NULL,

    [TradeType]           NVARCHAR(12)  NOT NULL,   -- swing / positional
    [Direction]           NVARCHAR(6)   NOT NULL,   -- long / short
    [Status]              NVARCHAR(10)  NOT NULL,   -- active / closed

    -- Which scanner opened the trade, and every scanner that has flagged it since (JSON list)
    [EntryScanner]        NVARCHAR(50)  NULL,
    [FlaggedScannersJson] NVARCHAR(MAX) NULL,
    [ScannerHitCount]     INT           NOT NULL CONSTRAINT [DF_IndianTrades_ScannerHitCount] DEFAULT (0),

    [EntryAt]             DATETIME2     NOT NULL,
    [EntryPrice]          DECIMAL(18,4) NOT NULL,

    -- Stop management
    [InitialStop]         DECIMAL(18,4) NULL,
    [CurrentStop]         DECIMAL(18,4) NULL,
    [StopBasis]           NVARCHAR(16)  NULL,        -- pct6 / ema20 / breakeven / trail10
    [RiskPerShare]        DECIMAL(18,4) NULL,        -- R = |entry - initialStop|
    [MovedToBe]           BIT           NOT NULL CONSTRAINT [DF_IndianTrades_MovedToBe] DEFAULT (0),

    -- Live tracking
    [LastPrice]           DECIMAL(18,4) NULL,
    [PnLPct]              DECIMAL(12,4) NULL,
    [MfePct]              DECIMAL(12,4) NULL,        -- max favourable excursion
    [MaePct]              DECIMAL(12,4) NULL,        -- max adverse excursion

    -- Exit
    [ExitAt]              DATETIME2     NULL,
    [ExitPrice]           DECIMAL(18,4) NULL,
    [ExitReason]          NVARCHAR(16)  NULL,        -- sl_hit / ema_close / trail

    [CreatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_IndianTrades_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_IndianTrades_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_IndianTrades] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_IndianTrades_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker]),
    INDEX [IX_IndianTrades_Ticker_Type_Status] NONCLUSTERED ([Ticker], [TradeType], [Status]),
    INDEX [IX_IndianTrades_Status] NONCLUSTERED ([Status])
);
