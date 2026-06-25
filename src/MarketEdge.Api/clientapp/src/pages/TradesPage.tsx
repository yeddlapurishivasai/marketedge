import { useState, useEffect, useCallback, Fragment } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, StockScore, Trade, TradeStats, TradeProfile } from '../api';
import { fetchScores, fetchTrades, fetchTradeStats, triggerScanner } from '../api';
import { ChevronLeft, ChevronDown, RefreshCw, TrendingUp, Loader2, Gauge, Activity, History } from 'lucide-react';

function fmtPct(v?: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtNum(v?: number | null, dp = 2): string {
  if (v == null) return '—';
  return v.toFixed(dp);
}

function curSym(market: Market): string {
  return market === 'us' ? '$' : '₹';
}

function fmtPrice(v: number | null | undefined, market: Market): string {
  if (v == null) return '—';
  return `${curSym(market)}${v.toFixed(2)}`;
}

function fmtMoney(v: number | null | undefined, market: Market): string {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}${curSym(market)}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtDateTime(v?: string | null): string {
  if (!v) return '—';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function ScoreBadge({ score }: { score?: number | null }) {
  if (score == null) return <span className="cell-muted">—</span>;
  const color = score >= 70 ? 'var(--success)' : score >= 50 ? 'var(--warning, #d08700)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 700, color }}>{score}</span>;
}

function SideBadge({ side }: { side?: string | null }) {
  if (!side || side === 'none') return <span className="cell-muted">—</span>;
  const long = side === 'long';
  return (
    <span className="badge" style={{
      background: long ? 'rgba(22,163,74,0.15)' : 'rgba(220,38,38,0.15)',
      color: long ? 'var(--success)' : 'var(--danger)', fontWeight: 600, textTransform: 'uppercase', fontSize: '0.7rem',
    }}>{side}</span>
  );
}

function PnLCell({ v }: { v?: number | null }) {
  if (v == null) return <span className="cell-muted">—</span>;
  const color = v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 600, color }}>{fmtPct(v)}</span>;
}

function MoneyCell({ v, market }: { v?: number | null; market: Market }) {
  if (v == null) return <span className="cell-muted">—</span>;
  const color = v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-muted)';
  return <span style={{ fontWeight: 600, color }}>{fmtMoney(v, market)}</span>;
}

interface CheckContrib { label: string; group: string; pass: boolean; weight: number; }
interface ProfileComp { bull: number; bear: number; phat: number; n: number; z: number; contribs: CheckContrib[]; }
interface ScoreComponents {
  groups?: Record<string, string>;
  freshness?: number;
  daysSinceEarnings?: number | null;
  scannerHits?: number | null;
  upsideSource?: string | null;
  swing?: ProfileComp;
  positional?: ProfileComp;
}

const GROUP_LABELS: Record<string, string> = {
  tech: 'Technical', fund: 'Fundamental', catalyst: 'Catalyst', est: 'Estimates', track: 'Track record',
};
const UPSIDE_SRC_LABELS: Record<string, string> = {
  forward_eps: 'Analyst forward EPS (next FY vs current FY)',
  earnings_growth_yoy: 'Latest reported earnings growth YoY (forward analyst EPS unavailable)',
};

