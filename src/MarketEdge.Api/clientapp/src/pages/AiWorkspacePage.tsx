import { useCallback, useDeferredValue, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BrainCircuit, ChevronLeft, Eye, Loader2, RefreshCw, Search, X } from 'lucide-react';
import type { AiInvestmentAnalysisDetail, AiInvestmentAnalysisHistoryItem, Market } from '../api';
import { fetchAiAnalysisDetail, fetchAiAnalysisHistory } from '../api';

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function AnalysisModal({ detail, onClose }: {
  detail: AiInvestmentAnalysisDetail;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal ai-report-modal" onClick={event => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3 className="modal-title">{detail.metadata.ticker} AI Analysis</h3>
            <span className="cell-muted">{detail.metadata.weekNumber} · {detail.metadata.model}</span>
          </div>
          <button className="modal-close" onClick={onClose} title="Close"><X size={18} /></button>
        </div>
        <div className="modal-body ai-report-body">
          <div className="ai-report-costs">
            <span>Input {formatTokens(detail.metadata.cost.inputTokens)}</span>
            <span>Output {formatTokens(detail.metadata.cost.outputTokens)}</span>
            <span>${detail.metadata.cost.totalCostUsd.toFixed(4)}</span>
            <span>${detail.metadata.cost.projected1000TickersCostUsd.toFixed(2)} / 1,000</span>
          </div>
          <pre className="ai-report-json">{JSON.stringify(detail.analysis, null, 2)}</pre>
        </div>
      </div>
    </div>
  );
}

export default function AiWorkspacePage() {
  const { market } = useParams<{ market: string }>();
  const navigate = useNavigate();
  const currentMarket = market as Market;
  const [rows, setRows] = useState<AiInvestmentAnalysisHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query.trim().toUpperCase());
  const [selected, setSelected] = useState<AiInvestmentAnalysisDetail | null>(null);
  const [openingId, setOpeningId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await fetchAiAnalysisHistory(currentMarket));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load AI analyses');
    } finally {
      setLoading(false);
    }
  }, [currentMarket]);

  useEffect(() => { void load(); }, [load]);

  const openReport = async (row: AiInvestmentAnalysisHistoryItem) => {
    setOpeningId(row.id);
    setError(null);
    try {
      setSelected(await fetchAiAnalysisDetail(currentMarket, row.ticker, row.weekNumber));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load AI analysis report');
    } finally {
      setOpeningId(null);
    }
  };

  const filtered = deferredQuery
    ? rows.filter(row => row.ticker.includes(deferredQuery) || row.weekNumber.includes(deferredQuery))
    : rows;
  const uniqueStocks = new Set(rows.map(row => row.ticker)).size;
  const totalCost = rows.reduce((sum, row) => sum + row.cost.totalCostUsd, 0);

  return (
    <div className="page">
      <div className="page-header">
        <button className="back-link" onClick={() => navigate(`/${currentMarket}`)}>
          <ChevronLeft size={16} /> Back
        </button>
        <h1 className="page-title"><BrainCircuit size={24} /> AI Workspace</h1>
        <span className="page-subtitle">{currentMarket === 'india' ? 'India' : 'US'}</span>
        <div className="header-actions">
          <div className="ai-search">
            <Search size={15} />
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Ticker or week" />
          </div>
          <button className="btn btn-outline btn-sm" onClick={load} disabled={loading}>
            {loading ? <Loader2 size={14} className="spin-icon" /> : <RefreshCw size={14} />} Refresh
          </button>
        </div>
      </div>

      <div className="analysis-tabs" role="tablist">
        <button className="analysis-tab active" role="tab" aria-selected="true">Stock Analysis</button>
      </div>

      <div className="ai-workspace-summary">
        <span><strong>{uniqueStocks}</strong> stocks</span>
        <span><strong>{rows.length}</strong> weekly analyses</span>
        <span><strong>${totalCost.toFixed(4)}</strong> recorded cost</span>
      </div>

      {error && <div className="lookup-error">{error}</div>}
      {loading && rows.length === 0 ? (
        <div className="loading"><div className="spinner" />Loading AI analyses...</div>
      ) : (
        <div className="table-container ai-analysis-table">
          {filtered.length === 0 ? (
            <div className="empty-state"><p className="empty-state-text">No AI analyses found.</p></div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Week</th>
                  <th>Model</th>
                  <th>Input Tokens</th>
                  <th>Output Tokens</th>
                  <th>Cost</th>
                  <th>1,000 Tickers</th>
                  <th>Updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {filtered.map(row => (
                  <tr key={row.id}>
                    <td>
                      <button className="symbol-link" onClick={() => navigate(`/${currentMarket}/lookup/${row.ticker}`)}>
                        {row.ticker}
                      </button>
                    </td>
                    <td>{row.weekNumber}</td>
                    <td>{row.model}</td>
                    <td>{formatTokens(row.cost.inputTokens)}</td>
                    <td>{formatTokens(row.cost.outputTokens)}</td>
                    <td>${row.cost.totalCostUsd.toFixed(4)}</td>
                    <td>${row.cost.projected1000TickersCostUsd.toFixed(2)}</td>
                    <td>{formatDate(row.updatedAt)}</td>
                    <td>
                      <button className="btn btn-ghost btn-sm" onClick={() => void openReport(row)} title="Open report">
                        {openingId === row.id ? <Loader2 size={15} className="spin-icon" /> : <Eye size={15} />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {selected && <AnalysisModal detail={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}