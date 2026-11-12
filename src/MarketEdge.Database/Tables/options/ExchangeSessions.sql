CREATE TABLE [options].[ExchangeSessions]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [IndexCode] NVARCHAR(20) NOT NULL,
    [SessionDate] DATE NOT NULL,
    [Exchange] NVARCHAR(10) NOT NULL,
    [IsTradingDay] BIT NOT NULL,
    -- Exchange-local Asia/Kolkata wall-clock times; audit timestamps elsewhere are UTC.
    [OpenTime] TIME NULL,
    [CloseTime] TIME NULL,
    [IsWeeklyExpiry] BIT NOT NULL CONSTRAINT [DF_Options_ExchangeSessions_IsWeeklyExpiry] DEFAULT (0),
    [IsMonthlyExpiry] BIT NOT NULL CONSTRAINT [DF_Options_ExchangeSessions_IsMonthlyExpiry] DEFAULT (0),
    [Source] NVARCHAR(500) NOT NULL,
    CONSTRAINT [PK_Options_ExchangeSessions] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_ExchangeSessions_IndexDate] UNIQUE ([IndexCode], [SessionDate]),
    CONSTRAINT [FK_Options_ExchangeSessions_MarketIndices] FOREIGN KEY ([IndexCode]) REFERENCES [options].[MarketIndices] ([Code]),
    CONSTRAINT [CK_Options_ExchangeSessions_Hours] CHECK (
        ([IsTradingDay] = 1 AND [OpenTime] IS NOT NULL AND [CloseTime] IS NOT NULL AND [OpenTime] < [CloseTime]) OR
        ([IsTradingDay] = 0 AND [OpenTime] IS NULL AND [CloseTime] IS NULL AND [IsWeeklyExpiry] = 0 AND [IsMonthlyExpiry] = 0)),
    INDEX [IX_Options_ExchangeSessions_NextTrading] NONCLUSTERED ([IndexCode], [IsTradingDay], [SessionDate])
);
