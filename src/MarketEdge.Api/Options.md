# India options backend

The SQL project owns all `options` tables. Publish its dacpac and index
seed data before using `/api/india/options`. There are no EF migrations.

## Configuration

Use user secrets or deployment secret injection, never committed credentials:

- `Options:KiteApiKey` (or `MARKETEDGE_KITE_API_KEY`)
- `Options:KiteApiSecret`
- `Options:KiteRedirectUrl`: register the exact absolute API
  `/api/india/options/connection/callback` URL in Kite.
- `Options:TokenEncryptionKey` (or `MARKETEDGE_OPTIONS_TOKEN_KEY`): base64 encoding
  of a cryptographically random 32-byte key, identical in API and worker.
- Existing `AzureStorage` queue and `AzureAiFoundry` endpoint/model/rates.
- `Options:AiEnabled`: controls the final-selector background poller.
- `Options:MetadataAdminRole` (default `Options.MetadataAdmin`): separate app/user
  role required for global metadata writes when application auth is enabled.
  Existing read scopes or `Api.Access` do not grant metadata administration.

Credentials use AES-256-GCM: base64(nonce[12] || tag[16] || ciphertext), with UTF-8
user key as authenticated associated data. Expected expiry is next calendar day
06:00 IST, not a guarantee: a profile check before generation invalidates credentials
early on HTTP 401 or Kite `TokenException`. Permission-only HTTP 403 responses do
not destroy the session; they explicitly request permission/entitlement checks.
Broker error bodies are never surfaced or logged. The worker must also invalidate
broker authentication errors.
Disconnect clears the local encrypted credential; it does not place or cancel orders.

API authentication scopes all forecasts/connections by tenant and user. When
`AzureAd:Enabled=false`, the identity is the shared `local-development`; never
disable authentication on a publicly reachable deployment.

## Login deployment

The browser must navigate to the official login URL returned by POST
`connection/login`. Kite preserves the opaque `state` through `redirect_params`.
The anonymous callback needs no bearer token: a short-lived protected cookie
and protected server-side state recover the initiating account, with atomic
one-time consumption. Redirects are fixed to `/india/options`; no token reaches
the SPA. Use HTTPS in production.

Keep `.local/options-login` writable only by the API identity and persistent
across restarts. Multiple API instances must share this directory and the same
ASP.NET Data Protection key ring/application name (`MarketEdge.Options`), or
route the login and callback to the same instance. Protect the key ring at rest
using the host's supported Data Protection provider. Old unconsumed state expires
after ten minutes and is cleaned on subsequent logins.

## Forecasts and metadata

POST `forecasts` requires `{indexCode,mode,requestId}`; requestId is a UUID unique
per intentional request. Identical retries return the same run; changed parameters
under the same UUID are rejected. The API atomically creates dbo.JobRuns and the
forecast, then queues base64 JSON `{jobType:"options_analysis",market:"india",
runId,forecastRunId,userKey}`. Queue errors persist failed status; a new explicit
request is needed after recovery. No forecasting/pricing calculations run in .NET.
History returns the latest hundred own-user runs; detail includes persisted
analysis, candidates, valuations, AI and latest versioned outcomes.

Metadata imports are all-or-nothing effective-dated replacements for the imported
constituent date/session dates only. Stock symbols must already exist, weights
must be positive and sum to 100% within 0.01%, with at most six decimal places.
Trading dates, session flags and hours must come from an authoritative named source;
the API never fills missing days or infers holidays. Legacy weekly/monthly flags
remain accepted for compatibility but do not select expiries. Expiry dates come
only from Zerodha's current instrument master. Weekly/monthly exclude today;
same-day contracts belong to Expiry Intraday. Monthly-style symbols are validated
against the received contract fields, never generated or inferred from the last
expiry in a month. Times are
strict HH:mm IST. NSE sessions belong to NIFTY50; BSE sessions to SENSEX.
Metadata writes require the separately configured metadata-admin role in authenticated
deployments. Auth-disabled mode retains local-development import access only; never
expose that mode publicly. Callback failures emit sanitized warning types without
broker response bodies, state, request tokens or credentials.
GET metadata also returns imported `sessions` from yesterday through the next
sixty UTC calendar days for pre-run labels. Missing dates are unknown, never
assumed holidays; exact forecast targets remain the worker's responsibility.

