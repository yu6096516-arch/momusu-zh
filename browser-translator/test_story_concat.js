'use strict';
const fs = require('fs'), assert = require('assert/strict'), api = require('./monmusu_cn_story_index.js');
const {monmusuCnCreateBridge} = require('./monmusu_cn_shim.js');
const doc = JSON.parse(fs.readFileSync('resources/monmusu_cn_story_index.json', 'utf8'));
const index = api.createIndex(doc), scene = '1101011nml';
// Read the rows from the SAME revision the index was built from.  The bundled
// ref/ snapshot is a frozen older corpus: after a translation update its
// wording no longer matches the index, and pinning it makes this test fail on
// every legitimate corpus change.  concatenation.scenes is exactly the data
// the production matcher consumes.
const sceneIdx = doc.scenarios.indexOf(scene);
const flat = doc.concatenation.scenes[sceneIdx];
const rows = {};
for (let i = 0; i < flat.length; i += 2) {
  rows[doc.strings[flat[i]]] = flat[i + 1] >= 0 ? doc.strings[flat[i + 1]] : '';
}
let checks = 0;
function must(ok, message) { assert.ok(ok, message); checks++; }
function lookup(idx, src, sc = scene) {
  const ctx = api.createSceneContext(idx);
  if (sc) ctx.beginParsing(sc);
  ctx.prepare(src);
  return idx.lookup(src, ctx);
}
const groups = [
  ['自由な少年との', 'リン・クロミに加え', 'いやいやながら幼馴染み'],
  ['違う違う違う、', '私は！', 'ケンタウロス娘じゃ！', 'なーい！！']
].map(starts => starts.map(s => Object.keys(rows).find(k => k.startsWith(s))));
const examples = [];
for (const keys of groups) {
  for (let n = 1; n <= keys.length; n++) {
    for (const sep of ['', '\n', ' ', '\u3000', ' \r\n\u3000', '\t', '\\n']) {
      const input = keys.slice(0, n).join(sep), expected = keys.slice(0, n).map(k => rows[k]).join(sep);
      const actual = lookup(index, input);
      must(actual === expected, 'cumulative composition ' + n + ' sep=' + JSON.stringify(sep));
      const detail = index.concatStats.last;
      if (n > 1) must(detail && detail.reason === 'hit' && detail.segments.length === n, 'exact entry boundaries');
      if (sep === '' && n === keys.length) examples.push({input, actual, scene,
        segments: detail.segments, provenance: 'user-confirmed grouping; joined test input'});
    }
  }
}
const first = groups[0][0], last = groups[0][2];
must(lookup(index, first.slice(0, Math.floor(first.length / 2))) === '', 'unfinished half entry');
must(lookup(index, first + '未知の割り込み文字列' + last) === '', 'unknown middle');
must(lookup(index, groups[0].join(''), '1100011nml') === '', 'wrong scene blocks globally available pieces');
must(lookup(index, groups[0].join(''), '') === '', 'no scene is conservative');
must(lookup(index, groups[1].map(k => rows[k]).join('')) === '', 'already Chinese');
must(lookup(index, 'あ'.repeat(1000000)) === '', 'hostile long input');
must(lookup(index, groups[1][3].repeat(200)) === '', 'repeated short source bounded');
must(index.concatStats.last.reason === 'budget-exceeded', 'segment budget enforced');
must(lookup(index, '……'.repeat(4000)) === '', 'punctuation cannot be a component');
must(lookup(index, '私は！') === '', 'cannot autocomplete a missing required fullwidth space');

