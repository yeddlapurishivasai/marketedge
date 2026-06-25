import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import type { Market, StockScore, Trade, TradeStats, TradeProfile } from '../api';
import { fetchScores, fetchTrades, fetchTradeStats, triggerScanner } from '../api';
import { ChevronLeft, RefreshCw, TrendingUp, Loader2, Gauge, Activity, History } from 'lucide-react';

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
              {rows.map(r => (
                <tr key={r.ticker}>
                  <td style={{ fontWeight: 600 }}>{r.ticker}</td>
                  <td className="cell-center"><ScoreBadge score={scoreOf(r)} /></td>
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
              ))}
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
        setNote('Backfill started — replaying the last 3 days of breakouts. Refresh in a minute.');
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
          <Stat label="Open P&L" value={fmtMoney(stats.openPnLAmount, market)}
            color={(stats.openPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
          <Stat label="Realized P&L" value={fmtMoney(stats.realizedPnLAmount, market)}
            color={(stats.realizedPnLAmount ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
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
          title="Replay the last 3 days of volume-confirmed breakouts into the blotter">
          {backfilling ? <Loader2 size={14} className="spin" /> : <History size={14} />} Backfill 3 days
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
            break of support/resistance. Use “Backfill 3 days” to seed from recent breakouts.
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
