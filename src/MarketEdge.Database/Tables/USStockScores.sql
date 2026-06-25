CREATE TABLE [dbo].[USStockScores]
(
    [Ticker]                NVARCHAR(20)  NOT NULL,
    [AsOfDate]              DATE          NULL,

    -- Deterministic upside (Part A)
    [UpsideEpsPct]          DECIMAL(12,4) NULL,     -- (forwardEps/trailingEps - 1) * 100
    [UpsideAnalystPct]      DECIMAL(12,4) NULL,     -- (targetMeanPrice/close - 1) * 100
    [TargetPrice]           DECIMAL(18,4) NULL,

    -- AI flow (future) upside / downside (Part B placeholder)
    [AiUpsidePct]           DECIMAL(12,4) NULL,
    [AiDownsidePct]         DECIMAL(12,4) NULL,
    [AiRationale]           NVARCHAR(MAX) NULL,

    -- Wilson lower-bound scores, swing profile (technical precedence)
    [SwingScore]            INT           NULL,      -- 0..100 (bull)
    [SwingSide]             NVARCHAR(6)   NULL,      -- long / short / none
    [SwingBull]             INT           NULL,
    [SwingBear]             INT           NULL,

    -- Wilson lower-bound scores, positional profile (fundamentals = 50%)
    [PositionalScore]       INT           NULL,
    [PositionalSide]        NVARCHAR(6)   NULL,
    [PositionalBull]        INT           NULL,
    [PositionalBear]        INT           NULL,

    -- Inputs / explainability
    [FundFreshnessDecay]    DECIMAL(9,6)  NULL,      -- 0.5^(days_since_earnings/30)
    [DaysSinceEarnings]     INT           NULL,
    [ScannerHits]           INT           NULL,
    [IsFno]                 BIT           NULL,
    [ComponentsJson]        NVARCHAR(MAX) NULL,      -- per-check pass/weight breakdown (token-friendly)

    [ScoredAt]              DATETIME2     NOT NULL CONSTRAINT [DF_USStockScores_ScoredAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USStockScores] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_USStockScores_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker])
);