Routine reference data is populated automatically by the worker from public
exchange calendars and published spot-session hours; manual imports are optional
administrative overrides. NIFTY50 weights use the owner-approved, dated,
equity-normalized Nippon NiftyBeES holdings proxy, prominently labelled as not
official/live index weights. Source dates, URLs and normalization method remain
in forecast evidence. No complete public SENSEX weights are currently integrated.
Rate, dividend and fixed-cost settings are optional: forecasts and quote-based
gross payoffs remain available, while unsupported model/net-cost fields remain
null with explicit reasons. The AI selector preserves these unavailable values
and cannot substitute gross returns for net returns.

## Final AI selector

The hosted selector polls completed forecasts every thirty seconds. A unique
persisted AiSelections claim precedes any provider request; interruption or
validation failure is terminal, never a second paid request. Pending after a
process crash means interrupted/unavailable, not a promised retry.

Only three scenarios and up to two candidates per buying/selling risk tier are
sent, with deterministic fields only. Tuple encoding and lossless repeated-value
references reduce input size; candidates reduce to one per tier if needed. Only
the compact AI copy rounds numeric values to four decimal places; persisted
engine/display values retain full precision. Shortlisting prioritizes bias-fitting structures,
then rotates deterministically across distinct strategies and expiries before taking
another candidate of the same structure/expiry; lexical IDs only break final ties.
Repeated generic explanations are normalized while
maximum/estimated stop loss, capital, costs, bounds, expiry, full-loss/gamma warnings,
and scenario-specific probability limitations remain available. Gross payoff
bounds and planning/execution status remain distinct from unknown net values.
Non-directional OI scores are omitted, not reinterpreted as a bullish vote.
Market input includes spot, support/resistance, VIX, sampled-chain OI PCR and dated
FII/DII cash nets. The independent fundamentals input includes weighted structural
bounds and AI/analyst/mathematical coverage; these longer-horizon values are not
relabelled as the run's target. Supported oil context and up to three short dated untrusted headlines are included,
reducing to one headline when necessary. These are not a scheduled event calendar.
No extra news-analysis AI request is made. Instructions plus input are capped at
2,372 offline `o200k_base` text tokens, reserving 128 tokens for framing within the
approved 2,500-token input budget. Embedded tokenizer data avoids a runtime
download. This replaces the overly restrictive byte-as-token bound, without
increasing the token allowance. Provider-reported input usage is checked too;
an unexpected tokenizer/framing mismatch fails explicitly without another call.
Output is capped at 800 tokens. Oversized input or
incomplete output fails explicitly without inference retries. No web
or tools are enabled. Output must contain exactly six tier/side decisions:
existing eligible candidate IDs or explicit no-trade, plus one existing scenario
(or null only when no numerical range is available). All-no-trade does not remove
an available range. A separate short qualitative `scenarioReason` explains the
selection. Numerical claims in either reason and additional fields are rejected. Numerical
display values always come from worker payloads, never AI. Matching validated
same-user/model/prompt/input selections reuse cache at zero new cost; other calls
record actual usage and inference-only cost, preserving legacy tool-inclusive
cost calculations elsewhere. Database/configuration failures are explicit API
problem responses or logged background failures, not empty successful results.

The v5 compact input also retains individual metric values and their true weighted
signal contributions. Non-directional context has a null numeric direction weight;
VIX and OI must not be reinterpreted as bullish/bearish votes. Selling selections
rank only supplied hedged plans with deterministic index-level SL breach levels.
Unknown fee/IV fields do not by themselves prohibit a gross planning watchlist.

## Retained runs and API deletion

