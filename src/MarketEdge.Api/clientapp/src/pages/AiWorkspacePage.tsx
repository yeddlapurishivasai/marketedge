import { useCallback, useDeferredValue, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BrainCircuit, ChevronLeft, Eye, Loader2, RefreshCw, Search, X } from 'lucide-react';
import type { AiInvestmentAnalysisDetail, AiInvestmentAnalysisHistoryItem, Market } from '../api';
import { fetchAiAnalysisDetail, fetchAiAnalysisHistory } from '../api';

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
                <div><span>Valuation</span><strong>{label(valuation?.assessment)}</strong></div>
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
                        <td>{scenario.bear?.target ?? '—'} <span>{formatPercent(scenario.bear?.returnPct)}</span></td>
                        <td>{scenario.base?.target ?? '—'} <span>{formatPercent(scenario.base?.returnPct)}</span></td>
                        <td>{scenario.bull?.target ?? '—'} <span>{formatPercent(scenario.bull?.returnPct)}</span></td>
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
                  <th>Valuation</th>
                  <th>3M Base</th>
                  <th>6M Base</th>
                  <th>1Y Base</th>
                  <th>2Y Base</th>
                  <th>Short Term</th>
                  <th>Long Term</th>
                  <th>Updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {filtered.map(row => {
                  const summary = row.summary ?? {};
                  return <tr key={row.id}>
                    <td>
                      <button className="symbol-link" onClick={() => navigate(`/${currentMarket}/lookup/${row.ticker}`)}>
                        {row.ticker}
                      </button>
                    </td>
                    <td>{row.weekNumber}</td>
                    <td><span className="ai-table-decision">{label(summary.valuation)}</span></td>
                    <td>{formatPercent(summary.baseReturn3mPct)}</td>
                    <td>{formatPercent(summary.baseReturn6mPct)}</td>
                    <td>{formatPercent(summary.baseReturn1yPct)}</td>
                    <td>{formatPercent(summary.baseReturn2yPct)}</td>
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