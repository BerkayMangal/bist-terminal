// ================================================================
// BISTBULL TERMINAL — BULLALFA v1.4
// static/js/bullalfa.js  (UI rewrite — BullWatch tutarlı)
//
// Görsel dil: BullWatch (terminal.js _bwCard / renderBullwatchPage)
// ile birebir aynı. Mevcut .pkc / .pill / .btn / .clk-t class'larını
// kullanır; ayrı CSS gerekmez. Tüm renkler --ylw/--grn/--blu/...
// CSS var'larından — tema değişirse otomatik uyar.
// ================================================================

(function () {
  'use strict';

  // ── State ─────────────────────────────────────────────────────
  const BA = {
    scan: null,
    crossMap: null,         // ticker -> [{signal, stars, type, quality}]
    page: 1,
    perPage: 50,
    filters: { mode: null, sector: null },
    refreshing: false,
    _periodicTimer: null,   // 5-min auto-refresh timer
  };

  // ── Helpers ───────────────────────────────────────────────────
  const esc = s => {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  };
  const fmt = (x, d = 2) => x == null ? '—' : Number(x).toFixed(d);

  // ── Mode theming — color + bg + icon + label ──────────────────
  const MODE = {
    'HIZLI':      { color: 'var(--blu)',  bg: 'var(--blud)',           icon: '🚀', label: 'HIZLI' },
    'SWING':      { color: 'var(--grn)',  bg: 'var(--grnd)',           icon: '📈', label: 'SWING' },
    'POZİSYON':   { color: 'var(--gold)', bg: 'rgba(255,213,79,.12)',  icon: '🎯', label: 'POZİSYON' },
    'TOPLANIYOR': { color: 'var(--prp)',  bg: 'var(--prpd)',           icon: '🔍', label: 'TOPLANIYOR' },
    'SAKİN':      { color: 'var(--t3)',   bg: 'var(--bg3)',            icon: '😴', label: 'SAKİN' },
    'UZAK DUR':   { color: 'var(--red)',  bg: 'var(--redd)',           icon: '⛔', label: 'UZAK DUR' },
  };
  const modeMeta = m => MODE[m] || MODE['SAKİN'];

  const GRADE_COLOR = g =>
    g === 'A+' || g === 'A' ? 'var(--grn)' :
    g === 'B' ? 'var(--ylw)' :
    g === 'C' ? 'var(--orn)' :
    g === 'D' ? 'var(--red)' : 'var(--t3)';

  const SECTOR_ICON = {
    banka: '🏦', holding: '🏢', gyo: '🏗️', sanayi: '🏭',
    savunma: '🛡️', enerji: '⚡', perakende: '🛒', ulasim: '✈️',
    teknoloji: '💻', metal: '⛏️', gida: '🍞',
  };
  const sectorIcon = s => SECTOR_ICON[String(s||'').toLowerCase()] || '📊';

  // ── API ───────────────────────────────────────────────────────
  async function fetchScan(page, perPage, mode, sector) {
    const params = new URLSearchParams({ page, per_page: perPage });
    if (mode) params.set('mode', mode);
    if (sector) params.set('sector', sector);
    const r = await fetch('/api/bullalfa/scan?' + params.toString());
    if (!r.ok) throw new Error('scan fetch failed: ' + r.status);
    return r.json();
  }

  // Tolerant — never throws. Cross signals are an enrichment, not
  // a hard dependency. If the user hasn't visited Sinyaller yet
  // the cache might be empty/cold; that's fine, just return null.
  async function fetchCrossSilent() {
    try {
      const r = await fetch('/api/cross');
      if (!r.ok) return null;
      return await r.json();
    } catch (_) { return null; }
  }

  // Build {ticker -> [signal,...]} index from the cross response.
  function buildCrossMap(crossData) {
    const map = {};
    const list = (crossData && crossData.signals) || [];
    for (const s of list) {
      const t = String(s.ticker || '').toUpperCase();
      if (!t) continue;
      if (!map[t]) map[t] = [];
      map[t].push({
        signal: s.signal,
        stars:  s.stars || 1,
        type:   s.signal_type,        // bullish | bearish | neutral
        quality: s.signal_quality,    // A | B | C
        category: s.category,         // kirilim | momentum
      });
    }
    // Sort each ticker's signals by stars desc → most reliable first
    for (const k of Object.keys(map)) {
      map[k].sort((a, b) => (b.stars || 0) - (a.stars || 0));
    }
    return map;
  }

  // ── Card ──────────────────────────────────────────────────────
  function renderCard(s) {
    const m = modeMeta(s.mode);
    const q = s.quality || {};
    const grade = q.grade || '?';
    const score = q.score == null ? '—' : Math.round(q.score);
    const gcol = GRADE_COLOR(grade);
    const kalite = (q.tags || {}).kalite || '';
    const sg = s.sector_group || '';
    const sIco = sectorIcon(sg);
    const why = s.why_now || [];
    const exp = s.explainer || {};
    const caveats = [].concat(exp.warnings || [], exp.caveats || []);
    const isSakin = s.mode === 'SAKİN';
    const isWarn = s.mode === 'UZAK DUR';

    // Why-now block — only for actionable / building / risk modes
    let whyHtml = '';
    if (!isSakin && why.length) {
      const headerColor = isWarn ? 'var(--red)' : (s.mode === 'TOPLANIYOR' ? 'var(--prp)' : 'var(--cyn)');
      const headerIco = isWarn ? '⚠️' : (s.mode === 'TOPLANIYOR' ? '🔍' : '💡');
      const headerText = isWarn ? 'NEDEN UZAK DUR?' : 'NEDEN ŞİMDİ?';
      whyHtml = `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--bdr);font-size:12px;line-height:1.55">
        <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${headerColor};text-transform:uppercase;letter-spacing:.5px;font-weight:700;margin-bottom:6px">${headerIco} ${headerText}</div>
        <ul style="margin:0;padding-left:18px;color:var(--t1)">
          ${why.slice(0, 4).map(w => `<li style="margin-bottom:3px">${esc(w)}</li>`).join('')}
        </ul>
      </div>`;
    } else if (isSakin) {
      whyHtml = `<div style="margin-top:10px;color:var(--t4);font-size:11px;font-style:italic">Şu an dikkat çekici bir kurulum yok — radarda.</div>`;
    }

    // Risk frame — entry/stop/targets for actionable modes
    let rfHtml = '';
    const rf = s.risk_frame;
    if (rf && (s.mode === 'HIZLI' || s.mode === 'SWING' || s.mode === 'POZİSYON')) {
      const ez = rf.entry_zone || [];
      rfHtml = `<div style="margin-top:10px;padding:8px 10px;background:var(--bg2);border-radius:6px;font-size:11px;font-family:'JetBrains Mono',monospace;line-height:1.7">
        <div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">GİRİŞ</span><span style="color:var(--t1)">${fmt(ez[0])} – ${fmt(ez[1])}</span></div>
        <div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">STOP</span><span style="color:var(--red)">${fmt(rf.stop)}${rf.stop_pct != null ? ' (' + fmt(rf.stop_pct, 1) + '%)' : ''}</span></div>
        <div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">HEDEF</span><span style="color:var(--grn)">1R ${fmt(rf.target_1r)} · 2R ${fmt(rf.target_2r)} · 3R ${fmt(rf.target_3r)}</span></div>
        ${rf.max_hold_bars ? `<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">SÜRE</span><span style="color:var(--t2)">${esc(rf.max_hold_bars)} gün</span></div>` : ''}
      </div>`;
    }

    // Caveats
    let cavHtml = '';
    if (caveats.length) {
      cavHtml = `<div style="margin-top:8px;font-size:10px;color:var(--t4);font-style:italic">⚠️ ${caveats.slice(0, 3).map(esc).join(' · ')}</div>`;
    }

    // Cross Hunter integration — show up to 2 most-reliable cross
    // signals for this ticker as badges. Tap = jump to Sinyaller tab.
    let crossHtml = '';
    const crossList = (BA.crossMap || {})[String(s.ticker || '').toUpperCase()];
    if (crossList && crossList.length) {
      const top = crossList.slice(0, 2);
      crossHtml = `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <span style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:.3px">⚡ Aktif sinyaller:</span>
        ${top.map(c => {
          const col = c.type === 'bullish' ? 'var(--grn)' : c.type === 'bearish' ? 'var(--red)' : 'var(--t3)';
          const stars = '⭐'.repeat(Math.min(5, Math.max(1, c.stars || 1)));
          return `<span style="display:inline-flex;align-items:center;gap:4px;background:${col}1a;color:${col};border:1px solid ${col}44;font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:3px 7px;border-radius:4px" title="Sinyaller tabında detay">${esc(c.signal)} ${stars}</span>`;
        }).join('')}
        ${crossList.length > 2 ? `<span style="font-size:10px;color:var(--t4)">+${crossList.length - 2}</span>` : ''}
      </div>`;
    }

    // Calibration state
    const calib = s.calibration_state;
    let calibHtml = '';
    if (calib && calib !== 'active') {
      calibHtml = `<span style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t4);background:var(--bg2);padding:2px 6px;border-radius:3px;text-transform:uppercase;letter-spacing:.3px">Kalibrasyon: ${esc(calib === 'preview' ? 'ön-aşama' : calib)}</span>`;
    }

    return `<div class="pkc" style="border-left-color:${m.color};margin-bottom:0">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="clk-t" style="font-size:18px;font-weight:700" onclick="window.loadTicker && window.loadTicker('${esc(s.ticker)}')">${esc(s.ticker)}</span>
          </div>
          <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;align-items:center">
            <span style="display:inline-block;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;background:${m.bg};color:${m.color};border:1px solid ${m.color}55">${m.icon} ${m.label}</span>
            ${sg ? `<span style="display:inline-flex;align-items:center;gap:4px;background:var(--bg2);color:var(--t2);font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:3px 8px;border-radius:4px;text-transform:uppercase;letter-spacing:.4px">${sIco} ${esc(sg)}</span>` : ''}
            ${kalite ? `<span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3)">Kalite ${esc(kalite)}</span>` : ''}
            ${calibHtml}
          </div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;color:${gcol};line-height:1">${esc(score)}</div>
          <div style="font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;margin-top:2px">${esc(grade)} · SCORE</div>
        </div>
      </div>
      ${whyHtml}
      ${rfHtml}
      ${crossHtml}
      ${cavHtml}
    </div>`;
  }

  // ── Macro regime ribbon ───────────────────────────────────────
  function renderRibbon(macro) {
    if (!macro) return '';
    const regime = macro.regime || 'neutral';
    const LBL = { risk_on: 'RİSK-ON', neutral: 'NÖTR', risk_off: 'RİSK-OFF' };
    const COL = { risk_on: 'var(--grn)', neutral: 'var(--ylw)', risk_off: 'var(--red)' };
    const ICO = { risk_on: '🟢', neutral: '🟡', risk_off: '🔴' };
    const c = COL[regime] || 'var(--t3)';
    return `<div style="display:inline-flex;align-items:center;gap:8px;padding:6px 14px;background:${c}1a;border:1px solid ${c}66;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:11px;color:${c};font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px">${ICO[regime] || '⚪'} Makro Rejim: ${esc(LBL[regime] || regime)}</div>`;
  }

  // ── Mode filter tabs ──────────────────────────────────────────
  function renderModeTabs(byMode, total) {
    const order = ['HIZLI', 'SWING', 'POZİSYON', 'TOPLANIYOR', 'SAKİN', 'UZAK DUR'];
    const cur = BA.filters.mode || '';
    const tabs = [
      `<button class="btn btn-sm" style="background:${cur ? 'var(--bg3)' : 'linear-gradient(135deg,var(--acc),var(--acc2))'};color:${cur ? 'var(--t2)' : '#000'};font-size:11px;padding:6px 12px;min-height:36px;border:1px solid ${cur ? 'transparent' : 'var(--acc)'}" onclick="BullAlfa._setMode('')">Tümü (${total})</button>`
    ];
    for (const m of order) {
      const cnt = byMode[m] || 0;
      if (cnt === 0 && cur !== m) continue;
      const meta = MODE[m];
      const on = cur === m;
      tabs.push(`<button class="btn btn-sm" style="background:${on ? meta.bg : 'var(--bg3)'};color:${meta.color};border:1px solid ${on ? meta.color : 'transparent'};font-size:11px;padding:6px 12px;min-height:36px" onclick="BullAlfa._setMode('${esc(m)}')">${meta.icon} ${meta.label} (${cnt})</button>`);
    }
    return `<div style="display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap">${tabs.join('')}</div>`;
  }

  // ── SWING / actionable spotlight ──────────────────────────────
  // When BullAlfa flags HIZLI/SWING/POZİSYON setups, show them
  // prominently at top so the user doesn't have to scroll/filter.
  // (Per user feedback: "ozellikle swing tarafi icin bana demeli bunu")
  function renderActionableSpotlight(signals) {
    const ACTIONABLE = new Set(['HIZLI', 'SWING', 'POZİSYON']);
    const acts = (signals || []).filter(s => ACTIONABLE.has(s.mode));
    if (!acts.length) return '';
    // Sort by mode priority (SWING first per user request) then score desc
    const modeRank = { SWING: 0, POZİSYON: 1, HIZLI: 2 };
    acts.sort((a, b) => {
      const dr = (modeRank[a.mode] || 9) - (modeRank[b.mode] || 9);
      if (dr !== 0) return dr;
      return ((b.quality || {}).score || 0) - ((a.quality || {}).score || 0);
    });
    const top = acts.slice(0, 8);
    return `<div style="margin-bottom:14px;padding:14px 16px;background:linear-gradient(135deg,rgba(76,175,80,.08),rgba(255,213,79,.06));border:1px solid var(--grn);border-radius:var(--rad)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
        <span style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--grn);text-transform:uppercase;letter-spacing:.5px;font-weight:700">
          🎯 BUGÜNÜN AKTİF KURULUMLARI · ${acts.length} hisse
        </span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4)">tıkla → detaya git</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${top.map(s => {
          const m = modeMeta(s.mode);
          const sc = Math.round((s.quality || {}).score || 0);
          return `<button class="btn btn-sm" style="background:${m.bg};color:${m.color};border:1px solid ${m.color}55;font-size:11px;padding:6px 10px;min-height:36px;display:inline-flex;align-items:center;gap:6px" onclick="window.loadTicker && window.loadTicker('${esc(s.ticker)}')">
            <span>${m.icon}</span>
            <b>${esc(s.ticker)}</b>
            <span style="opacity:.7">${m.label}</span>
            <span style="color:var(--t1);font-weight:700">${sc}</span>
          </button>`;
        }).join('')}
        ${acts.length > 8 ? `<button class="btn btn-sm" style="background:var(--bg3);color:var(--t2);font-size:11px;padding:6px 10px" onclick="BullAlfa._setMode('SWING')">+${acts.length - 8} daha →</button>` : ''}
      </div>
    </div>`;
  }

  // ── Sector filter tabs ────────────────────────────────────────
  function renderSectorTabs(concentration) {
    const entries = Object.entries(concentration || {})
      .filter(([_, n]) => n > 0)
      .sort((a, b) => b[1] - a[1]);
    if (entries.length <= 1) return '';
    const cur = BA.filters.sector || '';
    const total = entries.reduce((a, [_, n]) => a + n, 0);
    let html = `<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;align-items:center">
      <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);text-transform:uppercase;letter-spacing:.5px;margin-right:4px">SEKTÖR</span>
      <button class="btn btn-sm" style="background:${cur ? 'var(--bg3)' : 'var(--ylwd)'};color:${cur ? 'var(--t2)' : 'var(--ylw)'};border:1px solid ${cur ? 'transparent' : 'var(--ylw)'};font-size:11px;padding:4px 10px;min-height:32px" onclick="BullAlfa._setSector('')">Tümü (${total})</button>`;
    for (const [s, n] of entries) {
      const on = cur === s;
      html += `<button class="btn btn-sm" style="background:${on ? 'var(--bg4)' : 'var(--bg3)'};color:var(--t2);border:1px solid ${on ? 'var(--t2)' : 'transparent'};font-size:11px;padding:4px 10px;min-height:32px" onclick="BullAlfa._setSector('${esc(s)}')">${sectorIcon(s)} ${esc(s)} (${n})</button>`;
    }
    return html + '</div>';
  }

  // ── Empty / warming / error states ────────────────────────────
  function renderWarmingState() {
    return `<div style="padding:40px 20px;text-align:center;background:rgba(255,202,40,.06);border:1px dashed var(--ylw);border-radius:var(--rad);margin:14px 0">
      <div style="font-size:36px;margin-bottom:12px">⏳</div>
      <div style="color:var(--ylw);font-weight:700;font-size:16px;margin-bottom:8px;font-family:'JetBrains Mono',monospace">HİSSELER HAZIRLANIYOR</div>
      <div style="color:var(--t3);font-size:13px;line-height:1.6">İlk tarama ~1-3 dakika sürer.<br>Sayfa 30 saniyede bir otomatik yenilenir.</div>
    </div>`;
  }
  function renderEmptyFilter() {
    return `<div style="padding:30px 20px;text-align:center;color:var(--t3);background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);margin:10px 0">
      <div style="font-size:32px;margin-bottom:8px">🔍</div>
      <div style="font-size:14px">Filtre kriterlerine uygun hisse bulunamadı.</div>
      <div style="font-size:11px;color:var(--t4);margin-top:6px">Mod veya sektör filtresini değiştir, ya da 'Tümü' tıkla.</div>
    </div>`;
  }
  function renderError(msg) {
    return `<div style="padding:30px 20px;text-align:center;color:var(--red);background:var(--redd);border:1px solid var(--red);border-radius:var(--rad);margin:14px 0">
      <div style="font-size:24px;margin-bottom:8px">❌</div>
      <div style="font-weight:700">Veri alınamadı</div>
      <div style="font-size:11px;color:var(--t3);margin-top:6px;font-family:'JetBrains Mono',monospace">${esc(msg)}</div>
    </div>`;
  }
  function renderLoading() {
    return `<div style="padding:40px 20px;text-align:center;color:var(--t3)">
      <div class="sp" style="margin:0 auto 12px"></div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:13px">Yükleniyor…</div>
    </div>`;
  }

  // ── Header ────────────────────────────────────────────────────
  function renderHeader(meta, total) {
    const asof = meta.cache_as_of || meta.generated_at;
    const asofStr = asof ? new Date(asof).toLocaleString('tr-TR') : '<i style="color:var(--t4)">veri hazırlanıyor</i>';
    const cb = meta.circuit_breaker || {};
    const cbBadge = cb.frozen ? `<span class="pill p-red" style="font-size:10px;margin-left:8px">⛔ Veri Donmuş</span>` : '';
    return `<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;flex-wrap:wrap;gap:12px">
      <div>
        <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--acc);margin:0">🎯 BullAlfa — BIST Tarayıcı ${cbBadge}</h2>
        <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Son güncelleme: ${asofStr} · ${total} hisse · evren BIST 100 · <span style="color:var(--t4)">5dk'da bir otomatik yenilenir</span></p>
        <p style="font-size:11px;color:var(--t4);margin-top:4px;font-family:'JetBrains Mono',monospace">📅 Veri: son tamamlanmış işlem günü · 7-motor (Trend / Göreli Güç / Hacim / Kırılım / Pivot / Volatilite / Yorgunluk)</p>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-grn" id="ba-refresh-btn" ${BA.refreshing ? 'disabled' : ''}>🔄 ${BA.refreshing ? 'TARANIYOR…' : 'YENİDEN TARA'}</button>
      </div>
    </div>`;
  }

  // ── Info box (about) ──────────────────────────────────────────
  function renderAboutBox() {
    return `<div style="padding:12px 16px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-base);color:var(--t2);line-height:1.6">
      <b style="color:var(--acc)">BullAlfa nasıl çalışır?</b> Her BIST hissesini 6 moda ayırır:
      <b style="color:var(--blu)">HIZLI</b> intraday momentum ·
      <b style="color:var(--grn)">SWING</b> 3-15 gün ·
      <b style="color:var(--gold)">POZİSYON</b> 15-60 gün ·
      <b style="color:var(--prp)">TOPLANIYOR</b> kurulum şekilleniyor ·
      <b style="color:var(--t3)">SAKİN</b> radar yok ·
      <b style="color:var(--red)">UZAK DUR</b> risk yüksek.
      <span style="display:inline-block;margin-top:4px;color:var(--t3);font-size:12px">⚡ Cross Hunter'ın bulduğu teknik sinyaller her kartta badge olarak görünür — birlikte okuyun.</span>
      <span style="color:var(--t4);font-size:11px">Yatırım tavsiyesi değildir.</span>
    </div>`;
  }

  // ── Render ────────────────────────────────────────────────────
  async function renderTab(rootEl) {
    if (!rootEl) return;
    // First render — loading spinner
    if (!BA.scan) rootEl.innerHTML = renderLoading();

    // Parallel fetch — Cross signals enrich BullAlfa cards but are
    // not required; if /api/cross fails or has no data, badges just
    // don't show.
    let scan, crossData;
    try {
      [scan, crossData] = await Promise.all([
        fetchScan(BA.page, BA.perPage, BA.filters.mode, BA.filters.sector),
        fetchCrossSilent(),
      ]);
    } catch (e) {
      rootEl.innerHTML = renderError(e.message);
      return;
    }
    BA.scan = scan;
    BA.crossMap = buildCrossMap(crossData);

    const meta = scan.meta || {};
    const signals = scan.signals || [];
    const macro = (signals[0] && signals[0].macro) || meta.macro || null;
    const isEmpty = signals.length === 0;
    const isWarming = meta.warming_up === true;
    const byMode = meta.by_mode || {};
    const sectorConc = meta.sector_concentration || {};
    const p = meta.pagination || {};
    const total = p.total != null ? p.total : signals.length;

    let html = '';
    html += renderHeader(meta, total);
    html += renderAboutBox();
    if (macro) html += renderRibbon(macro);

    if (isWarming && isEmpty) {
      html += renderWarmingState();
      rootEl.innerHTML = html;
      wireRefresh(rootEl);
      setTimeout(() => {
        if (BA.scan && BA.scan.meta && BA.scan.meta.warming_up) renderTab(rootEl);
      }, 30000);
      return;
    }

    // SWING spotlight — prominent banner with actionable tickers.
    // Only shows when there's something actionable (HIZLI/SWING/POZİSYON).
    html += renderActionableSpotlight(signals);

    html += renderModeTabs(byMode, total);
    html += renderSectorTabs(sectorConc);

    if (isEmpty) {
      html += renderEmptyFilter();
    } else {
      html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;margin-top:8px">
        ${signals.map(renderCard).join('')}
      </div>`;
    }

    // Pagination
    if (p.total && p.total > p.per_page) {
      const totalPages = Math.max(1, Math.ceil(p.total / p.per_page));
      html += `<div style="display:flex;justify-content:center;align-items:center;gap:10px;margin-top:20px;font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t2)">
        <button class="btn btn-sm" id="ba-prev" ${p.page <= 1 ? 'disabled' : ''}>‹ ÖNCEKİ</button>
        <span>Sayfa <b style="color:var(--t1)">${p.page}</b> / ${totalPages}</span>
        <button class="btn btn-sm" id="ba-next" ${p.page >= totalPages ? 'disabled' : ''}>SONRAKİ ›</button>
      </div>`;
    }

    rootEl.innerHTML = html;
    wireRefresh(rootEl);
    const prev = document.getElementById('ba-prev');
    const next = document.getElementById('ba-next');
    if (prev) prev.onclick = () => { BA.page = Math.max(1, BA.page - 1); renderTab(rootEl); };
    if (next) next.onclick = () => { BA.page = BA.page + 1; renderTab(rootEl); };

    // Start the 5-min auto-refresh after the first successful render.
    // (Backend cache TTL is also 5 min — they tick in sync.)
    startPeriodicRefresh(rootEl);
  }

  // Polite 5-minute auto-refresh. Only fires when tab is visible
  // (browser tab is foreground AND the BullAlfa page div is in view).
  function startPeriodicRefresh(rootEl) {
    if (BA._periodicTimer) return;  // already running
    BA._periodicTimer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      if (!rootEl || !rootEl.offsetParent) return;  // hidden via display:none
      if (BA.refreshing) return;                    // manual refresh in flight
      renderTab(rootEl);
    }, 5 * 60 * 1000);  // 5 dakika
  }

  function wireRefresh(rootEl) {
    const btn = document.getElementById('ba-refresh-btn');
    if (btn) btn.onclick = async () => {
      if (BA.refreshing) return;
      BA.refreshing = true;
      btn.disabled = true;
      btn.innerHTML = '⏳ TARANIYOR…';
      try { await fetch('/api/bullalfa/scan/refresh'); } catch (_) {}
      BA.refreshing = false;
      renderTab(rootEl);
    };
  }

  // ── Public API ────────────────────────────────────────────────
  function _rerender() {
    const el = document.getElementById('pg-bullalfa');
    if (el) renderTab(el);
  }
  window.BullAlfa = {
    renderTab,
    fetchScan,
    _setMode: (m) => { BA.filters.mode = m || null; BA.page = 1; _rerender(); },
    _setSector: (s) => { BA.filters.sector = s || null; BA.page = 1; _rerender(); },
    state: BA,
  };
})();
