# NIFTY50 / SENSEX options workspace

The India-only workspace is available at `/india/options`. It uses paid Zerodha
market data and the financial results already stored in MarketEdge. It does not
place orders or automatically execute exits.

## Database deployment

All new feature tables live in `[options]`. Existing stocks, financials and jobs
stay in `[dbo]`. Schema ownership remains the SQL project; do not use EF migrations.

```powershell
dotnet build src\MarketEdge.Database\MarketEdge.Database.sqlproj
sqlpackage /Action:Publish /SourceFile:src\MarketEdge.Database\bin\Debug\MarketEdge.Database.dacpac /TargetServerName:localhost /TargetDatabaseName:MarketEdge /p:TrustServerCertificate=True
```

Publishing seeds the two index definitions, not constituent weights or exchange
calendars. Existing stock seed data is still managed separately.

For hosted releases, use the existing `deploy\deploy-database.ps1` and
`deploy\deploy-apps.ps1` scripts without `-SeedData` or `-Configure` when the
production connections are already configured. With frontend dependencies
already installed for the current lockfile, set `$env:SkipNpmInstall = 'true'`
before the application deployment to rebuild the SPA without reinstalling its
packages. Database deployment retains its data-loss protection.

The worker automatically refreshes public reference data before forecasts and in
its supervised collector. Users do not need to enter routine trading dates,
closing times, rates, dividend yield or a fixed cost allowance.

- NSE cash-market holidays and normal hours come from the public NSE endpoints.
- BSE holidays come from its separate equity calendar; cash hours use BSE notice
  20260801-1. Known exceptional sessions have their own dated exchange notices.
- Only published calendar years are populated. Unverified special-session hours
  remain explicit gaps, not invented closures or ordinary weekdays.
- NIFTY50 membership comes from Nifty Indices. The approved weight source is the
  issuer-published Nippon NiftyBeES monthly portfolio, joined to members by ISIN
  and equity-normalized to 100%. This is prominently labelled a **dated ETF
  holdings proxy, not official or live index weights**. Raw equity NAV coverage,
  source date, source URL and method are retained. Snapshots older than 62 days
  are rejected; constituents must map to existing stock catalogue records.
- No complete public SENSEX weight source is currently integrated; its weighted
  fundamentals remain unavailable rather than borrowing NIFTY weights.

The worker `/status` exposes the active rules under `optionsCollector.configVersion`
and reference refresh outcomes under
`optionsCollector.referenceData`. Failed sources retain explicit warnings and
retry with bounded backoff. Successful sources refresh daily. Existing manual
overrides and historical snapshots are preserved. Administrator metadata overrides
remain available through the protected API, not an import/editor section in the
forecast workspace.

Zerodha's daily instrument master is the only source of listed option expiry
dates, strikes and lot sizes. Weekly selects the nearest listed expiry strictly
after today; same-day expiry belongs to Expiry Intraday. Monthly selects a listed
monthly-style contract by its existing three-letter month symbol, validated
against its actual expiry, exchange, strike and option type. It never assumes the
last listed date in a month is the monthly expiry, constructs a trading symbol or
uses imported expiry flags to select a contract. Unknown monthly naming fails
explicitly rather than guessing. The master is not a holiday/session-hours
calendar. The workspace never hardcodes Tuesday or invents missing trading dates.
When application authentication is enabled, metadata writes require the separate
`Options.MetadataAdmin` role (configurable via `Options__MetadataAdminRole`);
ordinary API read access is insufficient.

## Zerodha configuration and login

Configure these on the API using environment variables or secret storage, never
by committing credentials:

| API setting | Purpose |
|---|---|
| `Options__KiteApiKey` | Paid Kite app API key |
| `Options__KiteApiSecret` | Kite app secret; server only |
| `Options__KiteRedirectUrl` | Exact callback URL registered with Kite, ending `/api/india/options/connection/callback` |
| `Options__TokenEncryptionKey` | Base64 random 32-byte AES key shared securely with the worker |
| `Options__AiEnabled` | Enable final AI selection using the existing Foundry configuration; disabled selection is recorded as terminal `skipped`, without a provider call |

Use the official **Connect to Zerodha / Re-login** button. Broker login is
separate from MarketEdge application sign-in. Do not enter broker passwords,
two-factor codes or access tokens into MarketEdge forms.

