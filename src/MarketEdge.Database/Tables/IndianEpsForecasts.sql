CREATE TABLE [dbo].[IndianEpsForecasts]
(
    [Ticker]         NVARCHAR(30)  NOT NULL,
    [AsOfDate]       DATE          NOT NULL,
    [PeriodType]     CHAR(1)       NOT NULL,   -- 'Q' or 'Y'
    [PeriodEndDate]  DATE          NOT NULL,
    [ConsensusEps]   DECIMAL(10,4) NULL,       -- INR
    [HighEps]        DECIMAL(10,4) NULL,       -- INR
    [LowEps]         DECIMAL(10,4) NULL,       -- INR
    [NumEstimates]   INT           NULL,
    [RevisionsUp]    INT           NOT NULL CONSTRAINT [DF_IndianEpsForecasts_RevisionsUp] DEFAULT (0),
    [RevisionsDown]  INT           NOT NULL CONSTRAINT [DF_IndianEpsForecasts_RevisionsDown] DEFAULT (0),
    [UpdatedAt]      DATETIME2     NOT NULL CONSTRAINT [DF_IndianEpsForecasts_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianEpsForecasts] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate], [PeriodType], [PeriodEndDate]),
    CONSTRAINT [FK_IndianEpsForecasts_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker]),
    CONSTRAINT [CK_IndianEpsForecasts_PeriodType] CHECK ([PeriodType] IN ('Q', 'Y')),
    INDEX [IX_IndianEpsForecasts_Period] NONCLUSTERED ([Ticker], [PeriodType], [PeriodEndDate])
);
