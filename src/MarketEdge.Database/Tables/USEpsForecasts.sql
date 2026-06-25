CREATE TABLE [dbo].[USEpsForecasts]
(
    [Ticker]         NVARCHAR(20)  NOT NULL,
    [AsOfDate]       DATE          NOT NULL,
    [PeriodType]     CHAR(1)       NOT NULL,   -- 'Q' or 'Y'
    [PeriodEndDate]  DATE          NOT NULL,
    [ConsensusEps]   DECIMAL(10,4) NULL,       -- USD
    [HighEps]        DECIMAL(10,4) NULL,       -- USD
    [LowEps]         DECIMAL(10,4) NULL,       -- USD
    [NumEstimates]   INT           NULL,
    [RevisionsUp]    INT           NOT NULL CONSTRAINT [DF_USEpsForecasts_RevisionsUp] DEFAULT (0),
    [RevisionsDown]  INT           NOT NULL CONSTRAINT [DF_USEpsForecasts_RevisionsDown] DEFAULT (0),
    [UpdatedAt]      DATETIME2     NOT NULL CONSTRAINT [DF_USEpsForecasts_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USEpsForecasts] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate], [PeriodType], [PeriodEndDate]),
    CONSTRAINT [FK_USEpsForecasts_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    CONSTRAINT [CK_USEpsForecasts_PeriodType] CHECK ([PeriodType] IN ('Q', 'Y')),
    INDEX [IX_USEpsForecasts_Period] NONCLUSTERED ([Ticker], [PeriodType], [PeriodEndDate])
);