Kite documents access-token expiry at **06:00 IST the next day**, not 24 hours
after login. Revocation/master logout can expire a session earlier. Re-login
requires the user; ordinary retail access does not imply a usable refresh token.
Saved forecasts remain readable without a current broker session.
The generic job list, detail and cancellation endpoints also enforce forecast
ownership for options jobs; other accounts cannot inspect or cancel them.

Daily technicals use the same verified completed-session cutoff as broker history
capture. Today's candle is included only after the published session close plus
the fifteen-minute publication grace; unfinished candles remain excluded.

API and worker must share the same encryption key. The stored credential is
AES-GCM authenticated with the account scope as additional data; plaintext tokens
must never be logged, sent to AI or stored in browser storage.

## Worker configuration

See [the worker environment example](../src/MarketEdge.Worker/.env.example).

| Environment variable | Purpose |
|---|---|
| `MARKETEDGE_KITE_API_KEY` | Same app key as the API |
| `MARKETEDGE_OPTIONS_TOKEN_KEY` | Same base64 AES key as the API |
| `MARKETEDGE_OPTIONS_ENABLED=true` | Enable prospective quote capture/reconciliation after deploying the schema |
| `MARKETEDGE_OPTIONS_RISK_FREE_RATE` | Optional annual continuously compounded rate for model-dependent metrics |
| `MARKETEDGE_OPTIONS_DIVIDEND_YIELD` | Optional annual index dividend yield for model-dependent metrics |
| `MARKETEDGE_OPTIONS_COST_PER_LEG` | Optional round-trip rupee cost estimate per option leg per lot |
| `MARKETEDGE_OPTIONS_RETENTION_DAYS` | Unlinked capture retention, default 30 and minimum 7 days |
| `MARKETEDGE_OPTIONS_ENTRY_MINUTES_BEFORE_CLOSE` | Expiry intraday fresh-entry cutoff, default 30 minutes before session close |
| `MARKETEDGE_OPTIONS_EXIT_MINUTES_BEFORE_CLOSE` | Expiry intraday planned exit, default 15 minutes before session close |

Zero dividend yield is an explicit assumption, not a missing value. A rate of
`0.06` means 6%, not `6`. Review rates/yield and trading-cost assumptions before
use. All-in costs must cover expected brokerage, exchange/regulatory charges,
applicable transaction taxes and slippage for the selected size. This initial
allowance is not a broker fee quotation or a guarantee of actual charges.
These inputs do not replace quoted option premiums: the rate and dividend yield
are used for IV/Greeks and hypothetical future repricing. Trading costs are
deducted from strategy payoffs; the Kite API subscription is not a per-trade cost.
Zerodha's hypothetical-order charges API is not yet integrated, and actual future
fill prices/slippage cannot be known in advance.

Missing model inputs do not block forecasts or valid quote-based expiry-payoff
alternatives. IV, Greeks, model-implied probabilities, future repricing and
estimated stop losses remain unavailable when unsupported. Missing charges mean
gross-before-charges payoff values are shown separately; net profit, net maximum
loss and net hypothetical reconciliation remain unavailable, never zero-cost
successes. Explicitly configured model behavior remains available.

The worker's supervised collector samples REST quotes approximately every 20
seconds for relevant active forecasts, separately from queue analysis. Requests
are rate-limited and instruments are cached daily. This is **sampled data**, not
a continuous exchange tick archive. The worker `/status` exposes collector health.
Nightly forecasts include today's daily candle only after the actual published
spot-session close plus a 15-minute publication grace; live unfinished candles
remain excluded. After-hours Daily/Weekly/Monthly forecasts can include
**planning-only** alternatives, never fresh entry instructions. References must
belong to the verified last completed session, with contiguous calendar coverage
through today. The quote window starts fifteen minutes before spot close and
includes dated post-close updates on that same session date; these are not
asserted to be official closing prints. Last-traded premium fallbacks additionally
require an actual trade no later than fifteen minutes after spot close.
The calendar lookback is fourteen days; missing coverage does not become an
assumed weekday. Live underlying/option freshness and cross-leg skew checks remain
unchanged. Expiry Intraday never receives this after-hours relaxation.

