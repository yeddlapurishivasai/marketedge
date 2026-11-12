SET NOCOUNT ON;

UPDATE target
SET [Name] = source.[Name],
    [SpotSymbol] = source.[SpotSymbol],
    [DerivativeExchange] = source.[DerivativeExchange]
FROM [options].[MarketIndices] AS target
INNER JOIN
(
    VALUES
        (N'NIFTY50', N'NIFTY 50', N'NSE:NIFTY 50', N'NFO'),
        (N'SENSEX', N'SENSEX', N'BSE:SENSEX', N'BFO')
) AS source ([Code], [Name], [SpotSymbol], [DerivativeExchange])
    ON target.[Code] = source.[Code];

INSERT INTO [options].[MarketIndices] ([Code], [Name], [SpotSymbol], [DerivativeExchange])
SELECT source.[Code], source.[Name], source.[SpotSymbol], source.[DerivativeExchange]
FROM
(
    VALUES
        (N'NIFTY50', N'NIFTY 50', N'NSE:NIFTY 50', N'NFO'),
        (N'SENSEX', N'SENSEX', N'BSE:SENSEX', N'BFO')
) AS source ([Code], [Name], [SpotSymbol], [DerivativeExchange])
WHERE NOT EXISTS
(
    SELECT 1
    FROM [options].[MarketIndices] AS target WITH (UPDLOCK, HOLDLOCK)
    WHERE target.[Code] = source.[Code]
);
