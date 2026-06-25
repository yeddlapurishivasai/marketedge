import { useState, useEffect, useCallback, Fragment } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, StockScore, Trade, TradeStats, TradeProfile, ScannerPerformance, ScoringWeight } from '../api';
import { fetchScores, fetchTrades, fetchTradeStats, triggerScanner, fetchScannerPerformance, fetchScoringWeights, updateScoringWeight } from '../api';
import { ChevronLeft, ChevronDown, RefreshCw, TrendingUp, Loader2, Gauge, Activity, History, Target, Sliders } from 'lucide-react';

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
  notes?: { epsUpsidePct?: number | null; patternWeight?: number | null };
}
const COMPONENT_LABELS: Record<string, string> = {
  pattern: 'Pattern (triggering scanner)',
  fundamental: 'Fundamentals',
  eps: 'EPS upside',
  ai: 'AI signal (placeholder)',
};

function ConfidenceRationale({ trade }: { trade: Trade }) {
  let data: ConfidenceRationaleData | null = null;
  try {
    data = trade.confidenceRationaleJson ? JSON.parse(trade.confidenceRationaleJson) : null;
  } catch { data = null; }
  if (!data) return <div className="cell-muted" style={{ padding: 8 }}>No rationale recorded for this trade.</div>;

  const comps = data.components ?? [];
  return (
    <div style={{ padding: '10px 12px' }}>
      <div style={{ marginBottom: 8, fontSize: '0.85rem' }}>
        <strong>Why confidence {data.confidence}?</strong>{' '}
        <span className="cell-muted">
          {data.profile} trade triggered by {data.scanner || '—'}. Confidence blends each component below by
          its profile weight: <code>100 × Σ(weight × score) / Σ(weight)</code> over the available components.
        </span>
      </div>
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
                {c.component === 'pattern' && `Adaptive weight for ${data!.scanner || 'scanner'} (rises on wins, falls on losses)`}
                {c.component === 'eps' && data!.notes?.epsUpsidePct != null && `EPS upside ${data!.notes.epsUpsidePct}% → clamped to 0–1 over 25%`}
                {c.component === 'fundamental' && (c.available ? 'Fraction of fundamental checks passing' : 'No fundamentals for this symbol')}
                {c.component === 'ai' && 'Neutral 0.5 until an AI signal feeds in'}
                {!c.available && c.component !== 'ai' && c.component !== 'fundamental' && 'Not available — dropped from the blend'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CheckContrib { label: string; group: string; pass: boolean; weight: number; }
interface ProfileComp { bull: number; bear: number; phat: number; n: number; z: number; contribs: CheckContrib[]; }
interface ScannerTag { name: string; winRate?: number | null; wilson?: number | null; trades?: number | null; }
interface ScoreComponents {
  groups?: Record<string, string>;
  freshness?: number;
  daysSinceEarnings?: number | null;
  scannerHits?: number | null;
  upsideSource?: string | null;
  scanners?: ScannerTag[];
  swing?: ProfileComp;
  positional?: ProfileComp;
}

const GROUP_LABELS: Record<string, string> = {
  tech: 'Technical', fund: 'Fundamental', catalyst: 'Catalyst', est: 'Estimates', track: 'Track record',
};
const UPSIDE_SRC_LABELS: Record<string, string> = {
  forward_eps: 'Analyst forward EPS (next FY vs current FY)',
  earnings_growth_yoy: 'Latest reported earnings growth YoY (forward analyst EPS unavailable)',
};

function ScoreBreakdown({ comp, profile }: { comp: ScoreComponents; profile: TradeProfile }) {
  const p = profile === 'swing' ? comp.swing : comp.positional;
  if (!p) return <div style={{ padding: 12, color: 'var(--text-muted)' }}>No breakdown available.</div>;
  const contribs = p.contribs ?? [];
  const passW = contribs.filter(c => c.pass).reduce((s, c) => s + c.weight, 0);
  const totalW = contribs.reduce((s, c) => s + c.weight, 0);
  return (
    <div style={{ padding: '12px 16px', background: 'var(--bg-subtle, rgba(127,127,127,0.06))', fontSize: '0.85rem' }}>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>How this score was computed</div>
          <div style={{ color: 'var(--text-muted)' }}>
            Wilson lower bound of the weighted pass-fraction. A small evidence base (n)
            widens the confidence interval and pulls the score down — that is why even a
            strong setup with few applicable checks does not reach 100.
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
        <Stat label="Bull score" value={`${p.bull}`} color="var(--success)" />
        <Stat label="Bear score" value={`${p.bear}`} color="var(--danger)" />
        <Stat label="Pass fraction (p̂)" value={`${Math.round((p.phat ?? 0) * 100)}%`} />
        <Stat label="Evidence weight (n)" value={(p.n ?? 0).toFixed(2)} />
        <Stat label="Passed weight" value={`${passW.toFixed(2)} / ${totalW.toFixed(2)}`} />
        <Stat label="z (conservatism)" value={(p.z ?? 0).toFixed(2)} />
        {comp.freshness != null && <Stat label="Fund. freshness" value={comp.freshness.toFixed(2)} />}
      </div>
      <div style={{ color: 'var(--text-muted)', marginBottom: 8 }}>
        <strong>Bull</strong> = confidence that the positive (passing) evidence is real;{' '}
        <strong>Bear</strong> = same math applied to the failing checks (short evidence).
        {comp.upsideSource && (
          <> EPS upside source: {UPSIDE_SRC_LABELS[comp.upsideSource] ?? comp.upsideSource}.</>
        )}
      </div>
      {comp.scanners && comp.scanners.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Patterns that flagged this stock</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {comp.scanners.map((s, i) => {
              const wr = s.winRate != null ? Math.round(s.winRate * 100) : null;
              const proven = (s.wilson ?? 0) >= 0.5 && (s.trades ?? 0) >= 3;
              return (
                <span key={i} className="badge" style={{
                  background: proven ? 'rgba(22,163,74,0.15)' : 'rgba(127,127,127,0.12)',
                  color: proven ? 'var(--success)' : 'var(--text-muted)',
                  fontSize: '0.72rem', padding: '2px 8px', fontWeight: 600,
                }} title={s.trades ? `${s.trades} paper trades` : 'no trade history yet'}>
                  {s.name}{wr != null ? ` · ${wr}% win` : ' · untested'}
                </span>
              );
            })}
          </div>
        </div>
      )}
      <table className="table" style={{ fontSize: '0.82rem' }}>
        <thead>
          <tr>
            <th>Parameter</th>
            <th style={{ textAlign: 'center' }}>Group</th>
            <th style={{ textAlign: 'center' }}>Result</th>
            <th style={{ textAlign: 'right' }}>Weight</th>
          </tr>
        </thead>
        <tbody>
          {contribs.map((c, i) => (
            <tr key={i}>
              <td>{c.label}</td>
              <td style={{ textAlign: 'center', color: 'var(--text-muted)' }}>{GROUP_LABELS[c.group] ?? c.group}</td>
              <td style={{ textAlign: 'center', color: c.pass ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                {c.pass ? '✓ pass' : '✗ fail'}
              </td>
              <td style={{ textAlign: 'right' }}>{c.weight.toFixed(2)}</td>
            </tr>
          ))}
          {contribs.length === 0 && (
            <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>No applicable checks for this profile.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function parseComponents(json?: string | null): ScoreComponents | null {
  if (!json) return null;
  try { return JSON.parse(json) as ScoreComponents; } catch { return null; }
}

export default function TradesPage() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;
  const navigate = useNavigate();
  const [tab, setTab] = useState<'scores' | 'trades' | 'patterns' | 'weights'>('scores');

  return (
    <div className="page">
      <a className="back-link" onClick={() => navigate(`/${m}`)} style={{ cursor: 'pointer' }}>
        <ChevronLeft size={16} /> Back
      </a>
      <div className="page-header">
        <div>
          <h1 className="page-title">Scores &amp; Trades</h1>
          <p className="page-subtitle">
            Wilson lower-bound scoring &amp; the breakout paper-trade blotter &middot; {m === 'india' ? 'Indian' : 'US'} Market
          </p>
        </div>
      </div>

      <div className="toolbar" style={{ gap: 8 }}>
        <button className={`btn ${tab === 'scores' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('scores')}>
          <Gauge size={16} /> Scores
        </button>
        <button className={`btn ${tab === 'trades' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('trades')}>
          <Activity size={16} /> Paper Trades
        </button>
        <button className={`btn ${tab === 'patterns' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('patterns')}>
          <Target size={16} /> Pattern Performance
        </button>
        <button className={`btn ${tab === 'weights' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('weights')}>
          <Sliders size={16} /> Confidence Weights
        </button>
      </div>

      {tab === 'scores' ? <ScoresTab market={m} />
        : tab === 'trades' ? <TradesTab market={m} />
        : tab === 'patterns' ? <PatternsTab market={m} />
        : <WeightsTab market={m} />}
    </div>
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
          Each scanner is a pattern. Reliability is the Wilson lower bound of its paper-trade win rate —
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
          <p className="empty-state-text">No paper trades yet, so no pattern performance to show. Backfill trades from the Paper Trades tab.</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Pattern (scanner)</th>
                <th style={{ textAlign: 'center' }}>Reliability</th>
                <th style={{ textAlign: 'center' }}>Win rate</th>
                <th style={{ textAlign: 'center' }}>Trades</th>
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

  const patterns = rows.filter(r => r.category === 'pattern');
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
        <td style={{ fontWeight: 600 }}>{r.category === 'mix' ? mixLabel(r.componentKey) : r.componentKey}</td>
        <td className="cell-right">
          <input className="search-input" style={{ width: 80, textAlign: 'right' }} type="number" step="0.01" min="0" max="1"
            value={val}
            onChange={e => setDrafts(d => ({ ...d, [r.id]: e.target.value }))}
            onBlur={commit}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
        </td>
        <td className="cell-right cell-muted">{r.seedWeight.toFixed(2)}</td>
        {r.category === 'pattern' && <td className="cell-center">{r.wins} / {r.losses}</td>}
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
          Editable confidence weights. <strong>Pattern</strong> weights self-adjust as each scanner's paper trades
          win or lose; editing one pins it (auto-adaptation stops). <strong>Mix</strong> weights set how much each
          component drives a swing/positional trade's confidence.
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
          <h3 style={{ margin: '16px 0 8px' }}>Pattern weights (per scanner, self-adjusting)</h3>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Scanner</th>
                  <th style={{ textAlign: 'right' }}>Weight (0–1)</th>
                  <th style={{ textAlign: 'right' }}>Seed</th>
                  <th style={{ textAlign: 'center' }}>W / L</th>
                  <th style={{ textAlign: 'center' }}>Override</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>{patterns.map(renderRow)}</tbody>
            </table>
          </div>
          <h3 style={{ margin: '24px 0 8px' }}>Mix weights (per profile blend)</h3>
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

function ScoresTab({ market }: { market: Market }) {
  const [profile, setProfile] = useState<TradeProfile>('swing');
  const [side, setSide] = useState<string>('');
  const [rows, setRows] = useState<StockScore[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchScores(market, { profile, side: side || undefined, take: 200 })
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market, profile, side]);

  useEffect(() => { load(); }, [load]);

  const scoreOf = (r: StockScore) => profile === 'swing' ? r.swingScore : r.positionalScore;
  const sideOf = (r: StockScore) => profile === 'swing' ? r.swingSide : r.positionalSide;
  const bullOf = (r: StockScore) => profile === 'swing' ? r.swingBull : r.positionalBull;
  const bearOf = (r: StockScore) => profile === 'swing' ? r.swingBear : r.positionalBear;

  return (
    <>
      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap' }}>
        <button className={`btn btn-sm ${profile === 'swing' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setProfile('swing')}>
          Swing (technical)
        </button>
        <button className={`btn btn-sm ${profile === 'positional' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setProfile('positional')}>
          Positional (fundamentals 50%)
        </button>
        <span style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 4px' }} />
        <select className="search-input" style={{ width: 'auto' }} value={side} onChange={e => setSide(e.target.value)}>
          <option value="">All sides</option>
          <option value="long">Long</option>
          <option value="short">Short (F&amp;O)</option>
          <option value="none">No signal</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading scores...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><TrendingUp size={48} /></div>
          <p className="empty-state-text">No scores yet. They are computed on each scanner run.</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th style={{ textAlign: 'center' }}>Score</th>
                <th style={{ textAlign: 'center' }}>Side</th>
                <th style={{ textAlign: 'center' }}>Bull / Bear</th>
                <th style={{ textAlign: 'right' }}>EPS upside</th>
                <th style={{ textAlign: 'center' }}>Scanner hits</th>
                <th style={{ textAlign: 'center' }}>Days since earn.</th>
                <th style={{ textAlign: 'center' }}>Freshness</th>
                <th style={{ textAlign: 'center' }}>F&amp;O</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const isOpen = expanded === r.ticker;
                const comp = isOpen ? parseComponents(r.componentsJson) : null;
                return (
                  <Fragment key={r.ticker}>
                    <tr>
                      <td style={{ fontWeight: 600 }}>{r.ticker}</td>
                      <td className="cell-center">
                        <button
                          className="btn btn-ghost btn-sm"
                          style={{ padding: '2px 8px' }}
                          title="Show how this score was computed"
                          onClick={() => setExpanded(isOpen ? null : r.ticker)}
                        >
                          <ScoreBadge score={scoreOf(r)} />
                          <ChevronDown size={12} style={{ marginLeft: 4, transform: isOpen ? 'rotate(180deg)' : undefined, transition: 'transform 0.15s' }} />
                        </button>
                      </td>
                      <td className="cell-center"><SideBadge side={sideOf(r)} /></td>
                      <td className="cell-center" style={{ fontSize: '0.85rem' }}>
                        <span style={{ color: 'var(--success)' }}>{bullOf(r) ?? '—'}</span>
                        {' / '}
                        <span style={{ color: 'var(--danger)' }}>{bearOf(r) ?? '—'}</span>
                      </td>
                      <td className="cell-right">{fmtPct(r.upsideEpsPct)}</td>
                      <td className="cell-center">{r.scannerHits ?? '—'}</td>
                      <td className="cell-center">{r.daysSinceEarnings ?? '—'}</td>
                      <td className="cell-center">{r.fundFreshnessDecay != null ? r.fundFreshnessDecay.toFixed(2) : '—'}</td>
                      <td className="cell-center">{r.isFno ? '✓' : ''}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={9} style={{ padding: 0 }}>
                          {comp ? <ScoreBreakdown comp={comp} profile={profile} />
                            : <div style={{ padding: 12, color: 'var(--text-muted)' }}>No breakdown stored for this score.</div>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function TradesTab({ market }: { market: Market }) {
  const [status, setStatus] = useState<string>('active');
  const [tradeType, setTradeType] = useState<string>('');
  const [rows, setRows] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [note, setNote] = useState<string>('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchTrades(market, { status: status || undefined, tradeType: tradeType || undefined }),
      fetchTradeStats(market),
    ])
      .then(([t, s]) => { setRows(t); setStats(s); })
      .catch(() => { setRows([]); setStats(null); })
      .finally(() => setLoading(false));
  }, [market, status, tradeType]);

  useEffect(() => { load(); }, [load]);

  const backfill = useCallback(() => {
    setBackfilling(true);
    setNote('');
    triggerScanner(market, { universe: 'stage2', backfill: true })
      .then(() => {
        setNote('Backfill started — replaying the last 7 days of breakouts. Refresh in a minute.');
      })
      .catch(() => setNote('Failed to start backfill.'))
      .finally(() => setBackfilling(false));
  }, [market]);

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
        <select className="search-input" style={{ width: 'auto' }} value={tradeType} onChange={e => setTradeType(e.target.value)}>
          <option value="">All types</option>
          <option value="swing">Swing</option>
          <option value="positional">Positional</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={backfill} disabled={backfilling} style={{ marginLeft: 'auto' }}
          title="Replay the last 7 days of volume-confirmed breakouts into the blotter">
          {backfilling ? <Loader2 size={14} className="spin" /> : <History size={14} />} Backfill 7 days
        </button>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {note && <div className="hint" style={{ marginBottom: 8, color: 'var(--text-muted)' }}>{note}</div>}

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading trades...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Activity size={48} /></div>
          <p className="empty-state-text">
            No trades. A scanner hit is only a setup — a paper trade opens on a volume-confirmed
            break of support/resistance. Use “Backfill 7 days” to seed from recent breakouts.
          </p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Type</th>
                <th>Dir</th>
                <th>Entry time</th>
                <th style={{ textAlign: 'right' }}>Qty</th>
                <th style={{ textAlign: 'right' }}>Entry</th>
                <th style={{ textAlign: 'right' }}>Trail</th>
                <th style={{ textAlign: 'right' }}>{closed ? 'Last' : 'Current'}</th>
                <th style={{ textAlign: 'right' }}>Exit</th>
                <th style={{ textAlign: 'right' }}>P&amp;L %</th>
                <th style={{ textAlign: 'right' }}>P&amp;L</th>
                <th style={{ textAlign: 'right' }}>MFE / MAE</th>
                <th style={{ textAlign: 'center' }}>Confidence</th>
                <th style={{ textAlign: 'center' }}>Scanners</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(t => (
                <Fragment key={t.id}>
                <tr>
                  <td style={{ fontWeight: 600 }}>{t.ticker}</td>
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
                  <td className="cell-center">
                    {t.confidenceScore != null ? (
                      <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', gap: 4 }}
                        title="Explain why this trade got its confidence score"
                        onClick={() => setExpanded(expanded === t.id ? null : t.id)}>
                        <ScoreBadge score={Math.round(t.confidenceScore)} />
                        <ChevronDown size={12} style={{ transform: expanded === t.id ? 'rotate(180deg)' : 'none' }} />
                      </button>
                    ) : <span className="cell-muted">—</span>}
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
                    <td colSpan={14} style={{ background: 'var(--bg-subtle, rgba(127,127,127,0.06))' }}>
                      <ConfidenceRationale trade={t} />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
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
