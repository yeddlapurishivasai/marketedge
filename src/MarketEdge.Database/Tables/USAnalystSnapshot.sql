CREATE TABLE [dbo].[USAnalystSnapshot]
(
    [Ticker]            NVARCHAR(20)  NOT NULL,
    [AsOfDate]          DATE          NOT NULL,
    [ConsensusRating]   NVARCHAR(15)  NULL,    -- Buy / Hold / Sell / Strong Buy / Strong Sell
    [NumAnalysts]       INT           NULL,
    [CurrentQuarterEps] DECIMAL(10,4) NULL,    -- USD
    [NextQuarterEps]    DECIMAL(10,4) NULL,    -- USD
    [CurrentYearEps]    DECIMAL(10,4) NULL,    -- USD
    [NextYearEps]       DECIMAL(10,4) NULL,    -- USD
    [UpdatedAt]         DATETIME2     NOT NULL CONSTRAINT [DF_USAnalystSnapshot_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USAnalystSnapshot] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_USAnalystSnapshot_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker])
);
