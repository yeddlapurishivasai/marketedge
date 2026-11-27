import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function compile(path, additions = '') {
  return ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + additions, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}
const displayContext = { exports: {} };
vm.runInNewContext(compile('../src/stage4Display.ts'), displayContext);
const { weakestFirst, stage4Qualification, stage4RunScope, validIsoWeek } = displayContext.exports;

test('unknown analysis statuses never become false qualifications or confirmed exits', () => {
  for (const analysisStatus of ['insufficient', 'stale', 'error']) {
    for (const isStage4 of [true, false, null]) assert.equal(stage4Qualification({ analysisStatus, isStage4 }), null);
  }
  assert.equal(stage4Qualification({ analysisStatus: 'evaluated', isStage4: true }), true);
  assert.equal(stage4Qualification({ analysisStatus: 'evaluated', isStage4: false }), false);
  assert.equal(stage4Qualification({ analysisStatus: 'evaluated', isStage4: null }), null);
});

test('weakest-first ranking is RS, momentum, symbol; unknown values are last', () => {
  const rows = [
    { symbol: 'NULL', rsScore: null }, { symbol: 'B', rsScore: -10, momentumScore: -4 },
    { symbol: 'Z', rsScore: -20, momentumScore: 0 }, { symbol: 'A', rsScore: -10, momentumScore: -4 },
    { symbol: 'C', rsScore: -10, momentumScore: -2 }, { symbol: 'NAN', rsScore: NaN },
  ];
  assert.deepEqual(rows.sort(weakestFirst).map(s => s.symbol), ['Z', 'A', 'B', 'C', 'NAN', 'NULL']);
});
test('run scopes remain visibly distinct and real ISO week validation covers year boundaries', () => {
  assert.equal(stage4RunScope({ parameters: {} }), 'full');
  assert.equal(stage4RunScope({ parameters: { testSampleOnly: true, sectorIds: [1] } }), 'sample');
  for (const parameters of [{ limit: 1 }, { minMarketCap: 0 }, { maxMarketCap: 100 }, { sectorIds: [1] }]) {
    assert.equal(stage4RunScope({ parameters }), 'filtered');
  }
  assert.equal(stage4RunScope({ parameters: {}, metrics: { scope: 'sample' } }), 'sample');
  for (const week of ['2020-W53', '2026-W53', '2027-W01', '2026-W47']) assert.equal(validIsoWeek(week), true, week);
  for (const week of ['2025-W53', '2026-W54', '2026-W00', '2026-W1', '2026-W99', 'bad']) assert.equal(validIsoWeek(week), false, week);
});

