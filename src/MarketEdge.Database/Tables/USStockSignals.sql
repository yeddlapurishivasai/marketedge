CREATE TABLE [dbo].[USStockSignals]
(
    [Ticker]            NVARCHAR(20)  NOT NULL,

    -- Capital Work In Progress (CWIP / "Construction In Progress"): early major-capex signal.
    -- Many US issuers do not report CWIP separately, so these are frequently NULL; the US
    -- flow leans on NewsJson instead.
    [CapexCwip]         DECIMAL(20,2) NULL,          -- latest reported quarter
    [CapexCwipPrevQ]    DECIMAL(20,2) NULL,          -- prior quarter
    [CapexChangePct]    DECIMAL(12,4) NULL,          -- QoQ % change
    [CapexTrend]        NVARCHAR(12)  NULL,          -- rising / falling / flat

    -- Raw yfinance news headlines (recent window), structured for the UI list
    [NewsJson]          NVARCHAR(MAX) NULL,

    -- Compact, token-friendly summary of all auto-detected signals (AI workflow input,
    -- kept SEPARATE from the user-entered USStockNote.NoteText "additional context")
    [SignalsText]       NVARCHAR(MAX) NULL,

    [UpdatedAt]         DATETIME2     NOT NULL CONSTRAINT [DF_USStockSignals_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_USStockSignals] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_USStockSignals_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker])
);
