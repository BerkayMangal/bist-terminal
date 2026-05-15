// ================================================================
// Phase 5.4 — TradingView Ticker Tape widget
// static/js/widgets/tv-ticker.js
//
// Site-wide top banner showing live BIST30 prices.
// Mounted in the header on every page.
// ================================================================

(function () {
  'use strict';

  // Minimal BIST30 set — full list updated via /api/universe at runtime
  // if the host wants more accuracy.
  const DEFAULT_BIST30 = [
    'BIST:THYAO', 'BIST:GARAN', 'BIST:AKBNK', 'BIST:ISCTR', 'BIST:YKBNK',
    'BIST:KCHOL', 'BIST:SAHOL', 'BIST:EREGL', 'BIST:KRDMD', 'BIST:TUPRS',
    'BIST:BIMAS', 'BIST:MGROS', 'BIST:SOKM',  'BIST:ASELS', 'BIST:TCELL',
    'BIST:ARCLK', 'BIST:KOZAA', 'BIST:KOZAL', 'BIST:PETKM', 'BIST:HEKTS',
  ];

  /**
   * Render the ticker tape into a host element.
   * @param {object} opts
   * @param {string} [opts.target] CSS selector (default "#tv-ticker")
   * @param {string[]} [opts.symbols] Custom symbol list (full BIST: prefix)
   */
  window.renderTvTicker = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-ticker';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) {
      console.warn('[tv-ticker] target not found:', targetSel);
      return;
    }

    host.innerHTML = '';

    const symbols = (opts.symbols || DEFAULT_BIST30).map(function (s) {
      return { proName: s, title: s.replace('BIST:', '') };
    });

    const config = {
      symbols: symbols,
      showSymbolLogo: false,
      isTransparent: true,
      displayMode: 'compact',
      colorTheme: opts.theme || 'dark',
      locale: 'tr',
    };

    const container = document.createElement('div');
    container.className = 'tradingview-widget-container';
    const inner = document.createElement('div');
    inner.className = 'tradingview-widget-container__widget';
    container.appendChild(inner);

    const script = document.createElement('script');
    script.type = 'text/javascript';
    script.async = true;
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js';
    script.text = JSON.stringify(config);
    script.onerror = function () {
      host.innerHTML = '<div class="tv-widget-error">Ticker yüklenemedi</div>';
    };
    container.appendChild(script);

    host.appendChild(container);
  };
})();
