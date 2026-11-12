CREATE TABLE [options].[ForecastOutcomes]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [ForecastRunId] INT NOT NULL,
    [Version] INT NOT NULL,
    [Status] NVARCHAR(30) NOT NULL,
    [PayloadJson] NVARCHAR(MAX) NOT NULL,
    [EvaluatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_ForecastOutcomes_EvaluatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_Options_ForecastOutcomes] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_ForecastOutcomes_Version] UNIQUE ([ForecastRunId], [Version]),
    CONSTRAINT [FK_Options_ForecastOutcomes_ForecastRuns] FOREIGN KEY ([ForecastRunId]) REFERENCES [options].[ForecastRuns] ([Id]),
    CONSTRAINT [CK_Options_ForecastOutcomes_Version] CHECK ([Version] > 0),
    CONSTRAINT [CK_Options_ForecastOutcomes_Status] CHECK ([Status] IN (N'pending', N'pending-data', N'open', N'live', N'completed', N'partial', N'unavailable', N'ambiguous', N'failed', N'expired')),
    CONSTRAINT [CK_Options_ForecastOutcomes_PayloadJson] CHECK (ISJSON([PayloadJson]) = 1),
    INDEX [IX_Options_ForecastOutcomes_Status] NONCLUSTERED ([Status], [EvaluatedAt])
);
