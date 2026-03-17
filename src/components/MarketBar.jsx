import { useState, useEffect } from 'react';
import { fetchMarketData } from '../utils/api';

export default function MarketBar() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const d = await fetchMarketData();
      setData(d);
    } catch (e) {
      console.error('MarketBar error:', e);
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 5 * 60 * 1000); // 5 min
    return () => clearInterval(interval);
  }, []);

  const items = [
    { key: 'xu030', label: 'XU030' },
    { key: 'usdtry', label: 'USD/TRY' },
    { key: 'eurtry', label: 'EUR/TRY' },
    { key: 'brent', label: 'BRENT' },
    { key: 'gold', label: 'ALTIN' },
    { key: 'xbank', label: 'XBANK' },
  ];

  return (
    <div className="market-bar">
      {items.map(({ key, label }) => {
        const d = data?.[key];
        const pct = d?.change_pct ?? 0;
        return (
          <div className="market-bar__item" key={key}>
            <span className="market-bar__label">{label}</span>
            <span className="market-bar__price">
              {d ? d.price.toLocaleString('tr-TR', { maximumFractionDigits: 2 }) : '—'}
            </span>
            <span className={`market-bar__change ${pct >= 0 ? 'up' : 'down'}`}>
              {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
            </span>
          </div>
        );
      })}
      <span className="market-bar__time">
        {loading ? '⟳' : data?.timestamp || '—'}
        <button onClick={load} style={{
          background: 'none', border: 'none', color: 'var(--text-muted)',
          cursor: 'pointer', marginLeft: 8, fontFamily: 'var(--font-mono)', fontSize: 10
        }}>↻</button>
      </span>
    </div>
  );
}
