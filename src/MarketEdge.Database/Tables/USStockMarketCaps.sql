CREATE TABLE [dbo].[USStockMarketCaps]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StockId] INT NOT NULL,
    [MarketCap] DECIMAL(20, 2) NULL,
    [UpdatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USStockMarketCaps] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_USStockMarketCaps_StockId] UNIQUE ([StockId]),
    CONSTRAINT [FK_USStockMarketCaps_USStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[USStocks]([Id]) ON DELETE CASCADE,
    INDEX [IX_USStockMarketCaps_MarketCap] NONCLUSTERED ([MarketCap])
);
