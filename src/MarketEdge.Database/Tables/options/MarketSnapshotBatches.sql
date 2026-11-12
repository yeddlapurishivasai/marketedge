CREATE TABLE [options].[MarketSnapshotBatches]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [UserKey] NVARCHAR(200) NOT NULL,
    [IndexCode] NVARCHAR(20) NOT NULL,
    [CapturedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_MarketSnapshotBatches_CapturedAt] DEFAULT (SYSUTCDATETIME()),
    [Status] NVARCHAR(30) NOT NULL,
    [CoverageJson] NVARCHAR(MAX) NULL,
    [Source] NVARCHAR(30) NOT NULL,
    CONSTRAINT [PK_Options_MarketSnapshotBatches] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_Options_MarketSnapshotBatches_MarketIndices] FOREIGN KEY ([IndexCode]) REFERENCES [options].[MarketIndices] ([Code]),
    CONSTRAINT [CK_Options_MarketSnapshotBatches_Status] CHECK ([Status] IN (N'pending', N'collecting', N'complete', N'completed', N'partial', N'failed', N'stale')),
    CONSTRAINT [CK_Options_MarketSnapshotBatches_CoverageJson] CHECK ([CoverageJson] IS NULL OR ISJSON([CoverageJson]) = 1),
    INDEX [IX_Options_MarketSnapshotBatches_UserIndexCaptured] NONCLUSTERED ([UserKey], [IndexCode], [CapturedAt] DESC)
);
