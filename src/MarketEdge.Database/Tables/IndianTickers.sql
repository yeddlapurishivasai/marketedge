CREATE TABLE [dbo].[IndianTickers]
(
    [Ticker]         NVARCHAR(30) NOT NULL,
    [Exchange]       NVARCHAR(20) NULL,       -- NSE / BSE
    [Active]         BIT          NOT NULL CONSTRAINT [DF_IndianTickers_Active] DEFAULT (1),
    [IsFno]          BIT          NOT NULL CONSTRAINT [DF_IndianTickers_IsFno] DEFAULT (0),
    [BarsAvailable]  INT          NULL,
    [CreatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_IndianTickers_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_IndianTickers_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianTickers] PRIMARY KEY CLUSTERED ([Ticker]),
    INDEX [IX_IndianTickers_Active] NONCLUSTERED ([Active])
);
