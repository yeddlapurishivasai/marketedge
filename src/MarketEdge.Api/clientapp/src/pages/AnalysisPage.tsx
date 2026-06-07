import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, Sector, Stage2Summary, SectorRotation, Stage2History, StageAnalysisResult } from '../api';
import {
  fetchSectors, fetchStage2Summary, fetchSectorRotation, fetchStage2History,
  fetchJobRuns, triggerAnalysis, fetchStage2Stocks
} from '../api';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line, ScatterChart, Scatter, ZAxis, Cell
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

  const scatterData = rotation.map(r => ({
    x: Number(r.avgRSScore),
    y: Number(r.avgRSDelta2w),
    z: r.stockCount,
    name: r.sectorName,
    quadrant: r.quadrant,
    accum: r.accumulatingCount,
    dist: r.distributingCount
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
              <div className="quadrant-labels">
                <span className="ql-improving">↗ Improving</span>
                <span className="ql-leading">⬆ Leading</span>
                <span className="ql-lagging">↙ Lagging</span>
                <span className="ql-weakening">↘ Weakening</span>
              </div>
              <ResponsiveContainer width="100%" height={500}>
                <ScatterChart margin={{ top: 20, right: 40, bottom: 40, left: 40 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis
                    type="number" dataKey="x" name="RS Score"
                    stroke="var(--text-muted)" label={{ value: 'Relative Strength →', position: 'bottom', offset: 20, fill: 'var(--text-secondary)' }}
                  />
                  <YAxis
                    type="number" dataKey="y" name="RS Momentum"
                    stroke="var(--text-muted)" label={{ value: 'Momentum →', angle: -90, position: 'left', offset: 10, fill: 'var(--text-secondary)' }}
                  />
                  <ZAxis type="number" dataKey="z" range={[100, 1000]} name="Stocks" />
                  <Tooltip
                    contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }}
                    content={({ payload }) => {
                      if (!payload?.length) return null;
                      const d = payload[0].payload;
                      return (
                        <div style={{ padding: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }}>
                          <strong>{d.name}</strong>
                          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                            <div>RS: {d.x.toFixed(2)} | Mom: {d.y.toFixed(2)}</div>
                            <div>Stocks: {d.z} | <span style={{ color: QUADRANT_COLORS[d.quadrant] }}>{d.quadrant}</span></div>
                            <div>Accumulating: {d.accum} | Distributing: {d.dist}</div>
                          </div>
                        </div>
                      );
                    }}
                  />
                  <Scatter data={scatterData}>
                    {scatterData.map((entry, idx) => (
                      <Cell key={idx} fill={QUADRANT_COLORS[entry.quadrant] || '#888'} fillOpacity={0.7} />
                    ))}
                  </Scatter>
                  {/* Reference lines at 0 */}
                  <CartesianGrid />
                </ScatterChart>
              </ResponsiveContainer>

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
