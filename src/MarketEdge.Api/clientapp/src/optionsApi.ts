import { authFetch } from './api';

export type IndexCode = 'NIFTY50' | 'SENSEX';
export type ForecastMode = 'daily' | 'weekly' | 'monthly' | 'expiry-intraday';
export type RiskTier = 'low' | 'medium' | 'high';
export type StrategySide = 'selling' | 'buying';
export interface BrokerConnection {
  status: string;
  configured: boolean;
  brokerUserId?: string | null;
  loginAt?: string | null;
  expiresAt?: string | null;
  lastError?: string | null;
}
export interface OptionLeg {
  exchange: string;
  tradingSymbol: string;
  instrumentToken: number;
  direction: 'buy' | 'sell';
  quantity: number;
  strike: number;
  optionType: 'CE' | 'PE';
  entryPrice: number;
  quoteObservedAt?: string | null;
  observedAt?: string | null;
  priceBasis?: 'captured-bid-ask' | 'last-traded-reference' | null;
  impliedVolatility?: number | null;
  greeks?: Record<string, number> | null;
}
export interface ScenarioProfitProbability {
  probability: number | null;
  probabilityType: 'model-implied' | 'unavailable';
  condition: { low: number | null; high: number | null; at: string };
  gridPoints: number;
  matchedGridPoints: number;
  reason: string;
}
export interface OptionCandidate {
  id: string;
  side: StrategySide;
  riskTier: RiskTier;
  riskTierReasons?: string[];
  riskTierRule?: string;
  riskExposureRatio?: number | null;
  strategy: string;
  expiry: string;
  lotSize: number;
  legs: OptionLeg[];
  netPremium: number;
  maxProfit: number | null;
  maxLoss: number | null;
  grossMaxLoss?: number | null;
  grossMaxProfit?: number | null;
  payoffBasis?: 'net-after-configured-charges' | 'gross-before-charges';
  modelMetricsReason?: string | null;
  costEstimate: number | null;
  capitalBasis: number | null;
  rewardRisk: number | null;
  probabilityOfProfit: number | null;
  probabilityType: string;
  probabilityReason?: string | null;
  scenarioProfitProbabilities?: Record<string, ScenarioProfitProbability>;
  scenarioIds: string[];
  moneyness?: 'ITM' | 'ATM' | 'OTM' | null;
  technicalStop?: { lower?: number | null; upper?: number | null; trigger: string } | null;
  estimatedStopLoss?: number | null;
  estimatedStopLossReason?: string | null;
  targetPremium?: number | null;
  targetProfit?: number | null;
  grossTargetProfit?: number | null;
  executionStatus?: string | null;
  requiresLiveRevalidation?: boolean;
  quoteAsOf?: string | null;
  quoteObservedAt?: string | null;
  plannedExitAt?: string | null;
  warnings: string[];
}
export interface ConstituentValuation {
  symbol: string;
  companyName: string;
  weightPct: number;
  weightSource?: string | null;
  weightAsOf?: string | null;
  valuationSource?: string | null;
  valuationAsOf?: string | null;
  analystCount?: number | null;
  earningsGrowthYoyPct?: number | null;
  revenueGrowthYoyPct?: number | null;
  aiModel?: string | null;
  aiPriceForecasts?: { horizon: string; low: number | null; base: number | null; high: number | null }[];
  indexContributions?: { low?: number | null; base?: number | null; high?: number | null };
  livePrice: number | null;
  fairLow: number | null;
  fairBase: number | null;
  fairHigh: number | null;
  lowMovePct: number | null;
  baseMovePct: number | null;
  highMovePct: number | null;
  method: string;
  latestQuarterEnd?: string | null;
  reason?: string | null;
  sector?: string;
  [key: string]: unknown;
}
export interface AnalysisIndicator {
  key: string; label: string; value: number | string | null; unit?: string;
  direction?: string; contribution?: number | null; reason?: string;
  contributionUnit?: string; contextOnly?: boolean; timeframe?: string;
  signalContribution?: number | null;
  influenceRole?: 'directional' | 'context';
  contributionBasis?: string | null;
  asOf?: string | null; observedAt?: string | null; source?: string | null; sourceUrl?: string | null;
  sessionDate?: string | null; min?: number; max?: number;
  [key: string]: unknown;
}
export interface AnalysisDimension {
  key: string;
  label: string;
  score: number | null;
  scale: string;
  coverage: number | null;
  reason?: string | null;
  asOf?: string | null;
  source?: string | null;
  indicators: AnalysisIndicator[];
}
export interface WeightReference {
  sourceType?: string | null;
  sourceDate?: string | null;
  sourceUrl?: string | null;
  method?: string | null;
  rawEquityNavPct?: number | null;
  [key: string]: unknown;
}
export interface ForecastScenario {
  id: string;
  label: string;
  low: number | null;
  high: number | null;
  probability: number | null;
  probabilityType: string;
  intervalType?: string;
  targetAt?: string | null;
  reason?: string;
}
export interface MarketNewsItem {
  title: string;
  url?: string | null;
  publishedAt?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  timestampBasis?: string;
  untrustedText?: boolean;
  [key: string]: unknown;
}
export interface OptionsMarketContext {
  dimension?: AnalysisDimension | null;
  news?: MarketNewsItem[];
  evidence?: {
    flows?: {
      status: 'available' | 'partial' | 'unavailable';
      source?: string;
      sourceUrl?: string | null;
      documentationUrl?: string | null;
      asOf?: string | null;
      sessionDate?: string | null;
      capturedAt?: string;
      unit?: 'INR crore';
      fii?: { buy: number | null; sell: number | null; net: number | null } | null;
      dii?: { buy: number | null; sell: number | null; net: number | null } | null;
      quoteType?: 'last-reported-end-of-day';
      asOfPrecision?: 'session-date';
      unitBasis?: string;
      reason?: string;
    } | null;
    crude?: {
      name: string;
      price: number | null;
      previousClose?: number | null;
      changePct?: number | null;
      unit?: string;
      currency?: string;
      source?: string;
      sourceUrl?: string | null;
      quoteType?: string;
      asOf?: string | null;
      asOfPrecision?: string;
      sessionDate?: string | null;
      asOfMeaning?: string;
      isAlternative?: boolean;
      comparison?: string;
      [key: string]: unknown;
    } | null;
    capturedAt?: string;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}
export interface FundamentalRange {
  status: string;
  low: number | null;
  base: number | null;
  high: number | null;
  coverage: number | null;
  aiCoverage?: number | null;
  analystCoverage?: number | null;
  mathematicalCoverage?: number | null;
  uncoveredWeightPct?: number | null;
  horizon?: string;
  sensitivityOnly?: boolean;
  method?: string;
  reason?: string;
}
export interface ForecastAnalysis {
  schemaVersion: string;
  modelVersion?: string | null;
  configVersion?: string | null;
  spot: number | null;
  spotObservedAt?: string | null;
  asOf?: string | null;
  bias: string;
  dimensions: AnalysisDimension[];
  directionContributions?: {
    key: string;
    coverage: number | null;
    configuredWeight: number | null;
    coverageAdjustedWeight: number | null;
    effectiveWeight: number | null;
    contribution: number | null;
    reason?: string;
  }[];
  scenarios: ForecastScenario[];
  levels?: { support: number | null; resistance: number | null } | null;
  warnings: string[];
  constituents: ConstituentValuation[];
  candidates: OptionCandidate[];
  entryCutoffAt?: string | null;
  plannedExitAt?: string | null;
  executionStatus?: string | null;
  snapshotPolicy?: {
    executionStatus: 'planning-only' | 'live-ready' | 'unavailable';
    quoteAsOf?: string | null;
    sessionDate?: string | null;
    reason?: string | null;
    windowStart?: string | null;
    windowEnd?: string | null;
    tradeWindowEnd?: string | null;
  } | null;
  snapshotEvidence?: {
    source: string;
    batchId?: string | number | null;
    capturedAt?: string | null;
    reason?: string | null;
  } | null;
  quoteAsOf?: string | null;
  quoteObservedAt?: string | null;
  marketContext?: OptionsMarketContext | null;
  fundamentalRange?: FundamentalRange | null;
  modelInputs?: {
    riskFreeRate: number | null;
    dividendYield: number | null;
    roundTripCostPerLeg: number | null;
  } | null;
  referenceData?: {
    weights?: WeightReference | null;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}
export interface ForecastSummary {
  id: number;
  indexCode: IndexCode;
  mode: ForecastMode;
  status: string;
  jobRunId?: number | null;
  generatedAt: string;
  targetAt?: string | null;
  inputCutoffAt?: string | null;
  modelVersion?: string | null;
  errorMessage?: string | null;
  aiStatus?: string | null;
  jobStatus?: string | null;
  jobRunStatus?: string | null;
  ai?: { status: string } | null;
}
export interface ForecastOutcomePayload {
  status?: string;
  predictedBias?: string;
  baselineSpot?: number | null;
  targetAt?: string | null;
  actualClose?: number | null;
  actualReturnPct?: number | null;
  actualDirection?: string;
  directionCorrect?: boolean | null;
  observedHigh?: number | null;
  observedLow?: number | null;
  coverageType?: string;
  terminalSource?: string;
  pathStatus?: string;
  warning?: string;
  scenarios?: { id: string; terminalCovered: boolean | null; low: number | null; high: number | null }[];
  [key: string]: unknown;
}
export interface StrategyOutcomePayload {
  status?: string;
  netHypotheticalPnl?: number | null;
  grossHypotheticalPnl?: number | null;
  exitAt?: string | null;
  reason?: string;
  coverageType?: string;
  eventOrderCertain?: boolean;
  warning?: string;
  [key: string]: unknown;
}
export interface StrategyOutcomeRecord extends StrategyOutcomePayload {
  candidateId?: string;
  candidateKey?: string;
  payload?: StrategyOutcomePayload;
}
export interface ForecastDetail extends ForecastSummary {
  analysis: ForecastAnalysis | null;
  candidates?: OptionCandidate[];
  valuations?: ConstituentValuation[];
  ai?: {
    status: string;
    selection?: {
      scenarioId: string | null;
      scenarioReason?: string | null;
      recommendations: { side: StrategySide; riskTier: RiskTier; candidateId: string | null; reason: string }[];
    } | null;
    inputTokens?: number;
    outputTokens?: number;
    costUsd?: number | null;
    errorMessage?: string | null;
  } | null;
  outcome?: { status: string; payload: ForecastOutcomePayload; [key: string]: unknown } | null;
  strategyOutcomes?: StrategyOutcomeRecord[];
}
export interface ExchangeSession {
  sessionDate: string;
  exchange: string;
  isTradingDay: boolean;
  openTime: string | null;
  closeTime: string | null;
  isWeeklyExpiry: boolean;
  isMonthlyExpiry: boolean;
}
export interface OptionsMetadataImport {
  indexCode: IndexCode;
  effectiveDate: string;
  source: string;
  constituents: { symbol: string; weightPct: number }[];
  sessions: ExchangeSession[];
}
export interface OptionsMetadata {
  indices?: { code: IndexCode; name: string; [key: string]: unknown }[];
  constituentDates?: { indexCode: IndexCode; effectiveDate: string }[];
  calendarCoverage?: { indexCode: IndexCode; from: string; to: string; count: number }[];
  sessions?: (ExchangeSession & { indexCode: IndexCode; source?: string })[];
  [key: string]: unknown;
}

const BASE = '/api/india/options';
export class OptionsApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'OptionsApiError';
  }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const problem = JSON.parse(text);
      message = problem.detail || problem.message || problem.title || text;
    } catch { /* The server can return plain-text errors. */ }
    throw new OptionsApiError(response.status, response.status === 401
      ? 'Application sign-in is required. Sign in, then retry; broker login is separate.'
      : message || `Options request failed (${response.status}).`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
function jsonBody(value: unknown): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) };
}
export const fetchOptionsConnection = () => request<BrokerConnection>('/connection');
export const startOptionsLogin = () => request<{ loginUrl: string }>('/connection/login', { method: 'POST' });
export const disconnectOptions = () => request<void>('/connection', { method: 'DELETE' });
export const fetchOptionsMetadata = () => request<OptionsMetadata>('/metadata');
export const importOptionsMetadata = (data: OptionsMetadataImport) => request<unknown>('/metadata', jsonBody(data));
export const createOptionsForecast = (indexCode: IndexCode, mode: ForecastMode, requestId: string) =>
  request<{ id: number; jobRunId: number; status: string }>('/forecasts', jsonBody({ indexCode, mode, requestId }));
