import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, JobRun, IngestionStep } from '../api';
import { fetchJobRuns, triggerIngestion } from '../api';
import {
  ChevronLeft, RefreshCw, Database, Download, LineChart, FileText,
  PlayCircle, Clock, CheckCircle2, XCircle, Loader2, AlertCircle, ListChecks
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

interface StepDef {
  step: IngestionStep;
  label: string;
  desc: string;
  icon: React.ReactNode;
}

const STEPS: StepDef[] = [
  { step: 'seed_tickers', label: 'Seed Tickers', desc: 'Populate the ticker master from the catalog', icon: <Database size={20} /> },
  { step: 'bars', label: 'Ingest Bars', desc: 'Fetch last 1 year of daily OHLCV (rolling window)', icon: <Download size={20} /> },
  { step: 'technical', label: 'Ingest Technical', desc: 'Compute daily technical snapshots', icon: <LineChart size={20} /> },
  { step: 'fundamentals', label: 'Ingest Fundamentals', desc: 'Analyst, EPS & market-cap (best-effort)', icon: <FileText size={20} /> },
];

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

function stepLabel(run: JobRun): string {
  const s = run.parameters?.step;
  return typeof s === 'string' ? s.replace(/_/g, ' ') : run.jobType.replace(/_/g, ' ');
}

export default function AdminPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [runs, setRuns] = useState<JobRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [testSample, setTestSample] = useState(true);
  const [limit, setLimit] = useState<string>('');
  const [busy, setBusy] = useState<IngestionStep | null>(null);
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

  const trigger = async (step: IngestionStep) => {
    setBusy(step);
    setMessage(null);
    try {
      const parsedLimit = limit.trim() ? parseInt(limit, 10) : undefined;
      if (parsedLimit !== undefined && (isNaN(parsedLimit) || parsedLimit < 1)) {
        setMessage('Limit must be a positive number.');
        setBusy(null);
        return;
      }
      const { runId } = await triggerIngestion(m, { step, testSample, limit: parsedLimit });
      setMessage(`Started run #${runId} (${step.replace(/_/g, ' ')}).`);
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to trigger ingestion.');
    }
    setBusy(null);
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

      {/* Options */}
      <div className="card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={testSample} onChange={e => setTestSample(e.target.checked)} />
          Test sample only
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          Limit
          <input
            type="number"
            min={1}
            placeholder="all"
            value={limit}
            onChange={e => setLimit(e.target.value)}
            style={{ width: 90 }}
          />
        </label>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          Options apply to the next step you start.
        </span>
      </div>

      {message && (
        <div className="card" style={{ padding: '10px 16px', marginBottom: 16, fontSize: '0.85rem' }}>
          {message}
        </div>
      )}

      {/* Step cards */}
      <div className="menu-grid" style={{ marginBottom: 20 }}>
        {STEPS.map(s => (
          <div key={s.step} className="menu-card" style={{ cursor: 'default' }}>
            <div className="menu-card-icon jobs">{s.icon}</div>
            <div className="menu-card-text">
              <h3>{s.label}</h3>
              <p>{s.desc}</p>
            </div>
            <button
              className="btn btn-primary btn-sm"
              style={{ marginLeft: 'auto' }}
              disabled={busy !== null}
              onClick={() => trigger(s.step)}
            >
              {busy === s.step ? <Loader2 size={14} className="spin-icon" /> : <PlayCircle size={14} />} Run
            </button>
          </div>
        ))}
      </div>

      <button
        className="btn btn-primary"
        style={{ marginBottom: 24 }}
        disabled={busy !== null}
        onClick={() => trigger('full')}
      >
        {busy === 'full' ? <Loader2 size={16} className="spin-icon" /> : <ListChecks size={16} />}
        &nbsp;Run Full Pipeline (seed → bars → technical → fundamentals)
      </button>

      {/* Recent runs */}
      <h2 className="section-title">Recent ingestion runs</h2>
      <div className="table-container">
        {loading ? (
          <div className="loading"><div className="spinner" />Loading...</div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🗄️</div>
            <p className="empty-state-text">No ingestion runs yet. Start a step above.</p>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Step</th>
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
                  <td style={{ textTransform: 'capitalize' }}>{stepLabel(run)}</td>
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
