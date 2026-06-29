/*
  One-time cleanup: delete India SHORT triggers flagged today.

  Breakouts are now long-only (shorts become a future "breakdowns" feature), but a few
  short setups got flagged in prod before the long-only deploy. This removes today's
  India short rows from both the breakout blotter and the near-pivot watchlist.

  Safe to run once against MarketEdge (prod). Wrapped in a transaction with a preview;
  inspect the SELECT output, then COMMIT (or ROLLBACK to abort).

  Run:
    sqlcmd -S market-edge-dr-sql-server-01.database.windows.net -d MarketEdge -U sqladmin -C -i deploy\onetime\delete-india-short-triggers.sql
*/
SET NOCOUNT ON;
BEGIN TRAN;

-- Preview what will be deleted
SELECT 'IndianBreakouts' AS tbl, Id, Ticker, TradeType, Direction, EntryAt
FROM dbo.IndianBreakouts
WHERE Direction = 'short' AND CAST(EntryAt AS DATE) = CAST(GETUTCDATE() AS DATE);

SELECT 'IndianNearPivots' AS tbl, Id, Ticker, TradeType, Direction, ScanDate
FROM dbo.IndianNearPivots
WHERE Direction = 'short' AND ScanDate = CAST(GETUTCDATE() AS DATE);

DELETE FROM dbo.IndianBreakouts
WHERE Direction = 'short' AND CAST(EntryAt AS DATE) = CAST(GETUTCDATE() AS DATE);
PRINT CONCAT('IndianBreakouts short rows deleted: ', @@ROWCOUNT);

DELETE FROM dbo.IndianNearPivots
WHERE Direction = 'short' AND ScanDate = CAST(GETUTCDATE() AS DATE);
PRINT CONCAT('IndianNearPivots short rows deleted: ', @@ROWCOUNT);

-- Inspect the printed counts above, then commit. ROLLBACK instead if anything looks wrong.
COMMIT TRAN;
