import { useState, useEffect, useCallback, Fragment } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, Breakout, BreakoutStats, BreakoutProfile, ScannerPerformance, ScoringWeight, BreakoutPnlSummary, BreakoutDay, NearPivot } from '../api';
import { fetchBreakouts, fetchBreakoutStats, fetchScannerPerformance, fetchScoringWeights, updateScoringWeight, fetchBreakoutPnl, fetchBreakoutsByDay, fetchNearPivots, triggerScanner, fetchJobRun } from '../api';
import { ChevronLeft, ChevronDown, RefreshCw, Loader2, Activity, Target, Sliders, Crosshair, LineChart, Table } from 'lucide-react';
import { StockLookupModal, MiniSymbolChart } from './StockLookupPage';

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtNum(v?: number | null, dp = 2): string {
  if (v == null) return '—';
  return v.toFixed(dp);
}

function curSym(market: Market): string {
  return market === 'us' ? '$' : '₹';
}

function fmtPrice(v: number | null | undefined, market: Market): string {
  if (v == null) return '—';
  return `${curSym(market)}${v.toFixed(2)}`;
}

function fmtMoney(v: number | null | undefined, market: Market): string {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}${curSym(market)}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtDateTime(v?: string | null): string {
  if (!v) return '—';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function ScoreBadge({ score }: { score?: number | null }) {
  if (score == null) return <span className="cell-muted">—</span>;
  const color = score >= 70 ? 'var(--success)' : score >= 50 ? 'var(--warning, #d08700)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 700, color }}>{score}</span>;
}

function SideBadge({ side }: { side?: string | null }) {
  if (!side || side === 'none') return <span className="cell-muted">—</span>;
  const long = side === 'long';
  return (
    <span className="badge" style={{
      background: long ? 'rgba(22,163,74,0.15)' : 'rgba(220,38,38,0.15)',
      color: long ? 'var(--success)' : 'var(--danger)', fontWeight: 600, textTransform: 'uppercase', fontSize: '0.7rem',
    }}>{side}</span>
  );
}

function PnLCell({ v }: { v?: number | null }) {
  if (v == null) return <span className="cell-muted">—</span>;
  const color = v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 600, color }}>{fmtPct(v)}</span>;
}

function MoneyCell({ v, market }: { v?: number | null; market: Market }) {
  if (v == null) return <span className="cell-muted">—</span>;
  const color = v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 600, color }}>{fmtMoney(v, market)}</span>;
}

interface RationaleComponent {
  component: string;
  weight: number;
  score: number | null;
  available: boolean;
  contribution: number;
}
interface ConfidenceRationaleData {
  profile?: string;
  scanner?: string | null;
  confidence?: number;
  components?: RationaleComponent[];
  notes?: { epsUpsidePct?: number | null; baseAccumulation?: number | null; breakoutVolumeScore?: number | null };
}
const COMPONENT_LABELS: Record<string, string> = {
  setup: 'Setup (scanner reliability)',
  fundamental: 'Fundamentals',
  volume: 'Breakout volume',
  eps: 'EPS upside',
  ai: 'AI signal (placeholder)',
};

