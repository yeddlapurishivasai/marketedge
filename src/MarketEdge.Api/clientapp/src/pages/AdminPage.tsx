import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, ScannerSchedule } from '../api';
import { triggerIngestion, fetchScannerSchedule, updateScannerSchedule } from '../api';
import {
  ChevronLeft, Database, PlayCircle, Loader2, Clock
} from 'lucide-react';

const SCHEDULE_MARKETS: { market: Market; label: string }[] = [
  { market: 'india', label: '🇮🇳 India (NSE)' },
  { market: 'us', label: '🇺🇸 US' },
];

export default function AdminPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [testSample, setTestSample] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const [schedules, setSchedules] = useState<Record<string, ScannerSchedule>>({});

  const loadSchedules = useCallback(async () => {
    try {
      const entries = await Promise.all(
        SCHEDULE_MARKETS.map(async ({ market }) => [market, await fetchScannerSchedule(market)] as const)
      );
      setSchedules(Object.fromEntries(entries));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSchedules(); }, [loadSchedules]);

  // Keep the market-open indicators fresh.
  useEffect(() => {
    const interval = setInterval(loadSchedules, 30000);
    return () => clearInterval(interval);
  }, [loadSchedules]);

  const ingest = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { runId } = await triggerIngestion(m, { testSample });
      setMessage(`Started ingestion run #${runId} (${testSample ? 'Sample 200' : 'Full universe'}).`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to trigger ingestion.');
    }
    setBusy(false);
  };

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
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <Database size={24} style={{ marginRight: 8 }} />
          Data Ingestion
        </h1>
        <span className="page-subtitle">{m === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
      </div>

      {/* Ingest panel */}
      <div className="card" style={{ padding: 20, marginBottom: 20, maxWidth: 560 }}>
        <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
          Runs the full pipeline for {m === 'india' ? 'India' : 'US'}: daily bars
          (seeds tickers, rolling 1-year window) → technicals → fundamentals.
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
              Sample (200 stocks)
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
          <div style={{ marginTop: 6, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
            {testSample
              ? 'Ingests only the curated test-sample stocks — fast, ideal for local testing.'
              : 'Ingests the entire ticker universe — slower, full dataset.'}
          </div>
        </div>

        <button
          className="btn btn-primary"
          style={{ marginTop: 16 }}
          disabled={busy}
          onClick={ingest}
        >
          {busy ? <Loader2 size={16} className="spin-icon" /> : <PlayCircle size={16} />}
          &nbsp;Ingest Data
        </button>

        {message && (
          <div style={{ marginTop: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            {message}
          </div>
        )}

        <div style={{ marginTop: 14, fontSize: '0.82rem' }}>
          <button className="btn btn-outline btn-sm" onClick={() => navigate(`/${m}/jobs`)}>
            View job runs →
          </button>
        </div>
      </div>

      {/* Scanner schedule */}
      <div className="card" style={{ padding: 20, marginBottom: 20, maxWidth: 560 }}>
        <h2 className="section-title" style={{ marginTop: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Clock size={18} /> Scanner Schedule
        </h2>
        <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          When enabled, all technical scanners run every 15 minutes during market hours
          (auto start/stop on each exchange's local trading session).
        </p>

        {SCHEDULE_MARKETS.map(({ market, label }) => {
          const s = schedules[market];
          const open = s?.isMarketOpen ?? false;
          return (
            <div
              key={market}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 0', borderTop: '1px solid var(--border)',
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
    </div>
  );
}