// Synthetic adversarial data still invokes the unmodified PRODUCTION createIndex/lookup.
function fixture(scenes, globals = {}, names = []) {
  const strings = [''], ids = new Map([['', 0]]);
  function sid(s) { if (!ids.has(s)) { ids.set(s, strings.length); strings.push(s); } return ids.get(s); }
  const flat = scenes.map(data => Object.entries(data).flatMap(([k, v]) => [sid(k),
    Array.isArray(v) ? v.map(sid) : v ? sid(v) : -1]));
  const globalRows = Object.entries(globals).map(([k, v]) => [sid(k), sid(v)]);
  const half = {global: globalRows, ambiguous: []};
  return api.createIndex({schema: 2, strings, scenarios: scenes.map((_, i) => 'scene' + i),
    names, parse: half, display: half, stats: {}, concatenation: {schema: 1, scenes: flat}});
}
let test = fixture([{'あいう': '甲', 'えおかき': '乙', 'あいうえ': '丙', 'おかき': '丁'}]);
must(lookup(test, 'あいうえおかき', 'scene0') === '', 'different-output prefix ambiguity');
must(test.concatStats.last.reason === 'ambiguous-coverage', 'ambiguity counted');
test = fixture([{'あいう': '甲', 'えおかき': '乙', 'あいうえ': '甲', 'おかき': '乙'}]);
must(lookup(test, 'あいうえおかき', 'scene0') === '', 'even equivalent-output boundary ambiguity is rejected');
test = fixture([{'あいう': ['甲', '丙'], 'えおか': '乙'}]);
must(lookup(test, 'あいうえおか', 'scene0') === '', 'multiple translations of one component');
test = fixture([{'あいう': '甲'}, {'えおか': '乙'}], {'あいう': '甲', 'えおか': '乙'});
must(lookup(test, 'あいうえおか', 'scene0') === '', 'cross-scene pieces cannot borrow global translations');
test = fixture([{'あいう': '甲', 'えおか': '乙'}], {'あいうえおか': '全文優先'});
must(lookup(test, 'あいうえおか', 'scene0') === '全文優先', 'full exact entry wins over splitting');
must(test.concatStats.last === null, 'exact path does not build or query trie');
test = fixture([{'あいう': '甲', 'えおか': '乙'}], {}, [['あいう', '名字']]);
must(lookup(test, 'あいうえおか', 'scene0') === '', 'speaker names excluded from components');
test = fixture([{'あいう': '甲', 'えおか': ''}]);
must(lookup(test, 'あいうえおか', 'scene0') === '', 'missing source translation never partially translates');
test = fixture([{'あいう': '中'.repeat(20000), 'えおか': '文'.repeat(20000)}]);
must(lookup(test, 'あいうえおか', 'scene0') === '' && test.concatStats.last.reason === 'budget-exceeded', 'output allocation limit');
test = fixture([Object.fromEntries(Array.from({length: 50}, (_, i) => ['あ'.repeat(i + 3), '文']))]);
must(lookup(test, 'あ'.repeat(200) + '未知', 'scene0') === '' && test.concatStats.last.reason === 'budget-exceeded', 'backtracking limited');
// A dead shorter prefix must not prevent the unique complete coverage.
test = fixture([{'あいう': '短', 'あいうえ': '長', 'おかき': '後'}]);
must(lookup(test, 'あいうえおかき', 'scene0') === '長後', 'backtrack from incomplete short prefix');
// Context pending-cell evidence is consumed once, never lost by the fallback wrapper.
const pendingCtx = api.createSceneContext(index);
const joined = groups[0].join(''); pendingCtx.prepare(joined);
pendingCtx.setPendingParsedCell(joined, index.resolveSceneId(scene));
must(index.lookup(joined, pendingCtx) === groups[0].map(k => rows[k]).join(''), 'pending-cell scene survives exact miss');

// Actual production Wasm import protocol writes a newly composed result.
const bridge = monmusuCnCreateBridge(); bridge.setStoryIndex(doc); bridge.storyTrace(true);
bridge.setMemory(new WebAssembly.Memory({initial: 4}));
// Use the bridge's public writeChars for the simulated source payload, not a matcher stub.
const memory = new WebAssembly.Memory({initial: 4}); bridge.setMemory(memory);
const view = new DataView(memory.buffer), ptr = 65536, dst = 131072;
bridge.getContext().beginParsing(scene);
for (const keys of groups) {
  for (let n = 1; n <= keys.length; n++) {
    const input = keys.slice(0, n).join(''), expected = keys.slice(0, n).map(k => rows[k]).join('');
    view.setUint32(ptr + 8, input.length, true); bridge.writeChars(ptr + 12, input);
    const length = bridge.handle(ptr, bridge.TAG_TRANSLATE);
    must(length === expected.length, 'composed probe size');
    must(bridge.handle(ptr, bridge.TAG_TRANSLATE | dst) === length, 'composed bridge fill');
    let actual = '';
    for (let i = 0; i < length; i++) actual += String.fromCharCode(view.getUint16(dst + i * 2, true));
    must(actual === expected, 'composed UTF-16 payload has no duplicate append');
  }
}
fs.writeFileSync('work/phase24_story_examples.json', JSON.stringify({checks, browser: 'NOT RUN', examples,
  limits: index.concatLimits, stats: index.concatStats}, null, 2) + '\n');
console.log(JSON.stringify({checks, browser: 'NOT RUN', limits: index.concatLimits}));
