CREATE TABLE [options].[IndexConstituentHistory]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [IndexCode] NVARCHAR(20) NOT NULL,
    [StockId] INT NOT NULL,
    [EffectiveDate] DATE NOT NULL,
    [WeightPct] DECIMAL(9,6) NOT NULL,
    [Source] NVARCHAR(500) NOT NULL,
    CONSTRAINT [PK_Options_IndexConstituentHistory] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_IndexConstituentHistory_Membership] UNIQUE ([IndexCode], [StockId], [EffectiveDate]),
    CONSTRAINT [FK_Options_IndexConstituentHistory_MarketIndices] FOREIGN KEY ([IndexCode]) REFERENCES [options].[MarketIndices] ([Code]),
    CONSTRAINT [FK_Options_IndexConstituentHistory_IndianStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[IndianStocks] ([Id]),
    CONSTRAINT [CK_Options_IndexConstituentHistory_Weight] CHECK ([WeightPct] > 0 AND [WeightPct] <= 100),
    INDEX [IX_Options_IndexConstituentHistory_AsOf] NONCLUSTERED ([IndexCode], [EffectiveDate] DESC) INCLUDE ([StockId], [WeightPct]),
    INDEX [IX_Options_IndexConstituentHistory_StockId] NONCLUSTERED ([StockId])
);
