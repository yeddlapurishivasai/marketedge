/*
  One-time cleanup: delete all SHORT-direction breakouts from both blotters.

  Breakouts are long-only (short "breakdown" setups are a future feature), so any
  Direction = 'short' rows are stale leftovers from earlier runs and should be removed.

  As of 2026-06-29 prod has: US = 0 short, India = 1 short (CUMMINSIND positional, Id 63).
  The script is written generically (deletes every short row) so it stays correct
  regardless of the exact current count.

  Transactional + self-guarding: previews the counts, aborts (ROLLBACK, nothing deleted)
  if either table matches far more than expected, otherwise commits.

  Run:
    sqlcmd -S market-edge-dr-sql-server-01.database.windows.net -d MarketEdge -U sqladmin -C -i deploy\onetime\delete-short-breakouts.sql
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRAN;

DECLARE @us int = (SELECT COUNT(*) FROM dbo.USBreakouts     WHERE Direction = 'short');
DECLARE @in int = (SELECT COUNT(*) FROM dbo.IndianBreakouts WHERE Direction = 'short');

PRINT CONCAT('US    short rows to delete: ', @us);
PRINT CONCAT('India short rows to delete: ', @in);

-- Safety: short trades should be a tiny stale residue. Abort if a table has an
-- unexpectedly large number of them (likely a wrong filter / data anomaly).
IF @us > 50 OR @in > 50
BEGIN
    PRINT 'Short-row counts exceed safety threshold -- rolling back, nothing deleted.';
    ROLLBACK TRAN;
    RETURN;
END

DELETE FROM dbo.USBreakouts     WHERE Direction = 'short';
PRINT CONCAT('Deleted US short:    ', @@ROWCOUNT);

DELETE FROM dbo.IndianBreakouts WHERE Direction = 'short';
PRINT CONCAT('Deleted India short: ', @@ROWCOUNT);

COMMIT TRAN;
PRINT 'Committed.';
