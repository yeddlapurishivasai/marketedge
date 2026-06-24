CREATE TABLE [dbo].[IndianStockMarketCaps]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StockId] INT NOT NULL,
    [MarketCap] DECIMAL(20, 2) NULL,
    [UpdatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianStockMarketCaps] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_IndianStockMarketCaps_StockId] UNIQUE ([StockId]),
    CONSTRAINT [FK_IndianStockMarketCaps_IndianStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[IndianStocks]([Id]) ON DELETE CASCADE,
    INDEX [IX_IndianStockMarketCaps_MarketCap] NONCLUSTERED ([MarketCap])
);
