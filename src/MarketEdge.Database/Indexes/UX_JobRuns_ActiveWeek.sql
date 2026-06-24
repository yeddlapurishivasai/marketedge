-- Enforces at most one authoritative stage analysis run per (JobType, Market, WeekNumber).
-- Only "active" runs (queued/running/completed) participate; failed/cancelled runs are
-- excluded so a week can be retried after a failure and keep prior cancelled attempts.
CREATE UNIQUE NONCLUSTERED INDEX [UX_JobRuns_ActiveWeek]
    ON [dbo].[JobRuns] ([JobType], [Market], [WeekNumber])
    WHERE [Status] IN ('queued', 'running', 'completed');
