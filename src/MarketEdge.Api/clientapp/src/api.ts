export interface Sector {
  id: number;
  sectorName: string;
  stockCount: number;
}

export interface Stock {
  id: number;
  symbol: string;
  companyName: string;
  sectorId: number;
  sectorName?: string;
  broadSector?: string;
  marketCap?: number;
  isFno: boolean;
  isTestSample: boolean;
}

export interface PagedResult<T> {
  items: T[];
  totalCount: number;
  page: number;
  pageSize: number;
}

export type Market = 'india' | 'us';

const BASE = '/api';

export async function fetchSectors(market: Market, testSampleOnly = false): Promise<Sector[]> {
  const qs = testSampleOnly ? '?testSampleOnly=true' : '';
  const res = await fetch(`${BASE}/${market}/sectors${qs}`);
  if (!res.ok) throw new Error('Failed to fetch sectors');
  return res.json();
}

export async function createSector(market: Market, sectorName: string): Promise<Sector> {
  const res = await fetch(`${BASE}/${market}/sectors`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sectorName })
  });
  if (!res.ok) throw new Error('Failed to create sector');
  return res.json();
}

export async function renameSector(market: Market, id: number, sectorName: string): Promise<void> {
  const res = await fetch(`${BASE}/${market}/sectors/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sectorName })
  });
  if (!res.ok) throw new Error('Failed to rename sector');
}

export async function deleteSector(market: Market, id: number): Promise<void> {
  const res = await fetch(`${BASE}/${market}/sectors/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Cannot delete sector with stocks');
}

export async function fetchStocks(
  market: Market,
  params: { q?: string; sectorId?: number; page?: number; pageSize?: number }
): Promise<PagedResult<Stock>> {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q);
  if (params.sectorId) sp.set('sectorId', params.sectorId.toString());
  sp.set('page', (params.page || 1).toString());
  sp.set('pageSize', (params.pageSize || 50).toString());
  const res = await fetch(`${BASE}/${market}/stocks?${sp}`);
  if (!res.ok) throw new Error('Failed to fetch stocks');
  return res.json();
}

export async function createStock(market: Market, data: { symbol: string; companyName: string; sectorId: number; broadSector?: string; isFno?: boolean }): Promise<Stock> {
  const res = await fetch(`${BASE}/${market}/stocks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error('Failed to create stock');
  return res.json();
}

export async function updateStock(market: Market, id: number, data: { companyName?: string; sectorId?: number; broadSector?: string; isFno?: boolean }): Promise<void> {
  const res = await fetch(`${BASE}/${market}/stocks/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error('Failed to update stock');
}

export async function deleteStock(market: Market, id: number): Promise<void> {
  const res = await fetch(`${BASE}/${market}/stocks/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete stock');
}

export async function moveStocks(market: Market, stockIds: number[], targetSectorId: number): Promise<void> {
  const res = await fetch(`${BASE}/${market}/stocks/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stockIds, targetSectorId })
  });
  if (!res.ok) throw new Error('Failed to move stocks');
}

// ── Job Runs ──

export interface JobStage {
  key: string;
  label: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  progress: number;
  detail?: string;
}

export interface JobRun {
  id: number;
  jobType: string;
  market: string;
  weekNumber: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  parameters?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  stages?: JobStage[];
  errorMessage?: string;
  startedAt?: string;
  completedAt?: string;
  createdAt: string;
  durationSeconds?: number;
}

export async function fetchJobRuns(params?: { market?: string; jobType?: string; page?: number; pageSize?: number }): Promise<JobRun[]> {
  const sp = new URLSearchParams();
  if (params?.market) sp.set('market', params.market);
  if (params?.jobType) sp.set('jobType', params.jobType);
  sp.set('page', (params?.page || 1).toString());
  sp.set('pageSize', (params?.pageSize || 20).toString());
  const res = await fetch(`${BASE}/jobs?${sp}`);
  if (!res.ok) throw new Error('Failed to fetch job runs');
  return res.json();
}

export async function fetchJobRun(id: number): Promise<JobRun> {
  const res = await fetch(`${BASE}/jobs/${id}`);
  if (!res.ok) throw new Error('Failed to fetch job run');
  return res.json();
}

