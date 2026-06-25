import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, ScannerInfo, ScannerResult, ScannerSchedule } from '../api';
import {
  fetchScanners, triggerScanner, fetchScannerDates, fetchScannerResults,
  fetchScannerSchedule, updateScannerSchedule
} from '../api';
import { StockLookupModal } from './StockLookupPage';
import {
  ChevronLeft, RefreshCw, Radar, PlayCircle, Loader2,
  ChevronRight, ChevronDown, Clock
} from 'lucide-react';

function fmtVol(v?: number | null): string {
  if (v == null) return '—';
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(v);
}

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtDate(d?: string | null): string {
  return d ? new Date(d).toLocaleDateString() : '—';
}

function triggerSummary(json?: string | null): string {
  if (!json) return '—';
  try {
    const obj = JSON.parse(json);
    return Object.entries(obj)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? Number(v).toFixed(2) : v}`)
      .join(' · ');
  } catch {
    return json;
  }
}

function ScannerSection({ m, info, onPickSymbol }: {
  m: Market;
  info: ScannerInfo;
  onPickSymbol: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [results, setResults] = useState<ScannerResult[]>([]);
  const [loading, setLoading] = useState(false);

  const loadResults = useCallback(async (date?: string) => {
    setLoading(true);
    try {
      const rows = await fetchScannerResults(m, info.name, date);
      setResults(rows);
    } catch {
      setResults([]);
    }
    setLoading(false);
  }, [m, info.name]);

  const expand = async () => {
    const next = !open;
    setOpen(next);
    if (next && dates.length === 0) {
      try {
        const ds = await fetchScannerDates(m, info.name);
        setDates(ds);
        const first = ds[0] ?? '';
        setSelectedDate(first);
        await loadResults(first || undefined);
      } catch {
        await loadResults();
      }
    }
  };

  const onDateChange = async (d: string) => {
    setSelectedDate(d);
    await loadResults(d || undefined);
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', cursor: info.comingSoon ? 'default' : 'pointer' }}
        onClick={info.comingSoon ? undefined : expand}
      >
        {!info.comingSoon && (open ? <ChevronDown size={16} /> : <ChevronRight size={16} />)}
        <span style={{ fontWeight: 600 }}>{info.label}</span>
        {info.comingSoon ? (
          <span className="status-badge status-queued" style={{ marginLeft: 8 }}>Coming soon</span>
        ) : (
          <span className="badge" style={{ marginLeft: 8 }}>{info.latestHits} hits</span>
        )}
        {!info.comingSoon && info.latestScanDate && (
          <span className="cell-muted" style={{ marginLeft: 'auto', fontSize: '0.8rem' }}>
            Latest: {fmtDate(info.latestScanDate)}
          </span>
        )}
      </div>

      {open && !info.comingSoon && (
        <div style={{ padding: '0 16px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <label className="cell-muted" style={{ fontSize: '0.8rem' }}>Scan date</label>
            <select
              className="select-input"
              value={selectedDate}
              onChange={e => onDateChange(e.target.value)}
              disabled={dates.length === 0}
            >
              {dates.length === 0 && <option value="">No results yet</option>}
              {dates.map(d => (
                <option key={d} value={d}>{fmtDate(d)}</option>
              ))}
            </select>
            <button className="btn btn-outline btn-sm" onClick={() => loadResults(selectedDate || undefined)}>
              <RefreshCw size={13} /> Refresh
            </button>
          </div>

          {loading ? (
            <div className="loading"><div className="spinner" />Loading...</div>
          ) : results.length === 0 ? (
            <div className="empty-state" style={{ padding: 20 }}>
              <p className="empty-state-text">No matches for this scan date.</p>
            </div>
          ) : (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Company</th>
                    <th>Sector</th>
                    <th style={{ textAlign: 'right' }}>Close</th>
                    <th style={{ textAlign: 'right' }}>Day %</th>
                    <th style={{ textAlign: 'right' }}>Volume</th>
                    <th style={{ textAlign: 'right' }}>RVol</th>
                    <th style={{ textAlign: 'right' }}>RS</th>
                    <th>Triggers</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map(r => (
                    <tr key={r.symbol}>
                      <td>
                        <button className="stock-link" onClick={() => onPickSymbol(r.symbol)}>{r.symbol}</button>
                      </td>
                      <td>{r.companyName ?? '—'}</td>
                      <td className="cell-muted">{r.industry ?? r.sectorName ?? '—'}</td>
                      <td style={{ textAlign: 'right' }}>{r.closePrice?.toFixed(2) ?? '—'}</td>
                      <td style={{ textAlign: 'right', color: (r.dayChangePct ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                        {fmtPct(r.dayChangePct)}
                      </td>
                      <td style={{ textAlign: 'right' }}>{fmtVol(r.volume)}</td>
                      <td style={{ textAlign: 'right' }}>{r.relVolume?.toFixed(2) ?? '—'}</td>
                      <td style={{ textAlign: 'right' }}>{r.rsRating ?? '—'}</td>
                      <td className="cell-muted" style={{ fontSize: '0.78rem' }}>{triggerSummary(r.triggerDetails)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ScannersPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [scanners, setScanners] = useState<ScannerInfo[]>([]);
  const [schedule, setSchedule] = useState<ScannerSchedule | null>(null);
  const [universe, setUniverse] = useState<'stage2' | 'all'>('stage2');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [lookupSymbol, setLookupSymbol] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, sch] = await Promise.all([fetchScanners(m), fetchScannerSchedule(m)]);
      setScanners(s);
      setSchedule(sch);
    } catch { /* ignore */ }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  const runPreClose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { runId } = await triggerScanner(m, { scannerName: null, universe });
      setMessage(`Started pre-close scan #${runId} (all scanners, ${universe} universe). Results appear once the run completes.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to trigger scan.');
    }
    setBusy(false);
  };

  const toggleSchedule = async () => {
    if (!schedule) return;
    try {
      const updated = await updateScannerSchedule(m, { enabled: !schedule.enabled, intervalMinutes: schedule.intervalMinutes });
      setSchedule(updated);
    } catch { /* ignore */ }
  };

  const active = scanners.filter(s => !s.comingSoon);
  const comingSoon = scanners.filter(s => s.comingSoon);

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <Radar size={24} style={{ marginRight: 8 }} />
          Technical Scanners
        </h1>
        <span className="page-subtitle">{m === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-outline btn-sm" onClick={load}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Controls */}
      <div className="card" style={{ padding: 20, marginBottom: 20 }}>
        <div className="form-group" style={{ marginBottom: 14 }}>
          <label className="form-label">Universe</label>
          <div style={{ display: 'flex', gap: 8, maxWidth: 420 }}>
            <button
              type="button"
              className={`btn btn-sm ${universe === 'stage2' ? 'btn-primary' : 'btn-outline'}`}
              style={{ flex: 1 }}
              onClick={() => setUniverse('stage2')}
            >
              Stage 2 stocks
            </button>
            <button
              type="button"
              className={`btn btn-sm ${universe === 'all' ? 'btn-primary' : 'btn-outline'}`}
              style={{ flex: 1 }}
              onClick={() => setUniverse('all')}
            >
              Full universe
            </button>
          </div>
          <div style={{ marginTop: 6, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
            Each scan refreshes today's price for the selected universe, then screens it. Stage 2
            is faster; full universe covers every catalogued stock.
          </div>
        </div>

        <button className="btn btn-primary" disabled={busy} onClick={runPreClose}>
          {busy ? <Loader2 size={16} className="spin-icon" /> : <PlayCircle size={16} />}
          &nbsp;Run Pre-Close Scan (all scanners)
        </button>

        {schedule && (
          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Clock size={16} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: '0.88rem' }}>
              Auto-schedule (every {schedule.intervalMinutes} min during market hours)
            </span>
            <button
              className={`btn btn-sm ${schedule.enabled ? 'btn-primary' : 'btn-outline'}`}
              onClick={toggleSchedule}
            >
              {schedule.enabled ? 'Enabled' : 'Disabled'}
            </button>
            <span className="cell-muted" style={{ fontSize: '0.8rem' }}>
              Last enqueued: {schedule.lastEnqueuedAt ? new Date(schedule.lastEnqueuedAt).toLocaleString() : '—'}
            </span>
          </div>
        )}

        {message && (
          <div style={{ marginTop: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            {message}
          </div>
        )}
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" />Loading scanners...</div>
      ) : (
        <>
          {active.map(s => (
            <ScannerSection key={s.name} m={m} info={s} onPickSymbol={setLookupSymbol} />
          ))}
          {comingSoon.map(s => (
            <ScannerSection key={s.name} m={m} info={s} onPickSymbol={setLookupSymbol} />
          ))}
        </>
      )}

      {lookupSymbol && (
        <StockLookupModal market={m} symbol={lookupSymbol} onClose={() => setLookupSymbol(null)} />
      )}
    </div>
  );
}
