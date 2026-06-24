import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useParams, useNavigate } from 'react-router-dom';
import type { Market, Sector, Stock } from './api';
import { fetchSectors, fetchStocks, createSector, renameSector, deleteSector, deleteStock, moveStocks } from './api';
import {
  Sun, Moon, ChevronLeft, Search, Plus, Pencil, Trash2,
  ArrowRightLeft, X, TrendingUp, LayoutGrid, BarChart3,
  IndianRupee, DollarSign, ChevronRight, Activity, Target
} from 'lucide-react';
import JobsPage from './pages/JobsPage';
import AnalysisPage from './pages/AnalysisPage';
import './styles.css';

// Theme context
const ThemeContext = createContext<{ theme: string; toggle: () => void }>({ theme: 'light', toggle: () => {} });

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState(() => localStorage.getItem('me-theme') || 'light');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('me-theme', theme);
  }, [theme]);
  const toggle = () => setTheme(t => t === 'light' ? 'dark' : 'light');
  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>;
}

// Modal component
function Modal({ open, title, onClose, children, footer }: {
  open: boolean; title: string; onClose: () => void;
  children: React.ReactNode; footer?: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">{title}</h3>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// Toast
function Toast({ message, type, onDone }: { message: string; type: 'success' | 'error'; onDone: () => void }) {
  useEffect(() => { const t = setTimeout(onDone, 2500); return () => clearTimeout(t); }, [onDone]);
  return <div className={`toast toast-${type}`}>{message}</div>;
}

function useToast() {
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const show = (message: string, type: 'success' | 'error' = 'success') => setToast({ message, type });
  const el = toast ? <Toast message={toast.message} type={toast.type} onDone={() => setToast(null)} /> : null;
  return { show, el };
}

// Nav bar
function NavBar() {
  const { theme, toggle } = useContext(ThemeContext);
  return (
    <header className="nav-header">
      <div className="nav-content">
        <NavLink to="/" className="nav-brand">
          <div className="nav-brand-icon"><TrendingUp size={18} /></div>
          <span className="nav-brand-text">MarketEdge</span>
        </NavLink>
        <button className="btn btn-ghost" onClick={toggle} title="Toggle theme">
          {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
        </button>
      </div>
    </header>
  );
}

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <div className="app-container">
          <NavBar />
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/:market" element={<MarketMenu />} />
            <Route path="/:market/sectors" element={<SectorsPage />} />
            <Route path="/:market/sectors/:sectorId" element={<SectorDetail />} />
            <Route path="/:market/stocks" element={<StocksPage />} />
            <Route path="/:market/analysis" element={<AnalysisPage />} />
            <Route path="/:market/jobs" element={<JobsPage />} />
          </Routes>
        </div>
      </BrowserRouter>
    </ThemeProvider>
  );
}

function Home() {
  const navigate = useNavigate();
  return (
    <div className="home-hero">
      <div className="home-logo"><TrendingUp size={32} /></div>
      <h1 className="home-title">MarketEdge</h1>
      <p className="home-desc">Manage sectors and stocks across markets</p>
      <div className="market-cards">
        <div className="market-card" onClick={() => navigate('/india')}>
          <span className="market-card-flag"><IndianRupee size={40} strokeWidth={1.5} /></span>
          <div className="market-card-label">Indian Market</div>
          <div className="market-card-count">NSE Listed</div>
        </div>
        <div className="market-card" onClick={() => navigate('/us')}>
          <span className="market-card-flag"><DollarSign size={40} strokeWidth={1.5} /></span>
          <div className="market-card-label">US Market</div>
          <div className="market-card-count">NASDAQ &middot; NYSE &middot; AMEX</div>
        </div>
      </div>
    </div>
  );
}

function MarketMenu() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;
  const navigate = useNavigate();
  const label = m === 'india' ? 'Indian Market' : 'US Market';
  const Icon = m === 'india' ? IndianRupee : DollarSign;

  return (
    <div className="home-hero">
      <NavLink to="/" className="back-link" style={{ position: 'absolute', top: 80, left: 24 }}>
        <ChevronLeft size={16} /> Home
      </NavLink>
      <Icon size={36} strokeWidth={1.5} style={{ color: 'var(--primary)', marginBottom: 12 }} />
      <h1 className="page-title" style={{ marginBottom: 32 }}>{label}</h1>
      <div className="menu-grid">
        <div className="menu-card" onClick={() => navigate(`/${m}/sectors`)}>
          <div className="menu-card-icon sectors"><LayoutGrid size={22} /></div>
          <div className="menu-card-text">
            <h3>Sectors</h3>
            <p>View and manage industry sectors</p>
          </div>
          <ChevronRight size={16} style={{ color: 'var(--text-muted)', marginLeft: 'auto' }} />
        </div>
        <div className="menu-card" onClick={() => navigate(`/${m}/stocks`)}>
          <div className="menu-card-icon stocks"><BarChart3 size={22} /></div>
          <div className="menu-card-text">
            <h3>Stocks</h3>
            <p>Search, move, and manage stocks</p>
          </div>
          <ChevronRight size={16} style={{ color: 'var(--text-muted)', marginLeft: 'auto' }} />
        </div>
        <div className="menu-card" onClick={() => navigate(`/${m}/analysis`)}>
          <div className="menu-card-icon analysis"><Target size={22} /></div>
          <div className="menu-card-text">
            <h3>Stage 2 Analysis</h3>
            <p>RS, momentum &amp; sector rotation</p>
          </div>
          <ChevronRight size={16} style={{ color: 'var(--text-muted)', marginLeft: 'auto' }} />
        </div>
        <div className="menu-card" onClick={() => navigate(`/${m}/jobs`)}>
          <div className="menu-card-icon jobs"><Activity size={22} /></div>
          <div className="menu-card-text">
            <h3>Job Runs</h3>
            <p>Monitor analysis job runs</p>
          </div>
          <ChevronRight size={16} style={{ color: 'var(--text-muted)', marginLeft: 'auto' }} />
        </div>
      </div>
    </div>
  );
}

