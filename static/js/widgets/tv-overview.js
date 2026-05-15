// ================================================================
// Phase 5.4 — TradingView Symbol Overview widget
// static/js/widgets/tv-overview.js
//
// Embeds TradingView's symbol-overview chart into a host element.
// The host element must have id="tv-overview" by default; pass a
// custom selector via the `target` argument to override.
//
// Symbol substitution:
//   When the page is /stock/THYAO, we render BIST:THYAO. We accept
//   the symbol as an argument so the host page is in control —
//   no DOM-scraping or URL-parsing in this module.
//
// Lazy load:
//   The widget script is appended to <head> only on the first call;
//   subsequent calls reuse it (no double-loading).
// ================================================================

(function () {
  'use strict';

  // Track loaded state — single TradingView script across all widgets
  let _scriptLoaded = false;

  function _ensureTradingViewScript() {
    if (_scriptLoaded) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      const existing = document.querySelector('script[src*="tradingview.com"][data-tv]');
      if (existing) {
        _scriptLoaded = true;
        resolve();
        return;
      }
      const s = document.createElement('script');
      s.src = 'https://s3.tradingview.com/tv.js';
      s.async = true;
      s.dataset.tv = '1';
      s.onload = function () { _scriptLoaded = true; resolve(); };
      s.onerror = function () { reject(new Error('TradingView script load failed')); };
      document.head.appendChild(s);
    });
  }

  /**
   * Render a symbol overview chart.
   * @param {object} opts
   * @param {string} opts.symbol  e.g. "THYAO" or "BIST:THYAO"
   * @param {string} [opts.target] CSS selector or id (defaults to "tv-overview")
   * @param {string} [opts.theme] "dark" | "light" (default: "dark")
   * @param {number} [opts.height] container height in px (default: 400)
   */
  window.renderTvOverview = function (opts) {
    opts = opts || {};
    const sym = (opts.symbol || 'XU100').toUpperCase();
    // Add BIST: prefix if missing — keeps callers simple
    const fullSym = sym.indexOf(':') >= 0 ? sym : 'BIST:' + sym.replace('.IS', '');

    const targetSel = opts.target || '#tv-overview';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) {
      console.warn('[tv-overview] target not found:', targetSel);
      return;
    }
    host.innerHTML = '<div class="tv-widget-loading">TradingView yükleniyor…</div>';

    _ensureTradingViewScript()
      .then(function () {
        // Clear loading text and let TradingView render in place
        host.innerHTML = '';
        // The widget API attaches itself to window.TradingView once tv.js
        // finishes downloading.
        if (!window.TradingView || !window.TradingView.widget) {
          host.innerHTML = '<div class="tv-widget-error">TradingView yüklenemedi</div>';
          return;
        }
        new window.TradingView.widget({
          autosize: true,
          symbol: fullSym,
          interval: 'D',
          timezone: 'Europe/Istanbul',
          theme: opts.theme || 'dark',
          style: '1',
          locale: 'tr',
          enable_publishing: false,
          hide_top_toolbar: false,
          hide_legend: false,
          save_image: false,
          container_id: host.id || 'tv-overview',
        });
      })
      .catch(function (err) {
        host.innerHTML = '<div class="tv-widget-error">Grafik yüklenemedi</div>';
        console.warn('[tv-overview]', err);
      });
  };

  // Lazy-load helper: render only when the host enters the viewport
  window.lazyRenderTvOverview = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-overview';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) return;
    if (!('IntersectionObserver' in window)) {
      window.renderTvOverview(opts);
      return;
    }
    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          io.disconnect();
          window.renderTvOverview(opts);
        }
      });
    }, { rootMargin: '120px' });
    io.observe(host);
  };
})();
