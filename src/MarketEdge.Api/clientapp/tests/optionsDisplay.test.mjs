import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { createOptionsFixture } from './optionsWorkspace.fixture.mjs';

const require = createRequire(import.meta.url);
function loadDisplay({ hooks, document, api = {}, router = {}, timers = {} } = {}) {
  const source = readFileSync(new URL('../src/pages/OptionsPage.tsx', import.meta.url), 'utf8')
    + '\nexport { Analysis, CandidateDetail, Strategies, Constituents, OptionalInputsNotice, WeightProvenance, BrokerBar, ForecastComposer, ForecastWarnings, AiSelection, SnapshotInputStatus, RangeForecast, ForecastViews, WorkspaceTabs, MarketContext, Reconciliation, StructuralFundamentalRange, InfoPopover, MarketMetrics, MetricCard, metricSemantics, metricGroups, IndexExitRule, SelectedSellingPlan, History, ConfirmForecastDelete, forecastDeletionReason, forecastAfterRemoval, timestamp };';
  const compiled = ts.transpileModule(source + '\nexport { isWorking };', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const sandbox = { exports: {}, Error, URL, URLSearchParams, document, ...timers, require: name => name === 'react' && hooks ? { ...React, ...hooks }
    : name === 'lucide-react' ? new Proxy({}, { get: () => () => null })
    : name === 'react-router-dom' ? router : name.startsWith('.') ? api : require(name) };
  vm.runInNewContext(compiled, sandbox);
  return sandbox.exports;
}

function findElements(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(item => findElements(item, predicate));
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...findElements(node.props?.children, predicate)];
}
function elementText(node) {
  if (Array.isArray(node)) return node.map(elementText).join('');
  return node && typeof node === 'object' ? elementText(node.props?.children) : node == null || typeof node === 'boolean' ? '' : String(node);
}
function hookRenderer(name, props = {}, api = {}) {
  const slots = [], pending = [], timers = new Map();
  let cursor = 0, dirty = true, tree, timerId = 0;
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, i) => !Object.is(value, b[i]));
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) {
        slots[i] = { value: typeof initial === 'function' ? initial() : initial };
        slots[i].set = next => {
          const value = typeof next === 'function' ? next(slots[i].value) : next;
          if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
        };
      }
      return [slots[i].value, slots[i].set];
    },
    useRef(initial) { const i = cursor++; return slots[i] ||= { current: initial }; },
    useId() { const i = cursor++; return `test-${i}`; },
    useCallback(callback, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: callback, deps };
      return slots[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) {
        const previous = slots[i];
        slots[i] = { deps };
        pending.push(() => { previous?.cleanup?.(); slots[i].cleanup = effect(); });
      }
    },
  };
  const ui = loadDisplay({ hooks, api, router: { useSearchParams: () => [new URLSearchParams(), () => {}] },
    timers: { setInterval: () => ++timerId, clearInterval: () => {},
      setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
      clearTimeout: id => timers.delete(id) } });
  const render = () => {
    cursor = 0; dirty = false;
    tree = ui[name](props);
    pending.splice(0).forEach(effect => effect());
    return tree;
  };
  render();
  return {
    ui, timers, render, get tree() { return tree; },
    get view() { return findElements(tree, item => item.type === ui.ForecastViews)[0]?.props; },
    async flush() {
      for (let i = 0; i < 15; i++) { await Promise.resolve(); if (dirty) render(); }
      assert.equal(dirty, false, 'component state settled');
    },
    dispose() { slots.forEach(slot => slot.cleanup?.()); },
  };
}
class FixtureOptionsError extends Error {
  constructor(status, text) { super(text); this.status = status; }
}
function deletionWorkspace({ ids = [3, 2], deleteStatus, detailMissing = false, initialReadError = false } = {}) {
  const records = ids.map(id => ({ ...createOptionsFixture(), id }));
  const calls = [];
  let firstRead = true;
  const api = {
    OptionsApiError: FixtureOptionsError,
    fetchOptionsConnection: async () => ({ status: 'disconnected' }),
    fetchOptionsMetadata: async () => ({}),
    fetchOptionsForecasts: async () => { calls.push('list'); return { items: records }; },
    fetchOptionsForecast: async id => {
      calls.push(`get:${id}`);
      if (firstRead && initialReadError) { firstRead = false; throw new FixtureOptionsError(503, 'Detail temporarily unavailable'); }
      if (detailMissing && id === 3) throw new FixtureOptionsError(404, 'Not found');
      return records.find(item => item.id === id);
    },
    deleteForecast: async id => {
      calls.push(`delete:${id}`);
      if (deleteStatus) throw new FixtureOptionsError(deleteStatus, `Server detail ${deleteStatus}`);
      records.splice(records.findIndex(item => item.id === id), 1);
    },
    createOptionsForecast: () => assert.fail('Deletion must not generate a forecast or AI call'),
  };
  return { renderer: hookRenderer('default', {}, api), calls };
}

test('history deletion requires confirmation, supports cancel/Escape and invokes delete only on explicit confirm', async () => {
  const requests = [];
  let restored = 0;
  const row = { ...createOptionsFixture(), id: 3 };
  const history = hookRenderer('History', { items: [row], selectedId: 3, open: () => {}, onDelete: id => requests.push(id) });
  const clickDelete = () => findElements(history.tree, node => node.props?.['aria-label'] === 'Delete forecast #3')[0]
    .props.onClick({ currentTarget: { isConnected: true, focus: () => restored++ } });
  clickDelete(); await history.flush();
  assert.equal(requests.length, 0);
  let dialog = hookRenderer('ConfirmForecastDelete', findElements(history.tree, node => node.type === history.ui.ConfirmForecastDelete)[0].props);
  assert.match(elementText(dialog.tree), /cannot be undone/);
  findElements(dialog.tree, node => node.type === 'button' && elementText(node) === 'Cancel')[0].props.onClick();
  await history.flush(); dialog.dispose();
  assert.equal(findElements(history.tree, node => node.type === history.ui.ConfirmForecastDelete).length, 0);
  assert.equal(requests.length, 0);
  assert.equal(restored, 1);
  clickDelete(); await history.flush();
  dialog = hookRenderer('ConfirmForecastDelete', findElements(history.tree, node => node.type === history.ui.ConfirmForecastDelete)[0].props);
  findElements(dialog.tree, node => node.props?.role === 'alertdialog')[0].props.onKeyDown({ key: 'Escape', preventDefault() {} });
  await history.flush(); dialog.dispose();
  assert.equal(requests.length, 0);
  clickDelete(); await history.flush();
  dialog = hookRenderer('ConfirmForecastDelete', findElements(history.tree, node => node.type === history.ui.ConfirmForecastDelete)[0].props);
  findElements(dialog.tree, node => node.type === 'button' && elementText(node) === 'Delete permanently')[0].props.onClick();
  await history.flush(); dialog.dispose(); history.dispose();
  assert.deepEqual(requests, [3]);
});

test('known active forecast, AI and job states disable deletion without treating absent AI as pending', () => {
  const { forecastDeletionReason, History } = loadDisplay();
  const complete = { ...createOptionsFixture(), ai: null };
  assert.equal(forecastDeletionReason(complete), '');
  for (const item of [
    { ...complete, status: 'queued' }, { ...complete, status: 'running' },
    { ...complete, aiStatus: 'pending' }, { ...complete, ai: { status: 'running' } },
    { ...complete, jobStatus: 'running' }, { ...complete, jobRunStatus: 'queued' },
  ]) {
    assert.ok(forecastDeletionReason(item));
    const markup = renderToStaticMarkup(React.createElement(History, { items: [item], selectedId: null, open() {}, onDelete() {} }));
    assert.match(markup, /aria-label="Delete forecast #[^"]+"[^>]*disabled/);
  }
  assert.ok(forecastDeletionReason(complete, { ...complete, ai: { status: 'pending' } }));
  assert.ok(forecastDeletionReason(complete, null, { status: 'running' }));
  const deleting = renderToStaticMarkup(React.createElement(History, { items: [complete], selectedId: null, open() {}, onDelete() {}, deletingId: complete.id }));
  assert.match(deleting, /aria-label="Deleting forecast #[^"]+"[^>]*disabled/);
});

