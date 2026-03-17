import { useState } from 'react';
import MarketBar from './components/MarketBar';
import Navigation from './components/Navigation';
import Dashboard from './pages/Dashboard';
import CrossHunter from './pages/CrossHunter';
import QuantumScanner from './pages/QuantumScanner';
import TakasAnalizi from './pages/TakasAnalizi';

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [signalCount, setSignalCount] = useState(0);

  const renderPage = () => {
    switch (page) {
      case 'dashboard':   return <Dashboard />;
      case 'crosshunter': return <CrossHunter onSignalCount={setSignalCount} />;
      case 'quantum':     return <QuantumScanner />;
      case 'takas':       return <TakasAnalizi />;
      default:            return <Dashboard />;
    }
  };

  return (
    <div className="app-container">
      <MarketBar />
      <Navigation activePage={page} setPage={setPage} signalCount={signalCount} />
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  );
}
