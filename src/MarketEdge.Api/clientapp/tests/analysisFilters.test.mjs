import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/pages/AnalysisPage.tsx', import.meta.url), 'utf8')
  .replace('import.meta.env.DEV', 'false');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;

const stocks = [
  ['ZETA', 'leading', 'accumulating'],
  ['ALPHA', 'leading', 'accumulating'],
  ['BETA', 'leading', 'distributing'],
  ['GAMMA', 'weakening', 'neutral'],
  ['DELTA', 'lagging', 'distributing'],
  ['EPSILON', 'improving', 'accumulating'],
  ['MISSING', undefined, undefined],
  ['NULL', null, null],
].map(([symbol, quadrant, adClassification], i) => ({
  id: i + 1, symbol, quadrant, adClassification, companyName: symbol,
  sectorName: 'Technology', sectorId: 1, isStage2: true, isFno: i % 2 === 0,
  classification: i % 2 === 0 ? 'continuing' : 'new',
  marketCap: (i + 1) * 10_000_000, squeezeOn: i % 2 === 0,
}));

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

function renderer(market = 'india') {
  const slots = [], pending = [], requests = [];
  let cursor = 0, dirty = true, tree;
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
    useMemo(callback, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: callback(), deps };
      return slots[i].value;
    },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) {
        const previous = slots[i];
        slots[i] = { deps };
        pending.push(() => { previous?.cleanup?.(); slots[i].cleanup = effect(); });
      }
    },
  };
  const api = {
    fetchStage2Summary: async () => ({
      stage2Count: stocks.length, newAdditions: 0, reEntries: 0, continuing: stocks.length,
      removed: 0, bySector: [], top25: stocks,
    }),
    fetchStage2History: async () => [],
    fetchJobRuns: async () => [{ id: 1, status: 'completed' }],
    fetchSectorRotation: async () => [],
    fetchRotationHistory: async () => [],
    fetchSectors: async () => [],
    fetchStage2Stocks: async (m, runId, params) => {
      requests.push({ market: m, runId, ...params });
      return stocks.filter(stock => (!params.fnoOnly || stock.isFno) &&
        (!params.classification || stock.classification === params.classification) &&
        (!params.squeeze || (params.squeeze === 'on' && stock.squeezeOn)) &&
        (!params.minMarketCap || stock.marketCap >= params.minMarketCap));
    },
  };
  const sandbox = { exports: {}, Date, setInterval, clearInterval, require: name =>
    name === 'react' ? { ...React, ...hooks }
      : name === 'react-router-dom' ? { useParams: () => ({ market }), useNavigate: () => () => {} }
        : name === '../api' ? api
          : name === '../format' ? { formatMarketCap: () => '-', formatPrice: () => '-' }
            : name === 'lucide-react' || name === 'recharts' || name.startsWith('.')
              ? new Proxy({}, { get: () => () => null }) : require(name) };
  vm.runInNewContext(compiled, sandbox);
  const render = () => {
    cursor = 0; dirty = false;
    tree = sandbox.exports.default();
    pending.splice(0).forEach(effect => effect());
  };
  render();
  return {
    requests,
    get tree() { return tree; },
    async flush() {
      for (let i = 0; i < 20; i++) { await Promise.resolve(); if (dirty) render(); }
      assert.equal(dirty, false, 'component state settled');
    },
    async tab(label) {
      const button = elements(tree, node => node.type === 'button' && text(node) === label)[0];
      assert.ok(button, `tab ${label} exists`);
      button.props.onClick();
      await this.flush();
    },
    async filter(label, value) {
      const select = elements(tree, node => node.type === 'select' && node.props['aria-label'] === label)[0];
      assert.ok(select, `filter ${label} exists`);
      select.props.onChange({ target: { value } });
      await this.flush();
    },
    symbols() {
      return elements(tree, node => node.type === 'tr' && node.key != null)
        .map(row => stocks.find(stock => String(stock.id) === row.key)?.symbol);
    },
    count(expected) { assert.ok(text(tree).includes(`Showing ${expected} stocks`)); },
    dispose() { slots.forEach(slot => slot.cleanup?.()); },
  };
}

