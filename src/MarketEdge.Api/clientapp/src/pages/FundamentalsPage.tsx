import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, FundamentalIdeaRow } from '../api';
import { fetchFundamentalIdeas } from '../api';
import { StockLookupModal } from './StockLookupPage';
import { ChevronLeft, RefreshCw, LineChart, ArrowUpRight, ArrowDownRight, Info, X } from 'lucide-react';

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtPp(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)} pp`;
}

function fmtMoney(v?: number | null): string {
  if (v == null) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(d?: string | null): string {
  return d ? new Date(d).toLocaleDateString() : '—';
}

const signColor = (v?: number | null) =>
  v == null ? 'var(--text-muted)' : v >= 0 ? 'var(--success)' : 'var(--danger)';

// Confidence (0..100) -> colour ramp: red (low) -> amber -> green (high).
function confColor(v?: number | null): string {
  if (v == null) return 'var(--text-muted)';
  if (v >= 66) return 'var(--success)';
  if (v >= 40) return '#d99106';
  return 'var(--danger)';
}

// Compact confidence bar with the numeric score. Rendered as a fixed-width unit so the
// bar + number line up vertically across rows and sit directly under the metric value.
function ConfBar({ v, strong = false }: { v?: number | null; strong?: boolean }) {
  if (v == null) return <span className="cell-muted" style={{ fontVariantNumeric: 'tabular-nums' }}>—</span>;
  const color = confColor(v);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end', width: 66 }}>
      <span style={{ position: 'relative', flex: '1 1 auto', height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
        <span style={{ position: 'absolute', inset: 0, width: `${Math.max(0, Math.min(100, v))}%`, background: color, borderRadius: 3 }} />
      </span>
      <span style={{ flex: '0 0 auto', fontWeight: strong ? 700 : 500, color, fontVariantNumeric: 'tabular-nums', width: 20, textAlign: 'right' }}>
        {v.toFixed(0)}
      </span>
    </span>
  );
}

// Map yfinance action codes / verbs to a friendly verb.
function actionLabel(action?: string | null): string {
  if (!action) return '';
  const a = action.toLowerCase();
  if (a === 'up' || a === 'upgrade') return 'Upgrade';
  if (a === 'down' || a === 'downgrade') return 'Downgrade';
  if (a === 'init') return 'Initiated';
  if (a === 'reit') return 'Reiterated';
  if (a === 'main') return 'Maintains';
  return action;
}

// A bare metric value cell (no confidence) for the dedicated value columns.
function ValueCell({ value, kind }: { value?: number | null; kind: 'pct' | 'pp' }) {
  const fmt = kind === 'pp' ? fmtPp : fmtPct;
  return (
    <span style={{ color: signColor(value), fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{fmt(value)}</span>
  );
}

// Compact confidence value for a dedicated confidence column: small bar + colored number.
function ConfNum({ v }: { v?: number | null }) {
  if (v == null) return <span className="cell-muted" style={{ fontVariantNumeric: 'tabular-nums' }}>—</span>;
  const color = confColor(v);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end', width: '100%' }}>
      <span style={{ position: 'relative', flex: '1 1 auto', maxWidth: 34, height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
        <span style={{ position: 'absolute', inset: 0, width: `${Math.max(0, Math.min(100, v))}%`, background: color, borderRadius: 3 }} />
      </span>
      <span style={{ fontWeight: 600, color, fontVariantNumeric: 'tabular-nums', width: 20, textAlign: 'right' }}>{v.toFixed(0)}</span>
    </span>
  );
}

function RatingCell({ r }: { r: FundamentalIdeaRow }) {
  if (!r.latestRatingGrade && !r.latestRatingAction) return <span className="cell-muted">—</span>;
  const label = actionLabel(r.latestRatingAction);
  const isUp = label === 'Upgrade';
  const isDown = label === 'Downgrade';
  const color = isUp ? 'var(--success)' : isDown ? 'var(--danger)' : 'var(--text)';
  const Icon = isUp ? ArrowUpRight : isDown ? ArrowDownRight : null;
  return (
    <span title={r.latestRatingFirm ?? undefined} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {Icon && <Icon size={14} style={{ color }} />}
      <span style={{ fontWeight: 600 }}>{r.latestRatingGrade ?? '—'}</span>
      {label && <span style={{ color, fontSize: '0.78rem' }}>· {label}</span>}
      {r.latestRatingDate && <span className="cell-muted" style={{ fontSize: '0.74rem' }}>· {fmtDate(r.latestRatingDate)}</span>}
    </span>
  );
}

// Long/short/neutral bucketing + direction-aware confidence now come from the API
// (FundamentalsService / IdeaDirection): r.side is the server-computed bucket and the
// confidence columns are already oriented to that side (mirrored for shorts).
type IdeaSide = 'long' | 'short' | 'neutral';

// --- Confidence breakdown modal ----------------------------------------------
interface RationaleMetric { metric: string; phat: number; n: number; days: number | null; z: number; confidence: number; }
interface Rationale {
  n: number;
  weights: Record<string, number>;
  metrics: RationaleMetric[];
  targetUpsidePct: number | null;
  fundamental: number | null;
  technical: number | null;
  technicalDetail: { source?: string; wins?: number; total?: number; scanner?: string | null; ownTotal?: number } | null;
  overall: number | null;
  blend: { fundamental: number; technical: number };
}

const METRIC_LABEL: Record<string, string> = {
  epsBeat: 'EPS beat',
  opmExpansion: 'OPM expansion',
  opExpansion: 'Operating-profit expansion',
  rating: 'Analyst rating',
  targetUpside: 'Target upside',
};

function ConfidenceBreakdown({ row, side, onClose }: { row: FundamentalIdeaRow; side: 'long' | 'short'; onClose: () => void }) {
  const rationaleJson = side === 'short' ? row.confidenceRationaleShortJson : row.confidenceRationaleJson;
  let data: Rationale | null = null;
  try { data = rationaleJson ? JSON.parse(rationaleJson) as Rationale : null; } catch { data = null; }

  const fundParts = data?.metrics.map(mm => {
    const w = data!.weights[mm.metric] ?? 0;
    return { ...mm, weight: w, contribution: w * mm.confidence };
  }) ?? [];
  const wsum = fundParts.reduce((a, p) => a + p.weight, 0);

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ maxWidth: 760, width: '100%', maxHeight: '88vh', overflowY: 'auto', padding: 22 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <Info size={18} />
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>How we scored {row.symbol} ({side})</h2>
          <button onClick={onClose} className="btn btn-sm btn-outline" style={{ marginLeft: 'auto' }}><X size={14} /></button>
        </div>
        <p className="cell-muted" style={{ fontSize: '0.82rem', marginTop: 0 }}>
          Each metric's raw value is normalised to a 0–1 strength (p̂), then passed through a
          Wilson lower bound over n={data?.n ?? '—'} available metrics. The z widens with the
          signal's age (z = 1.28·(1 + days/30)), so confidence decays as the result/rating gets older.
        </p>

        {!data ? (
          <p className="cell-muted">No rationale stored for this idea yet — re-run a fundamentals refresh to populate it.</p>
        ) : (
          <>
            <table className="table" style={{ width: '100%', fontSize: '0.84rem', marginBottom: 14 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left' }}>Metric</th>
                  <th style={{ textAlign: 'right' }}>p̂</th>
                  <th style={{ textAlign: 'right' }}>age (d)</th>
                  <th style={{ textAlign: 'right' }}>z</th>
                  <th style={{ textAlign: 'right' }}>Confidence</th>
                  <th style={{ textAlign: 'right' }}>Weight</th>
                  <th style={{ textAlign: 'right' }}>Contribution</th>
                </tr>
              </thead>
              <tbody>
                {fundParts.map(p => (
                  <tr key={p.metric}>
                    <td>{METRIC_LABEL[p.metric] ?? p.metric}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.phat.toFixed(3)}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.days ?? '—'}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.z.toFixed(3)}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 600, color: confColor(p.confidence) }}>{p.confidence.toFixed(2)}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{(p.weight * 100).toFixed(0)}%</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{(p.contribution / (wsum || 1)).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                <span><strong>Fundamental confidence</strong> = weighted blend of the rows above</span>
                <span style={{ fontWeight: 700, color: confColor(data.fundamental) }}>{data.fundamental?.toFixed(2) ?? '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>
                  <strong>Technical confidence</strong>{' '}
                  {data.technical == null
                    ? <span className="cell-muted">— no closed trades{data.technicalDetail?.scanner ? ` (scanner ${data.technicalDetail.scanner} has no win/loss record yet)` : ''}</span>
                    : <span className="cell-muted">from {data.technicalDetail?.source === 'own' ? 'this stock\u2019s' : 'scanner'} {data.technicalDetail?.wins}/{data.technicalDetail?.total} wins</span>}
                </span>
                <span style={{ fontWeight: 700, color: confColor(data.technical) }}>{data.technical?.toFixed(2) ?? '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                <span>
                  <strong>Overall</strong>{' '}
                  <span className="cell-muted">
                    {data.technical == null
                      ? '= fundamental only (technical missing)'
                      : `= ${(data.blend.fundamental * 100).toFixed(0)}% fundamental + ${(data.blend.technical * 100).toFixed(0)}% technical`}
                  </span>
                </span>
                <span style={{ fontWeight: 800, fontSize: '1.05rem', color: confColor(data.overall) }}>{data.overall?.toFixed(2) ?? '—'}</span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

type SortKey =
  | 'symbol' | 'companyName' | 'earningsDate' | 'epsBeatPct' | 'opmExpansionYoyPct'
  | 'operatingProfitExpansionYoyPct' | 'latestRatingDate' | 'targetLowPrice'
  | 'targetMeanPrice' | 'targetHighPrice'
  | 'overallConfidence' | 'fundamentalConfidence' | 'technicalConfidence'
  | 'epsBeatConfidence' | 'opmExpansionConfidence' | 'operatingProfitExpansionConfidence'
  | 'analystRatingConfidence' | 'targetUpsideConfidence';

export default function FundamentalsPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [rows, setRows] = useState<FundamentalIdeaRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<string | null>(null);
  const [explain, setExplain] = useState<FundamentalIdeaRow | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('overallConfidence');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [quality, setQuality] = useState<IdeaSide>('long');
  const [stageOnly, setStageOnly] = useState(false);
  const [windowDays, setWindowDays] = useState(0); // 0 = any
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchFundamentalIdeas(m));
    } catch {
      setRows([]);
    }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  // Reset to the first page whenever the market, sort, filters or dataset change.
  useEffect(() => { setPage(1); }, [m, sortKey, sortDir, quality, stageOnly, windowDays, rows.length]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'symbol' || key === 'companyName' ? 'asc' : 'desc');
    }
  };

  const filtered = rows.filter(r => {
    // Side (long / short / neutral) comes from the API (server-side dead-band on directionScore).
    if (r.side !== quality) return false;
    // Stage-2 sub-filter — not applicable to shorts (a short isn't a Stage-2 setup).
    if (stageOnly && quality !== 'short' && r.isStage2 !== true) return false;
    // "Results in last N days" window on the earnings date.
    if (windowDays > 0) {
      const ed = r.earningsDate ? new Date(r.earningsDate).getTime() : null;
      if (ed == null || ed < Date.now() - windowDays * 86400000) return false;
    }
    return true;
  });

  // Which side's confidence to surface: in the Short view every confidence column
  // shows the bearish (short) score; otherwise the bullish (long) score.
  const confSide: 'long' | 'short' = quality === 'short' ? 'short' : 'long';
  const SHORT_FIELD: Partial<Record<SortKey, keyof FundamentalIdeaRow>> = {
    overallConfidence: 'overallConfidenceShort',
    fundamentalConfidence: 'fundamentalConfidenceShort',
    epsBeatConfidence: 'epsBeatConfidenceShort',
    opmExpansionConfidence: 'opmExpansionConfidenceShort',
    operatingProfitExpansionConfidence: 'operatingProfitExpansionConfidenceShort',
    analystRatingConfidence: 'analystRatingConfidenceShort',
    // targetUpsideConfidence has no bearish twin (target upside needs a live price)
  };
  const CONF_KEYS = new Set<SortKey>([
    'overallConfidence', 'fundamentalConfidence', 'technicalConfidence',
    'epsBeatConfidence', 'opmExpansionConfidence', 'operatingProfitExpansionConfidence',
    'analystRatingConfidence', 'targetUpsideConfidence',
  ]);
  const confValue = (r: FundamentalIdeaRow, key: SortKey): number | null => {
    if (confSide === 'short') {
      if (key === 'targetUpsideConfidence') return null;
      const sf = SHORT_FIELD[key];
      if (sf) return (r[sf] as number | null) ?? null;
    }
    return (r[key] as number | null) ?? null;
  };

  const sorted = [...filtered].sort((a, b) => {
    const isConf = CONF_KEYS.has(sortKey);
    const av = (isConf ? confValue(a, sortKey) : a[sortKey]) as string | number | null | undefined;
    const bv = (isConf ? confValue(b, sortKey) : b[sortKey]) as string | number | null | undefined;
    const aNull = av == null;
    const bNull = bv == null;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    let cmp: number;
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pageCount);
  const paged = sorted.slice((current - 1) * pageSize, current * pageSize);
  const firstRow = sorted.length === 0 ? 0 : (current - 1) * pageSize + 1;
  const lastRow = Math.min(current * pageSize, sorted.length);

  const arrow = (key: SortKey) => (sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');
  const th = (key: SortKey, label: string, right = false) => (
    <th
      onClick={() => toggleSort(key)}
      style={{ cursor: 'pointer', userSelect: 'none', textAlign: right ? 'right' : 'left', whiteSpace: 'nowrap' }}
    >
      {label}{arrow(key)}
    </th>
  );

  // Header for a confidence column: compact, right-aligned, sorts by that confidence key.
  const confTh = (confKey: SortKey, label: string) => (
    <th
      onClick={() => toggleSort(confKey)}
      title={`${label} confidence`}
      style={{ cursor: 'pointer', userSelect: 'none', textAlign: 'right', whiteSpace: 'nowrap', fontSize: '0.72rem' }}
    >
      conf{arrow(confKey)}
    </th>
  );

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <LineChart size={24} style={{ marginRight: 8 }} />
          Fundamental Ideas
        </h1>
        <span className="page-subtitle">{m === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-outline btn-sm" onClick={() => load()}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div className="card" style={{ padding: 18, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
          <h2 className="section-title" style={{ margin: 0 }}>
            {quality === 'short' ? 'Short ideas' : quality === 'long' ? 'Long ideas' : 'Neutral'}
          </h2>
          <span className="badge">{filtered.length}</span>
          <span className="cell-muted" style={{ fontSize: '0.78rem' }}>
            {quality === 'short'
              ? 'Bearish fundamentals (EPS miss / margin contraction / sell rating) — short candidates.'
              : quality === 'long'
              ? 'Bullish fundamentals (beat / expansion / buy rating) — long candidates.'
              : 'Mixed/flat fundamentals — neither a clear long nor short.'}
          </span>
          <div style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            {/* Results window dropdown */}
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: '0.8rem' }}>
              <span className="cell-muted">Results:</span>
              <select
                value={windowDays}
                onChange={e => setWindowDays(Number(e.target.value))}
                className="btn btn-outline btn-sm"
                style={{ padding: '4px 8px' }}
              >
                <option value={0}>Any time</option>
                <option value={10}>Last 10 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </label>

            {/* Stage-2 sub-filter — hidden for shorts (not applicable). */}
            {quality !== 'short' && (
              <div style={{ display: 'inline-flex', gap: 0, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                {([[false, 'All'], [true, 'Stage 2']] as Array<[boolean, string]>).map(([key, label]) => (
                  <button
                    key={String(key)}
                    onClick={() => setStageOnly(key)}
                    className="btn btn-sm"
                    style={{
                      border: 'none', borderRadius: 0,
                      background: stageOnly === key ? 'var(--accent)' : 'transparent',
                      color: stageOnly === key ? '#fff' : 'var(--text)',
                      fontWeight: stageOnly === key ? 600 : 500,
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}

            {/* Long / Short / Neutral */}
            <div style={{ display: 'inline-flex', gap: 0, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              {([
                ['long', 'Long'],
                ['short', 'Short'],
                ['neutral', 'Neutral'],
              ] as Array<[IdeaSide, string]>).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setQuality(key)}
                  className="btn btn-sm"
                  style={{
                    border: 'none',
                    borderRadius: 0,
                    background: quality === key ? 'var(--accent)' : 'transparent',
                    color: quality === key ? '#fff' : 'var(--text)',
                    fontWeight: quality === key ? 600 : 500,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="loading"><div className="spinner" />Loading...</div>
        ) : rows.length === 0 ? (
          <div className="empty-state" style={{ padding: 28 }}>
            <div className="empty-state-icon">📊</div>
            <p className="empty-state-text">
              No active ideas yet. Ideas are captured when a stock reports earnings — run a fundamentals refresh for stocks whose results are due.
            </p>
          </div>
        ) : (
          <>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 8 }}>
              Showing <strong style={{ color: confSide === 'short' ? 'var(--danger, #d9534f)' : 'var(--success, #2e7d32)' }}>{confSide}</strong> confidence scores
              {confSide === 'short' && ' — higher = stronger conviction the stock falls'}
            </div>
            <table className="table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <colgroup>
                <col style={{ width: '5.5%' }} />{/* Symbol */}
                <col style={{ width: '12%' }} />{/* Company */}
                <col style={{ width: '7.5%' }} />{/* Earnings */}
                <col style={{ width: '6.5%' }} />{/* Overall */}
                <col style={{ width: '5.5%' }} />{/* Fund */}
                <col style={{ width: '5%' }} />{/* Tech */}
                <col style={{ width: '6%' }} />{/* EPS val */}
                <col style={{ width: '5%' }} />{/* EPS conf */}
                <col style={{ width: '6%' }} />{/* OPM val */}
                <col style={{ width: '5%' }} />{/* OPM conf */}
                <col style={{ width: '6.5%' }} />{/* Op val */}
                <col style={{ width: '5%' }} />{/* Op conf */}
                <col style={{ width: '8%' }} />{/* Rating val */}
                <col style={{ width: '5%' }} />{/* Rating conf */}
                <col style={{ width: '6.5%' }} />{/* Target val */}
                <col style={{ width: '5%' }} />{/* Target conf */}
              </colgroup>
              <thead>
                <tr>
                  {th('symbol', 'Symbol')}
                  {th('companyName', 'Company')}
                  {th('earningsDate', 'Earnings', true)}
                  {th('overallConfidence', 'Overall', true)}
                  {th('fundamentalConfidence', 'Fund', true)}
                  {th('technicalConfidence', 'Tech', true)}
                  {th('epsBeatPct', 'EPS beat', true)}
                  {confTh('epsBeatConfidence', 'EPS beat')}
                  {th('opmExpansionYoyPct', 'OPM exp', true)}
                  {confTh('opmExpansionConfidence', 'OPM exp')}
                  {th('operatingProfitExpansionYoyPct', 'Op-profit', true)}
                  {confTh('operatingProfitExpansionConfidence', 'Op-profit')}
                  {th('latestRatingDate', 'Rating', true)}
                  {confTh('analystRatingConfidence', 'Rating')}
                  {th('targetMeanPrice', 'Target', true)}
                  {confTh('targetUpsideConfidence', 'Target')}
                </tr>
              </thead>
              <tbody>
                {paged.length === 0 ? (
                  <tr><td colSpan={16} style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)' }}>No ideas match this filter.</td></tr>
                ) : paged.map(r => (
                  <tr key={r.symbol}>
                    <td style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      <button className="stock-link" onClick={() => setPicked(r.symbol)}>{r.symbol}</button>
                    </td>
                    <td style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.companyName ?? undefined}>
                      {r.companyName ?? '—'}
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtDate(r.earningsDate)}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        onClick={() => setExplain(r)}
                        title="Explain this score"
                        style={{ display: 'inline-flex', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'inherit' }}
                      >
                        <ConfBar v={confValue(r, 'overallConfidence')} strong />
                      </button>
                    </td>
                    <td style={{ textAlign: 'right' }}><ConfBar v={confValue(r, 'fundamentalConfidence')} /></td>
                    <td style={{ textAlign: 'right' }}><ConfBar v={r.technicalConfidence} /></td>
                    <td style={{ textAlign: 'right' }}><ValueCell value={r.epsBeatPct} kind="pct" /></td>
                    <td style={{ textAlign: 'right' }}><ConfNum v={confValue(r, 'epsBeatConfidence')} /></td>
                    <td style={{ textAlign: 'right' }}><ValueCell value={r.opmExpansionYoyPct} kind="pp" /></td>
                    <td style={{ textAlign: 'right' }}><ConfNum v={confValue(r, 'opmExpansionConfidence')} /></td>
                    <td style={{ textAlign: 'right' }}><ValueCell value={r.operatingProfitExpansionYoyPct} kind="pct" /></td>
                    <td style={{ textAlign: 'right' }}><ConfNum v={confValue(r, 'operatingProfitExpansionConfidence')} /></td>
                    <td style={{ overflow: 'hidden' }}><RatingCell r={r} /></td>
                    <td style={{ textAlign: 'right' }}><ConfNum v={confValue(r, 'analystRatingConfidence')} /></td>
                    <td style={{ textAlign: 'right' }}>
                      <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }} title={`Low ${fmtMoney(r.targetLowPrice)} · High ${fmtMoney(r.targetHighPrice)}`}>
                        {fmtMoney(r.targetMeanPrice)}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}><ConfNum v={confValue(r, 'targetUpsideConfidence')} /></td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="pagination">
              <button className="pagination-btn" onClick={() => setPage(1)} disabled={current <= 1}>« First</button>
              <button className="pagination-btn" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={current <= 1}>‹ Prev</button>
              <span className="pagination-info">
                {firstRow}–{lastRow} of {sorted.length} · Page {current}/{pageCount}
              </span>
              <button className="pagination-btn" onClick={() => setPage(p => Math.min(pageCount, p + 1))} disabled={current >= pageCount}>Next ›</button>
              <button className="pagination-btn" onClick={() => setPage(pageCount)} disabled={current >= pageCount}>Last »</button>
            </div>
          </>
        )}
      </div>

      {picked && (
        <StockLookupModal market={m} symbol={picked} onClose={() => setPicked(null)} />
      )}
      {explain && (
        <ConfidenceBreakdown row={explain} side={confSide} onClose={() => setExplain(null)} />
      )}
    </div>
  );
}
