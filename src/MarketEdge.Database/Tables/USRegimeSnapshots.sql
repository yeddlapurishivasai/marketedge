CREATE TABLE [dbo].[USRegimeSnapshots]
(
    [AsOfDate]                 DATE           NOT NULL,
    [ConditionAsOfDate]        DATE           NULL,
    [BreadthAsOfDate]          DATE           NULL,
    [BenchmarkSymbol]          NVARCHAR(30)   NULL,
    [VolatilitySymbol]         NVARCHAR(30)   NULL,
    [EvaluatedCount]           INT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_EvaluatedCount] DEFAULT (0),

    -- Effective regime (spec §4)
    [Regime]                   NVARCHAR(30)   NOT NULL,
    [RegimeLabel]              NVARCHAR(60)   NOT NULL,
    [RegimeTone]               NVARCHAR(10)   NOT NULL,
    [Posture]                  NVARCHAR(400)  NULL,
    [Available]                BIT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_Available] DEFAULT (0),

    -- Benchmark condition (spec §3.1)
    [ConditionLabel]           NVARCHAR(30)   NOT NULL,
    [ConditionTone]            NVARCHAR(10)   NOT NULL,
    [ConditionExplanation]     NVARCHAR(400)  NULL,
    [ConditionAvailable]       BIT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_ConditionAvailable] DEFAULT (0),
    [ConditionClose]           DECIMAL(18,4)  NULL,
    [ConditionSma20]           DECIMAL(18,4)  NULL,
    [ConditionSma50]           DECIMAL(18,4)  NULL,
    [ConditionSma200]          DECIMAL(18,4)  NULL,
    [ConditionCloseVsSma20Pct] DECIMAL(10,4)  NULL,
    [ConditionCloseVsSma50Pct] DECIMAL(10,4)  NULL,
    [ConditionCloseVsSma200Pct] DECIMAL(10,4) NULL,
    [ConditionVolumeVsAvgPct]  DECIMAL(10,4)  NULL,

    -- Breadth composite (spec §3.2)
    [BreadthLabel]             NVARCHAR(30)   NOT NULL,
    [BreadthTone]              NVARCHAR(10)   NOT NULL,
    [BreadthScore]             INT            NULL,
    [BreadthPositiveSignals]   INT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_BreadthPositive] DEFAULT (0),
    [BreadthAvailableSignals]  INT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_BreadthAvail] DEFAULT (0),
    [BreadthAvailable]         BIT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_BreadthAvailable] DEFAULT (0),
    [SignalsJson]              NVARCHAR(MAX)  NULL,

    -- Raw participation facts (transparency / debugging)
    [PctAboveSma10]            DECIMAL(6,2)   NULL,
    [PctAboveSma20]            DECIMAL(6,2)   NULL,
    [PctAboveSma50]            DECIMAL(6,2)   NULL,
    [PctAboveSma200]           DECIMAL(6,2)   NULL,
    [PctSma20AboveSma50]       DECIMAL(6,2)   NULL,
    [PctSma50AboveSma200]      DECIMAL(6,2)   NULL,

    -- Benchmark / volatility context
    [BenchmarkYtdPct]          DECIMAL(10,4)  NULL,
    [Benchmark1wPct]           DECIMAL(10,4)  NULL,
    [Benchmark1mPct]           DECIMAL(10,4)  NULL,
    [Benchmark1yPct]           DECIMAL(10,4)  NULL,
    [BenchmarkPctFrom52wHigh]  DECIMAL(10,4)  NULL,
    [VolatilityClose]          DECIMAL(10,4)  NULL,

    [IsIntraday]               BIT            NOT NULL CONSTRAINT [DF_USRegimeSnapshots_IsIntraday] DEFAULT (0),
    [CreatedAt]                DATETIME2      NOT NULL CONSTRAINT [DF_USRegimeSnapshots_CreatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USRegimeSnapshots] PRIMARY KEY CLUSTERED ([AsOfDate])
);