export async function cancelJobRun(id: number): Promise<void> {
  const res = await fetch(`${BASE}/jobs/${id}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to cancel job run');
}

// ── Data Ingestion (Admin) ──

export interface TriggerIngestionRequest {
  testSample?: boolean;
  limit?: number;
  steps?: string[];
  missingOnly?: boolean;
}

export async function triggerIngestion(market: Market, request: TriggerIngestionRequest): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/ingestion/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to trigger ingestion');
  return res.json();
}

// ── Stage 2 Analysis ──

export interface TriggerAnalysisRequest {
  minMarketCap?: number;
  maxMarketCap?: number;
  sectorIds?: number[];
  limit?: number;
  testSampleOnly?: boolean;
  retryFailedOnly?: boolean;
  weekNumber?: string;
  force?: boolean;
}

export async function triggerAnalysis(market: Market, request?: TriggerAnalysisRequest): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/analysis/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request || {})
  });
  if (!res.ok) throw new Error('Failed to trigger analysis');
  return res.json();
}

export interface StageAnalysisResult {
  id: number;
  runId: number;
  symbol: string;
  companyName: string;
  sectorId: number;
  sectorName: string;
  closePrice?: number;
  ma10?: number;
  ma30?: number;
  marketCap?: number;
  isStage2: boolean;
  classification?: string;
  weeksInStage2?: number;
  rsScore?: number;
  rsRank?: number;
  rsRating?: number;
  rs1w?: number;
  rs2w?: number;
  rs3w?: number;
  rsDelta1w?: number;
  rsDelta2w?: number;
  rsDelta3w?: number;
  momentumScore?: number;
  roc1w?: number;
  roc2w?: number;
  roc3w?: number;
  quadrant?: string;
  adRatio?: number;
  adClassification?: string;
}

export interface SectorStage2Count {
  sectorName: string;
  stage2Count: number;
  totalCount: number;
}

export interface Stage2Summary {
  totalStocks: number;
  stage2Count: number;
  newAdditions: number;
  reEntries: number;
  continuing: number;
  removed: number;
  bySector: SectorStage2Count[];
  top25: StageAnalysisResult[];
}

export async function fetchStage2Summary(market: Market): Promise<Stage2Summary> {
  const res = await fetch(`${BASE}/${market}/analysis/summary`);
  if (!res.ok) throw new Error('Failed to fetch summary');
  return res.json();
}

export async function fetchStage2Stocks(market: Market, runId: number, params?: { classification?: string; sectorId?: number }): Promise<StageAnalysisResult[]> {
  const sp = new URLSearchParams();
  if (params?.classification) sp.set('classification', params.classification);
  if (params?.sectorId) sp.set('sectorId', params.sectorId.toString());
  const res = await fetch(`${BASE}/${market}/analysis/runs/${runId}/stocks?${sp}`);
  if (!res.ok) throw new Error('Failed to fetch stage 2 stocks');
  return res.json();
}

export interface SectorRotation {
  sectorName: string;
  sectorId: number;
  avgRSScore: number;
  avgRSDelta2w: number;
  quadrant: string;
  stockCount: number;
  accumulatingCount: number;
  distributingCount: number;
}

export async function fetchSectorRotation(market: Market, runId: number): Promise<SectorRotation[]> {
  const res = await fetch(`${BASE}/${market}/analysis/runs/${runId}/sector-rotation`);
  if (!res.ok) throw new Error('Failed to fetch sector rotation');
  return res.json();
}

export interface Stage2History {
  runId: number;
  runDate: string;
  totalStage2: number;
  bySector: SectorStage2Count[];
}

export async function fetchStage2History(market: Market, maxRuns = 10): Promise<Stage2History[]> {
  const res = await fetch(`${BASE}/${market}/analysis/history?maxRuns=${maxRuns}`);
  if (!res.ok) throw new Error('Failed to fetch history');
  return res.json();
}

export interface SectorRotationHistory {
  runId: number;
  runDate: string;
  sectors: SectorRotation[];
}

export async function fetchRotationHistory(market: Market, maxRuns = 12): Promise<SectorRotationHistory[]> {
  const res = await fetch(`${BASE}/${market}/analysis/rotation-history?maxRuns=${maxRuns}`);
  if (!res.ok) throw new Error('Failed to fetch rotation history');
  return res.json();
}

// ── Stock Lookup ──

export interface LookupCandidate {
  symbol: string;
  companyName: string;
  industry?: string | null;
  broadSector?: string | null;
}

export interface LookupTechnical {
  asOfDate?: string | null;
  close?: number | null;
  dayPct?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  high52w?: number | null;
  from52wHigh?: number | null;
  marketCap?: number | null;
  rs?: number | null;
  rs1d?: number | null;
  rs1w?: number | null;
  rs1m?: number | null;
  rs3m?: number | null;
  rs6m?: number | null;
  rsType?: string | null;
  rsDate?: string | null;
  scannerHits?: number | null;
  lastScannerHit?: string | null;
}

export interface LookupAnalyst {
  asOfDate?: string | null;
  consensusRating?: string | null;
  numAnalysts?: number | null;
  currentQuarterEps?: number | null;
  nextQuarterEps?: number | null;
  currentYearEps?: number | null;
  nextYearEps?: number | null;
  targetLowPrice?: number | null;
  targetMeanPrice?: number | null;
  targetHighPrice?: number | null;
  recommendations: RecommendationPeriod[];
  latestRatingFirm?: string | null;
  latestRatingGrade?: string | null;
  latestRatingAction?: string | null;
  latestRatingDate?: string | null;
}

export interface RecommendationPeriod {
  period: string;       // yfinance relative label: '0m' = current month, '-1m' = 1 month ago, ...
  strongBuy: number;
  buy: number;
  hold: number;
  sell: number;
  strongSell: number;
}

export interface LookupEpsForecast {
  periodType: string;
  periodEndDate: string;
  consensusEps?: number | null;
  highEps?: number | null;
  lowEps?: number | null;
  numEstimates?: number | null;
  revisionsUp: number;
  revisionsDown: number;
}

export interface StockLookupDetail {
  symbol: string;
  companyName: string;
  broadSector?: string | null;
  industry?: string | null;
  market: string;
  exchange?: string | null;
  active: boolean;
  isFno: boolean;
  barsAvailable?: number | null;
  technical?: LookupTechnical | null;
  analyst?: LookupAnalyst | null;
  quarterlyEps: LookupEpsForecast[];
  yearlyEps: LookupEpsForecast[];
}

export interface LookupBar {
  date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
}

export async function searchLookup(market: Market, q: string): Promise<LookupCandidate[]> {
  const res = await fetch(`${BASE}/${market}/lookup/search?q=${encodeURIComponent(q)}`);
  if (!res.ok) throw new Error('Failed to search symbols');
  return res.json();
}

export async function fetchLookupDetail(market: Market, symbol: string): Promise<StockLookupDetail> {
  const res = await fetch(`${BASE}/${market}/lookup/${encodeURIComponent(symbol)}`);
  if (res.status === 404) throw new Error(`No data found for '${symbol}' in ${market.toUpperCase()}`);
  if (!res.ok) throw new Error('Failed to load symbol detail');
  return res.json();
}

export async function fetchLookupBars(market: Market, symbol: string, timeframe: 'daily' | 'weekly'): Promise<LookupBar[]> {
  const res = await fetch(`${BASE}/${market}/lookup/${encodeURIComponent(symbol)}/bars?timeframe=${timeframe}`);
  if (!res.ok) throw new Error('Failed to load price bars');
  return res.json();
}

export async function refreshStockData(market: Market, symbol: string): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/lookup/${encodeURIComponent(symbol)}/refresh-stock`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to refresh stock');
  return res.json();
}

// ── Scanners (feature 011) ──

export interface ScannerInfo {
  name: string;
  label: string;
  family: string;
  comingSoon: boolean;
  latestHits: number;
  latestScanDate?: string | null;
}

export interface ScannerResult {
  symbol: string;
  companyName?: string | null;
  sectorName?: string | null;
  industry?: string | null;
  closePrice?: number | null;
  dayChangePct?: number | null;
  volume?: number | null;
  relVolume?: number | null;
  rsRating?: number | null;
  triggerDetails?: string | null;
}

export interface ScannerSchedule {
  market: string;
  enabled: boolean;
  intervalMinutes: number;
  lastEnqueuedAt?: string | null;
  updatedAt: string;
  isMarketOpen: boolean;
  lastRunAt?: string | null;
}

export async function fetchScanners(market: Market): Promise<ScannerInfo[]> {
  const res = await fetch(`${BASE}/${market}/scanners`);
  if (!res.ok) throw new Error('Failed to load scanners');
  return res.json();
}

export async function triggerScanner(market: Market, body: { scannerName?: string | null; universe?: string; manageTrades?: boolean }): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/scanners/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to trigger scanner');
  return res.json();
}