The Index Options UI is latest-only. For the selected index and horizon,
Analysis, Constituents, Selling Strategies and Buying Strategies share the same
latest forecast, including queued or failed latest runs rather than silently
falling back to an older successful prediction. Reload and read-only refresh
restore the latest result. There is no History tab, previous-run selector or UI
deletion action. New requests replace the displayed result; tab changes and
latest-result reads never submit a new forecast or buy another AI selection.
Detailed methodology, sources, additional indicators, outcomes and alternative
strategies remain expandable; key levels, ranges, directional labels and risk
information are primary. Colors supplement, never replace, labels.

Forecast and strangle run-list endpoints accept optional `limit` from 1 to 100,
defaulting to 100 for existing API clients. The UI requests `limit=1`, scoped to
the authenticated account and selected index (and forecast horizon), ordered
by descending run ID. Previous database runs, snapshots, reconciliation and
cost records remain untouched and available to backend workflows.

`DELETE /api/india/options/forecasts/{id}` uses the existing authenticated account.
It returns 204 after deleting an owned terminal forecast and its dependent
valuations, suggestions/legs, outcomes and AI selection. Missing or another
account's IDs return 404. Queued/running forecasts, active AI selections and active
linked jobs return 409. SQL deletion locks the forecast parent before checking
children, matching worker reconciliation lock order. Reconciliation skips an
already-deleted parent instead of creating orphan outcomes.

Shared broker snapshots, quotes, reference data and terminal job audit remain.
History deletion cannot erase the source batch another forecast needs for
last-session planning. The deletion API remains available for existing clients but is not exposed
in the latest-only UI. Existing runs are not automatically deleted.

## Index Options: intraday strangle selling

The India Index Options workspace has a separate Strangle Selling tab at
`/india/options?tab=strangle-selling`. The selected tab is restored after Zerodha
login returns to Index Options. It uses the same
Zerodha connection as this workspace, but its analysis runs are separate from
options forecasts. No strategy needs to be saved to read current positions.
Publish the updated dacpac before
deploying the API and worker. AI Workspace remains stock-analysis only.

Entry suggestions and model Greeks require explicit annualized decimal model inputs:
`MARKETEDGE_STRANGLE_RISK_FREE_RATE` and
`MARKETEDGE_STRANGLE_DIVIDEND_YIELD` (for example, `0.06` means 6%, not 0.06%).
A run can instead supply `riskFreeRate` and `dividendYield`, which take precedence
over the worker configuration. The UI starts with the user-approved, editable
model assumptions **6% interest and 0% dividend yield**; these are not claimed to
be live market rates. They are sent explicitly on both entry and adjustment
requests and recorded with the calculation. The API accepts interest between
-5% and 100% and dividend yield between 0% and 100%, in annualized decimal form.
A deliberately configured zero dividend yield is valid; a missing value is not
silently treated as zero. Missing model inputs do not block reading positions or
an AI exposure review; the missing Greeks are explicitly reported as unavailable.
The existing Kite API key, token-encryption key, SQL
and queue configuration are also required. `Options:StrangleAiEnabled` controls
strangle AI ranking in addition to the existing `Options:AiEnabled` switch and
Azure AI Foundry configuration. Disabling AI does not turn deterministic
calculations into AI output.

The account-scoped API is under `/api/india/options/strangles`: POST/GET `runs`,
GET `runs/{id}`, and POST/GET `strategies`. A run request includes a unique UUID
`requestId`, `indexCode`, `action` (`entry` or `adjustment`), `wingWidth`,
optional `xOverride`, and optional `strategyId`. The UI sends `strategyId: null`
for adjustments: this reads current positions and requests a read-only AI risk
review and bounded candidate selection, without a baseline, manual fill entry or
saved-strategy selection. Repeating
the same UUID and parameters returns the same run; use a new UUID for an
intentional fresh capture. Current-position review does not infer X or an original
loss budget from broker averages or P&L. Entry controls do not gate this review.
The existing baseline-linked API remains supported for previously integrated
clients: its adjustment X comes from the immutable baseline, and only those
requests may include `selectedTradeIds`: one to two hundred
unique actual Zerodha trade IDs, at most one hundred characters each, covering
all original and subsequent fills for that strategy. The server canonicalizes
the set for retry comparison. These IDs are not order IDs or proposed fills.

