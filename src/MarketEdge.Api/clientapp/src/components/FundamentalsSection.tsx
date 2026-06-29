import { useState, useEffect } from 'react';
import type { Market, FundamentalRow, FundamentalSignals, EpsQuarter } from '../api';
import { fetchFundamentalDetail, saveFundamentalNote } from '../api';
import { Radar, TrendingUp, TrendingDown, Minus, Loader2 } from 'lucide-react';

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

function Prop({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="prop-row">
      <span className="prop-label">{label}</span>
      <span className="prop-value">{value ?? '—'}</span>
    </div>
  );
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

// Last 4 reported quarters of EPS: estimate vs actual, beat $ and surprise %. Oldest -> newest
// so it reads like the earnings chart on financial sites.
function EpsHistoryTable({ quarters, market }: { quarters: EpsQuarter[]; market: Market }) {
  if (!quarters || quarters.length === 0) return null;
  const sym = curSym(market);
  const ordered = [...quarters].reverse();
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr><th>Quarter</th><th>Estimate</th><th>Actual</th><th>Beat</th><th>Surprise</th></tr>
        </thead>
        <tbody>
          {ordered.map((q, i) => {
            const beat = q.actual != null && q.estimate != null ? q.actual - q.estimate : null;
            const positive = beat != null && beat >= 0;
            const cls = beat == null ? undefined : positive ? 'rev-up' : 'rev-down';
            return (
              <tr key={i}>
                <td>{fmtDate(q.date)}</td>
                <td>{q.estimate == null ? '—' : `${sym}${fmtNum(q.estimate)}`}</td>
                <td style={{ fontWeight: 600 }}>{q.actual == null ? '—' : `${sym}${fmtNum(q.actual)}`}</td>
                <td className={cls}>{beat == null ? '—' : `${positive ? '+' : '-'}${sym}${fmtNum(Math.abs(beat))}`}</td>
                <td className={cls}>{fmtPct(q.surprisePct)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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

/**
 * Reported-fundamentals block for the unified stock detail view: quarterly financials,
 * reported-EPS history (last 4 quarters), auto-detected signals and the per-stock AI note.
 * Self-loads from the fundamentals endpoint; renders nothing if the symbol has no row.
 */
export function FundamentalsSection({ market, symbol }: { market: Market; symbol: string }) {
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
      if (!cancelled) setLoading(false);
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

  if (loading) {
    return (
      <div className="lookup-section">
        <div className="loading"><div className="spinner" /> Loading fundamentals…</div>
      </div>
    );
  }
  if (!row) return null;

  const epsSurprise = `${row.lastReportedEps == null ? '—' : curSym(market) + fmtNum(row.lastReportedEps)} (${fmtPct(row.lastEpsSurprisePct)})`;

  return (
    <>
      <div className="lookup-section">
        <h2 className="section-title">
          Fundamentals
          {row.latestQuarterEnd && <span className="pill">Q end {fmtDate(row.latestQuarterEnd)}</span>}
        </h2>
        <div className="card prop-grid">
          <Prop label="Last earnings date" value={fmtDate(row.lastEarningsDate)} />
          <Prop label="Next earnings date" value={fmtDate(row.nextEarningsDate)} />
          <Prop label="Previous earnings date" value={fmtDate(row.prevEarningsDate)} />
          <Prop label="Reported EPS (surprise)" value={epsSurprise} />
          <Prop label="Trailing P/E" value={row.trailingPe == null ? '—' : fmtNum(row.trailingPe)} />
          <Prop label="Forward P/E" value={row.forwardPe == null ? '—' : fmtNum(row.forwardPe)} />
          <Prop label="Revenue" value={fmtMoney(row.revenue, market)} />
          <Prop label="Revenue growth YoY" value={fmtPct(row.revenueGrowthYoyPct)} />
          <Prop label="Operating profit" value={fmtMoney(row.operatingProfit, market)} />
          <Prop label="Operating profit trend" value={<TrendBadge trend={row.operatingProfitTrend} />} />
          <Prop label="OPM" value={`${fmtNum(row.opm)}%`} />
          <Prop label="OPM trend" value={<TrendBadge trend={row.opmTrend} />} />
          <Prop label="Net profit" value={fmtMoney(row.netProfit, market)} />
          <Prop label="Net margin" value={`${fmtNum(row.netMarginPct)}%`} />
          <Prop label="Earnings growth YoY" value={fmtPct(row.earningsGrowthYoyPct)} />
          <Prop label="Earnings growth QoQ" value={fmtPct(row.earningsGrowthQoqPct)} />
          <Prop label="Earnings increasing" value={row.earningsIncreasing == null ? '—' : (row.earningsIncreasing ? 'Yes' : 'No')} />
        </div>
      </div>

      {row.epsHistory.length > 0 && (
        <div className="lookup-section">
          <h2 className="section-title">
            Earnings Per Share — reported
            <span className="pill">last {row.epsHistory.length} quarter{row.epsHistory.length > 1 ? 's' : ''}</span>
          </h2>
          <EpsHistoryTable quarters={row.epsHistory} market={market} />
        </div>
      )}

      <div className="lookup-section">
        <h2 className="section-title">Signals &amp; Context <span className="pill">AI input</span></h2>
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
    </>
  );
}