function ConfidenceRationale({ breakout }: { breakout: Breakout }) {
  let data: ConfidenceRationaleData | null = null;
  try {
    data = breakout.confidenceRationaleJson ? JSON.parse(breakout.confidenceRationaleJson) : null;
  } catch { data = null; }
  if (!data) return <div className="cell-muted" style={{ padding: 8 }}>No rationale recorded for this breakout.</div>;

  const comps = data.components ?? [];
  return (
    <div style={{ padding: '10px 12px' }}>
      <div style={{ marginBottom: 8, fontSize: '0.85rem' }}>
        <strong>Why confidence {data.confidence}?</strong>{' '}
        <span className="cell-muted">
          {data.profile} breakout triggered by {data.scanner || '—'}. Confidence blends each component below by
          its profile weight: <code>100 × Σ(weight × score) / Σ(weight)</code> over the available components.
        </span>
      </div>
      <div className="table-scroll">
      <table className="table" style={{ fontSize: '0.82rem' }}>
        <thead>
          <tr>
            <th>Component</th>
            <th style={{ textAlign: 'right' }}>Mix weight</th>
            <th style={{ textAlign: 'right' }}>Score (0–1)</th>
            <th style={{ textAlign: 'right' }}>Contribution</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {comps.map(c => (
            <tr key={c.component} style={{ opacity: c.available ? 1 : 0.5 }}>
              <td style={{ fontWeight: 600 }}>{COMPONENT_LABELS[c.component] ?? c.component}</td>
              <td className="cell-right">{c.weight.toFixed(2)}</td>
              <td className="cell-right">{c.score != null ? c.score.toFixed(2) : '—'}</td>
              <td className="cell-right">{c.available ? c.contribution.toFixed(3) : '—'}</td>
              <td className="cell-muted" style={{ fontSize: '0.78rem' }}>
                {c.component === 'eps' && data!.notes?.epsUpsidePct != null && `EPS upside ${data!.notes.epsUpsidePct}% → clamped to 0–1 over 25%`}
                {c.component === 'volume' && (data!.notes?.baseAccumulation != null
                  ? `Base ${(data!.notes.baseAccumulation * 100).toFixed(0)}% up-day vol; 60% breakout bar + 40% accumulation`
                  : 'Breakout-bar volume vs 20-day avg')}
                {c.component === 'fundamental' && (c.available ? 'Fraction of fundamental checks passing' : 'No fundamentals for this symbol')}
                {c.component === 'ai' && 'Neutral 0.5 until an AI signal feeds in'}
                {!c.available && c.component !== 'ai' && c.component !== 'fundamental' && 'Not available — dropped from the blend'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}


export default function BreakoutsPage() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;
  const navigate = useNavigate();
  const [tab, setTab] = useState<'breakouts' | 'nearpivot' | 'patterns' | 'weights'>('breakouts');
  // Swing vs positional is shared across breakout views so the chosen style persists when switching sections.
  const [profile, setProfile] = useState<BreakoutProfile>('swing');
  const profiled = tab === 'breakouts';

  return (
    <div className="page">
      <a className="back-link" onClick={() => navigate(`/${m}`)} style={{ cursor: 'pointer' }}>
        <ChevronLeft size={16} /> Back
      </a>
      <div className="page-header">
        <div>
          <h1 className="page-title">Breakouts</h1>
          <p className="page-subtitle">
            Breakout blotter, confidence, and performance &middot; {m === 'india' ? 'Indian' : 'US'} Market
          </p>
        </div>
      </div>

      <div className="toolbar" style={{ gap: 8 }}>
        <button className={`btn ${tab === 'breakouts' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('breakouts')}>
          <Activity size={16} /> Breakouts
        </button>
        <button className={`btn ${tab === 'nearpivot' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('nearpivot')}>
          <Crosshair size={16} /> Near Pivot
        </button>
        <button className={`btn ${tab === 'patterns' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('patterns')}>
          <Target size={16} /> Pattern Performance
        </button>
        <button className={`btn ${tab === 'weights' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('weights')}>
          <Sliders size={16} /> Confidence Weights
        </button>
      </div>

      {profiled && <ProfileTabs profile={profile} onChange={setProfile} />}

      {tab === 'breakouts' ? <BreakoutsTab market={m} profile={profile} />
        : tab === 'nearpivot' ? <NearPivotTab market={m} />
        : tab === 'patterns' ? <PatternsTab market={m} />
        : <WeightsTab market={m} />}
    </div>
  );
}

/**
 * Swing / Positional sub-tab selector shared by breakout views.
 * Swing leans on technical setup; positional weights fundamentals more heavily.
 */
function ProfileTabs({ profile, onChange }: { profile: BreakoutProfile; onChange: (p: BreakoutProfile) => void }) {
  return (
    <div className="sub-tabs">
      <button className={`sub-tab ${profile === 'swing' ? 'on' : ''}`} onClick={() => onChange('swing')}>
        Swing <span className="sub-tab-note">technical</span>
      </button>
      <button className={`sub-tab ${profile === 'positional' ? 'on' : ''}`} onClick={() => onChange('positional')}>
        Positional <span className="sub-tab-note">fundamentals 70%</span>
      </button>
    </div>
  );
}

const PIVOT_PCTS = [2, 3, 5, 8, 10, 15];

/**
 * Near Pivot: scanner-flagged names sitting within a chosen %% of their breakout pivot but
 * not yet broken out. The distance threshold is adjustable so you can widen/tighten the watch.
 */
function NearPivotTab({ market }: { market: Market }) {
  const [rows, setRows] = useState<NearPivot[]>([]);
  const [loading, setLoading] = useState(false);
  const [maxPct, setMaxPct] = useState(5);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [lookup, setLookup] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<keyof NearPivot>('distancePct');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const [chartView, setChartView] = useState(false);
  const pageSize = 25;

  const load = useCallback(() => {
    setLoading(true);
    fetchNearPivots(market, { maxDistancePct: maxPct })
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market, maxPct]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [rows, sortKey, sortDir, maxPct]);

  const runScan = useCallback(async () => {
    setRunning(true); setRunMsg('Queued scan…');
    try {
      // Near-pivot scan refreshes the watchlist only; it must NOT open paper breakouts
      // (those come from the scheduled pre-close run).
      const { runId } = await triggerScanner(market, { universe: 'stage2', manageTrades: false });
      for (let i = 0; i < 120; i++) {
        await new Promise(r => setTimeout(r, 5000));
        const j = await fetchJobRun(runId);
        setRunMsg(`Scan ${j.status}${j.progress ? ` ${j.progress}%` : ''}…`);
        if (j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled') {
          setRunMsg(`Scan ${j.status}.`); break;
        }
      }
      load();
    } catch (e) { setRunMsg(`Scan failed: ${e instanceof Error ? e.message : e}`); }
    finally { setRunning(false); }
  }, [market, load]);

  const toggleSort = (key: keyof NearPivot) => {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir(key === 'distancePct' ? 'asc' : 'desc'); }
  };
  const merged: NearPivot[] = (() => {
    const byKey = new Map<string, NearPivot>();
    for (const r of rows) {
      const k = `${r.ticker}|${r.direction}|${r.pivotPrice.toFixed(2)}`;
      const cur = byKey.get(k);
      if (cur) {
        const types = new Set(cur.tradeType.split('+').concat(r.tradeType));
        cur.tradeType = [...types].sort().join('+');
        cur.scannerHitCount = Math.max(cur.scannerHitCount, r.scannerHitCount);
        cur.flaggedScanners = [...new Set([...cur.flaggedScanners, ...r.flaggedScanners])];
      } else {
        byKey.set(k, { ...r });
      }
    }
    return [...byKey.values()];
  })();
  const sorted = [...merged].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null) return bv == null ? 0 : 1;
    if (bv == null) return -1;
    const cmp = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const pageRows = sorted.slice((page - 1) * pageSize, page * pageSize);
  const arrow = (key: keyof NearPivot) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
  const th = (key: keyof NearPivot, label: string, align: 'left' | 'right' | 'center' = 'left') => (
    <th onClick={() => toggleSort(key)} style={{ cursor: 'pointer', userSelect: 'none', textAlign: align, whiteSpace: 'nowrap' }}>
      {label}{arrow(key)}
    </th>
  );

  return (
    <>
      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <p className="page-subtitle" style={{ margin: 0 }}>
          Swing &amp; positional setups within
        </p>
        <select className="search-input" style={{ width: 'auto' }} value={maxPct}
          onChange={e => setMaxPct(Number(e.target.value))}>
          {PIVOT_PCTS.map(p => <option key={p} value={p}>{p}%</option>)}
        </select>
        <p className="page-subtitle" style={{ margin: 0 }}>of breakout — flagged, not yet broken.</p>
        {runMsg && <span className="page-subtitle" style={{ margin: 0 }}>{runMsg}</span>}
        <button className="btn btn-primary btn-sm" onClick={runScan} disabled={running} style={{ marginLeft: 'auto' }}>
          {running ? <Loader2 size={14} className="spin" /> : <Activity size={14} />} {running ? 'Running…' : 'Run scan'}
        </button>
        <div className="seg-toggle">
          <button className={!chartView ? 'on' : ''} onClick={() => setChartView(false)} title="Grid view"><Table size={13} /> Grid</button>
          <button className={chartView ? 'on' : ''} onClick={() => setChartView(true)} title="Chart-only view"><LineChart size={13} /> Charts</button>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading near-pivot candidates...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Crosshair size={48} /></div>
          <p className="empty-state-text">
            No names within {maxPct}% of their pivot. Candidates appear after the daily pre-close
            scan when a scanner flags a stock sitting just below its breakout level. Widen the % to see more.
          </p>
        </div>
      ) : (
        <>
        {chartView ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
            {pageRows.map(r => (
              <MiniSymbolChart key={r.id} market={market} symbol={r.ticker}
                label={`${r.tradeType} · ${r.distancePct.toFixed(1)}%`} onOpen={() => setLookup(r.ticker)} />
            ))}
          </div>
        ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                {th('ticker', 'Ticker')}
                {th('tradeType', 'Type')}
                {th('direction', 'Dir')}
                {th('lastClose', 'Last', 'right')}
                {th('pivotPrice', 'Pivot', 'right')}
                {th('distancePct', 'To pivot', 'right')}
                {th('relVolume', 'Rel vol', 'right')}
                {th('volumeConfirmed', 'Vol ok', 'center')}
                {th('scannerHitCount', 'Scanners', 'center')}
              </tr>
            </thead>
            <tbody>
              {pageRows.map(r => (
                <tr key={r.id}>
                  <td><button className="stock-link" onClick={() => setLookup(r.ticker)} title={r.companyName || ''}>{r.ticker}</button></td>
                  <td style={{ textTransform: 'capitalize' }}>{r.tradeType}</td>
                  <td><SideBadge side={r.direction} /></td>
                  <td className="cell-right">{fmtPrice(r.lastClose, market)}</td>
                  <td className="cell-right">{fmtPrice(r.pivotPrice, market)}</td>
                  <td className="cell-right" style={{ fontWeight: 600, color: 'var(--warning, #d08700)' }}>{r.distancePct.toFixed(2)}%</td>
                  <td className="cell-right">{r.relVolume != null ? `${r.relVolume.toFixed(2)}x` : '—'}</td>
                  <td className="cell-center">{r.volumeConfirmed ? '✓' : '—'}</td>
                  <td className="cell-center" title={r.flaggedScanners.join(', ')}>
                    <span className="badge badge-count">{r.scannerHitCount}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {totalPages > 1 && (
          <div className="toolbar" style={{ gap: 8, alignItems: 'center', justifyContent: 'flex-end', marginTop: 8 }}>
            <span className="page-subtitle" style={{ margin: 0 }}>
              {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, sorted.length)} of {sorted.length}
            </span>
            <button className="btn btn-ghost btn-sm" disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>Prev</button>
            <span className="page-subtitle" style={{ margin: 0 }}>{page} / {totalPages}</span>
            <button className="btn btn-ghost btn-sm" disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}>Next</button>
          </div>
        )}
        </>
      )}
      {lookup && <StockLookupModal market={market} symbol={lookup} onClose={() => setLookup(null)} />}
    </>
  );
}

function PatternsTab({ market }: { market: Market }) {
  const [rows, setRows] = useState<ScannerPerformance[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    fetchScannerPerformance(market)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="toolbar" style={{ gap: 8 }}>
        <p className="page-subtitle" style={{ margin: 0 }}>
          Each scanner is a pattern. Reliability is the Wilson lower bound of its breakout win rate —
          which patterns are actually paying off.
        </p>
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading pattern performance...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Target size={48} /></div>
          <p className="empty-state-text">No breakouts yet, so no pattern performance to show. Patterns build up as live breakouts trigger.</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Pattern (scanner)</th>
                <th style={{ textAlign: 'center' }}>Reliability</th>
                <th style={{ textAlign: 'center' }}>Win rate</th>
                <th style={{ textAlign: 'center' }}>Breakouts</th>
                <th style={{ textAlign: 'center' }}>Open</th>
                <th style={{ textAlign: 'center' }}>W / L</th>
                <th style={{ textAlign: 'right' }}>Avg P&amp;L</th>
                <th style={{ textAlign: 'right' }}>Realized</th>
                <th style={{ textAlign: 'right' }}>Open P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.scanner}>
                  <td style={{ fontWeight: 600 }}>{r.scanner}</td>
                  <td className="cell-center"><ScoreBadge score={Math.round(r.reliabilityScore)} /></td>
                  <td className="cell-center">{r.winRatePct != null ? `${r.winRatePct.toFixed(0)}%` : '—'}</td>
                  <td className="cell-center">{r.trades}</td>
                  <td className="cell-center">{r.openCount}</td>
                  <td className="cell-center">{r.wins} / {r.losses}</td>
                  <td className="cell-right"><PnLCell v={r.avgPnLPct} /></td>
                  <td className="cell-right"><MoneyCell v={r.realizedPnLAmount} market={market} /></td>
                  <td className="cell-right"><MoneyCell v={r.openPnLAmount} market={market} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function WeightsTab({ market }: { market: Market }) {
  const [rows, setRows] = useState<ScoringWeight[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});

  const load = useCallback(() => {
    setLoading(true);
    fetchScoringWeights(market)
      .then(rs => { setRows(rs); setDrafts({}); })
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  const save = useCallback((id: number, update: { weight?: number; manualOverride?: boolean }) => {
    setSaving(id);
    updateScoringWeight(market, id, update)
      .then(updated => setRows(rs => rs.map(r => r.id === id ? updated : r)))
      .catch(() => { /* keep prior */ })
      .finally(() => setSaving(null));
  }, [market]);

  const mix = rows.filter(r => r.category === 'mix');

  const renderRow = (r: ScoringWeight) => {
    const draft = drafts[r.id];
    const val = draft !== undefined ? draft : r.weight.toFixed(2);
    const commit = () => {
      const n = parseFloat(val);
      if (!isNaN(n) && Math.abs(n - r.weight) > 1e-9) save(r.id, { weight: n });
      setDrafts(d => { const c = { ...d }; delete c[r.id]; return c; });
    };
    return (
      <tr key={r.id}>
        <td style={{ fontWeight: 600 }}>{mixLabel(r.componentKey)}</td>
        <td className="cell-right">
          <input className="search-input" style={{ width: 80, textAlign: 'right' }} type="number" step="0.01" min="0" max="1"
            value={val}
            onChange={e => setDrafts(d => ({ ...d, [r.id]: e.target.value }))}
            onBlur={commit}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
        </td>
        <td className="cell-right cell-muted">{r.seedWeight.toFixed(2)}</td>
        <td className="cell-center">
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input type="checkbox" checked={r.manualOverride}
              onChange={e => save(r.id, { manualOverride: e.target.checked })} />
            <span className="cell-muted" style={{ fontSize: '0.78rem' }}>{r.manualOverride ? 'pinned' : 'auto'}</span>
          </label>
        </td>
        <td className="cell-center">{saving === r.id ? <Loader2 size={14} className="spin" /> : null}</td>
      </tr>
    );
  };

  return (
    <>
      <div className="toolbar" style={{ gap: 8 }}>
        <p className="page-subtitle" style={{ margin: 0 }}>
          Editable mix weights set how much setup, fundamental, and volume components drive each
          swing/positional breakout confidence. Scanner reliability now lives on the Pattern Performance tab.
        </p>
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading weights...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Sliders size={48} /></div>
          <p className="empty-state-text">No weights seeded yet. They are created on the next scanner run.</p>
        </div>
      ) : (
        <>
          <h3 style={{ margin: '16px 0 8px' }}>Mix weights (per profile blend)</h3>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Component</th>
                  <th style={{ textAlign: 'right' }}>Weight</th>
                  <th style={{ textAlign: 'right' }}>Seed</th>
                  <th style={{ textAlign: 'center' }}>Override</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>{mix.map(renderRow)}</tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function mixLabel(key: string): string {
  const [profile, component] = key.split(':');
  const comp = COMPONENT_LABELS[component] ?? component;
  return `${profile.charAt(0).toUpperCase()}${profile.slice(1)} · ${comp}`;
}

type BreakoutView = 'positions' | 'pnl' | 'day';

function BreakoutsTab({ market, profile }: { market: Market; profile: BreakoutProfile }) {
  const [view, setView] = useState<BreakoutView>('positions');
  return (
    <>
      <div className="sub-tabs" style={{
        marginTop: 6, marginBottom: 4, marginLeft: 14, paddingLeft: 14,
        borderLeft: '2px solid var(--border, rgba(127,127,127,0.3))',
      }}>
        <span className="sub-tab-note" style={{ alignSelf: 'center', textTransform: 'capitalize', marginRight: 4 }}>
          {profile} ›
        </span>
        <button className={`sub-tab ${view === 'positions' ? 'on' : ''}`} onClick={() => setView('positions')}>
          Positions <span className="sub-tab-note">active &amp; closed</span>
        </button>
        <button className={`sub-tab ${view === 'pnl' ? 'on' : ''}`} onClick={() => setView('pnl')}>
          P&amp;L <span className="sub-tab-note">by period</span>
        </button>
        <button className={`sub-tab ${view === 'day' ? 'on' : ''}`} onClick={() => setView('day')}>
          Day <span className="sub-tab-note">entries &amp; exits</span>
        </button>
      </div>
      {view === 'positions' ? <PositionsView market={market} profile={profile} />
        : view === 'pnl' ? <PnlView market={market} profile={profile} />
        : <DayView market={market} profile={profile} />}
    </>
  );
}

function PositionsView({ market, profile }: { market: Market; profile: BreakoutProfile }) {
  const [status, setStatus] = useState<string>('active');
  const [rows, setRows] = useState<Breakout[]>([]);
  const [stats, setStats] = useState<BreakoutStats | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchBreakouts(market, { status: status || undefined, tradeType: profile }),
      fetchBreakoutStats(market),
    ])
      .then(([t, s]) => { setRows(t); setStats(s); })
      .catch(() => { setRows([]); setStats(null); })
      .finally(() => setLoading(false));
  }, [market, status, profile]);

  useEffect(() => { load(); }, [load]);

  const closed = status === 'closed';

  return (
    <>
      {stats && (
        <div className="toolbar" style={{ gap: 16, flexWrap: 'wrap' }}>
          <Stat label="Active" value={stats.activeCount} />
          <Stat label="Closed" value={stats.closedCount} />
          <Stat label="Wins" value={stats.wins} color="var(--success)" />
          <Stat label="Losses" value={stats.losses} color="var(--danger)" />
          <Stat label="Win rate" value={stats.winRatePct != null ? `${stats.winRatePct}%` : '—'} />
          <Stat label="Swing P&L (open/real)"
            value={`${fmtMoney(stats.swingOpenPnLAmount, market)} / ${fmtMoney(stats.swingRealizedPnLAmount, market)}`}
            color={(stats.swingOpenPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          <Stat label="Positional P&L (open/real)"
            value={`${fmtMoney(stats.positionalOpenPnLAmount, market)} / ${fmtMoney(stats.positionalRealizedPnLAmount, market)}`}
            color={(stats.positionalOpenPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          <Stat label="Total P&L (open/real)"
            value={`${fmtMoney(stats.openPnLAmount, market)} / ${fmtMoney(stats.realizedPnLAmount, market)}`}
            color={(stats.openPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
        </div>
      )}

      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap' }}>
        <select className="search-input" style={{ width: 'auto' }} value={status} onChange={e => setStatus(e.target.value)}>
          <option value="active">Active</option>
          <option value="closed">Closed</option>
          <option value="">All</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading breakouts...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Activity size={48} /></div>
          <p className="empty-state-text">
            No breakouts yet. A scanner hit is only a setup — a breakout opens on a volume-confirmed
            break of support/resistance on the daily pre-close scan. The blotter fills as live breakouts trigger.
          </p>
        </div>
      ) : (
        <BreakoutBlotter rows={rows} market={market} closed={closed} />
      )}
    </>
  );
}

/** Shared breakout table with an expandable confidence-rationale row. */
function BreakoutBlotter({ rows, market, closed }: { rows: Breakout[]; market: Market; closed: boolean }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [lookup, setLookup] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<keyof Breakout>('confidenceScore');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [chartView, setChartView] = useState(false);

  const toggleSort = (key: keyof Breakout) => {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };
  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null) return bv == null ? 0 : 1;
    if (bv == null) return -1;
    let cmp = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });
  const arrow = (key: keyof Breakout) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
  const th = (key: keyof Breakout, label: string, align: 'left' | 'right' | 'center' = 'left') => (
    <th onClick={() => toggleSort(key)} style={{ cursor: 'pointer', userSelect: 'none', textAlign: align, whiteSpace: 'nowrap' }}>
      {label}{arrow(key)}
    </th>
  );

  return (
    <>
    <div className="toolbar" style={{ justifyContent: 'flex-end', marginBottom: 6 }}>
      <div className="seg-toggle">
        <button className={!chartView ? 'on' : ''} onClick={() => setChartView(false)} title="Grid view"><Table size={13} /> Grid</button>
        <button className={chartView ? 'on' : ''} onClick={() => setChartView(true)} title="Chart-only view"><LineChart size={13} /> Charts</button>
      </div>
    </div>
    {chartView ? (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
        {sorted.map(t => (
          <MiniSymbolChart key={t.id} market={market} symbol={t.ticker}
            label={`${t.tradeType} · ${fmtNum(t.pnLPct)}%`} onOpen={() => setLookup(t.ticker)} />
        ))}
        {lookup && <StockLookupModal market={market} symbol={lookup} onClose={() => setLookup(null)} />}
      </div>
    ) : (
    <div className="table-container">
      <table className="table">
        <thead>
          <tr>
            {th('ticker', 'Ticker')}
            {th('confidenceScore', 'Confidence', 'center')}
            {th('tradeType', 'Type')}
            {th('direction', 'Dir')}
            {th('entryAt', 'Entry time')}
            {th('qty', 'Qty', 'right')}
            {th('entryPrice', 'Entry', 'right')}
            {th('currentStop', 'Trail', 'right')}
            {th('lastPrice', closed ? 'Last' : 'Current', 'right')}
            {th('exitPrice', 'Exit', 'right')}
            {th('pnLPct', 'P&L %', 'right')}
            {th('pnLAmount', 'P&L', 'right')}
            {th('mfePct', 'MFE / MAE', 'right')}
            {th('scannerHitCount', 'Scanners', 'center')}
            {th('status', 'Status')}
          </tr>
        </thead>
        <tbody>
          {sorted.map(t => (
            <Fragment key={t.id}>
            <tr>
              <td><button className="stock-link" onClick={() => setLookup(t.ticker)}>{t.ticker}</button></td>
              <td className="cell-center">
                {t.confidenceScore != null ? (
                  <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', gap: 4 }}
                    title="Explain why this breakout got its confidence score"
                    onClick={() => setExpanded(expanded === t.id ? null : t.id)}>
                    <ScoreBadge score={Math.round(t.confidenceScore)} />
                    <ChevronDown size={12} style={{ transform: expanded === t.id ? 'rotate(180deg)' : 'none' }} />
                  </button>
                ) : <span className="cell-muted">—</span>}
              </td>
              <td>{t.tradeType}</td>
              <td><SideBadge side={t.direction} /></td>
              <td style={{ fontSize: '0.8rem', whiteSpace: 'nowrap' }}>{fmtDateTime(t.entryAt)}</td>
              <td className="cell-right">{t.qty ?? '—'}</td>
              <td className="cell-right">{fmtPrice(t.entryPrice, market)}</td>
              <td className="cell-right" title={t.stopBasis || ''}>{fmtPrice(t.currentStop, market)}</td>
              <td className="cell-right">{fmtPrice(t.lastPrice, market)}</td>
              <td className="cell-right">{t.status === 'closed' ? fmtPrice(t.exitPrice, market) : '—'}</td>
              <td className="cell-right"><PnLCell v={t.pnLPct} /></td>
              <td className="cell-right"><MoneyCell v={t.pnLAmount} market={market} /></td>
              <td className="cell-right" style={{ fontSize: '0.8rem' }}>
                <span style={{ color: 'var(--success)' }}>{fmtNum(t.mfePct)}</span>
                {' / '}
                <span style={{ color: 'var(--danger)' }}>{fmtNum(t.maePct)}</span>
              </td>
              <td className="cell-center" title={t.flaggedScanners.join(', ')}>
                <span className="badge badge-count">{t.scannerHitCount}</span>
              </td>
              <td>
                {t.status === 'closed'
                  ? <span className="cell-muted" style={{ fontSize: '0.8rem' }}>closed · {t.exitReason}</span>
                  : <span style={{ color: 'var(--success)', fontSize: '0.8rem' }}>active{t.movedToBe ? ' · BE+' : ''}</span>}
              </td>
            </tr>
            {expanded === t.id && (
              <tr>
                <td colSpan={15} style={{ background: 'var(--bg-subtle, rgba(127,127,127,0.06))' }}>
                  <ConfidenceRationale breakout={t} />
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {lookup && <StockLookupModal market={market} symbol={lookup} onClose={() => setLookup(null)} />}
    </div>
    )}
    </>
  );
}

type PeriodKey = '1d' | '1w' | '1m' | '3m' | '6m' | 'custom';

const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: '1d', label: '1D' },
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: 'custom', label: 'Custom' },
];

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** [from, to] window (to is exclusive end-of-day) for a named period. */
function periodRange(key: PeriodKey, customFrom: string, customTo: string): { from: string; to: string } {
  const end = new Date();
  end.setHours(23, 59, 59, 999);
  if (key === 'custom') {
    return { from: `${customFrom}T00:00:00`, to: `${customTo}T23:59:59` };
  }
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  if (key === '1d') { /* today */ }
  else if (key === '1w') start.setDate(start.getDate() - 6);
  else if (key === '1m') start.setMonth(start.getMonth() - 1);
  else if (key === '3m') start.setMonth(start.getMonth() - 3);
  else if (key === '6m') start.setMonth(start.getMonth() - 6);
  return { from: isoDate(start) + 'T00:00:00', to: isoDate(end) + 'T23:59:59' };
}

function PnlView({ market, profile }: { market: Market; profile: BreakoutProfile }) {
  const [period, setPeriod] = useState<PeriodKey>('1m');
  const [customFrom, setCustomFrom] = useState(isoDate(new Date(Date.now() - 30 * 864e5)));
  const [customTo, setCustomTo] = useState(isoDate(new Date()));
  const [data, setData] = useState<BreakoutPnlSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    const { from, to } = periodRange(period, customFrom, customTo);
    setLoading(true);
    fetchBreakoutPnl(market, from, to, profile)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [market, profile, period, customFrom, customTo]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="toolbar" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        {PERIODS.map(p => (
          <button key={p.key} className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setPeriod(p.key)}>{p.label}</button>
        ))}
        {period === 'custom' && (
          <>
            <input type="date" className="search-input" style={{ width: 'auto' }} value={customFrom}
              max={customTo} onChange={e => setCustomFrom(e.target.value)} />
            <span className="cell-muted">→</span>
            <input type="date" className="search-input" style={{ width: 'auto' }} value={customTo}
              min={customFrom} onChange={e => setCustomTo(e.target.value)} />
          </>
        )}
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading P&amp;L...</div>
      ) : !data ? (
        <div className="empty-state"><p className="empty-state-text">No P&amp;L data.</p></div>
      ) : (
        <>
          <div className="toolbar" style={{ gap: 16, flexWrap: 'wrap' }}>
            <Stat label={`Realized P&L (${profile})`} value={fmtMoney(data.realizedPnLAmount, market)}
              color={data.realizedPnLAmount >= 0 ? 'var(--success)' : 'var(--danger)'} />
            <Stat label="Unrealized P&L (open now)" value={fmtMoney(data.openPnLAmount, market)}
              color={data.openPnLAmount >= 0 ? 'var(--success)' : 'var(--danger)'} />
            <Stat label="Closed in period" value={data.realizedCount} />
            <Stat label="Open breakouts" value={data.openCount} />
            <Stat label="Wins" value={data.wins} color="var(--success)" />
            <Stat label="Losses" value={data.losses} color="var(--danger)" />
            <Stat label="Win rate" value={data.winRatePct != null ? `${data.winRatePct}%` : '—'} />
            <Stat label="Avg realized %" value={fmtPct(data.avgRealizedPnLPct)}
              color={(data.avgRealizedPnLPct ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          </div>
          <p className="cell-muted" style={{ fontSize: '0.78rem', margin: '4px 0 0' }}>
            Realized = {profile} breakouts closed between {fmtDateTime(data.from)} and {fmtDateTime(data.to)}.
            Unrealized = live P&amp;L of all currently-open {profile} breakouts (period-independent).
          </p>
        </>
      )}
    </>
  );
}

function DayView({ market, profile }: { market: Market; profile: BreakoutProfile }) {
  const [day, setDay] = useState(isoDate(new Date()));
  const [data, setData] = useState<BreakoutDay | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    fetchBreakoutsByDay(market, day, profile)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [market, profile, day]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input type="date" className="search-input" style={{ width: 'auto' }} value={day}
          max={isoDate(new Date())} onChange={e => setDay(e.target.value)} />
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading day...</div>
      ) : !data ? (
        <div className="empty-state"><p className="empty-state-text">No data for this day.</p></div>
      ) : (
        <>
          <h3 style={{ margin: '12px 0 6px', fontSize: '0.95rem' }}>
            Entries <span className="badge badge-count">{data.entries.length}</span>
          </h3>
          {data.entries.length === 0
            ? <p className="cell-muted" style={{ fontSize: '0.85rem' }}>No {profile} entries on this day.</p>
            : <BreakoutBlotter rows={data.entries} market={market} closed={false} />}

          <h3 style={{ margin: '18px 0 6px', fontSize: '0.95rem' }}>
            Exits <span className="badge badge-count">{data.exits.length}</span>
          </h3>
          {data.exits.length === 0
            ? <p className="cell-muted" style={{ fontSize: '0.85rem' }}>No {profile} exits on this day.</p>
            : <BreakoutBlotter rows={data.exits} market={market} closed={true} />}
        </>
      )}
    </>
  );
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <span style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontSize: '1.1rem', fontWeight: 700, color: color || 'var(--text)' }}>{value}</span>
    </div>
  );
}