The two explicit actions are **Position suggestion at current market** and
**AI suggested adjustments**. Each intentional click requests fresh data; polling
only reads progress/results for that request. The screen is latest-only: the
next deliberate request clears and replaces the previous suggestion or AI
position view. There is no suggestion-history browser or automatic loading of
older results. Opening or refreshing Strangle Selling restores only its latest
persisted run for the selected index, without recapturing broker positions or
requesting paid AI. Internal run/job records remain for request idempotency, cost
accounting and safe queue processing, not as user-saved positions.
An entry request does not implicitly
purchase an AI adjustment. These are advisory actions, not broker orders.

### Entry construction and point units

Choose NIFTY50 or SENSEX. The current broker master supplies actual contracts,
nearest expiry (including today), exchange lot sizes and tick sizes. At the
nearest common call/put ATM strike, sum the qualified bid/ask midpoints. If spot
is `S` and this sum is `P`, the starting short put strike is
`floor((S-P)/100)*100`, and the short call strike is `ceil((S+P)/100)*100`.
For example, spot 24,500 and ATM sum 220 gives a 24,200 put and 24,800 call.
Required strikes must actually exist; they are not generated trading symbols.
Both entry suggestions and adjustment candidates prefer similar executable
short-put and short-call premiums within a bounded set of listed strikes.
This is a soft preference, not an equality requirement: chart bias, protective
coverage, liquidity, Greeks and stressed-loss limits take precedence.
The result discloses the two premiums, their imbalance and the selection reason.
An adjustment must not increase risk simply to collect a more balanced premium.
Entry compares at most five listed baskets, using at most seventeen quote
symbols, and a premium-balanced alternative cannot widen the qualified anchor
basket's loss or stress.

Buy a same-expiry put below the short put and a call above the short call.
Default editable distances from the short strikes are 1,000 NIFTY points and
3,000 SENSEX points, rounded outward to listed contracts. Conservative entry
quotes sell at bid and buy at ask; conservative liquidation uses the reverse
sides. A hedge can reduce estimated margin but does not make the stop a
guaranteed maximum loss. Any basket-margin figure is a broker estimate, not the
strategy's payoff bound or a confirmed order.

X is a suggested combined-position stop distance in option points, not index
points and not a separate stop on each leg. Target profit is +2X; stop loss is
-X. Convert points to rupees using the matched underlying quantity, not the
number of legs. Hedge purchase and liquidation cash flows are part of the basket.
Gross values must not be presented as after-fee returns when charges are unknown.

### Data-driven X and Greeks

The deterministic X calculation uses completed current-session minute returns,
recent five-minute price range, conservative spreads and full basket repricing.
The stress grid moves spot in both directions, advances time up to five minutes
without exceeding the exit deadline, and shocks IV by one absolute volatility
percentage point in either direction. The IV shock is a disclosed policy
assumption, not an observed volatility forecast. The calculation requires enough
completed candles and valid model inputs; unavailable inputs do not become a
fixed default X. A manual override does not bypass quote, hedge or session gates.

Displayed net Greeks include signed quantities: delta per underlying point,
gamma per point squared, vega per one volatility percentage point, and theta
per calendar day. They explain exposures; full repricing, rather than delta
alone, captures nonlinear gamma risk. The rule is a planning heuristic, not a
backtested optimal stop or evidence of the probability of reaching 2X. Same-day
expiry can produce extreme gamma and rapidly changing liquidity.

### Actual positions and adjustments

Adjustment analysis reads the connected account's actual positions. Broker
`net` positions are exposure; `day` activity is not a second copy to add.
Click **AI suggested adjustments** with an active Zerodha connection to capture
the selected index's actual nonzero exposure, all its current-session option
trade fills (including fully closed adjustment contracts), per-leg Greeks when
model inputs/quotes are available, and observed intraday context. Arbitrary
quantities, naked positions and different expiries can be reviewed; they are not
silently converted into a matched four-leg strategy. Portfolio Greeks are summed
using each actual signed quantity, with no partial portfolio total if any leg
lacks qualified data. An empty open-position set skips paid AI, but does not erase
the session's realized profits or losses.

