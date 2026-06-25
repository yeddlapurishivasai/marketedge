CREATE TABLE [dbo].[USScannerResults]
(
    [Id]             INT IDENTITY(1,1) NOT NULL,
    [RunId]          INT            NOT NULL,
    [ScannerName]    NVARCHAR(50)   NOT NULL,
    [ScanDate]       DATE           NOT NULL,
    [Symbol]         NVARCHAR(50)   NOT NULL,
    [CompanyName]    NVARCHAR(500)  NULL,
    [SectorName]     NVARCHAR(200)  NULL,
    [Industry]       NVARCHAR(200)  NULL,

    [ClosePrice]     DECIMAL(18,4)  NULL,
    [DayChangePct]   DECIMAL(10,4)  NULL,
    [Volume]         BIGINT         NULL,
    [RelVolume]      DECIMAL(12,4)  NULL,
    [RsRating]       INT            NULL,

    [TriggerDetails] NVARCHAR(MAX)  NULL,
    [CreatedAt]      DATETIME2      NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USScannerResults] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_USScannerResults_JobRuns] FOREIGN KEY ([RunId]) REFERENCES [dbo].[JobRuns]([Id]),
    CONSTRAINT [UX_USScannerResults_ScannerDateSymbol] UNIQUE NONCLUSTERED ([ScannerName], [ScanDate], [Symbol]),
    INDEX [IX_USScannerResults_ScannerDate] NONCLUSTERED ([ScannerName], [ScanDate]),
    INDEX [IX_USScannerResults_Symbol] NONCLUSTERED ([Symbol]),
    INDEX [IX_USScannerResults_RunId] NONCLUSTERED ([RunId])
);
