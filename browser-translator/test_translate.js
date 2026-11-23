// Local execution test for the translate-mode patch.
//
//   resources/ntd.test.wasm      same bytecode as production, but the IL2CPP allocator
//                            is replaced by a bump allocator so it can run without
//                            an initialised runtime.  Exercises the MATCH path.
//   resources/ntd.patched.wasm   the shipped artefact; only the non-allocating paths can
//                            run cold (null / no-match), which is what we assert.
//
// The JavaScript side is the real monmusu_cn_shim.js, not a reimplementation.
const fs = require('fs');
const path = require('path');
const { monmusuCnCreateBridge } = require('./monmusu_cn_shim.js');

let fails = 0;
const ok = (m) => console.log('  ok   ' + m);
const bad = (m) => { console.error('  FAIL ' + m); fails++; };

function loadTable(p) {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  return j;
}

function makeImports(mod, overrides) {
  const obj = {};
  for (const imp of WebAssembly.Module.imports(mod)) {
    obj[imp.module] = obj[imp.module] || {};
    obj[imp.module][imp.name] = () => 0;
  }
  for (const [mod_, name, fn] of overrides) obj[mod_][name] = fn;
  return obj;
}

function findMemory(inst) {
  for (const k of Object.keys(inst.exports)) {
    const v = inst.exports[k];
    if (v && typeof v.grow === 'function' && v.buffer instanceof ArrayBuffer) return v;
  }
  return null;
}

// ---------- helpers to plant / read an Il2CppString ----------
function writeIl2CppString(heap, addr, s) {
  const dv = new DataView(heap.buffer);
  dv.setUint32(addr + 0, 0, true);          // klass (unused by the bridge)
  dv.setUint32(addr + 4, 0, true);          // monitor
  dv.setUint32(addr + 8, s.length, true);   // length
  for (let i = 0; i < s.length; i++) dv.setUint16(addr + 12 + 2 * i, s.charCodeAt(i), true);
}
function readIl2CppString(heap, addr) {
  const dv = new DataView(heap.buffer);
  const n = dv.getUint32(addr + 8, true);
  let out = '';
  for (let i = 0; i < n; i++) out += String.fromCharCode(dv.getUint16(addr + 12 + 2 * i, true));
  return out;
}

const table = loadTable(path.join(__dirname, 'resources', 'monmusu_cn_translations.json'));
const pairs = table.pairs;

// =====================================================================
// 1. test build: full protocol including managed allocation + fill
// =====================================================================
console.log('1) resources/ntd.test.wasm  (stub allocator, exercises the match path)');
const testBuf = fs.readFileSync(path.join(__dirname, 'resources', 'ntd.test.wasm'));
if (!WebAssembly.validate(testBuf)) { bad('test build rejected by WebAssembly.validate'); }
else ok('WebAssembly.validate() passed');

const testMod = new WebAssembly.Module(testBuf);
const bridge = monmusuCnCreateBridge();
bridge.setTable(pairs);
ok(`bridge table loaded: ${bridge.stats.tableSize} entries (scope=${table.scope})`);

const importNames = WebAssembly.Module.imports(testMod);
if (importNames.length !== 540) bad(`import count ${importNames.length} != 540`);
else ok('import count 540 (build signature matches)');

const origCalls = { n: 0, lastArgs: null };
const origImport = function (a, b) { origCalls.n++; origCalls.lastArgs = [a, b]; return 4242; };
const wrapped = bridge.wrapImport(origImport);

const inst = new WebAssembly.Instance(testMod,
  makeImports(testMod, [['a', 'nf', wrapped]]));
const mem = findMemory(inst);
if (!mem) bad('no memory export found'); else ok(`memory attached (${mem.buffer.byteLength / 1048576} MiB)`);
bridge.setMemory(mem);

const heap = new Uint8Array(mem.buffer);
const hook = inst.exports.monmusu_cn_test;
if (typeof hook !== 'function') bad('monmusu_cn_test export missing'); else ok('monmusu_cn_test export resolved');

const A = 0x01200000;      // scratch source strings
const GUARD = 64;

// --- acceptance string ---
{
  const src = '創世記。';
  writeIl2CppString(heap, A, src);
  const before = heap.slice(A, A + 12 + 2 * src.length);
  const out = hook(A);
  if (out === 0 || out === A) bad(`acceptance: expected a NEW string pointer, got ${out}`);
  else {
    const got = readIl2CppString(heap, out);
    if (got === '创世记。') ok(`創世記。 -> 创世记。  (new object at ${out.toString(16)})`);
    else bad(`acceptance: got ${JSON.stringify(got)}`);
  }
  const after = heap.slice(A, A + 12 + 2 * src.length);
  if (before.every((v, i) => v === after[i])) ok('original string object left untouched');
  else bad('original string object was modified');
}

