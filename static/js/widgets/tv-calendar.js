// ================================================================
// Phase 5.4 — TradingView Economic Calendar widget
// static/js/widgets/tv-calendar.js
//
// Embeds TradingView's economic-events calendar.
// Used as the centerpiece of /macro page.
// ================================================================

(function () {
  'use strict';

  /**
   * Render the economic calendar widget into a host element.
   * @param {object} opts
   * @param {string} [opts.target] CSS selector or id (default "#tv-calendar")
   * @param {string[]} [opts.countryFilter] ISO codes — defaults to TR/US/EU
   */
  window.renderTvCalendar = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-calendar';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) {
      console.warn('[tv-calendar] target not found:', targetSel);
      return;
    }

    host.innerHTML = '<div class="tv-widget-loading">Takvim yükleniyor…</div>';

    // Embed via TradingView's widget API. We use the embed-widget URL
    // because the calendar is a separate widget (not part of tv.js).
    const config = {
      colorTheme: opts.theme || 'dark',
      isTransparent: false,
      width: '100%',
      height: opts.height || 500,
      locale: 'tr',
      importanceFilter: '-1,0,1',
      countryFilter: (opts.countryFilter || ['tr', 'us', 'eu', 'gb']).join(','),
    };

    // Build the TradingView container the widget API expects
    const container = document.createElement('div');
    container.className = 'tradingview-widget-container';
    const inner = document.createElement('div');
    inner.className = 'tradingview-widget-container__widget';
    container.appendChild(inner);

    const script = document.createElement('script');
    script.type = 'text/javascript';
    script.async = true;
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-events.js';
    script.text = JSON.stringify(config);
    script.onerror = function () {
      host.innerHTML = '<div class="tv-widget-error">Takvim yüklenemedi</div>';
    };
    container.appendChild(script);

    host.innerHTML = '';
    host.appendChild(container);
  };

  window.lazyRenderTvCalendar = function (opts) {
    opts = opts || {};
    const targetSel = opts.target || '#tv-calendar';
    const host = (typeof targetSel === 'string')
      ? document.querySelector(targetSel)
      : targetSel;
    if (!host) return;
    if (!('IntersectionObserver' in window)) {
      window.renderTvCalendar(opts);
      return;
    }
    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          io.disconnect();
          window.renderTvCalendar(opts);
        }
      });
    }, { rootMargin: '120px' });
    io.observe(host);
  };
})();
