// Phase A.10 Step 2-C — bwBuildShortlist unit tests.
// Run with: node tests/test_bw_shortlist.js
// (No browser needed — function is a pure data transform.)
//
// Inline copy of bwBuildShortlist (must stay in sync with terminal.js)
function bwBuildShortlist(items){
  if(!Array.isArray(items)) return {hazirlananlar:[], atestlenenler:[], late_risk:[]};
  const usable = items.filter(it => (it.data_status || '').toLowerCase() !== 'missing');
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
  const HAZIRLANAN_STATES = new Set(['HAZIRLANIYOR', 'TEYİT BEKLİYOR']);
  const ATESLENEN_STATES = new Set(['ATEŞLENDİ']);
  const LATE_STATES = new Set(['GEÇ KALMIŞ OLABİLİR']);
  const hazirlanan_pool = usable.filter(it => HAZIRLANAN_STATES.has(it.readiness));
  const ateslenen_pool = usable.filter(it => ATESLENEN_STATES.has(it.readiness));
  const late_pool = usable.filter(it => LATE_STATES.has(it.readiness));
  const primaryFilter = it => (it.engine_conflict_matrix?.confidence_tier || '').toUpperCase() !== 'LOW';
  const haz_primary = hazirlanan_pool.filter(primaryFilter).sort(_sortKey);
  const ate_primary = ateslenen_pool.filter(primaryFilter).sort(_sortKey);
  const late_sorted = late_pool.slice().sort(_sortKey);
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
  return {
    hazirlananlar: diversify(haz_primary, 3, 5),
    atestlenenler: diversify(ate_primary, 3, 5),
    late_risk: diversify(late_sorted, 3, 5),
  };
}

// Test framework
let pass = 0, fail = 0;
function eq(actual, expected, msg){
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if(ok){ pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.log(`  ✗ ${msg}\n    expected: ${JSON.stringify(expected)}\n    got:      ${JSON.stringify(actual)}`); }
}
function truthy(actual, msg){
  if(actual){ pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.log(`  ✗ ${msg}: got ${JSON.stringify(actual)}`); }
}

// Helper to build a fake BullWatch item
function _it(symbol, opts){
  return {
    symbol,
    score: opts.score ?? 50,
    zone: opts.zone || 'EARLY',
    readiness: opts.readiness || 'İZLEMEDE',
    sector_tr: opts.sector_tr || 'Endüstri',
    data_status: opts.data_status ?? 'live',
    engine_conflict_matrix: {
      confidence_tier: opts.ct || 'MEDIUM',
      evidence_depth_count: opts.depth ?? 2,
    },
  };
}

console.log('\n=== Phase A.10 Step 2-C — bwBuildShortlist tests ===\n');

// 1. Empty / invalid input
console.log('1. Empty input handling');
{
  const r = bwBuildShortlist([]);
  eq(r.hazirlananlar, [], 'empty array → empty hazirlananlar');
  eq(r.atestlenenler, [], 'empty array → empty atestlenenler');
  eq(r.late_risk,    [], 'empty array → empty late_risk');
}
{
  const r = bwBuildShortlist(null);
  eq(r.hazirlananlar, [], 'null input → empty groups');
}

// 2. Bucket assignment
console.log('\n2. Bucket assignment by readiness');
{
  const items = [
    _it('A1', {readiness: 'HAZIRLANIYOR', score: 60}),
    _it('A2', {readiness: 'TEYİT BEKLİYOR', score: 55}),
    _it('B1', {readiness: 'ATEŞLENDİ', score: 70}),
    _it('C1', {readiness: 'GEÇ KALMIŞ OLABİLİR', score: 65}),
    _it('Z1', {readiness: 'İZLEMEDE', score: 40}),
  ];
  const r = bwBuildShortlist(items);
  eq(r.hazirlananlar.map(i => i.symbol), ['A1', 'A2'], 'HAZIRLANIYOR + TEYİT BEKLİYOR → hazirlananlar');
  eq(r.atestlenenler.map(i => i.symbol), ['B1'], 'ATEŞLENDİ → atestlenenler');
  eq(r.late_risk.map(i => i.symbol),     ['C1'], 'GEÇ KALMIŞ OLABİLİR → late_risk');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'Z1') === -1, 'İZLEMEDE excluded from all groups');
}

// 3. data_status=missing exclusion
console.log('\n3. data_status=missing exclusion');
{
  const items = [
    _it('OK', {readiness: 'HAZIRLANIYOR', score: 60, data_status: 'live'}),
    _it('NO', {readiness: 'HAZIRLANIYOR', score: 70, data_status: 'missing'}),
    _it('PRTL', {readiness: 'HAZIRLANIYOR', score: 65, data_status: 'partial'}),
  ];
  const r = bwBuildShortlist(items);
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'NO') === -1, 'data_status=missing excluded');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'OK') !== -1, 'data_status=live included');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'PRTL') !== -1, 'data_status=partial still allowed');
}

// 4. LOW confidence filtering for primary groups
console.log('\n4. LOW confidence excluded from primary groups');
{
  const items = [
    _it('HI',  {readiness: 'HAZIRLANIYOR', score: 60, ct: 'HIGH'}),
    _it('MED', {readiness: 'HAZIRLANIYOR', score: 65, ct: 'MEDIUM'}),
    _it('LO',  {readiness: 'HAZIRLANIYOR', score: 70, ct: 'LOW'}),  // higher score, but LOW
  ];
  const r = bwBuildShortlist(items);
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'LO') === -1, 'LOW excluded from primary');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'MED') !== -1, 'MEDIUM allowed in primary');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'HI') !== -1, 'HIGH allowed in primary');
}
{
  // Late-risk allows LOW
  const items = [
    _it('LATE_LO', {readiness: 'GEÇ KALMIŞ OLABİLİR', score: 50, ct: 'LOW'}),
  ];
  const r = bwBuildShortlist(items);
  eq(r.late_risk.length, 1, 'LOW confidence allowed in late_risk warning group');
}

