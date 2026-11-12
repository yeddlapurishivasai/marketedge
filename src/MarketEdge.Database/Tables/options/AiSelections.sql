CREATE TABLE [options].[AiSelections]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [ForecastRunId] INT NOT NULL,
    [InputHash] NVARCHAR(64) NOT NULL,
    [Status] NVARCHAR(30) NOT NULL,
    [Model] NVARCHAR(100) NOT NULL,
    [PromptVersion] NVARCHAR(50) NOT NULL,
    [InputJson] NVARCHAR(MAX) NULL,
    [OutputJson] NVARCHAR(MAX) NULL,
    [InputTokens] BIGINT NOT NULL CONSTRAINT [DF_Options_AiSelections_InputTokens] DEFAULT (0),
    [OutputTokens] BIGINT NOT NULL CONSTRAINT [DF_Options_AiSelections_OutputTokens] DEFAULT (0),
    [CachedInputTokens] BIGINT NOT NULL CONSTRAINT [DF_Options_AiSelections_CachedInputTokens] DEFAULT (0),
    [CostUsd] DECIMAL(20,8) NULL,
    [ErrorMessage] NVARCHAR(2000) NULL,
    [CreatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_AiSelections_CreatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_Options_AiSelections] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_AiSelections_ForecastRunId] UNIQUE ([ForecastRunId]),
    CONSTRAINT [FK_Options_AiSelections_ForecastRuns] FOREIGN KEY ([ForecastRunId]) REFERENCES [options].[ForecastRuns] ([Id]),
    CONSTRAINT [CK_Options_AiSelections_Status] CHECK ([Status] IN (N'pending', N'running', N'completed', N'failed', N'skipped')),
    CONSTRAINT [CK_Options_AiSelections_InputJson] CHECK ([InputJson] IS NULL OR ISJSON([InputJson]) = 1),
    CONSTRAINT [CK_Options_AiSelections_OutputJson] CHECK ([OutputJson] IS NULL OR ISJSON([OutputJson]) = 1),
    CONSTRAINT [CK_Options_AiSelections_Usage] CHECK (
        [InputTokens] >= 0 AND [OutputTokens] >= 0 AND [CachedInputTokens] >= 0 AND
        [CachedInputTokens] <= [InputTokens] AND ([CostUsd] IS NULL OR [CostUsd] >= 0)),
    INDEX [IX_Options_AiSelections_InputHash] NONCLUSTERED ([InputHash], [Model], [PromptVersion])
);