function apiFixture(response = () => new Response('{}')) {
  const calls = [];
  const context = {
    exports: {}, URLSearchParams, Headers,
    fetch: async (url, init) => { calls.push({ url, init }); return response(url, init); },
  };
  vm.runInNewContext(compile('../src/api.ts'), context);
  return { api: context.exports, calls };
}
for (const market of ['india', 'us']) {
  test(`${market}: Stage 4 client routes, optional snapshot and trigger/retry payloads are isolated`, async () => {
    const { api, calls } = apiFixture(url => new Response(url.includes('/stocks?') ? '[]' : '{"runId":7,"top25":[]}'));
    await api.triggerStage4(market);
    assert.equal(calls[0].url, `/api/${market}/stage4/trigger`);
    assert.deepEqual(JSON.parse(calls[0].init.body), {});
    await api.triggerStage4(market, { retrySourceRunId: 7 });
    assert.deepEqual(JSON.parse(calls[1].init.body), { retrySourceRunId: 7 });
    await api.fetchStage4Summary(market);
    await api.fetchStage4Summary(market, 7);
    await api.fetchStage4Stocks(market, 7, { classification: 'removed', sectorId: 3, minMarketCap: 0 });
    await api.fetchStage4History(market);
    await api.fetchStage4Rotation(market, 7);
    await api.fetchStage4Schedule(market);
    await api.updateStage4Schedule(market, { enabled: false, hourLocal: 20 });
    assert.equal(calls[2].url, `/api/${market}/stage4/summary`);
    assert.equal(calls[3].url, `/api/${market}/stage4/summary?runId=7`);
    assert.equal(calls[4].url, `/api/${market}/stage4/runs/7/stocks?classification=removed&sectorId=3&minMarketCap=0`);
    assert.equal(calls[5].url, `/api/${market}/stage4/history`);
    assert.equal(calls[6].url, `/api/${market}/stage4/runs/7/sector-rotation`);
    assert.equal(calls[7].url, `/api/${market}/stage4/schedule`);
    assert.equal(calls[8].init.method, 'PUT');
    assert.deepEqual(JSON.parse(calls[8].init.body), { enabled: false, hourLocal: 20 });
    assert.ok(calls.every(c => !c.url.includes('/analysis/')), 'Stage 2 routes untouched');
  });
}
test('Stage 4 empty summary and API errors remain explicit', async () => {
  assert.equal(await apiFixture(() => new Response(null, { status: 204 })).api.fetchStage4Summary('india'), null);
  assert.equal(await apiFixture(() => new Response('No completed snapshot', { status: 404 })).api.fetchStage4Summary('india'), null);
  await assert.rejects(apiFixture(() => new Response('Run not found', { status: 404 })).api.fetchStage4Summary('india', 7), e => e.message === 'Run not found');
  for (const [body, message] of [
    ['{"detail":"Week is unfinished"}', 'Week is unfinished'],
    ['{"error":"Run already active"}', 'Run already active'],
    ['Benchmark unavailable', 'Benchmark unavailable'],
  ]) {
    await assert.rejects(apiFixture(() => new Response(body, { status: 400 })).api.triggerStage4('us'), e => e.message === message);
  }
});
test('moving-average indicators support the .NET acronym wire names without changing Stage 2', async () => {
  const row = { id: 1, mA10: 95, mA30: 100, mA30FourWeeksAgo: 105 };
  const { api } = apiFixture(url => new Response(JSON.stringify(url.includes('/stocks?') ? [row] : { runId: 7, top25: [row] })));
  const summary = await api.fetchStage4Summary('india');
  const stocks = await api.fetchStage4Stocks('india', 7);
  for (const result of [summary.top25[0], stocks[0]]) {
    assert.equal(result.ma10, 95); assert.equal(result.ma30, 100); assert.equal(result.ma30FourWeeksAgo, 105);
  }
});
test('history can be pinned to the selected snapshot scope and filters', async () => {
  const { api, calls } = apiFixture(() => new Response('[]'));
  await api.fetchStage4History('us', 7);
  assert.equal(calls[0].url, '/api/us/stage4/history?runId=7');
});