test('selected deletion loads the latest remaining saved version and final deletion clears all selected detail', async () => {
  const { renderer, calls } = deletionWorkspace();
  await renderer.flush();
  assert.equal(renderer.view.detail.id, 3);
  await renderer.view.onDelete(3); await renderer.flush();
  assert.deepEqual(Array.from(renderer.view.history, item => item.id), [2]);
  assert.equal(renderer.view.selectedId, 2);
  assert.equal(renderer.view.detail.id, 2);
  assert.deepEqual(calls, ['list', 'get:3', 'delete:3', 'get:2']);
  await renderer.view.onDelete(2); await renderer.flush();
  assert.equal(renderer.view.history.length, 0);
  assert.equal(renderer.view.selectedId, null);
  assert.equal(renderer.view.detail, null);
  assert.equal(renderer.view.job, null);
  assert.match(elementText(renderer.tree), /Forecast #2 deleted/);
  assert.deepEqual(calls.filter(call => call.startsWith('delete:')), ['delete:3', 'delete:2']);
  renderer.dispose();
});

test('unselected deletion preserves the currently viewed immutable detail', () => {
  const { forecastAfterRemoval } = loadDisplay();
  const rows = [3, 2, 1].map(id => ({ id }));
  const next = forecastAfterRemoval(rows, 3, 2);
  assert.equal(next.selectedId, 3);
  assert.deepEqual(Array.from(next.history, row => row.id), [3, 1]);
  assert.deepEqual(rows.map(row => row.id), [3, 2, 1]);
});

test('stale DELETE and detail GET 404 evict locally, refresh history and never fetch the deleted ID again', async () => {
  for (const options of [{ deleteStatus: 404 }, { detailMissing: true }]) {
    const { renderer, calls } = deletionWorkspace(options);
    await renderer.flush();
    if (!options.detailMissing) { await renderer.view.onDelete(3); await renderer.flush(); }
    assert.equal(renderer.view.selectedId, 2);
    assert.equal(renderer.view.detail.id, 2);
    assert.deepEqual(Array.from(renderer.view.history, item => item.id), [2]);
    assert.equal(calls.filter(call => call === 'get:3').length, 1);
    assert.equal(renderer.timers.size, 0, 'no stale-detail retry timer');
    assert.match(elementText(renderer.tree), /deleted or is no longer available.*History refreshed/);
    assert.equal(findElements(renderer.tree, node => node.props?.role === 'alert').length, 0);
    renderer.dispose();
  }
});

test('403 and 409 remain specific and visible after refresh, without removing history or automatically retrying DELETE', async () => {
  for (const status of [403, 409]) {
    const { renderer, calls } = deletionWorkspace({ deleteStatus: status });
    await renderer.flush();
    await renderer.view.onDelete(3); await renderer.flush();
    assert.equal(renderer.view.detail.id, 3);
    assert.equal(renderer.view.selectedId, 3);
    assert.equal(renderer.view.history.length, 2);
    const alerts = findElements(renderer.tree, node => node.props?.role === 'alert').map(elementText).join(' ');
    assert.match(alerts, status === 403 ? /do not have permission/ : /AI selection or its job is active/);
    assert.match(alerts, new RegExp(`Server detail ${status}`));
    assert.equal(calls.filter(call => call.startsWith('delete:')).length, 1);
    renderer.dispose();
  }
});

test('removing the final stale version clears its earlier read error and cancels pending detail retries', async () => {
  const { renderer, calls } = deletionWorkspace({ ids: [3], deleteStatus: 404, initialReadError: true });
  await renderer.flush();
  assert.equal(renderer.timers.size, 1);
  assert.match(elementText(renderer.tree), /Detail temporarily unavailable/);
  await renderer.view.onDelete(3); await renderer.flush();
  assert.equal(renderer.view.history.length, 0);
  assert.equal(renderer.view.selectedId, null);
  assert.equal(renderer.view.detail, null);
  assert.equal(renderer.timers.size, 0);
  assert.equal(findElements(renderer.tree, node => node.props?.role === 'alert').length, 0);
  assert.equal(calls.filter(call => call === 'get:3').length, 1);
  renderer.dispose();
});

const quoteOnly = {
  id: 'quote-only', side: 'selling', riskTier: 'high', strategy: 'put-credit-spread',
  expiry: '2026-11-10', lotSize: 75, netPremium: 1000, maxProfit: null, maxLoss: null,
  costEstimate: null, capitalBasis: null, rewardRisk: null, probabilityOfProfit: null,
  probabilityType: 'unavailable', scenarioIds: ['base'], estimatedStopLoss: null,
  targetProfit: null, grossMaxLoss: 6500, grossMaxProfit: 1000, grossTargetProfit: 500,
  warnings: ['Gross before charges only'], technicalStop: { lower: 24900, upper: null },
  legs: [
    { exchange: 'NFO', tradingSymbol: 'SELL', direction: 'sell', quantity: 75, strike: 25000 },
    { exchange: 'NFO', tradingSymbol: 'BUY', direction: 'buy', quantity: 75, strike: 24900 },
  ],
};

test('epoch sentinels, blank and invalid observation times are unavailable while valid source precision is preserved', () => {
  const { timestamp } = loadDisplay();
  for (const value of [undefined, null, '', '   ', '0', '1970-01-01', '1970-01-01T00:00:00Z',
    '1970-01-01T05:30:00+05:30', '1970-01-01T00:00:00+05:30', 'invalid date', '2026-02-30'])
    assert.equal(timestamp(value), 'Unavailable', String(value));
  assert.equal(timestamp('2026-11-09'), '2026-11-09 (source date)');
  assert.equal(timestamp(' 2026-11-09 '), '2026-11-09 (source date)');
  assert.match(timestamp('2026-11-09T12:05:06Z'), /9 Nov 2026, 5:35 pm IST/);
});

test('legacy epoch quote times never appear as real dates or receive fresh replacements in saved display fields', () => {
  const { ForecastViews, CandidateDetail } = loadDisplay();
  const detail = createOptionsFixture();
  const epoch = '1970-01-01T00:00:00Z';
  detail.analysis.spotObservedAt = epoch;
  detail.analysis.quoteAsOf = epoch;
  detail.analysis.snapshotPolicy.quoteAsOf = epoch;
  detail.analysis.executionStatus = 'unavailable';
  detail.analysis.snapshotPolicy.executionStatus = 'unavailable';
  const original = JSON.stringify(detail);
  const markup = renderToStaticMarkup(React.createElement(ForecastViews, {
    tab: 'Analysis', onTabChange() {}, detail, history: [detail], selectedId: detail.id,
    open() {}, openCandidate() {}, clock: Date.parse('2026-11-10T02:00:00Z'), job: null,
  }));
  assert.match(markup, /Quotes observed Unavailable/);
  assert.match(markup, /Underlying quote observed<\/dt><dd>Unavailable/);
  assert.equal((markup.match(/.{0,70}1970.{0,70}/g) || []).join('\n'), '');
  const candidate = { ...quoteOnly, quoteAsOf: epoch,
    legs: quoteOnly.legs.map(leg => ({ ...leg, quoteObservedAt: epoch })) };
  const legs = renderToStaticMarkup(React.createElement(CandidateDetail, { candidate }));
  assert.match(legs, /Quote reference: Unavailable/);
  assert.doesNotMatch(legs, /1970/);
  const history = hookRenderer('History', {
    items: [{ ...detail, targetAt: 'invalid date', generatedAt: epoch }], selectedId: detail.id, open() {},
  });
  findElements(history.tree, node => node.type === 'input' && node.props.type === 'date')[0].props.onChange({ target: { value: '2026-11-09' } });
  history.render();
  assert.match(elementText(history.tree), /No forecasts match/);
  history.dispose();
  assert.equal(JSON.stringify(detail), original, 'immutable source epoch remains untouched');
});

test('quote-only alternatives display gross payoffs separately and never turn missing fees into net values', () => {
  const { CandidateDetail, Strategies } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(Strategies, { side: 'selling', candidates: [quoteOnly] }));
  assert.match(markup, /quote-only/);
  assert.doesNotMatch(markup, /withheld/);
  assert.match(markup, /Gross expiry loss \/ reward \(before charges\)/);
  assert.match(markup, /Charges are unavailable/);
  assert.match(markup, /no fee-inclusive loss cap/);
  assert.match(markup, /₹6,500/);
  const detail = renderToStaticMarkup(React.createElement(CandidateDetail, { candidate: quoteOnly }));
  for (const label of ['Maximum loss', 'Maximum profit', 'Estimated costs', 'Capital basis', 'Profit target'])
    assert.ok(detail.includes(`<dt>${label}</dt><dd>Unavailable</dd>`), label);
  assert.match(detail, /Estimated stop loss \(not guaranteed\)<\/dt><dd>Unavailable/);
});

test('leg observation times remain explicit and never fall back to forecast or aggregate quote times', () => {
  const { CandidateDetail } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(CandidateDetail, { candidate: {
    ...quoteOnly, quoteAsOf: '2026-11-10T10:00:00Z',
    legs: [{ ...quoteOnly.legs[0], quoteObservedAt: '2026-11-09T10:00:00Z' }, quoteOnly.legs[1]],
  } }));
  assert.match(markup, /<th>Quote observed<\/th>/);
  assert.match(markup, /9 Nov 2026/);
  assert.match(markup, /<\/td><td>Unavailable<\/td><\/tr>/);
});

