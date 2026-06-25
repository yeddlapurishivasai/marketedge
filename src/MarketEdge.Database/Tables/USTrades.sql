CREATE TABLE [dbo].[USTrades]
(
    [Id]                  INT IDENTITY(1,1) NOT NULL,
    [Ticker]              NVARCHAR(20)  NOT NULL,
    [CompanyName]         NVARCHAR(500) NULL,

    [TradeType]           NVARCHAR(12)  NOT NULL,   -- swing / positional
    [Direction]           NVARCHAR(6)   NOT NULL,   -- long / short
    [Status]              NVARCHAR(10)  NOT NULL,   -- active / closed

    -- Which scanner opened the trade, and every scanner that has flagged it since (JSON list)
    [EntryScanner]        NVARCHAR(50)  NULL,
    [FlaggedScannersJson] NVARCHAR(MAX) NULL,
    [ScannerHitCount]     INT           NOT NULL CONSTRAINT [DF_USTrades_ScannerHitCount] DEFAULT (0),

    [EntryAt]             DATETIME2     NOT NULL,
    [EntryPrice]          DECIMAL(18,4) NOT NULL,
    [Qty]                 INT           NULL,        -- shares sized off a fixed notional per position

    -- Stop management
    [InitialStop]         DECIMAL(18,4) NULL,
    [CurrentStop]         DECIMAL(18,4) NULL,
    [StopBasis]           NVARCHAR(16)  NULL,        -- pct6 / ema20 / breakeven / trail10
    [RiskPerShare]        DECIMAL(18,4) NULL,        -- R = |entry - initialStop|
    [MovedToBe]           BIT           NOT NULL CONSTRAINT [DF_USTrades_MovedToBe] DEFAULT (0),

    -- Live tracking
    [LastPrice]           DECIMAL(18,4) NULL,
    [PnLPct]              DECIMAL(12,4) NULL,
    [PnLAmount]           DECIMAL(18,4) NULL,        -- pure profit = (price - entry) * dir * qty
    [MfePct]              DECIMAL(12,4) NULL,        -- max favourable excursion
    [MaePct]              DECIMAL(12,4) NULL,        -- max adverse excursion

    -- Exit
    [ExitAt]              DATETIME2     NULL,
    [ExitPrice]           DECIMAL(18,4) NULL,
    [ExitReason]          NVARCHAR(16)  NULL,        -- sl_hit / ema_close / trail

    [CreatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_USTrades_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_USTrades_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USTrades] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_USTrades_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USTrades_Ticker_Type_Status] NONCLUSTERED ([Ticker], [TradeType], [Status]),
    INDEX [IX_USTrades_Status] NONCLUSTERED ([Status])
);