function elements(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(item => elements(item, predicate));
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...elements(node.props?.children, predicate)];
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  return node && typeof node === 'object' ? text(node.props?.children)
    : node == null || typeof node === 'boolean' ? '' : String(node);
}
const stocks = [
  { id: 1, symbol: 'WEAKER', rsScore: -20, isStage4: true, classification: 'new', sectorId: 1, analysisStatus: 'evaluated', marketCap: 1_000_000_000, adClassification: 'neutral' },
  { id: 2, symbol: 'WEAK', rsScore: -10, isStage4: true, classification: 'continuing', sectorId: 2, analysisStatus: 'evaluated', marketCap: 2_000_000_000, adClassification: 'distributing' },
  { id: 3, symbol: 'REMOVED', rsScore: 2, isStage4: false, classification: 'removed', sectorId: 2, analysisStatus: 'evaluated' },
  { id: 4, symbol: 'UNKNOWN', rsScore: null, isStage4: null, sectorId: 1, analysisStatus: 'stale' },
];
function renderer(market, { scope = 'full', summaryError = '', stocksError = '', initialSearch = '', form = false } = {}) {
  const slots = [], pending = [], requests = [], launches = [];
  let cursor = 0, dirty = true, tree, search = new URLSearchParams(initialSearch);
  const summary = {
    runId: 7, weekNumber: '2026-W47', scope, totalStocks: 4, evaluatedStocks: 3, skippedStocks: 1,
    errorStocks: 0, stage4Count: 2, newAdditions: 1, reEntries: 0, continuing: 1, removed: 1,
    bySector: [{ sectorId: 1, sectorName: 'Tech', stage4Count: 1, totalCount: 2 }],
    top25: [...stocks].reverse(), warnings: ['One stale result'],
    metrics: { eligibleStocks: 4, evaluatedStocks: 3, skippedStocks: 1 },
  };
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
    useMemo(callback, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((v, j) => !Object.is(v, slots[i].deps[j]))) slots[i] = { deps, value: callback() };
      return slots[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((v, j) => !Object.is(v, slots[i].deps[j]))) {
        const previous = slots[i]; slots[i] = { deps };
        pending.push(() => { previous?.cleanup?.(); slots[i].cleanup = effect(); });
      }
    },
  };
  const api = {
    fetchStage4Summary: async (m, id) => {
      requests.push(['summary', m, id]); if (summaryError) throw Error(summaryError); return summary;
    },
    fetchStage4Stocks: async (m, id, params) => {
      requests.push(['stocks', m, id, params.classification]); if (stocksError) throw Error(stocksError); return stocks;
    },
    fetchStage4Rotation: async (m, id) => { requests.push(['rotation', m, id]); return []; },
    fetchStage4History: async (m, id) => {
      requests.push(['history', m, id]);
      return [
      { runId: 7, weekNumber: '2026-W47', scope, runDate: '2026-11-21', totalStage4: 2, bySector: [] },
      { runId: 6, weekNumber: '2026-W46', scope: 'sample', runDate: '2026-11-14', totalStage4: 1, bySector: [] },
      { runId: 5, weekNumber: '2026-W46', scope: 'full', runDate: '2026-11-14', totalStage4: 3, bySector: [] },
      { runId: 10, weekNumber: '2026-W48', scope: 'full', runDate: '2026-11-28', totalStage4: 4, bySector: [] },
      ];
    },
    fetchJobRuns: async () => [
      { id: 7, status: 'completed', weekNumber: '2026-W47', progress: 100, metrics: { scope } },
      { id: 8, status: 'failed', weekNumber: '2026-W47', progress: 30, errorMessage: 'Refresh failed' },
    ],
    fetchSectors: async () => [],
    triggerStage4: async (m, body) => { launches.push({ market: m, ...body }); return { runId: 9 }; },
  };
  const context = {
    exports: {}, Date, URLSearchParams, setInterval, clearInterval,
    window: { addEventListener() {}, removeEventListener() {} },
    require: name => name === 'react' ? { ...React, ...hooks }
      : name === 'react-router-dom' ? {
        useParams: () => ({ market }), useSearchParams: () => [search, value => { search = new URLSearchParams(value); dirty = true; }], Link: 'a',
      } : name === '../api' ? api : name === '../stage4Display' ? displayContext.exports
        : name === '../format' ? { currencySymbol: () => '$', formatPrice: () => '-', formatMarketCap: () => '-' }
          : name.endsWith('.css') || name === 'lucide-react' || name.startsWith('.')
            ? new Proxy({}, { get: () => () => null }) : require(name),
  };
  vm.runInNewContext(compile('../src/pages/Stage4AnalysisPage.tsx', '\nexport { Stage4Workspace, TriggerForm };'), context);
  const render = () => {
    cursor = 0; dirty = false;
    tree = form ? context.exports.TriggerForm({
      sectors: [{ id: 1, sectorName: 'Tech' }], market, busy: false,
      onRun: async request => { launches.push(JSON.parse(JSON.stringify(request))); },
    }) : context.exports.Stage4Workspace({ market });
    pending.splice(0).forEach(f => f());
  };
  render();
  return {
    requests, launches,
    get tree() { return tree; },
    async flush() { for (let i = 0; i < 30; i++) { await Promise.resolve(); if (dirty) render(); } assert.equal(dirty, false); },
    async click(label) {
      const button = elements(tree, n => n.type === 'button' && (text(n).trim() === label || n.props['aria-label'] === label))[0];
      assert.ok(button, label); button.props.onClick(); await this.flush();
    },
    async select(label, value) {
      const field = elements(tree, n => n.type === 'label' && text(n).startsWith(label))[0];
      assert.ok(field, label);
      elements(field, n => n.type === 'select')[0].props.onChange({ target: { value } });
      await this.flush();
    },
    async input(label, value) {
      const field = elements(tree, n => n.type === 'label' && text(n).startsWith(label))[0];
      assert.ok(field, label);
      elements(field, n => n.type === 'input')[0].props.onChange({ target: { value } });
      await this.flush();
    },
    async check(label, checked = true) {
      const field = elements(tree, n => n.type === 'label' && text(n) === label)[0];
      assert.ok(field, label);
      elements(field, n => n.type === 'input')[0].props.onChange({ target: { checked } });
      await this.flush();
    },
    async submit() { tree.props.onSubmit({ preventDefault() {} }); await this.flush(); },
    async sort(label) {
      const table = elements(tree, n => n.type?.name === 'StockTable')[0];
      const button = elements(table.type(table.props), n => n.type === 'button' && text(n).startsWith(label))[0];
      assert.ok(button, label);
      button.props.onClick();
      await this.flush();
    },
    rows() { return Array.from(elements(tree, n => n.type?.name === 'StockTable')[0]?.props.rows ?? [], s => s.symbol); },
    lookupLinks() {
      const table = elements(tree, n => n.type?.name === 'StockTable')[0];
      return elements(table.type(table.props), n => n.type === 'a').map(n => n.props);
    },
    orderedSymbols() { return this.lookupLinks().map(link => link.children); },
    dispose() { slots.forEach(s => s.cleanup?.()); },
  };
}
for (const market of ['india', 'us']) {
  test(`${market}: one coherent run, fixed F&O, weakest-first Top 25 and explicit unknown/removal filters`, async t => {
    const ui = renderer(market);
    t.after(() => ui.dispose());
    await ui.flush();
    assert.ok(text(ui.tree).includes('F&O only'));
    assert.ok(text(ui.tree).includes('One stale result'));
    assert.ok(text(ui.tree).includes('Partial coverage'));
    assert.deepEqual(ui.rows(), [], 'overview uses the Stage 2 charts rather than a full stock table');
    await ui.click('Top 25');
    assert.deepEqual(ui.rows(), ['WEAKER', 'WEAK']);
    const links = ui.lookupLinks();
    assert.deepEqual(links.map(l => l.to), [`/${market}/lookup/WEAKER`, `/${market}/lookup/WEAK`]);
    assert.ok(links.every(l => l.target === '_blank' && l.title.includes('not the Stage 4 snapshot')));
    assert.deepEqual(ui.requests, [['summary', market, undefined], ['stocks', market, 7, 'all'], ['rotation', market, 7], ['history', market, 7]]);
    const selector = elements(ui.tree, n => n.type === 'select')[0];
    assert.ok(!elements(selector, n => n.type === 'option').some(o => String(o.props.value) === '8'), 'failed snapshots not selectable');
    await ui.click('F&O Stocks');
    assert.deepEqual(ui.rows(), ['WEAKER', 'WEAK']);
    await ui.select('Outcome', 'unknown');
    assert.deepEqual(ui.rows(), ['UNKNOWN']);
    await ui.select('Classification', 'removed');
    assert.deepEqual(ui.rows(), ['REMOVED']);
    assert.equal(ui.requests.length, 4, 'display filters do not change the snapshot');
    await ui.click('History');
    assert.ok(!text(ui.tree).includes('#6'), 'sample history not mixed with full scope');
    assert.ok(!text(ui.tree).includes('#10'), 'later weeks not mixed with selected week');
    assert.ok(text(ui.tree).includes('#5'));
    await ui.click('Runs');
    await ui.click('Retry unresolved #8');
    assert.deepEqual(ui.launches, [{ market, retrySourceRunId: 8 }]);
  });

  test(`${market}: Stage 2-style stock sorting, A/D filters, cap units and sector drill-down work`, async t => {
    const ui = renderer(market);
    t.after(() => ui.dispose());
    await ui.flush();
    await ui.click('F&O Stocks');
    assert.deepEqual(ui.orderedSymbols(), ['WEAKER', 'WEAK']);
    await ui.sort('Symbol');
    assert.deepEqual(ui.orderedSymbols(), ['WEAK', 'WEAKER']);
    await ui.sort('Symbol');
    assert.deepEqual(ui.orderedSymbols(), ['WEAKER', 'WEAK']);
    await ui.select('A/D Class', 'distributing');
    assert.deepEqual(ui.rows(), ['WEAK']);
    await ui.select('A/D Class', '');
    await ui.input('Min cap', market === 'india' ? '150' : '1500');
    assert.deepEqual(ui.rows(), ['WEAK'], 'display cap is scaled from crore rupees or millions of dollars');
    await ui.click('Reset filters');
    assert.deepEqual(ui.rows(), ['WEAKER', 'WEAK']);
    await ui.click('By Sector');
    await ui.click('1');
    assert.deepEqual(ui.rows(), ['WEAKER']);
  });
}
test('sample scope is conspicuous and does not compare unrelated history', async t => {
  const ui = renderer('us', { scope: 'sample', initialSearch: 'runId=7' });
  t.after(() => ui.dispose());
  await ui.flush();
  assert.ok(text(ui.tree).includes('SAMPLE F&O scope'));
  assert.ok(text(ui.tree).includes('Not a full-universe overview'));
  assert.deepEqual(ui.requests[0], ['summary', 'us', 7]);
  await ui.click('History');
  assert.ok(!text(ui.tree).includes('#5'));
  assert.ok(text(ui.tree).includes('#6'), 'matching earlier sample history is retained');
});
test('summary and stock errors are visible rather than success-shaped empty results', async t => {
  for (const config of [{ summaryError: 'Benchmark failed' }, { stocksError: 'Stocks unavailable' }]) {
    const ui = renderer('india', config);
    t.after(() => ui.dispose());
    await ui.flush();
    assert.ok(elements(ui.tree, n => n.props?.role === 'alert').some(n => text(n).includes(config.summaryError || config.stocksError)));
  }
});
for (const market of ['india', 'us']) {
  test(`${market}: manual trigger leaves week resolution to API and validates explicit ISO weeks`, async t => {
    const ui = renderer(market, { form: true });
    t.after(() => ui.dispose());
    await ui.submit();
    assert.deepEqual(ui.launches, [{}], 'blank week does not send the current browser week or disable F&O');
    await ui.input('Closed ISO week', '2025-W53');
    await ui.submit();
    assert.equal(ui.launches.length, 1);
    assert.ok(text(ui.tree).includes('Enter a real ISO week'));
    await ui.input('Closed ISO week', '2026-W47');
    await ui.click('F&O test sample');
    await ui.check('Tech');
    await ui.input('Limit', '5');
    await ui.submit();
    assert.deepEqual(ui.launches[1], { testSampleOnly: true, weekNumber: '2026-W47', sectorIds: [1], limit: 5 });
    await ui.input('Min market cap', '100');
    await ui.input('Max market cap', '50');
    await ui.submit();
    assert.equal(ui.launches.length, 2, 'invalid cap range is not submitted');
  });
}

