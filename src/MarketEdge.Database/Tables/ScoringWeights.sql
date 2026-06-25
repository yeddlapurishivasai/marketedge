CREATE TABLE [dbo].[ScoringWeights]
(
    [Id]             INT IDENTITY(1,1) NOT NULL,
    [Market]         NVARCHAR(10)  NOT NULL,   -- india / us

    -- Category groups the weight:
    --   'pattern'  -> one row per scanner; ComponentKey = scanner name; Weight is the
    --                 adaptive "pattern goodness" (0..1) that rises/falls with paper-trade outcomes.
    --   'mix'      -> per-profile blend weights; ComponentKey = '{profile}:{component}'
    --                 e.g. 'swing:pattern', 'positional:fundamental'. Editable, not auto-adapted.
    [Category]       NVARCHAR(20)  NOT NULL,
    [ComponentKey]   NVARCHAR(100) NOT NULL,

    [Weight]         DECIMAL(9,4)  NOT NULL,   -- current (possibly adapted / overridden) weight
    [SeedWeight]     DECIMAL(9,4)  NOT NULL,   -- initial value, for reset/reference

    [Wins]           INT           NOT NULL CONSTRAINT [DF_ScoringWeights_Wins]   DEFAULT (0),
    [Losses]         INT           NOT NULL CONSTRAINT [DF_ScoringWeights_Losses] DEFAULT (0),

    -- When set, the weight is held fixed at its manual value (auto-adaptation skips it).
    [ManualOverride] BIT           NOT NULL CONSTRAINT [DF_ScoringWeights_Manual] DEFAULT (0),

    [UpdatedAt]      DATETIME2     NOT NULL CONSTRAINT [DF_ScoringWeights_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_ScoringWeights] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [CK_ScoringWeights_Market] CHECK ([Market] IN ('india', 'us')),
    CONSTRAINT [UQ_ScoringWeights] UNIQUE ([Market], [Category], [ComponentKey])
);
