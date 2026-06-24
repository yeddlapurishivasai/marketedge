-- Enforces at most one authoritative stage analysis run per (JobType, Market, WeekNumber).
-- Only "active" runs (queued/running) participate; completed/failed/cancelled runs
-- are excluded so a week can be retried after a failure or completion and keep prior
-- cancelled attempts.
CREATE UNIQUE NONCLUSTERED INDEX [UX_JobRuns_ActiveWeek]
    ON [dbo].[JobRuns] ([JobType], [Market], [WeekNumber])
    WHERE [Status] IN ('queued', 'running');
