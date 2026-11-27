CREATE TABLE [dbo].[Stage4Schedules]
(
    [Market] NVARCHAR(10) NOT NULL,
    [Enabled] BIT NOT NULL DEFAULT 1,
    [HourLocal] INT NOT NULL DEFAULT 20,
    [LastEnqueuedAt] DATETIME2 NULL,
    [UpdatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_Stage4Schedules] PRIMARY KEY CLUSTERED ([Market]),
    CONSTRAINT [CK_Stage4Schedules_Market] CHECK ([Market] IN ('india', 'us')),
    CONSTRAINT [CK_Stage4Schedules_HourLocal] CHECK ([HourLocal] BETWEEN 0 AND 23)
);
