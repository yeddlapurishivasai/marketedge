CREATE TABLE [options].[StrangleStrategies]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [UserKey] NVARCHAR(200) NOT NULL,
    [EntryRunId] INT NOT NULL,
    [IndexCode] NVARCHAR(16) NOT NULL,
    [SessionDate] DATE NOT NULL,
    [BaselineJson] NVARCHAR(MAX) NOT NULL,
    [CreatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_StrangleStrategies_CreatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_StrangleStrategies] PRIMARY KEY ([Id]),
    CONSTRAINT [UQ_StrangleStrategies_Entry] UNIQUE ([UserKey], [EntryRunId]),
    CONSTRAINT [UQ_StrangleStrategies_Owner] UNIQUE ([Id], [UserKey], [IndexCode]),
    CONSTRAINT [FK_StrangleStrategies_Entry] FOREIGN KEY ([EntryRunId], [UserKey], [IndexCode]) REFERENCES [options].[StrangleRuns] ([Id], [UserKey], [IndexCode]),
    CONSTRAINT [CK_StrangleStrategies_Index] CHECK ([IndexCode] IN (N'NIFTY50', N'SENSEX')),
    CONSTRAINT [CK_StrangleStrategies_Json] CHECK (ISJSON([BaselineJson]) = 1),
    INDEX [IX_StrangleStrategies_Session] NONCLUSTERED ([UserKey], [IndexCode], [SessionDate])
);