Broker epoch placeholders such as `1970-01-01` are missing timestamps, never valid
observations. Outside the live session, the worker may restore one actual persisted
broker batch for the **same account and index**, validated against the last
completed session window. Original observation/capture times and the source batch
remain explicit; data from different batches are not mixed. Restoration is
for planning only, never for a live entry or Expiry Intraday. If neither a usable
broker observation nor a verified snapshot exists, the missing source is reported
explicitly rather than producing an invented price or stop.

Planning premiums use retained bid/ask references when valid. If the closed
market has no usable book, an explicitly labelled last-traded reference requires
a broker trade timestamp in the closing trade window. Such references do not count
as executable book liquidity. Every plan requires live revalidation, has no live
IV/Greek/repricing claims, and is excluded from hypothetical entry P&L: capturing
a closing reference is not observing an entry. Irrelevant uncovered far expiries
are excluded without creating a warning for each future year.
Retain forecast-linked input/path evidence; only unlinked captures expire
automatically. Monitor storage growth and configure an archival policy before
large-scale use.

Forecast creation permits only one queued/running forecast per account, index
and mode. Reusing the same request ID returns the original run; duplicate queue
deliveries cannot restart an already claimed forecast. If a worker terminates
after claiming a run, it deliberately does not regenerate trades retrospectively.
After confirming no worker is still processing that run, an administrator must
mark that specific `[options].[ForecastRuns]` row and its linked `[dbo].[JobRuns]`
row failed in one transaction, record the interruption in both error messages,
and set the job completion timestamp. Preserve existing evidence and request a
new forecast with current inputs rather than resetting the old run to queued.

## Forecast modes

- **Daily:** before 14:00 IST on a trading day, target today's close; at/after
  14:00, target the next trading session. Non-trading days roll forward.
- **Weekly / Monthly:** actual eligible broker-listed expiries strictly after
  today, not fixed day counts or manually supplied expiry classifications.
- **Expiry Intraday:** selling-oriented mode for today's actual expiry session.
  Initial fresh-entry cutoff is 30 minutes before close and planned exit is
  15 minutes before close (15:00/15:15 in a normal session). It is independent of
  the daily 14:00 rollover. These are analytical instructions, not executed exits.

Normal selling uses an eligible expiry covering the target. Buying compares
eligible later expiries and excludes same-day expiry. The UI shows the exact
target, contract expiry and time remaining.

## Analysis, valuation and strategies

Only dimensions receive 0-100 scores. A high technical/fundamental score means
more bullish evidence; a high volatility score means greater volatility, not
bullishness. Scores are not winning probabilities.

Individual indicators are shown separately rather than hidden behind category
scores. Bullish/bearish directional signals use signed fills; VIX, OI, volatility,
liquidity and other non-directional evidence stay contextual. Per-indicator
`signalContribution` reconciles to the coverage-adjusted directional aggregate:
the three correlated SMAs share one trend vote, while RSI/MACD have their own
votes. These contributions are not index-price points or probabilities. Context
indicators have no fabricated numeric direction weight. Methodology and detailed
explanations are behind keyboard/touch-accessible info controls.

Fair-value ranges use available trailing reported EPS and sector peer multiples.
They are model estimates, not analyst targets. Four distinct valid quarters are
required; exceptional-item adjustments are unavailable unless the stored data
supports them. Banks/financials without book-value/ROE inputs show unavailable.
Missing or stale inputs are disclosed, not filled by AI.

The second input group reuses existing MarketEdge data rather than launching new
stock-by-stock inference. Current-quarter stored stock AI intrinsic-value bounds
take precedence; their base is the deterministic midpoint. AI results for an older
reporting quarter are not reused. Next are existing analyst twelve-month targets
captured after the stored results announcement, then the reported trailing-EPS /
same-sector peer-P/E calculation where appropriate. Source, age, original forecast
horizon and unavailable inputs remain visible per stock.

The covered-weight structural index sensitivity is
`spot * (1 + sum(weight * (stock value / captured stock price - 1)))`.
It shows each stock's index-point contribution and separate AI/analyst/mathematical
coverage. Uncovered stocks stay at captured prices; their missing valuations are
not extrapolated from covered stocks. This is explicitly **structural context**,
not a claim that twelve-month targets will be reached by tomorrow or next expiry.
Both this second input group and refreshed market evidence reach the one final
selector; only the selected run's deterministic horizon bands can be returned.

