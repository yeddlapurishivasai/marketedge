import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, MarketRegime, RegimeSchedule } from '../api';
import { fetchRegime, refreshRegime, fetchRegimeSchedule, updateRegimeSchedule } from '../api';
import { ChevronLeft, Gauge, RefreshCw, PlayCircle, Loader2, AlertTriangle } from 'lucide-react';

export function toneColor(tone?: string | null): string {
  switch (tone) {
    case 'green': return 'var(--success)';
    case 'red': return 'var(--danger)';
    case 'yellow': return 'var(--warning)';
    default: return 'var(--text-muted)';
  }
}

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtNum(v?: number | null): string {
  if (v == null) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtDate(d?: string | null): string {
  return d ? new Date(d).toLocaleDateString() : '—';
}

/** Compact, display-only regime summary. Optionally links to the full regime page. */
export function RegimeBanner({ market, onOpen }: { market: Market; onOpen?: () => void }) {
  const [regime, setRegime] = useState<MarketRegime | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const r = await fetchRegime(market);
        if (!cancelled) setRegime(r);
      } catch {
        if (!cancelled) setRegime(null);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [market]);

  if (loading) return <div className="regime-banner regime-banner--muted">Loading market regime…</div>;
  if (!regime) return <div className="regime-banner regime-banner--muted">Market regime unavailable.</div>;

  return (
    <div
      className="regime-banner"
      onClick={onOpen}
      style={{ cursor: onOpen ? 'pointer' : 'default', borderLeft: `3px solid ${toneColor(regime.tone)}` }}
    >
      <span className="regime-dot" style={{ background: toneColor(regime.tone) }} />
      <span className="regime-banner-title">{regime.regimeLabel}</span>
      <span className="regime-banner-sep">·</span>
      <span title="Benchmark condition">
        Trend <strong style={{ color: toneColor(regime.condition.tone) }}>{regime.condition.label}</strong>
      </span>
      <span className="regime-banner-sep">·</span>
      <span title="Breadth composite">
        Breadth <strong style={{ color: toneColor(regime.breadth.tone) }}>
          {regime.breadth.label}{regime.breadth.score != null ? ` (${regime.breadth.score})` : ''}
        </strong>
      </span>
      {regime.isIntraday && (
        <span className="regime-live" title="Reflects live intraday index price (market is open)">
          ● LIVE
        </span>
      )}
      {regime.stale && (
        <span className="regime-stale" title={regime.staleReason ?? 'Data may be stale'}>
          <AlertTriangle size={12} /> stale
        </span>
      )}
      <span className="regime-banner-asof">as of {fmtDate(regime.asOfDate)}</span>
    </div>
  );
}