The session accounting scope is **all today's option trades for the selected
index and connected account**, across broker products, not a guessed grouping of
only the surviving four legs. Fill IDs, quantities, directions, timestamps and
current net exposure must reconcile; overnight exposure or missing/ambiguous
fills prevents verified totals. Realized P&L uses the disclosed FIFO convention.
Open P&L uses conservative liquidation quotes (bid for longs, ask for shorts).
Session total equals all fill cash flows plus that liquidation value; realized
P&L is not added a second time. These are gross before unknown charges, not the
broker's tax ledger. Broker average prices after a roll do not replace fills.

For a supported single-expiry position, the worker computes the exact piecewise
expiry payoff of current signed legs plus all session cash flows, including
completed adjustments. Naked unbounded tails are labelled unbounded, never zero.
Mixed-expiry or incomplete data remains unavailable rather than being combined
into an unjustified same-expiry payoff. Maximum expiry profit is not a guaranteed
intraday target.

The displayed **suggested session SL** is a planning cap equal to half a positive,
finite maximum expiry profit (the requested 2:1 profit/loss rule). Its session
stop level is the negative of that cap, and remaining headroom is
`max(0, cap + current session P&L)`. It is recalculated from the current session
ledger on each explicit capture, not described as an immutable original stop.
It is distinct from maximum contractual loss and is not a guaranteed stop fill.
The current-position profit target is **twice the displayed session SL**, as
explicitly selected for this tab; it therefore equals the positive finite expiry
profit ceiling under this policy. That ceiling is not promised to be achievable
intraday. If session P&L reaches either the negative SL or the positive target,
manual exit takes precedence. Candidate
stress tests and contractual-loss bounds may reject staying or rolling even
before a threshold is reached, but must not turn that rejection into a
discretionary early-exit recommendation.

The AI can select only worker-qualified stay/hold or defensive hedged
replacement-roll candidates between the thresholds. There are no discretionary
close or standalone partial-reduction candidates. Every proposed adjustment
must strictly reduce the current worst-case stressed loss on the same scenario
grid while respecting the current session headroom and contractual-loss bound.
Proposed quantities, strikes, Greeks and
rupee limits come from deterministic calculations, never generated model numbers.
Current-session cash flows preserve realized adjustment profit/loss throughout
candidate evaluation. Candidates cannot add lots, widen the current contractual
loss bound or exceed the supplied session headroom under the stress tests.
When evidence cannot support actionable candidates, AI is limited to a clearly
labelled exposure review rather than inventing a change. No save/confirm step is
part of this flow.
Each deliberate click captures again; status polling does not reread the broker
or purchase another review. The internal run records the analysis, not a user-saved
trading strategy.

### NIFTY five-minute selling regime

Both indices use **NIFTY's completed current-session five-minute candles** for
the selling-regime signal. SENSEX retains its own contracts, prices, Greeks and
stress tests; it does not substitute its own chart for the requested NIFTY signal.
Five-minute bars are assembled from contiguous session-aligned minute candles;
an incomplete current five-minute candle is excluded. Support/resistance is
derived from prior completed bars, not retrospectively fitted using the signal
bar. Missing, stale or gapped chart data is explicitly unavailable and cannot
authorize a directional roll or straddle conversion.

**Risk-on** means option-selling-friendly conditions: the support/resistance
range is holding, volatility is contained and the portfolio passes Greek/stress
qualification. **Risk-off** means a confirmed range break, volatility expansion
or excessive Greek stress. Bullish/bearish/neutral direction is separate:
risk-on does not simply mean bullish and risk-off does not simply mean bearish.
The result records the versioned calculation, levels, observed bar time,
direction and threshold evidence. The UI shows the selling regime and levels,
with definitions and detailed evidence expandable.
The selling-regime version is `nifty-selling-regime-v2`. Levels use up to six
prior completed bars plus the first three-bar opening range. Chart risk-on
requires the last completed close inside that range, range width at most 0.6%
of the close, and true range at most 1.2 times the median of up to six prior
true ranges. Chart risk-off requires two completed closes beyond prior
confirmation levels, or true range at least 1.5 times that median. Other
qualified chart conditions are mixed. Portfolio fair-value and adverse-Greek
stress must each fit the remaining SL budget (budget ratio at most one);
failure vetoes risk-on. These disclosed thresholds are heuristics, not
backtested probabilities.

