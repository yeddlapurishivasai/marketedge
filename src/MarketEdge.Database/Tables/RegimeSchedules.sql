CREATE TABLE [dbo].[RegimeSchedules]
(
    [Market]          NVARCHAR(10)  NOT NULL,
    [Enabled]         BIT           NOT NULL DEFAULT 1,
    [HourLocal]       INT           NOT NULL DEFAULT 20,
    [LastEnqueuedAt]  DATETIME2     NULL,
    [UpdatedAt]       DATETIME2     NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_RegimeSchedules] PRIMARY KEY CLUSTERED ([Market]),
    CONSTRAINT [CK_RegimeSchedules_Market] CHECK ([Market] IN ('india', 'us'))
);
