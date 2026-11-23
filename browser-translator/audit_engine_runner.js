/* Execute every audit case against the PRODUCTION translation engines.
 *
 *   node audit_engine_runner.js <cases.jsonl.gz> <results.jsonl.gz>
 *
 * No matcher is reimplemented here.  We require the exact files that
 * patch_loader.py concatenates into resources/ntd.loader.js, build the indexes
 * through the same monmusu_cn_shim.js bridge methods the browser calls
 * (setStoryIndex / setUiIndex / setTable), and call the very same query
 * functions: lookupLazy() for UI and index.lookup(source, ctx) for story.
 *
 * Story cases go through the production scene context (beginParsing /
 * prepare / endParsing) so scene-specific rows resolve exactly as they do
 * during playback.  The prologue rollback table goes through the production
 * flat-map branch of handle() with a small linear-memory stub.
 *
 * Output is gzip JSON lines: {i, ok, a?, r?, w?}.  "a" (the actual returned
 * string) is only written for failing cases, keeping the result file small.
 */
'use strict';
const fs = require('fs');
const zlib = require('zlib');
const readline = require('readline');
const path = require('path');

const HERE = __dirname;
const uiDoc = JSON.parse(fs.readFileSync(path.join(HERE, 'resources', 'monmusu_cn_ui_index.json'), 'utf8'));
const storyDoc = JSON.parse(fs.readFileSync(path.join(HERE, 'resources', 'monmusu_cn_story_index.json'), 'utf8'));
const fallbackDoc = JSON.parse(fs.readFileSync(path.join(HERE, 'resources', 'monmusu_cn_translations.json'), 'utf8'));

require('./monmusu_cn_ui_index.js');       // sets globalThis.monmusuCnUi
require('./monmusu_cn_story_index.js');    // sets globalThis.monmusuCnStory
const { monmusuCnCreateBridge } = require('./monmusu_cn_shim.js');

// --- production bridge: this is the object the browser exposes as __monmusuCnBridge
const bridge = monmusuCnCreateBridge();
bridge.setStoryIndex(storyDoc);
bridge.setUiIndex(uiDoc);
bridge.fontDisable();
const storyIndex = bridge.getIndex();
const storyCtx = bridge.getContext();
const uiIndex = bridge.getUiIndex();

// --- prologue rollback table: separate bridge, no story index installed
const fbBridge = monmusuCnCreateBridge();
fbBridge.setTable(fallbackDoc.pairs);
const mem = new WebAssembly.Memory({ initial: 4 });
fbBridge.setMemory(mem);
const dv = new DataView(mem.buffer);
const SRC_PTR = 0x10000, DST_PTR = 0x20000;

function putSrc(s) {
  dv.setUint32(SRC_PTR + 8, s.length, true);
  for (let i = 0; i < s.length; i++) dv.setUint16(SRC_PTR + 12 + i * 2, s.charCodeAt(i), true);
}
function getDst(n) {
  let out = '';
  for (let i = 0; i < n; i++) out += String.fromCharCode(dv.getUint16(DST_PTR + i * 2, true));
  return out;
}
function fallbackLookup(src) {
  putSrc(src);
  // handleTranslate() always reads arg1 as the source Il2CppString*; the fill
  // destination is packed into arg2 (same protocol the wasm hook uses).
  const n = fbBridge.handle(SRC_PTR, fbBridge.TAG_TRANSLATE);
  if (!n) return null;
  const filled = fbBridge.handle(SRC_PTR, fbBridge.TAG_TRANSLATE | DST_PTR);
  if (!filled) return null;
  return getDst(filled);
}

// --- one case -------------------------------------------------------------
let uiWalks = 0;
function runCase(c) {
  let actual = null, rule = '', why = '';
  try {
    if (c.engine === 'ui') {
      const getPath = c.path ? () => { uiWalks++; return c.path; } : null;
      actual = uiIndex.lookupLazy(c.source, getPath);
      rule = (uiIndex.stats && uiIndex.stats.lastRule) || '';
      if (!actual) {
        const st = uiIndex.stats;
        why = st && st.pathLookups === st.lookups ? 'path-gated' : 'no-rule';
      }
      if (actual === '') actual = null;
    } else if (c.engine === 'story') {
      // production scene context, per case, no state leaking between cases
      if (c.scene) storyCtx.beginParsing(c.scene);
      storyCtx.prepare(c.source);
      actual = storyIndex.lookup(c.source, storyCtx);
      if (c.scene) storyCtx.endParsing();
      rule = c.scene ? ('scene=' + c.scene) : 'no-scene';
      if (actual === '') actual = null;
    } else if (c.engine === 'fallback') {
      actual = fallbackLookup(c.source);
      rule = 'flat';
      if (actual === '') actual = null;
    } else {
      return { i: c.id, ok: false, a: null, r: 'unknown-engine', w: c.engine };
    }
  } catch (e) {
    return { i: c.id, ok: false, a: null, r: 'exception', w: String(e && e.message || e) };
  }
  const ok = c.expect_hit ? (actual === c.expected) : (actual === null || actual === '');
  return ok ? { i: c.id, ok: true }
            : { i: c.id, ok: false, a: actual, r: rule, w: why };
}

async function main() {
  const inPath = process.argv[2];
  const outPath = process.argv[3];
  if (!inPath || !outPath) {
    console.error('usage: node audit_engine_runner.js <cases.gz> <results.gz>');
    process.exit(2);
  }
  const input = fs.createReadStream(inPath).pipe(zlib.createGunzip());
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  const out = zlib.createGzip();
  const sink = fs.createWriteStream(outPath);
  out.pipe(sink);

  let meta = null, count = 0, passed = 0;
  const concatenation = {cases: 0, pass: 0, fail: 0, untestable: 0, ambiguous: 0,
    byProvenance: {}, fallbackHits: 0};
  for await (const line of rl) {
    if (!line) continue;
    const obj = JSON.parse(line);
    if (obj._meta || obj._story_conflicts) continue;   // metadata, not a case
    const r = runCase(obj);
    if (obj.kind === 'story_concat') {
      const why = storyIndex.concatStats.last;
      const provenance = obj.variant.split(':')[1];
      const bucket = concatenation.byProvenance[provenance] ||
        (concatenation.byProvenance[provenance] = {cases: 0, pass: 0, fail: 0, untestable: 0});
      concatenation.cases++; bucket.cases++;
      if (why && why.reason === 'hit') concatenation.fallbackHits++;
      if (!r.ok && why && why.reason === 'ambiguous-coverage') {
        r.w = 'concat-ambiguous-coverage';
        concatenation.ambiguous++; concatenation.untestable++; bucket.untestable++;
      } else if (r.ok) { concatenation.pass++; bucket.pass++; }
      else { concatenation.fail++; bucket.fail++; }
    }
    if (r.ok) passed++;
    count++;
    out.write(JSON.stringify(r) + '\n');
  }
  out.end();
  await new Promise((res, rej) => { sink.on('finish', res); sink.on('error', rej); });
  console.log(JSON.stringify({ cases: count, passed, failed: count - passed,
                               concatenation,
                               uiPathWalks: uiWalks,
                               storyScenarios: storyIndex.scenarioCount,
                               uiMeta: uiIndex.meta,
                               storyStats: storyIndex.stats }));
}

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
