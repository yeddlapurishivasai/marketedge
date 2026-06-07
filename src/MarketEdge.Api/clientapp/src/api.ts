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
