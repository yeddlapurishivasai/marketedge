CREATE TABLE [options].[StrategyLegs]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StrategySuggestionId] INT NOT NULL,
    [Exchange] NVARCHAR(10) NOT NULL,
    [TradingSymbol] NVARCHAR(100) NOT NULL,
    [InstrumentToken] BIGINT NOT NULL,
    [Direction] NVARCHAR(10) NOT NULL,
    [Quantity] INT NOT NULL,
    [Strike] DECIMAL(20,6) NOT NULL,
    [OptionType] NVARCHAR(5) NOT NULL,
    [EntryPrice] DECIMAL(20,6) NOT NULL,
    CONSTRAINT [PK_Options_StrategyLegs] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_Options_StrategyLegs_StrategySuggestions] FOREIGN KEY ([StrategySuggestionId]) REFERENCES [options].[StrategySuggestions] ([Id]),
    CONSTRAINT [CK_Options_StrategyLegs_Direction] CHECK ([Direction] IN (N'buy', N'sell')),
    CONSTRAINT [CK_Options_StrategyLegs_OptionType] CHECK ([OptionType] IN (N'CE', N'PE')),
    CONSTRAINT [CK_Options_StrategyLegs_Contract] CHECK ([InstrumentToken] > 0 AND [Quantity] > 0 AND [Strike] > 0 AND [EntryPrice] > 0),
    INDEX [IX_Options_StrategyLegs_Suggestion] NONCLUSTERED ([StrategySuggestionId]),
    INDEX [IX_Options_StrategyLegs_Contract] NONCLUSTERED ([Exchange], [TradingSymbol]) INCLUDE ([StrategySuggestionId])
);
