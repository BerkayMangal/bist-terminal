// ================================================================
// BISTBULL TERMINAL — BULLALFA v1.4
// static/js/bullalfa.js
//
// Mobile-first (~380px) tab + card components per spec §23.
// Mode-specific card layouts: HIZLI / SWING / POZİSYON / TOPLANIYOR
// / SAKİN / UZAK DUR.
//
// API contracts (spec §19):
//   GET /api/bullalfa/scan?page=N&per_page=M&mode=X&sector=Y
//     → { signals: [...], meta: {...} }
//   GET /api/bullalfa/{ticker}
//     → { signal: {...}, schema_version: "1.4" }
//
// Style mirrors `static/terminal.js` — single-letter helpers for
// brevity, esc() for XSS, CSS vars for theming.
// ================================================================

(function () {
  'use strict';

  // ===== STATE =====
  const BA = {
    scan: null,
    ticker: null,
    page: 1,
    perPage: 50,
    filters: { mode: null, sector: null },
    macroRibbon: null,
  };

  // ===== HELPERS (mirror terminal.js conventions) =====
  const $ = id => document.getElementById(id);
  const esc = s => {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  };
  const fmt = (x, d = 2) => x == null ? '—' : Number(x).toFixed(d);
  const fmtPct = (x, d = 1) => x == null ? '—' : (Number(x) * 100).toFixed(d) + '%';
  const fmtTL = x => {
    if (x == null) return '—';
    if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B TL';
    if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M TL';
    if (x >= 1e3) return (x / 1e3).toFixed(0) + 'K TL';
    return x.toFixed(0) + ' TL';
  };

  // ===== MODE THEMING =====
  const MODE_COLOR = {
    'HIZLI':      'var(--grn)',
    'SWING':      'var(--grn)',
    'POZİSYON':   'var(--grn)',
    'TOPLANIYOR': 'var(--ylw)',
    'SAKİN':      'var(--t3)',
    'UZAK DUR':   'var(--red)',
  };
  const MODE_BG = {
    'HIZLI':      'rgba(76,175,80,.10)',
    'SWING':      'rgba(76,175,80,.10)',
    'POZİSYON':   'rgba(76,175,80,.10)',
    'TOPLANIYOR': 'rgba(255,202,40,.10)',
    'SAKİN':      'rgba(127,127,127,.06)',
    'UZAK DUR':   'rgba(239,83,80,.12)',
  };
  const GRADE_COLOR = g =>
    g === 'A+' || g === 'A' ? 'var(--grn)' :
    g === 'B' ? 'var(--ylw)' :
    g === 'C' ? 'var(--ylw)' : 'var(--red)';

  // Engine pass/warn/fail icon — three states from §23.
  const ICN = {
    ok: '✅', warn: '⚠️', fail: '❌',
  };
  const e1Icon = e1 => e1 ? ICN.ok : ICN.fail;
  const e3Icon = e3 => {
    if (!e3) return ICN.fail;
    if (e3.passed) return ICN.ok;
    if (e3.rvol >= 1.0) return ICN.warn;
    return ICN.fail;
  };
  const e4Icon = e4 => {
    if (!e4 || !e4.type) return ICN.fail;
    if (e4.bars_ago === 0 || e4.bars_ago === 1) return ICN.ok;
    return ICN.warn;
  };
  const e7Icon = e7 => {
    if (e7 == null || e7 === 0) return ICN.fail;
    if (e7 > 0.6) return ICN.fail;
    if (e7 > 0.3) return ICN.warn;
    return ICN.fail; // low exhaustion is a "good" state, but the §23
                     // sample shows ❌ for "Yorgun" — meaning "the
                     // 'tired' check did NOT fire". Match the sample.
  };

  // ===== API =====
  async function fetchScan(page = 1, perPage = 50, mode = null, sector = null) {
    const params = new URLSearchParams({ page, per_page: perPage });
    if (mode) params.set('mode', mode);
    if (sector) params.set('sector', sector);
    const r = await fetch('/api/bullalfa/scan?' + params.toString());
    if (!r.ok) throw new Error('scan fetch failed: ' + r.status);
    return r.json();
  }

  async function fetchTicker(ticker) {
    const r = await fetch('/api/bullalfa/' + encodeURIComponent(ticker));
    if (!r.ok) throw new Error('ticker fetch failed: ' + r.status);
    return r.json();
  }

  // ===== CARD RENDERERS (per §23) =====

  // ---- HEADER LINE — common across modes
  function renderHeader(s) {
    const q = s.quality || {};
    const tags = q.tags || {};
    const grade = q.grade || '?';
    const score = q.score == null ? '?' : q.score;
    const kalite = tags.kalite || '';
    const valueTag = tags.value ? `<span class="ba-tag">${esc(tags.value)}</span>` : '';
    const buffettTag = tags.buffett === 'Geçti' ? '<span class="ba-tag">Buffett: Geçti</span>' : '';
    const grahamTag = tags.graham === 'Geçti' ? '<span class="ba-tag">Graham: Geçti</span>' : '';
    return `
      <div class="ba-card-header">
        <span class="ba-ticker">${esc(s.ticker)}</span>
        <span class="ba-grade" style="color:${GRADE_COLOR(grade)}">${esc(grade)} (${esc(score)})</span>
        <span class="ba-kalite">Kalite ${esc(kalite)}</span>
      </div>
      <div class="ba-tags">${valueTag}${buffettTag}${grahamTag}</div>
    `;
  }

  // ---- MODE LINE
  function renderModeLine(s) {
    const mode = s.mode;
    const opp = s.opportunity_score;
    const conf = (s.confidence || {}).final;
    const horizon = s.horizon_label || '—';
    const lifecycleStatus = (s.lifecycle || {}).status;
    const fresh = lifecycleStatus ? `${esc(lifecycleStatus)}` : '';
    let body = '';
    if (mode === 'HIZLI' || mode === 'SWING' || mode === 'POZİSYON') {
      body += `<div class="ba-row"><span class="ba-lbl">MODE</span><span style="color:${MODE_COLOR[mode]}">${esc(mode)}</span></div>`;
      body += `<div class="ba-row"><span class="ba-lbl">HORİZON</span><span>${esc(horizon)}</span></div>`;
      body += `<div class="ba-row"><span class="ba-lbl">GÜVEN</span><span>${esc(fmt(conf, 0))}% ${esc(fresh)}</span></div>`;
      body += `<div class="ba-row"><span class="ba-lbl">FIRSAT</span><span>${esc(opp)}/100</span></div>`;
    } else if (mode === 'TOPLANIYOR') {
      body += `<div class="ba-row"><span class="ba-lbl">MODE</span><span style="color:${MODE_COLOR[mode]}">${esc(mode)}</span></div>`;
      body += `<div class="ba-row"><span class="ba-lbl">FIRSAT</span><span>${esc(opp)}/100  <span class="ba-muted">(kurulum şekilleniyor)</span></span></div>`;
    } else if (mode === 'UZAK DUR') {
      body += `<div class="ba-row"><span class="ba-lbl">MODE</span><span style="color:${MODE_COLOR[mode]}">${esc(mode)}</span></div>`;
    }
    return body;
  }

  // ---- WHY NOW
  function renderWhyNow(s) {
    const why = s.why_now || [];
    if (mode_is_sakin(s)) return '';
    if (!why.length) return '';
    const label = s.mode === 'UZAK DUR' ? 'NEDEN?' : 'NEDEN ŞİMDİ?';
    return `
      <div class="ba-section-title">${label}</div>
      <ul class="ba-bullets">
        ${why.map(b => `<li>${esc(b)}</li>`).join('')}
      </ul>
    `;
  }

  // ---- ENGINE STATES (actionable only)
  function renderEngineStates(s) {
    if (s.mode !== 'HIZLI' && s.mode !== 'SWING' && s.mode !== 'POZİSYON') return '';
    const e = s.engines || {};
    const e1 = e1Icon(e.e1_trend);
    const e2 = (e.e2_relstr && e.e2_relstr.score >= 1) ? ICN.ok :
               (e.e2_relstr && e.e2_relstr.score >= 0.5) ? ICN.warn : ICN.fail;
    const e3 = e3Icon(e.e3_volume);
    const e4 = e4Icon(e.e4_breakout);
    const e7 = (e.e7_exhaustion > 0.6) ? ICN.fail :
               (e.e7_exhaustion > 0.3) ? ICN.warn : ICN.ok;
    return `
      <div class="ba-engine-row">
        <span>Trend ${e1}</span>
        <span>Göreli güç ${e2}</span>
        <span>Hacim ${e3}</span>
        <span>Kırılım ${e4}</span>
        <span>Yorgun değil ${e7}</span>
      </div>
    `;
  }

  // ---- RISK FRAME (actionable only)
  function renderRiskFrame(s) {
    const rf = s.risk_frame;
    if (!rf) return '';
    const ez = rf.entry_zone || [];
    const stop = fmt(rf.stop, 2);
    const stopPct = rf.stop_pct == null ? '' : ` (${fmt(rf.stop_pct, 1)}%)`;
    const t1 = fmt(rf.target_1r, 2);
    const t2 = fmt(rf.target_2r, 2);
    const t3 = fmt(rf.target_3r, 2);
    return `
      <div class="ba-risk">
        <div class="ba-row"><span class="ba-lbl">GİRİŞ</span><span>${esc(fmt(ez[0], 2))} – ${esc(fmt(ez[1], 2))}</span></div>
        <div class="ba-row"><span class="ba-lbl">STOP</span><span>${esc(stop)}${esc(stopPct)}</span></div>
        <div class="ba-row"><span class="ba-lbl">HEDEF</span><span>1R ${esc(t1)} · 2R ${esc(t2)} · 3R ${esc(t3)}</span></div>
        <div class="ba-row"><span class="ba-lbl">MAX SÜRE</span><span>${esc(rf.max_hold_bars)} işlem günü</span></div>
        <div class="ba-row"><span class="ba-lbl">TRAIL</span><span>${esc(rf.trail_rule)}</span></div>
        <div class="ba-row ba-muted"><span></span><span>${esc(rf.invalidation)}</span></div>
      </div>
    `;
  }

  // ---- TOPLANIYOR HINT
  function renderToplaniyorHint(s) {
    if (s.mode !== 'TOPLANIYOR') return '';
    return `
      <div class="ba-toplaniyor-hint">
        ⚡ Potansiyel kırılım yakın<br>
        <span class="ba-muted">Henüz teyit yok, takipte</span>
      </div>
    `;
  }

  // ---- SAKIN MESSAGE
  function renderSakinMessage(s) {
    if (s.mode !== 'SAKİN') return '';
    return `<div class="ba-sakin-line">Şu an dikkat çekici bir kurulum yok.</div>`;
  }

  // ---- CAVEATS / WARNINGS
  function renderCaveats(s) {
    const exp = s.explainer || {};
    const items = []
      .concat(exp.warnings || [])
      .concat(exp.caveats || []);
    // Dedup, preserving order.
    const seen = new Set();
    const list = [];
    for (const x of items) {
      if (x && !seen.has(x)) {
        seen.add(x);
        list.push(x);
      }
    }
    if (!list.length) return '';
    return `<div class="ba-caveats">${list.map(esc).join(' · ')}</div>`;
  }

  function mode_is_sakin(s) { return s.mode === 'SAKİN'; }

  // ---- CARD DISPATCH
  function renderCard(s) {
    const bg = MODE_BG[s.mode] || 'transparent';
    return `
      <div class="ba-card" data-mode="${esc(s.mode)}" data-ticker="${esc(s.ticker)}" style="background:${bg}">
        ${renderHeader(s)}
        ${renderSakinMessage(s)}
        ${renderModeLine(s)}
        ${renderWhyNow(s)}
        ${renderEngineStates(s)}
        ${renderRiskFrame(s)}
        ${renderToplaniyorHint(s)}
        ${renderCaveats(s)}
      </div>
    `;
  }

  // ===== MACRO RIBBON (top of tab) =====
  function renderMacroRibbon(macro) {
    if (!macro) return '';
    const regime = macro.regime || 'neutral';
    const REGIME_LABEL = { risk_on: 'RİSK-ON', neutral: 'NÖTR', risk_off: 'RİSK-OFF' };
    const REGIME_COLOR = { risk_on: 'var(--grn)', neutral: 'var(--ylw)', risk_off: 'var(--red)' };
    const label = REGIME_LABEL[regime] || regime;
    const color = REGIME_COLOR[regime] || 'var(--t3)';
    let suffix = '';
    if (macro.hizli_disabled) {
      suffix = ' — Hızlı sinyaller devre dışı';
    } else if (regime === 'neutral') {
      suffix = ' — Hızlı modda dikkat';
    }
    return `<div class="ba-ribbon" style="color:${color}">Rejim: ${esc(label)}${esc(suffix)}</div>`;
  }

  // ===== SECTOR CONCENTRATION BANNER (§17) =====
  function renderConcentrationBanner(meta) {
    const sc = (meta || {}).sector_concentration || {};
    let max = 0, sector = null;
    for (const k of Object.keys(sc)) {
      if (sc[k] > max) { max = sc[k]; sector = k; }
    }
    if (max < 5 || !sector) return '';
    return `<div class="ba-concentration">Bugün ${esc(sector)} sektöründe ${max} sinyal var — yoğun korelasyon, dikkat.</div>`;
  }

  // ===== FILTERS / PAGINATION =====
  function renderFilters() {
    const modes = ['', 'HIZLI', 'SWING', 'POZİSYON', 'TOPLANIYOR', 'SAKİN', 'UZAK DUR'];
    const sectors = ['', 'banka', 'holding', 'gyo', 'sanayi', 'savunma', 'enerji', 'perakende', 'ulasim'];
    const optsM = modes.map(m =>
      `<option value="${esc(m)}"${BA.filters.mode === m ? ' selected' : ''}>${esc(m || 'Tüm modlar')}</option>`
    ).join('');
    const optsS = sectors.map(g =>
      `<option value="${esc(g)}"${BA.filters.sector === g ? ' selected' : ''}>${esc(g || 'Tüm sektörler')}</option>`
    ).join('');
    return `
      <div class="ba-filters">
        <select id="ba-filter-mode">${optsM}</select>
        <select id="ba-filter-sector">${optsS}</select>
        <button id="ba-filter-apply">Uygula</button>
        <button id="ba-filter-refresh">Yenile</button>
      </div>
    `;
  }

  function renderPagination(meta) {
    const p = (meta || {}).pagination;
    if (!p) return '';
    const totalPages = Math.max(1, Math.ceil(p.total / p.per_page));
    return `
      <div class="ba-pagination">
        <button id="ba-page-prev"${p.page <= 1 ? ' disabled' : ''}>‹</button>
        <span>${esc(p.page)} / ${esc(totalPages)}</span>
        <button id="ba-page-next"${p.page >= totalPages ? ' disabled' : ''}>›</button>
        <span class="ba-muted">${esc(p.total)} hisse</span>
      </div>
    `;
  }

  // ===== TAB ROOT RENDER =====
  async function renderTab(rootEl) {
    if (!rootEl) return;
    rootEl.innerHTML = '<div class="ba-loading">Yükleniyor…</div>';
    let scan;
    try {
      scan = await fetchScan(BA.page, BA.perPage, BA.filters.mode, BA.filters.sector);
    } catch (e) {
      rootEl.innerHTML = `<div class="ba-error">Veri alınamadı: ${esc(e.message)}</div>`;
      return;
    }
    BA.scan = scan;
    const meta = scan.meta || {};
    const macro = (scan.signals && scan.signals[0] && scan.signals[0].macro) || null;
    const ribbon = renderMacroRibbon(macro);
    const banner = renderConcentrationBanner(meta);
    const filters = renderFilters();
    const cards = (scan.signals || []).map(renderCard).join('');
    const pag = renderPagination(meta);
    rootEl.innerHTML = `
      ${ribbon}
      ${banner}
      ${filters}
      <div class="ba-cards">${cards}</div>
      ${pag}
    `;
    wireControls(rootEl);
  }

  function wireControls(rootEl) {
    const fMode = $('ba-filter-mode'), fSec = $('ba-filter-sector');
    const apply = $('ba-filter-apply'), refresh = $('ba-filter-refresh');
    const prev = $('ba-page-prev'), next = $('ba-page-next');
    if (apply) apply.onclick = () => {
      BA.filters.mode = fMode && fMode.value ? fMode.value : null;
      BA.filters.sector = fSec && fSec.value ? fSec.value : null;
      BA.page = 1;
      renderTab(rootEl);
    };
    if (refresh) refresh.onclick = async () => {
      try { await fetch('/api/bullalfa/scan/refresh'); } catch (_) {}
      renderTab(rootEl);
    };
    if (prev) prev.onclick = () => { BA.page = Math.max(1, BA.page - 1); renderTab(rootEl); };
    if (next) next.onclick = () => { BA.page = BA.page + 1; renderTab(rootEl); };
  }

  // ===== SINGLE-TICKER VIEW =====
  async function renderTicker(rootEl, ticker) {
    if (!rootEl || !ticker) return;
    rootEl.innerHTML = '<div class="ba-loading">Yükleniyor…</div>';
    try {
      const data = await fetchTicker(ticker);
      const card = renderCard(data.signal);
      const ribbon = renderMacroRibbon(data.signal && data.signal.macro);
      rootEl.innerHTML = ribbon + card;
    } catch (e) {
      rootEl.innerHTML = `<div class="ba-error">Veri alınamadı: ${esc(e.message)}</div>`;
    }
  }

  // ===== PUBLIC API =====
  // Wire-up pattern: in the page that hosts the BullAlfa tab,
  //   <script src="/static/js/bullalfa.js"></script>
  //   <script>BullAlfa.renderTab(document.getElementById('ba-tab'));</script>
  // For per-ticker drill-down:
  //   BullAlfa.renderTicker(document.getElementById('ba-detail'), 'ASELS');
  window.BullAlfa = {
    renderTab,
    renderTicker,
    fetchScan,
    fetchTicker,
    state: BA,
  };
})();