test('planning v4 premiums identify references and last-traded books without claiming entry fills or outcome P&L', () => {
  const { CandidateDetail, Reconciliation } = loadDisplay();
  const candidate = { ...quoteOnly, executionStatus: 'planning-only', requiresLiveRevalidation: true,
    legs: quoteOnly.legs.map(leg => ({ ...leg, entryPrice: 42, priceBasis: 'last-traded-reference', quoteObservedAt: '2026-11-09T10:00:00Z' })) };
  const markup = renderToStaticMarkup(React.createElement(CandidateDetail, { candidate }));
  assert.match(markup, /Reference premium \(signed credit\/debit, before charges\)/);
  assert.match(markup, /Last-traded reference · no executable book/);
  assert.match(markup, /Reference premiums only, not entry fills/);
  assert.doesNotMatch(markup, /<th>Entry estimate|Entry premium \(/);
  const detail = workspaceFixture();
  detail.analysis.candidates = [candidate];
  detail.strategyOutcomes = [{ candidateId: candidate.id, status: 'unavailable', payload: {
    status: 'unavailable', netHypotheticalPnl: 111, grossHypotheticalPnl: 123, exitAt: '2026-11-10T10:00:00Z',
  } }];
  const outcome = renderToStaticMarkup(React.createElement(Reconciliation, { detail }));
  assert.match(outcome, /Recorded net hypothetical P&amp;L<\/dt><dd>Unavailable/);
  assert.match(outcome, /Recorded gross hypothetical P&amp;L \(before charges\)<\/dt><dd>Unavailable/);
  assert.match(outcome, /Observed exit time<\/dt><dd>Unavailable/);
});

test('unknown-cost long calls are not misrepresented as having known unbounded net rewards', () => {
  const { CandidateDetail } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(CandidateDetail, {
    candidate: { ...quoteOnly, strategy: 'long-call', side: 'buying', grossMaxProfit: null },
  }));
  assert.match(markup, /Maximum profit<\/dt><dd>Unavailable/);
  assert.match(markup, /Unbounded gross at expiry \(before charges\)/);
  const legacy = { ...quoteOnly, costEstimate: 2, maxLoss: 6502, maxProfit: null, strategy: 'long-call' };
  const legacyMarkup = renderToStaticMarkup(React.createElement(CandidateDetail, { candidate: legacy }));
  assert.match(legacyMarkup, /Unbounded at expiry \(not a promised return\)/);
});

test('normal workspace has no admin import/editor; missing source facts still point to automatic refresh', () => {
  const { Constituents } = loadDisplay();
  const source = readFileSync(new URL('../src/pages/OptionsPage.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /MetadataImport|importOptionsMetadata|validateMetadataImport|type="file"|Metadata JSON|override JSON template/);
  const constituents = renderToStaticMarkup(React.createElement(Constituents, { items: [] }));
  assert.match(constituents, /Automatic public-reference refresh is pending or unavailable/);
  assert.doesNotMatch(constituents, /manual import|admin override/);
  assert.doesNotMatch(constituents, /Import authoritative/);
});

test('request notice explicitly makes model and cost configuration nonblocking with metric limitations', () => {
  const { OptionalInputsNotice } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(OptionalInputsNotice));
  assert.match(markup, /configuration are optional; they do not block forecasts/);
  assert.match(markup, /IV, Greeks, model probabilities, future repricing and model-derived stop-loss estimates are unavailable/);
  assert.match(markup, /only gross payoffs before charges are available/);
  assert.match(markup, /net profit, net profit probabilities and fee-inclusive maximum loss are unavailable/);
  assert.match(markup, /No missing value is assumed to be zero/);
});

const proxyWeights = {
  sourceType: 'etf-holdings-proxy', sourceDate: '2026-10-31',
  sourceUrl: 'https://example.org/nippon-holdings.xlsx',
  method: 'Normalize disclosed equity holdings to one hundred percent', rawEquityNavPct: 99.87,
};
const proxyValuation = {
  symbol: 'FIXTURE', companyName: 'Synthetic fixture', weightPct: 5.25,
  weightSource: 'auto:nippon-niftybees-etf-holdings-proxy', weightAsOf: '2026-10-31',
  fairBase: null, method: 'Unavailable',
};

test('analysis and constituent views prominently disclose dated ETF proxy weights, not official/live weights', () => {
  const { Analysis, Constituents } = loadDisplay();
  const analysis = renderToStaticMarkup(React.createElement(Analysis, { data: {
    dimensions: [], scenarios: [], constituents: [proxyValuation], referenceData: { weights: proxyWeights },
  } }));
  const constituents = renderToStaticMarkup(React.createElement(Constituents, {
    items: [proxyValuation], weights: proxyWeights,
  }));
  for (const markup of [analysis, constituents]) {
    const visible = markup.split('<details')[0];
    assert.match(visible, /ETF proxy weight provenance/);
    assert.match(visible, /Nippon NiftyBeES ETF holdings proxy/);
    assert.match(visible, /As of 2026-10-31/);
    assert.match(visible, /not official or live index weights/);
    assert.match(markup, /Normalize disclosed equity holdings/);
    assert.match(markup, /99\.87%/);
    assert.match(markup, /href="https:\/\/example.org\/nippon-holdings.xlsx"/);
  }
  assert.match(constituents, /5\.25% equity-normalized ETF proxy weight/);
  assert.match(constituents, /Weight as of: 2026-10-31/);
  assert.match(constituents, /Weight source: auto:nippon-niftybees-etf-holdings-proxy/);
  assert.doesNotMatch(constituents, /5\.25% index weight/);
});

test('valuation provenance remains visible without reference metadata and legacy snapshots never imply official weights', () => {
  const { Constituents, WeightProvenance } = loadDisplay();
  const fallback = renderToStaticMarkup(React.createElement(Constituents, { items: [proxyValuation] }));
  assert.match(fallback, /Nippon NiftyBeES ETF holdings proxy/);
  assert.match(fallback, /As of 2026-10-31/);
  const legacy = { ...proxyValuation };
  delete legacy.weightSource;
  delete legacy.weightAsOf;
  const original = JSON.stringify(legacy);
  const legacyMarkup = renderToStaticMarkup(React.createElement(Constituents, { items: [legacy] }));
  assert.doesNotMatch(legacyMarkup, /Nippon NiftyBeES|% index weight/);
  assert.match(legacyMarkup, /Weight source: Unavailable in original snapshot/);
  assert.match(legacyMarkup, /Weight as of: Unavailable/);
  assert.equal(JSON.stringify(legacy), original);
  const unsafe = renderToStaticMarkup(React.createElement(WeightProvenance, {
    weights: { sourceType: 'etf-holdings-proxy', sourceUrl: 'javascript:alert(1)' },
  }));
  assert.doesNotMatch(unsafe, /href=/);
  assert.match(unsafe, /As of Unavailable/);
});

function elements(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => elements(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [...(predicate(node) ? [node] : []), ...elements(node.props.children, predicate)];
}
const composerProps = {
  indexCode: 'NIFTY50', mode: 'daily', dailyLabel: "Today's Forecast",
  connected: true, runState: 'idle', missingCalendar: false, changeSelection() {}, generate() {},
};

test('one forecast action includes final AI and exposes accessible disconnected and pipeline states', () => {
  const { ForecastComposer } = loadDisplay();
  for (const [runState, connected, disabled, label] of [
    ['idle', true, false, 'Get Forecast'], ['idle', false, true, 'Get Forecast'],
    ['submitting', true, true, 'Starting forecast…'], ['running', true, true, 'Running forecast…'],
    ['selecting', true, true, 'Final AI selection…'],
  ]) {
    const tree = ForecastComposer({ ...composerProps, runState, connected });
    const buttons = elements(tree, node => node.type === 'button');
    assert.equal(buttons.length, 1);
    assert.equal(buttons[0].props.disabled, disabled);
    assert.equal(buttons[0].props['aria-busy'], runState !== 'idle');
    const markup = renderToStaticMarkup(tree);
    assert.ok(markup.includes(label));
    assert.match(markup, /automatic final AI selection/);
    if (!connected) assert.match(markup, /Broker connection required for a new run/);
    for (const select of elements(tree, node => node.type === 'select'))
      assert.equal(select.props.disabled, runState !== 'idle');
    assert.match(markup, /aria-label="About Forecast rules &amp; optional inputs"/);
    assert.match(markup, /aria-expanded="false"/);
    assert.doesNotMatch(markup, /<details[^>]*open=/);
  }
});

test('index/horizon controls preserve callbacks and only explicit forecast action generates', () => {
  const { ForecastComposer } = loadDisplay();
  const calls = [];
  const tree = ForecastComposer({
    ...composerProps, changeSelection: (...args) => calls.push(args), generate: () => calls.push('generate'),
  });
  assert.deepEqual(calls, []);
  const [index, horizon] = elements(tree, node => node.type === 'select');
  index.props.onChange({ target: { value: 'SENSEX' } });
  horizon.props.onChange({ target: { value: 'weekly' } });
  assert.deepEqual(calls, [['SENSEX', 'daily'], ['NIFTY50', 'weekly']]);
  elements(tree, node => node.type === 'button')[0].props.onClick();
  assert.equal(calls.at(-1), 'generate');
});

test('broker bar uses secondary controls and leaves expiry/login failures visible, details collapsed', () => {
  const { BrokerBar } = loadDisplay();
  const props = {
    connection: { configured: true, status: 'connected', brokerUserId: 'DEMO', expiresAt: '2026-11-10T00:30:00Z' },
    error: '', callbackStatus: null, busy: false, clock: Date.parse('2026-11-09T12:00:00Z'),
    login() {}, disconnect() {}, refresh() {},
  };
  const connected = renderToStaticMarkup(React.createElement(BrokerBar, props));
  assert.match(connected, /Connected/);
  assert.match(connected, /Account DEMO/);
  assert.match(connected, /Expires /);
  assert.doesNotMatch(connected, /btn-primary|<details[^>]*open=/);
  const expired = renderToStaticMarkup(React.createElement(BrokerBar, {
    ...props, clock: Date.parse('2026-11-11T00:00:00Z'), error: 'Broker rejected credentials',
  }));
  const visible = expired.replace(/<details[\s\S]*?<\/details>/g, '');
  assert.match(visible, /Session expired/);
  assert.match(visible, /Re-login to run a new forecast/);
  assert.match(visible, /role="alert"[^>]*>Broker rejected credentials/);
  assert.match(visible, /Re-login/);
});

test('routine assumptions are collapsed but model limitations, actionable and unknown warnings remain visible', () => {
  const { ForecastWarnings } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(ForecastWarnings, { items: [
    'Daily-only technicals: opening range unavailable', 'Rate/dividend yield unavailable: no model metrics',
    'Underlying quote is stale; all candidates suppressed', 'Underlying quote is stale; all candidates suppressed',
    'Unexpected source failure',
  ] }));
  const [visible, notes] = markup.split('<details');
  assert.match(visible, /Underlying quote is stale/);
  assert.match(visible, /Unexpected source failure/);
  assert.equal((visible.match(/Underlying quote is stale/g) || []).length, 1);
  assert.doesNotMatch(visible, /Daily-only technicals/);
  assert.match(visible, /Rate\/dividend/);
  assert.match(visible, /Model &amp; source limitations/);
  assert.match(notes, /Model assumptions &amp; technical notes/);
  assert.match(notes, /Daily-only technicals/);
  assert.doesNotMatch(markup, /<details[^>]*open=/);
});

test('pending AI does not render six repeated empty warnings and never provides another run action', () => {
  const { AiSelection } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(AiSelection, {
    detail: { status: 'completed', ai: { status: 'pending' }, analysis: { candidates: [], scenarios: [] } },
    openCandidate() {},
  }));
  assert.match(markup, /AI selection is pending/);
  assert.doesNotMatch(markup, /options-selection |Get Forecast|Run AI|Re-run/);
  assert.match(markup, /no separate paid AI rerun/);
});

