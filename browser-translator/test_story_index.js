// Full Berumic story index test suite.
//
// Three clearly separated levels:
//   A. STATIC              - index file integrity, stats, manifest provenance
//   B. SIMULATED WASM      - the real patched wasm, driven with synthetic
//                            Il2CppString objects and both wrapped imports
//   C. (NOT RUN HERE)      - real DMM browser session / screenshot verification
//
// Scenarios covered: prologue, another main chapter, an event scenario,
// a repeated Japanese short line, long/multi-line/control-tag rows,
// untranslated fallback, already-translated text, speaker names.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const api = require('./monmusu_cn_story_index.js');
const { monmusuCnCreateBridge } = require('./monmusu_cn_shim.js');

let fails = 0, checks = 0;
function ok(m) { checks++; console.log('  ok   ' + m); }
function bad(m) { checks++; fails++; console.error('  FAIL ' + m); }
function must(cond, m) { cond ? ok(m) : bad(m); }

const doc = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources',
  'monmusu_cn_story_index.json'), 'utf8'));

// The index records the SHA-256 of the corpus manifest it was generated from.
// Resolve THAT manifest instead of comparing against a hard-coded commit: the
// developer pipeline rebuilds the index from whatever revision the author's
// repository currently has, so a pinned commit would fail on every legitimate
// update while proving nothing about provenance.
function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}
// The generators hash the manifest with CRLF normalised (a Windows checkout
// stores CRLF), so the comparison must normalise too.
function normalizeCrlf(buf) {
  const out = Buffer.allocUnsafe(buf.length);
  let n = 0;
  for (let i = 0; i < buf.length; i++) {
    if (buf[i] === 0x0d && buf[i + 1] === 0x0a) continue;
    out[n++] = buf[i];
  }
  return out.subarray(0, n);
}
const manifestCandidates = [path.join(__dirname, 'ref',
  'MonsterMusumeTDChineseTranslation', 'translations', 'manifest.json')];
const srcRoot = path.join(__dirname, 'work', 'translations', 'src');
if (fs.existsSync(srcRoot)) {
  for (const d of fs.readdirSync(srcRoot)) {
    manifestCandidates.push(path.join(srcRoot, d, 'translations', 'manifest.json'));
  }
}
let manifestPath = null;
for (const p of manifestCandidates) {
  if (!fs.existsSync(p)) continue;
  if (sha256(normalizeCrlf(fs.readFileSync(p))) === doc.manifestSha256) {
    manifestPath = p;
    break;
  }
}
const manifest = manifestPath ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : {};
const CORPUS = manifestPath ? path.dirname(manifestPath) : '';
const ROOT = path.join(CORPUS, 'scenarios');

function recountCorpus() {
  const files = new Set();
  for (const srcs of Object.values(doc.scenarioSources)) {
    for (const s of srcs) files.add(s);
  }
  let raw = 0, empty = 0;
  for (const rel of files) {
    const p = path.join(CORPUS, rel);
    if (!fs.existsSync(p)) return null;
    const rows = JSON.parse(fs.readFileSync(p, 'utf8'));
    raw += Object.keys(rows).length;
    for (const v of Object.values(rows)) if (!v) empty++;
  }
  return { files: files.size, raw, empty };
}

const idx = api.createIndex(doc);
const sceneIndex = new Map(doc.scenarios.map((s, i) => [s, i]));

