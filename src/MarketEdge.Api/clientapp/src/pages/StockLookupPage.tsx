import { useState, useEffect, useRef, useCallback, useContext } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  createChart, ColorType, CandlestickSeries, HistogramSeries, LineSeries,
  type IChartApi, type ISeriesApi
} from 'lightweight-charts';
import type {
  Market, StockLookupDetail, LookupBar, LookupCandidate, LookupEpsForecast,
  UpsideProjection, UpsideCase
} from '../api';
import {
  searchLookup, fetchLookupDetail, fetchLookupBars, refreshStockData
} from '../api';
import { formatMarketCap, formatPrice, currencySymbol } from '../format';
import { ChevronLeft, Search, RefreshCw, Loader2, ExternalLink, X } from 'lucide-react';
import { ThemeContext } from '../theme';

type Timeframe = 'daily' | 'weekly';
const EMA_PERIODS = [10, 20, 50, 200] as const;
const EMA_COLORS: Record<number, string> = { 10: '#3b82f6', 20: '#f59e0b', 50: '#a855f7', 200: '#ef4444' };

function ema(closes: { time: string; value: number }[], period: number) {
  const k = 2 / (period + 1);
  const out: { time: string; value: number }[] = [];
  let prev: number | undefined;
  closes.forEach((c, i) => {
    prev = prev === undefined ? c.value : c.value * k + prev * (1 - k);
    if (i >= period - 1) out.push({ time: c.time, value: prev });
  });
  return out;
}

