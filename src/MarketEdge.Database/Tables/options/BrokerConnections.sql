CREATE TABLE [options].[BrokerConnections]
(
    [Id] INT IDENTITY(1,1) NOT NULL,
    [UserKey] NVARCHAR(200) NOT NULL,
    [BrokerUserId] NVARCHAR(50) NULL,
    [EncryptedToken] NVARCHAR(MAX) NULL,
    [LoginAt] DATETIME2 NULL,
    [ExpiresAt] DATETIME2 NULL,
    [Status] NVARCHAR(30) NOT NULL CONSTRAINT [DF_Options_BrokerConnections_Status] DEFAULT (N'disconnected'),
    [LastError] NVARCHAR(1000) NULL,
    [UpdatedAt] DATETIME2 NOT NULL CONSTRAINT [DF_Options_BrokerConnections_UpdatedAt] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_Options_BrokerConnections] PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT [UQ_Options_BrokerConnections_UserKey] UNIQUE ([UserKey]),
    CONSTRAINT [CK_Options_BrokerConnections_Status] CHECK ([Status] IN (N'disconnected', N'connected', N'relogin-required', N'error', N'pending')),
    CONSTRAINT [CK_Options_BrokerConnections_Expiry] CHECK ([ExpiresAt] IS NULL OR [LoginAt] IS NULL OR [ExpiresAt] > [LoginAt])
);