// 5. Sort: score desc → confidence_tier desc → depth desc
console.log('\n5. Sort order');
{
  const items = [
    _it('S60_HIGH', {readiness: 'HAZIRLANIYOR', score: 60, ct: 'HIGH', depth: 2}),
    _it('S60_MED',  {readiness: 'HAZIRLANIYOR', score: 60, ct: 'MEDIUM', depth: 4}),
    _it('S70_LOW',  {readiness: 'HAZIRLANIYOR', score: 70, ct: 'HIGH', depth: 1}),
    // Different sectors so diversity doesn't cap them
    _it('S55_HIGH', {readiness: 'HAZIRLANIYOR', score: 55, ct: 'HIGH', depth: 5,
                    sector_tr: 'Madencilik'}),
  ];
  const r = bwBuildShortlist(items);
  // Note: 'S70_LOW' has ct:HIGH (the var name is misleading) — score 70 wins
  eq(r.hazirlananlar.map(i => i.symbol),
     ['S70_LOW', 'S60_HIGH', 'S60_MED', 'S55_HIGH'],
     'sorted by score desc → ct desc → depth desc');
}

// 6. Per-group cap (5 items)
console.log('\n6. Group cap at 5');
{
  const items = [];
  for(let i = 0; i < 10; i++){
    items.push(_it('A' + i, {
      readiness: 'HAZIRLANIYOR', score: 60 - i,
      sector_tr: 'Sec' + i,  // unique sectors so diversity won't trim
    }));
  }
  const r = bwBuildShortlist(items);
  eq(r.hazirlananlar.length, 5, '10 inputs → 5 per group cap');
}

// 7. Sector diversity cap (max 3 per sector_tr per group)
console.log('\n7. Sector diversity cap');
{
  const items = [
    _it('E1', {readiness: 'HAZIRLANIYOR', score: 90, sector_tr: 'Endüstri'}),
    _it('E2', {readiness: 'HAZIRLANIYOR', score: 80, sector_tr: 'Endüstri'}),
    _it('E3', {readiness: 'HAZIRLANIYOR', score: 70, sector_tr: 'Endüstri'}),
    _it('E4', {readiness: 'HAZIRLANIYOR', score: 60, sector_tr: 'Endüstri'}),  // over cap
    _it('M1', {readiness: 'HAZIRLANIYOR', score: 55, sector_tr: 'Madencilik'}),
  ];
  const r = bwBuildShortlist(items);
  // E4 must be filtered out (4th Endüstri)
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'E4') === -1, 'E4 (4th Endüstri) excluded by sector cap');
  truthy(r.hazirlananlar.findIndex(i => i.symbol === 'M1') !== -1, 'M1 (different sector) included');
}

// 8. Independence — full grid is NOT modified by shortlist
console.log('\n8. Function does not mutate input array');
{
  const original = [
    _it('A', {readiness: 'HAZIRLANIYOR', score: 60}),
    _it('B', {readiness: 'ATEŞLENDİ', score: 70}),
    _it('C', {readiness: 'İZLEMEDE', score: 40}),
  ];
  const lengthBefore = original.length;
  bwBuildShortlist(original);
  eq(original.length, lengthBefore, 'input array length unchanged');
}

// 9. Late-risk renders separately even if it would also qualify ignition
console.log('\n9. Late-risk priority');
{
  // Backend gives it readiness=GEÇ KALMIŞ OLABİLİR; frontend trusts that
  const items = [
    _it('LATE', {readiness: 'GEÇ KALMIŞ OLABİLİR', score: 75, ct: 'HIGH'}),
    _it('FIRE', {readiness: 'ATEŞLENDİ', score: 70, ct: 'HIGH'}),
  ];
  const r = bwBuildShortlist(items);
  eq(r.late_risk.map(i => i.symbol), ['LATE'], 'late_risk only contains GEÇ KALMIŞ OLABİLİR');
  eq(r.atestlenenler.map(i => i.symbol), ['FIRE'], 'atestlenenler only contains ATEŞLENDİ');
}

// 10. Total cap respected (max 12-15)
console.log('\n10. Total cap (5+5+5 = 15)');
{
  const items = [];
  // Make 8 of each readiness state
  for(let i = 0; i < 8; i++){
    items.push(_it('H' + i, {readiness: 'HAZIRLANIYOR', score: 90-i, sector_tr: 'S'+i}));
    items.push(_it('A' + i, {readiness: 'ATEŞLENDİ', score: 80-i, sector_tr: 'T'+i}));
    items.push(_it('L' + i, {readiness: 'GEÇ KALMIŞ OLABİLİR', score: 70-i, sector_tr: 'U'+i}));
  }
  const r = bwBuildShortlist(items);
  const total = r.hazirlananlar.length + r.atestlenenler.length + r.late_risk.length;
  truthy(total <= 15, `total ≤ 15 (got ${total})`);
  eq(r.hazirlananlar.length, 5, 'hazirlananlar capped at 5');
  eq(r.atestlenenler.length, 5, 'atestlenenler capped at 5');
  eq(r.late_risk.length,    5, 'late_risk capped at 5');
}

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
