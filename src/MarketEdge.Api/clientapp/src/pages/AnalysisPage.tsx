import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, Sector, Stage2Summary, SectorRotation, SectorRotationHistory, Stage2History, StageAnalysisResult } from '../api';
import {
  fetchSectors, fetchStage2Summary, fetchSectorRotation, fetchStage2History,
  fetchJobRuns, triggerAnalysis, fetchStage2Stocks, fetchRotationHistory
} from '../api';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line
} from 'recharts';
import {
  ChevronLeft, Play, TrendingUp, Target, Zap, ArrowDownRight,
  ArrowUpRight, Minus, Filter
} from 'lucide-react';

const QUADRANT_COLORS: Record<string, string> = {
  leading: '#22c55e',
  improving: '#3b82f6',
  weakening: '#f59e0b',
  lagging: '#ef4444'
};

function formatMarketCap(value?: number): string {
  if (!value) return '—';
  if (value >= 1e12) return `${(value / 1e12).toFixed(1)}T`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e7) return `${(value / 1e7).toFixed(1)}Cr`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  return value.toFixed(0);
}

// Custom SVG Quadrant Chart with colored backgrounds and sector labels
function QuadrantChart({ data, currentDate }: { data: SectorRotation[]; currentDate?: string }) {
  const W = 900, H = 600;
  const pad = { top: 30, right: 30, bottom: 50, left: 60 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  if (!data.length) return <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>No rotation data available</div>;

  // Compute bounds symmetrically around 0
  const xs = data.map(d => Number(d.avgRSScore));
  const ys = data.map(d => Number(d.avgRSDelta2w));
  const xMax = Math.max(Math.abs(Math.min(...xs)), Math.abs(Math.max(...xs)), 5) * 1.2;
  const yMax = Math.max(Math.abs(Math.min(...ys)), Math.abs(Math.max(...ys)), 5) * 1.2;

  const toX = (v: number) => pad.left + ((v + xMax) / (2 * xMax)) * cw;
  const toY = (v: number) => pad.top + ((yMax - v) / (2 * yMax)) * ch;

  const cx = toX(0), cy = toY(0);

  const [hovered, setHovered] = useState<string | null>(null);

  return (
    <div style={{ position: 'relative' }}>
      {currentDate && (
        <div style={{ position: 'absolute', top: 8, right: 12, fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 500 }}>
          {new Date(currentDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}
        </div>
      )}
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} style={{ fontFamily: 'inherit' }}>
        {/* Quadrant backgrounds */}
        <rect x={pad.left} y={pad.top} width={cx - pad.left} height={cy - pad.top} fill="#dcfce7" opacity={0.5} /> {/* Improving: top-left */}
        <rect x={cx} y={pad.top} width={pad.left + cw - cx} height={cy - pad.top} fill="#bbf7d0" opacity={0.5} /> {/* Leading: top-right */}
        <rect x={pad.left} y={cy} width={cx - pad.left} height={pad.top + ch - cy} fill="#fce4ec" opacity={0.5} /> {/* Lagging: bottom-left */}
        <rect x={cx} y={cy} width={pad.left + cw - cx} height={pad.top + ch - cy} fill="#fef9c3" opacity={0.5} /> {/* Weakening: bottom-right */}

        {/* Quadrant labels */}
        <text x={pad.left + (cx - pad.left) / 2} y={pad.top + (cy - pad.top) / 2} textAnchor="middle" fontSize="28" fill="#22c55e" opacity={0.2} fontWeight="bold">Improving</text>
        <text x={cx + (pad.left + cw - cx) / 2} y={pad.top + (cy - pad.top) / 2} textAnchor="middle" fontSize="28" fill="#16a34a" opacity={0.2} fontWeight="bold">Leading</text>
        <text x={pad.left + (cx - pad.left) / 2} y={cy + (pad.top + ch - cy) / 2} textAnchor="middle" fontSize="28" fill="#ef4444" opacity={0.2} fontWeight="bold">Lagging</text>
        <text x={cx + (pad.left + cw - cx) / 2} y={cy + (pad.top + ch - cy) / 2} textAnchor="middle" fontSize="28" fill="#f59e0b" opacity={0.2} fontWeight="bold">Weakening</text>

        {/* Axes */}
        <line x1={pad.left} y1={cy} x2={pad.left + cw} y2={cy} stroke="var(--border)" strokeWidth={1.5} strokeDasharray="4 2" />
        <line x1={cx} y1={pad.top} x2={cx} y2={pad.top + ch} stroke="var(--border)" strokeWidth={1.5} strokeDasharray="4 2" />

        {/* Axis labels */}
        <text x={pad.left + cw} y={cy - 6} textAnchor="end" fontSize="11" fill="var(--text-muted)">Relative Strength →</text>
        <text x={cx + 6} y={pad.top + 14} textAnchor="start" fontSize="11" fill="var(--text-muted)">↑ Momentum</text>

        {/* Data points with labels */}
        {data.map((d) => {
          const px = toX(Number(d.avgRSScore));
          const py = toY(Number(d.avgRSDelta2w));
          const isHov = hovered === d.sectorName;
          const color = QUADRANT_COLORS[d.quadrant] || '#888';
          return (
            <g key={d.sectorId} onMouseEnter={() => setHovered(d.sectorName)} onMouseLeave={() => setHovered(null)} style={{ cursor: 'pointer' }}>
              <circle cx={px} cy={py} r={isHov ? 7 : 5} fill={color} stroke="#fff" strokeWidth={1.5} opacity={isHov ? 1 : 0.8} />
              <text x={px + 8} y={py + 4} fontSize={isHov ? 11 : 9} fill="var(--text)" fontWeight={isHov ? 600 : 400} opacity={0.9}>
                {d.sectorName.length > 25 ? d.sectorName.slice(0, 22) + '…' : d.sectorName}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Tooltip on hover */}
      {hovered && (() => {
        const d = data.find(x => x.sectorName === hovered);
        if (!d) return null;
        return (
          <div style={{
            position: 'absolute', top: 40, left: 70,
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
            padding: '10px 14px', fontSize: '0.8rem', boxShadow: '0 4px 12px rgba(0,0,0,0.15)', zIndex: 10
          }}>
            <strong>{d.sectorName}</strong>
            <div style={{ color: 'var(--text-secondary)', marginTop: 4 }}>
              RS: {Number(d.avgRSScore).toFixed(2)} | Mom: {Number(d.avgRSDelta2w).toFixed(2)}
            </div>
            <div style={{ color: 'var(--text-secondary)' }}>
              Stocks: {d.stockCount} | <span style={{ color: QUADRANT_COLORS[d.quadrant] }}>{d.quadrant}</span>
            </div>
            <div style={{ color: 'var(--text-secondary)' }}>
              Accum: {d.accumulatingCount} | Dist: {d.distributingCount}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

export default function AnalysisPage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const m = market as Market;

  const [summary, setSummary] = useState<Stage2Summary | null>(null);
  const [rotation, setRotation] = useState<SectorRotation[]>([]);
  const [history, setHistory] = useState<Stage2History[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'overview' | 'top25' | 'sectors' | 'rotation' | 'stocks'>('overview');
  const [triggering, setTriggering] = useState(false);
  const [showTriggerModal, setShowTriggerModal] = useState(false);
  const [minMcap, setMinMcap] = useState('');
  const [maxMcap, setMaxMcap] = useState('');
  const [limitVal, setLimitVal] = useState('');
  const [selectedSectorIds, setSelectedSectorIds] = useState<number[]>([]);
  const [allSectors, setAllSectors] = useState<Sector[]>([]);
  const [latestRunId, setLatestRunId] = useState<number | null>(null);
  const [stocks, setStocks] = useState<StageAnalysisResult[]>([]);
  const [classFilter, setClassFilter] = useState('');
  const [stocksLoading, setStocksLoading] = useState(false);
  const [rotationHistory, setRotationHistory] = useState<SectorRotationHistory[]>([]);
  const [timelineIdx, setTimelineIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, h, runs, sectors] = await Promise.all([
        fetchStage2Summary(m).catch(() => null),
        fetchStage2History(m).catch(() => []),
        fetchJobRuns({ market: m, jobType: 'stage2_analysis', pageSize: 1 }).catch(() => []),
        fetchSectors(m).catch(() => [])
      ]);
      setSummary(s);
      setHistory(h);
      setAllSectors(sectors);
      if (runs.length > 0 && runs[0].status === 'completed') {
        setLatestRunId(runs[0].id);
        const rot = await fetchSectorRotation(m, runs[0].id).catch(() => []);
        setRotation(rot);
        const rotHist = await fetchRotationHistory(m, 12).catch(() => []);
        setRotationHistory(rotHist);
        if (rotHist.length > 0) setTimelineIdx(rotHist.length - 1);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      const req: { minMarketCap?: number; maxMarketCap?: number; sectorIds?: number[]; limit?: number } = {};
      if (minMcap) req.minMarketCap = parseFloat(minMcap);
      if (maxMcap) req.maxMarketCap = parseFloat(maxMcap);
      if (selectedSectorIds.length > 0) req.sectorIds = selectedSectorIds;
      if (limitVal) req.limit = parseInt(limitVal);
      await triggerAnalysis(m, req);
      setShowTriggerModal(false);
      setMinMcap('');
      setMaxMcap('');
      setLimitVal('');
      setSelectedSectorIds([]);
      navigate(`/${m}/jobs`);
    } catch { /* ignore */ }
    setTriggering(false);
  };

  const loadStocks = useCallback(async (classification?: string) => {
    if (!latestRunId) return;
    setStocksLoading(true);
    try {
      const data = await fetchStage2Stocks(m, latestRunId, { classification: classification || undefined });
      setStocks(data);
    } catch { /* ignore */ }
    setStocksLoading(false);
  }, [m, latestRunId]);

  useEffect(() => {
    if (tab === 'stocks' && latestRunId) loadStocks(classFilter);
  }, [tab, classFilter, latestRunId, loadStocks]);

  // Timeline animation
  useEffect(() => {
    if (!isPlaying || rotationHistory.length <= 1) return;
    const interval = setInterval(() => {
      setTimelineIdx(prev => {
        if (prev >= rotationHistory.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, 1500);
    return () => clearInterval(interval);
  }, [isPlaying, rotationHistory.length]);

  if (loading) return <div className="loading"><div className="spinner" />Loading analysis...</div>;

  const barData = summary?.bySector.slice(0, 20).map(s => ({
    name: s.sectorName.length > 25 ? s.sectorName.slice(0, 22) + '...' : s.sectorName,
    stage2: s.stage2Count,
    total: s.totalCount
  })) || [];

  const lineData = history.map(h => ({
    date: new Date(h.runDate).toLocaleDateString(),
    stage2: h.totalStage2
  }));

  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${m}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title">
          <TrendingUp size={24} style={{ marginRight: 8 }} />
          Stage 2 Analysis
        </h1>
        <span className="page-subtitle">{market === 'india' ? '🇮🇳 India' : '🇺🇸 US'}</span>
        <button
          className="btn btn-primary"
          style={{ marginLeft: 'auto' }}
          onClick={() => setShowTriggerModal(true)}
          disabled={triggering}
        >
          <Play size={14} /> Run Analysis
        </button>
      </div>

      {/* Trigger modal */}
      {showTriggerModal && (
        <div className="modal-overlay" onClick={() => setShowTriggerModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Run Stage 2 Analysis</h3>
              <button className="modal-close" onClick={() => setShowTriggerModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
                Configure filters for the analysis run. Leave fields blank for defaults.
              </p>

              {/* Sector Selection */}
              <div className="form-group">
                <label className="form-label">Sectors (select one or more, leave empty for all)</label>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                    onClick={() => setSelectedSectorIds(allSectors.map(s => s.id))}
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                    onClick={() => setSelectedSectorIds([])}
                  >
                    Deselect All
                  </button>
                </div>
                <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6, padding: 8 }}>
                  {allSectors.map(s => (
                    <label key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', cursor: 'pointer', fontSize: '0.85rem' }}>
                      <input
                        type="checkbox"
                        checked={selectedSectorIds.includes(s.id)}
                        onChange={e => {
                          if (e.target.checked) setSelectedSectorIds(prev => [...prev, s.id]);
                          else setSelectedSectorIds(prev => prev.filter(id => id !== s.id));
                        }}
                      />
                      {s.sectorName} <span style={{ color: 'var(--text-secondary)', marginLeft: 'auto' }}>({s.stockCount})</span>
                    </label>
                  ))}
                </div>
                {selectedSectorIds.length > 0 && (
                  <div style={{ marginTop: 4, fontSize: '0.8rem', color: 'var(--primary)' }}>
                    {selectedSectorIds.length} sector(s) selected
                    <button style={{ marginLeft: 8, fontSize: '0.75rem', color: 'var(--danger)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
                      onClick={() => setSelectedSectorIds([])}>Clear</button>
                  </div>
                )}
              </div>

              {/* Limit */}
              <div className="form-group">
                <label className="form-label">Stock Limit (max stocks to analyze)</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder="Leave blank for all stocks in selected sectors"
                  value={limitVal}
                  onChange={e => setLimitVal(e.target.value)}
                />
              </div>

              {/* Market Cap */}
              <div className="form-group">
                <label className="form-label">Min Market Cap ({m === 'india' ? '₹' : '$'})</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder={m === 'india' ? 'e.g., 50000000000 (₹5000 Cr)' : 'e.g., 10000000000 ($10B)'}
                  value={minMcap}
                  onChange={e => setMinMcap(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Max Market Cap ({m === 'india' ? '₹' : '$'})</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder="Leave blank for no upper limit"
                  value={maxMcap}
                  onChange={e => setMaxMcap(e.target.value)}
                />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-outline" onClick={() => setShowTriggerModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleTrigger} disabled={triggering}>
                {triggering ? 'Triggering...' : 'Start Analysis'}
              </button>
            </div>
          </div>
        </div>
      )}

      {!summary ? (
        <div className="empty-state">
          <div className="empty-state-icon">📊</div>
          <p className="empty-state-text">No completed analysis runs yet. Click "Run Analysis" to start.</p>
        </div>
      ) : (
        <>
          {/* Summary stats */}
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-value">{summary.stage2Count}</div>
              <div className="stat-label">Stage 2 Stocks</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--success)' }}>
                <ArrowUpRight size={18} style={{ display: 'inline' }} /> {summary.newAdditions}
              </div>
              <div className="stat-label">New Additions</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: '#a855f7' }}>
                <Zap size={18} style={{ display: 'inline' }} /> {summary.reEntries}
              </div>
              <div className="stat-label">Re-Entries</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--primary)' }}>
                <Minus size={18} style={{ display: 'inline' }} /> {summary.continuing}
              </div>
              <div className="stat-label">Continuing</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--danger)' }}>
                <ArrowDownRight size={18} style={{ display: 'inline' }} /> {summary.removed}
              </div>
              <div className="stat-label">Removed</div>
            </div>
          </div>

          {/* Tabs */}
          <div className="analysis-tabs">
            {(['overview', 'top25', 'sectors', 'rotation', 'stocks'] as const).map(t => (
              <button
                key={t}
                className={`analysis-tab ${tab === t ? 'active' : ''}`}
                onClick={() => setTab(t)}
              >
                {t === 'overview' && 'Overview'}
                {t === 'top25' && 'Top 25'}
                {t === 'sectors' && 'By Sector'}
                {t === 'rotation' && 'Sector Rotation'}
                {t === 'stocks' && 'All Stocks'}
              </button>
            ))}
          </div>

          {/* Overview tab */}
          {tab === 'overview' && (
            <div className="analysis-grid">
              {/* Bar chart — Stage 2 by sector */}
              <div className="chart-card">
                <h3 className="chart-title">Stage 2 Stocks by Sector (Top 20)</h3>
                <ResponsiveContainer width="100%" height={400}>
                  <BarChart data={barData} layout="vertical" margin={{ left: 120, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis type="number" stroke="var(--text-muted)" />
                    <YAxis dataKey="name" type="category" width={120} tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                    <Tooltip
                      contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }}
                    />
                    <Bar dataKey="stage2" fill="var(--primary)" name="Stage 2" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Line chart — Stage 2 over time */}
              {lineData.length > 1 && (
                <div className="chart-card">
                  <h3 className="chart-title">Stage 2 Count Over Time</h3>
                  <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={lineData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" stroke="var(--text-muted)" tick={{ fontSize: 11 }} />
                      <YAxis stroke="var(--text-muted)" />
                      <Tooltip
                        contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }}
                      />
                      <Line type="monotone" dataKey="stage2" stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}

          {/* Top 25 tab */}
          {tab === 'top25' && (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Symbol</th>
                    <th>Company</th>
                    <th>Sector</th>
                    <th>Price</th>
                    <th>RS Score</th>
                    <th>RS Rank</th>
                    <th>Momentum</th>
                    <th>Quadrant</th>
                    <th>A/D</th>
                    <th>Classification</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.top25.map((s, i) => (
                    <tr key={s.id}>
                      <td>{i + 1}</td>
                      <td className="cell-symbol">{s.symbol}</td>
                      <td>{s.companyName.length > 30 ? s.companyName.slice(0, 27) + '...' : s.companyName}</td>
                      <td className="cell-muted">{s.sectorName}</td>
                      <td>{s.closePrice?.toFixed(2)}</td>
                      <td style={{ color: (s.rsScore ?? 0) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                        {s.rsScore?.toFixed(2)}
                      </td>
                      <td>{s.rsRank}</td>
                      <td style={{ color: (s.momentumScore ?? 0) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                        {s.momentumScore?.toFixed(2)}
                      </td>
                      <td>
                        <span className={`quadrant-badge q-${s.quadrant}`}>{s.quadrant}</span>
                      </td>
                      <td>
                        <span className={`ad-badge ad-${s.adClassification}`}>{s.adClassification}</span>
                      </td>
                      <td>
                        <span className={`class-badge class-${s.classification}`}>{s.classification}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* By Sector tab */}
          {tab === 'sectors' && (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Sector</th>
                    <th>Stage 2</th>
                    <th>Total</th>
                    <th>% in Stage 2</th>
                    <th>Bar</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.bySector.map(s => (
                    <tr key={s.sectorName}>
                      <td style={{ fontWeight: 500 }}>{s.sectorName}</td>
                      <td style={{ fontWeight: 600, color: 'var(--primary)' }}>{s.stage2Count}</td>
                      <td className="cell-muted">{s.totalCount}</td>
                      <td>{s.totalCount > 0 ? ((s.stage2Count / s.totalCount) * 100).toFixed(1) : 0}%</td>
                      <td style={{ width: 200 }}>
                        <div className="sector-bar">
                          <div
                            className="sector-bar-fill"
                            style={{ width: `${s.totalCount > 0 ? (s.stage2Count / s.totalCount) * 100 : 0}%` }}
                          />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Sector Rotation tab */}
          {tab === 'rotation' && (
            <div className="chart-card">
              <h3 className="chart-title">
                <Target size={18} style={{ marginRight: 8 }} />
                Sector Rotation — Relative Strength vs Momentum
              </h3>

              {/* Quadrant chart with colored backgrounds */}
              <QuadrantChart
                data={rotationHistory.length > 0 ? (rotationHistory[timelineIdx]?.sectors || []) : rotation}
                currentDate={rotationHistory[timelineIdx]?.runDate}
              />

              {/* Timeline slider */}
              {rotationHistory.length > 1 && (
                <div style={{ margin: '16px 0', display: 'flex', alignItems: 'center', gap: 12 }}>
                  <button
                    onClick={() => {
                      if (isPlaying) {
                        setIsPlaying(false);
                      } else {
                        setIsPlaying(true);
                        setTimelineIdx(0);
                      }
                    }}
                    style={{ background: 'var(--surface-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', color: 'var(--text)' }}
                  >
                    {isPlaying ? '⏸' : '▶'}
                  </button>
                  <input
                    type="range"
                    min={0}
                    max={rotationHistory.length - 1}
                    value={timelineIdx}
                    onChange={e => { setTimelineIdx(Number(e.target.value)); setIsPlaying(false); }}
                    style={{ flex: 1 }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', minWidth: 220, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {rotationHistory.map((rh, i) => (
                      <span key={i} style={{ fontWeight: i === timelineIdx ? 700 : 400, color: i === timelineIdx ? 'var(--primary)' : 'var(--text-muted)' }}>
                        {i === 0 || i === rotationHistory.length - 1 || i === timelineIdx
                          ? new Date(rh.runDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
                          : ''}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Sector rotation table */}
              <div className="table-container" style={{ marginTop: 24 }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Sector</th>
                      <th>Quadrant</th>
                      <th>Avg RS</th>
                      <th>Avg Momentum</th>
                      <th>Stocks</th>
                      <th>Accumulating</th>
                      <th>Distributing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rotation.map(r => (
                      <tr key={r.sectorId}>
                        <td style={{ fontWeight: 500 }}>{r.sectorName}</td>
                        <td>
                          <span className={`quadrant-badge q-${r.quadrant}`}>{r.quadrant}</span>
                        </td>
                        <td style={{ color: Number(r.avgRSScore) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                          {Number(r.avgRSScore).toFixed(2)}
                        </td>
                        <td style={{ color: Number(r.avgRSDelta2w) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                          {Number(r.avgRSDelta2w).toFixed(2)}
                        </td>
                        <td>{r.stockCount}</td>
                        <td style={{ color: 'var(--success)' }}>{r.accumulatingCount}</td>
                        <td style={{ color: 'var(--danger)' }}>{r.distributingCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* All Stocks tab */}
          {tab === 'stocks' && (
            <>
              <div className="toolbar">
                <Filter size={16} style={{ color: 'var(--text-muted)' }} />
                <select
                  className="select-input"
                  value={classFilter}
                  onChange={e => setClassFilter(e.target.value)}
                >
                  <option value="">All Stage 2</option>
                  <option value="continuing">Continuing</option>
                  <option value="new">New Additions</option>
                  <option value="reentry">Re-Entries</option>
                  <option value="removed">Removed</option>
                </select>
              </div>

              {stocksLoading ? (
                <div className="loading"><div className="spinner" />Loading stocks...</div>
              ) : (
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Company</th>
                        <th>Sector</th>
                        <th>Price</th>
                        <th>Mkt Cap</th>
                        <th>RS</th>
                        <th>Rank</th>
                        <th>Momentum</th>
                        <th>Quadrant</th>
                        <th>A/D</th>
                        <th>Class</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stocks.map(s => (
                        <tr key={s.id}>
                          <td className="cell-symbol">{s.symbol}</td>
                          <td>{s.companyName.length > 25 ? s.companyName.slice(0, 22) + '...' : s.companyName}</td>
                          <td className="cell-muted">{s.sectorName}</td>
                          <td>{s.closePrice?.toFixed(2)}</td>
                          <td className="cell-muted">{formatMarketCap(s.marketCap ?? undefined)}</td>
                          <td style={{ color: (s.rsScore ?? 0) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                            {s.rsScore?.toFixed(2)}
                          </td>
                          <td>{s.rsRank}</td>
                          <td style={{ color: (s.momentumScore ?? 0) > 0 ? 'var(--success)' : 'var(--danger)' }}>
                            {s.momentumScore?.toFixed(2)}
                          </td>
                          <td><span className={`quadrant-badge q-${s.quadrant}`}>{s.quadrant}</span></td>
                          <td><span className={`ad-badge ad-${s.adClassification}`}>{s.adClassification}</span></td>
                          <td><span className={`class-badge class-${s.classification}`}>{s.classification}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                    Showing {stocks.length} stocks
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