export async function fetchScannerDates(market: Market, scannerName: string): Promise<string[]> {
  const res = await fetch(`${BASE}/${market}/scanners/${encodeURIComponent(scannerName)}/dates`);
  if (!res.ok) throw new Error('Failed to load scan dates');
  return res.json();
}

export async function fetchScannerResults(market: Market, scannerName: string, date?: string): Promise<ScannerResult[]> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : '';
  const res = await fetch(`${BASE}/${market}/scanners/${encodeURIComponent(scannerName)}/results${qs}`);
  if (!res.ok) throw new Error('Failed to load scanner results');
  return res.json();
}

export async function fetchScannerSchedule(market: Market): Promise<ScannerSchedule> {
  const res = await fetch(`${BASE}/${market}/scanners/schedule`);
  if (!res.ok) throw new Error('Failed to load schedule');
  return res.json();
}

export async function updateScannerSchedule(market: Market, body: { enabled: boolean; intervalMinutes?: number }): Promise<ScannerSchedule> {
  const res = await fetch(`${BASE}/${market}/scanners/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Failed to update schedule');
  return res.json();
}

// Nightly/weekend job schedules (fundamentals refresh, weekend stage2 analysis).
export interface JobSchedule {
  market: string;
  enabled: boolean;
  hourLocal: number;
  lastEnqueuedAt?: string | null;
  updatedAt: string;
  lastRunAt?: string | null;
}

export async function fetchFundamentalsSchedule(market: Market): Promise<JobSchedule> {
  const res = await fetch(`${BASE}/${market}/ingestion/fundamentals-schedule`);
  if (!res.ok) throw new Error('Failed to load fundamentals schedule');
  return res.json();
}

export async function updateFundamentalsSchedule(market: Market, body: { enabled: boolean; hourLocal?: number }): Promise<JobSchedule> {
  const res = await fetch(`${BASE}/${market}/ingestion/fundamentals-schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Failed to update fundamentals schedule');
  return res.json();
}

export async function fetchStage2Schedule(market: Market): Promise<JobSchedule> {
  const res = await fetch(`${BASE}/${market}/analysis/schedule`);
  if (!res.ok) throw new Error('Failed to load stage2 schedule');
  return res.json();
}

export async function updateStage2Schedule(market: Market, body: { enabled: boolean; hourLocal?: number }): Promise<JobSchedule> {
  const res = await fetch(`${BASE}/${market}/analysis/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Failed to update stage2 schedule');
  return res.json();
}

// ---------- Fundamental Scanners ----------

export interface FundamentalRow {
  symbol: string;
  companyName: string;
  broadSector?: string | null;
  industry?: string | null;
  asOfDate: string;
  latestQuarterEnd?: string | null;
  revenue?: number | null;
  revenueGrowthYoyPct?: number | null;
  operatingProfit?: number | null;
  opm?: number | null;
  opmPrevQ?: number | null;
  opmYoyQ?: number | null;
  opmTrend?: string | null;
  netProfit?: number | null;
  netMarginPct?: number | null;
  earningsGrowthYoyPct?: number | null;
  earningsGrowthQoqPct?: number | null;
  earningsIncreasing?: boolean | null;
  operatingProfitTrend?: string | null;
  lastEarningsDate?: string | null;
  prevEarningsDate?: string | null;
  nextEarningsDate?: string | null;
  lastReportedEps?: number | null;
  lastEpsSurprisePct?: number | null;
  trailingPe?: number | null;
  forwardPe?: number | null;
  earningsAnnouncedRecent: boolean;
  epsHistory: EpsQuarter[];
}

export interface EpsQuarter {
  date?: string | null;
  estimate?: number | null;
  actual?: number | null;
  surprisePct?: number | null;
}

export interface SignalNewsItem {
  title: string;
  publisher?: string | null;
  date?: string | null;
  link?: string | null;
  tags?: string[] | null;
}

export interface FundamentalSignals {
  capexCwip?: number | null;
  capexCwipPrevQ?: number | null;
  capexChangePct?: number | null;
  capexTrend?: string | null;
  capexAsOf?: string | null;
  detected: string[];
  news: SignalNewsItem[];
  signalsText?: string | null;
  updatedAt: string;
}

export interface FundamentalDetail {
  row: FundamentalRow;
  note?: string | null;
  signals?: FundamentalSignals | null;
}

export type FundamentalScanner =
  | 'all'
  | 'earnings_increasing'
  | 'margin_expanding'
  | 'operating_profit_expanding'
  | 'recently_announced';

export async function fetchFundamentals(market: Market, scanner: FundamentalScanner = 'all'): Promise<FundamentalRow[]> {
  const qs = scanner && scanner !== 'all' ? `?scanner=${encodeURIComponent(scanner)}` : '';
  const res = await fetch(`${BASE}/${market}/fundamentals${qs}`);
  if (!res.ok) throw new Error('Failed to load fundamentals');
  return res.json();
}

export async function fetchFundamentalDetail(market: Market, symbol: string): Promise<FundamentalDetail> {
  const res = await fetch(`${BASE}/${market}/fundamentals/${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error('Failed to load fundamental detail');
  return res.json();
}

export interface FundamentalIdeaRow {
  symbol: string;
  companyName: string;
  broadSector?: string | null;
  industry?: string | null;
  earningsDate: string;
  epsBeatPct?: number | null;
  opmExpansionYoyPct?: number | null;
  operatingProfitExpansionYoyPct?: number | null;
  latestRatingFirm?: string | null;
  latestRatingGrade?: string | null;
  latestRatingAction?: string | null;
  latestRatingDate?: string | null;
  targetLowPrice?: number | null;
  targetMeanPrice?: number | null;
  targetHighPrice?: number | null;
  epsBeatConfidence?: number | null;
  opmExpansionConfidence?: number | null;
  operatingProfitExpansionConfidence?: number | null;
  analystRatingConfidence?: number | null;
  targetUpsideConfidence?: number | null;
  fundamentalConfidence?: number | null;
  technicalConfidence?: number | null;
  overallConfidence?: number | null;
  daysSinceEarnings?: number | null;
  daysSinceRating?: number | null;
  confidenceRationaleJson?: string | null;
  isStage2?: boolean | null;
  directionScore?: number | null;
  side?: 'long' | 'short' | 'neutral' | null;
  epsBeatConfidenceShort?: number | null;
  opmExpansionConfidenceShort?: number | null;
  operatingProfitExpansionConfidenceShort?: number | null;
  analystRatingConfidenceShort?: number | null;
  fundamentalConfidenceShort?: number | null;
  overallConfidenceShort?: number | null;
  confidenceRationaleShortJson?: string | null;
  updatedAt: string;
}

export async function fetchFundamentalIdeas(market: Market): Promise<FundamentalIdeaRow[]> {
  const res = await fetch(`${BASE}/${market}/fundamentals/ideas`);
  if (!res.ok) throw new Error('Failed to load fundamental ideas');
  return res.json();
}

/**
 * Enqueues a stage-2 fundamentals refresh (the screener's data source). Pass
 * `force: true` to bypass the earnings-window filter and refresh every stage-2 ticker.
 */
export async function triggerFundamentalsRefresh(
  market: Market,
  body: { force?: boolean; universe?: 'stage2' | 'all'; missingOnly?: boolean } = {},
): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/fundamentals/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force: body.force ?? false, universe: body.universe ?? 'stage2', missingOnly: body.missingOnly ?? false }),
  });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to trigger fundamentals refresh');
  return res.json();
}