export default function RegimePage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [regime, setRegime] = useState<MarketRegime | null>(null);
  const [schedule, setSchedule] = useState<RegimeSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, s] = await Promise.all([fetchRegime(m), fetchRegimeSchedule(m).catch(() => null)]);
      setRegime(r);
      setSchedule(s);
    } catch {
      setRegime(null);
    }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  const refresh = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { runId } = await refreshRegime(m);
      setMessage(`Started regime refresh #${runId}. The snapshot updates once the worker completes.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to refresh regime.');
    }
    setBusy(false);
  };

  const toggleSchedule = async () => {
    if (!schedule) return;
    try {
      const updated = await updateRegimeSchedule(m, { enabled: !schedule.enabled, hourLocal: schedule.hourLocal });
      setSchedule(updated);
    } catch { /* ignore */ }
  };

  const c = regime?.condition;
  const b = regime?.breadth;

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <Gauge size={24} style={{ marginRight: 8 }} />
          Market Regime
        </h1>
        <span className="page-subtitle">{m === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <div className="header-actions">
          <button className="btn btn-outline btn-sm" onClick={load}>
            <RefreshCw size={14} /> Reload
          </button>
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={refresh}>
            {busy ? <Loader2 size={14} className="spin-icon" /> : <PlayCircle size={14} />}
            &nbsp;Refresh data
          </button>
        </div>
      </div>

      {message && (
        <div style={{ marginBottom: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{message}</div>
      )}

      {loading ? (
        <div className="loading"><div className="spinner" />Loading market regime…</div>
      ) : !regime ? (
        <div className="card" style={{ padding: 24 }}>Market regime is unavailable.</div>
      ) : (
        <>
          {/* Effective regime hero */}
          <div className="card regime-hero" style={{ borderTop: `4px solid ${toneColor(regime.tone)}` }}>
            <div className="regime-hero-main">
              <span className="regime-dot regime-dot--lg" style={{ background: toneColor(regime.tone) }} />
              <div>
                <div className="regime-hero-label">{regime.regimeLabel}</div>
                <div className="regime-hero-posture">{regime.posture}</div>
              </div>
            </div>
            <div className="regime-hero-meta">
              <div>as of <strong>{fmtDate(regime.asOfDate)}</strong></div>
              {regime.isIntraday && (
                <div className="regime-live" title="Reflects live intraday index price (market is open)">
                  ● LIVE · intraday
                </div>
              )}
              {regime.stale && (
                <div className="regime-stale" title={regime.staleReason ?? ''}>
                  <AlertTriangle size={13} /> {regime.staleReason ?? 'stale'}
                </div>
              )}
            </div>
          </div>

          <div className="regime-cards">
            {/* Benchmark condition */}
            <div className="card regime-detail">
              <div className="regime-detail-head">
                <h3>Benchmark condition</h3>
                <span className="regime-pill" style={{ color: toneColor(c?.tone), borderColor: toneColor(c?.tone) }}>
                  {c?.label}
                </span>
              </div>
              <p className="regime-detail-desc">{c?.explanation}</p>
              <dl className="regime-stats">
                <div><dt>Benchmark</dt><dd>{c?.benchmarkSymbol ?? '—'}</dd></div>
                <div><dt>Close</dt><dd>{fmtNum(c?.close)}</dd></div>
                <div><dt>vs SMA20</dt><dd>{fmtPct(c?.closeVsSma20Pct)}</dd></div>
                <div><dt>vs SMA50</dt><dd>{fmtPct(c?.closeVsSma50Pct)}</dd></div>
                <div><dt>vs SMA200</dt><dd>{fmtPct(c?.closeVsSma200Pct)}</dd></div>
                <div><dt>Volume vs avg</dt><dd>{fmtPct(c?.volumeVsAvgPct)}</dd></div>
              </dl>
            </div>

            {/* Breadth composite */}
            <div className="card regime-detail">
              <div className="regime-detail-head">
                <h3>Breadth composite</h3>
                <span className="regime-pill" style={{ color: toneColor(b?.tone), borderColor: toneColor(b?.tone) }}>
                  {b?.label}
                </span>
              </div>
              <div className="regime-score-row">
                <span className="regime-score" style={{ color: toneColor(b?.tone) }}>
                  {b?.score != null ? b.score : '—'}
                </span>
                <span className="regime-score-sub">
                  {b ? `${b.positiveSignals}/${b.availableSignals} signals positive` : ''}<br />
                  {b ? `${b.evaluatedCount.toLocaleString()} stocks evaluated` : ''}
                </span>
              </div>
              <table className="regime-signals">
                <thead>
                  <tr><th>Signal</th><th>Value</th><th>Positive when</th><th></th></tr>
                </thead>
                <tbody>
                  {b?.signals.map(s => (
                    <tr key={s.key}>
                      <td>{s.label}</td>
                      <td>{fmtNum(s.value)}</td>
                      <td className="regime-signal-th">{s.threshold}</td>
                      <td>
                        <span
                          className="regime-dot regime-dot--sm"
                          style={{ background: s.positive == null ? 'var(--text-muted)' : s.positive ? 'var(--success)' : 'var(--danger)' }}
                          title={s.positive == null ? 'unavailable' : s.positive ? 'positive' : 'negative'}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {schedule && (
            <div className="card" style={{ padding: 16, marginTop: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span className="form-label" style={{ margin: 0 }}>Nightly refresh</span>
              <button
                type="button"
                className={`btn btn-sm ${schedule.enabled ? 'btn-primary' : 'btn-outline'}`}
                onClick={toggleSchedule}
              >
                {schedule.enabled ? 'Enabled' : 'Disabled'}
              </button>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Runs after {String(schedule.hourLocal).padStart(2, '0')}:00 exchange-local.
                {schedule.lastRunAt ? ` Last run ${new Date(schedule.lastRunAt).toLocaleString()}.` : ''}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
