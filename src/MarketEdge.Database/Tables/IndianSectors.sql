CREATE TABLE [dbo].[IndianSectors]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [SectorName] NVARCHAR(200) NOT NULL,
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianSectors] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_IndianSectors_SectorName] UNIQUE ([SectorName])
);