for (const market of ['india', 'us']) {
  test(`${market}: defaults, all values, AND matching, missing values and clearing`, async t => {
    const ui = renderer(market);
    t.after(() => ui.dispose());
    await ui.flush();
    await ui.tab('All Stocks');
    assert.deepEqual(ui.symbols(), stocks.map(stock => stock.symbol));
    ui.count(stocks.length);
    for (const [label, field, values] of [
      ['Quadrant', 'quadrant', ['leading', 'weakening', 'lagging', 'improving']],
      ['A/D Class', 'adClassification', ['accumulating', 'neutral', 'distributing']],
    ]) {
      const select = elements(ui.tree, node => node.type === 'select' && node.props['aria-label'] === label)[0];
      assert.equal(select.props.value, '');
      assert.deepEqual(elements(select, node => node.type === 'option').map(option => option.props.value), ['', ...values]);
      for (const value of values) {
        await ui.filter(label, value);
        const expected = stocks.filter(stock => stock[field] === value).map(stock => stock.symbol);
        assert.deepEqual(ui.symbols(), expected);
        ui.count(expected.length);
      }
      await ui.filter(label, '');
    }
    await ui.filter('Quadrant', 'leading');
    await ui.filter('A/D Class', 'accumulating');
    assert.deepEqual(ui.symbols(), ['ZETA', 'ALPHA']);
    ui.count(2);
    const header = elements(ui.tree, node => node.props?.sortKey === 'symbol')[0];
    header.props.onSort('symbol');
    await ui.flush();
    const descending = elements(ui.tree, node => node.props?.sortKey === 'symbol')[0];
    descending.props.onSort('symbol');
    await ui.flush();
    assert.deepEqual(ui.symbols(), ['ALPHA', 'ZETA']);
    ui.count(2);
    await ui.filter('A/D Class', 'neutral');
    assert.deepEqual(ui.symbols(), []);
    assert.ok(text(ui.tree).includes('No stocks match the selected filters.'));
    ui.count(0);
    const emptyCell = elements(ui.tree, node => node.type === 'td' && text(node).includes('No stocks match'))[0];
    assert.equal(emptyCell.props.colSpan, 13);
    await ui.filter('Quadrant', '');
    assert.deepEqual(ui.symbols(), ['GAMMA']);
    await ui.filter('A/D Class', '');
    assert.equal(ui.symbols().length, stocks.length);
    assert.ok(ui.symbols().includes('MISSING'));
    assert.ok(ui.symbols().includes('NULL'));
    assert.equal(ui.requests.length, 1, 'display filters do not refetch stocks');
    assert.equal(ui.requests[0].market, market);
  });
}

test('filters persist across stock tabs, intersect F&O results and leave other tabs unchanged', async t => {
  const ui = renderer();
  t.after(() => ui.dispose());
  await ui.flush();
  await ui.tab('All Stocks');
  await ui.filter('Quadrant', 'leading');
  await ui.filter('A/D Class', 'accumulating');
  await ui.tab('F&O Stocks');
  assert.deepEqual(ui.symbols(), ['ZETA']);
  ui.count(1);
  assert.equal(ui.requests.at(-1).fnoOnly, true);
  await ui.filter('A/D Class', 'neutral');
  ui.count(0);
  assert.equal(elements(ui.tree, node => node.type === 'td' && node.props.colSpan === 14).length, 1);
  await ui.tab('Top 25');
  assert.deepEqual(ui.symbols(), stocks.map(stock => stock.symbol));
  for (const label of ['Top 25', 'Overview', 'By Sector', 'Sector Rotation']) {
    await ui.tab(label);
    assert.equal(elements(ui.tree, node => node.type === 'select' &&
      ['Quadrant', 'A/D Class'].includes(node.props['aria-label'])).length, 0);
  }
  await ui.tab('All Stocks');
  ui.count(0);
  await ui.filter('A/D Class', '');
  assert.deepEqual(ui.symbols(), ['ZETA', 'ALPHA', 'BETA']);
  await ui.filter('Quadrant', '');
  ui.count(stocks.length);
});

test('display filters combine with existing server filters without changing their request parameters', async t => {
  const ui = renderer();
  t.after(() => ui.dispose());
  await ui.flush();
  await ui.tab('All Stocks');
  await ui.filter('Quadrant', 'leading');
  await ui.filter('A/D Class', 'accumulating');
  const classification = elements(ui.tree, node => node.type === 'select' &&
    elements(node, option => option.type === 'option' && option.props.value === 'continuing').length > 0)[0];
  classification.props.onChange({ target: { value: 'continuing' } });
  await ui.flush();
  assert.deepEqual(ui.symbols(), ['ZETA']);
  const squeeze = elements(ui.tree, node => node.type === 'select' &&
    elements(node, option => option.type === 'option' && option.props.value === 'on').length > 0)[0];
  squeeze.props.onChange({ target: { value: 'on' } });
  await ui.flush();
  assert.deepEqual(ui.symbols(), ['ZETA']);
  const marketCap = elements(ui.tree, node => node.type === 'input' &&
    node.props['aria-label'] === 'Minimum market cap in crore rupees')[0];
  marketCap.props.onChange({ target: { value: '2' } });
  await ui.flush();
  assert.deepEqual(ui.symbols(), []);
  ui.count(0);
  assert.equal(ui.requests.at(-1).classification, 'continuing');
  assert.equal(ui.requests.at(-1).squeeze, 'on');
  assert.equal(ui.requests.at(-1).minMarketCap, 20_000_000);
  await ui.filter('A/D Class', '');
  assert.deepEqual(ui.symbols(), ['BETA']);
  ui.count(1);
});
