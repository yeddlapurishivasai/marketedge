CREATE TABLE [dbo].[AiStockAnalysisResults]
(
    [Id]                            BIGINT IDENTITY(1,1) NOT NULL,
    [Market]                        NVARCHAR(10) NOT NULL,
    [Ticker]                        NVARCHAR(30) NOT NULL,
    [WeekNumber]                    NVARCHAR(10) NOT NULL,
    [SchemaVersion]                 NVARCHAR(64) NOT NULL,
    [SkillVersion]                  NVARCHAR(20) NOT NULL,
    [Model]                         NVARCHAR(120) NOT NULL,
    [InputJson]                     NVARCHAR(MAX) NOT NULL,
    [EarningsCallJson]              NVARCHAR(MAX) NOT NULL,
    [CorporateFilingsJson]          NVARCHAR(MAX) NOT NULL,
    [AnalysisJson]                  NVARCHAR(MAX) NOT NULL,
    [InputTokens]                   BIGINT NOT NULL,
    [CachedInputTokens]             BIGINT NOT NULL,
    [OutputTokens]                  BIGINT NOT NULL,
    [InputCostUsd]                  DECIMAL(18,8) NOT NULL,
    [OutputCostUsd]                 DECIMAL(18,8) NOT NULL,
    [ToolCallsCostUsd]              DECIMAL(18,8) NOT NULL,
    [TotalCostUsd]                  DECIMAL(18,8) NOT NULL,
    [Projected1000TickersCostUsd]   DECIMAL(18,8) NOT NULL,
    [CreatedAt]                     DATETIME2 NOT NULL CONSTRAINT [DF_AiStockAnalysisResults_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]                     DATETIME2 NOT NULL CONSTRAINT [DF_AiStockAnalysisResults_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_AiStockAnalysisResults] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [CK_AiStockAnalysisResults_Market] CHECK ([Market] IN ('india', 'us')),
    CONSTRAINT [CK_AiStockAnalysisResults_Tokens] CHECK (
        [InputTokens] >= 0 AND [CachedInputTokens] >= 0 AND [OutputTokens] >= 0
        AND [CachedInputTokens] <= [InputTokens]),
    CONSTRAINT [CK_AiStockAnalysisResults_Costs] CHECK (
        [InputCostUsd] >= 0 AND [OutputCostUsd] >= 0 AND [ToolCallsCostUsd] >= 0
        AND [TotalCostUsd] = [InputCostUsd] + [OutputCostUsd] + [ToolCallsCostUsd]
        AND [Projected1000TickersCostUsd] = [TotalCostUsd] * 1000),
    CONSTRAINT [UX_AiStockAnalysisResults_MarketTickerWeek]
        UNIQUE NONCLUSTERED ([Market], [Ticker], [WeekNumber]),
    INDEX [IX_AiStockAnalysisResults_MarketTickerWeek]
        NONCLUSTERED ([Market], [Ticker], [WeekNumber] DESC)
)