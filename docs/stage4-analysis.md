# Weekly Stage 4 analysis

Open **Stage 4 Analysis** from either market menu (`/india/stage4` or
`/us/stage4`). This is separate from Stage 2. Every run is restricted to that
market's catalog stocks with `IsFno = true`; there is no all-stocks override.
The flag is an eligibility source, **not** a guarantee that a stock can be
shorted, borrowed, or traded with liquid derivatives.

The page uses Stage 2's layout: the header **Run Analysis** button, five summary
cards, underline tabs, sector/count overview charts, sector bars, rotation
quadrant chart, and compact sortable stock tables. **Top 25** lists the weakest
qualifying names; **F&O Stocks** is the stock-list equivalent because Stage 4
never includes non-F&O stocks. **History** and **Runs** retain snapshot and retry
controls. In the stock table, expand **Indicators / coverage** for moving
averages, RS/ROC changes, input dates, and unavailable-data reasons.

## Qualification and interpretation

A stock qualifies only when its closed weekly close is strictly below both
MA10 and MA30, MA10 is strictly below MA30, MA30 is strictly lower than four
weekly intervals earlier, and Mansfield relative strength is strictly negative.
Equality fails qualification. At least 52 aligned stock/benchmark weekly
observations and valid target-week coverage are required. The benchmarks are
Nifty (`^NSEI`) for India and S&P 500 (`^GSPC`) for US.

The weakest 25 and stock table sort by Mansfield RS ascending, momentum
ascending, then symbol. More-negative RS means weaker benchmark-relative
performance. RS is not a global RS rating. Momentum, ROC, quadrants, and
accumulation/distribution retain their existing mathematical meanings; they
are context rather than additional qualification gates. No squeeze,
fundamental-score, global RS-rating, entry, stop, target, contract-selection,
or execution workflow is included.

## Closed weeks and manual runs

Click **Run Analysis** to open the run dialog. Leave the ISO week blank to let the API
choose the latest closed exchange-local week. It does not default to the
browser's current, possibly unfinished week. A manual week uses `YYYY-Www`;
invalid, future, or unfinished weeks are rejected. Completion follows the
existing Friday close / weekday convention, not a complete holiday or
early-close exchange calendar.

Choose full eligible F&O scope or an F&O test sample. Optional sector,
market-cap bounds, and symbol limit further narrow the scope. Market-cap
inputs in the run dialog are raw local-currency amounts (rupees for India, dollars for US).
Sampling and filters apply only after mandatory F&O eligibility. The stock-list
display filter follows Stage 2's units: crore rupees for India and millions of
dollars for US; run-dialog market-cap inputs remain raw local-currency amounts.
The sample is the intersection of catalog `IsTestSample = true` and
`IsFno = true`; it can contain fewer than 200 stocks and is not a guaranteed
fixed-size universe.

Every latest-closed-week run refreshes its selected F&O universe with a scoped
two-year daily-history request, without pruning unrelated symbols. This is a
Stage 4 override: normal ingestion retains its rolling one-year default and
existing maintenance can later prune older daily bars globally. Two years of
stock history are therefore not guaranteed to remain available.

Historical runs depend on whatever stock history is still ingested and a
bounded benchmark fetch; they do not reconstruct pruned prices. Historical
coverage may be unavailable even after a previous two-year Stage 4 refresh.
The table shows actual price dates, eligibility capture dates, and explicit
evaluated, insufficient, stale, or error outcomes.

## Snapshots, coverage, and history

The default dashboard shows the newest **closed, completed, full-scope**
snapshot by target week, then successful run. An older backfill, running job,
failed rerun, or completed sample cannot silently replace it. Use the snapshot
selector or **Runs → View snapshot** to choose another completed run. The
run ID, week, and FULL/FILTERED/SAMPLE badge identify the scope shared by the
summary, weakest 25, stock table, sector breadth, and rotation.

Summary counts distinguish eligible, evaluated, skipped, and error stocks.
Warnings and partial coverage remain visible. Missing/error data is unknown,
not evidence of a negative qualification or confirmed trend exit. **F&O Stocks**
provides classification, sector, minimum market cap, quadrant, and outcome
filters; use **Unknown coverage** to inspect unresolved outcomes or **All
frozen-scope outcomes** to include removals and evaluated nonqualifiers.
Display filters do not alter the stored summary.

Sector weakness is Stage 4 count divided by eligible stocks in that run's
scope, not whole-market breadth; unknown inputs remain in the denominator.
Rotation preserves the conventional leading, improving, weakening, and
lagging quadrants. Weekly history is requested with the selected run ID and
limited to completed snapshots through that week with matching scope and
frozen filters. The selected week's exact run is pinned; later weeks,
different filters, and full/sample scope mismatches are excluded.

New, re-entry, continuing, and removed classifications use comparable
finalized observations. Missing weeks, unknown outcomes, or a stock leaving
the eligible universe are not confirmed exits and do not extend consecutive
Stage 4 week streaks.

Catalog F&O flags, sector assignments, and market caps are captured when a
run starts. They are **not historical derivatives-universe or fundamental
snapshots**. A historical price-as-of run is not a survivorship-free backtest.

Symbol links open the existing **current** stock lookup in a separate tab,
not a historical lookup snapshot or an embedded Stage 4 fundamentals/setup
panel. The Stage 4 feature does not fetch squeeze, fundamental-score, or
global RS-rating extras. The chart button shows stored daily bars bounded by the
result's price-as-of date, without entry/pivot overlays; those bars may have
been updated since analysis. Stored weekly indicators in the table are the
analysis snapshot.

## Jobs, retries, and schedules

**Runs** lists Stage 4 jobs and their parameters, metrics, errors, and progress.
**Jobs** supports the `stage4_analysis` type, result links, details, and the
existing cancellation controls. Only completed runs are selectable analysis
snapshots. Failed or completed runs expose **Retry unresolved**: this creates
a new run using the source's frozen week/universe, carries valid outcomes
forward, and reevaluates unresolved stocks. Retry ignores manual run options.
Refresh after completion to load the latest published snapshot.
Frozen universe metadata is retained in SQL rather than copied into queue
messages, so full-market retries stay within Azure Queue Storage's message
limit. When a queue delivery returns after a worker interruption, an expired
running claim is atomically marked failed and its saved outcomes remain
available for **Retry unresolved**. A still-live claim is not restarted.

In **Admin → Weekend Stage 4 Analysis · F&O only**, India and US have independent
schedules enabled by default on Saturday at **20:00 exchange-local time**
(`Asia/Kolkata` and `America/New_York`, respectively). The enable switch and
hour change only Stage 4 settings, not Stage 2 or other scheduled jobs.
The hour is a whole number from 0 to 23 and saves when you leave its input.