Selling always has protective option legs. Buying includes outright long
calls/puts and debit spreads. ITM-to-OTM comparisons show the premium/probability
trade-off; cheaper OTM options are not inherently safer.

Each suggestion discloses per-lot payoff, estimated costs, contractual maximum
loss, technical index stop, estimated stop loss, premium target and time exit.
Technical stops are not guaranteed fills or loss caps. Broker margin is not
equivalent to contractual maximum loss.
Selected Selling plans prominently expose their actual index-level breach
condition (exit at/below support, at/above resistance, or either boundary for
two-sided hedges). Missing fees or IV models do not prevent ranking a valid
gross-payoff planning watchlist, but the AI cannot invent absent contracts or SLs.

The Analysis tab leads with the three finite price bands and their exact target,
even when model probabilities are unavailable. Constituents, Selling and Buying
have separate panels rather than repeating the analysis/warning wall.

## Crude and dated news context

Crude uses the nearest eligible broker-listed MCX CRUDEOIL future (INR/barrel).
If unavailable, completed Brent futures daily closes from Yahoo Finance are an
explicitly named **USD alternative**, not an equivalent MCX quote. Source,
currency, previous close, change, capture time and observation/session-date
precision remain visible. Crude is cached for five minutes.
Deliberate forecasts bypass the crude/news/flow caches and attempt a new capture;
the broker master and effective-dated financial records retain their own lifecycle.

FII/FPI and DII cash activity comes from the official NSE dated CSV, in INR crore.
The source date and gross buys/sells/net values are preserved. This is the latest
reported end-of-day cash activity, not live intraday flows or F&O positioning.

Public RBI press-release RSS, or a dated market-news RSS fallback, supplies at
most five short linked headlines, cached for one hour and bounded to seven days.
These are untrusted news context, **not a scheduled economic-events calendar**.
No articles or external instructions are sent to the model. The macro score is
only a transparent oil-importer heuristic, `50 + clamp(-crude change %, -50, 50)`;
coverage is partial because rates and FX are not captured. Raw FII/DII activity
adds evidence coverage, not an invented calibrated index-return coefficient. It is not a
calibrated index-return effect and is not an extra directional-weight vote.

## One final AI call

Get Forecast first calculates and persists deterministic results. One final
compact call through MarketEdge's existing AI integration selects a supplied
range scenario and eligible low/medium/high-risk candidate IDs for buying and
selling. It cannot invent strikes, probabilities, targets or risk values.
Range selection is independent of trade eligibility: no defensible entry does
not erase an available numerical range. The compact input can include crude and
up to three dated headlines, reducing to one headline before exceeding the
existing budget. AI-input numerical values are rounded deterministically to four
decimal places; persisted/displayed engine values retain their original precision.

No company-by-company calls, live-tick calls, web research or tool calls are used.
The response is validated; cache reuse avoids an additional call. Inference
failure leaves deterministic results accessible and explicitly marks AI selection
unavailable. Relative risk tiers do not mean any options trade is safe.

## History and reconciliation

Every deliberate generation creates an immutable timestamped version. History
compares original ranges/direction with observations and independently evaluates
each generated strategy as a **hypothetical** alternative, never an actual trade.
Alternative profits must not be added into a claimed portfolio return.
One forecast may be active per account/index/mode. Duplicate queue delivery cannot
run the same forecast twice. Other indices, modes and accounts can queue independently;
existing weekly analysis-job uniqueness rules continue to apply to their own jobs.

Because capture is sampled, an observed first stop/target cannot exclude an
earlier event between samples. These strategy outcomes are labelled partial,
not certain fills. Missing legs, stale exit quotes and incomplete paths produce
explicit pending/unavailable results. No expired option premium is reconstructed
from an index candle. Capture must be running prospectively; old price history is
not a history of forecasts the system actually issued.

Official closing values and continuous tick-path verification are not implied by
a sampled near-close observation. After session close, an authenticated broker
daily-candle backfill can complete the terminal-close comparison; path coverage
remains partial. Probability estimates are labelled by their
model and event definition, not presented as calibrated historical success rates.

## Validation

```powershell
Set-Location src\MarketEdge.Worker
python -m unittest test_options_engine test_options_pipeline -v
```

Also build the SQL/API projects and client application, and run the focused API
options tests. Live broker integration requires configured credentials, published
tables, imported official metadata and an authenticated session.
