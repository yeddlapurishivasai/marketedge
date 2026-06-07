CREATE TABLE [dbo].[IndianStageAnalysisResults]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [RunId] INT NOT NULL,
    [Symbol] NVARCHAR(50) NOT NULL,
    [CompanyName] NVARCHAR(500) NOT NULL,
    [SectorId] INT NOT NULL,
    [SectorName] NVARCHAR(200) NOT NULL,

    -- Price & Moving Averages
    [ClosePrice] DECIMAL(18,4) NULL,
    [MA10] DECIMAL(18,4) NULL,
    [MA30] DECIMAL(18,4) NULL,
    [MarketCap] DECIMAL(22,2) NULL,

    -- Stage 2 determination
    [IsStage2] BIT NOT NULL DEFAULT 0,
    [Classification] NVARCHAR(20) NULL,

    -- Relative Strength
    [RSScore] DECIMAL(10,4) NULL,
    [RSRank] INT NULL,
    [RSMomentum] DECIMAL(10,4) NULL,

    -- Momentum
    [MomentumScore] DECIMAL(10,4) NULL,
    [ROC12w] DECIMAL(10,4) NULL,
    [ROC26w] DECIMAL(10,4) NULL,
    [ROC52w] DECIMAL(10,4) NULL,

    -- Sector Rotation
    [Quadrant] NVARCHAR(20) NULL,

    -- Accumulation / Distribution
    [ADRatio] DECIMAL(5,4) NULL,
    [ADClassification] NVARCHAR(20) NULL,

    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_IndianStageAnalysisResults] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_IndianStageAnalysisResults_JobRuns] FOREIGN KEY ([RunId]) REFERENCES [dbo].[JobRuns]([Id]),
    CONSTRAINT [CK_IndianStageAnalysisResults_Classification] CHECK ([Classification] IN ('continuing', 'new', 'reentry', 'removed')),
    CONSTRAINT [CK_IndianStageAnalysisResults_Quadrant] CHECK ([Quadrant] IN ('leading', 'weakening', 'lagging', 'improving')),
    CONSTRAINT [CK_IndianStageAnalysisResults_ADClassification] CHECK ([ADClassification] IN ('accumulating', 'distributing', 'neutral')),
    INDEX [IX_IndianStageAnalysisResults_RunId] NONCLUSTERED ([RunId]),
    INDEX [IX_IndianStageAnalysisResults_Symbol] NONCLUSTERED ([Symbol]),
    INDEX [IX_IndianStageAnalysisResults_IsStage2] NONCLUSTERED ([IsStage2]) INCLUDE ([RunId], [Symbol], [Classification]),
    INDEX [IX_IndianStageAnalysisResults_RunId_IsStage2] NONCLUSTERED ([RunId], [IsStage2]) INCLUDE ([Symbol], [SectorName], [RSScore], [MomentumScore])
)