for (const market of ['india', 'us']) {
  test(`${market}: Stage 4 matches Stage 2 header, five summary cards, underline tabs and overview charts`, async t => {
    const ui = renderer(market);
    t.after(() => ui.dispose());
    await ui.flush();
    const header = elements(ui.tree, n => n.props?.className === 'page-header')[0];
    assert.ok(elements(header, n => n.type === 'h1' && n.props.className === 'page-title').length);
    assert.ok(elements(header, n => n.type === 'button' && text(n).trim() === 'Run Analysis' && n.props.className.includes('btn-primary')).length);
    const cards = elements(ui.tree, n => n.props?.className === 'stat-card');
    assert.equal(cards.length, 5);
    for (const label of ['Stage 4 Stocks', 'New Additions', 'Re-Entries', 'Continuing', 'Removed']) {
      assert.ok(cards.some(card => text(card).includes(label)), label);
    }
    const tabs = elements(ui.tree, n => n.props?.className === 'analysis-tabs')[0];
    assert.deepEqual(elements(tabs, n => n.type === 'button').map(text), [
      'Overview', 'Top 25', 'By Sector', 'Sector Rotation', 'F&O Stocks', 'History', 'Runs',
    ]);
    assert.ok(elements(ui.tree, n => n.props?.stage === 4 && Array.isArray(n.props.sectors) && Array.isArray(n.props.history)).length);
    const metrics = elements(ui.tree, n => n.type === 'details' && text(n).startsWith('Snapshot metrics'))[0];
    assert.ok(metrics, 'diagnostic metrics remain available');
    assert.ok(!metrics.props.open, 'diagnostic JSON does not expand above the Stage 2-style summary cards');
    assert.equal(elements(ui.tree, n => n.type === 'details' && text(n).startsWith('Run closed-week analysis')).length, 0);
    await ui.click('Run Analysis');
    const dialog = elements(ui.tree, n => n.props?.role === 'dialog')[0];
    assert.ok(text(dialog).includes('Run Stage 4 Analysis'));
    await ui.click('Close run dialog');
    assert.equal(elements(ui.tree, n => n.props?.role === 'dialog').length, 0);
  });
}

test('both stages reuse identical overview chart markup and dimensions', () => {
  const context = {
    exports: {},
    require: name => name === 'recharts'
      ? new Proxy({}, { get: (_, property) => String(property) }) : require(name),
  };
  vm.runInNewContext(compile('../src/components/AnalysisOverviewCharts.tsx'), context);
  const props = { sectors: [{ name: 'Tech', count: 2 }], history: [{ date: 'W46', count: 1 }, { date: 'W47', count: 2 }] };
  for (const stage of [2, 4]) {
    const tree = context.exports.AnalysisOverviewCharts({ ...props, stage });
    assert.equal(tree.props.className, 'analysis-grid');
    assert.equal(elements(tree, n => n.props?.className === 'chart-card').length, 2);
    assert.equal(elements(tree, n => n.type === 'ResponsiveContainer')[0].props.height, 400);
    assert.equal(elements(tree, n => n.type === 'ResponsiveContainer')[1].props.height, 300);
    assert.ok(text(tree).includes(`Stage ${stage} Stocks by Sector (Top 20)`));
    assert.ok(text(tree).includes(`Stage ${stage} Count Over Time`));
  }
});
