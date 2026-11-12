# India options backend

The SQL project owns all fourteen `options` tables. Publish its dacpac and index
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

## Deleting history

`DELETE /api/india/options/forecasts/{id}` uses the existing authenticated account.
It returns 204 after deleting an owned terminal forecast and its dependent
valuations, suggestions/legs, outcomes and AI selection. Missing or another
account's IDs return 404. Queued/running forecasts, active AI selections and active
linked jobs return 409. SQL deletion locks the forecast parent before checking
children, matching worker reconciliation lock order. Reconciliation skips an
already-deleted parent instead of creating orphan outcomes.

Shared broker snapshots, quotes, reference data and terminal job audit remain.
History deletion cannot erase the source batch another forecast needs for
last-session planning. The UI requires confirmation and refreshes the selected
forecast/history after deletion. Existing runs are not automatically deleted.
