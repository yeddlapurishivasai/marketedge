/*
  One-time cleanup: delete the paper-breakout rows opened by the Jun-29 manual
  pivot scan runs that should not have been opened.

  EntryAt is stored in UTC (the UI also displays UTC -- nothing is IST). Each run
  inserts its whole batch within ~1-3 seconds, and both runs are cleanly isolated
  (no other breakout rows fall between/around these windows -- verified on prod
  2026-06-29):

    * US     scan run @ 2026-06-29 12:27:55-58 UTC  -> window 12:27:50 .. 12:28:05
             expected 192 rows (Ids 147-338)
    * India  scan run @ 2026-06-29 12:36:10     UTC  -> window 12:36:05 .. 12:36:15
             expected  36 rows (Ids  74-109)

  Transactional + self-guarding: previews the counts, aborts (ROLLBACK, nothing
  deleted) if either window matches far more than the known run, otherwise commits.

  Run:
    sqlcmd -S market-edge-dr-sql-server-01.database.windows.net -d MarketEdge -U sqladmin -C -i deploy\onetime\delete-breakouts-jun29-scan.sql
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @usFrom datetime2 = '2026-06-29 12:27:50',
        @usTo   datetime2 = '2026-06-29 12:28:05',
        @inFrom datetime2 = '2026-06-29 12:36:05',
        @inTo   datetime2 = '2026-06-29 12:36:15';

BEGIN TRAN;

DECLARE @us int = (SELECT COUNT(*) FROM dbo.USBreakouts     WHERE EntryAt >= @usFrom AND EntryAt < @usTo);
DECLARE @in int = (SELECT COUNT(*) FROM dbo.IndianBreakouts WHERE EntryAt >= @inFrom AND EntryAt < @inTo);

PRINT CONCAT('US    rows to delete: ', @us, ' (expected 192)');
PRINT CONCAT('India rows to delete: ', @in, ' (expected 36)');

-- Safety: if the windows somehow match well beyond the known scan runs, abort.
IF @us > 200 OR @in > 50
BEGIN
    PRINT 'Row counts exceed safety threshold -- rolling back, nothing deleted.';
    ROLLBACK TRAN;
    RETURN;
END

DELETE FROM dbo.USBreakouts     WHERE EntryAt >= @usFrom AND EntryAt < @usTo;
PRINT CONCAT('Deleted US:    ', @@ROWCOUNT);

DELETE FROM dbo.IndianBreakouts WHERE EntryAt >= @inFrom AND EntryAt < @inTo;
PRINT CONCAT('Deleted India: ', @@ROWCOUNT);

COMMIT TRAN;
PRINT 'Committed.';
