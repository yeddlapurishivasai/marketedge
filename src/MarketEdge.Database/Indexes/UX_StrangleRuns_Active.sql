CREATE UNIQUE NONCLUSTERED INDEX [UX_StrangleRuns_Active]
    ON [options].[StrangleRuns] ([UserKey], [IndexCode], [Action])
    WHERE [Status] IN (N'queued', N'running');