function ScoreBreakdown({ comp, profile }: { comp: ScoreComponents; profile: TradeProfile }) {
  const p = profile === 'swing' ? comp.swing : comp.positional;
  if (!p) return <div style={{ padding: 12, color: 'var(--text-muted)' }}>No breakdown available.</div>;
  const contribs = p.contribs ?? [];
  const passW = contribs.filter(c => c.pass).reduce((s, c) => s + c.weight, 0);
  const totalW = contribs.reduce((s, c) => s + c.weight, 0);
  return (
    <div style={{ padding: '12px 16px', background: 'var(--bg-subtle, rgba(127,127,127,0.06))', fontSize: '0.85rem' }}>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>How this score was computed</div>
          <div style={{ color: 'var(--text-muted)' }}>
            Wilson lower bound of the weighted pass-fraction. A small evidence base (n)
            widens the confidence interval and pulls the score down — that is why even a
            strong setup with few applicable checks does not reach 100.
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
        <Stat label="Bull score" value={`${p.bull}`} color="var(--success)" />
        <Stat label="Bear score" value={`${p.bear}`} color="var(--danger)" />
        <Stat label="Pass fraction (p̂)" value={`${Math.round((p.phat ?? 0) * 100)}%`} />
        <Stat label="Evidence weight (n)" value={(p.n ?? 0).toFixed(2)} />
        <Stat label="Passed weight" value={`${passW.toFixed(2)} / ${totalW.toFixed(2)}`} />
        <Stat label="z (conservatism)" value={(p.z ?? 0).toFixed(2)} />
        {comp.freshness != null && <Stat label="Fund. freshness" value={comp.freshness.toFixed(2)} />}
      </div>
      <div style={{ color: 'var(--text-muted)', marginBottom: 8 }}>
        <strong>Bull</strong> = confidence that the positive (passing) evidence is real;{' '}
        <strong>Bear</strong> = same math applied to the failing checks (short evidence).
        {comp.upsideSource && (
          <> EPS upside source: {UPSIDE_SRC_LABELS[comp.upsideSource] ?? comp.upsideSource}.</>
        )}
      </div>
      <table className="table" style={{ fontSize: '0.82rem' }}>
        <thead>
          <tr>
            <th>Parameter</th>
            <th style={{ textAlign: 'center' }}>Group</th>
            <th style={{ textAlign: 'center' }}>Result</th>
            <th style={{ textAlign: 'right' }}>Weight</th>
          </tr>
        </thead>
        <tbody>
          {contribs.map((c, i) => (
            <tr key={i}>
              <td>{c.label}</td>
              <td style={{ textAlign: 'center', color: 'var(--text-muted)' }}>{GROUP_LABELS[c.group] ?? c.group}</td>
              <td style={{ textAlign: 'center', color: c.pass ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                {c.pass ? '✓ pass' : '✗ fail'}
              </td>
              <td style={{ textAlign: 'right' }}>{c.weight.toFixed(2)}</td>
            </tr>
          ))}
          {contribs.length === 0 && (
            <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>No applicable checks for this profile.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function parseComponents(json?: string | null): ScoreComponents | null {
  if (!json) return null;
  try { return JSON.parse(json) as ScoreComponents; } catch { return null; }
}

export default function TradesPage() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;
  const navigate = useNavigate();
  const [tab, setTab] = useState<'scores' | 'trades'>('scores');

  return (
    <div className="page">
      <a className="back-link" onClick={() => navigate(`/${m}`)} style={{ cursor: 'pointer' }}>
        <ChevronLeft size={16} /> Back
      </a>
      <div className="page-header">
        <div>
          <h1 className="page-title">Scores &amp; Trades</h1>
          <p className="page-subtitle">
            Wilson lower-bound scoring &amp; the breakout paper-trade blotter &middot; {m === 'india' ? 'Indian' : 'US'} Market
          </p>
        </div>
      </div>

      <div className="toolbar" style={{ gap: 8 }}>
        <button className={`btn ${tab === 'scores' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('scores')}>
          <Gauge size={16} /> Scores
        </button>
        <button className={`btn ${tab === 'trades' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('trades')}>
          <Activity size={16} /> Paper Trades
        </button>
      </div>

      {tab === 'scores' ? <ScoresTab market={m} /> : <TradesTab market={m} />}
    </div>
  );
}

function ScoresTab({ market }: { market: Market }) {
  const [profile, setProfile] = useState<TradeProfile>('swing');
  const [side, setSide] = useState<string>('');
  const [rows, setRows] = useState<StockScore[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchScores(market, { profile, side: side || undefined, take: 200 })
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market, profile, side]);

  useEffect(() => { load(); }, [load]);

  const scoreOf = (r: StockScore) => profile === 'swing' ? r.swingScore : r.positionalScore;
  const sideOf = (r: StockScore) => profile === 'swing' ? r.swingSide : r.positionalSide;
  const bullOf = (r: StockScore) => profile === 'swing' ? r.swingBull : r.positionalBull;
  const bearOf = (r: StockScore) => profile === 'swing' ? r.swingBear : r.positionalBear;

  return (
    <>
      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap' }}>
        <button className={`btn btn-sm ${profile === 'swing' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setProfile('swing')}>
          Swing (technical)
        </button>
        <button className={`btn btn-sm ${profile === 'positional' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setProfile('positional')}>
          Positional (fundamentals 50%)
        </button>
        <span style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 4px' }} />
        <select className="search-input" style={{ width: 'auto' }} value={side} onChange={e => setSide(e.target.value)}>
          <option value="">All sides</option>
          <option value="long">Long</option>
          <option value="short">Short (F&amp;O)</option>
          <option value="none">No signal</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading scores...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><TrendingUp size={48} /></div>
          <p className="empty-state-text">No scores yet. They are computed on each scanner run.</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th style={{ textAlign: 'center' }}>Score</th>
                <th style={{ textAlign: 'center' }}>Side</th>
                <th style={{ textAlign: 'center' }}>Bull / Bear</th>
                <th style={{ textAlign: 'right' }}>EPS upside</th>
                <th style={{ textAlign: 'center' }}>Scanner hits</th>
                <th style={{ textAlign: 'center' }}>Days since earn.</th>
                <th style={{ textAlign: 'center' }}>Freshness</th>
                <th style={{ textAlign: 'center' }}>F&amp;O</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const isOpen = expanded === r.ticker;
                const comp = isOpen ? parseComponents(r.componentsJson) : null;
                return (
                  <Fragment key={r.ticker}>
                    <tr>
                      <td style={{ fontWeight: 600 }}>{r.ticker}</td>
                      <td className="cell-center">
                        <button
                          className="btn btn-ghost btn-sm"
                          style={{ padding: '2px 8px' }}
                          title="Show how this score was computed"
                          onClick={() => setExpanded(isOpen ? null : r.ticker)}
                        >
                          <ScoreBadge score={scoreOf(r)} />
                          <ChevronDown size={12} style={{ marginLeft: 4, transform: isOpen ? 'rotate(180deg)' : undefined, transition: 'transform 0.15s' }} />
                        </button>
                      </td>
                      <td className="cell-center"><SideBadge side={sideOf(r)} /></td>
                      <td className="cell-center" style={{ fontSize: '0.85rem' }}>
                        <span style={{ color: 'var(--success)' }}>{bullOf(r) ?? '—'}</span>
                        {' / '}
                        <span style={{ color: 'var(--danger)' }}>{bearOf(r) ?? '—'}</span>
                      </td>
                      <td className="cell-right">{fmtPct(r.upsideEpsPct)}</td>
                      <td className="cell-center">{r.scannerHits ?? '—'}</td>
                      <td className="cell-center">{r.daysSinceEarnings ?? '—'}</td>
                      <td className="cell-center">{r.fundFreshnessDecay != null ? r.fundFreshnessDecay.toFixed(2) : '—'}</td>
                      <td className="cell-center">{r.isFno ? '✓' : ''}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={9} style={{ padding: 0 }}>
                          {comp ? <ScoreBreakdown comp={comp} profile={profile} />
                            : <div style={{ padding: 12, color: 'var(--text-muted)' }}>No breakdown stored for this score.</div>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function TradesTab({ market }: { market: Market }) {
  const [status, setStatus] = useState<string>('active');
  const [tradeType, setTradeType] = useState<string>('');
  const [rows, setRows] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [note, setNote] = useState<string>('');

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchTrades(market, { status: status || undefined, tradeType: tradeType || undefined }),
      fetchTradeStats(market),
    ])
      .then(([t, s]) => { setRows(t); setStats(s); })
      .catch(() => { setRows([]); setStats(null); })
      .finally(() => setLoading(false));
  }, [market, status, tradeType]);

  useEffect(() => { load(); }, [load]);

  const backfill = useCallback(() => {
    setBackfilling(true);
    setNote('');
    triggerScanner(market, { universe: 'stage2', backfill: true })
      .then(() => {
        setNote('Backfill started — replaying the last 7 days of breakouts. Refresh in a minute.');
      })
      .catch(() => setNote('Failed to start backfill.'))
      .finally(() => setBackfilling(false));
  }, [market]);

  const closed = status === 'closed';

  return (
    <>
      {stats && (
        <div className="toolbar" style={{ gap: 16, flexWrap: 'wrap' }}>
          <Stat label="Active" value={stats.activeCount} />
          <Stat label="Closed" value={stats.closedCount} />
          <Stat label="Wins" value={stats.wins} color="var(--success)" />
          <Stat label="Losses" value={stats.losses} color="var(--danger)" />
          <Stat label="Win rate" value={stats.winRatePct != null ? `${stats.winRatePct}%` : '—'} />
          <Stat label="Swing P&L (open/real)"
            value={`${fmtMoney(stats.swingOpenPnLAmount, market)} / ${fmtMoney(stats.swingRealizedPnLAmount, market)}`}
            color={(stats.swingOpenPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          <Stat label="Positional P&L (open/real)"
            value={`${fmtMoney(stats.positionalOpenPnLAmount, market)} / ${fmtMoney(stats.positionalRealizedPnLAmount, market)}`}
            color={(stats.positionalOpenPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          <Stat label="Total P&L (open/real)"
            value={`${fmtMoney(stats.openPnLAmount, market)} / ${fmtMoney(stats.realizedPnLAmount, market)}`}
            color={(stats.openPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
        </div>
      )}

      <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap' }}>
        <select className="search-input" style={{ width: 'auto' }} value={status} onChange={e => setStatus(e.target.value)}>
          <option value="active">Active</option>
          <option value="closed">Closed</option>
          <option value="">All</option>
        </select>
        <select className="search-input" style={{ width: 'auto' }} value={tradeType} onChange={e => setTradeType(e.target.value)}>
          <option value="">All types</option>
          <option value="swing">Swing</option>
          <option value="positional">Positional</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={backfill} disabled={backfilling} style={{ marginLeft: 'auto' }}
          title="Replay the last 7 days of volume-confirmed breakouts into the blotter">
          {backfilling ? <Loader2 size={14} className="spin" /> : <History size={14} />} Backfill 7 days
        </button>
        <button className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {note && <div className="hint" style={{ marginBottom: 8, color: 'var(--text-muted)' }}>{note}</div>}

      {loading ? (
        <div className="loading"><Loader2 size={18} className="spin" /> Loading trades...</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Activity size={48} /></div>
          <p className="empty-state-text">
            No trades. A scanner hit is only a setup — a paper trade opens on a volume-confirmed
            break of support/resistance. Use “Backfill 7 days” to seed from recent breakouts.
          </p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Type</th>
                <th>Dir</th>
                <th>Entry time</th>
                <th style={{ textAlign: 'right' }}>Qty</th>
                <th style={{ textAlign: 'right' }}>Entry</th>
                <th style={{ textAlign: 'right' }}>Trail</th>
                <th style={{ textAlign: 'right' }}>{closed ? 'Last' : 'Current'}</th>
                <th style={{ textAlign: 'right' }}>Exit</th>
                <th style={{ textAlign: 'right' }}>P&amp;L %</th>
                <th style={{ textAlign: 'right' }}>P&amp;L</th>
                <th style={{ textAlign: 'right' }}>MFE / MAE</th>
                <th style={{ textAlign: 'center' }}>Scanners</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(t => (
                <tr key={t.id}>
                  <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                  <td>{t.tradeType}</td>
                  <td><SideBadge side={t.direction} /></td>
                  <td style={{ fontSize: '0.8rem', whiteSpace: 'nowrap' }}>{fmtDateTime(t.entryAt)}</td>
                  <td className="cell-right">{t.qty ?? '—'}</td>
                  <td className="cell-right">{fmtPrice(t.entryPrice, market)}</td>
                  <td className="cell-right" title={t.stopBasis || ''}>{fmtPrice(t.currentStop, market)}</td>
                  <td className="cell-right">{fmtPrice(t.lastPrice, market)}</td>
                  <td className="cell-right">{t.status === 'closed' ? fmtPrice(t.exitPrice, market) : '—'}</td>
                  <td className="cell-right"><PnLCell v={t.pnLPct} /></td>
                  <td className="cell-right"><MoneyCell v={t.pnLAmount} market={market} /></td>
                  <td className="cell-right" style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: 'var(--success)' }}>{fmtNum(t.mfePct)}</span>
                    {' / '}
                    <span style={{ color: 'var(--danger)' }}>{fmtNum(t.maePct)}</span>
                  </td>
                  <td className="cell-center" title={t.flaggedScanners.join(', ')}>
                    <span className="badge badge-count">{t.scannerHitCount}</span>
                  </td>
                  <td>
                    {t.status === 'closed'
                      ? <span className="cell-muted" style={{ fontSize: '0.8rem' }}>closed · {t.exitReason}</span>
                      : <span style={{ color: 'var(--success)', fontSize: '0.8rem' }}>active{t.movedToBe ? ' · BE+' : ''}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <span style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontSize: '1.1rem', fontWeight: 700, color: color || 'var(--text)' }}>{value}</span>
    </div>
  );
}