test('disabled AI is terminal, does not block generation or deletion and remains explicit', () => {
  const { isWorking, forecastDeletionReason, AiSelection } = loadDisplay();
  const detail = { ...createOptionsFixture(),
    ai: { status: 'skipped', selection: null, errorMessage: 'AI selection is disabled by configuration.' } };
  assert.equal(isWorking(detail), false);
  assert.equal(forecastDeletionReason(detail, detail), '');
  const markup = renderToStaticMarkup(React.createElement(AiSelection, { detail, openCandidate() {} }));
  assert.match(markup, /AI selection is disabled by configuration/);
  assert.doesNotMatch(markup, /AI selection is pending|Final AI selection in progress/);
});

test('snapshot indicators keep model and gross/net limitations visible without duplicating the provenance section', () => {
  const { SnapshotInputStatus } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(SnapshotInputStatus, { data: {
    modelInputs: { riskFreeRate: null, dividendYield: null, roundTripCostPerLeg: null },
    candidates: [], warnings: [], referenceData: { weights: proxyWeights },
  } }));
  assert.match(markup, /Model metrics unavailable · optional inputs missing/);
  assert.match(markup, /Gross before charges · net loss cap unavailable/);
  assert.doesNotMatch(markup, /ETF proxy/);
  assert.doesNotMatch(markup, /<details/);
});

test('AI shortlist shows gross expiry risk instead of a fabricated net cap and only opens saved strategies', () => {
  const { AiSelection } = loadDisplay();
  const calls = [];
  const detail = {
    status: 'completed', analysis: { candidates: [quoteOnly], scenarios: [{ id: 'base', label: 'Base', low: 24800, high: 25200 }] },
    ai: { status: 'completed', selection: { scenarioId: 'base', recommendations: [
      { side: 'selling', riskTier: 'high', candidateId: quoteOnly.id, reason: 'Gross payoff comparison only' },
    ] } },
  };
  const tree = AiSelection({ detail, openCandidate: candidate => calls.push(candidate.id) });
  const markup = renderToStaticMarkup(tree);
  assert.match(markup, /Gross expiry loss ₹6,500 before charges\. Net loss cap unavailable/);
  assert.doesNotMatch(markup, /Net max loss ₹|₹0/);
  const buttons = elements(tree, node => node.type === 'button');
  assert.equal(buttons.length, 1);
  assert.deepEqual(calls, []);
  buttons[0].props.onClick();
  assert.deepEqual(calls, ['quote-only']);
});

test('hundreds of common contract warnings become counted summaries with every unique original accessible', () => {
  const { ForecastWarnings } = loadDisplay();
  const quoteWarnings = Array.from({ length: 266 }, (_, index) => {
    const reason = index < 200 ? 'stale/future quote' : index < 240 ? 'zero/missing executable quote'
      : index < 260 ? 'wide spread' : 'invalid tick size';
    return `${index % 2 ? 'BFO:SENSEX' : 'NFO:NIFTY'}UI${index}CE: ${reason}`;
  });
  const important = [
    'NFO:AUTHCE: broker authentication failed; re-login required',
    'NFO:ARBITRAGECE: arbitrage-invalid executable quote',
    'ETF holdings proxy dated 2026-10-31; not official/live index weights',
    'Rate/dividend yield unavailable: IV and Greeks unavailable',
    'Round-trip charges unavailable: gross before charges only',
    'Underlying quote stale; all actionable candidates suppressed',
  ];
  const originals = [...quoteWarnings, quoteWarnings[0], ...important];
  const originalCopy = JSON.stringify(originals);
  const markup = renderToStaticMarkup(React.createElement(ForecastWarnings, { items: originals }));
  const [visible, fullDetails] = markup.split('<details');
  assert.match(visible, /266 contracts affected/);
  assert.match(visible, /Stale or future-dated quotes <strong>200<\/strong>/);
  assert.match(visible, /Missing or zero bid\/ask <strong>40<\/strong>/);
  assert.match(visible, /Wide spreads <strong>20<\/strong>/);
  assert.match(visible, /Invalid tick metadata <strong>6<\/strong>/);
  assert.doesNotMatch(visible, /NFO:NIFTYUI0CE|BFO:SENSEXUI1CE/);
  for (const warning of important) assert.ok(visible.includes(warning), warning);
  assert.match(fullDetails, /View 266 distinct contract warnings/);
  assert.match(fullDetails, /tabindex="0" role="region" aria-label="Full contract quote warnings"/);
  for (const warning of quoteWarnings) assert.ok(fullDetails.includes(warning), warning);
  assert.equal((markup.match(/NFO:NIFTYUI0CE: stale\/future quote/g) || []).length, 1);
  assert.doesNotMatch(markup, /<details[^>]*open=/);
  assert.equal(JSON.stringify(originals), originalCopy);
});

test('quote grouping never absorbs global stale warnings, unknown failures or instrument authentication errors', () => {
  const { ForecastWarnings } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(ForecastWarnings, { items: [
    'NFO:TESTCE: stale/future quote', 'NFO:TESTCE: wide spread',
    'NFO:TESTCE: invalid access token', 'BFO:TESTPE: unknown quote failure',
    'Underlying quote is stale/future-dated; all actionable candidates suppressed.',
  ] }));
  const [visible, details] = markup.split('<details');
  assert.match(visible, /1 contract affected/);
  assert.match(visible, /invalid access token/);
  assert.match(visible, /unknown quote failure/);
  assert.match(visible, /all actionable candidates suppressed/);
  assert.match(details, /View 2 distinct contract warnings/);
});

