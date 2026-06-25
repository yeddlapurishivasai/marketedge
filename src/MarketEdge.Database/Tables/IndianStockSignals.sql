CREATE TABLE [dbo].[IndianStockSignals]
(
    [Ticker]            NVARCHAR(30)  NOT NULL,

    -- Capital Work In Progress (CWIP / "Construction In Progress"): early major-capex signal
    [CapexCwip]         DECIMAL(20,2) NULL,          -- latest reported quarter
    [CapexCwipPrevQ]    DECIMAL(20,2) NULL,          -- prior quarter
    [CapexChangePct]    DECIMAL(12,4) NULL,          -- QoQ % change
    [CapexTrend]        NVARCHAR(12)  NULL,          -- rising / falling / flat
    [CapexAsOf]         DATE          NULL,          -- quarter-end the CWIP figure is dated to

    -- Raw yfinance news headlines (recent window), structured for the UI list
    [NewsJson]          NVARCHAR(MAX) NULL,

    -- Compact, token-friendly summary of all auto-detected signals (AI workflow input,
    -- kept SEPARATE from the user-entered IndianStockNote.NoteText "additional context")
    [SignalsText]       NVARCHAR(MAX) NULL,

    [UpdatedAt]         DATETIME2     NOT NULL CONSTRAINT [DF_IndianStockSignals_UpdatedAt] DEFAULT GETUTCDATE(),

    CONSTRAINT [PK_IndianStockSignals] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_IndianStockSignals_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker])
);
