CREATE TABLE [dbo].[USStocks]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [Symbol] NVARCHAR(50) NOT NULL,
    [CompanyName] NVARCHAR(500) NOT NULL,
    [SectorId] INT NOT NULL,
    [BroadSector] NVARCHAR(200) NULL,
    [MarketCap] DECIMAL(20, 2) NULL,
    [IsFno] BIT NOT NULL CONSTRAINT [DF_USStocks_IsFno] DEFAULT (0),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USStocks] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_USStocks_Symbol] UNIQUE ([Symbol]),
    CONSTRAINT [FK_USStocks_USSectors] FOREIGN KEY ([SectorId]) REFERENCES [dbo].[USSectors]([Id]),
    INDEX [IX_USStocks_SectorId] NONCLUSTERED ([SectorId]),
    INDEX [IX_USStocks_Symbol] NONCLUSTERED ([Symbol]),
    INDEX [IX_USStocks_MarketCap] NONCLUSTERED ([MarketCap])
);