function SectorsPage() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState('');
  const [renaming, setRenaming] = useState<Sector | null>(null);
  const [renameVal, setRenameVal] = useState('');
  const { show, el: toastEl } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setSectors(await fetchSectors(m));
    setLoading(false);
  }, [m]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createSector(m, newName.trim());
    setNewName('');
    setShowAdd(false);
    show('Sector created');
    load();
  };

  const handleDelete = async (s: Sector) => {
    if (!confirm(`Delete "${s.sectorName}"? It must have no stocks.`)) return;
    try { await deleteSector(m, s.id); show('Sector deleted'); load(); }
    catch { show('Cannot delete — sector has stocks', 'error'); }
  };

  const handleRename = async () => {
    if (!renaming || !renameVal.trim()) return;
    await renameSector(m, renaming.id, renameVal.trim());
    setRenaming(null);
    show('Sector renamed');
    load();
  };

  const filtered = sectors.filter(s => s.sectorName.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="page">
      <NavLink to={`/${m}`} className="back-link"><ChevronLeft size={16} /> Back</NavLink>
      <div className="page-header">
        <div>
          <h1 className="page-title">Sectors</h1>
          <p className="page-subtitle">{sectors.length} sectors &middot; {m === 'india' ? 'Indian' : 'US'} Market</p>
        </div>
        <button className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setShowAdd(true)}>
          <Plus size={16} /> Add Sector
        </button>
      </div>

      <div className="toolbar">
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="search-input" style={{ paddingLeft: 36 }}
            placeholder="Search sectors..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" /> Loading sectors...</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><LayoutGrid size={48} /></div>
          <p className="empty-state-text">No sectors found</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Sector Name</th>
                <th style={{ width: 100, textAlign: 'center' }}>Stocks</th>
                <th style={{ width: 100, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.id}>
                  <td>
                    <NavLink className="sector-link" to={`/${m}/sectors/${s.id}`}>
                      {s.sectorName}
                    </NavLink>
                  </td>
                  <td className="cell-center"><span className="badge badge-count">{s.stockCount}</span></td>
                  <td className="cell-actions">
                    <button className="btn btn-ghost btn-sm" onClick={() => { setRenaming(s); setRenameVal(s.sectorName); }}>
                      <Pencil size={14} />
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => handleDelete(s)} style={{ color: 'var(--danger)' }}>
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Sector Modal */}
      <Modal open={showAdd} title="Add New Sector" onClose={() => setShowAdd(false)}
        footer={<><button className="btn btn-outline" onClick={() => setShowAdd(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={handleCreate}>Create</button></>}>
        <div className="form-group">
          <label className="form-label">Sector Name</label>
          <input className="form-input" value={newName} onChange={e => setNewName(e.target.value)}
            placeholder="e.g. Information Technology" autoFocus
            onKeyDown={e => e.key === 'Enter' && handleCreate()} />
        </div>
      </Modal>

      {/* Rename Modal */}
      <Modal open={!!renaming} title="Rename Sector" onClose={() => setRenaming(null)}
        footer={<><button className="btn btn-outline" onClick={() => setRenaming(null)}>Cancel</button>
          <button className="btn btn-primary" onClick={handleRename}>Save</button></>}>
        <div className="form-group">
          <label className="form-label">Sector Name</label>
          <input className="form-input" value={renameVal} onChange={e => setRenameVal(e.target.value)}
            autoFocus onKeyDown={e => e.key === 'Enter' && handleRename()} />
        </div>
      </Modal>

      {toastEl}
    </div>
  );
}

