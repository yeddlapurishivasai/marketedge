import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, FundamentalRow, FundamentalScanner, FundamentalSignals } from '../api';
import { fetchFundamentals, fetchFundamentalDetail, saveFundamentalNote } from '../api';
import {
  ChevronLeft, RefreshCw, LineChart, X, Loader2, TrendingUp, TrendingDown, Minus, BadgeCheck, Radar
} from 'lucide-react';

const FILTERS: { name: FundamentalScanner; label: string }[] = [
  { name: 'all', label: 'All stocks' },
  { name: 'recently_announced', label: 'Recently announced (7d)' },
  { name: 'earnings_increasing', label: 'Earnings increasing' },
  { name: 'margin_expanding', label: 'OPM expanding' },
  { name: 'operating_profit_expanding', label: 'Operating profit expanding' },
];

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtNum(v?: number | null, dp = 2): string {
  if (v == null) return '—';
  return v.toFixed(dp);
}

function fmtDate(d?: string | null): string {
  return d ? new Date(d).toLocaleDateString() : '—';
}

function curSym(market: Market): string {
  return market === 'us' ? '$' : '₹';
}

function fmtMoney(v: number | null | undefined, market: Market): string {
  if (v == null) return '—';
  const sym = curSym(market);
  const abs = Math.abs(v);
  if (market === 'us') {
    if (abs >= 1e9) return `${sym}${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sym}${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${sym}${(v / 1e3).toFixed(2)}K`;
    return `${sym}${v.toFixed(0)}`;
  }
  if (abs >= 1e7) return `${sym}${(v / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sym}${(v / 1e5).toFixed(2)} L`;
  return `${sym}${v.toFixed(0)}`;
}

function TrendBadge({ trend }: { trend?: string | null }) {
  if (!trend) return <span className="cell-muted">—</span>;
  const color = trend === 'expanding' ? 'var(--success)' : trend === 'decreasing' ? 'var(--danger)' : 'var(--text-muted)';
  const Icon = trend === 'expanding' ? TrendingUp : trend === 'decreasing' ? TrendingDown : Minus;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color, fontSize: '0.8rem' }}>
      <Icon size={13} /> {trend}
    </span>
  );
}