function PriceChart({ bars, activeEmas, theme }: { bars: LookupBar[]; activeEmas: number[]; theme: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;
    const dark = theme === 'dark';
    const textColor = dark ? '#cbd5e1' : '#334155';
    const gridColor = dark ? 'rgba(148,163,184,0.12)' : 'rgba(15,23,42,0.06)';

    const chart: IChartApi = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor },
      grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      rightPriceScale: { borderColor: gridColor },
      timeScale: { borderColor: gridColor, timeVisible: false },
      crosshair: { mode: 0 },
    });

    const candle: ISeriesApi<'Candlestick'> = chart.addSeries(CandlestickSeries, {
      upColor: '#16a34a', downColor: '#ef4444', borderVisible: false,
      wickUpColor: '#16a34a', wickDownColor: '#ef4444',
    });
    candle.setData(bars
      .filter(b => b.open != null && b.high != null && b.low != null && b.close != null)
      .map(b => ({ time: b.date, open: b.open!, high: b.high!, low: b.low!, close: b.close! })));

    const volume: ISeriesApi<'Histogram'> = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' }, priceScaleId: '',
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(bars.filter(b => b.volume != null).map(b => ({
      time: b.date, value: b.volume!,
      color: (b.close ?? 0) >= (b.open ?? 0) ? 'rgba(22,163,74,0.45)' : 'rgba(239,68,68,0.45)',
    })));

    const closes = bars.filter(b => b.close != null).map(b => ({ time: b.date, value: b.close! }));
    for (const p of activeEmas) {
      const line = chart.addSeries(LineSeries, { color: EMA_COLORS[p], lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      line.setData(ema(closes, p));
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, activeEmas, theme]);

  return <div ref={containerRef} className="lookup-chart" />;
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone?: 'up' | 'down' }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone ? `metric-${tone}` : ''}`}>{value}</div>
    </div>
  );
}

function Prop({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="prop-row">
      <span className="prop-label">{label}</span>
      <span className="prop-value">{value ?? '—'}</span>
    </div>
  );
}

function fmtDate(d?: string | null): string {
  if (!d) return '—';
  const dt = new Date(d);
  return `${dt.getMonth() + 1}/${dt.getDate()}/${String(dt.getFullYear()).slice(-2)}`;
}

function EpsTable({ rows, market, periodLabel }: { rows: LookupEpsForecast[]; market: Market; periodLabel: string }) {
  const sym = currencySymbol(market);
  if (rows.length === 0) return <p className="muted-note">No estimates available.</p>;
  // Constant-P/E upside: price scales with EPS, so each period's implied price move vs the
  // current (nearest) period is (eps / currentEps - 1) * 100. Only meaningful when the
  // current-period consensus is positive.
  const base = rows[0]?.consensusEps;
  const hasBase = base != null && base > 0;
  return (
    <table className="table eps-table">
      <thead>
        <tr><th>Period</th><th>Consensus EPS</th><th>Upside @ const P/E</th><th>High</th><th>Low</th><th>Estimates</th><th>Revisions</th></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const up = hasBase && r.consensusEps != null ? (r.consensusEps / (base as number) - 1) * 100 : null;
          return (
            <tr key={i}>
              <td>{fmtPeriod(r.periodEndDate)}</td>
              <td>{r.consensusEps != null ? `${sym}${r.consensusEps.toFixed(2)}` : '—'}</td>
              <td>
                {i === 0
                  ? <span className="muted-note">current</span>
                  : up != null
                    ? <span className={up >= 0 ? 'rev-up' : 'rev-down'}>{up >= 0 ? '+' : ''}{up.toFixed(1)}%</span>
                    : '—'}
              </td>
              <td>{r.highEps != null ? `${sym}${r.highEps.toFixed(2)}` : '—'}</td>
              <td>{r.lowEps != null ? `${sym}${r.lowEps.toFixed(2)}` : '—'}</td>
              <td>{r.numEstimates ?? '—'}</td>
              <td>
                <span className="rev-up">▲ {r.revisionsUp}</span>
                <span className="rev-down">▼ {r.revisionsDown}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
      <caption className="muted-note" style={{ captionSide: 'bottom', textAlign: 'left', paddingTop: 4 }}>
        Upside assumes the current P/E is held constant — implied price moves in line with
        consensus EPS vs the current {periodLabel}.
      </caption>
    </table>
  );
}

function fmtPeriod(d: string): string {
  const dt = new Date(d);
  return dt.toLocaleString('en-US', { month: 'short', year: 'numeric' });
}

// Best/base/worst EPS upside, computed server-side at constant P/E. Each case shows the
// implied % price move and the implied stock price for the Low / Consensus / High estimate.
function UpsideCases({ year, quarter, market }: { year?: UpsideProjection | null; quarter?: UpsideProjection | null; market: Market }) {
  const sym = currencySymbol(market);
  const has = (p?: UpsideProjection | null): p is UpsideProjection => !!p && !!(p.bear || p.base || p.bull);
  if (!has(year) && !has(quarter)) return null;

  const caseCell = (c?: UpsideCase | null) => {
    if (!c || c.upsidePct == null) return <span className="muted-note">—</span>;
    const up = c.upsidePct;
    return (
      <div className="upside-case">
        <span className={`upside-value-sm ${up >= 0 ? 'rev-up' : 'rev-down'}`}>{up >= 0 ? '+' : ''}{up.toFixed(1)}%</span>
        {c.impliedPrice != null && <span className="upside-price">{sym}{c.impliedPrice.toFixed(2)}</span>}
      </div>
    );
  };

  const row = (label: string, p?: UpsideProjection | null) => has(p) ? (
    <tr>
      <td style={{ fontWeight: 600 }}>
        {label}{p.source === 'ai' && <span className="pill" style={{ marginLeft: 6 }}>AI</span>}
      </td>
      <td className="cell-center">{caseCell(p.bear)}</td>
      <td className="cell-center">{caseCell(p.base)}</td>
      <td className="cell-center">{caseCell(p.bull)}</td>
    </tr>
  ) : null;

  return (
    <div className="upside-callout">
      <span className="upside-title">Potential upside @ constant P/E</span>
      <table className="table" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>Horizon</th>
            <th style={{ textAlign: 'center' }}>Bear · low EPS</th>
            <th style={{ textAlign: 'center' }}>Base · consensus</th>
            <th style={{ textAlign: 'center' }}>Bull · high EPS</th>
          </tr>
        </thead>
        <tbody>
          {row('Per quarter', quarter)}
          {row('Per year', year)}
        </tbody>
      </table>
      <p className="muted-note">
        Holds the current P/E and moves price with the analyst Low / Consensus / High EPS estimate —
        each cell shows the implied % move and price.
      </p>
    </div>
  );
}

/**
 * Self-contained stock detail view: loads the symbol's detail + bars and renders the
 * chart, metric cards, properties and analyst/EPS tables. Reused by the full Stock Lookup
 * page and by the StockLookupModal (so clicking a row never disturbs the list scroll).
 */
export function StockDetailView({ market, symbol }: { market: Market; symbol: string }) {
  const { theme } = useContext(ThemeContext);

  const [detail, setDetail] = useState<StockLookupDetail | null>(null);
  const [bars, setBars] = useState<LookupBar[]>([]);
  const [timeframe, setTimeframe] = useState<Timeframe>('daily');
  const [activeEmas, setActiveEmas] = useState<number[]>([20]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBars = useCallback(async (tf: Timeframe) => {
    try { setBars(await fetchLookupBars(market, symbol, tf)); } catch { setBars([]); }
  }, [market, symbol]);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true); setError(null); setDetail(null); setBars([]);
      try {
        const d = await fetchLookupDetail(market, symbol);
        if (!active) return;
        setDetail(d);
        await loadBars(timeframe);
      } catch (e) {
        if (!active) return;
        setDetail(null); setBars([]);
        setError(e instanceof Error ? e.message : 'Lookup failed');
      }
      if (active) setLoading(false);
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, symbol]);

  // Reload bars when the timeframe changes for the loaded symbol.
  useEffect(() => {
    if (detail) loadBars(timeframe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe]);

  const toggleEma = (p: number) =>
    setActiveEmas(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p].sort((a, b) => a - b));

  const refresh = async () => {
    if (!detail) return;
    setRefreshing(true);
    try {
      await refreshStockData(market, detail.symbol);
      // Re-ingest (bars + technical + fundamentals) and rescore run on the worker; poll the
      // symbol detail a few times so the view picks up the refreshed data.
      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 2500));
        const d = await fetchLookupDetail(market, detail.symbol);
        setDetail(d);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Refresh failed');
    }
    setRefreshing(false);
  };

  const t = detail?.technical;
  const a = detail?.analyst;
  const dayTone = t?.dayPct == null ? undefined : t.dayPct >= 0 ? 'up' : 'down';
  const tvSymbol = detail ? `${detail.exchange ?? ''}:${detail.symbol}` : '';

  if (loading && !detail) {
    return <div className="loading"><div className="spinner" /> Loading {symbol}…</div>;
  }
  if (error) return <div className="lookup-error">{error}</div>;
  if (!detail) return null;

  return (
    <div className="lookup-body">
      {/* Header card */}
      <div className="card lookup-id-card">
        <div className="lookup-id-top">
          <span className="lookup-market-tag">{detail.market.toUpperCase()}</span>
          {detail.exchange && (
            <a
              className="tv-link"
              href={`https://www.tradingview.com/symbols/${encodeURIComponent(tvSymbol)}/`}
              target="_blank" rel="noreferrer"
            >
              TradingView <ExternalLink size={12} />
            </a>
          )}
          {t?.asOfDate && <span className="as-of">As Of {fmtDate(t.asOfDate)}</span>}
        </div>
        <h2 className="lookup-symbol">
          {detail.symbol} <span className="lookup-company">{detail.companyName}</span>
        </h2>
        <div className="lookup-sector">
          {detail.broadSector && <span>{detail.broadSector}</span>}
          {detail.industry && <span> · {detail.industry}</span>}
        </div>

        <div className="analyst-refresh">
          <div>
            <div className="section-title" style={{ margin: 0 }}>Refresh &amp; Rescore</div>
            <p className="muted-note">Re-ingest bars, technical and fundamentals for this symbol, then recompute its score. Runs as a worker job.</p>
          </div>
          <button className="btn btn-primary" onClick={refresh} disabled={refreshing}>
            {refreshing ? <Loader2 size={16} className="spin-icon" /> : <RefreshCw size={16} />} {refreshing ? 'Refreshing…' : 'Refresh & Rescore'}
          </button>
        </div>
      </div>

      {/* Chart */}
      <div className="card lookup-chart-card">
        <div className="chart-toolbar">
          <div className="seg-toggle">
            <button className={timeframe === 'daily' ? 'on' : ''} onClick={() => setTimeframe('daily')}>DAILY</button>
            <button className={timeframe === 'weekly' ? 'on' : ''} onClick={() => setTimeframe('weekly')}>WEEKLY</button>
          </div>
          <div className="ema-toggle">
            {EMA_PERIODS.map(p => (
              <button
                key={p}
                className={activeEmas.includes(p) ? 'on' : ''}
                style={activeEmas.includes(p) ? { borderColor: EMA_COLORS[p], color: EMA_COLORS[p] } : undefined}
                onClick={() => toggleEma(p)}
              >EMA {p}</button>
            ))}
          </div>
        </div>
        {bars.length === 0
          ? <div className="empty-state"><p className="empty-state-text">No price bars for this symbol.</p></div>
          : <PriceChart bars={bars} activeEmas={activeEmas} theme={theme} />}
      </div>

      {/* Metric cards */}
      <div className="metric-grid">
        <Metric label="CLOSE" value={t?.close != null ? formatPrice(t.close, market) : '—'} />
        <Metric label="DAY %" value={t?.dayPct != null ? `${t.dayPct.toFixed(2)}%` : '—'} tone={dayTone} />
        <Metric label="RS" value={t?.rs ?? '—'} />
        <Metric label="RS 1D" value={t?.rs1d ?? '—'} />
        <Metric label="RS 1W" value={t?.rs1w ?? '—'} />
        <Metric label="RS 1M" value={t?.rs1m ?? '—'} />
        <Metric label="RS 3M" value={t?.rs3m ?? '—'} />
        <Metric label="RS 6M" value={t?.rs6m ?? '—'} />
      </div>
      <div className="metric-grid">
        <Metric label="CONSENSUS RATING" value={a?.consensusRating ?? '—'} />
        <Metric label="CURRENT QUARTER EPS" value={a?.currentQuarterEps != null ? a.currentQuarterEps.toFixed(2) : '—'} />
        <Metric label="NEXT QUARTER EPS" value={a?.nextQuarterEps != null ? a.nextQuarterEps.toFixed(2) : '—'} />
        <Metric label="CURRENT YEAR EPS" value={a?.currentYearEps != null ? a.currentYearEps.toFixed(2) : '—'} />
        <Metric label="NEXT YEAR EPS" value={a?.nextYearEps != null ? a.nextYearEps.toFixed(2) : '—'} />
      </div>

      {/* Properties */}
      <div className="card prop-grid">
        <Prop label="Exchange" value={detail.exchange} />
        <Prop label="Market Cap" value={t?.marketCap != null ? formatMarketCap(t.marketCap, market) : '—'} />
        <Prop label="52W High" value={t?.high52w != null ? formatPrice(t.high52w, market) : '—'} />
        <Prop label="From 52W High" value={t?.from52wHigh != null ? `${t.from52wHigh.toFixed(2)}%` : '—'} />
        <Prop label="Open" value={t?.open != null ? formatPrice(t.open, market) : '—'} />
        <Prop label="High" value={t?.high != null ? formatPrice(t.high, market) : '—'} />
        <Prop label="Low" value={t?.low != null ? formatPrice(t.low, market) : '—'} />
        <Prop label="Options" value={detail.isFno ? 'Yes' : 'No'} />
        <Prop label="Active" value={detail.active ? 'Yes' : 'No'} />
        <Prop label="RS Type" value={t?.rsType} />
        <Prop label="RS Date" value={t?.rsDate ? fmtDate(t.rsDate) : '—'} />
        <Prop label="Bars Available" value={detail.barsAvailable ?? '—'} />
        <Prop label="Scanner Hits" value={t?.scannerHits ?? '—'} />
        <Prop label="Last Scanner Hit" value={t?.lastScannerHit ? fmtDate(t.lastScannerHit) : '—'} />
      </div>

      {/* Analyst snapshot */}
      {a && (
        <div className="lookup-section">
          <h2 className="section-title">Analyst Snapshot</h2>
          {a.asOfDate && <span className="pill">As Of {fmtDate(a.asOfDate)}</span>}
          <table className="table">
            <thead>
              <tr><th>Consensus</th><th>Current Quarter EPS</th><th>Next Quarter EPS</th><th>Current Year EPS</th><th>Next Year EPS</th></tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 700 }}>{a.consensusRating ?? '—'}</td>
                <td>{a.currentQuarterEps?.toFixed(2) ?? '—'}</td>
                <td>{a.nextQuarterEps?.toFixed(2) ?? '—'}</td>
                <td>{a.currentYearEps?.toFixed(2) ?? '—'}</td>
                <td>{a.nextYearEps?.toFixed(2) ?? '—'}</td>
              </tr>
            </tbody>
          </table>
          <UpsideCases year={detail.yearUpside} quarter={detail.quarterUpside} market={market} />
          {a.numAnalysts != null && <p className="muted-note">Based on {a.numAnalysts} analysts offering recommendations for '{detail.symbol}'.</p>}
        </div>
      )}

      <div className="lookup-section">
        <h2 className="section-title">Quarterly EPS Forecasts <span className="pill">{detail.quarterlyEps.length} rows</span></h2>
        <EpsTable rows={detail.quarterlyEps} market={market} periodLabel="quarter" />
      </div>
      <div className="lookup-section">
        <h2 className="section-title">Yearly EPS Forecasts <span className="pill">{detail.yearlyEps.length} rows</span></h2>
        <EpsTable rows={detail.yearlyEps} market={market} periodLabel="year" />
      </div>
    </div>
  );
}

