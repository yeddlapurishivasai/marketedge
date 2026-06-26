CREATE TABLE [dbo].[USTickers]
(
    [Ticker]         NVARCHAR(20) NOT NULL,
    [Exchange]       NVARCHAR(20) NULL,       -- NASDAQ / NYSE / AMEX
    [Active]         BIT          NOT NULL CONSTRAINT [DF_USTickers_Active] DEFAULT (1),
    [IsFno]          BIT          NOT NULL CONSTRAINT [DF_USTickers_IsFno] DEFAULT (0),
    [BarsAvailable]  INT          NULL,
    [CreatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_USTickers_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_USTickers_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USTickers] PRIMARY KEY CLUSTERED ([Ticker]),
    INDEX [IX_USTickers_Active] NONCLUSTERED ([Active])
);
