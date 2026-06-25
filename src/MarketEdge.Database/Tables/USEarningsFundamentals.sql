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
    [LastReportedEps]       DECIMAL(12,4) NULL,
    [LastEpsSurprisePct]    DECIMAL(12,4) NULL,

    [UpdatedAt]             DATETIME2     NOT NULL CONSTRAINT [DF_USEarningsFundamentals_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USEarningsFundamentals] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_USEarningsFundamentals_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USEarningsFundamentals_LastEarningsDate] NONCLUSTERED ([LastEarningsDate])
);
