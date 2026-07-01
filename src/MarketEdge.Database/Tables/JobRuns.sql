CREATE TABLE [dbo].[JobRuns]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [JobType] NVARCHAR(50) NOT NULL,
    [Market] NVARCHAR(10) NOT NULL,
    [WeekNumber] NVARCHAR(10) NOT NULL DEFAULT '',
    [Status] NVARCHAR(20) NOT NULL DEFAULT 'queued',
    [Progress] INT NOT NULL DEFAULT 0,
    [Parameters] NVARCHAR(MAX) NULL,
    [Metrics] NVARCHAR(MAX) NULL,
    [ErrorMessage] NVARCHAR(MAX) NULL,
    [StartedAt] DATETIME2 NULL,
    [CompletedAt] DATETIME2 NULL,
    [CreatedAt] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    [Stages] NVARCHAR(MAX) NULL,

    CONSTRAINT [PK_JobRuns] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [CK_JobRuns_Status] CHECK ([Status] IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT [CK_JobRuns_Market] CHECK ([Market] IN ('india', 'us')),
    INDEX [IX_JobRuns_JobType_Market] NONCLUSTERED ([JobType], [Market]),
    INDEX [IX_JobRuns_WeekNumber] NONCLUSTERED ([WeekNumber], [Market], [JobType]),
    INDEX [IX_JobRuns_Status] NONCLUSTERED ([Status]),
    INDEX [IX_JobRuns_CreatedAt] NONCLUSTERED ([CreatedAt] DESC)
)
