import { useState, useMemo } from 'react';
import { fetchQuantumScan } from '../utils/api';
import { STOCKS, SECTORS } from '../config/stocks';

const SCORE_COLS = [
  { key: 'kangal_score', label: 'KANGAL', primary: true },
  { key: 'value_score', label: 'Değer' },
  { key: 'momentum_score', label: 'Momentum' },
  { key: 'teknik_score', label: 'Teknik' },
  { key: 'temel_score', label: 'Temel' },
  { key: 'flow_score', label: 'Akış' },
];

function ScoreCell({ value }) {
  const c = value >= 70 ? 'var(--green)' : value >= 40 ? 'var(--amber)' : 'var(--red)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div className="score-bar" style={{ width: 40 }}>
        <div className="score-bar__fill" style={{ width: `${value}%`, background: c }} />
      </div>
      <span style={{ fontSize: 11, fontWeight: 600, color: c, minWidth: 24 }}>{value}</span>
    </div>
  );
}

function RejimTag({ rejim }) {
  const cls = rejim === 'TREND' ? 'trend' : rejim === 'BREAKOUT' ? 'breakout' :
              rejim === 'VOLATILE' ? 'volatile' : 'range';
  return <span className={`tag ${cls}`}>{rejim}</span>;
}

export default function QuantumScanner() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState('kangal_score');
  const [sortDir, setSortDir] = useState('desc');
  const [sectorFilter, setSectorFilter] = useState('all');
  const [signalFilter, setSignalFilter] = useState('all');
  const [rejimFilter, setRejimFilter] = useState('all');
  const [minScore, setMinScore] = useState(0);

  const scan = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchQuantumScan(STOCKS);
      setData(d);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const processed = useMemo(() => {
    if (!data?.stocks) return [];
    let list = [...data.stocks];

    // Merge sector info from config
    list = list.map(s => ({
      ...s,
      sektor: s.sektor || STOCKS.find(st => st.ticker === s.ticker)?.sector || '—'
    }));

    // Filters
    if (sectorFilter !== 'all') list = list.filter(s => s.sektor === sectorFilter);
    if (signalFilter !== 'all') list = list.filter(s => s.sinyal === signalFilter);
    if (rejimFilter !== 'all') list = list.filter(s => s.rejim === rejimFilter);
    if (minScore > 0) list = list.filter(s => s.kangal_score >= minScore);

    // Sort
    list.sort((a, b) => {
      const av = a[sortKey] ?? 0;
      const bv = b[sortKey] ?? 0;
      return sortDir === 'desc' ? bv - av : av - bv;
    });

    return list;
  }, [data, sortKey, sortDir, sectorFilter, signalFilter, rejimFilter, minScore]);

  const stats = useMemo(() => {
    if (!data?.stocks) return {};
    const stocks = data.stocks;
    return {
      avgKangal: Math.round(stocks.reduce((s, x) => s + (x.kangal_score || 0), 0) / stocks.length),
      alCount: stocks.filter(s => s.sinyal === 'AL').length,
      satCount: stocks.filter(s => s.sinyal === 'SAT').length,
      breakoutCount: stocks.filter(s => s.rejim === 'BREAKOUT').length,
    };
  }, [data]);

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 700, color: 'var(--purple)' }}>
          ◈ Quantum Tarayıcı
        </h2>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {STOCKS.length} hisse · 6 skor boyutu · KANGAL modülleri
        </span>
        <button className="btn btn-primary" onClick={scan} disabled={loading} style={{ marginLeft: 'auto' }}>
          {loading ? '⟳ Taranıyor...' : '◈ Taramayı Başlat'}
        </button>
      </div>

      {error && (
        <div style={{ padding: 12, background: 'var(--red-dim)', border: '1px solid var(--red)',
          borderRadius: 'var(--radius)', color: 'var(--red)', fontSize: 12, marginBottom: 16 }}>
          Hata: {error}
        </div>
      )}

      {loading && (
        <div className="loading">
          <div className="loading__spinner" />
          <div className="loading__text">40 hisse quantum taramasından geçiriliyor...</div>
        </div>
      )}

      {!data && !loading && (
        <div className="empty-state">
          <div className="empty-state__icon">◈</div>
          <div className="empty-state__text">
            TARAMAYI BAŞLAT butonuna bas.<br/>
            40 hisseye 6 boyutlu quantum skor atanacak.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8,
            marginTop: 16, maxWidth: 500 }}>
            {SCORE_COLS.map(c => (
              <div key={c.key} style={{
                padding: '8px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', fontSize: 11, textAlign: 'center',
                color: c.primary ? 'var(--amber)' : 'var(--text-secondary)',
                fontWeight: c.primary ? 600 : 400
              }}>
                {c.label}
              </div>
            ))}
          </div>
        </div>
      )}

      {data && !loading && (
        <>
          {/* Summary */}
          <div className="summary-bar">
            <div className="summary-bar__item">
              <span className="summary-bar__label">Piyasa Rejimi</span>
              <span className="summary-bar__value" style={{ fontSize: 14 }}>
                <RejimTag rejim={data.market_rejim || 'RANGE'} />
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Ort. KANGAL</span>
              <span className="summary-bar__value" style={{
                color: stats.avgKangal >= 60 ? 'var(--green)' : 'var(--amber)'
              }}>{stats.avgKangal}</span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">AL Sinyal</span>
              <span className="summary-bar__value" style={{ color: 'var(--green)' }}>{stats.alCount}</span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">SAT Sinyal</span>
              <span className="summary-bar__value" style={{ color: 'var(--red)' }}>{stats.satCount}</span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Breakout</span>
              <span className="summary-bar__value" style={{ color: 'var(--blue)' }}>{stats.breakoutCount}</span>
            </div>
          </div>

          {/* Filters */}
          <div className="filter-bar">
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>SEKTÖR:</span>
            <button className={`filter-chip ${sectorFilter === 'all' ? 'active' : ''}`}
              onClick={() => setSectorFilter('all')}>Tümü</button>
            {SECTORS.map(s => (
              <button key={s} className={`filter-chip ${sectorFilter === s ? 'active' : ''}`}
                onClick={() => setSectorFilter(s)}>{s}</button>
            ))}
          </div>
          <div className="filter-bar" style={{ paddingTop: 0 }}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>SİNYAL:</span>
            {['all', 'AL', 'SAT', 'NÖTR'].map(s => (
              <button key={s} className={`filter-chip ${signalFilter === s ? 'active' : ''}`}
                onClick={() => setSignalFilter(s)}>{s === 'all' ? 'Tümü' : s}</button>
            ))}
            <span style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 4px' }}>|</span>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>REJİM:</span>
            {['all', 'TREND', 'BREAKOUT', 'RANGE', 'VOLATILE'].map(r => (
              <button key={r} className={`filter-chip ${rejimFilter === r ? 'active' : ''}`}
                onClick={() => setRejimFilter(r)}>{r === 'all' ? 'Tümü' : r}</button>
            ))}
            <span style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 4px' }}>|</span>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>MIN KANGAL:</span>
            {[0, 50, 60, 70, 80].map(v => (
              <button key={v} className={`filter-chip ${minScore === v ? 'active' : ''}`}
                onClick={() => setMinScore(v)}>{v === 0 ? 'Tümü' : `>${v}`}</button>
            ))}
          </div>

          {/* Count */}
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
            {processed.length} / {data.stocks?.length} hisse gösteriliyor
          </div>

          {/* Table */}
          <div className="panel" style={{ padding: 0, overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th onClick={() => handleSort('ticker')}
                    className={sortKey === 'ticker' ? 'sorted' : ''}>Hisse ▾</th>
                  <th>Sektör</th>
                  <th onClick={() => handleSort('fiyat')}
                    className={sortKey === 'fiyat' ? 'sorted' : ''}>Fiyat</th>
                  <th onClick={() => handleSort('degisim_pct')}
                    className={sortKey === 'degisim_pct' ? 'sorted' : ''}>Δ%</th>
                  {SCORE_COLS.map(c => (
                    <th key={c.key} onClick={() => handleSort(c.key)}
                      className={sortKey === c.key ? 'sorted' : ''}
                      style={c.primary ? { color: 'var(--amber)' } : {}}>
                      {c.label}
                    </th>
                  ))}
                  <th>Rejim</th>
                  <th>Sinyal</th>
                </tr>
              </thead>
              <tbody>
                {processed.map((s, i) => (
                  <tr key={s.ticker}>
                    <td style={{ fontWeight: 700, color: 'var(--amber)' }}>{s.ticker}</td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 10 }}>{s.sektor}</td>
                    <td>₺{s.fiyat?.toLocaleString('tr-TR')}</td>
                    <td style={{ color: s.degisim_pct >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                      {s.degisim_pct >= 0 ? '+' : ''}{s.degisim_pct?.toFixed(2)}%
                    </td>
                    {SCORE_COLS.map(c => (
                      <td key={c.key}><ScoreCell value={s[c.key] || 0} /></td>
                    ))}
                    <td><RejimTag rejim={s.rejim} /></td>
                    <td>
                      <span className={`tag ${s.sinyal === 'AL' ? 'al' : s.sinyal === 'SAT' ? 'sat' : 'notr'}`}>
                        {s.sinyal}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
