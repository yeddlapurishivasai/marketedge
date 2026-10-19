import { useCallback, useDeferredValue, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowDown, ArrowUp, ArrowUpDown, BrainCircuit, ChevronLeft,
  Eye, Loader2, Play, RefreshCw, Search, X
} from 'lucide-react';
import type { AiInvestmentAnalysisDetail, AiInvestmentAnalysisHistoryItem, JobRun, Market } from '../api';
import { fetchAiAnalysisDetail, fetchAiAnalysisHistory, fetchJobRun, triggerAiAnalysisBatch } from '../api';

interface AiPriceCase { target?: number | null; returnPct?: number | null }
interface AiPriceScenario {
  horizon: string;
  bear?: AiPriceCase;
  base?: AiPriceCase;
  bull?: AiPriceCase;
  confidence?: string;
}
interface AiReport {
  valuation?: {
    assessment?: string;
    methods?: Array<string | Record<string, unknown>>;
    assumptions?: string[];
    intrinsicValueLow?: number | null;
    intrinsicValueHigh?: number | null;
    currency?: string | null;
    confidence?: string;
  };
  fundamentalAnalysis?: {
    performance?: string;
    growth?: string;
    cashFlowAndBalanceSheet?: string;
    competitivePosition?: string;
    risks?: string[];
  };
  priceScenarios?: AiPriceScenario[];
  earningsTriggers?: Array<{
    trigger: string;
    assessment?: string;
    evidence?: string[];
    confidence?: string;
  }>;
  actions?: {
    shortTerm?: { action?: string; entryRange?: unknown; riskControls?: string[] };
    longTerm?: { action?: string; entryRange?: unknown; thesis?: string };
  };
  materialConclusions?: Array<string | {
    type?: string;
    label?: string;
    conclusion?: string;
    statement?: string;
    confidence?: string;
  }>;
  sources?: Array<{ url?: string; title?: string; publicationDate?: string; publisher?: string }>;
}

type SortKey = 'ticker' | 'weekNumber' | 'valuation' | 'baseReturn3mPct'
  | 'baseReturn6mPct' | 'baseReturn1yPct' | 'baseReturn2yPct'
  | 'shortTermAction' | 'longTermAction' | 'updatedAt';
type SortDirection = 'asc' | 'desc';

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function formatPercent(value?: number | null): string {
  if (value == null) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
}

function label(value?: string | null): string {
  return value ? value.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase()) : 'Unavailable';
}

function methodText(value: string | Record<string, unknown>): string {
  if (typeof value === 'string') return value;
  return String(value.statement ?? value.method ?? JSON.stringify(value));
}

function conclusionText(value: NonNullable<AiReport['materialConclusions']>[number]): string {
  if (typeof value === 'string') return value;
  return value.conclusion ?? value.statement ?? 'Unavailable';
}

