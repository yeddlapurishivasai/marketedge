import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, ScannerInfo, ScannerResult, ScannerSchedule } from '../api';
import {
  fetchScanners, triggerScanner, fetchScannerDates, fetchScannerResults,
  fetchScannerSchedule, updateScannerSchedule
} from '../api';
import { StockLookupModal } from './StockLookupPage';
import {
  ChevronLeft, RefreshCw, Radar, PlayCircle, Loader2, Clock
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

function ScannerDetail({ m, info, onPickSymbol }: {
  m: Market;
  info: ScannerInfo;
  onPickSymbol: (s: string) => void;
}) {
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [results, setResults] = useState<ScannerResult[]>([]);
  const [loading, setLoading] = useState(true);

  const loadResults = useCallback(async (date?: string) => {
    setLoading(true);
    try {
      setResults(await fetchScannerResults(m, info.name, date));
    } catch {
      setResults([]);
    }
    setLoading(false);
  }, [m, info.name]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const ds = await fetchScannerDates(m, info.name);
        if (cancelled) return;
        setDates(ds);
        const first = ds[0] ?? '';
        setSelectedDate(first);
        await loadResults(first || undefined);
      } catch {
        if (!cancelled) { setDates([]); await loadResults(); }
      }
    })();
    return () => { cancelled = true; };
  }, [m, info.name, loadResults]);

  const onDateChange = async (d: string) => {
    setSelectedDate(d);
    await loadResults(d || undefined);
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <h2 className="section-title" style={{ margin: 0 }}>{info.label}</h2>
        <span className="badge">{results.length} hits</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
          <label className="cell-muted" style={{ fontSize: '0.8rem' }}>Scan date</label>
          <select
            className="select-input"
            value={selectedDate}
            onChange={e => onDateChange(e.target.value)}
            disabled={dates.length === 0}
          >
            {dates.length === 0 && <option value="">No results yet</option>}
            {dates.map(d => <option key={d} value={d}>{fmtDate(d)}</option>)}
          </select>
          <button className="btn btn-outline btn-sm" onClick={() => loadResults(selectedDate || undefined)}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" />Loading...</div>
      ) : results.length === 0 ? (
        <div className="empty-state" style={{ padding: 28 }}>
          <div className="empty-state-icon">🔍</div>
          <p className="empty-state-text">No matches for this scan date. Run a scan to populate results.</p>
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
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, sch] = await Promise.all([fetchScanners(m), fetchScannerSchedule(m)]);
      setScanners(s);
      setSchedule(sch);
      setSelected(prev => prev ?? s.find(x => !x.comingSoon)?.name ?? null);
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

  const runOne = async (name: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const { runId } = await triggerScanner(m, { scannerName: name, universe });
      setMessage(`Started ${name} scan #${runId} (${universe} universe).`);
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

  // Group scanners by family for the side panel.
  const groups = scanners.reduce<Record<string, ScannerInfo[]>>((acc, s) => {
    (acc[s.family] ??= []).push(s);
    return acc;
  }, {});
  const selectedInfo = scanners.find(s => s.name === selected) ?? null;

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
      <div className="card" style={{ padding: 16, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="form-label" style={{ margin: 0 }}>Universe</span>
          <button
            type="button"
            className={`btn btn-sm ${universe === 'stage2' ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => setUniverse('stage2')}
          >
            Stage 2
          </button>
          <button
            type="button"
            className={`btn btn-sm ${universe === 'all' ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => setUniverse('all')}
          >
            Full universe
          </button>
        </div>

        <button className="btn btn-primary btn-sm" disabled={busy} onClick={runPreClose}>
          {busy ? <Loader2 size={15} className="spin-icon" /> : <PlayCircle size={15} />}
          &nbsp;Run Pre-Close Scan
        </button>

        {schedule && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginLeft: 'auto' }}>
            <Clock size={15} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: '0.84rem' }} className="cell-muted">
              Auto every {schedule.intervalMinutes}m (market hours)
            </span>
            <button
              className={`btn btn-sm ${schedule.enabled ? 'btn-primary' : 'btn-outline'}`}
              onClick={toggleSchedule}
            >
              {schedule.enabled ? 'Enabled' : 'Disabled'}
            </button>
          </div>
        )}
      </div>

      {message && (
        <div style={{ marginBottom: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{message}</div>
      )}

      {loading ? (
        <div className="loading"><div className="spinner" />Loading scanners...</div>
      ) : (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          {/* Side panel */}
          <div className="card" style={{ width: 260, flexShrink: 0, padding: 8, maxHeight: '70vh', overflowY: 'auto' }}>
            {Object.entries(groups).map(([family, items]) => (
              <div key={family} style={{ marginBottom: 8 }}>
                <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', padding: '6px 10px 2px' }}>
                  {family}
                </div>
                {items.map(s => {
                  const isSel = s.name === selected;
                  return (
                    <button
                      key={s.name}
                      onClick={() => !s.comingSoon && setSelected(s.name)}
                      disabled={s.comingSoon}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                        padding: '7px 10px', border: 'none', borderRadius: 6, cursor: s.comingSoon ? 'default' : 'pointer',
                        background: isSel ? 'var(--primary)' : 'transparent',
                        color: isSel ? '#fff' : (s.comingSoon ? 'var(--text-muted)' : 'var(--text)'),
                        fontSize: '0.86rem', textAlign: 'left'
                      }}
                    >
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {s.label}
                      </span>
                      {s.comingSoon ? (
                        <span style={{ fontSize: '0.66rem', opacity: 0.8 }}>soon</span>
                      ) : (
                        <span
                          style={{
                            fontSize: '0.72rem', fontWeight: 600, minWidth: 22, textAlign: 'center',
                            padding: '1px 6px', borderRadius: 10,
                            background: isSel ? 'rgba(255,255,255,0.25)' : 'var(--border)',
                            color: isSel ? '#fff' : 'var(--text-secondary)'
                          }}
                        >
                          {s.latestHits}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          {/* Detail */}
          <div className="card" style={{ flex: 1, padding: 18, minWidth: 0 }}>
            {selectedInfo && !selectedInfo.comingSoon ? (
              <>
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: -6 }}>
                  <button className="btn btn-outline btn-sm" disabled={busy} onClick={() => runOne(selectedInfo.name)}>
                    {busy ? <Loader2 size={13} className="spin-icon" /> : <PlayCircle size={13} />}
                    &nbsp;Run this scanner
                  </button>
                </div>
                <ScannerDetail m={m} info={selectedInfo} onPickSymbol={setLookupSymbol} />
              </>
            ) : (
              <div className="empty-state" style={{ padding: 40 }}>
                <p className="empty-state-text">Select a scanner from the list.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {lookupSymbol && (
        <StockLookupModal market={m} symbol={lookupSymbol} onClose={() => setLookupSymbol(null)} />
      )}
    </div>
  );
}