function SectorDetail() {
  const { market, sectorId } = useParams<{ market: string; sectorId: string }>();
  const m = market as Market;
  const sid = Number(sectorId);

  const [stocks, setStocks] = useState<Stock[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [sectorName, setSectorName] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showMove, setShowMove] = useState(false);
  const [moveTo, setMoveTo] = useState<number | ''>('');
  const [loading, setLoading] = useState(true);
  const { show, el: toastEl } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    const [stockRes, sectorList] = await Promise.all([
      fetchStocks(m, { sectorId: sid, q: search || undefined, page, pageSize: 50 }),
      fetchSectors(m)
    ]);
    setStocks(stockRes.items);
    setTotalCount(stockRes.totalCount);
    setSectors(sectorList);
    const current = sectorList.find(s => s.id === sid);
    if (current) setSectorName(current.sectorName);
    setLoading(false);
  }, [m, sid, search, page]);

  useEffect(() => { load(); }, [load]);

  const handleDeleteStock = async (st: Stock) => {
    if (!confirm(`Delete "${st.symbol}"?`)) return;
    await deleteStock(m, st.id);
    show('Stock deleted');
    load();
  };

  const handleMove = async () => {
    if (!moveTo || selected.size === 0) return;
    await moveStocks(m, [...selected], Number(moveTo));
    const targetName = sectors.find(s => s.id === Number(moveTo))?.sectorName;
    show(`Moved ${selected.size} stock(s) to ${targetName}`);
    setSelected(new Set());
    setMoveTo('');
    setShowMove(false);
    load();
  };

  const toggleSelect = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const toggleAll = () => {
    if (selected.size === stocks.length) setSelected(new Set());
    else setSelected(new Set(stocks.map(s => s.id)));
  };

  const totalPages = Math.ceil(totalCount / 50);

  return (
    <div className="page">
      <NavLink to={`/${m}/sectors`} className="back-link"><ChevronLeft size={16} /> Sectors</NavLink>
      <div className="page-header">
        <div>
          <h1 className="page-title">{sectorName}</h1>
          <p className="page-subtitle">{totalCount} stocks</p>
        </div>
        {selected.size > 0 && (
          <button className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setShowMove(true)}>
            <ArrowRightLeft size={16} /> Move {selected.size} stock{selected.size > 1 ? 's' : ''}
          </button>
        )}
      </div>

      <div className="toolbar">
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="search-input" style={{ paddingLeft: 36 }}
            placeholder="Search stocks..." value={search}
            onChange={e => { setSearch(e.target.value); setPage(1); }} />
        </div>
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" /> Loading...</div>
      ) : stocks.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><BarChart3 size={48} /></div>
          <p className="empty-state-text">No stocks in this sector</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input type="checkbox" className="checkbox"
                    checked={selected.size === stocks.length && stocks.length > 0}
                    onChange={toggleAll} />
                </th>
                <th>Symbol</th>
                <th>Company Name</th>
                <th style={{ width: 80, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {stocks.map(st => (
                <tr key={st.id}>
                  <td><input type="checkbox" className="checkbox" checked={selected.has(st.id)} onChange={() => toggleSelect(st.id)} /></td>
                  <td className="cell-symbol">{st.symbol}</td>
                  <td>{st.companyName}</td>
                  <td className="cell-actions">
                    <button className="btn btn-ghost btn-sm" onClick={() => { setSelected(new Set([st.id])); setShowMove(true); }}>
                      <ArrowRightLeft size={14} />
                    </button>
                    <button className="btn btn-ghost btn-sm" style={{ color: 'var(--danger)' }} onClick={() => handleDeleteStock(st)}>
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="pagination">
          <button className="pagination-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
          <span className="pagination-info">Page {page} of {totalPages}</span>
          <button className="pagination-btn" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}

      {/* Move Modal */}
      <Modal open={showMove} title="Move Stocks to Sector" onClose={() => setShowMove(false)}
        footer={<><button className="btn btn-outline" onClick={() => setShowMove(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={!moveTo} onClick={handleMove}>Move</button></>}>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
          Moving <strong>{selected.size}</strong> stock{selected.size > 1 ? 's' : ''} from <strong>{sectorName}</strong>
        </p>
        <div className="form-group">
          <label className="form-label">Target Sector</label>
          <select className="form-select" value={moveTo} onChange={e => setMoveTo(Number(e.target.value) || '')}>
            <option value="">Select sector...</option>
            {sectors.filter(s => s.id !== sid).map(s => (
              <option key={s.id} value={s.id}>{s.sectorName} ({s.stockCount})</option>
            ))}
          </select>
        </div>
      </Modal>

      {toastEl}
    </div>
  );
}

function StocksPage() {
  const { market } = useParams<{ market: string }>();
  const m = market as Market;

  const [stocks, setStocks] = useState<Stock[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [search, setSearch] = useState('');
  const [sectorFilter, setSectorFilter] = useState<number | ''>('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showMove, setShowMove] = useState(false);
  const [moveTo, setMoveTo] = useState<number | ''>('');
  const [loading, setLoading] = useState(true);
  const { show, el: toastEl } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    const [stockRes, sectorList] = await Promise.all([
      fetchStocks(m, { q: search || undefined, sectorId: sectorFilter || undefined, page, pageSize: 50 }),
      fetchSectors(m)
    ]);
    setStocks(stockRes.items);
    setTotalCount(stockRes.totalCount);
    setSectors(sectorList);
    setLoading(false);
  }, [m, search, sectorFilter, page]);

  useEffect(() => { load(); }, [load]);

  const handleDeleteStock = async (st: Stock) => {
    if (!confirm(`Delete "${st.symbol}"?`)) return;
    await deleteStock(m, st.id);
    show('Stock deleted');
    load();
  };

  const handleMove = async () => {
    if (!moveTo || selected.size === 0) return;
    await moveStocks(m, [...selected], Number(moveTo));
    const targetName = sectors.find(s => s.id === Number(moveTo))?.sectorName;
    show(`Moved ${selected.size} stock(s) to ${targetName}`);
    setSelected(new Set());
    setMoveTo('');
    setShowMove(false);
    load();
  };

  const toggleSelect = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const toggleAll = () => {
    if (selected.size === stocks.length) setSelected(new Set());
    else setSelected(new Set(stocks.map(s => s.id)));
  };

  const totalPages = Math.ceil(totalCount / 50);

  return (
    <div className="page">
      <NavLink to={`/${m}`} className="back-link"><ChevronLeft size={16} /> Back</NavLink>
      <div className="page-header">
        <div>
          <h1 className="page-title">Stocks</h1>
          <p className="page-subtitle">{totalCount} stocks &middot; {m === 'india' ? 'Indian' : 'US'} Market</p>
        </div>
        {selected.size > 0 && (
          <button className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setShowMove(true)}>
            <ArrowRightLeft size={16} /> Move {selected.size} stock{selected.size > 1 ? 's' : ''}
          </button>
        )}
      </div>

      <div className="toolbar">
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="search-input" style={{ paddingLeft: 36 }}
            placeholder="Search by symbol or company..." value={search}
            onChange={e => { setSearch(e.target.value); setPage(1); }} />
        </div>
        <select className="select-input" value={sectorFilter}
          onChange={e => { setSectorFilter(Number(e.target.value) || ''); setPage(1); }}>
          <option value="">All Sectors</option>
          {sectors.map(s => (
            <option key={s.id} value={s.id}>{s.sectorName} ({s.stockCount})</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" /> Loading...</div>
      ) : stocks.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><BarChart3 size={48} /></div>
          <p className="empty-state-text">No stocks found</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input type="checkbox" className="checkbox"
                    checked={selected.size === stocks.length && stocks.length > 0}
                    onChange={toggleAll} />
                </th>
                <th>Symbol</th>
                <th>Company Name</th>
                <th>Sector</th>
                <th style={{ width: 80, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {stocks.map(st => (
                <tr key={st.id}>
                  <td><input type="checkbox" className="checkbox" checked={selected.has(st.id)} onChange={() => toggleSelect(st.id)} /></td>
                  <td className="cell-symbol">{st.symbol}</td>
                  <td>{st.companyName}</td>
                  <td className="cell-muted">{st.sectorName || '-'}</td>
                  <td className="cell-actions">
                    <button className="btn btn-ghost btn-sm" onClick={() => { setSelected(new Set([st.id])); setShowMove(true); }}>
                      <ArrowRightLeft size={14} />
                    </button>
                    <button className="btn btn-ghost btn-sm" style={{ color: 'var(--danger)' }} onClick={() => handleDeleteStock(st)}>
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="pagination">
          <button className="pagination-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
          <span className="pagination-info">Page {page} of {totalPages}</span>
          <button className="pagination-btn" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}

      {/* Move Modal */}
      <Modal open={showMove} title="Move Stocks to Sector" onClose={() => setShowMove(false)}
        footer={<><button className="btn btn-outline" onClick={() => setShowMove(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={!moveTo} onClick={handleMove}>Move</button></>}>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
          Moving <strong>{selected.size}</strong> stock{selected.size > 1 ? 's' : ''} to a new sector
        </p>
        <div className="form-group">
          <label className="form-label">Target Sector</label>
          <select className="form-select" value={moveTo} onChange={e => setMoveTo(Number(e.target.value) || '')}>
            <option value="">Select sector...</option>
            {sectors.map(s => (
              <option key={s.id} value={s.id}>{s.sectorName} ({s.stockCount})</option>
            ))}
          </select>
        </div>
      </Modal>

      {toastEl}
    </div>
  );
}

export default App;
