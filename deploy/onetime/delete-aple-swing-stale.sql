/*
  One-time cleanup: delete the stale APLE US *swing* breakout.

  A single active US swing row for APLE (Id 146, entered 2026-06-29 11:56:56 UTC,
  ConfidenceScore 47.71) is stale and should be removed from the blotter. Scoped to
  Ticker = 'APLE' AND TradeType = 'swing' so it can never touch the positional row or
  any other ticker.

  Transactional + self-guarding: previews the matched row(s), aborts (ROLLBACK, nothing
  deleted) if it somehow matches more than one row, otherwise commits.

  Run:
    sqlcmd -S market-edge-dr-sql-server-01.database.windows.net -d MarketEdge -U sqladmin -C -i deploy\onetime\delete-aple-swing-stale.sql
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRAN;

-- Preview what will be deleted.
SELECT Id, Ticker, TradeType, Direction, Status,
       CONVERT(varchar(23), EntryAt, 121) AS EntryAt_UTC, ConfidenceScore
FROM dbo.USBreakouts
WHERE Ticker = 'APLE' AND TradeType = 'swing';

DECLARE @cnt int = (SELECT COUNT(*) FROM dbo.USBreakouts WHERE Ticker = 'APLE' AND TradeType = 'swing');
PRINT CONCAT('APLE swing rows to delete: ', @cnt, ' (expected 1)');

-- Safety: only ever remove the single stale swing row.
IF @cnt > 1
BEGIN
    PRINT 'Matched more than one row -- rolling back, nothing deleted.';
    ROLLBACK TRAN;
    RETURN;
END

DELETE FROM dbo.USBreakouts WHERE Ticker = 'APLE' AND TradeType = 'swing';
PRINT CONCAT('Deleted: ', @@ROWCOUNT);

COMMIT TRAN;
PRINT 'Committed.';
