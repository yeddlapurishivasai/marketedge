CREATE TABLE [dbo].[USStockFundamentals]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StockId] INT NOT NULL,
    [MarketCap] DECIMAL(20, 2) NULL,
    [UpdatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USStockFundamentals] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_USStockFundamentals_StockId] UNIQUE ([StockId]),
    CONSTRAINT [FK_USStockFundamentals_USStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[USStocks]([Id]) ON DELETE CASCADE,
    INDEX [IX_USStockFundamentals_MarketCap] NONCLUSTERED ([MarketCap])
);