export async function saveFundamentalNote(market: Market, symbol: string, noteText: string): Promise<void> {
  const res = await fetch(`${BASE}/${market}/fundamentals/${encodeURIComponent(symbol)}/note`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ noteText })
  });
  if (!res.ok) throw new Error('Failed to save note');
}

// --- Breakout confidence engine ---
export type BreakoutProfile = 'swing' | 'positional';

export interface Breakout {
  id: number;
  ticker: string;
  companyName?: string | null;
  tradeType: string;
  direction: string;
  status: string;
  entryScanner?: string | null;
  flaggedScanners: string[];
  scannerHitCount: number;
  entryAt: string;
  entryPrice: number;
  qty?: number | null;
  initialStop?: number | null;
  currentStop?: number | null;
  stopBasis?: string | null;
  riskPerShare?: number | null;
  movedToBe: boolean;
  lastPrice?: number | null;
  pnLPct?: number | null;
  pnLAmount?: number | null;
  mfePct?: number | null;
  maePct?: number | null;
  exitAt?: string | null;
  exitPrice?: number | null;
  exitReason?: string | null;
  confidenceScore?: number | null;
  confidenceRationaleJson?: string | null;
  updatedAt: string;
}

export interface BreakoutStats {
  activeCount: number;
  closedCount: number;
  wins: number;
  losses: number;
  winRatePct?: number | null;
  avgPnLPct?: number | null;
  realizedPnLAmount?: number | null;
  openPnLAmount?: number | null;
  swingOpenPnLAmount?: number | null;
  swingRealizedPnLAmount?: number | null;
  positionalOpenPnLAmount?: number | null;
  positionalRealizedPnLAmount?: number | null;
}

