import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Market, ScannerSchedule, JobSchedule, TriggerIngestionRequest } from '../api';
import {
  triggerIngestion, fetchScannerSchedule, updateScannerSchedule,
  fetchFundamentalsSchedule, updateFundamentalsSchedule,
  fetchStage2Schedule, updateStage2Schedule,
} from '../api';
import {
  ChevronLeft, Database, PlayCircle, Loader2, Clock, LineChart, RefreshCw
} from 'lucide-react';

const MARKETS: { market: Market; label: string }[] = [
  { market: 'india', label: '🇮🇳 India (NSE)' },
  { market: 'us', label: '🇺🇸 US' },
];

function formatDateTime(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function IngestPanel({ market, label }: { market: Market; label: string }) {
  const navigate = useNavigate();
  const [testSample, setTestSample] = useState(true);
  const [busy, setBusy] = useState<null | 'full' | 'fundamentals' | 'missing'>(null);
  const [message, setMessage] = useState<string | null>(null);

  const run = async (
    kind: 'full' | 'fundamentals' | 'missing',
    request: TriggerIngestionRequest,
    describe: string,
  ) => {
    setBusy(kind);
    setMessage(null);
    try {
      const { runId } = await triggerIngestion(market, request);
      setMessage(`Started run #${runId} — ${describe}.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to trigger ingestion.');
    }
    setBusy(null);
  };

  const ingest = () =>
    run('full', { testSample }, `${testSample ? 'Sample 200' : 'Full universe'} · full pipeline`);

  const refreshFundamentals = () =>
    run('fundamentals', { testSample, steps: ['fundamentals'] },
      `${testSample ? 'Sample 200' : 'Full universe'} · fundamentals only`);

  const refreshMissing = () =>
    run('missing', { testSample, missingOnly: true },
      `${testSample ? 'Sample 200' : 'Full universe'} · fill missing technical + fundamental data`);

  return (
    <div className="card" style={{ padding: 20, flex: 1, minWidth: 320 }}>
      <h3 style={{ marginTop: 0, marginBottom: 8 }}>{label}</h3>
      <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
        Runs the full pipeline: daily bars (seeds tickers, rolling 1-year window) →
        technicals → fundamentals.
      </p>

      <div className="form-group">
        <label className="form-label">Run Mode</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className={`btn btn-sm ${testSample ? 'btn-primary' : 'btn-outline'}`}
            style={{ flex: 1 }}
            onClick={() => setTestSample(true)}
          >
            Sample (200)
          </button>
          <button
            type="button"
            className={`btn btn-sm ${!testSample ? 'btn-primary' : 'btn-outline'}`}
            style={{ flex: 1 }}
            onClick={() => setTestSample(false)}
          >
            Full universe
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" disabled={busy !== null} onClick={ingest}>
          {busy === 'full' ? <Loader2 size={16} className="spin-icon" /> : <PlayCircle size={16} />}
          &nbsp;Ingest Data
        </button>
        <button className="btn btn-outline btn-sm" onClick={() => navigate(`/${market}/jobs`)}>
          View job runs →
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-outline btn-sm" disabled={busy !== null} onClick={refreshFundamentals} title="Re-run only the fundamentals step (earnings, margins, growth)">
          {busy === 'fundamentals' ? <Loader2 size={14} className="spin-icon" /> : <LineChart size={14} />}
          &nbsp;Refresh Fundamentals
        </button>
        <button className="btn btn-outline btn-sm" disabled={busy !== null} onClick={refreshMissing} title="Run the whole pipeline but only for tickers missing technical or fundamental data">
          {busy === 'missing' ? <Loader2 size={14} className="spin-icon" /> : <RefreshCw size={14} />}
          &nbsp;Refresh Data (fill missing)
        </button>
      </div>

      {message && (
        <div style={{ marginTop: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          {message}
        </div>
      )}
    </div>
  );
}

function JobScheduleCard({
  title, description, cadence,
  fetchFn, updateFn,
}: {
  title: string;
  description: string;
  cadence: string;
  fetchFn: (market: Market) => Promise<JobSchedule>;
  updateFn: (market: Market, body: { enabled: boolean; hourLocal?: number }) => Promise<JobSchedule>;
}) {
  const [schedules, setSchedules] = useState<Record<string, JobSchedule>>({});

  const load = useCallback(async () => {
    try {
      const entries = await Promise.all(
        MARKETS.map(async ({ market }) => [market, await fetchFn(market)] as const)
      );
      setSchedules(Object.fromEntries(entries));
    } catch { /* ignore */ }
  }, [fetchFn]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const toggle = async (market: Market) => {
    const s = schedules[market];
    if (!s) return;
    try {
      const updated = await updateFn(market, { enabled: !s.enabled, hourLocal: s.hourLocal });
      setSchedules(prev => ({ ...prev, [market]: updated }));
    } catch { /* ignore */ }
  };

  const changeHour = async (market: Market, hourLocal: number) => {
    const s = schedules[market];
    if (!s || Number.isNaN(hourLocal)) return;
    try {
      const updated = await updateFn(market, { enabled: s.enabled, hourLocal });
      setSchedules(prev => ({ ...prev, [market]: updated }));
    } catch { /* ignore */ }
  };

  return (
    <>
      <h2 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Clock size={18} /> {title}
      </h2>
      <div className="card" style={{ padding: 20, maxWidth: 720 }}>
        <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          {description}
        </p>
        {MARKETS.map(({ market, label }) => {
          const s = schedules[market];
          return (
            <div
              key={market}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '12px 0', borderTop: '1px solid var(--border)',
              }}
            >
              <span style={{ fontWeight: 600, minWidth: 140 }}>{label}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem' }}>
                {cadence} at
                <input
                  type="number" min={0} max={23}
                  value={s?.hourLocal ?? 20}
                  disabled={!s}
                  onChange={e => changeHour(market, parseInt(e.target.value, 10))}
                  style={{ width: 52, padding: '2px 6px' }}
                />
                <span className="cell-muted">local</span>
              </span>
              <span className="cell-muted" style={{ fontSize: '0.8rem' }}>
                Last run: {formatDateTime(s?.lastRunAt)}
              </span>
              <button
                className={`btn btn-sm ${s?.enabled ? 'btn-primary' : 'btn-outline'}`}
                style={{ marginLeft: 'auto' }}
                disabled={!s}
                onClick={() => toggle(market)}
              >
                {s?.enabled ? 'Enabled' : 'Disabled'}
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}

export default function AdminPage() {
  const navigate = useNavigate();

  const [schedules, setSchedules] = useState<Record<string, ScannerSchedule>>({});

  const loadSchedules = useCallback(async () => {
    try {
      const entries = await Promise.all(
        MARKETS.map(async ({ market }) => [market, await fetchScannerSchedule(market)] as const)
      );
      setSchedules(Object.fromEntries(entries));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSchedules(); }, [loadSchedules]);

  // Keep market-open indicators and last-run times fresh.
  useEffect(() => {
    const interval = setInterval(loadSchedules, 30000);
    return () => clearInterval(interval);
  }, [loadSchedules]);

  const toggleSchedule = async (market: Market) => {
    const s = schedules[market];
    if (!s) return;
    try {
      const updated = await updateScannerSchedule(market, {
        enabled: !s.enabled,
        intervalMinutes: s.intervalMinutes,
      });
      setSchedules(prev => ({ ...prev, [market]: updated }));
    } catch { /* ignore */ }
  };

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate('/')}>
          <ChevronLeft size={16} /> Home
        </button>
        <h1 className="page-title">
          <Database size={24} style={{ marginRight: 8 }} />
          Admin
        </h1>
        <span className="page-subtitle">Data ingestion &amp; scanner schedule</span>
      </div>

      {/* Ingestion — both markets */}
      <h2 className="section-title">Data Ingestion</h2>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
        {MARKETS.map(({ market, label }) => (
          <IngestPanel key={market} market={market} label={label} />
        ))}
      </div>

      {/* Scanner schedule — both markets */}
      <h2 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Clock size={18} /> Scanner Schedule
      </h2>
      <div className="card" style={{ padding: 20, maxWidth: 720 }}>
        <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          When enabled, all technical scanners run every 15 minutes during market hours
          (auto start/stop on each exchange's local trading session). Enabled by default.
        </p>

        {MARKETS.map(({ market, label }) => {
          const s = schedules[market];
          const open = s?.isMarketOpen ?? false;
          return (
            <div
              key={market}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '12px 0', borderTop: '1px solid var(--border)',
              }}
            >
              <span style={{ fontWeight: 600, minWidth: 140 }}>{label}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem' }}>
                <span
                  style={{
                    width: 9, height: 9, borderRadius: '50%',
                    background: open ? 'var(--success)' : 'var(--danger)',
                    display: 'inline-block',
                  }}
                />
                {open ? 'Market open' : 'Market closed'}
              </span>
              <span className="cell-muted" style={{ fontSize: '0.8rem' }}>
                Last run: {formatDateTime(s?.lastRunAt)}
              </span>
              <button
                className={`btn btn-sm ${s?.enabled ? 'btn-primary' : 'btn-outline'}`}
                style={{ marginLeft: 'auto' }}
                disabled={!s}
                onClick={() => toggleSchedule(market)}
              >
                {s?.enabled ? 'Enabled' : 'Disabled'}
              </button>
            </div>
          );
        })}
      </div>

      <JobScheduleCard
        title="Nightly Fundamentals Refresh"
        cadence="Nightly"
        description="When enabled, fundamentals (analyst targets, EPS forecasts, earnings, market cap) for the stage2 universe refresh once per night after the chosen exchange-local hour, on weekdays. Enabled by default."
        fetchFn={fetchFundamentalsSchedule}
        updateFn={updateFundamentalsSchedule}
      />

      <JobScheduleCard
        title="Weekend Stage 2 Analysis"
        cadence="Sat & Sun"
        description="When enabled, a full stage 2 analysis runs once per weekend day (Saturday and Sunday) after the chosen exchange-local hour, refreshing the week's stage2 classification while markets are closed. Enabled by default."
        fetchFn={fetchStage2Schedule}
        updateFn={updateStage2Schedule}
      />
    </div>
  );
}
