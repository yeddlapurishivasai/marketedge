CREATE TABLE [dbo].[IndianAnalystSnapshot]
(
    [Ticker]            NVARCHAR(30)  NOT NULL,
    [AsOfDate]          DATE          NOT NULL,
    [ConsensusRating]   NVARCHAR(15)  NULL,    -- Buy / Hold / Sell / Strong Buy / Strong Sell
    [NumAnalysts]       INT           NULL,
    [CurrentQuarterEps] DECIMAL(10,4) NULL,    -- INR
    [NextQuarterEps]    DECIMAL(10,4) NULL,    -- INR
    [CurrentYearEps]    DECIMAL(10,4) NULL,    -- INR
    [NextYearEps]       DECIMAL(10,4) NULL,    -- INR
    [UpdatedAt]         DATETIME2     NOT NULL CONSTRAINT [DF_IndianAnalystSnapshot_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianAnalystSnapshot] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_IndianAnalystSnapshot_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker])
);