export async function fetchBreakouts(
  market: Market,
  params: { status?: string; tradeType?: string } = {}
): Promise<Breakout[]> {
  const sp = new URLSearchParams();
  if (params.status) sp.set('status', params.status);
  if (params.tradeType) sp.set('tradeType', params.tradeType);
  const qs = sp.toString();
  const res = await fetch(`${BASE}/${market}/breakouts${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error('Failed to fetch breakouts');
  return res.json();
}

export async function fetchBreakoutStats(market: Market): Promise<BreakoutStats> {
  const res = await fetch(`${BASE}/${market}/breakouts/stats`);
  if (!res.ok) throw new Error('Failed to fetch breakout stats');
  return res.json();
}

export interface BreakoutPnlSummary {
  from: string;
  to: string;
  tradeType?: string | null;
  realizedCount: number;
  wins: number;
  losses: number;
  winRatePct?: number | null;
  realizedPnLAmount: number;
  avgRealizedPnLPct?: number | null;
  openCount: number;
  openPnLAmount: number;
}

export interface BreakoutDay {
  date: string;
  tradeType?: string | null;
  entries: Breakout[];
  exits: Breakout[];
}

export async function fetchBreakoutPnl(
  market: Market,
  from: string,
  to: string,
  tradeType?: string
): Promise<BreakoutPnlSummary> {
  const sp = new URLSearchParams({ from, to });
  if (tradeType) sp.set('tradeType', tradeType);
  const res = await fetch(`${BASE}/${market}/breakouts/pnl?${sp}`);
  if (!res.ok) throw new Error('Failed to fetch breakout P&L');
  return res.json();
}

export async function fetchBreakoutsByDay(
  market: Market,
  date: string,
  tradeType?: string
): Promise<BreakoutDay> {
  const sp = new URLSearchParams({ date });
  if (tradeType) sp.set('tradeType', tradeType);
  const res = await fetch(`${BASE}/${market}/breakouts/day?${sp}`);
  if (!res.ok) throw new Error('Failed to fetch day breakouts');
  return res.json();
}

export interface NearPivot {
  id: number;
  ticker: string;
  companyName?: string | null;
  tradeType: string;
  direction: string;
  flaggedScanners: string[];
  scannerHitCount: number;
  lastClose: number;
  pivotPrice: number;
  distancePct: number;
  relVolume?: number | null;
  volumeConfirmed: boolean;
  scanDate: string;
  updatedAt: string;
}

export async function fetchNearPivots(
  market: Market,
  params: { tradeType?: string; maxDistancePct?: number } = {}
): Promise<NearPivot[]> {
  const sp = new URLSearchParams();
  if (params.tradeType) sp.set('tradeType', params.tradeType);
  if (params.maxDistancePct != null) sp.set('maxDistancePct', String(params.maxDistancePct));
  const qs = sp.toString();
  const res = await fetch(`${BASE}/${market}/breakouts/near-pivot${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error('Failed to fetch near-pivot candidates');
  return res.json();
}

export interface ScannerPerformance {
  scanner: string;
  trades: number;
  closed: number;
  openCount: number;
  wins: number;
  losses: number;
  winRatePct?: number | null;
  reliabilityScore: number;
  avgPnLPct?: number | null;
  realizedPnLAmount?: number | null;
  openPnLAmount?: number | null;
}

export async function fetchScannerPerformance(market: Market): Promise<ScannerPerformance[]> {
  const res = await fetch(`${BASE}/${market}/scanners/performance`);
  if (!res.ok) throw new Error('Failed to fetch scanner performance');
  return res.json();
}

export interface ScoringWeight {
  id: number;
  market: string;
  category: string;       // 'pattern' | 'mix'
  componentKey: string;   // scanner name OR '{profile}:{component}'
  weight: number;
  seedWeight: number;
  wins: number;
  losses: number;
  manualOverride: boolean;
  updatedAt: string;
}

export async function fetchScoringWeights(market: Market): Promise<ScoringWeight[]> {
  const res = await fetch(`${BASE}/${market}/scoring/weights`);
  if (!res.ok) throw new Error('Failed to fetch scoring weights');
  return res.json();
}

export async function updateScoringWeight(
  market: Market,
  id: number,
  update: { weight?: number; manualOverride?: boolean }
): Promise<ScoringWeight> {
  const res = await fetch(`${BASE}/${market}/scoring/weights/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error('Failed to update scoring weight');
  return res.json();
}

// --- Database snapshot (bacpac export/import) ---
export interface DatabaseInfo {
  server: string;
  database: string;
  sqlPackageAvailable: boolean;
  storageConfigured: boolean;
}

export async function fetchDatabaseInfo(): Promise<DatabaseInfo> {
  const res = await fetch(`${BASE}/admin/database/info`);
  if (!res.ok) throw new Error('Failed to fetch database info');
  return res.json();
}

export interface DatabaseExport {
  fileName: string;
  sizeBytes: number;
  url: string;
  expiresUtc: string;
}

// Trigger a server-side export; the .bacpac is uploaded to storage and a SAS download URL is returned.
export async function exportDatabase(): Promise<DatabaseExport> {
  const res = await fetch(`${BASE}/admin/database/export`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.text()) || 'Export failed');
  return res.json();
}

export async function importDatabase(file: File, targetDatabase?: string): Promise<{ database: string; server: string }> {
  const fd = new FormData();
  fd.append('file', file);
  if (targetDatabase) fd.append('targetDatabase', targetDatabase);
  const res = await fetch(`${BASE}/admin/database/import`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error((await res.text()) || 'Import failed');
  return res.json();
}

// --- Market regime (feature 013) ---
export interface MarketCondition {
  label: string;                  // Pessimistic | Bearish | Cautious | Euphoric | Uptrend | Neutral | Unavailable
  tone: string;                   // red | yellow | green | grey
  explanation: string;
  benchmarkSymbol?: string | null;
  asOfDate?: string | null;
  close?: number | null;
  sma20?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  closeVsSma20Pct?: number | null;
  closeVsSma50Pct?: number | null;
  closeVsSma200Pct?: number | null;
  volumeVsAvgPct?: number | null;
  available: boolean;
}

export interface BreadthSignal {
  key: string;
  label: string;
  value?: number | null;
  threshold: string;
  positive?: boolean | null;
}

export interface MarketBreadth {
  label: string;                  // Bullish | Positive | Neutral | Negative | Bearish | Unavailable
  tone: string;
  score?: number | null;          // 0..100
  positiveSignals: number;
  availableSignals: number;
  evaluatedCount: number;
  asOfDate?: string | null;
  benchmarkSymbol?: string | null;
  volatilitySymbol?: string | null;
  signals: BreadthSignal[];
  available: boolean;
}

export interface MarketRegime {
  market: string;
  regime: string;                 // RiskOn | SelectiveRiskOn | Caution | RiskOff | Mixed | Unavailable
  regimeLabel: string;
  tone: string;
  posture: string;
  condition: MarketCondition;
  breadth: MarketBreadth;
  asOfDate?: string | null;
  available: boolean;
  stale: boolean;
  staleReason?: string | null;
}

export interface RegimeSchedule {
  market: string;
  enabled: boolean;
  hourLocal: number;
  lastEnqueuedAt?: string | null;
  updatedAt: string;
  lastRunAt?: string | null;
}

export async function fetchRegime(market: Market): Promise<MarketRegime> {
  const res = await fetch(`${BASE}/${market}/regime`);
  if (!res.ok) throw new Error('Failed to load market regime');
  return res.json();
}

export async function refreshRegime(market: Market): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/regime/refresh`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to refresh regime');
  return res.json();
}

export async function fetchRegimeSchedule(market: Market): Promise<RegimeSchedule> {
  const res = await fetch(`${BASE}/${market}/regime/schedule`);
  if (!res.ok) throw new Error('Failed to load regime schedule');
  return res.json();
}

export async function updateRegimeSchedule(market: Market, body: { enabled: boolean; hourLocal?: number }): Promise<RegimeSchedule> {
  const res = await fetch(`${BASE}/${market}/regime/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('Failed to update regime schedule');
  return res.json();
}