function workspaceFixture() {
  const scenarios = [
    { id: 'narrow', label: 'Narrow', low: 25011.37, high: 25121.49 },
    { id: 'base', label: 'Baseline', low: 24901.23, high: 25231.87 },
    { id: 'wide', label: 'Wide', low: 24791.11, high: 25341.99 },
  ].map(item => ({ ...item, probability: null, probabilityType: 'unavailable', targetAt: '2026-11-11T10:00:00Z' }));
  const selling = { ...quoteOnly, id: 'only-selling', executionStatus: 'planning-only', quoteAsOf: '2026-11-09T10:00:00Z' };
  const buying = { ...quoteOnly, id: 'only-buying', side: 'buying', strategy: 'long-call', grossMaxProfit: null,
    executionStatus: 'planning-only', quoteObservedAt: '2026-11-09T10:00:00Z', legs: [{ ...quoteOnly.legs[0], direction: 'buy', optionType: 'CE' }] };
  return {
    id: 501, indexCode: 'NIFTY50', mode: 'daily', status: 'completed', generatedAt: '2026-11-10T00:00:00Z',
    targetAt: '2026-11-11T10:00:00Z', ai: { status: 'completed', selection: { scenarioId: null, recommendations: [] } },
    analysis: {
      schemaVersion: 'options.v1', spot: 25050, bias: 'neutral', scenarios,
      dimensions: [{ key: 'technicals', label: 'Technicals', score: 55, coverage: 1, scale: 'Bearish to bullish', indicators: [] }],
      candidates: [selling, buying], constituents: [proxyValuation], referenceData: { weights: proxyWeights },
      executionStatus: 'planning-only', quoteAsOf: '2026-11-09T10:00:00Z',
      warnings: ['NFO:NIFTYQUOTECE: stale/future quote', 'Rate/dividend yield unavailable: no IV/Greeks'],
    },
  };
}

test('controlled tab callbacks render exclusive real panels with matching accessible tab/panel semantics', () => {
  const { ForecastViews, WorkspaceTabs } = loadDisplay();
  const detail = workspaceFixture();
  let selected = 'Analysis';
  let actions = 0;
  const props = { detail, history: [detail], selectedId: 501, job: null, clock: Date.parse('2026-11-10T00:00:00Z'),
    open() { actions++; }, openCandidate() { actions++; }, onTabChange(next) { selected = next; } };
  const expected = {
    Analysis: 'Forecast price ranges', Constituents: 'Constituent valuations',
    'Selling Strategies': 'Selling strategy alternatives', 'Buying Strategies': 'Buying strategy alternatives',
    History: 'Original prediction versus observed outcome',
  };
  for (const [tab, heading] of Object.entries(expected)) {
    const tabs = WorkspaceTabs({ tab: selected, onChange: props.onTabChange, historyCount: 1 });
    const target = elements(tabs, node => node.props.role === 'tab' && node.props.id === `options-tab-${tab.toLowerCase().replaceAll(' ', '-')}`)[0];
    target.props.onClick();
    assert.equal(selected, tab);
    const tree = ForecastViews({ ...props, tab: selected });
    const panels = elements(tree, node => node.props.role === 'tabpanel');
    assert.equal(panels.length, 1);
    assert.equal(target.props['aria-controls'], panels[0].props.id);
    const markup = renderToStaticMarkup(panels[0]);
    assert.ok(markup.includes(heading), `${tab}: ${heading}`);
    for (const [other, otherHeading] of Object.entries(expected))
      if (other !== tab) assert.ok(!markup.includes(otherHeading), `${tab} leaked ${other}`);
    const selectedTabs = elements(WorkspaceTabs({ tab: selected, onChange: props.onTabChange, historyCount: 1 }),
      node => node.props.role === 'tab' && node.props['aria-selected']);
    assert.equal(selectedTabs.length, 1);
    if (tab !== 'Analysis') {
      assert.doesNotMatch(markup, /NFO:NIFTYQUOTECE|Data quality &amp; model limitations|Forecast price ranges/);
      assert.doesNotMatch(renderToStaticMarkup(tree), /NFO:NIFTYQUOTECE|Data quality &amp; model limitations/);
    }
    if (tab === 'Selling Strategies') {
      assert.match(markup, /only-selling/);
      assert.doesNotMatch(markup, /only-buying|Buying AI selection/);
      assert.match(markup, /Selling AI selection/);
      assert.match(markup, /1 selling alternative/);
      assert.match(markup, /NOT currently executable/);
      assert.match(markup, /No selling alternative was selected by AI/);
      assert.doesNotMatch(markup, /options-selection is-no-trade/);
    }
    if (tab === 'Buying Strategies') {
      assert.match(markup, /only-buying|Buying AI selection/);
      assert.match(markup, /1 buying alternative/);
      assert.doesNotMatch(markup, /only-selling|Selling AI selection/);
    }
  }
  assert.equal(actions, 0, 'Changing tabs must never create a forecast or select a strategy');
});

test('tab keyboard callbacks change controlled state and focus without triggering forecast work', () => {
  const { WorkspaceTabs } = loadDisplay();
  let current = 'Analysis', focused = '', prevented = false;
  const tabs = WorkspaceTabs({ tab: current, historyCount: 0, onChange(next) { current = next; } });
  const first = elements(tabs, node => node.props.role === 'tab')[0];
  const event = key => ({ key, preventDefault() { prevented = true; }, currentTarget: { parentElement: {
    querySelector(selector) { return { focus() { focused = selector; } }; },
  } } });
  first.props.onKeyDown(event('ArrowRight'));
  assert.equal(current, 'Constituents');
  assert.equal(focused, '#options-tab-constituents');
  assert.equal(prevented, true);
  first.props.onKeyDown(event('End'));
  assert.equal(current, 'History');
  first.props.onKeyDown(event('Home'));
  assert.equal(current, 'Analysis');
});

test('actual numeric scenario bounds remain prominent without probabilities and before dimensions/warnings', () => {
  const { Analysis, RangeForecast } = loadDisplay();
  const detail = workspaceFixture();
  const markup = renderToStaticMarkup(React.createElement(Analysis, { data: detail.analysis, targetAt: detail.targetAt }));
  const rangeIndex = markup.indexOf('Forecast price ranges');
  assert.ok(rangeIndex >= 0 && rangeIndex < markup.indexOf('Analysis dimensions'));
  assert.ok(rangeIndex < markup.indexOf('Data quality &amp; model limitations'));
  for (const bounds of ['25,011.37 – 25,121.49', '24,901.23 – 25,231.87', '24,791.11 – 25,341.99'])
    assert.ok(markup.includes(bounds), bounds);
  assert.match(markup, /11 Nov 2026/);
  assert.match(markup, /neutral bias/);
  const range = renderToStaticMarkup(React.createElement(RangeForecast, { data: detail.analysis, targetAt: detail.targetAt }));
  assert.equal((range.match(/Descriptive range · probability unavailable/g) || []).length, 3);
  assert.doesNotMatch(range, /<details/);
  const invalid = { ...detail.analysis, scenarios: [{ id: 'base', label: 'Baseline', low: 30000, high: 20000, probability: null }] };
  assert.match(renderToStaticMarkup(React.createElement(RangeForecast, { data: invalid })), /Unavailable/);
});

test('persisted Daily and Weekly baseline bands remain visible with null probabilities and zero candidates', () => {
  const { Analysis, AiSelection, Strategies } = loadDisplay();
  for (const [mode, low, high, targetAt, expected] of [
    ['daily', 24712.12586914313, 25381.648545489905, '2026-11-11T10:00:00Z', '24,712.13 – 25,381.65'],
    ['weekly', 24330.199699536726, 25780.079956945166, '2026-11-17T10:00:00Z', '24,330.2 – 25,780.08'],
  ]) {
    const detail = workspaceFixture();
    detail.mode = mode;
    detail.analysis.candidates = [];
    detail.analysis.scenarios = [{ id: 'base', label: 'Baseline', low, high, probability: null, probabilityType: 'unavailable', targetAt }];
    const analysis = renderToStaticMarkup(React.createElement(Analysis, { data: detail.analysis }));
    assert.ok(analysis.includes(expected), mode);
    assert.match(analysis.split('<details')[0], /Descriptive range · probability unavailable/);
    for (const side of ['selling', 'buying']) {
      const ai = renderToStaticMarkup(React.createElement(AiSelection, { detail, side, openCandidate() {} }));
      assert.match(ai, /completed/);
      assert.match(ai, /snapshot contains no candidates for this side/);
      assert.doesNotMatch(ai, /options-shortlist-grid|No scenario selected|options-selection has-candidate/);
      const strategies = renderToStaticMarkup(React.createElement(Strategies, { side, candidates: [] }));
      assert.match(strategies, /No broker-quote-supported/);
      assert.doesNotMatch(strategies, /<table|<select/);
    }
  }
});

