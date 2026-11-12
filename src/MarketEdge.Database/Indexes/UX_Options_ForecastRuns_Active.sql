CREATE UNIQUE NONCLUSTERED INDEX [UX_Options_ForecastRuns_Active]
    ON [options].[ForecastRuns] ([UserKey], [IndexCode], [Mode])
    WHERE [Status] IN (N'queued', N'running');