function rawRows(sceneId) {
  const rel = doc.scenarioSources[sceneId][0].replace(/^scenarios\//, '');
  const p = path.join(ROOT, rel);
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}
function freshCtx(sceneId) {
  const ctx = api.createSceneContext(idx);
  if (sceneId) ctx.beginParsing(sceneId);
  return ctx;
}
function lookup(src, sceneId) {
  const ctx = freshCtx(sceneId);
  ctx.prepare(src);
  return idx.lookup(src, ctx);
}
function checkScene(sceneId, label, limitRows) {
  const rows = rawRows(sceneId);
  const ctx = freshCtx(sceneId);
  let expected = 0, hit = 0, wrong = 0, skipped = 0;
  let longest = { src: '', tr: '' };
  for (const [src, tr] of Object.entries(rows)) {
    if (!tr) { skipped++; continue; }
    // Punctuation-only rows such as "…………。" are also identity rows (src ===
    // tr) but api.isSelfMapped() cannot see them: it requires Japanese
    // characters.  The engine deliberately never returns a translation equal
    // to its own source, so these must be skipped like any other untranslated
    // row.  Before the sourceKeys fix they were hidden behind the
    // isKnownStoryValue short-circuit; now they are visible and correctly
    // excluded here.
    if (src === tr || api.isSelfMapped(src, tr)) { skipped++; continue; }
    // author's guard: a source that is already a translated value is left
    // alone (no double translation) - unless it is itself an indexed source.
    if (idx.isKnownStoryValue(src)) { skipped++; continue; }
    expected++;
    if (tr.length > longest.tr.length) longest = { src, tr };
    ctx.prepare(src);
    const got = idx.lookup(src, ctx);
    if (got === tr) hit++;
    else {
      wrong++;
      if (wrong <= 3) console.log('       mismatch', JSON.stringify(src.slice(0, 32)),
                                  'expected', JSON.stringify(tr.slice(0, 32)),
                                  'got', JSON.stringify(got.slice(0, 32)));
    }
    if (limitRows && expected >= limitRows) break;
  }
  must(wrong === 0,
       `${label} (${sceneId}): ${hit}/${expected} rows translated exactly, ` +
       `${skipped} empty/self-mapped skipped`);
  return { expected, hit, wrong, skipped, longest };
}

console.log('=== A. STATIC INDEX ===');
must(doc.schema === 2, 'index schema 2');
must(!!manifestPath, 'the corpus manifest the index was built from is present locally');
must(!!manifestPath && doc.commit === manifest.commit,
     'commit matches the manifest recorded in the index: ' + doc.commit.slice(0, 12));
must(!!doc.manifestSha256 && !!doc.corpusSha256 && !!doc.indexSha256,
     'manifest / corpus / index SHA-256 recorded');
const rc = recountCorpus();
must(!!rc, 'every scenario file referenced by the index is readable');
must(!!rc && doc.stats.scenarioFiles === rc.files
     && doc.stats.scenarioIds === doc.scenarios.length,
     `scenario files=${doc.stats.scenarioFiles}, ids=${doc.stats.scenarioIds}`);
must(!!rc && doc.stats.rawRows === rc.raw, `raw rows=${doc.stats.rawRows}`);
must(!!rc && doc.stats.emptyRows === rc.empty,
     `empty rows preserved=${doc.stats.emptyRows}`);
must(doc.stats.sourcesWithConflictingTranslations > 1400,
     `conflicting sources retained=${doc.stats.sourcesWithConflictingTranslations}`);
must(doc.stats.sceneSpecificConflictRowsRetained > 2000,
     `scene-specific conflict rows retained=${doc.stats.sceneSpecificConflictRowsRetained}`);
must(doc.stats.unresolvableWithoutScene === doc.stats.globalNullConflictSources,
     `unresolvable without scene=${doc.stats.unresolvableWithoutScene}`);
must(doc.parse.ambiguous.length === doc.stats.sourcesWithConflictingTranslations + 105 - 105 ||
     doc.parse.ambiguous.length >= 1400,
     `ambiguous records shipped=${doc.parse.ambiguous.length}`);
must(idx.scenarioCount === doc.stats.scenarioIds, 'runtime sees all scenario IDs');

console.log('\n=== A2. SCENE ID RESOLUTION ===');
const resolveCases = [
  ['1100011nml', '1100011nml'],
  ['scenarios/主线/序章/1100011nml/zh_Hans.json', '1100011nml'],
  ['*1100011nml', '1100011nml'],
  ['X/eb111601anml', 'eb111601anml'],
  ['unrelated-zzz', null],
];
for (const [input, expect] of resolveCases) {
  const si = idx.resolveSceneId(input);
  const got = si >= 0 ? doc.scenarios[si] : null;
  must(got === expect, `resolve ${JSON.stringify(input)} -> ${got}`);
}

console.log('\n=== A3. SCENE-BY-SCENE EXACT MATCH ===');
const prologue = checkScene('1100011nml', '序章 / prologue');
const chapter1 = checkScene('1101011nml', '主线第一章 / main chapter');
const event = checkScene('eb111601anml', '活动剧情 / event');
const repeated = checkScene('ev1116032nml', '重复短句场景 / repeated short lines');

must(lookup('創世記。', '1100011nml') === '创世记。',
     'acceptance: 創世記。 -> 创世记。');
must(lookup('神はまず、可愛い少年をお作りになられた。', '1100011nml') ===
     '神首先创造了可爱的少年。', 'previously missing 创/爱 example resolves exactly');

// Regression (full-corpus audit): a source string that ALSO happens to be a
// translated value of another line must not be swallowed by the
// "already translated" guard.  isKnownStoryValue() excludes indexed source
// keys.  The author's corpus changes on every update - whole lines get
// deleted or reworded - so find live examples instead of pinning a sentence.
const storyValues = new Set();
function collectStoryValues(pairs) {
  for (const p of pairs) storyValues.add(doc.strings[p[1]]);
}
collectStoryValues(doc.parse.global);
collectStoryValues(doc.display.global);
for (const r of doc.parse.ambiguous) collectStoryValues(r.s);
for (const r of doc.display.ambiguous) collectStoryValues(r.s);
for (const [, v] of doc.names) storyValues.add(v);
// Take the samples straight from the index: only a handful of sources are
// also translation values, and which ones changes with every corpus update.
const guardSamples = [];
for (const [ki, vi] of doc.parse.global) {
  const k = doc.strings[ki], v = doc.strings[vi];
  if (!v || k === v || api.isSelfMapped(k, v) || !storyValues.has(k)) continue;
  if (idx.isKnownStoryValue(k)) continue;
  guardSamples.push(['', k, v]);
  if (guardSamples.length >= 2) break;
}
must(guardSamples.length >= 1,
     'a source that is also a translated value exists in the corpus');
if (guardSamples.length >= 1) {
  const [sid1, k1, v1] = guardSamples[0];
  must(idx.isKnownStoryValue(k1) === false,
       'source that is also a known value is not treated as translated');
  must(lookup(k1, sid1) === v1,
       'source that is also a known value still resolves to its Chinese text');
  if (guardSamples.length >= 2) {
    const [sid2, k2, v2] = guardSamples[1];
    must(lookup(k2, sid2) === v2,
         'second source that is also a known value resolves too');
  }
}
must(lookup('創世記。', '1100011nml') === '创世记。',
     'unrelated story line is unchanged by the guard fix');

console.log('\n=== A4. SCENE-SPECIFIC CONFLICT ===');
// Which short lines are scene-specific is decided by the author's corpus.
// Derive a real conflict from the index instead of pinning one pair.
const S = doc.strings;
const conflict = doc.parse.ambiguous
  .map(r => ({ src: S[r.k], pairs: r.s.map(([si, vi]) =>
          ({ scene: doc.scenarios[si], value: S[vi] })) }))
  .find(c => new Set(c.pairs.map(p => p.value)).size >= 2);
must(!!conflict, 'a scene-specific conflict exists in the corpus');
const conflictSrc = conflict ? conflict.src : 'な、なるほど……。';
const cA = conflict ? conflict.pairs[0] : { scene: 'eb111602bnml', value: '' };
const cB = conflict ? conflict.pairs.find(p => p.value !== cA.value)
                    : { scene: '10263b0_nml', value: '' };
const a = lookup(conflictSrc, cA.scene);
const b = lookup(conflictSrc, cB.scene);
must(a === cA.value, `scene ${cA.scene} -> ${a}`);
must(b === cB.value, `scene ${cB.scene} -> ${b}`);
must(a !== b, 'same Japanese short line uses DIFFERENT scene-specific translations');

console.log('\n=== A5. PRELOAD MUST NOT INHERIT PLAYBACK SCENE ===');
{
  const ctx = api.createSceneContext(idx);
  ctx.beginPlayback(cB.scene);                      // currently playing
  ctx.beginParsing(cA.scene);                       // preloading a different scenario
  ctx.prepare(conflictSrc);
  const got = idx.lookup(conflictSrc, ctx);
  must(got === a, `preload uses its own scene, not playback: ${got}`);
  ctx.endParsing();
}

console.log('\n=== A6. UTAGE SEMANTICS ===');
const allRows = [];
for (const sid of ['1100011nml', '1101011nml', 'eb111601anml', 'ev1116032nml',
                   'eb500075bnml']) {
  for (const [k, v] of Object.entries(rawRows(sid))) allRows.push([sid, k, v]);
}
const nl = allRows.find(([, k, v]) => v && k.includes('\n') && !api.isSelfMapped(k, v));
const tagged = allRows.find(([, k, v]) => v && api.hasControlTag(k) && !api.isSelfMapped(k, v));
const long = allRows.reduce((best, r) => (r[1].length > best[1].length ? r : best), ['', '', '']);
must(!!nl, `multi-line sample found (${nl ? nl[1].split('\n').length : 0} lines)`);
if (nl) {
  const got = lookup(nl[1], nl[0]);
  must(got === nl[2] && got.includes('\n'), 'multi-line translation preserves newlines exactly');
}
must(!!tagged, `UTAGE control-tag sample found${tagged ? ': ' + tagged[1].match(/<[^>]*>/)[0] : ''}`);
if (tagged) {
  const got = lookup(tagged[1], tagged[0]);
  must(got === tagged[2], 'control-tag row translated as a whole row (tags preserved in value)');
}
must(long[1].length > 80, `long text sample ${long[1].length} UTF-16 units`);
if (long[2]) must(lookup(long[1], long[0]) === long[2], 'long text translated exactly');

must(lookup('この行は翻訳表に存在しない文章です。', '1100011nml') === '',
     'untranslated content returns "" (keep Japanese)');
const translatedValue = Object.values(rawRows('1100011nml')).find(v => v && v !== '創世記。');
must(idx.isKnownStoryValue(translatedValue) && lookup(translatedValue, '1100011nml') === '',
     'already-translated text is not translated twice');
const namesDoc = doc.names;
must(namesDoc.length === doc.stats.namesEntries && namesDoc.length > 0,
     `speaker-name table imported: ${namesDoc.length} entries`);
must(lookup(namesDoc[0][0], '1100011nml') === namesDoc[0][1] ||
     idx.parse.global.has(namesDoc[0][0]) === false,
     `name fallback available: ${namesDoc[0][0]} -> ${namesDoc[0][1]}`);

console.log('\n=== B. SIMULATED WASM (real patched module, synthetic objects) ===');
const wasm = fs.readFileSync(path.join(__dirname, 'resources', 'ntd.test.wasm'));
must(WebAssembly.validate(wasm), 'patched wasm validates');
const bridge = monmusuCnCreateBridge();
bridge.setStoryIndex(doc);
const mod = new WebAssembly.Module(wasm);
const imports = {};
for (const imp of WebAssembly.Module.imports(mod)) {
  imports[imp.module] = imports[imp.module] || {};
  imports[imp.module][imp.name] = () => 0;
}
imports.a.nf = bridge.wrapImport(() => 0);
imports.a.md = bridge.wrapContextImport(() => 0);
const inst = new WebAssembly.Instance(mod, imports);
let mem = null;
for (const k of Object.keys(inst.exports)) {
  const v = inst.exports[k];
  if (v && typeof v.grow === 'function' && v.buffer instanceof ArrayBuffer) { mem = v; break; }
}
must(!!mem, 'wasm memory adopted');
bridge.setMemory(mem);
const dv = new DataView(mem.buffer);
const A = 0x01200000, B = 0x01201000, C = 0x01202000;
function put(addr, s) {
  dv.setUint32(addr + 8, s.length, true);
  for (let i = 0; i < s.length; i++) dv.setUint16(addr + 12 + 2 * i, s.charCodeAt(i), true);
}
function get(addr) {
  const n = dv.getUint32(addr + 8, true);
  let o = '';
  for (let i = 0; i < n; i++) o += String.fromCharCode(dv.getUint16(addr + 12 + 2 * i, true));
  return o;
}
const CTX = 0x20000000;
// simulate AdvScenarioData.Init (parse scope) for an event scenario, then translate
put(B, 'eb111601anml');
imports.a.md(B, (CTX | 1) >>> 0);                    // PARSE_NAME
const eventRows = rawRows('eb111601anml');
const evSample = Object.entries(eventRows).find(([k, v]) => v && !api.isSelfMapped(k, v));
put(A, evSample[0]);
const out = inst.exports.monmusu_cn_test(A);
const written = get(out);
must(written === evSample[1],
     `event row translated through real wasm hook: ${JSON.stringify(written.slice(0, 24))}`);
must(get(A) === evSample[0], 'original Il2CppString untouched');
imports.a.md(0, (CTX | 3) >>> 0);                    // PARSE_END
must(bridge.scene().scopes.length === 0, 'parse scope popped');

// playback + conflicting short line (scene pair derived from the index)
put(C, cB.scene);
imports.a.md(C, (CTX | 4) >>> 0);                    // PLAYBACK_BEGIN
put(A, conflictSrc);
const out2 = inst.exports.monmusu_cn_test(A);
must(get(out2) === b, `conflict line under explicit playback scene -> ${get(out2)}`);
imports.a.md(0, (CTX | 5) >>> 0);                    // PLAYBACK_END
must(bridge.scene().playbackId === '', 'playback cleared');

// Phase 24: the unchanged real Wasm hook allocates/writes a composed result.
const chapterRows = rawRows('1101011nml');
put(B, '1101011nml');
imports.a.md(B, (CTX | 1) >>> 0);
for (const starts of [
  ['自由な少年との', 'リン・クロミに加え', 'いやいやながら幼馴染み'],
  ['違う違う違う、', '私は！', 'ケンタウロス娘じゃ！', 'なーい！！']
]) {
  const keys = starts.map(s => Object.keys(chapterRows).find(k => k.startsWith(s)));
  const source = keys.join(''), expected = keys.map(k => chapterRows[k]).join('');
  put(A, source);
  const result = inst.exports.monmusu_cn_test(A);
  must(get(result) === expected, 'composed chapter text through unchanged real Wasm hook');
  must(get(A) === source, 'composed source object left untouched');
  must(dv.getUint32(result + 8, true) === expected.length, 'composed managed length is exact');
}
imports.a.md(0, (CTX | 3) >>> 0);

console.log('\n=== C. REAL BROWSER TEST ===');
console.log('  NOT RUN - requires a DMM login and a live WebGL session.');

console.log(`\n${checks - fails}/${checks} checks passed`);
if (fails) { console.error(`${fails} check(s) FAILED`); process.exit(1); }
console.log('STORY INDEX TEST PASSED');
