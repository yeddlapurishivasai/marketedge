CREATE TABLE [dbo].[USStockNote]
(
    [Ticker]     NVARCHAR(20)  NOT NULL,
    [NoteText]   NVARCHAR(MAX) NULL,
    [UpdatedAt]  DATETIME2     NOT NULL CONSTRAINT [DF_USStockNote_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USStockNote] PRIMARY KEY CLUSTERED ([Ticker]),
    CONSTRAINT [FK_USStockNote_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker])
);
