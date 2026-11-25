import { useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchOptionsConnection, startOptionsLogin } from '../optionsApi';
import type { BrokerConnection, IndexCode } from '../optionsApi';
import { createStrangleRun, fetchLatestStrangleRun, fetchStrangleRun } from '../strangleApi';
import type {
  StrangleAction, StrangleCandidate, StrangleGreeks, StrangleLeg,
  StranglePosition, StrangleResult, StrangleRun, StrangleRunRequest,
} from '../strangleApi';
import './StrangleSellingTab.css';

const number = (value: unknown, digits = 2) => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString('en-IN', { maximumFractionDigits: digits }) : 'Unavailable';
const rupees = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? `₹${number(value)}` : 'Unavailable';
const time = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', timeZoneName: 'short' }) : 'Unavailable';
const message = (error: unknown) => error instanceof Error ? error.message : 'Request failed.';
const working = (run: StrangleRun) => ['queued', 'running', 'pending', 'processing', 'ai_pending', 'ai_running'].includes(run.status.toLowerCase())
  || ['queued', 'running', 'pending', 'processing'].includes(String(run.ai?.status ?? '').toLowerCase());
const positive = (value: string) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0;
const inRange = (value: string, min: number, max: number) => value.trim() !== ''
  && Number.isFinite(Number(value)) && Number(value) >= min && Number(value) <= max;
const greekKeys = ['delta', 'gamma', 'vega', 'theta'] as const;
const actionLabel = (candidate?: StrangleCandidate) => ({
  hold: 'STAY / HOLD', roll: candidate?.structure === 'hedged-straddle' ? 'DEFEND / HEDGED STRADDLE' : 'DEFEND / HEDGED ROLL',
}[candidate?.action ?? ''] ?? 'Review only');

function Evidence({ title, children }: { title: string; children: React.ReactNode }) {
  return <details className="strangle-evidence"><summary>{title}</summary>{children}</details>;
}

function Pnl({ value }: { value: unknown }) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return <>Unavailable</>;
  const label = value > 0 ? 'Profit' : value < 0 ? 'Loss' : 'Flat';
  return <span className={value > 0 ? 'strangle-positive' : value < 0 ? 'strangle-negative' : undefined}
    aria-label={`${label}: ${rupees(Math.abs(value))}`}>
    {value > 0 ? '+' : value < 0 ? '−' : ''}{rupees(Math.abs(value))}
  </span>;
}

function GreekStrip({ value, label }: { value?: StrangleGreeks; label: string }) {
  return <div className="strangle-greeks" aria-label={label}><span>{label}</span>
    {greekKeys.map(key => <span key={key}>{key} <strong>{number(value?.quantityWeighted?.[key], 4)}</strong></span>)}
  </div>;
}

function SellingContext({ result, candidate }: { result: StrangleResult; candidate?: StrangleCandidate }) {
  const regime = result.regime;
  const balance = candidate?.premiumBalance ?? result.premiumBalance;
  const qualified = regime?.sourceIndexCode === 'NIFTY50' && regime.timeframe === '5minute';
  const state = qualified ? regime.state : 'unavailable';
  return <div className="strangle-market-context">
    <div className="strangle-badges">
      <span className={`strangle-badge ${state === 'risk-on' ? 'strangle-positive' : state === 'risk-off' ? 'strangle-warning' : ''}`}>
        {state === 'risk-on' ? 'RISK-ON · premium selling' : state === 'risk-off' ? 'RISK-OFF · defensive only' : 'Selling regime unavailable / mixed'}
      </span>
      <span className="strangle-badge">NIFTY · completed 5m</span>
      <span className="strangle-badge">Bias: {qualified ? regime.direction ?? 'unavailable' : 'unavailable'}</span>
    </div>
    <p className="strangle-muted">Support <strong>{qualified ? number(regime.support) : 'Unavailable'}</strong>
      {' · '}Resistance <strong>{qualified ? number(regime.resistance) : 'Unavailable'}</strong>
      {' · '}5m close <strong>{qualified ? number(regime.lastClose) : 'Unavailable'}</strong></p>
    {balance && <p className="strangle-muted">Short premiums · PE {rupees(balance.putPremium)} / CE {rupees(balance.callPremium)}
      {' · '}{balance.putPremium == null || balance.callPremium == null ? 'unavailable' : balance.preferred ? 'similar' : 'unequal / risk-led'}</p>}
    <Evidence title="Risk-on / risk-off definition & strike choice">
      <p>Risk-on: support/resistance range holding, contained volatility and acceptable Greek stress.
        Risk-off: confirmed range break, volatility expansion or excessive Greek stress. Bullish/bearish bias is separate.</p>
      <p>{regime?.basis ?? 'Qualified NIFTY five-minute chart evidence unavailable.'} {regime?.reason}</p>
      <p>Adjustments must reduce stressed loss, not chase premium. A hedged straddle needs risk-on conditions and qualified before/after Greeks.</p>
      {balance && <p>{balance.reason} Premium equality is a preference, never a reason to widen risk.</p>}
    </Evidence>
  </div>;
}

