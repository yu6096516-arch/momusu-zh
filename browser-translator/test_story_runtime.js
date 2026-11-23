/* Phase 23: actual production index + JS import bridge, no replacement matcher.
 * Inputs marked hypothesis are reconstructed probes, NOT captured browser text.
 * This test deliberately does not assert that unsupported probes are correct.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const crypto = require('crypto');
const vm = require('vm');
const api = require('./monmusu_cn_story_index.js');
const {monmusuCnCreateBridge} = require('./monmusu_cn_shim.js');
const root = __dirname, scene = '1101011nml';
const doc = JSON.parse(fs.readFileSync(path.join(root, 'resources/monmusu_cn_story_index.json'), 'utf8'));
// The ref/ snapshot is a frozen older corpus.  After a translation update its
// wording no longer matches the production index, so read the rows from the
// same revision the index was built from (concatenation.scenes is exactly what
// the production matcher consumes).
const sourceFile = doc.scenarioSources[scene][0];
const flat = doc.concatenation.scenes[doc.scenarios.indexOf(scene)];
const rows = {};
for (let i = 0; i < flat.length; i += 2) {
  rows[doc.strings[flat[i]]] = flat[i + 1] >= 0 ? doc.strings[flat[i + 1]] : '';
}
const raw = Buffer.from(JSON.stringify(rows), 'utf8');
const index = api.createIndex(doc), si = index.resolveSceneId(scene);
const starts = ['自由な少年との', 'リン・クロミに加え', 'いやいやながら幼馴染み',
  '違う違う違う、', '私は！', 'ケンタウロス娘じゃ！', 'なーい！！'];
const keys = starts.map(start => {
  const hits = Object.keys(rows).filter(k => k.startsWith(start));
  assert.equal(hits.length, 1, start);
  return hits[0];
});
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
function lookup(source, context = scene) {
  const ctx = api.createSceneContext(index);
  if (context) ctx.beginParsing(context);
  ctx.prepare(source);
  return index.lookup(source, ctx);
}
function tableEvidence(key) {
  return ['parse', 'display'].map(half => {
    const g = doc[half].global.find(r => doc.strings[r[0]] === key);
    const a = doc[half].ambiguous.find(r => doc.strings[r.k] === key);
    return {half, global: g || null, ambiguous: a || null,
      sceneTranslation: a ? (a.s.find(r => r[0] === si) || null) : null};
  });
}
const evidence = keys.map(key => {
  check(lookup(key) === api.normalizeKey(rows[key]), 'original source: ' + key);
  check(!index.isKnownStoryValue(key), 'guard must not swallow source');
  return {source: key, translation: rows[key], stringId: doc.strings.indexOf(key),
    scene, sceneIndex: si, sourceFile, tables: tableEvidence(key),
    knownValue: index.isKnownStoryValue(key), actual: lookup(key), withoutScene: lookup(key, '')};
});
const probes = [];
function probe(label, source, context = scene) {
  const actual = lookup(source, context);
  probes.push({label, source, scene: context, provenance: 'hypothesis-not-browser-capture',
    actual, hit: !!actual, knownValue: index.isKnownStoryValue(source)});
}
for (const key of keys) {
  if (key.includes('\n')) {
    for (const [label, newline] of [['crlf', '\r\n'], ['cr', '\r'], ['literal-newline', '\\n']]) {
      const src = key.replace(/\n/g, newline);
      check(lookup(src) === rows[key], label);
      probe(label, src);
    }
  }
  probe('trailing-space', key + ' ');
  probe('fullwidth-space', key.replace(/\u3000/g, ' '));
  probe('punctuation-width', key.replace(/！/g, '!').replace(/３/g, '3'));
}
for (const separator of ['', '\n', ' ']) {
  probe('narration-joined-' + JSON.stringify(separator), keys.slice(0, 3).join(separator));
  probe('suzu-last-three-joined-' + JSON.stringify(separator), keys.slice(4).join(separator));
  probe('suzu-all-four-joined-' + JSON.stringify(separator), keys.slice(3).join(separator));
}
probe('wrong-scene-original', keys[0], '1100011nml');
check(lookup('鈴') === '' && !index.isKnownStoryValue('鈴'), '鈴 has no upstream name mapping; no invented translation');
check(lookup('スズ') === '铃', 'original speaker name translates');

// Exercise the actual production JS probe/fill protocol in linear memory.
const bridge = monmusuCnCreateBridge();
bridge.setStoryIndex(doc);
bridge.setUiIndex(JSON.parse(fs.readFileSync(path.join(root, 'resources/monmusu_cn_ui_index.json'), 'utf8')));
bridge.fontDisable();
const memory = new WebAssembly.Memory({initial: 4});
bridge.setMemory(memory);
const dv = new DataView(memory.buffer), srcPtr = 0x10000, dst = 0x20000;
function put(text) {
  dv.setUint32(srcPtr + 8, text.length, true);
  for (let i = 0; i < text.length; i++) dv.setUint16(srcPtr + 12 + i * 2, text.charCodeAt(i), true);
}
function written(n) {
  let text = '';
  for (let i = 0; i < n; i++) text += String.fromCharCode(dv.getUint16(dst + i * 2, true));
  return text;
}
bridge.getContext().beginParsing(scene);
put(keys[0]);
bridge.handle(srcPtr, bridge.TAG_TRANSLATE);
check(bridge.storyTrace().length === 0, 'trace disabled by default');
bridge.storyTrace(true);
for (const key of keys) {
  put(key);
  const n = bridge.handle(srcPtr, bridge.TAG_TRANSLATE);
  check(n === rows[key].length, 'probe length');
  check(bridge.handle(srcPtr, bridge.TAG_TRANSLATE | dst) === n, 'fill count');
  check(written(n) === rows[key], 'actual UTF-16 buffer');
  const rec = bridge.storyTrace().at(-1);
  check(rec.enteredStoryQuery && rec.write === 'buffer-written' && rec.finalDisplay === 'NOT_OBSERVED', 'trace write evidence');
}
put(keys[0]);
bridge.handle(srcPtr, bridge.TAG_TRANSLATE);
bridge.handle(srcPtr, bridge.TAG_TRANSLATE | 16); // invalid destination, cannot write
check(bridge.storyTrace().at(-1).write === 'failed', 'write failure recorded');
put(keys.slice(0, 3).join('\n'));
bridge.handle(srcPtr, bridge.TAG_TRANSLATE);
check(bridge.storyTrace().at(-1).hit &&
  bridge.storyTrace().at(-1).concatenation.reason === 'hit', 'joined story hit and boundaries recorded');
put(keys[3]);
bridge.handle(srcPtr, bridge.UI_TAG);
const uiRow = bridge.storyTrace().at(-1);
check(uiRow.hook === 'tmp-ui' && !uiRow.enteredStoryQuery, 'TMP UI route is separate from story');
const uiActual = uiRow.translation;
if (uiActual) {
  bridge.handle(dst, bridge.UI_FILL);
  check(written(uiActual.length) === uiActual && bridge.storyTrace().at(-1).write === 'buffer-written', 'UI fill traced');
}
// Simulated prefix updates demonstrate capture only, not the game's typewriter implementation.
for (let i = 1; i <= 3; i++) {
  put(keys[3].slice(0, i)); bridge.handle(srcPtr, bridge.UI_TAG);
}
check(bridge.storyTrace().slice(-3).map(r => r.source.length).join(',') === '1,2,3', 'prefix updates retained');
put(scene);
bridge.handleContext(srcPtr, bridge.TAG_CONTEXT | bridge.CMD.PLAYBACK_BEGIN);
check(bridge.storyTrace().at(-1).hook === 'context', 'existing context import traced');
const snapshots = bridge.storyTrace(); snapshots[0].source = 'mutated';
check(bridge.storyTrace()[0].source !== 'mutated', 'diagnostic snapshots isolated');
for (let i = 0; i < 305; i++) { put(keys[0]); bridge.handle(srcPtr, bridge.TAG_TRANSLATE); }
check(bridge.storyTrace().length === 300, 'trace bounded');
bridge.storyTraceClear(); bridge.storyTrace(false);
put(keys[0]); bridge.handle(srcPtr, bridge.TAG_TRANSLATE);
check(bridge.storyTrace().length === 0, 'trace off and clear');
const empty = monmusuCnCreateBridge(); empty.setMemory(memory); empty.storyTrace(true);
empty.handle(srcPtr, empty.TAG_TRANSLATE);
check(empty.storyTrace().at(-1).reason === 'no-translation-table', 'missing index visible');

// Validate the paste-in diagnostic against both current and previous API shapes.
const diagnostic = fs.readFileSync(path.join(root, 'phase23_browser_diagnostics.js'), 'utf8');
for (const legacy of [false, true]) {
  const b = legacy ? {...bridge, storyTrace: undefined} : bridge;
  const sandbox = {window: {__monmusuCnBridge: b}, location: {href: 'https://example.invalid/game'}};
  const beforeLookup = b.getIndex().lookup;
  vm.runInNewContext(diagnostic, sandbox);
  put(keys[0]); b.handle(srcPtr, b.TAG_TRANSLATE);
  const captured = JSON.parse(sandbox.window.__monmusuCnStoryDiag.dump());
  check(captured.story.at(-1).source === keys[0] && captured.story.at(-1).hit, 'paste-in diagnostic captures query');
  sandbox.window.__monmusuCnStoryDiag.stop();
  check(b.getIndex().lookup === beforeLookup, 'paste-in diagnostic restores lookup');
}

const report = {scene, sourceFile, sourceSha256: crypto.createHash('sha256').update(raw).digest('hex'),
  commit: doc.commit, corpusSha256: doc.corpusSha256, indexSha256: doc.indexSha256,
  browser: 'NOT RUN', runtimeStringCaptured: false, checks, evidence, probes,
  tmpUiRoute: {source: keys[3], actual: uiActual, enteredStoryQuery: false},
  speaker: {source: 'スズ', actual: lookup('スズ'), suppliedDisplayName: '鈴',
    suppliedDisplayNameActual: lookup('鈴'), suppliedDisplayNameKnown: index.isKnownStoryValue('鈴')},
  limitation: 'Phase 24 composition support enabled. Probe separators are reconstructed; browser NOT RUN.'};
fs.writeFileSync(path.join(root, 'work/phase23_story_evidence.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({checks, originals: evidence.map(r => ({source: r.source, actual: r.actual})),
  probes: probes.filter(r => r.label.includes('joined')), tmpUi: report.tmpUiRoute, browser: report.browser}, null, 2));