test('dated crude/news data render only from supplied observations and reject unsafe links', () => {
  const { MarketContext } = loadDisplay();
  const data = workspaceFixture().analysis;
  data.dimensions.push({ key: 'macro', indicators: [{ key: 'brent', label: 'Brent crude', value: 74.2, unit: 'USD/barrel', reason: 'Captured 2026-11-09' }] });
  data.marketContext = { news: [
    { title: 'Synthetic dated market headline', url: 'https://example.org/news', publishedAt: '2026-11-09T10:00:00Z' },
    { title: 'Unsafe fixture link', url: 'javascript:alert(1)', publishedAt: null },
  ], evidence: { source: 'Synthetic fixture' } };
  const markup = renderToStaticMarkup(React.createElement(MarketContext, { data }));
  assert.match(markup, /Brent crude/);
  assert.match(markup, /74\.2/);
  assert.match(markup, /Synthetic dated market headline/);
  assert.match(markup, /9 Nov 2026/);
  assert.match(markup, /href="https:\/\/example.org\/news"/);
  assert.doesNotMatch(markup, /href="javascript/);
  assert.match(markup, /Unsafe fixture link/);
});

test('real context contract exposes dated alternative crude price without inventing a live timestamp', () => {
  const { MarketContext } = loadDisplay();
  const data = workspaceFixture().analysis;
  data.dimensions.push({ key: 'macro', indicators: [{ key: 'crude-change', label: 'Brent crude futures daily close', value: -1,
    unit: '%', source: 'Yahoo Finance via yfinance', asOf: '2026-11-09T23:59:59Z' }] });
  data.marketContext = { news: [], evidence: { crude: {
    name: 'Brent crude futures daily close (MCX alternative)', price: 74.2, previousClose: 75, changePct: -1,
    unit: 'USD/barrel', source: 'Yahoo Finance via yfinance', sourceUrl: 'https://example.org/brent',
    quoteType: 'last-session', isAlternative: true, asOfPrecision: 'session-date', sessionDate: '2026-11-09',
    asOf: '2026-11-09T23:59:59Z', asOfMeaning: 'End of source session date; not an observed quote/settlement timestamp.',
  } } };
  const markup = renderToStaticMarkup(React.createElement(MarketContext, { data }));
  assert.match(markup, /74\.2 USD\/barrel/);
  assert.match(markup, /last-session/);
  assert.match(markup, /Alternative benchmark · not an MCX quote/);
  assert.match(markup, /Source session date<\/dt><dd>2026-11-09/);
  assert.match(markup, /not an observed quote\/settlement timestamp/);
  assert.match(markup, /No dated news items were supplied/);
});

test('v4 macro wrapping preserves dimension explanation and only three dated headlines are expanded', () => {
  const { MarketContext } = loadDisplay();
  const data = workspaceFixture().analysis;
  data.marketContext = { dimension: { key: 'macro', indicators: [], reason: 'Oil change is contextual only; no calibrated index impact.' },
    news: Array.from({ length: 5 }, (_, i) => ({ title: `Dated headline ${i + 1}`, url: `https://example.org/news/${i}`,
      publishedAt: '2026-11-09T10:00:00Z', source: 'Example dated feed', untrustedText: true })),
    evidence: { crude: { name: 'MCX crude', price: 6700, currency: 'INR', previousClose: 6750, changePct: -0.74,
      asOf: '2026-11-09T10:00:00Z', source: 'Zerodha Kite', quoteType: 'delayed-observation' } } };
  const markup = renderToStaticMarkup(React.createElement(MarketContext, { data }));
  const visible = markup.split('title="More dated headlines')[0];
  assert.match(visible, /Oil change is contextual only/);
  assert.match(visible, /6,700 INR/);
  assert.match(visible, /Zerodha Kite/);
  assert.match(visible, /not a scheduled-event calendar/);
  assert.equal((visible.match(/Dated headline \d/g) || []).length, 3);
  assert.match(markup, /More dated headlines \(2\)/);
  assert.match(markup, /Dated headline 5/);
});

test('structural fundamentals remain separate from market run-target ranges with honest weighted source coverage', () => {
  const { ForecastViews, StructuralFundamentalRange } = loadDisplay();
  const detail = workspaceFixture();
  detail.analysis.fundamentalRange = { status: 'partial', low: 23000, base: 26000, high: 29000, coverage: 0.7,
    aiCoverage: 0.3, analystCoverage: 0.2, mathematicalCoverage: 0.2, uncoveredWeightPct: 30,
    horizon: 'structural-not-run-target', sensitivityOnly: true, reason: 'Uncovered constituents held at captured prices, not assigned neutral valuations.' };
  detail.analysis.dimensions.push({ key: 'fundamentals', label: 'Fundamental-only score', score: 60, indicators: [] });
  const props = { detail, history: [detail], selectedId: detail.id, onTabChange() {}, open() {}, openCandidate() {}, clock: 0, job: null };
  const market = renderToStaticMarkup(React.createElement(ForecastViews, { ...props, tab: 'Analysis' }));
  assert.match(market, /<h2>Forecast price ranges<\/h2>/);
  assert.doesNotMatch(market, /<h2>Structural fundamental range<\/h2>|<h3>Fundamental-only score<\/h3>/);
  const fundamental = renderToStaticMarkup(React.createElement(ForecastViews, { ...props, tab: 'Constituents' }));
  assert.match(fundamental, /<h2>Structural fundamental range<\/h2>/);
  assert.match(fundamental, /NOT a Daily\/Weekly target/);
  assert.match(fundamental, /Covered constituent weight<\/dt><dd>70%/);
  assert.match(fundamental, /Stored analyst-target coverage<\/dt><dd>20%/);
  assert.match(fundamental, /Uncovered constituents held at captured prices/);
  assert.doesNotMatch(fundamental, /<h2>Forecast price ranges<\/h2>/);
  assert.equal(renderToStaticMarkup(React.createElement(StructuralFundamentalRange, {})), '');
  const invalid = renderToStaticMarkup(React.createElement(StructuralFundamentalRange, { value: {
    status: 'unavailable', low: null, base: null, high: null, coverage: 0,
  } }));
  assert.equal((invalid.match(/class="options-range-value">Unavailable/g) || []).length, 3);
});

test('constituent inputs retain stored AI and analyst sources, dates, distinct forecast horizons and contributions', () => {
  const { Constituents } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(Constituents, { items: [{
    ...proxyValuation, method: 'stored-stock-AI-valuation', valuationSource: 'existing-ai',
    valuationAsOf: '2026-11-09T10:00:00Z', aiModel: 'Stored fixture model',
    aiPriceForecasts: [{ horizon: '3m', low: 90, base: 100, high: 110 }, { horizon: '1y', low: 110, base: 120, high: 130 }],
    indexContributions: { low: -10, base: 0, high: 10 },
  }, { ...proxyValuation, symbol: 'ANALYST', method: 'stored-analyst-12m-targets', valuationSource: 'analyst-consensus', analystCount: 7 }] }));
  assert.match(markup, /Valuation source: existing-ai/);
  assert.match(markup, /Stored analyst count: 7 · twelve-month targets/);
  assert.match(markup, /Stored stock-AI forecasts · separate horizons/);
  assert.match(markup, /<th scope="row">3m<\/th>/);
  assert.match(markup, /<th scope="row">1y<\/th>/);
  assert.match(markup, /Structural index-point contribution/);
  assert.doesNotMatch(markup, /Fair values are models, not analyst targets/);
});

test('AI range explanations select existing deterministic endpoints and support legacy records without explanations', () => {
  const { RangeForecast } = loadDisplay();
  const data = workspaceFixture().analysis;
  const ai = { status: 'completed', selection: { scenarioId: 'wide', scenarioReason: 'Wider bands reflect uncertainty across the captured inputs.', recommendations: [] } };
  const markup = renderToStaticMarkup(React.createElement(RangeForecast, { data, ai }));
  assert.match(markup, /AI-selected range: Wide/);
  assert.match(markup, /Wider bands reflect uncertainty across the captured inputs/);
  assert.match(markup, /Wide scenario <span class="options-status is-pending">AI selected/);
  assert.match(markup, /24,791\.11 – 25,341\.99/);
  assert.match(markup, /AI selects and explains supplied ranges; it does not generate their endpoints/);
  const legacy = renderToStaticMarkup(React.createElement(RangeForecast, { data, ai: { ...ai, selection: { scenarioId: 'base', recommendations: [] } } }));
  assert.match(legacy, /AI-selected range: Baseline/);
  assert.match(legacy, /No qualitative range explanation was stored/);
  const unsafe = renderToStaticMarkup(React.createElement(RangeForecast, { data, ai: { ...ai, selection: { ...ai.selection, scenarioReason: 'Guaranteed endpoint 30000' } } }));
  assert.doesNotMatch(unsafe, /Guaranteed endpoint 30000/);
  assert.match(unsafe, /24,791\.11 – 25,341\.99/);
});

