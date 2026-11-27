import { useContext, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowDownRight, ArrowUpRight, BarChart3, ChevronLeft, Filter, Minus,
  Play, RefreshCw, Target, TrendingDown, X, Zap,
} from 'lucide-react';
import type {
  JobRun, LookupBar, Market, Sector, SectorRotation, Stage4Classification,
  Stage4History, Stage4Result, Stage4Summary, TriggerStage4Request,
} from '../api';
import {
  fetchJobRuns, fetchLookupBars, fetchSectors, fetchStage4History,
  fetchStage4Rotation, fetchStage4Stocks, fetchStage4Summary, triggerStage4,
} from '../api';
import { currencySymbol, formatMarketCap, formatPrice } from '../format';
import { stage4Qualification, stage4RunScope, validIsoWeek, weakestFirst } from '../stage4Display';
import { ThemeContext } from '../theme';
import { AnalysisOverviewCharts } from '../components/AnalysisOverviewCharts';
import { QuadrantChart } from './AnalysisPage';
import { PriceChart } from './StockLookupPage';
import './Stage4AnalysisPage.css';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const number = (value?: number | null) => value != null && Number.isFinite(value) ? value.toFixed(2) : '—';
const date = (value?: string | null) => value ? value.slice(0, 10) : 'Unavailable';
const classifications: Stage4Classification[] = ['new', 'reentry', 'continuing', 'removed'];
const classLabel = (value: string) => value === 'reentry' ? 'Re-entry' : value.charAt(0).toUpperCase() + value.slice(1);

interface Snapshot {
  key: string;
  summary: Stage4Summary | null;
  stocks: Stage4Result[];
  rotation: SectorRotation[];
  history: Stage4History[];
  errors: string[];
}

type StockSort = { key: keyof Stage4Result; direction: 'asc' | 'desc' } | null;

