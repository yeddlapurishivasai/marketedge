import { authFetch } from './api';
import type { IndexCode, OptionLeg } from './optionsApi';

export type StrangleAction = 'entry' | 'adjustment';
export type Evidence = string | number | boolean | null | Evidence[] | { [key: string]: Evidence };
export interface StrangleLeg extends Omit<OptionLeg, 'priceBasis'> {
  expiry?: string;
  product?: string;
  priceBasis?: string | null;
  bid?: number;
  ask?: number;
  liquidationPrice?: number;
  signedQuantity?: number;
}
export interface StrangleGreeks {
  delta?: number | null;
  gamma?: number | null;
  vega?: number | null;
  theta?: number | null;
  quantityWeighted?: { delta?: number; gamma?: number; vega?: number; theta?: number };
  [key: string]: Evidence | undefined;
}
export interface StranglePremiumBalance {
  putPremium: number | null;
  callPremium: number | null;
  imbalanceRatio: number | null;
  preferred: boolean;
  reason: string;
}
export interface StrangleRegime {
  version?: string;
  status?: 'available' | 'unavailable';
  state?: 'risk-on' | 'risk-off' | 'mixed' | 'unavailable';
  chartState?: 'risk-on' | 'risk-off' | 'mixed' | 'unavailable';
  sourceIndexCode?: string;
  timeframe?: string;
  direction?: 'up' | 'down' | 'neutral';
  support?: number | null;
  resistance?: number | null;
  lastClose?: number | null;
  observedAt?: string | null;
  basis?: string;
  reason?: string | null;
  [key: string]: Evidence | undefined;
}
export interface StrangleCandidate {
  id: string;
  action: string;
  reason?: string;
  eligible?: boolean;
  legs?: StrangleLeg[];
  closingLegs?: StrangleLeg[];
  steps?: string[];
  greeks?: StrangleGreeks;
  greeksAfter?: StrangleGreeks;
  maxProfit?: number | null;
  maxLoss?: number | null;
  remainingRiskBudget?: number | null;
  stressLossRupees?: number | null;
  strategyPnlRupees?: number | null;
  closeQuantity?: number | null;
  structure?: 'hedged-strangle' | 'hedged-straddle';
  premiumBalance?: StranglePremiumBalance;
  [key: string]: unknown;
}
export interface StranglePosition {
  exchange: string;
  tradingSymbol: string;
  product: string;
  quantity: number;
  averagePrice?: number;
  average_price?: number;
  tradingsymbol?: string;
  entryPrice?: number;
  [key: string]: unknown;
}
export interface StrangleResult {
  analysisMode?: 'current-positions';
  positionRisk?: Array<{
    exchange: string;
    tradingSymbol: string;
    product?: string;
    signedQuantity: number;
    strike?: number;
    expiry?: string;
    optionType?: string;
    brokerPnlRupees?: number;
    overnightQuantity?: number;
    greeks?: StrangleGreeks;
    quoteObservedAt?: string;
    unavailableReason?: string;
  }>;
  sessionPnl?: {
    status: 'verified' | 'partial' | 'unavailable';
    realizedRupees: number | null;
    unrealizedRupees: number | null;
    totalRupees: number | null;
    cashFlowRupees: number | null;
    closeValueRupees: number | null;
    tradeCount: number;
    source: string;
    scope: string;
    reason?: string;
  };
  payoff?: {
    status: 'available' | 'unavailable';
    maxProfitRupees: number | null;
    maxLossRupees: number | null;
    profitUnbounded: boolean;
    lossUnbounded: boolean;
    remainingUpsideRupees: number | null;
    expiry?: string;
    basis: string;
    reason?: string;
  };
  riskPlan?: {
    status: 'available' | 'unavailable';
    suggestedSlRupees: number | null;
    stopPnlRupees: number | null;
    targetPnlRupees?: number | null;
    remainingLossRupees: number | null;
    stressLossRupees?: number | null;
    basis: string;
    reason?: string;
  };
  exitPolicy?: {
    version: string;
    reason: 'stop-loss' | 'target' | 'time' | null;
    stopPnlRupees: number | null;
    targetPnlRupees: number | null;
  };
  premiumBalance?: StranglePremiumBalance;
  modelInputs?: {
    riskFreeRate: number | null;
    dividendYield: number | null;
    source: string;
  };
  asOf?: string;
  indexCode?: IndexCode;
  expiry?: string;
  spot?: number;
  atmStrike?: number;
  atmPremiumSum?: number;
  shortPutStrike?: number;
  shortCallStrike?: number;
  legs?: StrangleLeg[];
  netCreditPoints?: number;
  quantity?: number;
  originalQuantity?: number;
  lotSize?: number;
  suggestedXPoints?: number | null;
  xPoints?: number | null;
  targetPoints?: number | null;
  stopPoints?: number | null;
  stopRiskRupees?: number;
  targetRupees?: number;
  pnlPointBasis?: string;
  greeks?: StrangleGreeks;
  maxProfit?: number | null;
  maxLoss?: number | null;
  margin?: Evidence;
  trend?: Evidence;
  regime?: StrangleRegime;
  xCalculation?: Evidence;
  positions?: StranglePosition[] | { net?: StranglePosition[]; day?: StranglePosition[]; [key: string]: unknown };
  strategyPnlPoints?: number | null;
  strategyPnlRupees?: number | null;
  originalRiskBudget?: number;
  remainingRiskBudget?: number;
  candidates?: StrangleCandidate[];
  warnings?: string[];
  blockers?: string[];
  executionStatus?: string;
  mandatoryExit?: boolean;
  eligible?: boolean;
  positionsConfirmed?: boolean;
  exitAt?: string;
  entryCutoffAt?: string;
  [key: string]: unknown;
}
export interface StrangleAi {
  status?: string;
  provider?: string | null;
  candidateId?: string;
  selectedCandidateId?: string;
  rationale?: string;
  reason?: string;
  error?: string;
  errorMessage?: string;
  output?: { candidateId: string; reason: string } | null;
  [key: string]: unknown;
}
export interface StrangleRun {
  id: number;
  jobRunId: number;
  indexCode: IndexCode;
  action: StrangleAction;
  status: string;
  generatedAt?: string | null;
  errorMessage?: string | null;
  result: StrangleResult | null;
  ai: StrangleAi | null;
  [key: string]: unknown;
}
export interface StrangleRunRequest {
  requestId: string;
  indexCode: IndexCode;
  action: StrangleAction;
  wingWidth: number;
  xOverride: number | null;
  strategyId: number | null;
  riskFreeRate?: number | null;
  dividendYield?: number | null;
  selectedTradeIds?: string[];
}
export interface BaselineLeg {
  exchange: string;
  tradingSymbol: string;
  product: string;
  quantity: number;
  entryPrice: number;
}
export interface StrangleCashFlow {
  exchange: string;
  tradingSymbol: string;
  product: string;
  signedQuantity: number;
  price: number;
  tradeId: string;
}
export interface StrangleBaseline {
  id: number;
  indexCode: IndexCode;
  entryRunId: number;
  xPoints?: number;
  baseline?: {
    xPoints: number;
    legs?: BaselineLeg[];
    originalRiskBudget: number;
    originalMaxLoss?: number;
    quantity?: number;
    positionsConfirmed?: boolean;
    cashFlows?: StrangleCashFlow[];
    [key: string]: unknown;
  };
  legs?: BaselineLeg[];
  createdAt?: string;
  originalRiskBudgetRupees?: number;
  [key: string]: unknown;
}

const BASE = '/api/india/options/strangles';
async function request<T>(path: string, data?: unknown): Promise<T> {
  const response = await authFetch(`${BASE}${path}`, data === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const problem = JSON.parse(text);
      message = problem.detail || problem.message || problem.title || text;
    } catch { /* Plain-text API errors remain visible. */ }
    throw new Error(response.status === 401
      ? 'Application sign-in is required; Zerodha login is separate.'
      : message || `Strangle request failed (${response.status}).`);
  }
  return response.json();
}
export const createStrangleRun = (data: StrangleRunRequest) => request<StrangleRun>('/runs', data);
export const fetchStrangleRun = (id: number) => request<StrangleRun>(`/runs/${id}`);
export const fetchLatestStrangleRun = async (indexCode: IndexCode) =>
  (await request<{ items: StrangleRun[] }>(`/runs?${new URLSearchParams({ indexCode, limit: '1' })}`)).items[0] ?? null;
export const fetchStrangleBaselines = () => request<{ items: StrangleBaseline[] }>('/strategies');
export const confirmStrangleBaseline = (data: { entryRunId: number; legs: BaselineLeg[]; xPoints: number }) =>
  request<StrangleBaseline>('/strategies', data);