Adjustments exist only to defend the SL, not to maximize premium collection.
In qualified risk-on conditions the candidate generator may consider a
same-expiry **hedged straddle** (short CE and PE at the same listed strike with
outer protective wings). This is conditional, never automatic: before/after
Greeks must be available, worst-case stressed loss must strictly improve, and
all existing quantity, liquidity, margin and loss-budget checks still apply.
Risk-off cannot authorize a straddle-tightening adjustment. If no defensive
candidate qualifies, the screen says review-only rather than pretending a
straddle, stay or early exit is approved.
If a contract changes direction during replacement, both the old-position
close and the new-position open count toward cash flow, displayed actions and
required executable depth. Turning a short quantity into an equally sized
protective long requires buying twice that quantity; matching contract symbols
must not erase either transaction or manufacture profit/headroom.

#### Existing baseline-linked API clients

The following accounting and candidate rules apply only when an API caller
explicitly supplies a previously confirmed `strategyId`, not to the current-position
UI review. Mixed strategies sharing contracts, partial fills and overnight trades
can prevent reliable attribution; missing attribution must not be replaced with
assumed fills or invented strategy P&L.

The confirmed X and original rupee risk budget do not reset when a position is
refreshed or rolled. Risk includes realized adjustment cash flows and the
conservative liquidation value of remaining legs. Intraday trend is distinct
from the existing daily-bar forecast analysis. Risk-on/risk-off context is
timestamped evidence, not proof of future direction or live institutional flows.

After executing a manual roll or reduction, these API clients must supply the
complete session fill set in `selectedTradeIds`.
The worker reads actual broker fills to derive prices, signed quantities and
cash flows; the browser does not supply a replacement accounting ledger.
Verified reconciliation is saved on the analysis run, not by rewriting the
original baseline. Unknown, incomplete, mixed-strategy or inconsistent fills
block further advice. Do not create a new baseline to reset losses: overlapping
same-session contracts are rejected. After a reduction, P&L in points and the
cash stop/target still use the original accounting quantity, not the smaller
remaining position.
Previously verified fills cannot be dropped from later selections. If no IDs are
supplied after a verified reconciliation, the stored ledger is reverified against
fresh broker data; it is never replaced with the original entry-only P&L.

The AI can rank only deterministic candidates that pass the risk checks: hold
or defensive same-expiry hedged replacement rolls. It cannot
invent strikes, add lots, widen the original risk budget or override an exit.
Missing or conflicting evidence stays unavailable/mixed. Provider failure must
leave the deterministic result visible and identify AI as unavailable, not
silently substitute a successful-looking recommendation.

### Intraday deadlines and responsibilities

The exit policy is **SL, target or mandatory intraday deadline only**.
There is no discretionary intermediate full or partial exit recommendation.
A protective replacement roll is an adjustment, not an instruction to close
without replacement. Legacy baseline-linked clients retain their original
`-X / +2X` cash thresholds; current-position review uses the displayed
recalculated session-SL/target policy described above.
`exitPolicy.version` is `sl-target-time-v1`; its reason is null before an exit,
or `stop-loss`, `target` or `time` when manual exit is mandatory. Deterministic
exit signals take precedence over and bypass paid AI selection.

The normal manual exit deadline is **15:15 Asia/Kolkata**, or fifteen minutes
before an earlier verified session close. New entries stop fifteen minutes
before that deadline. A stop, target or exit deadline takes precedence over a
defensive roll. Stale or after-hours snapshots are not live entry advice.

**No orders are placed, no broker stop is installed, and no automatic square-off
or unattended stop monitoring is provided.** Results show the last checked state.
The user must monitor the actual position and arrange broker-side/manual risk
management, including when this browser is closed. Quotes, candle history and
margin estimates require the appropriate Kite account permissions; unavailable
or unverified data is not a trade signal.
