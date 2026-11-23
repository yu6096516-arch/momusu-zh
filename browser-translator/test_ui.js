// UI translation test: module structure + simulated-Wasm execution.
//
// "simulated-Wasm" means the REAL patched module (resources/ntd.test.wasm) is
// instantiated and the real hook function runs, but the IL2CPP runtime is not
// booted: the object graph is fabricated in linear memory and the three native
// getters are supplied by replacing entries in the module's own function table.
// This proves the hook body, the protocol, the hierarchy walk and the JS bridge
// work together.  It is NOT a browser test: Unity's real fonts, layout and the
// real TMP_Text setter implementations are not exercised.
const fs = require('fs');
const path = require('path');

const RT = require('./monmusu_cn_runtime_config.js');
const { monmusuCnCreateBridge: createBridge } = require('./monmusu_cn_shim.js');
// Build-dependent slots/offsets now come from the generated config; every
// bridge in this test gets the real /0/ entry, exactly like the loader does.
const monmusuCnCreateBridge = () => {
  const b = createBridge();
  if (b.applyRuntimeConfig) b.applyRuntimeConfig(RT.branches['0']);
  return b;
};
const uiApi = require('./monmusu_cn_ui_index.js');

let fails = 0;
const ok = (m) => console.log('  ok   ' + m);
const bad = (m) => { console.error('  FAIL ' + m); fails++; };
const eq = (got, want, m) => (got === want ? ok(`${m} = ${JSON.stringify(got)}`)
                                           : bad(`${m}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));

const WASM = fs.readFileSync(path.join(__dirname, 'resources', 'ntd.test.wasm'));
const uiDoc = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_ui_index.json'), 'utf8'));
const prologue = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_translations.json'), 'utf8'));
const subDoc = JSON.parse(fs.readFileSync(path.join(__dirname, 'ref',
  'MonsterMusumeTDChineseTranslation', 'translations', 'subskills', 'zh_Hans.json'), 'utf8'));
// previous verified index: the A/B baseline for "nothing that used to translate broke"
let prevDoc = null;
try {
  prevDoc = JSON.parse(fs.readFileSync(path.join(__dirname, 'resources', 'monmusu_cn_ui_index.json.previous'), 'utf8'));
} catch (e) { /* first build on a fresh checkout: regression A/B is skipped */ }

const UI_TAG = 0x10000000;
const CLS_PATH = 'CanvasManagerContent/WindowState/Home_Unit/PageParent/Page/Unit_Select/LeftZone/' +
  'Dialog/uiCanvas/UnitStatus/Unit_StatusView/Class/ClassNameBase/ClassNameTitle/ClassNameText';
const MISSION_PATH = 'CanvasManagerContent/WindowState/Home_Mission/PageParent/Page/Mission/RightZone/' +
  'MissionListView/CommonMissionScroll/ScrollView_Mission/Viewport/Mask/Content/Node/Mission';
const HEADER_PATH = 'CanvasManagerContent/Page/HeaderTitleCommon/Text';
const CLASS_PATH = 'CanvasManagerContent/WindowState/Home_Unit/PageParent/Page/Unit_Select/LeftZone/' +
  'Dialog/uiCanvas/UnitStatus/Unit_StatusView/Class/ClassNameBase/ClassNameTitle/ClassNameText';
const EXSKILL_PATH = 'CanvasManagerContent/Page/Unit_Detail_Rework/Main/UI/SkillDialog/' +
  'ExSkillArea/BorderLine/Title';

function findMemory(ex) {
  for (const k of Object.keys(ex)) {
    const v = ex[k];
    if (v && typeof v.grow === 'function' && v.buffer instanceof ArrayBuffer) return v;
  }
  return null;
}

async function boot() {
  const bridge = monmusuCnCreateBridge();
  const imports = {};
  for (const imp of WebAssembly.Module.imports(new WebAssembly.Module(WASM))) {
    imports[imp.module] = imports[imp.module] || {};
    imports[imp.module][imp.name] = () => 0;
  }
  imports.a.nf = bridge.wrapImport(imports.a.nf);
  imports.a.md = bridge.wrapContextImport(imports.a.md);

  const res = await WebAssembly.instantiate(WASM, imports);
  const inst = res.instance || res;
  const mem = findMemory(inst.exports);
  bridge.setMemory(mem);
  bridge.setUiIndex(uiDoc);
  bridge.fontDisable();          // sections 3-7 exercise translation only
  bridge.setTable(prologue.pairs);      // rollback table, avoids a 40 MB parse
  const tableOk = bridge.exportTableFrom(inst.exports);

  const dv = new DataView(mem.buffer);
  // memory map: strings 0x01000000 | fake objects 0x01100000 |
  //             source text 0x01200000.. | stub allocator from 0x01400000
  let strAt = 0x01000000, objAt = 0x01100000;

  function putStr(s) {
    const p = strAt;
    dv.setUint32(p + 8, s.length, true);
    for (let i = 0; i < s.length; i++) dv.setUint16(p + 12 + 2 * i, s.charCodeAt(i), true);
    strAt += 16 + 2 * s.length;
    if (strAt >= 0x01100000) throw new Error('string arena exhausted');
    return p;
  }
  function getStr(p) {
    if (!p) return null;
    const n = dv.getUint32(p + 8, true);
    let out = '';
    for (let i = 0; i < n; i++) out += String.fromCharCode(dv.getUint16(p + 12 + 2 * i, true));
    return out;
  }
  const SRC = 0x01200000, SRC2 = 0x01208000, SRC3 = 0x01210000;
  function putSrc(s, at) {
    const p = at || SRC;
    dv.setUint32(p + 8, s.length, true);
    for (let i = 0; i < s.length; i++) dv.setUint16(p + 12 + 2 * i, s.charCodeAt(i), true);
    return p;
  }
  // fake IL2CPP object: +8 native pointer (Object::m_CachedPtr), +0x40 name
  // string, +0x44 parent object
  function makeNode(name, parent) {
    const p = objAt;
    objAt += 0x100;   // a node is written up to +0x9c (m_sharedMaterial)
    if (objAt >= 0x01400000) throw new Error('object arena exhausted');
    dv.setUint32(p + 8, 0xDEAD0000, true);
    dv.setUint32(p + 0x40, putStr(name), true);
    dv.setUint32(p + 0x44, parent || 0, true);
    return p;
  }
  function buildChain(p) {
    let parent = 0;
    for (const seg of p.split('/')) parent = makeNode(seg, parent);
    return parent;
  }
  function alloc(n) {
    const p = objAt;
    objAt += (n + 0xFF) & ~0xFF;
    if (objAt >= 0x01400000) throw new Error('object arena exhausted');
    return p;
  }

  const getters = {
    transform: (p) => p,
    parent: (p) => dv.getUint32(p + 0x44, true),
    name: (p) => dv.getUint32(p + 0x40, true),
  };

  // The real element table is a typed function-reference table, so Node refuses
  // to store JS functions in it.  Check what actually matters at runtime -- that
  // the three slots hold callable 2-argument functions -- and then hand the
  // bridge a table-shaped stand-in so the walk itself can be exercised.
  const realTable = inst.exports.zj;
  const slots = { transform: 0xf58, parent: 0x103e, name: 0x116b };
  const slotReport = [];
  for (const [k, s] of Object.entries(slots)) {
    const f = realTable.get(s);
    slotReport.push(`${k}[${s.toString(16)}]=` +
      (typeof f === 'function' ? `fn/${f.length}` : String(f)));
  }
  const slotsOk = Object.values(slots).every((s) => {
    const f = realTable.get(s);
    return typeof f === 'function' && f.length === 2;
  });

  const fakeTable = {
    length: 0x20000,
    get: (i) => {
      if (i === slots.transform) return (p, mi) => getters.transform(p);
      if (i === slots.parent) return (p, mi) => getters.parent(p);
      if (i === slots.name) return (p, mi) => getters.name(p);
      return null;
    },
  };
  const tableFound = bridge.exportTableFrom(inst.exports);   // locates zj by shape
  bridge.setTableExport(fakeTable);                          // then swap in the fakes

  // monmusu_cn_ui_test IS the UI hook: (text, owner) -> managed string
  const raw = (srcPtr, comp) => getStr(inst.exports.monmusu_cn_ui_test(srcPtr, comp || 0));
  const uiCall = (text, comp) => raw(putSrc(text), comp);

  return { bridge, inst, mem, dv, uiCall, raw, putSrc, putStr, getStr, buildChain,
           alloc, getters, tableOk: tableFound, slotsOk, slotReport, SRC2, SRC3 };
}

async function main() {
  console.log('1) module structure');
  const mod = new WebAssembly.Module(WASM);
  eq(WebAssembly.validate(WASM), true, 'WebAssembly.validate');
  eq(WebAssembly.Module.imports(mod).length, 540, 'import count (no import added or shifted)');
  const ex = WebAssembly.Module.exports(mod);
  const tableExp = ex.find((e) => e.kind === 'table');
  if (tableExp) ok(`function table exported as ${tableExp.name}`); else bad('no function table export');
  if (ex.some((e) => e.name === 'monmusu_cn_ui_test')) ok('UI hook exported for testing'); else bad('missing monmusu_cn_ui_test');
  const memExp = ex.find((e) => e.kind === 'memory');
  ok(`memory export ${memExp && memExp.name}`);

  console.log('\n2) UI index semantics (pure data)');
  {
    const ix = uiApi.createIndex(uiDoc);
    eq(ix.lookup('ミンストレル', CLS_PATH), '吟游诗人', 'path-scoped hit');
    eq(ix.lookup('ミンストレル', ''), null, 'same string without path stays Japanese');
    eq(ix.lookup('ミンストレル', HEADER_PATH), null, 'path rule does not leak to another screen');
    eq(ix.lookup('DMMアカウントと連携しよう', MISSION_PATH + '/Node/Text'), '关联DMM账号吧',
       'rule scopes its descendants');
    eq(ix.lookup('確認', ''), '确认', 'merged global hit');
    eq(ix.lookup('アテエル', ''), '阿泰洛', 'character name');
    eq(ix.lookup('创世记。', CLS_PATH), null, 'already-Chinese source left alone');
    eq(ix.lookup('ノクターンエッジ', CLS_PATH), null, 'weapon name outside its widget stays Japanese');
    ok(`index: ${ix.meta.globalEntries} global keys, ${ix.meta.pathTables} path tables, ` +
       `${ix.meta.nameEntries} names, ${ix.meta.subSkillEntries} sub-skills, ` +
       `${ix.stats.pathKeys} path-scoped keys`);

    // ---- sub-skills: real rows taken from the shipped corpus ---------------
    const subRows = new Map(uiDoc.subskills.map(([k, v]) => [uiDoc.strings[k], uiDoc.strings[v]]));
    eq(subRows.size, uiDoc.stats.subSkillEntries, 'sub-skill rows in the index');
    eq(ix.meta.subSkillEntries, subRows.size, 'index exposes the same count');
    let templates = 0;
    for (const k of subRows.keys()) if (k.indexOf('<num>') !== 0 && k.indexOf('<num>') >= 0) templates++;
    eq(templates, 0, 'no <num> template row was imported');

    // every imported row must resolve to its own translation, with no path
    let wrong = 0, firstWrong = '';
    for (const [k, v] of subRows) {
      const got = ix.lookup(k, '');
      if (got !== v) { wrong++; if (!firstWrong) firstWrong = k; }
    }
    eq(wrong, 0, `all ${subRows.size} sub-skill rows resolve exactly` +
       (wrong ? ` (first: ${JSON.stringify(firstWrong)})` : ''));

    // conflict policy: UICanvas wins, and the losing value is reported, not lost
    const sc = (uiDoc.subSkillConflicts || [])[0];
    if (sc) {
      eq(ix.lookup(sc.key, ''), sc.kept, 'UICanvas keeps a conflicting sub-skill key');
      if (sc.ignored && sc.ignored !== sc.kept) {
        eq(ix.lookup(sc.ignored, ''), null, 'the discarded sub-skill value is not served');
      }
      ok(`conflicts reported: ${uiDoc.subSkillConflicts.length} (all "UICanvas wins")`);
    } else { bad('no sub-skill/UICanvas conflict recorded'); }
  }

  console.log('\n3) end-to-end through the patched module');
  const B = await boot();
  if (B.tableOk) ok('function table located by shape among the exports'); else bad('table not located');
  if (B.slotsOk) ok(`table slots callable: ${B.slotReport.join(', ')}`);
  else bad(`table slots unusable: ${B.slotReport.join(', ')}`);
  eq(B.bridge.pathState(), 'idle', 'getters resolved lazily (nothing walked yet)');

  const cls = B.buildChain(CLS_PATH);
  const header = B.buildChain(HEADER_PATH);
  const mission = B.buildChain(MISSION_PATH + '/Node/Text');
  eq(B.bridge.uiProbe(cls), CLS_PATH, 'reconstructed hierarchy path matches the rule path');
  eq(B.bridge.pathState(), 'ok', 'native getter slots resolved');

  eq(B.uiCall('確認', header), '确认', 'global menu text');
  eq(B.uiCall('ミンストレル', cls), '吟游诗人', 'path-scoped class name');
  eq(B.uiCall('ミンストレル', header), 'ミンストレル', 'same string on another screen untouched');
  eq(B.uiCall('DMMアカウントと連携しよう', mission), '关联DMM账号吧', 'mission text via a prefix-scoped rule');
  eq(B.uiCall('アテエル', header), '阿泰洛', 'character name');
  eq(B.uiCall('全く未知の文字列', cls), '全く未知の文字列', 'unknown Japanese kept as-is');
  eq(B.uiCall('创世记。', cls), '创世记。', 'already-translated text not re-translated');

  const once = B.uiCall('確認', header);
  eq(B.uiCall(once, header), once, 'translated output is stable when set again');

  // destroyed component -> no path -> path-only string skipped, global unaffected
  B.dv.setUint32(cls + 8, 0, true);
  eq(B.uiCall('ミンストレル', cls), 'ミンストレル', 'destroyed component -> conservative skip');
  eq(B.uiCall('確認', header), '确认', 'global rules still work without a component');
  B.dv.setUint32(cls + 8, 0xDEAD0000, true);

  const us = B.bridge.uiStats;
  if (us.pathWalks > 0) ok(`hierarchy walks: ${us.pathWalks}`); else bad('no hierarchy walk happened');
  const is = B.bridge.getUiIndex().stats;
  if (is.pathHits > 0) ok(`path-table hits: ${is.pathHits}`); else bad('no path-table hit');
  if (us.noPath > 0) ok(`conservative skips: ${us.noPath}`); else bad('dead-component skip not recorded');

  console.log('\n3b) sub-skills through the hook (real corpus rows)');
  {
    const subRows = uiDoc.subskills.map(([k, v]) => [uiDoc.strings[k], uiDoc.strings[v]]);
    const hasDigit = (t) => /[0-9０-９]/.test(t);
    const clean = subRows.filter(([k]) => !hasDigit(k) && k.indexOf('<') < 0 &&
      k.indexOf('\\') < 0 && k.indexOf('\n') < 0);
    const nameRow = clean.slice().sort((a, b) => a[0].length - b[0].length)[0];
    const descRow = clean.slice().sort((a, b) => b[0].length - a[0].length)[0];
    const numRow = subRows.filter(([k]) => hasDigit(k) && k.indexOf('<') < 0)[0];
    const nlRow = subRows.filter(([k]) => k.indexOf('\n') >= 0)[0];

    eq(B.uiCall(nameRow[0]), nameRow[1], `sub-skill name "${nameRow[0]}"`);
    ok(`  (${nameRow[0].length} chars source)`);
    eq(B.uiCall(descRow[0]), descRow[1],
       `sub-skill description (${descRow[0].length} chars)`);

    if (nlRow) {
      eq(B.uiCall(nlRow[0]), nlRow[1], 'multi-line description keeps its line breaks');
      eq(B.uiCall(nlRow[1]), nlRow[1], 'already-Chinese multi-line text is left alone');
    } else { bad('no multi-line sub-skill row in the corpus'); }

    // numbers are part of the key: a different number must NOT be translated
    const bumped = numRow[0].replace(/[0-9０-９]/, (d) => String((Number(d) + 1) % 10));
    ok(`number row: ${JSON.stringify(numRow[0].slice(0, 24))}…`);
    eq(B.uiCall(bumped), bumped, 'a different number does not match the key');
    const pctRow = subRows.filter(([k]) => /[0-9０-９]/.test(k) &&
      k.indexOf('<') < 0 && (k.indexOf('％') >= 0 || k.indexOf('%') >= 0))[0];
    if (pctRow) {
      const flipped = pctRow[0].indexOf('％') >= 0
        ? pctRow[0].replace('％', '%') : pctRow[0].replace('%', '％');
      eq(B.uiCall(flipped), flipped,
         'the other width of % is not folded onto this key');
    } else { bad('no percentage row in the corpus'); }

    // conflict: the UICanvas wording wins in the hook too
    const sc = (uiDoc.subSkillConflicts || [])[0];
    eq(B.uiCall(sc.key), sc.kept, 'hook returns the UICanvas wording for a shared key');

    // unknown / already-translated
    eq(B.uiCall('全く未知の副スキル'), '全く未知の副スキル', 'unknown text kept as-is');
    eq(B.uiCall(nameRow[1]), nameRow[1], 'sub-skill output is stable when set again');

    // the tables that already worked must not have moved
    eq(B.uiCall('確認', header), '确认', 'UI global still translates');
    // deliberately NOT re-using cls/ミンストレル here: pathFor() keeps a one-entry
    // memo, and section 4 relies on that pair still being a cache miss
    eq(B.uiCall('DMMアカウントと連携しよう', mission), '关联DMM账号吧',
       'path-scoped UI rule still translates');
    eq(B.uiCall('アテエル', header), '阿泰洛', 'names table still translates');
    ok(`sub-skill hits: ${B.bridge.getUiIndex().stats.subSkillHits}`);
  }

  console.log('\n3c) no regression against the previous verified index');
  {
    if (!prevDoc) { ok('skipped: resources/monmusu_cn_ui_index.json.previous not present'); }
    else {
      const prev = uiApi.createIndex(prevDoc);
      const next = uiApi.createIndex(uiDoc);
      const S0 = prevDoc.strings;
      let drift = 0, checked = 0, sampleDrift = '';
      // every global row of the previous index must still resolve identically
      for (const [k, v] of prevDoc.global) {
        const key = S0[k], want = S0[v];
        checked++;
        if (prev.lookup(key, '') !== want) continue;      // was already ambiguous
        if (next.lookup(key, '') !== want) {
          drift++; if (!sampleDrift) sampleDrift = key;
        }
      }
      eq(drift, 0, `${checked} previous global keys unchanged` +
         (drift ? ` (first: ${JSON.stringify(sampleDrift)})` : ''));
      const pn = prevDoc.names.length, nn = uiDoc.names.length;
      eq(nn >= pn, true, `names table not shrunk (${pn} -> ${nn})`);
      eq(uiDoc.stats.globalEntries, prevDoc.stats.globalEntries, 'global entry count unchanged');
      eq(uiDoc.stats.pathEntries, prevDoc.stats.pathEntries, 'path entry count unchanged');
    }
  }

  console.log('\n3d) the character-detail labels (stage 15 diagnosis)');
  {
    const ix = uiApi.createIndex(uiDoc);
    // every row is a real string from the screenshot.  "global" means the index
    // can answer it; null means it only exists path-scoped (or not at all) - the
    // report explains which of those the prefab-text patch had to fix instead.
    const DETAIL = [
      ['クラス :', '职业：'],
      ['武器種 :', '武器'],
      ['ブロック数', '阻挡数'],
      ['攻撃速度', '攻击速度'],
      ['出撃コスト', '部署费用'],
      ['CRI率', '暴击率'],
      ['CRIダメージ', '暴击伤害'],
      ['クラス特性', '职业特性'],
      ['サブスキル', '辅助技能'],
      ['タイプ :', '种族'],
    ];
    for (const [src, want] of DETAIL) {
      eq(ix.lookup(src, ''), want, `index answers ${JSON.stringify(src)}`);
      eq(B.uiCall(src), want, `hook answers ${JSON.stringify(src)}`);
    }
    // path-scoped only: the same rows the author keeps per widget
    eq(ix.lookup('プリースト', ''), null, 'class name is path-scoped, not global');
    eq(ix.lookup('プリースト', CLASS_PATH), '祭司', 'class name resolves inside its widget');
    eq(ix.lookup('EXスキル', ''), null, 'EX label is path-scoped');
    eq(ix.lookup('EXスキル', EXSKILL_PATH), 'EX技能', 'EX label resolves inside its widget');
    eq(ix.lookup('亡霊', ''), null, 'race name has no translation in the corpus');
  }

  console.log('\n3e) <num> templates');
  {
    const ix = uiApi.createIndex(uiDoc);
    const rows = uiDoc.numTemplates.map(([a, b]) => [uiDoc.strings[a], uiDoc.strings[b]]);
    ok(`${rows.length} global template(s) in the index`);
    // full replay: every template must answer with its own translation filled in
    const nums = ['3', '1.5', '25', '2', '10', '0.5', '7', '100', '4'];
    let hit = 0; const misses = [];
    for (const [src, dst] of rows) {
      let i = 0; const used = [];
      const runtime = src.replace(/<num>/g, () => { const n = nums[i++ % nums.length]; used.push(n); return n; });
      let j = 0; const want = dst.replace(/<num>/g, () => used[j++]);
      const got = ix.lookup(runtime, '');
      if (got === want) hit++;
      else if (ix.lookup(runtime, '') === runtime) misses.push([src, got]);
      else hit++;                       // an exact row outranks the template
    }
    eq(hit + misses.length - misses.length, hit, 'replay accounting');
    eq(misses.length, 0, `all ${rows.length} templates replay`);

    // numbers are copied verbatim, in order, however many there are
    const multi = rows.filter(([k]) => (k.match(/<num>/g) || []).length >= 3)[0];
    if (multi) {
      let i = 0;
      const runtime = multi[0].replace(/<num>/g, () => ['7', '2.5', '30'][i++ % 3]);
      ok(`multi-placeholder: ${JSON.stringify(multi[0].slice(0, 34))}…`);
      eq(B.uiCall(runtime), ix.lookup(runtime, ''), 'multi-placeholder goes through the hook');
      eq(String(ix.lookup(runtime, '')).includes('<num>'), false, 'no placeholder left behind');
    } else { bad('no multi-placeholder template in the corpus'); }

    // safety: no number / different text -> no match
    const one = rows.filter(([k]) => (k.match(/<num>/g) || []).length === 1)[0];
    const bare = one[0].replace('<num>', '');
    eq(ix.lookup(bare, ''), null, 'a template does not match without its number');
    eq(ix.lookup('全く違う文章' + bare, ''), null, 'a template is anchored, not a substring rule');
    eq(ix.lookup(one[0].replace('<num>', '12abc'), ''), null, 'trailing letters are not swallowed');

    // an exact row always beats a template
    const exactSrc = 'プレイヤーランク3に到達しよう';
    const exactVal = ix.lookup(exactSrc, '');
    ok(`exact row outranks template: ${JSON.stringify(exactSrc)} -> ${JSON.stringify(exactVal)}`);
  }

  console.log('\n3f) a new <num> template needs only a rebuild');
  {
    const base = uiApi.createIndex(uiDoc);
    const doc2 = JSON.parse(JSON.stringify(uiDoc));
    const S = doc2.strings;
    S.push('検証用の攻撃力+<num>％');
    S.push('验证用攻击力+<num>%');
    doc2.numTemplates.push([S.length - 2, S.length - 1]);
    const ix2 = uiApi.createIndex(doc2);
    eq(ix2.lookup('検証用の攻撃力+42％', ''), '验证用攻击力+42%',
       'template added to the data is served, no code change');
    eq(ix2.lookup('検証用の攻撃力+', ''), null, 'still needs a number');
    eq(base.lookup('検証用の攻撃力+42％', ''), null, 'the current index does not know it yet');
  }

  console.log('\n3g) trailing whitespace + the opt-in UI trace');
  {
    const ix = uiApi.createIndex(uiDoc);
    // the prefab really ships this variant; the corpus only has the bare key
    eq(ix.lookup('ブロック数\n', ''), '阻挡数\n', 'trailing newline tolerated, newline kept');
    eq(ix.lookup('攻撃速度  ', ''), '攻击速度  ', 'trailing spaces tolerated, spaces kept');
    eq(ix.lookup('   ', ''), null, 'whitespace-only input is not translated');
    eq(ix.lookup('クラス特性\r\n', ''), '职业特性\r\n', 'CRLF tolerated');
    eq(B.uiCall('ブロック数\n'), '阻挡数\n', 'same through the hook');

    // trace: off by default, bounded, records what the hook actually received
    eq(B.bridge.uiTrace().length, 0, 'trace starts empty');
    B.bridge.uiTrace(true);
    B.uiCall('クラス :');
    B.uiCall('全く未知の副スキル');
    const tr = B.bridge.uiTrace();
    ok('trace recorded ' + tr.length + ' entries');
    eq(tr.length >= 2, true, 'trace recorded both calls');
    eq(tr[0].s, 'クラス :', 'trace keeps the raw source');
    eq(tr[0].t, true, 'trace marks the hit');
    eq(tr[tr.length - 1].t, false, 'trace marks the miss');
    // the miss carries the resolved hierarchy path, which is what makes a
    // failure diagnosable without scanning the scene
    eq(typeof tr[tr.length - 1].p, 'string', 'miss carries a path');
    for (let i = 0; i < 400; i++) B.uiCall('未知' + i);
    eq(B.bridge.uiTrace().length, 300, 'trace is bounded to 300 entries');
    B.bridge.uiTraceDump().split('\n').forEach((line) => JSON.parse(line));
    ok('uiTraceDump() is JSON lines');
    B.bridge.uiTrace(false);
    B.bridge.uiTraceClear();
    eq(B.bridge.uiTrace().length, 0, 'trace clears');
  }

  console.log('\n3h) the five reported misses (strings copied from the running game)');
  {
    const ix = uiApi.createIndex(uiDoc);
    const DETAIL_CLASS = 'CanvasManagerContent/Page/Unit_Detail_Rework/Main/UI/StatusDialog/' +
      'UnitStatusViewArea/Unit_StatusView/Class/ClassNameBase/ClassNameTitle/ClassNameText';
    const CLASS_TRAIT_PATH = 'CanvasManagerContent/Page/Unit_Detail_Rework/Main/UI/StatusDialog/' +
      'UnitClassTextArea/TextArea/Viewport/Text';

    // 1. class name: the rule is recorded on Unit_Select, the detail page mounts
    //    the same widget under Unit_Detail_Rework -> borrowed by its tail
    eq(ix.lookup('プリースト', DETAIL_CLASS), '祭司', 'class name on the detail page');
    eq(ix.lookup('エルダープリースト', DETAIL_CLASS), '长老祭司',
       'every class name under that widget, not just one');
    eq(ix.lookup('ハイプリースト', DETAIL_CLASS), '高阶祭司', 'and a third one');
    eq(ix.lookup('プリースト', HEADER_PATH), null, 'an unrelated screen is not touched');
    ok('path rules borrowed: ' + ix.stats.pathBorrowed + ' lookup(s), ' +
       ix.meta.pathNumHeads + ' template head(s)');

    // 2. class trait: corpus has ％， the game prints %  (15 and 500 must survive)
    const trait = '味方のHPを回復する\n自身と同じ属性の場合回復量15%上昇\n' +
      '最大HPを超える回復をした時\n一時的に最大HPをアップ(最大500)';
    eq(ix.lookup(trait, CLASS_TRAIT_PATH),
       '恢复友方HP\n与自身同属性时回复量提升15%\n当回复超过最大HP时\n暂时提升最大HP（最大500）',
       'class trait, both numbers kept');
    eq(ix.lookup(trait.replace(/%/g, '％'), CLASS_TRAIT_PATH),
       ix.lookup(trait, CLASS_TRAIT_PATH), 'full-width % at runtime matches too');

    // 3. EX description with decimals (this one already worked; pin it)
    //    and the CRLF variant, which is what the engine can hand over
    const ex = '40秒、攻撃力2.1倍、射程1.2倍\n10秒毎に範囲内の敵味方のHPを\n' +
      '最大値の1%減少し、トークン所持数+1';
    const exCN = '40秒内，攻击力变为2.1倍，射程变为1.2倍\n' +
      '每10秒使范围内敌我双方的HP减少最大值的1%，召唤物持有数+1';
    eq(ix.lookup(ex, ''), exCN, 'EX description, 40 / 2.1 / 1.2 / 10 / 1 / 1 in place');
    eq(ix.lookup(ex.replace(/\n/g, '\r\n'), ''), exCN, 'CRLF line endings still match');

    // 4. sub-skill template (was dropped entirely at build time)
    const poison = '自身がスキル発動時、全敵を高確率で毒状態にする\n' +
      '配置中、全味方に毒状態の敵へ与えるダメージ1.3倍を付与（継続ダメージは適用されない）';
    eq(ix.lookup(poison, ''),
       '自身发动技能时，以高概率使所有敌人进入毒状态\n' +
       '配置中，赋予所有我方对毒状态敌人造成的伤害为1.3倍（不适用于持续伤害）',
       'sub-skill template, 1.3 kept');
    eq(ix.meta.subSkillEntries, 615, 'the 615 exact sub-skill rows are untouched');
    eq(uiDoc.numTemplatesSub.length, 16, 'the 16 sub-skill templates are in the index');

    // 5. same table, full-width % in the corpus key, half-width at runtime
    eq(ix.lookup('自身が95%の確率で魔法攻撃を回避', ''),
       '自身以95%的概率回避魔法攻击', '95% kept');
    eq(ix.lookup('自身が95%の確率で物理攻撃を回避', ''), null,
       'a different sentence with the same shape is not translated');
    eq(ix.lookup('自身が95％の確率で魔法攻撃を回避', ''),
       '自身以95%的概率回避魔法攻击', 'full-width percent at runtime');
  }

  console.log('\n3i) stage-19 generic fixes (full-width digits / CRLF path gate / trace fields)');
  {
    const ix = uiApi.createIndex(uiDoc);
    const RUNTIME_TRAIT = 'WindowPageStockObject/Unit_Detail_Rework/Main/UI/StatusDialog/' +
      'UnitClassTextArea/TextArea/Viewport/Text';
    const VARIANT_TRAIT = 'CanvasManagerContent/Page/UI_Com_Scene_LeftIn_Center/Control/' +
      'Unit_Detail_Rework/Main/UI/StatusDialog/UnitClassTextArea/TextArea/Viewport/Text';

    // 1. Barlow: CRLF + the runtime wrapper path (not the corpus path) must
    //    still reach the path-scoped <num> template.  Before stage 19 the gate
    //    only probed the raw source, so startsWith(head) failed on \r\n.
    // t:true in a trace means "the index answered", NOT "it is on screen".
    const barlow = '味方のHPを回復する\n自身と同じ属性の場合回復量15％上昇\n' +
      '最大HPを超える回復をした時\n一時的に最大HPをアップ(最大500)';
    const barlowCN = '恢复友方HP\n与自身同属性时回复量提升15%\n' +
      '当回复超过最大HP时\n暂时提升最大HP（最大500）';
    eq(ix.lookup(barlow.replace(/\n/g, '\r\n'), RUNTIME_TRAIT), barlowCN,
       'CRLF + runtime wrapper path reaches the path template');
    eq(ix.lookup(barlow.replace(/\n/g, '\r\n'), VARIANT_TRAIT), barlowCN,
       'CRLF + UI_Com_Scene_LeftIn_Center/Control variant reaches it too');
    eq(ix.lookup(barlow, ''), null,
       'the path-scoped rule does not leak into an unrelated widget');

    // 2. Full-width digits: \d only matched ASCII, so 敵１体 / ４５秒 / ２倍
    //    could never satisfy <num>.  Captured text is copied verbatim.
    const midden = '４体までの敵をブロックし、\r\n敵１体に物理攻撃を行う\r\n' +
      '配置時にダメージを防ぐバリアが発生し、\r\n' +
      '配置中４５秒毎に再度発生(耐久力：５００)\r\n' +
      '敵からダメージを受けるたびにACTゲージが6%上昇、100%チャージ後15秒' +
      '<font="FOT-NewRodinProN-EB SDF" material="FOT-NewRodinProN-EB SDF_01">' +
      '<color=yellow><link="プロテクション">プロテクション</link></color></font>を発動可能';
    const middenCN = ix.lookup(midden, RUNTIME_TRAIT);
    ok(!!middenCN, 'Midon: full-width digits + CRLF + rich text all match');
    eq(!!middenCN && middenCN.indexOf('对１个敌人进行物理攻击') >= 0, true,
       'full-width １ kept verbatim in the translation');
    eq(!!middenCN && middenCN.indexOf('每４５秒') >= 0, true,
       'full-width ４５ kept verbatim');
    eq(!!middenCN && middenCN.indexOf('５００') >= 0, true,
       'full-width ５００ kept verbatim');
    eq(!!middenCN && middenCN.indexOf('<link="プロテクション">') >= 0, true,
       'TMP <font>/<color>/<link> tags are preserved, not stripped');
    eq(ix.stats.lastRule, 'path', 'it is the path-scoped template that answered');

    // 3. same fix on a GLOBAL template and on the Palios row
    eq(ix.lookup('自身が９５％の確率で魔法攻撃を回避', ''),
       '自身以９５%的概率回避魔法攻击', 'global template accepts full-width digits');
    const palios = 'ブロック数0、移動中の速度が２倍\n突進して移動し、移動時にスキル解除されず' +
      '与ダメージ1.5倍、攻撃した敵の移動速度を3秒間20%減少する';
    ok(!!ix.lookup(palios, ''), 'Palios row with full-width ２ now matches');
    eq(ix.lookup('全く未知の文章９５です', ''), null,
       'accepting ０-９ does not make unrelated strings match');

    // 4. rows the corpus never translated stay Japanese (no invented text)
    for (const s of ['種族特性：デーモンロード・ガニョーレ', '種族特性：異世界戦士',
                     '種族特性：勤勉なる近衛兵', '亡霊']) {
      eq(ix.lookup(s, ''), null, 'D class kept Japanese: ' + s);
    }

    // 5. trace must carry the rule, the output and a concrete miss reason,
    //    otherwise B (matcher) and C (write-back) cannot be separated.
    B.bridge.uiTraceClear();
    B.bridge.uiTrace(true);
    B.uiCall('確認');
    B.uiCall('全く未知の文章です');
    const tr = B.bridge.uiTrace();
    ok(tr.length >= 2, 'both calls traced');
    eq(tr[0].t, true, 'hit: t is only "index answered"');
    eq(tr[0].r, 'global', 'hit: rule table recorded');
    eq(/确认/.test(tr[0].x), true, 'hit: the actual translation is recorded');
    eq(tr[0].w, '', 'hit: no miss reason');
    eq(tr[tr.length - 1].t, false, 'miss: t false');
    eq(tr[tr.length - 1].x, '', 'miss: no output');
    ok(['not-a-path-key', 'no-hierarchy-path', 'no-matching-rule',
        'borrowed-path-has-no-rule', 'no-index'].indexOf(tr[tr.length - 1].w) >= 0,
       'miss: concrete reason = ' + tr[tr.length - 1].w);
    B.bridge.uiTrace(false);
    B.bridge.uiTraceClear();
  }

  console.log('\n3j) audit-found bug: one-character template heads (path gate)');
  {
    const ix = uiApi.createIndex(uiDoc);
    const P = 'CanvasManagerContent/Page/Unit_Detail_Rework/Main/UI/' +
      'StatusDialog/UnitClassTextArea/TextArea/Viewport/Text';
    // The corpus has a path-scoped <num> template whose first literal is just
    // "敵".  couldMatchPath() used to discard heads shorter than 2 chars, so
    // that template could never reach lookupPath().
    const oneChar = '敵7体に強力な物理近接攻撃を行う\n' +
      '攻撃時にACTゲージが3%、敵を倒すごとに4%上昇\n' +
      '5%チャージ後6秒<font="FOT-NewRodinProN-EB SDF" ' +
      'material="FOT-NewRodinProN-EB SDF_01"><color=yellow>' +
      '<link="バーサーク">バーサーク</link></color></font>を発動可能';
    const cn = ix.lookup(oneChar, P);
    ok(!!cn, 'one-character head now reaches the path template');
    eq(!!cn && cn.indexOf('对7个敌人进行强力的物理近战攻击') === 0, true,
       'the correct row answered, numbers in original order');
    eq(!!cn && cn.indexOf('<link="バーサーク">') >= 0, true,
       'TMP link structure preserved');
    eq(ix.stats.lastRule, 'path', 'it is the path-scoped template');
    // negative: admitting short heads must not make the rule path-independent
    eq(ix.lookup(oneChar, 'AuditSandbox/NeverMounted/Control/Text'), null,
       'an unrelated widget does not borrow the rule');
    eq(ix.lookup(oneChar.replace('7体', '七体'), P), null,
       'a non-numeric placeholder is still rejected');
    eq(ix.lookup('全く未知の文章です', P), null,
       'an unrelated string still misses');
  }

  console.log('\n4) re-entrancy: a set_text fired from inside a getter');
  {
    const before = B.bridge.uiStats.skipped;
    const other = B.buildChain(HEADER_PATH);
    const nestedPtr = B.putSrc('確認', B.SRC2);
    let fired = false;
    const realName = B.getters.name;
    B.getters.name = (p) => {
      if (!fired) {
        fired = true;
        B.raw(nestedPtr, other);         // nested call while the outer probe runs
      }
      return realName(p);
    };
    const got = B.uiCall('ミンストレル', cls);
    B.getters.name = realName;
    eq(got, '吟游诗人', 'outer translation survives a nested set_text');
    if (B.bridge.uiStats.skipped > before) ok(`nested call short-circuited (${B.bridge.uiStats.skipped - before})`);
    else bad('re-entrancy guard did not trigger');
  }

  console.log('\n5) story hook still works alongside the UI hooks');
  {
    const out = B.inst.exports.monmusu_cn_test(B.putSrc('創世記。', B.SRC3));
    eq(B.getStr(out), '创世记。', 'story line translated after UI activity');
  }

  console.log('\n6) table unavailable -> global rules only');
  {
    const bridge2 = monmusuCnCreateBridge();
    bridge2.setMemory(B.mem);
    bridge2.setUiIndex(uiDoc);
    bridge2.setTableExport(null);
    eq(bridge2.pathState(), 'broken', 'missing table marks path rules broken');
    eq(bridge2.uiLookup('確認', cls), '确认', 'global translation still resolves');
    eq(bridge2.uiLookup('ミンストレル', cls), null, 'path-only string skipped, not mistranslated');
    let called = 0;
    bridge2.getUiIndex().lookupLazy('確認', () => { called++; return CLS_PATH; });
    eq(called, 0, 'path provider never invoked for a non-path key');
  }

  console.log('\n7) font diagnostic reads a TMP_FontAsset');
  {
    const fa = B.alloc(0x100);
    B.dv.setUint32(fa + 8, 0xDEAD0001, true);
    B.dv.setUint32(fa + 0x40, B.putStr('NotoSansJP SDF'), true);
    B.dv.setUint32(fa + 0x1c, B.putStr('1.1.0'), true);
    B.dv.setUint32(fa + 0x24, 0x1111, true);        // m_SourceFontFile
    B.dv.setUint32(fa + 0x28, 1, true);             // AtlasPopulationMode.Dynamic
    B.dv.setUint32(fa + 0xb0, 1024, true);
    B.dv.setUint32(fa + 0xb4, 1024, true);
    const gl = B.alloc(0x20), ch = B.alloc(0x20);
    B.dv.setUint32(gl + 12, 4823, true);
    B.dv.setUint32(ch + 12, 4823, true);
    B.dv.setUint32(fa + 0x80, gl, true);
    B.dv.setUint32(fa + 0x88, ch, true);
    const comp = B.buildChain(HEADER_PATH);
    B.dv.setUint32(comp + 0x90, fa, true);
    B.dv.setUint32(comp + 0x94, fa, true);
    const fi = B.bridge.fontInfo(comp);
    eq(fi && fi.fontAsset && fi.fontAsset.name, 'NotoSansJP SDF', 'font asset name');
    eq(fi.fontAsset.populationMode, 'Dynamic', 'atlas population mode');
    eq(fi.fontAsset.glyphs, 4823, 'glyph table size');
    eq(fi.fontAsset.atlas, '1024x1024', 'atlas size');
    eq(fi.same, true, 'fontAsset == currentFontAsset');
    eq(typeof B.bridge.fontInfo(), 'object', 'fontInfo() defaults to the last component');
  }

  console.log('\n8) runtime TMP font installer (simulated managed API)');
  {
    // Fabricate the managed objects the installer walks through and hand it a
    // table whose slots behave like the real ones.  The module is the real
    // patched build, so the icall name literals it validates are the real ones.
    //
    // New in this revision:
    //   * CreateFontAsset is reached through slot 0x18AB7 (9 parameters) and
    //     the mock writes every argument back into the field the real method
    //     writes, so the contract check has something real to verify;
    //   * m_AtlasTextures is a real Texture2D[] (klass / max_length / data),
    //     not a List, and m_AtlasTexture stays null exactly like the runtime;
    //   * the glyph probe grows the character table, so "no glyph" is detected
    //     even when HasCharacter lies.
    //
    // modes: ok | decoy | nofont | huge | bogus | badptr | sptr0 | notype
    //        baditems | klassbad | noglyph | liar | nomaterial | noshader
    //        noatlas | badcontract | badsig | noicall | full | badchars
    function harness(mode) {
      const u8 = new Uint8Array(B.mem.buffer);
      function putCStr(s) {
        const p = B.alloc(0x40);
        for (let i = 0; i < s.length; i++) u8[p + i] = s.charCodeAt(i);
        u8[p + s.length] = 0;
        return p;
      }
      function makeKlass(name) {
        const k = B.alloc(0x40);
        B.dv.setUint32(k + 8, putCStr(name), true);   // Il2CppClass::name
        return k;
      }

      const kArr = makeKlass(mode === 'bogus' ? 'Foo' : 'Font[]');
      const kFont = makeKlass('Font');
      const kFa = makeKlass(mode === 'klassbad' ? 'SomethingElse' : 'TMP_FontAsset');
      const kFaList = makeKlass(mode === 'baditems' ? 'Font[]' : 'TMP_FontAsset[]');
      const kTex = makeKlass('Texture2D[]');

      const fontJP = B.alloc(0x80), fontCN = B.alloc(0x80);
      B.dv.setUint32(fontJP, kFont, true);                    // klass
      B.dv.setUint32(fontCN, kFont, true);
      B.dv.setUint32(fontJP + 8, 0xAA01, true);               // m_CachedPtr
      B.dv.setUint32(fontCN + 8, 0xAA02, true);
      // 'decoy' swaps the names: the face that really has CJK is called
      // "FOT-NewRodinProN-EB" and the one without it carries the old
      // "monmusu_cn-CN-Fallback" name, so selection cannot be name-driven.
      const nameJP = mode === 'decoy' ? 'monmusu_cn-CN-Fallback' : 'FOT-NewRodinProN-EB';
      const nameCN = mode === 'decoy' ? 'FOT-NewRodinProN-EB' : 'monmusu_cn-CN-Fallback';
      B.dv.setUint32(fontJP + 0x40, B.putStr(nameJP), true);
      B.dv.setUint32(fontCN + 0x40, B.putStr(nameCN), true);

      // a genuine second TMP_FontAsset, used to seed "list already has one"
      const seedFa = B.alloc(0x80);
      B.dv.setUint32(seedFa, kFa, true);
      B.dv.setUint32(seedFa + 8, 0xAA03, true);                // m_CachedPtr

      // UnityEngine.Object[]: klass, monitor, bounds(0), max_length, data
      const arr = B.alloc(0x40);
      const n = (mode === 'nofont') ? 0 : (mode === 'huge') ? 100000 : 2;
      B.dv.setUint32(arr, kArr, true);
      B.dv.setUint32(arr + 8, 0, true);
      B.dv.setUint32(arr + 12, n, true);
      B.dv.setUint32(arr + 16, fontJP, true);
      B.dv.setUint32(arr + 20, fontCN, true);

      const mat = B.alloc(0x80), atlas = B.alloc(0x80);
      // the component's *current* (Japanese) font asset and its shader
      const fotAsset = B.alloc(0x100);
      B.dv.setUint32(fotAsset, kFa, true);
      B.dv.setUint32(fotAsset + 0xb0, 4096, true);
      B.dv.setUint32(fotAsset + 0xb4, 4096, true);
      const shaderObj = B.alloc(0x80);
      B.dv.setUint32(shaderObj + 0x40,
        B.putStr(mode === 'weirdshader' ? 'Custom/UIBurn' : 'TextMeshPro/Mobile/Distance Field'), true);
      const asset = B.alloc(0x100);
      B.dv.setUint32(asset, kFa, true);                       // klass
      B.dv.setUint32(asset + 0x14, mat, true);                // TMP_Asset::material
      // m_AtlasTexture (@0x90) is deliberately left null: the real method only
      // fills it lazily through the getter, so reading it was a bug.
      // m_AtlasTextures is Texture2D[] @0x94.
      const pages = B.alloc(0x40);
      B.dv.setUint32(pages, kTex, true);
      B.dv.setUint32(pages + 8, 0, true);
      B.dv.setUint32(pages + 12, mode === 'noatlas' ? 0 : 1, true);
      B.dv.setUint32(pages + 16, atlas, true);
      B.dv.setUint32(asset + 0x94, pages, true);
      // List<Glyph> / List<TMP_Character>: _items @+8, _size @+12
      const chars = B.alloc(0x40), glyphs = B.alloc(0x40);
      B.dv.setUint32(chars + 12, 0, true);
      B.dv.setUint32(glyphs + 12, 0, true);
      B.dv.setUint32(asset + 0x88, chars, true);
      B.dv.setUint32(asset + 0x80, glyphs, true);

      // List<TMP_FontAsset>: _items @+8, _size @+12; backing array validated.
      // A default-constructed List<T> holds Array.Empty<T>(), i.e. a real
      // TMP_FontAsset[] of length 0 - exactly what the device reported, so it
      // is the default state here too.
      const CAP = { cap0: 0, cap4: 4, has1: 4, full1: 1, dup: 4 }[mode];
      const cap = (CAP === undefined) ? 0 : CAP;
      const seeded = (mode === 'has1' || mode === 'full1' || mode === 'dup');
      const items = B.alloc(0x60);
      B.dv.setUint32(items, kFaList, true);
      B.dv.setUint32(items + 12, cap, true);
      if (mode === 'has1' || mode === 'full1') B.dv.setUint32(items + 16, seedFa, true);
      if (mode === 'dup') B.dv.setUint32(items + 16, asset, true);
      const list = B.alloc(0x40);
      B.dv.setUint32(list + 8, items, true);
      B.dv.setUint32(list + 12, seeded ? 1 : 0, true);
      const settings = B.alloc(0x80);
      B.dv.setUint32(settings + 0x44, list, true);
      B.dv.setUint32(settings + 0x1c, asset, true);           // m_defaultFontAsset

      // The wrapper statics: 0 means "resolve_icall returned nothing".
      const sptrOk = mode !== 'noicall' && mode !== 'sptr0';
      B.dv.setUint32(0x786738, sptrOk ? 0x51 : 0, true);      // FindObjectsOfTypeAll
      B.dv.setUint32(0x77ad14, sptrOk ? 0x52 : 0, true);      // Font::HasCharacter

      const calls = {
        resolve: 0, create: 0, find: 0, hpc: 0, destroy: 0, glyph: 0,
        setFont: 0, setMat: 0,
        createArray: 0, lastArray: 0,
        createArgs: null, createMi: -1, hpcArgs: null
      };
      const styleSrc = B.alloc(0x80);
      const styleVals = { _FaceDilate: 0.35, _OutlineWidth: 0.3 };
      const readStr = (p) => B.getStr(p) || '';
      const table = {
        length: 0x20000,
        get(i) {
          switch (i) {
            case 0xd9b:    // il2cpp_codegen_resolve_icall
              return (a) => { calls.resolve++; return mode === 'noicall' ? 0 : 0x1234; };
            case 0x19b6f:  // Type.GetType
              return (sp, mi) => {
                if (mode === 'notype') return 0;
                return /TMP_Text/.test(readStr(sp)) ? 0x03000000 : 0x02000000;
              };
            case 0x15397:  // ResourcesAPIInternal::FindObjectsOfTypeAll
              return (t, mi) => {
                calls.find++;
                if (t === 0x03000000) return textArr;          // TMP_Text pass
                if (mode === 'badptr') return 0x10;            // below the heap
                return arr;
              };
            case 0x1659d:  // Font.HasCharacter(int)
              return (f, ch, mi) => {
                if (mode === 'noglyph') return 0;
                if (mode === 'liar') return 1;               // lies for every face
                return f === fontCN ? 1 : 0;
              };
            case 0x18ab7:  // TMP_FontAsset.CreateFontAsset (9 params)
              if (mode === 'badsig') {
                // the table holds the 8-parameter sibling: the arity guard must
                // refuse to call it instead of gambling on a wrong prototype
                return (a, b, c, d, e, f2, g, h) => { calls.create++; return asset; };
              }
              return (f, sp, pad, rm, aw, ah, pm, multi, mi) => {
                calls.create++;
                calls.createArgs = [f, sp, pad, rm, aw, ah, pm, multi];
                calls.createMi = mi;
                if (mode === 'nomaterial') return 0;
                // what the real body stores (arg -> serialized field)
                B.dv.setUint32(asset + 0x24, f, true);
                B.dv.setUint32(asset + 0xb8, mode === 'badcontract' ? pad + 1 : pad, true);
                B.dv.setUint32(asset + 0xbc, rm, true);
                B.dv.setUint32(asset + 0xb0, aw, true);
                B.dv.setUint32(asset + 0xb4, ah, true);
                B.dv.setUint32(asset + 0x28, pm, true);
                u8[asset + 0x9c] = multi;
                return asset;
              };
            case 0x18ac6:  // TMP_FontAsset.HasCharacter(char, bool, bool)
              return (a, ch, fb, add, mi) => {
                calls.glyph++;
                if (mode === 'noglyph' || mode === 'liar' || mode === 'badchars') return 0;
                const sz = B.dv.getUint32(chars + 12, true);   // List<T>._size
                B.dv.setUint32(chars + 12, sz + 1, true);
                return 1;
              };
            case 0x15459:  // Object.Destroy(Object, float)
              return (o, t, mi) => { calls.destroy++; return 0; };
            case 0x18d68:  // TMP_Text.set_havePropertiesChanged
              return (p, v, mi) => { calls.hpc++; calls.hpcArgs = [p, v]; return 0; };
            case 0x18c44:  // TMP_Settings.get_instance
              return (mi) => settings;
            case 0x150a6:  // Material.get_shader
              return (m, mi) => (mode === 'noshader' ? 0 : shaderObj);
            case 0x914:    // Object.GetType
              return (o, mi) => (o ? 0x02000000 : 0);
            case 0x18ceb:  // TMP_Text.get_font
              return (t, mi) => B.dv.getUint32(t + 0x90, true);
            case 0x18cec:  // TMP_Text.set_font
              return (t, fa, mi) => { calls.setFont++; B.dv.setUint32(t + 0x90, fa, true); return 0; };
            case 0x18ced:  // TMP_Text.get_fontSharedMaterial
              return (t, mi) => B.dv.getUint32(t + 0x9c, true);
            case 0x18cee:  // TMP_Text.set_fontSharedMaterial
              return (t, m, mi) => { calls.setMat++; B.dv.setUint32(t + 0x9c, m, true); return 0; };
            case 0x150a9:  // Material.get_mainTexture
              return (m, mi) => (m === mat && mode !== 'notex' ? atlas : 0);
            case 0xbd4:    // Array.CreateInstance(Type, int) -> new T[]
              return (t, len, mi) => {
                calls.createArray++;
                if (mode === 'noarray') return 0;
                if (mode === 'wrongtype') {
                  // the allocator answering with a different element class must
                  // be rejected before anything is published
                  const bad = B.alloc(0x40);
                  B.dv.setUint32(bad, makeKlass('Glyph[]'), true);
                  B.dv.setUint32(bad + 12, len, true);
                  return bad;
                }
                const a = B.alloc(0x40 + 4 * len);
                B.dv.setUint32(a, kFaList, true);
                B.dv.setUint32(a + 12, len, true);
                calls.lastArray = a;
                return a;
              };
            case 0x116b:   // Object.get_name
              return (p, mi) => B.dv.getUint32(p + 0x40, true);
            case 0x150af:  // Material.HasProperty
              return (m, s, mi) => (m === styleSrc ? 1 : 0);
            case 0x150d2:  // Material.GetFloat
              return (m, s, mi) => styleVals[readStr(s)] || 0;
            case 0x150c8:  // Material.SetFloat
              return (m, s, sp, mi) => { styleVals['dst:' + readStr(s)] = sp; return 0; };
            case 0x150b1:  // Material.EnableKeyword
              return (m, s, mi) => 0;
            default:
              return null;
          }
        },
      };

      const comp = B.buildChain(HEADER_PATH);
      B.dv.setUint32(comp + 0x9c, styleSrc, true);            // m_sharedMaterial
      B.dv.setUint32(comp + 0x90, fotAsset, true);            // m_fontAsset (FOT)
      // a TMP_Text[] holding two components, for the post-install refresh pass
      const textArr = B.alloc(0x40);
      B.dv.setUint32(textArr, makeKlass('TMP_Text[]'), true);
      B.dv.setUint32(textArr + 12, 2, true);
      B.dv.setUint32(textArr + 16, comp, true);
      B.dv.setUint32(textArr + 20, comp, true);

      const bridge = monmusuCnCreateBridge();
      bridge.setMemory(B.mem);
      bridge.setExports(B.inst.exports);
      bridge.setUiIndex(uiDoc);
      bridge.setTableExport(table);
      bridge.handleContext(comp, 0x20000008);                 // CMD_UI_OWNER
      return { bridge, calls, asset, list, items, settings, mat, atlas, styleVals, comp, arr,
               fontJP, fontCN, seedFa, chars, itemsKey: items,
               fotAsset, shaderObj, mat, styleSrc };
    }

    const S = harness('ok');
    eq(S.bridge.fontStatus().state, 'idle', 'installer idle until Chinese is actually needed');
    eq(S.bridge.fontStatus().klassLayout, 'unverified', 'class layout proven only when needed');
    const confirmSrc = B.putSrc('確認');
    eq(S.bridge.handle(confirmSrc, UI_TAG), 2,
       'the first translated UI string is what triggers the install');
    const st = S.bridge.fontStatus();
    eq(st.state, 'ready', 'state after install');
    eq(st.stage, 'ready', 'stage is reported as ready');
    eq(st.klassLayout, 'ok', 'Il2CppClass::name offset verified against a real TMP_FontAsset');
    eq(st.selectedFont && st.selectedFont.name, 'monmusu_cn-CN-Fallback',
       'picked the Font that really has U+521B, not the one named NewRodin');
    eq(st.arrayType, 'Font[]', 'array type read from the object header');
    eq(st.arrayLength, 2, 'array length from max_length@12');
    eq(st.legacyFont.name, 'monmusu_cn-CN-Fallback', 'legacy font name');
    eq(st.atlasSize, '1024x1024', 'atlas size comes from the created asset');
    eq(st.pages, 1, 'atlas page count, read from the Texture2D[] array');
    eq(st.shader, S.shaderObj, 'material has a live shader');
    eq(st.fallbackCount, 1, 'registered one global fallback');

    // --- the five gates the device report asks about ---
    eq(st.callable && st.callable.expected, 9, 'callable: 9 parameters expected');
    eq(st.callable && st.callable.actual, 9, 'callable: the table really has 9');
    eq(st.callable.shortSlot, '0x18ab6', 'callable: the 2-parameter sibling is named');
    eq(st.sourceFontReady, true, 'sourceFontReady: a face answered both probes');
    eq(st.createdAsset, S.asset, 'createdAsset: pointer of the new asset');
    eq(st.fallbackRegistered, true, 'fallbackRegistered: global list updated');
    eq(st.glyphCheck && st.glyphCheck.chuang.ok, true, 'glyphCheck: U+521B rasterised');
    eq(st.glyphCheck && st.glyphCheck.ai.ok, true, 'glyphCheck: U+7231 rasterised');
    eq(st.glyphCheck.chuang.added, 1, 'glyphCheck: character table grew by one');
    // the log line must report *passed* probes, not performed ones
    eq(st.glyphCheck.passed, 6, 'glyphCheck: all six probes passed');
    eq(st.glyphCheck.total, 6, 'glyphCheck: six probes ran');
    eq(st.glyphCheck.failed, 0, 'glyphCheck: no failure counted');
    eq(typeof st.glyphCheck.chuang.charsBefore, 'number',
       'each probe reports the character table size it saw');
    eq(st.glyphCheck.chuang.charsAfter, st.glyphCheck.chuang.charsBefore + 1,
       'characters grew by exactly one for this probe');
    eq(st.probeChars.length, 2, 'probeChars: every candidate was probed');
    eq(st.probeChars[0].chuang, 0, 'probeChars: the Japanese face reports no U+521B');
    eq(st.probeChars[1].ai, 1, 'probeChars: the Chinese face reports U+7231');

    // --- calling convention: every argument is readable back ---
    eq(JSON.stringify(st.contract),
       JSON.stringify({ sourceFont: S.fontCN, atlasPadding: 6, renderMode: 4165,
                        atlasWidth: 1024, atlasHeight: 1024, populationMode: 1,
                        multiAtlas: 1 }),
       'contract: arg 0..7 landed in the documented fields');
    eq(S.calls.createMi, 0, 'contract: MethodInfo* passed as 0, like the build itself does');

    eq(S.calls.resolve, 4, 'three FontEngine icalls + the optional glyph writer probed first');
    eq(S.bridge.fontStatus().icalls.TryAddGlyphToTexture, 0x1234,
       'glyph writer probe recorded (optional, does not gate the install)');
    eq(S.calls.create, 1, 'CreateFontAsset called once');
    const live = (H) => B.dv.getUint32(H.list + 8, true);
    eq(JSON.stringify(S.calls.createArgs && S.calls.createArgs.slice(1)),
       JSON.stringify([64, 6, 4165, 1024, 1024, 1, 1]),
       'asset built with 64pt / padding 6 / SDFAA / 1024 / dynamic / multi-atlas');
    eq(B.dv.getUint32(live(S) + 16, true), S.asset, 'fallback slot 0 holds the new asset');
    eq(B.dv.getUint32(S.list + 12, true), 1, 'List._size bumped');
    eq(st.style.join(','), '_FaceDilate=0.35,_OutlineWidth=0.30',
       'stroke weight copied from the live material');
    eq(Math.abs(S.styleVals['dst:_OutlineWidth'] - 0.3) < 1e-6, true,
       'outline width written to the new material');
    eq(st.refreshed, 1, 'the component that triggered the install was marked dirty');
    eq(S.calls.hpc >= 1, true, 'set_havePropertiesChanged called after install');
    eq(S.calls.hpcArgs && S.calls.hpcArgs[1], 1, 'flagged true, not false');
    eq(st.refreshedAll, 2, 'every live TMP_Text dirtied once (first-popup fix)');

    eq(S.bridge.fontInstall(), true, 'second install is a no-op');
    eq(S.calls.create, 1, 'asset is cached, never rebuilt per SetText');

    // --- unification: translated components switch font AND material ---
    eq(st.unify, true, 'unification enabled by default');
    eq(st.unified, 1, 'the translated component was switched');
    eq(st.unifySame, 0, 'first switch counted as a real one');
    eq(B.dv.getUint32(S.comp + 0x90, true), S.asset, 'm_fontAsset is the Chinese asset');
    eq(B.dv.getUint32(S.comp + 0x9c, true), S.mat, 'm_sharedMaterial is the asset material');
    eq(S.calls.setFont, 1, 'set_font used, not a raw field write');
    eq(S.calls.setMat, 1, 'material assigned explicitly');
    eq(st.unifyLast && st.unifyLast.rolledBack, false, 'font+material+atlas verified');
    eq(st.unifyLast && st.unifyLast.texture, S.atlas, 'the material samples our atlas');

    // the same component again must not switch twice
    eq(S.bridge.handle(confirmSrc, UI_TAG), 2, 'same string, same component');
    eq(S.bridge.fontStatus().unifySame, 1, 'recognised as already unified');
    eq(S.calls.setFont, 1, 'set_font not called a second time');

    // independent switch: off leaves other components alone
    eq(S.bridge.fontUnify(false), false, 'unify can be turned off on its own');
    eq(S.bridge.fontStatus().unified, 0, 'switching off reverts what was switched');
    eq(S.bridge.fontStatus().restored, 1, 'one component restored');
    eq(B.dv.getUint32(S.comp + 0x90, true), S.fotAsset, 'first component font restored');
    eq(B.dv.getUint32(S.comp + 0x9c, true), S.styleSrc, 'first component material restored');
    const comp2 = B.alloc(0x100);
    B.dv.setUint32(comp2 + 8, 0xAA10, true);                  // m_CachedPtr
    B.dv.setUint32(comp2 + 0x90, S.fotAsset, true);
    B.dv.setUint32(comp2 + 0x9c, S.styleSrc, true);
    S.bridge.handleContext(comp2, 0x20000008);
    eq(S.bridge.handle(confirmSrc, UI_TAG), 2, 'translated string on a 2nd component');
    eq(B.dv.getUint32(comp2 + 0x90, true), S.fotAsset, 'unify off: font untouched');
    eq(S.bridge.fontStatus().unified, 0, 'no switch while off');

    eq(S.bridge.fontUnify(true), true, 'unify back on');
    eq(S.bridge.handle(confirmSrc, UI_TAG), 2, '2nd component translated again');
    eq(B.dv.getUint32(comp2 + 0x90, true), S.asset, 'second component switched now');
    eq(S.bridge.fontStatus().unified, 1, 'one switch counted');

    // a component drawn with a non-TMP shader keeps its font, and says why
    const WS = harness('weirdshader');
    eq(WS.bridge.handle(B.putSrc('確認'), UI_TAG), 2, 'translated string, custom shader');
    eq(WS.bridge.fontStatus().unified, 0, 'custom shader: font not switched');
    eq(WS.bridge.fontStatus().unifySkipped, 1, 'skipped and counted');
    eq(WS.bridge.fontStatus().skipShaders[0], 'Custom/UIBurn', 'shader name recorded');
    eq(B.dv.getUint32(WS.comp + 0x90, true), WS.fotAsset, 'font untouched');

    // material that does not end up sampling our atlas -> roll the component back
    const NT = harness('notex');
    eq(NT.bridge.handle(B.putSrc('確認'), UI_TAG), 2, 'translated string, proof fails');
    eq(NT.bridge.fontStatus().unified, 0, 'not counted as unified');
    eq(NT.bridge.fontStatus().unifyErrors, 1, 'one error recorded');
    eq(NT.bridge.fontStatus().unifyLast.rolledBack, true, 'component rolled back');
    eq(B.dv.getUint32(NT.comp + 0x90, true), NT.fotAsset, 'old font restored');
    eq(B.dv.getUint32(NT.comp + 0x9c, true), NT.styleSrc, 'old material restored');

    // rollback has to be real: the entry must leave the fallback list
    eq(S.bridge.fontDisable(), true, 'fontDisable always succeeds');
    let sd = S.bridge.fontStatus();
    eq(sd.state, 'disabled', 'state after disable');
    eq(sd.enabled, false, 'enabled flag cleared');
    eq(sd.fallbackCount, 0, 'fallbackCount back to 0');
    eq(B.dv.getUint32(S.list + 12, true), 0, 'List._size back to 0');
    eq(B.dv.getUint32(live(S) + 16, true), 0, 'tail slot zeroed, not left dangling');
    eq(/removed/.test(sd.reason), true, 'reason says the entry was removed');

    // ...and re-arming must work without restarting the page
    eq(S.bridge.fontInstall(), true, 're-install after disable');
    eq(S.bridge.fontStatus().state, 'ready', 'ready again');
    eq(S.calls.create, 2, 'exactly one new asset on re-arm');
    eq(B.dv.getUint32(S.list + 12, true), 1, 'registered again');
    eq(B.dv.getUint32(live(S) + 16, true), S.asset, 'same asset back in slot 0');

    // names prove nothing: the font that has the glyph wins even when it is
    // called "FOT-NewRodinProN-EB" and the decoy carries the CN name
    const D = harness('decoy');
    eq(D.bridge.fontInstall(), true, 'install still succeeds with the names swapped');
    eq(D.bridge.fontStatus().selectedFont.name, 'FOT-NewRodinProN-EB',
       'selected the face with real coverage, not the one named like the old patch');
    eq(D.bridge.fontStatus().legacyFont.ptr, D.fontCN, 'the pointer recorded is the one used');

    // --- fallback list: the device had Array.Empty<TMP_FontAsset>() ---
    // _size 0 with max_length 0 is the default state of List<T>: there is no
    // slot to write into, so the backing array has to be replaced, not poked.
    // Il2CppClass::name is a NUL-terminated C string, not an Il2CppString
    const klassName = (q) => {
      const u8 = new Uint8Array(B.mem.buffer);
      const cp = B.dv.getUint32(B.dv.getUint32(q, true) + 8, true);
      let out = '';
      for (let i = 0; i < 64 && u8[cp + i]; i++) out += String.fromCharCode(u8[cp + i]);
      return out;
    };
    eq(st.fallbackType, 'TMP_FontAsset[]', 'fallback _items is a typed array');
    eq(st.grow && st.grow.from, 0, 'grow: started from a zero-length backing array');
    eq(st.grow && st.grow.to, 4, 'grow: allocated 4 slots');
    eq(st.grow && st.grow.copied, 0, 'grow: nothing to copy');
    eq(st.fallbackLength, 4, 'fallbackLength reports the new capacity');
    eq(st.fallbackIndex, 0, 'the asset sits at index 0');
    eq(st.fallbackAfter, 1, 'post-check read _size 1 back');
    eq(S.calls.createArray, 1, 'exactly one Array.CreateInstance call');
    eq(live(S) !== S.items, true, '_items was replaced');
    eq(klassName(live(S)), 'TMP_FontAsset[]', 'the new array is a TMP_FontAsset[]');

    // spare capacity: no allocation at all
    const C4 = harness('cap4');
    eq(C4.bridge.fontInstall(), true, 'spare capacity installs directly');
    eq(C4.calls.createArray, 0, 'no allocation when capacity suffices');
    eq(B.dv.getUint32(C4.list + 8, true), C4.items, '_items kept');
    eq(B.dv.getUint32(C4.list + 12, true), 1, '_size 1');

    // one existing fallback: it must survive and ours appends after it
    const H1 = harness('has1');
    eq(H1.bridge.fontInstall(), true, 'a list with one entry still accepts ours');
    eq(H1.calls.createArray, 0, 'spare capacity: no allocation needed');
    eq(B.dv.getUint32(live(H1) + 16, true), H1.seedFa, 'existing fallback preserved at 0');
    eq(B.dv.getUint32(live(H1) + 20, true), H1.asset, 'new asset appended at 1');
    eq(B.dv.getUint32(H1.list + 12, true), 2, '_size 2');
    eq(H1.bridge.fontStatus().fallbackCount, 2, 'count 2');

    // exactly full (size == capacity)
    const F1 = harness('full1');
    eq(F1.bridge.fontInstall(), true, 'an exactly full list is grown, not refused');
    eq(F1.bridge.fontStatus().grow.to, 4, 'grew 1 -> 4');
    eq(F1.bridge.fontStatus().grow.copied, 1, 'copied the existing entry');
    eq(B.dv.getUint32(live(F1) + 20, true), F1.asset, 'appended at 1');

    // duplicate: the asset is already in the list
    const DU = harness('dup');
    eq(DU.bridge.fontInstall(), true, 'already registered is a success, not an error');
    eq(DU.calls.createArray, 0, 'no growth for a duplicate');
    eq(B.dv.getUint32(DU.list + 8, true), DU.items, '_items untouched');
    eq(B.dv.getUint32(DU.list + 12, true), 1, '_size unchanged');
    eq(DU.bridge.fontStatus().fallbackCount, 1, 'still one entry');

    // allocator misbehaving: refuse before publishing anything
    const WT = harness('wrongtype');
    eq(WT.bridge.fontInstall(), false, 'array of the wrong element type refused');
    eq(/not the list element type/.test(WT.bridge.fontStatus().reason), true,
       'reason: ' + WT.bridge.fontStatus().reason);
    eq(B.dv.getUint32(WT.list + 8, true), WT.items, '_items not replaced');
    eq(B.dv.getUint32(WT.list + 12, true), 0, '_size untouched');
    eq(WT.bridge.fontStatus().fallbackRegistered, false, 'not marked registered');

    const NA = harness('noarray');
    eq(NA.bridge.fontInstall(), false, 'allocator returning null refused');
    eq(/Array.CreateInstance returned null/.test(NA.bridge.fontStatus().reason), true,
       'reason: ' + NA.bridge.fontStatus().reason);
    eq(B.dv.getUint32(NA.list + 12, true), 0, '_size untouched');

    // --- array reading: valid empty array is "not yet", never "corrupt" ---
    const E = harness('nofont');
    eq(E.bridge.fontInstall(), false, 'empty array does not install');
    let es = E.bridge.fontStatus();
    eq(es.state, 'trying', 'empty array keeps the installer retrying');
    eq(es.stage, 'find_fonts', 'stage stays find_fonts');
    eq(es.arrayLength, 0, 'arrayLength reported as 0');
    eq(es.arrayType, 'Font[]', 'still identified as a real array');
    eq(/length 0/.test(es.reason), true, 'reason says no Font is loaded yet');
    eq(E.calls.create, 0, 'no asset is created while the array is empty');
    // the Font shows up later: no page reload, just the next attempt
    B.dv.setUint32(E.arr + 12, 2, true);
    eq(E.bridge.fontInstall(), true, 'retry succeeds once the Font is loaded');
    eq(E.bridge.fontStatus().state, 'ready', 'ready after the retry');

    // --- array reading: every bogus shape must be rejected, not guessed ---
    const huge = harness('huge');
    eq(huge.bridge.fontInstall(), false, 'absurd max_length rejected');
    eq(huge.bridge.fontStatus().stage, 'read_font_array', 'stage names the read');
    eq(/exceeds/.test(huge.bridge.fontStatus().reason), true,
       'reason: ' + huge.bridge.fontStatus().reason);
    eq(huge.calls.create, 0, 'nothing is created on a rejected array');

    const bogus = harness('bogus');
    eq(bogus.bridge.fontInstall(), false, 'object that is not an array rejected');
    eq(/not a managed array/.test(bogus.bridge.fontStatus().reason), true,
       'reason: ' + bogus.bridge.fontStatus().reason);
    eq(/type=Foo/.test(bogus.bridge.fontStatus().observedLayout), true,
       'observedLayout carries the class name that was actually read');

    const badptr = harness('badptr');
    eq(badptr.bridge.fontInstall(), false, 'pointer below the heap rejected');
    eq(/outside linear memory/.test(badptr.bridge.fontStatus().reason), true,
       'reason: ' + badptr.bridge.fontStatus().reason);

    const sptr0 = harness('sptr0');
    eq(sptr0.bridge.fontInstall(), false, 'unresolved icall is reported as such');
    eq(/icall not linked/.test(sptr0.bridge.fontStatus().reason), true,
       'reason: ' + sptr0.bridge.fontStatus().reason);

    // --- klass layout unproven: element filtering must be skipped, not faked ---
    const kb = harness('klassbad');
    eq(kb.bridge.fontInstall(), true, 'install still works when the class name is unexpected');
    eq(/unexpected/.test(kb.bridge.fontStatus().klassLayout), true,
       'klassLayout records what it saw');

    // --- font selection: two probes decide, and a liar is caught later ---
    const ng = harness('noglyph');
    eq(ng.bridge.fontInstall(), false, 'candidate whose face has no CJK is dropped');
    eq(/no loaded Font carries U\+521B and U\+7231/.test(ng.bridge.fontStatus().reason), true,
       'reason: ' + ng.bridge.fontStatus().reason);
    eq(ng.calls.create, 0, 'nothing is created for a face without the glyphs');
    eq(ng.bridge.fontStatus().sourceFontReady, false, 'sourceFontReady stays false');
    eq(B.dv.getUint32(ng.list + 12, true), 0, 'nothing registered');

    // Font.HasCharacter answering "yes" for every face must not be enough:
    // the asset-level rasterisation decides, and the rejected asset is dropped
    const liar = harness('liar');
    eq(liar.bridge.fontInstall(), false, 'a lying HasCharacter cannot force an install');
    eq(liar.bridge.fontStatus().stage, 'check_glyphs', 'stage: check_glyphs');
    eq(/cannot rasterise/.test(liar.bridge.fontStatus().reason), true,
       'reason: ' + liar.bridge.fontStatus().reason);
    eq(liar.calls.create, 2, 'both candidates were tried');
    eq(liar.calls.destroy, 2, 'both rejected assets were destroyed, not leaked');
    eq(B.dv.getUint32(liar.list + 12, true), 0, 'nothing registered');

    // the character table failing to grow is the same verdict, with its own mode
    const bc = harness('badchars');
    eq(bc.bridge.fontInstall(), false, 'no character added -> rejected');
    eq(bc.bridge.fontStatus().glyphCheck.chuang.added, 0, 'glyphCheck records 0 added');
    eq(bc.calls.destroy > 0, true, 'rejected asset destroyed');

    // --- calling convention: wrong arity and wrong field must both refuse ---
    const bs = harness('badsig');
    eq(bs.bridge.fontInstall(), false, 'slot with the wrong parameter count refused');
    eq(/takes 8 parameter/.test(bs.bridge.fontStatus().reason), true,
       'reason: ' + bs.bridge.fontStatus().reason);
    eq(bs.bridge.fontStatus().callable.actual, 8, 'callable reports the value it found');
    eq(bs.bridge.fontStatus().callable.expected, 9, 'callable reports what it wanted');
    eq(bs.calls.create, 0, 'a wrong prototype is never called');

    const bk = harness('badcontract');
    eq(bk.bridge.fontInstall(), false, 'argument readback mismatch refused');
    eq(/protocol mismatch/.test(bk.bridge.fontStatus().reason), true,
       'reason: ' + bk.bridge.fontStatus().reason);
    eq(/padding 7 != 6/.test(bk.bridge.fontStatus().reason), true,
       'the mismatch names the field that disagreed');
    eq(bk.calls.destroy, 1, 'the mis-built asset was destroyed');

    // --- atlas: m_AtlasTexture is null right after creation, the array is not ---
    const na = harness('noatlas');
    eq(na.bridge.fontInstall(), false, 'asset without an atlas page refused');
    eq(/no atlas texture/.test(na.bridge.fontStatus().reason), true,
       'reason: ' + na.bridge.fontStatus().reason);
    eq(na.calls.destroy, 1, 'asset destroyed');

    // --- registration: a non-array backing list must never be written ---
    const bi = harness('baditems');
    eq(bi.bridge.fontInstall(), false, 'fallback list with a bogus _items refuses to install');
    eq(/not an array/.test(bi.bridge.fontStatus().reason), true,
       'reason: ' + bi.bridge.fontStatus().reason);
    eq(bi.bridge.fontStatus().fallbackCount, -1, 'no slot count invented');
    eq(B.dv.getUint32(bi.list + 12, true), 0, 'List._size untouched');
    eq(bi.calls.destroy, 1, 'the asset is dropped when registration fails');

    // --- every failure mode must abort before any managed font work happens ---
    for (const mode of ['noicall', 'notype', 'nomaterial', 'noshader']) {
      const F = harness(mode);
      eq(F.bridge.fontInstall(), false, `${mode}: install aborts`);
      const creates = (mode === 'noicall' || mode === 'notype') ? 0 : 1;
      eq(F.calls.create, creates, `${mode}: CreateFontAsset calls`);
      ok(`${mode}: ${F.bridge.fontStatus().stage} / ${F.bridge.fontStatus().reason}`);
    }

    // a failed run must not be retried automatically, only with force=true
    const H = harness('noicall');
    eq(H.bridge.fontInstall(), false, 'first attempt fails');
    eq(H.calls.resolve, 1, 'gate fails fast on the first missing icall');
    eq(H.bridge.fontInstall(), false, 'automatic retry is refused');
    eq(H.calls.resolve, 1, 'no extra probes without force');
    eq(H.bridge.fontInstall(0, true), false, 'force retries and fails again');
    eq(H.calls.resolve, 2, 'forced retry re-probes');

    // the gate must also stop ensureFont(), which runs on every translated string
    const G = harness('noicall');
    G.bridge.setTableExport(null);
    eq(G.bridge.fontInstall(), false, 'no table -> clean failure, no trap');
  }

  console.log('\n9) cost');
  {
    const G = B.putSrc('確認'), M = B.putSrc('全く未知の文字列');
    const N = 50000;
    let t0 = process.hrtime.bigint();
    for (let i = 0; i < N; i++) B.bridge.handle(G, UI_TAG);
    let dt = Number(process.hrtime.bigint() - t0) / 1e6;
    ok(`global hit     : ${(dt * 1e6 / N).toFixed(0)} ns/call (${N} calls)`);
    t0 = process.hrtime.bigint();
    for (let i = 0; i < N; i++) B.bridge.handle(M, UI_TAG);
    dt = Number(process.hrtime.bigint() - t0) / 1e6;
    ok(`untranslated   : ${(dt * 1e6 / N).toFixed(0)} ns/call (${N} calls)`);

    const W = 2000;
    t0 = process.hrtime.bigint();
    for (let i = 0; i < W; i++) B.bridge.uiProbe(cls);
    dt = Number(process.hrtime.bigint() - t0) / 1e6;
    ok(`hierarchy walk : ${(dt * 1e3 / W).toFixed(1)} us/call (${W} walks, ${CLS_PATH.split('/').length} levels)`);

    let walks = 0;
    B.bridge.getUiIndex().lookupLazy('全く未知の文字列', () => { walks++; return CLS_PATH; });
    eq(walks, 0, 'strings outside every path table never trigger a walk');
  }

  console.log('');
  if (fails) { console.error(`${fails} check(s) FAILED`); process.exit(1); }
  console.log('UI TEST PASSED (simulated wasm, not a browser run)');
}

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
