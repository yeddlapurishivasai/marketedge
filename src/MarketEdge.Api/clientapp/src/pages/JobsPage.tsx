import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, JobRun } from '../api';
import { fetchJobRuns, fetchJobRun, cancelJobRun } from '../api';
import {
  ChevronLeft, RefreshCw, Clock, CheckCircle2, XCircle,
  Loader2, AlertCircle, Activity, StopCircle, Maximize2, X
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

/**
 * Flattens a metrics object into displayable label/value pairs. Nested objects
 * (e.g. `breakouts` = { opened, managed, ... }, `perScanner`) are expanded into
 * "parent · child" rows so they don't render as "[object Object]".
 */
function flattenMetrics(metrics: Record<string, unknown>, prefix = ''): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  for (const [k, v] of Object.entries(metrics)) {
    const label = `${prefix}${k.replace(/_/g, ' ')}`;
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      out.push(...flattenMetrics(v as Record<string, unknown>, `${label} · `));
    } else {
      out.push({ label, value: Array.isArray(v) ? v.join(', ') : String(v) });
    }
  }
  return out;
}

function formatDuration(seconds?: number): string {
  if (!seconds) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return `${min}m ${sec}s`;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString();
}

function formatJobType(type: string): string {
  return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const JOB_TYPES = [
  { value: '', label: 'All types' },
  { value: 'data_ingestion', label: 'Data Ingestion' },
  { value: 'stage2_analysis', label: 'Stage 2 Analysis' },
  { value: 'scanner', label: 'Scanner' },
  { value: 'stock_refresh', label: 'Stock Refresh' },
];

export default function JobsPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [runs, setRuns] = useState<JobRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState<JobRun | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [jobType, setJobType] = useState('');
  const [metricPopup, setMetricPopup] = useState<{ label: string; value: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchJobRuns({ market: m, jobType: jobType || undefined, pageSize: 50 });
      setRuns(data);
    } catch { /* ignore */ }
    setLoading(false);
  }, [m, jobType]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 5s when there are running/queued jobs
  useEffect(() => {
    if (!autoRefresh) return;
    const hasActive = runs.some(r => r.status === 'running' || r.status === 'queued');
    if (!hasActive) return;
    const interval = setInterval(async () => {
      const data = await fetchJobRuns({ market: m, jobType: jobType || undefined, pageSize: 50 });
      setRuns(data);
      if (selectedRun && (selectedRun.status === 'running' || selectedRun.status === 'queued')) {
        const updated = await fetchJobRun(selectedRun.id);
        setSelectedRun(updated);
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, runs, m, selectedRun, jobType]);

  const handleRowClick = async (run: JobRun) => {
    try {
      const detail = await fetchJobRun(run.id);
      setSelectedRun(detail);
    } catch {
      setSelectedRun(run);
    }
  };

  const handleCancel = async (runId: number) => {
    if (!confirm(`Cancel job run #${runId}?`)) return;
    try {
      await cancelJobRun(runId);
      await load();
      if (selectedRun?.id === runId) {
        const updated = await fetchJobRun(runId);
        setSelectedRun(updated);
      }
    } catch { /* ignore */ }
  };

  if (loading) return <div className="loading"><div className="spinner" />Loading jobs...</div>;

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <Activity size={24} style={{ marginRight: 8 }} />
          Job Runs
        </h1>
        <span className="page-subtitle">{market === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <div className="header-actions">
          <select
            className="select-input"
            value={jobType}
            onChange={e => setJobType(e.target.value)}
            title="Filter by job type"
          >
            {JOB_TYPES.map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} />
            Auto-refresh
          </label>
          <button className="btn btn-outline btn-sm" onClick={load}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Summary stats */}
      <div className="stats-row">
        <div className="stat-card">
          <div className="stat-value">{runs.length}</div>
          <div className="stat-label">Total Runs</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--success)' }}>
            {runs.filter(r => r.status === 'completed').length}
          </div>
          <div className="stat-label">Completed</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--warning)' }}>
            {runs.filter(r => r.status === 'running').length}
          </div>
          <div className="stat-label">Running</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--danger)' }}>
            {runs.filter(r => r.status === 'failed').length}
          </div>
          <div className="stat-label">Failed</div>
        </div>
      </div>

      <div className="jobs-layout">
        {/* Jobs table */}
        <div className="table-container" style={{ flex: 1 }}>
          {runs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📋</div>
              <p className="empty-state-text">No job runs yet. Trigger an analysis to get started.</p>
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map(run => (
                  <tr
                    key={run.id}
                    onClick={() => handleRowClick(run)}
                    style={{ cursor: 'pointer' }}
                    className={selectedRun?.id === run.id ? 'row-selected' : ''}
                  >
                    <td style={{ fontWeight: 600 }}>#{run.id}</td>
                    <td>{formatJobType(run.jobType)}</td>
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
                    <td className="cell-muted">{run.startedAt ? formatDate(run.startedAt) : '—'}</td>
                    <td className="cell-muted">{formatDuration(run.durationSeconds)}</td>
                    <td>
                      {(run.status === 'running' || run.status === 'queued') && (
                        <button
                          className="btn btn-outline btn-sm"
                          style={{ color: 'var(--danger)', borderColor: 'var(--danger)', padding: '2px 8px' }}
                          onClick={e => { e.stopPropagation(); handleCancel(run.id); }}
                          title="Cancel this job"
                        >
                          <StopCircle size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Run detail panel */}
        {selectedRun && (
          <div className="run-detail-panel">
            <div className="run-detail-header">
              <h3>Run #{selectedRun.id}</h3>
              <span className={`status-badge ${STATUS_CLASS[selectedRun.status]}`}>
                {STATUS_ICON[selectedRun.status]} {selectedRun.status}
              </span>
              {(selectedRun.status === 'running' || selectedRun.status === 'queued') && (
                <button
                  className="btn btn-outline btn-sm"
                  style={{ color: 'var(--danger)', borderColor: 'var(--danger)', marginLeft: 'auto' }}
                  onClick={() => handleCancel(selectedRun.id)}
                >
                  <StopCircle size={14} /> Cancel
                </button>
              )}
            </div>

            <div className="run-detail-body">
              <div className="detail-row">
                <span className="detail-label">Job Type</span>
                <span>{formatJobType(selectedRun.jobType)}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Market</span>
                <span>{selectedRun.market === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Created</span>
                <span>{formatDate(selectedRun.createdAt)}</span>
              </div>
              {selectedRun.startedAt && (
                <div className="detail-row">
                  <span className="detail-label">Started</span>
                  <span>{formatDate(selectedRun.startedAt)}</span>
                </div>
              )}
              {selectedRun.completedAt && (
                <div className="detail-row">
                  <span className="detail-label">Completed</span>
                  <span>{formatDate(selectedRun.completedAt)}</span>
                </div>
              )}
              {selectedRun.durationSeconds != null && (
                <div className="detail-row">
                  <span className="detail-label">Duration</span>
                  <span>{formatDuration(selectedRun.durationSeconds)}</span>
                </div>
              )}

              {/* Progress */}
              <div className="detail-section">
                <h4>Progress</h4>
                <div className="progress-bar-lg">
                  <div
                    className={`progress-bar-fill-lg ${selectedRun.status === 'failed' ? 'progress-failed' : ''}`}
                    style={{ width: `${selectedRun.progress}%` }}
                  />
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{selectedRun.progress}%</span>
              </div>

              {/* Parameters */}
              {selectedRun.parameters && Object.keys(selectedRun.parameters).length > 0 && (
                <div className="detail-section">
                  <h4>Parameters</h4>
                  {Object.entries(selectedRun.parameters).map(([k, v]) => (
                    <div className="detail-row" key={k}>
                      <span className="detail-label">{k}</span>
                      <span>{String(v)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Metrics */}
              {selectedRun.metrics && Object.keys(selectedRun.metrics).length > 0 && (
                <div className="detail-section">
                  <h4>Metrics</h4>
                  <div className="metrics-grid">
                    {flattenMetrics(selectedRun.metrics).map(({ label, value }) => {
                      const isLong = value.length > 60;
                      return (
                        <div className="metric-item" key={label}>
                          <div className={`metric-value${isLong ? ' metric-value-clamp' : ''}`}>{value}</div>
                          {isLong && (
                            <button className="metric-expand" onClick={() => setMetricPopup({ label, value })}>
                              <Maximize2 size={11} /> View full
                            </button>
                          )}
                          <div className="metric-label">{label}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Error */}
              {selectedRun.errorMessage && (
                <div className="detail-section">
                  <h4 style={{ color: 'var(--danger)' }}>Error</h4>
                  <pre className="error-box">{selectedRun.errorMessage}</pre>
                </div>
              )}

              {/* Link to results */}
              {selectedRun.status === 'completed' && selectedRun.jobType === 'stage2_analysis' && (
                <button
                  className="btn btn-primary"
                  style={{ marginTop: 16, width: '100%' }}
                  onClick={() => navigate(`/${selectedRun.market}/analysis`)}
                >
                  View Analysis Results →
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Full-text metric popup */}
      {metricPopup && (
        <div className="modal-overlay" onClick={() => setMetricPopup(null)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 720, width: '92vw' }}>
            <div className="modal-header">
              <h3 className="modal-title" style={{ textTransform: 'capitalize' }}>{metricPopup.label}</h3>
              <button className="modal-close" onClick={() => setMetricPopup(null)}><X size={18} /></button>
            </div>
            <div className="modal-body">
              <pre className="error-box" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{metricPopup.value}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
