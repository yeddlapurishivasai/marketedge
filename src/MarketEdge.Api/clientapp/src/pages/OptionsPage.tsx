import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { NavLink, useSearchParams } from 'react-router-dom';
import { AlertCircle, ArrowRight, ChevronLeft, ExternalLink, Info, LoaderCircle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { fetchJobRun } from '../api';
import type { JobRun } from '../api';
import {
  createOptionsForecast, disconnectOptions, fetchOptionsConnection, fetchOptionsForecast,
  fetchOptionsForecasts, fetchOptionsMetadata, startOptionsLogin,
  deleteForecast, OptionsApiError,
} from '../optionsApi';
import type {
  BrokerConnection, ConstituentValuation, ForecastAnalysis, ForecastDetail, ForecastMode, ForecastSummary,
  AnalysisDimension, AnalysisIndicator, FundamentalRange, IndexCode, OptionCandidate, OptionsMetadata, RiskTier, StrategySide, WeightReference,
} from '../optionsApi';
import './OptionsPage.css';

const MODES: { value: ForecastMode; label: string }[] = [
  { value: 'daily', label: 'Daily' }, { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' }, { value: 'expiry-intraday', label: 'Expiry Intraday' },
];
const TABS = ['Analysis', 'Constituents', 'Selling Strategies', 'Buying Strategies', 'History'] as const;
type Tab = typeof TABS[number];
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value)
  ? 'Unavailable' : value.toLocaleString('en-IN', { maximumFractionDigits: 2 });
const probabilityFraction = (value: number | null | undefined) => value == null || !Number.isFinite(value)
  ? 'Unavailable' : value.toLocaleString('en-IN', { maximumFractionDigits: 6 });
const money = (value: number | null | undefined) => value == null ? 'Unavailable' : `₹${number(value)}`;
const reward = (candidate: OptionCandidate) => candidate.maxProfit == null && candidate.strategy === 'long-call' && candidate.costEstimate != null
  ? 'Unbounded at expiry (not a promised return)' : money(candidate.maxProfit);
const grossReward = (candidate: OptionCandidate) => candidate.grossMaxProfit == null && candidate.strategy === 'long-call'
  && candidate.grossMaxLoss != null ? 'Unbounded gross at expiry (before charges)' : money(candidate.grossMaxProfit);
const signed = (value: number | null | undefined) => value == null ? 'Unavailable' : `${value > 0 ? '+' : ''}${number(value)}%`;
function timestamp(value?: string | null) {
  if (!value) return 'Unavailable';
  const text = value.trim();
  const date = new Date(text);
  if (!text || text === '0' || !Number.isFinite(date.getTime()) || date.getTime() <= 0 || date.getUTCFullYear() === 1970) return 'Unavailable';
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return date.toISOString().slice(0, 10) === text ? `${text} (source date)` : 'Unavailable';
  return `${date.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' })} IST`;
}
function evidenceJson(value: unknown) {
  return JSON.stringify(value, (key, entry: unknown) => {
    const temporal = /(?:At|AsOf|Timestamp|Date|Time|_at|_as_of|_timestamp|_date|_time)$/.test(key)
      || ['asOf', 'timestamp', 'date', 'time', 'datetime'].includes(key);
    return temporal && (entry == null || entry === 0 || (typeof entry === 'string' && timestamp(entry) === 'Unavailable'))
      ? 'Unavailable' : entry;
  }, 2) ?? 'Unavailable';
}
const message = (error: unknown) => error instanceof Error ? error.message : 'The request failed. Please retry.';
const isWorking = (detail: ForecastDetail) => ['queued', 'running'].includes(detail.status)
  || (detail.status === 'completed' && (!detail.ai || ['pending', 'queued', 'running'].includes(detail.ai.status)));

function InfoPopover({ label, children, showLabel = false }: { label: string; children: React.ReactNode; showLabel?: boolean }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard); };
  }, [open]);
  return <span className="options-info" ref={root}>
    <button ref={trigger} type="button" className={`options-info-trigger ${showLabel ? 'has-label' : ''}`} title={label} aria-label={`About ${label}`}
      aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!event.repeat) setOpen(value => !value); }
      }}><Info size={15} aria-hidden="true" />{showLabel && <span>{label}</span>}</button>
    <span ref={panel} id={id} role="dialog" aria-modal="false" aria-label={label} tabIndex={-1}
      className="options-info-panel" hidden={!open}>
      <span className="options-info-heading"><strong>{label}</strong><button type="button" aria-label={`Close ${label}`}
        onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button></span>
      {children}
    </span>
  </span>;
}
function Evidence({ value, label = 'Source evidence / provenance' }: { value: unknown; label?: string }) {
  return <InfoPopover label={label} showLabel><p>Missing or invalid source timestamps display as Unavailable; saved values are unchanged.</p><pre>{evidenceJson(value)}</pre></InfoPopover>;
}
function Warnings({ items }: { items?: string[] }) {
  const unique = [...new Set(items)];
  return unique.length ? <ul className="options-warnings">{unique.map(item => <li key={item}>{item}</li>)}</ul> : null;
}
function ForecastWarnings({ items = [] }: { items?: string[] }) {
  const routinePattern = /^(European continuous-carry model|Daily-only technicals:)/;
  const limitationPattern = /^(?:fundamentals|breadth) unavailable: not supplied by parent|^Macro\/flows context is unavailable|^Rate\/dividend yield unavailable:|^Round-trip charges unavailable:/;
  const quoteReasons: Record<string, string> = {
    'stale/future quote': 'Stale or future-dated quotes',
    'zero/missing executable quote': 'Missing or zero bid/ask',
    'zero/missing last price': 'Missing or zero last prices',
    'crossed quote': 'Crossed bid/ask',
    'wide spread': 'Wide spreads',
    'invalid strike/lot size': 'Invalid strike or lot metadata',
    'invalid tick size': 'Invalid tick metadata',
    'executable premium below minimum tick': 'Premiums below minimum tick',
    'expired contract': 'Expired contracts',
    'no finite implied volatility solution': 'IV solution unavailable',
  };
  const unique = [...new Set(items)];
  const grouped = new Map<string, { label: string; warnings: string[] }>();
  const contracts = new Set<string>();
  const routine: string[] = [], limitations: string[] = [], important: string[] = [];
  for (const item of unique) {
    const match = /^(NFO|BFO):([^:\s]+): (.+)$/.exec(item);
    const reason = match?.[3].trim().toLowerCase();
    if (match && reason && Object.hasOwn(quoteReasons, reason)) {
      const group = grouped.get(reason) || { label: quoteReasons[reason], warnings: [] };
      group.warnings.push(item);
      grouped.set(reason, group);
      contracts.add(`${match[1]}:${match[2]}`);
    } else if (routinePattern.test(item)) routine.push(item);
    else if (limitationPattern.test(item)) limitations.push(item);
    else important.push(item);
  }
  const groups = [...grouped.values()].sort((a, b) => b.warnings.length - a.warnings.length || a.label.localeCompare(b.label));
  const quoteWarningCount = groups.reduce((count, group) => count + group.warnings.length, 0);
  return <>
    {important.length > 0 && <section className="options-attention" aria-label="Forecast attention">
      <h3><AlertCircle size={16} aria-hidden="true" /> Attention · {important.length}</h3>
      <Warnings items={important} />
    </section>}
    {limitations.length > 0 && <section className="options-limitations" aria-label="Model and source limitations">
      <h3>Model &amp; source limitations</h3>
      <ul>{limitations.map(item => <li key={item}>{item}</li>)}</ul>
    </section>}
    {groups.length > 0 && <section className="options-quote-summary" aria-label="Quote quality summary">
      <div className="options-section-heading"><h3>Quote checks</h3><span className="options-count">{contracts.size} contract{contracts.size === 1 ? '' : 's'} affected</span></div>
      <p>Unusable quotes are excluded from strategy calculations.</p>
      <ul className="options-quote-counts">{groups.map(group => <li key={group.label}>{group.label} <strong>{group.warnings.length}</strong></li>)}</ul>
      <details className="options-inline-details">
        <summary>View {quoteWarningCount} distinct contract warning{quoteWarningCount === 1 ? '' : 's'}</summary>
        <div className="options-quote-details" tabIndex={0} role="region" aria-label="Full contract quote warnings">
          {groups.map(group => <div key={group.label}><h4>{group.label} · {group.warnings.length}</h4>
            <ul>{group.warnings.map(item => <li key={item}>{item}</li>)}</ul></div>)}
        </div>
      </details>
    </section>}
    {routine.length > 0 && <details className="options-inline-details options-model-notes">
      <summary>Model assumptions &amp; technical notes <span className="options-count">{routine.length}</span></summary>
      <ul className="options-routine-notes">{routine.map(item => <li key={item}>{item}</li>)}</ul>
    </details>}
  </>;
}
function Facts({ items }: { items: [string, React.ReactNode][] }) {
  return <dl className="options-facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
function OptionalInputsNotice() {
  return <p>Interest-rate, dividend-yield and fixed trading-cost configuration are optional; they do not block forecasts or quote-based expiry payoff alternatives.
    Without rate/yield inputs, IV, Greeks, model probabilities, future repricing and model-derived stop-loss estimates are unavailable; historical-volatility bands and valid technical stop levels remain usable.
    Without charges, only gross payoffs before charges are available; net profit, net profit probabilities and fee-inclusive maximum loss are unavailable. No missing value is assumed to be zero.</p>;
}
function SnapshotInputStatus({ data }: { data: ForecastAnalysis }) {
  const inputs = data.modelInputs;
  const warnings = data.warnings || [];
  const noModel = (inputs && (inputs.riskFreeRate == null || inputs.dividendYield == null))
    || warnings.some(item => item.startsWith('Rate/dividend yield unavailable:'));
  const noFees = (inputs && inputs.roundTripCostPerLeg == null) || (data.candidates || []).some(item => item.costEstimate == null)
    || warnings.some(item => item.startsWith('Round-trip charges unavailable:'));
  return <div className="options-data-flags">
    {noModel && <span>Model metrics unavailable · optional inputs missing</span>}
    {noFees && <span>Gross before charges · net loss cap unavailable</span>}
  </div>;
}
type ForecastRunState = 'idle' | 'submitting' | 'running' | 'selecting';
function BrokerBar({ connection, error, callbackStatus, busy, clock, login, disconnect, refresh }: {
  connection: BrokerConnection | null; error: string; callbackStatus: string | null; busy: boolean; clock: number;
  login: () => void; disconnect: () => void; refresh: () => void;
}) {
  const expired = !!connection?.expiresAt && new Date(connection.expiresAt).getTime() <= clock;
  const connected = connection?.status === 'connected' && !expired;
  const checking = !connection && !error;
  const rawFailure = error || connection?.lastError || (callbackStatus === 'error' ? 'Broker login failed. Retry the official login flow.' : '');
  const failure = expired && /^(?:Broker )?session expired[.;, ]/i.test(rawFailure) ? '' : rawFailure;
  const status = checking ? 'Checking connection' : expired ? 'Session expired' : connected ? 'Connected' : 'Not connected';
  return <section className="options-broker" aria-label="Broker connection">
    <div className="options-connection-bar">
      <div className="options-connection-identity">
        <strong>Zerodha</strong>
        <span className={`options-status ${connected ? 'is-connected' : checking ? 'is-pending' : 'is-attention'}`} role="status">
          <span className="options-status-dot" aria-hidden="true" />{status}
        </span>
        {connected && connection?.brokerUserId && <span className="options-account">Account {connection.brokerUserId}</span>}
        {connected && connection?.expiresAt && <span className="options-expiry">Expires {timestamp(connection.expiresAt)}</span>}
      </div>
      <div className="options-connection-actions">
        <button className="btn btn-secondary options-button-subtle" disabled={busy || !connection?.configured} onClick={login}>
          <ExternalLink size={14} aria-hidden="true" /> {busy ? 'Working…' : connected || expired ? 'Re-login' : 'Connect Zerodha'}
        </button>
        <button className="btn btn-secondary options-icon-button" onClick={refresh} aria-label="Refresh connection and saved data" title="Refresh connection and saved data">
          <RefreshCw size={15} aria-hidden="true" />
        </button>
        <details className="options-connection-details">
          <summary>Connection details</summary>
          <div className="options-connection-panel">
            <Facts items={[['Login', timestamp(connection?.loginAt)], ['Session expiry', timestamp(connection?.expiresAt)]]} />
            <p>Application sign-in is separate from Zerodha login. Refresh checks saved status; it never creates a forecast.</p>
            {connected && <button className="btn btn-secondary" disabled={busy} onClick={disconnect}>Disconnect Zerodha</button>}
          </div>
        </details>
      </div>
    </div>
    {!checking && !connected && <p className="options-login-required">
      <AlertCircle size={15} aria-hidden="true" />
      {connection && !connection.configured ? 'Broker setup required on the server.' : expired ? 'Re-login to run a new forecast.' : 'Connect Zerodha to run a new forecast.'}
      {' '}Saved forecasts remain available.
    </p>}
    {failure && <p role="alert" className="options-error">{failure}</p>}
  </section>;
}
function ForecastComposer({ indexCode, mode, dailyLabel, connected, runState, missingCalendar, changeSelection, generate, compact = false }: {
  indexCode: IndexCode; mode: ForecastMode; dailyLabel: string; connected: boolean; runState: ForecastRunState;
  missingCalendar: boolean; changeSelection: (index: IndexCode, mode: ForecastMode) => void; generate: () => void; compact?: boolean;
}) {
  const active = runState !== 'idle';
  const showHelp = !compact || active || !connected;
  const action = { idle: 'Get Forecast', submitting: 'Starting forecast…', running: 'Running forecast…', selecting: 'Final AI selection…' }[runState];
  return <section className={`options-composer ${compact ? 'is-compact' : ''}`} aria-label="Forecast request">
    <div className="options-composer-intro">
      <span className="options-eyebrow">NEW ANALYSIS</span>
      <h2>Build your market view</h2>
      <p>One forecast with automatic final AI selection.</p>
    </div>
    <div className="options-request-controls">
      <label>Index<select value={indexCode} disabled={active} onChange={event => changeSelection(event.target.value as IndexCode, mode)}>
        <option value="NIFTY50">NIFTY 50</option><option value="SENSEX">SENSEX</option>
      </select></label>
      <label>Horizon<select value={mode} disabled={active} onChange={event => changeSelection(indexCode, event.target.value as ForecastMode)}>
        {MODES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select></label>
      <div className="options-forecast-action">
        <button className="btn btn-primary options-run-button" disabled={active || !connected} onClick={generate}
          aria-busy={active} aria-describedby={showHelp ? 'options-action-help' : undefined}>
          {active ? <LoaderCircle className="options-spin" size={18} aria-hidden="true" /> : <ArrowRight size={18} aria-hidden="true" />}
          {action}
        </button>
        {showHelp && <span id="options-action-help" role="status">{active ? 'Analysis → alternatives → final AI shortlist' : !connected ? 'Broker connection required for a new run' : 'New immutable version · AI included'}</span>}
      </div>
    </div>
    <div className="options-composer-footer">
      <span>{mode === 'daily' ? dailyLabel : `${MODES.find(item => item.value === mode)?.label} · broker-listed expiry`}</span>
      <InfoPopover label="Forecast rules & optional inputs">
        <div className="options-help-panel">
          <p>Daily: before 14:00 IST on a trading session, target today's close; otherwise the next trading session's close. Public calendars define holidays and exceptional sessions. Exact accepted targets appear on each saved version.</p>
          <p>Weekly/monthly forecasts use actual listed eligible expiries, never a silently substituted horizon. Expiry Intraday uses session-specific entry and exit cutoffs.</p>
          <OptionalInputsNotice />
          <p>Each deliberate run creates a timestamped version. Opening history or changing tabs never requests AI. There is no separate AI rerun.</p>
        </div>
      </InfoPopover>
    </div>
    {missingCalendar && <p className="options-inline-warning">Public calendar refresh pending or unavailable; retried on generation. Dates are never inferred.</p>}
    {mode === 'expiry-intraday' && <p className="options-inline-warning">Expiry-day risk: high gamma, liquidity and stale-quote exposure. The worker validates session eligibility and cutoffs.</p>}
  </section>;
}
const isEtfProxy = (source?: string | null) => !!source && /etf-holdings-proxy|nifty[\s-]*bees/i.test(source);
function WeightProvenance({ weights, items = [], compact = false }: { weights?: WeightReference | null; items?: ConstituentValuation[]; compact?: boolean }) {
  const proxy = weights?.sourceType === 'etf-holdings-proxy' || items.some(item => isEtfProxy(item.weightSource));
  if (!proxy) return null;
  const dates = [...new Set(items.map(item => item.weightAsOf).filter((value): value is string => !!value))];
  let sourceUrl: string | null = null;
  try {
    const url = new URL(weights?.sourceUrl || '');
    if (url.protocol === 'https:' || url.protocol === 'http:') sourceUrl = url.href;
  } catch { /* Missing or invalid source links are not rendered as links. */ }
  const methods = <>
    <p>These dated proxy weights are used for constituent weighting; they do not establish current index composition or current index weights.</p>
    <p>Normalization method: {weights?.method || 'Equity holdings normalized to 100%; non-equity NAV excluded.'}</p>
    {weights?.rawEquityNavPct != null && <p>Raw equity share of ETF NAV before normalization: {number(weights.rawEquityNavPct)}%.</p>}
    {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">ETF holdings source publication</a>
      : <p>Source publication link unavailable in this snapshot.</p>}
  </>;
  return <section className={`options-notice ${compact ? 'is-compact' : ''}`} aria-label="ETF proxy weight provenance">
    <h3>Weight source: Nippon NiftyBeES ETF holdings proxy</h3>
    <p><strong>As of {weights?.sourceDate || dates.join(', ') || 'Unavailable'}. Equity-normalized ETF holdings, not official or live index weights.</strong></p>
    <InfoPopover label="Weight methodology & source publication">{methods}</InfoPopover>
  </section>;
}

function qualitativeRangeReason(value?: string | null) {
  const reason = value?.trim();
  return reason && reason.length <= 160 && !/\p{N}/u.test(reason) ? reason : null;
}
function RangeForecast({ data, targetAt, ai }: { data: ForecastAnalysis; targetAt?: string | null; ai?: ForecastDetail['ai'] }) {
  const selected = ai?.status === 'completed' ? data.scenarios.find(item => item.id === ai.selection?.scenarioId) : undefined;
  const reason = qualitativeRangeReason(ai?.selection?.scenarioReason);
  return <section className="options-range-hero" aria-label="Forecast price ranges">
    <div className="options-section-heading"><div><span className="options-eyebrow">INDEX PRICE · SCENARIO RANGES</span>
      <h2>Forecast price ranges</h2></div><span className="options-status is-pending">{data.bias} bias</span></div>
    <p className="options-range-target">Target: <strong>{timestamp(targetAt || data.scenarios[0]?.targetAt || data.plannedExitAt)}</strong> · captured index {number(data.spot)}</p>
    <div className="options-range-grid">{data.scenarios.map(scenario => {
      const available = scenario.low != null && scenario.high != null && Number.isFinite(scenario.low)
        && Number.isFinite(scenario.high) && scenario.low <= scenario.high;
      return <article className={`options-range-card ${selected?.id === scenario.id ? 'is-selected' : !selected && scenario.id === 'base' ? 'is-baseline' : ''}`} key={scenario.id}>
        <h3>{scenario.label} scenario {selected?.id === scenario.id && <span className="options-status is-pending">AI selected</span>}</h3>
        <strong className="options-range-value">{available ? `${number(scenario.low)} – ${number(scenario.high)}` : 'Unavailable'}</strong>
        <span>Index points · lower to upper bound</span>
        <p>{scenario.probability == null ? 'Descriptive range · probability unavailable' : `${probabilityFraction(scenario.probability)} probability · ${scenario.probabilityType}`}</p>
      </article>;
    })}</div>
    {!data.scenarios.length && <p>No supported price range was supplied in this snapshot.</p>}
    {ai?.status === 'completed' && <div className="options-ai-range-summary">
      <strong>{selected ? `AI-selected range: ${selected.label}` : 'No matching AI range selection stored; deterministic ranges remain available.'}</strong>
      <InfoPopover label="Range selection"><p>{reason || 'No qualitative range explanation was stored for this version.'}</p></InfoPopover>
    </div>}
    <div className="options-range-footnote"><small>Calculated ranges · not guaranteed</small><InfoPopover label="Calculated range methodology">
      <p>Calculated deterministic ranges, not guaranteed prices or returns. AI selects and explains supplied ranges; it does not generate their endpoints.</p>
    </InfoPopover></div>
  </section>;
}
function webUrl(value?: string | null) {
  try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
function MarketContext({ data }: { data: ForecastAnalysis }) {
  const context = data.marketContext;
  const macro = context?.dimension || data.dimensions.find(item => item.key === 'macro');
  const indicators = macro?.indicators || [];
  const crude = context?.evidence?.crude;
  const flows = context?.evidence?.flows;
  const flowUrl = webUrl(flows?.sourceUrl);
  const crudeUrl = webUrl(crude?.sourceUrl);
  const news = context?.news || [];
  function headlines(items: typeof news) {
    return <ul className="options-news-list">{items.map((item, index) => {
      const url = webUrl(item.url);
      return <li key={`${item.url || item.title}-${index}`}>
        {url ? <a href={url} target="_blank" rel="noopener noreferrer">{item.title}</a> : <strong>{item.title}</strong>}
        <small>{timestamp(item.publishedAt)}{item.source ? ` · ${item.source}` : ''}</small>
      </li>;
    })}</ul>;
  }
  if (!context && !indicators.length) return null;
  return <section className="options-card" aria-label="Crude, institutional flows and news context">
    <div className="options-section-heading"><h2>Crude, flows &amp; news context</h2><InfoPopover label="Market context">
      <p>Oil-context heuristic only, not a calibrated index impact or directional vote. Dated news is context, not a scheduled-event calendar.</p>
      {macro?.reason && <p>{macro.reason}</p>}
    </InfoPopover></div>
    {crude && <article className="options-crude-observation">
      <div className="options-section-heading"><h3>{crude.name}</h3><span className="options-status is-pending">{crude.quoteType || 'Observation type unavailable'}</span></div>
      {crude.isAlternative && <p className="options-inline-warning">Alternative benchmark · not an MCX quote.</p>}
      <Facts items={[['Captured price', `${number(crude.price)} ${crude.unit || crude.currency || ''}`], ['Previous close', number(crude.previousClose)],
        ['Change versus comparison', signed(crude.changePct)],
        [crude.asOfPrecision === 'session-date' ? 'Source session date' : 'Observed at',
          crude.asOfPrecision === 'session-date' ? crude.sessionDate || 'Unavailable' : timestamp(crude.asOf)]]} />
      <InfoPopover label="Crude comparison and date precision">{crude.comparison && <p>{crude.comparison}</p>}{crude.asOfMeaning && <p>{crude.asOfMeaning}</p>}</InfoPopover>
      <p>Source: {crudeUrl ? <a href={crudeUrl} target="_blank" rel="noopener noreferrer">{crude.source || 'Source publication'}</a> : crude.source || 'Unavailable'}</p>
    </article>}
    {flows && <article className="options-crude-observation" aria-label="FII and DII cash activity">
      <div className="options-section-heading"><h3>FII / DII cash activity</h3><span className="options-status is-pending">{flows.status}</span></div>
      <p>Last-reported end-of-day · source session <strong>{flows.sessionDate || flows.asOf || 'Unavailable'}</strong> · {flows.unit || 'Unit unavailable'}</p>
      <InfoPopover label="FII/DII methodology"><p>Raw cash activity, no calibrated direction weight. These observations do not change the oil-only macro score.</p>
        {flows.reason && <p>{flows.reason}</p>}{flows.unitBasis && <p>{flows.unitBasis}</p>}</InfoPopover>
      {flows.status !== 'unavailable' && <div className="options-table-wrap"><table><caption>Reported cash purchases, sales and net activity — {flows.unit || 'unit unavailable'}</caption>
        <thead><tr><th>Institution</th><th>Buy</th><th>Sell</th><th>Net</th></tr></thead>
        <tbody>{(['fii', 'dii'] as const).map(key => <tr key={key}><th scope="row">{key.toUpperCase()}</th>
          <td>{number(flows[key]?.buy)}</td><td>{number(flows[key]?.sell)}</td><td>{number(flows[key]?.net)}</td></tr>)}</tbody>
      </table></div>}
      {flows.status === 'unavailable' && <small>Reported cash activity unavailable.</small>}
      <p>Source: {flowUrl ? <a href={flowUrl} target="_blank" rel="noopener noreferrer">{flows.source || 'Source publication'}</a> : flows.source || 'Unavailable'}</p>
    </article>}
    {indicators.length > 0 && <InfoPopover label="Raw macro input observations"><div className="options-table-wrap"><table><caption>Observed macro inputs</caption>
      <thead><tr><th>Observation</th><th>Value</th><th>Source / limitations</th></tr></thead>
      <tbody>{indicators.map(item => <tr key={item.key}><th scope="row">{item.label}</th><td>{item.value ?? 'Unavailable'} {item.unit}</td>
        <td>{item.reason || item.source || 'Unavailable'}{item.asOf && <small>{item.sessionDate || /^\d{4}-\d{2}-\d{2}$/.test(item.asOf)
          ? `Source session: ${item.sessionDate || item.asOf}` : crude?.asOfPrecision === 'session-date' && item.key === 'crude-change'
            ? `Source session: ${crude.sessionDate || 'Unavailable'}` : timestamp(item.asOf)}</small>}</td></tr>)}</tbody>
    </table></div></InfoPopover>}
    {headlines(news.slice(0, 3))}
    {news.length > 3 && <InfoPopover label={`More dated headlines (${news.length - 3})`}>{headlines(news.slice(3))}</InfoPopover>}
    {!context?.news?.length && <p>No dated news items were supplied in this snapshot.</p>}
    {context?.evidence != null && <Evidence value={context.evidence} label="Crude / flows / news source evidence and coverage" />}
  </section>;
}
function metricSemantics(metric: AnalysisIndicator, category: string) {
  const unavailable = metric.value == null || (typeof metric.value === 'number' && !Number.isFinite(metric.value));
  const context = metric.influenceRole === 'context' || metric.contextOnly || ['volatility', 'liquidity', 'derivatives', 'macro'].includes(category)
    || /vix|adx|atr\d|realizedvol|realised|pcr|put.?call|basis|validquotes|validbooks|cash-net/i.test(metric.key);
  if (unavailable) return { tone: 'unavailable', label: '— Unavailable', context: !!context };
  if (context) return { tone: 'context', label: ['adverse', 'supportive'].includes(metric.direction || '')
    ? `◇ ${metric.direction} context` : '◇ Non-directional context', context: true };
  if (metric.direction === 'bullish') return { tone: 'bullish', label: '↑ Bullish', context: false };
  if (metric.direction === 'bearish') return { tone: 'bearish', label: '↓ Bearish', context: false };
  if (metric.direction === 'neutral') return { tone: 'neutral', label: '↔ Neutral', context: false };
  if (metric.influenceRole === 'directional' && metric.signalContribution != null && Number.isFinite(metric.signalContribution)
    && Math.abs(metric.signalContribution) <= 1) {
    return metric.signalContribution > 0 ? { tone: 'bullish', label: '↑ Positive model influence', context: false }
      : metric.signalContribution < 0 ? { tone: 'bearish', label: '↓ Negative model influence', context: false }
        : { tone: 'neutral', label: '↔ Neutral model influence', context: false };
  }
  return { tone: 'context', label: '◇ Direction not supplied', context: true };
}
function metricGroups(data: ForecastAnalysis): AnalysisDimension[] {
  const groups = data.dimensions.filter(item => item.key !== 'fundamentals').map(item => ({ ...item, indicators: [...item.indicators] }));
  const context = data.marketContext;
  if (context?.dimension && !groups.some(item => item.key === 'macro')) groups.push({ ...context.dimension, indicators: [...context.dimension.indicators] });
  const ensure = (key: string, label: string, metric: AnalysisIndicator, pattern: RegExp) => {
    if (groups.some(group => group.indicators.some(item => pattern.test(`${item.key} ${item.label}`)))) return;
    let group = groups.find(item => item.key === key);
    if (!group) { group = { key, label, indicators: [], score: null, coverage: null, scale: 'No aggregate supplied' }; groups.push(group); }
    group.indicators.push(metric);
  };
  const vix = data.vix as { last_price?: number; timestamp?: string; source?: string } | null | undefined;
  ensure('volatility', 'Volatility', { key: 'india-vix', label: 'India VIX', value: vix?.last_price ?? null, unit: '%',
    asOf: vix?.timestamp, source: vix?.source, contextOnly: true }, /vix/i);
  const crude = context?.evidence?.crude;
  ensure('macro', 'Macro context', { key: 'crude-change', label: crude?.name || 'Crude oil', value: crude?.changePct ?? null, unit: '%',
    asOf: crude?.asOfPrecision === 'session-date' ? crude.sessionDate : crude?.asOf, source: crude?.source, contextOnly: true }, /crude|brent|oil/i);
  for (const key of ['fii', 'dii'] as const) {
    const flows = context?.evidence?.flows;
    ensure('macro', 'Macro context', { key: `${key}-cash-net`, label: `${key.toUpperCase()} cash net`, value: flows?.[key]?.net ?? null,
      unit: flows?.unit, asOf: flows?.sessionDate || flows?.asOf, source: flows?.source, contextOnly: true }, new RegExp(key, 'i'));
  }
  ensure('derivatives', 'Derivatives positioning', { key: 'window-oi-pcr', label: 'OI put/call ratio', value: null, contextOnly: true }, /pcr|put.?call/i);
  ensure('derivatives', 'Derivatives positioning', { key: 'futures-basis', label: 'Futures basis', value: null, contextOnly: true }, /basis/i);
  for (const group of groups) group.indicators = group.indicators.map(metric => {
    if (/vix/i.test(metric.key)) return { ...metric, asOf: metric.asOf || vix?.timestamp, source: metric.source || vix?.source };
    if (/crude/i.test(metric.key) && crude?.asOfPrecision === 'session-date')
      return { ...metric, asOf: crude.sessionDate || metric.asOf, sessionDate: crude.sessionDate };
    return metric;
  });
  return groups;
}
function MetricCard({ metric, group, capturedAt }: { metric: AnalysisIndicator; group: AnalysisDimension; capturedAt?: string | null }) {
  const semantics = metricSemantics(metric, group.key);
  const observed = metric.sessionDate || metric.asOf || metric.observedAt;
  const numeric = typeof metric.value === 'number' && Number.isFinite(metric.value);
  const bounded = /^(rsi|adx)/i.test(metric.key);
  const min = metric.min ?? (bounded ? 0 : undefined), max = metric.max ?? (bounded ? 100 : undefined);
  const relative = numeric && min != null && max != null && Number.isFinite(min) && Number.isFinite(max)
    && max > min && (metric.value as number) >= min && (metric.value as number) <= max;
  const weighted = metric.influenceRole === 'directional' && !semantics.context && metric.signalContribution != null
    && Number.isFinite(metric.signalContribution) && Math.abs(metric.signalContribution) <= 1;
  const legacy = metric.influenceRole == null && metric.signalContribution === undefined;
  const contribution = weighted ? metric.signalContribution : legacy ? metric.contribution : null;
  const hasContribution = semantics.tone !== 'unavailable' && contribution != null && Number.isFinite(contribution) && (!semantics.context || contribution !== 0);
  const scale = weighted ? 1 : Math.max(1, ...group.indicators.filter(item => item.contributionUnit === metric.contributionUnit)
    .map(item => typeof item.contribution === 'number' && Number.isFinite(item.contribution) ? Math.abs(item.contribution) : 0));
  const width = hasContribution ? Math.abs(contribution) / scale * 50 : 0;
  const source = metric.source || group.source;
  const url = webUrl(metric.sourceUrl);
  return <article className={`options-metric is-${semantics.tone}`} aria-label={metric.label}>
    <div className="options-section-heading"><h4>{metric.label}</h4><InfoPopover label={`Metric ${metric.label}`}>
      <p>{metric.reason || group.reason || 'No interpretation supplied for this observation.'}</p>
      {metric.contributionBasis && <p>{metric.contributionBasis}</p>}
      <Facts items={[['Timeframe', metric.timeframe || 'Not supplied'], ['Backend direction', metric.direction || 'Not supplied'],
        ['Contribution units', weighted ? 'Coverage-adjusted directional weighted fraction; not index points or probability'
          : metric.contributionUnit || 'Raw category model units, not an aggregate weight or probability']]} />
      <pre>{evidenceJson(metric)}</pre>
    </InfoPopover></div>
    <strong className="options-metric-value">{typeof metric.value === 'number' ? number(metric.value) : metric.value || 'Unavailable'} <small>{metric.unit}</small></strong>
    <span className="options-metric-direction">{semantics.label}</span>
    {relative && <div className="options-metric-relative" role="meter" aria-label={`${metric.label} relative position, not probability`}
      aria-valuemin={min} aria-valuemax={max} aria-valuenow={metric.value as number}>
      <span style={{ width: `${((metric.value as number) - min!) / (max! - min!) * 100}%` }} />
    </div>}
    {hasContribution && <div data-influence-basis={weighted ? 'coverage-adjusted' : 'legacy-raw'} className={`options-contribution ${semantics.context ? 'is-context' : contribution > 0 ? 'is-positive' : contribution < 0 ? 'is-negative' : 'is-zero'}`}>
      <small>{weighted ? 'Weighted influence' : semantics.context ? 'Context contribution' : 'Raw model vote'}: {contribution > 0 ? '+' : ''}
        {weighted ? probabilityFraction(contribution) : number(contribution)} {weighted ? '(fraction)' : metric.contributionUnit || 'model units'}</small>
      <div role="meter" aria-label={weighted ? `${metric.label} coverage-adjusted directional influence, weighted fraction`
        : `${metric.label} signed raw contribution, not aggregate weight`} aria-valuemin={-scale} aria-valuemax={scale} aria-valuenow={contribution}>
        <span style={{ left: `${contribution < 0 ? 50 - width : 50}%`, width: `${width}%` }} />
      </div>
    </div>}
    {metric.influenceRole === 'directional' && !weighted && <small>Weighted influence unavailable</small>}
    <small className="options-metric-source">{url ? <a href={url} target="_blank" rel="noopener noreferrer">{source || 'Source publication'}</a> : source || 'Source not supplied'}</small>
    <small>{observed ? `Observed: ${timestamp(observed)}` : group.asOf ? `Category snapshot: ${timestamp(group.asOf)}`
      : `Snapshot captured: ${timestamp(capturedAt)} · metric observation time not supplied`}</small>
  </article>;
}
function MarketMetrics({ data }: { data: ForecastAnalysis }) {
  const groups = metricGroups(data);
  const ageWarnings = [...new Set(data.warnings || [])].filter(item => !/^(?:NFO|BFO):/.test(item)
    && /stale|outdated|older than|expired|outside.*session|date.?mismatch/i.test(item));
  return <section aria-label="Individual market inputs">
    <div className="options-section-heading"><h2 className="options-panel-heading">Analysis dimensions</h2>
      <InfoPopover label="Metric directions and contributions"><p>Individual observations are primary. Category scores are aggregates, not probabilities.
        Green ↑ and red ↓ require explicit directional evidence. VIX, ADX, OI/PCR, basis, flows and liquidity are context, not inferred index direction.
        Relative fills appear only for stated finite scales. Weighted influence is the supplied coverage-adjusted directional fraction on a fixed −1 to +1 scale,
        not index points or probability. Legacy raw vote/context fills are explicitly unnormalised. SMA observations share one trend vote; the supplied weighted fractions account for that split.
        Missing observation times are labelled snapshot captures.</p></InfoPopover></div>
    <div className="options-metric-legend" aria-label="Metric direction legend"><span className="is-bullish">↑ Bullish</span><span className="is-bearish">↓ Bearish</span>
      <span className="is-neutral">↔ Neutral</span><span className="is-context">◇ Non-directional context</span></div>
    {ageWarnings.length > 0 && <div className="options-age-warning">Recorded source-age limitations · {ageWarnings.length}
      <InfoPopover label="Source-age warnings"><Warnings items={ageWarnings} /></InfoPopover></div>}
    {groups.map(group => <section className="options-metric-group" key={group.key} aria-label={`${group.label} individual metrics`}>
      <header className="options-section-heading"><h3>{group.label}</h3><span className="options-metric-aggregate">Category score {number(group.score)} / 100
        <InfoPopover label={`${group.label} aggregate`}><p>Scale: {group.scale}. Coverage: {group.coverage == null ? 'Unavailable' : `${number(group.coverage * 100)}%`}.
          This is a category aggregate, not a directional vote for every metric.</p>{group.reason && <p>{group.reason}</p>}</InfoPopover></span></header>
      <div className="options-metric-grid">{group.indicators.map(metric => <MetricCard key={metric.key} metric={metric} group={group} capturedAt={data.asOf} />)}</div>
      {!group.indicators.length && <small>No individual observations supplied.</small>}
    </section>)}
  </section>;
}
function Analysis({ data, targetAt, ai }: { data: ForecastAnalysis; targetAt?: string | null; ai?: ForecastDetail['ai'] }) {
  return <>
    <RangeForecast data={data} targetAt={targetAt} ai={ai} />
    <SnapshotInputStatus data={data} />
    <WeightProvenance compact weights={data.referenceData?.weights} items={data.constituents} />
    <MarketMetrics data={data} />
    <MarketContext data={data} />
    <div className="options-evidence-toolbar">
    {!!data.directionContributions?.length && <InfoPopover label="Directional contribution evidence" showLabel>
      <p>Raw model weights and signed contributions, not probabilities or individual indicator scores. Coverage adjustment makes missing inputs visible.</p>
      <div className="options-table-wrap"><table><thead><tr><th>Dimension</th><th>Coverage</th><th>Configured weight</th><th>Coverage-adjusted weight</th><th>Effective weight</th><th>Contribution</th><th>Reason</th></tr></thead>
        <tbody>{data.directionContributions.map(item => <tr key={item.key}><th scope="row">{item.key}</th>
          <td>{item.coverage == null ? 'Unavailable' : `${number(item.coverage * 100)}%`}</td>
          <td>{number(item.configuredWeight)}</td><td>{number(item.coverageAdjustedWeight)}</td>
          <td>{number(item.effectiveWeight)}</td><td>{number(item.contribution)}</td><td>{item.reason || 'Unavailable'}</td>
        </tr>)}</tbody></table></div>
    </InfoPopover>}
    <InfoPopover label="Scenario probabilities, support & resistance" showLabel>
      <p>Ranges are scenario estimates, not guaranteed returns. Probability type is shown separately.</p>
      <div className="options-table-wrap"><table><thead><tr><th>Scenario</th><th>Low (points)</th><th>High (points)</th><th>Probability (raw)</th><th>Type / limitations</th></tr></thead>
        <tbody>{data.scenarios.map(scenario => <tr key={scenario.id}><th scope="row">{scenario.label} ({scenario.id})</th>
          <td>{number(scenario.low)}</td><td>{number(scenario.high)}</td><td>{probabilityFraction(scenario.probability)}</td>
          <td>{scenario.probabilityType}: {scenario.reason || 'No further explanation supplied'}</td></tr>)}</tbody></table></div>
      {!data.scenarios.length && <p>No supported range scenario is available.</p>}
      <Facts items={[['Support (points)', number(data.levels?.support)], ['Resistance (points)', number(data.levels?.resistance)]]} />
    </InfoPopover>
    <InfoPopover label={`Data quality & model limitations (${new Set(data.warnings).size})`} showLabel>
      <ForecastWarnings items={data.warnings} />
    </InfoPopover>
    <Evidence value={data} label="Immutable analysis snapshot, source coverage and model evidence" />
    </div>
  </>;
}

function StructuralFundamentalRange({ value }: { value?: FundamentalRange | null }) {
  if (!value) return null;
  const bounds = [value.low, value.base, value.high];
  const available = value.status !== 'unavailable' && bounds.every(bound => bound != null && Number.isFinite(bound))
    && value.low! <= value.base! && value.base! <= value.high!;
  const coverage = (fraction?: number | null) => fraction != null && Number.isFinite(fraction) && fraction >= 0 && fraction <= 1
    ? `${number(fraction * 100)}%` : 'Unavailable';
  return <section className="options-card options-structural-range" aria-label="Structural fundamental range">
    <span className="options-eyebrow">INDEPENDENT INPUT · WEIGHTED VALUATION SENSITIVITY</span>
    <div className="options-section-heading"><h2>Structural fundamental range</h2><span className="options-status is-pending">{value.status}</span></div>
    <p><strong>Structural weighted-index proxy, NOT a Daily/Weekly target or an expiry return prediction.</strong></p>
    <div className="options-range-grid">{(['low', 'base', 'high'] as const).map(key => <article className="options-range-card" key={key}>
      <h3>{key === 'low' ? 'Lower' : key === 'base' ? 'Base' : 'Upper'} sensitivity</h3>
      <strong className="options-range-value">{available ? number(value[key]) : 'Unavailable'}</strong>
      <span>Structural index points · not a run-target band</span>
    </article>)}</div>
    <Facts items={[['Covered constituent weight', coverage(value.coverage)], ['Stored stock-AI valuation coverage', coverage(value.aiCoverage)],
      ['Stored analyst-target coverage', coverage(value.analystCoverage)], ['Mathematical fallback coverage', coverage(value.mathematicalCoverage)],
      ['Uncovered weight', value.uncoveredWeightPct == null ? 'Unavailable' : `${number(value.uncoveredWeightPct)}%`]]} />
    <InfoPopover label="Structural aggregation method, horizon and coverage evidence" showLabel>
      <p>Stock valuation horizons are not compressed into the selected option horizon.</p>
      {value.reason && <p>{value.reason}</p>}<pre>{evidenceJson(value)}</pre>
    </InfoPopover>
  </section>;
}
function FundamentalInfluences({ data }: { data: ForecastAnalysis }) {
  const groups = data.dimensions.filter(group => group.key === 'fundamentals' && group.indicators.length > 0);
  if (!groups.length) return null;
  return <section className="options-card" aria-label="Fundamental indicator influences">
    <div className="options-section-heading"><h2>Fundamental indicator influences</h2><InfoPopover label="Fundamental influence weighting">
      <p>These are supplied model contributions to directional bias, not structural index points, option payoff or calibrated probabilities.
        Coverage-adjusted fractions are already normalised by the backend. Legacy raw votes are not converted into weighted influence.</p>
    </InfoPopover></div>
    {groups.map(group => <div className="options-metric-grid" key={group.key}>
      {group.indicators.map(metric => <MetricCard key={metric.key} metric={metric} group={group} capturedAt={data.asOf} />)}
    </div>)}
  </section>;
}
function Constituents({ items, weights }: { items: ConstituentValuation[]; weights?: WeightReference | null }) {
  const [query, setQuery] = useState('');
  const [coverage, setCoverage] = useState('all');
  const [sector, setSector] = useState('');
  const sectors = [...new Set(items.map(item => item.sector).filter((value): value is string => !!value))];
  const filtered = items.filter(item => `${item.symbol} ${item.companyName}`.toLowerCase().includes(query.toLowerCase())
    && (!sector || item.sector === sector)
    && (coverage === 'all' || (coverage === 'available' ? item.fairBase != null : item.fairBase == null)));
  return <section className="options-card">
    <div className="options-section-heading"><h3>Constituent valuations</h3><InfoPopover label="Valuation sources and horizons">
      <p>Sources and horizons remain distinct: stored stock-AI valuations, twelve-month analyst targets, or mathematical fallback. These are structural inputs, not Daily/Weekly price predictions.</p>
    </InfoPopover></div>
    <WeightProvenance weights={weights} items={items} />
    <div className="options-controls">
      <label>Company / symbol<input value={query} onChange={event => setQuery(event.target.value)} type="search" /></label>
      <label>Valuation coverage<select value={coverage} onChange={event => setCoverage(event.target.value)}>
        <option value="all">All constituents</option><option value="available">Available</option><option value="unavailable">Unavailable</option>
      </select></label>
      {sectors.length > 0 && <label>Sector<select value={sector} onChange={event => setSector(event.target.value)}>
        <option value="">All sectors</option>{sectors.map(value => <option key={value}>{value}</option>)}
      </select></label>}
    </div>
    <div className="options-table-wrap"><table><caption>{filtered.length} of {items.length} constituent records</caption>
      <thead><tr><th>Constituent / weight</th><th>Result period</th><th>Captured price</th><th>Fair low / base / high</th><th>Signed downside / upside (low / base / high)</th><th>Method / evidence</th></tr></thead>
      <tbody>{filtered.map(item => <tr key={item.symbol}>
        <th scope="row">{item.symbol}<small>{item.companyName}</small>
          <small>{number(item.weightPct)}% {weights?.sourceType === 'etf-holdings-proxy' || isEtfProxy(item.weightSource) ? 'equity-normalized ETF proxy weight' : 'captured constituent weight'}</small>
          <small>Weight as of: {item.weightAsOf || weights?.sourceDate || 'Unavailable'}</small>
          <small>Weight source: {item.weightSource || weights?.sourceType || 'Unavailable in original snapshot'}</small></th>
        <td>{item.latestQuarterEnd || 'Unavailable'}</td><td>{money(item.livePrice)}</td>
        <td>{money(item.fairLow)} / {money(item.fairBase)} / {money(item.fairHigh)}</td>
        <td>{signed(item.lowMovePct)} / {signed(item.baseMovePct)} / {signed(item.highMovePct)}</td>
        <td>{item.method}<small>Valuation source: {item.valuationSource || item.method || 'Unavailable'}</small>
          {item.valuationAsOf && <small>Valuation as of: {timestamp(item.valuationAsOf)}</small>}
          {item.analystCount != null && <small>Stored analyst count: {item.analystCount} · twelve-month targets</small>}
          {item.aiModel && <small>Stored stock-AI model: {item.aiModel}</small>}
          <InfoPopover label={`Valuation ${item.symbol}`}>
          <p>{item.reason || (item.fairBase == null ? 'Required valuation inputs unavailable.' : '')}</p>
          {(item.earningsGrowthYoyPct != null || item.revenueGrowthYoyPct != null) && <details className="options-inline-details">
            <summary>Latest-quarter growth inputs</summary>
            <Facts items={[['Reported earnings growth YoY', signed(item.earningsGrowthYoyPct)], ['Reported revenue growth YoY', signed(item.revenueGrowthYoyPct)]]} />
          </details>}
          {!!item.aiPriceForecasts?.length && <details className="options-inline-details"><summary>Stored stock-AI forecasts · separate horizons</summary>
            <p>Existing stock forecasts are reused as recorded; they are not this run's Daily/Weekly predictions.</p>
            <table><thead><tr><th>Stock forecast horizon</th><th>Low / base / high</th></tr></thead><tbody>
              {item.aiPriceForecasts.map((forecast, i) => <tr key={`${forecast.horizon}-${i}`}><th scope="row">{forecast.horizon}</th>
                <td>{money(forecast.low)} / {money(forecast.base)} / {money(forecast.high)}</td></tr>)}
            </tbody></table>
          </details>}
          {item.indexContributions && <details className="options-inline-details"><summary>Structural index-point contribution</summary>
            <Facts items={[['Lower sensitivity contribution', number(item.indexContributions.low)], ['Base sensitivity contribution', number(item.indexContributions.base)],
              ['Upper sensitivity contribution', number(item.indexContributions.high)]]} />
          </details>}
          <h4>Financial evidence for {item.symbol}</h4><pre>{evidenceJson(item)}</pre>
          </InfoPopover></td>
      </tr>)}</tbody></table></div>
    {!filtered.length && <p>No matching valuations. Automatic public-reference refresh is pending or unavailable where source facts are missing.</p>}
  </section>;
}

function ExecutionStatus({ candidate }: { candidate: OptionCandidate }) {
  return <span className={`options-execution ${candidate.executionStatus === 'planning-only' ? 'is-planning' : ''}`}>
    {candidate.executionStatus === 'planning-only' ? 'Planning only · NOT currently executable'
      : candidate.executionStatus === 'live-ready' || candidate.executionStatus === 'executable' ? 'Live-ready at capture · revalidate live'
        : candidate.executionStatus === 'unavailable' ? 'Execution unavailable · not an entry suggestion' : 'Snapshot alternative · revalidate live'}
  </span>;
}
function CandidateDetail({ candidate }: { candidate: OptionCandidate }) {
  const reference = candidate.executionStatus === 'planning-only' || candidate.legs.some(leg => leg.priceBasis === 'last-traded-reference');
  return <details className="options-candidate" id={`option-candidate-${candidate.id}`}>
    <summary>{candidate.id} · {candidate.strategy} · {candidate.moneyness || 'Structure'} · expiry {candidate.expiry} · {candidate.riskTier} risk <ExecutionStatus candidate={candidate} /></summary>
    <p className="options-age-warning">Quote reference: {timestamp(candidate.quoteAsOf || candidate.quoteObservedAt)} · live revalidation required</p>
    {reference && <p className="options-inline-warning">Reference premiums only, not entry fills. Last-traded references do not establish executable bid/ask or a hypothetical fill.</p>}
    {candidate.side === 'selling' && <IndexExitRule candidate={candidate} />}
    <InfoPopover label="Candidate execution and risk assumptions">
      <p>Per disclosed structure / quantities below. These are alternatives, not an allocated portfolio.</p>
      {candidate.requiresLiveRevalidation && <p>Fresh synchronous live bid/ask revalidation is required before considering any entry.</p>}
      {candidate.riskTierRule && <p>Risk-tier model rule: {candidate.riskTierRule}</p>}
      <Warnings items={candidate.riskTierReasons} />
    </InfoPopover>
    <Facts items={[
      [reference ? 'Reference premium (signed credit/debit, before charges)' : 'Captured premium estimate (signed credit/debit, before charges)', money(candidate.netPremium)],
      ['Maximum loss', money(candidate.maxLoss)], ['Maximum profit', reward(candidate)],
      ['Gross expiry maximum loss (before charges)', money(candidate.grossMaxLoss)],
      ['Gross expiry maximum profit (before charges)', grossReward(candidate)],
      ['Estimated costs', money(candidate.costEstimate)], ['Capital basis', money(candidate.capitalBasis)],
      ['Reward / risk', number(candidate.rewardRisk)], ['Lot size', number(candidate.lotSize)],
      ['Unconditional profit probability (fraction 0–1)', probabilityFraction(candidate.probabilityOfProfit)], ['Unconditional probability type', candidate.probabilityType],
      ['Technical lower stop', number(candidate.technicalStop?.lower)], ['Technical upper stop', number(candidate.technicalStop?.upper)],
      ['Stop trigger', candidate.technicalStop?.trigger || 'Unavailable'],
      ['Estimated stop loss (not guaranteed)', money(candidate.estimatedStopLoss)],
      ['Premium target', money(candidate.targetPremium)], ['Profit target', money(candidate.targetProfit)],
      ['Gross profit target (before charges)', money(candidate.grossTargetProfit)],
      ['Planned exit', timestamp(candidate.plannedExitAt)], ['Scenario IDs', candidate.scenarioIds.join(', ') || 'Unavailable'],
    ]} />
    {candidate.costEstimate == null && <p className="options-inline-warning">Charges are unavailable · gross before charges only · no fee-inclusive loss cap.</p>}
    <InfoPopover label="Model metrics and stop estimates">{candidate.modelMetricsReason && <p>{candidate.modelMetricsReason}</p>}
      {candidate.estimatedStopLossReason && <p>{candidate.estimatedStopLossReason}</p>}
      <p>{candidate.probabilityReason || 'No additional probability calibration evidence supplied.'}</p>
    </InfoPopover>
    <InfoPopover label="Scenario-conditional model profit probabilities" showLabel>
    <p>These condition on terminal index spot being inside the stated interval at the stated time. They are distinct from the unconditional probability above, are not first-hit stop/target probabilities, and are not historically calibrated. Grid counts describe numerical quadrature resolution, not empirical or historical sample evidence.</p>
    {candidate.scenarioProfitProbabilities && Object.keys(candidate.scenarioProfitProbabilities).length > 0
      ? <div className="options-table-wrap"><table><thead><tr><th>Scenario ID</th><th>Condition interval (points)</th><th>Condition time</th><th>Conditional probability (fraction 0–1)</th><th>Type</th><th>Matched / total grid points</th><th>Model limitations</th></tr></thead>
        <tbody>{Object.entries(candidate.scenarioProfitProbabilities).map(([id, value]) => <tr key={id}>
          <th scope="row">{id}</th><td>{number(value.condition.low)}–{number(value.condition.high)}</td><td>{timestamp(value.condition.at)}</td>
          <td>{probabilityFraction(value.probability)}</td><td>{value.probabilityType}</td><td>{number(value.matchedGridPoints)} / {number(value.gridPoints)}</td><td>{value.reason}</td>
        </tr>)}</tbody></table></div>
      : <p>Conditional probabilities were not supplied in this original snapshot. Unconditional probability is not substituted.</p>}
    </InfoPopover>
    <div className="options-table-wrap"><table><caption>Original contract legs — no order is submitted</caption>
      <thead><tr><th>Direction</th><th>Exchange / contract</th><th>Token</th><th>Option / strike</th><th>Quantity</th><th>{reference ? 'Reference premium' : 'Captured premium estimate'}</th><th>Price basis</th><th>Quote observed</th></tr></thead>
      <tbody>{candidate.legs.map((leg, i) => <tr key={`${leg.tradingSymbol}-${i}`}>
        <td>{leg.direction}</td><th scope="row">{leg.exchange}: {leg.tradingSymbol}</th><td>{leg.instrumentToken}</td>
        <td>{leg.optionType} {number(leg.strike)}</td><td>{leg.quantity}</td><td>{money(leg.entryPrice)}</td>
        <td>{leg.priceBasis === 'last-traded-reference' ? <strong className="options-inline-warning">Last-traded reference · no executable book</strong>
          : leg.priceBasis === 'captured-bid-ask' ? 'Captured bid/ask reference' : 'Unavailable in original snapshot'}</td>
        <td>{timestamp(leg.quoteObservedAt || leg.observedAt)}</td>
      </tr>)}</tbody></table></div>
    {['long-call', 'long-put'].includes(candidate.strategy) && <small className="options-age-warning">Entire premium debit at risk · time decay, IV and slippage matter.</small>}
    <InfoPopover label="Trade warnings and monitoring rules" showLabel><Warnings items={candidate.warnings} />
      <p>Technical stops and premium targets are monitoring rules, not automatic exits or guaranteed fills. Gaps, slippage and costs affect outcomes. Maximum payoff and estimated pre-expiry exit are different.</p>
    </InfoPopover>
    <Evidence value={candidate} label="Complete original candidate evidence / payoff assumptions" />
  </details>;
}
function isHedged(candidate: OptionCandidate) {
  return ['put-credit-spread', 'call-credit-spread', 'iron-condor', 'iron-fly'].includes(candidate.strategy)
    && candidate.legs.some(leg => leg.direction === 'buy') && candidate.legs.some(leg => leg.direction === 'sell')
    && ((candidate.maxLoss != null && Number.isFinite(candidate.maxLoss) && candidate.maxLoss > 0)
      || (candidate.costEstimate == null && candidate.grossMaxLoss != null && Number.isFinite(candidate.grossMaxLoss) && candidate.grossMaxLoss > 0));
}
function IndexExitRule({ candidate }: { candidate: OptionCandidate }) {
  const lower = candidate.technicalStop?.lower, upper = candidate.technicalStop?.upper;
  const hasLower = lower != null && Number.isFinite(lower) && lower > 0;
  const hasUpper = upper != null && Number.isFinite(upper) && upper > 0;
  return <section className="options-index-stop" aria-label="Index-level stop loss">
    <h5>Index-level SL · index points</h5>
    {!hasLower && !hasUpper && <strong>Unavailable — no valid index breach level supplied</strong>}
    {hasLower && <strong>Exit if index ≤ {number(lower)}</strong>}
    {hasUpper && <strong>Exit if index ≥ {number(upper)}</strong>}
    <small>Manual exit rule · no automatic execution or guaranteed fill</small>
  </section>;
}
function SelectedSellingPlan({ candidate, analysis }: { candidate: OptionCandidate; analysis?: ForecastAnalysis | null }) {
  return <div className="options-selling-plan">
    <IndexExitRule candidate={candidate} />
    <h5>Hedged legs · reference premiums</h5>
    <ul className="options-selected-legs">{candidate.legs.map((leg, index) => <li key={`${leg.tradingSymbol}-${index}`}>
      <div><strong>{leg.direction.toUpperCase()} {leg.quantity} × {leg.optionType} {number(leg.strike)}</strong><strong>{money(leg.entryPrice)} ref</strong></div>
      <small>{leg.exchange}: {leg.tradingSymbol}</small>
      {leg.priceBasis === 'last-traded-reference' && <small className="options-age-warning">Last-traded reference · no executable book</small>}
    </li>)}</ul>
    <Facts items={[['Index reference', number(analysis?.spot)], ['Premium reference (before charges)', money(candidate.netPremium)],
      ['Expiry', candidate.expiry], [candidate.costEstimate == null ? 'Gross target profit' : 'Net target profit', money(candidate.costEstimate == null ? candidate.grossTargetProfit : candidate.targetProfit)],
      ['Premium target', money(candidate.targetPremium)], ['Time exit', timestamp(candidate.plannedExitAt || analysis?.plannedExitAt)]]} />
  </div>;
}
function Strategies({ side, candidates }: { side: StrategySide; candidates: OptionCandidate[] }) {
  const [expiry, setExpiry] = useState('');
  const [tier, setTier] = useState('');
  const eligible = candidates.filter(candidate => candidate.side === side && (side !== 'selling' || isHedged(candidate)));
  const expiries = [...new Set(eligible.map(candidate => candidate.expiry))].sort();
  const ranks = { ITM: 0, ATM: 1, OTM: 2 };
  const filtered = eligible.filter(candidate => (!expiry || expiry === candidate.expiry) && (!tier || tier === candidate.riskTier))
    .sort((a, b) => (ranks[a.moneyness as keyof typeof ranks] ?? 3) - (ranks[b.moneyness as keyof typeof ranks] ?? 3)
      || a.expiry.localeCompare(b.expiry));
  const rejected = candidates.filter(candidate => candidate.side === side).length - eligible.length;
  const planningCount = eligible.filter(candidate => candidate.executionStatus === 'planning-only').length;
  return <section className="options-card">
    <div className="options-section-heading">
      <h2>{side === 'selling' ? 'Selling strategy alternatives' : 'Buying strategy alternatives'}</h2>
      <span className="options-status is-pending">{eligible.length} {side} alternative{eligible.length === 1 ? '' : 's'}</span>
    </div>
    {planningCount > 0 && <p className="options-inline-warning">{planningCount} planning-only alternative{planningCount === 1 ? '' : 's'} · NOT currently executable. Reference quotes, not closing prints, require live revalidation.</p>}
    {eligible.length > 0 && <InfoPopover label={`${side === 'selling' ? 'Selling' : 'Buying'} strategy safeguards`}><p>{side === 'selling'
      ? 'Only expiry-payoff-bounded hedged structures are displayed. Unknown charges mean no fee-inclusive loss cap. Compare complete legs and same-day expiry risk.'
      : 'Compare long calls/puts and debit spreads across eligible expiries. Cheaper OTM premiums do not imply lower risk. Same-day buying is excluded by the analysis policy.'}</p></InfoPopover>}
    {rejected > 0 && <p role="alert">{rejected} selling candidate(s) withheld: a verified bounded-risk hedged structure was not supplied.</p>}
    {eligible.length > 0 && <><div className="options-controls">
      <label>Expiry<select value={expiry} onChange={event => setExpiry(event.target.value)}>
        <option value="">Compare all expiries</option>{expiries.map(value => <option key={value}>{value}</option>)}
      </select></label>
      <label>Risk tier<select value={tier} onChange={event => setTier(event.target.value)}>
        <option value="">All tiers</option>{['low', 'medium', 'high'].map(value => <option key={value}>{value}</option>)}
      </select></label>
    </div>
    <div className="options-table-wrap"><table><caption>{filtered.length} of {eligible.length} eligible {side} alternatives — open details below for exact legs and rules</caption>
      <thead><tr><th>Candidate</th><th>Moneyness / strategy</th><th>Expiry</th><th>Tier</th><th>Net max loss</th><th>Net max reward</th><th>Gross expiry loss / reward (before charges)</th><th>Costs</th><th>Probability type</th><th>Planned exit</th></tr></thead>
      <tbody>{filtered.map(candidate => <tr key={candidate.id}>
        <th scope="row"><a href={`#option-candidate-${candidate.id}`}>{candidate.id}</a><ExecutionStatus candidate={candidate} />
          <small>Quotes: {timestamp(candidate.quoteAsOf || candidate.quoteObservedAt)}</small></th>
        <td>{candidate.moneyness || 'Structure'} / {candidate.strategy}</td><td>{candidate.expiry}</td><td>{candidate.riskTier}</td>
        <td>{money(candidate.maxLoss)}</td><td>{reward(candidate)}</td>
        <td>{money(candidate.grossMaxLoss)} / {grossReward(candidate)}</td><td>{money(candidate.costEstimate)}</td>
        <td>{candidate.probabilityType}</td><td>{timestamp(candidate.plannedExitAt)}</td>
      </tr>)}</tbody></table></div></>}
    {!filtered.length && <p>{eligible.length ? `No ${side} alternatives match these filters.` : `No broker-quote-supported ${side} alternatives in this saved snapshot.`}</p>}
    {filtered.map(candidate => <CandidateDetail key={candidate.id} candidate={candidate} />)}
  </section>;
}

function AiSelection({ detail, openCandidate, side = 'selling' }: { detail: ForecastDetail; openCandidate: (candidate: OptionCandidate) => void; side?: StrategySide }) {
  const { ai, analysis } = detail;
  const tiers: RiskTier[] = ['low', 'medium', 'high'];
  const selectedTiers = tiers.filter(tier => {
    const recommendation = ai?.selection?.recommendations.find(item => item.side === side && item.riskTier === tier);
    return analysis?.candidates.some(item => item.id === recommendation?.candidateId && item.side === side && item.riskTier === tier
      && !!analysis.scenarios.find(scenario => scenario.id === ai?.selection?.scenarioId)
      && item.scenarioIds.includes(ai?.selection?.scenarioId || '') && (side !== 'selling' || isHedged(item)));
  });
  const selectedScenario = analysis?.scenarios.find(item => item.id === ai?.selection?.scenarioId);
  const rangeReason = qualitativeRangeReason(ai?.selection?.scenarioReason);
  return <section className="options-card">
    <div className="options-section-heading">
      <h3><ShieldCheck size={18} aria-hidden="true" /> {side === 'selling' ? 'Selling' : 'Buying'} AI selection</h3>
      <span className="options-status is-pending" role="status">{ai?.status || 'pending'}</span>
    </div>
    {ai?.errorMessage && <p className="options-error" role="alert">{ai.errorMessage}</p>}
    {ai?.status !== 'completed' && <p>AI selection is {ai?.status || 'pending'}. Deterministic alternatives remain independent.</p>}
    {ai?.status === 'completed' && selectedTiers.length === 0 && <p>{analysis?.candidates.some(item => item.side === side)
      ? `No ${side} alternative was selected by AI. No suitable side/tier/scenario match.`
      : `This snapshot contains no candidates for this side. No supported ${side} plan is available.`}</p>}
    {ai?.status === 'completed' && <>{(selectedScenario || ai.selection?.scenarioId) && <p>Selected scenario: {selectedScenario
      ? `${selectedScenario.label}: ${number(selectedScenario.low)}–${number(selectedScenario.high)} points`
      : 'Unavailable — selection does not reference a stored scenario.'}</p>}
    {selectedScenario && rangeReason && <InfoPopover label="Selected horizon rationale"><p>{rangeReason}</p></InfoPopover>}
    {selectedTiers.length > 0 && <div className="options-shortlist-grid">
      {selectedTiers.map(tier => {
        const recommendation = ai?.selection?.recommendations.find(item => item.side === side && item.riskTier === tier);
        const candidate = analysis?.candidates.find(item => item.id === recommendation?.candidateId && item.side === side && item.riskTier === tier
          && !!selectedScenario && item.scenarioIds.includes(selectedScenario.id)
          && (side !== 'selling' || isHedged(item)));
        const conditional = selectedScenario ? candidate?.scenarioProfitProbabilities?.[selectedScenario.id] : undefined;
        return <article className={`options-selection ${candidate ? 'has-candidate' : 'is-no-trade'}`} key={`${side}-${tier}`}><h4>{side} <span>· {tier} risk</span></h4>
          {candidate ? <>
            <button type="button" className="options-strategy-link" onClick={() => openCandidate(candidate)}>{candidate.strategy.replaceAll('-', ' ')} <ArrowRight size={14} aria-hidden="true" /></button>
            <ExecutionStatus candidate={candidate} />
            <small className="options-age-warning">Quote reference: {timestamp(candidate.quoteAsOf || candidate.quoteObservedAt)} · revalidate live</small>
            <p className="options-muted">{candidate.moneyness || 'Structure'} · {candidate.expiry}</p>
            {candidate.costEstimate == null ? <p className="options-inline-warning">Gross expiry loss {money(candidate.grossMaxLoss)} before charges. Net loss cap unavailable.</p>
              : <p>Net max loss {money(candidate.maxLoss)} · max reward {reward(candidate)}</p>}
            {side === 'selling' && <SelectedSellingPlan candidate={candidate} analysis={analysis} />}
            {side === 'buying' && <small>Premium debit can be lost; technical stops are not guaranteed.</small>}
            {candidate.riskTierReasons?.filter(item => /same.day|OTM|gap|slippage|uncapped/i.test(item)).slice(0, 2).map(item =>
              <small className="options-age-warning" key={item}>{item}</small>)}
            <InfoPopover label={`${side} ${tier} rationale and risks`}><p>{recommendation?.reason}</p><Warnings items={candidate.riskTierReasons} /></InfoPopover>
            <InfoPopover label="Conditional probability & costs">
              <p>Costs: {money(candidate.costEstimate)}. Selected-scenario conditional model profit probability: {probabilityFraction(conditional?.probability)} (fraction 0–1)
                {' '}· {conditional?.probabilityType || 'unavailable'}. Not the unconditional probability of profit.</p>
              {conditional && <p>Condition: terminal index {number(conditional.condition.low)}–{number(conditional.condition.high)} points at {timestamp(conditional.condition.at)}. {conditional.reason}</p>}
            </InfoPopover>
          </> : <><strong className="options-no-trade-label">No suitable trade</strong>
            <p>{recommendation?.reason || 'No supported alternative selected for this tier.'}</p></>}
        </article>;
      })}
    </div>}</>}
    <InfoPopover label="Selection rules & AI usage" showLabel>
      <p>AI only selects precomputed scenario/candidate IDs. Opening history or changing tabs never requests AI; this page has no separate paid AI rerun.</p>
      <p>AI usage: {number(ai?.inputTokens)} input / {number(ai?.outputTokens)} output tokens · cost USD {number(ai?.costUsd)}.</p>
      <Warnings items={ai?.selection?.recommendations.filter(item => item.side === side && !item.candidateId).map(item => item.reason)} />
    </InfoPopover>
  </section>;
}

function forecastDeletionReason(item: ForecastSummary, detail?: ForecastDetail | null, job?: JobRun | null) {
  const active = (status?: string | null) => !!status && ['pending', 'queued', 'running', 'processing', 'in_progress'].includes(status.toLowerCase());
  if (active(item.status)) return 'Forecast is still active.';
  if (active(item.aiStatus) || active(item.ai?.status) || active(detail?.ai?.status)) return 'AI selection is still active.';
  if (active(item.jobStatus) || active(item.jobRunStatus) || active(job?.status)) return 'Forecast job is still active.';
  return '';
}

function forecastAfterRemoval(items: ForecastSummary[], selectedId: number | null, removedId: number) {
  const history = items.filter(item => item.id !== removedId);
  return { history, selectedId: selectedId === removedId ? history[0]?.id ?? null : selectedId };
}

function ConfirmForecastDelete({ id, reason, cancel, confirm, returnFocus }: {
  id: number; reason: string; cancel: () => void; confirm: () => void; returnFocus: HTMLButtonElement | null;
}) {
  const labelId = useId();
  const cancelButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelButton.current?.focus();
    return () => { if (returnFocus?.isConnected) returnFocus.focus(); };
  }, [returnFocus]);
  return <div className="options-delete-overlay">
    <section className="options-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby={`${labelId}-title`} aria-describedby={`${labelId}-description`}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); cancel(); }
        if (event.key === 'Tab') {
          const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
          const first = buttons[0], last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
      <h3 id={`${labelId}-title`}>Delete forecast #{id}?</h3>
      <p id={`${labelId}-description`}>This permanently removes this saved forecast from your history. This cannot be undone.</p>
      {reason && <p role="status">{reason} Wait for it to finish before deleting.</p>}
      <div className="options-delete-actions">
        <button ref={cancelButton} className="btn btn-secondary" onClick={cancel}>Cancel</button>
        <button className="btn options-delete-confirm" disabled={!!reason} onClick={() => { if (!reason) confirm(); }}>Delete permanently</button>
      </div>
    </section>
  </div>;
}

function History({ items, selectedId, open, onDelete, deletingId = null, detail, job }: {
  items: ForecastSummary[]; selectedId: number | null; open: (id: number) => void;
  onDelete?: (id: number) => void; deletingId?: number | null; detail?: ForecastDetail | null; job?: JobRun | null;
}) {
  const [status, setStatus] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [generatedDate, setGeneratedDate] = useState('');
  const [confirmation, setConfirmation] = useState<{ id: number; trigger: HTMLButtonElement } | null>(null);
  const reasonFor = (item: ForecastSummary) => forecastDeletionReason(item, detail?.id === item.id ? detail : null, item.jobRunId === job?.id ? job : null);
  const confirming = items.find(item => item.id === confirmation?.id);
  const istDate = (value?: string | null) => timestamp(value) !== 'Unavailable'
    ? new Date(value!.trim()).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }) : '';
  const filtered = items.filter(item => (!status || item.status === status)
    && (!targetDate || istDate(item.targetAt) === targetDate) && (!generatedDate || istDate(item.generatedAt) === generatedDate));
  return <section className="options-card">
    <div className="options-section-heading"><h3>Immutable forecast history</h3><InfoPopover label="Forecast history">
      <p>Index and mode filters above scope this list. Each explicit successful generation is a separate original prediction, never a reconstructed past forecast.</p>
    </InfoPopover></div>
    <div className="options-controls">
      <label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="">All</option>
        {[...new Set(items.map(item => item.status))].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Target / expiry date (IST)<input type="date" value={targetDate} onChange={event => setTargetDate(event.target.value)} /></label>
      <label>Generated date (IST)<input type="date" value={generatedDate} onChange={event => setGeneratedDate(event.target.value)} /></label>
    </div>
    <div className="options-table-wrap"><table><thead><tr><th>Original version</th><th>Generated (IST)</th><th>Target (IST)</th><th>Status</th><th>Model</th><th>Actions</th></tr></thead>
      <tbody>{filtered.map(item => <tr key={item.id} aria-current={selectedId === item.id ? 'true' : undefined}>
        <th scope="row"><button className="btn btn-secondary" onClick={() => open(item.id)}>Open #{item.id} · {item.indexCode} · {item.mode}</button></th>
        <td>{timestamp(item.generatedAt)}</td><td>{timestamp(item.targetAt)}</td><td>{item.status}</td><td>{item.modelVersion || 'Unavailable'}</td>
        <td><button className="options-icon-button options-history-delete" aria-label={`${deletingId === item.id ? 'Deleting' : 'Delete'} forecast #${item.id}`}
          title={reasonFor(item) || (deletingId != null ? 'A forecast deletion is in progress.' : 'Delete this saved forecast')}
          disabled={!onDelete || deletingId != null || !!reasonFor(item)}
          onClick={event => {
            if (!onDelete || deletingId != null || reasonFor(item)) return;
            setConfirmation({ id: item.id, trigger: event.currentTarget });
          }}>{deletingId === item.id ? <LoaderCircle size={16} className="options-spin" aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}</button>
          {reasonFor(item) && <small className="options-delete-reason">{reasonFor(item)}</small>}
        </td>
      </tr>)}</tbody></table></div>
    {!filtered.length && <p>No forecasts match. Use Get Forecast explicitly to create a prospective prediction.</p>}
    {confirming && onDelete && <ConfirmForecastDelete id={confirming.id}
      reason={reasonFor(confirming) || (deletingId != null ? 'A forecast deletion is in progress.' : '')}
      returnFocus={confirmation?.trigger || null} cancel={() => setConfirmation(null)}
      confirm={() => { setConfirmation(null); onDelete(confirming.id); }} />}
  </section>;
}

function Reconciliation({ detail }: { detail: ForecastDetail }) {
  const actual = detail.outcome?.payload;
  const verdict = (value?: boolean | null) => value === true ? 'Yes' : value === false ? 'No' : 'Unavailable';
  return <section className="options-card">
    <h3>Original prediction versus observed outcome</h3>
    <Facts items={[
      ['Original bias', detail.analysis?.bias || 'Unavailable'], ['Original spot', number(detail.analysis?.spot)],
      ['Original target', timestamp(detail.targetAt)], ['Outcome status', detail.outcome?.status || 'Pending data'],
    ]} />
    <h4>Actual observations (as recorded)</h4>
    <Facts items={[
      ['Actual close (points)', number(actual?.actualClose)], ['Actual return', signed(actual?.actualReturnPct)],
      ['Actual direction', actual?.actualDirection || 'Unavailable'], ['Direction correct', verdict(actual?.directionCorrect)],
      ['Observed high (sampled points)', number(actual?.observedHigh)], ['Observed low (sampled points)', number(actual?.observedLow)],
      ['Coverage type', actual?.coverageType || 'Unavailable'], ['Terminal source', actual?.terminalSource || 'Unavailable'],
      ['Path coverage status', actual?.pathStatus || 'Unavailable'],
    ]} />
    {actual?.warning && <Warnings items={[actual.warning]} />}
    {!!actual?.scenarios?.length && <div className="options-table-wrap"><table>
      <caption>Original scenario versus terminal actual — terminal coverage is not path-range coverage</caption>
      <thead><tr><th>Original scenario ID</th><th>Original low</th><th>Original high</th><th>Terminal actual inside interval</th></tr></thead>
      <tbody>{actual.scenarios.map(scenario => <tr key={scenario.id}><th scope="row">{scenario.id}</th>
        <td>{number(scenario.low)}</td><td>{number(scenario.high)}</td><td>{verdict(scenario.terminalCovered)}</td>
      </tr>)}</tbody></table></div>}
    <InfoPopover label="Observed outcome coverage"><p>The prediction above remains unchanged. Terminal-price coverage, path-range coverage and direction accuracy are distinct; missing or ambiguous observations are not wins.</p></InfoPopover>
    <Evidence value={detail.outcome || { status: 'pending', reason: 'No observed outcome supplied yet.' }} label="Raw persisted forecast outcome — actuals, coverage and version when supplied" />
    <h4>Hypothetical strategy outcomes — not real trades</h4>
    <InfoPopover label="Hypothetical outcome assumptions"><p>Every candidate is an independent hypothetical alternative. Results do not claim orders, fills or a combined portfolio return. Stops and targets are not executed automatically.</p>
      <p>Values below are the recorded outcome payload, not reconstructed price-path evidence. No option-premium chart or fill path is invented from index candles.</p></InfoPopover>
    {(detail.strategyOutcomes || []).map((outcome, i) => {
      const payload = outcome.payload || outcome;
      const candidate = detail.analysis?.candidates.find(item => item.id === (outcome.candidateId || outcome.candidateKey));
      const planning = candidate?.executionStatus === 'planning-only';
      const unavailable = planning || (payload.status || outcome.status) === 'unavailable';
      return <article className="options-candidate" key={i}>
        <h4>Hypothetical outcome {String(outcome.candidateId || outcome.candidateKey || i + 1)} · {payload.status || outcome.status || 'Pending data'}</h4>
        <Facts items={[
          ['Recorded net hypothetical P&L', money(unavailable ? null : payload.netHypotheticalPnl)],
          ['Recorded gross hypothetical P&L (before charges)', money(unavailable ? null : payload.grossHypotheticalPnl)],
          ['Observed exit time', timestamp(unavailable ? null : payload.exitAt)], ['Exit reason / data limitation', payload.reason || 'Unavailable'],
          ['Quote coverage type', payload.coverageType || 'Unavailable'],
          ['Event order certain', payload.eventOrderCertain === false ? 'No — ambiguous / sampled sequence' : verdict(payload.eventOrderCertain)],
        ]} />
        {planning && <p>Planning reference premiums are not entry fills. Strategy P&amp;L and observed exits are unavailable for this plan.</p>}
        {payload.warning && <Warnings items={[payload.warning]} />}
        <InfoPopover label={`Outcome interpretation ${String(outcome.candidateId || outcome.candidateKey || i + 1)}`}>
          <p>Partial, ambiguous, unavailable and pending outcomes are not final realised fills or wins. Net hypothetical P&amp;L is the recorded netHypotheticalPnl field; other engine P&amp;L fields are not substituted.</p>
        </InfoPopover>
        <Evidence value={outcome} label="Raw persisted hypothetical outcome payload" />
      </article>;
    })}
    {!detail.strategyOutcomes?.length && <p>Pending data: no reconciled strategy outcomes are available. Collector gaps, insufficient quote coverage and uncertain event order must remain unresolved.</p>}
  </section>;
}

const tabId = (tab: Tab) => tab.toLowerCase().replaceAll(' ', '-');
function WorkspaceTabs({ tab, onChange, historyCount }: { tab: Tab; onChange: (tab: Tab) => void; historyCount: number }) {
  function keyboard(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' ? (index + 1) % TABS.length : event.key === 'ArrowLeft' ? (index + TABS.length - 1) % TABS.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : null;
    if (next == null) return;
    event.preventDefault();
    onChange(TABS[next]);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#options-tab-${tabId(TABS[next])}`)?.focus();
  }
  return <nav className="options-tabs" role="tablist" aria-label="Options workspace views">{TABS.map((item, index) =>
    <button key={item} id={`options-tab-${tabId(item)}`} type="button" role="tab" className={`options-tab ${tab === item ? 'is-active' : ''}`}
      aria-selected={tab === item} aria-controls={`options-panel-${tabId(item)}`} tabIndex={tab === item ? 0 : -1}
      onKeyDown={event => keyboard(event, index)} onClick={() => onChange(item)}>
      {item}{item === 'History' && <span className="options-count">{historyCount}</span>}
    </button>)}</nav>;
}
function ForecastVersionBar({ detail, clock }: { detail: ForecastDetail; clock: number }) {
  const analysis = detail.analysis;
  const snapshot = analysis?.snapshotEvidence;
  const unavailable = analysis?.executionStatus === 'unavailable' || analysis?.snapshotPolicy?.executionStatus === 'unavailable';
  const planning = !unavailable && (analysis?.executionStatus === 'planning-only'
    || analysis?.snapshotPolicy?.executionStatus === 'planning-only'
    || (!!analysis?.candidates.length && analysis.candidates.every(item => item.executionStatus === 'planning-only')));
  const quoteAt = analysis?.quoteAsOf || analysis?.snapshotPolicy?.quoteAsOf || analysis?.quoteObservedAt
    || analysis?.candidates.find(item => item.quoteAsOf || item.quoteObservedAt)?.quoteAsOf
    || analysis?.candidates.find(item => item.quoteObservedAt)?.quoteObservedAt;
  const historical = !!analysis?.plannedExitAt && Date.parse(analysis.plannedExitAt) <= clock;
  const critical = [...new Set([detail.errorMessage, ...(analysis?.warnings || []).filter(item =>
    /authentication|unauthorized|forbidden|credentials|access token|re-login|login required/i.test(item))].filter((item): item is string => !!item))];
  return <>
    <section className="options-version-bar" aria-label="Selected forecast">
      <strong>Forecast #{detail.id} <span>{detail.indexCode} · {detail.mode}</span></strong>
      <span>Target {timestamp(detail.targetAt || analysis?.scenarios[0]?.targetAt || analysis?.plannedExitAt)}</span><span className={`options-status ${detail.status === 'failed' ? 'is-attention' : 'is-pending'}`}>{detail.status}</span>
    </section>
    {snapshot?.source === 'persisted-broker-snapshot' && <div className="options-snapshot-reference" aria-label="Snapshot source">
      <span className="options-status is-attention">Last-session snapshot</span>
      <span>As of {timestamp(snapshot.capturedAt)} · batch capture</span>
      <InfoPopover label="Last-session snapshot">
        <Facts items={[['Source', snapshot.source], ['Batch ID', snapshot.batchId ?? 'Unavailable'], ['Batch captured', timestamp(snapshot.capturedAt)]]} />
        <p>{snapshot.reason || 'Stored broker evidence; no quote freshness is inferred.'}</p>
        <p>Batch capture time is not an individual quote observation. Live revalidation is required before considering any entry.</p>
      </InfoPopover>
    </div>}
    <p className="options-execution-notice">
      {planning ? 'Planning only · NOT currently executable. Last-session observations, not official closing prints; revalidate live.'
        : unavailable ? 'No executable option snapshot · forecast price ranges remain available where supplied.'
          : historical ? 'Historical snapshot · planned exit passed. Not a fresh entry suggestion.'
          : 'Research snapshot · live quote revalidation required before considering any entry.'}
      {quoteAt ? ` Quotes observed ${timestamp(quoteAt)}.` : ''}
    </p>
    {critical.map(item => <p className="options-error" role="alert" key={item}>{item}</p>)}
  </>;
}
function ForecastViews({ tab, onTabChange, detail, history, selectedId, open, openCandidate, clock, job, onDelete, deletingId }: {
  tab: Tab; onTabChange: (tab: Tab) => void; detail: ForecastDetail | null; history: ForecastSummary[];
  selectedId: number | null; open: (id: number) => void; openCandidate: (candidate: OptionCandidate) => void; clock: number; job: JobRun | null;
  onDelete?: (id: number) => void; deletingId?: number | null;
}) {
  const data = detail?.analysis;
  return <>
    <WorkspaceTabs tab={tab} onChange={onTabChange} historyCount={history.length} />
    {detail && <ForecastVersionBar detail={detail} clock={clock} />}
    <section className="options-panel" role="tabpanel" id={`options-panel-${tabId(tab)}`} aria-labelledby={`options-tab-${tabId(tab)}`} tabIndex={0}>
      {tab === 'History' ? <><History items={history} selectedId={selectedId} open={open} onDelete={onDelete} deletingId={deletingId} detail={detail} job={job} />{detail && <Reconciliation detail={detail} />}</>
        : data && detail ? <>
          {tab === 'Analysis' && <>
            <Analysis data={data} targetAt={detail.targetAt} ai={detail.ai} />
            <InfoPopover label="Saved version timing & pipeline evidence" showLabel>
              <Facts items={[['Generated', timestamp(detail.generatedAt)], ['Input cutoff', timestamp(detail.inputCutoffAt)],
                ['Snapshot as of', timestamp(data.asOf)], ['Underlying quote observed', timestamp(data.spotObservedAt)],
                ['Model version', detail.modelVersion || data.modelVersion || 'Unavailable'],
                ['Entry cutoff', timestamp(data.entryCutoffAt)], ['Planned exit', timestamp(data.plannedExitAt)],
                ['Reference policy', data.snapshotPolicy?.reason || 'Unavailable in original snapshot'],
                ['Reference session', data.snapshotPolicy?.sessionDate || 'Unavailable'],
                ['Quote observation window starts', timestamp(data.snapshotPolicy?.windowStart)],
                ['Quote observation window ends', timestamp(data.snapshotPolicy?.windowEnd)],
                ['Last-trade reference window ends', timestamp(data.snapshotPolicy?.tradeWindowEnd)]]} />
              {job && <><h4>Recorded pipeline progress</h4><pre>{evidenceJson(job)}</pre></>}
            </InfoPopover>
          </>}
          {tab === 'Constituents' && <><StructuralFundamentalRange value={data.fundamentalRange} /><FundamentalInfluences data={data} /><Constituents key={detail.id} items={data.constituents} weights={data.referenceData?.weights} /></>}
          {tab === 'Selling Strategies' && <><AiSelection side="selling" detail={detail} openCandidate={openCandidate} /><Strategies key={`${detail.id}-selling`} side="selling" candidates={data.candidates} /></>}
          {tab === 'Buying Strategies' && <><AiSelection side="buying" detail={detail} openCandidate={openCandidate} /><Strategies key={`${detail.id}-buying`} side="buying" candidates={data.candidates} /></>}
        </> : <section className="options-empty">
          <h2>{tab === 'Analysis' ? 'Your forecast price ranges will appear here' : tab === 'Constituents' ? 'No constituent snapshot selected' : `No ${tab === 'Selling Strategies' ? 'selling' : 'buying'} strategy snapshot selected`}</h2>
          <p>{detail && ['queued', 'running'].includes(detail.status) ? 'This forecast is in progress. The saved analysis will appear automatically.'
            : 'Select a saved forecast from History or explicitly get a new forecast. Opening this panel never starts AI.'}</p>
          <button className="btn btn-secondary" onClick={() => onTabChange('History')}>Browse history</button>
        </section>}
    </section>
  </>;
}

export default function OptionsPage() {
  const [search, setSearch] = useSearchParams();
  const [indexCode, setIndexCode] = useState<IndexCode>('NIFTY50');
  const [mode, setMode] = useState<ForecastMode>('daily');
  const [tab, setTab] = useState<Tab>('Analysis');
  const [connection, setConnection] = useState<BrokerConnection | null>(null);
  const [metadata, setMetadata] = useState<OptionsMetadata | null>(null);
  const [history, setHistory] = useState<ForecastSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ForecastDetail | null>(null);
  const [job, setJob] = useState<JobRun | null>(null);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const [deleteNotice, setDeleteNotice] = useState('');
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [clock, setClock] = useState(() => Date.now());
  const pendingRequest = useRef<{ indexCode: IndexCode; mode: ForecastMode; id: string } | null>(null);
  const deletedIds = useRef(new Set<number>());
  const deletingRef = useRef<number | null>(null);
  const workspace = useRef({ history, selectedId, detail, job });
  const callbackStatus = search.get('connection');

  useEffect(() => { workspace.current = { history, selectedId, detail, job }; }, [history, selectedId, detail, job]);
  const evictForecast = useCallback((id: number, notice: string) => {
    deletedIds.current.add(id);
    const current = workspace.current;
    const next = forecastAfterRemoval(current.history, current.selectedId, id);
    workspace.current = { ...current, ...next, detail: current.detail?.id === id ? null : current.detail };
    setHistory(next.history);
    setSelectedId(selected => selected === id ? next.selectedId : selected);
    setDetail(selected => selected?.id === id ? null : selected);
    if (current.selectedId === id) { setJob(null); setLoading(next.selectedId != null); setError(''); }
    setDeleteNotice(notice);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    fetchOptionsConnection().then(value => { if (active) { setConnection(value); setConnectionError(''); } })
      .catch(error => { if (active) setConnectionError(message(error)); });
    fetchOptionsMetadata().then(value => { if (active) setMetadata(value); })
      .catch(error => { if (active) setError(`Metadata: ${message(error)}`); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    let active = true;
    fetchOptionsForecasts(indexCode, mode).then(value => {
      if (!active) return;
      const items = value.items.filter(item => !deletedIds.current.has(item.id));
      setHistory(items);
      setSelectedId(current => current != null && !deletedIds.current.has(current) ? current : items[0]?.id ?? null);
    })
      .catch(error => { if (active) setError(message(error)); });
    return () => { active = false; };
  }, [indexCode, mode, revision]);
  useEffect(() => {
    if (selectedId == null) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function poll() {
      if (!active || deletedIds.current.has(selectedId!) || deletingRef.current === selectedId) return;
      try {
        const value = await fetchOptionsForecast(selectedId!);
        if (!active || deletedIds.current.has(selectedId!) || deletingRef.current === selectedId) return;
        failures = 0;
        setDetail(value);
        setError('');
        setHistory(items => items.map(item => item.id === value.id ? { ...item, ...value } : item));
        if (value.jobRunId && ['queued', 'running'].includes(value.status)) {
          try {
            const progress = await fetchJobRun(value.jobRunId);
            if (active) setJob(progress);
          } catch (error) { if (active) setError(`Job progress unavailable: ${message(error)}`); }
        }
        if (active && isWorking(value)) timer = setTimeout(poll, 3000);
      } catch (error) {
        if (!active || deletedIds.current.has(selectedId!) || deletingRef.current === selectedId) return;
        if (error instanceof OptionsApiError && error.status === 404) {
          evictForecast(selectedId!, `Forecast #${selectedId} was deleted or is no longer available. History refreshed.`);
          setRevision(value => value + 1);
          return;
        }
        setError(message(error));
        if (++failures < 3) timer = setTimeout(poll, 10_000);
      } finally { if (active && !deletedIds.current.has(selectedId!)) setLoading(false); }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [selectedId, revision, evictForecast]);

  async function removeForecast(id: number) {
    if (deletingRef.current != null) return;
    const current = workspace.current;
    const item = current.history.find(item => item.id === id);
    if (!item) { setDeleteError('This forecast is no longer listed. Refresh history before deleting.'); return; }
    const reason = forecastDeletionReason(item, current.detail?.id === id ? current.detail : null, item.jobRunId === current.job?.id ? current.job : null);
    if (reason) { setDeleteError(`${reason} Wait for it to finish before deleting.`); return; }
    deletingRef.current = id;
    setDeletingId(id);
    setDeleteError('');
    setDeleteNotice('');
    try {
      await deleteForecast(id);
      evictForecast(id, `Forecast #${id} deleted.`);
    } catch (error) {
      if (error instanceof OptionsApiError && error.status === 404) {
        evictForecast(id, `Forecast #${id} was already deleted or is no longer available. History refreshed.`);
        setRevision(value => value + 1);
      } else {
        const explanation = error instanceof OptionsApiError && error.status === 403
          ? 'You do not have permission to delete this forecast.'
          : error instanceof OptionsApiError && error.status === 409
            ? 'This forecast, its AI selection or its job is active. Wait for it to finish before deleting.'
            : 'Could not delete the forecast.';
        setDeleteError(`${explanation} ${message(error)}`);
        setRevision(value => value + 1);
      }
    } finally { deletingRef.current = null; setDeletingId(null); }
  }

  async function login() {
    setConnectionBusy(true);
    setConnectionError('');
    try {
      const { loginUrl } = await startOptionsLogin();
      const url = new URL(loginUrl);
      if (url.protocol !== 'https:' || url.hostname !== 'kite.zerodha.com' || url.username || url.password) {
        throw new Error('The server did not return an official HTTPS Zerodha login URL.');
      }
      window.location.assign(url.href);
    } catch (error) { setConnectionError(message(error)); }
    finally { setConnectionBusy(false); }
  }
  async function disconnect() {
    setConnectionBusy(true);
    try { await disconnectOptions(); setRevision(value => value + 1); }
    catch (error) { setConnectionError(message(error)); }
    finally { setConnectionBusy(false); }
  }
  async function generate() {
    if (busy || !connected || (detail && isWorking(detail))) return;
    setBusy(true);
    setError('');
    if (!pendingRequest.current || pendingRequest.current.indexCode !== indexCode || pendingRequest.current.mode !== mode) {
      pendingRequest.current = { indexCode, mode, id: crypto.randomUUID() };
    }
    try {
      const created = await createOptionsForecast(indexCode, mode, pendingRequest.current.id);
      pendingRequest.current = null;
      selectForecast(created.id);
      setTab('Analysis');
      setRevision(value => value + 1);
    } catch (error) { setError(`${message(error)} Retrying the same selection reuses its request ID to avoid duplicate generation.`); }
    finally { setBusy(false); }
  }
  function changeSelection(nextIndex: IndexCode, nextMode: ForecastMode) {
    setIndexCode(nextIndex); setMode(nextMode); setSelectedId(null); setDetail(null); setJob(null); setHistory([]); setError(''); setLoading(false);
    workspace.current = { history: [], selectedId: null, detail: null, job: null };
    setDeleteError(''); setDeleteNotice('');
  }
  function selectForecast(id: number) {
    if (deletedIds.current.has(id)) return;
    workspace.current = { ...workspace.current, selectedId: id, detail: null, job: null };
    setSelectedId(id); setDetail(null); setJob(null); setLoading(true);
    if (selectedId === id) setRevision(value => value + 1);
  }
  function openCandidate(candidate: OptionCandidate) {
    setTab(candidate.side === 'selling' ? 'Selling Strategies' : 'Buying Strategies');
    requestAnimationFrame(() => {
      const element = document.getElementById(`option-candidate-${candidate.id}`) as HTMLDetailsElement | null;
      if (element) { element.open = true; element.scrollIntoView({ behavior: 'smooth', block: 'center' }); element.querySelector('summary')?.focus(); }
    });
  }
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(clock);
  const part = (type: string) => parts.find(item => item.type === type)?.value || '';
  const today = `${part('year')}-${part('month')}-${part('day')}`;
  const afterCutoff = Number(part('hour')) >= 14;
  const todaySession = metadata?.sessions?.find(item => item.indexCode === indexCode && item.sessionDate === today);
  const dailyLabel = afterCutoff || todaySession?.isTradingDay === false ? 'Next Trading Day Forecast'
    : todaySession?.isTradingDay ? "Today's Forecast" : 'Daily Forecast (public reference unavailable)';
  const expired = !!connection?.expiresAt && new Date(connection.expiresAt).getTime() <= clock;
  const connected = connection?.status === 'connected' && !expired;
  const runState: ForecastRunState = busy ? 'submitting'
    : detail && ['queued', 'running'].includes(detail.status) ? 'running'
    : detail && isWorking(detail) ? 'selecting' : 'idle';

  return <main className="page options-page">
    <header className="options-header">
      <div><NavLink to="/india" className="back-link"><ChevronLeft size={16} /> Indian Market</NavLink><h1>Index Options</h1></div>
      <span className="options-research-badge"><ShieldCheck size={16} aria-hidden="true" /> Research only</span>
    </header>
    <p className="options-disclosure">Hypothetical research. No orders or automatic exits. No guaranteed returns; low risk does not mean safe.</p>
    <BrokerBar connection={connection} error={connectionError} callbackStatus={callbackStatus} busy={connectionBusy} clock={clock}
      login={login} disconnect={disconnect} refresh={() => {
        setRevision(value => value + 1);
        if (callbackStatus) { const next = new URLSearchParams(search); next.delete('connection'); setSearch(next, { replace: true }); }
      }} />
    <ForecastComposer indexCode={indexCode} mode={mode} dailyLabel={dailyLabel} connected={connected} runState={runState}
      missingCalendar={mode === 'daily' && !todaySession} changeSelection={changeSelection} generate={generate} compact={!!detail} />
    {error && <p className="options-error" role="alert">{error}</p>}
    {deleteError && <p className="options-error" role="alert">{deleteError}</p>}
    {deleteNotice && <p className="options-delete-notice" role="status">{deleteNotice}</p>}
    {loading && <p className="options-loading" role="status"><LoaderCircle className="options-spin" size={16} aria-hidden="true" /> Loading saved forecast…</p>}
    {detail && isWorking(detail) && <p className="options-loading" role="status"><LoaderCircle className="options-spin" size={15} aria-hidden="true" /> {runState === 'selecting' ? 'Final AI selection in progress' : 'Forecast in progress'} · updates automatically{job ? ` · ${number(job.progress)}%` : ''}</p>}
    <ForecastViews tab={tab} onTabChange={setTab} detail={detail} history={history} selectedId={selectedId}
      open={selectForecast} openCandidate={openCandidate} clock={clock} job={job} onDelete={removeForecast} deletingId={deletingId} />
  </main>;
}
