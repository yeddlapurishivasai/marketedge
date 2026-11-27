import type { JobRun, Stage4Result, Stage4Scope } from './api';

export function stage4Qualification(result: Pick<Stage4Result, 'analysisStatus' | 'isStage4'>): boolean | null {
  return result.analysisStatus === 'evaluated' ? result.isStage4 : null;
}

export function weakestFirst(a: Stage4Result, b: Stage4Result): number {
  const score = (value: number | null | undefined) => value != null && Number.isFinite(value) ? value : Infinity;
  const rs = score(a.rsScore) - score(b.rsScore);
  const momentum = score(a.momentumScore) - score(b.momentumScore);
  return (Number.isNaN(rs) ? 0 : rs) || (Number.isNaN(momentum) ? 0 : momentum) || a.symbol.localeCompare(b.symbol);
}

export function stage4RunScope(run: JobRun): Stage4Scope {
  const p = run.parameters ?? {};
  const scope = run.metrics?.scope ?? p.scope;
  if (scope === 'sample' || scope === 'filtered' || scope === 'full') return scope;
  if (p.testSampleOnly === true) return 'sample';
  if (p.limit != null || p.minMarketCap != null || p.maxMarketCap != null ||
    (Array.isArray(p.sectorIds) && p.sectorIds.length > 0)) return 'filtered';
  return 'full';
}

export function validIsoWeek(value: string): boolean {
  const match = /^(\d{4})-W(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]), week = Number(match[2]);
  if (year < 1900 || week < 1 || week > 53) return false;
  const jan1 = new Date(Date.UTC(year, 0, 1)).getUTCDay();
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return week < 53 || jan1 === 4 || (jan1 === 3 && leap);
}