test('FII/DII cash evidence preserves crore units, date-only precision and partial missingness without a direction vote', () => {
  const { MarketContext } = loadDisplay();
  const data = workspaceFixture().analysis;
  data.marketContext = { dimension: { key: 'macro', indicators: [{ key: 'fii-cash-net', label: 'FII net cash', value: -50,
    unit: 'INR crore', contribution: null, asOf: '2026-11-09', sessionDate: '2026-11-09',
    reason: 'Raw last-reported end-of-day cash activity; no calibrated direction weight.' }] }, evidence: { flows: {
    status: 'partial', source: 'NSE', sourceUrl: 'https://www.nseindia.com/api/fiidiiTradeReact?csv=true',
    asOf: '2026-11-09', sessionDate: '2026-11-09', unit: 'INR crore', quoteType: 'last-reported-end-of-day',
    asOfPrecision: 'session-date', fii: { buy: 200, sell: 250, net: -50 }, dii: null,
    unitBasis: 'Official CSV explicitly labels values in INR crore.',
  } } };
  const markup = renderToStaticMarkup(React.createElement(MarketContext, { data }));
  assert.match(markup, /FII \/ DII cash activity/);
  assert.match(markup, /source session <strong>2026-11-09<\/strong> · INR crore/);
  assert.match(markup, /<th scope="row">FII<\/th><td>200<\/td><td>250<\/td><td>-50<\/td>/);
  assert.match(markup, /<th scope="row">DII<\/th><td>Unavailable<\/td><td>Unavailable<\/td><td>Unavailable<\/td>/);
  assert.match(markup, /do not change the oil-only macro score/);
  assert.match(markup, /Source session: 2026-11-09/);
  assert.doesNotMatch(markup, /5:30 am IST/);
  data.marketContext.evidence.flows.status = 'unavailable';
  const missing = renderToStaticMarkup(React.createElement(MarketContext, { data }));
  assert.doesNotMatch(missing, /<th>Institution<\/th>/);
});

test('dated post-close quote observations remain distinct from official closing prints and trade windows', () => {
  const { ForecastViews } = loadDisplay();
  const detail = workspaceFixture();
  detail.analysis.spotObservedAt = '2026-11-09T12:05:06Z';
  detail.analysis.quoteAsOf = '2026-11-09T12:05:06Z';
  detail.analysis.snapshotPolicy = { executionStatus: 'planning-only', sessionDate: '2026-11-09',
    quoteAsOf: '2026-11-09T12:05:06Z', windowStart: '2026-11-09T09:45:00Z',
    windowEnd: '2026-11-09T18:29:59Z', tradeWindowEnd: '2026-11-09T10:15:00Z',
    reason: 'Dated post-close updates are reference observations, not a closing print or entry.' };
  const markup = renderToStaticMarkup(React.createElement(ForecastViews, { tab: 'Analysis', detail,
    history: [detail], selectedId: detail.id, onTabChange() {}, open() {}, openCandidate() {}, clock: 0, job: null }));
  assert.match(markup, /not official closing prints/);
  assert.match(markup, /Underlying quote observed<\/dt><dd>9 Nov 2026, 5:35 pm IST/);
  assert.match(markup, /Reference policy<\/dt><dd>Dated post-close updates/);
  assert.match(markup, /Quote observation window ends<\/dt><dd>9 Nov 2026, 11:59 pm IST/);
  assert.match(markup, /Last-trade reference window ends<\/dt><dd>9 Nov 2026, 3:45 pm IST/);
});

test('critical auth errors remain global while noncritical diagnostics are confined to Analysis', () => {
  const { ForecastViews } = loadDisplay();
  const detail = workspaceFixture();
  detail.analysis.warnings.push('Broker authentication failed; re-login required');
  const tree = ForecastViews({ tab: 'Constituents', onTabChange() {}, detail, history: [detail], selectedId: 501,
    open() {}, openCandidate() {}, clock: Date.parse('2026-11-10T00:00:00Z'), job: null });
  const markup = renderToStaticMarkup(tree);
  assert.match(markup, /role="alert"[^>]*>Broker authentication failed; re-login required/);
  assert.doesNotMatch(markup, /NFO:NIFTYQUOTECE|Analysis dimensions|Forecast price ranges|Selling AI selection/);
});

test('individual metric semantics require explicit direction and never turn volatility/strength/OI/liquidity into index votes', () => {
  const { metricSemantics } = loadDisplay();
  assert.equal(metricSemantics({ key: 'sma20', value: 24900, direction: 'bullish' }, 'technicals').tone, 'bullish');
  assert.equal(metricSemantics({ key: 'sma50', value: 25100, direction: 'bearish' }, 'technicals').tone, 'bearish');
  assert.equal(metricSemantics({ key: 'ema20', value: 24950 }, 'technicals').tone, 'context');
  assert.equal(metricSemantics({ key: 'macd', value: -5, direction: 'bullish' }, 'technicals').tone, 'bullish', 'raw sign is not invented direction');
  for (const [key, category] of [['india-vix', 'volatility'], ['adx14', 'technicals'], ['window-oi-pcr', 'derivatives'],
    ['basis-2026-11-17', 'derivatives'], ['validBooks', 'liquidity'], ['fii-cash-net', 'macro']]) {
    const result = metricSemantics({ key, value: -10, direction: 'bullish' }, category);
    assert.equal(result.tone, 'context', key);
    assert.match(result.label, /Non-directional/);
  }
  assert.equal(metricSemantics({ key: 'sma20', value: null, direction: 'bearish' }, 'technicals').tone, 'unavailable');
});

test('individual metric cards show bounded relative positions and signed raw units without percentage/weight inventions', () => {
  const { MetricCard } = loadDisplay();
  const group = createOptionsFixture().analysis.dimensions[0];
  const render = metric => renderToStaticMarkup(React.createElement(MetricCard, { metric, group, capturedAt: '2026-11-10T01:50:00Z' }));
  const rsi = render(group.indicators.find(item => item.key === 'rsi14'));
  assert.match(rsi, /RSI 14 relative position, not probability/);
  assert.match(rsi, /aria-valuemin="0" aria-valuemax="100" aria-valuenow="62"/);
  assert.match(rsi, /signed raw contribution, not aggregate weight/);
  const sma = render(group.indicators[0]);
  assert.doesNotMatch(sma, /class="options-metric-relative"/);
  assert.match(sma, /Raw model vote: \+1 model units/);
  const contraryContribution = render({ ...group.indicators[0], contribution: -1 });
  assert.match(contraryContribution, /↑ Bullish/);
  assert.match(contraryContribution, /options-contribution is-negative/);
  const adx = render(group.indicators.find(item => item.key === 'adx14'));
  assert.match(adx, /options-metric is-context/);
  assert.match(adx, /Non-directional context/);
  const missingTime = render({ key: 'unknown', label: 'No observation timestamp', value: 0 });
  assert.match(missingTime, /Snapshot captured: 10 Nov 2026, 7:20 am IST · metric observation time not supplied/);
  assert.doesNotMatch(missingTime, /<small>Observed:/);
});

test('market metric preparation preserves immutable values and exposes missing core inputs explicitly', () => {
  const { metricGroups, MarketMetrics } = loadDisplay();
  const detail = createOptionsFixture(), before = JSON.stringify(detail);
  const groups = metricGroups(detail.analysis);
  assert.equal(JSON.stringify(detail), before);
  for (const label of ['SMA 20', 'SMA 50', 'SMA 200', 'RSI 14', 'MACD 12/26', 'ADX 14', 'India VIX',
    'OI put/call ratio', 'Futures basis November', 'FII cash net', 'DII cash net']) {
    assert.ok(groups.some(group => group.indicators.some(metric => metric.label === label)), label);
  }
  const missing = metricGroups({ dimensions: [], warnings: [] });
  assert.equal(missing.flatMap(group => group.indicators).length, 6);
  assert.ok(missing.flatMap(group => group.indicators).every(metric => metric.value === null));
  const markup = renderToStaticMarkup(React.createElement(MarketMetrics, { data: detail.analysis }));
  assert.match(markup, /Metric direction legend/);
  assert.doesNotMatch(markup, /options-score/);
});

