CREATE TABLE [dbo].[USNearPivots]
(
    [Id]                  INT IDENTITY(1,1) NOT NULL,
    [Ticker]              NVARCHAR(20)  NOT NULL,
    [CompanyName]         NVARCHAR(500) NULL,

    [TradeType]           NVARCHAR(12)  NOT NULL,   -- swing / positional
    [Direction]           NVARCHAR(6)   NOT NULL,   -- long / short

    -- Every scanner that flagged this symbol on the run that detected the setup
    [FlaggedScannersJson] NVARCHAR(MAX) NULL,
    [ScannerHitCount]     INT           NOT NULL CONSTRAINT [DF_USNearPivots_ScannerHitCount] DEFAULT (0),

    -- How close price is to its breakout pivot (resistance for longs, support for shorts)
    [LastClose]           DECIMAL(18,4) NOT NULL,
    [PivotPrice]          DECIMAL(18,4) NOT NULL,        -- prior highest-high (long) / lowest-low (short)
    [DistancePct]         DECIMAL(9,4)  NOT NULL,        -- % from close to pivot (0 = touching, lower = closer)
    [RelVolume]           DECIMAL(12,4) NULL,            -- breakout-bar vol / 20-day avg
    [VolumeConfirmed]     BIT           NOT NULL CONSTRAINT [DF_USNearPivots_VolumeConfirmed] DEFAULT (0),

    [ScanDate]            DATE          NOT NULL,
    [CreatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_USNearPivots_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]           DATETIME2     NOT NULL CONSTRAINT [DF_USNearPivots_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USNearPivots] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [FK_USNearPivots_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USNearPivots_Type_Distance] NONCLUSTERED ([TradeType], [DistancePct]),
    INDEX [IX_USNearPivots_ScanDate] NONCLUSTERED ([ScanDate])
);