function CurrentPositions({ result, title = 'My positions', embedded = false }: { result: StrangleResult; title?: string; embedded?: boolean }) {
  const raw: StranglePosition[] | undefined = Array.isArray(result.positions) ? result.positions : result.positions?.net;
  const positions = (raw ?? []).filter(position => position.quantity !== 0);
  return <section className={embedded ? undefined : 'strangle-card'}>{!embedded && <h3>{title}</h3>}
    {positions.length ? <div className="strangle-table-wrap"><table className="table">
      <thead><tr><th>Held contract / product</th><th>Strike / expiry</th><th>Held quantity</th><th>Broker average / P&amp;L</th><th>Δ / Γ / ν / θ (per unit)</th></tr></thead>
      <tbody>{positions.map((position, index) => {
        const symbol = position.tradingSymbol ?? position.tradingsymbol;
        const risk = result.positionRisk?.find(item => item.exchange === position.exchange
          && item.tradingSymbol === symbol && item.product === position.product);
        const signed = risk?.signedQuantity ?? position.quantity;
        return <tr key={`${position.exchange}:${symbol}:${position.product}:${index}`}>
          <td><strong>{symbol}</strong><small>{position.exchange} · {position.product}</small></td>
          <td>{number(risk?.strike)} {risk?.optionType}<small>{risk?.expiry ?? 'Expiry unavailable'}</small></td>
          <td><strong className={signed < 0 ? 'strangle-sell' : 'strangle-buy'}>{signed < 0 ? 'SELL' : 'BUY'} {number(Math.abs(signed), 0)}</strong></td>
          <td>{rupees(position.averagePrice ?? position.average_price)}<small><Pnl value={risk?.brokerPnlRupees ?? position.pnl} /> P&amp;L</small></td>
          <td title={risk?.unavailableReason}>{greekKeys.map(key => number(risk?.greeks?.[key], 4)).join(' / ')}
            {risk?.unavailableReason && <small>{risk.unavailableReason}</small>}</td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="strangle-muted">{raw ? 'No open positions in this snapshot.' : 'Broker positions unavailable.'}</p>}
    {!embedded && <GreekStrip value={result.greeks} label="Current · signed quantity-weighted Greeks" />}
  </section>;
}

function Legs({ legs, expiry, closing = false, result }: {
  legs: StrangleLeg[]; expiry?: string; closing?: boolean; result: StrangleResult;
}) {
  return <div className="strangle-table-wrap"><table className="table">
    <thead><tr><th>{closing ? 'Manual closing action / contract' : 'Held direction after action / contract'}</th><th>Strike / expiry</th>
      <th>Quantity / product</th><th>Reference price / basis</th></tr></thead>
    <tbody>{legs.map((leg, index) => {
      const raw = Array.isArray(result.positions) ? result.positions : result.positions?.net;
      const held = result.positionRisk?.find(item => item.exchange === leg.exchange && item.tradingSymbol === leg.tradingSymbol && item.product === leg.product)?.signedQuantity
        ?? raw?.find(item => item.exchange === leg.exchange && (item.tradingSymbol ?? item.tradingsymbol) === leg.tradingSymbol && item.product === leg.product)?.quantity;
      // Older snapshots describe closing legs in held directions. Prefer broker exposure to disambiguate.
      const direction = closing ? held != null ? held < 0 ? 'buy' : 'sell'
        : leg.direction === 'sell' ? 'buy' : 'sell' : leg.signedQuantity != null ? leg.signedQuantity < 0 ? 'sell' : 'buy' : leg.direction;
      return <tr key={`${leg.tradingSymbol}:${index}`}>
        <td><strong className={direction === 'sell' ? 'strangle-sell' : 'strangle-buy'}>{direction.toUpperCase()} {leg.optionType}</strong><small>{leg.exchange}:{leg.tradingSymbol}</small></td>
        <td>{number(leg.strike)}<small>{leg.expiry ?? expiry ?? 'Expiry unavailable'}</small></td>
        <td>{number(Math.abs(leg.quantity), 0)}<small>{leg.product ?? 'Product unavailable'}</small></td>
        <td>{rupees(closing ? leg.liquidationPrice : leg.entryPrice)}<small>{leg.priceBasis ?? 'Basis unavailable'} · not a fill</small>
          <small>{time(leg.quoteObservedAt ?? leg.observedAt)}</small></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function Analysis({ run, clock }: { run: StrangleRun; clock: number }) {
  const result = run.result;
  if (!result) return <p className="strangle-muted">Snapshot {working(run) ? 'in progress…' : 'unavailable.'}</p>;
  const age = result.asOf ? clock - Date.parse(result.asOf) : NaN;
  const stale = !Number.isFinite(age) || age > 30_000 || age < -2000;
  const deadlineReached = !!result.exitAt && Number.isFinite(Date.parse(result.exitAt)) && clock >= Date.parse(result.exitAt);
  const mustExit = result.mandatoryExit === true || result.executionStatus === 'exit-required' || deadlineReached;
  const completedId = run.ai?.status?.toLowerCase() === 'completed'
    ? run.ai.output?.candidateId ?? run.ai.candidateId ?? run.ai.selectedCandidateId : undefined;
  const selectedId = !mustExit && !result.blockers?.length && result.eligible !== false ? completedId : undefined;
  const candidate = result.candidates?.find(item => item.id === selectedId && item.eligible !== false);
  const defensiveRoll = candidate?.action !== 'roll' || (
    ['risk-on', 'risk-off'].includes(result.regime?.state ?? '')
    && result.regime?.sourceIndexCode === 'NIFTY50' && result.regime.timeframe === '5minute'
    && (candidate.structure !== 'hedged-straddle' || result.regime?.state === 'risk-on')
    && typeof candidate.stressLossRupees === 'number' && Number.isFinite(candidate.stressLossRupees)
    && typeof result.riskPlan?.stressLossRupees === 'number'
    && candidate.stressLossRupees < result.riskPlan.stressLossRupees - .01);
  const approved = candidate && ['hold', 'roll'].includes(candidate.action)
    && result.exitPolicy?.version === 'sl-target-time-v1' && result.exitPolicy.reason === null && defensiveRoll;
  const entry = run.action === 'entry';
  const reviewNarrative = !entry && !mustExit && completedId === 'review-current-positions'
    ? run.ai?.output?.reason ?? run.ai?.reason ?? run.ai?.rationale : undefined;
  const entryApproved = entry && result.eligible === true && !mustExit && !result.blockers?.length;
  const pnl = result.sessionPnl;
  const payoff = result.payoff;
  const plan = result.riskPlan;
  const headline = mustExit ? 'EXIT — manual close required' : entry ? entryApproved ? 'Entry suggestion' : 'Review only'
    : approved ? actionLabel(candidate) : 'Review only';
  const outcomeTone = mustExit ? 'strangle-exit'
    : approved && candidate.action === 'roll' ? 'strangle-adjust' : 'strangle-info';
  const exitReason = deadlineReached ? 'Intraday deadline reached' : result.exitPolicy?.reason === 'stop-loss' ? 'SL reached'
    : result.exitPolicy?.reason === 'target' ? 'Target reached'
      : result.exitPolicy?.reason === 'time' ? 'Intraday deadline reached' : 'Exit required';
  const reason = mustExit ? `${exitReason}. Close manually; AI cannot override this exit.`
    : approved ? run.ai?.output?.reason ?? run.ai?.reason ?? run.ai?.rationale ?? candidate?.reason
      : entryApproved ? 'Reference prices, not fills.' : 'No approved trade or hold.';
  return <>
    <section className={`strangle-card strangle-outcome ${outcomeTone}`} aria-label="Analysis outcome">
      <div className="strangle-heading"><h2>{headline}</h2><div className="strangle-badges">
        <span className={stale ? 'strangle-badge strangle-warning' : 'strangle-badge'}>{stale ? 'STALE · refresh before acting' : 'Snapshot · not live'}</span>
        {mustExit && <span className="strangle-badge strangle-danger">EXIT</span>}
        {!entry && <span className="strangle-badge">AI {run.ai?.status ?? 'unavailable'}</span>}
      </div></div>
      <p>{reason}</p>
      {reviewNarrative && <p><strong>AI risk review{stale ? ' · stale' : ''}:</strong> {reviewNarrative}</p>}
      {!entry && !mustExit && completedId && completedId !== 'review-current-positions' && !approved &&
        <p className="strangle-error" role="status">AI selection does not meet the defensive policy. No position change approved.</p>}
      {(run.ai?.errorMessage ?? run.ai?.error) && <p className={run.ai?.status === 'skipped' ? 'strangle-muted' : 'strangle-error'} role="status">{run.ai?.errorMessage ?? run.ai?.error}</p>}
      <p className="strangle-muted">{time(result.asOf)} · {run.indexCode}{result.expiry ? ` · ${result.expiry}` : ''}</p>
      {!entry && <dl className="strangle-metrics strangle-pnl">
        <div><dt>Realized (closed / adjustment)</dt><dd><Pnl value={pnl?.realizedRupees} /></dd></div>
        <div><dt>Open P&amp;L</dt><dd><Pnl value={pnl?.unrealizedRupees} /></dd></div>
        <div><dt>Total today</dt><dd><Pnl value={pnl?.totalRupees} /></dd></div>
      </dl>}
      {!entry && <p className="strangle-muted">P&amp;L: {pnl?.status ?? 'unavailable'}</p>}
      <dl className="strangle-metrics">
        <div><dt>Max profit · expiry payoff</dt><dd className="strangle-positive">{payoff?.profitUnbounded ? 'Unlimited' : rupees(payoff?.status === 'available' ? payoff.maxProfitRupees : entry ? result.maxProfit : null)}</dd></div>
        <div><dt>Maximum contractual loss</dt><dd className="strangle-negative">{payoff?.lossUnbounded ? 'Unlimited' : rupees(payoff?.status === 'available' ? payoff.maxLossRupees : entry ? result.maxLoss : null)}</dd></div>
        <div><dt>{entry ? 'Entry stop loss (−X)' : 'Suggested session SL'}</dt><dd className="strangle-negative">{rupees(entry ? result.stopRiskRupees : plan?.status === 'available' ? plan.suggestedSlRupees : null)}</dd></div>
        <div><dt>{entry ? 'Entry target (+2X)' : 'Remaining SL headroom'}</dt><dd className={entry ? 'strangle-positive' : undefined}>{rupees(entry ? result.targetRupees : plan?.status === 'available' ? plan.remainingLossRupees : null)}</dd></div>
        {!entry && <div><dt>Session target (+2× SL)</dt><dd className="strangle-positive">{rupees(plan?.status === 'available' ? plan.targetPnlRupees : null)}</dd></div>}
      </dl>
      <p className="strangle-muted">{entry
        ? `Data-driven X: ${number(result.xPoints)} points · stop −X / target +2X.`
        : 'Session SL = ½ positive finite max profit · target = 2× SL · recalculated.'}</p>
      <p className="strangle-muted">Exit only at SL, target or the mandatory intraday deadline. No discretionary full or partial exit.</p>
      <SellingContext result={result} candidate={approved ? candidate : undefined} />
    </section>
    <div className={`strangle-positions ${entry ? 'strangle-entry' : ''}`}>
    {!entry && <CurrentPositions result={result} />}
    <section className="strangle-card"><h3>{entry ? 'Suggested entry positions' : 'Suggested positions after action'}</h3>
      {mustExit ? <p>Close manually; no positions should remain. No AI-approved roll.</p>
        : entryApproved ? result.legs?.length ? <Legs legs={result.legs} expiry={result.expiry} result={result} /> : <p>No qualified entry legs available.</p>
          : approved && candidate.action === 'hold' ? <><p><strong>Unchanged · no new orders.</strong></p>
              {candidate.legs?.length ? <Legs legs={candidate.legs} expiry={result.expiry} result={result} /> : <CurrentPositions result={result} embedded />}</>
              : approved && candidate.legs?.length ? <Legs legs={candidate.legs} expiry={result.expiry} result={result} />
                : <p>No approved position change.</p>}
      {approved && candidate.closingLegs?.length ? <><h4>Existing legs to replace</h4>
        <p className="strangle-muted">Closing actions · follow the ordered sequence below and keep shorts hedged.</p>
        <Legs legs={candidate.closingLegs} expiry={result.expiry} closing result={result} /></> : null}
      {approved && candidate.action !== 'hold' && <p className="strangle-muted">Defensive replacement only; follow the safe sequence and never unhedge open shorts.</p>}
      {(entryApproved || approved) && <GreekStrip value={entry ? result.greeks : candidate?.greeksAfter ?? candidate?.greeks ?? (candidate?.action === 'hold' ? result.greeks : undefined)} label="Proposed · signed quantity-weighted Greeks" />}
      {!!candidate?.steps?.length && <Evidence title="Manual risk-safe action sequence"><ol>{candidate.steps.map((step, index) => <li key={index}>{step}</li>)}</ol></Evidence>}
    </section>
    </div>
    <section className="strangle-card strangle-details">
      <Evidence title={`Data warnings & limitations (${(result.warnings?.length ?? 0) + (result.blockers?.length ?? 0)})`}>
        {[...(result.blockers ?? []), ...(result.warnings ?? [])].map((warning, index) => <p key={index}>{warning}</p>)}
        <p>{payoff?.reason ?? (payoff ? 'Payoff uses session cash flows and current expiry exposure.' : 'Payoff unavailable.')}</p>
        <p>{entry ? 'Entry X and its INR stop/target are deterministic advisory values, not execution fills or automatic stops.'
          : plan?.reason ?? (plan ? 'Stop plan is advisory, not a broker order.' : 'Session SL unavailable.')}</p>
        {!entry && <p>{pnl?.scope ?? 'P&L scope unavailable.'} {pnl?.source} {pnl?.reason} Realized values use gross FIFO; closed adjustment legs are included when verified.</p>}
        <p>Greeks use signed quantities, not lot counts. Vega is per absolute volatility point; theta is per calendar day. Missing values are not zero.</p>
      </Evidence>
      <Evidence title="Model assumptions, payoff & risk basis">
        <p>Model assumptions, not live rates: interest {result.modelInputs?.riskFreeRate == null ? 'Unavailable' : `${number(result.modelInputs.riskFreeRate * 100)}%`};
          {' '}dividend yield {result.modelInputs?.dividendYield == null ? 'Unavailable' : `${number(result.modelInputs.dividendYield * 100)}%`}. {result.modelInputs?.source}</p>
        <p>{payoff?.basis ?? 'Payoff basis unavailable.'}</p>
        {entry ? <p>Entry X: {number(result.xPoints)} points · Stop {rupees(result.stopRiskRupees)} · Target {rupees(result.targetRupees)}. See the deterministic X calculation under evidence.</p>
          : <><p>{plan?.basis ?? 'Risk-plan basis unavailable.'}</p>
            <p>Stop P&amp;L {rupees(plan?.stopPnlRupees)} · Stress loss {rupees(plan?.stressLossRupees)} · Remaining upside {rupees(payoff?.remainingUpsideRupees)}. SL is recalculated, not frozen; expiry payoff is not a guaranteed intraday target.</p></>}
      </Evidence>
      <Evidence title="Evidence, AI logs & calculation detail">
        <p>{run.ai?.errorMessage ?? run.ai?.error}</p>
        <pre>{JSON.stringify({ ai: run.ai, trend: result.trend, regime: result.regime, xCalculation: result.xCalculation,
          margin: result.margin, positionRisk: result.positionRisk, sessionPnl: pnl, candidates: result.candidates }, null, 2)}</pre>
      </Evidence>
    </section>
  </>;
}

function exitDeadline(clock: number, result?: StrangleResult | null) {
  const date = new Date(clock + 330 * 60_000).toISOString().slice(0, 10);
  const standard = Date.parse(`${date}T15:15:00+05:30`);
  const supplied = result?.exitAt ? Date.parse(result.exitAt) : NaN;
  return Number.isFinite(supplied) && new Date(supplied + 330 * 60_000).toISOString().slice(0, 10) === date
    ? Math.min(standard, supplied) : standard;
}

export default function StrangleSellingTab({ active }: { active: boolean }) {
  const [indexCode, setIndexCode] = useState<IndexCode>('NIFTY50');
  return <div className="strangle-selling">
    <div className="strangle-heading"><h2>Strangle selling</h2>
      <label className="strangle-index">Index<select value={indexCode} onChange={event => setIndexCode(event.target.value as IndexCode)}>
        <option value="NIFTY50">NIFTY50</option><option value="SENSEX">SENSEX</option>
      </select></label></div>
    <IndexWorkspace key={indexCode} indexCode={indexCode} active={active} />
  </div>;
}

function IndexWorkspace({ indexCode, active }: { indexCode: IndexCode; active: boolean }) {
  const [wingWidth, setWingWidth] = useState(indexCode === 'NIFTY50' ? '1000' : '3000');
  const [xOverride, setXOverride] = useState('');
  const [rate, setRate] = useState('6');
  const [yieldPercent, setYieldPercent] = useState('0');
  const [connection, setConnection] = useState<BrokerConnection | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [run, setRun] = useState<StrangleRun | null>(null);
  const [revision, setRevision] = useState(0);
  const [clock, setClock] = useState(Date.now);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState<StrangleRunRequest | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const submitting = useRef(false);
  const loadedRevision = useRef<number | null>(null);
  const connected = connection?.status === 'connected' && !connectionError
    && (!connection.expiresAt || Date.parse(connection.expiresAt) > clock);
  const pending = !!run && working(run);
  const entryValid = positive(wingWidth) && (xOverride.trim() === '' || positive(xOverride));
  const rateValid = inRange(rate, -5, 100);
  const yieldValid = inRange(yieldPercent, 0, 100);
  const modelsValid = rateValid && yieldValid;
  const deadline = exitDeadline(clock, run?.result);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    fetchOptionsConnection().then(value => {
      if (!cancelled) { setConnection(value); setConnectionError(''); }
    }).catch(error => { if (!cancelled) setConnectionError(message(error)); });
    return () => { cancelled = true; };
  }, [active, indexCode, revision]);
  useEffect(() => {
    if (!active || loadedRevision.current === revision) return;
    let cancelled = false;
    const version = generation.current;
    fetchLatestStrangleRun(indexCode).then(value => {
      if (cancelled || generation.current !== version) return;
      loadedRevision.current = revision;
      setRun(value); setSelectedId(value?.id ?? null);
      setError('');
    }).catch(error => {
      if (!cancelled && generation.current === version) {
        loadedRevision.current = null;
        setError(`Latest analysis: ${message(error)}. Refresh to retry.`);
      }
    });
    return () => { cancelled = true; };
  }, [active, indexCode, revision]);
  useEffect(() => {
    if (!active || selectedId == null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function poll() {
      try {
        const value = await fetchStrangleRun(selectedId!);
        if (cancelled) return;
        failures = 0;
        setRun(value);
        if (working(value)) timer = setTimeout(poll, 3000);
      } catch (error) {
        if (cancelled) return;
        setError(`Status: ${message(error)}. Refresh persisted status to resume.`);
        if (++failures < 3) timer = setTimeout(poll, 10_000);
      }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active, selectedId, revision]);

  function invalidate() {
    generation.current++;
    loadedRevision.current = revision;
    setRun(null); setSelectedId(null); setRetry(null);
    setError(''); setNotice('Inputs changed — request fresh analysis.');
  }
  async function login() {
    setBusy(true); setConnectionError('');
    try {
      const { loginUrl } = await startOptionsLogin();
      const url = new URL(loginUrl);
      if (url.protocol !== 'https:' || url.hostname !== 'kite.zerodha.com' || url.username || url.password)
        throw new Error('Expected the official HTTPS Zerodha login URL.');
      window.location.assign(url.href);
    } catch (error) { if (mounted.current) setConnectionError(message(error)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function submit(action: StrangleAction) {
    if (submitting.current || !connected || !modelsValid || (action === 'entry' && !entryValid)) return;
    submitting.current = true;
    const version = ++generation.current;
    loadedRevision.current = revision;
    const request: StrangleRunRequest = (retry?.action === action ? retry : null) ?? {
      requestId: crypto.randomUUID(), indexCode, action,
      wingWidth: action === 'entry' ? Number(wingWidth) : indexCode === 'NIFTY50' ? 1000 : 3000,
      xOverride: action === 'entry' && xOverride.trim() ? Number(xOverride) : null,
      strategyId: null, riskFreeRate: Number(rate) / 100, dividendYield: Number(yieldPercent) / 100,
    };
    setBusy(true); setError(''); setNotice(''); setRun(null); setSelectedId(null);
    setRetry(request);
    try {
      const value = await createStrangleRun(request);
      if (!mounted.current || generation.current !== version) return;
      setRetry(null); setRun(value); setSelectedId(value.id);
    } catch (error) {
      if (mounted.current && generation.current === version) setError(`${message(error)} Retry uses the same request ID to avoid duplicate work.`);
    } finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  return <>
    <section className="strangle-card">
      <div className="strangle-heading"><strong>Fresh analysis</strong><div className="strangle-connection">
        <span>{connectionError ? 'Connection unavailable' : connected ? 'Broker connected' : connection?.status ?? 'Checking broker…'}</span>
        <button className="btn btn-outline btn-sm" aria-label="Refresh broker connection and latest job status" onClick={() => setRevision(value => value + 1)} disabled={busy}><RefreshCw size={14} /></button>
        {!connected && <button className="btn btn-outline btn-sm" onClick={() => void login()} disabled={busy}>Reconnect</button>}
      </div></div>
      <div className="strangle-actions">
        <button className="btn btn-outline" onClick={() => void submit('entry')} disabled={!connected || busy || pending || !entryValid || !modelsValid || clock >= deadline - 15 * 60_000}>Get entry suggestion</button>
        <button className="btn btn-primary" onClick={() => void submit('adjustment')} disabled={!connected || busy || pending || !modelsValid}>AI adjustment · my positions</button>
      </div>
      <div className="strangle-settings">
        <Evidence title="Entry settings">
          <div className="strangle-controls">
            <label>Hedge width (points)<input type="number" min="1" step="1" value={wingWidth} disabled={busy} aria-invalid={!positive(wingWidth)} onChange={event => { invalidate(); setWingWidth(event.target.value); }} /></label>
            <label>Optional X override (points)<input type="number" min="0.01" step="any" value={xOverride} disabled={busy} aria-invalid={xOverride.trim() !== '' && !positive(xOverride)} onChange={event => { invalidate(); setXOverride(event.target.value); }} /></label>
          </div>
          {!entryValid && <p className="strangle-error">Use a positive hedge width and a positive X, or leave X empty. Adjustment does not use these entry controls.</p>}
        </Evidence>
        <Evidence title={`Model assumptions · ${rate}% interest / ${yieldPercent}% yield`}>
          <p className="strangle-muted">Model assumptions, not live rates. Applied to both entry and adjustment.</p>
          <div className="strangle-controls">
            <label>Interest rate (%)<input type="number" min="-5" max="100" step="any" value={rate} aria-invalid={!rateValid} disabled={busy} onChange={event => { invalidate(); setRate(event.target.value); }} /></label>
            <label>Dividend yield (%)<input type="number" min="0" max="100" step="any" value={yieldPercent} aria-invalid={!yieldValid} disabled={busy} onChange={event => { invalidate(); setYieldPercent(event.target.value); }} /></label>
          </div>
          {!modelsValid && <p className="strangle-error">Enter finite interest −5% to 100% and dividend yield 0% to 100%.</p>}
        </Evidence>
      </div>
      {!modelsValid && <p className="strangle-error" role="alert">Model assumptions need valid values before analysis.</p>}
      {(connectionError || connection?.lastError) && <p className="strangle-error" role="alert">{connectionError || connection?.lastError}</p>}
      {(busy || pending) && <p role="status" className="strangle-progress"><Loader2 size={16} /> {busy ? 'Requesting snapshot…' : 'Analysis in progress…'}</p>}
      {error && <p role="alert" className="strangle-error">{error}</p>}
      {run?.errorMessage && <p role="alert" className="strangle-error">{run.errorMessage}</p>}
      {notice && <p role="status" className="strangle-muted">{notice}</p>}
    </section>
    <p className={`strangle-disclaimer ${clock >= deadline ? 'strangle-error' : ''}`}>Advisory snapshots only; no orders or stop monitoring. Manually exit by {time(new Date(deadline).toISOString())}{clock >= deadline ? ' — deadline passed.' : '.'}</p>
    {run && <Analysis run={run} clock={clock} />}
  </>;
}