test('information popovers provide unique controls, closed dialogs and explicit keyboard/touch-capable buttons', () => {
  const { InfoPopover } = loadDisplay();
  const markup = renderToStaticMarkup(React.createElement(React.Fragment, null,
    React.createElement(InfoPopover, { label: 'First evidence' }, 'First explanation'),
    React.createElement(InfoPopover, { label: 'Second evidence' }, 'Second explanation')));
  const controls = [...markup.matchAll(/aria-controls="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(controls).size, 2);
  for (const id of controls) assert.ok(markup.includes(`id="${id}"`));
  assert.equal((markup.match(/aria-expanded="false"/g) || []).length, 2);
  assert.equal((markup.match(/role="dialog"/g) || []).length, 2);
  assert.equal((markup.match(/ hidden=""/g) || []).length, 2);
  assert.match(markup, /aria-label="Close First evidence"/);
});

test('info handlers open via keyboard/click and dismiss via Escape, close and outside touch with focus/listener cleanup', () => {
  let open = false, refIndex = 0, effect, cleanup, panelFocus = 0, triggerFocus = 0, prevented = 0;
  const listeners = new Map();
  const refs = [
    { current: { contains: target => target === 'inside' } },
    { current: { focus() { triggerFocus++; } } },
    { current: { focus() { panelFocus++; } } },
  ];
  const { InfoPopover } = loadDisplay({ hooks: {
    useState() { return [open, value => { open = typeof value === 'function' ? value(open) : value; }]; },
    useRef() { return refs[refIndex++]; },
    useId() { return 'controlled-info'; },
    useEffect(callback) { effect = callback; },
  }, document: {
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
  } });
  const render = () => {
    cleanup?.(); refIndex = 0;
    const tree = InfoPopover({ label: 'Test evidence', children: 'Methodology' });
    cleanup = effect();
    return tree;
  };
  const trigger = tree => elements(tree, node => node.props['aria-label'] === 'About Test evidence')[0];
  const keyboard = key => ({ key, repeat: false, preventDefault() { prevented++; } });
  let tree = render();
  assert.equal(trigger(tree).props['aria-expanded'], false);
  trigger(tree).props.onKeyDown(keyboard('Enter'));
  tree = render();
  assert.equal(trigger(tree).props['aria-expanded'], true);
  assert.equal(elements(tree, node => node.props.role === 'dialog')[0].props.hidden, false);
  assert.equal(panelFocus, 1);
  listeners.get('pointerdown')({ target: 'inside', pointerType: 'touch' });
  assert.equal(open, true);
  listeners.get('keydown')(keyboard('Escape'));
  tree = render();
  assert.equal(open, false);
  assert.equal(triggerFocus, 1);
  assert.equal(listeners.size, 0);
  trigger(tree).props.onKeyDown(keyboard(' '));
  tree = render();
  assert.equal(open, true);
  listeners.get('pointerdown')({ target: 'outside', pointerType: 'touch' });
  tree = render();
  assert.equal(open, false);
  trigger(tree).props.onClick();
  tree = render();
  elements(tree, node => node.props['aria-label'] === 'Close Test evidence')[0].props.onClick();
  tree = render();
  assert.equal(trigger(tree).props['aria-expanded'], false);
  assert.equal(triggerFocus, 2);
  assert.equal(listeners.size, 0);
  assert.equal(prevented, 3);
});

test('persisted snapshot provenance is compact and never upgrades unavailable execution or manufactures a plan', () => {
  const { ForecastViews } = loadDisplay();
  const detail = createOptionsFixture();
  detail.analysis.executionStatus = 'unavailable';
  detail.analysis.candidates = [];
  detail.analysis.snapshotEvidence = { source: 'persisted-broker-snapshot', batchId: 42, capturedAt: '2026-11-09T12:10:00Z',
    reason: 'Validated same-user/index last-session broker batch; not a fresh observation.' };
  const render = () => renderToStaticMarkup(React.createElement(ForecastViews, { tab: 'Selling Strategies', detail,
    history: [detail], selectedId: detail.id, onTabChange() {}, open() {}, openCandidate() {}, clock: 0, job: null }));
  const markup = render();
  assert.match(markup, /Last-session snapshot<\/span>/);
  assert.match(markup, /As of 9 Nov 2026, 5:40 pm IST · batch capture/);
  assert.match(markup, /aria-label="About Last-session snapshot"/);
  assert.match(markup, /Batch capture time is not an individual quote observation/);
  assert.match(markup, /No executable option snapshot/);
  assert.doesNotMatch(markup, /Planning only · NOT currently executable|options-selected-legs|Exit if index/);
  detail.analysis.snapshotEvidence.capturedAt = null;
  assert.match(render(), /As of Unavailable · batch capture/);
  delete detail.analysis.snapshotEvidence;
  assert.doesNotMatch(render(), /aria-label="Snapshot source"/);
});

test('v5 weighted influence uses supplied fractions on a fixed scale; context/null metadata never becomes a vote', () => {
  const { MetricCard } = loadDisplay();
  const group = createOptionsFixture().analysis.dimensions[0];
  const weighted = { ...group.indicators[0], signalContribution: 0.025, influenceRole: 'directional',
    contributionBasis: 'One trend vote shared across covered moving averages.' };
  const render = metric => renderToStaticMarkup(React.createElement(MetricCard, { metric, group }));
  const markup = render(weighted);
  assert.match(markup, /Weighted influence: \+0\.025 \(fraction\)/);
  assert.match(markup, /data-influence-basis="coverage-adjusted"/);
  assert.match(markup, /aria-valuemin="-1" aria-valuemax="1" aria-valuenow="0.025"/);
  assert.match(markup, /One trend vote shared across covered moving averages/);
  assert.doesNotMatch(markup, /Raw model vote:/);
  const context = render({ ...weighted, key: 'india-vix', influenceRole: 'context', signalContribution: null });
  assert.match(context, /Non-directional context/);
  assert.doesNotMatch(context, /data-influence-basis/);
  const missing = render({ ...weighted, signalContribution: null });
  assert.match(missing, /Weighted influence unavailable/);
  assert.doesNotMatch(missing, /data-influence-basis/);
  const invalid = render({ ...weighted, signalContribution: 2 });
  assert.match(invalid, /Weighted influence unavailable/);
  const legacy = render(group.indicators[0]);
  assert.match(legacy, /Raw model vote: \+1 model units/);
  assert.match(legacy, /data-influence-basis="legacy-raw"/);
  assert.doesNotMatch(legacy, /Weighted influence:/);
});

test('fundamental weighted indicators stay in Constituents instead of the market input panel', () => {
  const { ForecastViews } = loadDisplay();
  const detail = createOptionsFixture();
  detail.analysis.dimensions.push({ key: 'fundamentals', label: 'Fundamentals', score: 60, coverage: 0.8, scale: 'Growth context',
    indicators: ['Earnings', 'Revenue', 'Margin'].map((label, i) => ({ key: `fund-${i}`, label: `${label} weighted input`, value: 10,
      signalContribution: 0.02, influenceRole: 'directional', contributionBasis: 'Covered weight-adjusted fundamental evidence' })) });
  const props = { detail, history: [detail], selectedId: detail.id, onTabChange() {}, open() {}, openCandidate() {}, clock: 0, job: null };
  const market = renderToStaticMarkup(React.createElement(ForecastViews, { ...props, tab: 'Analysis' }));
  assert.doesNotMatch(market, /aria-label="Fundamental indicator influences"/);
  const fundamental = renderToStaticMarkup(React.createElement(ForecastViews, { ...props, tab: 'Constituents' }));
  assert.match(fundamental, /aria-label="Fundamental indicator influences"/);
  for (const label of ['Earnings', 'Revenue', 'Margin']) assert.match(fundamental, new RegExp(`<h4>${label} weighted input`));
  assert.equal((fundamental.match(/data-influence-basis="coverage-adjusted"/g) || []).length, 3);
  assert.doesNotMatch(fundamental, /<h2>Forecast price ranges/);
});

test('selected hedged Selling cards expose complete legs and index-level exits, never substitute premium stops', () => {
  const { AiSelection, IndexExitRule } = loadDisplay();
  const detail = createOptionsFixture();
  const markup = renderToStaticMarkup(React.createElement(AiSelection, { detail, side: 'selling', openCandidate() {} }));
  for (const rule of ['Exit if index ≤ 24,750', 'Exit if index ≥ 25,250']) assert.ok(markup.includes(rule), rule);
  for (const leg of ['SELL 75 × PE 24,800', 'BUY 75 × PE 24,700', 'SELL 75 × CE 25,200', 'BUY 75 × CE 25,300'])
    assert.ok(markup.includes(leg), leg);
  assert.match(markup, /Time exit/);
  assert.match(markup, /Premium reference \(before charges\)/);
  assert.match(markup, /Net loss cap unavailable/);
  assert.match(markup, /Manual exit rule · no automatic execution or guaranteed fill/);
  const missing = renderToStaticMarkup(React.createElement(IndexExitRule, { candidate: { ...detail.analysis.candidates[0], technicalStop: null, estimatedStopLoss: 123 } }));
  assert.match(missing, /Unavailable — no valid index breach level supplied/);
  assert.doesNotMatch(missing, /Exit if index|123/);
  detail.ai.selection.recommendations.forEach(item => { item.candidateId = null; });
  const empty = renderToStaticMarkup(React.createElement(AiSelection, { detail, side: 'selling', openCandidate() {} }));
  assert.doesNotMatch(empty, /options-selected-legs|Exit if index/);
});
