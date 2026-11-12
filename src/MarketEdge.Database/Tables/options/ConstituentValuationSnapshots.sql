CREATE TABLE [options].[ConstituentValuationSnapshots]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [ForecastRunId] INT NOT NULL,
    [StockId] INT NOT NULL,
    [PayloadJson] NVARCHAR(MAX) NOT NULL,
    CONSTRAINT [PK_Options_ConstituentValuationSnapshots] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_ConstituentValuationSnapshots_ForecastStock] UNIQUE ([ForecastRunId], [StockId]),
    CONSTRAINT [FK_Options_ConstituentValuationSnapshots_ForecastRuns] FOREIGN KEY ([ForecastRunId]) REFERENCES [options].[ForecastRuns] ([Id]),
    CONSTRAINT [FK_Options_ConstituentValuationSnapshots_IndianStocks] FOREIGN KEY ([StockId]) REFERENCES [dbo].[IndianStocks] ([Id]),
    CONSTRAINT [CK_Options_ConstituentValuationSnapshots_PayloadJson] CHECK (ISJSON([PayloadJson]) = 1),
    INDEX [IX_Options_ConstituentValuationSnapshots_StockId] NONCLUSTERED ([StockId])
);
