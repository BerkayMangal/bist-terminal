// Phase A.10 Step 2-A.2 UX safety — bwComputeDiff unit tests.
// Run with: node tests/test_bw_diff.js
// (No browser needed — function is pure data transform.)

// Inline copy of bwComputeDiff (must stay in sync with terminal.js)
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

let pass = 0, fail = 0;
function eq(actual, expected, msg){
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if(ok){ pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.log(`  ✗ ${msg}\n    expected: ${JSON.stringify(expected)}\n    got:      ${JSON.stringify(actual)}`); }
}

console.log('\n── Test 1: empty old + empty new ──');
let d = bwComputeDiff({items:[]}, {items:[]});
eq(d.new_count, 0, 'no new');
eq(d.removed_count, 0, 'no removed');

console.log('\n── Test 2: 1 added, 1 removed ──');
d = bwComputeDiff(
  {items:[{symbol:'A', score:50, zone:'EARLY'}]},
  {items:[{symbol:'B', score:60, zone:'CONFIRMED'}]}
);
eq(d.new_count, 1, 'B is new');
eq(d.removed_count, 1, 'A is removed');
eq(d.new_symbols, ['B'], 'new_symbols');
eq(d.removed_symbols, ['A'], 'removed_symbols');

console.log('\n── Test 3: same symbols, score change ≥5 ──');
d = bwComputeDiff(
  {items:[{symbol:'X', score:50, zone:'EARLY'}]},
  {items:[{symbol:'X', score:58, zone:'EARLY'}]}
);
eq(d.score_changed_count, 1, 'score changed');
eq(d.new_count, 0, 'no new');
eq(d.removed_count, 0, 'no removed');

console.log('\n── Test 4: same symbols, score change <5 (no count) ──');
d = bwComputeDiff(
  {items:[{symbol:'X', score:50, zone:'EARLY'}]},
  {items:[{symbol:'X', score:53, zone:'EARLY'}]}
);
eq(d.score_changed_count, 0, 'within threshold');

console.log('\n── Test 5: zone upgrade (EARLY → CONFIRMED) ──');
d = bwComputeDiff(
  {items:[{symbol:'X', score:50, zone:'EARLY'}]},
  {items:[{symbol:'X', score:55, zone:'CONFIRMED'}]}
);
eq(d.upgraded_count, 1, 'upgraded');
eq(d.downgraded_count, 0, 'not downgraded');

console.log('\n── Test 6: zone downgrade (CONVICTION → CONFIRMED) ──');
d = bwComputeDiff(
  {items:[{symbol:'X', score:80, zone:'CONVICTION'}]},
  {items:[{symbol:'X', score:65, zone:'CONFIRMED'}]}
);
eq(d.downgraded_count, 1, 'downgraded');
eq(d.upgraded_count, 0, 'not upgraded');

console.log('\n── Test 7: realistic mix ──');
d = bwComputeDiff(
  {items:[
    {symbol:'A', score:60, zone:'CONFIRMED'},
    {symbol:'B', score:50, zone:'EARLY'},
    {symbol:'C', score:75, zone:'CONFIRMED'},
    {symbol:'D', score:80, zone:'CONVICTION'},
  ]},
  {items:[
    {symbol:'A', score:62, zone:'CONFIRMED'},      // unchanged (within 5)
    {symbol:'B', score:71, zone:'CONFIRMED'},      // upgraded EARLY→CONFIRMED + score↑
    {symbol:'D', score:65, zone:'CONFIRMED'},      // downgraded CONV→CONF + score↓
    {symbol:'E', score:55, zone:'EARLY'},          // new
    // C removed
  ]}
);
eq(d.new_count, 1, '1 new (E)');
eq(d.removed_count, 1, '1 removed (C)');
eq(d.score_changed_count, 2, 'B and D score changes');
eq(d.upgraded_count, 1, 'B upgraded');
eq(d.downgraded_count, 1, 'D downgraded');

console.log('\n── Test 8: nullish handling ──');
d = bwComputeDiff(null, {items:[{symbol:'A', score:50, zone:'EARLY'}]});
eq(d.new_count, 1, 'null old → all new');

d = bwComputeDiff({items:[{symbol:'A', score:50, zone:'EARLY'}]}, null);
eq(d.removed_count, 1, 'null new → all removed');

d = bwComputeDiff(undefined, undefined);
eq(d.new_count, 0, 'undefined safe');

console.log('\n── Test 9: identical lists ──');
const items = [
  {symbol:'A', score:50, zone:'EARLY'},
  {symbol:'B', score:60, zone:'CONFIRMED'},
];
d = bwComputeDiff({items}, {items});
eq(d.new_count, 0, 'no diff');
eq(d.removed_count, 0, 'no diff');
eq(d.score_changed_count, 0, 'no score change');
eq(d.upgraded_count, 0, 'no upgrade');
eq(d.downgraded_count, 0, 'no downgrade');

console.log(`\n══════════════════════════════════════════`);
console.log(` ${pass} passed, ${fail} failed`);
console.log(`══════════════════════════════════════════`);
process.exit(fail > 0 ? 1 : 0);