function StockTable({ rows, market, onChart, ranked = false, sort, onSort }: {
  rows: Stage4Result[]; market: Market;
  onChart: (stock: Stage4Result) => void;
  ranked?: boolean; sort?: StockSort; onSort?: (key: keyof Stage4Result) => void;
}) {
  const sorted = sort ? [...rows].sort((a, b) => {
    const av = a[sort.key], bv = b[sort.key];
    if (av == null) return bv == null ? weakestFirst(a, b) : 1;
    if (bv == null) return -1;
    const difference = typeof av === 'number' && typeof bv === 'number'
      ? av - bv : String(av).localeCompare(String(bv));
    return (sort.direction === 'asc' ? difference : -difference) || weakestFirst(a, b);
  }) : rows;
  const header = (label: string, key: keyof Stage4Result) => <th
    aria-sort={sort?.key === key ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}
  ><button className="stage4-sort" onClick={() => onSort?.(key)}>
    {label}{sort?.key === key ? sort.direction === 'asc' ? ' ▲' : ' ▼' : ''}
  </button></th>;
  return <div className="table-container"><table className="table">
    <thead><tr>
      {ranked && <th>#</th>}<th></th>
      {header('Symbol', 'symbol')}{header('Company', 'companyName')}{header('Sector', 'sectorName')}
      {header('Price', 'closePrice')}{!ranked && header('Mkt Cap', 'marketCap')}
      {header('RS Score', 'rsScore')}{header('Momentum', 'momentumScore')}
      {header('Quadrant', 'quadrant')}{header('A/D', 'adClassification')}
      {header(ranked ? 'Classification' : 'Class', 'classification')}
      {!ranked && <>{header('Weeks', 'weeksInStage4')}<th>Indicators / coverage</th></>}
    </tr></thead>
    <tbody>{!sorted.length && <tr><td colSpan={ranked ? 11 : 13} className="stage4-empty">No stocks match this snapshot and these filters.</td></tr>}
    {sorted.map((s, index) => <tr key={s.id || s.symbol}>
      {ranked && <td>{index + 1}</td>}
      <td><button className="stage4-chart-button" aria-label={`Price chart for ${s.symbol}`} onClick={() => onChart(s)}><BarChart3 size={14} /></button></td>
      <td className="cell-symbol"><Link className="stock-link" to={`/${market}/lookup/${encodeURIComponent(s.symbol)}`} target="_blank" rel="noopener noreferrer" title={`Open current lookup for ${s.symbol} in a separate tab (not the Stage 4 snapshot)`}>{s.symbol}</Link></td>
      <td title={s.companyName}>{s.companyName?.length > 30 ? `${s.companyName.slice(0, 27)}...` : s.companyName}</td>
      <td className="cell-muted">{s.sectorName}</td>
      <td>{formatPrice(s.closePrice, market)}</td>
      {!ranked && <td className="cell-muted">{formatMarketCap(s.marketCap, market)}</td>}
      <td className="stage4-rs" title="Mansfield relative strength; lower is weaker">{number(s.rsScore)}</td>
      <td style={{ color: s.momentumScore == null ? undefined : s.momentumScore > 0 ? 'var(--success)' : 'var(--danger)' }}>{number(s.momentumScore)}</td>
      <td><span className={`quadrant-badge q-${s.quadrant}`}>{s.quadrant || '—'}</span></td>
      <td><span className={`ad-badge ad-${s.adClassification}`}>{s.adClassification || '—'}</span></td>
      <td><span className={`class-badge class-${stage4Qualification(s) == null ? 'unknown' : s.classification}`}>
        {stage4Qualification(s) == null ? 'Unknown' : s.classification ? classLabel(s.classification) : s.isStage4 === true ? 'Stage 4' : 'Not Stage 4'}
      </span></td>
      {!ranked && <><td>{s.weeksInStage4 ?? '—'}</td><td><details className="stage4-indicators">
        <summary>{s.analysisStatus}</summary>
        <dl>
          <dt>MA10 / MA30</dt><dd>{formatPrice(s.ma10, market)} / {formatPrice(s.ma30, market)}</dd>
          <dt>MA30 −4w</dt><dd>{formatPrice(s.ma30FourWeeksAgo, market)}</dd>
          <dt>RS Δ 1/2/3w</dt><dd>{number(s.rsDelta1w)} / {number(s.rsDelta2w)} / {number(s.rsDelta3w)}</dd>
          <dt>ROC 1/2/3w %</dt><dd>{number(s.roc1w)} / {number(s.roc2w)} / {number(s.roc3w)}</dd>
          <dt>A/D ratio</dt><dd>{number(s.adRatio)}</dd>
          <dt>Price</dt><dd>{date(s.priceAsOfDate)}</dd>
          <dt>Eligibility</dt><dd title={s.eligibilityCapturedAt ?? undefined}>{date(s.eligibilityCapturedAt)}</dd>
        </dl>
        {s.errorMessage && <p className="stage4-error-text">{s.errorMessage}</p>}
      </details></td></>}
    </tr>)}</tbody>
  </table><div className="stage4-table-count">Showing {rows.length} stocks</div></div>;
}

function SnapshotChart({ market, stock, onClose }: { market: Market; stock: Stage4Result; onClose: () => void }) {
  const { theme } = useContext(ThemeContext);
  const [data, setData] = useState<{ bars: LookupBar[]; error?: string } | null>(null);
  useEffect(() => {
    let active = true;
    if (stock.priceAsOfDate) {
      fetchLookupBars(market, stock.symbol, 'daily').then(bars => {
        if (active) setData({ bars: bars.filter(b => b.date.slice(0, 10) <= stock.priceAsOfDate!.slice(0, 10)) });
      }).catch(e => { if (active) setData({ bars: [], error: errorText(e) }); });
    }
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', escape);
    return () => { active = false; window.removeEventListener('keydown', escape); };
  }, [market, stock, onClose]);
  return <div className="modal-overlay" onClick={onClose}>
    <section className="modal stage4-chart-modal" role="dialog" aria-modal="true" aria-label={`${stock.symbol} snapshot chart`} onClick={e => e.stopPropagation()}>
      <div className="modal-header"><h3>{stock.symbol} · {stock.weekNumber}</h3>
        <button className="modal-close" aria-label="Close chart" onClick={onClose}><X size={18} /></button></div>
      <div className="modal-body">
        <p>Daily prices through {date(stock.priceAsOfDate)}. The table contains the stored weekly indicators; this chart uses currently stored daily bars, not an immutable price archive.</p>
        {!stock.priceAsOfDate ? <p role="alert">No price-as-of date is available for this outcome.</p>
          : !data ? <p role="status">Loading price chart…</p>
          : data.error ? <p role="alert">{data.error}</p>
          : !data.bars.length ? <p>No stored prices are available through this snapshot date.</p>
          : <PriceChart bars={data.bars} activeEmas={[]} showPivot={false} theme={theme} height={420} />}
      </div>
    </section>
  </div>;
}

export default function Stage4AnalysisPage() {
  const { market } = useParams();
  if (market !== 'india' && market !== 'us') return <div className="page"><p role="alert">Unsupported market.</p><Link to="/">Home</Link></div>;
  return <Stage4Workspace key={market} market={market} />;
}

function Stage4Workspace({ market }: { market: Market }) {
  const [search, setSearch] = useSearchParams();
  const selected = search.get('runId') ?? '';
  const validRun = selected === '' || /^[1-9]\d*$/.test(selected);
  const sourceRun = search.get('sourceRunId');
  const [refresh, setRefresh] = useState(0);
  const [runsRefresh, setRunsRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [runs, setRuns] = useState<JobRun[]>([]);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [catalogErrors, setCatalogErrors] = useState<string[]>([]);
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const [tab, setTab] = useState(sourceRun ? 'runs' : 'overview');
  const [showTrigger, setShowTrigger] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [classification, setClassification] = useState('');
  const [sectorId, setSectorId] = useState('');
  const [minCap, setMinCap] = useState('');
  const [quadrant, setQuadrant] = useState('');
  const [adClass, setAdClass] = useState('');
  const [outcome, setOutcome] = useState('stage4');
  const [topSort, setTopSort] = useState<StockSort>({ key: 'rsScore', direction: 'asc' });
  const [stockSort, setStockSort] = useState<StockSort>({ key: 'rsScore', direction: 'asc' });
  const [chart, setChart] = useState<Stage4Result | null>(null);
  const key = `${selected}:${refresh}`;
  const current = snapshot?.key === key ? snapshot : null;
  const summary = current?.summary;

  useEffect(() => {
    let active = true;
    async function load() {
      if (!validRun) {
        if (active) setSnapshot({ key, summary: null, stocks: [], rotation: [], history: [], errors: ['Invalid run ID. Select a completed snapshot.'] });
        return;
      }
      try {
        const summary = await fetchStage4Summary(market, selected ? Number(selected) : undefined);
        if (!summary?.runId) {
          if (active) setSnapshot({ key, summary: null, stocks: [], rotation: [], history: [], errors: [] });
          return;
        }
        const [stocks, rotation, history] = await Promise.allSettled([
          fetchStage4Stocks(market, summary.runId, { classification: 'all' }), fetchStage4Rotation(market, summary.runId),
          fetchStage4History(market, summary.runId),
        ]);
        if (active) setSnapshot({
          key, summary,
          stocks: stocks.status === 'fulfilled' ? stocks.value : [],
          rotation: rotation.status === 'fulfilled' ? rotation.value : [],
          history: history.status === 'fulfilled' ? history.value : [],
          errors: [
            ...(stocks.status === 'rejected' ? [`Stocks: ${errorText(stocks.reason)}`] : []),
            ...(rotation.status === 'rejected' ? [`Sector rotation: ${errorText(rotation.reason)}`] : []),
            ...(history.status === 'rejected' ? [`History: ${errorText(history.reason)}`] : []),
          ],
        });
      } catch (e) {
        if (active) setSnapshot({ key, summary: null, stocks: [], rotation: [], history: [], errors: [errorText(e)] });
      }
    }
    void load();
    return () => { active = false; };
  }, [market, selected, validRun, key]);

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      fetchJobRuns({ market, jobType: 'stage4_analysis', pageSize: 100 }),
      fetchSectors(market),
    ]).then(([jobs, catalog]) => {
      if (!active) return;
      const errors: string[] = [];
      if (jobs.status === 'fulfilled') setRuns(jobs.value);
      else errors.push(`Runs: ${errorText(jobs.reason)}`);
      if (catalog.status === 'fulfilled') setSectors(catalog.value);
      else errors.push(`Sectors: ${errorText(catalog.reason)}`);
      setCatalogErrors(errors);
      setCatalogLoaded(true);
    });
    return () => { active = false; };
  }, [market, refresh, runsRefresh]);

  useEffect(() => {
    if (!runs.some(r => r.status === 'queued' || r.status === 'running')) return;
    const timer = setInterval(() => setRunsRefresh(n => n + 1), 10000);
    return () => clearInterval(timer);
  }, [runs]);

  useEffect(() => {
    if (!showTrigger) return;
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) setShowTrigger(false); };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [showTrigger, busy]);

  const chooseRun = (id: string) => setSearch(id ? { runId: id } : {});
  const launch = async (request: TriggerStage4Request) => {
    setBusy(true); setActionError(''); setNotice('');
    try {
      const { runId } = await triggerStage4(market, request);
      setNotice(`Queued Stage 4 run #${runId}. Your published snapshot is unchanged. Follow progress under Runs or Jobs; refresh after completion to load the latest full snapshot.`);
      setRunsRefresh(n => n + 1);
      setTab('runs');
      return true;
    } catch (e) { setActionError(errorText(e)); return false; }
    finally { setBusy(false); }
  };
  const visible = useMemo(() => (current?.stocks ?? []).filter(s =>
    (!classification || (stage4Qualification(s) != null && s.classification === classification)) &&
    (!sectorId || s.sectorId === Number(sectorId)) &&
    (!minCap || (s.marketCap != null && s.marketCap >= Number(minCap) * (market === 'india' ? 10_000_000 : 1_000_000))) &&
    (!quadrant || s.quadrant === quadrant) &&
    (!adClass || s.adClassification === adClass) &&
    (outcome === 'all' || (outcome === 'stage4' ? stage4Qualification(s) === true
      : outcome === 'unknown' ? stage4Qualification(s) == null : stage4Qualification(s) === false))
  ).sort(weakestFirst), [current, classification, sectorId, minCap, quadrant, adClass, outcome, market]);
  const top25 = [...(summary?.top25 ?? [])].filter(s => stage4Qualification(s) === true).sort(weakestFirst).slice(0, 25);
  const completed = runs.filter(r => r.status === 'completed');
  const selectedOutsideList = summary && !completed.some(r => r.id === summary.runId);
  // The run-pinned API also matches frozen filters, not merely the scope label.
  const comparableHistory = summary ? (current?.history ?? []).filter(h =>
    h.runId === summary.runId || (h.weekNumber <= summary.weekNumber && h.scope === summary.scope)
  ) : [];
  const historyRows = summary && !comparableHistory.some(h => h.runId === summary.runId)
    ? [{ runId: summary.runId, weekNumber: summary.weekNumber, runDate: '', totalStage4: summary.stage4Count, bySector: summary.bySector, scope: summary.scope }, ...comparableHistory]
    : comparableHistory;
  const barData = [...(summary?.bySector ?? [])].sort((a, b) => b.stage4Count - a.stage4Count).slice(0, 20).map(s => ({
    name: s.sectorName.length > 25 ? `${s.sectorName.slice(0, 22)}...` : s.sectorName,
    count: s.stage4Count,
  }));
  const lineData = [...historyRows].sort((a, b) => a.weekNumber.localeCompare(b.weekNumber)).map(h => ({
    date: h.weekNumber, count: h.totalStage4,
  }));
  const cycleSort = (sort: StockSort, key: keyof Stage4Result): StockSort => sort?.key !== key
    ? { key, direction: 'asc' } : sort.direction === 'asc' ? { key, direction: 'desc' } : null;

  return <main className="page stage4-page">
    <div className="page-header">
      <Link to={`/${market}`} className="back-link"><ChevronLeft size={16} /> Back</Link>
      <h1 className="page-title"><TrendingDown size={24} className="stage4-title-icon" />Stage 4 Analysis</h1>
      <span className="page-subtitle">{market === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
      <button className="btn btn-primary stage4-run-button" onClick={() => { setActionError(''); setShowTrigger(true); }} disabled={busy}>
        <Play size={14} /> Run Analysis
      </button>
    </div>
    <section className="toolbar stage4-snapshot-toolbar" aria-label="Snapshot selection">
      <span className="badge badge-count">F&amp;O only</span>
      <span className="cell-muted">Closed weeks only</span>
      <label className="stage4-snapshot-picker"><span className="cell-muted">Snapshot</span>
        <select className="select-input" value={selected} onChange={e => chooseRun(e.target.value)}>
          <option value="">Latest closed completed FULL F&amp;O snapshot</option>
          {selectedOutsideList && <option value={summary.runId}>#{summary.runId} · {summary.weekNumber} · {summary.scope}</option>}
          {selected && !summary && !completed.some(r => String(r.id) === selected) && <option value={selected}>Requested run #{selected}</option>}
          {completed.map(r => <option key={r.id} value={r.id}>#{r.id} · {r.weekNumber} · {stage4RunScope(r)} · {date(r.completedAt)}</option>)}
        </select>
      </label>
      <button className="btn btn-outline btn-sm" aria-label="Refresh Stage 4 snapshot" onClick={() => setRefresh(n => n + 1)}><RefreshCw size={14} /> Refresh</button>
      <Link className="btn btn-outline btn-sm" to={`/${market}/jobs`}>Jobs</Link>
      {summary && <span className="stage4-snapshot"><strong>Run #{summary.runId} · {summary.weekNumber}</strong>
        <span className={`stage4-badge stage4-scope-${summary.scope}`}>{summary.scope.toUpperCase()} F&amp;O scope</span>
        {summary.scope !== 'full' && <strong>Not a full-universe overview.</strong>}
      </span>}
    </section>

    {showTrigger && <div className="modal-overlay" onClick={() => { if (!busy) setShowTrigger(false); }}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="stage4-trigger-title" onClick={e => e.stopPropagation()}>
        <div className="modal-header"><h3 className="modal-title" id="stage4-trigger-title">Run Stage 4 Analysis</h3>
          <button className="modal-close" aria-label="Close run dialog" disabled={busy} onClick={() => setShowTrigger(false)}><X size={18} /></button>
        </div>
        {actionError && <p className="stage4-message stage4-error-text" role="alert">{actionError}</p>}
        <TriggerForm sectors={sectors} market={market} busy={busy} onCancel={() => setShowTrigger(false)} onRun={async request => {
          if (await launch(request)) setShowTrigger(false);
        }} />
      </section>
    </div>}
    {notice && <p className="stage4-message" role="status">{notice}</p>}
    {!showTrigger && actionError && <p className="stage4-message stage4-error-text" role="alert">{actionError}</p>}
    {catalogErrors.map(e => <p className="stage4-message stage4-error-text" role="alert" key={e}>{e} <button className="stage4-link" onClick={() => setRunsRefresh(n => n + 1)}>Retry</button></p>)}
    {current?.errors.map(e => <p className="stage4-message stage4-error-text" role="alert" key={e}>{e} <button className="stage4-link" onClick={() => setRefresh(n => n + 1)}>Retry</button></p>)}
    {summary && <details className="stage4-coverage" open={summary.skippedStocks > 0 || summary.errorStocks > 0 || !!summary.warnings?.length}>
      <summary>Data coverage: {summary.evaluatedStocks} / {summary.totalStocks} evaluated · {summary.skippedStocks} skipped · {summary.errorStocks} errors</summary>
      <p className="cell-muted stage4-context">Bearish weekly trends for F&amp;O stocks. No entries, stops, targets or execution; eligibility does not guarantee borrow availability or derivatives liquidity.</p>
      {!!summary.warnings?.length && <ul aria-label="Data warnings">{summary.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {(summary.skippedStocks > 0 || summary.errorStocks > 0) && <p role="status">Partial coverage: skipped/error outcomes are unknown, not confirmed non-Stage 4 stocks or exits. See F&amp;O Stocks → Unknown coverage.</p>}
      {summary.metrics && <details><summary>Snapshot metrics</summary><pre className="stage4-json">{JSON.stringify(summary.metrics, null, 2)}</pre></details>}
    </details>}
    {summary && <div className="stats-row">
      <div className="stat-card"><div className="stat-value">{summary.stage4Count}</div><div className="stat-label">Stage 4 Stocks</div></div>
      <div className="stat-card"><div className="stat-value" style={{ color: 'var(--success)' }}><ArrowUpRight size={18} className="stage4-stat-icon" /> {summary.newAdditions}</div><div className="stat-label">New Additions</div></div>
      <div className="stat-card"><div className="stat-value" style={{ color: '#a855f7' }}><Zap size={18} className="stage4-stat-icon" /> {summary.reEntries}</div><div className="stat-label">Re-Entries</div></div>
      <div className="stat-card"><div className="stat-value" style={{ color: 'var(--primary)' }}><Minus size={18} className="stage4-stat-icon" /> {summary.continuing}</div><div className="stat-label">Continuing</div></div>
      <div className="stat-card"><div className="stat-value" style={{ color: 'var(--danger)' }}><ArrowDownRight size={18} className="stage4-stat-icon" /> {summary.removed}</div><div className="stat-label">Removed</div></div>
    </div>}
    {(summary || tab === 'runs') && <nav className="analysis-tabs" aria-label="Stage 4 views">{[
      ['overview', 'Overview'], ['top25', 'Top 25'], ['sectors', 'By Sector'],
      ['rotation', 'Sector Rotation'], ['stocks', 'F&O Stocks'], ['history', 'History'], ['runs', 'Runs'],
    ].map(([value, label]) => <button key={value} className={`analysis-tab ${tab === value ? 'active' : ''}`} aria-pressed={tab === value} onClick={() => setTab(value)}>{label}</button>)}</nav>}
    {!current && tab !== 'runs' && <div role="status" className="loading"><div className="spinner" />Loading analysis...</div>}
    {current && !summary && !current.errors.length && tab !== 'runs' && <div className="empty-state">
      <div className="empty-state-icon"><TrendingDown size={40} /></div>
      <p className="empty-state-text">No completed {selected ? 'selected' : 'full F&O'} snapshot is available. Click "Run Analysis" to start or select a completed sample/filtered run.</p>
      <button className="btn btn-outline" onClick={() => setTab('runs')}>View Stage 4 runs</button>
    </div>}
    {summary && tab === 'overview' && <AnalysisOverviewCharts stage={4} sectors={barData} history={lineData} />}
    {summary && tab === 'top25' && <>
      <p className="cell-muted">Weakest 25 · {summary.weekNumber}. Mansfield RS ascending, momentum ascending, then symbol. Lower / more-negative RS means weaker benchmark-relative performance.</p>
      <StockTable rows={top25} market={market} onChart={setChart} ranked sort={topSort} onSort={key => setTopSort(sort => cycleSort(sort, key))} />
    </>}

    {summary && tab === 'stocks' && <>
      <section className="toolbar stage4-filters" aria-label="Snapshot stock filters">
        <Filter size={16} className="cell-muted" />
        <label className="stage4-field">Classification<select value={classification} onChange={e => {
          setClassification(e.target.value);
          if (e.target.value === 'removed') setOutcome('all');
        }}><option value="">All Stage 4</option>{classifications.map(c => <option key={c} value={c}>{classLabel(c)}</option>)}</select></label>
        <label className="stage4-field">Sector<select value={sectorId} onChange={e => setSectorId(e.target.value)}><option value="">All sectors in snapshot</option>{summary.bySector.map(s => <option key={s.sectorId} value={s.sectorId}>{s.sectorName}</option>)}</select></label>
        <label className="stage4-field">Min cap ({market === 'india' ? '₹ Cr' : '$M'})<input type="number" min="0" value={minCap} onChange={e => setMinCap(e.target.value)} /></label>
        <label className="stage4-field">Quadrant<select value={quadrant} onChange={e => setQuadrant(e.target.value)}><option value="">All quadrants</option>{['leading', 'improving', 'weakening', 'lagging'].map(q => <option key={q}>{q}</option>)}</select></label>
        <label className="stage4-field">A/D Class<select value={adClass} onChange={e => setAdClass(e.target.value)}><option value="">All A/D classes</option>{['accumulating', 'neutral', 'distributing'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label className="stage4-field">Outcome<select value={outcome} onChange={e => setOutcome(e.target.value)}><option value="stage4">Stage 4 only</option><option value="all">All frozen-scope outcomes</option><option value="unknown">Unknown coverage</option><option value="not">Evaluated, not Stage 4</option></select></label>
        <button className="btn btn-outline btn-sm" onClick={() => { setClassification(''); setSectorId(''); setMinCap(''); setQuadrant(''); setAdClass(''); setOutcome('stage4'); }}>Reset filters</button>
      </section>
      <p>{visible.length} outcomes · run #{summary.runId} · {summary.weekNumber} · {summary.scope} scope. Filters do not change the stored snapshot or summary counts.</p>
      <StockTable rows={visible} market={market} onChart={setChart} sort={stockSort} onSort={key => setStockSort(sort => cycleSort(sort, key))} />
    </>}

    {summary && tab === 'sectors' && <section>
      <p className="cell-muted">Stage 4 / eligible in this run's scope; unknown inputs remain in the denominator. This is not whole-market breadth.</p>
      {!summary.bySector.length ? <p className="stage4-empty">No sector breadth available.</p> : <div className="table-container"><table className="table"><thead><tr><th>Sector</th><th>Stage 4</th><th>Total</th><th>% in Stage 4</th><th>Bar</th></tr></thead><tbody>
        {[...summary.bySector].sort((a, b) => b.stage4Count - a.stage4Count || a.sectorName.localeCompare(b.sectorName)).map(s =>
          <tr key={s.sectorId}><td className="stage4-sector-name">{s.sectorName}</td>
            <td>{s.stage4Count > 0 ? <button className="stock-link" onClick={() => {
              setSectorId(String(s.sectorId)); setClassification(''); setOutcome('stage4');
              setMinCap(''); setQuadrant(''); setAdClass(''); setTab('stocks');
            }}>{s.stage4Count}</button> : 0}</td>
            <td className="cell-muted">{s.totalCount}</td>
            <td>{s.totalCount ? (s.stage4Count / s.totalCount * 100).toFixed(1) : '0'}%</td>
            <td className="stage4-sector-bar"><div className="sector-bar"><div className="sector-bar-fill" style={{ width: `${s.totalCount ? s.stage4Count / s.totalCount * 100 : 0}%` }} /></div></td>
          </tr>)}
      </tbody></table></div>}
    </section>}

    {summary && tab === 'rotation' && <section className="chart-card">
      <h3 className="chart-title"><Target size={18} className="stage4-title-icon" />Sector Rotation — Relative Strength vs Momentum</h3>
      <QuadrantChart data={current?.rotation ?? []} />
      <p className="cell-muted">Quadrants retain their usual RS / RS-change meaning: leading (+/+), weakening (+/−), improving (−/+), lagging (−/−). Negative momentum and distributing volume are descriptive, not qualification gates.</p>
      {!current?.rotation.length ? <p className="stage4-empty">No sector rotation available for this snapshot.</p> : <div className="table-container stage4-rotation-table"><table className="table"><thead><tr><th>Sector</th><th>Quadrant</th><th>Avg RS</th><th>Avg Momentum</th><th>Stocks</th><th>Accumulating</th><th>Distributing</th></tr></thead><tbody>
        {[...current.rotation].sort((a, b) => a.avgRSScore - b.avgRSScore).map(r => <tr key={r.sectorId}><td className="stage4-sector-name">{r.sectorName}</td><td><span className={`quadrant-badge q-${r.quadrant}`}>{r.quadrant}</span></td><td style={{ color: r.avgRSScore > 0 ? 'var(--success)' : 'var(--danger)' }}>{number(r.avgRSScore)}</td><td style={{ color: r.avgRSDelta2w > 0 ? 'var(--success)' : 'var(--danger)' }}>{number(r.avgRSDelta2w)}</td><td>{r.stockCount}</td><td style={{ color: 'var(--success)' }}>{r.accumulatingCount}</td><td style={{ color: 'var(--danger)' }}>{r.distributingCount}</td></tr>)}
      </tbody></table></div>}
    </section>}

    {summary && tab === 'history' && <section>
      <h2 className="section-title">Weekly history · {summary.scope} scope through {summary.weekNumber}</h2>
      <p className="cell-muted">Completed snapshots with the selected scope and matching frozen filters only, through the selected week. The selected week's exact run is pinned; select a row to view that snapshot.</p>
      <div className="table-container"><table className="table"><thead><tr><th>Week / run</th><th>Completed</th><th>Scope</th><th>Stage 4</th><th>Sector counts (Stage 4 / eligible)</th></tr></thead><tbody>
        {[...historyRows].sort((a, b) => b.weekNumber.localeCompare(a.weekNumber) || b.runId - a.runId).map(h => <tr key={h.runId} className={h.runId === summary.runId ? 'stage4-selected' : ''}>
          <td><button className="stage4-link" onClick={() => chooseRun(String(h.runId))}>{h.weekNumber} · #{h.runId}{h.runId === summary.runId ? ' (selected)' : ''}</button></td><td>{date(h.runDate)}</td><td>{h.scope ?? 'full'}</td><td>{h.totalStage4}</td><td>{h.bySector.map(s => `${s.sectorName}: ${s.stage4Count}/${s.totalCount}`).join(' · ') || '—'}</td>
        </tr>)}
      </tbody></table></div>
    </section>}

    {tab === 'runs' && <section>
      <h2 className="section-title">Stage 4 runs</h2>
      <p className="cell-muted">Latest 100 jobs. Retry creates a new run with the source's frozen universe/week and carries forward valid outcomes. It ignores manual run options. Only failed/completed source runs can be retried.</p>
      {!catalogLoaded ? <p role="status">Loading runs…</p> : !runs.length ? <p className="stage4-empty">No Stage 4 jobs yet.</p> : <div className="table-container"><table className="table"><thead><tr><th>Run</th><th>Week / scope</th><th>Status</th><th>Coverage / metrics</th><th>Error</th><th>Actions</th></tr></thead><tbody>
        {runs.map(r => <tr key={r.id} className={String(r.id) === sourceRun ? 'stage4-selected' : ''}>
          <td>#{r.id}<div className="cell-muted">{date(r.createdAt)}</div></td><td>{r.weekNumber}<div><span className={`stage4-badge stage4-scope-${stage4RunScope(r)}`}>{stage4RunScope(r)}</span></div></td>
          <td>{r.status} · {r.progress}%</td><td><details><summary>Metrics / frozen parameters</summary><pre className="stage4-json">{JSON.stringify({ metrics: r.metrics, parameters: r.parameters }, null, 2)}</pre></details></td>
          <td className="stage4-error-text">{r.errorMessage || '—'}</td><td><div className="stage4-run-actions">
            {r.status === 'completed' && <button className="btn btn-outline btn-sm" onClick={() => { chooseRun(String(r.id)); setTab('overview'); }}>View snapshot</button>}
            {(r.status === 'completed' || r.status === 'failed') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={() => launch({ retrySourceRunId: r.id })}>Retry unresolved #{r.id}</button>}
            <Link to={`/${market}/jobs`}>Job details / cancellation</Link>
          </div></td>
        </tr>)}
      </tbody></table></div>}
    </section>}
    {chart && <SnapshotChart key={`${chart.runId}:${chart.symbol}`} market={market} stock={chart} onClose={() => setChart(null)} />}
  </main>;
}

function TriggerForm({ sectors, market, busy, onRun, onCancel }: {
  sectors: Sector[]; market: Market; busy: boolean; onRun: (request: TriggerStage4Request) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const [sample, setSample] = useState(false);
  const [week, setWeek] = useState('');
  const [sectorIds, setSectorIds] = useState<number[]>([]);
  const [limit, setLimit] = useState('');
  const [minCap, setMinCap] = useState('');
  const [maxCap, setMaxCap] = useState('');
  const [error, setError] = useState('');
  return <form onSubmit={e => {
    e.preventDefault(); setError('');
    if (week && !validIsoWeek(week)) { setError('Enter a real ISO week, e.g. 2026-W47.'); return; }
    if (minCap && maxCap && Number(minCap) > Number(maxCap)) { setError('Minimum market cap must not exceed maximum.'); return; }
    void onRun({
      ...(sample ? { testSampleOnly: true } : {}),
      ...(week ? { weekNumber: week } : {}),
      ...(sectorIds.length ? { sectorIds } : {}),
      ...(limit ? { limit: Number(limit) } : {}),
      ...(minCap ? { minMarketCap: Number(minCap) } : {}),
      ...(maxCap ? { maxMarketCap: Number(maxCap) } : {}),
    });
  }}>
    <div className="modal-body">
      <p className="cell-muted">Configure filters for the analysis run. F&amp;O eligibility is mandatory in both markets.</p>
      <div className="form-group">
        <label className="form-label">Run Mode</label>
        <div className="stage4-run-modes">
          <button type="button" className={`btn btn-sm ${sample ? 'btn-primary' : 'btn-outline'}`} onClick={() => setSample(true)}>F&amp;O test sample</button>
          <button type="button" className={`btn btn-sm ${!sample ? 'btn-primary' : 'btn-outline'}`} onClick={() => setSample(false)}>Full F&amp;O universe</button>
        </div>
        <p className="cell-muted stage4-form-help">{sample ? 'Curated test-sample stocks that are also F&O eligible; not a fixed stock count.' : 'All F&O-eligible stocks in the selected sectors.'}</p>
      </div>
      <label className="form-group stage4-form-field"><span className="form-label">Closed ISO week (optional)</span><input className="form-input" type="week" value={week} onChange={e => setWeek(e.target.value)} /></label>
      <p className="cell-muted stage4-form-help">Leave blank for the latest closed exchange-local week. Future or unfinished weeks are rejected; the browser's current week is never assumed.</p>
      <div className="form-group">
        <span className="form-label">Sectors (leave empty for all)</span>
        <div className="stage4-sector-actions">
          <button type="button" className="btn btn-sm" onClick={() => setSectorIds(sectors.map(s => s.id))}>Select All</button>
          <button type="button" className="btn btn-sm" onClick={() => setSectorIds([])}>Deselect All</button>
        </div>
        <div className="stage4-sector-options">{sectors.map(sector => <label key={sector.id}>
          <input type="checkbox" checked={sectorIds.includes(sector.id)} onChange={e => setSectorIds(ids => e.target.checked ? [...ids, sector.id] : ids.filter(id => id !== sector.id))} />
          {sector.sectorName}
        </label>)}</div>
      </div>
      <label className="form-group stage4-form-field"><span className="form-label">Limit (optional)</span><input className="form-input" type="number" min="1" step="1" value={limit} onChange={e => setLimit(e.target.value)} /></label>
      <label className="form-group stage4-form-field"><span className="form-label">Min market cap ({currencySymbol(market)}, raw)</span><input className="form-input" type="number" min="0" value={minCap} onChange={e => setMinCap(e.target.value)} /></label>
      <label className="form-group stage4-form-field"><span className="form-label">Max market cap ({currencySymbol(market)}, raw)</span><input className="form-input" type="number" min="0" value={maxCap} onChange={e => setMaxCap(e.target.value)} /></label>
      <p className="cell-muted stage4-form-help">Run scope: {sample ? 'SAMPLE' : sectorIds.length || limit || minCap || maxCap ? 'FILTERED' : 'FULL'} F&amp;O. Historical prices use eligibility flags and market caps captured when the run starts, not a historical derivatives universe.</p>
      {error && <p className="stage4-error-text" role="alert">{error}</p>}
    </div>
    <div className="modal-footer">
      {onCancel && <button className="btn btn-outline" type="button" disabled={busy} onClick={onCancel}>Cancel</button>}
      <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? 'Queuing...' : 'Start Analysis'}</button>
    </div>
  </form>;
}
