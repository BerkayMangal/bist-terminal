import { useState } from 'react';
import { fetchTakasAnalysis } from '../utils/api';

function BrokerBar({ kurum, net_lot, yon, yorum, maxLot }) {
  const pct = Math.min(Math.abs(net_lot) / maxLot * 100, 100);
  const isAlici = yon === 'ALICI';
  return (
    <div className="broker-row">
      <div style={{ width: 140, fontWeight: 600, fontSize: 12, color: 'var(--text-primary)' }}>
        {kurum}
      </div>
      <div style={{ width: 80, textAlign: 'right', fontWeight: 600, fontSize: 12,
        color: isAlici ? 'var(--green)' : 'var(--red)' }}>
        {isAlici ? '+' : '-'}{Math.abs(net_lot).toLocaleString('tr-TR')}
      </div>
      <div className="broker-row__bar">
        <div className="broker-row__fill" style={{
          width: `${pct}%`,
          background: isAlici ? 'var(--green)' : 'var(--red)',
        }} />
      </div>
      <span className={`tag ${isAlici ? 'al' : 'sat'}`} style={{ minWidth: 50, textAlign: 'center' }}>
        {yon}
      </span>
      <div style={{ flex: 1, fontSize: 10, color: 'var(--text-muted)', minWidth: 120 }}>
        {yorum}
      </div>
    </div>
  );
}

export default function TakasAnalizi() {
  const [ticker, setTicker] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const analyze = async () => {
    if (!ticker.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchTakasAnalysis(ticker.toUpperCase().trim());
      setData(d);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const handleKey = (e) => { if (e.key === 'Enter') analyze(); };

  const maxLot = data?.araci_kurumlar
    ? Math.max(...data.araci_kurumlar.map(k => Math.abs(k.net_lot)), 1)
    : 1;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 700, color: 'var(--blue)' }}>
          ◎ Takas Analizi
        </h2>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          KAP Aracı Kurum Verileri · Kim Mal Topluyor?
        </span>
      </div>

      {/* Search */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <div className="search-box" style={{ flex: 1, maxWidth: 400 }}>
          <span style={{ color: 'var(--blue)', fontSize: 14 }}>◎</span>
          <input
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={handleKey}
            placeholder="Hisse kodu gir — GARAN, THYAO..."
          />
        </div>
        <button className="btn btn-primary" onClick={analyze} disabled={loading}>
          {loading ? '⟳ Analiz...' : '◎ Takas Analizi'}
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
          <div className="loading__text">{ticker} takas verileri analiz ediliyor...</div>
        </div>
      )}

      {!data && !loading && (
        <div className="empty-state">
          <div className="empty-state__icon">◎</div>
          <div className="empty-state__text">
            Hisse kodu girip TAKAS ANALİZİ butonuna bas.<br/>
            Aracı kurum bazlı alım/satım verilerini göreceksin.
          </div>
        </div>
      )}

      {data && !loading && (
        <>
          {/* Summary */}
          <div className="summary-bar">
            <div className="summary-bar__item">
              <span className="summary-bar__label">Hisse</span>
              <span className="summary-bar__value" style={{ color: 'var(--amber)' }}>{data.ticker}</span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Yabancı Oranı</span>
              <span className="summary-bar__value">%{data.yabanci_oran_pct}</span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Yabancı Trend</span>
              <span className="summary-bar__value" style={{
                color: data.yabanci_trend === 'Artış' ? 'var(--green)' :
                       data.yabanci_trend === 'Azalış' ? 'var(--red)' : 'var(--amber)',
                fontSize: 14
              }}>
                {data.yabanci_trend === 'Artış' ? '▲' : data.yabanci_trend === 'Azalış' ? '▼' : '●'}{' '}
                {data.yabanci_trend}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Net Hacim</span>
              <span className="summary-bar__value" style={{
                color: data.net_hacim_mn_tl >= 0 ? 'var(--green)' : 'var(--red)'
              }}>
                {data.net_hacim_mn_tl >= 0 ? '+' : ''}{data.net_hacim_mn_tl} Mn TL
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Kurumsal İlgi</span>
              <span className="summary-bar__value" style={{ fontSize: 14 }}>
                <span className={`tag ${data.analiz?.kurumsal_ilgi === 'Yüksek' ? 'al' :
                  data.analiz?.kurumsal_ilgi === 'Düşük' ? 'sat' : 'notr'}`}>
                  {data.analiz?.kurumsal_ilgi}
                </span>
              </span>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
            {/* Broker List */}
            <div className="panel" style={{ padding: 0 }}>
              <div style={{
                padding: '12px 16px', borderBottom: '1px solid var(--border)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center'
              }}>
                <span className="panel__title">Aracı Kurum Net İşlemler</span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                  {data.araci_kurumlar?.length || 0} kurum
                </span>
              </div>
              {data.araci_kurumlar?.map((k, i) => (
                <BrokerBar key={i} {...k} maxLot={maxLot} />
              ))}
            </div>

            {/* Right side: Analysis + Block trades */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Kim Topluyor */}
              <div className="panel" style={{
                borderLeft: '3px solid var(--green)',
                background: 'linear-gradient(135deg, var(--bg-panel), #0a1a0a)'
              }}>
                <div className="panel__title" style={{ marginBottom: 8 }}>KİM MAL TOPLUYOR?</div>
                <p style={{ color: 'var(--green)', fontSize: 13, fontWeight: 600, lineHeight: 1.6, marginBottom: 8 }}>
                  {data.analiz?.kim_topluyor}
                </p>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                  {data.analiz?.yorum}
                </p>
              </div>

              {/* Blok İşlemler */}
              {data.blok_islemler?.length > 0 && (
                <div className="panel">
                  <div className="panel__title" style={{ marginBottom: 8 }}>BLOK İŞLEMLER</div>
                  {data.blok_islemler.map((b, i) => (
                    <div key={i} style={{
                      padding: '6px 0', borderBottom: '1px solid var(--border)',
                      fontSize: 11
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--amber)', fontWeight: 600 }}>
                          {b.lot?.toLocaleString('tr-TR')} lot
                        </span>
                        <span style={{ color: 'var(--text-muted)' }}>₺{b.fiyat}</span>
                        <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>{b.tarih}</span>
                      </div>
                      <div style={{ color: 'var(--text-secondary)', fontSize: 10, marginTop: 2 }}>
                        {b.aciklama}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