export const fetchOptionsForecasts = (indexCode: IndexCode, mode: ForecastMode) =>
  request<{ items: ForecastSummary[] }>(`/forecasts?${new URLSearchParams({ indexCode, mode })}`);
export const deleteForecast = (id: number) => request<void>(`/forecasts/${id}`, { method: 'DELETE' });
export async function fetchOptionsForecast(id: number): Promise<ForecastDetail> {
  const detail = await request<ForecastDetail>(`/forecasts/${id}`);
  if (!detail.analysis) return detail;
  return {
    ...detail,
    analysis: {
      ...detail.analysis,
      candidates: detail.analysis.candidates?.length ? detail.analysis.candidates : detail.candidates || [],
      constituents: detail.analysis.constituents?.length ? detail.analysis.constituents : detail.valuations || [],
    },
  };
}

export function validateMetadataImport(text: string): OptionsMetadataImport {
  const data = JSON.parse(text) as OptionsMetadataImport;
  const validDate = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  const validTime = (value: unknown) => typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
  if (!data || !['NIFTY50', 'SENSEX'].includes(data.indexCode) || !validDate(data.effectiveDate)
      || typeof data.source !== 'string' || !data.source.trim() || data.source.length > 500) {
    throw new Error('Provide NIFTY50/SENSEX, a valid effectiveDate (YYYY-MM-DD), and a source (1–500 characters).');
  }
  if (!Array.isArray(data.constituents) || !data.constituents.length || data.constituents.length > 200
      || data.constituents.some(c => !c || typeof c.symbol !== 'string' || !c.symbol.trim()
        || !Number.isFinite(c.weightPct) || c.weightPct <= 0 || c.weightPct > 100
        || Number(c.weightPct.toFixed(6)) !== c.weightPct)
      || new Set(data.constituents.map(c => c.symbol.trim().toUpperCase())).size !== data.constituents.length
      || Math.abs(data.constituents.reduce((sum, c) => sum + c.weightPct, 0) - 100) > 0.01 + 1e-9) {
    throw new Error('Supply unique official constituent symbols and positive weights with at most 6 decimal places totaling 100% (±0.01%). No weights are inferred.');
  }
  if (!Array.isArray(data.sessions) || !data.sessions.length || data.sessions.length > 2000
      || new Set(data.sessions.map(s => s?.sessionDate)).size !== data.sessions.length
      || data.sessions.some(s => !s || !validDate(s.sessionDate)
        || s.exchange !== (data.indexCode === 'NIFTY50' ? 'NSE' : 'BSE')
        || typeof s.isTradingDay !== 'boolean'
        || (s.isWeeklyExpiry !== undefined && typeof s.isWeeklyExpiry !== 'boolean')
        || (s.isMonthlyExpiry !== undefined && typeof s.isMonthlyExpiry !== 'boolean')
        || (s.isTradingDay && (!validTime(s.openTime) || !validTime(s.closeTime) || s.openTime! >= s.closeTime!))
        || (!s.isTradingDay && (s.isWeeklyExpiry || s.isMonthlyExpiry)))) {
    throw new Error('Supply unique valid sessions, NSE for NIFTY50/BSE for SENSEX, boolean flags, and trading-day openTime < closeTime in HH:mm IST. Holidays cannot be expiry sessions.');
  }
  return {
    ...data,
    sessions: data.sessions.map(s => ({
      ...s, isWeeklyExpiry: s.isWeeklyExpiry ?? false, isMonthlyExpiry: s.isMonthlyExpiry ?? false,
    })),
  };
}