function AutoSignalsBlock({ signals, market }: { signals: FundamentalSignals | null; market: Market }) {
  if (!signals) return null;
  const capexColor = signals.capexTrend === 'rising' ? 'var(--success)'
    : signals.capexTrend === 'falling' ? 'var(--danger)' : 'var(--text-muted)';
  return (
    <div style={{ marginBottom: 18 }}>
      <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <Radar size={14} /> Auto-detected signals (daily) — read-only AI input
        {signals.updatedAt && (
          <span className="cell-muted" style={{ fontWeight: 400, fontSize: '0.74rem' }}>
            · scraped {fmtDate(signals.updatedAt)}
          </span>
        )}
      </label>
      <p className="cell-muted" style={{ fontSize: '0.78rem', marginTop: 0, marginBottom: 6 }}>
        Scraped from yfinance during ingestion. Fed to the AI workflow alongside your additional context below.
      </p>
      <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg-subtle, rgba(127,127,127,0.06))' }}>
        {signals.detected.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
            <span style={{ fontSize: '0.78rem', fontWeight: 600 }}>Detected:</span>
            {signals.detected.map(t => <TagBadge key={t} tag={t} />)}
          </div>
        )}
        <div style={{ fontSize: '0.82rem', marginBottom: 8 }}>
          <strong>Capex (CWIP):</strong>{' '}
          {signals.capexCwip == null ? (
            <span className="cell-muted">not reported</span>
          ) : (
            <span>
              {fmtMoney(signals.capexCwip, market)} vs {fmtMoney(signals.capexCwipPrevQ, market)} prev Q{' '}
              <span style={{ color: capexColor }}>
                ({fmtPct(signals.capexChangePct)}{signals.capexTrend ? `, ${signals.capexTrend}` : ''})
              </span>
              {signals.capexAsOf && (
                <span className="cell-muted"> · as of {fmtDate(signals.capexAsOf)}</span>
              )}
            </span>
          )}
        </div>
        <div style={{ fontSize: '0.82rem' }}>
          <strong>Recent news:</strong>
          {signals.news.length === 0 ? (
            <span className="cell-muted"> none in window</span>
          ) : (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {signals.news.map((n, i) => (
                <li key={i} style={{ marginBottom: 6 }}>
                  {n.date && <span className="cell-muted">{n.date} · </span>}
                  {n.link ? (
                    <a href={n.link} target="_blank" rel="noreferrer">{n.title}</a>
                  ) : (
                    n.title
                  )}
                  {n.publisher && <span className="cell-muted"> ({n.publisher})</span>}
                  {n.tags && n.tags.length > 0 && (
                    <span style={{ display: 'inline-flex', gap: 4, marginLeft: 6, verticalAlign: 'middle' }}>
                      {n.tags.map(t => <TagBadge key={t} tag={t} />)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

const TAG_COLORS: Record<string, string> = {
  'M&A': '#7c3aed',
  'NEW-BIZ': '#2563eb',
  'SPINOFF': '#db2777',
  'POLICY': '#d97706',
  'DEMAND/PRICING': '#059669',
};

function TagBadge({ tag }: { tag: string }) {
  const color = TAG_COLORS[tag] ?? 'var(--text-muted)';
  return (
    <span style={{
      fontSize: '0.68rem', fontWeight: 600, padding: '1px 6px', borderRadius: 10,
      color: '#fff', background: color, whiteSpace: 'nowrap',
    }}>{tag}</span>
  );
}

function FundamentalDetailModal({ market, symbol, onClose }: { market: Market; symbol: string; onClose: () => void }) {
  const [row, setRow] = useState<FundamentalRow | null>(null);
  const [note, setNote] = useState('');
  const [signals, setSignals] = useState<FundamentalSignals | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const d = await fetchFundamentalDetail(market, symbol);
        if (cancelled) return;
        setRow(d.row);
        setNote(d.note ?? '');
        setSignals(d.signals ?? null);
      } catch {
        if (!cancelled) setRow(null);
      }
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [market, symbol]);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await saveFundamentalNote(market, symbol, note);
      setSaved(true);
    } catch { /* ignore */ }
    setSaving(false);
  };

  const metric = (label: string, value: ReactNode) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
      <span className="cell-muted" style={{ fontSize: '0.82rem' }}>{label}</span>
      <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>{value}</span>
    </div>
  );

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 560, width: '92%' }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            {symbol}
            {row?.earningsAnnouncedRecent && (
              <span className="badge" style={{ background: 'var(--success)', color: '#fff', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <BadgeCheck size={13} /> Earnings announced
              </span>
            )}
          </h2>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>

        {loading ? (
          <div className="loading"><div className="spinner" />Loading...</div>
        ) : !row ? (
          <div className="empty-state" style={{ padding: 24 }}>
            <p className="empty-state-text">No fundamentals available for this stock.</p>
          </div>
        ) : (
          <div style={{ padding: '4px 2px' }}>
            <div className="cell-muted" style={{ fontSize: '0.85rem', marginBottom: 10 }}>
              {row.companyName}{row.industry ? ` · ${row.industry}` : ''}
            </div>
            <div style={{ marginBottom: 16 }}>
              {metric('Latest quarter end', fmtDate(row.latestQuarterEnd))}
              {metric('Last earnings date', fmtDate(row.lastEarningsDate))}
              {metric('Previous earnings date', fmtDate(row.prevEarningsDate))}
              {metric('Reported EPS (surprise)', `${row.lastReportedEps == null ? '—' : curSym(market) + fmtNum(row.lastReportedEps)} (${fmtPct(row.lastEpsSurprisePct)})`)}
              {metric('Revenue', fmtMoney(row.revenue, market))}
              {metric('Revenue growth YoY', fmtPct(row.revenueGrowthYoyPct))}
              {metric('Operating profit', fmtMoney(row.operatingProfit, market))}
              {metric('Operating profit trend', <TrendBadge trend={row.operatingProfitTrend} />)}
              {metric('OPM', `${fmtNum(row.opm)}%`)}
              {metric('OPM trend', <TrendBadge trend={row.opmTrend} />)}
              {metric('Net profit', fmtMoney(row.netProfit, market))}
              {metric('Net margin', `${fmtNum(row.netMarginPct)}%`)}
              {metric('Earnings growth YoY', fmtPct(row.earningsGrowthYoyPct))}
              {metric('Earnings growth QoQ', fmtPct(row.earningsGrowthQoqPct))}
              {metric('Earnings increasing', row.earningsIncreasing == null ? '—' : (row.earningsIncreasing ? 'Yes' : 'No'))}
            </div>

            <AutoSignalsBlock signals={signals} market={market} />

            <div>
              <label className="form-label" style={{ display: 'block', marginBottom: 4 }}>
                Additional context — input for AI workflow
              </label>
              <p className="cell-muted" style={{ fontSize: '0.78rem', marginTop: 0, marginBottom: 6 }}>
                Paste concall takeaways, new business lines, capex, M&amp;A, policy notes, etc. Saved per stock.
              </p>
              <textarea
                className="form-input"
                style={{ width: '100%', minHeight: 120, resize: 'vertical', fontFamily: 'inherit' }}
                value={note}
                onChange={e => { setNote(e.target.value); setSaved(false); }}
                placeholder="Add fundamental context for this stock..."
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}>
                  {saving ? <Loader2 size={14} className="spin-icon" /> : null} Save note
                </button>
                {saved && <span style={{ color: 'var(--success)', fontSize: '0.82rem' }}>Saved ✓</span>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function FundamentalsPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [filter, setFilter] = useState<FundamentalScanner>('all');
  const [rows, setRows] = useState<FundamentalRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<keyof FundamentalRow>('lastEarningsDate');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const load = useCallback(async (f: FundamentalScanner) => {
    setLoading(true);
    try {
      setRows(await fetchFundamentals(m, f));
    } catch {
      setRows([]);
    }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(filter); }, [load, filter]);

  const toggleSort = (key: keyof FundamentalRow) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'symbol' || key === 'companyName' ? 'asc' : 'desc');
    }
  };

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    const aNull = av == null;
    const bNull = bv == null;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    let cmp: number;
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
    else if (typeof av === 'boolean' && typeof bv === 'boolean') cmp = (av ? 1 : 0) - (bv ? 1 : 0);
    else cmp = String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const arrow = (key: keyof FundamentalRow) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
  const th = (key: keyof FundamentalRow, label: string, right = false) => (
    <th
      onClick={() => toggleSort(key)}
      style={{ cursor: 'pointer', userSelect: 'none', textAlign: right ? 'right' : 'left', whiteSpace: 'nowrap' }}
    >
      {label}{arrow(key)}
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
          Fundamental Scanners
        </h1>
        <span className="page-subtitle">{m === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-outline btn-sm" onClick={() => load(filter)}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        {/* Filters */}
        <div className="card" style={{ width: 240, flexShrink: 0, padding: 8 }}>
          <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', padding: '6px 10px 4px' }}>
            Filters
          </div>
          {FILTERS.map(f => {
            const isSel = f.name === filter;
            return (
              <button
                key={f.name}
                onClick={() => setFilter(f.name)}
                style={{
                  display: 'block', width: '100%', padding: '8px 10px', border: 'none', borderRadius: 6,
                  cursor: 'pointer', textAlign: 'left', marginBottom: 2,
                  background: isSel ? 'var(--primary)' : 'transparent',
                  color: isSel ? '#fff' : 'var(--text)', fontSize: '0.86rem'
                }}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        {/* Table */}
        <div className="card" style={{ flex: 1, padding: 18, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <h2 className="section-title" style={{ margin: 0 }}>
              {FILTERS.find(f => f.name === filter)?.label}
            </h2>
            <span className="badge">{rows.length}</span>
          </div>

          {loading ? (
            <div className="loading"><div className="spinner" />Loading...</div>
          ) : rows.length === 0 ? (
            <div className="empty-state" style={{ padding: 28 }}>
              <div className="empty-state-icon">📊</div>
              <p className="empty-state-text">No stocks match this filter. Run a data refresh to ingest earnings fundamentals.</p>
            </div>
          ) : (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    {th('symbol', 'Symbol')}
                    {th('companyName', 'Company')}
                    {th('lastEarningsDate', 'Last earnings', true)}
                    {th('earningsGrowthYoyPct', 'PAT YoY %', true)}
                    {th('earningsIncreasing', 'Earn ↑', true)}
                    {th('netMarginPct', 'Net margin %', true)}
                    {th('opm', 'OPM %', true)}
                    <th>OPM trend</th>
                    <th>Op-profit trend</th>
                    {th('lastEpsSurprisePct', 'Surprise %', true)}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(r => (
                    <tr key={r.symbol}>
                      <td>
                        <button className="stock-link" onClick={() => setPicked(r.symbol)}>{r.symbol}</button>
                        {r.earningsAnnouncedRecent && (
                          <span title="Earnings announced in last 7 days" style={{ marginLeft: 6, color: 'var(--success)' }}>
                            <BadgeCheck size={13} style={{ verticalAlign: 'middle' }} />
                          </span>
                        )}
                      </td>
                      <td>{r.companyName ?? '—'}</td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtDate(r.lastEarningsDate)}</td>
                      <td style={{ textAlign: 'right', color: (r.earningsGrowthYoyPct ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                        {fmtPct(r.earningsGrowthYoyPct)}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        {r.earningsIncreasing == null ? '—' : (r.earningsIncreasing
                          ? <TrendingUp size={14} style={{ color: 'var(--success)' }} />
                          : <TrendingDown size={14} style={{ color: 'var(--danger)' }} />)}
                      </td>
                      <td style={{ textAlign: 'right' }}>{fmtNum(r.netMarginPct)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtNum(r.opm)}</td>
                      <td><TrendBadge trend={r.opmTrend} /></td>
                      <td><TrendBadge trend={r.operatingProfitTrend} /></td>
                      <td style={{ textAlign: 'right' }}>{fmtPct(r.lastEpsSurprisePct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {picked && (
        <FundamentalDetailModal market={m} symbol={picked} onClose={() => setPicked(null)} />
      )}
    </div>
  );
}
