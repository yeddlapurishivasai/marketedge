CREATE TABLE [options].[InstrumentQuoteSnapshots]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL,
    [BatchId] INT NOT NULL,
    -- Preserve contract identity independently of the replaceable instrument master.
    [InstrumentToken] BIGINT NOT NULL,
    [Exchange] NVARCHAR(10) NOT NULL,
    [TradingSymbol] NVARCHAR(100) NOT NULL,
    [ObservedAt] DATETIME2 NOT NULL,
    [LastPrice] DECIMAL(20,6) NOT NULL,
    [Bid] DECIMAL(20,6) NULL,
    [Ask] DECIMAL(20,6) NULL,
    [Volume] BIGINT NULL,
    [OpenInterest] BIGINT NULL,
    [PayloadJson] NVARCHAR(MAX) NULL,
    CONSTRAINT [PK_Options_InstrumentQuoteSnapshots] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_Options_InstrumentQuoteSnapshots_MarketSnapshotBatches] FOREIGN KEY ([BatchId]) REFERENCES [options].[MarketSnapshotBatches] ([Id]),
    CONSTRAINT [CK_Options_InstrumentQuoteSnapshots_Values] CHECK (
        [InstrumentToken] > 0 AND [LastPrice] >= 0 AND ([Bid] IS NULL OR [Bid] >= 0) AND
        ([Ask] IS NULL OR [Ask] >= 0) AND ([Volume] IS NULL OR [Volume] >= 0) AND
        ([OpenInterest] IS NULL OR [OpenInterest] >= 0)),
    CONSTRAINT [CK_Options_InstrumentQuoteSnapshots_PayloadJson] CHECK ([PayloadJson] IS NULL OR ISJSON([PayloadJson]) = 1),
    INDEX [IX_Options_InstrumentQuoteSnapshots_Batch] NONCLUSTERED ([BatchId], [Exchange], [TradingSymbol]),
    INDEX [IX_Options_InstrumentQuoteSnapshots_Path] NONCLUSTERED ([Exchange], [TradingSymbol], [ObservedAt]) INCLUDE ([BatchId], [LastPrice], [Bid], [Ask]),
    INDEX [IX_Options_InstrumentQuoteSnapshots_ObservedAt] NONCLUSTERED ([ObservedAt])
);
