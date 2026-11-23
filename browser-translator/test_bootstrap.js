// Integration test for the code that is actually appended to ntd.loader.js.
//
// Runs monmusu_cn_story_index.js + monmusu_cn_shim.js + monmusu_cn_bootstrap.js unmodified inside a
// minimal sandbox, then drives WebAssembly.instantiate() the way Unity does:
//   * the full story index is awaited before instantiation
//   * BOTH repurposed imports are wrapped (336 translation, 228 scene context)
//   * memory is adopted automatically
//   * translation works end to end through the patched module
//   * scene-context commands reach the JS state
//   * a wrong build signature leaves the game untouched
//   * index failure falls back to the prologue table; both failing = untranslated
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let fails = 0;
const ok = (m) => console.log('  ok   ' + m);
const bad = (m) => { console.error('  FAIL ' + m); fails++; };

const INDEXJS = fs.readFileSync(path.join(__dirname, 'monmusu_cn_story_index.js'), 'utf8');
const UIJS = fs.readFileSync(path.join(__dirname, 'monmusu_cn_ui_index.js'), 'utf8');
const SHIM = fs.readFileSync(path.join(__dirname, 'monmusu_cn_shim.js'), 'utf8');
const RUNTIME = fs.readFileSync(path.join(__dirname, 'monmusu_cn_runtime_config.js'), 'utf8');
const BOOT = fs.readFileSync(path.join(__dirname, 'monmusu_cn_bootstrap.js'), 'utf8');
const storyIndex = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_story_index.json'), 'utf8'));
const uiIndex = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_ui_index.json'), 'utf8'));
const prologue = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_translations.json'), 'utf8'));
const wasm = fs.readFileSync(path.join(__dirname, 'resources', 'ntd.test.wasm'));

const BASE = 'https://assets.game-monmusu-td.net/web/arl/202611021000/0/Build/';
const LOADER_URL = BASE + 'ntd.loader.js';
const INDEX_URL = BASE + 'monmusu_cn_story_index.json';
const UI_URL = BASE + 'monmusu_cn_ui_index.json';
const FALLBACK_URL = BASE + 'monmusu_cn_translations.json';