// --- every prologue pair, through the real hook ---
{
  let good = 0, badCase = null;
  for (const [jp, zh] of pairs) {
    writeIl2CppString(heap, A, jp);
    const out = hook(A);
    if (!out || out === A) { badCase = [jp, zh, 'returned original']; break; }
    const got = readIl2CppString(heap, out);
    if (got !== zh) { badCase = [jp, zh, got]; break; }
    good++;
  }
  if (badCase) bad(`pair loop stopped at ${JSON.stringify(badCase).slice(0, 120)}`);
  else ok(`all ${good} prologue pairs translated correctly`);
}

// --- no-match returns the original pointer, exactly ---
{
  const src = 'この行は翻訳表にない。';
  writeIl2CppString(heap, A, src);
  const out = hook(A);
  if (out === A) ok('unmatched string returns the original pointer'); else bad('unmatched string changed');
}

// --- long / multi-line / surrogate content ---
{
  const cases = [
    ['改行を含む\n複数行のテキストです。', '含换行的\n多行文本。'],
    ['サロゲート𝔘テスト', '代理对𝔘测试🀄'],
    ['x'.repeat(200), 'y'.repeat(200)],
    ['　全角スペース　', '　全角空格　'],
  ];
  const b2 = monmusuCnCreateBridge();
  b2.setTable(cases);
  const w2 = b2.wrapImport(origImport);
  const inst2 = new WebAssembly.Instance(testMod, makeImports(testMod, [['a', 'nf', w2]]));
  b2.setMemory(findMemory(inst2));
  const h2 = new Uint8Array(findMemory(inst2).buffer);
  const hook2 = inst2.exports.monmusu_cn_test;
  let good = 0;
  for (const [jp, zh] of cases) {
    writeIl2CppString(h2, A, jp);
    const out = hook2(A);
    if (out && out !== A && readIl2CppString(h2, out) === zh) good++;
    else { bad(`edge case failed: ${JSON.stringify(jp).slice(0, 40)}`); break; }
  }
  if (good === cases.length) ok(`${good} edge cases: newline, surrogate pairs, 200 units, full-width space`);
}

// --- null pointer ---
{
  const out = hook(0);
  if (out === 0) ok('null string handled without trap'); else bad('null handling wrong');
}

// --- passthrough for non-tagged calls ---
{
  const before = origCalls.n;
  const r = wrapped(12345, 7);
  if (r === 4242 && origCalls.n === before + 1) ok('non-tagged call forwarded to the original import');
  else bad(`passthrough broken (r=${r}, calls=${origCalls.n - before})`);
}

// --- allocations do not overlap the source ---
{
  const src = '創世記。';
  writeIl2CppString(heap, A, src);
  const outs = [];
  for (let i = 0; i < 3; i++) outs.push(hook(A));
  const uniq = new Set(outs);
  if (uniq.size === 3 && !outs.includes(A)) ok('each translated line gets its own new object');
  else bad('allocation reuse detected');
}

console.log(`  stats: ${JSON.stringify(bridge.stats)}`);

// =====================================================================
// 2. production build: structure + cold-safe paths only
// =====================================================================
console.log('\n2) resources/ntd.patched.wasm  (shipped artefact)');
const prodBuf = fs.readFileSync(path.join(__dirname, 'resources', 'ntd.patched.wasm'));
if (!WebAssembly.validate(prodBuf)) bad('production build rejected by WebAssembly.validate');
else ok('WebAssembly.validate() passed');

const prodMod = new WebAssembly.Module(prodBuf);
const prodInst = new WebAssembly.Instance(prodMod, makeImports(prodMod, [['a', 'nf', wrapped]]));
const prodMem = findMemory(prodInst);
bridge.setMemory(prodMem);
const hookP = prodInst.exports.monmusu_cn_test;
{
  const src = 'この行は翻訳表にない。';
  writeIl2CppString(heap, A, src);   // heap view of the test module's memory; re-view below
  const hp = new Uint8Array(prodMem.buffer);
  writeIl2CppString(hp, A, src);
  const out = hookP(A);
  if (out === A) ok('unmatched string returns the original pointer (no allocation attempted)');
  else bad('unmatched path in production build misbehaves');
  if (hookP(0) === 0) ok('null handled'); else bad('null handling wrong in production build');
}

// =====================================================================
console.log('');
if (fails) { console.error(`${fails} check(s) FAILED`); process.exit(1); }
console.log('ALL TRANSLATE TESTS PASSED');
