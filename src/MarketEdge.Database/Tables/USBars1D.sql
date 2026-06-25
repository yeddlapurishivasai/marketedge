CREATE TABLE [dbo].[USBars1D]
(
    [Ticker]     NVARCHAR(20)  NOT NULL,
    [BarDate]    DATE          NOT NULL,
    [Open]       DECIMAL(18,4) NULL,
    [High]       DECIMAL(18,4) NULL,
    [Low]        DECIMAL(18,4) NULL,
    [Close]      DECIMAL(18,4) NULL,
    [Volume]     BIGINT        NULL,
    [AdjClose]   DECIMAL(18,4) NULL,
    CONSTRAINT [PK_USBars1D] PRIMARY KEY CLUSTERED ([Ticker], [BarDate]),
    CONSTRAINT [FK_USBars1D_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USBars1D_BarDate] NONCLUSTERED ([BarDate])
);
