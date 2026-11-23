// Structural validation + real execution test of the injected hook.
// Runs the patched module with stub imports, plants synthetic Il2CppString
// objects in linear memory, calls the exported hook, and checks the result.
const fs = require('fs');
const path = require('path');

// NOTE: this harness exercises the ORIGINAL overwrite-mode PoC (in-place ASCII
// rewriting). The translate-mode patch is covered by test_translate.js and by
// verify_patch.py's byte-identity proof against the shipped Wasm.
const wasmPath = process.argv[2] ||
  path.join(__dirname, '..', '..', 'build', 'wasm', 'ntd.overwrite.wasm');
const exportName = process.argv[3] || 'monmusu_cn_test';
const buf = fs.readFileSync(wasmPath);

function fatal(msg) { console.error('FAIL: ' + msg); process.exit(1); }
function ok(msg) { console.log('  ok  ' + msg); }

console.log('file: ' + wasmPath + '  (' + buf.length + ' bytes)');

// --- 1. structural validation ---
if (!WebAssembly.validate(buf)) fatal('WebAssembly.validate() rejected the module');
ok('WebAssembly.validate() passed');

// --- 2. instantiate with stub imports ---
const mod = new WebAssembly.Module(buf);
const imports = WebAssembly.Module.imports(mod);
const importObj = {};
for (const imp of imports) {
  importObj[imp.module] = importObj[imp.module] || {};
  importObj[imp.module][imp.name] = () => 0;
}
ok('module compiled; ' + imports.length + ' stub imports supplied');

const inst = new WebAssembly.Instance(mod, importObj);
const mem = new DataView(inst.exports.bj.buffer);
ok('instantiated; memory export "bj" = ' + (inst.exports.bj.buffer.byteLength / 1048576) + ' MiB');

const hook = inst.exports[exportName];
if (typeof hook !== 'function') fatal('hook export ' + exportName + ' missing');
ok('hook export resolved');

const HEAP = new Uint8Array(inst.exports.bj.buffer);
const A = 0x01000000;                    // scratch area well past the 7.3 MiB data image

function writeString(addr, s) {
  const n = s.length;
  HEAP.fill(0, addr, addr + 12 + 2 * Math.max(n, 64));
  new DataView(HEAP.buffer).setUint32(addr + 8, n, true);
  for (let i = 0; i < n; i++) {
    new DataView(HEAP.buffer).setUint16(addr + 12 + 2 * i, s.charCodeAt(i), true);
  }
}
function readString(addr) {
  const dv = new DataView(HEAP.buffer);
  const n = dv.getUint32(addr + 8, true);
  let out = '';
  for (let i = 0; i < n; i++) out += String.fromCharCode(dv.getUint16(addr + 12 + 2 * i, true));
  return out;
}

// --- 3. behaviour tests ---
const cases = [
  { name: 'prologue_narration_01', src: '神はまず、可愛い少年をお作りになられた。', expect: 'BPT WEBGL TEST' },
  { name: 'prologue_narration_02', src: '（ゲシュペンス島・創世記、第１章第１節より抜粋）', expect: 'BPT WEBGL TEST' },
  { name: 'prologue_dialogue_01', src: 'いえ、痒くは……ていうか、ちっちゃ！？', expect: 'BPT WEBGL TEST' },
  { name: 'prologue_dialogue_02', src: 'なにも……なにも、思い出せない……ううっ、頭が……。', expect: 'BPT WEBGL TEST' },
  // must NOT be touched
  { name: 'unrelated_line', src: '創世記。', expect: '創世記。' },
  { name: 'prefix_only', src: '神はまず、可愛い少年をお作りになられた', expect: '神はまず、可愛い少年をお作りになられた' },
  { name: 'ascii', src: 'Hello, world!', expect: 'Hello, world!' },
  { name: 'empty', src: '', expect: '' },
];

let fail = 0;
for (const c of cases) {
  writeString(A, c.src);
  hook(A);
  const got = readString(A);
  if (got === c.expect) {
    ok(`${c.name}: ${JSON.stringify(c.src).slice(0, 30)} -> ${JSON.stringify(got)}`);
  } else {
    console.error(`  FAIL ${c.name}: expected ${JSON.stringify(c.expect)} got ${JSON.stringify(got)}`);
    fail++;
  }
}

// --- 4. null pointer must not fault ---
try { hook(0); ok('null pointer handled without trap'); }
catch (e) { console.error('  FAIL null pointer trapped: ' + e.message); fail++; }

// --- 5. neighbouring memory untouched ---
const guardBefore = HEAP.slice(A - 64, A);
writeString(A, cases[0].src);
hook(A);
const guardAfter = HEAP.slice(A - 64, A);
if (guardBefore.every((v, i) => v === guardAfter[i])) ok('no out-of-object writes before the string');
else { console.error('  FAIL wrote outside the object (before)'); fail++; }

if (fail) { console.error(`\n${fail} test(s) failed`); process.exit(1); }
console.log('\nALL TESTS PASSED');
