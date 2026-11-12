CREATE TABLE [options].[StrategySuggestions]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [ForecastRunId] INT NOT NULL,
    [CandidateKey] NVARCHAR(100) NOT NULL,
    [Side] NVARCHAR(10) NOT NULL,
    [RiskTier] NVARCHAR(10) NOT NULL,
    [Strategy] NVARCHAR(40) NOT NULL,
    [Expiry] DATE NOT NULL,
    [PayloadJson] NVARCHAR(MAX) NOT NULL,
    CONSTRAINT [PK_Options_StrategySuggestions] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_StrategySuggestions_Candidate] UNIQUE ([ForecastRunId], [CandidateKey]),
    CONSTRAINT [FK_Options_StrategySuggestions_ForecastRuns] FOREIGN KEY ([ForecastRunId]) REFERENCES [options].[ForecastRuns] ([Id]),
    CONSTRAINT [CK_Options_StrategySuggestions_RiskTier] CHECK ([RiskTier] IN (N'low', N'medium', N'high')),
    CONSTRAINT [CK_Options_StrategySuggestions_Structure] CHECK (
        ([Side] = N'selling' AND [Strategy] IN (N'put-credit-spread', N'call-credit-spread', N'iron-condor', N'iron-fly')) OR
        ([Side] = N'buying' AND [Strategy] IN (N'long-call', N'long-put', N'call-debit-spread', N'put-debit-spread'))),
    CONSTRAINT [CK_Options_StrategySuggestions_PayloadJson] CHECK (ISJSON([PayloadJson]) = 1)
);
