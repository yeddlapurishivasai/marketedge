/*
  One-time cleanup: delete US breakouts with confidence below 60.

  Removes low-conviction swing AND positional paper breakouts from the US blotter where the
  computed ConfidenceScore is under 60. Rows with a NULL ConfidenceScore (no canonical
  fundamental score yet) are intentionally LEFT untouched -- a NULL is "no score", not
  "below 60". To also drop those, change the WHERE clause to:
      WHERE Direction = 'long' AND (ConfidenceScore < 60 OR ConfidenceScore IS NULL)

  Safe to run once against MarketEdge (prod). Wrapped in a transaction with a preview;
  inspect the SELECT output and printed count, then COMMIT (or ROLLBACK to abort).

  Run:
    sqlcmd -S market-edge-dr-sql-server-01.database.windows.net -d MarketEdge -U sqladmin -C -i deploy\onetime\delete-us-breakouts-below-60.sql
*/
SET NOCOUNT ON;
BEGIN TRAN;

-- Preview what will be deleted (by trade type)
SELECT TradeType, COUNT(*) AS to_delete, MIN(ConfidenceScore) AS min_score, MAX(ConfidenceScore) AS max_score
FROM dbo.USBreakouts
WHERE ConfidenceScore < 60
GROUP BY TradeType;

DELETE FROM dbo.USBreakouts
WHERE ConfidenceScore < 60;
PRINT CONCAT('USBreakouts rows deleted (ConfidenceScore < 60): ', @@ROWCOUNT);

-- Inspect the preview + printed count above, then commit. ROLLBACK instead if anything looks wrong.
COMMIT TRAN;
