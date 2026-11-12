import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function loadApi(authFetch = () => { throw new Error('Unexpected network request'); }) {
  const source = readFileSync(new URL('../src/optionsApi.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const sandbox = { exports: {}, require: () => ({ authFetch }), URLSearchParams };
  vm.runInNewContext(compiled, sandbox);
  return sandbox.exports;
}

function fixture() {
  return {
    indexCode: 'NIFTY50', effectiveDate: '2026-11-09', source: 'Synthetic unit-test fixture, not market data',
    constituents: [{ symbol: 'FIXTURE', weightPct: 100 }],
    sessions: [{
      sessionDate: '2026-11-09', exchange: 'NSE', isTradingDay: true,
      openTime: '09:15', closeTime: '15:30', isWeeklyExpiry: false, isMonthlyExpiry: false,
    }],
  };
}
test('deleteForecast uses authenticated DELETE and accepts an empty 204 without creating work', async () => {
  const calls = [];
  const api = loadApi(async (url, init) => {
    calls.push({ url, init });
    return new Response(null, { status: 204 });
  });
  assert.equal(await api.deleteForecast(42), undefined);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/india/options/forecasts/42');
  assert.equal(calls[0].init.method, 'DELETE');
  assert.equal(calls[0].init.body, undefined);
});
test('deletion preserves HTTP status and specific permission, stale-history and active-work errors', async () => {
  for (const [status, detail] of [[403, 'Not permitted'], [404, 'Forecast not found'], [409, 'AI is pending']]) {
    const api = loadApi(async () => new Response(JSON.stringify({ detail }), { status }));
    await assert.rejects(api.deleteForecast(42), error => error instanceof api.OptionsApiError
      && error.status === status && error.message === detail);
  }
});
test('metadata accepts complete explicit facts without fetching or inferring inputs', () => {
  const { validateMetadataImport } = loadApi();
  assert.equal(validateMetadataImport(JSON.stringify(fixture())).constituents[0].weightPct, 100);
  const sensex = fixture();
  sensex.indexCode = 'SENSEX';
  sensex.sessions[0].exchange = 'BSE';
  assert.equal(validateMetadataImport(JSON.stringify(sensex)).indexCode, 'SENSEX');
  const boundary = fixture();
  boundary.constituents[0].weightPct = 99.99;
  assert.equal(validateMetadataImport(JSON.stringify(boundary)).constituents[0].weightPct, 99.99);
});
test('metadata rejects malformed JSON, missing provenance, invalid weights and calendar facts', () => {
  const { validateMetadataImport } = loadApi();
  for (const change of [
    value => { value.source = ''; },
    value => { value.effectiveDate = '2026-02-30'; },
    value => { value.constituents[0].weightPct = 50; },
    value => { value.constituents[0].weightPct = 99.98; },
    value => { value.constituents[0].weightPct = 99.9999999; },
    value => { value.constituents.push({ ...value.constituents[0] }); },
    value => { value.sessions[0].exchange = 'BSE'; },
    value => { value.sessions[0].openTime = '15:40'; },
    value => { value.sessions[0].closeTime = '25:30'; },
    value => { value.sessions[0].isTradingDay = false; value.sessions[0].isWeeklyExpiry = true; },
    value => { value.sessions.push({ ...value.sessions[0] }); },
  ]) {
    const invalid = fixture();
    change(invalid);
    assert.throws(() => validateMetadataImport(JSON.stringify(invalid)));
  }
  for (const text of ['null', '{', '{}']) assert.throws(() => validateMetadataImport(text));
});
test('session imports need no expiry classification because contracts supply expiry dates', () => {
  const { validateMetadataImport } = loadApi();
  const input = fixture();
  delete input.sessions[0].isWeeklyExpiry;
  delete input.sessions[0].isMonthlyExpiry;
  const result = validateMetadataImport(JSON.stringify(input));
  assert.equal(result.sessions[0].isWeeklyExpiry, false);
  assert.equal(result.sessions[0].isMonthlyExpiry, false);
  assert.equal(result.sessions[0].sessionDate, input.sessions[0].sessionDate);
  for (const invalid of ['true', null, 1]) {
    input.sessions[0].isMonthlyExpiry = invalid;
    assert.throws(() => validateMetadataImport(JSON.stringify(input)));
  }
});
test('forecast creation reuses caller request ID and shared authFetch', async () => {
  const calls = [];
  const api = loadApi(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ id: 1, jobRunId: 2, status: 'queued' }));
  });
  const requestId = 'c0d59705-807e-4167-9960-b3fe73e84bd0';
  await api.createOptionsForecast('NIFTY50', 'daily', requestId);
  await api.createOptionsForecast('NIFTY50', 'daily', requestId);
  assert.equal(calls[0].url, '/api/india/options/forecasts');
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), { indexCode: 'NIFTY50', mode: 'daily', requestId });
  assert.equal(calls[0].init.body, calls[1].init.body);
});
test('read-only history, detail and metadata do not submit forecast or AI work', async () => {
  const calls = [];
  const api = loadApi(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ items: [] }));
  });
  await api.fetchOptionsForecasts('SENSEX', 'expiry-intraday');
  await api.fetchOptionsForecast(42);
  await api.fetchOptionsMetadata();
  assert.ok(calls.every(call => !call.init?.method || call.init.method === 'GET'));
  assert.equal(calls[0].url, '/api/india/options/forecasts?indexCode=SENSEX&mode=expiry-intraday');
  assert.equal(calls[1].url, '/api/india/options/forecasts/42');
});
test('detail supports persisted top-level candidates and valuations without modifying original analysis', async () => {
  const original = {
    id: 42, analysis: { candidates: [], constituents: [], bias: 'neutral' },
    candidates: [{ id: 'stored-candidate', scenarioProfitProbabilities: {
      base: { probability: 0.6, probabilityType: 'model-implied', condition: { low: 24800, high: 25200, at: '2026-11-10T10:00:00Z' }, gridPoints: 101, matchedGridPoints: 25, reason: 'Synthetic conditional fixture' },
    } }], valuations: [{ symbol: 'FIXTURE' }],
  };
  const api = loadApi(async () => new Response(JSON.stringify(original)));
  const detail = await api.fetchOptionsForecast(42);
  assert.equal(detail.analysis.candidates[0].id, 'stored-candidate');
  assert.equal(detail.analysis.constituents[0].symbol, 'FIXTURE');
  assert.equal(detail.analysis.candidates[0].scenarioProfitProbabilities.base.probability, 0.6);
  assert.equal(original.analysis.candidates.length, 0);
});
test('options v1 numeric scenarios and after-hours planning metadata survive detail mapping unchanged', async () => {
  const scenarios = [
    { id: 'narrow', label: 'Narrow', low: 25011.37, high: 25121.49 },
    { id: 'base', label: 'Baseline', low: 24901.23, high: 25231.87 },
    { id: 'wide', label: 'Wide', low: 24791.11, high: 25341.99 },
  ].map(item => ({ ...item, probability: null, probabilityType: 'unavailable', targetAt: '2026-11-11T10:00:00Z' }));
  const original = {
    id: 501, analysis: { schemaVersion: 'options.v1', scenarios, candidates: [], constituents: [], executionStatus: 'planning-only',
      dimensions: [{ key: 'technicals', indicators: [{ key: 'sma20', value: 24900, contribution: 1,
        signalContribution: 0.025, influenceRole: 'directional', contributionBasis: 'Coverage-adjusted shared trend vote' }] }] },
    candidates: [{ id: 'planning', side: 'selling', executionStatus: 'planning-only', quoteAsOf: '2026-11-09T10:00:00Z', costEstimate: null }],
    valuations: [],
    ai: { status: 'completed', selection: { scenarioId: 'base', scenarioReason: 'Captured inputs support the baseline band.', recommendations: [] } },
  };
  const api = loadApi(async () => new Response(JSON.stringify(original)));
  const detail = await api.fetchOptionsForecast(501);
  assert.deepEqual(JSON.parse(JSON.stringify(detail.analysis.scenarios)), scenarios);
  assert.equal(detail.analysis.candidates[0].executionStatus, 'planning-only');
  assert.equal(detail.analysis.candidates[0].quoteAsOf, '2026-11-09T10:00:00Z');
  assert.equal(detail.analysis.candidates[0].costEstimate, null);
  assert.equal(original.analysis.candidates.length, 0);
  assert.equal(detail.ai.selection.scenarioReason, original.ai.selection.scenarioReason);
  assert.equal(detail.analysis.dimensions[0].indicators[0].signalContribution, 0.025);
  assert.equal(detail.analysis.dimensions[0].indicators[0].contribution, 1);
});
test('metadata preserves authoritative sessions and server TimeOnly values without inferring missing dates', async () => {
  const session = {
    indexCode: 'SENSEX', sessionDate: '2026-11-09', exchange: 'BSE', isTradingDay: false,
    openTime: null, closeTime: null, isWeeklyExpiry: false, isMonthlyExpiry: false, source: 'Test fixture only',
  };
  const api = loadApi(async () => new Response(JSON.stringify({
    indices: [], constituentDates: [], calendarCoverage: [], sessions: [
      session, { ...session, sessionDate: '2026-11-10', isTradingDay: true, openTime: '09:15:00', closeTime: '15:30:00' },
    ],
  })));
  const metadata = await api.fetchOptionsMetadata();
  assert.equal(metadata.sessions[0].isTradingDay, false);
  assert.equal(metadata.sessions[1].openTime, '09:15:00');
  assert.equal(metadata.sessions.length, 2);
  assert.equal(metadata.sessions.find(item => item.sessionDate === '2026-11-11'), undefined);
});
test('persisted outcomes retain partial coverage and net hypothetical P&L distinct from engine pnl', async () => {
  const api = loadApi(async () => new Response(JSON.stringify({
    id: 1, analysis: null,
    outcome: { status: 'partial', payload: {
      actualClose: 25100, actualReturnPct: 0.4, pathStatus: 'partial', coverageType: 'sampled-quotes',
      terminalSource: 'kite-daily-candle', scenarios: [{ id: 'base', terminalCovered: true, low: 24800, high: 25200 }],
    } },
    strategyOutcomes: [{ candidateKey: 'fixture', status: 'partial', payload: {
      status: 'partial', netHypotheticalPnl: 350, pnl: 450, eventOrderCertain: false, coverageType: 'sampled-quotes',
    } }],
  })));
  const detail = await api.fetchOptionsForecast(1);
  assert.equal(detail.outcome.payload.pathStatus, 'partial');
  assert.equal(detail.strategyOutcomes[0].payload.netHypotheticalPnl, 350);
  assert.notEqual(detail.strategyOutcomes[0].payload.netHypotheticalPnl, detail.strategyOutcomes[0].payload.pnl);
  assert.equal(detail.strategyOutcomes[0].payload.eventOrderCertain, false);
});
test('server errors expose actionable details and distinguish application sign-in', async () => {
  const failed = loadApi(async () => new Response(JSON.stringify({ detail: 'Calendar coverage missing' }), { status: 422 }));
  await assert.rejects(() => failed.fetchOptionsMetadata(), /Calendar coverage missing/);
  const problem = loadApi(async () => new Response(JSON.stringify({ status: 503, title: 'Broker data unavailable' }), { status: 503 }));
  await assert.rejects(() => problem.fetchOptionsConnection(), /Broker data unavailable/);
  const unauthorized = loadApi(async () => new Response('', { status: 401 }));
  await assert.rejects(() => unauthorized.fetchOptionsConnection(), /Application sign-in is required/);
  const disconnected = loadApi(async () => new Response(null, { status: 204 }));
  assert.equal(await disconnected.disconnectOptions(), undefined);
});