/**
 * Stock detail shown as a modal/popup overlay. Opening it from the Stocks list keeps the
 * list mounted underneath, so scroll position and search state are preserved.
 */
export function StockLookupModal({ market, symbol, onClose }: { market: Market; symbol: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal lookup-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">{symbol} Lookup</h3>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="modal-body">
          <StockDetailView market={market} symbol={symbol} />
        </div>
      </div>
    </div>
  );
}

export default function StockLookupPage() {
  const { market: routeMarket, symbol: routeSymbol } = useParams<{ market: string; symbol?: string }>();
  const navigate = useNavigate();

  const [market, setMarket] = useState<Market>((routeMarket as Market) || 'us');
  const [query, setQuery] = useState(routeSymbol ? decodeURIComponent(routeSymbol) : '');
  const [suggestions, setSuggestions] = useState<LookupCandidate[]>([]);

  useEffect(() => {
    if (routeMarket) setMarket(routeMarket as Market);
  }, [routeMarket]);

  // Lightweight autocomplete.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 1 || q === routeSymbol) { setSuggestions([]); return; }
    const id = setTimeout(async () => {
      try { setSuggestions(await searchLookup(market, q)); } catch { /* ignore */ }
    }, 220);
    return () => clearTimeout(id);
  }, [query, market, routeSymbol]);

  const go = (sym: string) => {
    const s = sym.trim();
    if (s) { setSuggestions([]); navigate(`/${market}/lookup/${encodeURIComponent(s)}`); }
  };

  return (
    <div className="page">
      <div className="lookup-header-bar">
        <div>
          <button className="back-link" onClick={() => navigate(`/${market}/stocks`)}>
            <ChevronLeft size={16} /> Stocks
          </button>
          <h1 className="page-title" style={{ marginBottom: 2 }}>Stock Lookup</h1>
          <p className="page-subtitle">Search by symbol or company name, then inspect the chart and stock properties.</p>
        </div>
        <form className="lookup-search" onSubmit={e => { e.preventDefault(); go(query); }}>
          <div className="lookup-search-input">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Symbol or company"
              autoFocus
            />
            {suggestions.length > 0 && (
              <ul className="lookup-suggestions">
                {suggestions.map(s => (
                  <li key={s.symbol} onMouseDown={() => go(s.symbol)}>
                    <strong>{s.symbol}</strong> <span>{s.companyName}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <select value={market} onChange={e => setMarket(e.target.value as Market)}>
            <option value="us">US</option>
            <option value="india">India</option>
          </select>
          <button type="submit" className="btn btn-primary">
            <Search size={16} /> Search
          </button>
        </form>
      </div>

      {routeSymbol
        ? <StockDetailView market={market} symbol={decodeURIComponent(routeSymbol)} />
        : (
          <div className="empty-state" style={{ marginTop: 40 }}>
            <div className="empty-state-icon">🔎</div>
            <p className="empty-state-text">Search a symbol to begin.</p>
          </div>
        )}
    </div>
  );
}