function makeSandbox(fetchImpl, loaderUrl = LOADER_URL) {
  const calls = { fetch: [], instantiate: 0, streaming: 0, logs: [] };
  const realInstantiate = WebAssembly.instantiate;
  const realStreaming = WebAssembly.instantiateStreaming;
  const sandbox = {
    console: {
      log: (...a) => { calls.logs.push(['log', a.join(' ')]); },
      warn: (...a) => { calls.logs.push(['warn', a.join(' ')]); },
      error: (...a) => { calls.logs.push(['error', a.join(' ')]); },
    },
    Promise, setTimeout, clearTimeout, URL, ArrayBuffer, DataView, Math, Object,
    Map, Set, Array, String, Number, JSON, RegExp, Date, performance,
    document: { currentScript: { src: loaderUrl } },
    window: {},
    fetch: (url, opts) => { calls.fetch.push(url); return fetchImpl(url, opts); },
    WebAssembly: {
      instantiate: (b, i) => { calls.instantiate++; return realInstantiate(b, i); },
      instantiateStreaming: (r, i) => { calls.streaming++; return realStreaming(r, i); },
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.module = undefined;
  vm.createContext(sandbox);
  return { sandbox, calls };
}

function loadAll(sandbox, withRuntime = true) {
  // Same order build_release.py uses: the generated config first, then the
  // engines, then the bootstrap that consumes it.
  if (withRuntime) vm.runInContext(RUNTIME, sandbox, { filename: 'monmusu_cn_runtime_config.js' });
  vm.runInContext(INDEXJS, sandbox, { filename: 'monmusu_cn_story_index.js' });
  vm.runInContext(UIJS, sandbox, { filename: 'monmusu_cn_ui_index.js' });
  vm.runInContext(SHIM, sandbox, { filename: 'monmusu_cn_shim.js' });
  vm.runInContext(BOOT, sandbox, { filename: 'monmusu_cn_bootstrap.js' });
}

function makeImports(mod, overrides) {
  const obj = {};
  for (const imp of WebAssembly.Module.imports(mod)) {
    obj[imp.module] = obj[imp.module] || {};
    obj[imp.module][imp.name] = () => 0;
  }
  for (const [m, n, fn] of overrides) obj[m][n] = fn;
  return obj;
}
function findMemory(inst) {
  for (const k of Object.keys(inst.exports)) {
    const v = inst.exports[k];
    if (v && typeof v.grow === 'function' && v.buffer instanceof ArrayBuffer) return v;
  }
  return null;
}
function putString(dv, addr, s) {
  dv.setUint32(addr + 8, s.length, true);
  for (let i = 0; i < s.length; i++) dv.setUint16(addr + 12 + 2 * i, s.charCodeAt(i), true);
}
function getString(dv, addr) {
  const n = dv.getUint32(addr + 8, true);
  let out = '';
  for (let i = 0; i < n; i++) out += String.fromCharCode(dv.getUint16(addr + 12 + 2 * i, true));
  return out;
}

const fetchOk = (url) => {
  if (url === INDEX_URL) return Promise.resolve({ ok: true, json: () => Promise.resolve(storyIndex) });
  if (url === UI_URL) return Promise.resolve({ ok: true, json: () => Promise.resolve(uiIndex) });
  if (url === FALLBACK_URL) return Promise.resolve({ ok: true, json: () => Promise.resolve(prologue) });
  return Promise.reject(new Error('unexpected URL ' + url));
};

async function main() {
  console.log('1) bootstrap installs the full index and both import wrappers');
  const { sandbox, calls } = makeSandbox(fetchOk);
  sandbox.localStorage = {getItem: key => key === 'monmusu_cn.storyTrace' ? '1' : null};
  loadAll(sandbox);
  if (calls.fetch[0] === INDEX_URL) ok('story index URL derived from document.currentScript.src');
  else bad(`unexpected index URL: ${calls.fetch[0]}`);

  const mod = new WebAssembly.Module(wasm);
  const imports = makeImports(mod, [['a', 'nf', () => 777], ['a', 'md', () => 888]]);
  const origTrans = imports.a.nf, origCtx = imports.a.md;
  const res = await sandbox.WebAssembly.instantiate(wasm, imports);
  if (calls.instantiate === 1) ok('WebAssembly.instantiate forwarded exactly once');
  else bad(`instantiate called ${calls.instantiate} times`);
  if (imports.a.nf !== origTrans) ok('translation import a.nf wrapped'); else bad('a.nf not wrapped');
  if (imports.a.md !== origCtx) ok('scene-context import a.md wrapped'); else bad('a.md not wrapped');

  const inst = res.instance || res;
  const mem = findMemory(inst);
  const bridge = sandbox.window.__monmusuCnBridge;
  if (mem && bridge && bridge.hasTable()) ok('memory adopted and story index loaded');
  else bad('bridge not fully initialised');
  if (bridge && bridge.getIndex() && bridge.getIndex().scenarioCount > 4000)
    ok(`story index scenes = ${bridge.getIndex().scenarioCount}`);
  else bad('story index not attached');

  if (calls.fetch.indexOf(UI_URL) >= 0) ok('UI index requested from the same directory');
  else bad('UI index never requested');
  const ui = bridge && bridge.getUiIndex();
  if (ui && ui.meta.pathTables > 100)
    ok(`ui index attached: ${ui.meta.globalEntries} global, ${ui.meta.pathTables} path tables, ${ui.meta.nameEntries} names`);
  else bad('ui index not attached');
  if (bridge.getUiIndex().lookup('確認', '')) ok('ui index answers a plain menu string');
  else bad('ui index lookup failed after bootstrap');

  const dv = new DataView(mem.buffer);
  const A = 0x01200000;
  const src = '創世記。';
  putString(dv, A, src);
  const out = inst.exports.monmusu_cn_test(A);
  const got = getString(dv, out);
  if (got === '创世记。') ok(`in-game path: 創世記。 -> 创世记。`);
  else bad(`translation failed: ${JSON.stringify(got)}`);
  const trace = bridge.storyTrace().find(r => r.source === src);
  if (trace && trace.enteredStoryQuery && trace.write === 'buffer-written')
    ok('storage opt-in traces the real Wasm probe/fill bridge');
  else bad('storage story trace did not capture the Wasm write');

  // passthrough for both imports
  if (imports.a.nf(1, 2) === 777 && imports.a.md(3, 4) === 888)
    ok('non-tagged calls still reach both original imports');
  else bad('passthrough broken');

  // scene-context command reaches JS state
  const B = 0x01201000;
  putString(dv, B, '1100011nml');
  imports.a.md(B, (0x20000000 | 4) >>> 0);   // PLAYBACK_BEGIN
  const snap = bridge.scene();
  if (snap && snap.playbackId === '1100011nml')
    ok('scene-context command sets playback scene: ' + snap.playbackId);
  else bad('scene context not updated: ' + JSON.stringify(snap));

  console.log('\n2) wrong build signature -> patch skipped, game untouched');
  const s2 = makeSandbox(fetchOk);
  loadAll(s2.sandbox);
  const imports2 = makeImports(mod, []);
  const dropKey = Object.keys(imports2.a).find((k) => k !== 'nf' && k !== 'md');
  delete imports2.a[dropKey];
  const before2 = imports2.a.nf;
  let rejected = false;
  try { await s2.sandbox.WebAssembly.instantiate(wasm, imports2); } catch (e) { rejected = true; }
  if (rejected) ok('instantiation rejected as expected (missing import)');
  if (imports2.a.nf === before2) ok('imports left untouched on signature mismatch');
  else bad('imports were wrapped despite a signature mismatch');
  if (s2.calls.logs.some(([l, m]) => l === 'warn' && m.includes('patch skipped')))
    ok('skip reason logged'); else bad('no diagnostic logged');

  console.log('\n3) full index fails -> prologue fallback still translates');
  const s3 = makeSandbox((url) => url === INDEX_URL
    ? Promise.reject(new Error('offline')) : fetchOk(url));
  loadAll(s3.sandbox);
  const imports3 = makeImports(mod, []);
  const res3 = await s3.sandbox.WebAssembly.instantiate(wasm, imports3);
  const inst3 = res3.instance || res3;
  const dv3 = new DataView(findMemory(inst3).buffer);
  putString(dv3, A, src);
  const out3 = inst3.exports.monmusu_cn_test(A);
  if (getString(dv3, out3) === '创世记。') ok('fallback table translated the prologue line');
  else bad('fallback translation failed');

  console.log('\n4) both fetches fail -> game boots untranslated');
  const s4 = makeSandbox(() => Promise.reject(new Error('offline')));
  loadAll(s4.sandbox);
  const res4 = await s4.sandbox.WebAssembly.instantiate(wasm, makeImports(mod, []));
  const inst4 = res4.instance || res4;
  const dv4 = new DataView(findMemory(inst4).buffer);
  putString(dv4, A, src);
  if (inst4.exports.monmusu_cn_test(A) === A) ok('no data -> hook returns the original pointer');
  else bad('hook behaved unexpectedly with no data');

  console.log('\n5) FANZA branch (/1/) derives its own resource base');
  const BASE1 = BASE.replace('/0/Build/', '/1/Build/');
  const LOADER1 = BASE1 + 'ntd.loader.js';
  const fetch1 = (url) => {
    if (url === BASE1 + 'monmusu_cn_story_index.json')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(storyIndex) });
    if (url === BASE1 + 'monmusu_cn_ui_index.json')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(uiIndex) });
    if (url === BASE1 + 'monmusu_cn_translations.json')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(prologue) });
    return Promise.reject(new Error('unexpected URL ' + url));
  };
  const s5 = makeSandbox(fetch1, LOADER1);
  loadAll(s5.sandbox);
  const res5 = await s5.sandbox.WebAssembly.instantiate(wasm, makeImports(mod, []));
  const inst5 = res5.instance || res5;
  if (s5.calls.fetch[0] === BASE1 + 'monmusu_cn_story_index.json')
    ok('story index requested from /1/Build/');
  else bad(`wrong /1/ index URL: ${s5.calls.fetch[0]}`);
  if (s5.calls.fetch.indexOf(BASE1 + 'monmusu_cn_ui_index.json') >= 0)
    ok('UI index requested from /1/Build/');
  else bad('UI index not requested from /1/');
  if (s5.calls.logs.some(([l, m]) => l === 'log' && m.includes('armed for build 202611021000/1')))
    ok('bootstrap reports branch 1');
  else bad('bootstrap did not report branch 1');
  if (s5.calls.logs.some(([l, m]) => l === 'log' && m.includes('(config ok)')))
    ok('branch 1 used its own generated config');
  else bad('branch 1 config state not reported');
  const dv5 = new DataView(findMemory(inst5).buffer);
  putString(dv5, A, src);
  const out5 = inst5.exports.monmusu_cn_test(A);
  if (getString(dv5, out5) === '创世记。') ok('/1/ session still translates');
  else bad('/1/ session failed to translate');

  console.log('\n6) loader without the generated config -> refuses to patch, loudly');
  const s6 = makeSandbox(fetchOk);
  loadAll(s6.sandbox, false);
  const imports6 = makeImports(mod, []);
  const before6 = imports6.a.nf;
  await s6.sandbox.WebAssembly.instantiate(wasm, imports6);
  if (imports6.a.nf === before6) ok('imports untouched without a runtime config');
  else bad('imports were wrapped without a runtime config');
  if (s6.calls.logs.some(([l, m]) => l === 'warn' && m.includes('no runtime config -> patch skipped')))
    ok('missing config logged with the reason');
  else bad('missing config not reported');
  const rc6 = s6.sandbox.window.__monmusuCnRuntime && s6.sandbox.window.__monmusuCnRuntime();
  if (rc6 && rc6.configState === 'missing') ok('diagnostics expose configState=missing');
  else bad('runtime diagnostics missing');

  console.log('\n7) config for the other branch -> refused, translation off');
  // page is /1/ in this sandbox, but only a /0/ entry exists -> must not patch
  const s7b = makeSandbox(fetchOk, BASE.replace('/0/Build/', '/1/Build/') + 'ntd.loader.js');
  vm.runInContext('var MONMUSU_CN_RUNTIME = {schema:1, branches:{\"0\":(' +
    JSON.stringify(require('./monmusu_cn_runtime_config.js').branches['0']) + ')}};',
    s7b.sandbox, { filename: 'monmusu_cn_runtime_config.js' });
  vm.runInContext(INDEXJS, s7b.sandbox, { filename: 'monmusu_cn_story_index.js' });
  vm.runInContext(UIJS, s7b.sandbox, { filename: 'monmusu_cn_ui_index.js' });
  vm.runInContext(SHIM, s7b.sandbox, { filename: 'monmusu_cn_shim.js' });
  vm.runInContext(BOOT, s7b.sandbox, { filename: 'monmusu_cn_bootstrap.js' });
  const imports7 = makeImports(mod, []);
  const before7 = imports7.a.nf;
  await s7b.sandbox.WebAssembly.instantiate(wasm, imports7);
  if (imports7.a.nf === before7) ok('/1/ page with only a /0/ config is not patched');
  else bad('patched with the wrong branch config');
  if (s7b.calls.logs.some(([l, m]) => l === 'warn' && m.includes('no generated runtime config for branch 1')))
    ok('branch mismatch logged');
  else bad('branch mismatch not logged');

  console.log('');
  if (fails) { console.error(`${fails} check(s) FAILED`); process.exit(1); }
  console.log('BOOTSTRAP TEST PASSED');
}

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
