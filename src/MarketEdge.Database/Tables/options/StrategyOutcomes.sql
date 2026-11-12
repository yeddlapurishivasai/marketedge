CREATE TABLE [options].[StrategyOutcomes]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [StrategySuggestionId] INT NOT NULL,
    [Version] INT NOT NULL,
    [Status] NVARCHAR(30) NOT NULL,
    [PayloadJson] NVARCHAR(MAX) NOT NULL,
    [EvaluatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_StrategyOutcomes_EvaluatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_Options_StrategyOutcomes] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_StrategyOutcomes_Version] UNIQUE ([StrategySuggestionId], [Version]),
    CONSTRAINT [FK_Options_StrategyOutcomes_StrategySuggestions] FOREIGN KEY ([StrategySuggestionId]) REFERENCES [options].[StrategySuggestions] ([Id]),
    CONSTRAINT [CK_Options_StrategyOutcomes_Version] CHECK ([Version] > 0),
    CONSTRAINT [CK_Options_StrategyOutcomes_Status] CHECK ([Status] IN (N'pending', N'pending-data', N'open', N'live', N'completed', N'partial', N'unavailable', N'ambiguous', N'failed', N'expired')),
    CONSTRAINT [CK_Options_StrategyOutcomes_PayloadJson] CHECK (ISJSON([PayloadJson]) = 1),
    INDEX [IX_Options_StrategyOutcomes_Status] NONCLUSTERED ([Status], [EvaluatedAt])
);
