# Schema Contract: Market Data Ingestion (SQL Server / dacpac)

Source of truth for the **10** base-data tables added under
`src/MarketEdge.Database/Tables/`. Adapted from the "MarketEdge v2" proposal to the
existing SQL Server + dacpac stack (constitution Principle I): NVARCHAR strings, `BIT`
booleans, `DATETIME2` timestamps, `GETUTCDATE()` defaults, named
`PK_/FK_/CK_/UX_/IX_` constraints, one `.sql` file per table.

**Per-market rule**: each table is a `Indian*` / `US*` pair, identical except
`Ticker NVARCHAR(30)` (India) vs `NVARCHAR(20)` (US) and the currency comment
(INR for `Indian*`, USD for `US*`). Below, the US variant is authoritative; the
Indian mirror differs only by ticker length, currency comment, and the `Indian`
name prefix.

## Type mapping (v2 proposal → SQL Server)

| Proposal | SQL Server |
|----------|------------|
| `VARCHAR(n)` | `NVARCHAR(n)` |
| `BOOLEAN` / `DEFAULT TRUE`/`FALSE` | `BIT` / `DEFAULT 1`/`0` |
| `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | `DATETIME2 DEFAULT GETUTCDATE()` |
| `DATE` | `DATE` |
| `CHAR(1)` | `CHAR(1)` |
| `DECIMAL(p,s)` / `BIGINT` / `INT` | unchanged |

## 1. `{Indian|US}Tickers` — master list (one row per symbol)

```sql
CREATE TABLE [dbo].[USTickers]
(
    [Ticker]         NVARCHAR(20) NOT NULL,   -- NVARCHAR(30) for IndianTickers
    [Exchange]       NVARCHAR(20) NULL,       -- NASDAQ / NYSE / AMEX  (NSE / BSE for India)
    [Active]         BIT          NOT NULL CONSTRAINT [DF_USTickers_Active] DEFAULT (1),
    [IsFno]          BIT          NOT NULL CONSTRAINT [DF_USTickers_IsFno] DEFAULT (0),
    [BarsAvailable]  INT          NULL,
    [CreatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_USTickers_CreatedAt] DEFAULT GETUTCDATE(),
    [UpdatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_USTickers_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USTickers] PRIMARY KEY CLUSTERED ([Ticker]),
    INDEX [IX_USTickers_Active] NONCLUSTERED ([Active])
);
```

## 2. `{Indian|US}TickerTechnical` — daily RS + price snapshot

```sql
CREATE TABLE [dbo].[USTickerTechnical]
(
    [Ticker]          NVARCHAR(20) NOT NULL,   -- NVARCHAR(30) for India
    [AsOfDate]        DATE         NOT NULL,
    [Close]           DECIMAL(18,4) NULL,      -- USD (INR for Indian*)
    [DayPct]          DECIMAL(8,4)  NULL,
    [Open]            DECIMAL(18,4) NULL,
    [High]            DECIMAL(18,4) NULL,
    [Low]             DECIMAL(18,4) NULL,
    [High52w]         DECIMAL(18,4) NULL,
    [From52wHigh]     DECIMAL(8,4)  NULL,
    [MarketCap]       BIGINT        NULL,      -- USD (INR for Indian*)
    [Rs]              INT           NULL,
    [Rs1d]            INT           NULL,
    [Rs1w]            INT           NULL,
    [Rs1m]            INT           NULL,
    [Rs3m]            INT           NULL,
    [Rs6m]            INT           NULL,
    [RsType]          NVARCHAR(20)  NULL,
    [RsDate]          DATE          NULL,
    [ScannerHits]     INT           NULL,
    [LastScannerHit]  DATE          NULL,
    [UpdatedAt]       DATETIME2     NOT NULL CONSTRAINT [DF_USTickerTechnical_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USTickerTechnical] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_USTickerTechnical_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    INDEX [IX_USTickerTechnical_AsOfDate] NONCLUSTERED ([AsOfDate])
);
```

## 3. `{Indian|US}AnalystSnapshot` — headline consensus card

```sql
CREATE TABLE [dbo].[USAnalystSnapshot]
(
    [Ticker]            NVARCHAR(20) NOT NULL,   -- NVARCHAR(30) for India
    [AsOfDate]          DATE         NOT NULL,
    [ConsensusRating]   NVARCHAR(15) NULL,       -- Buy / Hold / Sell / Strong Buy / Strong Sell
    [NumAnalysts]       INT          NULL,
    [CurrentQuarterEps] DECIMAL(10,4) NULL,
    [NextQuarterEps]    DECIMAL(10,4) NULL,
    [CurrentYearEps]    DECIMAL(10,4) NULL,
    [NextYearEps]       DECIMAL(10,4) NULL,
    [UpdatedAt]         DATETIME2    NOT NULL CONSTRAINT [DF_USAnalystSnapshot_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USAnalystSnapshot] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate]),
    CONSTRAINT [FK_USAnalystSnapshot_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker])
);
```

## 4. `{Indian|US}EpsForecasts` — per-period EPS forecasts (Q + Y)

`PeriodType` discriminates Quarterly vs Yearly; `AsOfDate` is part of the PK so
revision history is preserved.

```sql
CREATE TABLE [dbo].[USEpsForecasts]
(
    [Ticker]         NVARCHAR(20) NOT NULL,   -- NVARCHAR(30) for India
    [AsOfDate]       DATE         NOT NULL,
    [PeriodType]     CHAR(1)      NOT NULL,   -- 'Q' or 'Y'
    [PeriodEndDate]  DATE         NOT NULL,
    [ConsensusEps]   DECIMAL(10,4) NULL,
    [HighEps]        DECIMAL(10,4) NULL,
    [LowEps]         DECIMAL(10,4) NULL,
    [NumEstimates]   INT          NULL,
    [RevisionsUp]    INT          NOT NULL CONSTRAINT [DF_USEpsForecasts_RevisionsUp] DEFAULT (0),
    [RevisionsDown]  INT          NOT NULL CONSTRAINT [DF_USEpsForecasts_RevisionsDown] DEFAULT (0),
    [UpdatedAt]      DATETIME2    NOT NULL CONSTRAINT [DF_USEpsForecasts_UpdatedAt] DEFAULT GETUTCDATE(),
    CONSTRAINT [PK_USEpsForecasts] PRIMARY KEY CLUSTERED ([Ticker], [AsOfDate], [PeriodType], [PeriodEndDate]),
    CONSTRAINT [FK_USEpsForecasts_USTickers] FOREIGN KEY ([Ticker]) REFERENCES [dbo].[USTickers]([Ticker]),
    CONSTRAINT [CK_USEpsForecasts_PeriodType] CHECK ([PeriodType] IN ('Q', 'Y')),
    INDEX [IX_USEpsForecasts_Period] NONCLUSTERED ([Ticker], [PeriodType], [PeriodEndDate])
);
```

## 5. `{Indian|US}Bars1D` — daily OHLCV history

```sql
CREATE TABLE [dbo].[USBars1D]
(
    [Ticker]     NVARCHAR(20) NOT NULL,   -- NVARCHAR(30) for India
    [BarDate]    DATE         NOT NULL,
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
```

## 6. (removed) intraday 5-minute bars

Intraday 5-minute bars are intentionally **not** part of this feature; weekly Stage 2
/ sector rotation does not need them. If intraday support is added later it will be a
separate feature.

## Indian mirror

For each table above, the `Indian*` file is identical except:

- Table/constraint/index name prefix `US` → `Indian` (e.g. `IndianBars1D`,
  `PK_IndianBars1D`, `FK_IndianBars1D_IndianTickers`).
- `Ticker` and FK column type `NVARCHAR(20)` → `NVARCHAR(30)`.
- Currency comment `USD` → `INR`.

## Publish ordering (FK dependency)

The dacpac resolves order automatically, but logically the master must exist first:

1. `IndianTickers`, `USTickers`
2. `IndianTickerTechnical`, `USTickerTechnical`
3. `IndianAnalystSnapshot`, `USAnalystSnapshot`, `IndianEpsForecasts`, `USEpsForecasts`
4. `IndianBars1D`, `USBars1D`

## Ingestion DML (pipeline, not schema)

| Table | Natural key (MERGE target) |
|-------|----------------------------|
| `*Tickers` | `(Ticker)` |
| `*TickerTechnical` | `(Ticker, AsOfDate)` |
| `*AnalystSnapshot` | `(Ticker, AsOfDate)` |
| `*EpsForecasts` | `(Ticker, AsOfDate, PeriodType, PeriodEndDate)` |
| `*Bars1D` | `(Ticker, BarDate)` |

All writes are idempotent `MERGE` upserts; NaN/inf → `NULL`; market resolved via a
`{india,us} → table-set` map.
