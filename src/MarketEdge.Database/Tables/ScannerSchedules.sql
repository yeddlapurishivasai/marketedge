CREATE TABLE [dbo].[ScannerSchedules]
(
    [Market]          NVARCHAR(10)  NOT NULL,
    [Enabled]         BIT           NOT NULL DEFAULT 0,
    [IntervalMinutes] INT           NOT NULL DEFAULT 15,
    [LastEnqueuedAt]  DATETIME2     NULL,
    [UpdatedAt]       DATETIME2     NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_ScannerSchedules] PRIMARY KEY CLUSTERED ([Market]),
    CONSTRAINT [CK_ScannerSchedules_Market] CHECK ([Market] IN ('india', 'us'))
);
