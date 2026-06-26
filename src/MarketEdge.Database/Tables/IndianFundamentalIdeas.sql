CREATE TABLE [dbo].[IndianFundamentalIdeas]
(
    [Ticker]                            NVARCHAR(30)  NOT NULL,
    [EarningsDate]                      DATE          NOT NULL,   -- result date this idea was captured for

    -- Earnings-based screener metrics (captured only when a result is due)
    [EpsBeatPct]                        DECIMAL(12,4) NULL,       -- reported-EPS surprise vs estimate
    [OpmExpansionYoyPct]                DECIMAL(12,4) NULL,       -- OPM (pp) change vs year-ago quarter
    [OperatingProfitExpansionYoyPct]    DECIMAL(12,4) NULL,       -- operating-profit % change vs year-ago quarter

    -- Analyst rating change (detected daily) + price targets
    [LatestRatingFirm]                  NVARCHAR(120) NULL,
    [LatestRatingGrade]                 NVARCHAR(60)  NULL,       -- Overweight / Outperform / Neutral / ...
    [LatestRatingAction]                NVARCHAR(40)  NULL,       -- Upgrade / Downgrade / Maintains / ...
    [LatestRatingDate]                  DATE          NULL,
    [TargetLowPrice]                    DECIMAL(18,4) NULL,
    [TargetMeanPrice]                   DECIMAL(18,4) NULL,
    [TargetHighPrice]                   DECIMAL(18,4) NULL,

    -- Confidence scoring (0..100). Each metric is normalised to a 0..1 strength (phat)
    -- and fed through a Wilson lower bound whose z widens with the age of the underlying
    -- signal (z = z0 * (1 + days / halflife)), so confidence decays as the result / rating
    -- gets older. FundamentalConfidence is the fixed-weight blend of the per-metric scores;
    -- TechnicalConfidence is the Wilson lower bound of the stock's realised trade win rate;
    -- OverallConfidence blends the two. ConfidenceRationaleJson holds the per-metric breakdown.
    [EpsBeatConfidence]                 DECIMAL(6,2)  NULL,
    [OpmExpansionConfidence]            DECIMAL(6,2)  NULL,
    [OperatingProfitExpansionConfidence] DECIMAL(6,2) NULL,
    [AnalystRatingConfidence]           DECIMAL(6,2)  NULL,
    [TargetUpsideConfidence]            DECIMAL(6,2)  NULL,
    [FundamentalConfidence]             DECIMAL(6,2)  NULL,
    [TechnicalConfidence]               DECIMAL(6,2)  NULL,
    [OverallConfidence]                 DECIMAL(6,2)  NULL,
    [DaysSinceEarnings]                 INT           NULL,
    [DaysSinceRating]                   INT           NULL,
    [ConfidenceRationaleJson]           NVARCHAR(MAX) NULL,

    -- Lifecycle: rows for superseded earnings results are marked stale and hidden in the UI
    -- (a future purge job deletes them). Only IsStale = 0 rows are surfaced.
    [IsStale]                           BIT           NOT NULL CONSTRAINT [DF_IndianFundamentalIdeas_IsStale] DEFAULT (0),
    [CapturedAt]                        DATETIME2(0)  NOT NULL CONSTRAINT [DF_IndianFundamentalIdeas_CapturedAt] DEFAULT (SYSUTCDATETIME()),
    [UpdatedAt]                         DATETIME2(0)  NOT NULL CONSTRAINT [DF_IndianFundamentalIdeas_UpdatedAt] DEFAULT (SYSUTCDATETIME()),

    CONSTRAINT [PK_IndianFundamentalIdeas] PRIMARY KEY CLUSTERED ([Ticker], [EarningsDate]),
    CONSTRAINT [FK_IndianFundamentalIdeas_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker]),
    INDEX [IX_IndianFundamentalIdeas_Active] NONCLUSTERED ([IsStale], [EarningsDate])
);
