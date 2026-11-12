CREATE TABLE [options].[DerivativeInstruments]
(
    [InstrumentToken] BIGINT NOT NULL,
    [Exchange] NVARCHAR(10) NOT NULL,
    [TradingSymbol] NVARCHAR(100) NOT NULL,
    [Underlying] NVARCHAR(20) NOT NULL,
    [Expiry] DATE NULL,
    [Strike] DECIMAL(20,6) NOT NULL,
    [InstrumentType] NVARCHAR(10) NOT NULL,
    [LotSize] INT NOT NULL,
    [TickSize] DECIMAL(12,6) NOT NULL,
    [UpdatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_DerivativeInstruments_UpdatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_Options_DerivativeInstruments] PRIMARY KEY CLUSTERED ([Exchange], [TradingSymbol]),
    CONSTRAINT [CK_Options_DerivativeInstruments_Token] CHECK ([InstrumentToken] > 0),
    CONSTRAINT [CK_Options_DerivativeInstruments_Contract] CHECK ([Strike] >= 0 AND [LotSize] > 0 AND [TickSize] > 0),
    INDEX [IX_Options_DerivativeInstruments_Token] NONCLUSTERED ([InstrumentToken]),
    INDEX [IX_Options_DerivativeInstruments_Chain] NONCLUSTERED ([Underlying], [Exchange], [Expiry], [InstrumentType], [Strike])
);
