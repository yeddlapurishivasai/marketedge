CREATE TABLE [dbo].[IndianStocks]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [Symbol] NVARCHAR(50) NOT NULL,
    [CompanyName] NVARCHAR(500) NOT NULL,
    [SectorId] INT NOT NULL,
    [BroadSector] NVARCHAR(200) NULL,
    [IsFno] BIT NOT NULL CONSTRAINT [DF_IndianStocks_IsFno] DEFAULT (0),
    [IsTestSample] BIT NOT NULL CONSTRAINT [DF_IndianStocks_IsTestSample] DEFAULT (0),
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianStocks] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_IndianStocks_Symbol] UNIQUE ([Symbol]),
    CONSTRAINT [FK_IndianStocks_IndianSectors] FOREIGN KEY ([SectorId]) REFERENCES [dbo].[IndianSectors]([Id]),
    INDEX [IX_IndianStocks_SectorId] NONCLUSTERED ([SectorId]),
    INDEX [IX_IndianStocks_Symbol] NONCLUSTERED ([Symbol])
);
