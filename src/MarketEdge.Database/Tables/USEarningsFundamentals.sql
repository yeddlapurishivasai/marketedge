CREATE TABLE [dbo].[USEarningsFundamentals]
(
    [Ticker]                NVARCHAR(20)  NOT NULL,
    [AsOfDate]              DATE          NOT NULL,      -- ingestion date
    [LatestQuarterEnd]      DATE          NULL,

    -- Revenue (USD), current / previous quarter / year-ago quarter
    [Revenue]               DECIMAL(20,2) NULL,
    [RevenuePrevQ]          DECIMAL(20,2) NULL,
    [RevenueYoyQ]           DECIMAL(20,2) NULL,
    [RevenueGrowthYoyPct]   DECIMAL(12,4) NULL,

    -- Operating profit (USD) + OPM (%)
    [OperatingProfit]       DECIMAL(20,2) NULL,
    [OperatingProfitPrevQ]  DECIMAL(20,2) NULL,
    [OperatingProfitYoyQ]   DECIMAL(20,2) NULL,
    [Opm]                   DECIMAL(9,4)  NULL,
    [OpmPrevQ]              DECIMAL(9,4)  NULL,
    [OpmYoyQ]               DECIMAL(9,4)  NULL,

    -- Net profit (USD) + net margin (%)
    [NetProfit]             DECIMAL(20,2) NULL,
    [NetProfitPrevQ]        DECIMAL(20,2) NULL,
    [NetProfitYoyQ]         DECIMAL(20,2) NULL,
    [NetMarginPct]          DECIMAL(9,4)  NULL,
    [EarningsGrowthYoyPct]  DECIMAL(12,4) NULL,
    [EarningsGrowthQoqPct]  DECIMAL(12,4) NULL,

    -- Derived directional flags
    [EarningsIncreasing]    BIT           NULL,          -- PAT(cur) > PAT(yoy) and > 0
    [OperatingProfitTrend]  NVARCHAR(12)  NULL,          -- expanding / decreasing / flat
    [OpmTrend]              NVARCHAR(12)  NULL,          -- expanding / decreasing / flat

    -- Earnings announcement dates (from yfinance earnings_dates)
    [LastEarningsDate]      DATE          NULL,
    [PrevEarningsDate]      DATE          NULL,
    [NextEarningsDate]      DATE          NULL,          -- nearest upcoming earnings (drives daily-run scheduling)
    [LastReportedEps]       DECIMAL(12,4) NULL,
    [LastEpsSurprisePct]    DECIMAL(12,4) NULL,

    -- Reported EPS history: last 4 reported quarters (Q1 = most recent), from
    -- yfinance get_earnings_dates (EPS Estimate / Reported EPS / Surprise(%)).
    [EpsQ1Date]             DATE          NULL,
    [EpsQ1Estimate]         DECIMAL(12,4) NULL,
    [EpsQ1Actual]           DECIMAL(12,4) NULL,
    [EpsQ1SurprisePct]      DECIMAL(12,4) NULL,
    [EpsQ2Date]             DATE          NULL,
    [EpsQ2Estimate]         DECIMAL(12,4) NULL,
    [EpsQ2Actual]           DECIMAL(12,4) NULL,
    [EpsQ2SurprisePct]      DECIMAL(12,4) NULL,
    [EpsQ3Date]             DATE          NULL,
    [EpsQ3Estimate]         DECIMAL(12,4) NULL,
    [EpsQ3Actual]           DECIMAL(12,4) NULL,
    [EpsQ3SurprisePct]      DECIMAL(12,4) NULL,
    [EpsQ4Date]             DATE          NULL,
    [EpsQ4Estimate]         DECIMAL(12,4) NULL,
    [EpsQ4Actual]           DECIMAL(12,4) NULL,
    [EpsQ4SurprisePct]      DECIMAL(12,4) NULL,

    -- Valuation multiples (from yfinance ticker.info)
    [TrailingPe]            DECIMAL(18,4) NULL,
    [ForwardPe]             DECIMAL(18,4) NULL,

    [UpdatedAt]             DATETIME2     NOT NULL CONSTRAINT [DF_USEarningsFundamentals_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USEarningsFundamentals] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_USEarningsFundamentals_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USEarningsFundamentals_LastEarningsDate] NONCLUSTERED ([LastEarningsDate]),
    INDEX [IX_USEarningsFundamentals_NextEarningsDate] NONCLUSTERED ([NextEarningsDate])
);
