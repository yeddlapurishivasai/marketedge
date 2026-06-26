CREATE TABLE [dbo].[IndianStockNote]
(
    [Ticker]     NVARCHAR(30)  NOT NULL,
    [NoteText]   NVARCHAR(MAX) NULL,
    [UpdatedAt]  DATETIME2     NOT NULL CONSTRAINT [DF_IndianStockNote_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_IndianStockNote] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_IndianStockNote_IndianTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[IndianTickers]([Ticker])
);
