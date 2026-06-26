/*
================================================================================
  Pre-deployment migration: Trades -> Breakouts (+ drop obsolete StockScores)
================================================================================

  PURPOSE
    Bring an existing MarketEdge database in line with the new dacpac WITHOUT
    losing the long-running ingested data, and WITHOUT tripping sqlpackage's
    BlockOnPossibleDataLoss guard.

  WHAT IT DOES
    1. Renames the {Indian|US}Trades tables (and all their constraints/indexes)
       to the new {Indian|US}Breakouts names. Because the column layout is
       IDENTICAL, this is a pure metadata rename: every existing trade row is
       preserved and the post-migration schema exactly matches the dacpac, so
       the subsequent publish performs NO drop/recreate of these tables.
    2. Drops the obsolete {Indian|US}StockScores tables. The standing per-stock
       score subsystem was removed; this data is disposable.

  WHAT IT DOES *NOT* TOUCH
    Tickers, fundamentals, EPS history, stage-analysis, schedules, job runs,
    scoring weights and every other ingested table are left completely intact.

  SAFETY
    - Fully idempotent: guarded by existence checks, so re-running is a no-op.
    - Wrapped in a transaction; any failure rolls the whole thing back.
    - Run this BEFORE `sqlpackage /Action:Publish`. With this applied, the
      publish can (and should) keep BlockOnPossibleDataLoss = TRUE.
================================================================================
*/

SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRANSACTION;

PRINT '--- Migrating Trades -> Breakouts ---';

---------------------------------------------------------------------------
-- INDIAN
---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.IndianTrades', N'U') IS NOT NULL
   AND OBJECT_ID(N'dbo.IndianBreakouts', N'U') IS NULL
BEGIN
    PRINT 'Renaming IndianTrades -> IndianBreakouts';
    EXEC sp_rename N'dbo.IndianTrades', N'IndianBreakouts';

    -- Non-clustered indexes (reference the table by its NEW name)
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_IndianTrades_Ticker_Type_Status' AND object_id = OBJECT_ID(N'dbo.IndianBreakouts'))
        EXEC sp_rename N'dbo.IndianBreakouts.IX_IndianTrades_Ticker_Type_Status', N'IX_IndianBreakouts_Ticker_Type_Status', N'INDEX';
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_IndianTrades_Status' AND object_id = OBJECT_ID(N'dbo.IndianBreakouts'))
        EXEC sp_rename N'dbo.IndianBreakouts.IX_IndianTrades_Status', N'IX_IndianBreakouts_Status', N'INDEX';

    -- Primary key / foreign key / default constraints (object-level rename)
    IF OBJECT_ID(N'dbo.PK_IndianTrades') IS NOT NULL
        EXEC sp_rename N'dbo.PK_IndianTrades', N'PK_IndianBreakouts', N'OBJECT';
    IF OBJECT_ID(N'dbo.FK_IndianTrades_IndianTickers') IS NOT NULL
        EXEC sp_rename N'dbo.FK_IndianTrades_IndianTickers', N'FK_IndianBreakouts_IndianTickers', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_IndianTrades_ScannerHitCount') IS NOT NULL
        EXEC sp_rename N'dbo.DF_IndianTrades_ScannerHitCount', N'DF_IndianBreakouts_ScannerHitCount', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_IndianTrades_MovedToBe') IS NOT NULL
        EXEC sp_rename N'dbo.DF_IndianTrades_MovedToBe', N'DF_IndianBreakouts_MovedToBe', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_IndianTrades_CreatedAt') IS NOT NULL
        EXEC sp_rename N'dbo.DF_IndianTrades_CreatedAt', N'DF_IndianBreakouts_CreatedAt', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_IndianTrades_UpdatedAt') IS NOT NULL
        EXEC sp_rename N'dbo.DF_IndianTrades_UpdatedAt', N'DF_IndianBreakouts_UpdatedAt', N'OBJECT';
END
ELSE
    PRINT 'IndianTrades rename skipped (already migrated or table absent).';

---------------------------------------------------------------------------
-- US
---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.USTrades', N'U') IS NOT NULL
   AND OBJECT_ID(N'dbo.USBreakouts', N'U') IS NULL
BEGIN
    PRINT 'Renaming USTrades -> USBreakouts';
    EXEC sp_rename N'dbo.USTrades', N'USBreakouts';

    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_USTrades_Ticker_Type_Status' AND object_id = OBJECT_ID(N'dbo.USBreakouts'))
        EXEC sp_rename N'dbo.USBreakouts.IX_USTrades_Ticker_Type_Status', N'IX_USBreakouts_Ticker_Type_Status', N'INDEX';
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_USTrades_Status' AND object_id = OBJECT_ID(N'dbo.USBreakouts'))
        EXEC sp_rename N'dbo.USBreakouts.IX_USTrades_Status', N'IX_USBreakouts_Status', N'INDEX';

    IF OBJECT_ID(N'dbo.PK_USTrades') IS NOT NULL
        EXEC sp_rename N'dbo.PK_USTrades', N'PK_USBreakouts', N'OBJECT';
    IF OBJECT_ID(N'dbo.FK_USTrades_USTickers') IS NOT NULL
        EXEC sp_rename N'dbo.FK_USTrades_USTickers', N'FK_USBreakouts_USTickers', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_USTrades_ScannerHitCount') IS NOT NULL
        EXEC sp_rename N'dbo.DF_USTrades_ScannerHitCount', N'DF_USBreakouts_ScannerHitCount', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_USTrades_MovedToBe') IS NOT NULL
        EXEC sp_rename N'dbo.DF_USTrades_MovedToBe', N'DF_USBreakouts_MovedToBe', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_USTrades_CreatedAt') IS NOT NULL
        EXEC sp_rename N'dbo.DF_USTrades_CreatedAt', N'DF_USBreakouts_CreatedAt', N'OBJECT';
    IF OBJECT_ID(N'dbo.DF_USTrades_UpdatedAt') IS NOT NULL
        EXEC sp_rename N'dbo.DF_USTrades_UpdatedAt', N'DF_USBreakouts_UpdatedAt', N'OBJECT';
END
ELSE
    PRINT 'USTrades rename skipped (already migrated or table absent).';

---------------------------------------------------------------------------
-- Drop obsolete StockScores tables (standing per-stock score system removed)
---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.IndianStockScores', N'U') IS NOT NULL
BEGIN
    PRINT 'Dropping obsolete IndianStockScores';
    DROP TABLE dbo.IndianStockScores;
END
IF OBJECT_ID(N'dbo.USStockScores', N'U') IS NOT NULL
BEGIN
    PRINT 'Dropping obsolete USStockScores';
    DROP TABLE dbo.USStockScores;
END

COMMIT TRANSACTION;

PRINT '--- Migration complete ---';
