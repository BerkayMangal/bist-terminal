import { useState } from 'react';
import { fetchStockAnalysis } from '../utils/api';

function ScoreCircle({ score, max, size = 48, color }) {
  const pct = (score / max) * 100;
  const c = color || (pct >= 70 ? 'var(--green)' : pct >= 40 ? 'var(--amber)' : 'var(--red)');
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;
  return (
    <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--border)" strokeWidth="3" />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={c} strokeWidth="3"
        strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" />
      <text x={size/2} y={size/2} textAnchor="middle" dominantBaseline="central"
        fill={c} fontSize="12" fontWeight="700" fontFamily="var(--font-display)"
        style={{ transform: 'rotate(90deg)', transformOrigin: 'center' }}>
        {score}
      </text>
    </svg>
  );
}

function Tag({ text, type }) {
  const cls = text?.includes('AL') ? 'al' : text?.includes('SAT') ? 'sat' : 'notr';
  return <span className={`tag ${type || cls}`}>{text}</span>;
}

function Panel({ title, children, badge }) {
  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__title">{title}</span>
        {badge && <Tag text={badge} />}
      </div>
      {children}
    </div>
  );
}

export default function Dashboard() {
  const [ticker, setTicker] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const analyze = async () => {
    if (!ticker.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchStockAnalysis(ticker.toUpperCase().trim());
      setData(d);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const handleKey = (e) => { if (e.key === 'Enter') analyze(); };

  return (
    <div>
      {/* Search */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <div className="search-box" style={{ flex: 1, maxWidth: 400 }}>
          <span style={{ color: 'var(--amber)', fontSize: 14 }}>⌕</span>
          <input
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={handleKey}
            placeholder="Hisse kodu gir — GARAN, THYAO, AKBNK..."
            autoFocus
          />
        </div>
        <button className="btn btn-primary" onClick={analyze} disabled={loading}>
          {loading ? '⟳ Analiz...' : '◉ Analiz Et'}
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
          <div className="loading__text">Claude {ticker} analiz ediyor...</div>
        </div>
      )}

      {!data && !loading && (
        <div className="empty-state">
          <div className="empty-state__icon">◉</div>
          <div className="empty-state__text">
            Hisse kodu girip ANALİZ ET butonuna bas.<br/>
            8 modül birden çalışacak.
          </div>
        </div>
      )}

      {data && !loading && (
        <>
          {/* Header */}
          <div className="summary-bar">
            <div className="summary-bar__item">
              <span className="summary-bar__label">Hisse</span>
              <span className="summary-bar__value" style={{ color: 'var(--amber)' }}>
                {data.ticker}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Şirket</span>
              <span className="summary-bar__value" style={{ fontSize: 14 }}>
                {data.sirket_adi}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Fiyat</span>
              <span className="summary-bar__value">
                ₺{data.guncel_fiyat?.toLocaleString('tr-TR')}
              </span>
            </div>
            <div className="summary-bar__item">
              <span className="summary-bar__label">Değişim</span>
              <span className="summary-bar__value" style={{
                color: data.degisim_pct >= 0 ? 'var(--green)' : 'var(--red)'
              }}>
                {data.degisim_pct >= 0 ? '+' : ''}{data.degisim_pct}%
              </span>
            </div>
            {data.karar && (
              <div className="summary-bar__item" style={{ marginLeft: 'auto' }}>
                <span className="summary-bar__label">Karar</span>
                <Tag text={data.karar.tavsiye} />
              </div>
            )}
          </div>

          {/* 8 Module Grid */}
          <div className="dashboard-grid">
            {/* Piotroski */}
            {data.piotroski && (
              <Panel title="Piotroski F-Score" badge={`${data.piotroski.skor}/9`}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 10 }}>
                  <ScoreCircle score={data.piotroski.skor} max={9} />
                  <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                    {data.piotroski.yorum}
                  </p>
                </div>
                {data.piotroski.detaylar?.slice(0, 5).map((d, i) => (
                  <div key={i} style={{ display: 'flex', gap: 6, fontSize: 10, padding: '2px 0',
                    color: d.sonuc ? 'var(--green)' : 'var(--red)' }}>
                    <span>{d.sonuc ? '✓' : '✗'}</span>
                    <span style={{ color: 'var(--text-secondary)' }}>{d.kriter}</span>
                  </div>
                ))}
              </Panel>
            )}

            {/* Altman */}
            {data.altman && (
              <Panel title="Altman Z-Score" badge={data.altman.risk_seviyesi}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8 }}>
                  <div style={{
                    fontSize: 28, fontWeight: 800, fontFamily: 'var(--font-display)',
                    color: data.altman.z_skor > 2.99 ? 'var(--green)' :
                           data.altman.z_skor > 1.81 ? 'var(--amber)' : 'var(--red)'
                  }}>
                    {data.altman.z_skor?.toFixed(2)}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                    {'>'} 2.99 Güvenli<br/>
                    1.81–2.99 Gri Bölge<br/>
                    {'<'} 1.81 Tehlike
                  </div>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                  {data.altman.yorum}
                </p>
              </Panel>
            )}

            {/* Graham */}
            {data.graham && (
              <Panel title="Graham Değer Analizi">
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                  <div>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>İÇ DEĞER</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--green)',
                      fontFamily: 'var(--font-display)' }}>
                      ₺{data.graham.ic_deger?.toLocaleString('tr-TR')}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>GÜNCEL</div>
                    <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'var(--font-display)' }}>
                      ₺{data.graham.guncel_fiyat?.toLocaleString('tr-TR')}
                    </div>
                  </div>
                </div>
                <div style={{
                  padding: '6px 10px', borderRadius: 4, fontSize: 12, fontWeight: 600, textAlign: 'center',
                  background: data.graham.marj_guvenlik_pct > 0 ? 'var(--green-dim)' : 'var(--red-dim)',
                  color: data.graham.marj_guvenlik_pct > 0 ? 'var(--green)' : 'var(--red)',
                  marginBottom: 8
                }}>
                  Güvenlik Marjı: {data.graham.marj_guvenlik_pct > 0 ? '+' : ''}
                  {data.graham.marj_guvenlik_pct}%
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                  {data.graham.yorum}
                </p>
              </Panel>
            )}

            {/* Teknik */}
            {data.teknik && (
              <Panel title="Teknik Analiz" badge={data.teknik.macd_sinyal}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
                  {[
                    { label: 'RSI', value: data.teknik.rsi, color: data.teknik.rsi > 70 ? 'var(--red)' : data.teknik.rsi < 30 ? 'var(--green)' : 'var(--amber)' },
                    { label: 'Trend', value: data.teknik.trend },
                    { label: 'Destek', value: `₺${data.teknik.destek?.toLocaleString('tr-TR')}` },
                    { label: 'Direnç', value: `₺${data.teknik.direnc?.toLocaleString('tr-TR')}` },
                  ].map(({ label, value, color }) => (
                    <div key={label} style={{ padding: '6px 8px', background: 'var(--bg-secondary)', borderRadius: 4 }}>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>{label}</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: color || 'var(--text-primary)' }}>{value}</div>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 6, fontSize: 10, color: 'var(--text-muted)' }}>
                  <span>EMA20: {data.teknik.ema20}</span>
                  <span>|</span>
                  <span>EMA50: {data.teknik.ema50}</span>
                  <span>|</span>
                  <span>EMA200: {data.teknik.ema200}</span>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5, marginTop: 8 }}>
                  {data.teknik.yorum}
                </p>
              </Panel>
            )}

            {/* Haber & Sentiment */}
            {data.haber && (
              <Panel title="Haber & Sentiment" badge={data.haber.sentiment}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                  <div style={{
                    fontSize: 24, fontWeight: 800, fontFamily: 'var(--font-display)',
                    color: data.haber.skor > 60 ? 'var(--green)' : data.haber.skor > 40 ? 'var(--amber)' : 'var(--red)'
                  }}>
                    {data.haber.skor}/100
                  </div>
                  <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Sentiment Skoru</span>
                </div>
                {data.haber.son_haberler?.slice(0, 3).map((h, i) => (
                  <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--border)', fontSize: 11 }}>
                    <span style={{
                      color: h.etki === 'Pozitif' ? 'var(--green)' : h.etki === 'Negatif' ? 'var(--red)' : 'var(--text-muted)',
                      marginRight: 6
                    }}>
                      {h.etki === 'Pozitif' ? '▲' : h.etki === 'Negatif' ? '▼' : '●'}
                    </span>
                    <span style={{ color: 'var(--text-secondary)' }}>{h.baslik}</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 6, fontSize: 9 }}>{h.kaynak}</span>
                  </div>
                ))}
              </Panel>
            )}

            {/* VİOP */}
            {data.viop && (
              <Panel title="VİOP Korelasyon">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
                  <div style={{ padding: '6px 8px', background: 'var(--bg-secondary)', borderRadius: 4 }}>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>AÇIK POZİSYON</div>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                      {data.viop.acik_pozisyon?.toLocaleString('tr-TR')}
                    </div>
                  </div>
                  <div style={{ padding: '6px 8px', background: 'var(--bg-secondary)', borderRadius: 4 }}>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>BAZ FARKI</div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--amber)' }}>
                      {data.viop.baz_farki}%
                    </div>
                  </div>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                  {data.viop.yorum}
                </p>
              </Panel>
            )}

            {/* Rakipler */}
            {data.rakipler && (
              <Panel title="Rakip Karşılaştırma">
                <table className="data-table" style={{ fontSize: 11 }}>
                  <thead>
                    <tr>
                      <th>Hisse</th><th>F/K</th><th>PD/DD</th><th>YTD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rakipler.liste?.map((r, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 600, color: 'var(--amber)' }}>{r.ticker}</td>
                        <td>{r.fk?.toFixed(1)}</td>
                        <td>{r.pd_dd?.toFixed(2)}</td>
                        <td style={{ color: r.getiri_ytd >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {r.getiri_ytd >= 0 ? '+' : ''}{r.getiri_ytd}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5, marginTop: 8 }}>
                  {data.rakipler.konumlama}
                </p>
              </Panel>
            )}

            {/* DCF */}
            {data.dcf && (
              <Panel title="DCF / Hedef Fiyat" badge={data.dcf.tavsiye}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <div>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>HEDEF FİYAT</div>
                    <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--blue)',
                      fontFamily: 'var(--font-display)' }}>
                      ₺{data.dcf.hedef_fiyat?.toLocaleString('tr-TR')}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>POTANSİYEL</div>
                    <div style={{
                      fontSize: 24, fontWeight: 800, fontFamily: 'var(--font-display)',
                      color: data.dcf.potansiyel_getiri_pct > 0 ? 'var(--green)' : 'var(--red)'
                    }}>
                      {data.dcf.potansiyel_getiri_pct > 0 ? '+' : ''}{data.dcf.potansiyel_getiri_pct}%
                    </div>
                  </div>
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.5 }}>
                  {data.dcf.varsayimlar}
                </p>
              </Panel>
            )}
          </div>

          {/* Genel Karar */}
          {data.karar && (
            <div style={{
              marginTop: 16, padding: '16px 20px',
              background: 'var(--bg-panel)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              borderLeft: `3px solid ${data.karar.tavsiye?.includes('AL') ? 'var(--green)' :
                data.karar.tavsiye?.includes('SAT') ? 'var(--red)' : 'var(--amber)'}`
            }}>
              <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{ fontSize: 9, letterSpacing: 2, color: 'var(--text-muted)', marginBottom: 6 }}>
                    GENEL DEĞERLENDİRME
                  </div>
                  <p style={{ color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.7 }}>
                    {data.karar.ozet}
                  </p>
                </div>
                <div style={{ minWidth: 180 }}>
                  <div style={{ fontSize: 11, color: 'var(--green)', marginBottom: 4 }}>
                    ▲ FIRSAT: {data.karar.kritik_firsat}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--red)' }}>
                    ▼ RİSK: {data.karar.kritik_risk}
                  </div>
                </div>
              </div>
            </div>
          )}

          <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 9,
            marginTop: 16, letterSpacing: 2 }}>
            AI ANALİZ · YATIRIM TAVSİYESİ DEĞİLDİR
          </div>
        </>
      )}
    </div>
  );
}
