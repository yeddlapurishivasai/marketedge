CREATE TABLE [options].[MarketIndices]
(
    [Code] NVARCHAR(20) NOT NULL,
    [Name] NVARCHAR(100) NOT NULL,
    [SpotSymbol] NVARCHAR(80) NOT NULL,
    [DerivativeExchange] NVARCHAR(10) NOT NULL,
    CONSTRAINT [PK_Options_MarketIndices] PRIMARY KEY CLUSTERED ([Code]),
    CONSTRAINT [CK_Options_MarketIndices_Code] CHECK ([Code] IN (N'NIFTY50', N'SENSEX')),
    CONSTRAINT [CK_Options_MarketIndices_Exchange] CHECK (
        ([Code] = N'NIFTY50' AND [DerivativeExchange] = N'NFO') OR
        ([Code] = N'SENSEX' AND [DerivativeExchange] = N'BFO'))
);
