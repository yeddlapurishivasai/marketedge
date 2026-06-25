import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, JobRun } from '../api';
import { fetchJobRuns, triggerIngestion } from '../api';
import {
  ChevronLeft, RefreshCw, Database, PlayCircle,
  Clock, CheckCircle2, XCircle, Loader2, AlertCircle
} from 'lucide-react';

const STATUS_ICON: Record<string, React.ReactNode> = {
  queued: <Clock size={16} />,
  running: <Loader2 size={16} className="spin-icon" />,
  completed: <CheckCircle2 size={16} />,
  failed: <XCircle size={16} />,
  cancelled: <AlertCircle size={16} />
};

const STATUS_CLASS: Record<string, string> = {
  queued: 'status-queued',
  running: 'status-running',
  completed: 'status-completed',
  failed: 'status-failed',
  cancelled: 'status-cancelled'
};

function formatDuration(seconds?: number): string {
  if (!seconds) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return `${min}m ${sec}s`;
}

function formatDate(dateStr?: string): string {
  return dateStr ? new Date(dateStr).toLocaleString() : '—';
}

function runMode(run: JobRun): string {
  return run.parameters?.testSample ? 'Sample (200)' : 'Full universe';
}

export default function AdminPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [runs, setRuns] = useState<JobRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [testSample, setTestSample] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchJobRuns({ market: m, jobType: 'data_ingestion', pageSize: 25 });
      setRuns(data);
    } catch { /* ignore */ }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 4s while a run is active.
  useEffect(() => {
    const hasActive = runs.some(r => r.status === 'running' || r.status === 'queued');
    if (!hasActive) return;
    const interval = setInterval(load, 4000);
    return () => clearInterval(interval);
  }, [runs, load]);

  const ingest = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { runId } = await triggerIngestion(m, { testSample });
      setMessage(`Started ingestion run #${runId} (${testSample ? 'Sample 200' : 'Full universe'}).`);
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to trigger ingestion.');
    }
    setBusy(false);
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
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-outline btn-sm" onClick={load}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Ingest panel */}
      <div className="card" style={{ padding: 20, marginBottom: 20, maxWidth: 560 }}>
        <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
          Runs the full pipeline for {m === 'india' ? 'India' : 'US'}: daily bars
          (seeds tickers, rolling 1-year window) → technicals → fundamentals.
        </p>

        {/* Run mode: sample vs full universe (mirrors Stage 2) */}
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
      </div>

      {/* Recent runs */}
      <h2 className="section-title">Recent ingestion runs</h2>
      <div className="table-container">
        {loading ? (
          <div className="loading"><div className="spinner" />Loading...</div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🗄️</div>
            <p className="empty-state-text">No ingestion runs yet. Click "Ingest Data" above.</p>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Started</th>
                <th>Duration</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr key={run.id}>
                  <td style={{ fontWeight: 600 }}>#{run.id}</td>
                  <td>{runMode(run)}</td>
                  <td>
                    <span className={`status-badge ${STATUS_CLASS[run.status]}`}>
                      {STATUS_ICON[run.status]} {run.status}
                    </span>
                  </td>
                  <td>
                    <div className="progress-bar-container">
                      <div className="progress-bar-fill" style={{ width: `${run.progress}%` }} />
                      <span className="progress-bar-text">{run.progress}%</span>
                    </div>
                  </td>
                  <td className="cell-muted">{formatDate(run.startedAt)}</td>
                  <td className="cell-muted">{formatDuration(run.durationSeconds)}</td>
                  <td>
                    <button className="btn btn-outline btn-sm" onClick={() => navigate(`/${m}/jobs`)}>
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
