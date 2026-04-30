// ================================================================
// Phase 5.4 — TradingView Forex Cross Rates widget
// static/js/widgets/tv-forex.js
//
// Displays USD/TRY, EUR/TRY, GBP/TRY at the bottom of /macro.
// Important context for any BIST analysis.
// ================================================================

(function () {
  'use strict';

  /**
   * Render the forex cross rates widget.
   * @param {object} opts
   * @param {string} [opts.target] CSS selector (default "#tv-forex")
   */
  window.renderTvForex = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-forex';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) {
      console.warn('[tv-forex] target not found:', targetSel);
      return;
    }

    host.innerHTML = '';

    const config = {
      width: '100%',
      height: opts.height || 400,
      currencies: ['EUR', 'USD', 'GBP', 'TRY', 'JPY', 'CHF'],
      isTransparent: false,
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
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-forex-cross-rates.js';
    script.text = JSON.stringify(config);
    script.onerror = function () {
      host.innerHTML = '<div class="tv-widget-error">Döviz tablosu yüklenemedi</div>';
    };
    container.appendChild(script);

    host.appendChild(container);
  };

  window.lazyRenderTvForex = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-forex';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) return;
    if (!('IntersectionObserver' in window)) {
      window.renderTvForex(opts);
      return;
    }
    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          io.disconnect();
          window.renderTvForex(opts);
        }
      });
    }, { rootMargin: '120px' });
    io.observe(host);
  };
})();
