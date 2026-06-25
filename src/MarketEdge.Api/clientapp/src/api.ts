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

export interface JobRun {
  id: number;
  jobType: string;
  market: string;
  weekNumber: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  parameters?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
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

export async function refreshAnalystData(market: Market, symbol: string): Promise<{ runId: number }> {
  const res = await fetch(`${BASE}/${market}/lookup/${encodeURIComponent(symbol)}/refresh-analyst`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.text()) || 'Failed to refresh analyst data');
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
}

export async function fetchScanners(market: Market): Promise<ScannerInfo[]> {
  const res = await fetch(`${BASE}/${market}/scanners`);
  if (!res.ok) throw new Error('Failed to load scanners');
  return res.json();
}

export async function triggerScanner(market: Market, body: { scannerName?: string | null; universe?: string }): Promise<{ runId: number }> {
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