function AnalysisModal({ detail, onClose }: {
  detail: AiInvestmentAnalysisDetail;
  onClose: () => void;
}) {
  const [view, setView] = useState<'summary' | 'json'>('summary');
  const report = detail.analysis as AiReport;
  const valuation = report.valuation;
  const scenarios = report.priceScenarios ?? [];
  const triggers = report.earningsTriggers ?? [];
  const conclusions = report.materialConclusions ?? [];
  const sources = report.sources ?? [];

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
          <div className="analysis-tabs ai-report-tabs" role="tablist">
            <button className={`analysis-tab ${view === 'summary' ? 'active' : ''}`} onClick={() => setView('summary')}>Summary</button>
            <button className={`analysis-tab ${view === 'json' ? 'active' : ''}`} onClick={() => setView('json')}>JSON</button>
          </div>
          <div className="ai-report-costs">
            <span>Input {formatTokens(detail.metadata.cost.inputTokens)}</span>
            <span>Output {formatTokens(detail.metadata.cost.outputTokens)}</span>
            <span>${detail.metadata.cost.totalCostUsd.toFixed(4)}</span>
            <span>${detail.metadata.cost.projected1000TickersCostUsd.toFixed(2)} / 1,000</span>
          </div>
          {view === 'json' ? (
            <pre className="ai-report-json">{JSON.stringify(detail.analysis, null, 2)}</pre>
          ) : (
            <div className="ai-report-summary">
              <div className="ai-decision-grid">
                <div><span>Valuation</span><strong className={`ai-tone-${valuationTone(valuation?.assessment)}`}>{label(valuation?.assessment)}</strong></div>
                <div><span>Short Term</span><strong>{label(report.actions?.shortTerm?.action)}</strong></div>
                <div><span>Long Term</span><strong>{label(report.actions?.longTerm?.action)}</strong></div>
              </div>

              <section className="ai-report-section">
                <h4>Fundamental Summary</h4>
                <dl className="ai-summary-list">
                  <div><dt>Performance</dt><dd>{report.fundamentalAnalysis?.performance ?? 'Unavailable'}</dd></div>
                  <div><dt>Growth</dt><dd>{report.fundamentalAnalysis?.growth ?? 'Unavailable'}</dd></div>
                  <div><dt>Cash Flow &amp; Balance Sheet</dt><dd>{report.fundamentalAnalysis?.cashFlowAndBalanceSheet ?? 'Unavailable'}</dd></div>
                  <div><dt>Competitive Position</dt><dd>{report.fundamentalAnalysis?.competitivePosition ?? 'Unavailable'}</dd></div>
                </dl>
              </section>

              <section className="ai-report-section">
                <div className="ai-section-heading">
                  <h4>Valuation</h4>
                  <span>{valuation?.currency ?? ''} {valuation?.intrinsicValueLow ?? '—'} – {valuation?.intrinsicValueHigh ?? '—'} · {label(valuation?.confidence)} confidence</span>
                </div>
                {(valuation?.methods ?? []).map((method, index) => <p key={index}>{methodText(method)}</p>)}
                {(valuation?.assumptions?.length ?? 0) > 0 && (
                  <ul>{valuation!.assumptions!.map((item, index) => <li key={index}>{item}</li>)}</ul>
                )}
              </section>

              <section className="ai-report-section">
                <h4>Upside Projections</h4>
                <div className="table-container">
                  <table className="table ai-projection-table">
                    <thead><tr><th>Horizon</th><th>Bear</th><th>Base</th><th>Bull</th><th>Confidence</th></tr></thead>
                    <tbody>{scenarios.map(scenario => (
                      <tr key={scenario.horizon}>
                        <td>{scenario.horizon.toUpperCase()}</td>
                        <td className={`ai-tone-${predictionTone(scenario.bear?.returnPct)}`}>{scenario.bear?.target ?? '—'} <span>{formatPercent(scenario.bear?.returnPct)}</span></td>
                        <td className={`ai-tone-${predictionTone(scenario.base?.returnPct)}`}>{scenario.base?.target ?? '—'} <span>{formatPercent(scenario.base?.returnPct)}</span></td>
                        <td className={`ai-tone-${predictionTone(scenario.bull?.returnPct)}`}>{scenario.bull?.target ?? '—'} <span>{formatPercent(scenario.bull?.returnPct)}</span></td>
                        <td>{label(scenario.confidence)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>

              <section className="ai-report-section">
                <h4>Earnings Triggers</h4>
                <div className="ai-trigger-list">
                  {triggers.map(trigger => (
                    <div key={trigger.trigger}>
                      <div className="ai-trigger-head">
                        <strong>{trigger.trigger}</strong>
                        <span className={`ai-signal ai-signal-${trigger.assessment ?? 'unavailable'}`}>{label(trigger.assessment)}</span>
                      </div>
                      {trigger.evidence?.map((evidence, index) => <p key={index}>{evidence}</p>)}
                    </div>
                  ))}
                </div>
              </section>

              <section className="ai-report-section">
                <h4>Investment Actions</h4>
                <div className="ai-action-columns">
                  <div>
                    <strong>Short Term · {label(report.actions?.shortTerm?.action)}</strong>
                    <ul>{report.actions?.shortTerm?.riskControls?.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  </div>
                  <div>
                    <strong>Long Term · {label(report.actions?.longTerm?.action)}</strong>
                    <p>{report.actions?.longTerm?.thesis ?? 'Unavailable'}</p>
                  </div>
                </div>
              </section>

              <section className="ai-report-section">
                <h4>Material Conclusions</h4>
                <ul>{conclusions.map((item, index) => <li key={index}>{conclusionText(item)}</li>)}</ul>
              </section>

              <section className="ai-report-section">
                <h4>Sources</h4>
                <ul className="ai-source-list">{sources.map((source, index) => (
                  <li key={index}>
                    {source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title ?? source.url}</a> : source.title ?? 'Unknown source'}
                    {source.publicationDate && <span>{source.publicationDate}</span>}
                  </li>
                ))}</ul>
              </section>
            </div>
          )}
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
  const [batchRun, setBatchRun] = useState<JobRun | null>(null);
  const [startingBatch, setStartingBatch] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('updatedAt');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

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

  useEffect(() => {
    if (!batchRun || !['queued', 'running'].includes(batchRun.status)) return;
    const interval = setInterval(async () => {
      try {
        const updated = await fetchJobRun(batchRun.id);
        setBatchRun(updated);
        if (updated.status === 'completed') {
          setBatchMessage(`Weekly run #${updated.id} completed.`);
          await load();
        } else if (updated.status === 'failed' || updated.status === 'cancelled') {
          setBatchMessage(`Weekly run #${updated.id} ${updated.status}.`);
        }
      } catch { /* next poll will retry */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [batchRun, load]);

  const runTopTwenty = async () => {
    setStartingBatch(true);
    setError(null);
    try {
      const trigger = await triggerAiAnalysisBatch(currentMarket);
      const job = await fetchJobRun(trigger.runId);
      setBatchRun(job);
      setBatchMessage(trigger.alreadyExists
        ? `Weekly run #${trigger.runId} already exists for ${trigger.weekNumber}.`
        : `Started weekly top-20 run #${trigger.runId} for ${trigger.weekNumber}.`);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : 'Failed to start weekly AI analysis');
    } finally {
      setStartingBatch(false);
    }
  };

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
  const sortedRows = [...filtered].sort((left, right) =>
    compareRows(left, right, sortKey, sortDirection));
  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDirection(direction => direction === 'asc' ? 'desc' : 'asc');
    else {
      setSortKey(key);
      setSortDirection(key === 'ticker' || key === 'weekNumber' ? 'asc' : 'desc');
    }
  };
  const sortHeader = (key: SortKey, text: string) => {
    const active = sortKey === key;
    const Icon = !active ? ArrowUpDown : sortDirection === 'asc' ? ArrowUp : ArrowDown;
    return (
      <th aria-sort={active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button className={`ai-sort-button ${active ? 'active' : ''}`} onClick={() => toggleSort(key)}>
          {text}<Icon size={13} />
        </button>
      </th>
    );
  };
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
          <button
            className="btn btn-primary btn-sm"
            onClick={() => void runTopTwenty()}
            disabled={startingBatch || batchRun?.status === 'queued' || batchRun?.status === 'running'}
          >
            {startingBatch || batchRun?.status === 'queued' || batchRun?.status === 'running'
              ? <Loader2 size={14} className="spin-icon" />
              : <Play size={14} />}
            Run Top 20
          </button>
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

      {batchMessage && (
        <div className="ai-batch-status">
          <span>{batchMessage}</span>
          {batchRun && <strong>{batchRun.status} · {batchRun.progress}%</strong>}
        </div>
      )}

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
                  {sortHeader('ticker', 'Ticker')}
                  {sortHeader('weekNumber', 'Week')}
                  {sortHeader('valuation', 'Valuation')}
                  {sortHeader('baseReturn3mPct', '3M Base')}
                  {sortHeader('baseReturn6mPct', '6M Base')}
                  {sortHeader('baseReturn1yPct', '1Y Base')}
                  {sortHeader('baseReturn2yPct', '2Y Base')}
                  {sortHeader('shortTermAction', 'Short Term')}
                  {sortHeader('longTermAction', 'Long Term')}
                  {sortHeader('updatedAt', 'Updated')}
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {sortedRows.map(row => {
                  const summary = row.summary ?? {};
                  return <tr key={row.id}>
                    <td>
                      <button className="symbol-link" onClick={() => navigate(`/${currentMarket}/lookup/${row.ticker}`)}>
                        {row.ticker}
                      </button>
                    </td>
                    <td>{row.weekNumber}</td>
                    <td><span className={`ai-table-decision ai-tone-${valuationTone(summary.valuation)}`}>{label(summary.valuation)}</span></td>
                    <td><span className={`ai-prediction ai-tone-${predictionTone(summary.baseReturn3mPct)}`}>{formatPercent(summary.baseReturn3mPct)}</span></td>
                    <td><span className={`ai-prediction ai-tone-${predictionTone(summary.baseReturn6mPct)}`}>{formatPercent(summary.baseReturn6mPct)}</span></td>
                    <td><span className={`ai-prediction ai-tone-${predictionTone(summary.baseReturn1yPct)}`}>{formatPercent(summary.baseReturn1yPct)}</span></td>
                    <td><span className={`ai-prediction ai-tone-${predictionTone(summary.baseReturn2yPct)}`}>{formatPercent(summary.baseReturn2yPct)}</span></td>
                    <td><span className={`ai-signal ai-signal-${summary.shortTermAction ?? 'unavailable'}`}>{label(summary.shortTermAction)}</span></td>
                    <td><span className={`ai-signal ai-signal-${summary.longTermAction ?? 'unavailable'}`}>{label(summary.longTermAction)}</span></td>
                    <td>{formatDate(row.updatedAt)}</td>
                    <td>
                      <button className="btn btn-ghost btn-sm" onClick={() => void openReport(row)} title="Open report">
                        {openingId === row.id ? <Loader2 size={15} className="spin-icon" /> : <Eye size={15} />}
                      </button>
                    </td>
                  </tr>;
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {selected && <AnalysisModal detail={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function sortValue(row: AiInvestmentAnalysisHistoryItem, key: SortKey): string | number | null {
  if (key === 'ticker' || key === 'weekNumber' || key === 'updatedAt') return row[key];
  return row.summary?.[key] ?? null;
}

function compareRows(
  left: AiInvestmentAnalysisHistoryItem,
  right: AiInvestmentAnalysisHistoryItem,
  key: SortKey,
  direction: SortDirection,
): number {
  const leftValue = sortValue(left, key);
  const rightValue = sortValue(right, key);
  if (leftValue == null && rightValue == null) return 0;
  if (leftValue == null) return 1;
  if (rightValue == null) return -1;
  const result = typeof leftValue === 'number' && typeof rightValue === 'number'
    ? leftValue - rightValue
    : String(leftValue).localeCompare(String(rightValue));
  return direction === 'asc' ? result : -result;
}

function valuationTone(value?: string | null): string {
  if (value === 'undervalued') return 'positive';
  if (value === 'fairly_valued') return 'neutral';
  if (value === 'overvalued') return 'negative';
  return 'muted';
}

function predictionTone(value?: number | null): string {
  if (value == null || value === 0) return 'muted';
  return value > 0 ? 'positive' : 'negative';
}