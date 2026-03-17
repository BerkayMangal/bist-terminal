import { useState } from 'react';
import { fetchCrossSignals } from '../utils/api';
import { STOCKS, CROSS_TYPES } from '../config/stocks';

export default function CrossHunter({ onSignalCount }) {
  const [signals, setSignals] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('all');
  const [dirFilter, setDirFilter] = useState('all');
  const [error, setError] = useState(null);

  const scan = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchCrossSignals(STOCKS);
      setSignals(d);
      onSignalCount?.(d.signals?.length || 0);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const filtered = signals?.signals?.filter(s => {
    if (filter !== 'all' && s.sinyal_tipi !== filter) return false;
    if (dirFilter !== 'all' && s.yon !== dirFilter) return false;
    return true;
  }) || [];

  const sorted = [...filtered].sort((a, b) => b.guc - a.guc);

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 700, color: 'var(--amber)' }}>
          ⚡ Cross Hunter
        </h2>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {STOCKS.length} hisse · {CROSS_TYPES.length} sinyal tipi
        </span>
        <button className="btn btn-primary" onClick={scan} disabled={loading} style={{ marginLeft: 'auto' }}>
          {loading ? '⟳ Taranıyor...' : '⚡ Taramayı Başlat'}
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
          <div className="loading__text">40 hisse taranıyor... EMA, RSI, MACD kontrol ediliyor</div>
        </div>
      )}

      {!signals && !loading && (
        <div className="empty-state">
          <div className="empty-state__icon">⚡</div>
          <div className="empty-state__text">
            TARAMAYI BAŞLAT butonuna bas.<br/>
            40 hissede 6 farklı çapraz sinyal taranacak.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 16, maxWidth: 500 }}>
            {CROSS_TYPES.map(ct => (
              <div key={ct.id} style={{
                padding: '8px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', fontSize: 10
              }}>
                <div style={{ color: 'var(--amber)', fontWeight: 600, marginBottom: 2 }}>{ct.label}</div>
                <div style={{ color: 'var(--text-muted)' }}>{ct.desc}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {signals && !loading && (
        <>
          {/* Summary */}
          <div className="summary-bar">
            <div className="summary-bar__item">
              <span className="summary-bar__label">Toplam Sinyal</span>
              <span className="summary-bar__value" style={{ color: 'var(--amber)' }}>
                {signals.signals?.length || 0}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">AL Sinyali</span>
              <span className="summary-bar__value" style={{ color: 'var(--green)' }}>
                {signals.ozet?.toplam_al || 0}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">SAT Sinyali</span>
              <span className="summary-bar__value" style={{ color: 'var(--red)' }}>
                {signals.ozet?.toplam_sat || 0}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">En Güçlü</span>
              <span className="summary-bar__value" style={{ color: 'var(--green)', fontSize: 14 }}>
                {signals.ozet?.en_guclu || '—'}
              </span>
            </div>
          </div>

          {/* Filters */}
          <div className="filter-bar">
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginRight: 4 }}>TİP:</span>
            <button className={`filter-chip ${filter === 'all' ? 'active' : ''}`}
              onClick={() => setFilter('all')}>Tümü</button>
            {CROSS_TYPES.map(ct => (
              <button key={ct.id} className={`filter-chip ${filter === ct.id.toUpperCase() ? 'active' : ''}`}
                onClick={() => setFilter(ct.id.toUpperCase())}>{ct.label}</button>
            ))}
            <span style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 8px' }}>|</span>
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginRight: 4 }}>YÖN:</span>
            {['all', 'AL', 'SAT'].map(d => (
              <button key={d} className={`filter-chip ${dirFilter === d ? 'active' : ''}`}
                onClick={() => setDirFilter(d)}>{d === 'all' ? 'Tümü' : d}</button>
            ))}
          </div>

          {/* Signal Table */}
          <div className="panel" style={{ padding: 0, overflow: 'auto', maxHeight: 'calc(100vh - 320px)' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Hisse</th>
                  <th>Sinyal</th>
                  <th>Yön</th>
                  <th>Güç</th>
                  <th>Fiyat</th>
                  <th>Tarih</th>
                  <th>Açıklama</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((s, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 700, color: 'var(--amber)' }}>{s.ticker}</td>
                    <td>
                      <span style={{
                        padding: '2px 6px', borderRadius: 3, fontSize: 10,
                        background: 'var(--blue-dim)', color: 'var(--blue)'
                      }}>
                        {s.sinyal_tipi}
                      </span>
                    </td>
                    <td><span className={`tag ${s.yon === 'AL' ? 'al' : 'sat'}`}>{s.yon}</span></td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div className="score-bar">
                          <div className="score-bar__fill" style={{
                            width: `${s.guc * 10}%`,
                            background: s.guc >= 7 ? 'var(--green)' : s.guc >= 4 ? 'var(--amber)' : 'var(--red)'
                          }} />
                        </div>
                        <span style={{ fontSize: 11, fontWeight: 600 }}>{s.guc}</span>
                      </div>
                    </td>
                    <td>₺{s.fiyat?.toLocaleString('tr-TR')}</td>
                    <td style={{ color: 'var(--text-muted)', fontSize: 11 }}>{s.tarih}</td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 11, maxWidth: 250,
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.aciklama}</td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                      Filtrelerle eşleşen sinyal yok
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
