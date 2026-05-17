// BISTBULL TERMINAL V10.0 — Frontend Application

// ===== STATE =====
const S={page:'home',scan:null,cross:null,macro:null,dash:null,takas:null,social:null,hero:null,quote:null,book:null,wl:JSON.parse(localStorage.getItem('bb_wl')||'[]'),seen:JSON.parse(localStorage.getItem('bb_seen')||'[]'),_alerts:[]};
const QT=['ASELS','THYAO','BIMAS','KCHOL','TUPRS','AKBNK','GARAN','FROTO','TOASO','PGSUS'];
const PAGES=[{id:'nasil',label:'Nasıl?',icon:'❓'},{id:'home',label:'Ana Sayfa',icon:'🏠'},{id:'akis',label:'Akış',icon:'📰'},{id:'radar',label:'Radar',icon:'📡'},{id:'bullwatch',label:'BullWatch',icon:'🐂'},{id:'bulten',label:'Günlük Bülten',icon:'📰'},{id:'alarmlar',label:'Alarmlar',icon:'🚨'},{id:'bullalfa',label:'BullAlfa',icon:'🎯'},{id:'viop',label:'VIOP',icon:'🎲'},{id:'bilancolar',label:'Bilançolar',icon:'📊'},{id:'makro',label:'Makro',icon:'🌍'},{id:'portfoy',label:'Portföy',icon:'💼'},{id:'diag',label:'Tanı',icon:'🔧'}];
const $=s=>document.getElementById(s);

// ===== XSS SANITIZER =====
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=String(s);return d.innerHTML;}

// ===== FORMATTERS =====
const fN=(x,d=2)=>{if(x==null)return'N/A';if(Math.abs(x)>=1e9)return(x/1e9).toFixed(2)+'B';if(Math.abs(x)>=1e6)return(x/1e6).toFixed(2)+'M';if(Math.abs(x)>=1e3)return x.toLocaleString('en',{maximumFractionDigits:0});return x.toFixed(d);};
const fP=(x,d=1)=>x==null?'N/A':(x*100).toFixed(d)+'%';
const sC=v=>{if(v==null)return'var(--t3)';return v>=75?'var(--grn)':v>=55?'var(--ylw)':'var(--red)';};
const sPill=v=>{if(v==null)return'<span class="pill p-blu">—</span>';return v>=75?`<span class="pill p-grn">${v.toFixed(0)}</span>`:v>=55?`<span class="pill p-ylw">${v.toFixed(0)}</span>`:`<span class="pill p-red">${v.toFixed(0)}</span>`;};
const sPL=l=>(l==='Geçti'||l==='Pass')?'<span class="pill p-grn">Geçti</span>':(l==='Sınırda'||l==='Borderline')?'<span class="pill p-ylw">Sınırda</span>':'<span class="pill p-red">Kaldı</span>';
const cC=v=>v>=0?'var(--grn)':'var(--red)';
const cS=v=>v>=0?'+'+v.toFixed(2):v.toFixed(2);

// ===== RADAR KALİTE NOTU + (eski) VERDICT =====
// Radar artık aksiyon etiketi (AL/GİR) DEĞİL, şirket kalite notu üretir:
// Çok Başarılı / Başarılı / Orta / Zayıf / Riskli. Eski AL/İZLE/KAÇIN
// anahtarları BullAlfa/BullWatch hâlâ kullanabildiği için korunuyor.
const VERDICT_MAP={'AL':'GİR','İZLE':'BEKLE','BEKLE':'BEKLE','KAÇIN':'UZAK DUR'};
const VERDICT_COLOR={'AL':'var(--grn)','İZLE':'var(--ylw)','BEKLE':'var(--ylw)','KAÇIN':'var(--red)',
  'Çok Başarılı':'var(--grn)','Başarılı':'var(--grn)','Orta':'var(--ylw)','Zayıf':'var(--red)','Riskli':'var(--red)'};
const VERDICT_BG={'AL':'rgba(76,175,80,.12)','İZLE':'rgba(255,202,40,.12)','BEKLE':'rgba(255,202,40,.12)','KAÇIN':'rgba(239,83,80,.12)',
  'Çok Başarılı':'rgba(76,175,80,.14)','Başarılı':'rgba(76,175,80,.12)','Orta':'rgba(255,202,40,.12)','Zayıf':'rgba(239,83,80,.12)','Riskli':'rgba(239,83,80,.14)'};
const VERDICT_DESC={'AL':'Temel analiz güçlü, zamanlama uygun — değerlendirmeye değer.','İZLE':'İlginç profil ama henüz tam net değil — yakından izlenebilir.','BEKLE':'Şirket iyi ama fiyat yüksek veya zamanlama geç olabilir.','KAÇIN':'Veriler risk işaret ediyor — dikkatli olunmasını öneririz.',
  'Çok Başarılı':'Temel analiz çok güçlü — kaliteli, sağlam bir şirket.','Başarılı':'Temelleri sağlam, iyi bir şirket profili.','Orta':'Ortalama temel görünüm — ne belirgin güçlü ne zayıf.','Zayıf':'Temel analiz zayıf — dikkatli değerlendirilmeli.','Riskli':'Veriler ciddi risk işaret ediyor.'};
function vLabel(dc){return VERDICT_MAP[dc]||dc;}
function vColor(dc){return VERDICT_COLOR[dc]||'var(--t3)';}
function vBg(dc){return VERDICT_BG[dc]||'var(--bg3)';}
function confLevel(pct){if(pct>=80)return'Yüksek';if(pct>=60)return'Orta';return'Düşük';}
function confColor(pct){if(pct>=80)return'var(--grn)';if(pct>=60)return'var(--ylw)';return'var(--red)';}
// Bilanço veri yaşı rozeti — kullanıcı tarama sonucunun ne kadar taze
// olduğunu anlasın diye (raw_cache TTL 24h, stale grace 7g).
// r.data_age_hours: 0..168h olağan; 168+ stale (gri), 24+ uyarı (sarı).
// Skeleton helpers — telegraph "content coming" instead of a spinner.
// One row-style skeleton serves Bilançolar + Alarmlar (both list-style
// with ticker + meta + reaction pills). The count argument controls
// how many placeholder rows render — eyeballed to fit a typical
// viewport without forcing a scroll.
function _skelRow(){
  return `<div class="skel-card">
    <div class="skel-row-header">
      <div class="skel-row-tickerblock">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <div class="skel skel-line lg" style="width:60px"></div>
          <div class="skel skel-pill"></div>
          <div class="skel skel-pill" style="width:90px"></div>
        </div>
        <div class="skel skel-line long"></div>
      </div>
      <div class="skel skel-line short" style="width:50px"></div>
    </div>
    <div style="display:flex;gap:4px;margin-top:6px">
      <div class="skel skel-pill" style="width:60px;height:14px"></div>
      <div class="skel skel-pill" style="width:60px;height:14px"></div>
      <div class="skel skel-pill" style="width:60px;height:14px"></div>
    </div>
  </div>`;
}
function _skelList(count){
  return `<div class="card"><div class="card-b" style="padding:14px">${
    Array.from({length: count}, () => _skelRow()).join('')
  }</div></div>`;
}
function _skelTwoPane(){
  // Bilançolar-style two-pane skeleton
  return `<div class="g2" style="gap:14px">
    <div>${_skelList(6)}</div>
    <div>${_skelList(4)}</div>
  </div>`;
}
function _skelHeader(title){
  return `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--t2)">${esc(title)}</h2>
      <div class="skel skel-line short" style="width:200px;margin-top:6px"></div>
    </div>
    <div class="skel skel-pill" style="width:48px;height:32px"></div>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:14px">
    <div class="skel skel-pill" style="width:90px;height:28px"></div>
    <div class="skel skel-pill" style="width:90px;height:28px"></div>
    <div class="skel skel-pill" style="width:90px;height:28px"></div>
    <div class="skel skel-pill" style="width:90px;height:28px"></div>
  </div>`;
}

function bilancoBadge(r){
  const h = r && r.data_age_hours;
  if (h == null) return '';
  const hours = Number(h);
  if (!Number.isFinite(hours)) return '';
  const label = hours < 1 ? 'az önce'
              : hours < 24 ? `${Math.round(hours)} sa önce`
              : `${Math.round(hours/24)} gün önce`;
  let color = 'var(--t4)';
  let warn = '';
  if (hours > 168) { color = 'var(--red)'; warn = ' ⚠️'; }
  else if (hours > 24) { color = 'var(--ylw)'; }
  return ` · <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:${color}" title="Bilanço verisi son ${label} çekildi (raw_cache TTL=24h)">📅 Bilanço: ${label}${warn}</span>`;
}

// ===== METRIC TOOLTIP DATA =====
const METRIC_TIPS={
  'F/K':'Fiyat/Kazanç oranı. Şirketin kârına kaç kat fiyat biçildiği. Düşük F/K = ucuz olabilir, yüksek = pahalı.',
  'PD/DD':'Piyasa Değeri / Defter Değeri. 1\'den düşükse hisse defter değerinden ucuz satılıyor demek.',
  'FD/FAVÖK':'Firma Değeri / FAVÖK. Borçla birlikte şirkete ödenen fiyat. 7\'den düşük genellikle makul kabul edilir.',
  'ROE':'Özsermaye Kârlılığı. Şirket ortak parasını ne kadar verimli kullanıyor. %15+ güçlü sayılır.',
  'ROA':'Aktif Kârlılığı. Şirketin tüm varlıklarını ne kadar verimli kullandığı.',
  'ROIC':'Yatırılan Sermaye Getirisi. En kapsamlı kârlılık ölçütü — hem borç hem özsermaye dahil.',
  'PEG':'F/K oranını büyümeyle karşılaştırır. 1\'den düşük = büyümesine göre ucuz.',
  'FCF Getiri':'Serbest Nakit Akışı / Piyasa Değeri. Şirket gerçekte ne kadar nakit üretiyor?',
  'Piotroski':'0-9 arası finansal sağlık puanı. 7+ güçlü, 3- zayıf. Joseph Piotroski\'nin modeli.',
  'Altman Z':'İflas riski ölçütü. 3\'ün üstü güvenli, 1.8\'in altı riskli. Bankalar için geçersiz.',
  'Beneish M-Score':'Muhasebe manipülasyonu testi. -2.22\'den düşük güvenli, yüksek değerler şüphe yaratır.',
  'Graham Değer':'Benjamin Graham\'ın içsel değer formülü. Mevcut fiyatın altındaysa güvenlik payı var.',
  'Güvenlik Payı':'Hesaplanan değer ile mevcut fiyat arasındaki fark. Ne kadar büyükse o kadar güvenli.',
  'Temel Analiz Skoru':'Şirketin bilançosu, kârlılığı ve değerlemesi değerlendirilerek 0-100 arası skor. 70+ güçlü.',
  'Fiyat Trendi':'Son dönemde fiyatın genel yönü — yukarı, yatay veya aşağı.',
  'Değerleme':'Hisse fiyatının gerçek değere göre konumu. Sektör ortalamasıyla karşılaştırılır.',
  'Finansal Sağlık':'Şirketin borç-nakit dengesi ve ödeme gücü.',
  'Rekabet Avantajı':'Rakiplerin bu şirketi kopyalaması ne kadar zor — marj stabilitesi ve fiyatlama gücü.',
  'Parayı Doğru Kullanıyor':'Şirket kazandığı parayı yatırımcı için en iyi şekilde harcıyor mu?',
};

// ===== INLINE TOOLTIP HELPER =====
function tipHtml(label, tipKey){
  const tip=METRIC_TIPS[tipKey||label]||'';
  if(!tip)return`<span style="font-size:var(--fs-xs);color:var(--t3)">${label}</span>`;
  return`<span style="display:inline-flex;align-items:center;gap:3px;cursor:help" title="${tip}"><span style="font-size:var(--fs-xs);color:var(--t3)">${label}</span><span style="font-size:9px;color:var(--t4);border:1px solid var(--bdr);border-radius:50%;width:12px;height:12px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">?</span></span>`;
}

// ===== API =====
// HOTFIX 1 (2026-Q2 production incident): raw fetch() has no default
// timeout, so a slow /api/heatmap (previously 10min on cold cache)
// would block the whole UI. AbortController + per-endpoint timeout
// caps any regression to a user-visible error instead of a blank page.
const _API_TIMEOUTS = {
  '/api/heatmap': 3000,    // < 3s or frontend shows empty-state
  '/api/analyze': 15000,   // single-symbol deep analysis is slower
  '/api/scan':    30000,   // user-initiated full scan
  '/api/agent':   20000,   // AI call
  '/api/ai-summary': 20000,
  // /api/ai/{symbol}/consensus does a LIVE Claude call (~600 tokens) +
  // analyze_symbol on cache miss — 10-20s end to end. The 8s default
  // was aborting it → "AI analizi yüklenemedi". Trailing slash keeps
  // this distinct from /api/ai-summary (which is /api/ai-, not /api/ai/).
  '/api/ai/': 35000,
  '/api/bullwatch': 300000,  // first-run scan can take 1-3 min on cold cache (yfinance is slow)
  '/api/kap/disclosure': 5000,    // disclosure detail is a single SQLite/Redis read
  '/api/kap': 8000,                // generic KAP endpoints (recent / by-ticker / calendar)
};
// AI analyze endpoint takes longer (Grok call + analyze_symbol). Detect
// the suffix and override.
const _API_TIMEOUT_OVERRIDES = [
  [/\/api\/kap\/disclosure\/\d+\/analyze$/, 45000],  // Grok + analyze_symbol
];
function _timeoutFor(path) {
  for (const [rx, ms] of _API_TIMEOUT_OVERRIDES) {
    if (rx.test(path)) return ms;
  }
  for (const prefix of Object.keys(_API_TIMEOUTS)) {
    if (path.startsWith(prefix)) return _API_TIMEOUTS[prefix];
  }
  return 8000;  // default
}
async function api(p, opts){
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), _timeoutFor(p));
  try {
    const fopts = { signal: ctrl.signal };
    if (opts && opts.method) fopts.method = opts.method;
    if (opts && opts.body)   fopts.body   = opts.body;
    if (opts && opts.headers) fopts.headers = opts.headers;
    const r = await fetch(p, fopts);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('timeout');
    throw e;
  } finally {
    clearTimeout(t);
  }
}

// ===== CACHED API — stale-while-revalidate for frontend =====
const _apiCache = {};
const _API_TTL = {
  '/api/macro': 120000, '/api/top10': 60000, '/api/cross': 120000,
  '/api/quote': 86400000, '/api/book': 86400000, '/api/market-status': 60000,
  '/api/dashboard': 60000, '/api/hero-summary': 120000, '/api/heatmap': 120000,
};
async function cachedApi(path) {
  const now = Date.now();
  const hit = _apiCache[path];
  const ttl = _API_TTL[path] || 30000;
  if (hit && (now - hit.ts) < ttl) return hit.data;
  if (hit && (now - hit.ts) < ttl * 3) {
    api(path).then(d => { _apiCache[path] = {data: d, ts: Date.now()}; }).catch(() => {});
    return hit.data;
  }
  const data = await api(path);
  _apiCache[path] = {data, ts: now};
  return data;
}


// ===== CLOCK =====
setInterval(()=>{$('clk').textContent=new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})+' IST';},1000);

// ===== NAVIGATION =====
const nav=$('nav');const mobNav=$('mobNav');PAGES.forEach(p=>{const b=document.createElement('button');b.className='nav-b'+(p.id==='home'?' on':'');b.textContent=p.label;b.dataset.p=p.id;b.onclick=()=>goPage(p.id);nav.appendChild(b);if(mobNav){const mb=document.createElement('button');mb.className='mob-bnav-item'+(p.id==='home'?' on':'');mb.dataset.p=p.id;mb.onclick=()=>goPage(p.id);mb.innerHTML=`<span class="ico" aria-hidden="true">${p.icon||'•'}</span><span>${esc(p.label)}</span>`;mobNav.appendChild(mb);}});

// Unread badge for the Bilançolar tab — polls /api/kap/recent every 2
// min and compares the latest disclosure_index against the last one the
// user saw (tracked in localStorage). Adds a red dot to the NAV button.
async function _updateBilancoBadge(){
  try {
    const r = await api('/api/kap/recent?limit=1');
    const it = (r && (r.items || [])[0]);
    if (!it) return;
    const lastSeen = parseInt(localStorage.getItem('bb_kap_last_seen') || '0', 10);
    const hasUnread = (it.disclosure_index || 0) > lastSeen;
    const setDot = (sel) => {
      const btn = document.querySelector(sel);
      if (!btn) return;
      // Idempotent: strip any existing dot first, then re-add if needed
      btn.textContent = btn.textContent.replace(' ●', '');
      const mobLabel = btn.querySelector && btn.querySelector('span:nth-child(2)');
      if (mobLabel) mobLabel.textContent = mobLabel.textContent.replace(' ●', '');
      if (hasUnread) {
        if (mobLabel) mobLabel.textContent = mobLabel.textContent + ' ●';
        else btn.textContent = btn.textContent + ' ●';
        btn.style.color = 'var(--red)';
      } else {
        btn.style.color = '';
      }
    };
    setDot('.nav-b[data-p="bilancolar"]');
    setDot('.mob-bnav-item[data-p="bilancolar"]');
  } catch (e) {
    // Silent — badge is decorative
  }
}
setTimeout(_updateBilancoBadge, 5000);
setInterval(_updateBilancoBadge, 120000);

function goPage(id){
  S.page=id;
  nav.querySelectorAll('.nav-b').forEach(b=>b.classList.toggle('on',b.dataset.p===id));
  if(mobNav)mobNav.querySelectorAll('.mob-bnav-item').forEach(b=>b.classList.toggle('on',b.dataset.p===id));
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.dataset.page===id));
  // Scroll the active nav button into view — important on mobile where
  // the 9-tab nav lives in an overflow-x:auto container; otherwise the
  // newly-activated tab can be offscreen.
  const activeBtn = nav.querySelector('.nav-b.on');
  if (activeBtn && activeBtn.scrollIntoView) {
    activeBtn.scrollIntoView({behavior:'smooth', inline:'center', block:'nearest'});
  }
  // Always scroll page body to top when changing tabs — phones especially
  // get stuck mid-scroll otherwise.
  try { window.scrollTo({top:0, behavior:'instant'}); } catch (e) { window.scrollTo(0,0); }
  if(id==='home')renderHome();
  if(id==='akis')renderAkisPage();
  if(id==='viop')renderViopPage();
  if(id==='diag')renderDiagPage();
  if(id==='radar')renderRadarPage();
  if(id==='cross'){goPage('bullalfa');BullAlfa&&BullAlfa._setMode&&BullAlfa._setMode('__SIGNALS__');return;}
  if(id==='bullwatch')renderBullwatchPage();
  if(id==='bulten')renderBultenPage();
  if(id==='bullalfa')renderBullalfaPage();
  if(id==='alarmlar')renderAlarmlarPage();
  if(id==='bilancolar')renderBilancolarPage();
  if(id==='makro')renderMakroPage();
  if(id==='nasil')renderNasilPage();
  if(id==='takas')renderTakasPage();
  if(id==='sosyal')renderSosyalPage();
  if(id==='portfoy')renderPortfoyPage();
}

// ===== QUICK TICKERS =====
// ===== QUICK TICKERS =====
const qtEl=$('qticks');QT.forEach(t=>{const d=document.createElement('div');d.className='qtk';d.textContent=t;d.onclick=()=>{closeDisc();loadTicker(t);};qtEl.appendChild(d);});

// ===== DISCOVERY PANEL =====
const discEl=$('srchDisc');
let _discOpen=false,_srchTimer=null;

function openDisc(){
  if(_discOpen)return;
  _discOpen=true;
  discEl.classList.add('open');
  renderDiscovery('');
}
function closeDisc(){
  _discOpen=false;
  discEl.classList.remove('open');
}
function pickStock(ticker){
  closeDisc();
  $('sinp').value='';
  loadTicker(ticker);
}

function renderDiscovery(q){
  if(q.length>=2){renderAutocomplete(q);return;}
  // ── DISCOVERY STATE: empty query ──
  const sc=S.scan;
  const seen=S.seen||[];
  let h='';

  // 1. Recently viewed
  if(seen.length){
    h+=`<div class="disc-section"><div class="disc-label">🕐 Son Baktıklarınız</div><div style="display:flex;flex-wrap:wrap;gap:4px">`;
    seen.slice(0,8).forEach(t=>{
      h+=`<div class="disc-chip" onmousedown="event.preventDefault();pickStock('${esc(t)}')">${esc(t)}</div>`;
    });
    h+=`</div></div>`;
  }

  // 2. Top scoring stocks from scan
  if(sc&&sc.items&&sc.items.length){
    const top5=[...sc.items].sort((a,b)=>(b.deger||b.overall||0)-(a.deger||a.overall||0)).slice(0,5);
    h+=`<div class="disc-section"><div class="disc-label">🏆 En Yüksek Skorlu</div>`;
    top5.forEach(it=>{
      const dc=it.decision||'';const dcCol=vColor(dc);const dcLbl=vLabel(dc);
      const score=(it.deger||it.overall||0).toFixed(0);
      h+=`<div class="disc-row" onmousedown="event.preventDefault();pickStock('${esc(it.ticker)}')">
        <span class="disc-ticker">${esc(it.ticker)}</span>
        <span class="disc-name">${esc(it.name||it.sector||'')}</span>
        <span class="disc-badge" style="background:${dcCol}15;color:${dcCol}">${dcLbl}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--grn);font-weight:700">${score}</span>
      </div>`;
    });
    h+=`</div>`;

    // 3. Strong quality picks (was: momentum/ivme picks; Plan A switched
    // Radar to pure-fundamental so we surface a quality leaderboard here).
    const topQual=[...sc.items].sort((a,b)=>((b.scores||{}).quality||0)-((a.scores||{}).quality||0)).slice(0,4);
    h+=`<div class="disc-section"><div class="disc-label">⭐ Güçlü Şirket Kalitesi</div>`;
    topQual.forEach(it=>{
      const qv=(((it.scores||{}).quality)||0).toFixed(0);
      h+=`<div class="disc-row" onmousedown="event.preventDefault();pickStock('${esc(it.ticker)}')">
        <span class="disc-ticker">${esc(it.ticker)}</span>
        <span class="disc-name">${esc(it.sector||'')}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3)">Kalite</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--blu);font-weight:700">${qv}</span>
      </div>`;
    });
    h+=`</div>`;

    // 4. Sectors browse
    const sectors={};
    sc.items.forEach(it=>{if(it.sector&&it.ticker){if(!sectors[it.sector])sectors[it.sector]=[];sectors[it.sector].push(it.ticker);}});
    const secKeys=Object.keys(sectors).slice(0,6);
    if(secKeys.length){
      h+=`<div class="disc-section"><div class="disc-label">🏢 Sektöre Göre</div><div style="display:flex;flex-wrap:wrap;gap:4px">`;
      secKeys.forEach(sec=>{
        const count=sectors[sec].length;
        h+=`<div class="disc-chip" onmousedown="event.preventDefault();showSector('${esc(sec)}')" style="font-size:10px">${esc(sec)} <span style="opacity:.5">(${count})</span></div>`;
      });
      h+=`</div></div>`;
    }
  } else {
    // No scan data yet — show popular chips
    h+=`<div class="disc-section"><div class="disc-label">🔥 Popüler Hisseler</div><div style="display:flex;flex-wrap:wrap;gap:4px">`;
    ['THYAO','ASELS','GARAN','BIMAS','KCHOL','TUPRS','AKBNK','FROTO','TOASO','PGSUS','SASA','EREGL'].forEach(t=>{
      h+=`<div class="disc-chip" onmousedown="event.preventDefault();pickStock('${esc(t)}')">${esc(t)}</div>`;
    });
    h+=`</div></div>`;
  }

  // Footer hint
  h+=`<div style="padding:8px 16px;font-size:10px;color:var(--t4);text-align:center;border-top:1px solid var(--bdr)">Yazmaya başla → otomatik tamamlama · Enter → hisse gör</div>`;
  discEl.innerHTML=h;
}

function renderAutocomplete(q){
  discEl.innerHTML=`<div style="padding:12px 16px;font-size:11px;color:var(--t3)">Aranıyor…</div>`;
  api('/api/search-suggest?q='+encodeURIComponent(q)).then(function(d){
    if(!d||!d.suggestions||!d.suggestions.length){
      discEl.innerHTML=`<div style="padding:14px 16px;font-size:12px;color:var(--t3)">Sonuç bulunamadı: <b style="color:var(--t1)">${esc(q)}</b><br><span style="font-size:10px">Enter ile direkt ara</span></div>`;
      return;
    }
    let h=`<div class="disc-section"><div class="disc-label">🔍 Eşleşen Hisseler</div>`;
    d.suggestions.slice(0,8).forEach(function(s){
      const sc2=S.scan&&S.scan.items?S.scan.items.find(i=>i.ticker===s.ticker):null;
      const dc=sc2?.decision||'';const dcCol=vColor(dc);const dcLbl=vLabel(dc);
      const score=sc2?(sc2.deger||sc2.overall||0).toFixed(0):'';
      h+=`<div class="disc-row" onmousedown="event.preventDefault();pickStock('${esc(s.ticker)}')">
        <span class="disc-ticker">${esc(s.ticker)}</span>
        <span class="disc-name">${esc(s.match!=='ticker'?s.match:'')}</span>
        ${dc?`<span class="disc-badge" style="background:${dcCol}15;color:${dcCol}">${dcLbl}</span>`:''}
        ${score?`<span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--grn);font-weight:700">${score}</span>`:''}
      </div>`;
    });
    h+=`</div>`;
    discEl.innerHTML=h;
  }).catch(function(){
    // Fallback: filter from scan data
    if(S.scan&&S.scan.items){
      const matches=S.scan.items.filter(i=>i.ticker.includes(q.toUpperCase())||(i.name&&i.name.toUpperCase().includes(q.toUpperCase()))).slice(0,6);
      if(matches.length){
        let h=`<div class="disc-section"><div class="disc-label">🔍 Eşleşen Hisseler</div>`;
        matches.forEach(function(it){
          const dc=it.decision||'';const dcCol=vColor(dc);const dcLbl=vLabel(dc);
          h+=`<div class="disc-row" onmousedown="event.preventDefault();pickStock('${esc(it.ticker)}')">
            <span class="disc-ticker">${esc(it.ticker)}</span>
            <span class="disc-name">${esc(it.name||it.sector||'')}</span>
            ${dc?`<span class="disc-badge" style="background:${dcCol}15;color:${dcCol}">${dcLbl}</span>`:''}
          </div>`;
        });
        h+=`</div>`;
        discEl.innerHTML=h;
      }
    }
  });
}

function showSector(sec){
  closeDisc();
  const items=(S.scan&&S.scan.items||[]).filter(i=>i.sector===sec).slice(0,6);
  if(!items.length)return;
  // Navigate to radar page with sector filter (future) or just load first
  // For now: show sector in a mini panel modal approach
  $('sinp').value=sec;
  openDisc();
  let h=`<div class="disc-section"><div class="disc-label">🏢 ${esc(sec)} Sektörü</div>`;
  items.forEach(it=>{
    const dc=it.decision||'';const dcCol=vColor(dc);const dcLbl=vLabel(dc);
    const score=(it.deger||it.overall||0).toFixed(0);
    h+=`<div class="disc-row" onmousedown="event.preventDefault();pickStock('${esc(it.ticker)}');$('sinp').value=''">
      <span class="disc-ticker">${esc(it.ticker)}</span>
      <span class="disc-name">${esc(it.name||'')}</span>
      <span class="disc-badge" style="background:${dcCol}15;color:${dcCol}">${dcLbl}</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--grn);font-weight:700">${score}</span>
    </div>`;
  });
  h+=`</div><div style="padding:8px 16px"><button class="btn btn-sm btn-blu" onmousedown="event.preventDefault();$('sinp').value='';closeDisc();goPage('radar')">Radar\'da Gör →</button></div>`;
  discEl.innerHTML=h;
}

// ── Search input events ──
$('sinp').addEventListener('focus',function(){openDisc();renderDiscovery(this.value.trim());});
$('sinp').addEventListener('input',function(e){
  const v=e.target.value.trim();
  if(!_discOpen)openDisc();
  if(_srchTimer)clearTimeout(_srchTimer);
  _srchTimer=setTimeout(function(){renderDiscovery(v);},220);
});
$('sinp').addEventListener('keydown',function(e){
  if(e.key==='Enter'){
    e.preventDefault();
    const v=e.target.value.trim();
    if(v.length<2)return;
    closeDisc();
    api('/api/resolve-ticker?q='+encodeURIComponent(v)).then(function(d){
      if(d&&d.tickers&&d.tickers.length){loadTicker(d.tickers[0]);}
      else{if(v.length>=2)loadTicker(v.toUpperCase());}
      e.target.value='';
    }).catch(function(){
      if(v.length>=2)loadTicker(v.toUpperCase());
      e.target.value='';
    });
  }
  if(e.key==='Escape'){closeDisc();e.target.blur();}
});
$('sinp').addEventListener('blur',function(){setTimeout(function(){if(!document.activeElement||document.activeElement.id!=='sinp'){closeDisc();}},250);});
// Click outside to close
document.addEventListener('click',function(e){if(_discOpen&&!$('sbarWrap').contains(e.target)){closeDisc();}});

// ===== WATCHLIST + SEEN =====
function wlAdd(t){t=t.toUpperCase();if(!S.wl.includes(t)){S.wl.push(t);localStorage.setItem('bb_wl',JSON.stringify(S.wl));}fetch('/api/watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:t})}).catch(()=>{});}
function wlRm(t){S.wl=S.wl.filter(x=>x!==t);localStorage.setItem('bb_wl',JSON.stringify(S.wl));fetch('/api/watchlist/'+t,{method:'DELETE'}).catch(()=>{});}
function seenAdd(t){t=t.toUpperCase();S.seen=S.seen.filter(x=>x!==t);S.seen.unshift(t);S.seen=S.seen.slice(0,10);localStorage.setItem('bb_seen',JSON.stringify(S.seen));}

// ===== UI COMPONENTS =====
function ring(v,l,sz=100){const r=(sz/2)-6,ci=2*Math.PI*r,off=ci*(1-(v||0)/100),c=sC(v);return`<div class="ring" style="width:${sz}px;height:${sz}px"><svg width="${sz}" height="${sz}" viewBox="0 0 ${sz} ${sz}"><circle cx="${sz/2}" cy="${sz/2}" r="${r}" fill="none" stroke="var(--bdr)" stroke-width="3"/><circle cx="${sz/2}" cy="${sz/2}" r="${r}" fill="none" stroke="${c}" stroke-width="3" stroke-dasharray="${ci}" stroke-dashoffset="${off}" stroke-linecap="round" style="transition:stroke-dashoffset 1s"/></svg><span class="rv" style="color:${c};font-size:${sz>80?24:16}px">${v!=null?v.toFixed(0):'?'}</span>${l?`<span class="rl" style="bottom:${sz>80?12:6}px">${esc(l)}</span>`:''}</div>`;}
function scoreBars(sc){
const dG=[
  {k:'value',   l:'Değerleme',                   desc:'Bu hisse sektörüne kıyasla ucuz mu pahalı mı? F/K, PD/DD ve FCF baz alınır.',w:18},
  {k:'quality',  l:'Şirket Kalitesi',             desc:'Kârlılık ve verimlilik — ROE, ROIC, net marj. Gerçekten iyi bir şirket mi?', w:25},
  {k:'growth',   l:'Büyüme',                      desc:'Şirketin gelirleri ve kârı artıyor mu? Yüksek enflasyonda reel büyüme önemli.',w:12},
  {k:'balance',  l:'Finansal Sağlık',             desc:'Borç ve nakit dengesi. Altman Z-Score ve borç/özsermaye temel alınır.',      w:15},
  {k:'earnings', l:'Kâr Kalitesi',                desc:'Kazandığı para gerçekten kasaya giriyor mu? Nakit akışı kârı destekliyor mu?',w:13},
  {k:'capital',  l:'Parayı Doğru Kullanıyor mu?', desc:'Yönetim kazandığı parayı yatırımcı için en akıllıca şekilde harcıyor mu?',   w:10},
  {k:'moat',     l:'Rekabet Avantajı',             desc:'Rakiplerin bu şirketi kopyalaması ne kadar zor? Marj stabilitesi ölçülür.',  w:7},
];
const iG=[
  {k:'momentum',   l:'Fiyat Trendi',   desc:'Son dönemde fiyat yukarı mı gidiyor aşağı mı? RSI ve fiyat/MA pozisyonu.',  w:40},
  {k:'tech_break', l:'Teknik Kırılım', desc:'Önemli fiyat seviyeleri kırılıyor mu? Golden Cross, 52 haftalık zirve.',    w:35},
  {k:'inst_flow',  l:'Kurum Akışı',    desc:'Büyük kurumsal yatırımcılar bu hisseyi alıyor mu? Hacim-fiyat korelasyonu.',w:25},
];
const gb=v=>{if(v==null)return{t:'—',c:'var(--t4)'};if(v>=70)return{t:'Güçlü',c:'var(--grn)'};if(v>=50)return{t:'Orta',c:'var(--ylw)'};return{t:'Zayıf',c:'var(--red)'};};
const bar=(d)=>{const v=sc[d.k],c=sC(v),g=gb(v);
return`<div class="sb" style="margin-bottom:10px"><div class="sb-l" style="margin-bottom:4px"><span style="display:inline-flex;align-items:center;gap:4px"><span style="color:var(--t2);font-size:12px">${d.l}</span><span title="${d.desc}" style="cursor:help;color:var(--t4);font-size:9px;border:1px solid var(--bdr);border-radius:50%;width:13px;height:13px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">?</span><span style="font-size:9px;color:var(--t4)">${d.w}%</span></span><div style="display:flex;align-items:center;gap:8px"><span style="font-size:10px;color:${g.c};font-weight:600">${g.t}</span><span class="v" style="color:${c};font-weight:700">${v!=null?v.toFixed(0):'?'}</span></div></div><div class="sb-bar"><div class="sb-fill" style="width:${v||0}%;background:linear-gradient(90deg,${c}99,${c})"></div></div></div>`;};
return`<div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--grn);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏛️ Temel Analiz Boyutları</div>${dG.map(bar).join('')}<div style="margin-top:12px;font-size:10px;color:var(--t4);font-style:italic">Radar skoru yalnızca temel (fundamental) analize dayanır — momentum ve teknik sinyaller Cross Hunter ile BullAlfa modüllerinde.</div>`;}
function mBox(l,v,tip){return`<div class="mi"${tip?` title="${tip}"`:""} style="${tip?'cursor:help':''}"><div class="mi-l">${l}</div><div class="mi-v">${esc(String(v))}</div></div>`;}
// Veri Tazeliği — küçük renkli rozet, her satırda gösterilir.
// "Fresh / Old / Stale / Unknown" durumunu age_hours üzerinden bantlara
// ayırır. Tıklayınca modal açar ve /api/diag/fundamentals/{ticker}'i
// yükler — borsapy fetch yaşı, son KAP finansal raporu, gap.
function _radarFreshBadge(ticker){
  const f = S.diagFresh && S.diagFresh[(ticker||'').toUpperCase()];
  if (!f) {
    return `<span class="clk-t" onclick="event.stopPropagation();showFreshModal('${esc(ticker)}')" title="Veri tazeliği — tıkla" style="display:inline-flex;align-items:center;font-family:'JetBrains Mono',monospace;font-size:9px;padding:2px 5px;background:var(--bg3);color:var(--t4);border-radius:3px">⋯</span>`;
  }
  const st = f.age_status || 'unknown';
  const m = {
    fresh:   {ic:'✓', col:'var(--grn)', bg:'rgba(38,194,129,.12)', lbl: f.age_hours!=null?`${f.age_hours.toFixed(0)}sa`:'fresh'},
    old:     {ic:'◷', col:'var(--ylw)', bg:'rgba(255,193,7,.14)',  lbl: f.age_hours!=null?`${f.age_hours.toFixed(0)}sa`:'old'},
    stale:   {ic:'✕', col:'var(--red)', bg:'rgba(239,83,80,.14)',  lbl: f.age_hours!=null?`${(f.age_hours/24).toFixed(0)}g`:'stale'},
    unknown: {ic:'?', col:'var(--t4)',  bg:'var(--bg3)',           lbl:'—'},
  }[st];
  const tip = `${st.toUpperCase()} · borsapy ${f.age_hours!=null?f.age_hours.toFixed(0)+'sa':'?'} · KAP ${f.kap_age_days!=null?f.kap_age_days.toFixed(0)+'g':'?'}${f.gap_days!=null&&f.gap_days>1?' · gap +'+f.gap_days.toFixed(0)+'g ⚠':''}`;
  return `<span class="clk-t" onclick="event.stopPropagation();showFreshModal('${esc(ticker)}')" title="${esc(tip)}" style="display:inline-flex;align-items:center;gap:3px;font-family:'JetBrains Mono',monospace;font-size:9px;padding:2px 5px;background:${m.bg};color:${m.col};border-radius:3px;cursor:pointer"><span>${m.ic}</span><b>${esc(m.lbl)}</b></span>`;
}

function renderRadarTbl(items,sortBy='deger'){
const sorted=[...items].sort((a,b)=>sortBy==='piotroski'?((b.scores?.earnings||0)-(a.scores?.earnings||0)):sortBy==='balance'?((b.scores?.balance||0)-(a.scores?.balance||0)):sortBy==='quality'?((b.scores?.quality||0)-(a.scores?.quality||0)):sortBy==='roe'?((b.roe||0)-(a.roe||0)):sortBy==='ciro_pd'?((b.ciro_pd||0)-(a.ciro_pd||0)):(b.deger||b.overall||0)-(a.deger||a.overall||0));
let h=`<div style="display:flex;gap:4px;margin-bottom:10px;flex-wrap:wrap"><button class="btn btn-sm ${sortBy==='deger'?'btn-grn':''}" style="${sortBy!=='deger'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='deger';renderRadarPage()">🏛️ Değer</button><button class="btn btn-sm ${sortBy==='quality'?'btn-grn':''}" style="${sortBy!=='quality'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='quality';renderRadarPage()">Kalite</button><button class="btn btn-sm ${sortBy==='balance'?'btn-grn':''}" style="${sortBy!=='balance'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='balance';renderRadarPage()">Bilanço</button><button class="btn btn-sm ${sortBy==='piotroski'?'btn-grn':''}" style="${sortBy!=='piotroski'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='piotroski';renderRadarPage()">Kâr Kalitesi</button><button class="btn btn-sm ${sortBy==='roe'?'btn-grn':''}" style="${sortBy!=='roe'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='roe';renderRadarPage()">ROE</button><button class="btn btn-sm ${sortBy==='ciro_pd'?'btn-grn':''}" style="${sortBy!=='ciro_pd'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._radarSort='ciro_pd';renderRadarPage()">Ciro/PD</button></div>`;
h+='<table class="dtb"><thead><tr><th>#</th><th>Hisse</th><th title="Veri tazeliği — borsapy fetch yaşı + KAP son finansal rapor">📅 Veri</th><th title="Radar kalite notu">Not</th><th style="color:var(--grn)">Değer</th><th>Değ</th><th>Kal</th><th>Bil</th><th>ROE</th><th>F/K</th><th>Ciro/PD</th></tr></thead><tbody>';
sorted.forEach((it,i)=>{const dc=it.decision;const dCol=vColor(dc);const dcLabel=vLabel(dc);const cpL=it.ciro_pd_label;const cpBadge=cpL?`<span style="font-family:'JetBrains Mono',monospace;font-size:8px;font-weight:700;padding:1px 5px;border-radius:3px;background:${cpL.color}18;color:${cpL.color}">${cpL.label}</span>`:'<span style="color:var(--t4);font-size:9px">—</span>';const roeVal=it.roe;const roePct=roeVal!=null?`<span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:${roeVal>=0.15?'var(--grn)':roeVal>=0.08?'var(--t1)':'var(--red)'};font-weight:${roeVal>=0.15?700:400}">${(roeVal*100).toFixed(0)}%</span>`:'<span style="color:var(--t4);font-size:9px">—</span>';const dqt=it.data_quality_tier||'full';const dqDot=dqt==='full'?'':'<span title="'+(dqt==='partial'?'Kısmi veri':'Sadece piyasa verisi')+'" style="font-size:7px;margin-left:2px">'+(dqt==='partial'?'🟡':'🔴')+'</span>';h+=`<tr${it.is_fatal?' style="opacity:0.5"':''}><td style="color:var(--t3)">${i+1}</td><td class="clk-t" onclick="loadTicker('${esc(it.ticker)}')">${esc(it.ticker)}${dqDot}${it.size_tier==='mikro'?'<span title="Mikro-cap — düşük likidite, dikkatli ol" style="font-size:7px;color:var(--ylw);margin-left:3px">mikro</span>':''}${it.is_fatal?'<span style="color:var(--red);font-size:8px"> ⛔</span>':''}</td><td>${_radarFreshBadge(it.ticker)}</td><td><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);font-weight:700;color:${dCol};padding:2px 6px;background:${dCol}15;border-radius:3px">${dcLabel||'—'}</span></td><td><span style="font-weight:700;color:var(--grn)">${(it.deger||it.overall||0).toFixed(0)}</span></td><td>${sPill(it.scores?.value)}</td><td>${sPill(it.scores?.quality)}</td><td>${sPill(it.scores?.balance)}</td><td>${roePct}</td><td style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t1)">${it.pe?it.pe.toFixed(1):'—'}</td><td>${cpBadge}</td></tr>`;});
return h+'</tbody></table>';}

// ===== WELCOME + TRACKING =====
function showWelcome(){const el=$('welcomeOv');if(el){el.style.display='block';document.body.style.overflow='hidden';trackEv('welcome_view');}}
function dismissWelcome(){const el=$('welcomeOv');if(el){el.style.display='none';document.body.style.overflow='';}localStorage.setItem('bb_welcomed','1');trackEv('welcome_cta');}
function trackEv(ev){try{navigator.sendBeacon('/api/track?e='+encodeURIComponent(ev));}catch(e){fetch('/api/track?e='+encodeURIComponent(ev)).catch(()=>{});}}

// ===== TÜYO KONTROL =====
function tuyoKontrol(){const inp=$('tuyoInp');if(!inp)return;const t=inp.value.trim().toUpperCase().replace('.IS','');if(!t||t.length<3)return;inp.value='';trackEv('tuyo_'+t);loadTicker(t);}
document.addEventListener('keydown',e=>{if(e.target&&e.target.id==='tuyoInp'&&e.key==='Enter'){e.preventDefault();tuyoKontrol();}});

if(new URLSearchParams(window.location.search).has('reset')){localStorage.removeItem('bb_welcomed');localStorage.removeItem('bb_info_dismissed');history.replaceState(null,'',window.location.pathname);}
if(!localStorage.getItem('bb_welcomed')){document.addEventListener('DOMContentLoaded',showWelcome);}

// ===== PIYASA NABZI =====
function renderNabiz(items){
if(!items||!items.length)return'<div style="color:var(--t4);font-size:var(--fs-sm)">Yükleniyor...</div>';
const keys=['XU030','USDTRY','EURTRY','BRENT','GOLD','VIX','XU100','DXY','SP500','US10Y'];
const show=keys.map(k=>items.find(m=>m.key===k)).filter(Boolean).slice(0,10);
const list=show.length>=6?show:items.slice(0,10);
return list.map(m=>{
  const ytdCol=m.ytd_pct>0?'var(--grn)':m.ytd_pct<0?'var(--red)':'var(--t3)';
  return`<div class="nab-item"><div class="nab-name">${esc(m.flag||'')} ${esc(m.name)}</div><div class="nab-price">${fN(m.price,m.key?.includes('TRY')?4:2)}</div><div class="nab-chg" style="color:${cC(m.change_pct)}">${cS(m.change_pct)}%</div>${m.ytd_pct!=null?`<div class="nab-ytd">YTD: <span style="color:${ytdCol};font-weight:700">${cS(m.ytd_pct)}%</span></div>`:''}</div>`;
}).join('');}

// ===== HOME PAGE =====
const _loadMsgs=['bakıyoruz…','bilançoları tarıyoruz…','rasyoları hesaplıyoruz…','momentum kontrol ediliyor…','neredeyse bitti…','son kontroller…'];
function _rndLoad(){return _loadMsgs[Math.floor(Math.random()*_loadMsgs.length)];}

function renderHome(){
const pg=$('pg-home');const d=S.dash;const sc=S.scan;const hr=S.hero;
const hasSc=sc&&sc.items&&sc.items.length>0;const hasD=d&&d.top3&&d.top3.length>0;
let h='';
trackEv('page_view');

// === 1. MARKET STATUS BANNER (kapaliysa) ===
const ms=S.marketStatus;
if(ms&&ms.status!=='open'){
  const bannerCol=ms.status==='closed'?'var(--ylw)':ms.status==='pre_market'?'var(--blu)':'var(--t3)';
  const bannerIcon=ms.status==='closed'?'🏖️':ms.status==='pre_market'?'⏰':'🌙';
  const bannerBg=ms.status==='closed'?'rgba(255,202,40,.06)':'rgba(100,181,246,.06)';
  h+=`<div style="margin-bottom:20px;padding:14px 18px;background:${bannerBg};border:1px solid ${bannerCol}30;border-left:3px solid ${bannerCol};border-radius:0 var(--rad) var(--rad) 0;display:flex;align-items:center;gap:12px;flex-wrap:wrap">`;
  h+=`<span style="font-size:22px">${bannerIcon}</span>`;
  h+=`<div style="flex:1;min-width:200px"><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-base);font-weight:700;color:${bannerCol}">${esc(ms.reason)}</div>`;
  h+=`<div style="font-size:var(--fs-sm);color:var(--t2);margin-top:3px">${esc(ms.reason_detail||'')}${ms.next_open?' · Sonraki açılış: '+ms.next_open:''}${ms.data_age?' · Veri: '+esc(ms.data_age):''}</div></div>`;
  if(ms.global_open){h+=`<span style="font-size:var(--fs-xs);color:var(--blu);font-family:'JetBrains Mono',monospace;white-space:nowrap">🌍 Global piyasalar açık</span>`;}
  h+=`</div>`;
}

// === 2. HERO — İLK EKRAN, EN ÖNEMLI ===
// Piyasa Nabzi — buyuk rakamlarla ilk bakis
h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">📡 Piyasa Nabzı</span><button class="btn btn-sm btn-blu" onclick="goPage('makro')">DETAY →</button></div><div class="card-b"><div class="nab-grid" id="nabGrid">${S.macro?renderNabiz(S.macro.items||[]):'<div class="skel-row"><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div></div><div class="skel-row"><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div><div class="skel" style="height:48px"></div></div>'}</div></div></div>`;
// Günün Sözü — ince sicak motivasyon
if(S.quote){h+=`<div style="margin-bottom:20px;padding:14px 20px;background:linear-gradient(135deg,var(--bg3),var(--bg2));border-left:3px solid var(--gold);border-radius:0 var(--rad) var(--rad) 0"><div style="font-style:italic;color:var(--t2);font-size:var(--fs-base);line-height:1.7">"${esc(S.quote.text)}"</div><div style="margin-top:6px;color:var(--gold);font-weight:600;font-size:var(--fs-sm)">— ${esc(S.quote.author)}</div></div>`;}

// === TÜYO KONTROL — merkez feature ===
h+=`<div class="card" style="margin-bottom:20px;border:1px solid rgba(255,179,0,.25);background:linear-gradient(135deg,var(--bg2),rgba(255,179,0,.03))"><div style="padding:20px 18px"><div style="text-align:center;margin-bottom:16px"><div style="font-size:28px;margin-bottom:8px">🐂</div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);font-weight:700;color:var(--t1);margin-bottom:6px">Hisse yaz, sonucu gör</div><div style="font-size:var(--fs-base);color:var(--t2);line-height:1.6">Bir hisse kodunu yaz — temel güçlü mü, zayıf mı, hemen görelim.</div></div><div style="display:flex;gap:8px;max-width:400px;margin:0 auto"><input type="text" id="tuyoInp" placeholder="Örn: THYAO, ASELS, BIMAS..." autocomplete="off" enterkeyhint="go" style="flex:1;font-family:'JetBrains Mono',monospace;font-size:var(--fs-md);padding:12px 14px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;text-transform:uppercase;min-height:48px"><button onclick="tuyoKontrol()" class="btn btn-grn" style="font-size:var(--fs-md);padding:12px 20px;min-height:48px;white-space:nowrap">Sonucu Gör</button></div><div style="text-align:center;margin-top:10px;font-size:var(--fs-xs);color:var(--t4)">260+ hisse taranıyor · arama ile tüm hisseler analiz edilebilir</div></div></div>`;

// Hero
h+=`<div class="hero-wrap"><div class="hero-tag">📡 BUGÜN NE OLUYOR?</div>`;
if(hr){
  const modeCol=hr.mode_color==='green'?'var(--grn)':hr.mode_color==='red'?'var(--red)':'var(--ylw)';
  h+=`<div style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid var(--bdr)">`;
  h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xl);font-weight:700;color:${modeCol};margin-bottom:10px">📊 ${esc(hr.mode_label)}</div>`;
  h+=`<div style="font-size:var(--fs-base);color:var(--t2);line-height:1.7">${esc(hr.story||'')}</div></div>`;
  h+=`<div class="g2" style="margin-bottom:18px">`;
  // DEĞER LİDERLERİ
  h+=`<div class="hmc opp" style="border-left-color:var(--grn)"><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--grn);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">🏛️ Değer Liderleri</div>`;
  if(hr.deger_leaders&&hr.deger_leaders.length){hr.deger_leaders.forEach((dl,i)=>{h+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;${i<hr.deger_leaders.length-1?'border-bottom:1px solid var(--bdr)':''}"><span class="clk-t" style="font-size:var(--fs-md)" onclick="loadTicker('${esc(dl.ticker)}')">${esc(dl.ticker)}</span><div style="text-align:right"><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);font-weight:700;color:var(--grn)">${dl.deger}</span><span style="font-size:var(--fs-xs);color:var(--t3);margin-left:4px">D</span></div></div>`;});}else{h+=`<div class="hmc-desc" style="color:var(--t3)">Tarama sonrası görünür</div>`;}
  h+=`<div style="font-size:var(--fs-xs);color:var(--t4);margin-top:8px;font-style:italic">Uzun vade · Haftalık güncellenir</div></div>`;
  // KALİTE LİDERLERİ — replaces the old "İvme Liderleri" panel. Radar is
  // pure fundamental after Plan A; we surface a second FA dimension here
  // (quality: ROE, margin stability, ROIC) instead of momentum.
  h+=`<div class="hmc opp" style="border-left-color:var(--blu)"><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--blu);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">⭐ Kalite Liderleri</div>`;
  const _qLead=hr.quality_leaders||hr.ivme_leaders;  // legacy field fallback
  if(_qLead&&_qLead.length){_qLead.forEach((dl,i)=>{h+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;${i<_qLead.length-1?'border-bottom:1px solid var(--bdr)':''}"><span class="clk-t" style="font-size:var(--fs-md)" onclick="loadTicker('${esc(dl.ticker)}')">${esc(dl.ticker)}</span><div style="text-align:right"><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);font-weight:700;color:var(--blu)">${dl.quality!=null?dl.quality.toFixed(0):'—'}</span><span style="font-size:var(--fs-xs);color:var(--t3);margin-left:4px">K</span></div></div>`;});}else{h+=`<div class="hmc-desc" style="color:var(--t3)">Tarama sonrası görünür</div>`;}
  h+=`<div style="font-size:var(--fs-xs);color:var(--t4);margin-top:8px;font-style:italic">ROE · marj stabilitesi · ROIC</div></div>`;
  h+=`</div>`;
  // Risk + Bot + Watch
  h+=`<div class="g3" style="margin-bottom:18px">`;
  h+=`<div class="hmc risk"><div class="hmc-icon">⚠️</div><div class="hmc-label">Dikkat</div>${hr.risk?`<div class="hmc-value"><span class="clk-t" onclick="loadTicker('${esc(hr.risk.ticker)}')">${esc(hr.risk.ticker)}</span> <span style="color:var(--red)">D:${hr.risk.deger||'?'}</span></div><div class="hmc-desc">${esc(hr.risk.reason||'')}</div>`:'<div class="hmc-desc" style="color:var(--t3)">Tarama sonrası</div>'}</div>`;
  h+=`<div class="hmc bot"><div class="hmc-icon">🤖</div><div class="hmc-label">Bot Ne Diyor?</div><div class="hmc-desc">${esc(hr.bot_says||'AI yorumu yükleniyor...')}</div></div>`;
  h+=`<div class="hmc watch"><div class="hmc-icon">👀</div><div class="hmc-label">Takip Et</div><div class="hmc-desc">${(hr.watch||[]).map(w=>`<div style="padding:3px 0;color:var(--t1)">• ${esc(w)}</div>`).join('')||'—'}</div></div>`;
  h+=`</div>`;
  // BUGÜN NE YAPMALI — aksiyon odaklı özet
  if(hasSc){
    const best=sc.items[0];const worst=sc.items[sc.items.length-1];
    // Plan A: removed topIvme spotlight — Radar is fundamental-only now.
    // The "quality leader" is surfaced as a second pick by sorting on the
    // quality dimension instead of momentum.
    const topQ=sc.items.reduce((a,b)=>((b.scores||{}).quality||0)>((a.scores||{}).quality||0)?b:a,sc.items[0]);
    h+=`<div style="margin-bottom:18px;padding:16px;background:linear-gradient(135deg,rgba(255,179,0,.06),rgba(100,181,246,.04));border:1px solid rgba(255,179,0,.2);border-radius:var(--rad)">`;
    h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px">📋 Bugün Ne Yapmalı?</div>`;
    h+=`<div id="homeActionText" style="font-size:var(--fs-base);color:var(--t1);line-height:1.7;margin-bottom:12px"><span style="color:var(--t3)">Makro analiz yükleniyor...</span></div>`;
    h+=`<div style="display:flex;flex-direction:column;gap:10px;font-size:var(--fs-base);line-height:1.6">`;
    h+=`<div style="display:flex;gap:8px;align-items:flex-start"><span style="color:var(--grn);font-weight:700;flex-shrink:0">✓</span><span style="color:var(--t1)"><span class="clk-t" onclick="loadTicker('${esc(best.ticker)}')">${esc(best.ticker)}</span> değer skoru <b style="color:var(--grn)">${(best.deger||best.overall).toFixed(0)}</b> ile en güçlü — takip et</span></div>`;
    if(topQ.ticker!==best.ticker){h+=`<div style="display:flex;gap:8px;align-items:flex-start"><span style="color:var(--blu);font-weight:700;flex-shrink:0">⭐</span><span style="color:var(--t1)"><span class="clk-t" onclick="loadTicker('${esc(topQ.ticker)}')">${esc(topQ.ticker)}</span> kalite skoru <b style="color:var(--blu)">${(((topQ.scores||{}).quality)||50).toFixed(0)}</b> — şirket kalitesi güçlü</span></div>`;}
    h+=`<div style="display:flex;gap:8px;align-items:flex-start"><span style="color:var(--red);font-weight:700;flex-shrink:0">✗</span><span style="color:var(--t2)"><span class="clk-t" onclick="loadTicker('${esc(worst.ticker)}')">${esc(worst.ticker)}</span> değer skoru <b style="color:var(--red)">${(worst.deger||worst.overall).toFixed(0)}</b> — dikkatli ol</span></div>`;
    h+=`</div></div>`;
  }
} else {
  h+=`<div style="font-size:var(--fs-lg);font-weight:700;color:var(--t1);margin-bottom:8px">${hasSc?sc.items.length+' hisseye baktık':'260+ hisse tarıyoruz — biraz sürecek...'}</div>`;
  h+=`<div style="color:var(--t2);font-size:var(--fs-base)">${hasSc?'Sonuçlar hazır.':_rndLoad()}</div>`;
}
h+=`<div id="hSt"></div>`;
h+=`<div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap"><button class="btn btn-grn" id="scanBtn" onclick="startScan()">🔄 YENİLE</button><button class="btn btn-blu" onclick="loadBriefing()">🤖 AI BRİFİNG</button></div>`;
h+=`</div>`;

// === 3. TOP PICKS ===
if(hasD){h+=`<div class="g3" style="margin-bottom:20px"><div class="card"><div class="card-h"><span class="card-t">🏆 Günün 3 Hissesi</span></div><div class="card-b">${d.top3.map(i=>`<div class="pkc"><div style="display:flex;justify-content:space-between;align-items:center"><span class="pkc-tick" onclick="loadTicker('${esc(i.ticker)}')">${esc(i.ticker)}</span><span class="pkc-score" style="color:${sC(i.overall)}">${i.overall}</span></div><div style="font-size:var(--fs-xs);color:var(--t3)">${esc(i.name||'')} · ${esc(i.style)}</div><div class="pkc-reason">${(i.positives||[]).map(p=>esc(p)).join(' · ')}</div></div>`).join('')}</div></div><div class="card"><div class="card-h"><span class="card-t">💡 3 Fırsat</span></div><div class="card-b">${(d.opportunities||[]).map(i=>`<div class="pkc opp"><div style="display:flex;justify-content:space-between"><span class="pkc-tick" onclick="loadTicker('${esc(i.ticker)}')">${esc(i.ticker)}</span><span style="color:var(--ylw);font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm)">${i.overall}/100</span></div><div class="pkc-reason">${esc(i.reason||'')}</div></div>`).join('')||'<p style="color:var(--t3)">Tarama sonrası</p>'}</div></div><div class="card"><div class="card-h"><span class="card-t">⚠️ 3 Risk</span></div><div class="card-b">${(d.risks||[]).map(i=>`<div class="pkc risk"><div style="display:flex;justify-content:space-between"><span class="pkc-tick" onclick="loadTicker('${esc(i.ticker)}')">${esc(i.ticker)}</span><span style="color:var(--red);font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm)">${i.overall}/100</span></div><div class="pkc-reason">${esc(i.reason||'')}</div></div>`).join('')||'<p style="color:var(--t3)">Tarama sonrası</p>'}</div></div></div>`;}

// === 4. PORTFÖY + ARAÇLAR ===
const _pf=getPF();
if(_pf.length&&hasSc){
  let pfTotal=0,pfItems=[];
  _pf.forEach(p=>{const found=(sc?.items||[]).find(i=>i.ticker===p.ticker);const price=found?.price||p.avg;const val=p.lot*price;const cost=p.lot*p.avg;const pnl=val-cost;pfTotal+=pnl;pfItems.push({ticker:p.ticker,pnl,deger:found?.deger||found?.overall||50});});
  const pfBest=pfItems.reduce((a,b)=>b.deger>a.deger?b:a,pfItems[0]);
  const pfWorst=pfItems.reduce((a,b)=>b.deger<a.deger?b:a,pfItems[0]);
  const pfCol=pfTotal>=0?'var(--grn)':'var(--red)';
  h+=`<div class="card" style="margin-bottom:20px;border-left:3px solid ${pfCol}"><div class="card-h"><span class="card-t">📒 Portföyüm</span><button class="btn btn-sm btn-blu" onclick="goPage('portfoy')">DETAY →</button></div><div class="card-b"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t3)">Tahmini K/Z</span><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xl);font-weight:700;color:${pfCol}">${pfTotal>=0?'+':''}${fN(pfTotal)} TL</div></div><div style="text-align:right"><div style="font-size:var(--fs-xs);color:var(--t3)">${_pf.length} hisse</div></div></div><div style="display:flex;gap:8px;font-size:var(--fs-sm);flex-wrap:wrap"><span style="color:var(--grn)">En güçlü: <span class="clk-t" onclick="loadTicker('${esc(pfBest.ticker)}')">${esc(pfBest.ticker)}</span> (D:${pfBest.deger.toFixed(0)})</span><span style="color:var(--t4)">·</span><span style="color:var(--red)">Dikkat: <span class="clk-t" onclick="loadTicker('${esc(pfWorst.ticker)}')">${esc(pfWorst.ticker)}</span> (D:${pfWorst.deger.toFixed(0)})</span></div></div></div>`;
}
h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">⚡ Son Sinyaller</span><button class="btn btn-sm btn-orn" onclick="goPage('bullalfa');setTimeout(()=>BullAlfa&&BullAlfa._setMode&&BullAlfa._setMode('__SIGNALS__'),50)">TÜMÜ →</button></div><div class="card-b">${S.cross&&S.cross.signals&&S.cross.signals.length?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px">${S.cross.signals.filter(s=>(s.stars||1)>=2).slice(0,6).map(s=>{const sq=s.signal_quality||'C';const sqCls=sq==='A'?'qb-a':sq==='B'?'qb-b':'qb-c';const sigCol=s.signal_type==='bullish'?'bull':s.signal_type==='bearish'?'bear':'';return`<div class="sigc ${sigCol}" style="padding:10px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span class="clk-t" style="font-size:var(--fs-sm);font-weight:700" onclick="loadTicker('${esc(s.ticker)}')">${esc(s.ticker)}</span><span class="qb ${sqCls}">${sq}</span></div><div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t2)">${esc(s.signal)}</div>${s.reason&&s.reason.length?`<div style="font-size:9px;color:var(--grn);margin-top:3px">${s.reason.slice(0,1).map(r=>'✓ '+esc(r)).join('')}</div>`:''}</div>`;}).join('')}</div>`:'<p style="color:var(--t3);font-size:var(--fs-sm)">Sinyaller → Cross sayfasından tarayın</p>'}</div></div>`;
h+=`<div class="g2" style="margin-bottom:20px"><div class="card"><div class="card-h"><span class="card-t">⭐ Takip Listem</span><button class="btn btn-sm btn-blu" onclick="loadWatchlistEnriched()" title="Zengin veri yükle">🔄</button></div><div class="card-b" id="wlPanel">${S.wl.length?S.wl.map(t=>`<div class="wl-item"><span class="wl-tick" onclick="loadTicker('${esc(t)}')">${esc(t)}</span><button class="wl-rm" onclick="wlRm('${esc(t)}');renderHome()">✕</button></div>`).join(''):'<p style="color:var(--t3);font-size:var(--fs-sm)">Hisse detayında ⭐ ile ekleyin</p>'}</div></div><div class="card"><div class="card-h"><span class="card-t">🔔 Uyarılar</span><button class="btn btn-sm btn-orn" onclick="refreshAlerts()">YENİLE</button></div><div class="card-b" id="alertPanel">${S._alerts&&S._alerts.length?S._alerts.slice(0,5).map(a=>{const sc=a.severity==='high'?'var(--red)':a.severity==='warning'?'var(--ylw)':'var(--blu)';return`<div style="padding:5px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs)"><div style="display:flex;justify-content:space-between"><span style="color:${sc};font-weight:600" onclick="loadTicker('${esc(a.symbol)}')" class="clk-t">${esc(a.title)}</span></div><div style="color:var(--t3);font-size:10px;margin-top:1px">${esc(a.message||'')}</div></div>`;}).join(''):'<p style="color:var(--t3);font-size:var(--fs-sm)">Henüz uyarı yok</p>'}</div></div></div>`;

// === 5. TOP 10 TABLE ===
if(hasSc){h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">🏛️ Top 10 — Saf Değerleme Radarı</span><button class="btn btn-sm btn-grn" onclick="goPage('radar')">TÜMÜ →</button></div><div style="padding:8px 16px;background:var(--bg3);border-bottom:1px solid var(--bdr);font-size:var(--fs-xs);color:var(--t3)">Uzun vadeli değer tarayıcı — Değerleme, Kalite, Bilanço, Büyüme</div><div class="card-b" style="overflow-x:auto">${renderRadarTbl(sc.items.slice(0,10),S._radarSort||'deger')}</div></div>`;}

// === 6. SEKTÖR + HEATMAP + ALPHA QUADRANT ===
h+=`<div class="g2" style="margin-bottom:20px"><div class="card"><div class="card-h"><span class="card-t">🏢 Sektör Güç Haritası</span></div><div class="card-b">${hr&&hr.strong_sectors?renderSectorCards(hr):(hasD&&d.sectors?renderSectors(d.sectors):'<div class="skel" style="height:80px"></div>')}</div></div><div class="card"><div class="card-h" style="display:flex;justify-content:space-between;align-items:center"><div style="display:flex;gap:4px"><button class="btn btn-sm ${S._vizTab!=='alpha'?'btn-orn':'btn-dim'}" onclick="S._vizTab='heat';renderHome()">🔥 Isı Haritası</button><button class="btn btn-sm ${S._vizTab==='alpha'?'btn-blu':'btn-dim'}" onclick="S._vizTab='alpha';renderHome()">🎯 Alpha Quadrant</button></div><button class="btn btn-sm btn-orn" onclick="S._heatLoaded=false;loadHeatmap().then(()=>renderHome())">🔄</button></div><div class="card-b">${S._vizTab==='alpha'?(S.alphaHtml||'<div class="skel" style="height:80px"></div>'):(S.heatmapHtml||'<div class="skel" style="height:80px"></div>')}</div></div></div>`;

// === 7. TRUST LINE ===
h+=`<div style="margin-bottom:20px;padding:16px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);text-align:center"><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);color:var(--t2);line-height:1.8">Gizli model yok · Aynı veri → aynı sonuç · Komisyonumuz yok<br><span style="color:var(--t4);font-size:var(--fs-xs)">Al desek de alma desek de bize giren çıkan yok.</span></div></div>`;

// === 8. KEŞFET — Nasıl Çalışır (dismissable) + Kitap + Söz ===
const infoDismissed=localStorage.getItem('bb_info_dismissed');
if(!infoDismissed){
h+=`<div class="card" style="margin-bottom:20px;position:relative" id="infoCard"><button onclick="localStorage.setItem('bb_info_dismissed','1');document.getElementById('infoCard').remove()" style="position:absolute;top:10px;right:10px;background:var(--bg4);border:1px solid var(--bdr);border-radius:50%;width:28px;height:28px;color:var(--t3);cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center">✕</button><div class="card-h"><span class="card-t">🐂 BistBull — Saf Temel Analiz Radarı</span></div><div class="card-b" style="font-size:var(--fs-base);color:var(--t2);line-height:1.7"><b style="color:var(--grn)">7 boyutlu temel analiz</b> — her hisse <b>Değerleme, Kalite, Büyüme, Bilanço, Kâr Kalitesi, Rekabet Avantajı</b> ve <b>Sermaye Kullanımı</b> boyutlarında puanlanır (Piotroski, Altman, Beneish, Graham, Buffett modelleriyle). Sonuç tek bir <span style="color:var(--grn)">radar skoru</span> (1-99) ve kalite notu: <b>Çok Başarılı / Başarılı / Orta / Zayıf / Riskli</b>. Türkiye filtresi (döviz, faiz, enflasyon muhasebesi) skora dahildir. <b style="color:var(--blu)">622 hisselik</b> tüm BIST evreni günde bir taranır. <b style="color:var(--prp)">Otomatik yatırım tezi</b> ve <b style="color:var(--prp)">Q asistanı</b> ile profesyonel analiz herkesin erişiminde.</div></div>`;
}
// Kitap + Söz (keşfet alanı — alt)
h+=`<div class="g2" style="margin-bottom:20px">`;
if(S.book){h+=`<div class="card"><div class="card-h"><span class="card-t">📚 Günün Kitabı</span></div><div class="card-b"><div style="display:flex;gap:14px;align-items:flex-start"><div style="font-size:32px;flex-shrink:0">📖</div><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-md);font-weight:700;color:var(--blu);margin-bottom:4px">${esc(S.book.title)}</div><div style="font-size:var(--fs-sm);color:var(--gold);margin-bottom:6px">${esc(S.book.author)}</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.6">${esc(S.book.description)}</div></div></div></div></div>`;}else{h+=`<div></div>`;}
if(S.quote){h+=`<div class="card"><div class="card-h"><span class="card-t">💬 Günün Sözü</span></div><div class="card-b"><div style="font-style:italic;color:var(--t2);font-size:var(--fs-base);line-height:1.7">"${esc(S.quote.text)}"</div><div style="margin-top:8px;color:var(--gold);font-weight:600;font-size:var(--fs-sm)">— ${esc(S.quote.author)}</div></div></div>`;}else{h+=`<div></div>`;}
h+=`</div>`;

// === 9. SON BAKTIKLARINIZ + SISTEM ===
h+=`<div class="g2" style="margin-bottom:20px">`;
h+=`<div class="card"><div class="card-h"><span class="card-t">🕐 Son Baktıklarınız</span></div><div class="card-b">${S.seen.length?S.seen.map(t=>`<span class="ls-item" onclick="loadTicker('${esc(t)}')">${esc(t)}</span>`).join(''):'<p style="color:var(--t3);font-size:var(--fs-sm)">Henüz hisse incelemediniz</p>'}</div></div>`;
const ls=S.liveStats||{};
h+=`<div class="card"><div class="card-h"><span class="card-t">📡 Sistem</span></div><div class="card-b"><div class="g4"><div class="live-stat"><div class="live-num">${ls.scans_done||'—'}</div><div class="live-label">Analiz</div></div><div class="live-stat"><div class="live-num">${ls.signals_total||'—'}</div><div class="live-label">Sinyal</div></div><div class="live-stat"><div class="live-num">${ls.macro_tracked||'—'}</div><div class="live-label">Makro</div></div><div class="live-stat"><div class="live-num">${ls.uptime_hours||'—'}</div><div class="live-label">Uptime</div></div></div></div></div>`;
h+=`</div>`;

pg.innerHTML=h;
if(!S.macro)loadMacro();
if(S.wl.length>0)setTimeout(()=>loadWatchlistEnriched(),200);
}

// ===== SECTOR CARDS =====
function renderSectorCards(hr){
  let h='';
  if(hr.strong_sectors&&hr.strong_sectors.length){h+=`<div style="margin-bottom:8px;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3);text-transform:uppercase">Güçlü Sektörler</div>`;hr.strong_sectors.forEach(s=>{h+=`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--bdr)"><span style="color:var(--t1);font-size:12px">${esc(s.name)}</span><span style="color:var(--grn);font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700">${s.score}</span></div>`;});}
  if(hr.weak_sectors&&hr.weak_sectors.length){h+=`<div style="margin-top:10px;margin-bottom:8px;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3);text-transform:uppercase">Zayıf Sektörler</div>`;hr.weak_sectors.forEach(s=>{h+=`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--bdr)"><span style="color:var(--t1);font-size:12px">${esc(s.name)}</span><span style="color:var(--red);font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700">${s.score}</span></div>`;});}
  return h||'<p style="color:var(--t3);font-size:12px">Tarama sonrası</p>';
}
function renderSectors(secs){if(!secs||!secs.length)return'';const mx=Math.max(...secs.map(s=>s.avg_score||1));return`<div style="display:flex;align-items:flex-end;gap:6px;height:100px;padding-bottom:20px;position:relative">${secs.slice(0,8).map(s=>{const ht=Math.max(10,((s.avg_score||0)/mx)*90),c=sC(s.avg_score);return`<div style="flex:1;height:${ht}%;background:${c};border-radius:3px 3px 0 0;min-width:30px;position:relative;cursor:pointer" title="${esc(s.sector)}: ${s.avg_score}"><div style="position:absolute;top:-14px;left:50%;transform:translateX(-50%);font-size:9px;font-weight:700;font-family:'JetBrains Mono',monospace;color:${c}">${s.avg_score?.toFixed(0)||'?'}</div><div style="position:absolute;bottom:-18px;left:50%;transform:translateX(-50%);font-size:7px;color:var(--t4);white-space:nowrap;font-family:'JetBrains Mono',monospace">${esc((s.sector||'?').slice(0,6))}</div></div>`;}).join('')}</div>`;}
function renderMacMini(items){const show=items.filter(m=>['turkiye','emtia'].includes(m.category)).slice(0,8);if(!show.length)return'<p style="color:var(--t3)">Veri yok</p>';return show.map(m=>`<div class="mac"><div class="mac-s">${esc(m.flag||'')} ${esc(m.name)}</div><div class="mac-p">${fN(m.price,m.key?.includes('TRY')?4:2)}</div><div class="mac-c" style="color:${cC(m.change_pct)}">${cS(m.change_pct)}%</div></div>`).join('');}

// ===== LOADERS =====
async function loadQuote(){try{S.quote=await api('/api/quote');}catch(e){}}
async function loadLiveStats(){try{S.liveStats=await api('/api/live/stats');}catch(e){}}
async function loadWatchlistEnriched(){const el=$('wlPanel');if(!el)return;el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Yükleniyor...</p>';try{const d=await api('/api/watchlist');if(d.items&&d.items.length){el.innerHTML=d.items.map(it=>{const sc=it.overall!=null?`<span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${it.overall>=60?'var(--grn)':it.overall>=45?'var(--ylw)':'var(--red)'}">${Math.round(it.overall)}</span>`:'';const sigs=it.signals&&it.signals.length?it.signals.slice(0,2).map(s=>{const sq=s.signal_quality||'C';const sqC=sq==='A'?'var(--grn)':sq==='B'?'var(--ylw)':'var(--t4)';return`<span style="font-family:'JetBrains Mono',monospace;font-size:9px;padding:1px 4px;border-radius:2px;background:${sqC}20;color:${sqC}">${sq}</span>`;}).join(''):'';return`<div class="wl-item"><span class="wl-tick" onclick="loadTicker('${esc(it.symbol)}')">${esc(it.symbol)} ${sc} ${sigs}</span><button class="wl-rm" onclick="wlRm('${esc(it.symbol)}');renderHome()">✕</button></div>`;}).join('');}else{el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Watchlist boş</p>';}}catch(e){el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Yüklenemedi</p>';}}
async function refreshAlerts(){const el=$('alertPanel');if(!el)return;el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Kontrol ediliyor...</p>';try{await api('/api/alerts/refresh',{method:'POST'});const d=await api('/api/alerts');S._alerts=d.alerts||[];const items=S._alerts.slice(0,5);if(items.length){el.innerHTML=items.map(a=>{const sc=a.severity==='high'?'var(--red)':a.severity==='warning'?'var(--ylw)':'var(--blu)';return`<div style="padding:5px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs)"><div style="display:flex;justify-content:space-between"><span style="color:${sc};font-weight:600;cursor:pointer" onclick="loadTicker('${esc(a.symbol)}')">${esc(a.title)}</span></div><div style="color:var(--t3);font-size:10px;margin-top:1px">${esc(a.message||'')}</div></div>`;}).join('');}else{el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Yeni uyarı yok</p>';}}catch(e){el.innerHTML='<p style="color:var(--t3);font-size:var(--fs-sm)">Hata</p>';}}
async function loadBook(){try{S.book=await api('/api/book');}catch(e){}}
async function loadMarketStatus(){try{S.marketStatus=await api('/api/market-status');}catch(e){}}
async function loadMacro(){try{S.macro=await cachedApi('/api/macro');const el=$('macMini');if(el)el.innerHTML=renderMacMini(S.macro.items||[]);const ng=$('nabGrid');if(ng)ng.innerHTML=renderNabiz(S.macro.items||[]);renderTickerBar(S.macro.items||[]);loadHomeAction();}catch(e){console.error('macro:',e);}}
function renderTickerBar(items){const tb=$('tbar');const inner=items.map(m=>`<div class="tbar-i"><span style="color:var(--t2);font-weight:600">${esc(m.flag||'')} ${esc(m.key||m.name)}</span><span style="color:var(--t1)">${fN(m.price,m.key?.includes('TRY')?4:2)}</span><span style="color:${cC(m.change_pct)};font-size:10px">${cS(m.change_pct)}%</span></div>`).join('');tb.innerHTML=`<div class="tbar-inner">${inner}${inner}</div>`;}

// ===== VIOP (options + futures, UOA + Tahtacı overlay) =====
// 3 view: overlay (killer) / uoa / today (raw snapshot)
// Default view picker: if no overlay+uoa hits yet (baseline doluyor),
// land on 'today' so the user sees the 200+ contract snapshot
// immediately instead of an empty state.
async function loadViop(force){
  const view = S._viopView || 'overlay';
  const cacheKey = '_viop_' + view;
  if (S[cacheKey] && !force) return S[cacheKey];

  let url;
  if (view === 'overlay') {
    url = '/api/viop/tahtaci-overlay?min_uoa_score=1.5&kap_window_days=14&limit=40';
  } else if (view === 'uoa') {
    const minS = S._viopMinScore || 2.0;
    const kind = S._viopKind || '';
    url = `/api/viop/uoa?min_score=${minS}&include_tentative=false&limit=60${kind?'&kind='+kind:''}`;
  } else {
    const kind = S._viopKind || '';
    url = `/api/viop/today?limit=80${kind?'&kind='+kind:''}`;
  }
  let payload;
  try {
    const [main, summary, health] = await Promise.all([
      api(url).catch(e => ({_err: String(e.message||e)})),
      api('/api/viop/tahtaci-overlay/summary').catch(()=>null),
      api('/api/viop/health').catch(()=>null),
    ]);
    const v = (main && (main.value || main)) || {};
    const s = (summary && (summary.value || summary)) || {};
    const h = (health && (health.value || health)) || {};
    payload = {
      items: v.items || [],
      summary: s,
      health: h,
      view, fetched_at: Date.now(),
      error: main && main._err ? main._err : null,
    };
  } catch(e) {
    console.warn('viop fetch failed', e);
    payload = { items: [], summary: {}, health: {}, view, error: String(e.message||e) };
  }
  // ALWAYS write to cache — even on error — so renderViopPage doesn't
  // see `!S[cacheKey]` and loop back into the skeleton-then-loadViop cycle.
  S[cacheKey] = payload;
  return payload;
}

// Manual refresh — POST /api/viop/refresh + reload current view.
async function viopManualRefresh(btn){
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Snapshot alınıyor…'; }
  try {
    const r = await fetch('/api/viop/refresh', {method: 'POST'});
    const j = await r.json();
    const v = j.value || j;
    const cyc = v.cycle || {};
    // Drop all caches so all 3 views re-render with fresh data
    S._viop_overlay = null; S._viop_uoa = null; S._viop_today = null;
    if (btn) {
      btn.textContent = `✓ ${cyc.rows_persisted||0} contract`;
      setTimeout(() => { btn.disabled = false; btn.textContent = '🔄 Snapshot Al'; }, 2500);
    }
    renderViopPage();
  } catch(e) {
    if (btn) {
      btn.textContent = `✗ ${String(e.message||e).slice(0,30)}`;
      setTimeout(() => { btn.disabled = false; btn.textContent = '🔄 Snapshot Al'; }, 3000);
    }
  }
}

// Baseline progress — UOA needs ≥5 days. Picks the most-snapshotted
// contract to estimate where we are in the warmup window.
function _viopBaselineProgress(health){
  const last = (health && health.last_cycle) || null;
  const stats = (health && health.stats) || {};
  const today = stats.snap_date_latest;
  if (!today) return null;
  // We don't have a "days of history" counter on the health endpoint
  // yet; until then, infer from snap_date_latest existence. This is a
  // floor — server-side could provide an exact number later.
  return {
    snap_today: today,
    contracts_today: stats.total_today || 0,
    last_cycle_ok: last && (last.rows_persisted || 0) > 0,
    last_cycle_at: last ? last.finished_at : null,
  };
}

function _viopBadge(label, n, col) {
  return `<span style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:var(--rad);background:${col}15;color:${col};font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700">${esc(label)} <b style="margin-left:2px">${n}</b></span>`;
}

function _viopOverlayRow(it, i, total) {
  const u = it.uoa || {};
  const ov = it.overlay || {};
  const sigs = ov.signals || [];
  const score = (ov.overlay_score || 0).toFixed(1);
  const uoaScore = (u.score || 0).toFixed(1);
  const ratio = u.ratio ? `${u.ratio.toFixed(1)}×` : '—';
  const code = it.code || '';
  const contract = it.contract || '';
  const underlying = it.underlying || '?';
  const sideCol = it.side === 'C' ? 'var(--grn)' : it.side === 'P' ? 'var(--red)' : 'var(--cyn)';
  const sideTxt = it.side === 'C' ? 'CALL' : it.side === 'P' ? 'PUT' : 'FUT';
  const strike = it.strike != null ? `@ ${it.strike}` : '';
  // KAP signal pills
  const sigPills = sigs.map(s => {
    const tagLabel = {
      INSIDER: '🚨 INSIDER', KAP_ALERT: '⚠️ KAP', MNA: '🤝 M&A',
      BUYBACK: '💰 BUYBACK', CAPITAL_CHANGE: '📈 CAPITAL',
      MGMT_CHANGE: '👤 MGMT',
    }[s.tag] || s.tag;
    return `<span style="display:inline-flex;align-items:center;font-family:'JetBrains Mono',monospace;font-size:9px;padding:1px 6px;background:rgba(239,83,80,.12);color:var(--red);border:1px solid rgba(239,83,80,.3);border-radius:3px" title="${esc(s.subject||'')} · ${s.age_days||0}g önce">${esc(tagLabel)} · ${(s.age_days||0).toFixed(0)}g</span>`;
  }).join(' ');
  return `<div style="padding:11px 14px;${i<total-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer;transition:background .1s" onclick="loadTicker('${esc(underlying)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px">
          <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--cyn)">${esc(underlying)}</span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${sideCol};font-weight:700;padding:1px 6px;background:${sideCol}15;border-radius:3px">${esc(sideTxt)} ${esc(strike)}</span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3)">${esc(it.expiry||'')}</span>
        </div>
        <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.4">${esc(contract)}</div>
        ${sigs.length ? `<div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">${sigPills}</div>` : ''}
      </div>
      <div style="text-align:right;font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;flex-shrink:0">
        <div style="font-size:18px;font-weight:700;color:var(--gold)">${score}</div>
        <div style="color:var(--t3)">UOA z=${uoaScore} · ${esc(ratio)}</div>
        <div style="color:var(--t4);margin-top:2px">vol ${u.today_tl ? (u.today_tl/1e6).toFixed(1)+'M' : '—'}</div>
      </div>
    </div>
  </div>`;
}

function _viopUoaRow(it, i, total) {
  const u = it.uoa || {};
  const score = (u.score || 0).toFixed(1);
  const ratio = u.ratio ? `${u.ratio.toFixed(1)}×` : '—';
  const underlying = it.underlying || '?';
  const sideCol = it.side === 'C' ? 'var(--grn)' : it.side === 'P' ? 'var(--red)' : 'var(--cyn)';
  const sideTxt = it.side === 'C' ? 'CALL' : it.side === 'P' ? 'PUT' : 'FUT';
  const strike = it.strike != null ? `@ ${it.strike}` : '';
  return `<div style="padding:10px 14px;${i<total-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer" onclick="loadTicker('${esc(underlying)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:2px">
          <span style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:var(--cyn)">${esc(underlying)}</span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${sideCol};font-weight:700;padding:1px 5px;background:${sideCol}15;border-radius:3px">${esc(sideTxt)} ${esc(strike)}</span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--t4)">${esc(it.expiry||'')} · ${esc(it.code)}</span>
        </div>
      </div>
      <div style="text-align:right;font-family:'JetBrains Mono',monospace;flex-shrink:0">
        <div style="font-size:16px;font-weight:700;color:var(--orn)">z=${score}</div>
        <div style="font-size:10px;color:var(--t3)">${esc(ratio)} normalin · vol ${u.today_tl ? (u.today_tl/1e6).toFixed(1)+'M' : '—'}</div>
        <div style="font-size:9.5px;color:var(--t4)">avg ${u.baseline_avg_tl ? (u.baseline_avg_tl/1e6).toFixed(2)+'M' : '—'} · ${u.baseline_days||0}g</div>
      </div>
    </div>
  </div>`;
}

function _viopTodayRow(it, i, total) {
  const underlying = it.underlying || '?';
  const sideCol = it.side === 'C' ? 'var(--grn)' : it.side === 'P' ? 'var(--red)' : 'var(--cyn)';
  const sideTxt = it.side === 'C' ? 'CALL' : it.side === 'P' ? 'PUT' : 'FUT';
  const strike = it.strike != null ? `@ ${it.strike}` : '';
  const ch = it.change || 0;
  const chCol = ch > 0 ? 'var(--grn)' : ch < 0 ? 'var(--red)' : 'var(--t3)';
  const chSign = ch > 0 ? '+' : '';
  return `<div style="padding:8px 14px;${i<total-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer" onclick="loadTicker('${esc(underlying)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px">
      <div style="flex:1;min-width:0;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--cyn)">${esc(underlying)}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:${sideCol};padding:1px 4px;background:${sideCol}15;border-radius:3px">${esc(sideTxt)} ${esc(strike)}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--t4)">${esc(it.expiry||'')}</span>
      </div>
      <div style="text-align:right;font-family:'JetBrains Mono',monospace;flex-shrink:0">
        <span style="color:var(--t1)">${it.price != null ? it.price.toFixed(2) : '—'}</span>
        <span style="color:${chCol};margin-left:6px;font-weight:700">${chSign}${ch.toFixed(2)}</span>
        <span style="color:var(--t3);margin-left:6px;font-size:10px">${it.volume_tl ? (it.volume_tl/1e6).toFixed(1)+'M TL' : '—'}</span>
      </div>
    </div>
  </div>`;
}

function renderViopPage(){
  const pg = $('pg-viop');
  const view = S._viopView || 'overlay';
  if (!S['_viop_'+view]) {
    pg.innerHTML = _skelHeader('🎲 VIOP — yükleniyor…') + _skelList(8);
    loadViop().then(() => renderViopPage());
    return;
  }
  const data = S['_viop_'+view];
  const items = data.items || [];
  const summary = data.summary || {};
  const health = data.health || {};
  const progress = _viopBaselineProgress(health);
  // If user is on overlay/uoa view and it's empty BUT today snapshot
  // exists, auto-flip to today view so they see SOMETHING immediately.
  // Only do this on the FIRST encounter (track via flag to not undo
  // user's deliberate switch back).
  if ((view === 'overlay' || view === 'uoa')
      && items.length === 0
      && !S._viopAutoFlipped
      && progress && progress.contracts_today > 0) {
    S._viopAutoFlipped = true;
    S._viopView = 'today';
    renderViopPage();
    return;
  }

  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--gold)">🎲 VIOP — Tahtacı + UOA</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Option flow + insider sinyalleri overlap'i · ${(health.stats||{}).total_today||0} contract takip ediliyor</p>
    </div>
    <div style="display:flex;gap:6px">
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2);font-size:11px" onclick="viopManualRefresh(this)">🔄 Snapshot Al</button>
      <button class="btn btn-grn" onclick="S['_viop_'+(S._viopView||'overlay')]=null;loadViop(true).then(()=>renderViopPage())">↻</button>
    </div>
  </div>`;

  // Always-on baseline progress banner — explains the wait.
  if (progress) {
    const fc = progress.last_cycle_at
      ? new Date(progress.last_cycle_at * 1000).toLocaleString('tr-TR', {hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short'})
      : '—';
    h += `<div style="margin-bottom:14px;padding:10px 14px;background:var(--bg3);border:1px solid var(--bdr);border-left:3px solid var(--cyn);border-radius:0 var(--rad) var(--rad) 0;font-size:11px;color:var(--t2);line-height:1.6">
      <b style="color:var(--cyn)">📡 Pipeline durumu:</b> Son snapshot ${esc(fc)} · ${progress.contracts_today} contract bugün. UOA z-score için <b>≥5 gün baseline gerekiyor</b> — sistem her saat snapshot alıyor, baseline dolunca Overlay + UOA view'leri kendiliğinden dolar. Şu an raw snapshot için <b>📋 Tüm Snapshot</b> view'ine bak.
    </div>`;
  }

  // Summary banner (only for overlay view)
  if (view === 'overlay' && summary && (summary.n_overlays || 0) > 0) {
    h += `<div style="margin-bottom:14px;padding:12px 14px;background:linear-gradient(135deg,rgba(255,179,0,.12),rgba(255,179,0,.04));border:1px solid var(--gold);border-radius:var(--rad)">
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:var(--fs-sm);color:var(--t2)">
        <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--gold);text-transform:uppercase;letter-spacing:.5px;font-weight:700">🔥 DOUBLE SMART MONEY</span>
        ${_viopBadge('Overlaps', summary.n_overlays||0, 'var(--gold)')}
        ${_viopBadge('Hisse', summary.unique_underlyings||0, 'var(--cyn)')}
        ${_viopBadge('Top z×kap', (summary.top_score||0).toFixed(1), 'var(--grn)')}
      </div>
    </div>`;
  }

  // View tabs. Setting _viopAutoFlipped=true so a deliberate user
  // switch isn't immediately reversed by the auto-flip in render.
  const tabs = [
    ['overlay', '🔥 Overlay (Tahtacı + UOA)', 'var(--gold)'],
    ['uoa', '⚡ UOA (saf z-score)', 'var(--orn)'],
    ['today', '📋 Tüm Snapshot', 'var(--cyn)'],
  ];
  h += `<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">${tabs.map(([k,l,c])=>{
    const on = view === k;
    return `<button class="btn btn-sm" style="${on?`background:${c}20;border:1px solid ${c};color:${c}`:'background:var(--bg3);color:var(--t2)'};font-size:11px" onclick="S._viopAutoFlipped=true;S._viopView='${k}';loadViop().then(()=>renderViopPage())">${esc(l)}</button>`;
  }).join('')}</div>`;

  // Optional kind filter for uoa/today
  if (view !== 'overlay') {
    const curK = S._viopKind || '';
    const kindChips = [['', 'Tümü'], ['option', '🎯 Opsiyon'], ['future', '📊 Vadeli']];
    h += `<div style="display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap;font-size:10px">${kindChips.map(([k,l])=>{
      const on = curK === k;
      return `<button class="btn btn-sm" style="${on?'background:var(--bg4);color:var(--t1);border:1px solid var(--t3)':'background:var(--bg3);color:var(--t3)'};font-size:10px;padding:3px 8px;min-height:26px" onclick="S._viopKind='${k}';S['_viop_'+(S._viopView||'overlay')]=null;loadViop(true).then(()=>renderViopPage())">${esc(l)}</button>`;
    }).join('')}</div>`;
  }

  // Explainer banner
  const explainer = {
    overlay: '🔥 <b style="color:var(--gold)">Double smart money:</b> aynı hissede son 14g KAP operator signal (insider/M&A/buyback...) + bugün option/futures UOA z-score birleşimi. Overlay = UOA × (1 + KAP strength). En nadir ve en güçlü sinyal.',
    uoa: '⚡ <b style="color:var(--orn)">Unusual Options Activity:</b> bugünkü hacim, contract\'ın 30g rolling baseline\'ına karşı z-score. ≥2.0 = ~95. percentile anomaly. Stdev floor ile dead-flat baseline\'lar normalize.',
    today: '📋 <b style="color:var(--cyn)">Tüm snapshot:</b> bugünkü tüm VIOP contract\'ları, hacim büyüğünden küçüğe. UOA hesaplanmadan ham veri görmek için.',
  }[view] || '';
  h += `<div style="padding:10px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:12px;font-size:11px;color:var(--t2);line-height:1.55">${explainer}</div>`;

  if (!items.length) {
    if (view === 'today') {
      // 'today' empty == real problem: feed loop hasn't fetched yet
      h += `<div class="emp" style="padding:30px 20px;text-align:center"><h3 style="color:var(--t2);font-size:14px;margin-bottom:8px">📡 Snapshot henüz alınmadı</h3>
        <p style="color:var(--t4);font-size:11px;line-height:1.6;margin-bottom:14px">Background feed loop ilk fetch'i yapana kadar boş gelir. Sağ üstteki <b>"🔄 Snapshot Al"</b> butonu ile manuel tetikleyebilirsin (~5 saniye sürer).</p>
        <button class="btn btn-grn" onclick="viopManualRefresh(this)">🔄 Şimdi Snapshot Al</button></div>`;
    } else {
      // overlay / uoa empty when today has data → baseline waiting
      h += `<div class="emp" style="padding:30px 20px;text-align:center"><h3 style="color:var(--t2);font-size:14px;margin-bottom:8px">⏳ Baseline biriyor</h3>
        <p style="color:var(--t4);font-size:11px;line-height:1.6;margin-bottom:10px">${view==='overlay' ? 'Tahtacı × UOA overlap' : 'UOA z-score anomaly'} için ≥5 gün snapshot baseline gerekiyor. Sistem her saat snapshot alıyor.</p>
        <p style="color:var(--t4);font-size:11px;line-height:1.6">Şu an raw VIOP universe'ünü görmek için → <button class="btn btn-sm" style="background:var(--bg3);color:var(--cyn);font-size:11px" onclick="S._viopAutoFlipped=true;S._viopView='today';loadViop().then(()=>renderViopPage())">📋 Tüm Snapshot</button></p></div>`;
    }
    pg.innerHTML = h;
    return;
  }

  h += '<div class="card"><div class="card-b" style="padding:0">';
  const rowFn = view === 'overlay' ? _viopOverlayRow : view === 'uoa' ? _viopUoaRow : _viopTodayRow;
  items.forEach((it, i) => { h += rowFn(it, i, items.length); });
  h += '</div></div>';

  // Help footer
  if (view === 'overlay') {
    h += `<div style="margin-top:14px;padding:10px 14px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t3);line-height:1.6">
      💡 <b style="color:var(--t2)">Skor okuması:</b> büyük altın rakam = overlay (UOA × KAP boost). Sağında "UOA z=X" raw z-score, "Nx normalin" ratio. INSIDER pill'i en güçlü tag (1.0×), BUYBACK 0.55×, MGMT_CHANGE 0.35×. Yaşlandıkça (14g pencere) decay olur.
    </div>`;
  }

  pg.innerHTML = h;
}

// ===== TANI / DIAGNOSTIC PAGE =====
// Kullanıcı "BullWatch dönüyor birşey gelmiyor", "+ Aldım kaybettim" gibi
// problemleri self-debug edebilsin. /api/diag/system tüm critical state'i
// tek bir endpointte topluyor.
async function loadDiag(){
  try {
    const r = await api('/api/diag/system');
    S.diagSystem = (r && (r.value || r)) || {};
  } catch(e) {
    S.diagSystem = { error: String(e.message||e) };
  }
}

async function forceBwReset(btn){
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Sıfırlanıyor…'; }
  try {
    const r = await fetch('/api/diag/bullwatch/force-reset', {method:'POST'});
    const j = await r.json();
    alert(`BullWatch scan reset: was_running=${j.was_running}, reset=${j.reset}`);
    S.diagSystem = null;
    renderDiagPage();
  } catch(e) {
    alert('Hata: ' + (e.message || e));
    if (btn) { btn.disabled = false; btn.textContent = '🛑 BullWatch Scan Reset'; }
  }
}

function renderDiagPage(){
  const pg = $('pg-diag');
  if (!S.diagSystem) {
    pg.innerHTML = '<div class="ld"><div class="sp"></div><div class="ld-t">Sistem durumu kontrol ediliyor…</div></div>';
    loadDiag().then(() => renderDiagPage());
    return;
  }
  const d = S.diagSystem;
  if (d.error) {
    pg.innerHTML = `<div class="emp"><h3 style="color:var(--red)">Tanı yüklenemedi: ${esc(d.error)}</h3></div>`;
    return;
  }
  const bw = d.bullwatch || {};
  const kap = d.kap || {};
  const pf = d.portfolio || {};
  const viop = d.viop || {};
  const ar = d.auto_refresh || {};

  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--cyn)">🔧 Sistem Tanı</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Critical state'lerin tek bakışta sağlık raporu.</p>
    </div>
    <button class="btn btn-grn" onclick="S.diagSystem=null;loadDiag().then(()=>renderDiagPage())">🔄 Yenile</button>
  </div>`;

  // BullWatch state
  const bwCol = bw.hung ? 'var(--red)' : bw.scan_running ? 'var(--ylw)' : bw.cache_populated ? 'var(--grn)' : 'var(--t3)';
  const bwIcon = bw.hung ? '✕' : bw.scan_running ? '⏳' : bw.cache_populated ? '✓' : '?';
  const bwLbl = bw.hung ? 'SCAN SIKIŞTI' : bw.scan_running ? 'SCAN ÇALIŞIYOR' : bw.cache_populated ? 'HAZIR' : 'BOŞ';
  h += `<div class="card" style="margin-bottom:14px;border-left:3px solid ${bwCol}">
    <div class="card-h"><span class="card-t">🐂 BullWatch</span><span style="color:${bwCol};font-weight:700;font-family:'JetBrains Mono',monospace;font-size:12px">${bwIcon} ${esc(bwLbl)}</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6;font-family:'JetBrains Mono',monospace">
      Cache: ${bw.items_count||0} item · ${bw.cache_populated?'populated':'boş'}<br>
      ${bw.scan_running ? `Scan: <b>${bw.scan_progress||0}/${bw.scan_total||0}</b> · <b>${(bw.scan_elapsed_sec||0).toFixed(0)}s</b> geçti` : 'Scan: durdu'}
      ${bw.hung ? `<br><span style="color:var(--red);font-weight:700">⚠️ 8+ dakikadır scan'a yanıt yok. Force reset gerekebilir.</span><br><button class="btn btn-sm" style="background:var(--redd);color:var(--red);margin-top:8px" onclick="forceBwReset(this)">🛑 BullWatch Scan Reset</button>` : ''}
    </div>
  </div>`;

  // KAP feed
  const kapAge = kap.newest_publish_date ? Math.round((Date.now() - new Date(kap.newest_publish_date).getTime())/3600000) : null;
  const kapCol = kapAge != null && kapAge < 12 ? 'var(--grn)' : kapAge != null && kapAge < 48 ? 'var(--ylw)' : 'var(--red)';
  h += `<div class="card" style="margin-bottom:14px;border-left:3px solid ${kapCol}">
    <div class="card-h"><span class="card-t">📰 KAP Feed</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6;font-family:'JetBrains Mono',monospace">
      SQLite: ${kap.total_in_sqlite||0} kayıt · Redis: ${kap.total_in_redis||0}<br>
      Son disclosure: ${kapAge != null ? kapAge + ' saat önce' : '—'}
    </div>
  </div>`;

  // Portfolio
  h += `<div class="card" style="margin-bottom:14px;border-left:3px solid var(--grn)">
    <div class="card-h"><span class="card-t">💼 Portfolio</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6;font-family:'JetBrains Mono',monospace">
      Açık: <b>${pf.open_count||0}</b> · Kapalı: ${pf.closed_count||0} (${pf.winners||0}W / ${pf.losers||0}L)<br>
      Toplam P&L: ${(pf.total_pnl_pct||0).toFixed(1)}% · Win rate: ${pf.win_rate!=null?pf.win_rate+'%':'—'}
    </div>
  </div>`;

  // VIOP
  h += `<div class="card" style="margin-bottom:14px;border-left:3px solid var(--gold)">
    <div class="card-h"><span class="card-t">🎲 VIOP</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6;font-family:'JetBrains Mono',monospace">
      Bugün: ${viop.total_today||0} contract · Son snap: ${esc(viop.snap_date_latest||'—')}
    </div>
  </div>`;

  // Auto-refresh
  const lc = (ar.last_cycle) || null;
  h += `<div class="card" style="margin-bottom:14px;border-left:3px solid var(--cyn)">
    <div class="card-h"><span class="card-t">🔄 Auto-Refresh</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6;font-family:'JetBrains Mono',monospace">
      ${lc ? `Son cycle: ${(lc.duration_sec||0).toFixed(0)}s · ${lc.succeeded||0}/${lc.attempted||0} OK · ${lc.score_change_count||0} skor değişti` : 'Henüz çalışmadı'}
    </div>
  </div>`;

  // Quick action probes
  h += `<div class="card" style="margin-bottom:14px">
    <div class="card-h"><span class="card-t">🧪 Hızlı Test</span></div>
    <div class="card-b" style="font-size:12px;line-height:1.6">
      <p style="color:var(--t3);margin-bottom:10px">Self-debug için bir test "+ Aldım" sırası çalıştırır — bug raporu için kullan.</p>
      <button class="btn btn-sm btn-grn" onclick="runPortfolioE2ETest(this)">🧪 Portfolio E2E (test pozisyon aç+listele+kapat)</button>
      <div id="diagE2EOut" style="margin-top:12px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t2);line-height:1.6"></div>
    </div>
  </div>`;
  pg.innerHTML = h;
}

async function runPortfolioE2ETest(btn){
  const out = document.getElementById('diagE2EOut');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Çalışıyor…'; }
  out.innerHTML = '';
  const log = (msg, ok) => {
    out.innerHTML += `<div style="color:${ok?'var(--grn)':'var(--red)'}">${ok?'✓':'✕'} ${esc(msg)}</div>`;
  };
  const ticker = 'TST' + Math.floor(Math.random()*900+100);
  try {
    // Step 1: Open
    const r1 = await fetch('/api/portfolio/positions', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ticker, entry_price: 10.5, lot: 100, notes: 'e2e diag'}),
    });
    const j1 = await r1.json();
    if (!j1.position) throw new Error('open failed: ' + JSON.stringify(j1));
    const pid = j1.position.position_id;
    log(`POST açıldı: ${ticker} (id ${pid.slice(0,8)}...)`, true);

    // Step 2: List & check appearance
    const r2 = await api('/api/portfolio/positions');
    const found = (r2.items || []).find(p => p.ticker === ticker);
    log(`GET listede ${found?'BULDU':'BULAMADI'}: ${ticker}`, !!found);
    if (!found) throw new Error('NOT IN LIST — backend bug!');
    if (!found.signal) throw new Error('signal field missing');
    log(`Signal verdict: ${found.signal.verdict}`, true);

    // Step 3: Close (cleanup)
    const r3 = await fetch('/api/portfolio/positions/' + pid + '/close', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({exit_price: 11.0, exit_reason: 'e2e cleanup'}),
    });
    const j3 = await r3.json();
    log(`POST close: ok=${j3.ok}`, !!j3.ok);

    log('🎉 Tüm flow tamam — portfolio sistemi sağlıklı.', true);
  } catch(e) {
    log('HATA: ' + (e.message || e), false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🧪 Portfolio E2E (test pozisyon aç+listele+kapat)'; }
  }
}

// ===== AKIŞ (UNIFIED ACTIVITY FEED) =====
// Tek chronological feed: CONVICTION alarm + listeye giriş/çıkış +
// KAP finansal rapor + auto-refresh skor değişimi. Watchlist filtresi.
async function loadAkis(force){
  if (S.akis && !force) return S.akis;
  const wlOn = S._akisWlOnly !== false;       // default ON
  const hours = S._akisHours || 24;
  let url = `/api/activity/recent?since_hours=${hours}&limit=80`;
  if (wlOn && S.wl && S.wl.length) {
    url += '&watchlist=' + encodeURIComponent(S.wl.join(','));
  }
  try {
    const r = await api(url);
    const v = (r && (r.value || r)) || {};
    S.akis = {
      items: v.items || [],
      counts: v.counts || {},
      watchlist_filter: !!v.watchlist_filter,
      since_hours: v.since_hours || hours,
      fetched_at: Date.now(),
    };
  } catch(e) {
    console.warn('akis fetch failed', e);
    S.akis = { items: [], counts: {} };
  }
  return S.akis;
}

function _akisItemStyle(t){
  const m = {
    ALARM:         {ic:'🚨', col:'var(--red)', bg:'rgba(239,83,80,.12)', lbl:'CONVICTION Alarm'},
    MEMBERSHIP:    {ic:'📋', col:'var(--orn)', bg:'rgba(255,167,38,.12)', lbl:'Liste Hareketi'},
    KAP_FINANCIAL: {ic:'📰', col:'var(--cyn)', bg:'var(--blud)', lbl:'KAP Bilanço'},
    SCORE_CHANGE:  {ic:'⚡', col:'var(--grn)', bg:'var(--grnd)', lbl:'Skor Değişimi'},
  };
  return m[t] || {ic:'•', col:'var(--t3)', bg:'var(--bg3)', lbl:t};
}

function _akisTimeAgo(iso){
  return _alarmTimeAgo(iso);
}

function renderAkisPage(){
  const pg = $('pg-akis');
  if (!S.akis) {
    pg.innerHTML = _skelHeader('📰 Akış — son 24 saat yükleniyor…') + _skelList(8);
    loadAkis().then(() => renderAkisPage());
    return;
  }
  const items = S.akis.items || [];
  const counts = S.akis.counts || {};
  const wlOn = S._akisWlOnly !== false;
  const hours = S._akisHours || 24;

  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--cyn)">📰 Aktivite Akışı</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Son ${hours} saatte sistemde ne olduğunu tek listede gör · ${items.length} olay</p>
    </div>
    <button class="btn btn-grn" onclick="S.akis=null;loadAkis(true).then(()=>renderAkisPage())">🔄</button>
  </div>`;

  // Source-type counts strip
  h += `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">
    ${['ALARM','MEMBERSHIP','KAP_FINANCIAL','SCORE_CHANGE'].map(t=>{
      const st = _akisItemStyle(t);
      const c = counts[t] || 0;
      return `<span style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:var(--rad);background:${st.bg};color:${st.col};font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700"><span>${st.ic}</span>${esc(st.lbl)} <b style="margin-left:2px">${c}</b></span>`;
    }).join('')}
  </div>`;

  // Filter controls: hours selector + watchlist toggle
  h += '<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;align-items:center;font-size:11px">';
  [6, 24, 72, 168].forEach(hr => {
    const on = hours === hr;
    const lbl = hr === 168 ? '7g' : hr === 72 ? '3g' : hr === 24 ? '24sa' : '6sa';
    h += `<button class="btn btn-sm" style="${on?'background:var(--prp)20;border:1px solid var(--prp);color:var(--prp)':'background:var(--bg3);color:var(--t2)'};font-size:11px" onclick="S._akisHours=${hr};S.akis=null;loadAkis(true).then(()=>renderAkisPage())">${lbl}</button>`;
  });
  h += `<label style="margin-left:auto;display:inline-flex;align-items:center;gap:6px;color:var(--t2);cursor:pointer">
    <input type="checkbox" ${wlOn?'checked':''} onchange="S._akisWlOnly=this.checked;S.akis=null;loadAkis(true).then(()=>renderAkisPage())" /> Sadece Watchlist
  </label></div>`;

  // Explainer
  h += `<div style="padding:10px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:12px;font-size:11px;color:var(--t2);line-height:1.55">
    💡 <b style="color:var(--cyn)">Tek akış, dört kaynak:</b> CONVICTION alarmları, BullWatch liste hareketleri, KAP'a düşen finansal raporlar, ve auto-refresh'in yakaladığı anlamlı skor değişimleri — hepsi chronological sırayla. Tıkla → ilgili hisseye git.
  </div>`;

  if (!items.length) {
    const tip = wlOn
      ? 'Watchlist filtresi aktif. Son ' + hours + ' saatte senin hisselerin için olay yok. Filtreyi kapatabilir ya da süreyi artırabilirsin.'
      : 'Son ' + hours + ' saatte hiç olay olmamış. Sistem yeni başlatılmış olabilir.';
    h += `<div class="emp" style="padding:30px 20px;text-align:center"><h3 style="color:var(--t2);font-size:14px;margin-bottom:8px">Akış sessiz</h3><p style="color:var(--t4);font-size:11px;line-height:1.6">${esc(tip)}</p></div>`;
    pg.innerHTML = h;
    return;
  }

  h += '<div class="card"><div class="card-b" style="padding:0">';
  items.forEach((it, i) => {
    const st = _akisItemStyle(it.type);
    const ago = _akisTimeAgo(it.occurred_at);
    const wlBadge = (S.wl||[]).includes(it.ticker)
      ? '<span style="font-size:10px;color:var(--ylw);margin-left:6px" title="Watchlist">⭐</span>' : '';
    h += `<div class="kap-row" style="padding:11px 14px;${i<items.length-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer;transition:background .1s" onclick="loadTicker('${esc(it.ticker)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px">
            <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--cyn)">${esc(it.ticker)}</span>
            ${wlBadge}
            <span style="display:inline-flex;align-items:center;gap:3px;font-family:'JetBrains Mono',monospace;font-size:10px;color:${st.col};font-weight:700;padding:2px 6px;background:${st.bg};border-radius:3px"><span>${st.ic}</span>${esc(st.lbl)}</span>
          </div>
          <div class="kap-row-subj" style="font-size:11.5px;color:var(--t2);line-height:1.45">${esc(it.summary || '')}</div>
          ${it.detail ? `<div style="font-size:10.5px;color:var(--t4);margin-top:2px;line-height:1.4">${esc(it.detail)}</div>` : ''}
        </div>
        <div style="text-align:right;font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;flex-shrink:0;white-space:nowrap">${esc(ago)}</div>
      </div>
    </div>`;
  });
  h += '</div></div>';

  pg.innerHTML = h;
}

// ===== ALARMLAR (BULLWATCH HIGH-CONVICTION HISTORY) =====
// BullWatch list is volatile by design (re-rank per scan), so the user
// can't track "system gave a strong call N days ago — where is the
// ticker now?". This tab is the immutable history of those strong
// calls + their post-alarm price reactions.
async function loadAlarmlar(force){
  if (S.alarmlar && !force) return S.alarmlar;
  let recent = []; let stats = {};
  try {
    const r = await api('/api/bullwatch/alerts/recent?limit=100');
    recent = (r && (r.items || r.value)) || [];
  } catch (e) {
    console.warn('alerts/recent failed', e);
  }
  try {
    const s = await api('/api/bullwatch/alerts/stats');
    stats = (s && s.stats) || {};
  } catch (e) {}
  S.alarmlar = { recent, stats, fetched_at: Date.now() };
  // Eagerly pull membership stats (cheap) so the chip badge shows count
  if (!S.bwMembership || force) {
    try {
      const ms = await api('/api/bullwatch/membership/stats');
      const stt = (ms && (ms.value || ms)) || {};
      S.bwMembership = { items: null, stats: stt.stats || {} };
    } catch(e) { /* ignore */ }
  }
  return S.alarmlar;
}

function _alarmTimeAgo(iso){
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '';
  const ms = Date.now() - t;
  const min = Math.round(ms / 60000);
  if (min < 1) return 'az önce';
  if (min < 60) return `${min} dk önce`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} sa önce`;
  return `${Math.round(hr / 24)} gün önce`;
}

function renderAlarmlarPage(){
  const pg = $('pg-alarmlar');
  if (!S.alarmlar) {
    pg.innerHTML = _skelHeader('🚨 Alarm Geçmişi — yükleniyor…') + _skelList(8);
    loadAlarmlar().then(() => renderAlarmlarPage());
    return;
  }
  const alarms = S.alarmlar.recent || [];
  const stats = S.alarmlar.stats || {};
  const filt = S._alarmFilter || 'all';

  // Filter buckets
  const isWatched = (t) => (S.wl || []).includes(t);
  const isActive = (a) => {
    // "Active" = alarm verildi ve hala bullwatch listesinde olabilir.
    // Bunu tam bilemiyoruz client'tan ama proxy: son 7 günde alarm + score >= 75 idi.
    const t = new Date(a.alarmed_at).getTime();
    return Date.now() - t < 7 * 24 * 3600 * 1000;
  };
  const filtered = filt === 'wl'
    ? alarms.filter(a => isWatched(a.ticker))
    : filt === 'active'
      ? alarms.filter(isActive)
      : filt === 'month'
        ? alarms.filter(a => (Date.now() - new Date(a.alarmed_at).getTime()) < 30 * 24 * 3600 * 1000)
        : alarms;

  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--red)">🚨 Alarm Geçmişi — BullWatch</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">${alarms.length} kayıt · Son 30 günde ${stats.last_30d_count || 0} alarm · BullWatch sistem "çok emin" dediğinde kaydedilir</p>
    </div>
    <button class="btn btn-grn" onclick="loadAlarmlar(true).then(()=>renderAlarmlarPage())">🔄</button>
  </div>`;

  const membershipStats = (S.bwMembership && S.bwMembership.stats) || {};
  const memTotal = membershipStats.total_30d || 0;
  const chips = [
    ['all',    `Tümü (${alarms.length})`,                                                            'var(--acc)'],
    ['active', `🔥 Son Hafta (${alarms.filter(isActive).length})`,                                  'var(--grn)'],
    ['wl',     `Watchlist (${alarms.filter(a=>isWatched(a.ticker)).length})`,                       'var(--blu)'],
    ['month',  `Son 30 gün (${alarms.filter(a=>(Date.now()-new Date(a.alarmed_at).getTime())<30*24*3600*1000).length})`, 'var(--cyn)'],
    ['membership', `📋 Hareketler${memTotal?` (${memTotal})`:''}`, 'var(--orn)'],
    ['backtest', `📊 Backtest`, 'var(--prp)'],
  ];
  h += `<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap">${chips.map(([k,l,c])=>`<button class="btn btn-sm" style="${filt===k?`background:${c}20;border:1px solid ${c};color:${c}`:'background:var(--bg3);color:var(--t2)'}" onclick="S._alarmFilter='${k}';renderAlarmlarPage()">${l}</button>`).join('')}</div>`;

  // Backtest tab renders its own dashboard instead of the alarm list.
  if (filt === 'backtest') {
    pg.innerHTML = h + _bwBacktestPanel();
    if (!S.bwBacktest) _bwBacktestLoad();
    return;
  }
  // Membership-events tab — list churn (entries / exits / zone changes).
  if (filt === 'membership') {
    pg.innerHTML = h + _bwMembershipPanel();
    if (!S.bwMembership || !S.bwMembership.items) _bwMembershipLoad();
    return;
  }

  // Explainer banner
  h += `<div style="padding:12px 16px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-base);color:var(--t2);line-height:1.6">
    <b style="color:var(--red)">🚨 Alarm ne zaman verilir?</b> BullWatch sistem aşağıdaki <b>tüm</b> kriterleri sağladığında: zone=CONVICTION + score≥75 + yüksek veri kalitesi + ≥2 motor onayı. Aynı hisse 7 gün dedupe. <span style="color:var(--t4)">Liste değişse de bu kayıt sabit kalır — kalibrasyon için altın değerinde.</span>
  </div>`;

  if (!filtered.length) {
    h += '<div class="emp" style="padding:40px 20px"><h3 style="color:var(--t2);font-size:14px;margin-bottom:8px">Bu filtrede alarm yok</h3><p style="color:var(--t4);font-size:11px">Sistem her gün alarm vermez — "çok emin" dediği zaman not eder. Bu iyi bir şey.</p></div>';
    pg.innerHTML = h;
    return;
  }

  // Reaction badge helper (same shape as Bilançolar reactions)
  const reactionBadge = (label, pct) => {
    if (pct == null) return `<span style="font-size:9px;color:var(--t4);padding:1px 5px;background:var(--bg3);border-radius:3px">${label}: —</span>`;
    const c = pct > 0 ? 'var(--grn)' : pct < 0 ? 'var(--red)' : 'var(--t3)';
    const sign = pct > 0 ? '+' : '';
    return `<span style="font-size:9px;color:${c};font-weight:700;padding:1px 5px;background:${c}15;border-radius:3px">${label}: ${sign}${pct.toFixed(1)}%</span>`;
  };

  h += '<div class="card"><div class="card-b" style="padding:0">';
  filtered.forEach((a, i) => {
    const ago = _alarmTimeAgo(a.alarmed_at);
    const scoreCol = a.score_at_alarm >= 85 ? 'var(--grn)' : 'var(--ylw)';
    const watchedDot = isWatched(a.ticker) ? '<span style="font-size:10px;color:var(--ylw);margin-left:4px" title="Watchlist\'inde">⭐</span>' : '';
    h += `<div style="padding:12px 14px;${i<filtered.length-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer;transition:background .1s" onclick="loadTicker('${esc(a.ticker)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:3px">
            <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--cyn)">${esc(a.ticker)}</span>
            ${watchedDot}
            <span style="font-size:11px;color:${scoreCol};font-weight:700">Score: ${a.score_at_alarm}</span>
            <span style="font-size:10px;color:var(--t3)">${esc(a.zone_at_alarm)}</span>
            <span style="font-size:10px;color:var(--t4);padding:1px 5px;background:var(--bg3);border-radius:3px">${a.engines_fired} motor</span>
          </div>
          <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.pattern_at_alarm || '')}${a.sector_tr ? ' · ' + esc(a.sector_tr) : ''}</div>
        </div>
        <div style="text-align:right;font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;flex-shrink:0">
          <div>${ago}</div>
          ${a.price_at_alarm ? `<div style="margin-top:2px;color:var(--t3)">@ ${a.price_at_alarm.toFixed(2)} TL</div>` : ''}
        </div>
      </div>
      <div style="display:flex;gap:4px;font-family:'JetBrains Mono',monospace">
        ${reactionBadge('1g', a.reaction_1d_pct)}
        ${reactionBadge('1h', a.reaction_1w_pct)}
        ${reactionBadge('1a', a.reaction_1m_pct)}
      </div>
    </div>`;
  });
  h += '</div></div>';

  // Help footer
  h += `<div style="margin-top:14px;padding:10px 14px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t3);line-height:1.6">
    📊 <b style="color:var(--t2)">Reaction izleme:</b> Alarm verildiği fiyat üzerinden 1 gün / 1 hafta / 1 ay sonrası fiyat değişimi otomatik takip edilir. Sistem doğruluğunu kalibre etmek için bu kayıtlara bakın.
  </div>`;

  pg.innerHTML = h;
}

// ===== BACKTEST DASHBOARD (Tahtacı PR C) =====
// Shows aggregated win rates over the immutable alarm history. The
// data comes from /api/bullwatch/alerts/backtest which slices the
// SAME records the alarm list above already renders.
async function _bwBacktestLoad(){
  const days = S._btDays || 90;
  try {
    const r = await api(`/api/bullwatch/alerts/backtest?since_days=${days}`);
    S.bwBacktest = (r && (r.value || r)) || null;
  } catch(e){
    S.bwBacktest = { error: String(e && e.message || e) };
  }
  renderAlarmlarPage();
}

function _btPct(v){
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(1)}%`;
}
function _btWR(v){
  if (v == null) return '—';
  return `${(v * 100).toFixed(0)}%`;
}
function _btWRColor(v){
  if (v == null) return 'var(--t4)';
  if (v >= 0.65) return 'var(--grn)';
  if (v >= 0.50) return 'var(--ylw)';
  return 'var(--red)';
}

function _btStatCell(s){
  if (!s || s.n === 0) return `<td style="color:var(--t4);font-size:10px">—</td>`;
  return `<td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t1)">
    <span style="color:${_btWRColor(s.win_rate)};font-weight:700">${_btWR(s.win_rate)}</span>
    <span style="color:var(--t4);font-size:9px;margin-left:4px">n=${s.n}</span>
    <div style="font-size:9px;color:var(--t3);margin-top:2px">μ ${_btPct(s.mean)} · med ${_btPct(s.median)}</div>
  </td>`;
}

function _btBreakdownTable(title, rows, keyName){
  if (!rows || !rows.length) return '';
  let h = `<div class="card" style="margin-top:14px"><div class="card-h"><h3 style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--cyn)">${title}</h3></div><div class="card-b" style="padding:0;overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:11px">
      <thead><tr style="border-bottom:1px solid var(--bdr);color:var(--t4);font-size:10px;letter-spacing:.5px">
        <th style="text-align:left;padding:8px 12px">${keyName}</th>
        <th style="text-align:left;padding:8px 12px">n</th>
        <th style="text-align:left;padding:8px 12px">1g</th>
        <th style="text-align:left;padding:8px 12px">1h</th>
        <th style="text-align:left;padding:8px 12px">1a</th>
      </tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const k = r.band || r.zone || r.sector || r.pattern || '—';
    h += `<tr style="${i<rows.length-1?'border-bottom:1px solid var(--bdr);':''}">
      <td style="padding:10px 12px;font-weight:700;color:var(--t1)">${esc(k)}</td>
      <td style="padding:10px 12px;color:var(--t3);font-family:'JetBrains Mono',monospace">${r.n||0}</td>
      ${_btStatCell(r['1d'])}
      ${_btStatCell(r['1w'])}
      ${_btStatCell(r['1m'])}
    </tr>`;
  });
  h += '</tbody></table></div></div>';
  return h;
}

function _btHistogram(buckets){
  if (!buckets || !buckets.length) return '';
  const maxN = Math.max(1, ...buckets.map(b => b.count));
  let h = `<div class="card" style="margin-top:14px"><div class="card-h"><h3 style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--cyn)">📊 1-gün Getiri Dağılımı</h3></div><div class="card-b">`;
  h += '<div style="display:flex;flex-direction:column;gap:3px;font-family:\'JetBrains Mono\',monospace;font-size:11px">';
  buckets.forEach(b => {
    const w = (b.count / maxN) * 100;
    const isNeg = b.bucket.startsWith('-') || b.bucket.startsWith('<');
    const col = isNeg ? 'var(--red)' : 'var(--grn)';
    h += `<div style="display:flex;align-items:center;gap:8px">
      <div style="width:64px;color:var(--t3);text-align:right">${esc(b.bucket)}</div>
      <div style="flex:1;background:var(--bg3);height:14px;border-radius:2px;overflow:hidden"><div style="height:100%;background:${col};width:${w}%;opacity:.7"></div></div>
      <div style="width:32px;color:var(--t2);text-align:right">${b.count}</div>
    </div>`;
  });
  h += '</div></div></div>';
  return h;
}

function _btFakePump(fp){
  if (!fp || !fp.count) {
    return `<div class="card" style="margin-top:14px"><div class="card-b" style="text-align:center;padding:20px;color:var(--grn);font-size:12px">✓ Pump-and-dump tespit edilmedi (1g pozitif ama 1h negatif kombinasyonu yok)</div></div>`;
  }
  let h = `<div class="card" style="margin-top:14px;border-left:3px solid var(--red)"><div class="card-h"><h3 style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--red)">⚠️ Pump-and-Dump Sinyalleri</h3>
    <p style="font-size:11px;color:var(--t3);margin-top:4px">1 gün ≥+3% pozitif başlamış ama 1 hafta ≤-2% düşmüş alarmlar — operatör pump-and-fade imzası.</p></div>
  <div class="card-b" style="padding:12px 16px">
    <div style="font-size:11px;color:var(--t2);margin-bottom:10px">${fp.count} alarm / ${(fp.share*100).toFixed(0)}% — reaksiyon takip edilen alarmlarda.</div>
    <div style="display:flex;flex-direction:column;gap:6px">`;
  fp.samples.forEach(s => {
    h += `<div style="display:flex;justify-content:space-between;gap:8px;font-size:11px;padding:6px 10px;background:var(--bg3);border-radius:4px;cursor:pointer" onclick="loadTicker('${esc(s.ticker)}')">
      <span style="font-family:'JetBrains Mono',monospace;color:var(--cyn);font-weight:700">${esc(s.ticker)}</span>
      <span style="color:var(--t3);font-size:10px;flex:1;margin-left:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.pattern||'—')}</span>
      <span style="color:var(--grn);font-family:'JetBrains Mono',monospace">1g ${_btPct(s['1d_pct'])}</span>
      <span style="color:var(--red);font-family:'JetBrains Mono',monospace">1h ${_btPct(s['1w_pct'])}</span>
    </div>`;
  });
  h += '</div></div></div>';
  return h;
}

function _bwBacktestPanel(){
  const bt = S.bwBacktest;
  const days = S._btDays || 90;
  let h = `<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;align-items:center">
    <span style="font-size:11px;color:var(--t3);margin-right:4px">Periyot:</span>`;
  [30, 60, 90, 180, 365].forEach(d => {
    const active = days === d;
    h += `<button class="btn btn-sm" style="${active?'background:var(--prp)20;border:1px solid var(--prp);color:var(--prp)':'background:var(--bg3);color:var(--t2)'}" onclick="S._btDays=${d};S.bwBacktest=undefined;renderAlarmlarPage()">${d}g</button>`;
  });
  h += '</div>';

  if (!bt) {
    h += '<div class="ld"><div class="sp"></div><div class="ld-t">Backtest hesaplanıyor…</div></div>';
    return h;
  }
  if (bt.error) {
    h += `<div class="emp"><h3 style="color:var(--red)">Backtest yüklenemedi: ${esc(bt.error)}</h3></div>`;
    return h;
  }

  // Headline KPI strip
  const overall = bt.overall || {};
  const total = bt.total_alerts || 0;
  h += `<div class="card" style="margin-bottom:14px"><div class="card-b">
    <div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>
        <div style="font-size:10px;color:var(--t4);letter-spacing:.5px">TOPLAM ALARM</div>
        <div style="font-size:24px;font-weight:700;color:var(--cyn);font-family:'JetBrains Mono',monospace">${total}</div>
        <div style="font-size:10px;color:var(--t3)">son ${days} gün</div>
      </div>`;
  ['1d', '1w', '1m'].forEach(hKey => {
    const s = overall[hKey];
    const lbl = hKey === '1d' ? '1 GÜN' : hKey === '1w' ? '1 HAFTA' : '1 AY';
    const wr = s && s.win_rate != null ? _btWR(s.win_rate) : '—';
    const mean = s && s.mean != null ? _btPct(s.mean) : '—';
    const col = s ? _btWRColor(s.win_rate) : 'var(--t4)';
    h += `<div>
      <div style="font-size:10px;color:var(--t4);letter-spacing:.5px">${lbl} KAZANMA</div>
      <div style="font-size:24px;font-weight:700;color:${col};font-family:'JetBrains Mono',monospace">${wr}</div>
      <div style="font-size:10px;color:var(--t3)">μ ${mean} · n=${s?s.n:0}</div>
    </div>`;
  });
  // BIST100 baseline
  const bs = bt.baseline || {};
  if (bs['1d'] != null || bs['1w'] != null || bs['1m'] != null) {
    h += `<div>
      <div style="font-size:10px;color:var(--t4);letter-spacing:.5px">XU100 BASELINE</div>
      <div style="font-size:14px;color:var(--t2);font-family:'JetBrains Mono',monospace;margin-top:4px">1g ${_btPct(bs['1d'])} · 1h ${_btPct(bs['1w'])} · 1a ${_btPct(bs['1m'])}</div>
      <div style="font-size:10px;color:var(--t3)">aynı pencere</div>
    </div>`;
  }
  h += '</div></div></div>';

  // Helper banner
  h += `<div style="padding:10px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:11px;color:var(--t3);line-height:1.55">
    💡 <b style="color:var(--t2)">Kazanma oranı</b> = alarm sonrası getiri pozitif olan oran. <b>μ</b> = ortalama, <b>med</b> = medyan. Skor band, zone, sektör, pattern kırılımları kalibrasyon için altın değerinde — hangi koşullarda sistemin işe yaradığını gösterir.
  </div>`;

  h += _btBreakdownTable('🎯 Skor Bandına Göre', bt.by_score_band, 'Skor Bandı');
  h += _btBreakdownTable('🔥 Zone\'a Göre', bt.by_zone, 'Zone');
  h += _btBreakdownTable('🏭 Sektöre Göre (top 10)', bt.by_sector, 'Sektör');
  h += _btBreakdownTable('📐 Pattern\'e Göre (top 8)', bt.by_pattern, 'Pattern');
  h += _btFakePump(bt.fake_pump);
  h += _btHistogram(bt.histogram_1d);

  return h;
}

// ===== BULLWATCH MEMBERSHIP EVENTS =====
// "Listeye girdi / Listeden düştü / Zone yükseldi" alarm tipi. CONVICTION
// alarmlarından ayrı tablo. Default ekran watchlist filtresiyle gelir —
// gürültü düşük tutmak için.
async function _bwMembershipLoad(){
  const wlOnly = S._bwMemWatchlistOnly !== false;   // default ON
  const typeFilter = S._bwMemTypeFilter || '';
  let url = '/api/bullwatch/membership/recent?limit=150&since_days=30';
  if (typeFilter) url += '&event_type=' + encodeURIComponent(typeFilter);
  if (wlOnly && S.wl && S.wl.length) {
    url += '&tickers=' + encodeURIComponent(S.wl.join(','));
  }
  try {
    const [r, s] = await Promise.all([
      api(url),
      api('/api/bullwatch/membership/stats').catch(() => null),
    ]);
    const v = (r && (r.value || r)) || {};
    const ss = (s && (s.value || s)) || {};
    S.bwMembership = {
      items: v.items || [],
      stats: ss.stats || {},
      fetched_at: Date.now(),
    };
  } catch(e) {
    console.warn('membership fetch failed', e);
    S.bwMembership = { items: [], stats: {} };
  }
  renderAlarmlarPage();
}

function _bwMemEventStyle(t){
  const m = {
    ENTRY:          {ic:'🆕', col:'var(--blu)', bg:'var(--blud)',  lbl:'Listeye Girdi'},
    EXIT:           {ic:'🔻', col:'var(--orn)', bg:'rgba(255,167,38,.12)', lbl:'Listeden Düştü'},
    ZONE_UPGRADE:   {ic:'⚡', col:'var(--grn)', bg:'var(--grnd)',  lbl:'Zone Yükseldi'},
    ZONE_DOWNGRADE: {ic:'🔽', col:'var(--red)', bg:'var(--redd)',  lbl:'Zone Düştü'},
  };
  return m[t] || {ic:'?', col:'var(--t3)', bg:'var(--bg3)', lbl:t};
}

function _bwMembershipPanel(){
  const m = S.bwMembership;
  const wlOnly = S._bwMemWatchlistOnly !== false;
  const tf = S._bwMemTypeFilter || '';

  let h = '';
  // Explainer banner
  h += `<div style="padding:10px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:12px;font-size:11px;color:var(--t2);line-height:1.55">
    📋 <b style="color:var(--orn)">Liste Hareketleri:</b> BullWatch listesi her scan'de yeniden hesaplanır. Bu sayfa <b>her değişikliği</b> kaydeder — bir hisse listeye yeni girdiyse, düştüyse, zone'u yükseldiyse. Bu, CONVICTION alarmlarından <b>ayrı</b> bir feed: chatty ama izlenmek için ideal.
  </div>`;

  // Filter controls
  const types = [
    ['',               'Tümü',           'var(--t3)'],
    ['ENTRY',          '🆕 Girdi',       'var(--blu)'],
    ['EXIT',           '🔻 Düştü',       'var(--orn)'],
    ['ZONE_UPGRADE',   '⚡ Yükseldi',    'var(--grn)'],
    ['ZONE_DOWNGRADE', '🔽 Düştü',       'var(--red)'],
  ];
  h += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;align-items:center">';
  types.forEach(([k, lbl, c]) => {
    const on = tf === k;
    h += `<button class="btn btn-sm" style="${on?`background:${c}20;border:1px solid ${c};color:${c}`:'background:var(--bg3);color:var(--t2)'};font-size:11px" onclick="S._bwMemTypeFilter='${k}';S.bwMembership=null;_bwMembershipLoad()">${esc(lbl)}</button>`;
  });
  h += `<label style="margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--t2);cursor:pointer">
    <input type="checkbox" ${wlOnly?'checked':''} onchange="S._bwMemWatchlistOnly=this.checked;S.bwMembership=null;_bwMembershipLoad()" /> Sadece Watchlist
  </label></div>`;

  if (!m || !m.items) {
    h += '<div class="ld"><div class="sp"></div><div class="ld-t">Hareketler yükleniyor…</div></div>';
    return h;
  }
  if (!m.items.length) {
    const tip = wlOnly
      ? 'Watchlist filtresi aktif — yıldızladığın hisselerde son 30g hareket yok. Filtreyi kaldır veya watchlist\'e hisse ekle.'
      : 'Son 30 günde hareket kaydı yok. Sistem yeni başlatılmış olabilir — ilk scan tamamlanınca buraya kayıt düşmeye başlar.';
    h += `<div class="emp" style="padding:30px 20px;text-align:center"><h4 style="color:var(--t2)">Henüz hareket yok</h4><p style="color:var(--t4);font-size:11px;margin-top:6px">${esc(tip)}</p></div>`;
    return h;
  }

  // Group by ticker for compact rendering — same ticker can have
  // multiple events in 30 days.
  h += '<div class="card"><div class="card-b" style="padding:0">';
  m.items.forEach((it, i) => {
    const st = _bwMemEventStyle(it.event_type);
    const ago = _alarmTimeAgo(it.occurred_at);
    const wlBadge = (S.wl||[]).includes(it.ticker)
      ? '<span style="font-size:10px;color:var(--ylw);margin-left:6px" title="Watchlist">⭐</span>' : '';
    const sub = it.event_type === 'ZONE_UPGRADE' || it.event_type === 'ZONE_DOWNGRADE'
      ? `${esc(it.prev_zone||'?')} → ${esc(it.new_zone||'?')}` + (it.new_score!=null && it.prev_score!=null ? ` · skor ${it.prev_score.toFixed(0)} → ${it.new_score.toFixed(0)}` : '')
      : it.event_type === 'ENTRY'
        ? `Zone: ${esc(it.new_zone||'?')}` + (it.new_score!=null?` · Skor ${it.new_score.toFixed(0)}`:'') + (it.new_pattern?` · ${esc(it.new_pattern)}`:'')
        : `Önceki zone: ${esc(it.prev_zone||'?')}` + (it.prev_score!=null?` · Skor ${it.prev_score.toFixed(0)}`:'');
    h += `<div style="padding:10px 14px;${i<m.items.length-1?'border-bottom:1px solid var(--bdr);':''}cursor:pointer;transition:background .1s" onclick="loadTicker('${esc(it.ticker)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:3px">
            <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--cyn)">${esc(it.ticker)}</span>
            ${wlBadge}
            <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${st.col};font-weight:700;padding:2px 6px;background:${st.bg};border-radius:3px">${st.ic} ${esc(st.lbl)}</span>
          </div>
          <div style="font-size:11px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${sub}</div>
        </div>
        <div style="text-align:right;font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;flex-shrink:0">${esc(ago)}</div>
      </div>
    </div>`;
  });
  h += '</div></div>';
  h += `<div style="margin-top:14px;padding:10px 14px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t3);line-height:1.6">
    💡 <b style="color:var(--t2)">Nasıl çalışır:</b> Her BullWatch scan'inden sonra yeni liste önceki ile karşılaştırılır. Listeye giren / düşen / zone'u değişen her hisse buraya yazılır. Watchlist filtresi açıkken sadece yıldızladığın hisselerin hareketi görünür.
  </div>`;
  return h;
}

// ===== BİLANÇOLAR (KAP DISCLOSURE FEED) =====
// Faz 2 — Alert sayfası + bilanço takvimi. Two-pane layout: recent
// disclosures from /api/kap/recent on the left, watchlist-specific
// expected disclosures (calendar) on the right.
async function loadBilancolar(force){
  if (S.kap && !force) return S.kap;
  let recent = []; let calendar = {};
  try {
    const r = await api('/api/kap/recent?limit=100');
    recent = (r && (r.items || r.value)) || [];
  } catch (e) {
    console.warn('KAP recent fetch failed', e);
  }
  // Calendar — only fetch for the user's watchlist (otherwise it's 437 calls)
  const wl = (S.wl || []).slice(0, 30);  // safety cap
  if (wl.length) {
    const results = await Promise.all(
      wl.map(t => api('/api/kap/calendar/' + encodeURIComponent(t)).catch(() => null))
    );
    results.forEach((r, i) => {
      if (r && r.items) calendar[wl[i]] = r.items;
    });
  }
  S.kap = { recent, calendar, fetched_at: Date.now() };
  // Mark "seen" — clear unread badge after the user opens this page
  if (recent.length) {
    const newestIdx = Math.max(...recent.map(d => d.disclosure_index || 0));
    if (newestIdx > 0) localStorage.setItem('bb_kap_last_seen', String(newestIdx));
  }
  return S.kap;
}

function _kapItemLabel(d){
  const rt = d.rule_type || '';
  const yr = d.year || '';
  const tag = rt && yr ? `${yr} ${rt}` : (d.subject || '');
  return tag;
}

function _kapItemColor(d){
  // Type-based color: FR (balance sheet) = primary, others = muted
  if ((d.disclosure_type || '') === 'FR') return 'var(--grn)';
  return 'var(--t3)';
}

function _kapTimeAgo(iso){
  if (!iso) return '';
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return '';
  const ms = Date.now() - dt.getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'az önce';
  if (min < 60) return `${min} dk önce`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} sa önce`;
  return `${Math.round(hr / 24)} gün önce`;
}

function renderBilancolarPage(){
  const pg = $('pg-bilancolar');
  if (!S.kap) {
    pg.innerHTML = _skelHeader('📰 Bilanço Akışı — yükleniyor…') + _skelTwoPane();
    loadBilancolar().then(() => renderBilancolarPage());
    return;
  }
  const recent = S.kap.recent || [];
  const calendar = S.kap.calendar || {};
  const filt = S._kapFilter || 'all';
  // Tahtacı PR A: subject-text classifier (mirrors data/kap_client.py
  // OPERATOR_SIGNAL_PATTERNS). Used to power the "Operatör" filter
  // chip and decorate cards with a 🚨 badge.
  const _opPatterns = {
    INSIDER:        ['pay alım satım bildirim','pay sahipliği bildirim',
                     'değişen pay sahipliği','yönetim kurulu üyesi pay'],
    KAP_ALERT:      ['olağan dışı fiyat','olağandışı fiyat','olağan dışı miktar'],
    BUYBACK:        ['pay geri alım','geri alım program','pay alımı programı'],
    MNA:            ['finansal duran varlık edinim','birleşme',
                     'devralma','bağlı ortaklık devri','satın alma'],
    CAPITAL_CHANGE: ['sermaye artırım','bedelsiz sermaye',
                     'bedelli sermaye','sermaye azaltım'],
    MGMT_CHANGE:    ['yönetim kurulu','yönetici atama',
                     'genel müdür','yönetici değişiklik'],
  };
  const _opTag = (subj) => {
    const s = (subj || '').toLowerCase();
    for (const [tag, needles] of Object.entries(_opPatterns)) {
      if (needles.some(n => s.includes(n))) return tag;
    }
    return null;
  };
  const _opLabel = {
    INSIDER:        '👤 İçeriden Alım',
    KAP_ALERT:      '⚠️ KAP Uyarısı',
    BUYBACK:        '💰 Pay Geri Alım',
    MNA:            '🤝 Birleşme/Devralma',
    CAPITAL_CHANGE: '📈 Sermaye',
    MGMT_CHANGE:    '👔 Yönetim',
  };
  const isOperator = (d) => _opTag(d.subject) !== null;
  const isWeek = (d) => (Date.now() - new Date(d.publish_date).getTime()) < 7 * 24 * 3600 * 1000;
  const isToday = (d) => (Date.now() - new Date(d.publish_date).getTime()) < 24 * 3600 * 1000;
  const isWatched = (d) => (S.wl || []).includes(d.ticker);

  const filtered = filt === 'wl' ? recent.filter(isWatched)
    : filt === 'today'    ? recent.filter(isToday)
    : filt === 'week'     ? recent.filter(isWeek)
    : filt === 'operator' ? recent.filter(isOperator)
    : recent;

  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--acc)">📰 Bilanço Akışı — KAP</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Son ${recent.length} açıklama · Watchlist: ${(S.wl||[]).length} hisse takvimde</p>
    </div>
    <button class="btn btn-grn" onclick="loadBilancolar(true).then(()=>renderBilancolarPage())">🔄</button>
  </div>`;

  // Filter chips — "Operatör" chip highlights tahtacı-signed disclosures
  // (insider buy, KAP warning, M&A, buyback...) which are the most
  // actionable category for BullWatch users.
  const chips = [
    ['all',      `Tümü (${recent.length})`,                                  'var(--acc)'],
    ['operator', `🚨 Operatör (${recent.filter(isOperator).length})`,         'var(--red)'],
    ['wl',       `Watchlist (${recent.filter(isWatched).length})`,            'var(--blu)'],
    ['today',    `Bugün (${recent.filter(isToday).length})`,                  'var(--grn)'],
    ['week',     `Bu Hafta (${recent.filter(isWeek).length})`,                'var(--cyn)'],
  ];
  h += `<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap">${chips.map(([k,l,c])=>`<button class="btn btn-sm ${filt===k?'':''}" style="${filt===k?`background:${c}20;border:1px solid ${c};color:${c}`:'background:var(--bg3);color:var(--t2)'}" onclick="S._kapFilter='${k}';renderBilancolarPage()">${l}</button>`).join('')}</div>`;

  // Two-pane layout
  h += '<div class="g2" style="gap:14px">';

  // LEFT: Recent disclosures feed
  h += '<div><div style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--grn);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">📥 Son Açıklamalar</div>';
  if (!filtered.length) {
    h += '<div class="emp"><h3 style="color:var(--t3);font-size:14px">Bu filtrede açıklama yok</h3></div>';
  } else {
    h += '<div class="card"><div class="card-b" style="max-height:680px;overflow-y:auto;padding:0">';
    filtered.forEach((d, i) => {
      const lbl = _kapItemLabel(d);
      const col = _kapItemColor(d);
      const lateBadge = d.is_late ? '<span class="pill p-red" style="font-size:9px;padding:1px 5px">geç</span>' : '';
      const ago = _kapTimeAgo(d.publish_date);
      const aiBadge = d.ai_summary ? '<span style="font-size:9px;color:var(--prp);font-weight:700;padding:1px 5px;background:rgba(186,104,200,.15);border-radius:3px;margin-left:6px">📖 AI</span>' : '';
      // Tahtacı PR A: operator signal badge — most-actionable signal type.
      // Renders next to the disclosure type label so the user can scan
      // the feed for "operator activity" without reading every subject.
      const _opTagForCard = _opTag(d.subject);
      const opBadge = _opTagForCard
        ? `<span style="font-size:9px;color:var(--red);font-weight:700;padding:1px 5px;background:rgba(239,83,80,.15);border:1px solid rgba(239,83,80,.35);border-radius:3px;margin-left:6px" title="Tahtacı imzalı bildirim: ${esc(_opLabel[_opTagForCard])}">${esc(_opLabel[_opTagForCard])}</span>`
        : '';
      // Faz 4: reaction badges (1d / 1w / 1m). Green positive, red negative,
      // grey when not yet available (horizon hasn't elapsed).
      const reactionBadge = (label, pct) => {
        if (pct == null) return `<span style="font-size:9px;color:var(--t4);padding:1px 5px;background:var(--bg3);border-radius:3px">${label}: —</span>`;
        const c = pct > 0 ? 'var(--grn)' : pct < 0 ? 'var(--red)' : 'var(--t3)';
        const sign = pct > 0 ? '+' : '';
        return `<span style="font-size:9px;color:${c};font-weight:700;padding:1px 5px;background:${c}15;border-radius:3px">${label}: ${sign}${pct.toFixed(1)}%</span>`;
      };
      // reactionsRow inlined into the row footer below to share a single
      // flex line with the AI button on wide screens (wraps on phone).
      // Compact mobile-first row. Two visible lines max for subject
      // (line-clamp), badge row wraps so badges never push off-screen,
      // reactions + AI button share one footer row on wide / wrap on phone.
      const subjLine = `${esc(d.kap_title || '')} · ${esc(d.subject || '')}`;
      h += `<div class="kap-row" style="padding:10px 12px;${i<filtered.length-1?'border-bottom:1px solid var(--bdr);':''}transition:background .1s">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;cursor:pointer" onclick="loadTicker('${esc(d.ticker)}')" onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;flex-wrap:wrap">
              <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--cyn)">${esc(d.ticker)}</span>
              <span style="font-size:11px;color:${col};font-weight:600">${esc(lbl)}</span>
              ${lateBadge}${aiBadge}${opBadge}
            </div>
            <div class="kap-row-subj" style="font-size:11px;color:var(--t3);line-height:1.4">${subjLine}</div>
          </div>
          <div style="text-align:right;font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;flex-shrink:0;white-space:nowrap">${ago}</div>
        </div>
        <div class="kap-row-footer" style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <div style="display:flex;gap:4px;font-family:'JetBrains Mono',monospace;flex-wrap:wrap">
            ${reactionBadge('1g', d.reaction_1d_pct)}
            ${reactionBadge('1h', d.reaction_1w_pct)}
            ${reactionBadge('1a', d.reaction_1m_pct)}
          </div>
          <button class="btn btn-sm" style="background:var(--bg3);color:var(--prp);font-size:10px;padding:3px 8px;margin-left:auto" onclick="event.stopPropagation();openKapAnalysis(${d.disclosure_index})">${d.ai_summary ? '📖 AI' : '🤖 AI Üret'}</button>
        </div>
      </div>`;
    });
    h += '</div></div>';
  }
  h += '</div>';

  // RIGHT: Upcoming calendar (watchlist)
  h += '<div><div style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--blu);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">📅 Yaklaşan Açıklamalar (Watchlist)</div>';
  const wlTickers = Object.keys(calendar);
  if (!wlTickers.length) {
    h += '<div class="emp" style="padding:30px 20px"><h3 style="color:var(--t3);font-size:13px;margin-bottom:6px">Watchlist boş</h3><p style="color:var(--t4);font-size:11px;line-height:1.6">Bir hisseyi watchlist\'e ekle, sıradaki bilanço açıklamasını burada gör.</p></div>';
  } else {
    // Flatten + group by ticker
    h += '<div class="card"><div class="card-b" style="max-height:680px;overflow-y:auto;padding:0">';
    wlTickers.forEach((tk, tIdx) => {
      const entries = (calendar[tk] || []).filter(e => e.subject && e.subject.toLowerCase().includes('finansal'));
      if (!entries.length) return;
      h += `<div style="padding:8px 14px;${tIdx<wlTickers.length-1?'border-bottom:1px solid var(--bdr);':''}">
        <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--cyn);margin-bottom:4px;cursor:pointer" onclick="loadTicker('${esc(tk)}')">${esc(tk)}</div>`;
      entries.slice(0, 3).forEach(e => {
        h += `<div style="font-size:10px;color:var(--t3);padding:2px 0">${esc(e.ruleTypeTerm || '')} · ${esc(e.subject || '')} · <span style="color:var(--t2)">${esc(e.startDate || '')}–${esc(e.endDate || '')}</span></div>`;
      });
      h += '</div>';
    });
    h += '</div></div>';
  }
  h += '</div>';

  h += '</div>'; // close .g2

  // Help footer
  h += `<div style="margin-top:14px;padding:10px 14px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t3);line-height:1.6">
    📡 <b style="color:var(--t2)">Veri kaynağı:</b> KAP (Kamuyu Aydınlatma Platformu) · 5 dk peak / 30 dk off-hours güncellenir.
    Yeni bilanço açıklaması → ilgili hissenin <b>skoru otomatik tazelenir</b> (Plan C). Tıkla → hisse detayı.
  </div>`;

  pg.innerHTML = h;
}

// AI yorum modalı — disclosure card'daki 📖 buton'undan açılır.
// İlk açılışta cached yorumu gösterir; yoksa async olarak analiz tetikler
// ve hazır olunca aynı modalı doldurur.
async function openKapAnalysis(disclosureIndex){
  // Build modal shell first so user sees immediate feedback
  const existing = document.getElementById('kapAnalysisOv');
  if (existing) existing.remove();
  const ov = document.createElement('div');
  ov.id = 'kapAnalysisOv';
  ov.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:9998;display:flex;align-items:center;justify-content:center;padding:20px';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<div style="background:var(--bg1);border:1px solid var(--bdr);border-radius:var(--rad2);max-width:680px;width:100%;max-height:80vh;overflow-y:auto;padding:24px;position:relative">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h3 style="font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--prp);margin:0">🤖 KAP Bilanço AI Analizi</h3>
      <button onclick="document.getElementById('kapAnalysisOv').remove()" style="background:var(--bg3);color:var(--t2);border:1px solid var(--bdr);border-radius:var(--rad);padding:4px 10px;cursor:pointer;font-size:13px">Kapat</button>
    </div>
    <div id="kapAnalysisBody"><div class="ld"><div class="sp"></div><div class="ld-t">Disclosure yükleniyor...</div></div></div>
    <p style="font-size:10px;color:var(--t4);margin-top:14px;line-height:1.6">⚠️ AI analizi geçmiş veri + mevcut bilanço metriklerine dayanır. Yatırım tavsiyesi değildir. Karar kullanıcıya aittir.</p>
  </div>`;
  document.body.appendChild(ov);

  const body = document.getElementById('kapAnalysisBody');
  // Fetch the disclosure detail; if ai_summary exists we render it, else trigger analysis
  let disclosure = null;
  try {
    const r = await api(`/api/kap/disclosure/${disclosureIndex}`);
    disclosure = r && r.disclosure;
  } catch (e) {
    body.innerHTML = '<div class="emp"><h3 style="color:var(--red);font-size:14px">Disclosure alınamadı</h3></div>';
    return;
  }
  if (!disclosure) {
    body.innerHTML = '<div class="emp"><h3 style="color:var(--red);font-size:14px">Disclosure bulunamadı</h3></div>';
    return;
  }

  const header = `<div style="margin-bottom:14px;padding:12px;background:var(--bg3);border-radius:var(--rad);border-left:3px solid var(--prp)">
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">
      <span style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:var(--cyn)">${esc(disclosure.ticker)}</span>
      <span style="font-size:12px;color:var(--t2)">${esc(disclosure.kap_title || '')}</span>
    </div>
    <div style="font-size:12px;color:var(--t2)">${esc(disclosure.year || '')} ${esc(disclosure.rule_type || '')} · ${esc(disclosure.subject || '')}</div>
    <div style="font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace;margin-top:4px">${esc(disclosure.publish_date_raw || disclosure.publish_date || '')}</div>
  </div>`;

  if (disclosure.ai_summary) {
    body.innerHTML = header + _renderKapAiSummary(disclosure.ai_summary);
    return;
  }

  // No cached AI summary — trigger generation
  body.innerHTML = header + '<div class="ld"><div class="sp"></div><div class="ld-t">AI analizi üretiliyor… ~10-20 sn</div></div>';
  try {
    const r = await api(`/api/kap/disclosure/${disclosureIndex}/analyze`, { method: 'POST' });
    if (r && r.ai_summary) {
      body.innerHTML = header + _renderKapAiSummary(r.ai_summary);
    } else {
      body.innerHTML = header + '<div class="emp"><h3 style="color:var(--ylw);font-size:14px">AI analizi üretilemedi</h3><p style="font-size:11px;color:var(--t3)">Tekrar denemek için kapatıp tekrar açabilirsiniz.</p></div>';
    }
  } catch (e) {
    body.innerHTML = header + `<div class="emp"><h3 style="color:var(--red);font-size:14px">AI servisi hata verdi</h3><p style="font-size:11px;color:var(--t3)">${esc(e.message || '')}</p></div>`;
  }
}

// Render AI summary text into pretty sections (ÖZET / POZİTİF / ...)
function _renderKapAiSummary(text){
  const sections = [
    { tag: 'ÖZET',    label: '🎯 Özet',           color: 'var(--cyn)' },
    { tag: 'POZİTİF', label: '✓ Pozitif',         color: 'var(--grn)' },
    { tag: 'NEGATİF', label: '⚠ Negatif',          color: 'var(--red)' },
    { tag: 'DEĞİŞİM', label: '📊 Değişim',        color: 'var(--ylw)' },
    { tag: 'SEKTÖR',  label: '🏢 Sektör Bağlamı', color: 'var(--blu)' },
    { tag: 'TAKİP',   label: '👁 Takip Noktaları', color: 'var(--prp)' },
  ];
  let html = '';
  let hadStructured = false;
  sections.forEach(s => {
    const re = new RegExp(`^\\s*${s.tag}\\s*:\\s*(.+?)(?=\\n\\s*(?:ÖZET|POZİTİF|NEGATİF|DEĞİŞİM|SEKTÖR|TAKİP)\\s*:|$)`, 'sm');
    const m = text.match(re);
    if (m) {
      hadStructured = true;
      html += `<div style="margin-bottom:10px;padding:10px 14px;background:var(--bg2);border-left:3px solid ${s.color};border-radius:var(--rad)">
        <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${s.color};text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">${s.label}</div>
        <div style="font-size:13px;color:var(--t1);line-height:1.6">${esc(m[1].trim())}</div>
      </div>`;
    }
  });
  // If AI ignored the format, just show raw text
  if (!hadStructured) {
    html = `<div style="padding:12px;background:var(--bg2);border-radius:var(--rad);font-size:13px;color:var(--t1);line-height:1.7;white-space:pre-wrap">${esc(text)}</div>`;
  }
  return html;
}

// ===== MAKRO PAGE =====
function renderMakroPage(){const pg=$('pg-makro');if(!S.macro){pg.innerHTML='<div class="ld"><div class="sp"></div><div class="ld-t">Makro verileri yükleniyor...</div></div>';loadMacro().then(()=>renderMakroPage());return;}const items=S.macro.items||[];if(!items.length){pg.innerHTML='<div class="emp"><h3 style="color:var(--t2)">Makro veri alınamadı</h3></div>';return;}const cats={turkiye:[],em:[],global:[],emtia:[]};items.forEach(m=>cats[m.category]?.push(m));const emSorted=[...cats.em,...cats.turkiye.filter(m=>m.key==='XU030'||m.key==='XU100')].sort((a,b)=>(b.ytd_pct||0)-(a.ytd_pct||0));let h='<div id="macroDecisionBlock"><div class="ld"><div class="sp"></div><div class="ld-t">Karar motoru hesaplanıyor...</div></div></div>';
// === FAİZ & RİSK KARTI ===
const rates=S.macro.rates||[];
if(rates.length){
h+=`<div class="card" style="margin-bottom:14px;border:1px solid rgba(255,179,0,.2)"><div class="card-h"><span class="card-t">🏦 Faiz & Risk Göstergeleri</span><span style="font-size:9px;color:var(--t4);font-family:'JetBrains Mono',monospace">Manuel veri</span></div><div class="card-b"><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">`;
rates.forEach(r=>{
  const chg=r.rate-r.prev;const chgCol=chg>0?'var(--red)':chg<0?'var(--grn)':'var(--t3)';
  const arrow=chg>0?'▲':chg<0?'▼':'—';
  h+=`<div style="background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);padding:10px;text-align:center">`;
  h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3);text-transform:uppercase;margin-bottom:4px">${esc(r.flag||'')} ${esc(r.name)}</div>`;
  h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:var(--t1)">${r.unit==='bps'?r.rate:r.rate.toFixed(2)}${esc(r.unit==='bps'?' bps':'%')}</div>`;
  h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${chgCol};margin-top:2px">${arrow} ${chg!==0?Math.abs(chg).toFixed(r.unit==='bps'?0:2):'Sabit'}</div>`;
  h+=`<div style="font-size:8px;color:var(--t4);margin-top:3px">${esc(r.note||'')} · ${esc(r.updated||'')}</div>`;
  h+=`</div>`;
});
h+=`</div></div></div>`;
}
h+=`<div class="card" style="margin-bottom:14px"><div class="card-h"><span class="card-t">🏁 EM YTD Siralama</span></div><div class="card-b" style="overflow-x:auto"><div style="margin-bottom:6px;display:flex;padding:4px 10px;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t4);text-transform:uppercase"><span style="width:24px">#</span><span style="width:24px"></span><span style="flex:1">Endeks</span><span style="width:80px;text-align:right">Fiyat</span><span style="width:65px;text-align:right">Gun</span><span style="width:65px;text-align:right">1H</span><span style="width:65px;text-align:right">1A</span><span style="width:65px;text-align:right">YTD</span></div>`;
emSorted.forEach((m,i)=>{const isTR=m.key==='XU030'||m.key==='XU100';h+=`<div class="em-row" style="${isTR?'background:rgba(0,230,118,.05);border-left:2px solid var(--grn)':''}"><span class="em-rank">${i+1}</span><span class="em-flag">${esc(m.flag||'')}</span><span class="em-name">${esc(m.name)}</span><span class="em-price">${fN(m.price,2)}</span><span class="em-chg" style="color:${cC(m.change_pct)}">${cS(m.change_pct)}%</span><span class="em-chg" style="color:${cC(m.w1_pct||0)}">${m.w1_pct!=null?cS(m.w1_pct)+'%':'—'}</span><span class="em-chg" style="color:${cC(m.m1_pct||0)}">${m.m1_pct!=null?cS(m.m1_pct)+'%':'—'}</span><span class="em-ytd" style="color:${cC(m.ytd_pct||0)}">${m.ytd_pct!=null?cS(m.ytd_pct)+'%':'—'}</span></div>`;});h+=`</div></div>`;
for(const[cat,label]of[['turkiye','🇹🇷 Turkiye'],['global','🌐 Global'],['emtia','🛢️ Emtia']]){const list=cats[cat]||[];if(!list.length)continue;h+=`<h3 style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t2);margin:14px 0 8px;text-transform:uppercase">${label}</h3><div class="g4" style="margin-bottom:12px">`;list.forEach(m=>{h+=`<div class="mac" style="padding:12px"><div class="mac-s">${esc(m.flag||'')} ${esc(m.name)}</div><div class="mac-p" style="font-size:18px">${fN(m.price,m.key?.includes('TRY')?4:2)}</div><div class="mac-c" style="color:${cC(m.change_pct)};font-size:12px">${cS(m.change_pct)}%</div><div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3);margin-top:4px">YTD: <span style="color:${cC(m.ytd_pct||0)};font-weight:700">${m.ytd_pct!=null?cS(m.ytd_pct)+'%':'—'}</span></div></div>`;});h+='</div>';}pg.innerHTML=h;loadMacroDecision();}
async function loadHomeAction(){const el=$('homeActionText');if(!el)return;try{const d=await api('/api/macro/decision');if(d.action_summary){const rc=d.regime==='RISK_ON'?'var(--grn)':d.regime==='RISK_OFF'?'var(--red)':'var(--ylw)';const rl=d.regime==='RISK_ON'?'Risk On':d.regime==='RISK_OFF'?'Risk Off':'Nötr';el.innerHTML=`<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:${rc};padding:2px 8px;border:1px solid ${rc};border-radius:4px">● ${esc(rl)}</span><span style="font-size:9px;color:var(--t4)">${d.confidence==='HIGH'?'Güven: Yüksek':d.confidence==='MEDIUM'?'Güven: Orta':'Güven: Düşük'}</span></div><div style="line-height:1.7">${esc(d.action_summary)}</div>${d.contradictions&&d.contradictions.length?`<div style="margin-top:8px;padding:8px 10px;background:rgba(255,179,0,.08);border-radius:4px;font-size:var(--fs-sm);color:var(--ylw)">⚠️ ${esc(d.contradictions[0].message)}</div>`:''}`;}else{el.innerHTML='<span style="color:var(--t3)">Makro veri yetersiz</span>';}}catch(e){el.innerHTML='<span style="color:var(--t3)">Yüklenemedi</span>';}}
async function loadMacroDecision(){
const box=$('macroDecisionBlock');if(!box)return;
try{
let d;for(let _r=0;_r<3;_r++){try{d=await api('/api/macro/decision');break;}catch(e){if(_r<2){await new Promise(r=>setTimeout(r,3000));box.innerHTML='<div class="ld" style="padding:14px"><div class="sp"></div><div class="ld-t">Makro veri bekleniyor...</div></div>';}else throw e;}}
const rc=d.regime==='RISK_ON'?'var(--grn)':d.regime==='RISK_OFF'?'var(--red)':'var(--ylw)';
const rl=d.regime==='RISK_ON'?'RİSK ON':d.regime==='RISK_OFF'?'RİSK OFF':'NÖTR';
const cc=d.confidence==='HIGH'?'Yüksek':d.confidence==='MEDIUM'?'Orta':'Düşük';
let h='';
// HERO: Regime
h+=`<div style="margin-bottom:14px;padding:20px;background:linear-gradient(135deg,rgba(${d.regime==='RISK_ON'?'0,230,118':d.regime==='RISK_OFF'?'255,82,82':'255,179,0'},.08),transparent);border:1px solid ${rc};border-radius:var(--rad)">`;
h+=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><div style="display:flex;align-items:center;gap:10px"><span style="font-size:28px;font-weight:900;font-family:'JetBrains Mono',monospace;color:${rc}">● ${rl}</span><span style="font-size:11px;color:var(--t3);font-family:'JetBrains Mono',monospace">Güven: ${cc}</span></div><span style="font-size:9px;color:var(--t4);font-family:'JetBrains Mono',monospace">${d.computed_at?new Date(d.computed_at).toLocaleString('tr-TR'):''}</span></div>`;
h+=`<div style="font-size:var(--fs-base);color:var(--t1);line-height:1.6">${esc(d.explanation)}</div>`;
if(d.confidence==='LOW'){h+=`<div style="margin-top:8px;padding:6px 10px;background:rgba(255,179,0,.1);border-radius:4px;font-size:var(--fs-xs);color:var(--ylw)">⚠️ Veri kalitesi düşük — bu rejim değerlendirmesi sınırlı veriye dayanıyor.</div>`;}
h+=`</div>`;
// SIGNALS TABLE
if(d.signals&&d.signals.length){
h+=`<div class="card" style="margin-bottom:14px"><div class="card-h"><span class="card-t">📡 Temel Sinyaller</span></div><div class="card-b">`;
d.signals.forEach(s=>{
const sc=s.score===1?'var(--grn)':s.score===-1?'var(--red)':'var(--t3)';
const icon=s.score===1?'🟢':s.score===-1?'🔴':'⚪';
const srcBadge=s.source==='tahmini'?`<span style="font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--ylw);padding:1px 4px;border:1px solid var(--ylw);border-radius:2px">TAHMİNİ</span>`:s.source==='günlük'?`<span style="font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--blu);padding:1px 4px;border:1px solid var(--bdr);border-radius:2px">GÜNLÜK</span>`:s.source==='eski'?`<span style="font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--red);padding:1px 4px;border:1px solid var(--red);border-radius:2px">ESKİ</span>`:`<span style="font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--t4);padding:1px 4px;border:1px solid var(--bdr);border-radius:2px">${esc(s.source).toUpperCase()}</span>`;
h+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bdr)"><div style="display:flex;align-items:center;gap:8px"><span>${icon}</span><span style="color:var(--t1);font-size:var(--fs-sm)">${esc(s.name)}</span></div><div style="display:flex;align-items:center;gap:8px"><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);color:${sc}">${esc(s.note)}</span><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);color:${sc};font-weight:700">${esc(s.label)}</span>${srcBadge}</div></div>`;
});
h+=`</div></div>`;}
// CONTRADICTIONS (conditional)
if(d.contradictions&&d.contradictions.length){
h+=`<div style="margin-bottom:14px;padding:14px;background:rgba(255,179,0,.08);border:1px solid rgba(255,179,0,.3);border-radius:var(--rad)"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ylw);text-transform:uppercase;margin-bottom:8px">⚠️ Dikkat: Çelişkili Sinyal</div>`;
d.contradictions.forEach(c=>{h+=`<div style="font-size:var(--fs-sm);color:var(--t1);line-height:1.6;margin-bottom:6px">${esc(c.message)}</div>`;});
h+=`</div>`;}
// SECTOR ROTATION
if(d.sectors){
h+=`<div class="card" style="margin-bottom:14px"><div class="card-h"><span class="card-t">🔄 Bu Ortamda Hangi Sektörler?</span><span style="font-family:'JetBrains Mono',monospace;font-size:8px;color:var(--ylw);padding:2px 6px;border:1px solid var(--ylw);border-radius:3px">Editöryal görüş</span></div><div class="card-b"><div style="display:flex;gap:12px;flex-wrap:wrap">`;
h+=`<div style="flex:1;min-width:140px"><div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--grn);text-transform:uppercase;margin-bottom:6px">Güçlü</div>`;
(d.sectors.strong||[]).forEach(s=>{h+=`<div style="padding:4px 10px;margin-bottom:4px;background:rgba(0,230,118,.08);border-radius:4px;font-size:var(--fs-sm);color:var(--t1)">${esc(s)}</div>`;});
h+=`</div><div style="flex:1;min-width:140px"><div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--red);text-transform:uppercase;margin-bottom:6px">Zayıf</div>`;
(d.sectors.weak||[]).forEach(s=>{h+=`<div style="padding:4px 10px;margin-bottom:4px;background:rgba(255,82,82,.08);border-radius:4px;font-size:var(--fs-sm);color:var(--t2)">${esc(s)}</div>`;});
h+=`</div></div></div></div>`;}
// ACTION SUMMARY
if(d.action_summary){
h+=`<div style="margin-bottom:14px;padding:16px;background:linear-gradient(135deg,rgba(255,179,0,.06),rgba(100,181,246,.04));border:1px solid rgba(255,179,0,.2);border-radius:var(--rad)">`;
h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">📋 Bugün Ne Yapmalı?</div>`;
h+=`<div style="font-size:var(--fs-base);color:var(--t1);line-height:1.7">${esc(d.action_summary)}</div>`;
h+=`</div>`;}
// AI ROLES BUTTON — AI Consolidation: single Claude, "external brief"
// (Perplexity) button removed since that provider is retired.
h+=`<div style="margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm btn-blu" onclick="loadMacroRoles()">🤖 AI Makro Yorumu</button></div><div id="macroRolesBlock"></div>`;
// FRESHNESS
if(d.freshness&&d.freshness.length){
h+=`<div style="margin-top:8px;padding:10px;background:var(--bg3);border-radius:var(--rad)"><div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t4);text-transform:uppercase;margin-bottom:6px">Veri Kaynakları</div><div style="display:flex;flex-wrap:wrap;gap:6px">`;
d.freshness.forEach(f=>{
const col=f.stale?'var(--red)':'var(--t3)';
const src=f.source==='günlük'?'🔵':f.source==='tahmini'?'🟡':f.source==='eski'?'🔴':f.source==='yok'?'⚫':'🔵';
h+=`<span style="font-family:'JetBrains Mono',monospace;font-size:9px;color:${col}">${src} ${esc(f.signal)} · ${esc(f.source)}</span>`;
});
h+=`</div></div>`;}
box.innerHTML=h;
}catch(e){console.error('macro decision:',e);box.innerHTML=`<div class="card" style="margin-bottom:14px;border-color:var(--ylw)"><div class="card-b"><span style="color:var(--ylw)">Karar motoru yüklenemedi</span></div></div>`;}}
async function loadMacroRoles(){
const box=$('macroRolesBlock');if(!box)return;
box.innerHTML='<div class="ld" style="padding:20px"><div class="sp"></div><div class="ld-t">Claude makro yorumu hazırlıyor...</div></div>';
try{
const d=await api('/api/macro/ai-roles');
if(!d.roles||d.error){box.innerHTML=`<div class="aib" style="margin-bottom:14px;border-color:var(--ylw)"><div class="aib-tx" style="color:var(--ylw)">${esc(d.error||'AI kullanılamıyor')}</div></div>`;return;}
let h='<div style="display:grid;grid-template-columns:1fr;gap:12px">';
const order=['interpreter','risk_controller','action_coach','reality_checker'];
const colors=['var(--blu)','var(--ylw)','var(--grn)','var(--orn)'];
order.forEach((key,i)=>{
const r=d.roles[key];if(!r)return;
const fb=r.is_fallback?'<span style="font-family:\'JetBrains Mono\',monospace;font-size:8px;color:var(--ylw);margin-left:6px;padding:1px 5px;border:1px solid var(--ylw);border-radius:3px">Veri yetersiz</span>':'<span style="font-family:\'JetBrains Mono\',monospace;font-size:8px;color:var(--t4);margin-left:6px;padding:1px 5px;border:1px solid var(--bdr);border-radius:3px">AI Yorum</span>';
h+=`<div style="padding:14px;background:var(--bg3);border:1px solid ${colors[i]};border-radius:var(--rad)">`;
h+=`<div style="display:flex;align-items:center;font-family:'JetBrains Mono',monospace;font-size:11px;color:${colors[i]};text-transform:uppercase;margin-bottom:8px">${esc(r.icon)} ${esc(r.label)}${fb}</div>`;
h+=`<div style="font-size:var(--fs-sm);color:var(--t1);line-height:1.6">${esc(r.commentary).replace(/\n/g,'<br>')}</div>`;
h+=`</div>`;
});
h+='</div>';
box.innerHTML=h;
}catch(e){box.innerHTML='';console.error('macro roles:',e);}}
async function loadMacroAI(){/* DEPRECATED — replaced by loadMacroRoles */}
// loadExternalBrief removed — the Perplexity-backed "Harici Piyasa
// Özeti" was retired in the AI Consolidation (Claude-only).

// ===== RADAR PAGE =====
// Veri Tazeliği summary banner — Radar üstüne fresh/old/stale dağılımını
// gösterir. compute_summary endpoint'ini tetikler ve sonucu S.diagFresh'e
// yazar (her satırın rozeti buradan okur). 5 dk cache.
async function loadRadarFreshness(force){
  if (!force && S.diagFreshSummary && (Date.now() - (S.diagFreshFetchedAt||0)) < 5*60*1000) return;
  const sc = S.scan;
  if (!sc || !sc.items || !sc.items.length) return;
  // En fazla 60 ticker — Radar zaten paged
  const tickers = sc.items.slice(0, 60).map(i => i.ticker).join(',');
  try {
    const r = await api('/api/diag/fundamentals?tickers=' + encodeURIComponent(tickers) + '&limit=60');
    const v = (r && (r.value || r)) || {};
    S.diagFreshSummary = v.summary || {};
    S.diagFreshThresholds = v.thresholds || {};
    S.diagFresh = {};
    (v.items || []).forEach(it => { S.diagFresh[(it.ticker||'').toUpperCase()] = it; });
    S.diagFreshFetchedAt = Date.now();
    if (S.page === 'radar') renderRadarPage();
  } catch (e) {
    console.warn('freshness fetch failed', e);
  }
}

function _radarFreshBanner(){
  const s = S.diagFreshSummary;
  if (!s || !s.total) return '';
  const th = S.diagFreshThresholds || {};
  const pct = (n) => s.total ? `${Math.round(n*100/s.total)}%` : '—';
  const dot = (col, ic, lbl, n) => `<span style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:var(--rad);background:${col}15;color:${col};font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);font-weight:700"><span>${ic}</span>${esc(lbl)} <b style="margin-left:2px">${n}</b> <span style="opacity:.7;font-weight:400">(${pct(n)})</span></span>`;
  const staleN = (s.stale||0) + (s.unknown||0);
  const warn = staleN > Math.max(2, s.total*0.2) ? '<span style="margin-left:8px;color:var(--orn);font-size:var(--fs-xs)">⚠️ Veri pipeline\'ı kontrol et</span>' : '';
  const stalePanel = staleN > 0
    ? `<button class="btn btn-sm" style="background:rgba(239,83,80,.15);border:1px solid var(--red);color:var(--red);font-size:11px;padding:4px 10px;min-height:28px" onclick="showStalePanel()">⚠️ ${staleN} ticker stale → İncele</button>`
    : '';
  return `<div style="margin-bottom:14px;padding:12px 14px;background:var(--bg2);border:1px solid var(--bdr);border-left:3px solid var(--cyn);border-radius:0 var(--rad) var(--rad) 0">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:var(--fs-sm);color:var(--t2)">
      <span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t4);text-transform:uppercase;letter-spacing:.5px;margin-right:6px">📅 Veri Tazeliği</span>
      ${dot('var(--grn)','✓','Fresh', s.fresh||0)}
      ${dot('var(--ylw)','◷','Old',   s.old||0)}
      ${dot('var(--red)','✕','Stale', s.stale||0)}
      ${dot('var(--t4)','?','Bilinmiyor', s.unknown||0)}
      ${stalePanel}
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2);font-size:10px;padding:3px 8px;min-height:24px" onclick="loadRadarFreshness(true)">🔄 Yenile</button>
    </div>
    <div style="font-size:var(--fs-xs);color:var(--t4);margin-top:6px">Fresh = borsapy son ${th.fresh_hours||26}sa içinde fetch · Stale = ${th.stale_hours||72}sa+ ${warn}</div>
    ${_radarKapHealthBanner()}
    ${_radarAutoRefreshBanner()}
  </div>`;
}

// KAP feed health summary — pipeline'ın üst kısmı çalışıyor mu?
// Background loop'un son cycle telemetry'sini gösterir + storage stats.
function _radarKapHealthBanner(){
  const k = S.kapHealth;
  if (!k) return '';
  const st = k.storage || {};
  const lc = k.last_cycle;
  const newestIso = st.newest_publish_date;
  const newestMs = newestIso ? new Date(newestIso).getTime() : null;
  const newestHrs = newestMs ? Math.round((Date.now() - newestMs)/3600000) : null;
  const total = (st.total_in_sqlite||0) + (st.total_in_redis||0);
  // Health verdict:
  //   green   — son disclosure ≤ 24h, last cycle çalışmış, 0 error
  //   yellow  — son disclosure 24-72h ya da last_cycle yok
  //   red     — son disclosure 72h+ ya da cycle error sayısı >0
  let col = 'var(--grn)', ic = '✓', verdict = 'Akış sağlıklı';
  if (lc && lc.errors > 0) { col = 'var(--red)'; ic = '✕'; verdict = `Loop ${lc.errors} hata aldı`; }
  else if (newestHrs == null) { col = 'var(--t4)'; ic = '?'; verdict = 'Henüz disclosure görülmemiş'; }
  else if (newestHrs > 72) { col = 'var(--red)'; ic = '✕'; verdict = `Son disclosure ${newestHrs}sa önce — feed durmuş olabilir`; }
  else if (newestHrs > 24 || !lc) { col = 'var(--ylw)'; ic = '◷'; verdict = lc ? `Son disclosure ${newestHrs}sa önce` : 'Loop henüz çalışmadı'; }
  const cycleLine = lc
    ? `Son poll: ${lc.duration_sec}s · ${lc.tickers_with_disclosures}/${lc.universe_size} ticker · ${lc.new_disclosures_persisted} yeni · ${lc.errors} hata`
    : 'Background loop henüz tetiklenmedi (uygulama yeni başlatıldı?)';
  return `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--bdr);display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:var(--fs-xs)">
    <span style="font-family:'JetBrains Mono',monospace;color:var(--t4);text-transform:uppercase;letter-spacing:.5px">📰 KAP Feed</span>
    <span style="display:inline-flex;align-items:center;gap:4px;color:${col};font-weight:700"><span>${ic}</span>${esc(verdict)}</span>
    <span style="color:var(--t3)">${esc(cycleLine)}</span>
    <span style="color:var(--t4);margin-left:auto">Storage: ${total} kayıt</span>
  </div>`;
}

async function loadKapHealth(){
  try {
    const r = await api('/api/kap/health');
    S.kapHealth = (r && (r.value || r)) || null;
    if (S.page === 'radar') renderRadarPage();
  } catch(e) {
    console.warn('kap health fetch failed', e);
  }
}

// Auto-refresh background loop telemetry — pasif sağlık satırı.
async function loadAutoRefreshStatus(){
  try {
    const r = await api('/api/diag/auto-refresh/status');
    S.autoRefresh = (r && (r.value || r)) || null;
    if (S.page === 'radar') renderRadarPage();
  } catch(e) {
    console.warn('auto-refresh status fetch failed', e);
  }
}

function _radarAutoRefreshBanner(){
  const ar = S.autoRefresh;
  if (!ar) return '';
  const lc = ar.last_cycle;
  const cfg = ar.config || {};
  const intervalHrs = cfg.interval_sec ? (cfg.interval_sec/3600).toFixed(0) : '?';
  let col = 'var(--t4)', ic = '?', verdict = 'Background loop henüz çalışmadı';
  let detail = `Her ${intervalHrs}sa\'te bir tetiklenir (max ${cfg.max_per_cycle||'?'} ticker/cycle)`;
  if (lc) {
    const ageMin = lc.finished_at ? Math.round((Date.now()/1000 - lc.finished_at)/60) : null;
    const ageStr = ageMin == null ? '?' : ageMin < 60 ? `${ageMin}dk önce` : ageMin < 1440 ? `${Math.round(ageMin/60)}sa önce` : `${Math.round(ageMin/1440)}g önce`;
    if (lc.failed > 0 && lc.succeeded === 0) {
      col = 'var(--red)'; ic = '✕'; verdict = `Son cycle başarısız (${lc.failed} hata)`;
    } else if (lc.attempted === 0) {
      col = 'var(--grn)'; ic = '✓'; verdict = `Aktif · ${ageStr} · refresh\'e gerek yoktu (0 stale)`;
    } else {
      col = 'var(--grn)'; ic = '✓'; verdict = `Aktif · ${ageStr}`;
    }
    detail = `Son: ${lc.attempted} ticker · ${lc.succeeded} başarılı · ${lc.failed} hata · ${lc.score_change_count||0} skor değişti${lc.avg_abs_delta?` (ortalama Δ ${lc.avg_abs_delta})`:''}`;
  }
  // Score changes liste — sadece anlamlı değişimler
  let changes = '';
  if (lc && lc.score_changes && lc.score_changes.length) {
    const top = lc.score_changes.slice(0, 5);
    changes = '<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px">';
    top.forEach(c => {
      const dCol = c.delta > 0 ? 'var(--grn)' : 'var(--red)';
      const sign = c.delta > 0 ? '+' : '';
      changes += `<span class="clk-t" onclick="showFreshModal('${esc(c.ticker)}')" style="font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 6px;background:${dCol}15;color:${dCol};border-radius:3px;cursor:pointer">${esc(c.ticker)} ${c.before}→${c.after} <b>${sign}${c.delta}</b></span>`;
    });
    changes += '</div>';
  }
  return `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--bdr);font-size:var(--fs-xs)">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-family:'JetBrains Mono',monospace;color:var(--t4);text-transform:uppercase;letter-spacing:.5px">🔄 Auto-Refresh</span>
      <span style="display:inline-flex;align-items:center;gap:4px;color:${col};font-weight:700"><span>${ic}</span>${esc(verdict)}</span>
      <span style="color:var(--t3)">${esc(detail)}</span>
    </div>
    ${changes}
  </div>`;
}

function renderRadarPage(){const pg=$('pg-radar');const sc=S.scan;if(!sc||!sc.items||!sc.items.length){pg.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px"><h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--cyn)">🏛️ Temel Analiz Radar</h2><button class="btn btn-grn" onclick="startScan()">▶ SCAN</button></div><div class="emp"><h3 style="color:var(--t2)">Henüz taranmadı</h3></div>`;return;}
  // Veri tazeliği — sayfa açıldıkça otomatik fetch (5dk cached)
  if (!S.diagFresh) { loadRadarFreshness(); }
  if (!S.kapHealth) { loadKapHealth(); }
  if (!S.autoRefresh) { loadAutoRefreshStatus(); }
  const sort=S._radarSort||'deger';pg.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px"><div><h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--cyn)">🏛️ Saf Değerleme Radarı — ${sc.items.length} Hisse</h2><p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">${sc.asof?new Date(sc.asof).toLocaleString('tr-TR'):''}</p></div><button class="btn btn-grn" onclick="startScan()">🔄</button></div>${_radarFreshBanner()}<div style="padding:12px 16px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-base);color:var(--t2);line-height:1.6"><b style="color:var(--cyn)">Saf Değerleme Radarı nasıl çalışır?</b> Uzun vadeli temel analiz tarayıcısı. ${sc.items.length} BIST hissesi 7 temel boyutta analiz edilir: Değerleme (F/K, PD/DD, FD/FAVÖK), Kalite (ROE, marjlar), Büyüme, Bilanço sağlamlığı (Altman Z, borç), Kâr Kalitesi (Beneish, nakit akış), Sermaye Kullanımı ve Rekabet Avantajı (marj stabilitesi). <span style="color:var(--ylw)">Kısa vadeli momentum ve teknik sinyaller için → Cross Hunter.</span></div><div class="card"><div class="card-b" style="overflow-x:auto">${renderRadarTbl(sc.items,sort)}</div></div>`;}

// Veri Tazeliği detay modal'ı — bir hissenin tüm freshness bundle'ını
// gösterir: borsapy fetch_at, latest_quarter, KAP son rapor, gap, uyarılar.
async function showFreshModal(ticker){
  // Audit fix: dedupe rapid double-clicks. If a freshness modal is
  // already open, close it first so we don't stack overlays — and we
  // tag the new one so subsequent triggers within ~300ms are no-ops.
  const existing = document.getElementById('freshModalOv');
  if (existing) existing.remove();
  const _now = Date.now();
  if (window.__lastFreshModalAt && (_now - window.__lastFreshModalAt) < 300) return;
  window.__lastFreshModalAt = _now;
  const ov = document.createElement('div');
  ov.id = 'freshModalOv';
  ov.className = 'mov';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<div style="background:var(--bg1);border:1px solid var(--bdr);border-radius:var(--rad);max-width:560px;width:100%;max-height:90vh;overflow-y:auto;padding:20px"><div style="text-align:center;color:var(--t3);padding:20px"><div class="sp" style="margin:0 auto 10px"></div>Yükleniyor…</div></div>`;
  document.body.appendChild(ov);
  let data = null;
  try {
    const r = await api('/api/diag/fundamentals/' + encodeURIComponent(ticker));
    data = (r && (r.value || r)) || null;
  } catch(e) {
    ov.querySelector('div > div').innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">Yüklenemedi: ${esc(String(e.message||e))}</div><div style="text-align:center"><button class="btn btn-sm" onclick="this.closest('.mov').remove()">Kapat</button></div>`;
    return;
  }
  if (!data) { ov.remove(); return; }
  const b = data.borsapy || {};
  const k = data.kap || {};
  const st = data.age_status || 'unknown';
  const stCol = {fresh:'var(--grn)',old:'var(--ylw)',stale:'var(--red)',unknown:'var(--t4)'}[st];
  const row = (lbl, val, col) => `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-sm)"><span style="color:var(--t3)">${esc(lbl)}</span><span style="font-family:'JetBrains Mono',monospace;color:${col||'var(--t1)'}">${val==null?'—':esc(String(val))}</span></div>`;
  const fmtIso = (s) => s ? new Date(s).toLocaleString('tr-TR') : '—';
  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px"><div><h3 style="font-family:'JetBrains Mono',monospace;color:var(--cyn);font-size:18px">📅 ${esc(data.ticker)} · Veri Tazeliği</h3><div style="font-size:11px;color:${stCol};text-transform:uppercase;letter-spacing:.5px;font-weight:700;margin-top:2px">${st}</div></div><button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="this.closest('.mov').remove()">✕</button></div>`;
  h += '<div style="margin-bottom:16px"><div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--cyn);letter-spacing:.5px;margin-bottom:6px">🐂 BORSAPY</div>';
  h += row('Son fetch', fmtIso(b.fetched_at));
  h += row('Yaş', b.age_hours!=null?`${b.age_hours.toFixed(1)} saat`:null, stCol);
  h += row('Son çeyrek (latest_quarter)', b.latest_quarter || null);
  h += row('Quarterly mevcut?', b.quarterly_available==null?null:(b.quarterly_available?'evet':'hayır'), b.quarterly_available?'var(--grn)':'var(--red)');
  h += row('Banka?', b.is_bank==null?null:(b.is_bank?'evet':'hayır'));
  h += row('Fetch attempts (son refresh)', b.fetch_attempts);
  h += '</div>';
  h += '<div style="margin-bottom:16px"><div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--ylw);letter-spacing:.5px;margin-bottom:6px">📰 KAP — SON FİNANSAL RAPOR</div>';
  if (Object.keys(k).length === 0) {
    h += '<div style="padding:12px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t3)">KAP storage\'da kayıt yok. (KAP feed loop running mu? <code>/api/kap/health</code>)</div>';
  } else {
    h += row('Yayın tarihi', fmtIso(k.publish_date));
    h += row('Yaş', k.age_days!=null?`${k.age_days.toFixed(1)} gün`:null);
    h += row('Tür', k.rule_type || null);
    h += row('Çeyrek/yıl', k.period && k.year ? `Q${k.period} ${k.year}` : (k.year||null));
    h += row('Konu', k.subject || null);
  }
  h += '</div>';
  if (data.gap_days != null) {
    const gapCol = data.gap_days > 1 ? 'var(--red)' : 'var(--grn)';
    const gapLbl = data.gap_days > 1 ? `+${data.gap_days.toFixed(1)}g — KAP ileride, borsapy geride` : `${data.gap_days.toFixed(1)}g — borsapy güncel`;
    h += `<div style="margin-bottom:16px;padding:10px 14px;background:${gapCol}10;border-left:3px solid ${gapCol};border-radius:0 var(--rad) var(--rad) 0;font-size:var(--fs-sm)"><b style="color:${gapCol}">Gap (KAP − borsapy):</b> <span style="font-family:'JetBrains Mono',monospace">${esc(gapLbl)}</span></div>`;
  }
  if (data.warnings && data.warnings.length) {
    h += '<div style="margin-bottom:8px;padding:10px 14px;background:rgba(255,167,38,.08);border-left:3px solid var(--orn);border-radius:0 var(--rad) var(--rad) 0"><div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--orn);letter-spacing:.5px;margin-bottom:6px">⚠️ UYARILAR</div>';
    data.warnings.forEach(w => { h += `<div style="font-size:var(--fs-sm);color:var(--t2);padding:3px 0">• ${esc(w)}</div>`; });
    h += '</div>';
  }
  // Score velocity — frozen verdict + 30-day stats
  const v = data.velocity;
  if (v && v.n_snapshots >= 2) {
    const frozenBar = v.frozen
      ? `<div style="margin-bottom:10px;padding:10px 14px;background:rgba(33,150,243,.10);border-left:3px solid var(--blu);border-radius:0 var(--rad) var(--rad) 0;font-size:var(--fs-sm)"><b style="color:var(--blu)">🧊 Skor donmuş</b> — son ${v.n_snapshots} snapshot, max günlük değişim ${v.max_jump}. Bu hisse için fundamentals gerçekten sabit mi, yoksa pipeline tıkalı mı? "🔄 Şimdi Yenile" ile test et.</div>`
      : '';
    h += frozenBar;
  }
  // 30-day score history sparkline — "skor gerçekten değişiyor mu?"
  h += `<div id="fmHist" style="margin-bottom:12px"></div>`;
  h += `<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
    <button id="fmRefBtn" class="btn btn-sm btn-grn" onclick="forceRefreshTicker('${esc(data.ticker)}', this)" style="flex:1;min-width:140px">🔄 Şimdi Yenile (cache'i kır + yeniden fetch)</button>
    <button class="btn btn-sm btn-blu" style="flex:1;min-width:140px" onclick="loadTicker('${esc(data.ticker)}');this.closest('.mov').remove()">Hisseyi Aç →</button>
  </div>`;
  h += `<div id="fmRefResult" style="margin-top:8px;font-size:11px;color:var(--t3)"></div>`;
  ov.querySelector('div').innerHTML = h;
  // Sparkline async
  loadFreshSparkline(data.ticker);
}

async function loadFreshSparkline(ticker){
  try {
    const r = await api('/api/diag/timeline/' + encodeURIComponent(ticker) + '?days=60');
    const v = (r && (r.value || r)) || {};
    const scoreEvents = v.score_events || [];
    const kapEvents = v.kap_events || [];
    const el = document.getElementById('fmHist');
    if (!el) return;
    if (!scoreEvents.length && !kapEvents.length) {
      el.innerHTML = `<div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);letter-spacing:.5px;margin-bottom:4px">📈 60 GÜNLÜK ZAMAN ÇİZGİSİ</div>
        <div style="padding:12px;background:var(--bg3);border-radius:var(--rad);font-size:11px;color:var(--t4);text-align:center">Henüz snapshot ya da KAP olayı yok.</div>`;
      return;
    }
    // x-axis: unified by date. Build a chronologically-anchored time range.
    const allDates = [...scoreEvents.map(e=>e.date), ...kapEvents.map(e=>new Date(e.date).toISOString().slice(0,10))];
    const dStart = new Date(Math.min(...allDates.map(d=>new Date(d).getTime())));
    const dEnd = new Date(Math.max(...allDates.map(d=>new Date(d).getTime())));
    const spanMs = Math.max(1, dEnd.getTime() - dStart.getTime());
    const dayMs = 24*3600*1000;
    const w = 540, hgt = 80, pad = 6;
    const xFor = (date) => pad + ((new Date(date).getTime() - dStart.getTime()) / spanMs) * (w - 2*pad);
    let svg = '';
    let summaryLine = '';
    if (scoreEvents.length) {
      const scores = scoreEvents.map(e=>e.score).filter(s=>s!=null);
      const min = Math.min(...scores), max = Math.max(...scores);
      const range = Math.max(1, max - min);
      const first = scores[0], last = scores[scores.length-1];
      const delta = last - first;
      const dCol = delta > 1 ? 'var(--grn)' : delta < -1 ? 'var(--red)' : 'var(--t3)';
      const dSign = delta > 0 ? '+' : '';
      const pts = scoreEvents.filter(e=>e.score!=null).map(e => {
        const x = xFor(e.date);
        const y = hgt - pad - ((e.score - min) / range) * (hgt - 2*pad);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      svg = `<polyline fill="none" stroke="${dCol}" stroke-width="2" points="${pts}" />`;
      summaryLine = `<div style="display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);margin-top:4px"><span>${esc(scoreEvents[0].date)} · ${first.toFixed(0)}</span><span style="color:${dCol};font-weight:700">${dSign}${delta.toFixed(1)} puan</span><span>${esc(scoreEvents[scoreEvents.length-1].date)} · ${last.toFixed(0)}</span></div><div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);margin-top:2px">min ${min.toFixed(0)} · max ${max.toFixed(0)} · range ${range.toFixed(0)} · ${scoreEvents.length} snapshot</div>`;
    }
    // KAP event vertical markers — orange dashed lines
    let kapMarkers = '';
    kapEvents.forEach(e => {
      const x = xFor(e.date);
      kapMarkers += `<line x1="${x.toFixed(1)}" y1="${pad}" x2="${x.toFixed(1)}" y2="${hgt-pad}" stroke="var(--orn)" stroke-width="1" stroke-dasharray="3 2" opacity=".8" />`;
      kapMarkers += `<circle cx="${x.toFixed(1)}" cy="${pad+2}" r="3" fill="var(--orn)" />`;
    });
    let html = `<div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--cyn);letter-spacing:.5px;margin-bottom:4px">📈 60 GÜNLÜK ZAMAN ÇİZGİSİ · ${scoreEvents.length} skor + ${kapEvents.length} KAP olayı</div>
      <div style="padding:8px 12px;background:var(--bg3);border-radius:var(--rad)">
        <svg width="100%" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" style="display:block">${kapMarkers}${svg}</svg>
        ${summaryLine || ''}`;
    // KAP events textual list — clickable, opens disclosure detail
    if (kapEvents.length) {
      html += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--bdr)"><div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--orn);letter-spacing:.5px;margin-bottom:6px">📰 KAP FİNANSAL RAPORLAR (zaman çizgisinde turuncu noktalar)</div>';
      kapEvents.slice().reverse().forEach(e => {
        const d = new Date(e.date);
        const dStr = d.toLocaleDateString('tr-TR', {day:'numeric',month:'short',year:'numeric'});
        const pq = e.period && e.year ? `Q${e.period} ${e.year}` : (e.year || '—');
        html += `<div style="display:flex;justify-content:space-between;font-size:11px;padding:3px 0;border-bottom:1px dashed var(--bdr)"><span style="color:var(--t3)">${esc(dStr)}</span><span style="color:var(--t2)">${esc(e.rule_type||'—')} · ${esc(pq)}</span></div>`;
      });
      html += '</div>';
    }
    html += '</div>';
    el.innerHTML = html;
  } catch(e) {
    console.warn('timeline fetch failed', e);
  }
}

// Stale tickers panel — large modal showing every ticker that needs
// attention, sortable, with a one-click "Tümünü Yenile" batch action.
// The batch endpoint refreshes up to 30 at a time (server-capped) with
// bounded parallelism so we don't hammer borsapy.
async function showStalePanel(){
  const ov = document.createElement('div');
  ov.className = 'mov';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<div id="stalePanelBody" style="background:var(--bg1);border:1px solid var(--bdr);border-radius:var(--rad);max-width:840px;width:100%;max-height:90vh;overflow-y:auto;padding:20px"><div style="text-align:center;color:var(--t3);padding:30px"><div class="sp" style="margin:0 auto 10px"></div>Stale ticker listesi yükleniyor…</div></div>`;
  document.body.appendChild(ov);

  let data = null;
  try {
    const r = await api('/api/diag/stale?threshold=stale&limit=60');
    data = (r && (r.value || r)) || null;
  } catch(e) {
    document.getElementById('stalePanelBody').innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">Yüklenemedi: ${esc(String(e.message||e))}</div><div style="text-align:center"><button class="btn btn-sm" onclick="this.closest('.mov').remove()">Kapat</button></div>`;
    return;
  }
  if (!data) { ov.remove(); return; }
  _renderStalePanel(data);
}

function _renderStalePanel(data){
  const body = document.getElementById('stalePanelBody');
  if (!body) return;
  const items = data.items || [];
  const s = data.summary || {};
  const th = data.thresholds || {};
  let h = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px">
    <div>
      <h3 style="font-family:'JetBrains Mono',monospace;color:var(--red);font-size:18px">⚠️ Stale / Unknown Ticker Listesi</h3>
      <div style="font-size:11px;color:var(--t3);margin-top:2px">${s.matched||0} / ${s.universe_size||0} ticker · Threshold: ${esc(s.threshold||'stale')}</div>
    </div>
    <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="this.closest('.mov').remove()">✕ Kapat</button>
  </div>`;

  h += `<div style="padding:10px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:12px;font-size:11px;color:var(--t2);line-height:1.55">
    💡 <b style="color:var(--cyn)">Stale</b> = borsapy son ${th.stale_hours||72}sa içinde refresh edilmemiş. <b>Unknown</b> = cache'te hiç giriş yok.<br>
    "Tümünü Yenile" cache'leri kırar ve borsapy'i tekrar çağırır — yaklaşık <b>${Math.max(15, Math.round((items.length||1)*4))}s</b> sürer (rate-limit + retry payı).
  </div>`;

  if (!items.length) {
    h += `<div class="emp" style="padding:30px 20px;text-align:center"><h4 style="color:var(--grn)">✓ Stale ticker yok</h4><p style="color:var(--t4);font-size:11px;margin-top:6px">Universe'in tümü fresh — pipeline sağlıklı.</p></div>`;
    body.innerHTML = h;
    return;
  }

  const tickerCsv = items.slice(0, 30).map(i => i.ticker).join(',');
  h += `<div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <button id="staleBatchBtn" class="btn btn-sm btn-grn" onclick="batchRefreshStale('${esc(tickerCsv)}', this)" style="flex:1;min-width:200px">🔄 İlk ${Math.min(items.length, 30)} ticker'ı yenile</button>
    <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="showStalePanel()">↻ Listeyi Yeniden Yükle</button>
  </div>
  <div id="staleBatchProgress" style="margin-bottom:10px;font-size:11px;color:var(--t3)"></div>`;

  h += `<div style="overflow-x:auto"><table class="dtb" style="width:100%;font-size:12px">
    <thead><tr>
      <th style="width:24px">#</th>
      <th>Hisse</th>
      <th>Durum</th>
      <th>Borsapy Yaş</th>
      <th>KAP Yaş</th>
      <th>Gap</th>
      <th>Latest Q</th>
      <th>Uyarılar</th>
    </tr></thead><tbody>`;
  items.forEach((it, i) => {
    const stCol = it.age_status === 'stale' ? 'var(--red)' : it.age_status === 'unknown' ? 'var(--t4)' : it.age_status === 'old' ? 'var(--ylw)' : 'var(--grn)';
    const ageStr = it.age_hours != null ? (it.age_hours > 48 ? `${(it.age_hours/24).toFixed(0)}g` : `${it.age_hours.toFixed(0)}sa`) : '—';
    const kapStr = it.kap_age_days != null ? `${it.kap_age_days.toFixed(0)}g` : '—';
    const gapStr = it.gap_days != null
      ? (it.gap_days > 1
          ? `<span style="color:var(--red);font-weight:700">+${it.gap_days.toFixed(0)}g ⚠</span>`
          : `<span style="color:var(--grn)">${it.gap_days.toFixed(0)}g</span>`)
      : '—';
    const warns = (it.warnings||[]).length;
    h += `<tr>
      <td style="color:var(--t4)">${i+1}</td>
      <td><span class="clk-t" onclick="event.stopPropagation();showFreshModal('${esc(it.ticker)}')" style="font-family:'JetBrains Mono',monospace;color:var(--cyn);font-weight:700">${esc(it.ticker)}</span></td>
      <td><span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${stCol};font-weight:700;text-transform:uppercase">${esc(it.age_status||'?')}</span></td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t2)">${ageStr}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t3)">${kapStr}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${gapStr}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t3)">${esc(it.latest_quarter||'—')}</td>
      <td style="font-size:11px;color:var(--orn)">${warns ? `<span title="${esc((it.warnings||[]).join(' · '))}">${warns} uyarı</span>` : '—'}</td>
    </tr>`;
  });
  h += '</tbody></table></div>';
  body.innerHTML = h;
}

async function batchRefreshStale(csv, btn){
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Yenileniyor… bekleyin'; }
  const prog = document.getElementById('staleBatchProgress');
  if (prog) prog.innerHTML = '<span style="color:var(--t4)">Borsapy çağrılıyor (bounded parallelism, ~4-30s/ticker)…</span>';
  try {
    const r = await fetch('/api/diag/fundamentals/batch-refresh?tickers=' + encodeURIComponent(csv) + '&max_concurrency=4', {method:'POST'});
    const j = await r.json();
    const v = j.value || j;
    const s = v.summary || {};
    const rows = v.items || [];
    let html = `<div style="padding:10px 14px;background:rgba(38,194,129,.10);border-left:3px solid var(--grn);border-radius:0 var(--rad) var(--rad) 0;margin-bottom:10px"><b style="color:var(--grn)">✓ Batch tamamlandı.</b> ${s.succeeded}/${s.requested} başarılı, ${s.failed} hata.</div>`;
    if (rows.length) {
      html += '<div style="font-size:11px;color:var(--t3);margin-bottom:6px">Sonuçlar:</div>';
      html += '<div style="display:flex;flex-direction:column;gap:4px;max-height:240px;overflow-y:auto">';
      rows.forEach(row => {
        const col = row.ok ? 'var(--grn)' : 'var(--red)';
        const ic = row.ok ? '✓' : '✕';
        const lq = row.new_latest_quarter ? ` · ${row.new_latest_quarter}` : '';
        const age = row.new_age_hours != null ? `${row.new_age_hours.toFixed(1)}sa` : '?';
        const errMsg = row.error ? ` — ${row.error}` : '';
        html += `<div style="display:flex;gap:8px;align-items:center;font-size:11px;font-family:'JetBrains Mono',monospace;padding:4px 8px;background:var(--bg3);border-radius:3px">
          <span style="color:${col};font-weight:700">${ic}</span>
          <span style="color:var(--cyn);min-width:60px">${esc(row.ticker)}</span>
          <span style="color:var(--t2)">→ ${esc(age)}${esc(lq)}</span>
          <span style="color:var(--t4);flex:1;text-align:right">${esc(errMsg)}</span>
        </div>`;
      });
      html += '</div>';
    }
    if (prog) prog.innerHTML = html;
    // Invalidate cached freshness summary so next load shows updated state
    S.diagFresh = null; S.diagFreshSummary = null; S.diagFreshFetchedAt = 0;
    // Auto-reload main freshness for the Radar banner
    setTimeout(() => loadRadarFreshness(true), 1000);
  } catch(e) {
    if (prog) prog.innerHTML = `<span style="color:var(--red)">Hata: ${esc(String(e.message||e))}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Tekrar Yenile'; }
  }
}

async function forceRefreshTicker(ticker, btn){
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Yenileniyor… (~10-30s)'; }
  const out = document.getElementById('fmRefResult');
  if (out) out.innerHTML = '<span style="color:var(--t4)">Cache invalidate ediliyor, borsapy yeniden çağrılıyor…</span>';
  try {
    const r = await fetch('/api/diag/fundamentals/' + encodeURIComponent(ticker) + '/refresh', {method:'POST'});
    const j = await r.json();
    const v = j.value || j;
    if (!v.analysis_ok && !v.after) {
      if (out) out.innerHTML = `<span style="color:var(--red)">Yenileme başarısız: ${esc(j.error || 'unknown')}</span>`;
      return;
    }
    const b = v.before || {}; const a = v.after || {};
    const bAge = b.borsapy?.age_hours; const aAge = a.borsapy?.age_hours;
    const scoreLine = v.new_score != null ? ` · Yeni skor: <b style="color:var(--grn)">${v.new_score.toFixed?.(0) || v.new_score}</b>` : '';
    if (out) out.innerHTML = `<span style="color:var(--grn)">✓ Yenilendi.</span> Önce: ${bAge!=null?bAge.toFixed(0)+'sa':'?'} → Sonra: ${aAge!=null?aAge.toFixed(0)+'sa':'?'}${scoreLine}`;
    // Invalidate the freshness cache so the modal/banner re-fetch
    S.diagFresh = null; S.diagFreshSummary = null; S.diagFreshFetchedAt = 0;
    // Re-load this one ticker's bundle for the open modal
    setTimeout(() => showFreshModal(ticker), 1200);
    setTimeout(() => loadRadarFreshness(true), 1500);
  } catch(e) {
    if (out) out.innerHTML = `<span style="color:var(--red)">Hata: ${esc(String(e.message||e))}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Şimdi Yenile (cache\'i kır + yeniden fetch)'; }
  }
}

// ===== CROSS / SİNYALLER =====
// Cross sayfası artık BullAlfa içinde "⚡ Sinyaller" mode'u olarak görünür.
// Bu fonksiyon parametrik container alır — pg-cross (geriye dönük uyumluluk
// için korunur) ya da pg-bullalfa içinde render edebilsin diye.
function renderCrossPage(containerId){
  const pg = $(containerId || 'pg-cross');
  if (!pg) return;
  if(!S.cross){pg.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px"><h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--orn)">⚡ Sinyaller — Cross Hunter</h2><button class="btn btn-orn" onclick="startCross('${esc(containerId||'pg-cross')}')">⚡ TARA</button></div><div style="padding:12px 16px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-base);color:var(--t2);line-height:1.6"><b style="color:var(--orn)">Sinyaller nasıl çalışır?</b> İki modda çalışır: <b style="color:var(--cyn)">Kırılımlar</b> = teknik kırılım sinyalleri (EMA cross, Ichimoku, VCP, destek/direnç). <b style="color:var(--blu)">Momentumlar</b> = trend gücü sinyalleri (MACD, RSI, Bollinger). Her sinyal ⭐1-5 güvenilirlik puanı alır.</div><div class="emp"><h3 style="color:var(--t2)">Taramak için butona basın</h3></div>`;return;}
  const sigs=S.cross.signals||[];const sm=S.cross.summary||{};const aiCom=S.cross.ai_commentary;const crossCat=S._crossCat||'all';
  const re = (cat) => `S._crossCat='${cat}';renderCrossPage('${esc(containerId||'pg-cross')}')`;
  const reS = (n) => `S._crossMinStar=${n};renderCrossPage('${esc(containerId||'pg-cross')}')`;
  let h=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px"><h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--orn)">⚡ Sinyaller — Cross Hunter</h2><button class="btn btn-orn" onclick="startCross('${esc(containerId||'pg-cross')}')">⚡ YENİDEN</button></div><div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap"><span class="pill p-blu">${sm.total||0} sinyal</span><span class="pill p-grn">🟢 ${sm.bullish||0}</span><span class="pill p-red">🔴 ${sm.bearish||0}</span><span class="pill p-ylw">⭐ ${sm.total_stars||0} güç</span><span class="pill p-blu">✓ ${sm.vol_confirmed||0} teyitli</span>${sm.quality_a?`<span class="pill p-grn">🅰️ ${sm.quality_a} A-kalite</span>`:''}${sm.quality_b?`<span class="pill p-ylw">🅱️ ${sm.quality_b} B-kalite</span>`:''}</div>`;
  h+=`<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap"><button class="btn btn-sm ${crossCat==='all'?'btn-orn':''}" style="${crossCat!=='all'?'background:var(--bg3);color:var(--t2)':''}" onclick="${re('all')}">Tümü (${sigs.length})</button><button class="btn btn-sm ${crossCat==='kirilim'?'btn-cyn':''}" style="${crossCat!=='kirilim'?'background:var(--bg3);color:var(--t2)':''}" onclick="${re('kirilim')}">🎯 Kırılımlar (${sigs.filter(s=>s.category==='kirilim').length})</button><button class="btn btn-sm ${crossCat==='momentum'?'btn-blu':''}" style="${crossCat!=='momentum'?'background:var(--bg3);color:var(--t2)':''}" onclick="${re('momentum')}">📊 Momentumlar (${sigs.filter(s=>s.category==='momentum').length})</button></div>`;
  const catDesc=crossCat==='kirilim'?'<b style="color:var(--cyn)">Kırılımlar:</b> Golden/Death Cross, Ichimoku Kumo Breakout, VCP, Rectangle, 52W High, Destek/Direnç kırılımları. Orta-uzun vade trend değişimi sinyalleri.':crossCat==='momentum'?'<b style="color:var(--blu)">Momentumlar:</b> MACD Cross, RSI Aşırı Alım/Satım, Bollinger Band kırılımları. Kısa vade trend gücü sinyalleri.':'Tüm sinyaller — kırılımlar ve momentumlar birlikte.';
  h+=`<div style="padding:8px 14px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-sm);color:var(--t2);line-height:1.5">${catDesc}</div>`;
  if(aiCom){h+=`<div class="aib" style="margin-bottom:14px"><div class="aib-t">🤖 Sinyal Analizi</div><div class="aib-tx">${esc(aiCom)}</div></div>`;}
  const catFiltered=crossCat==='all'?sigs:sigs.filter(s=>s.category===crossCat);const minStar=S._crossMinStar||1;const filtered=catFiltered.filter(s=>(s.stars||1)>=minStar);
  h+=`<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap"><button class="btn btn-sm ${minStar===1?'btn-orn':''}" style="${minStar!==1?'background:var(--bg3);color:var(--t2)':''}" onclick="${reS(1)}">Tümü (${catFiltered.length})</button><button class="btn btn-sm ${minStar===3?'btn-orn':''}" style="${minStar!==3?'background:var(--bg3);color:var(--t2)':''}" onclick="${reS(3)}">⭐3+ (${catFiltered.filter(s=>(s.stars||1)>=3).length})</button><button class="btn btn-sm ${minStar===4?'btn-orn':''}" style="${minStar!==4?'background:var(--bg3);color:var(--t2)':''}" onclick="${reS(4)}">⭐4+ (${catFiltered.filter(s=>(s.stars||1)>=4).length})</button></div>`;
  if(!filtered.length){h+='<div class="emp"><h3 style="color:var(--t2)">Bu filtre ile sinyal yok</h3></div>';}else{const grouped={};filtered.forEach(s=>{if(!grouped[s.ticker])grouped[s.ticker]={ticker:s.ticker,price:s.price,signals:[],totalStars:0,volConfirmed:s.vol_confirmed};grouped[s.ticker].signals.push(s);grouped[s.ticker].totalStars+=s.stars||1;});const tickers=Object.values(grouped).sort((a,b)=>b.totalStars-a.totalStars);h+=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px">`;tickers.forEach(g=>{const mainCls=g.signals[0].signal_type==='bullish'?'bull':g.signals[0].signal_type==='bearish'?'bear':'';h+=`<div class="sigc ${mainCls}" style="padding:14px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span class="clk-t" style="font-size:16px;font-weight:700" onclick="loadTicker('${esc(g.ticker)}')">${esc(g.ticker)}</span><div style="display:flex;align-items:center;gap:8px"><span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ylw)">⭐${g.totalStars}/${g.signals.length*5}</span>${g.volConfirmed?'<span style="font-size:10px;color:var(--grn)">✓hacim</span>':''}<span style="color:var(--t1);font-family:'JetBrains Mono',monospace;font-size:12px">${g.price?g.price.toFixed(2)+' TL':''}</span></div></div>`;g.signals.forEach(s=>{const starStr='⭐'.repeat(s.stars||1)+'☆'.repeat(5-(s.stars||1));const sigCol=s.signal_type==='bullish'?'var(--grn)':s.signal_type==='bearish'?'var(--red)':'var(--ylw)';const catIcon=s.category==='kirilim'?'🎯':'📊';const sq=s.signal_quality||'C';const sqCls=sq==='A'?'qb-a':sq==='B'?'qb-b':'qb-c';h+=`<div style="padding:6px 0;border-top:1px solid var(--bdr)"><div style="display:flex;justify-content:space-between;align-items:center"><div style="display:flex;align-items:center;gap:6px"><span class="qb ${sqCls}">${sq}</span><span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:${sigCol}">${catIcon} ${esc(s.signal)}</span></div><span style="font-size:10px;color:var(--t3)">${starStr}</span></div>${s.reason&&s.reason.length?`<div style="font-size:10px;color:var(--grn);margin-top:4px;line-height:1.5">${s.reason.slice(0,2).map(r=>'✓ '+esc(r)).join(' · ')}</div>`:''}${s.risk_flags&&s.risk_flags.length?`<div style="font-size:10px;color:var(--red);margin-top:2px;line-height:1.5">${s.risk_flags.slice(0,2).map(r=>'✗ '+esc(r)).join(' · ')}</div>`:''}</div>`;});h+=`</div>`;});h+=`</div>`;}pg.innerHTML=h;
}
async function startCross(containerId){
  const cid = containerId || 'pg-cross';
  const pg0 = $(cid); if(pg0) pg0.innerHTML='<div class="ld"><div class="sp"></div><div class="ld-t">Sinyaller kontrol ediliyor…</div></div>';
  try{
    S.cross=await api('/api/cross');
    const cnt=$('cnt-s');if(cnt)cnt.textContent=S.cross.summary?.total||0;
    // Audit fix: if the host container has gone away during await
    // (user switched modes / tabs), skip the render to avoid writing
    // stale data into whatever replaced it.
    if($(cid)) renderCrossPage(cid);
  }catch(e){
    const pg1=$(cid); if(pg1) pg1.innerHTML=`<div class="emp"><h3 style="color:var(--t2)">Hata: ${esc(e.message)}</h3></div>`;
  }
}

// ===== BULLWATCH PAGE =====
// Low-float micro-cap accumulation footprints. Cards only — no buy/sell
// language, no price targets. Pattern strings come straight from the
// engine in descriptive form ("Float Squeeze + Absorption + Tight Closes").
function _bwZoneStyle(zone){
  if(zone==='CONVICTION') return {col:'var(--grn)',pill:'p-grn',label:'CONVICTION',icon:'🟢',desc:'Kırılım sürecinde'};
  if(zone==='CONFIRMED')  return {col:'var(--ylw)',pill:'p-ylw',label:'CONFIRMED', icon:'🟡',desc:'Sahiplik + tape uyumlu'};
  return                       {col:'var(--blu)',pill:'p-blu',label:'EARLY',     icon:'🔵',desc:'İlk ayak izi'};
}
function _bwDqBadge(dq){
  if(dq==='high')   return '<span class="pill p-grn" style="font-size:9px;padding:2px 6px">HIGH DATA</span>';
  if(dq==='medium') return '<span class="pill p-ylw" style="font-size:9px;padding:2px 6px">MED DATA</span>';
  return                   '<span class="pill p-red" style="font-size:9px;padding:2px 6px">LOW DATA</span>';
}

// Phase A.10 Step 2-A.2 — UI helpers (display-only, no scoring impact)

// Cycle state badge — maps engine output to user-facing label.
function _bwCycleBadge(item){
  const cs = item.cycle_state || '';
  const map = {
    'TOPLANIYOR':    {bg:'rgba(106,167,255,.18)', col:'var(--cyn)', ico:'🌀'},
    'ATEŞLENİYOR':   {bg:'rgba(0,229,160,.18)',   col:'var(--grn)', ico:'⚡'},
    'DAĞITIM RİSKİ': {bg:'rgba(255,167,38,.18)',  col:'var(--orn)', ico:'⚠'},
    'BOŞALTIYOR':    {bg:'rgba(239,83,80,.18)',   col:'var(--red)', ico:'🔻'},
    'BELİRSİZ':      {bg:'var(--bg3)',            col:'var(--t3)',  ico:'•'},
  };
  const m = map[cs];
  if(!m) return '';
  return `<span title="Engine cycle state — bu hisse döngünün neresinde?" style="display:inline-flex;align-items:center;gap:4px;background:${m.bg};color:${m.col};font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:.4px">${m.ico} ${esc(cs)}</span>`;
}

// Data trust badge — uses data_status (Step 2-A) when available, otherwise
// falls back to v1 data_quality. Hover tooltip exposes provider + missing
// fields + override info. Never hides cards based on partial data.
function _bwDataBadge(item){
  const ds = item.data_status;            // "live"|"partial"|"stale"|"missing"
  const dq = item.data_quality;           // legacy fallback
  const provider = item.provider_used;
  const missing = item.missing_fields || [];
  const ovr = item.override_applied;
  const ovrFields = item.override_fields || [];

  // Compose tooltip — short multi-line text
  const tip = [];
  if(provider) tip.push('Source: ' + provider);
  if(ds)       tip.push('Status: ' + ds);
  if(missing.length) tip.push('Missing: ' + missing.slice(0,4).join(', '));
  if(ovr && ovrFields.length) tip.push('Override: ' + ovrFields.join(', '));
  const tipText = tip.length ? tip.join('\n') : '';

  // Pick color/label based on data_status (Step 2-A) primarily
  let label, cls;
  if(ds === 'live'){     label = 'DATA: LIVE';    cls = 'p-grn'; }
  else if(ds === 'partial'){ label = 'DATA: PARTIAL'; cls = 'p-ylw'; }
  else if(ds === 'stale'){   label = 'DATA: STALE';   cls = 'p-orn'; }
  else if(ds === 'missing'){ label = 'DATA: MISSING'; cls = 'p-red'; }
  else if(dq === 'high'){    label = 'DATA: HIGH';    cls = 'p-grn'; }
  else if(dq === 'medium'){  label = 'DATA: MED';     cls = 'p-ylw'; }
  else {                     label = 'DATA: LOW';     cls = 'p-red'; }

  return `<span class="pill ${cls}" title="${esc(tipText)}" style="font-size:9px;padding:2px 6px;cursor:help">${label}</span>`;
}

// ─── Phase A.10 Step 2-C — Workflow readiness badge ───
// Display-only label that complements cycle_state. Tells the user what
// the system thinks they should DO about this stock, in observation
// language only (never buy/sell). Tooltip carries the rationale.
function _bwReadinessBadge(item){
  const r = item.readiness || '';
  const map = {
    'HAZIRLANIYOR':         {bg:'rgba(106,167,255,.18)', col:'var(--cyn)', ico:'🔵'},
    'ATEŞLENDİ':            {bg:'rgba(0,229,160,.22)',   col:'var(--grn)', ico:'🟢'},
    'TEYİT BEKLİYOR':       {bg:'rgba(255,205,86,.18)',  col:'var(--ylw)', ico:'🟡'},
    'GEÇ KALMIŞ OLABİLİR':  {bg:'rgba(255,107,107,.18)', col:'var(--red)', ico:'🔴'},
    'İZLEMEDE':             {bg:'var(--bg3)',            col:'var(--t3)',  ico:'⚪'},
  };
  const m = map[r];
  if(!m) return '';
  const tip = item.readiness_rationale || r;
  return `<span title="${esc(tip)}" style="display:inline-flex;align-items:center;gap:4px;background:${m.bg};color:${m.col};font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:.4px;cursor:help">${m.ico} ${esc(r)}</span>`;
}

// Segment fit — explanatory only, never affects scoring or ordering.
// Compact pill; tooltip carries the explainer.
function _bwSegmentFitBadge(item){
  const f = item.segment_fit || '';
  if(!f) return '';
  const map = {
    'GÜÇLÜ': {bg:'rgba(0,229,160,.12)',  col:'var(--grn)', ico:'✓'},
    'ORTA':  {bg:'rgba(255,205,86,.12)', col:'var(--ylw)', ico:'~'},
    'ZAYIF': {bg:'rgba(255,167,38,.14)', col:'var(--orn)', ico:'!'},
  };
  const m = map[f];
  if(!m) return '';
  const tip = item.segment_fit_explainer || f;
  return `<span title="${esc(tip)}" style="display:inline-flex;align-items:center;gap:3px;background:${m.bg};color:${m.col};font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;padding:2px 6px;border-radius:3px;letter-spacing:.3px;cursor:help">${m.ico} FIT: ${esc(f)}</span>`;
}

// Evidence chips — 2-4 strongest facts in compact monochrome chips.
// Display order: prioritize the most discriminating signals first.
function _bwEvidenceChips(item){
  const chips = [];
  const m = item.metrics || {};
  const fp = m.float_pressure;
  const rvol = m.rvol;
  const ft = m.float_turnover_20d;
  const pin = item.price_pinning?.price_pinning_score;
  const pos = item.move_maturity?.indicators?.position_in_range;
  const ct = item.engine_conflict_matrix?.confidence_tier;
  const depth = item.engine_conflict_matrix?.evidence_depth_count;

  // Strongest facts first — at most 5 chips to keep visual budget
  if(fp != null && fp >= 0.04) chips.push({k:'Float', v:`${(fp*100).toFixed(1)}%`, c:'var(--grn)'});
  else if(fp != null && fp >= 0.02) chips.push({k:'Float', v:`${(fp*100).toFixed(1)}%`});
  if(rvol != null && rvol >= 1.5) chips.push({k:'RVOL', v:`${rvol.toFixed(1)}×`, c: rvol >= 3 ? 'var(--grn)' : null});
  if(ft != null && ft >= 1.5) chips.push({k:'Turnover', v:`${ft.toFixed(1)}×`});
  if(pin != null && pin >= 60) chips.push({k:'Pinning', v:`${pin.toFixed(0)}`});
  if(pos != null) chips.push({k:'Range', v:`${(pos*100).toFixed(0)}%`, c: pos>0.85?'var(--orn)':(pos<0.30?'var(--cyn)':null)});
  if(ct && depth!=null) chips.push({k:'Conf', v:`${ct}·${depth}`});
  if(item.data_status === 'partial') chips.push({k:'Veri', v:'partial', c:'var(--ylw)'});
  if(item.override_applied) chips.push({k:'Manual', v:(item.override_fields||[]).join('+')||'override', c:'var(--cyn)'});
  // Tahtacı PR B — sustained walk-up + holding-group activity badges.
  const walkup = m.walkup_days;
  if(walkup != null && walkup >= 5){
    const col = walkup >= 10 ? 'var(--red)' : (walkup >= 7 ? 'var(--orn)' : 'var(--ylw)');
    chips.push({k:'Walk-Up', v:`${walkup}g`, c: col});
  }
  const grpBoost = m.group_activity_boost;
  const grpName = m.group_name;
  if(grpBoost != null && grpBoost > 0 && grpName){
    chips.push({k:'Grup', v:`${grpName} +${grpBoost.toFixed(1)}`, c:'var(--cyn)'});
  }

  if(!chips.length) return '';
  const items = chips.slice(0, 5).map(ch => {
    const col = ch.c || 'var(--t3)';
    return `<span style="display:inline-flex;align-items:center;gap:3px;background:var(--bg3);color:${col};font-family:'JetBrains Mono',monospace;font-size:9.5px;padding:2px 6px;border-radius:3px;letter-spacing:.2px"><span style="opacity:.65">${esc(ch.k)}</span><b style="font-weight:600">${esc(ch.v)}</b></span>`;
  }).join('');
  return `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">${items}</div>`;
}
function _bwSectorStyle(sec){
  const m={
    'Endüstri':{col:'var(--grn)',bg:'var(--grnd)',ico:'🏭'},
    'Madencilik':{col:'var(--orn)',bg:'rgba(255,167,38,.15)',ico:'⛏️'},
    'Finansal':{col:'var(--cyn)',bg:'var(--blud)',ico:'🏦'},
    'Tüketim':{col:'var(--prp)',bg:'var(--prpd)',ico:'🛒'},
    'Teknoloji':{col:'var(--blu)',bg:'var(--blud)',ico:'💻'},
    'Sağlık':{col:'var(--red)',bg:'var(--redd)',ico:'⚕️'},
    'Diğer':{col:'var(--t3)',bg:'var(--bg3)',ico:'•'}
  };
  return m[sec]||m['Diğer'];
}

// ── Trend rozeti — günlük değişim göstergesi (yatırım tavsiyesi değil,
// sadece "ne değişti" gözlemi). Renkler: yeni=mavi, zone yükseldi=sarı,
// score yükseldi=yeşil, score düştü=kırmızı, soğudu=gri.
function _bwTrendBadge(delta){
  if(!delta||delta.type==='stable') return '';
  const m={
    'new':       {col:'var(--blu)', bg:'var(--blud)',  ico:'🆕', txt:'YENİ ELIGIBLE'},
    'zone_up':   {col:'var(--ylw)', bg:'rgba(255,193,7,.15)', ico:'⚡', txt:delta.label_short||'ZONE YÜKSELDİ'},
    'zone_down': {col:'var(--orn)', bg:'rgba(255,167,38,.15)', ico:'🔻', txt:delta.label_short||'ZONE DÜŞTÜ'},
    'score_up':  {col:'var(--grn)', bg:'var(--grnd)',  ico:'📈', txt:`${delta.label_short||'+'} PUAN`},
    'score_down':{col:'var(--red)', bg:'var(--redd)',  ico:'📉', txt:`${delta.label_short||'-'} PUAN`},
  }[delta.type];
  if(!m) return '';
  return `<span style="display:inline-flex;align-items:center;gap:4px;background:${m.bg};color:${m.col};font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:.4px;text-transform:uppercase">${m.ico} ${esc(m.txt)}</span>`;
}

// ── 7-günlük skor sparkline (mini bar chart). Eksik günler boş bar.
// Yatırım sinyali değil, sadece "skor zaman içinde nasıl gelişti" gözlemi.
function _bwSparkline(history){
  if(!history||!history.some(v=>v!=null)) return '';
  const W=70, H=18, n=history.length, gap=1;
  const bw=(W-gap*(n-1))/n;
  const max=100; // skor 0-100 arası, sabit ölçek (relative değil)
  const bars=history.map((v,i)=>{
    if(v==null) return `<rect x="${i*(bw+gap)}" y="${H-2}" width="${bw}" height="2" fill="var(--bg3)" rx="1"/>`;
    const h=Math.max(2,(v/max)*H);
    return `<rect x="${i*(bw+gap)}" y="${H-h}" width="${bw}" height="${h}" fill="var(--t3)" rx="1"/>`;
  }).join('');
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block">${bars}</svg>`;
}

// ── Watchlist localStorage utility — kişisel cihaza özel takip listesi
const _BW_WATCHLIST_KEY='bw_watchlist_v1';
function bwGetWatchlist(){
  try{ return JSON.parse(localStorage.getItem(_BW_WATCHLIST_KEY)||'[]'); }catch{ return []; }
}
function bwSetWatchlist(list){
  try{ localStorage.setItem(_BW_WATCHLIST_KEY, JSON.stringify(list)); }catch{}
  S._bwWatchlist=list;
}
function bwToggleWatchlist(symbol){
  const list=bwGetWatchlist();
  const i=list.indexOf(symbol);
  if(i>=0) list.splice(i,1); else list.push(symbol);
  bwSetWatchlist(list);
  // re-render to update the star + watchlist section
  if(typeof renderBullwatchPage==='function') renderBullwatchPage();
}
function _bwIsWatched(symbol){
  return (S._bwWatchlist||bwGetWatchlist()).includes(symbol);
}

// ── Takip Listesi mini kartı — sparkline + kısa durum.
// "AL/SAT" yerine "yükseldi/düştü/soğudu" gözlem dili.
function _bwWatchCard(it){
  const symbol=it.symbol;
  const elig=it.eligible;
  const cooled=it.cooled_off;
  const score=it.score;
  const zone=it.zone;
  const delta=it.delta;
  const history=it.score_history_7d||[];
  const sparkSvg=_bwSparkline(history);
  // Renk şeması: eligible+positive trend = yeşil-ish; cooled = sarı; ineligible & no history = gri
  let borderCol='var(--bdr)';
  let stateLine='';
  if(elig){
    const z=_bwZoneStyle(zone);
    borderCol=z.col;
    const dt=delta?.type;
    if(dt==='zone_up') stateLine=`<span style="color:var(--ylw);font-size:10px;font-weight:700">⚡ ${esc(delta.label_short||'')}</span>`;
    else if(dt==='new') stateLine=`<span style="color:var(--blu);font-size:10px;font-weight:700">🆕 yeni eligible</span>`;
    else if(dt==='score_up') stateLine=`<span style="color:var(--grn);font-size:10px;font-weight:700">📈 ${esc(delta.label_short||'')}</span>`;
    else if(dt==='score_down') stateLine=`<span style="color:var(--red);font-size:10px;font-weight:700">📉 ${esc(delta.label_short||'')}</span>`;
    else stateLine=`<span style="color:var(--t3);font-size:10px">${esc(zone||'—')}</span>`;
  }else if(cooled){
    borderCol='var(--orn)';
    stateLine=`<span style="color:var(--orn);font-size:10px;font-weight:700">📊 SOĞUDU · dün ${it.prev_score?.toFixed(0)||'?'} idi</span>`;
  }else{
    stateLine=`<span style="color:var(--t4);font-size:10px">eligible değil</span>`;
  }
  return `<div style="background:var(--bg2);border:1px solid ${borderCol};border-left-width:3px;border-radius:6px;padding:8px 10px;display:flex;flex-direction:column;gap:4px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
      <span class="clk-t" style="font-weight:700;font-size:13px" onclick="loadTicker('${esc(symbol)}')">${esc(symbol)}</span>
      <button onclick="event.stopPropagation();bwToggleWatchlist('${esc(symbol)}')" title="Takipten çıkar" style="background:none;border:0;cursor:pointer;color:var(--ylw);font-size:14px;padding:0;line-height:1">★</button>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;gap:4px">
      <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:${elig?'var(--t1)':'var(--t3)'}">${score!=null?score.toFixed(0):'—'}</span>
      ${sparkSvg}
    </div>
    ${stateLine}
  </div>`;
}

// ─── Phase A.10 Step 2-C — Workflow shortlist ───
// Bugünün BullWatch Shortlist'i — compact ranking layer ABOVE the full
// grid. Rules (matching backend Step 2-C contract):
//   - Max 12-15 total names
//   - Max 5 per group
//   - Sort: score desc → confidence_tier desc → evidence_depth_count desc
//   - Sector diversity: same sector capped at 3 per group
//   - Exclude data_status=missing
//   - Avoid LOW confidence in primary groups (allowed in late-risk only)
//   - Full grid is NOT filtered by shortlist — additive only
//
// Three groups returned: hazirlananlar, atestlenenler, late_risk.
function bwBuildShortlist(items){
  if(!Array.isArray(items)) return {hazirlananlar:[], atestlenenler:[], late_risk:[]};

  // Step 1 — exclude data_status=missing (shortlist only)
  const usable = items.filter(it => (it.data_status || '').toLowerCase() !== 'missing');

  // Step 2 — confidence rank for sort
  const ctRank = {HIGH: 3, MEDIUM: 2, LOW: 1};
  const _sortKey = (a, b) => {
    const sa = a.score || 0, sb = b.score || 0;
    if(sb !== sa) return sb - sa;
    const cta = ctRank[(a.engine_conflict_matrix?.confidence_tier || '').toUpperCase()] || 0;
    const ctb = ctRank[(b.engine_conflict_matrix?.confidence_tier || '').toUpperCase()] || 0;
    if(ctb !== cta) return ctb - cta;
    const da = a.engine_conflict_matrix?.evidence_depth_count || 0;
    const db = b.engine_conflict_matrix?.evidence_depth_count || 0;
    return db - da;
  };

  // Step 3 — bucket by readiness state
  const HAZIRLANAN_STATES = new Set(['HAZIRLANIYOR', 'TEYİT BEKLİYOR']);
  const ATESLENEN_STATES = new Set(['ATEŞLENDİ']);
  const LATE_STATES = new Set(['GEÇ KALMIŞ OLABİLİR']);

  const hazirlanan_pool = usable.filter(it => HAZIRLANAN_STATES.has(it.readiness));
  const ateslenen_pool = usable.filter(it => ATESLENEN_STATES.has(it.readiness));
  const late_pool = usable.filter(it => LATE_STATES.has(it.readiness));

  // Step 4 — primary groups exclude LOW confidence
  const primaryFilter = it => (it.engine_conflict_matrix?.confidence_tier || '').toUpperCase() !== 'LOW';
  const haz_primary = hazirlanan_pool.filter(primaryFilter).sort(_sortKey);
  const ate_primary = ateslenen_pool.filter(primaryFilter).sort(_sortKey);
  // Late-risk: ALL confidences allowed (warning group)
  const late_sorted = late_pool.slice().sort(_sortKey);

  // Step 5 — sector diversity within group: max 3 per sector_tr
  function diversify(arr, perSectorCap, totalCap){
    const taken = [];
    const sectorCount = {};
    for(const it of arr){
      const s = it.sector_tr || 'Diğer';
      if((sectorCount[s] || 0) >= perSectorCap) continue;
      taken.push(it);
      sectorCount[s] = (sectorCount[s] || 0) + 1;
      if(taken.length >= totalCap) break;
    }
    return taken;
  }

  const hazirlananlar = diversify(haz_primary, 3, 5);
  const atestlenenler = diversify(ate_primary, 3, 5);
  const late_risk = diversify(late_sorted, 3, 5);

  return {hazirlananlar, atestlenenler, late_risk};
}

// Render the shortlist section. Empty groups are still rendered (with a
// dim "—" placeholder) so users learn the layout. The section sits
// ABOVE the existing grid and never replaces or hides cards.
function _bwShortlistSection(items){
  const sl = bwBuildShortlist(items);
  const total = sl.hazirlananlar.length + sl.atestlenenler.length + sl.late_risk.length;
  if(total === 0) return '';  // No items qualify — skip the whole section

  const groupRender = (title, icon, color, list, isWarning) => {
    if(!list.length){
      return `<div style="margin-bottom:12px"><div style="display:flex;align-items:center;gap:6px;color:${color};font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:6px">${icon} ${title} <span style="color:var(--t4);font-weight:400">(0)</span></div><div style="color:var(--t4);font-size:11px;font-style:italic">— bu grupta uygun aday yok</div></div>`;
    }
    const rows = list.map(it => _bwShortlistRow(it, isWarning)).join('');
    return `<div style="margin-bottom:12px"><div style="display:flex;align-items:center;gap:6px;color:${color};font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:6px">${icon} ${title} <span style="color:var(--t4);font-weight:400">(${list.length})</span></div>${rows}</div>`;
  };

  const lateBox = sl.late_risk.length
    ? `<div style="background:rgba(255,107,107,.06);border:1px solid rgba(255,107,107,.3);border-radius:6px;padding:10px;margin-top:8px">
         <div style="font-size:10px;color:var(--red);font-family:'JetBrains Mono',monospace;font-weight:700;letter-spacing:.5px;margin-bottom:6px">⚠ DİKKAT — GEÇ EVRE / DAĞITIM RİSKİ</div>
         <div style="font-size:10px;color:var(--t4);margin-bottom:8px">İnsan gözüyle kontrol edilmeli — risk artıyor.</div>
         ${sl.late_risk.map(it => _bwShortlistRow(it, true)).join('')}
       </div>`
    : '';

  return `<div style="background:var(--bg2);border:1px solid var(--bdr);border-radius:8px;padding:14px;margin-bottom:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:var(--t1);letter-spacing:.5px">📋 BUGÜNÜN BULLWATCH SHORTLIST'İ</div>
      <div style="font-size:10px;color:var(--t4)">${total} aday — tüm liste aşağıda korunuyor</div>
    </div>
    ${groupRender('HAZIRLANANLAR', '🔵', 'var(--cyn)', sl.hazirlananlar, false)}
    ${groupRender('ATEŞLENENLER', '⚡', 'var(--grn)', sl.atestlenenler, false)}
    ${lateBox}
  </div>`;
}

// Compact one-row representation used inside the shortlist groups.
// Click on the symbol opens the full ticker (existing loadTicker).
function _bwShortlistRow(item, isWarning){
  const score = (item.score || 0).toFixed(0);
  const z = _bwZoneStyle(item.zone);
  const sec = item.sector_tr || 'Diğer';
  const rationale = item.readiness_rationale || '';
  const dataBadge = _bwDataBadge(item);
  const segBadge = _bwSegmentFitBadge(item);
  const borderColor = isWarning ? 'rgba(255,107,107,.4)' : 'var(--bdr)';
  const ev = item.evidence_layer?.evidence_chips || [];
  const top2 = ev.slice(0, 2).map(c =>
    `<span style="background:var(--bg3);color:var(--t3);font-size:9px;padding:2px 6px;border-radius:3px;font-family:'JetBrains Mono',monospace">${esc(c.label || c)}</span>`
  ).join(' ');
  return `<div style="display:flex;align-items:flex-start;gap:10px;padding:8px;border:1px solid ${borderColor};border-radius:6px;margin-bottom:6px;background:var(--bg3)">
    <div style="flex-shrink:0;width:42px;text-align:center">
      <div style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:${z.col};line-height:1">${score}</div>
      <div style="font-size:8px;color:var(--t4);margin-top:1px">SCORE</div>
    </div>
    <div style="flex:1;min-width:0">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px">
        <span class="clk-t" style="font-size:13px;font-weight:700;color:var(--t1)" onclick="loadTicker('${esc(item.symbol)}')">${esc(item.symbol)}</span>
        <span style="font-size:9px;color:var(--t4);font-family:'JetBrains Mono',monospace">${esc(sec)}</span>
        ${dataBadge}
        ${segBadge}
      </div>
      ${rationale ? `<div style="font-size:11px;color:var(--t2);line-height:1.4">${esc(rationale)}</div>` : ''}
      ${top2 ? `<div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${top2}</div>` : ''}
    </div>
  </div>`;
}

// BW Alarm Faz 3 — persistence badge.
// Looks up the ticker in S.alarmlar.recent (loaded lazily on bullwatch page
// entry) and returns a small chip:
//   🆕 (<24h): alarm just fired — first-look
//   ⏳ (1–7d): fresh alarm still in window
//   🔥 (7–30d): persisted! still in BullWatch list days later — the high-
//               conviction signal that survived multiple scans
function _bwAlarmBadge(ticker){
  const alarms = S.alarmlar && S.alarmlar.recent;
  if (!alarms || !alarms.length) return '';
  const sym = (ticker || '').toUpperCase().replace('.IS','');
  // Find the most recent alarm for this ticker
  let latest = null;
  for (const a of alarms) {
    if ((a.ticker || '').toUpperCase() === sym) {
      if (!latest || new Date(a.alarmed_at) > new Date(latest.alarmed_at)) {
        latest = a;
      }
    }
  }
  if (!latest) return '';
  const ageMs = Date.now() - new Date(latest.alarmed_at).getTime();
  const days = ageMs / (24 * 3600 * 1000);
  let icon, label, col, bg, title;
  if (days < 1) {
    icon = '🆕'; label = 'YENİ ALARM'; col = 'var(--blu)'; bg = 'var(--blud)';
    title = `BullWatch ${Math.round(ageMs/3600000)}sa önce alarm verdi`;
  } else if (days < 7) {
    icon = '⏳'; label = `${Math.round(days)}G ALARM`; col = 'var(--orn)'; bg = 'rgba(255,167,38,.15)';
    title = `${Math.round(days)} gün önce alarm verildi, hala listede`;
  } else if (days < 30) {
    icon = '🔥'; label = `KALICI ${Math.round(days)}G`; col = 'var(--red)'; bg = 'var(--redd)';
    title = `${Math.round(days)} gün önce alarm verildi ve HALA listede — yüksek güven sinyali`;
  } else {
    return '';
  }
  return `<span title="${esc(title)}" style="display:inline-flex;align-items:center;gap:4px;background:${bg};color:${col};font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:.4px">${icon} ${esc(label)}</span>`;
}

function _bwCard(item){
  const z = _bwZoneStyle(item.zone);
  const score = (item.score||0).toFixed(0);
  const fmc = item.metrics?.float_market_cap;
  const fmcStr = fmc ? `${(fmc/1e6).toFixed(0)}M TL float`:'';
  const rvol = item.metrics?.rvol;
  const rvolStr = rvol ? `RVOL ${rvol.toFixed(1)}×`:'';
  const fp = item.metrics?.float_pressure;
  const fpStr = fp ? `Float ${(fp*100).toFixed(1)}%`:'';
  const meta = [fmcStr,rvolStr,fpStr].filter(Boolean).join(' · ');
  const sec = item.sector_tr || 'Diğer';
  const ss = _bwSectorStyle(sec);
  const narr = item.narrative || {};
  const trendHtml = _bwTrendBadge(item.delta);
  const watched = _bwIsWatched(item.symbol);
  return `<div class="pkc" style="border-left-color:${z.col};margin-bottom:0">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="clk-t" style="font-size:18px;font-weight:700" onclick="loadTicker('${esc(item.symbol)}')">${esc(item.symbol)}</span>
          <button onclick="event.stopPropagation();bwToggleWatchlist('${esc(item.symbol)}')" title="${watched?'Takipten çıkar':'Takip et'}" style="background:none;border:0;cursor:pointer;font-size:16px;padding:2px;line-height:1;color:${watched?'var(--ylw)':'var(--t4)'}">${watched?'★':'☆'}</button>
        </div>
        <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;align-items:center">
          <span class="pill ${z.pill}">${z.icon} ${z.label}</span>
          ${_bwAlarmBadge(item.symbol)}
          ${_bwCycleBadge(item)}
          ${_bwReadinessBadge(item)}
          <span style="display:inline-flex;align-items:center;gap:4px;background:${ss.bg};color:${ss.col};font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:3px 8px;border-radius:4px;text-transform:uppercase;letter-spacing:.4px">${ss.ico} ${esc(sec)}</span>
          ${_bwSegmentFitBadge(item)}
          ${_bwDataBadge(item)}
          ${trendHtml}
        </div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;color:${z.col};line-height:1">${score}</div>
        <div style="font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;margin-top:2px">SCORE</div>
        <div style="display:flex;gap:4px;margin-top:6px">
          <button class="clk-t" style="background:none;border:0;cursor:pointer;font-size:10px;color:var(--gold);padding:2px 6px;background:rgba(255,179,0,.08);border:1px solid rgba(255,179,0,.25);border-radius:4px;font-family:'JetBrains Mono',monospace" onclick="event.stopPropagation();showBwExplainModal('${esc(item.symbol)}')" title="Niye bu skor? Tahtacı imzası ne kadar net?">🎯 Niye?</button>
          <button class="clk-t" style="background:none;border:0;cursor:pointer;font-size:10px;color:var(--grn);padding:2px 6px;background:rgba(38,194,129,.10);border:1px solid rgba(38,194,129,.30);border-radius:4px;font-family:'JetBrains Mono',monospace" onclick="event.stopPropagation();showBwOpenPositionModal('${esc(item.symbol)}', ${(item.metrics && item.metrics.last_price) || 'null'})" title="Pozisyon aç — sistem exit signal hesaplasın">+ Aldım</button>
        </div>
      </div>
    </div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t1);margin-top:6px;line-height:1.5;font-weight:600">${esc(item.pattern||'—')}</div>
    ${meta?`<div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3);margin-top:6px">${esc(meta)}</div>`:''}
    ${_bwEvidenceChips(item)}
    ${narr.whats_happening?`<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--bdr);font-size:12px;line-height:1.55">
      <div style="margin-bottom:8px"><span style="color:var(--cyn);font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.5px">🔍 NE OLUYOR</span><div style="color:var(--t1);margin-top:3px">${_bwMarkdown(narr.whats_happening)}</div></div>
      ${narr.what_to_watch?`<div style="margin-bottom:8px"><span style="color:var(--ylw);font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.5px">⏰ NE BEKLE</span><div style="color:var(--t2);margin-top:3px">${_bwMarkdown(narr.what_to_watch)}</div></div>`:''}
      ${narr.caveats?`<div><span style="color:var(--orn);font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.5px">⚠️ ŞÜPHE</span><div style="color:var(--t3);margin-top:3px;font-size:11px">${_bwMarkdown(narr.caveats)}</div></div>`:''}
    </div>`:''}
  </div>`;
}
// ===== BULLWATCH SECTOR ROTATION — "Tahtacılar hangi sektöre yöneldi?" =====
// Mevcut alarm + membership storage'lardan beslenen sektör akış paneli.
// CONVICTION mantığını bozmaz — sadece aggregate view.
async function loadBwSectorRotation(){
  const days = S._bwRotWindow || 7;
  try {
    const r = await api('/api/bullwatch/sector-rotation?window_days=' + days);
    S.bwSectorRot = (r && (r.value || r)) || {};
  } catch(e) {
    console.warn('sector rotation fetch failed', e);
    S.bwSectorRot = { sectors: [], error: String(e.message||e) };
  }
}

function _bwSectorRotationPanel(){
  const data = S.bwSectorRot;
  if (!data || !data.sectors || !data.sectors.length) return '';
  const sectors = data.sectors;
  const days = data.window_days || 7;
  // Max abs(net) for bar normalization
  const maxAbs = Math.max(1, ...sectors.map(s => Math.abs(s.net_score || 0)));

  const trendStyle = {
    hot:      {ic:'🔥', col:'var(--red)',  bg:'rgba(239,83,80,.10)',  lbl:'ısınıyor'},
    warm:     {ic:'⚡', col:'var(--orn)',  bg:'rgba(255,167,38,.08)', lbl:'uyanık'},
    neutral:  {ic:'➡️', col:'var(--t3)',   bg:'var(--bg3)',           lbl:'sakin'},
    cooling:  {ic:'❄️', col:'var(--cyn)',  bg:'rgba(34,211,238,.08)', lbl:'soğuyor'},
  };

  let h = `<div style="margin-bottom:14px;padding:12px 14px;background:var(--bg2);border:1px solid var(--bdr);border-left:3px solid var(--cyn);border-radius:0 var(--rad) var(--rad) 0">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--cyn);text-transform:uppercase;letter-spacing:.7px;font-weight:700">🌊 SEKTÖR ROTASYONU</span>
        <span style="font-size:10px;color:var(--t4)">son ${days}g · ${data.total_events||0} olay · tahtacılar hangi sektöre yöneldi</span>
      </div>
      <div style="display:flex;gap:4px">
        ${[7, 14, 30].map(d => {
          const on = days === d;
          return `<button class="btn btn-sm" style="${on?'background:var(--cyn)20;border:1px solid var(--cyn);color:var(--cyn)':'background:var(--bg3);color:var(--t3)'};font-size:10px;padding:3px 7px;min-height:24px" onclick="S._bwRotWindow=${d};S.bwSectorRot=null;loadBwSectorRotation().then(()=>renderBullwatchPage())">${d}g</button>`;
        }).join('')}
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:5px">`;

  sectors.forEach(s => {
    const st = trendStyle[s.trend] || trendStyle.neutral;
    const net = s.net_score || 0;
    const widthPct = Math.min(100, (Math.abs(net) / maxAbs) * 100);
    const isPositive = net >= 0;
    // Bar centered at midline: positives go right, negatives go left
    const tickers = (s.top_tickers || []).join(' · ') || '';
    h += `<div style="display:grid;grid-template-columns:120px 1fr auto;gap:8px;align-items:center;padding:4px 0;font-size:11px">
      <div style="font-family:'JetBrains Mono',monospace;color:${st.col};font-weight:600">${st.ic} ${esc(s.sector)}</div>
      <div style="position:relative;height:18px;background:var(--bg3);border-radius:2px;overflow:hidden">
        ${isPositive ?
          `<div style="position:absolute;left:50%;width:${widthPct/2}%;height:100%;background:linear-gradient(90deg,${st.col}30,${st.col}90)"></div>` :
          `<div style="position:absolute;right:50%;width:${widthPct/2}%;height:100%;background:linear-gradient(90deg,${st.col}90,${st.col}30)"></div>`}
        <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--bdr)"></div>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t1);font-weight:700">${isPositive ? '+' : ''}${net}</div>
      </div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--t4);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(tickers)}">${esc(tickers)}</div>
    </div>`;
  });
  h += `</div>
    <div style="margin-top:8px;font-size:10px;color:var(--t4);line-height:1.5">
      Pozitif sinyaller: 🚨 CONVICTION alarmı (×3) · ⚡ Zone yükselişi (×1.5) · 🆕 Listeye giriş (×1)<br>
      Negatif: 🔻 Listeden düşüş (×0.5) · 🔽 Zone düşüşü (×1)
    </div>
  </div>`;
  return h;
}

// ===== BULLWATCH PRE-ALARM PANEL — "Tahtacı yaklaşıyor" =====
// CONVICTION mantığı (score≥75 + ≥2 motor + data_quality=high) HİÇ
// DEĞİŞMEDİ — bu sadece score 70-74 arasındaki güçlü tahtacı imzalı
// adayları surface eden read-only ek panel. Alarm storage'a yazılmaz.
async function loadBwPreAlarms(){
  try {
    const r = await api('/api/bullwatch/pre-alarms?limit=8');
    const v = (r && (r.value || r)) || {};
    S.bwPreAlarms = {
      items: v.items || [],
      fetched_at: Date.now(),
    };
  } catch(e) {
    console.warn('pre-alarms fetch failed', e);
    S.bwPreAlarms = { items: [], error: String(e.message||e) };
  }
}

function _bwPreAlarmsPanel(){
  const data = S.bwPreAlarms;
  if (!data || !data.items || !data.items.length) return '';
  const items = data.items.slice(0, 6);
  // Header with count + "what is this" tooltip
  let h = `<div style="margin-bottom:14px;padding:12px 14px;background:linear-gradient(135deg,rgba(255,167,38,.10),rgba(255,167,38,.03));border:1px solid var(--orn);border-left:3px solid var(--orn);border-radius:0 var(--rad) var(--rad) 0">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--orn);text-transform:uppercase;letter-spacing:.7px;font-weight:700">⏳ TAHTACI YAKLAŞIYOR</span>
        <span style="font-size:10px;color:var(--t4);font-family:'JetBrains Mono',monospace">${items.length} aday · skor 70-74 · güçlü tahtacı imzası</span>
      </div>
      <span title="CONVICTION (skor≥75 + ≥2 motor + yüksek veri) henüz kriterleri karşılamayan ama tahtacı imzası ısınmakta olan adaylar. Alarm değil — erken görünürlük." style="font-size:10px;color:var(--t4);cursor:help;text-decoration:underline dotted">ne bu?</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px">`;
  items.forEach(c => {
    const ts = c.tahtaci_strength || 0;
    const tsPct = Math.round(ts * 100);
    const tsCol = ts >= 0.6 ? 'var(--gold)' : ts >= 0.4 ? 'var(--orn)' : 'var(--ylw)';
    const blocker = c.data_quality_blocker;
    const missingTxt = (c.missing_engines && c.missing_engines.length)
      ? `Eksik: ${c.missing_engines.slice(0,2).join(' + ')}`
      : '';
    h += `<div style="padding:10px 12px;background:var(--bg2);border:1px solid var(--bdr);border-left:3px solid ${tsCol};border-radius:0 var(--rad) var(--rad) 0;cursor:pointer;transition:transform .15s" onclick="showBwExplainModal('${esc(c.symbol)}')" onmouseover="this.style.transform='translateY(-1px)'" onmouseout="this.style.transform=''">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:6px">
            <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--cyn);font-size:13px">${esc(c.symbol)}</span>
            <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t1);font-weight:700">${c.score.toFixed(1)}</span>
            <span style="font-size:9px;color:var(--t4)">→ 75</span>
          </div>
          <div style="font-size:10px;color:${tsCol};margin-top:2px;font-weight:600">🎯 ${esc(c.tahtaci_label||'')}</div>
          ${missingTxt ? `<div style="font-size:9.5px;color:var(--t3);margin-top:3px;line-height:1.4">${esc(missingTxt)}</div>` : ''}
          ${blocker ? `<div style="font-size:9px;color:var(--orn);margin-top:2px;line-height:1.4">⚠ ${esc(blocker)}</div>` : ''}
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:${tsCol};line-height:1">${tsPct}</div>
          <div style="font-size:8px;color:${tsCol};opacity:.7;text-transform:uppercase;letter-spacing:.5px">tahtacı</div>
        </div>
      </div>
    </div>`;
  });
  h += '</div></div>';
  return h;
}

// ===== BULLWATCH EXPLAINABILITY MODAL — "Niye bu skor?" =====
// Tahtacı-merkezli: kullanıcı tıklayınca açar, headline'da "Tahtacı
// Signal Strength" daire, altında 3-kategori engine breakdown
// (🎯 Tahtacı imzaları / 📊 Teknik teyit / 🏛️ Temel bağlam).
async function showBwExplainModal(ticker){
  // Dedupe rapid double-clicks (lesson from PR #62 audit fix)
  const existing = document.getElementById('bwExplainOv');
  if (existing) existing.remove();
  const _now = Date.now();
  if (window.__lastBwExplainAt && (_now - window.__lastBwExplainAt) < 300) return;
  window.__lastBwExplainAt = _now;

  const ov = document.createElement('div');
  ov.id = 'bwExplainOv';
  ov.className = 'mov';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(6px)';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<div style="background:var(--bg1);border:1px solid var(--bdr2);border-radius:var(--rad);max-width:720px;width:100%;max-height:90vh;overflow-y:auto;padding:24px"><div style="text-align:center;color:var(--t3);padding:30px"><div class="sp" style="margin:0 auto 12px"></div>${esc(ticker)} skor açıklaması yükleniyor…</div></div>`;
  document.body.appendChild(ov);

  let data;
  try {
    const r = await api('/api/bullwatch/explain/' + encodeURIComponent(ticker));
    data = (r && (r.value || r)) || null;
  } catch(e) {
    ov.querySelector('div').innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">Yüklenemedi: ${esc(String(e.message||e))}</div><div style="text-align:center"><button class="btn btn-sm" onclick="this.closest('.mov').remove()">Kapat</button></div>`;
    return;
  }
  if (!data) { ov.remove(); return; }
  ov.querySelector('div').innerHTML = _bwExplainHtml(data);
  // Faz 4: CONVICTION zone'da AI commentary butonu ekle
  if ((data.zone || '').toUpperCase() === 'CONVICTION') {
    _injectBwAiCommentaryBtn(ov.querySelector('div'), data.symbol);
  }
}

// Faz 4 helpers — AI commentary on demand
function _injectBwAiCommentaryBtn(container, symbol){
  if (!container || !symbol) return;
  const btnWrap = document.createElement('div');
  btnWrap.id = 'bwAiComWrap';
  btnWrap.style.cssText = 'margin-top:14px;padding:12px;background:var(--bg2);border:1px solid var(--bdr);border-radius:var(--rad);border-left:3px solid var(--cyn)';
  btnWrap.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
    <div>
      <div style="font-size:11px;color:var(--cyn);font-family:'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:1px;font-weight:700">🤖 AI Yorumu</div>
      <div style="font-size:11px;color:var(--t3);margin-top:2px">CONVICTION skoru için kısa bir AI değerlendirmesi</div>
    </div>
    <button class="btn btn-sm btn-cyn" id="bwAiComBtn" onclick="window._loadBwAiCommentary('${esc(symbol)}')">Yorumu Yükle →</button>
  </div>
  <div id="bwAiComBody" style="margin-top:0"></div>`;
  container.appendChild(btnWrap);
}

window._loadBwAiCommentary = async function(symbol){
  const btn = document.getElementById('bwAiComBtn');
  const body = document.getElementById('bwAiComBody');
  if (!btn || !body) return;
  btn.disabled = true;
  btn.textContent = 'Yükleniyor…';
  body.innerHTML = `<div style="margin-top:10px;color:var(--t3);font-size:12px"><span class="sp" style="display:inline-block;width:14px;height:14px;vertical-align:middle;margin-right:6px"></span>AI yorumu hazırlanıyor…</div>`;
  try {
    const r = await api('/api/bullwatch/ai-commentary/' + encodeURIComponent(symbol));
    const data = (r && (r.value || r)) || null;
    if (data && data.commentary) {
      body.innerHTML = `<div style="margin-top:10px;padding:10px;background:var(--bg1);border-radius:4px;font-size:13px;line-height:1.6;color:var(--t1)">${esc(data.commentary)}</div>`;
      btn.style.display = 'none';
    } else {
      body.innerHTML = `<div style="margin-top:10px;color:var(--t3);font-size:11px">AI yorumu üretilemedi.</div>`;
      btn.disabled = false;
      btn.textContent = 'Tekrar Dene';
    }
  } catch (e) {
    body.innerHTML = `<div style="margin-top:10px;color:var(--red);font-size:11px">Hata: ${esc(e.message||'bilinmeyen')}</div>`;
    btn.disabled = false;
    btn.textContent = 'Tekrar Dene';
  }
};

function _bwExplainHtml(d){
  const ts = d.tahtaci_strength || {};
  const tsScore = ts.score || 0;
  const tsPct = Math.round(tsScore * 100);
  // Color for the headline tahtaci circle
  const tsCol = tsScore >= 0.6 ? 'var(--gold)' : tsScore >= 0.4 ? 'var(--orn)' : tsScore >= 0.2 ? 'var(--ylw)' : 'var(--t3)';
  const tsBg = tsScore >= 0.6 ? 'rgba(255,179,0,.18)' : tsScore >= 0.4 ? 'rgba(255,167,38,.15)' : 'rgba(255,193,7,.10)';
  let h = `<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;gap:12px">
    <div>
      <h3 style="font-family:'JetBrains Mono',monospace;color:var(--cyn);font-size:18px">🎯 ${esc(d.symbol)} · Niye bu skor?</h3>
      <div style="font-size:11px;color:var(--t3);margin-top:2px">Zone: <b style="color:var(--t1)">${esc(d.zone||'')}</b> · Final: <b style="color:var(--t1)">${(d.score||0).toFixed(1)}</b> · ${esc(d.pattern||'')}</div>
    </div>
    <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="this.closest('.mov').remove()">✕</button>
  </div>`;

  // Tahtacı Signal Strength headline — big circular badge
  h += `<div style="display:flex;align-items:center;gap:18px;padding:16px;background:${tsBg};border:1px solid ${tsCol}55;border-radius:var(--rad);margin-bottom:14px">
    <div style="flex-shrink:0;width:96px;height:96px;border-radius:50%;background:${tsCol}25;border:2px solid ${tsCol};display:flex;flex-direction:column;align-items:center;justify-content:center">
      <div style="font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;color:${tsCol};line-height:1">${tsPct}</div>
      <div style="font-size:9px;color:${tsCol};opacity:.85;text-transform:uppercase;letter-spacing:1px;margin-top:2px">/100</div>
    </div>
    <div style="flex:1;min-width:0">
      <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:${tsCol};text-transform:uppercase;letter-spacing:1px;font-weight:700">🎯 Tahtacı Signal Strength</div>
      <div style="font-size:16px;color:var(--t1);font-weight:700;margin-top:4px">${esc(ts.label||'—')}</div>
      <div style="font-size:11px;color:var(--t3);margin-top:6px;line-height:1.5">BullWatch'ın <b style="color:var(--t2)">tahtacı operasyonu tespit etmek</b> için baktığı 4 sinyalin birleşik gücü.</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;font-size:10px;font-family:'JetBrains Mono',monospace">
        ${_bwExplainBadge('KAP', (ts.components||{}).kap_activity, 'var(--red)')}
        ${_bwExplainBadge('Insider/Own', (ts.components||{}).ownership, 'var(--orn)')}
        ${_bwExplainBadge('Grup', (ts.components||{}).group_boost, 'var(--cyn)')}
        ${_bwExplainBadge('Walk-Up', (ts.components||{}).walkup_days, 'var(--blu)', 'g')}
      </div>
    </div>
  </div>`;

  // Engine breakdown — 3 categories
  const grouped = d.engines_grouped || {};
  ['tahtaci','teyit','baglam'].forEach(cat => {
    const bucket = grouped[cat];
    if (!bucket || !bucket.engines || !bucket.engines.length) return;
    h += `<div style="margin-bottom:12px">
      <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--cyn);font-weight:700;letter-spacing:.5px;margin-bottom:4px">${esc(bucket.label)}</div>
      <div style="font-size:10px;color:var(--t4);margin-bottom:8px;line-height:1.5">${esc(bucket.description||'')}</div>
      <div style="display:flex;flex-direction:column;gap:6px">`;
    bucket.engines.forEach(e => {
      h += _bwExplainEngineRow(e, d.delta);
    });
    h += '</div></div>';
  });

  // Data quality footer
  const dq = d.data_quality || {};
  const dqCol = dq.tier === 'high' ? 'var(--grn)' : dq.tier === 'medium' ? 'var(--ylw)' : 'var(--red)';
  h += `<div style="margin-top:14px;padding:10px 14px;background:var(--bg3);border-left:3px solid ${dqCol};border-radius:0 var(--rad) var(--rad) 0;font-size:11px;color:var(--t2);line-height:1.55">
    <b style="color:${dqCol}">📊 Veri kalitesi: ${esc(dq.tier||'?').toUpperCase()}</b><br>
    ${esc(dq.tier_explanation||'')}${dq.is_bank?' <i>(Banka — cashflow standart formatta gelmez, beklenen davranış.)</i>':''}
    ${(dq.missing_fields && dq.missing_fields.length)?`<br><span style="color:var(--t4);font-size:10px">Eksik: ${esc(dq.missing_fields.slice(0,5).join(', '))}</span>`:''}
  </div>`;

  // Action button → opens regular ticker detail
  h += `<div style="text-align:right;margin-top:14px"><button class="btn btn-sm btn-blu" onclick="loadTicker('${esc(d.symbol)}');this.closest('.mov').remove()">Hisseyi Aç →</button></div>`;
  return h;
}

function _bwExplainBadge(label, value, col, unit){
  if (value == null) return '';
  const v = typeof value === 'number' ? (unit ? value + unit : (value <= 1 ? (value*100).toFixed(0)+'%' : value.toFixed(0))) : value;
  return `<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 8px;background:${col}15;color:${col};border-radius:3px;font-weight:600"><span style="opacity:.7">${esc(label)}</span><b>${esc(String(v))}</b></span>`;
}

function _bwExplainEngineRow(e, delta){
  const avail = e.available;
  const sub = e.sub_score;
  const contrib = e.contribution_pct || 0;
  // Bar fill = sub_score (0..1) — visual signal strength of this engine
  const fillPct = avail ? Math.round((sub||0) * 100) : 0;
  // Delta indicator
  let deltaHtml = '';
  if (delta && delta.by_engine && delta.by_engine[e.key] != null) {
    const d = delta.by_engine[e.key];
    if (Math.abs(d) >= 0.05) {
      const dCol = d > 0 ? 'var(--grn)' : 'var(--red)';
      const dSign = d > 0 ? '↑' : '↓';
      deltaHtml = ` <span style="color:${dCol};font-size:9px;margin-left:4px">${dSign}${Math.abs(d).toFixed(2)}</span>`;
    }
  }
  const reasonsHtml = (e.reasons && e.reasons.length)
    ? `<div style="font-size:10px;color:var(--t3);margin-top:3px;line-height:1.5">${e.reasons.slice(0,3).map(r=>'✓ '+esc(r)).join('<br>')}</div>`
    : '';
  const opacity = avail ? '1' : '0.45';
  return `<div style="padding:8px 10px;background:var(--bg3);border-radius:var(--rad);opacity:${opacity}">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:4px">
      <span style="font-size:11px;color:var(--t1);font-weight:600" title="${esc(e.description||'')}">${esc(e.label)} ${avail?'':'<span style=\"color:var(--t4);font-size:9px\">· veri yok</span>'}${deltaHtml}</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--gold);font-weight:700">${contrib.toFixed(1)}</span>
    </div>
    <div style="height:6px;background:var(--bg2);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${fillPct}%;background:linear-gradient(90deg,var(--gold)80,var(--gold));transition:width .3s"></div>
    </div>
    ${reasonsHtml}
  </div>`;
}

// Lightweight markdown — only **bold** support, no XSS surface beyond esc()
function _bwMarkdown(s){
  if(!s) return '';
  return esc(s).replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
}

// ─── Phase A.10 Step 2-A.2 — UX safety state machine ───
// Goals: pressing "Yeniden tara" must NEVER blank the current results.
// New scan runs in background, cached separately as S.bwPending until
// the user explicitly accepts. State fields:
//   S.bwRefreshRunning  — boolean, scan in progress
//   S.bwScanProgress    — latest /health response while polling
//   S.bwPending         — fetched new BullWatch result, not yet swapped in
//   S.bwPendingDiff     — diff stats vs. current S.bullwatch
//   S.bwRefreshError    — last refresh failure message
//   S.bwSnapshotSavedAt — confirmation toast timestamp

function bwComputeDiff(oldBw, newBw){
  const oldMap = {}, newMap = {};
  (oldBw && oldBw.items || []).forEach(i => oldMap[i.symbol] = i);
  (newBw && newBw.items || []).forEach(i => newMap[i.symbol] = i);
  const zoneRank = {EARLY:1, CONFIRMED:2, CONVICTION:3};
  const newSymbols = [], removedSymbols = [];
  let scoreChanged = 0, upgraded = 0, downgraded = 0;
  Object.keys(newMap).forEach(sym => {
    if(!oldMap[sym]){ newSymbols.push(sym); return; }
    const o = oldMap[sym], n = newMap[sym];
    if(Math.abs((n.score||0) - (o.score||0)) >= 5) scoreChanged++;
    const oR = zoneRank[o.zone]||0, nR = zoneRank[n.zone]||0;
    if(nR > oR) upgraded++;
    else if(nR > 0 && oR > 0 && nR < oR) downgraded++;
  });
  Object.keys(oldMap).forEach(sym => {
    if(!newMap[sym]) removedSymbols.push(sym);
  });
  return {
    new_count: newSymbols.length,
    removed_count: removedSymbols.length,
    score_changed_count: scoreChanged,
    upgraded_count: upgraded,
    downgraded_count: downgraded,
    new_symbols: newSymbols,
    removed_symbols: removedSymbols,
  };
}

function bwAcceptPending(){
  if(!S.bwPending) return;
  S.bullwatch = S.bwPending;
  S.bwPending = null;
  S.bwPendingDiff = null;
  S.bwRefreshError = null;
  renderBullwatchPage();
}

function bwDiscardPending(){
  S.bwPending = null;
  S.bwPendingDiff = null;
  S.bwRefreshError = null;
  renderBullwatchPage();
}

function bwRetryRefresh(){
  S.bwRefreshError = null;
  loadBullwatch(true);
}

function bwSnapshotSave(){
  if(!S.bullwatch || !S.bullwatch.items || !S.bullwatch.items.length) return;
  try{
    localStorage.setItem('bw_snapshot_last_successful', JSON.stringify({
      saved_at: new Date().toISOString(),
      item_count: S.bullwatch.items.length,
      data: S.bullwatch,
    }));
    S.bwSnapshotSavedAt = Date.now();
    renderBullwatchPage();
    setTimeout(() => {
      // auto-clear toast after 3s
      if(S.bwSnapshotSavedAt && Date.now() - S.bwSnapshotSavedAt >= 3000){
        S.bwSnapshotSavedAt = null;
        renderBullwatchPage();
      }
    }, 3100);
  }catch(e){ console.warn('snapshot save failed:', e); }
}

function bwSnapshotRestore(){
  try{
    const raw = localStorage.getItem('bw_snapshot_last_successful');
    if(!raw) return null;
    return JSON.parse(raw);
  }catch(e){ return null; }
}

// Background refresh: like _bwPollUntilReady but writes to S.bwPending
// (never overwrites S.bullwatch, never blanks the page).
async function _bwBackgroundRefresh(){
  S.bwRefreshRunning = true;
  S.bwRefreshError = null;
  S.bwScanProgress = null;
  renderBullwatchPage();

  // Kick off the actual refresh on the server. We don't await this directly
  // — we poll /health and only fetch when ready. The fetchPromise is mostly
  // used to ensure refresh=true reaches the server.
  const fetchPromise = api('/api/bullwatch?refresh=true').catch(() => null);
  await new Promise(r => setTimeout(r, 1000));

  const startTime = Date.now();
  const MAX_POLL_SEC = 420;  // 7 dakika hard cap
  while(Date.now() - startTime < MAX_POLL_SEC * 1000){
    let h;
    try{ h = await api('/api/bullwatch/health'); }catch(e){ h = null; }
    if(h){
      S.bwScanProgress = h;
      // Cache hazır + scan bitti → fetch et
      if(h.cache_populated && !h.scan_running){
        try{
          const newBw = await fetchPromise || await api('/api/bullwatch');
          if(newBw && newBw.items){
            S.bwPending = newBw;
            S.bwPendingDiff = bwComputeDiff(S.bullwatch, newBw);
          }else{
            S.bwRefreshError = 'Yeni sonuçlar boş döndü.';
          }
        }catch(e){
          S.bwRefreshError = e.message || 'Sonuç alınamadı';
        }
        S.bwRefreshRunning = false;
        S.bwScanProgress = null;
        renderBullwatchPage();
        return;
      }
      // Hâlâ devam ediyor — re-render (banner progress'i güncellenir)
      renderBullwatchPage();
    }
    await new Promise(r => setTimeout(r, 3000));
  }

  // Timeout
  S.bwRefreshRunning = false;
  S.bwScanProgress = null;
  S.bwRefreshError = 'Tarama 7 dakikayı aştı — birkaç dakika sonra tekrar dene.';
  renderBullwatchPage();
}

// Inline banners rendered above the existing BullWatch header. Each is
// optional and additive — none of them remove or alter the cards below.
function _bwBannerRefreshRunning(){
  const sp = S.bwScanProgress || {};
  const done = sp.scan_progress || 0;
  const total = sp.scan_total || 0;
  const pct = sp.scan_progress_pct || 0;
  const elapsed = sp.scan_elapsed_sec || 0;
  const eta = done > 0 && pct < 99 && total > 0
    ? Math.round((elapsed/done) * (total-done)) : null;
  const elapsedStr = elapsed >= 120 ? `${(elapsed/60).toFixed(1)}dk` : `${elapsed.toFixed(0)}s`;
  const progressLine = total > 0
    ? `${done}/${total} hisse · ${pct.toFixed(0)}%${eta!=null?` · ~${eta}s kaldı`:''} · ${elapsedStr} geçti`
    : `Hazırlık başlıyor…`;
  return `<div style="background:linear-gradient(90deg,rgba(106,167,255,.15),rgba(106,167,255,.05));border:1px solid rgba(106,167,255,.4);border-radius:var(--rad);padding:12px 14px;margin-bottom:14px">
    <div style="display:flex;align-items:center;gap:10px;font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--cyn);font-weight:600">
      <div class="sp" style="width:14px;height:14px;border-width:2px;flex-shrink:0"></div>
      <span>🔄 Yeni tarama hazırlanıyor — mevcut sonuçlar korunuyor.</span>
    </div>
    ${total > 0 ? `<div style="height:5px;background:var(--bg3);border-radius:3px;overflow:hidden;margin-top:10px">
      <div style="width:${pct}%;height:100%;background:var(--cyn);transition:width .5s"></div>
    </div>` : ''}
    <div style="font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--t3);margin-top:6px">${esc(progressLine)}</div>
  </div>`;
}

function _bwBannerPending(){
  const d = S.bwPendingDiff || {};
  const parts = [];
  if(d.new_count) parts.push(`<b style="color:var(--grn)">+${d.new_count} yeni</b>`);
  if(d.removed_count) parts.push(`<b style="color:var(--red)">-${d.removed_count} çıktı</b>`);
  if(d.score_changed_count) parts.push(`${d.score_changed_count} skor değişti`);
  if(d.upgraded_count) parts.push(`<b style="color:var(--grn)">${d.upgraded_count} zone yükseldi</b>`);
  if(d.downgraded_count) parts.push(`<b style="color:var(--orn)">${d.downgraded_count} zone düştü</b>`);
  const summaryLine = parts.length ? parts.join(' · ') : 'Liste değişmedi.';
  return `<div style="background:linear-gradient(90deg,rgba(0,229,160,.15),rgba(0,229,160,.05));border:1px solid rgba(0,229,160,.45);border-radius:var(--rad);padding:14px 16px;margin-bottom:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:200px">
        <div style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--grn);font-weight:700;margin-bottom:4px">✅ Yeni tarama hazır</div>
        <div style="font-size:12px;color:var(--t2);font-family:'JetBrains Mono',monospace">${summaryLine}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-sm btn-grn" onclick="bwAcceptPending()">Yeni sonuçları göster</button>
        <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="bwDiscardPending()">Mevcut listeyi koru</button>
      </div>
    </div>
  </div>`;
}

function _bwBannerError(){
  return `<div style="background:linear-gradient(90deg,rgba(255,167,38,.15),rgba(255,167,38,.05));border:1px solid rgba(255,167,38,.45);border-radius:var(--rad);padding:12px 14px;margin-bottom:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:200px">
        <div style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--orn);font-weight:600">⚠ Yeni tarama başarısız oldu, son başarılı sonuçlar gösteriliyor.</div>
        <div style="font-size:11px;color:var(--t3);margin-top:3px">${esc(S.bwRefreshError||'')}</div>
      </div>
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="bwRetryRefresh()">Tekrar dene</button>
    </div>
  </div>`;
}

function _bwBannerSnapshotToast(){
  return `<div style="position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--bg3);border:1px solid var(--grn);color:var(--grn);padding:10px 18px;border-radius:var(--rad);font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,.3)">✓ Snapshot kaydedildi</div>`;
}

function _bwBannerSnapshotFallback(){
  return `<div style="background:linear-gradient(90deg,rgba(255,167,38,.12),rgba(255,167,38,.04));border:1px solid rgba(255,167,38,.35);border-radius:var(--rad);padding:10px 14px;margin-bottom:14px;font-size:12px;color:var(--orn);font-family:'JetBrains Mono',monospace">📦 Canlı veri alınamadı — son kayıtlı snapshot gösteriliyor.</div>`;
}

// Inline badge appended to the "Son başarılı tarama" line. Surfaces
// the D.1+ snapshot meta so users can tell at a glance whether the
// view comes from a snapshot, how old it is, and whether the server
// has already kicked a background refresh.
function bwSnapshotBadge(bw){
  const m = bw && bw._meta;
  if(!m) return '';
  const asofIso = m.as_of || bw.asof;
  if(!asofIso) return '';
  const ageMs = Date.now() - new Date(asofIso).getTime();
  if(!Number.isFinite(ageMs) || ageMs < 0) return '';
  const mins = Math.round(ageMs / 60000);
  const ageStr = mins < 1 ? 'az önce' :
                 mins < 60 ? `${mins} dk önce` :
                 `${Math.round(mins/60)} sa önce`;
  // Stale band: served from snapshot AND meta flagged stale, OR
  // age beyond 60 min regardless of flag (defensive — if the server
  // forgets the flag, the user still sees a hint).
  const stale = m.stale === true || mins > 60;
  const liveScan = m.from_snapshot === false;
  const refreshing = m.refresh_scheduled === true;
  let color = 'var(--t4)';     // default subtle grey
  let icon = '📸';
  let label = `snapshot · ${ageStr}`;
  if(liveScan){
    color = 'var(--ylw)';
    icon = '⚡';
    label = `canlı tarama · ${ageStr}`;
  } else if(stale){
    color = 'var(--orn)';
    icon = '⏳';
    label = `eski snapshot · ${ageStr}`;
  }
  if(refreshing) label += ' · yenileniyor';
  return `<span style="font-size:10px;color:${color};margin-left:6px;font-family:'JetBrains Mono',monospace" title="${m.from_snapshot===false?'Veri canlı taramadan geldi':'Veri snapshot katmanından alındı'}${refreshing?'; arka planda yeni tarama başladı':''}">${icon} ${label}</span>`;
}

function renderBullwatchPage(){
  const pg=$('pg-bullwatch');
  // Initial load — auto-trigger on first visit. Snapshot fallback handled
  // inside loadBullwatch() if API fails AND localStorage snapshot exists.
  if(S.bullwatch===undefined){
    pg.innerHTML=`<div class="ld"><div class="sp"></div><div class="ld-t">BullWatch hazırlanıyor…</div><div style="font-size:11px;color:var(--t4);margin-top:6px">Cache durumu kontrol ediliyor</div></div>`;
    loadBullwatch();
    return;
  }
  // Lazy-load alarm history so cards can show 🆕/⏳/🔥 persistence badges.
  // Cheap: 30-day window, cached in S.alarmlar (also shared with Alarmlar tab).
  if (!S.alarmlar && !S._alarmsLoading) {
    S._alarmsLoading = true;
    loadAlarmlar().then(() => {
      S._alarmsLoading = false;
      renderBullwatchPage();
    }).catch(() => { S._alarmsLoading = false; });
  }
  // Faz 2: lazy-load pre-alarm candidates (score 70-74 + güçlü tahtacı).
  // Mevcut CONVICTION listesi BOZULMAZ — bu sadece üstte ek panel.
  if (!S.bwPreAlarms && !S._bwPreAlarmsLoading) {
    S._bwPreAlarmsLoading = true;
    loadBwPreAlarms().then(() => {
      S._bwPreAlarmsLoading = false;
      renderBullwatchPage();
    }).catch(() => { S._bwPreAlarmsLoading = false; });
  }
  // Faz 3: sektör rotasyonu — son N gün'de hangi sektöre tahtacı
  // yöneldi aggregate paneli (alarm + membership storage'lardan).
  if (!S.bwSectorRot && !S._bwSectorRotLoading) {
    S._bwSectorRotLoading = true;
    loadBwSectorRotation().then(() => {
      S._bwSectorRotLoading = false;
      renderBullwatchPage();
    }).catch(() => { S._bwSectorRotLoading = false; });
  }
  const bw=S.bullwatch;
  // Empty / error state — but ONLY if we have NO usable cards at all.
  // (If we have current results AND a refresh error, we keep the cards
  //  visible and just show an error banner above them.)
  const hasUsableCards = bw && bw.items && bw.items.length > 0;
  if(bw&&bw.error && !hasUsableCards){
    pg.innerHTML=`<div class="emp"><h3 style="color:var(--t2)">BullWatch yüklenemedi: ${esc(bw.error)}</h3><button class="btn btn-grn" style="margin-top:14px" onclick="S.bullwatch=undefined;renderBullwatchPage()">Tekrar Dene</button></div>`;
    return;
  }
  const items=bw?.items||[];
  // Trend rozeti taşıyanları başa sırala — kullanıcı "ne yeni, ne ısınıyor"
  // sorusunu tek bakışta görsün. Sıralama BullWatch felsefesini bozmaz:
  // skor hâlâ ana metrik, sadece görsel öncelik trend taşıyanlara verilir.
  const _trendRank={'zone_up':1,'new':2,'score_up':3,'zone_down':4,'score_down':5,'stable':6};
  const sortedItems=[...items].sort((a,b)=>{
    const ra=_trendRank[a.delta?.type||'stable']||6;
    const rb=_trendRank[b.delta?.type||'stable']||6;
    if(ra!==rb) return ra-rb;
    return (b.score||0)-(a.score||0); // tie-break by score
  });
  const filt=S._bwZone||'all';
  const sectFilt=S._bwSector||'all';
  const alarmOnly=!!S._bwAlarmOnly;
  // Build alarm ticker set once per render (cheap; ≤500 alarms)
  const _alarmTickerSet = new Set();
  if (S.alarmlar && S.alarmlar.recent) {
    S.alarmlar.recent.forEach(a => {
      const t = (a.ticker || '').toUpperCase().replace('.IS','');
      const ageMs = Date.now() - new Date(a.alarmed_at).getTime();
      if (ageMs < 30 * 24 * 3600 * 1000) _alarmTickerSet.add(t);
    });
  }
  // Apply filters: zone first, then sector, then alarm-only
  let filtered=filt==='all'?sortedItems:sortedItems.filter(i=>i.zone===filt);
  if(sectFilt!=='all') filtered=filtered.filter(i=>(i.sector_tr||'Diğer')===sectFilt);
  if(alarmOnly) filtered=filtered.filter(i=>_alarmTickerSet.has((i.symbol||'').toUpperCase()));
  const counts={
    all:items.length,
    EARLY:items.filter(i=>i.zone==='EARLY').length,
    CONFIRMED:items.filter(i=>i.zone==='CONFIRMED').length,
    CONVICTION:items.filter(i=>i.zone==='CONVICTION').length,
  };
  // Sector counts from items already filtered by zone (so chip counts respect zone selection)
  const zoneFiltered=filt==='all'?items:items.filter(i=>i.zone===filt);
  const sectCounts={};
  zoneFiltered.forEach(i=>{const s=i.sector_tr||'Diğer';sectCounts[s]=(sectCounts[s]||0)+1});
  const sectOrder=['Endüstri','Madencilik','Tüketim','Teknoloji','Sağlık','Finansal','Diğer'];
  const visibleSects=sectOrder.filter(s=>sectCounts[s]>0);
  const asof=bw?._meta?.as_of||bw?.asof;
  const scanned=bw?.scanned||0;
  const eligible=bw?.eligible_count||items.length;
  const capTl=bw?.cap_tl||250e6;
  const capStr=`${(capTl/1e6).toFixed(0)}M TL`;
  const nearMisses=bw?.near_misses||[];
  // Snapshot meta badge — surfaces D.1+ meta fields so users can tell
  // at-a-glance whether they're seeing a snapshot or a live scan and
  // how old it is. Pure presentation, no behavior change.
  const snapBadge=bwSnapshotBadge(bw);
  // Phase A.10 Step 2-A.2 — UX safety banners (additive, above header)
  let bannersHtml = '';
  if(S.bwIsSnapshotFallback) bannersHtml += _bwBannerSnapshotFallback();
  if(S.bwRefreshRunning) bannersHtml += _bwBannerRefreshRunning();
  if(S.bwPending) bannersHtml += _bwBannerPending();
  if(S.bwRefreshError && !S.bwRefreshRunning && !S.bwPending) bannersHtml += _bwBannerError();

  let h=bannersHtml+`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);color:var(--acc)">🐂 BullWatch — Sessiz Birikim Radarı</h2>
      <p style="font-size:var(--fs-sm);color:var(--t3);margin-top:2px">Son başarılı tarama: ${asof?new Date(asof).toLocaleString('tr-TR'):'<i style="color:var(--t4)">veri hazırlanıyor</i>'} ${snapBadge} · ${scanned} hisse tarandı · ${eligible} eleğe takıldı · float ≤${capStr}</p>
      <p style="font-size:11px;color:var(--t3);margin-top:4px;font-family:'JetBrains Mono',monospace">📊 Aktif sinyal: <b style="color:var(--grn)">${counts.CONVICTION||0} conviction</b> · <b style="color:var(--ylw)">${counts.CONFIRMED||0} confirmed</b> · <b style="color:var(--blu)">${counts.EARLY||0} early</b></p>
      <p style="font-size:10px;color:var(--t4);margin-top:2px;font-family:'JetBrains Mono',monospace">📅 Veri: son tamamlanmış işlem günü (intraday partial bar dışlandı — gün içinde sonuçlar tutarlı)</p>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="bwSnapshotSave()" title="Mevcut listeyi tarayıcıda yedekle (sonraki API hatasında geri yüklenir)">📦 Snapshot al</button>
      <button class="btn btn-grn" onclick="loadBullwatch(true)" ${S.bwRefreshRunning?'disabled style="opacity:.5;cursor:not-allowed"':''}>🔄 ${S.bwRefreshRunning?'Tarama çalışıyor…':'YENİDEN TARA'}</button>
    </div>
  </div>
  <div style="padding:12px 16px;background:var(--bg3);border-radius:var(--rad);margin-bottom:14px;font-size:var(--fs-base);color:var(--t2);line-height:1.6">
    <b style="color:var(--acc)">BullWatch nasıl çalışır?</b> Düşük float (≤${capStr} float piyasa değeri), likit (≥5M TL günlük hacim) BIST mikro-kaplarında <b>sessiz birikim ayak izlerini</b> tespit eder. 7 motor: Float Pressure, Revenue Mispricing, Silent Volume, Price Action (Shakeout / Absorption / Tight Closes / Walk-Up), Compression, Ownership Intelligence, Fundamental Quality. <span style="color:var(--t4);font-size:11px">Yatırım tavsiyesi değildir — yalnızca tape okuma.</span>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
    <button class="btn btn-sm ${filt==='all'?'btn-grn':''}" style="${filt!=='all'?'background:var(--bg3);color:var(--t2)':''}" onclick="S._bwZone='all';renderBullwatchPage()">Tümü (${counts.all})</button>
    <button class="btn btn-sm ${filt==='CONVICTION'?'btn-grn':''}" style="${filt!=='CONVICTION'?'background:var(--bg3);color:var(--grn)':''}" onclick="S._bwZone='CONVICTION';renderBullwatchPage()">🟢 Conviction (${counts.CONVICTION})</button>
    <button class="btn btn-sm ${filt==='CONFIRMED'?'btn-grn':''}" style="${filt!=='CONFIRMED'?'background:var(--bg3);color:var(--ylw)':''}" onclick="S._bwZone='CONFIRMED';renderBullwatchPage()">🟡 Confirmed (${counts.CONFIRMED})</button>
    <button class="btn btn-sm ${filt==='EARLY'?'btn-grn':''}" style="${filt!=='EARLY'?'background:var(--bg3);color:var(--blu)':''}" onclick="S._bwZone='EARLY';renderBullwatchPage()">🔵 Early (${counts.EARLY})</button>
    ${_alarmTickerSet.size ? `<button class="btn btn-sm" style="${alarmOnly?'background:rgba(239,83,80,.2);border:1px solid var(--red);color:var(--red)':'background:var(--bg3);color:var(--red)'}" onclick="S._bwAlarmOnly=${!alarmOnly};renderBullwatchPage()" title="Sadece son 30 günde alarm verilmiş hisseleri göster">🚨 Alarmlı (${_alarmTickerSet.size})</button>` : ''}
  </div>
  ${visibleSects.length>1?`<div style="display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;align-items:center">
    <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);text-transform:uppercase;letter-spacing:.5px;margin-right:4px">SEKTÖR</span>
    <button class="btn btn-sm ${sectFilt==='all'?'btn-grn':''}" style="${sectFilt!=='all'?'background:var(--bg3);color:var(--t2)':''};font-size:11px;padding:4px 10px;min-height:32px" onclick="S._bwSector='all';renderBullwatchPage()">Tümü (${zoneFiltered.length})</button>
    ${visibleSects.map(s=>{const ss=_bwSectorStyle(s);const on=sectFilt===s;return `<button class="btn btn-sm" style="background:${on?ss.bg:'var(--bg3)'};color:${ss.col};border:${on?'1px solid '+ss.col:'1px solid transparent'};font-size:11px;padding:4px 10px;min-height:32px" onclick="S._bwSector='${esc(s)}';renderBullwatchPage()">${ss.ico} ${esc(s)} (${sectCounts[s]})</button>`}).join('')}
  </div>`:''}`;

  // ── Trend özeti — bugün vs dün, kaç YENİ / YÜKSELEN / DÜŞEN var
  const trendCounts={new:0,zone_up:0,score_up:0,zone_down:0,score_down:0};
  items.forEach(i=>{const t=i.delta?.type;if(t&&trendCounts[t]!==undefined)trendCounts[t]++;});
  const totalTrendActivity=trendCounts.new+trendCounts.zone_up+trendCounts.score_up+trendCounts.zone_down+trendCounts.score_down;
  if(totalTrendActivity>0){
    h+=`<div style="margin-bottom:14px;padding:12px 14px;background:var(--bg3);border-radius:var(--rad);border:1px solid var(--bdr)">
      <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t4);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">📊 BUGÜN DÜNDEN FARKLI OLANLAR</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:12px">
        ${trendCounts.zone_up?`<span style="color:var(--ylw);font-weight:700">⚡ ${trendCounts.zone_up} zone yükseldi</span>`:''}
        ${trendCounts.new?`<span style="color:var(--blu);font-weight:700">🆕 ${trendCounts.new} yeni eligible</span>`:''}
        ${trendCounts.score_up?`<span style="color:var(--grn);font-weight:700">📈 ${trendCounts.score_up} skor yükseldi</span>`:''}
        ${trendCounts.score_down?`<span style="color:var(--red)">📉 ${trendCounts.score_down} skor düştü</span>`:''}
        ${trendCounts.zone_down?`<span style="color:var(--orn)">🔻 ${trendCounts.zone_down} zone düştü</span>`:''}
      </div>
    </div>`;
  }

  // ── Takip Listesi — kullanıcının localStorage'a eklediği semboller
  // Backend'i stateless tutuyoruz: server zenginleştirme yapar, liste cihazda
  const watchlist=bwGetWatchlist();
  S._bwWatchlist=watchlist;
  if(watchlist.length){
    // Async fetch enriched state for watchlist
    if(!S._bwWatchlistState||S._bwWatchlistFetchedFor!==JSON.stringify(watchlist)){
      S._bwWatchlistFetchedFor=JSON.stringify(watchlist);
      fetch('/api/bullwatch/watchlist/state',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({symbols:watchlist})
      }).then(r=>r.json()).then(d=>{
        S._bwWatchlistState=d.data?.items||d.items||[];
        renderBullwatchPage();
      }).catch(()=>{});
    }
    h+=`<div style="margin-bottom:14px;padding:14px;background:rgba(255,193,7,.06);border:1px solid rgba(255,193,7,.25);border-radius:var(--rad)">
      <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ylw);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center">
        <span>🌟 TAKİP LİSTESİ (${watchlist.length})</span>
        <span style="font-size:9px;color:var(--t4);text-transform:none;letter-spacing:0">cihazına özel · localStorage</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px">
        ${(S._bwWatchlistState||watchlist.map(s=>({symbol:s,eligible:false,score_history_7d:[]}))).map(it=>_bwWatchCard(it)).join('')}
      </div>
    </div>`;
  }
  if(!filtered.length){
    h+=`<div class="emp" style="padding:30px 20px"><h3 style="color:var(--t2)">${items.length?'Bu zone için aday yok':'BullWatch evrenine uyan hisse bulunamadı'}</h3>
      <p style="color:var(--t4);font-size:12px;margin-top:8px">Filtreler: float ≤${capStr} · 20g hacim ≥5M TL</p>
      ${items.length?'<p style="color:var(--t4);font-size:12px">Başka bir zone seç ya da yeniden tara</p>':''}
    </div>`;
    if(nearMisses.length){
      h+=`<div style="margin-top:20px"><h3 style="color:var(--ylw);font-family:'JetBrains Mono',monospace;font-size:14px;margin-bottom:8px">📊 Yakın Adaylar — eleğe en az takılan ${nearMisses.length} hisse</h3>
        <p style="color:var(--t3);font-size:11px;margin-bottom:12px">Float cap'i bu hisseleri yakalamak üzere ayarlayabilirsin. URL'ye <code style="color:var(--cyn)">?cap_tl=2000000000</code> ekleyerek deneyebilirsin (örn. 2 milyar).</p>`;
      // Desktop: tablo
      h+=`<div class="card bw-near-table"><div class="card-b" style="overflow-x:auto"><table class="dtb"><thead><tr><th>#</th><th>Ticker</th><th>Float Mcap</th><th>Mcap</th><th>Free Float</th><th>20g Hacim</th><th>Sebep</th></tr></thead><tbody>`;
      // Defensive normalization — backend should already normalize, but
      // raw weird values (>1) should never display as "1890%"
      function _ffNorm(v){
        if(v==null) return null;
        const n=Number(v);
        if(!isFinite(n)||n<=0) return null;
        if(n<=1.0) return n;            // already a fraction
        if(n<=100.0) return n/100.0;    // percentage form
        return null;                    // nonsense > 100%
      }
      nearMisses.forEach((n,i)=>{
        const fmc=n.float_market_cap?`${(n.float_market_cap/1e6).toFixed(0)}M TL`:'—';
        const mc=n.market_cap?`${(n.market_cap/1e9).toFixed(1)}B TL`:'—';
        const ffN=_ffNorm(n.free_float);
        const ff=ffN!=null?`${(ffN*100).toFixed(0)}%`:'—';
        const atv=n.avg_traded_value_20d?`${(n.avg_traded_value_20d/1e6).toFixed(1)}M TL`:'—';
        h+=`<tr><td style="color:var(--t3)">${i+1}</td><td class="clk-t" onclick="loadTicker('${esc(n.symbol)}')">${esc(n.symbol)}</td><td style="color:var(--ylw);font-weight:700">${fmc}</td><td style="color:var(--t2)">${mc}</td><td style="color:var(--t2)">${ff}</td><td style="color:var(--t2)">${atv}</td><td style="color:var(--t4);font-size:10px">${esc(n.reject_reason||'')}</td></tr>`;
      });
      h+='</tbody></table></div></div>';
      // Mobile: kart listesi
      h+='<div class="bw-near-cards" style="display:none;margin-top:8px">';
      nearMisses.forEach((n,i)=>{
        const fmc=n.float_market_cap?`${(n.float_market_cap/1e6).toFixed(0)}M`:'—';
        const mc=n.market_cap?`${(n.market_cap/1e9).toFixed(1)}B`:'—';
        const ffN=_ffNorm(n.free_float);
        const ff=ffN!=null?`${(ffN*100).toFixed(0)}%`:'—';
        const atv=n.avg_traded_value_20d?`${(n.avg_traded_value_20d/1e6).toFixed(1)}M`:'—';
        h+=`<div style="background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);padding:12px;margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <div style="display:flex;align-items:center;gap:8px"><span style="color:var(--t4);font-size:11px">#${i+1}</span><span class="clk-t" style="font-weight:700;font-size:15px" onclick="loadTicker('${esc(n.symbol)}')">${esc(n.symbol)}</span></div>
            <span style="color:var(--ylw);font-weight:700;font-size:13px">${fmc} TL float</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;font-size:11px;color:var(--t3)">
            <div>Mcap<br><span style="color:var(--t1);font-weight:600">${mc} TL</span></div>
            <div>Free Float<br><span style="color:var(--t1);font-weight:600">${ff}</span></div>
            <div>20g Hacim<br><span style="color:var(--t1);font-weight:600">${atv} TL</span></div>
          </div>
          <div style="color:var(--t4);font-size:10px;margin-top:6px">${esc(n.reject_reason||'')}</div>
        </div>`;
      });
      h+='</div>';
      h+='</div>';
    }
  }else{
    // Faz 3: Sektör rotasyonu EN ÜSTTE — "tahtacılar hangi sektöre"
    // soruyu cevaplayan aggregate panel.
    h += _bwSectorRotationPanel();
    // Faz 2: Pre-alarm panel — "tahtacı yaklaşıyor" adayları.
    // Mevcut shortlist + full grid BOZULMADI, sadece üstüne ek panel.
    h += _bwPreAlarmsPanel();
    // Phase A.10 Step 2-C: prepend shortlist section ABOVE the full grid.
    // Shortlist is purely additive — full grid is rendered AFTER it,
    // unchanged. Items missing from shortlist STILL appear in the grid.
    h += _bwShortlistSection(filtered);
    h+=`<div class="bw-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">${filtered.map(_bwCard).join('')}</div>`;
  }
  // Snapshot save toast (auto-dismisses)
  if(S.bwSnapshotSavedAt && Date.now() - S.bwSnapshotSavedAt < 3000){
    h += _bwBannerSnapshotToast();
  }
  pg.innerHTML=h;
}
// BullWatch: akıllı yükleme — health-poll ile progress göster.
// Cache hazırsa anında, scan running ise X/Y göstergesi, yoksa scan tetikle.
//
// Phase A.10 Step 2-A.2: snapshot-safe refresh. When refresh=true AND we
// already have a successful list, run in background (S.bwPending) — never
// blank the page while the new scan runs.
async function loadBullwatch(refresh){
  const pg=$('pg-bullwatch');
  const hasCurrentResults = S.bullwatch && S.bullwatch.items && S.bullwatch.items.length > 0;

  // Snapshot-safe path: refresh requested AND we have current cards
  if(refresh && hasCurrentResults){
    return _bwBackgroundRefresh();
  }

  if(refresh){
    pg.innerHTML=`<div class="ld"><div class="sp"></div><div class="ld-t">BullWatch yeniden taranıyor…</div><div style="font-size:11px;color:var(--t4);margin-top:6px">Hazırlık başlıyor</div></div>`;
  }
  // 1) Health check — cache hazır mı? scan devam mı ediyor?
  let health=null;
  try{ health=await api('/api/bullwatch/health'); }catch(e){}
  const cacheReady=health&&health.cache_populated;
  const scanRunning=health&&health.scan_running;

  // 2) Cache hazır + refresh istenmemişse → direkt fetch
  if(cacheReady && !refresh){
    try{
      S.bullwatch=await api('/api/bullwatch');
      S.bwIsSnapshotFallback = false;
      renderBullwatchPage();
    }catch(e){
      // Phase A.10 Step 2-A.2: snapshot fallback — first load failed,
      // try to restore last successful snapshot from localStorage so
      // user sees something useful instead of an empty error state.
      const snap = bwSnapshotRestore();
      if(snap && snap.data && snap.data.items && snap.data.items.length){
        S.bullwatch = snap.data;
        S.bwIsSnapshotFallback = true;
      } else {
        S.bullwatch={items:[],error:e.message};
      }
      renderBullwatchPage();
    }
    return;
  }
  // 3) Scan devam ediyor → polling göstergesi
  if(scanRunning){
    return _bwPollUntilReady();
  }
  // 4) Cache yok, scan da yok → fetch et (server otomatik scan başlatır), bu sırada polling göster
  try{
    // Fetch'i fire-and-forget olarak başlat, biz polling ile takip edelim
    const fetchPromise=api('/api/bullwatch'+(refresh?'?refresh=true':''));
    // Server tarama başlatması için 1 saniye ver, sonra polling'e geç
    await new Promise(r=>setTimeout(r,1000));
    _bwPollUntilReady(fetchPromise);
  }catch(e){
    S.bullwatch={items:[],error:e.message};
    renderBullwatchPage();
  }
}

async function _bwPollUntilReady(fetchPromise){
  const pg=$('pg-bullwatch');
  const startTime=Date.now();
  const MAX_POLL_SEC=420;  // 7 dakika hard cap
  while(Date.now()-startTime<MAX_POLL_SEC*1000){
    let h;
    try{ h=await api('/api/bullwatch/health'); }catch(e){ h=null; }
    if(!h){
      pg.innerHTML=`<div class="ld"><div class="sp"></div><div class="ld-t">Bağlantı kontrol ediliyor…</div></div>`;
      await new Promise(r=>setTimeout(r,3000));
      continue;
    }
    // Cache hazır → fetch edip render
    if(h.cache_populated && !h.scan_running){
      try{
        S.bullwatch=await api('/api/bullwatch');
        renderBullwatchPage();
        return;
      }catch(e){
        S.bullwatch={items:[],error:e.message};
        renderBullwatchPage();
        return;
      }
    }
    // Hala devam ediyor → progress göster
    if(h.scan_running){
      const done=h.scan_progress||0;
      const total=h.scan_total||1;
      const pct=h.scan_progress_pct||0;
      const elapsed=h.scan_elapsed_sec||0;
      const phase=h.scan_phase||'scoring';
      // Stage 5: phase-aware UI. history_fetch used to silently
      // hold 0/N for 5-7 min before the user saw any movement.
      const isHistory = phase === 'history_fetch';
      const eta=done>0&&pct<99?Math.round((elapsed/done)*(total-done)):null;
      const isStragglers=!isHistory && pct>=98 && done<total;
      const elapsedStr=elapsed>=120?`${(elapsed/60).toFixed(1)}dk`:`${elapsed.toFixed(0)}s`;
      const headline = isHistory
        ? '📥 Tarihsel veri indiriliyor'
        : (isStragglers ? '⏳ Son hisseler bekleniyor' : '🐂 BullWatch tarama devam ediyor');
      const detail = isHistory
        ? `${total} hisse için 1 yıllık fiyat geçmişi indiriliyor. Bu adım 1-2 dakika sürer, sonra skorlama başlar.`
        : (isStragglers
          ? `${total-done} hisse veri sağlayıcıdan yanıt bekliyor. <b style="color:var(--t2)">Maksimum 4 dakikada</b> sonuç gelir veya parçalı liste gösterilir.`
          : `${total} BIST mikro-kapı paralel taranıyor. Bazı semboller yavaş yanıtlıyor — sayfa kapatma, otomatik yenilenecek.`);
      const barColor = isHistory ? 'var(--blu, #4a90e2)'
        : (isStragglers ? 'var(--ylw)' : 'var(--acc)');
      pg.innerHTML=`<div class="ld" style="padding:40px 20px">
        <div class="sp"></div>
        <div class="ld-t" style="margin-top:12px">${headline}</div>
        <div style="margin-top:16px;max-width:360px;width:100%">
          <div style="display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t2);margin-bottom:6px">
            <span>${done}/${total}${isHistory ? ' veri' : ' hisse'}</span>
            <span>${pct.toFixed(0)}%</span>
          </div>
          <div style="height:6px;background:var(--bg3);border-radius:3px;overflow:hidden">
            <div style="width:${pct}%;height:100%;background:${barColor};transition:width .5s"></div>
          </div>
          <div style="font-size:10px;color:var(--t4);margin-top:8px;text-align:center">
            ⏱️ ${elapsedStr} geçti${eta!=null?` · ~${eta}s kaldı`:''}
          </div>
        </div>
        <div style="font-size:11px;color:var(--t4);margin-top:14px;max-width:360px;text-align:center;line-height:1.5">
          ${detail}
        </div>
      </div>`;
      await new Promise(r=>setTimeout(r,3000));
      continue;
    }
    // Scan durdu, cache de yok → bir kez daha fetch'e dene
    try{
      S.bullwatch=await api('/api/bullwatch');
      renderBullwatchPage();
      return;
    }catch(e){
      S.bullwatch={items:[],error:'Tarama tamamlanamadı: '+e.message};
      renderBullwatchPage();
      return;
    }
  }
  // 7 dakikayı aştık
  S.bullwatch={items:[],error:'Tarama 7 dakikayı aştı — veri sağlayıcı yavaş yanıtlıyor. Birkaç dakika sonra tekrar dene.'};
  renderBullwatchPage();
}

// ===== TAKAS PAGE =====
function renderTakasPage(){const pg=$('pg-takas');if(!S.takas){pg.innerHTML='<div class="ld"><div class="sp"></div><div class="ld-t">Takas verileri yükleniyor...</div></div>';loadTakas();return;}const items=S.takas.items||[];let h=`<div style="margin-bottom:14px;padding:14px 18px;background:linear-gradient(135deg,rgba(255,193,7,.08),rgba(255,152,0,.05));border:1px solid rgba(255,193,7,.25);border-radius:var(--rad2);display:flex;align-items:center;gap:12px"><span style="font-size:24px">🚧</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--ylw);text-transform:uppercase;letter-spacing:1px">Yapım Aşamasında</div><div style="font-size:12px;color:var(--t2);margin-top:3px">Takas verisi MKK lisansı veya aracı kurum entegrasyonu gerektiriyor. Şimdilik yfinance kurumsal veri gösteriliyor.</div></div></div>`;h+=`<div style="margin-bottom:16px"><h2 style="font-family:'JetBrains Mono',monospace;font-size:15px;color:var(--prp)">📊 Takas — Yabancı Oranları</h2><p style="font-size:11px;color:var(--t3);margin-top:2px">Kaynak: ${esc(S.takas.source||'—')} | ${items.length} hisse</p></div>`;if(S.takas.error){h+=`<div class="aib" style="border-color:var(--ylw);margin-bottom:14px"><div class="aib-tx" style="color:var(--ylw)">${esc(S.takas.error)}</div></div>`;}if(items.length){h+=`<div class="card"><div class="card-b" style="overflow-x:auto"><table class="dtb"><thead><tr><th>#</th><th>Ticker</th><th>Yabanci %</th><th>Fiyat</th></tr></thead><tbody>`;items.slice(0,40).forEach((it,i)=>{const pct=it.foreign_pct||0;h+=`<tr><td style="color:var(--t3)">${i+1}</td><td class="clk-t" onclick="loadTicker('${esc(it.ticker)}')">${esc(it.ticker)}</td><td style="color:var(--t1);font-weight:700">${pct.toFixed(1)}%</td><td style="color:var(--t1)">${it.price?fN(it.price):'—'}</td></tr>`;});h+='</tbody></table></div></div>';}pg.innerHTML=h;}
async function loadTakas(){try{S.takas=await api('/api/takas');renderTakasPage();}catch(e){S.takas={items:[],error:e.message};renderTakasPage();}}

// ===== SOSYAL PAGE =====
function renderSosyalPage(){const pg=$('pg-sosyal');if(!S.social){pg.innerHTML='<div class="ld"><div class="sp"></div><div class="ld-t">Veriler yükleniyor…</div></div>';loadSocial();return;}let h=`<div style="margin-bottom:14px;padding:14px 18px;background:linear-gradient(135deg,rgba(255,193,7,.08),rgba(255,152,0,.05));border:1px solid rgba(255,193,7,.25);border-radius:var(--rad2);display:flex;align-items:center;gap:12px"><span style="font-size:24px">🚧</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--ylw);text-transform:uppercase;letter-spacing:1px">Yapım Aşamasında</div><div style="font-size:12px;color:var(--t2);margin-top:3px">X/Twitter duygu analizi için entegrasyon hazırlanıyor. Şimdilik AI tahmini gösteriliyor.</div></div></div>`;h+=`<div style="margin-bottom:16px"><h2 style="font-family:'JetBrains Mono',monospace;font-size:15px;color:var(--prp)">𝕏 Sosyal Medya</h2></div>`;if(S.social.summary){h+=`<div class="aib" style="margin-bottom:14px"><div class="aib-t">Sentiment: ${esc(S.social.overall_sentiment||'—')}</div><div class="aib-tx">${esc(S.social.summary)}</div></div>`;}const trends=S.social.trending||[];if(trends.length){h+=`<div class="card"><div class="card-h"><span class="card-t">🔥 Trending</span></div><div class="card-b"><table class="dtb"><thead><tr><th>#</th><th>Ticker</th><th>Sentiment</th><th>Skor</th><th>Neden</th></tr></thead><tbody>`;trends.forEach((t,i)=>{const col=t.sentiment==='bullish'?'var(--grn)':t.sentiment==='bearish'?'var(--red)':'var(--ylw)';h+=`<tr><td style="color:var(--t3)">${i+1}</td><td class="clk-t" onclick="loadTicker('${esc(t.ticker)}')">${esc(t.ticker)}</td><td style="color:${col};font-weight:700">${esc(t.sentiment||'—')}</td><td>${sPill(t.score)}</td><td style="color:var(--t2);font-size:10px">${esc(t.reason||'')}</td></tr>`;});h+=`</tbody></table></div></div>`;}pg.innerHTML=h;}
async function loadSocial(){try{S.social=await api('/api/social');renderSosyalPage();}catch(e){S.social={trending:[],summary:'Hata: '+e.message};renderSosyalPage();}}

// ===== SCAN + BRIEFING =====
async function startScan(){const btn=$('scanBtn');if(btn){btn.disabled=true;btn.textContent='⏳ Tarama başlatılıyor...';}try{const st=await api('/api/scan-status').catch(()=>({running:false}));if(st.running){if(btn)btn.textContent='⏳ Tarama devam ediyor...';await pollScanProgress(btn);return;}S.scan=await api('/api/scan');S.dash=await api('/api/dashboard');try{S.hero=await api('/api/hero-summary');}catch(e){}updCnt(S.dash);_reRender();}catch(e){const t=$('scanBtn');if(t){t.textContent='HATA';}}if(btn){btn.disabled=false;btn.textContent='🔄 YENİLE';}}
async function pollScanProgress(btn){for(let i=0;i<60;i++){await new Promise(r=>setTimeout(r,3000));try{const st=await api('/api/scan-status');if(btn){const pct=st.total>0?Math.round(st.progress/st.total*100):0;btn.textContent=`⏳ ${st.phase==='raw_fetch'?'Veri çekiliyor':'Analiz ediliyor'} ${pct}%`;}if(!st.running&&st.has_data){S.scan=await api('/api/top10');S.dash=await api('/api/dashboard').catch(()=>null);try{S.hero=await api('/api/hero-summary');}catch(e){}if(S.dash)updCnt(S.dash);_reRender();break;}}catch(e){}}if(btn){btn.disabled=false;btn.textContent='🔄 YENİLE';}}
function updCnt(d){if(!d)return;const c=d.counters||{};const _s=(id,v)=>{const e=$(id);if(e)e.textContent=v;};_s('cnt-a',c.total_analyzed||0);_s('cnt-s',c.cross_signals||0);_s('cnt-c',c.cache_raw||0);if(d.asof)_s('cnt-u',new Date(d.asof).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'}));}
function _reRender(){renderHome();if(S.page==='radar')renderRadarPage();if(S.page==='cross')renderCrossPage();if(S.page==='makro')renderMakroPage();}
async function loadBriefing(){const box=$('hSt');if(!box)return;box.innerHTML='<div class="hero-strat" style="border-color:var(--prp);background:var(--prpd);color:var(--prp)">🤖 AI brifing yükleniyor...</div>';try{const d=await api('/api/briefing');box.innerHTML=d.briefing?`<div class="hero-strat">${esc(d.briefing).replace(/\n/g,'<br>')}</div>`:`<div class="hero-strat" style="border-color:var(--ylw);background:var(--ylwd);color:var(--ylw)">${esc(d.error||'AI key ekleyin')}</div>`;}catch(e){box.innerHTML='';}}

// ===== TICKER DETAIL =====
async function loadTicker(t){t=t.replace('.IS','').toUpperCase();seenAdd(t);openD();$('dp').innerHTML=`<div class="ld"><div class="sp"></div><div class="ld-t" style="color:var(--t3)">${esc(t)}...</div></div>`;try{const[r,tech]=await Promise.all([api(`/api/analyze/${t}`),api(`/api/technical/${t}`).catch(()=>null)]);renderDetail(t,r,tech);
  // PHASE 5: lazy-load enrichment after the main detail panel paints.
  // Both endpoints are additive — failure does not break the panel.
  setTimeout(async () => {
    const sigHost = document.getElementById('sig-explain-host');
    if (sigHost) sigHost.innerHTML = await loadSignalExplanations(t);
    const aiHost = document.getElementById('ai-consensus-host');
    if (aiHost) aiHost.innerHTML = await loadAiConsensus(t);
  }, 50);
}catch(e){$('dp').innerHTML=`<div class="emp"><h3 style="color:var(--t2)">${esc(t)} alınamadı</h3><p style="color:var(--t3)">${esc(e.message)}</p></div>`;}}
function renderDetail(t,r,tech){const s=r.scores,m=r.metrics,L=r.legendary||{},inWL=S.wl.includes(t);
const v11=r.v11||{},v11l=r.v11_labels||{};
const cpL=v11.ciro_pd_label;const eqL=v11l.earnings_quality||{};const caL=v11l.capital_allocation||{};const convL=v11l.conviction||{};const regL=v11l.regime||'';const legV11=v11l.legendary||{};
const dc=r.decision||'';const dCol=vColor(dc);
const dcBg=vBg(dc);
const dcDesc=VERDICT_DESC[dc]||'';
// Radar Overhaul (2026-05): pure-fundamental quality labels. Old
// timing labels (TEYİTLİ/ERKEN/GEÇ) are gone — Radar ranks company
// quality + valuation, not entry timing.
const el=r.entry_label||'';
const elCol=el==='Kaliteli Değer'?'var(--grn)':el==='Pahalı Kalite'?'var(--cyn)':el==='Ucuz ama Riskli'?'var(--ylw)':el==='Zayıf Temel'?'var(--red)':'var(--t3)';
const elBg=el==='Kaliteli Değer'?'var(--grnd)':el==='Zayıf Temel'?'rgba(239,83,80,.15)':'var(--bg3)';
const elDesc=el==='Kaliteli Değer'?'Güçlü temel + makul değerleme — kalite ve fiyat bir arada.':el==='Pahalı Kalite'?'Şirket kaliteli ama fiyat zaten yüksek — değerleme pahalı.':el==='Ucuz ama Riskli'?'Ucuz görünüyor ama temel zayıf — değer tuzağı olabilir.':el==='Zayıf Temel'?'⚠️ Temel analiz zayıf veya risk yüksek.':el==='Dengeli'?'Ne öne çıkan ne zayıf — orta seviye temel.':'Değerlendiriliyor.';
const faScore=r.fa_score||r.deger||r.overall;const riskScore=r.risk_score||0;
$('dp').innerHTML=`<div class="dp-h" style="background:linear-gradient(135deg,var(--bg1),var(--bg2))">
<div style="flex:1">
  <!-- VERDICT ROW — first thing user sees -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">
    <div style="display:flex;align-items:baseline;gap:6px">
      <span style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:700;color:var(--cyn)">${esc(r.ticker)}</span>
      <span style="font-size:13px;color:var(--t3)">${esc(r.name)}</span>
    </div>
    ${dc?`<span style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:800;padding:6px 16px;border-radius:8px;background:${vBg(dc)};color:${vColor(dc)};border:1px solid ${vColor(dc)}40;letter-spacing:.5px">${vLabel(dc)}</span>`:''}
    ${dc?`<span style="font-size:12px;color:var(--t3);padding:3px 8px;background:var(--bg3);border-radius:4px;border:1px solid var(--bdr)">Güven: <span style="color:${confColor(r.confidence||0)};font-weight:600">${confLevel(r.confidence||0)}</span></span>`:''}
  </div>
  <!-- Sector + quality badges -->
  <div style="font-size:12px;color:var(--t3);margin-bottom:8px">${esc(r.sector||'')}${r.sector_group?' · '+esc(r.sector_group):''}${bilancoBadge(r)}</div>
  <div style="display:flex;gap:5px;flex-wrap:wrap">
    ${el?`<span class="pill" style="background:${elBg};color:${elCol};font-weight:700;font-size:var(--fs-xs);padding:3px 8px;border:1px solid ${elCol}30" title="${esc(elDesc)}">${esc(el)}</span>`:''}
    ${(r.confidence||0)<40?'<span class="pill" style="background:rgba(255,179,0,.1);color:var(--ylw);font-size:8px;border:1px solid var(--ylw)" title="Veri eksikliği nedeniyle bu etiket temkinli okunmalı">⚠ Veri sınırlı</span>':''}
    ${r.quality_tag?`<span class="pill" style="background:var(--grnd);color:var(--grn);font-size:var(--fs-xs)" title="Temel analiz kalite seviyesi">Kalite: ${esc(r.quality_tag)}</span>`:''}
    <span class="pill" style="background:${L.buffett_filter==='Geçti'?'var(--grnd)':L.buffett_filter==='Sınırda'?'var(--ylwd)':'rgba(239,83,80,.12)'};color:${L.buffett_filter==='Geçti'?'var(--grn)':L.buffett_filter==='Sınırda'?'var(--ylw)':'var(--red)'};font-size:var(--fs-xs)" title="Buffett Filtresi: Kalite + Rekabet Avantajı + Finansal Sağlık birleşimi">Buffett: ${esc(L.buffett_filter||'N/A')}</span>
    <span class="pill" style="background:${L.graham_filter==='Geçti'?'var(--grnd)':L.graham_filter==='Sınırda'?'var(--ylwd)':'rgba(239,83,80,.12)'};color:${L.graham_filter==='Geçti'?'var(--grn)':L.graham_filter==='Sınırda'?'var(--ylw)':'var(--red)'};font-size:var(--fs-xs)" title="Graham Filtresi: Değer + Finansal Sağlık + Güvenlik Payı">Graham: ${esc(L.graham_filter||'N/A')}</span>
    <span class="pill p-blu" title="Veri güvenilirliği — mevcut metrik sayısına göre hesaplanır. Kaynak: borsapy + yfinance">Veri: %${Math.round(r.confidence||0)} · Hesaplanmış</span>
    ${cpL?`<span class="pill" style="background:${cpL.color}15;color:${cpL.color};font-weight:700;font-size:var(--fs-xs)" title="Yıllık ciro / piyasa değeri. Yüksek değer = ucuz olabilir">${cpL.label} (${cpL.value}x)</span>`:''}
    ${eqL.label?`<span class="pill" style="background:${eqL.color||'var(--t3)'}15;color:${eqL.color||'var(--t3)'};font-size:var(--fs-xs)" title="Nakit akışı kârı destekliyor mu? CFO/NI oranı + Beneish testi ile ölçülür">Nakit Kalite: ${eqL.label}</span>`:''}
    ${v11.is_fatal?'<span class="pill" style="background:rgba(239,83,80,.2);color:var(--red);font-weight:700;font-size:var(--fs-xs)" title="Kritik risk tespit edildi: negatif özsermaye, şüpheli muhasebe veya borç krizi">⛔ KRİTİK RİSK</span>':''}
    ${r.is_hype?'<span class="pill" style="background:rgba(239,83,80,.15);color:var(--red);font-weight:700;font-size:var(--fs-xs)" title="Fiyat hızla yükseliyor ama temel zayıf — spekülasyon riski yüksek">⚠️ HYPE</span>':''}
    <button class="btn btn-sm ${inWL?'btn-orn':'btn-blu'}" onclick="toggleWL('${esc(t)}')" style="margin-left:auto">${inWL?'⭐ Takipten Çıkar':'☆ Takibe Al'}</button>
  </div>
</div>
<button class="dp-close" onclick="closeD()">✕</button>
</div>
<div class="dp-body">
<div class="dp-tabs">
  <button class="dp-tab on" onclick="dtab(this,'ov')">📊 Özet</button>
  <button class="dp-tab" onclick="dtab(this,'neden')">💬 Neden ${vLabel(dc)}?</button>
  <button class="dp-tab" onclick="dtab(this,'sc')">Skorlar</button>
  <button class="dp-tab" onclick="dtab(this,'val')">Değerleme</button>
  <button class="dp-tab" onclick="dtab(this,'qua')">Kalite</button>
  <button class="dp-tab" onclick="dtab(this,'tek')">Teknik</button>
  <button class="dp-tab" onclick="dtab(this,'leg')">Göstergeler</button>
  <button class="dp-tab" onclick="dtab(this,'cht')">Grafik</button>
</div>

<!-- TAB: ÖZET -->
<div class="ds on" id="ds-ov">
  <!-- KARAR AÇIKLAMASI — first-class, always visible -->
  <div style="margin-bottom:16px;padding:16px;background:linear-gradient(135deg,${vBg(dc)},var(--bg2));border:1px solid ${vColor(dc)}30;border-left:4px solid ${vColor(dc)};border-radius:0 var(--rad2) var(--rad2) 0">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
      <span style="font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:800;color:${vColor(dc)}">${vLabel(dc)}</span>
      <span style="font-size:12px;color:var(--t3)">Güven Seviyesi: <span style="color:${confColor(r.confidence||0)};font-weight:700">${confLevel(r.confidence||0)}</span></span>
    </div>
    <div style="font-size:14px;color:var(--t1);line-height:1.7;margin-bottom:8px">${VERDICT_DESC[dc]||''}</div>
    ${(r.positives||[]).length?`<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${(r.positives||[]).slice(0,3).map(p=>`<span style="font-size:11px;padding:3px 8px;background:rgba(76,175,80,.1);border:1px solid rgba(76,175,80,.2);border-radius:4px;color:var(--grn)">✓ ${esc(p)}</span>`).join('')}</div>`:''}
    <div style="margin-top:10px;font-size:10px;color:var(--t4)">Bu bir yatırım tavsiyesi değildir · <a href="#" style="color:var(--t4)" onclick="dtab(document.querySelector('.dp-tab:nth-child(2)'),\'neden\');return false">Neden bu karar? →</a></div>
  </div>

  <!-- PHASE 5.2.1 — TÜRKİYE 4 FİLTRE (mounted via helper, only if turkey_realities present) -->
  ${r.turkey_realities ? renderTurkeyFilterSection(r.turkey_realities) : ''}

  <!-- PHASE 5.2.2 — Sinyal açıklama kartları (lazy-loaded after first paint) -->
  <div class="card" style="margin-top:14px"><div class="card-h"><span class="card-t">⚡ Aktif Sinyaller — Açıklamalı</span></div>
    <div class="card-b" id="sig-explain-host" data-symbol="${esc(t)}"><div class="ld"><div class="sp"></div><div class="ld-t">Sinyaller hazırlanıyor...</div></div></div>
  </div>

  <!-- AI Analizi — Claude (lazy-loaded) -->
  <div class="card" style="margin-top:14px"><div class="card-h"><span class="card-t">🤖 AI Analizi</span></div>
    <div class="card-b" id="ai-consensus-host" data-symbol="${esc(t)}"><div class="ld"><div class="sp"></div><div class="ld-t">Claude analiz ediyor...</div></div></div>
  </div>

  <!-- 3 RING SCORES + KEY METRICS -->
  <div class="g2" style="margin-bottom:14px">
    <div>
      <div style="display:flex;justify-content:center;gap:18px;margin-bottom:12px">
        <div style="text-align:center">${ring(faScore,'TEMEL',90)}<div style="font-size:9px;color:var(--grn);margin-top:2px">Temel Analiz Skoru</div><div style="font-size:8px;color:var(--t4)">(Şirket kalitesi)</div></div>
        <div style="text-align:center">${ring(r.overall,'KARAR',90)}<div style="font-size:9px;color:var(--prp);margin-top:2px">Radar Skoru</div><div style="font-size:8px;color:var(--t4)">(Saf temel)</div></div>
      </div>
      <div class="mg" style="grid-template-columns:repeat(2,1fr)">
        ${mBox('Fiyat',m.price?fN(m.price)+' '+(r.currency||'TL'):'N/A','Şu anki işlem fiyatı')}
        ${mBox('Piyasa Değeri',fN(m.market_cap),'Şirketin borsa değeri (lot × fiyat)')}
        ${mBox('F/K Oranı',fN(m.pe),'Fiyat / Kazanç: Şirketin kârına kaç kat fiyat ödeniyor? 10 altı genelde ucuz, 20+ pahalı sayılır.')}
        ${mBox('PD/DD Oranı',fN(m.pb),'Piyasa / Defter Değeri. 1\'in altı = defter değerinden ucuz.')}
        ${mBox('Özsermaye Kârlılığı',fP(m.roe),'ROE: Şirket her 100 TL özsermayesi için kaç TL kâr ediyor? %15+ iyi kabul edilir.')}
        ${mBox('FCF Getiri',fP(m.fcf_yield),'Serbest Nakit Akışı / Piyasa Değeri. Şirket piyasa değerine göre ne kadar nakit üretiyor?')}
      </div>
    </div>
    <div>
      ${scoreBars(s)}
    </div>
  </div>

  <!-- SECTOR CONTEXT -->
  ${r.sector?`<div style="margin-bottom:14px;padding:10px 14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);font-size:12px;color:var(--t2)">🏢 <b style="color:var(--t1)">${esc(r.sector)}</b> sektöründe faaliyet gösteriyor · <span style="color:var(--t3)">Temel analizde sektör ortalamasıyla karşılaştırılır</span></div>`:''}

  <!-- AI SUMMARY -->
  <div class="aib" style="opacity:0.7"><div class="aib-t">🤖 AI YORUMU <span style="font-size:9px;color:var(--t4);font-weight:400;padding:1px 5px;border:1px solid var(--bdr);border-radius:3px">AI Yorum</span></div><div class="aib-tx" id="ai-${esc(t)}" style="color:var(--t3);font-size:var(--fs-sm)">Özet hazırlanıyor…</div></div>
  <!-- STOCK NEWS (Perplexity) -->
  <div id="news-${esc(t)}" style="display:none"></div>
</div>

<!-- TAB: NEDEN BU KARAR? (conversational, first-class) -->
<div class="ds" id="ds-neden">
  <div style="margin-bottom:16px;padding:16px;background:${vBg(dc)};border:1px solid ${vColor(dc)}30;border-radius:var(--rad2)">
    <div style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:${vColor(dc)};margin-bottom:12px">${esc(r.ticker)} için sistemin kararı: <span style="font-size:20px">${vLabel(dc)}</span></div>
    <div style="font-size:14px;color:var(--t1);line-height:1.8;margin-bottom:12px">${VERDICT_DESC[dc]||''}</div>
    ${r.explanation&&r.explanation.summary?`<div style="font-size:13px;color:var(--t2);line-height:1.8;margin-bottom:12px;padding:12px;background:var(--bg2);border-radius:var(--rad)">${esc(r.explanation.summary)}</div>`:''}
  </div>

  ${(r.positives||[]).length?`<div style="margin-bottom:14px"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--grn);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">✓ Güçlü Yönler</div>${(r.positives||[]).map(p=>`<div style="padding:8px 12px;margin-bottom:6px;background:rgba(76,175,80,.06);border:1px solid rgba(76,175,80,.15);border-left:3px solid var(--grn);border-radius:0 var(--rad) var(--rad) 0;font-size:13px;color:var(--t1);line-height:1.6">${esc(p)}</div>`).join('')}</div>`:''}

  ${(r.negatives||[]).length?`<div style="margin-bottom:14px"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--red);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">✗ Dikkat Edilmesi Gerekenler</div>${(r.negatives||[]).map(n=>`<div style="padding:8px 12px;margin-bottom:6px;background:rgba(239,83,80,.05);border:1px solid rgba(239,83,80,.15);border-left:3px solid var(--red);border-radius:0 var(--rad) var(--rad) 0;font-size:13px;color:var(--t2);line-height:1.6">${esc(n)}</div>`).join('')}</div>`:''}

  ${r.risk_reasons&&r.risk_reasons.length?`<div style="margin-bottom:14px"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ylw);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">⚠️ Risk Faktörleri</div>${r.risk_reasons.map(rr=>`<div style="padding:6px 12px;margin-bottom:4px;background:rgba(255,202,40,.05);border:1px solid rgba(255,202,40,.15);border-radius:var(--rad);font-size:12px;color:var(--ylw)">${esc(rr)}</div>`).join('')}</div>`:''}

  ${r.explanation&&r.explanation.top_positive_drivers&&r.explanation.top_positive_drivers.length?`<div style="margin-bottom:14px"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">📊 Skoru Yukarı Çekenler</div>${r.explanation.top_positive_drivers.slice(0,4).map(d=>`<div style="display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid var(--bdr)"><span style="color:var(--grn);font-size:14px;flex-shrink:0">▲</span><div><div style="font-size:12px;color:var(--t1);font-weight:600">${esc(d.name)}</div>${d.explanation?`<div style="font-size:11px;color:var(--t2);line-height:1.5;margin-top:2px">${esc(d.explanation)}</div>`:''}</div></div>`).join('')}</div>`:''}

  ${r.explanation&&r.explanation.top_negative_drivers&&r.explanation.top_negative_drivers.length?`<div style="margin-bottom:14px"><div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--red);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">📊 Skoru Aşağı Çekenler</div>${r.explanation.top_negative_drivers.slice(0,4).map(d=>`<div style="display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid var(--bdr)"><span style="color:var(--red);font-size:14px;flex-shrink:0">▼</span><div><div style="font-size:12px;color:var(--t1);font-weight:600">${esc(d.name)}</div>${d.explanation?`<div style="font-size:11px;color:var(--t2);line-height:1.5;margin-top:2px">${esc(d.explanation)}</div>`:''}</div></div>`).join('')}</div>`:''}

  <div style="margin-top:16px;padding:12px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);font-size:11px;color:var(--t4);line-height:1.6">
    Bu analiz geçmiş veriye ve matematiksel modellere dayanır. Geleceği garanti etmez. Karar her zaman sizindir.
  </div>
</div>

<!-- TAB: SKORLAR -->
<div class="ds" id="ds-sc">
  <div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--grn);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏛️ Temel Analiz Boyutları — Şirket Kalitesi</div>
  <div class="g4" style="margin-bottom:14px">${[['value','DEĞERLEME','Hisse ucuz mu pahalı mı?'],['quality','ŞİRKET KALİTESİ','Kârlılık ve verimlilik'],['growth','BÜYÜME','Gelir ve kâr artışı'],['balance','FİNANSAL SAĞLIK','Borç ve nakit dengesi'],['earnings','KÂR KALİTESİ','Nakit gerçekten geliyor mu?'],['moat','REKABET AVANTAJI','Rakiplere karşı üstünlük'],['capital','PARAYIZYI KULLANIYOR','Yönetim kararları']].map(([k,l,d])=>`<div style="text-align:center" title="${d}">${ring(s[k],l,76)}</div>`).join('')}</div>
  <div style="font-size:11px;color:var(--t4);text-align:center">0-100 arası skor · 70+ güçlü · 50+ orta · 50 altı zayıf · momentum/teknik için → <b>BullAlfa</b> ya da <b>Cross Hunter</b></div>
</div>

<!-- TAB: DEĞERLEME -->
<div class="ds" id="ds-val">${(function(){const V=r.valuation||{};const VC=r.valuation_confidence||{};const VA=r.valuation_assumptions||{};const VR=r.valuation_risks||[];const VX=r.valuation_context||{};const hasR=V.base_case!=null;const cC=VC.level==='high'?'var(--grn)':VC.level==='medium'?'var(--ylw)':'var(--t4)';const cT=VC.level==='high'?'Güvenilir veri':VC.level==='medium'?'Kısmi veri':'Temkinli oku';let vh='';if(hasR){const vp=V.vs_price!=null?(V.vs_price>15?'— Mevcut fiyata göre iskontolu görünüyor':V.vs_price<-15?'— Mevcut fiyata göre pahalı görünüyor':'— Mevcut fiyata yakın'):'';vh+=`<div style="margin-bottom:14px;padding:14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px">DEĞERLEME ARALIĞI</span><span style="font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 8px;border-radius:3px;background:${cC}15;color:${cC}" title="${esc(VC.reason||'')}">${cT}</span></div><div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:8px"><div style="text-align:center"><div style="font-size:9px;color:var(--red);text-transform:uppercase">Kötümser</div><div style="font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--red)">${V.bear_case!=null?Math.round(V.bear_case):'—'}</div></div><div style="text-align:center"><div style="font-size:9px;color:var(--grn);text-transform:uppercase">Baz Senaryo</div><div style="font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;color:var(--grn)">${V.base_case!=null?Math.round(V.base_case):'—'}</div><div style="font-size:9px;color:var(--t3)">${V.currency||'TL'}</div></div><div style="text-align:center"><div style="font-size:9px;color:var(--blu);text-transform:uppercase">İyimser</div><div style="font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--blu)">${V.bull_case!=null?Math.round(V.bull_case):'—'}</div></div></div><div style="font-size:9px;color:var(--t3)">${vp}</div></div>`;vh+=`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;font-family:'JetBrains Mono',monospace;font-size:9px">${VA.growth_rate!=null?`<span style="padding:3px 8px;background:var(--bg3);border:1px solid var(--bdr);border-radius:3px;color:var(--t2)" title="Son dönem gelir eğilimine göre hesaplanır">Tahmini Büyüme: ${(VA.growth_rate*100).toFixed(0)}%</span>`:''} ${VA.discount_rate!=null?`<span style="padding:3px 8px;background:var(--bg3);border:1px solid var(--bdr);border-radius:3px;color:var(--t2)" title="Piyasa faizi + risk primi">İskonto Oranı: ${(VA.discount_rate*100).toFixed(0)}%</span>`:''}</div>`;}else{vh+='<div style="margin-bottom:12px;padding:10px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);font-size:var(--fs-sm);color:var(--t3)">Değerleme aralığı için yeterli finansal veri bulunamadı.</div>';}if(VX.pb_note){vh+=`<div style="margin-bottom:8px;font-size:var(--fs-sm);color:var(--t2)"><span style="color:var(--acc)">⬡</span> PD/DD Notu: <span style="color:var(--ylw)">${esc(VX.pb_note)}</span></div>`;}if(VR.length){vh+=`<div style="margin-bottom:8px"><div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--ylw);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">DEĞERLEMEYİ ETKİLEYEN RİSKLER</div>${VR.map(x=>`<div style="font-size:var(--fs-xs);color:var(--t3);padding:2px 0"><span style="color:var(--ylw)">⚠</span> ${esc(x)}</div>`).join('')}</div>`;}return vh;})()}${cpL?`<div style="margin-bottom:12px;padding:10px 14px;background:${cpL.color}10;border:1px solid ${cpL.color}25;border-radius:var(--rad);display:flex;justify-content:space-between;align-items:center"><div><span style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:${cpL.color}">${cpL.label}</span><span style="font-size:11px;color:var(--t2);margin-left:8px">Ciro/Piyasa Değeri = ${cpL.value}x</span></div><span style="font-size:9px;color:var(--t3)" title="Yüksek Ciro/PD = ucuz olabilir, şirket fazla ciro yapıyor">ℹ️</span></div>`:''}
<div class="mg">
  ${mBox('F/K Oranı',fN(m.pe),'Fiyat/Kazanç: Şirketin kârına kaç kat fiyat biçildiği. 10 altı ucuz, 20+ pahalı genel kanı.')}
  ${mBox('PD/DD Oranı',fN(m.pb),'Piyasa/Defter: 1\'in altında = hisse defter değerinden ucuz satılıyor.')}
  ${mBox('FD/FAVÖK',fN(m.ev_ebitda),'Firma Değeri/FAVÖK: Borçla birlikte şirkete ödenen fiyat. 7 altı genellikle makul.')}
  ${mBox('Ciro/PD',v11.ciro_pd?v11.ciro_pd.toFixed(2)+'x':'N/A','Yıllık Ciro / Piyasa Değeri. Yüksek değer şirketin ucuz olabileceğine işaret eder.')}
  ${mBox('PEG Oranı',L.peg||'N/A','Fiyat/Kazanç oranını büyümeyle karşılaştırır. 1\'den düşük = büyümesine göre ucuz.')}
  ${mBox('Nakit Akışı Getiri',fP(m.fcf_yield),'Serbest Nakit Akışı / Piyasa Değeri. Şirket gerçekte ne kadar nakit üretiyor?')}
  ${mBox('Graham Değeri',fN(m.graham_fv),'Benjamin Graham\'ın içsel değer formülü. Fiyatın altındaysa güvenlik payı var.')}
  ${mBox('Güvenlik Payı',L.graham_mos||'N/A','Hesaplanan değer ile mevcut fiyat arasındaki fark. Yüksekse daha güvenli.')}
  ${mBox('Temettü Getiri',fP(m.dividend_yield),'Yıllık temettü / hisse fiyatı. Nakit dağıtımı ne kadar?')}
  ${mBox('Piyasa Değeri',fN(m.market_cap),'Şirketin toplam borsa değeri.')}
  ${mBox('Beta',fN(m.beta),'Piyasaya göre oynaklık. 1\'den büyük = piyasadan daha volatil, küçük = daha sakin.')}
</div>
<div style="margin-top:12px;padding:8px 12px;border-top:1px solid var(--bdr);font-size:10px;color:var(--t4);line-height:1.6">Bu bir fiyat hedefi veya al-sat sinyali değildir. Varsayımlara dayalı değerleme çerçevesidir. · Karar sizindir.</div>
</div>

<!-- TAB: KALİTE -->
<div class="ds" id="ds-qua">
  <div style="margin-bottom:8px;font-size:12px;color:var(--t3)">Kârlılık, verimlilik ve büyüme metrikleri. Yüksek değerler genellikle güçlü şirket göstergesidir.</div>
  <div class="mg">
    ${mBox('Özsermaye Kârlılığı (ROE)',fP(m.roe),'Şirket her 100 TL özsermaye için ne kadar kâr ediyor? %15+ iyi kabul edilir.')}
    ${mBox('Aktif Kârlılığı (ROA)',fP(m.roa),'Tüm varlıkların ne kadar verimli kullanıldığı. Sektöre göre kalibre edilir.')}
    ${mBox('Yatırım Getirisi (ROIC)',fP(m.roic),'Hem borç hem özsermaye dahil en kapsamlı kârlılık ölçütü.')}
    ${mBox('Brüt Kâr Marjı',fP(m.gross_margin),'Satışlardan üretim maliyeti düşüldükten sonra kalan. Yüksek = fiyatlama gücü.')}
    ${mBox('Faaliyet Marjı',fP(m.operating_margin),'Operasyonlardan elde edilen kâr oranı. Ana iş ne kadar kârlı?')}
    ${mBox('Net Kâr Marjı',fP(m.net_margin),'Her 100 TL satış gelirinden kaç TL net kâr elde ediliyor?')}
    ${mBox('Gelir Büyümesi',fP(m.revenue_growth),'Yıllık gelir artış oranı. Enflasyonun üzerinde reel büyüme önemli.')}
    ${mBox('Hisse Başı Kâr Büyümesi',fP(m.eps_growth),'HBK yıllık büyüme oranı. Sürdürülebilir büyüme mi?')}
    ${mBox('Toplam Gelir',fN(m.revenue),'Şirketin toplam yıllık cirosu.')}
    ${mBox('Net Kâr',fN(m.net_income),'Şirketin yıllık net kârı (tüm giderler düşüldükten sonra).')}
    ${mBox('FAVÖK',fN(m.ebitda),'Faiz, Amortisman, Vergi Öncesi Kâr. Operasyonel gücün ölçütü.')}
    ${mBox('Serbest Nakit Akışı',fN(m.free_cf),'Şirketin gerçekte ürettiği nakit. Kâğıt kârı değil, gerçek para.')}
  </div>
  <h4 style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t3);margin:14px 0 8px">BİLANÇO — Finansal Sağlık</h4>
  <div style="margin-bottom:8px;font-size:12px;color:var(--t3)">Borç ve nakit dengesi. Yüksek borç yüksek faiz ortamında risk yaratır.</div>
  <div class="mg">
    ${mBox('Toplam Varlık',fN(m.total_assets),'Şirketin sahip olduğu her şeyin toplam değeri.')}
    ${mBox('Toplam Borç',fN(m.total_debt),'Kısa ve uzun vadeli tüm borçlar. Nakitten düşük olmalı ideal.')}
    ${mBox('Nakit & Benzeri',fN(m.cash),'Şirketin kasasındaki nakit ve hızla nakde çevrilebilecek varlıklar.')}
    ${mBox('Özsermaye',fN(m.equity),'Varlıklar eksi borçlar. Negatif özsermaye ciddi risk işareti.')}
    ${mBox('Net Borç/FAVÖK',fN(m.net_debt_ebitda),'3\'ün altı genellikle yönetilebilir borç seviyesi.')}
    ${mBox('Borç/Özsermaye',fN(m.debt_equity),'Şirket kendi parasına mı yoksa borca mı dayanıyor?')}
    ${mBox('Cari Oran',fN(m.current_ratio),'Kısa vadeli varlıklar / kısa vadeli borçlar. 1\'den büyük olmalı.')}
    ${mBox('Faiz Karşılama',fN(m.interest_coverage),'Kâr, faiz ödemelerinin kaç katı? 3\'ün altı riskli.')}
  </div>
</div>

<!-- TAB: TEKNİK -->
<div class="ds" id="ds-tek"><div style="margin-bottom:12px;padding:8px 12px;background:var(--bg3);border:1px solid var(--bdr);border-left:3px solid var(--blu);border-radius:0 var(--rad) var(--rad) 0;font-size:11px;color:var(--t3);line-height:1.5">ℹ️ Teknik göstergeler yalnızca referans içindir — <b style="color:var(--t2)">Radar sıralamasına etki etmez</b>. Radar saf temel analiz sıralaması yapar. Giriş zamanlaması için Cross Hunter / BullWatch'a bakın.</div>${tech?renderTech(tech):'<div class="emp"><h3 style="color:var(--t2)">Teknik veri yok</h3></div>'}</div>

<!-- TAB: GÖSTERGELER -->
<div class="ds" id="ds-leg">
  ${regL?`<div style="margin-bottom:12px;padding:10px 14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t1);display:flex;justify-content:space-between;align-items:center"><span>${esc(regL)}</span>${convL.score!=null?`<span style="padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700;background:${convL.level==='HIGH'?'var(--grnd)':convL.level==='MEDIUM'?'var(--ylwd)':'var(--bg4)'};color:${convL.level==='HIGH'?'var(--grn)':convL.level==='MEDIUM'?'var(--ylw)':'var(--t3)'}">Güven: ${convL.score}</span>`:''}</div>`:''} 
  <div style="margin-bottom:8px;font-size:12px;color:var(--t3)">Akademik modeller ve lejander yatırımcı filtreleri. Sayısal değil, geçti/kaldı formatında.</div>
  <div class="mg" style="grid-template-columns:repeat(3,1fr)">
    ${mBox('Piotroski F-Score',L.piotroski||'N/A','0-9 arası finansal sağlık puanı. 7+ güçlü, 3- zayıf. Joseph Piotroski\'nin 9 kriterli modeli.')}
    ${mBox('Altman Z-Score',L.altman||'N/A','İflas riski ölçütü. 3+ güvenli, 1.8 altı riskli. Bankalar için geçersiz.')}
    ${mBox('Muhasebe Riski',(r.turkey_context&&r.turkey_context.accounting_risk?r.turkey_context.accounting_risk.level:null)||L.beneish||'N/A','Beneish M-Score: Muhasebe manipülasyonu testi. -2.22 altı temiz, yüksek değer şüphe yaratır.')}
    ${mBox('PEG Oranı',L.peg||'N/A','F/K oranını büyüme ile karşılaştırır. 1 altı = büyümesine göre ucuz.')}
    ${mBox('Graham Güvenlik Payı',L.graham_mos||'N/A','Hesaplanan değer ile piyasa fiyatı arasındaki fark. Graham\'ın güvenlik marjı.')}
    ${mBox('Buffett Filtresi',L.buffett_filter||'N/A','Kalite + Rekabet Avantajı + Finansal Sağlık birleşimi. Warren Buffett\'ın kriterleri.')}
    ${mBox('Graham Filtresi',L.graham_filter||'N/A','Değer + Finansal Sağlık + Güvenlik Payı. Benjamin Graham\'ın kriterleri.')}
    ${mBox('Nakit Kâr Kalitesi',eqL.label||'N/A','Nakit akışı kârı destekliyor mu? CFO/Net Kâr ≥ 1 = gerçek kâr.')}
    ${mBox('Sermaye Kullanımı',caL.label||'N/A','Yönetim kazandığı parayı yatırımcı için en iyi şekilde harcıyor mu?')}
  </div>
  ${(legV11.buffett_graham||legV11.anti_bubble||legV11.value_trap!=null)?`<div style="margin-top:12px;font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--cyn);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏆 Özel Filtreler</div><div style="display:flex;gap:6px;flex-wrap:wrap">${legV11.buffett_graham?'<span class="pill" style="background:rgba(76,175,80,.12);color:var(--grn);font-weight:700;font-size:10px;padding:4px 10px;border:1px solid rgba(76,175,80,.25)" title="Hem Buffett hem Graham kriterlerini geçiyor">🏛️ Buffett-Graham Hybrid</span>':''}${legV11.anti_bubble?'<span class="pill" style="background:rgba(0,188,212,.12);color:var(--cyn);font-weight:700;font-size:10px;padding:4px 10px;border:1px solid rgba(0,188,212,.25)" title="Balon riski düşük, sağlam temel">🛡️ Anti-Balon Bileşik</span>':''}${legV11.value_trap===true?'<span class="pill" style="background:rgba(76,175,80,.12);color:var(--grn);font-weight:700;font-size:10px;padding:4px 10px;border:1px solid rgba(76,175,80,.25)" title="Değer tuzağı testi geçti — ucuz ama gerçek">🎯 Değer Tuzağı Testi: GEÇTİ</span>':''}${legV11.value_trap===false?'<span class="pill" style="background:rgba(239,83,80,.12);color:var(--red);font-weight:700;font-size:10px;padding:4px 10px;border:1px solid rgba(239,83,80,.25)" title="Değer tuzağı riski var — dikkatli ol">🎯 Değer Tuzağı Testi: KALDI</span>':''}</div>`:''}
  <div style="margin-top:16px;padding:12px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad);font-size:13px;color:var(--t2);line-height:1.7">
    <b style="color:var(--t1)">Neden Güçlü?</b><br>${(r.positives||[]).map(p=>`<span style="color:var(--grn)">✓</span> ${esc(p)}`).join('<br>')}<br><br>
    <b style="color:var(--t1)">Dikkat Et:</b><br>${(r.negatives||[]).map(n=>`<span style="color:var(--red)">✗</span> ${esc(n)}`).join('<br>')}
  </div>
</div>

<!-- TAB: GRAFİK -->
<div class="ds" id="ds-cht">
  <div style="margin-bottom:8px;font-size:11px;color:var(--t3)">Son 1 yıllık fiyat grafiği · Veriler gecikmiş olabilir</div>
  <img class="chart-img" src="/api/chart/${encodeURIComponent(t)}" alt="${esc(t)}" onerror="this.parentElement.innerHTML='<div class=\\'emp\\'><h3>Grafik yüklenemedi</h3></div>'">
</div>

</div>`;
const _aiCtrl=new AbortController();setTimeout(()=>_aiCtrl.abort(),10000);api(`/api/ai-summary/${t}`,{signal:_aiCtrl.signal}).then(d=>{const el=$('ai-'+t);if(el){
const aib=el.closest('.aib');
const badge=aib?aib.querySelector('.aib-t span'):null;
if(d.is_fallback&&badge){badge.textContent='Veri yetersiz';badge.style.color='var(--ylw)';badge.style.borderColor='var(--ylw)';}
el.textContent=d.summary||'AI özeti şu an mevcut değil.';
if(d.summary)aib.style.opacity='1';
if(d.data_grade==='C'||d.data_grade==='D'){el.style.color='var(--ylw)';}
}}).catch(()=>{const el=$('ai-'+t);if(el)el.textContent='AI özeti şu an mevcut değil.';});
// Stock news via Perplexity (non-blocking, separate from decision)
api(`/api/stock-news/${t}`).then(d=>{if(d.available&&d.news){const nb=$('news-'+t);if(nb){nb.style.display='block';nb.innerHTML=`<div style="margin-top:10px;padding:12px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)"><div style="display:flex;align-items:center;gap:6px;margin-bottom:6px"><span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--cyn);text-transform:uppercase">🌍 Son Haberler</span><span style="font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--ylw);padding:1px 4px;border:1px solid var(--ylw);border-radius:2px">Karar motoruna girmez</span></div><div style="font-size:var(--fs-xs);color:var(--t2);line-height:1.6">${esc(d.news).replace(/\\n/g,'<br>')}</div><div style="margin-top:4px;font-size:8px;color:var(--t4)">Kaynak: ${esc(d.source||'Web')}</div></div>`;}}}).catch(()=>{});
// ── DOM ENHANCEMENTS (each section independently try/caught) ──
try{
// Delta card
var _wc=r.what_changed||[];var _d=r.delta||{};
if(_wc.length&&_wc[0]!=='Önemli bir de\u011fi\u015fiklik yok'){
var _dEl=document.createElement('div');_dEl.style.cssText='margin-top:14px;padding:12px 14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)';
var _dh2='<div style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">SON DE\u011e\u0130\u015e\u0130M</div>';
_dh2+='<div style="display:flex;gap:10px;margin-bottom:6px;font-family:\'JetBrains Mono\',monospace;font-size:11px">';
if(_d.score_7d!=null){var _sc3=_d.score_7d;_dh2+='<span style="color:'+(_sc3>=0?'var(--grn)':'var(--red)')+'">Skor '+(_sc3>=0?'+':'')+Math.round(_sc3)+'</span>';}
_dh2+='</div>';_dh2+=_wc.map(function(w){return'<div style="font-size:var(--fs-xs);color:var(--t2);padding:1px 0">\u2022 '+esc(w)+'</div>';}).join('');
_dEl.innerHTML=_dh2;var _ov1=document.getElementById('ds-ov');if(_ov1)_ov1.appendChild(_dEl);
}}catch(e){}

try{
// Timing intel card
var _ti=r.timing_intel||{};var _ra=r.recent_activity||[];var _wp=r.watch_points||[];var _ss=r.signal_summary||[];var _tt=r.trend_timeline||{};
if(_ti.state&&_ti.state!=='belirsiz'){
var _tiEl=document.createElement('div');_tiEl.style.cssText='margin-top:14px;padding:14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)';
var _tsC=_ti.state==='uygun'?'var(--grn)':_ti.state==='erken'?'var(--ylw)':'var(--t3)';
var _th='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px">ZAMANLAMA</span><span style="font-family:\'JetBrains Mono\',monospace;font-size:10px;padding:2px 8px;border-radius:3px;background:'+_tsC+'15;color:'+_tsC+'">'+esc(_ti.text||'')+'</span></div>';
if(_tt.kısa_vade){var _tlC2=function(v){return v==='g\u00fc\u00e7l\u00fc'?'var(--grn)':v==='zay\u0131f'?'var(--red)':'var(--t3)';};_th+='<div style="display:flex;gap:12px;margin-bottom:8px;font-family:\'JetBrains Mono\',monospace;font-size:9px"><span style="color:var(--t3)">K\u0131sa: <span style="color:'+_tlC2(_tt.kısa_vade)+'">'+esc(_tt.kısa_vade)+'</span></span><span style="color:var(--t3)">Orta: <span style="color:'+_tlC2(_tt.orta_vade||'-')+'">'+esc(_tt.orta_vade||'-')+'</span></span><span style="color:var(--t3)">Uzun: <span style="color:'+_tlC2(_tt.uzun_vade||'-')+'">'+esc(_tt.uzun_vade||'-')+'</span></span></div>';}
if(_ra.length){_th+='<div style="margin-bottom:6px"><div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--blu);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">SON GEL\u0130\u015eMELER</div>'+_ra.map(function(a){return'<div style="font-size:var(--fs-xs);color:var(--t2);padding:1px 0">\u2022 '+esc(a)+'</div>';}).join('')+'</div>';}
if(_wp.length){_th+='<div style="margin-bottom:6px"><div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--cyn);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">TAK\u0130P ET</div>'+_wp.map(function(w){return'<div style="font-size:var(--fs-xs);color:var(--t2);padding:1px 0">\u25b8 '+esc(w)+'</div>';}).join('')+'</div>';}
if(_ss.length){_th+='<div style="margin-bottom:6px"><div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">S\u0130NYALLER</div>'+_ss.map(function(s){return'<div style="font-size:var(--fs-xs);color:var(--t3);padding:1px 0">\u25cb '+esc(s)+'</div>';}).join('')+'</div>';}
_th+='<div style="font-size:9px;color:var(--t4);margin-top:6px;font-style:italic">Bu bir zamanlama yorumudur, kesin sinyal de\u011fildir.</div>';
_tiEl.innerHTML=_th;var _ov2=document.getElementById('ds-ov');if(_ov2)_ov2.appendChild(_tiEl);
}}catch(e){}

try{
// Dimension explanations (DOM append to Skorlar tab)
var _de=r.dimension_explanations||{};var _tc=r.turkey_context||{};
if(Object.keys(_de).length){
var _deEl=document.createElement('div');_deEl.style.cssText='margin-top:14px;padding:12px 14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)';
var _deh='<div style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">BOYUT YORUMLARI</div>';
var _dimOrder=[['value','Değerleme'],['quality','Kalite'],['growth','Büyüme'],['balance','Bilanço'],['earnings','Kâr Kalitesi'],['moat','Hendek'],['capital','Sermaye']];
_dimOrder.forEach(function(d){var exp=_de[d[0]];if(exp){_deh+='<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:var(--fs-xs);border-bottom:1px solid var(--bdr)"><span style="color:var(--t3)">'+d[1]+'</span><span style="color:var(--t2)">'+esc(exp)+'</span></div>';}});
_deEl.innerHTML=_deh;var _scTab=document.getElementById('ds-sc');if(_scTab)_scTab.appendChild(_deEl);}
}catch(e){}

try{
// Turkey context card
var _tc=r.turkey_context||{};var _ia=_tc.inflation_accounting||{};var _pqi=_tc.profit_quality_interpretation||{};var _ar=_tc.accounting_risk||{};var _tn=_tc.turkey_notes||[];
if(_pqi.level||_ar.level||_tn.length){
var _tcEl=document.createElement('div');_tcEl.style.cssText='margin-top:14px;padding:12px 14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)';
var _tch='<div style="font-family:\'JetBrains Mono\',monospace;font-size:var(--fs-xs);color:var(--acc);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">T\u00dcRK\u0130YE KONTEKST</div>';
if(_pqi.level){var _pqC=_pqi.level==='iyi'?'var(--grn)':_pqi.level==='orta'?'var(--ylw)':'var(--red)';_tch+='<div style="margin-bottom:6px;font-size:var(--fs-sm);color:var(--t2)"><span style="color:var(--t3)">K\u00e2r Kalitesi:</span> <span style="color:'+_pqC+'">'+esc(_pqi.summary||'')+'</span></div>';}
if(_ar.level&&_ar.level!=='bilinmiyor'){var _arC=_ar.level==='d\u00fc\u015f\u00fck'?'var(--grn)':_ar.level==='orta'?'var(--ylw)':'var(--red)';_tch+='<div style="margin-bottom:6px;font-size:var(--fs-sm);color:var(--t2)"><span style="color:var(--t3)">Muhasebe Riski:</span> <span style="color:'+_arC+'">'+esc(_ar.level)+'</span> <span style="color:var(--t4);font-size:var(--fs-xs)">'+esc(_ar.note||'')+'</span></div>';}
if(_ia.status&&_ia.status!=='normal'){_tch+='<div style="margin-bottom:6px;font-size:var(--fs-xs);color:var(--ylw)">\u26a0 '+esc(_ia.note||'')+'</div>';}
if(_tn.length){_tch+=_tn.map(function(n){return'<div style="font-size:var(--fs-xs);color:var(--t3);padding:1px 0">\u25b8 '+esc(n)+'</div>';}).join('');}
_tcEl.innerHTML=_tch;var _ov4=document.getElementById('ds-ov');if(_ov4)_ov4.appendChild(_tcEl);}
}catch(e){}

try{
// Karşılaştır button (near share)
var _dpH3=document.querySelector('.dp-h > div');
if(_dpH3){
var _cmpBtn=document.createElement('button');_cmpBtn.className='btn btn-sm';_cmpBtn.style.cssText='background:var(--bg3);border:1px solid var(--bdr);color:var(--cyn);font-size:10px;padding:4px 8px;margin-left:4px;cursor:pointer';_cmpBtn.textContent='\u2194 Kar\u015f\u0131la\u015ft\u0131r';
_cmpBtn.onclick=function(){
var _inp=prompt('Kar\u015f\u0131la\u015ft\u0131rmak istedi\u011fin hisseyi yaz (\u00f6r: Tofaş, FROTO, Ereğli):');
if(!_inp)return;_inp=_inp.trim().toUpperCase();if(_inp.length<2)return;
_cmpBtn.textContent='Y\u00fckleniyor...';_cmpBtn.disabled=true;
api('/api/resolve-ticker?q='+encodeURIComponent(_inp)).then(function(_rd){var _rt=(_rd&&_rd.tickers&&_rd.tickers[0])||_inp;return api('/api/compare?left='+encodeURIComponent(r.ticker)+'&right='+encodeURIComponent(_rt));}).then(function(d){
_cmpBtn.textContent='\u2194 Kar\u015f\u0131la\u015ft\u0131r';_cmpBtn.disabled=false;
if(!d||d.error){alert('Kar\u015f\u0131la\u015ft\u0131rma yap\u0131lamad\u0131');return;}
_showCompare(d);
}).catch(function(){_cmpBtn.textContent='\u2194 Kar\u015f\u0131la\u015ft\u0131r';_cmpBtn.disabled=false;});
};
var _pr3=_dpH3.querySelector('div:last-child');if(_pr3)_pr3.appendChild(_cmpBtn);}
}catch(e){}

try{
// Compare overlay renderer
window._showCompare=function(d){
var c=d.comparison||{};var L=d.left||{};var R=d.right||{};var lt=c.left_ticker||'?';var rt=c.right_ticker||'?';
var ov=document.createElement('div');ov.id='_cmpOverlay';ov.style.cssText='position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.85);overflow-y:auto;padding:20px';
ov.onclick=function(e){if(e.target===ov)ov.remove();};
function _pill(v,l){var col=v>=65?'var(--grn)':v>=45?'var(--ylw)':'var(--red)';return'<div style="text-align:center"><div style="font-size:9px;color:var(--t3)">'+l+'</div><div style="font-family:\'JetBrains Mono\',monospace;font-size:16px;font-weight:700;color:'+col+'">'+Math.round(v)+'</div></div>';}
function _dimRow(label,lv,rv){var col='var(--t3)';var arrow='=';if(lv>rv+3){col='var(--cyn)';arrow='\u25c0';}else if(rv>lv+3){col='var(--cyn)';arrow='\u25b6';}return'<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--bdr)"><span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:'+(lv>rv+3?'var(--grn)':'var(--t2)')+'">'+Math.round(lv)+'</span><span style="font-size:10px;color:var(--t3)">'+esc(label)+'</span><span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:'+(rv>lv+3?'var(--grn)':'var(--t2)')+'">'+Math.round(rv)+'</span></div>';}
var h='<div style="max-width:500px;margin:0 auto;background:var(--bg2);border:1px solid var(--bdr);border-radius:var(--rad2);padding:20px">';
h+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><span style="font-family:\'JetBrains Mono\',monospace;font-size:18px;color:var(--t1)">'+esc(lt)+' <span style="color:var(--t4)">vs</span> '+esc(rt)+'</span><button onclick="document.getElementById(\'_cmpOverlay\').remove()" style="background:none;border:none;color:var(--t3);font-size:18px;cursor:pointer">\u2715</button></div>';
h+='<div style="margin-bottom:14px;font-size:var(--fs-sm);color:var(--t2);line-height:1.6">'+esc(c.summary||'')+'</div>';
if((c.key_differences||[]).length){h+='<div style="margin-bottom:14px">'+c.key_differences.map(function(d2){return'<div style="font-size:var(--fs-xs);color:var(--cyn);padding:2px 0">\u25b8 '+esc(d2)+'</div>';}).join('')+'</div>';}
h+='<div style="display:flex;justify-content:space-between;margin-bottom:14px;padding:12px;background:var(--bg3);border-radius:var(--rad)">';
var ls=c.scores?.left||{};var rs2=c.scores?.right||{};
h+='<div style="text-align:center;flex:1">'+_pill(ls.overall||0,lt)+'</div>';
h+='<div style="text-align:center;flex:1">'+_pill(rs2.overall||0,rt)+'</div></div>';
var lsc=L.scores||{};var rsc=R.scores||{};
h+=_dimRow('De\u011ferleme',lsc.value||50,rsc.value||50);
h+=_dimRow('Kalite',lsc.quality||50,rsc.quality||50);
h+=_dimRow('B\u00fcy\u00fcme',lsc.growth||50,rsc.growth||50);
h+=_dimRow('Bilan\u00e7o',lsc.balance||50,rsc.balance||50);
h+=_dimRow('K\u00e2r Kal.',lsc.earnings||50,rsc.earnings||50);
h+=_dimRow('Momentum',lsc.momentum||50,rsc.momentum||50);
// Analyst commentary (deterministic, data-grounded)
if(c.analyst_commentary){h+='<div style="margin-top:14px;padding:10px;background:var(--bg3);border-radius:var(--rad);font-size:var(--fs-sm);color:var(--t1);line-height:1.6">'+esc(c.analyst_commentary)+'</div>';}
// AI commentary (if available)
if(d.ai_commentary&&!d.ai_commentary.includes('yeterli veri yok')){h+='<div style="margin-top:8px;padding:10px;background:linear-gradient(135deg,#140e30,var(--bg2));border:1px solid var(--prp);border-radius:var(--rad)"><div style="display:flex;align-items:center;gap:6px;margin-bottom:6px"><span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--prp);text-transform:uppercase">🤖 AI Karşılaştırma</span><span style="font-size:7px;color:var(--t4);padding:1px 4px;border:1px solid var(--bdr);border-radius:2px">AI Yorum</span></div><div style="font-size:var(--fs-sm);color:var(--t1);line-height:1.6">'+esc(d.ai_commentary)+'</div></div>';}
// Conclusion
h+='<div style="margin-top:10px;padding:10px;background:var(--bg3);border-radius:var(--rad);font-size:var(--fs-sm);color:var(--t2);line-height:1.6;font-style:italic">'+esc(c.conclusion||'')+'</div>';
if(d.pplx_news){h+='<div style="margin-top:10px;padding:10px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)"><div style="display:flex;align-items:center;gap:6px;margin-bottom:6px"><span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--cyn);text-transform:uppercase">🌍 Son Gelişmeler</span><span style="font-family:\'JetBrains Mono\',monospace;font-size:7px;color:var(--ylw);padding:1px 4px;border:1px solid var(--ylw);border-radius:2px">Karar motoruna girmez</span></div><div style="font-size:var(--fs-xs);color:var(--t2);line-height:1.6">'+esc(d.pplx_news).replace(/\n/g,'<br>')+'</div></div>';}
h+='<div style="margin-top:10px;font-size:9px;color:var(--t4);text-align:center">Bu bir karşılaştırma çerçevesidir, yatırım tavsiyesi değildir.</div>';
h+='</div>';ov.innerHTML=h;document.body.appendChild(ov);};

// Trust footer
var _dh4=r.data_health||{};var _dc4=r.decision_context||{};var _dhG=_dh4.grade||'A';
var _trEl=document.createElement('div');_trEl.style.cssText='margin-top:14px;padding:10px 14px;border-top:1px solid var(--bdr);font-size:10px;color:var(--t4);line-height:1.6';
var _trH='';
if(_dhG==='C'||_dhG==='D'){_trH+='<div style="margin-bottom:6px;padding:8px 12px;background:rgba(255,179,0,.08);border:1px solid rgba(255,179,0,.2);border-radius:var(--rad);display:flex;align-items:center;gap:8px"><span style="font-size:16px">⚠️</span><div><div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--ylw);font-weight:700">VERİ KALİTESİ DÜŞÜK ('+_dhG+')</div><div style="font-size:10px;color:var(--t2);margin-top:2px">Bazı finansal veriler eksik. Skorlar ve etiketler referans amaçlıdır — tek başına karar almak için yeterli değil.</div></div></div>';}
if(_dc4.caveats&&_dc4.caveats.length){_trH+='<div style="margin-bottom:4px;display:flex;gap:4px;flex-wrap:wrap">'+_dc4.caveats.map(function(c){return '<span style="font-family:\'JetBrains Mono\',monospace;font-size:8px;color:var(--ylw);padding:1px 5px;border:1px solid var(--ylw);border-radius:2px">'+esc(c)+'</span>';}).join('')+'</div>';}
_trH+='<div style="display:flex;gap:8px;margin-top:4px;font-family:\'JetBrains Mono\',monospace;font-size:8px;color:var(--t4)">';
_trH+='<span style="padding:1px 5px;border:1px solid var(--bdr);border-radius:2px">Skorlar: Hesaplanmış</span>';
_trH+='<span style="padding:1px 5px;border:1px solid var(--bdr);border-radius:2px">Bilanço: KAP · Çeyreklik</span>';
_trH+='<span style="padding:1px 5px;border:1px solid var(--bdr);border-radius:2px">Fiyat: Piyasa · Günlük</span>';
_trH+='</div>';
_trH+='<div style="color:var(--t4);font-size:9px;margin-top:6px">Bu bir al-sat sinyali değil. Karar yine senin.</div>';
_trEl.innerHTML=_trH;var _ov3=document.getElementById('ds-ov');if(_ov3)_ov3.appendChild(_trEl);
}catch(e){}

try{
// Share — WhatsApp-first, natural Turkish
var _dpH2=document.querySelector('.dp-h > div');
if(_dpH2){
function _mkSh(){
var tk=r.ticker||'';var dec=r.decision||'';var ti=(r.timing_intel||{}).state||'';var wc=(r.what_changed||[]);
var dt=(dec==='\u00c7ok Ba\u015far\u0131l\u0131'||dec==='Ba\u015far\u0131l\u0131')?'g\u00fc\u00e7l\u00fc g\u00f6r\u00fcn\u00fcyor':dec==='Orta'?'ortalama g\u00f6r\u00fcn\u00fcyor':dec==='Zay\u0131f'?'zay\u0131f g\u00f6r\u00fcn\u00fcyor':dec==='Riskli'?'riskli g\u00f6r\u00fcn\u00fcyor':'de\u011ferlendiriliyor';
var tt=ti==='uygun'?'zamanlama da uygun':ti==='erken'?'ama zamanlama biraz erken':ti==='bekle'?'\u015fu an beklemek daha mant\u0131kl\u0131':'';
var wt='';if(wc.length&&wc[0]!=='\u00d6nemli bir de\u011fi\u015fiklik yok'){wt='son durumda '+wc[0].toLowerCase();}
var l=[tk,'','\u015fu an '+dt];if(tt)l.push(tt);if(wt)l.push(wt);l.push('');l.push('bir bak derim:');l.push('https://bistbull.ai/terminal?t='+tk);
return l.join('\n');}
var _sc2=document.createElement('div');_sc2.style.cssText='display:inline-flex;gap:4px;margin-left:6px';
var _wa=document.createElement('button');_wa.className='btn btn-sm';_wa.style.cssText='background:rgba(37,211,102,.12);border:1px solid rgba(37,211,102,.25);color:#25d366;font-size:10px;padding:4px 8px;cursor:pointer';_wa.textContent='WhatsApp';
_wa.onclick=function(){window.open('https://wa.me/?text='+encodeURIComponent(_mkSh()),'_blank');};
var _cp=document.createElement('button');_cp.className='btn btn-sm';_cp.style.cssText='background:var(--bg3);border:1px solid var(--bdr);color:var(--t3);font-size:10px;padding:4px 8px;cursor:pointer';_cp.textContent='kopyala';
_cp.onclick=function(){navigator.clipboard.writeText(_mkSh()).then(function(){_cp.textContent='\u2705';setTimeout(function(){_cp.textContent='kopyala';},1500);}).catch(function(){});};
_sc2.appendChild(_wa);_sc2.appendChild(_cp);
var _pr2=_dpH2.querySelector('div:last-child');if(_pr2)_pr2.appendChild(_sc2);}
}catch(e){}

try{
// Valuation slider
var _vm=document.getElementById('_valSliderMount');
if(_vm&&r.valuation&&r.valuation.base_case!=null&&r.valuation.method!=='graham'&&r.valuation.method!=='unavailable'){
var _vi2=r.valuation_inputs||{};var _va2=r.valuation_assumptions||{};
var _oG=Math.round((_va2.growth_rate||0.10)*100);var _oD=Math.round((_va2.discount_rate||0.38)*100);
var _cf2=r.valuation.method==='dcf_fcf'?(_vi2.free_cf||0):(r.valuation.method==='dcf_earnings'?((_vi2.net_income||0)*0.7):((_vi2.revenue||0)*(_va2.margin_assumption||0.1)*0.7));
var _nd2=_vi2.net_debt||0;var _sh2=_vi2.shares_outstanding||1;var _pr3=_vi2.last_price||1;
function _dcf2(g,d){if(d<=0.04)d=0.09;var tot=0,pcf=Math.abs(_cf2)||1;for(var y=1;y<=5;y++){pcf*=(1+g);tot+=pcf/Math.pow(1+d,y);}var tv=pcf*1.04/(d-0.04);tot+=tv/Math.pow(1+d,5);var eq=tot-_nd2;if(eq<=0)eq=tot*0.1;return eq/_sh2;}
var _sp2=document.createElement('div');_sp2.style.cssText='margin-top:12px';
_sp2.innerHTML='<div><button id="_vT2" style="background:none;border:1px solid var(--bdr);border-radius:var(--rad);padding:5px 12px;font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--t3);cursor:pointer">\u2699 Varsay\u0131mlar\u0131 de\u011fi\u015ftir</button></div><div id="_vP2" style="display:none;margin-top:8px;padding:14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--acc);text-transform:uppercase;letter-spacing:1px">SENARYO ARACI</span><button id="_vR2" style="display:none;background:none;border:1px solid var(--bdr);border-radius:3px;padding:2px 8px;font-size:9px;color:var(--t3);cursor:pointer;font-family:\'JetBrains Mono\',monospace">\u21ba S\u0131f\u0131rla</button></div><div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:10px;color:var(--t2);margin-bottom:4px"><span>B\u00fcy\u00fcme</span><span id="_gV2" style="font-family:\'JetBrains Mono\',monospace;color:var(--grn)">'+_oG+'%</span></div><input id="_gS2" type="range" min="5" max="50" value="'+_oG+'" style="width:100%;accent-color:var(--grn)"></div><div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:10px;color:var(--t2);margin-bottom:4px"><span>\u0130skonto</span><span id="_dV2" style="font-family:\'JetBrains Mono\',monospace;color:var(--ylw)">'+_oD+'%</span></div><input id="_dS2" type="range" min="15" max="60" value="'+_oD+'" style="width:100%;accent-color:var(--ylw)"></div><div id="_vC2" style="display:none;margin-bottom:8px;padding:6px 10px;background:rgba(0,188,212,.06);border:1px solid rgba(0,188,212,.15);border-radius:var(--rad);font-size:9px;color:var(--cyn);font-family:\'JetBrains Mono\',monospace">\u21b3 Kendi varsay\u0131m\u0131na g\u00f6re</div><div style="display:flex;justify-content:space-between;align-items:flex-end;padding:8px;background:var(--bg4);border-radius:var(--rad)"><div style="text-align:center"><div style="font-size:8px;color:var(--t4)">Temkinli</div><div id="_sB2" style="font-family:\'JetBrains Mono\',monospace;font-size:14px;color:var(--red)">'+Math.round(r.valuation.bear_case)+'</div></div><div style="text-align:center"><div style="font-size:8px;color:var(--t4)">Baz</div><div id="_sM2" style="font-family:\'JetBrains Mono\',monospace;font-size:18px;font-weight:700;color:var(--grn)">'+Math.round(r.valuation.base_case)+'</div></div><div style="text-align:center"><div style="font-size:8px;color:var(--t4)">\u0130yimser</div><div id="_sU2" style="font-family:\'JetBrains Mono\',monospace;font-size:14px;color:var(--blu)">'+Math.round(r.valuation.bull_case)+'</div></div></div><div id="_vG2" style="display:none;margin-top:8px;padding-top:8px;border-top:1px solid var(--bdr);font-size:9px;color:var(--t4);line-height:1.5">Bu de\u011ferler sistemin varsay\u0131mlar\u0131 de\u011fildir.</div></div>';
_vm.appendChild(_sp2);
var _t2=document.getElementById('_vT2'),_p2=document.getElementById('_vP2');
if(_t2)_t2.onclick=function(){_p2.style.display=_p2.style.display==='none'?'block':'none';};
var _gs2=document.getElementById('_gS2'),_ds2=document.getElementById('_dS2');
function _upd2(){var g=parseInt(_gs2.value)/100,d=parseInt(_ds2.value)/100;document.getElementById('_gV2').textContent=Math.round(g*100)+'%';document.getElementById('_dV2').textContent=Math.round(d*100)+'%';var b=_dcf2(g,d);document.getElementById('_sB2').textContent=Math.round(b*0.6);document.getElementById('_sM2').textContent=Math.round(b);document.getElementById('_sU2').textContent=Math.round(b*1.4);var ic=Math.abs(g-_oG/100)>0.005||Math.abs(d-_oD/100)>0.005;document.getElementById('_vC2').style.display=ic?'block':'none';document.getElementById('_vR2').style.display=ic?'inline':'none';document.getElementById('_vG2').style.display=ic?'block':'none';}
if(_gs2){_gs2.oninput=_upd2;_ds2.oninput=_upd2;}
var _r2=document.getElementById('_vR2');if(_r2)_r2.onclick=function(){_gs2.value=_oG;_ds2.value=_oD;_upd2();};
}
}catch(e){}

}
function renderTech(t){
  // Map raw data to human-readable plain Turkish
  const sc=t.tech_score||50;
  const scCol=sc>=70?'var(--grn)':sc>=50?'var(--ylw)':'var(--red)';
  const scLabel=sc>=70?'Teknik olarak güçlü görünüyor':'Teknik olarak karışık sinyaller var';
  
  // Trend direction in plain Turkish
  const rsi=t.rsi;
  let trendDir='Yatay →';let trendColor='var(--ylw)';let trendExpl='Belirgin bir yön yok, bekle ve izle.';
  if(t.ma50&&t.ma200){
    if(t.ma50>t.ma200*1.03){trendDir='Yukarı Trend ↑';trendColor='var(--grn)';trendExpl='50 günlük ortalama, 200 günlük ortalamanın üzerinde — genel eğilim yukarı yönlü.';}
    else if(t.ma50<t.ma200*0.97){trendDir='Aşağı Trend ↓';trendColor='var(--red)';trendExpl='50 günlük ortalama, 200 günlük ortalamanın altında — genel eğilim aşağı yönlü.';}
    else{trendDir='Yatay →';trendColor='var(--ylw)';trendExpl='50 ve 200 günlük ortalamalar birbirine yakın — net bir yön henüz oluşmamış.';}
  }
  
  // RSI interpretation — no raw number, just meaning
  let rsiText='';let rsiColor='var(--t2)';
  if(rsi!=null){
    if(rsi>=70){rsiText='Aşırı alım bölgesinde — kısa vadede düzeltme gelebilir.';rsiColor='var(--red)';}
    else if(rsi>=60){rsiText='Güçlü momentum — trend devam ediyor olabilir.';rsiColor='var(--grn)';}
    else if(rsi<=30){rsiText='Aşırı satım bölgesinde — teknik olarak toparlanma ihtimali arttı.';rsiColor='var(--grn)';}
    else if(rsi<=40){rsiText='Zayıf momentum — satış baskısı devam ediyor.';rsiColor='var(--red)';}
    else{rsiText='Nötr momentum — net bir yön sinyali yok.';rsiColor='var(--ylw)';}
  }
  
  // Volume interpretation
  const volR=t.vol_ratio;
  let volText='';let volColor='var(--t2)';
  if(volR!=null){
    if(volR>=2){volText=`Hacim normalin ${volR.toFixed(1)} katı — çok fazla ilgi var, dikkatli ol (pompa riski olabilir).`;volColor='var(--ylw)';}
    else if(volR>=1.3){volText=`Hacim ortalamanın üzerinde (${volR.toFixed(1)}x) — daha çok insan bu hisseyle ilgileniyor.`;volColor='var(--grn)';}
    else if(volR<0.7){volText=`Hacim düşük (${volR.toFixed(1)}x) — alım/satımda fiyat kayması yaşanabilir.`;volColor='var(--red)';}
    else{volText=`Hacim normal seviyede (${volR.toFixed(1)}x).`;volColor='var(--t2)';}
  }
  
  // Support / resistance (use MA as proxy)
  const price=t.price||t.close;
  let suppText='',resiText='';
  if(t.ma50&&price){
    if(price>t.ma50){suppText=`${t.ma50.toFixed(0)} TL civarı (50 günlük ortalama — bu seviyenin altına düşmesi zorlaşabilir)`;}
    else{resiText=`${t.ma50.toFixed(0)} TL civarı (50 günlük ortalama — bu seviyeyi aşması zorlaşabilir)`;}
  }
  
  // Week change
  const w1=t.w1_pct;
  const w1Text=w1!=null?`Son 1 haftada ${w1>=0?'+':''}${w1.toFixed(1)}% ${w1>=0?'yükseldi':'düştü'}`:'';
  
  // Build human-voice technical card
  let h=`<div style="margin-bottom:14px;padding:14px;background:var(--bg3);border:1px solid var(--bdr2);border-radius:var(--rad2)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Teknik Görünüm <span title="Fiyat grafiğine bakarak hissenin kısa vadeli yönünü tahmin etmeye çalışır" style="cursor:help;color:var(--t4);font-size:9px;border:1px solid var(--bdr);border-radius:50%;width:12px;height:12px;display:inline-flex;align-items:center;justify-content:center">?</span></div>
        <div style="font-size:13px;color:var(--t3)">${scLabel}</div>
      </div>
      <div style="text-align:center">${ring(sc,'TEKNİK',80)}</div>
    </div>
    
    <!-- Trend direction pill -->
    <div style="display:flex;align-items:center;gap:10px;padding:10px;background:${trendColor}10;border:1px solid ${trendColor}25;border-radius:var(--rad);margin-bottom:10px">
      <span style="font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;color:${trendColor};white-space:nowrap">${trendDir}</span>
      <span style="font-size:12px;color:var(--t2);line-height:1.5">${trendExpl}</span>
    </div>
    
    <!-- Key facts in plain language -->
    <div style="display:flex;flex-direction:column;gap:8px">
      ${rsiText?`<div style="display:flex;gap:8px;align-items:flex-start;padding:8px;background:var(--bg2);border-radius:var(--rad)"><span style="font-size:16px;flex-shrink:0">📊</span><div><div style="font-size:11px;color:var(--t4);margin-bottom:2px">Momentum Durumu</div><div style="font-size:12px;color:${rsiColor};line-height:1.5">${rsiText}</div></div></div>`:''}
      ${volText?`<div style="display:flex;gap:8px;align-items:flex-start;padding:8px;background:var(--bg2);border-radius:var(--rad)"><span style="font-size:16px;flex-shrink:0">📦</span><div><div style="font-size:11px;color:var(--t4);margin-bottom:2px">Hacim Analizi</div><div style="font-size:12px;color:${volColor};line-height:1.5">${volText}</div></div></div>`:''}
      ${suppText?`<div style="display:flex;gap:8px;align-items:flex-start;padding:8px;background:var(--bg2);border-radius:var(--rad)"><span style="font-size:16px;flex-shrink:0">🛡️</span><div><div style="font-size:11px;color:var(--t4);margin-bottom:2px">Destek Seviyesi</div><div style="font-size:12px;color:var(--t2);line-height:1.5">${suppText}</div></div></div>`:''}
      ${resiText?`<div style="display:flex;gap:8px;align-items:flex-start;padding:8px;background:var(--bg2);border-radius:var(--rad)"><span style="font-size:16px;flex-shrink:0">🧱</span><div><div style="font-size:11px;color:var(--t4);margin-bottom:2px">Direnç Seviyesi</div><div style="font-size:12px;color:var(--t2);line-height:1.5">${resiText}</div></div></div>`:''}
      ${w1Text?`<div style="display:flex;gap:8px;align-items:flex-start;padding:8px;background:var(--bg2);border-radius:var(--rad)"><span style="font-size:16px;flex-shrink:0">📅</span><div><div style="font-size:11px;color:var(--t4);margin-bottom:2px">Kısa Vade Hareketi</div><div style="font-size:12px;color:${w1>=0?'var(--grn)':'var(--red)'};line-height:1.5">${w1Text}</div></div></div>`:''}
    </div>
    
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--bdr);font-size:10px;color:var(--t4)">
      Teknik analiz geçmiş fiyat hareketlerine dayanır — geleceği garanti etmez. RSI/MACD/Bollinger değerlerini anlamak için yardıma ihtiyacın varsa Q asistanına sorabilirsin.
    </div>
  </div>`;
  
  // Compact advanced metrics (collapsed, for power users)
  const comps=(t.components||[]);
  if(comps.length){
    h+=`<details style="margin-bottom:8px"><summary style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3);cursor:pointer;padding:6px 0;list-style:none">▸ Gelişmiş teknik bileşenler (${comps.length} sinyal)</summary><div style="margin-top:8px">`;
    h+=comps.map(c=>{const col=c.score>=65?'var(--grn)':c.score>=40?'var(--ylw)':'var(--red)';return`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--bdr)"><span style="font-size:10px;color:var(--t2)">${esc(c.name)}</span><span style="font-size:10px;color:${col}">${esc(c.desc)}</span></div>`;}).join('');
    h+=`</div></details>`;
  }
  
  return h;
}
function toggleWL(t){if(S.wl.includes(t))wlRm(t);else wlAdd(t);loadTicker(t);}
function dtab(btn,id){btn.closest('.dp-tabs').querySelectorAll('.dp-tab').forEach(t=>t.classList.remove('on'));btn.classList.add('on');btn.closest('.dp-body').querySelectorAll('.ds').forEach(s=>s.classList.remove('on'));$('ds-'+id).classList.add('on');}
function openD(){$('ov').classList.add('open');document.body.style.overflow='hidden';}
function closeD(){$('ov').classList.remove('open');document.body.style.overflow='';if(S.page==='home')renderHome();}

// ===== TOOLTIP HELPERS =====
function _decCol(d){return (d==='AL'||d==='Çok Başarılı'||d==='Başarılı')?'#00e676':(d==='İZLE'||d==='Orta')?'#ffb300':d==='BEKLE'?'#78909c':'#ef5350';}
function _showTip(e,html){const t=$('bbTip');if(!t)return;t.innerHTML=html;t.style.display='block';const bx=t.getBoundingClientRect();let tx=e.clientX+14,ty=e.clientY-10;if(tx+bx.width>window.innerWidth-10)tx=e.clientX-bx.width-14;if(ty+bx.height>window.innerHeight-10)ty=window.innerHeight-bx.height-10;if(ty<10)ty=10;t.style.left=tx+'px';t.style.top=ty+'px';}
function _moveTip(e){const t=$('bbTip');if(!t||t.style.display==='none')return;const bx=t.getBoundingClientRect();let tx=e.clientX+14,ty=e.clientY-10;if(tx+bx.width>window.innerWidth-10)tx=e.clientX-bx.width-14;if(ty+bx.height>window.innerHeight-10)ty=window.innerHeight-bx.height-10;if(ty<10)ty=10;t.style.left=tx+'px';t.style.top=ty+'px';}
function _hideTip(){const t=$('bbTip');if(t)t.style.display='none';}

// ===== HEATMAP — Finviz-grade treemap + glassmorphism tooltip =====
// ================================================================
// PHASE 5 — DETAIL PANEL ENRICHMENT HELPERS
// ----------------------------------------------------------------
// These helpers render the new sections required by the Phase 5
// brief (Türkiye 4 filtre, AI multi-model showdown, signal
// explanation cards, score-explain modal) WITHOUT touching the
// existing renderDetail monolith. They're called as opt-in mounts
// from the rendered HTML via custom data-* attributes; the legacy
// flow stays byte-identical (Rule 6).
// ================================================================

// 5.2.1 — Türkiye 4 Filter section
function renderTurkeyFilterSection(turkey) {
  if (!turkey || !turkey.filters) return '';
  const filterOrder = ['fx_shield', 'rate_resistance', 'pricing_power', 'tms29'];
  const iconMap = {
    fx_shield: '💱',
    rate_resistance: '📈',
    pricing_power: '🏷️',
    tms29: '📊',
  };
  let rows = '';
  filterOrder.forEach(key => {
    const f = turkey.filters[key];
    if (!f) return;
    const m = f.multiplier || 1.0;
    // Map [0.70, 1.15] → [0%, 100%] for the bar; centred at 1.0
    const pct = Math.max(0, Math.min(100, ((m - 0.70) / 0.45) * 100));
    const dir = m > 1.02 ? 'up' : m < 0.98 ? 'down' : 'flat';
    const multStr = ((m - 1.0) * 100).toFixed(0);
    const multSigned = m >= 1.0 ? '+' + multStr + '%' : multStr + '%';
    rows += `<div class="tr-filter-row" data-filter="${esc(key)}">
      <div class="tr-filter-icon">${iconMap[key] || '🇹🇷'}</div>
      <div class="tr-filter-body">
        <div class="tr-filter-name">${esc(f.name)}<span class="tr-filter-grade ${esc(f.grade || '?')}">${esc(f.grade || '?')}</span></div>
        <div class="tr-filter-bar-wrap"><div class="tr-filter-bar ${dir}" style="width:${pct.toFixed(0)}%"></div><div class="tr-filter-mid"></div></div>
        <div class="tr-filter-explanation">${esc(f.explanation || '')}</div>
      </div>
      <div class="tr-filter-mult ${dir}">${multSigned}</div>
    </div>`;
  });
  return `<div class="tr-filter-section" data-testid="turkey-filter-section">
    <div class="tr-filter-title">🇹🇷 Türkiye Filtresi
      <button class="info" onclick="window._showTurkeyHelp&&window._showTurkeyHelp()" title="Bu nedir?" aria-label="Türkiye filtresi nedir?">?</button>
    </div>
    ${rows}
    ${turkey.summary ? `<div class="tr-filter-summary">${esc(turkey.summary)}</div>` : ''}
  </div>`;
}

window._showTurkeyHelp = function () {
  const html = `<div style="padding:18px;font-size:13px;color:var(--t2);line-height:1.7">
    <h4 style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--acc);margin-bottom:8px;text-transform:uppercase">🇹🇷 Türkiye Filtresi Nedir?</h4>
    <p>BIST hisselerini değerlendirirken, ABD endekslerinde olmayan 4 makro gerçeği hesaba katar:</p>
    <ul style="margin:10px 0 10px 20px;line-height:1.8">
      <li><b>💱 Döviz Kalkanı:</b> Şirketin ihracat geliri var mı? Kur şokunda korunabilir mi?</li>
      <li><b>📈 Faiz Direnci:</b> Borç yapısı yüksek faize ne kadar dayanıklı?</li>
      <li><b>🏷️ Fiyat Geçişkenliği:</b> Maliyet artışını fiyata yansıtabilir mi (pricing power)?</li>
      <li><b>📊 TMS 29:</b> Enflasyon muhasebesi (TFRS) etkisi — kar düzeltilmesi gerek mi?</li>
    </ul>
    <p>Her filtre 0.70-1.15 arası bir çarpan üretir. 4'ün geometrik ortalaması nihai skoru ayarlar.</p>
  </div>`;
  // Reuse existing modal helper if present, otherwise alert
  if (typeof window.openModal === 'function') {
    window.openModal('🇹🇷 Türkiye Filtresi', html);
  } else {
    const m = document.createElement('div');
    m.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;padding:20px';
    m.innerHTML = `<div style="background:var(--bg1);border:1px solid var(--bdr2);border-radius:var(--rad2);max-width:500px;width:100%;max-height:80vh;overflow-y:auto" onclick="event.stopPropagation()">${html}<div style="padding:0 18px 18px"><button onclick="this.closest('div[style*=fixed]').remove()" class="btn btn-orn" style="width:100%">Kapat</button></div></div>`;
    m.onclick = () => m.remove();
    document.body.appendChild(m);
  }
};

// 5.2.2 — Signal explanation card
function renderSignalExplainCard(sig) {
  const wf = sig.walkforward || {};
  const badge = sig.reliability_badge || {};
  const sharpe = wf.sharpe != null ? wf.sharpe.toFixed(2) : '—';
  const ret60d = wf.mean_return_60d != null ? (wf.mean_return_60d * 100).toFixed(1) + '%' : '—';
  return `<div class="sig-explain-card" data-testid="signal-explain-card">
    <div class="sig-explain-head">
      <span class="sig-explain-name">${esc(sig.signal || '?')}</span>
      <span class="sig-explain-rel" style="color:${badge.code === 'walkforward_validated' ? 'var(--grn)' : badge.code === 'regime_dependent' ? 'var(--ylw)' : 'var(--t4)'}">${esc(badge.icon || '?')} ${esc(badge.label || '')}</span>
    </div>
    <div class="sig-explain-body">${esc(sig.plain_explanation || '')}</div>
    <div class="sig-explain-meta">
      <span class="tag" title="Walk-forward Sharpe (2018-2024)">Sharpe: ${sharpe}</span>
      <span class="tag" title="60-günlük ortalama getiri">60g: ${ret60d}</span>
      <span class="tag">⭐ ${sig.stars || 1}</span>
    </div>
    <div class="sig-explain-action">${esc(sig.action_label || 'Sadece izle')}</div>
  </div>`;
}

async function loadSignalExplanations(symbol) {
  try {
    const d = await api(`/api/cross/${encodeURIComponent(symbol)}/explain`);
    if (!d || !d.signals || !d.signals.length) {
      return '<p style="color:var(--t3);font-size:12px;padding:12px">Bu hisse için aktif sinyal yok.</p>';
    }
    return `<div data-testid="signal-explain-list">${d.signals.map(renderSignalExplainCard).join('')}</div>`;
  } catch (e) {
    return '<p style="color:var(--red);font-size:12px;padding:12px">Sinyal açıklamaları yüklenemedi.</p>';
  }
}

// 5.2.3 — AI Multi-Model Showdown
// AI Consolidation (2026-05): the multi-model "consensus" is gone —
// the site runs a single model (Claude). This renders Claude's single
// analysis cleanly: no per-model breakdown, no agreement %, no error
// rows for dead providers.
function renderAiConsensus(consensus) {
  if (!consensus) return '';
  const text = consensus.leader_text || consensus.analysis || '';
  if (!text) {
    return '<p style="color:var(--t3);font-size:12px;padding:12px">AI yorum hazırlanamadı.</p>';
  }
  return `<div class="ai-consensus-wrap" data-testid="ai-consensus">
    <div class="ai-consensus-leader">
      <span class="ai-consensus-badge">🤖 Claude AI Analizi</span>
      <div class="ai-consensus-text">${esc(text).replace(/\n/g,'<br>')}</div>
    </div>
  </div>`;
}

async function loadAiConsensus(symbol) {
  try {
    const d = await api(`/api/ai/${encodeURIComponent(symbol)}/consensus`);
    const c = d && (d.consensus || d);
    if (!c) {
      return '<p style="color:var(--t3);font-size:12px;padding:12px">AI yorum hazırlanamadı.</p>';
    }
    return renderAiConsensus(c);
  } catch (e) {
    return '<p style="color:var(--red);font-size:12px;padding:12px">AI analizi yüklenemedi.</p>';
  }
}

// 5.2.4 — Score Explain Modal
window._showScoreHelp = function (r) {
  if (!r) return;
  const breakdown = r.scores || {};
  const dim = (k, label) => {
    const v = breakdown[k];
    if (v == null) return '';
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--bdr)"><span>${esc(label)}</span><span class="num">${typeof v === 'number' ? v.toFixed(0) : esc(String(v))}</span></div>`;
  };
  const html = `<div class="score-modal-bd">
    <h4>Bu skor nereden geliyor?</h4>
    <p>Toplam skor (${r.overall != null ? r.overall.toFixed(0) : '?'}/100), aşağıdaki alt boyutların ağırlıklı ortalamasıdır.</p>
    <h4>Boyut Kırılımı</h4>
    ${dim('value', '💰 Değer')}
    ${dim('quality', '⭐ Kalite')}
    ${dim('balance', '🏦 Bilanço')}
    ${dim('momentum', '⚡ Momentum')}
    ${dim('risk', '⚠️ Risk')}
    <h4>Türkiye Filtresi Etkisi</h4>
    ${r.turkey_realities ? `<div>Composite çarpan: <span class="num">${(r.turkey_realities.composite_multiplier || 1.0).toFixed(2)}x</span> · grade: <span class="num">${esc(r.turkey_realities.composite_grade || '?')}</span></div>` : '<div>Bu hisse için Türkiye filtresi mevcut değil</div>'}
    ${r.fa_score != null ? `<h4>Walk-Forward Onay</h4><p>FA skoru kalibre edilmiş ve 7-fold walk-forward'da onaylanmıştır (Phase 4.3-4.7).</p>` : ''}
    <p style="margin-top:14px;color:var(--t4);font-size:11px">Bu bilgiler yatırım tavsiyesi değildir.</p>
  </div>`;
  const m = document.createElement('div');
  m.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;padding:20px';
  m.innerHTML = `<div data-testid="score-modal" style="background:var(--bg1);border:1px solid var(--bdr2);border-radius:var(--rad2);max-width:500px;width:100%;max-height:80vh;overflow-y:auto" onclick="event.stopPropagation()">${html}<div style="padding:0 18px 18px"><button onclick="this.closest('div[style*=fixed]').remove()" class="btn btn-orn" style="width:100%">Kapat</button></div></div>`;
  m.onclick = () => m.remove();
  document.body.appendChild(m);
};

// HOTFIX 1 + Phase 5.1.1: /api/heatmap returns <200ms with
// computing=true when backend cold-cache is still warming up.
// Phase 5 adds: shimmer skeleton (instant render), 5s polling
// with 30s timeout (was 30s/5min — too slow for a power-user),
// stale-while-error pattern (keeps last good heatmap on 5xx),
// and AbortController cleanup on page change.
let _heatmapRetryTimer = null;
let _heatmapAbort = null;
let _heatmapPollDeadline = 0;
const _HEATMAP_POLL_INTERVAL = 5000;   // 5s — Phase 5
const _HEATMAP_POLL_TIMEOUT  = 30000;  // 30s wall-clock cap

// Shimmer skeleton rendered immediately on cache miss / computing=true.
function _heatmapSkeletonHtml(){
  let cells = '';
  // 24 placeholder tiles with CSS shimmer animation
  for (let i = 0; i < 24; i++) {
    const w = 30 + Math.random() * 90;
    const h = 30 + Math.random() * 60;
    cells += `<div class="heat-skel-cell" style="width:${w.toFixed(0)}px;height:${h.toFixed(0)}px"></div>`;
  }
  return `<div class="heat-skel-wrap" data-testid="heatmap-skeleton">
    <div class="heat-skel-grid">${cells}</div>
    <p class="heat-skel-caption">Tarama sonrası gelecek — <span style="opacity:0.6">(arka planda hesaplanıyor)</span></p>
  </div>`;
}

async function loadHeatmap(){
  if (_heatmapRetryTimer) { clearTimeout(_heatmapRetryTimer); _heatmapRetryTimer = null; }
  // Cancel any in-flight request from a previous loadHeatmap call.
  if (_heatmapAbort) { try { _heatmapAbort.abort(); } catch(_){} }
  _heatmapAbort = new AbortController();

  // Initialise wall-clock deadline on first call in a polling chain.
  if (!_heatmapPollDeadline) _heatmapPollDeadline = Date.now() + _HEATMAP_POLL_TIMEOUT;

  // If we have NO heatmap on screen yet, render skeleton immediately.
  if (!S.heatmapHtml || S.heatmapHtml.indexOf('heat-skel') >= 0) {
    S.heatmapHtml = _heatmapSkeletonHtml();
    if (S.page === 'home') renderHome();
  }

  try{
    const d=await api('/api/heatmap');
    if(d.computing===true || !d.sectors || !d.sectors.length){
      // Keep the skeleton on screen, schedule another poll if within deadline.
      if (Date.now() < _heatmapPollDeadline) {
        _heatmapRetryTimer = setTimeout(() => loadHeatmap(), _HEATMAP_POLL_INTERVAL);
      } else {
        S.heatmapHtml = '<p data-testid="heatmap-timeout" style="color:var(--t4);font-size:12px">Veri henüz hazır değil, sayfa yenile.</p>';
        _heatmapPollDeadline = 0;
        if (S.page==='home') renderHome();
      }
      return;
    }
    _heatmapPollDeadline = 0;  // got real data, reset deadline
    S._heatmapRetries = 0;
    // 7-grade renk skalası: koyu kırmızı → siyah → neon yeşil
    function heatCol(chg){
      if(chg>=3)return'#00e676';if(chg>=2)return'#00c853';if(chg>=1)return'#2e7d32';if(chg>=0.3)return'#1b5e20';
      if(chg>=-0.3)return'#37474f';
      if(chg>=-1)return'#b71c1c';if(chg>=-2)return'#991b1b';if(chg>=-3)return'#7f1d1d';return'#4a0000';
    }
    // Squarified treemap layout
    function sqr(items,x,y,w,h){
      if(!items.length)return;
      if(items.length===1){items[0]._r={x,y,w,h};return;}
      const total=items.reduce((s,i)=>s+(i._v||1),0);
      if(total<=0)return;
      let row=[items[0]],rowSum=items[0]._v||1,bestRatio=Infinity;
      for(let i=1;i<items.length;i++){
        const testSum=rowSum+(items[i]._v||1);
        const frac=testSum/total;
        const isH=w>=h;const stripLen=isH?w*frac:h*frac;const stripW=isH?h:w;
        let worst=0;
        for(const ri of[...row,items[i]]){
          const iLen=stripW*((ri._v||1)/testSum);
          const ratio=Math.max(stripLen/iLen,iLen/stripLen);
          worst=Math.max(worst,ratio);
        }
        if(worst>bestRatio&&row.length>0)break;
        bestRatio=worst;row.push(items[i]);rowSum=testSum;
      }
      const frac=rowSum/total;const isH=w>=h;
      if(isH){
        const sw=w*frac;let cy=y;
        for(const ri of row){const ih=h*((ri._v||1)/rowSum);ri._r={x,y:cy,w:sw,h:ih};cy+=ih;}
        sqr(items.slice(row.length),x+sw,y,w-sw,h);
      }else{
        const sh=h*frac;let cx=x;
        for(const ri of row){const iw=w*((ri._v||1)/rowSum);ri._r={x:cx,y,w:iw,h:sh};cx+=iw;}
        sqr(items.slice(row.length),x,y+sh,w,h-sh);
      }
    }
    // Scan verisinden zenginleştirme (Piotroski, Altman, AI karar)
    const scanMap={};
    if(S.scan&&S.scan.items){S.scan.items.forEach(it=>{scanMap[it.ticker]=it;});}
    // Flatten, sort, layout
    const allStocks=[];
    d.sectors.forEach(sec=>{sec.stocks.forEach(st=>{allStocks.push({...st,sector:sec.sector});});});
    allStocks.sort((a,b)=>(b.market_cap||0)-(a.market_cap||0));
    allStocks.forEach(s=>{s._v=Math.max(s.market_cap||1,1);});
    const W=750,H=420;
    sqr(allStocks,0,0,W,H);
    // Store for tooltip lookup
    window._heatStocks=allStocks;
    window._heatScanMap=scanMap;
    // Build HTML
    let h2=`<div style="position:relative;width:100%;max-width:${W}px;height:${H}px;overflow:hidden;border-radius:var(--rad);border:1px solid var(--bdr);background:#0c0f14">`;
    allStocks.forEach((s,idx)=>{
      if(!s._r)return;
      const r=s._r;const chg=s.change_pct||0;
      const bg=heatCol(chg);const fs=r.w>70&&r.h>35?12:r.w>50&&r.h>28?10:r.w>35&&r.h>20?8:0;
      const showPct=r.w>32&&r.h>22;
      h2+=`<div onclick="loadTicker('${esc(s.ticker)}')" onmouseenter="_heatTip(event,${idx})" onmousemove="_moveTip(event)" onmouseleave="_hideTip()" style="position:absolute;left:${r.x.toFixed(1)}px;top:${r.y.toFixed(1)}px;width:${r.w.toFixed(1)}px;height:${r.h.toFixed(1)}px;background:${bg};display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border:0.5px solid rgba(0,0,0,.35);overflow:hidden;box-sizing:border-box;transition:filter .12s" onmousedown="this.style.filter='brightness(1.3)'" onmouseup="this.style.filter=''">`;
      if(fs>0)h2+=`<div style="font-family:'JetBrains Mono',monospace;font-size:${fs}px;font-weight:700;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.6);letter-spacing:.3px;line-height:1.1">${esc(s.ticker)}</div>`;
      if(showPct)h2+=`<div style="font-family:'JetBrains Mono',monospace;font-size:${Math.max(fs-1,8)}px;font-weight:600;color:${chg>=0?'#c8e6c9':'#ffcdd2'};text-shadow:0 1px 2px rgba(0,0,0,.5)">${chg>=0?'+':''}${chg.toFixed(1)}%</div>`;
      h2+=`</div>`;
    });
    h2+=`</div>`;
    // Renk skalası legend
    h2+=`<div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px"><div style="display:flex;gap:3px;align-items:center;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t4)"><span style="display:inline-block;width:10px;height:10px;background:#4a0000;border-radius:2px"></span><span>-3%</span>`;
    ['#7f1d1d','#991b1b','#b71c1c','#37474f','#1b5e20','#2e7d32','#00c853','#00e676'].forEach(c=>{h2+=`<span style="display:inline-block;width:10px;height:10px;background:${c};border-radius:2px"></span>`;});
    h2+=`<span>+3%</span></div>`;
    // Sektör legend
    h2+=`<div style="display:flex;gap:4px;flex-wrap:wrap;font-family:'JetBrains Mono',monospace;font-size:9px">`;
    d.sectors.forEach(sec=>{
      const col=sec.avg_change>=0?'var(--grn)':'var(--red)';
      h2+=`<span style="padding:2px 6px;background:var(--bg3);border:1px solid var(--bdr);border-radius:3px"><span style="color:var(--t2)">${esc(sec.sector.slice(0,12))}</span> <span style="color:${col};font-weight:700">${sec.avg_change>=0?'+':''}${sec.avg_change.toFixed(1)}%</span></span>`;
    });
    h2+=`</div></div>`;
    S.heatmapHtml=h2;
  }catch(e){
    if (e.name === 'AbortError') {
      // user navigated away — don't render anything, don't retry
      return;
    }
    if (e.message === 'timeout') {
      // Soft fail: keep skeleton, retry in 5s if within deadline.
      if (Date.now() < _heatmapPollDeadline) {
        _heatmapRetryTimer = setTimeout(() => loadHeatmap(), _HEATMAP_POLL_INTERVAL);
      } else {
        S.heatmapHtml='<p data-testid="heatmap-timeout" style="color:var(--t4);font-size:12px">Veri henüz hazır değil, sayfa yenile.</p>';
        _heatmapPollDeadline = 0;
      }
    } else {
      // Stale-while-error: if we already have a rendered heatmap on screen,
      // keep it visible with a small banner. Only show error state if there
      // is literally nothing to display.
      const hasGoodHeatmap = S.heatmapHtml && S.heatmapHtml.indexOf('heat-skel') < 0
                             && S.heatmapHtml.indexOf('heatmap-error') < 0
                             && S.heatmapHtml.indexOf('heatmap-timeout') < 0
                             && S.heatmapHtml.length > 200;  // crude "real content" check
      if (hasGoodHeatmap) {
        S.heatmapHtml = `<div data-testid="heatmap-stale-banner" style="padding:8px;border:1px dashed var(--red);border-radius:var(--rad);margin-bottom:8px;color:var(--red);font-size:11px;background:var(--redd)">Bağlantı sorunu — son veriler gösteriliyor</div>` + S.heatmapHtml;
      } else {
        S.heatmapHtml='<p data-testid="heatmap-error" style="color:var(--red);font-size:12px">Heatmap yüklenemedi — bağlantı sorunu</p>';
      }
      _heatmapPollDeadline = 0;
    }
  }
  if(S.page==='home')renderHome();
}

// Public hook so navigation can cancel pending heatmap requests.
window.cancelHeatmapPolling = function(){
  if (_heatmapRetryTimer) { clearTimeout(_heatmapRetryTimer); _heatmapRetryTimer = null; }
  if (_heatmapAbort) { try { _heatmapAbort.abort(); } catch(_){} _heatmapAbort = null; }
  _heatmapPollDeadline = 0;
};
// Glassmorphism tooltip builder — scan data enriched
function _heatTip(e,idx){
  const s=window._heatStocks?.[idx];if(!s)return;
  const si=window._heatScanMap?.[s.ticker];
  const dec=si?.decision||'—';const decBg=_decCol(dec);
  const pf=si?.legendary?.piotroski||'—';
  const az=si?.legendary?.altman||'—';
  const bn=si?.legendary?.beneish||'—';
  const deger=si?.deger||si?.overall||'—';
  const ivme=si?.ivme||'—';
  const sty=si?.style||'—';
  const chg=s.change_pct||0;
  let html=`<div class="tt-head"><span class="tt-tick">${esc(s.ticker)}</span><span class="tt-dec" style="background:${decBg}22;color:${decBg}">${dec}</span></div>`;
  html+=`<div class="tt-sub">${esc(s.sector)} · ${sty}</div>`;
  html+=`<div class="tt-grid">`;
  html+=`<span class="tt-lbl">Fiyat</span><span class="tt-val" style="color:#fff">${s.price?('₺'+s.price.toLocaleString('tr-TR')):'—'}</span>`;
  html+=`<span class="tt-lbl">Gün</span><span class="tt-val" style="color:${chg>=0?'#69f0ae':'#ef9a9a'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span>`;
  html+=`<span class="tt-lbl">Değer</span><span class="tt-val" style="color:#22d3ee">${deger}</span>`;
  html+=`<span class="tt-lbl">İvme</span><span class="tt-val" style="color:#a78bfa">${ivme}</span>`;
  html+=`<span class="tt-lbl">Piotroski</span><span class="tt-val">${pf}</span>`;
  html+=`<span class="tt-lbl">Altman Z</span><span class="tt-val">${az}</span>`;
  html+=`<span class="tt-lbl">Beneish</span><span class="tt-val">${bn}</span>`;
  html+=`</div>`;
  _showTip(e,html);
}

// ===== ALPHA QUADRANT — 4-bölgeli scatter/bubble chart =====
function renderAlphaQuadrant(){
  if(!S.scan||!S.scan.items||!S.scan.items.length){S.alphaHtml='<div style="color:var(--t4);font-size:12px;text-align:center;padding:40px 0">Scan sonrası Alpha Quadrant aktif olacak</div>';return;}
  const items=S.scan.items;
  const maxMc=Math.max(...items.map(i=>i.market_cap||0),1);
  // Quadrant label hesapla
  const alphaCount=items.filter(i=>(i.deger||i.overall||0)>=55&&(i.ivme||0)>=55).length;
  const hypeCount=items.filter(i=>(i.deger||i.overall||0)<55&&(i.ivme||0)>=55).length;
  const valueCount=items.filter(i=>(i.deger||i.overall||0)>=55&&(i.ivme||0)<55).length;
  const avoidCount=items.filter(i=>(i.deger||i.overall||0)<55&&(i.ivme||0)<55).length;
  // SVG ile elle çizim — canvas yerine (inline HTML olması gerekiyor)
  const W=720,H=440,PAD=42,PB=32,PL=42;
  const iW=W-PL-12,iH=H-PAD-PB;
  let svg=`<div style="position:relative;width:100%;max-width:${W}px;overflow:hidden">`;
  svg+=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;font-family:'JetBrains Mono',monospace">`;
  // Quadrant fills
  svg+=`<rect x="${PL}" y="${PAD}" width="${iW/2}" height="${iH/2}" fill="rgba(156,39,176,.06)" rx="0"/>`;
  svg+=`<rect x="${PL+iW/2}" y="${PAD}" width="${iW/2}" height="${iH/2}" fill="rgba(0,230,118,.06)" rx="0"/>`;
  svg+=`<rect x="${PL}" y="${PAD+iH/2}" width="${iW/2}" height="${iH/2}" fill="rgba(239,83,80,.06)" rx="0"/>`;
  svg+=`<rect x="${PL+iW/2}" y="${PAD+iH/2}" width="${iW/2}" height="${iH/2}" fill="rgba(255,179,0,.06)" rx="0"/>`;
  // Quadrant labels
  svg+=`<text x="${PL+iW*0.75}" y="${PAD+16}" fill="rgba(0,230,118,.45)" font-size="10" font-weight="600" text-anchor="middle">ALPHA ZONE (${alphaCount})</text>`;
  svg+=`<text x="${PL+iW*0.25}" y="${PAD+16}" fill="rgba(156,39,176,.4)" font-size="10" font-weight="600" text-anchor="middle">HYPE RİSKİ (${hypeCount})</text>`;
  svg+=`<text x="${PL+iW*0.75}" y="${PAD+iH-6}" fill="rgba(255,179,0,.4)" font-size="10" font-weight="600" text-anchor="middle">DEĞER — İVME YOK (${valueCount})</text>`;
  svg+=`<text x="${PL+iW*0.25}" y="${PAD+iH-6}" fill="rgba(239,83,80,.35)" font-size="10" font-weight="600" text-anchor="middle">ZAYIF (${avoidCount})</text>`;
  // Axes
  svg+=`<line x1="${PL}" y1="${PAD}" x2="${PL}" y2="${PAD+iH}" stroke="rgba(255,255,255,.1)" stroke-width="1"/>`;
  svg+=`<line x1="${PL}" y1="${PAD+iH}" x2="${PL+iW}" y2="${PAD+iH}" stroke="rgba(255,255,255,.1)" stroke-width="1"/>`;
  // Center cross (dashed)
  svg+=`<line x1="${PL+iW/2}" y1="${PAD}" x2="${PL+iW/2}" y2="${PAD+iH}" stroke="rgba(255,255,255,.08)" stroke-width="1" stroke-dasharray="4,4"/>`;
  svg+=`<line x1="${PL}" y1="${PAD+iH/2}" x2="${PL+iW}" y2="${PAD+iH/2}" stroke="rgba(255,255,255,.08)" stroke-width="1" stroke-dasharray="4,4"/>`;
  // Axis labels
  svg+=`<text x="${PL+iW/2}" y="${H-4}" fill="rgba(255,255,255,.35)" font-size="10" text-anchor="middle">DEĞER SKORU →</text>`;
  svg+=`<text transform="rotate(-90,12,${PAD+iH/2})" x="12" y="${PAD+iH/2}" fill="rgba(255,255,255,.35)" font-size="10" text-anchor="middle">İVME SKORU →</text>`;
  // Axis ticks
  for(let v=20;v<=90;v+=10){
    const px=PL+((v-15)/80)*iW;
    svg+=`<text x="${px}" y="${PAD+iH+14}" fill="rgba(255,255,255,.2)" font-size="8" text-anchor="middle">${v}</text>`;
    const py=PAD+iH-((v-15)/80)*iH;
    svg+=`<text x="${PL-6}" y="${py+3}" fill="rgba(255,255,255,.2)" font-size="8" text-anchor="end">${v}</text>`;
  }
  // Bubbles
  items.forEach((it,idx)=>{
    const dg=it.deger||it.overall||50;
    const iv=it.ivme||50;
    const mc=it.market_cap||0;
    const dec=it.decision||'BEKLE';
    const bCol=dec==='AL'?'rgba(0,230,118,.5)':dec==='İZLE'?'rgba(255,179,0,.4)':dec==='BEKLE'?'rgba(120,144,156,.35)':'rgba(239,83,80,.4)';
    const sCol=dec==='AL'?'#00e676':dec==='İZLE'?'#ffb300':dec==='BEKLE'?'#78909c':'#ef5350';
    const r=Math.max(5,Math.min(20,4+Math.sqrt(mc/1e9)*1.1));
    const px=PL+((dg-15)/80)*iW;
    const py=PAD+iH-((iv-15)/80)*iH;
    svg+=`<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="${r.toFixed(1)}" fill="${bCol}" stroke="${sCol}" stroke-width="1.2" style="cursor:pointer" onclick="loadTicker('${esc(it.ticker)}')" onmouseenter="_alphaTip(event,${idx})" onmousemove="_moveTip(event)" onmouseleave="_hideTip()"/>`;
    if(r>=8)svg+=`<text x="${px.toFixed(1)}" y="${(py-r-3).toFixed(1)}" fill="rgba(255,255,255,.7)" font-size="${r>=12?10:8}" font-weight="600" text-anchor="middle" style="pointer-events:none">${esc(it.ticker)}</text>`;
  });
  svg+=`</svg>`;
  // Legend
  svg+=`<div style="margin-top:8px;display:flex;gap:12px;flex-wrap:wrap;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3)">`;
  svg+=`<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#00e676;margin-right:3px"></span>AL (${items.filter(i=>i.decision==='AL').length})</span>`;
  svg+=`<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ffb300;margin-right:3px"></span>İZLE (${items.filter(i=>i.decision==='İZLE').length})</span>`;
  svg+=`<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#78909c;margin-right:3px"></span>BEKLE (${items.filter(i=>i.decision==='BEKLE').length})</span>`;
  svg+=`<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ef5350;margin-right:3px"></span>KAÇIN (${items.filter(i=>i.decision==='KAÇIN').length})</span>`;
  svg+=`<span style="margin-left:auto;color:var(--t4)">Boyut = piyasa değeri</span>`;
  svg+=`</div></div>`;
  S.alphaHtml=svg;
}
// Alpha Quadrant tooltip
function _alphaTip(e,idx){
  const items=S.scan?.items;if(!items||!items[idx])return;
  const it=items[idx];
  const dec=it.decision||'—';const decLabel=vLabel(dec);const decBg=_decCol(dec);
  const pf=it.legendary?.piotroski||'—';
  const az=it.legendary?.altman||'—';
  let html=`<div class="tt-head"><span class="tt-tick">${esc(it.ticker)}</span><span class="tt-dec" style="background:${decBg}22;color:${decBg}">${dec}</span></div>`;
  html+=`<div class="tt-sub">${esc(it.name||'')} · ${esc(it.style||'')}</div>`;
  html+=`<div class="tt-grid">`;
  html+=`<span class="tt-lbl">Değer</span><span class="tt-val" style="color:#22d3ee">${(it.deger||it.overall||0).toFixed(0)}</span>`;
  html+=`<span class="tt-lbl">İvme</span><span class="tt-val" style="color:#a78bfa">${(it.ivme||0).toFixed(0)}</span>`;
  html+=`<span class="tt-lbl">Overall</span><span class="tt-val" style="color:#fff">${(it.overall||0).toFixed(0)}</span>`;
  html+=`<span class="tt-lbl">Piotroski</span><span class="tt-val">${pf}</span>`;
  html+=`<span class="tt-lbl">Altman Z</span><span class="tt-val">${az}</span>`;
  html+=`<span class="tt-lbl">Temel</span><span class="tt-val" style="color:var(--ylw)">${it.entry_label||'—'}</span>`;
  html+=`<span class="tt-lbl">Kalite</span><span class="tt-val">${it.quality_tag||'—'}</span>`;
  html+=`<span class="tt-lbl">Fiyat</span><span class="tt-val" style="color:#fff">${it.price?('₺'+fN(it.price)):'—'}</span>`;
  html+=`</div>`;
  _showTip(e,html);
}

// ===== PORTFOY — FIXED weighted average bug =====
function getPF(){return JSON.parse(localStorage.getItem('bb_pf')||'[]');}
function savePF(pf){localStorage.setItem('bb_pf',JSON.stringify(pf));}
function addPF(ticker,lot,avg){
  const pf=getPF();
  const ex=pf.findIndex(p=>p.ticker===ticker.toUpperCase());
  if(ex>=0){
    // FIXED: calculate weighted avg BEFORE adding lot
    const oldLot=pf[ex].lot;
    const oldAvg=pf[ex].avg;
    const newTotalCost=(oldLot*oldAvg)+(lot*avg);
    const newTotalLot=oldLot+lot;
    pf[ex].lot=newTotalLot;
    pf[ex].avg=newTotalLot>0?newTotalCost/newTotalLot:avg;
  }else{
    pf.push({ticker:ticker.toUpperCase(),lot,avg});
  }
  savePF(pf);
}
function rmPF(ticker){savePF(getPF().filter(p=>p.ticker!==ticker));}

// ===== NASIL ÇALIŞIR PAGE =====
function renderNasilPage(){
const pg=$('pg-nasil');
let h=`<div style="margin-bottom:24px;text-align:center">
<div style="font-size:48px;margin-bottom:12px">🐂</div>
<h2 style="font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--acc);margin-bottom:8px">BistBull Nasıl Çalışır?</h2>
<p style="font-size:var(--fs-base);color:var(--t2);line-height:1.7;max-width:600px;margin:0 auto">Borsayı anlamak için finans mezunu olmana gerek yok.<br>BistBull sana basitçe şunu söylüyor: <b style="color:var(--t1)">"Bu şirket sağlam mı, şu an alınır mı?"</b></p>
</div>`;

h+=`<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:14px;text-align:center">3 Adımda Anlat</div>
<div class="g3" style="gap:12px;margin-bottom:24px">
<div style="padding:20px;background:linear-gradient(135deg,rgba(76,175,80,.08),rgba(76,175,80,.03));border:1px solid rgba(76,175,80,.2);border-radius:var(--rad2);text-align:center">
<div style="font-size:32px;margin-bottom:8px">🏛️</div>
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-md);color:var(--grn);font-weight:700;margin-bottom:8px">1. Şirket Sağlam mı?</div>
<div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.6">Bilançoya, kârlılığa, borca, büyümeye bakıyoruz. <b style="color:var(--t1)">7 farklı açıdan</b> inceliyoruz.</div>
<div style="margin-top:10px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--grn);background:rgba(76,175,80,.1);padding:4px 10px;border-radius:20px;display:inline-block">= DEĞER SKORU</div>
</div>
<div style="padding:20px;background:linear-gradient(135deg,rgba(100,181,246,.08),rgba(100,181,246,.03));border:1px solid rgba(100,181,246,.2);border-radius:var(--rad2);text-align:center">
<div style="font-size:32px;margin-bottom:8px">⚡</div>
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-md);color:var(--blu);font-weight:700;margin-bottom:8px">2. Zamanlama Uygun mu?</div>
<div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.6">Fiyat yükseliyor mu, hacim artıyor mu, kurumlar alıyor mu? <b style="color:var(--t1)">3 farklı sinyale</b> bakıyoruz.</div>
<div style="margin-top:10px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--blu);background:rgba(100,181,246,.1);padding:4px 10px;border-radius:20px;display:inline-block">= İVME SKORU</div>
</div>
<div style="padding:20px;background:linear-gradient(135deg,rgba(255,179,0,.08),rgba(255,179,0,.03));border:1px solid rgba(255,179,0,.2);border-radius:var(--rad2);text-align:center">
<div style="font-size:32px;margin-bottom:8px">🎯</div>
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-md);color:var(--acc);font-weight:700;margin-bottom:8px">3. Ne Yapmalı?</div>
<div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.6">İkisini birleştirip karar veriyoruz. Sağlam + iyi zamanlama = <span style="color:var(--grn);font-weight:700">AL</span>. Zayıf + kötü = <span style="color:var(--red);font-weight:700">KAÇIN</span>.</div>
<div style="margin-top:10px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--acc);background:rgba(255,179,0,.1);padding:4px 10px;border-radius:20px;display:inline-block">= GENEL SKOR</div>
</div>
</div>`;

h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">🏷️ Etiketler ne demek?</span></div><div class="card-b">
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t4);margin-bottom:10px">KARAR</div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px;margin-bottom:20px">
<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(76,175,80,.08);border-radius:var(--rad);border-left:3px solid var(--grn)"><span style="font-family:'JetBrains Mono',monospace;font-weight:800;color:var(--grn);min-width:50px">AL</span><span style="font-size:var(--fs-sm);color:var(--t2)">Şirket güçlü, zamanlama iyi görünüyor</span></div>
<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(100,181,246,.08);border-radius:var(--rad);border-left:3px solid var(--blu)"><span style="font-family:'JetBrains Mono',monospace;font-weight:800;color:var(--blu);min-width:50px">İZLE</span><span style="font-size:var(--fs-sm);color:var(--t2)">İlginç ama henüz net değil — takipte kal</span></div>
<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(255,202,40,.08);border-radius:var(--rad);border-left:3px solid var(--ylw)"><span style="font-family:'JetBrains Mono',monospace;font-weight:800;color:var(--ylw);min-width:50px">BEKLE</span><span style="font-size:var(--fs-sm);color:var(--t2)">İyi şirket ama pahalı veya zamanlama uygun değil</span></div>
<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;background:rgba(239,83,80,.08);border-radius:var(--rad);border-left:3px solid var(--red)"><span style="font-family:'JetBrains Mono',monospace;font-weight:800;color:var(--red);min-width:50px">KAÇIN</span><span style="font-size:var(--fs-sm);color:var(--t2)">Zayıf görünüyor — dikkatli ol</span></div>
</div>
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t4);margin-bottom:10px">TEMEL DURUM</div>
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px">
<span class="pill" style="background:var(--grnd);color:var(--grn)">Kaliteli Değer — güçlü temel + makul fiyat</span>
<span class="pill" style="background:var(--cynd,var(--bg3));color:var(--cyn)">Pahalı Kalite — iyi şirket ama pahalı</span>
<span class="pill" style="background:var(--ylwd);color:var(--ylw)">Ucuz ama Riskli — ucuz ama temel zayıf</span>
<span class="pill" style="background:var(--bg3);color:var(--t3)">Dengeli — orta seviye temel</span>
<span class="pill" style="background:var(--redd);color:var(--red)">Zayıf Temel — kaçınılması gereken</span>
</div>
<div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);color:var(--t4);margin-bottom:10px">KALİTE</div>
<div style="display:flex;flex-wrap:wrap;gap:6px">
<span class="pill" style="background:var(--grnd);color:var(--grn)">ELİT — en iyi %5</span>
<span class="pill" style="background:var(--grnd);color:var(--grn)">GÜÇLÜ — sağlam şirket</span>
<span class="pill" style="background:var(--ylwd);color:var(--ylw)">ORTA — idare eder</span>
<span class="pill" style="background:var(--redd);color:var(--red)">ZAYIF — dikkat</span>
<span class="pill" style="background:var(--redd);color:var(--red)">RİSKLİ — çok zayıf</span>
</div>
</div></div>`;

h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">📱 Sayfalar ne işe yarar?</span></div><div class="card-b" style="padding:0">
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid var(--bdr)"><span style="font-size:20px">🏠</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);font-weight:700;color:var(--t1)">Ana Sayfa</div><div style="font-size:var(--fs-sm);color:var(--t2)">Genel bakış. Piyasa durumu, en iyi hisseler, AI brifing.</div></div></div>
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid var(--bdr)"><span style="font-size:20px">🏛️</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);font-weight:700;color:var(--t1)">Radar</div><div style="font-size:var(--fs-sm);color:var(--t2)">Tüm hisseleri tarayıp sıralıyor. Uzun vadeli yatırımcı için.</div></div></div>
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid var(--bdr)"><span style="font-size:20px">⚡</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);font-weight:700;color:var(--t1)">Sinyaller</div><div style="font-size:var(--fs-sm);color:var(--t2)">Teknik kırılım ve momentum sinyalleri. Kısa vadeci için.</div></div></div>
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid var(--bdr)"><span style="font-size:20px">🌍</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);font-weight:700;color:var(--t1)">Makro</div><div style="font-size:var(--fs-sm);color:var(--t2)">Dolar, altın, faiz, BIST endeksi, gelişen piyasalar.</div></div></div>
<div style="display:flex;align-items:center;gap:14px;padding:14px 18px"><span style="font-size:20px">📒</span><div><div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);font-weight:700;color:var(--t1)">Portföy</div><div style="font-size:var(--fs-sm);color:var(--t2)">Sanal portföy oluştur, Q ile analiz ettir.</div></div></div>
</div></div>`;

h+=`<div class="card" style="margin-bottom:20px"><div class="card-h"><span class="card-t">❓ Sık Sorulanlar</span></div><div class="card-b">
<div style="padding:14px 0;border-bottom:1px solid var(--bdr)"><div style="font-weight:700;color:var(--t1);margin-bottom:6px">Bu bir yatırım tavsiyesi mi?</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.7"><b>Hayır.</b> BistBull bir karar destek aracıdır. Son karar her zaman senindir.</div></div>
<div style="padding:14px 0;border-bottom:1px solid var(--bdr)"><div style="font-weight:700;color:var(--t1);margin-bottom:6px">Veriler nereden geliyor?</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.7">Borsa İstanbul verileri (KAP/İş Yatırım) ve yfinance. 15-20 dakika gecikmeli olabilir.</div></div>
<div style="padding:14px 0;border-bottom:1px solid var(--bdr)"><div style="font-weight:700;color:var(--t1);margin-bottom:6px">AL diyor, almalı mıyım?</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.7">AL = "şu an iyi görünüyor" demek. Ama her analiz yanılabilir. Tek bir araca göre karar verme.</div></div>
<div style="padding:14px 0;border-bottom:1px solid var(--bdr)"><div style="font-weight:700;color:var(--t1);margin-bottom:6px">Neden bazı hisseler listede yok?</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.7">Veri kalitesi çok düşük olanlar otomatik filtrelenir.</div></div>
<div style="padding:14px 0"><div style="font-weight:700;color:var(--t1);margin-bottom:6px">Q asistanı nasıl çalışıyor?</div><div style="font-size:var(--fs-sm);color:var(--t2);line-height:1.7">Yapay zeka tüm verileri okuyup Türkçe yorum üretiyor. AI yanılabilir — referans olarak kullan.</div></div>
</div></div>`;

h+=`<details style="margin-bottom:20px"><summary style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-sm);color:var(--t3);cursor:pointer;padding:12px 16px;background:var(--bg2);border:1px solid var(--bdr);border-radius:var(--rad)">🔬 Teknik Detaylar (meraklılar için)</summary>
<div style="padding:16px;background:var(--bg2);border:1px solid var(--bdr);border-top:0;border-radius:0 0 var(--rad) var(--rad);font-size:var(--fs-sm);color:var(--t2);line-height:1.7">
<b style="color:var(--t1)">Değer Boyutları (7):</b> Değerleme %18, Kalite %30, Büyüme %15, Bilanço %10, Kâr Kalitesi %10, Sermaye %9, Hendek %8<br>
<b style="color:var(--t1)">İvme Boyutları (3):</b> Momentum %40, Teknik Kırılım %35, Kurum Akışı %25<br>
<b style="color:var(--t1)">Genel:</b> Değer×0.55 + İvme×0.35 + Stretch + Risk<br><br>
<b style="color:var(--t1)">Modeller:</b> Piotroski F-Score, Altman Z, Beneish M, Graham, Buffett Filtresi, DCF<br>
<b style="color:var(--t1)">Sektörler:</b> Banka, holding, sanayi, savunma, enerji, perakende, ulaştırma<br>
<b style="color:var(--t1)">Kaynak:</b> borsapy (birincil) + yfinance (yedek), circuit breaker ile otomatik geçiş
</div></details>`;

h+=`<div style="text-align:center;padding:20px;color:var(--t4);font-size:var(--fs-sm)">
Soru mu var? Sağ alttaki <span style="color:var(--acc);font-weight:700">Q</span> butonuna bas 💬
</div>`;
pg.innerHTML=h;}

// ===== BULLWATCH TRADE TRACKER (Faz 5) =====
// Server-side tahtacı pozisyonları + exit signals. Mevcut localStorage
// portföy aşağıda BOZULMADAN duruyor — ikisi YAN YANA çalışıyor.
async function loadBwTradePositions(){
  try {
    const r = await api('/api/portfolio/positions');
    const v = (r && (r.value || r)) || {};
    S.bwPositions = { items: v.items || [], fetched_at: Date.now() };
  } catch(e) {
    console.warn('positions fetch failed', e);
    S.bwPositions = { items: [], error: String(e.message||e) };
  }
}

async function loadBwPortfolioStats(){
  try {
    const r = await api('/api/portfolio/stats');
    const v = (r && (r.value || r)) || {};
    S.bwPortfolioStats = (v.stats || {});
  } catch(e) {/* silent */}
}

// BullWatch "+ Aldım" — entry modal
function showBwOpenPositionModal(ticker, suggestedPrice){
  const existing = document.getElementById('bwOpenPosOv');
  if (existing) existing.remove();
  const ov = document.createElement('div');
  ov.id = 'bwOpenPosOv';
  ov.className = 'mov';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  const px = suggestedPrice != null ? Number(suggestedPrice).toFixed(2) : '';
  ov.innerHTML = `<div style="background:var(--bg1);border:1px solid var(--bdr2);border-radius:var(--rad);max-width:440px;width:100%;padding:20px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h3 style="font-family:'JetBrains Mono',monospace;color:var(--grn);font-size:16px">💼 ${esc(ticker)} — Pozisyon Aç</h3>
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="this.closest('.mov').remove()">✕</button>
    </div>
    <p style="font-size:11px;color:var(--t3);line-height:1.55;margin-bottom:14px">BullWatch tahtacı imzasıyla pozisyon aç. Sistem her scan'den sonra exit signal hesaplar — zone düşerse, tahtacı çekilirse, stop'a yaklaşırsa sana <b style="color:var(--orn)">"sat"</b> önerir.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
      <div>
        <div style="font-size:10px;color:var(--t3);margin-bottom:3px">GİRİŞ FİYATI (TL)</div>
        <input id="bwPosPrice" type="number" step="0.01" value="${px}" inputmode="decimal" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px">
      </div>
      <div>
        <div style="font-size:10px;color:var(--t3);margin-bottom:3px">LOT (adet)</div>
        <input id="bwPosLot" type="number" value="100" inputmode="numeric" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px">
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
      <div>
        <div style="font-size:10px;color:var(--t3);margin-bottom:3px">STOP LOSS (%)</div>
        <input id="bwPosStop" type="number" step="0.5" value="-8" inputmode="decimal" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px">
      </div>
      <div>
        <div style="font-size:10px;color:var(--t3);margin-bottom:3px">HEDEF (%)</div>
        <input id="bwPosTarget" type="number" step="0.5" value="15" inputmode="decimal" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px">
      </div>
    </div>
    <div style="margin-bottom:14px">
      <div style="font-size:10px;color:var(--t3);margin-bottom:3px">NOT (opsiyonel)</div>
      <input id="bwPosNotes" type="text" placeholder="Tahtacı imzası net + walk-up 7g..." maxlength="200" style="font-family:'JetBrains Mono',monospace;font-size:12px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:40px">
    </div>
    <div id="bwPosError" style="display:none;color:var(--red);font-size:11px;margin-bottom:10px"></div>
    <div style="display:flex;gap:6px;justify-content:flex-end">
      <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="this.closest('.mov').remove()">İptal</button>
      <button id="bwPosSubmit" class="btn btn-sm btn-grn" onclick="submitBwOpenPosition('${esc(ticker)}', this)">💼 Pozisyonu Aç</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  setTimeout(() => { const inp = document.getElementById('bwPosLot'); if (inp) inp.focus(); }, 50);
}

async function submitBwOpenPosition(ticker, btn){
  const price = parseFloat(document.getElementById('bwPosPrice').value);
  const lot = parseFloat(document.getElementById('bwPosLot').value);
  const stop = parseFloat(document.getElementById('bwPosStop').value);
  const target = parseFloat(document.getElementById('bwPosTarget').value);
  const notes = (document.getElementById('bwPosNotes').value || '').trim();
  const err = document.getElementById('bwPosError');
  err.style.display = 'none';
  if (!price || price <= 0 || !lot || lot <= 0) {
    err.textContent = 'Geçerli fiyat ve lot giriniz.'; err.style.display = 'block'; return;
  }
  if (stop >= 0) {
    err.textContent = 'Stop loss negatif olmalı (örn: -8).'; err.style.display = 'block'; return;
  }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Açılıyor…'; }
  try {
    const r = await fetch('/api/portfolio/positions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ticker, entry_price: price, lot,
        notes: notes || null,
        stop_loss_pct: stop, take_profit_pct: target,
      }),
    });
    const j = await r.json();
    if (j.error || !r.ok) throw new Error(j.error || 'open failed');
    // Success — close modal, drop position cache, build a CLEAR toast.
    document.getElementById('bwOpenPosOv').remove();
    S.bwPositions = null;
    // Toast with action button — kullanıcı pozisyonun nereye gittiğini
    // anlamadığı için (audit feedback) explicit "Portföy'e Git" linki
    // ve yeterli süre.
    const toast = document.createElement('div');
    toast.id = 'bwPosToast';
    toast.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:10000;padding:14px 16px;background:var(--grn);color:#000;font-weight:700;border-radius:var(--rad);box-shadow:0 6px 24px rgba(34,197,94,.45);max-width:340px;font-size:13px;line-height:1.4';
    toast.innerHTML = `<div style="margin-bottom:10px">✓ ${esc(ticker)} pozisyonu açıldı<br><span style="font-weight:500;opacity:.9;font-size:11px">${lot} lot × ${price.toFixed(2)} TL = ${(lot*price).toFixed(0)} TL maliyet</span></div>
      <div style="display:flex;gap:6px;justify-content:flex-end">
        <button onclick="this.parentElement.parentElement.remove()" style="background:transparent;border:1px solid #000;color:#000;padding:5px 10px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer">Tamam</button>
        <button onclick="this.parentElement.parentElement.remove();goPage('portfoy')" style="background:#000;color:var(--grn);border:0;padding:5px 12px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer">📂 Portföy'e Git →</button>
      </div>`;
    document.body.appendChild(toast);
    // Auto-dismiss longer (8s instead of 3.5) so user has time to react
    setTimeout(() => { const t = document.getElementById('bwPosToast'); if (t) t.remove(); }, 8000);
    // Update the nav badge so user knows there's a new position
    _bumpPortfolioBadge();
  } catch(e) {
    err.textContent = String(e.message || e);
    err.style.display = 'block';
    if (btn) { btn.disabled = false; btn.textContent = '💼 Pozisyonu Aç'; }
  }
}

// Nav "Portföy" tab'ına küçük yeşil nokta ekle — kullanıcı yeni pozisyonun
// orada olduğunu anlasın. Portföy sayfasını açtığında nokta kaybolur.
function _bumpPortfolioBadge(){
  try {
    const btn = document.querySelector("[onclick*=\"goPage('portfoy')\"]");
    if (btn && !btn.querySelector('.pf-badge-dot')) {
      const dot = document.createElement('span');
      dot.className = 'pf-badge-dot';
      dot.style.cssText = 'display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--grn);margin-left:5px;box-shadow:0 0 8px var(--grn);animation:pfPulse 1.5s infinite';
      btn.appendChild(dot);
      // Inject @keyframes once
      if (!document.getElementById('pfBadgeKf')) {
        const st = document.createElement('style');
        st.id = 'pfBadgeKf';
        st.textContent = '@keyframes pfPulse{0%,100%{opacity:1}50%{opacity:.4}}';
        document.head.appendChild(st);
      }
    }
  } catch(e) {}
}

function _clearPortfolioBadge(){
  try {
    document.querySelectorAll('.pf-badge-dot').forEach(d => d.remove());
  } catch(e) {}
}

async function closeBwPosition(positionId, ticker){
  const px = prompt(`${ticker} pozisyonu kapatılıyor. Çıkış fiyatı (TL):`);
  if (!px) return;
  const xp = parseFloat(px);
  if (!xp || xp <= 0) { alert('Geçerli fiyat giriniz.'); return; }
  const reason = prompt('Sebep (opsiyonel, örn: "exit signal", "manuel"):') || null;
  try {
    const r = await fetch('/api/portfolio/positions/' + encodeURIComponent(positionId) + '/close', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({exit_price: xp, exit_reason: reason}),
    });
    const j = await r.json();
    if (j.error) { alert(j.error); return; }
    S.bwPositions = null;
    renderPortfoyPage();
  } catch(e) { alert(String(e.message || e)); }
}

function _bwTradePositionsSection(){
  const data = S.bwPositions;
  if (!data) {
    return `<div class="card" style="margin-bottom:18px"><div class="card-h"><span class="card-t">🎯 BullWatch Tahtacı Pozisyonları</span></div><div class="card-b"><div style="color:var(--t3);font-size:11px;padding:12px;text-align:center">Yükleniyor…</div></div></div>`;
  }
  const items = data.items || [];
  if (!items.length) {
    return `<div class="card" style="margin-bottom:18px"><div class="card-h"><span class="card-t">🎯 BullWatch Tahtacı Pozisyonları</span></div><div class="card-b">
      <div style="padding:14px;color:var(--t3);font-size:12px;line-height:1.6">Henüz açık BullWatch pozisyonu yok. BullWatch listesinde bir hissenin <b style="color:var(--grn)">"+ Aldım"</b> butonuna tıklayarak pozisyon açabilirsin.<br><span style="color:var(--t4);font-size:10px">Sistem her scan'den sonra exit signal hesaplar — zone düşer, tahtacı çekilirse seni uyarır.</span></div>
    </div></div>`;
  }
  const verdictStyle = {
    sell:    {bg:'rgba(239,83,80,.10)', col:'var(--red)', ic:'🚪', lbl:'SAT'},
    caution: {bg:'rgba(255,167,38,.10)', col:'var(--orn)', ic:'⚠️', lbl:'İZLE'},
    hold:    {bg:'rgba(38,194,129,.08)', col:'var(--grn)', ic:'✓', lbl:'TUT'},
  };
  let h = `<div class="card" style="margin-bottom:18px"><div class="card-h"><span class="card-t">🎯 BullWatch Tahtacı Pozisyonları (${items.length})</span><button class="btn btn-sm" style="background:var(--bg3);color:var(--t2)" onclick="S.bwPositions=null;loadBwTradePositions().then(()=>renderPortfoyPage())">🔄</button></div><div class="card-b" style="padding:0">`;
  items.forEach((it, i) => {
    const sig = it.signal || {};
    const v = sig.verdict || 'hold';
    const vs = verdictStyle[v] || verdictStyle.hold;
    const pnl = sig.pnl_pct;
    const pnlCol = pnl == null ? 'var(--t3)' : pnl >= 0 ? 'var(--grn)' : 'var(--red)';
    const pnlStr = pnl == null ? '—' : `${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}%`;
    const entryDate = it.entry_date ? new Date(it.entry_date).toLocaleDateString('tr-TR') : '';
    const ago = it.entry_date ? _alarmTimeAgo(it.entry_date) : '';
    const currentPx = sig.current_price;
    const reasons = sig.reasons || [];
    h += `<div style="padding:14px 16px;${i<items.length-1?'border-bottom:1px solid var(--bdr);':''};background:${vs.bg}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
            <span class="clk-t" style="font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;color:var(--cyn)" onclick="loadTicker('${esc(it.ticker)}')">${esc(it.ticker)}</span>
            <span style="display:inline-flex;align-items:center;gap:3px;font-family:'JetBrains Mono',monospace;font-size:11px;color:${vs.col};font-weight:700;padding:2px 8px;background:${vs.bg};border:1px solid ${vs.col}55;border-radius:4px">${vs.ic} ${vs.lbl}</span>
            <span style="font-size:10px;color:var(--t4)">${esc(entryDate)} · ${esc(ago)}</span>
          </div>
          <div style="font-size:11px;color:var(--t3);font-family:'JetBrains Mono',monospace">${it.lot} lot × ${(it.entry_price||0).toFixed(2)} = ${((it.lot||0)*(it.entry_price||0)).toFixed(0)} TL maliyet${currentPx?` · şimdi ${currentPx.toFixed(2)} TL`:''}</div>
          ${(it.zone_at_entry || it.pattern_at_entry) ? `<div style="font-size:10.5px;color:var(--t4);margin-top:2px">Giriş: ${esc(it.zone_at_entry||'')} · ${esc((it.pattern_at_entry||'').slice(0, 60))}</div>` : ''}
          ${reasons.length ? `<div style="margin-top:6px;font-size:10.5px;color:${vs.col};line-height:1.5">${reasons.slice(0, 3).map(r => '⚠ ' + esc(r)).join('<br>')}</div>` : ''}
          ${it.notes ? `<div style="margin-top:6px;font-size:10.5px;color:var(--t3);font-style:italic">📝 ${esc(it.notes)}</div>` : ''}
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;color:${pnlCol};line-height:1">${pnlStr}</div>
          <div style="font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;margin-top:2px">P&L</div>
          <button class="btn btn-sm" style="background:var(--bg3);color:var(--t2);font-size:10px;padding:3px 8px;margin-top:8px" onclick="closeBwPosition('${esc(it.position_id)}', '${esc(it.ticker)}')">🚪 Kapat</button>
        </div>
      </div>
    </div>`;
  });
  h += '</div></div>';
  return h;
}

function renderPortfoyPage(){
const pg=$('pg-portfoy');const pf=getPF();
// Audit fix: kullanıcı portföye geldi, badge'i temizle.
_clearPortfolioBadge();
// Faz 5: Lazy-load BullWatch positions (server-tracked, with exit signals).
// Mevcut localStorage portföy BOZULMADAN aşağıda kalıyor.
if (!S.bwPositions && !S._bwPositionsLoading) {
  S._bwPositionsLoading = true;
  loadBwTradePositions().then(() => {
    S._bwPositionsLoading = false;
    renderPortfoyPage();
  }).catch(() => { S._bwPositionsLoading = false; });
}
let h=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px"><div><h2 style="font-family:'JetBrains Mono',monospace;font-size:15px;color:var(--gold)">📒 Portföy Defteri</h2><p style="font-size:11px;color:var(--t3);margin-top:2px">Sanal portföyünüz — veriler localStorage'da saklanir</p></div><div style="display:flex;gap:6px"><button class="btn btn-sm btn-grn" onclick="showAddPF()">+ HISSE EKLE</button><button class="btn btn-sm btn-blu" onclick="askDedePortfoy()">🤖 Q'YA SOR</button></div></div>`;
// BullWatch trade tracker section
h += _bwTradePositionsSection();
h+=`<div id="pfAddForm" style="display:none;margin-bottom:14px;padding:14px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rad)"><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end"><div style="flex:1;min-width:80px"><div style="font-size:10px;color:var(--t3);margin-bottom:3px">TICKER</div><input id="pfTicker" type="text" placeholder="THYAO" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;text-transform:uppercase;min-height:44px"></div><div style="flex:1;min-width:60px"><div style="font-size:10px;color:var(--t3);margin-bottom:3px">LOT</div><input id="pfLot" type="number" placeholder="100" inputmode="numeric" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px"></div><div style="flex:1;min-width:80px"><div style="font-size:10px;color:var(--t3);margin-bottom:3px">MALIYET (TL)</div><input id="pfAvg" type="number" step="0.01" placeholder="45.50" inputmode="decimal" style="font-family:'JetBrains Mono',monospace;font-size:13px;padding:8px 10px;background:var(--bg0);border:1px solid var(--bdr);border-radius:var(--rad);color:var(--t1);outline:0;width:100%;min-height:44px"></div><button class="btn btn-grn btn-sm" onclick="doAddPF()">EKLE</button><button class="btn btn-sm" style="background:var(--bg2);color:var(--t3)" onclick="$('pfAddForm').style.display='none'">IPTAL</button></div></div>`;
h+=`<div id="pfDedeBox" style="display:none;margin-bottom:14px"></div>`;
if(!pf.length){
  h+=`<div class="emp"><h3 style="color:var(--t2)">Henüz portföyünüz boş</h3><p style="color:var(--t3)">+ Hisse Ekle ile sanal portföyünüzü oluşturun.<br>Q portföyünüzü analiz etsin!</p></div>`;
} else {
  h+=`<div class="card" style="margin-bottom:14px"><div class="card-h"><span class="card-t">📊 Portfoy (${pf.length} hisse)</span></div><div class="card-b" style="overflow-x:auto"><table class="dtb"><thead><tr><th>Ticker</th><th>Lot</th><th>Maliyet</th><th>Toplam</th><th></th></tr></thead><tbody>`;
  let totalCost=0;
  pf.forEach(p=>{
    const cost=p.lot*p.avg;totalCost+=cost;
    h+=`<tr><td class="clk-t" onclick="loadTicker('${esc(p.ticker)}')">${esc(p.ticker)}</td><td style="color:var(--t1)">${p.lot}</td><td style="color:var(--t1)">${p.avg.toFixed(2)} TL</td><td style="color:var(--cyn);font-weight:700">${fN(cost)} TL</td><td><button class="wl-rm" onclick="rmPF('${esc(p.ticker)}');renderPortfoyPage()">✕</button></td></tr>`;
  });
  h+=`</tbody></table><div style="padding:8px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--cyn);font-weight:700">Toplam Maliyet: ${fN(totalCost)} TL</div></div></div>`;
  const secMap={};pf.forEach(p=>{const items=S.scan?.items||[];const found=items.find(i=>i.ticker===p.ticker);const sec=found?.sector||'Bilinmiyor';if(!secMap[sec])secMap[sec]={count:0,cost:0};secMap[sec].count++;secMap[sec].cost+=p.lot*p.avg;});
  h+=`<div class="card" style="margin-bottom:14px"><div class="card-h"><span class="card-t">🏢 Sektör Dağılımı</span></div><div class="card-b">`;
  const totalC=Object.values(secMap).reduce((a,b)=>a+b.cost,0)||1;
  Object.entries(secMap).sort((a,b)=>b[1].cost-a[1].cost).forEach(([sec,v])=>{
    const pct=(v.cost/totalC*100).toFixed(1);
    h+=`<div style="margin-bottom:6px"><div style="display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:11px;margin-bottom:2px"><span style="color:var(--t2)">${esc(sec)} (${v.count})</span><span style="color:var(--t1)">${pct}%</span></div><div class="sb-bar"><div class="sb-fill" style="width:${pct}%;background:var(--cyn)"></div></div></div>`;
  });
  h+=`</div></div>`;
}
pg.innerHTML=h;
}
function showAddPF(){$('pfAddForm').style.display='block';$('pfTicker').focus();}
function doAddPF(){const t=$('pfTicker').value.trim().toUpperCase();const l=parseInt($('pfLot').value)||0;const a=parseFloat($('pfAvg').value)||0;if(!t||t.length<3||l<=0||a<=0){alert('Ticker, lot ve maliyet girin');return;}addPF(t,l,a);$('pfAddForm').style.display='none';$('pfTicker').value='';$('pfLot').value='';$('pfAvg').value='';renderPortfoyPage();}
async function askDedePortfoy(){const pf=getPF();if(!pf.length){alert('Önce portföye hisse ekleyin');return;}const box=$('pfDedeBox');box.style.display='block';box.innerHTML=`<div class="aib"><div class="aib-t">🤖 Q PORTFÖY ANALİZİ</div><div class="aib-tx">Portföye bakıyoruz…</div></div>`;const desc=pf.map(p=>`${p.ticker} ${p.lot} lot (maliyet: ${p.avg} TL)`).join(', ');try{const d=await api('/api/agent?q='+encodeURIComponent('Portfoyumde sunlar var: '+desc+'. Sektörel dağılım ve risk hakkında ne düşünüyorsun?'));box.innerHTML=`<div class="aib"><div class="aib-t">🤖 Q PORTFÖY ANALİZİ</div><div class="aib-tx">${esc(d.answer)}</div></div>`;}catch(e){box.innerHTML='';}}

// ===== INIT — progressive lazy loading =====
(async()=>{
renderHome(); // skeleton anında göster
// Auto-open ticker from URL param (?t=THYAO)
try{var _up=new URLSearchParams(window.location.search);var _ut=_up.get('t');if(_ut&&_ut.length>=2){setTimeout(function(){api('/api/resolve-ticker?q='+encodeURIComponent(_ut.trim())).then(function(d){if(d&&d.tickers&&d.tickers.length){loadTicker(d.tickers[0]);}else{loadTicker(_ut.trim().toUpperCase());}}).catch(function(){loadTicker(_ut.trim().toUpperCase());});},1500);}}catch(e){}

let _renderTimer=null;
function scheduleRender(){if(_renderTimer)return;_renderTimer=setTimeout(()=>{_renderTimer=null;_reRender();},150);}

// Phase 1: Tümü paralel — biten render eder
loadQuote().then(scheduleRender);
loadBook().then(scheduleRender);
loadMacro();
loadLiveStats().then(scheduleRender);
loadMarketStatus().then(scheduleRender);
api('/api/health').catch(()=>null);

// Phase 2: Scan data — progressif yükleme
(async()=>{
  async function loadScanData(){
    const d=await api('/api/top10');
    if(d.items&&d.items.length>0){
      S.scan=d;renderAlphaQuadrant();_reRender();
      Promise.all([api('/api/dashboard').catch(()=>null),api('/api/hero-summary').catch(()=>null)]).then(([dashR,heroR])=>{
        if(dashR)S.dash=dashR;if(heroR)S.hero=heroR;updCnt(S.dash);_reRender();
      });
      return true;
    }
    return false;
  }
  try{
    if(await loadScanData()) {}
    else{
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,2000));
        try{
          const st=await api('/api/scan-status').catch(()=>null);
          if(st&&st.total>0){
            const pct=Math.round(st.progress/st.total*100);
            const scanEl=document.querySelector('#pg-home .card-t');
            if(!scanEl){const ldEl=document.querySelector('.ld-t');if(ldEl)ldEl.textContent=`${st.phase==='raw_fetch'?'Veri çekiyoruz':'Hesaplıyoruz'} ${pct}%`;}
          }
          if(await loadScanData()) break;
        }catch(e){}
      }
    }
  }catch(e){
    for(let i=0;i<30;i++){await new Promise(r=>setTimeout(r,2000));try{if(await loadScanData()) break;}catch(e2){}}
  }
})();

// Phase 3: cross + heatmap — bağımsız paralel
api('/api/cross').then(d=>{S.cross=d;scheduleRender();}).catch(()=>{});
loadHeatmap().then(scheduleRender).catch(()=>{});
})();

// ===== Q ASSISTANT (XSS-safe) =====
function toggleAgent(){const el=$('agentPanel');el.style.display=el.style.display==='none'?'flex':'none';}
async function sendAgent(){
  const inp=$('agentInput');const box=$('agentMsgs');const q=inp.value.trim();if(!q)return;
  // XSS-safe: use textContent for user input
  const uDiv=document.createElement('div');uDiv.style.cssText='text-align:right;margin-bottom:8px';
  const uSpan=document.createElement('span');uSpan.style.cssText='background:var(--blud);border:1px solid rgba(33,150,243,.3);padding:6px 10px;border-radius:8px 8px 0 8px;font-size:12px;color:var(--t1);display:inline-block;max-width:80%';
  uSpan.textContent=q;uDiv.appendChild(uSpan);box.appendChild(uDiv);

  const ldDiv=document.createElement('div');ldDiv.style.cssText='margin-bottom:8px';ldDiv.id='agentLoading';
  ldDiv.innerHTML='<span style="color:var(--t3);font-size:11px">bakıyoruz…</span>';
  box.appendChild(ldDiv);box.scrollTop=box.scrollHeight;inp.value='';

  try{
    const d=await api('/api/agent?q='+encodeURIComponent(q));
    const ld=$('agentLoading');if(ld)ld.remove();
    const aDiv=document.createElement('div');aDiv.style.cssText='margin-bottom:8px';
    const aSpan=document.createElement('span');aSpan.style.cssText='background:var(--bg3);border:1px solid var(--bdr);padding:6px 10px;border-radius:8px 8px 8px 0;font-size:12px;color:var(--t1);display:inline-block;max-width:85%;line-height:1.5';
    aSpan.textContent=d.answer;aDiv.appendChild(aSpan);box.appendChild(aDiv);box.scrollTop=box.scrollHeight;
  }catch(e){
    const ld=$('agentLoading');if(ld)ld.remove();
    box.innerHTML+=`<div style="margin-bottom:8px"><span style="color:var(--red);font-size:11px">Hata</span></div>`;
  }
}

// ===== ONBOARDING TOUR (3 steps, first-time only) =====
const ONB_STEPS = [
  {
    title: 'Adım 1 / 3 · Ana Skor',
    text: 'Bu yuvarlak grafik hissenin genel skorunu gösteriyor. 70 üstü güçlü, 50 altı zayıf. Tek bakışta "iyi mi değil mi?" cevabını alırsın.',
    targetId: null,
    targetBox: {top:64,left:20,width:200,height:80},
    cardPos: 'bottom',
  },
  {
    title: 'Adım 2 / 3 · Sosyal Nabız',
    text: 'Bu bölüm X/Twitter platformunda bu hisse hakkında atılan tweet sayısını gösteriyor. Ani artış = piyasa bu hisseyi konuşuyor demek.',
    targetId: null,
    targetBox: {top:64,left:20,width:200,height:80},
    cardPos: 'bottom',
  },
  {
    title: 'Adım 3 / 3 · Hisseyi İncele',
    text: 'Listeden herhangi bir hisseye tıkla — tüm detay orada: karar nedenleri, skorlar, teknik görünüm, değerleme.',
    targetId: null,
    targetBox: {top:64,left:20,width:280,height:44},
    cardPos: 'bottom',
  },
];

let _onbStep = 0;

function startOnboarding() {
  if (localStorage.getItem('bb_onb_done')) return;
  _onbStep = 0;
  document.getElementById('onbOverlay').style.display = 'block';
  document.body.style.overflow = 'hidden';
  renderOnbStep();
}

function renderOnbStep() {
  const overlay = document.getElementById('onbOverlay');
  if (_onbStep >= ONB_STEPS.length) { finishOnboarding(); return; }
  const step = ONB_STEPS[_onbStep];

  // Determine target position dynamically
  let box = step.targetBox;
  if (step.targetId) {
    const el = document.getElementById(step.targetId);
    if (el) {
      const r = el.getBoundingClientRect();
      box = {top: r.top - 6, left: r.left - 6, width: r.width + 12, height: r.height + 12};
    }
  }

  // Dots
  const dots = ONB_STEPS.map((_, i) =>
    `<div class="onb-dot ${i === _onbStep ? 'active' : ''}"></div>`
  ).join('');

  // Card position
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let cardStyle = '';
  const cardW = Math.min(300, vw - 32);
  let cardLeft = Math.max(16, Math.min(box.left, vw - cardW - 16));
  let cardTop = box.top + box.height + 14;
  if (cardTop + 180 > vh) cardTop = box.top - 190;
  if (cardTop < 10) cardTop = 10;
  cardStyle = `top:${cardTop}px;left:${cardLeft}px;width:${cardW}px`;

  const isLast = _onbStep === ONB_STEPS.length - 1;

  overlay.innerHTML = `
    <div class="onb-spot" style="top:${box.top}px;left:${box.left}px;width:${box.width}px;height:${box.height}px"></div>
    <div class="onb-card" style="${cardStyle}">
      <div class="onb-title">${step.title}</div>
      <div class="onb-text">${step.text}</div>
      <div class="onb-dots">${dots}</div>
      <div class="onb-actions">
        <button class="onb-skip" onclick="finishOnboarding()">Geç</button>
        <button class="onb-next" onclick="_onbStep++;renderOnbStep()">${isLast ? 'Başla! 🚀' : 'İleri →'}</button>
      </div>
    </div>`;
}

function finishOnboarding() {
  localStorage.setItem('bb_onb_done', '1');
  const overlay = document.getElementById('onbOverlay');
  overlay.style.opacity = '0';
  overlay.style.transition = 'opacity .3s';
  setTimeout(() => { overlay.style.display = 'none'; overlay.style.opacity = ''; }, 300);
  document.body.style.overflow = '';
}

// Start onboarding after 1.5s if not done
setTimeout(() => {
  if (!localStorage.getItem('bb_onb_done') && localStorage.getItem('bb_welcomed')) {
    startOnboarding();
  }
}, 1500);

// ===== BULLALFA TAB =====
function renderBullalfaPage(){
  const el=document.getElementById('pg-bullalfa');
  if(!el)return;
  if(window.BullAlfa && typeof window.BullAlfa.renderTab==='function'){
    window.BullAlfa.renderTab(el);
  } else {
    el.innerHTML='<div style="padding:1rem;color:var(--t3)">BullAlfa modülü yüklenemedi (bullalfa.js eksik olabilir).</div>';
  }
}

// ===== GÜNLÜK BÜLTEN (Stage 7c) =====
// Daily bulletin page — consumes /api/daily-brief* endpoints from
// Stage 7b. Renders today's bulletin or the most recent one if today
// hasn't been generated yet (BIST closed days, fresh deploy).
//
// Layout:
//   - Headline + generated-at timestamp
//   - Stats row (scanned / conviction / confirmed / early)
//   - CONVICTION top 5 cards
//   - Confirmed-new-today list
//   - Sector rotation winners
//   - Biggest movers (gainers + losers)
//   - KAP highlights
//   - Pre-alarm candidates
//   - Archive sidebar (last 30 dates, click to load)
async function renderBultenPage(){
  const pg = $('pg-bulten');
  if (!pg) return;
  pg.innerHTML = `<div class="ld" style="padding:40px 20px">
    <div class="sp"></div>
    <div class="ld-t" style="margin-top:12px">📰 Bülten yükleniyor…</div>
  </div>`;
  let latest = null;
  let history = [];
  try {
    [latest, history] = await Promise.all([
      api('/api/daily-brief').catch(() => null),
      api('/api/daily-brief/history?limit=30').catch(() => ({dates: []})),
    ]);
  } catch (e) {
    pg.innerHTML = `<div style="padding:24px;color:var(--red)">Bülten alınamadı: ${esc(e.message || 'unknown')}</div>`;
    return;
  }
  const bulletin = latest && latest.bulletin ? latest.bulletin : null;
  const archiveDates = (history && history.dates) || [];
  if (!bulletin) {
    pg.innerHTML = `<div style="padding:24px">
      <h2 style="margin:0 0 12px">📰 Günlük Bülten</h2>
      <p style="color:var(--t3);max-width:540px">
        Henüz bülten yazılmadı. İlk bülten kapanış sonrası (İstanbul 18:30) otomatik
        olarak yazılacak. Tetikleyiciyi manuel test etmek istersen:
      </p>
      <div style="margin-top:12px">
        <button class="btn btn-sm btn-blu" onclick="window._regenBulletin()">🔄 Şimdi oluştur</button>
      </div>
    </div>`;
    return;
  }
  pg.innerHTML = _renderBultenContent(bulletin, archiveDates);
}

function _renderBultenContent(rec, archive){
  const c = (rec && rec.content) || {};
  const date = rec.bulletin_date || '—';
  const genAt = rec.generated_at ? new Date(rec.generated_at).toLocaleString('tr-TR') : '';
  const stats = c.stats || {};
  const headline = c.headline || 'Bültenin başlığı yok.';
  let h = `<div style="padding:16px 20px;max-width:1200px;margin:0 auto">`;

  // Header
  h += `<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px">
    <div>
      <h2 style="margin:0 0 4px;font-size:var(--fs-xl)">📰 Günlük Bülten — ${esc(date)}</h2>
      <div style="color:var(--t3);font-size:var(--fs-xs)">Oluşturulma: ${esc(genAt)}</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <select id="bultenArchive" onchange="_loadBultenDate(this.value)" style="background:var(--bg2);color:var(--t1);border:1px solid var(--bdr);border-radius:4px;padding:6px 10px;font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs)">
        <option value="">Arşivden seç…</option>
        ${(archive||[]).map(a => `<option value="${esc(a.bulletin_date)}" ${a.bulletin_date===date?'selected':''}>${esc(a.bulletin_date)}</option>`).join('')}
      </select>
      <button class="btn btn-sm btn-blu" onclick="window._regenBulletin()">🔄 Yenile</button>
    </div>
  </div>`;

  // Headline + stats
  h += `<div class="card" style="margin-bottom:16px"><div class="card-b">
    <div style="font-size:var(--fs-md);font-weight:600;margin-bottom:12px">${esc(headline)}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px">
      ${_bultenStatCell('Taranan', stats.scanned)}
      ${_bultenStatCell('CONVICTION', stats.conviction, 'var(--grn)')}
      ${_bultenStatCell('CONFIRMED', stats.confirmed, 'var(--ylw)')}
      ${_bultenStatCell('EARLY', stats.early, 'var(--blu)')}
    </div>
  </div></div>`;

  // CONVICTION top
  if ((c.conviction_top||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">🐂 CONVICTION — Top ${c.conviction_top.length}</span></div><div class="card-b">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px">
        ${c.conviction_top.map(s => `<div style="border:1px solid var(--bdr);border-radius:6px;padding:10px;background:var(--bg2)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span class="clk-t" style="font-size:var(--fs-md);font-weight:700" onclick="loadTicker('${esc(s.symbol)}')">${esc(s.symbol||'-')}</span>
            <span style="background:var(--grn);color:#000;padding:2px 8px;border-radius:3px;font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);font-weight:700">${(s.score||0).toFixed(0)}</span>
          </div>
          ${s.pattern ? `<div style="font-size:var(--fs-xs);color:var(--t2);margin-bottom:4px">${esc(s.pattern)}</div>` : ''}
          ${(s.reasons||[]).slice(0,2).map(r => `<div style="font-size:10px;color:var(--grn);margin-top:2px">✓ ${esc(r)}</div>`).join('')}
        </div>`).join('')}
      </div>
    </div></div>`;
  }

  // Confirmed new today
  if ((c.confirmed_new||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">🆕 Bugün CONFIRMED olanlar</span></div><div class="card-b">
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        ${c.confirmed_new.map(s => `<span class="clk-t" style="background:var(--bg2);padding:4px 10px;border-radius:3px;font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs);border:1px solid var(--ylw)" onclick="loadTicker('${esc(s.symbol)}')">${esc(s.symbol)} <span style="color:var(--t3)">${(s.score||0).toFixed(0)}</span></span>`).join('')}
      </div>
    </div></div>`;
  }

  // Sector rotation
  if ((c.sector_rotation||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">🔄 Sektör Rotasyonu — Liderler</span></div><div class="card-b">
      <table style="width:100%;font-family:'JetBrains Mono',monospace;font-size:var(--fs-xs)">
        <thead><tr style="text-align:left;color:var(--t3);border-bottom:1px solid var(--bdr)">
          <th style="padding:6px">Sektör</th><th style="padding:6px;text-align:right">Aktivite</th>
        </tr></thead>
        <tbody>${c.sector_rotation.map(s => `<tr style="border-bottom:1px solid var(--bdr)">
          <td style="padding:6px">${esc(s.sector||s.name||'-')}</td>
          <td style="padding:6px;text-align:right">${s.activity_score!=null?s.activity_score.toFixed(1):'-'}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div></div>`;
  }

  // Biggest movers
  const mv = c.biggest_movers || {};
  if ((mv.gainers||[]).length || (mv.losers||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">📈 Günün Hareketleri</span></div><div class="card-b">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div><div style="color:var(--grn);font-size:var(--fs-xs);font-weight:700;margin-bottom:6px">EN ÇOK YÜKSELEN</div>
          ${(mv.gainers||[]).map(g => `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs);font-family:'JetBrains Mono',monospace"><span class="clk-t" onclick="loadTicker('${esc(g.ticker)}')">${esc(g.ticker)}</span><span style="color:var(--grn)">+${(g.change_pct||0).toFixed(2)}%</span></div>`).join('')}
        </div>
        <div><div style="color:var(--red);font-size:var(--fs-xs);font-weight:700;margin-bottom:6px">EN ÇOK DÜŞEN</div>
          ${(mv.losers||[]).map(l => `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs);font-family:'JetBrains Mono',monospace"><span class="clk-t" onclick="loadTicker('${esc(l.ticker)}')">${esc(l.ticker)}</span><span style="color:var(--red)">${(l.change_pct||0).toFixed(2)}%</span></div>`).join('')}
        </div>
      </div>
    </div></div>`;
  }

  // KAP highlights
  if ((c.kap_highlights||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">📢 KAP Öne Çıkanlar</span></div><div class="card-b">
      ${c.kap_highlights.map(k => `<div style="padding:6px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs)">
        <span class="clk-t" style="font-family:'JetBrains Mono',monospace;font-weight:700" onclick="loadTicker('${esc(k.ticker)}')">${esc(k.ticker||'-')}</span>
        <span style="color:var(--ylw);font-size:10px;margin-left:6px;padding:1px 6px;background:var(--bg2);border-radius:3px">${esc(k.type||'')}</span>
        <span style="color:var(--t2);margin-left:8px">${esc(k.subject||'')}</span>
      </div>`).join('')}
    </div></div>`;
  }

  // Pre-alarms
  if ((c.pre_alarms||[]).length) {
    h += `<div class="card" style="margin-bottom:16px"><div class="card-h"><span class="card-t">⚠️ Pre-Alarm Adayları</span></div><div class="card-b">
      ${c.pre_alarms.map(p => `<div style="padding:6px 0;border-bottom:1px solid var(--bdr);font-size:var(--fs-xs)">
        <span class="clk-t" style="font-family:'JetBrains Mono',monospace;font-weight:700" onclick="loadTicker('${esc(p.symbol)}')">${esc(p.symbol)}</span>
        <span style="color:var(--t3);margin-left:6px">skor ${p.score!=null?p.score.toFixed(0):'-'}</span>
        ${(p.hints||[]).slice(0,1).map(hh => `<span style="color:var(--t2);margin-left:8px">${esc(hh)}</span>`).join('')}
      </div>`).join('')}
    </div></div>`;
  }
  h += '</div>';
  return h;
}

function _bultenStatCell(label, val, color){
  return `<div style="background:var(--bg2);border-radius:6px;padding:10px;text-align:center">
    <div style="color:var(--t3);font-size:10px;text-transform:uppercase;letter-spacing:0.5px">${esc(label)}</div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:var(--fs-lg);font-weight:700;color:${color||'var(--t1)'};margin-top:4px">${val!=null?val:'—'}</div>
  </div>`;
}

async function _loadBultenDate(date){
  if (!date) return;
  const pg = $('pg-bulten');
  pg.innerHTML = `<div class="ld" style="padding:40px 20px"><div class="sp"></div></div>`;
  try {
    const r = await api('/api/daily-brief/' + encodeURIComponent(date));
    const archive = await api('/api/daily-brief/history?limit=30').catch(() => ({dates:[]}));
    if (r && r.bulletin) {
      pg.innerHTML = _renderBultenContent(r.bulletin, archive.dates || []);
    } else {
      pg.innerHTML = `<div style="padding:24px;color:var(--t3)">Bu tarih için bülten yok.</div>`;
    }
  } catch (e) {
    pg.innerHTML = `<div style="padding:24px;color:var(--red)">Bülten alınamadı: ${esc(e.message||'')}</div>`;
  }
}

window._regenBulletin = async function(){
  const pg = $('pg-bulten');
  pg.innerHTML = `<div class="ld" style="padding:40px 20px"><div class="sp"></div><div class="ld-t" style="margin-top:12px">Bülten oluşturuluyor…</div></div>`;
  try {
    await api('/api/daily-brief/regenerate', {method: 'POST'});
    renderBultenPage();
  } catch (e) {
    pg.innerHTML = `<div style="padding:24px;color:var(--red)">Oluşturulamadı: ${esc(e.message||'')}</div>`;
  }
};
