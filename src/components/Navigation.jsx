export default function Navigation({ activePage, setPage, signalCount }) {
  const tabs = [
    { id: 'dashboard', label: 'Analiz', icon: '◉' },
    { id: 'crosshunter', label: 'Cross Hunter', icon: '⚡', badge: signalCount > 0 },
    { id: 'quantum', label: 'Quantum Tarayıcı', icon: '◈' },
    { id: 'takas', label: 'Takas Analizi', icon: '◎' },
  ];

  return (
    <nav className="nav">
      <div className="nav__logo">
        BIST TERMINAL<span>v2.0</span>
      </div>
      {tabs.map(t => (
        <button
          key={t.id}
          className={`nav__tab ${activePage === t.id ? 'active' : ''}`}
          onClick={() => setPage(t.id)}
        >
          {t.icon} {t.label}
          {t.badge && <span className="badge" />}
        </button>
      ))}
    </nav>
  );
}
