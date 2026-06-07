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
}

export interface PagedResult<T> {
  items: T[];
  totalCount: number;
  page: number;
  pageSize: number;
}

export type Market = 'india' | 'us';

const BASE = '/api';

export async function fetchSectors(market: Market): Promise<Sector[]> {
  const res = await fetch(`${BASE}/${market}/sectors`);
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

export async function createStock(market: Market, data: { symbol: string; companyName: string; sectorId: number; broadSector?: string }): Promise<Stock> {
  const res = await fetch(`${BASE}/${market}/stocks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error('Failed to create stock');
  return res.json();
}

export async function updateStock(market: Market, id: number, data: { companyName?: string; sectorId?: number; broadSector?: string }): Promise<void> {
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

// ── Stage 2 Analysis ──

export interface TriggerAnalysisRequest {
  minMarketCap?: number;
  maxMarketCap?: number;
  sectorIds?: number[];
  limit?: number;
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
  rsScore?: number;
  rsRank?: number;
  rsMomentum?: number;
  momentumScore?: number;
  roc12w?: number;
  roc26w?: number;
  roc52w?: number;
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
  avgRSMomentum: number;
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
