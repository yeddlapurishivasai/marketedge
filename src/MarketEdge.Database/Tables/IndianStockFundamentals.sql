CREATE TABLE [dbo].[IndianStockFundamentals]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StockId] INT NOT NULL,
    [MarketCap] DECIMAL(20, 2) NULL,
    [UpdatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianStockFundamentals] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_IndianStockFundamentals_StockId] UNIQUE ([StockId]),
    CONSTRAINT [FK_IndianStockFundamentals_IndianStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[IndianStocks]([Id]) ON DELETE CASCADE,
    INDEX [IX_IndianStockFundamentals_MarketCap] NONCLUSTERED ([MarketCap])
);
