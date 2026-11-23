/* monmusu_cn_shim.js - runtime translation bridge for the Monster Musume TD WebGL mod.
 *
 * Runs in the page (appended to ntd.loader.js) and is require()-able from Node
 * so the exact same code drives the local test harness.
 *
 * Two repurposed EXISTING wasm imports (no new import, no index shift):
 *
 *  import 336 (a.nf), (i32,i32)->i32   TRANSLATION protocol, unchanged:
 *     arg1 = Il2CppString* ; arg2 = TAG_TRANSLATE(0x40000000) | dstCharPtr
 *     dst == 0 -> probe, return translation length (0 = keep Japanese)
 *     dst != 0 -> write UTF-16 into the freshly allocated managed string
 *
 *     arg2 = UI_TAG(0x10000000)            UI probe, same contract
 *     arg2 = UI_TAG|UI_FILL(0x08000000)    UI fill into arg1
 *
 *  import 228 (a.md), (i32,i32)->i32   SCENE / UI CONTEXT protocol:
 *     arg1 = Il2CppString* (or 0) ; arg2 = TAG_CONTEXT(0x20000000) | command
 *     1 PARSE_NAME   2 PARSE_GRID   3 PARSE_END
 *     4 PLAYBACK_BEGIN              5 PLAYBACK_END
 *     6 CELL_NAME    7 CELL_SHEET
 *     8 UI_OWNER     arg1 = the TMP_Text component that is about to be set
 *
 * Calls without the corresponding tag are forwarded verbatim to the original
 * import, so the game's own single call site on each import is unaffected.
 *
 * UI hierarchy paths are rebuilt by calling the game's own native getters
 * (Component::get_transform / Transform::get_parent / Object::get_name) through
 * the exported function table.  That is only attempted when a source string
 * actually occurs in a path-scoped translation table, and any failure -- wrong
 * arity, dead object, or a trap -- disables path rules for the rest of the
 * session so a bad call can never take the game down.
 */
function monmusuCnCreateBridge() {
  'use strict';

  var TAG_TRANSLATE = 0x40000000;
  var TAG_CONTEXT = 0x20000000;
  var UI_TAG = 0x10000000;
  var UI_FILL = 0x08000000;
  var MASK = 0x3fffffff;
  var MAX_UNITS = 8192;
  var MAX_DEPTH = 64;

  /* ---- build bindings ------------------------------------------------------
     Every value below is a property of one specific Wasm: an element-table slot
     of a managed method or icall, the linear address of an icall name literal,
     a static cache word, or a managed field offset. DMM (/0/) and FANZA (/1/)
     ship different Wasm and 20 of these 24 slots move by 8 between them, so
     none of it may be written by hand. applyRuntimeConfig() fills them from the
     generated MONMUSU_CN_RUNTIME entry of the branch actually served.

     A null means "not resolved": every caller refuses to call instead of
     guessing a slot, because call_indirect through a wrong slot does not fail
     cleanly. The comments name the method each slot must resolve to; the
     binding is enforced at build time by tools/wasm/resolve_runtime.py.

     All three path getters are '(this, methodInfo) -> ptr' and re-initialise
     their own method info, so 0 is a valid second argument. */
  var SLOT_GET_TRANSFORM = null;
  var SLOT_GET_PARENT = null;
  var SLOT_GET_NAME = null;
  var OFF_CACHED_PTR = null;

  /* TMP_Text / TMP_FontAsset managed offsets (from dump.cs).  Read-only, used
     only by the font diagnostic: the font itself is not patched here. */
  var OFF_TEXT_FONTASSET = null;
  var OFF_TEXT_CURRENT_FA = null;
  var OFF_TEXT_SHARED_MAT = null;
  var OFF_FA_VERSION = null;
  var OFF_FA_SOURCE_FONT = null;
  var OFF_FA_POP_MODE = null;
  var OFF_FA_GLYPHS = null;
  var OFF_FA_CHARS = null;
  var OFF_FA_ATLAS_TEX = null;
  var OFF_FA_ATLAS_W = null;
  var OFF_FA_ATLAS_H = null;
  var POP_MODE = ['Static', 'Dynamic', 'DynamicOS'];

  var CMD = {
    PARSE_NAME: 1, PARSE_GRID: 2, PARSE_END: 3,
    PLAYBACK_BEGIN: 4, PLAYBACK_END: 5,
    CELL_NAME: 6, CELL_SHEET: 7,
    UI_OWNER: 8
  };

  /* ---- runtime TMP font installer (see section "font installer") ---- */
  var OFF_TA_MATERIAL = null;
  var OFF_SETTINGS_FALLBACK = null;
  var OFF_SETTINGS_DEFAULT_FA = null;
  var OFF_FA_ATLAS_TEX0 = null;
  var OFF_FA_ATLASES = null;
  var OFF_FA_ATLAS_INDEX = null;
  var OFF_FA_ATLAS_TEXW = null;
  var OFF_FA_ATLAS_TEXH = null;
  var OFF_FA_PADDING_SP = null;
  var OFF_FA_RENDER_MODE = null;
  var OFF_FA_MULTI = null;
  var CH_PROBE = 0x521b;             // '创' - absent from the Japanese FOT font
  var CH_PROBE2 = 0x7231;            // '爱' - same, but a different Unicode block
  /* Probe set the install has to rasterise before it claims success.  The
     first two also gate candidate selection; the rest are reported so a
     "single character is still a box" report can be answered with data. */
  var FONT_PROBES = [
    ['chuang', 0x521b, '创'], ['ai', 0x7231, '爱'], ['feng', 0x67ab, '枫'],
    ['zhan', 0x6218, '战'], ['lian', 0x70bc, '炼'], ['long', 0x9f99, '龙']
  ];

  /* il2cpp function table slots (Il2CppDumper RVA == elem table slot), all
     verified against dump.cs.  The trailing number in each comment is the
     parameter count JavaScript must see before it dares to call the slot. */
  var SLOT_RESOLVE_ICALL = null;
  var SLOT_TYPE_GETTYPE = null;
  var SLOT_FIND_ALL = null;
  var SLOT_FONT_HAS_CHAR = null;
  var SLOT_CREATE_FONT = null;
  var SLOT_CREATE_FONT_SHORT = null;
                                        // the arity diagnostic only)            2
  var SLOT_OBJECT_DESTROY = null;
  var SLOT_TMP_INSTANCE = null;
  var SLOT_MAT_SHADER = null;
  var SLOT_MAT_HAS_PROP = null;
  var SLOT_MAT_GET_FLOAT = null;
  var SLOT_MAT_SET_FLOAT = null;
  var SLOT_MAT_ENABLE_KW = null;

  /* Linear addresses of the icall name literals the compiler emits next to each
     FontEngine entry point. Resolved by content: the exact C string must have
     exactly one data-segment copy and that copy must be loaded by an i32.const
     in the module's code. Never located by proximity to an old address. */
  var ICALL_INIT = null;
  var ICALL_LOADFACE = null;
  var ICALL_FACEINFO = null;
  var ICALL_ADDGLYPH = null;
  var ICALL_PREFIX = 'UnityEngine.TextCore.LowLevel.FontEngine::';

  /* Stub-check for the two icall wrappers the discovery walks through.  Each
     wrapper caches its resolved native entry point in a static right after the
     class vtable; reading that word *after* the call tells a missing icall
     (0) apart from a call that simply produced nothing usable.  Both values
     come from the wrapper bodies themselves (i32.const before resolve_icall). */
  var FIND_ALL_SPTR = null;
  var HAS_CHAR_SPTR = null;

  /* Il2CppClass::name.  Validated at runtime against a known TMP_FontAsset
     before it is trusted for anything else. */
  var KLASS_NAME_OFF = null;

  /* More managed entry points the installer uses. */
  var SLOT_TMP_HAS_CHAR = null;
  var SLOT_TMP_SET_HPC = null;
  var SLOT_TMP_GET_FONT = null;
  var SLOT_TMP_SET_FONT = null;
  var SLOT_TMP_GET_SHARED_MAT = null;
  var SLOT_TMP_SET_SHARED_MAT = null;
  var SLOT_MAT_MAIN_TEX = null;
  var SLOT_OBJECT_GET_TYPE = null;
  var SLOT_ARRAY_CREATE = null;
                                     // (builds int[]{n}, -> CreateInstanceImpl)

  /* ---- runtime config ------------------------------------------------------
     MONMUSU_CN_RUNTIME is generated per branch and embedded in this loader by
     tools/build_release.py. The bootstrap hands the entry for the served branch
     to applyRuntimeConfig() before the module is instantiated; without it the
     translator still works (it only needs the import names) but the UI path
     rules and the runtime font fallback stay off, which is the safe direction. */
  var RUNTIME = null;
  var RUNTIME_STATE = 'unresolved';
  var RUNTIME_WHY = 'applyRuntimeConfig() was never called';

  function cfgInt(v) {
    return (typeof v === 'number' && isFinite(v) && v >= 0 && v === Math.floor(v)) ? v : null;
  }
  function cfgSlot(slots, id) {
    var e = slots[id];
    if (!e || e.status !== 'RESOLVED') return null;
    return cfgInt(e.slot);
  }
  function cfgField(fields, id) {
    var e = fields[id];
    if (!e || (e.status !== 'RESOLVED' && e.status !== 'VERIFIED_STATIC')) return null;
    return cfgInt(e.offset);
  }
  function cfgIcall(icalls, id) {
    var e = icalls[id];
    if (!e || e.status !== 'RESOLVED') return null;
    return cfgInt(e.literal);
  }

  function runtimeFail(why) {
    RUNTIME_STATE = 'unresolved';
    RUNTIME_WHY = why;
    FONT.enabled = false;
    FONT.state = 'disabled';
    FONT.reason = why;
    return false;
  }

  /* Returns true only when every binding the shim actually calls is present.
     Deliberately all-or-nothing for the font path: a partially filled table
     would install a font through some slots and not others. */
  function applyRuntimeConfig(cfg) {
    if (!cfg || cfg.schema !== 1) return runtimeFail('no runtime config for this build');
    var s = cfg.slots || {}, f = cfg.fields || {}, ic = cfg.icalls || {};
    RUNTIME = cfg;

    SLOT_GET_TRANSFORM = cfgSlot(s, 'get_transform');
    SLOT_GET_PARENT = cfgSlot(s, 'get_parent');
    SLOT_GET_NAME = cfgSlot(s, 'get_name');
    /* resolve_icall has no managed metadata, so the build reports it under
       icalls with a slot instead of under slots with a method name */
    SLOT_RESOLVE_ICALL = (ic.resolve_icall && ic.resolve_icall.status === 'RESOLVED')
      ? cfgInt(ic.resolve_icall.slot) : null;
    SLOT_TYPE_GETTYPE = cfgSlot(s, 'type_gettype');
    SLOT_FIND_ALL = cfgSlot(s, 'find_all');
    SLOT_FONT_HAS_CHAR = cfgSlot(s, 'font_has_char');
    SLOT_CREATE_FONT = cfgSlot(s, 'create_font');
    SLOT_CREATE_FONT_SHORT = cfgSlot(s, 'create_font_short');
    SLOT_OBJECT_DESTROY = cfgSlot(s, 'object_destroy');
    SLOT_TMP_INSTANCE = cfgSlot(s, 'tmp_instance');
    SLOT_MAT_SHADER = cfgSlot(s, 'mat_shader');
    SLOT_MAT_HAS_PROP = cfgSlot(s, 'mat_has_prop');
    SLOT_MAT_GET_FLOAT = cfgSlot(s, 'mat_get_float');
    SLOT_MAT_SET_FLOAT = cfgSlot(s, 'mat_set_float');
    SLOT_MAT_ENABLE_KW = cfgSlot(s, 'mat_enable_kw');
    SLOT_MAT_MAIN_TEX = cfgSlot(s, 'mat_main_tex');
    SLOT_TMP_HAS_CHAR = cfgSlot(s, 'tmp_has_char');
    SLOT_TMP_SET_HPC = cfgSlot(s, 'tmp_set_hpc');
    SLOT_TMP_GET_FONT = cfgSlot(s, 'tmp_get_font');
    SLOT_TMP_SET_FONT = cfgSlot(s, 'tmp_set_font');
    SLOT_TMP_GET_SHARED_MAT = cfgSlot(s, 'tmp_get_shared_mat');
    SLOT_TMP_SET_SHARED_MAT = cfgSlot(s, 'tmp_set_shared_mat');
    SLOT_OBJECT_GET_TYPE = cfgSlot(s, 'object_gettype');
    SLOT_ARRAY_CREATE = cfgSlot(s, 'array_create');

    ICALL_INIT = cfgIcall(ic, 'InitializeFontEngine');
    ICALL_LOADFACE = cfgIcall(ic, 'LoadFontFace');
    ICALL_FACEINFO = cfgIcall(ic, 'GetFaceInfo');
    ICALL_ADDGLYPH = cfgIcall(ic, 'TryAddGlyphToTexture');   // optional
    FIND_ALL_SPTR = cfgInt((ic.FindObjectsOfTypeAll || {}).cache_ptr);
    HAS_CHAR_SPTR = cfgInt((ic.HasCharacter || {}).cache_ptr);

    OFF_CACHED_PTR = cfgField(f, 'cached_ptr');
    KLASS_NAME_OFF = cfgField(f, 'klass_name');
    OFF_TEXT_FONTASSET = cfgField(f, 'tmp_text_font_asset');
    OFF_TEXT_CURRENT_FA = cfgField(f, 'tmp_text_current_font_asset');
    OFF_TEXT_SHARED_MAT = cfgField(f, 'tmp_text_shared_material');
    OFF_TA_MATERIAL = cfgField(f, 'tmp_asset_material');
    OFF_FA_VERSION = cfgField(f, 'tmp_font_asset_version');
    OFF_FA_SOURCE_FONT = cfgField(f, 'tmp_font_asset_source_font');
    OFF_FA_POP_MODE = cfgField(f, 'tmp_font_asset_pop_mode');
    OFF_FA_GLYPHS = cfgField(f, 'tmp_font_asset_glyphs');
    OFF_FA_CHARS = cfgField(f, 'tmp_font_asset_chars');
    OFF_FA_ATLAS_TEX = cfgField(f, 'tmp_font_asset_atlas_tex');
    OFF_FA_ATLAS_TEX0 = OFF_FA_ATLAS_TEX;
    OFF_FA_ATLASES = cfgField(f, 'tmp_font_asset_atlases');
    OFF_FA_ATLAS_INDEX = cfgField(f, 'tmp_font_asset_atlas_index');
    OFF_FA_MULTI = cfgField(f, 'tmp_font_asset_multi');
    OFF_FA_ATLAS_W = cfgField(f, 'tmp_font_asset_atlas_w');
    OFF_FA_ATLAS_H = cfgField(f, 'tmp_font_asset_atlas_h');
    OFF_FA_ATLAS_TEXW = OFF_FA_ATLAS_W;
    OFF_FA_ATLAS_TEXH = OFF_FA_ATLAS_H;
    OFF_FA_PADDING_SP = cfgField(f, 'tmp_font_asset_padding');
    OFF_FA_RENDER_MODE = cfgField(f, 'tmp_font_asset_render_mode');
    OFF_SETTINGS_DEFAULT_FA = cfgField(f, 'tmp_settings_default_font');
    OFF_SETTINGS_FALLBACK = cfgField(f, 'tmp_settings_fallbacks');

    var required = [
      ['get_transform', SLOT_GET_TRANSFORM], ['get_parent', SLOT_GET_PARENT],
      ['get_name', SLOT_GET_NAME], ['resolve_icall', SLOT_RESOLVE_ICALL],
      ['type_gettype', SLOT_TYPE_GETTYPE], ['find_all', SLOT_FIND_ALL],
      ['font_has_char', SLOT_FONT_HAS_CHAR], ['create_font', SLOT_CREATE_FONT],
      ['object_destroy', SLOT_OBJECT_DESTROY], ['tmp_instance', SLOT_TMP_INSTANCE],
      ['mat_shader', SLOT_MAT_SHADER], ['mat_has_prop', SLOT_MAT_HAS_PROP],
      ['mat_get_float', SLOT_MAT_GET_FLOAT], ['mat_set_float', SLOT_MAT_SET_FLOAT],
      ['mat_enable_kw', SLOT_MAT_ENABLE_KW], ['mat_main_tex', SLOT_MAT_MAIN_TEX],
      ['tmp_has_char', SLOT_TMP_HAS_CHAR], ['tmp_set_hpc', SLOT_TMP_SET_HPC],
      ['tmp_get_font', SLOT_TMP_GET_FONT], ['tmp_set_font', SLOT_TMP_SET_FONT],
      ['tmp_get_shared_mat', SLOT_TMP_GET_SHARED_MAT],
      ['tmp_set_shared_mat', SLOT_TMP_SET_SHARED_MAT],
      ['object_gettype', SLOT_OBJECT_GET_TYPE], ['array_create', SLOT_ARRAY_CREATE],
      ['icall_init', ICALL_INIT], ['icall_loadface', ICALL_LOADFACE],
      ['icall_faceinfo', ICALL_FACEINFO],
      ['find_all_cache', FIND_ALL_SPTR], ['has_char_cache', HAS_CHAR_SPTR],
      ['cached_ptr', OFF_CACHED_PTR], ['klass_name', KLASS_NAME_OFF],
      ['tmp_text_font_asset', OFF_TEXT_FONTASSET],
      ['tmp_text_current_font_asset', OFF_TEXT_CURRENT_FA],
      ['tmp_text_shared_material', OFF_TEXT_SHARED_MAT],
      ['tmp_asset_material', OFF_TA_MATERIAL],
      ['tmp_font_asset_version', OFF_FA_VERSION],
      ['tmp_font_asset_source_font', OFF_FA_SOURCE_FONT],
      ['tmp_font_asset_pop_mode', OFF_FA_POP_MODE],
      ['tmp_font_asset_glyphs', OFF_FA_GLYPHS],
      ['tmp_font_asset_chars', OFF_FA_CHARS],
      ['tmp_font_asset_atlas_tex', OFF_FA_ATLAS_TEX],
      ['tmp_font_asset_atlases', OFF_FA_ATLASES],
      ['tmp_font_asset_atlas_index', OFF_FA_ATLAS_INDEX],
      ['tmp_font_asset_multi', OFF_FA_MULTI],
      ['tmp_font_asset_atlas_w', OFF_FA_ATLAS_W],
      ['tmp_font_asset_atlas_h', OFF_FA_ATLAS_H],
      ['tmp_font_asset_padding', OFF_FA_PADDING_SP],
      ['tmp_font_asset_render_mode', OFF_FA_RENDER_MODE],
      ['tmp_settings_default_font', OFF_SETTINGS_DEFAULT_FA],
      ['tmp_settings_fallbacks', OFF_SETTINGS_FALLBACK]
    ];
    var missing = [];
    for (var i = 0; i < required.length; i++) {
      if (required[i][1] === null) missing.push(required[i][0]);
    }
    if (missing.length) return runtimeFail('runtime config incomplete: ' + missing.join(', '));

    RUNTIME_STATE = 'ok';
    RUNTIME_WHY = '';
    FONT.enabled = true;
    return true;
  }

  /* Discovery is retried instead of latched: the Font the story uses may not
     be loaded yet when the first UI string appears. */
  var FONT_RETRY_MS = 400;
  var FONT_MAX_ATTEMPTS = 600;
  var FONT_SELECT_ATTEMPTS = 8;      // "no CJK face yet" gives up quickly: the
                                     // carrier is in ntd.data, so waiting will
                                     // not conjure a Chinese face out of a font
                                     // that is already loaded without one
  var FONT_MAX_CANDIDATES = 4;

  /* Atlas plan: 64pt sampling with padding 6 keeps TMP's ~0.09 em SDF spread
     while fitting ~170 glyphs per 1024 page, versus ~80 for TMP's 90pt/9
     default.  Multi-atlas stays on so a page overflow is not a hard failure. */
  var FONT_SAMPLING = 64;
  var FONT_PADDING = 6;
  var FONT_RENDER_MODE = 4165;       // GlyphRenderMode.SDFAA
  var FONT_ATLAS = 1024;

  var mem = null;
  var flat = null;              // legacy prologue-only table (rollback path)
  var index = null;             // full Berumic story index
  var ctx = null;
  var uiIndex = null;           // Berumic UI index
  var table = null;             // WebAssembly.Table (exports.zj)
  var getTransform = null, getParent = null, getName = null;
  var pathState = 'idle';       // idle | ok | broken
  var owner = 0;                // component announcing itself to the UI path
  var inUi = false;             // re-entrancy guard
  var lastResult = { src: null, tr: '' };
  var lastUi = { src: null, tr: '', n: 0 };
  var uiMemo = { owner: 0, src: null, path: null };
  var samples = [];
  var stats = {
    tableSize: 0, probes: 0, hits: 0, misses: 0, fills: 0,
    passthrough: 0, contextCalls: 0, errors: 0
  };
  var uiStats = {
    ownerCalls: 0, probes: 0, hits: 0, misses: 0, fills: 0, stale: 0,
    pathWalks: 0, pathHits: 0, noPath: 0, broken: 0, skipped: 0, errors: 0,
    fontLogged: 0
  };
  var wasmExports = null;       // exported helpers (monmusu_cn_alloc)

  /* Opt-in UI trace.  Off by default and costs one boolean test per string;
     when on it keeps the last TRACE_MAX strings that reached the hook together
     with their resolved path and whether the index answered.  This is the only
     way to see the *actual* runtime text for a screen: the hook is the single
     place both the string and the component are available, and nothing is
     scanned globally.  Enable with __monmusuCnBridge.uiTrace(true) or by setting
     localStorage.monmusu_cn.uiTrace='1' before boot. */
  var TRACE_MAX = 300;
  var uiTraceOn = false, uiTraceBuf = [];
  // Diagnostic only: existing story/UI imports, never enumerate Unity objects.
  // A successful fill proves UTF-16 buffer write, NOT final Unity display.
  var storyTraceOn = false, storyTraceBuf = [], storyTraceSeq = 0;
  function traceStory(hook, src, tr, reason, component) {
    if (!storyTraceOn) return null;
    var row = {
      id: ++storyTraceSeq, time: Date.now(), hook: hook, source: src,
      context: ctx ? ctx.snapshot() : null, enteredHook: true,
      enteredStoryQuery: hook === 'story' && !!index && !!ctx &&
        reason !== 'invalid-or-empty-source' && reason !== 'read-source-exception',
      indexLoaded: !!index, uiIndexLoaded: !!uiIndex,
      knownStoryValue: index && src ? index.isKnownStoryValue(src) : false,
      hit: !!tr, translation: tr || '', component: component || 0,
      write: tr ? 'awaiting-fill' : 'not-requested', reason: reason || '',
      finalDisplay: 'NOT_OBSERVED'
    };
    if (hook === 'story' && index && index.concatStats) {
      row.concatenation = index.concatStats.last;
    }
    storyTraceBuf.push(row);
    if (storyTraceBuf.length > TRACE_MAX) storyTraceBuf.shift();
    return row;
  }

  /* One-shot runtime TMP font install.  'idle' until the first translated UI
     string needs a glyph the Japanese font does not have. */
  var FONT = {
    /* off until applyRuntimeConfig() proves the slots exist: calling a wrong
       element-table slot does not fail cleanly, it jumps into another method */
    enabled: false,
    state: 'idle',              // idle | trying | ready | failed | disabled
    stage: 'init',              // resolve_type | find_fonts | read_font_array |
                                // select_font | create_tmp_asset | register_fallback | ready
    reason: '',
    icalls: {},
    font: 0, fontName: '',
    asset: 0, material: 0, shader: 0,
    atlas: 0, atlasW: 0, atlasH: 0, pages: 0,
    characters: 0, glyphs: 0,
    style: [],
    fallbacks: -1,
    ms: -1,
    source: '',
    attempts: 0, retryAt: 0,
    /* diagnostics: filled while walking discovery, never by guessing */
    arrayType: '', arrayPointer: 0, arrayLength: -1,
    expectedLayout: '', observedLayout: '',
    fallbackType: '', fallbackLength: -1, fallbackObserved: '',
    fallbackIndex: -1, fallbackAfter: -1,
    grow: null, growObserved: '',
    selectedFont: null, candidates: [], fontsSeen: [],
    probeChars: [], sourceFontReady: false, callable: null,
    unify: true, unified: 0, restored: 0, unifySame: 0, unifySkipped: 0, unifyErrors: 0,
    skipShaders: [], unifyLast: null, refreshedAll: -1, refreshAll: true,
    contract: null, glyphCheck: null,
    createdAsset: 0, fallbackRegistered: false,
    klassLayout: 'unverified',
    refreshed: -1
  };

  function storyApi() {
    if (typeof monmusuCnStory !== 'undefined') return monmusuCnStory;
    if (typeof require === 'function') {
      try { return require('./monmusu_cn_story_index.js'); } catch (e) { return null; }
    }
    return null;
  }
  function uiApi() {
    if (typeof monmusuCnUi !== 'undefined') return monmusuCnUi;
    if (typeof require === 'function') {
      try { return require('./monmusu_cn_ui_index.js'); } catch (e) { return null; }
    }
    return null;
  }

  function setMemory(m) { mem = m; }
  function setExports(ex) { wasmExports = ex || null; }
  function hasTable() { return flat !== null || index !== null; }

  function setTable(pairs) {
    flat = new Map();
    for (var i = 0; i < pairs.length; i++) flat.set(pairs[i][0], pairs[i][1]);
    stats.tableSize = flat.size;
  }

  function setStoryIndex(doc) {
    var api = storyApi();
    if (!api) throw new Error('monmusu_cn_story_index.js not loaded');
    index = api.createIndex(doc);
    ctx = api.createSceneContext(index);
    stats.tableSize = index.parse.global.size + index.parse.ambiguous.size;
    stats.commit = doc.commit;
    stats.corpusSha256 = doc.corpusSha256;
    return index;
  }

  function setUiIndex(doc) {
    var api = uiApi();
    if (!api) throw new Error('monmusu_cn_ui_index.js not loaded');
    uiIndex = api.createIndex(doc);
    return uiIndex;
  }

  /* The Unity build exports its function table under an obfuscated name, so it
     is located by shape.  Memory exports are rejected by the buffer check. */
  function setTableExport(tbl) {
    if (!tbl || typeof tbl.get !== 'function' || typeof tbl.length !== 'number') {
      pathState = 'broken';
      return false;
    }
    table = tbl;
    return true;
  }

  function exportTableFrom(ex) {
    if (!ex) return false;
    for (var k in ex) {
      var v = ex[k];
      if (v && typeof v.get === 'function' && typeof v.set === 'function' &&
          typeof v.length === 'number' && typeof v.buffer === 'undefined') {
        return setTableExport(v);
      }
    }
    pathState = 'broken';
    return false;
  }

  function readSource(p) {
    if (!mem || !p) return null;
    var buf = mem.buffer, len = buf.byteLength;
    if (p < 1024 || p + 12 > len) return null;
    var v = new DataView(buf);
    var n = v.getUint32(p + 8, true);          /* Il2CppString.length @ +8 */
    if (n === 0 || n > MAX_UNITS) return null;
    var base = p + 12;                          /* chars @ +12 */
    if (base + n * 2 > len) return null;
    var out = '', chunk = 256;
    for (var i = 0; i < n; i += chunk) {
      var end = Math.min(i + chunk, n);
      var codes = new Array(end - i);
      for (var j = i; j < end; j++) codes[j - i] = v.getUint16(base + 2 * j, true);
      out += String.fromCharCode.apply(null, codes);
    }
    return out;
  }

  function writeChars(dst, s) {
    if (!mem || !dst) return false;
    var buf = mem.buffer;
    if (dst < 1024 || dst + s.length * 2 > buf.byteLength) return false;
    var v = new DataView(buf);
    for (var i = 0; i < s.length; i++) v.setUint16(dst + 2 * i, s.charCodeAt(i), true);
    return true;
  }

  function resolveText(src) {
    if (index && ctx) {
      ctx.prepare(src);
      return index.lookup(src, ctx);
    }
    if (flat) return flat.get(src) || '';
    return '';
  }

  /* ---------- UI hierarchy path ---------- */

  function slotFn(slot) {
    if (!table || slot === null || slot === undefined) return null;
    var f;
    try { f = table.get(slot); } catch (e) { return null; }
    if (typeof f !== 'function' || f.length !== 2) return null;
    return f;
  }

  function resolveGetters() {
    if (pathState === 'ok') return true;
    if (pathState === 'broken') return false;
    if (!table || !mem) { pathState = 'broken'; return false; }
    getTransform = slotFn(SLOT_GET_TRANSFORM);
    getParent = slotFn(SLOT_GET_PARENT);
    getName = slotFn(SLOT_GET_NAME);
    if (!getTransform || !getParent || !getName) {
      pathState = 'broken';
      uiStats.broken++;
      return false;
    }
    pathState = 'ok';
    return true;
  }

  /* UnityEngine.Object is only usable while its native counterpart exists. */
  function alive(p) {
    if (!p || !mem || OFF_CACHED_PTR === null) return false;
    if (p < 1024 || p + OFF_CACHED_PTR + 4 > mem.buffer.byteLength) return false;
    return new DataView(mem.buffer).getUint32(p + OFF_CACHED_PTR, true) !== 0;
  }

  /* Walk to the scene root and join the object names, leaf last.  Any doubt
     about a single segment aborts the whole path: a partial path would match
     the wrong rule, which is worse than not translating. */
  function buildPath(comp) {
    if (!resolveGetters()) return '';
    if (!alive(comp)) { uiStats.noPath++; return ''; }
    var t;
    try { t = getTransform(comp, 0) | 0; } catch (e) { pathState = 'broken'; uiStats.broken++; return ''; }
    if (!t) { uiStats.noPath++; return ''; }

    var segs = [], depth = 0;
    while (t) {
      if (++depth > MAX_DEPTH) { uiStats.noPath++; return ''; }
      if (!alive(t)) { uiStats.noPath++; return ''; }
      var s;
      try { s = getName(t, 0) | 0; } catch (e) { pathState = 'broken'; uiStats.broken++; return ''; }
      var name = s ? readSource(s) : null;
      if (!name) { uiStats.noPath++; return ''; }
      segs.push(name);
      var p;
      try { p = getParent(t, 0) | 0; } catch (e) { pathState = 'broken'; uiStats.broken++; return ''; }
      t = p;
    }
    segs.reverse();
    uiStats.pathWalks++;
    return uiIndex.normalizePath(segs.join('/'));
  }

  /* One-entry memo: TMP calls set_text and SetText back to back for the same
     component, and the probe/fill pair hits the same source twice.  Only
     successful paths are cached -- a failed walk must be retried, otherwise a
     single transient failure would disable that string for the whole session. */
  function pathFor(comp, src) {
    if (uiMemo.owner === comp && uiMemo.src === src) return uiMemo.path;
    var p = buildPath(comp);
    if (p) {
      uiMemo.owner = comp; uiMemo.src = src; uiMemo.path = p;
    } else {
      uiMemo.owner = 0; uiMemo.src = null; uiMemo.path = null;
    }
    return p;
  }

  function resolveUi(src, comp) {
    if (!uiIndex) return '';
    var path = (comp && pathState !== 'broken')
      ? function () { return pathFor(comp, src); }
      : null;
    var st = uiIndex.stats || null;
    /* Snapshot the path counters before the lookup: on a miss the delta tells
       *where* it died (never a path key / no hierarchy path / no usable rule),
       which is the difference between an index bug and a runtime path bug. */
    var before = st ? [st.pathLookups, st.pathResolved, st.pathBorrowed] : null;
    var t = uiIndex.lookupLazy(src, path);
    if (uiTraceOn) {
      var p = '';
      if (path) { try { p = path() || ''; } catch (e) { p = ''; } }
      /* r = which table/rule answered (path / global / sub / name / template /
         trim / known / miss), x = what was actually returned, w = why a miss
         happened.  t is ONLY "the index answered" - it says nothing about
         whether the translated string was written back or is still on screen.
         Without x/w a hit and a miss look the same, and a "matched but still
         Japanese" report (write-back) cannot be separated from "never matched"
         (matcher). */
      var r = st ? (st.lastRule || '') : '';
      var w = '';
      if (!t) {
        if (!st) w = 'no-index';
        else if (r === 'known') w = 'already-translated';
        else if (before && st.pathLookups === before[0]) w = 'not-a-path-key';
        else if (before && st.pathResolved === before[1]) w = 'no-hierarchy-path';
        else if (before && st.pathBorrowed > before[2]) w = 'borrowed-path-has-no-rule';
        else w = 'no-matching-rule';
      }
      var prev = uiTraceBuf.length ? uiTraceBuf[uiTraceBuf.length - 1] : null;
      if (!prev || prev.s !== src || prev.t !== !!t || prev.r !== r ||
          prev.p !== p || prev.w !== w) {
        uiTraceBuf.push({ s: src, t: !!t, p: p, r: r, w: w,
                          x: t ? String(t).slice(0, 160) : '' });
        if (uiTraceBuf.length > TRACE_MAX) uiTraceBuf.shift();
      }
    }
    return t || '';
  }

  /* ---------- font diagnostic (read-only, never mutates anything) ---------- */

  function u32(p) {
    if (!mem || !p || p < 1024 || p + 4 > mem.buffer.byteLength) return 0;
    return new DataView(mem.buffer).getUint32(p, true);
  }

  function describeFontAsset(p) {
    if (!p || !mem || !alive(p)) return null;
    var d = { ptr: p };
    try {
      var namePtr = getName ? (getName(p, 0) | 0) : 0;
      d.name = namePtr ? readSource(namePtr) : null;
      d.version = readSource(u32(p + OFF_FA_VERSION)) || null;
      var mode = u32(p + OFF_FA_POP_MODE);
      d.populationMode = POP_MODE[mode] || ('unknown(' + mode + ')');
      d.sourceFont = u32(p + OFF_FA_SOURCE_FONT);
      d.atlasTexture = u32(p + OFF_FA_ATLAS_TEX);
      d.atlas = u32(p + OFF_FA_ATLAS_W) + 'x' + u32(p + OFF_FA_ATLAS_H);
      // Il2Cpp List<T>: _items @ +8, _size @ +12
      d.glyphs = u32(u32(p + OFF_FA_GLYPHS) + 12);
      d.characters = u32(u32(p + OFF_FA_CHARS) + 12);
    } catch (e) {
      d.error = String((e && e.message) || e);
    }
    return d;
  }

  /* Reports which TMP font the UI actually renders with.  The Chinese font is
     NOT installed by this bridge: the author's mod swaps in its own TMP font
     asset, which needs a pre-built atlas.  Run __monmusuCnBridge.fontInfo() in the
     browser console to see the real asset, its population mode and glyph count. */
  function fontInfo(comp) {
    var c = comp || owner;
    if (!c || !mem) return null;
    var fa = u32(c + OFF_TEXT_FONTASSET);
    var cur = u32(c + OFF_TEXT_CURRENT_FA);
    return {
      component: c,
      fontAsset: describeFontAsset(fa),
      currentFontAsset: describeFontAsset(cur),
      same: fa === cur,
      sharedMaterial: u32(c + OFF_TEXT_SHARED_MAT)
    };
  }

  /* Opt-in, read-only layout snapshot of an already observed TMP owner.
     Offsets verified in dump_webgl/dump.cs for 202611021000/0. No object scan,
     setter, ForceMeshUpdate, allocator or font installation is invoked here. */
  function fontLayout(comp) {
    var p = (comp || owner) >>> 0;
    if (!mem || !p || !alive(p) || p + 0x318 > mem.buffer.byteLength)
      return { error: 'no live observed TMP component' };
    var klass = klassNameOf(p);
    if (!/^(TextMeshProUGUI|TextMeshPro|TMP_Text)$/.test(klass))
      return { component: p, error: 'unverified TMP class: ' + klass };
    var view = new DataView(mem.buffer);
    function f(offset) {
      var n = view.getFloat32(p + offset, true);
      return isFinite(n) ? n : null;
    }
    var info = u32(p + 0x2a0), face = u32(p + OFF_TEXT_FONTASSET);
    var out = {
      component: p, klass: klass, text: readSource(u32(p + 0x80)), font: fontInfo(p),
      fontSize: f(0x148), currentFontSize: f(0x14c), fontSizeBase: f(0x150),
      enableAutoSizing: !!u8(p + 0x18c), fontSizeMin: f(0x1a4), fontSizeMax: f(0x1a8),
      fontScaleMultiplier: f(0x314), characterSpacing: f(0x1ec), richTextCharacterSpacing: f(0x1f0),
      wordSpacing: f(0x1f8), lineSpacing: f(0x1fc), lineSpacingDelta: f(0x200),
      lineHeight: f(0x204), paragraphSpacing: f(0x210), charWidthMaxAdj: f(0x214),
      charWidthAdjDelta: f(0x218), enableWordWrapping: !!u8(p + 0x21c),
      overflowMode: u32(p + 0x224), margin: [f(0x27c), f(0x280), f(0x284), f(0x288)],
      cachedAvailableWidth: f(0x294), cachedAvailableHeight: f(0x298),
      previousRectSize: [f(0x2b0), f(0x2b4)],
      renderedWidth: f(0x2f4), renderedHeight: f(0x300),
      autoSizeIterations: u32(p + 0x198), timing: 'current-memory; may be before next TMP rebuild'
    };
    if (face && face + 0x80 < mem.buffer.byteLength && klassNameOf(face) === 'TMP_FontAsset') {
      out.faceInfo = {
        pointSize: view.getInt32(face + 0x38, true), scale: view.getFloat32(face + 0x3c, true),
        lineHeight: view.getFloat32(face + 0x44, true), ascent: view.getFloat32(face + 0x48, true),
        descent: view.getFloat32(face + 0x58, true), padding: u32(face + 0xb8)
      };
    }
    if (info && info + 0x44 < mem.buffer.byteLength && klassNameOf(info) === 'TMP_TextInfo') {
      out.renderedLineCount = u32(info + 0x20); out.renderedCharacterCount = u32(info + 0xc);
    }
    return out;
  }

  /* ================= runtime TMP font installer =================
   *
   * The UI font is FOT-NewRodinProN-EB SDF with m_AtlasPopulationMode=Static
   * and m_SourceFontFile=null, so it can never grow Chinese glyphs.  There is
   * no TMP font asset in the WebGL payload either.  What we do have is the
   * legacy Font the story uses, which already carries the injected Chinese
   * subset, so the asset is rebuilt at runtime:
   *
   *   FontEngine (native FreeType) -> TMP_FontAsset.CreateFontAsset(Font)
   *
   * and registered in TMP_Settings.fallbackFontAssets, which
   * TextMeshProUGUI.SetArraySizes really consults (verified by scanning the
   * call sites).  Components keep their original font and material; only the
   * glyphs the Japanese asset lacks come from ours, so nothing can lose text.
   *
   * Every step is gated on evidence:
   *   - the FontEngine icalls must resolve through il2cpp_codegen_resolve_icall
   *     (a miss there would make call_indirect jump through a null table slot)
   *   - the resulting asset must have a live material + shader + atlas
   *   - registration is a plain list append, so a full list fails cleanly
   */

  function hex4(v) { return '0x' + (v >>> 0).toString(16); }

  function cstrAt(p, max) {
    if (!mem || !p || p < 1024 || p + 8 > mem.buffer.byteLength) return null;
    var v = new Uint8Array(mem.buffer), out = '';
    for (var i = 0; i < (max || 160); i++) {
      var ch = v[p + i];
      if (!ch) return out;
      out += String.fromCharCode(ch);
    }
    return null;
  }

  function tableFn(slot, arity) {
    if (!table || slot === null || slot === undefined) return null;
    var f;
    try { f = table.get(slot); } catch (e) { return null; }
    if (typeof f !== 'function') return null;
    if (arity != null && f.length !== arity) return null;
    return f;
  }

  function callPtr(slot, args, arity, label) {
    var f = tableFn(slot, arity);
    if (!f) return { err: (label || hex4(slot)) + ' not callable (slot/arity mismatch)' };
    try { return { v: f.apply(null, args) >>> 0 }; }
    catch (e) { return { err: (label || hex4(slot)) + ' trapped: ' + ((e && e.message) || e) }; }
  }

  /* float-returning slots must not go through the >>> 0 path */
  function callNum(slot, args, arity, label) {
    var f = tableFn(slot, arity);
    if (!f) return { err: (label || hex4(slot)) + ' not callable (slot/arity mismatch)' };
    try {
      var v = f.apply(null, args);
      return { f: (typeof v === 'number' && isFinite(v)) ? v : 0 };
    } catch (e) { return { err: (label || hex4(slot)) + ' trapped: ' + ((e && e.message) || e) }; }
  }

  /* Managed string without a scratch buffer: allocate first, then fill
     chars+12 through the same writer the hooks use. */
  function allocManagedString(s) {
    if (!wasmExports || typeof wasmExports.monmusu_cn_alloc !== 'function') return 0;
    var p;
    try { p = wasmExports.monmusu_cn_alloc(s.length) >>> 0; } catch (e) { return 0; }
    if (!p) return 0;
    if (!writeChars(p + 12, s)) return 0;
    return p;
  }

  function nowMs() {
    return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  }

  function fontFail(stage, why) {
    FONT.state = 'failed';
    FONT.stage = stage || FONT.stage;
    FONT.reason = why;
    uiStats.errors++;
    try { console.warn('[monmusu_cn font] ' + FONT.stage + ': ' + why); } catch (e) { /* no console */ }
    return false;
  }

  /* Not a failure: the Font we need simply is not loaded yet, or nothing was
     usable this round.  Keeps state='trying' and lets ensureFont() try again
     after a short delay, which is what makes the install survive being
     triggered on the title screen. */
  function fontRetry(stage, why) {
    FONT.stage = stage || FONT.stage;
    FONT.reason = why;
    FONT.retryAt = nowMs() + FONT_RETRY_MS;
    /* one line, not one per attempt: the console should not fill with 600
       copies of the same "still waiting" message */
    if (FONT.attempts <= 1) {
      try { console.log('[monmusu_cn font] waiting: ' + FONT.stage + ': ' + why); } catch (e) { /* no console */ }
    }
    if (FONT.attempts >= FONT_MAX_ATTEMPTS) {
      return fontFail(stage, why + ' (gave up after ' + FONT.attempts + ' attempts)');
    }
    return false;
  }

  /* ---------- managed readers: read-only, every pointer validated ----------
   *
   * Il2CppArray on wasm32:  klass @0, monitor @4, bounds @8, max_length @12,
   * element data @16.  A single-dimension array has bounds == 0.
   * Il2CppClass::name is a NUL-terminated const char* at @8.
   *
   * The previous version read max_length @12 and, when that looked wrong,
   * *guessed* a second layout at @16.  That guess turned an empty array (or a
   * bogus pointer) into a list of garbage "elements" and then called managed
   * code on them.  There is no guessing here: if the object does not look like
   * an array by its own class name, the read fails and says so.
   */
  function klassNameOf(obj) {
    if (!mem || !obj) return '';
    var len = mem.buffer.byteLength;
    var p = obj >>> 0;
    if (p < 1024 || p + 4 > len) return '';
    var klass = u32(p);
    if (!klass || klass < 1024 || klass + KLASS_NAME_OFF + 4 > len) return '';
    return cstrAt(u32(klass + KLASS_NAME_OFF), 80) || '';
  }

  function readManagedArray(ptr, opts) {
    opts = opts || {};
    var maxLen = opts.maxLength || 4096;
    var out = {
      ok: false, ptr: ptr >>> 0, length: 0, base: 0, elems: [],
      typeName: '', expected: opts.expected || 'array type name ending in "[]"',
      observed: '', why: ''
    };
    if (!mem) { out.why = 'no memory'; out.observed = 'no memory'; return out; }
    var buf = mem.buffer, len = buf.byteLength, p = out.ptr;
    out.observed = 'ptr=' + hex4(p);
    if (p < 1024 || p + 16 > len) { out.why = 'pointer outside linear memory'; return out; }
    if (p & 3) { out.why = 'pointer not 4-byte aligned'; return out; }
    var dv = new DataView(buf);
    var klass = dv.getUint32(p, true);
    out.observed += ' klass=' + hex4(klass);
    if (klass < 1024 || klass + KLASS_NAME_OFF + 4 > len) {
      out.why = 'class pointer outside linear memory'; return out;
    }
    var nameP = dv.getUint32(klass + KLASS_NAME_OFF, true);
    out.typeName = cstrAt(nameP, 80) || '';
    out.observed += ' type=' + (out.typeName || '<unreadable>');
    if (!out.typeName || out.typeName.indexOf('[]') < 0) {
      out.why = 'object is not a managed array'; return out;
    }
    var bounds = dv.getUint32(p + 8, true);
    var n = dv.getUint32(p + 12, true);
    out.observed += ' bounds=' + hex4(bounds) + ' max_length=' + n;
    /* bounds is NULL for a vector; a non-null value must at least be a heap
       pointer, otherwise this is not an array header at all. */
    if (bounds !== 0 && (bounds < 1024 || bounds + 8 > len)) {
      out.why = 'bounds is neither null nor a heap pointer'; return out;
    }
    if (n > maxLen) { out.why = 'max_length ' + n + ' exceeds the ' + maxLen + ' element cap'; return out; }
    if (p + 16 + 4 * n > len) { out.why = 'element data runs past the end of memory'; return out; }
    out.length = n;
    out.base = p + 16;
    for (var i = 0; i < n; i++) out.elems.push(dv.getUint32(out.base + 4 * i, true));
    out.ok = true;
    return out;
  }

  /* A known object is used to prove KLASS_NAME_OFF before element filtering
     relies on it.  If it does not read back, element class checks are simply
     skipped instead of rejecting every candidate. */
  function verifyKlassLayout() {
    if (FONT.klassLayout !== 'unverified') return FONT.klassLayout === 'ok';
    var fa = owner ? u32(owner + OFF_TEXT_FONTASSET) : 0;
    if (!fa) {
      var si = callPtr(SLOT_TMP_INSTANCE, [0], 1, 'TMP_Settings.instance');
      if (!si.err && si.v) fa = u32(si.v + OFF_SETTINGS_DEFAULT_FA);
    }
    var nm = fa ? klassNameOf(fa) : '';
    FONT.klassLayout = (nm === 'TMP_FontAsset') ? 'ok' : ('unexpected(' + (nm || '?') + ')');
    return FONT.klassLayout === 'ok';
  }

  function refreshFontStats() {
    if (!FONT.asset) return null;
    var fa = FONT.asset;
    // Il2Cpp List<T>: _items @ +8, _size @ +12
    FONT.characters = u32(u32(fa + OFF_FA_CHARS) + 12);
    FONT.glyphs = u32(u32(fa + OFF_FA_GLYPHS) + 12);
    var pages = u32(fa + OFF_FA_ATLASES);
    FONT.pages = pages ? u32(pages + 12) : 0;
    return FONT;
  }

  /* ---------- growing the fallback list's backing array ----------
   *
   * A default-constructed List<T> keeps Array.Empty<T>() as _items, so the
   * device reported exactly that: _size = 0 and max_length = 0.  There is no
   * usable slot, and writing past max_length would overwrite the next object's
   * header - so the array has to be replaced, not poked.
   *
   * Why not List<T>.Add: this build has no entry point for it at all
   * (slot2name.json contains no List`1 method; the only generic List slots are
   * Json.NET's explicit IList implementations).  A shared-generic Add would
   * also need a MethodInfo* carrying the rgctx of List<TMP_FontAsset> so that
   * its EnsureCapacity can allocate the right array type, and that MethodInfo
   * is not something we can fabricate.
   *
   * So the array is allocated through the non-generic
   * System.Array.CreateInstance(Type, int) - slot 0xBD4, the same overload 30
   * managed call sites in this build use (LitJson, XmlSerializationReader,
   * MarshalByRefObject...).  Its body builds int[]{length} and forwards to
   * CreateInstance(Type, int[]) -> CreateInstanceImpl(RuntimeType, int[], null),
   * which goes straight to il2cpp_array_new with the element class taken from
   * the Type.  The Type comes from Object.GetType() on the asset we just built,
   * so the element class is TMP_FontAsset by construction - and that is then
   * *checked* against the class of the array the list already holds.
   *
   * Boehm GC on WebGL is non-moving and conservative, so publishing the new
   * array with a plain field write needs no write barrier: the List object is
   * reachable, and the collector scans its fields conservatively.
   */
  function growBackingArray(list, items, size, arr) {
    var capacity = size + 1;
    if (capacity < 4) capacity = 4;          // one allocation serves re-arms
    var t = callPtr(SLOT_OBJECT_GET_TYPE, [FONT.asset, 0], 2, 'Object.GetType');
    if (t.err) return { err: 'Object.GetType: ' + t.err };
    if (!t.v) return { err: 'Object.GetType returned null for the new asset' };
    /* Array.CreateInstance would throw (a managed exception, which aborts the
       runtime) if it were handed something that is not a RuntimeType handle.
       Check the class first, but only when the class-name offset is proven. */
    if (verifyKlassLayout()) {
      var tn = klassNameOf(t.v);
      if (tn && !/RuntimeType$/.test(tn)) {
        return { err: 'Object.GetType returned a ' + tn + ', not a runtime type handle' };
      }
    }

    var made = callPtr(SLOT_ARRAY_CREATE, [t.v, capacity, 0], 3, 'Array.CreateInstance');
    if (made.err) return { err: made.err };
    if (!made.v) return { err: 'Array.CreateInstance returned null' };

    var na = readManagedArray(made.v, {
      maxLength: 64, expected: 'TMP_FontAsset[]', label: 'new fallback array'
    });
    FONT.growObserved = na.observed;
    if (!na.ok) return { err: 'new backing array unreadable: ' + na.why };
    if (na.length !== capacity) {
      return { err: 'Array.CreateInstance produced length ' + na.length +
                    ', wanted ' + capacity };
    }
    /* the new array must be of the very type the list already holds: compare
       the class the allocator picked with the class of the old _items, instead
       of trusting a name */
    if (u32(na.ptr) !== u32(items)) {
      return { err: 'Array.CreateInstance produced ' + na.typeName +
                    ', not the list element type (' + (arr.typeName || '?') + ')' };
    }

    var v = new DataView(mem.buffer);
    for (var i = 0; i < size; i++) v.setUint32(na.base + 4 * i, arr.elems[i], true);
    v.setUint32(list + 8, na.ptr, true);     // List._items = new array
    FONT.grow = { from: arr.length, to: capacity, copied: size };
    FONT.fallbackLength = capacity;
    return na;
  }

  /* Append one asset to TMP_Settings.m_fallbackFontAssets.
   *
   * Handles every state the real settings object can be in: a never-touched
   * list (capacity 0), a list with spare capacity, a list that is exactly full,
   * a list that already contains our asset, and a list whose backing store is
   * not a TMP_FontAsset[] at all (refused).  After the write the list is read
   * back from the settings object, so "registered" means "observable through
   * the game's own object", not "we called a setter". */
  function registerFallback(asset) {
    if (!asset) return 'no asset to register';
    FONT.grow = null;
    var si = callPtr(SLOT_TMP_INSTANCE, [0], 1, 'TMP_Settings.instance');
    if (si.err) return si.err;
    if (!si.v) return 'TMP_Settings.instance is null';
    var list = u32(si.v + OFF_SETTINGS_FALLBACK);
    if (!list) return 'TMP_Settings.fallbackFontAssets is null';
    var items = u32(list + 8), size = u32(list + 12);
    if (!items) return 'fallback list has no backing array';
    var arr = readManagedArray(items, {
      maxLength: 64, expected: 'TMP_FontAsset[]',
      label: 'fallback._items'
    });
    FONT.fallbackType = arr.typeName;
    FONT.fallbackLength = arr.ok ? arr.length : -1;
    FONT.fallbackObserved = 'fallback._items ' + arr.observed;
    if (!arr.ok) return 'fallback _items is not an array: ' + arr.why;
    if (!/^(TMPro\.)?TMP_FontAsset\[\]$/.test(arr.typeName)) {
      return 'fallback _items is ' + arr.typeName + ': not an array of TMP_FontAsset';
    }
    if (size > arr.length) return 'fallback _size ' + size + ' > array length ' + arr.length;
    if (verifyKlassLayout()) {
      /* only the live entries are elements; anything past _size is unused
         capacity and is not ours to judge */
      for (var k = 0; k < size; k++) {
        if (!arr.elems[k]) continue;
        if (klassNameOf(arr.elems[k]) !== 'TMP_FontAsset') {
          return 'fallback _items holds a non-TMP_FontAsset at ' + k +
                 ' (' + (klassNameOf(arr.elems[k]) || '?') + ' ptr ' + hex4(arr.elems[k]) + ')';
        }
      }
    }

    for (var i = 0; i < size; i++) {
      if (arr.elems[i] === asset) {          // already there: never duplicate
        FONT.fallbacks = size;
        FONT.fallbackIndex = i;
        FONT.fallbackAfter = size;
        FONT.fallbackRegistered = true;
        return null;
      }
    }

    if (size + 1 > arr.length) {
      var grown = growBackingArray(list, items, size, arr);
      if (grown.err) return grown.err;
      arr = grown;
    }

    var v = new DataView(mem.buffer);
    v.setUint32(arr.base + 4 * size, asset, true);
    v.setUint32(list + 12, size + 1, true);

    /* read the list back through the settings object: the entry must be
       findable there, not merely "we think we wrote it" */
    var after = u32(list + 12);
    var chk = readManagedArray(u32(list + 8), {
      maxLength: 64, expected: 'TMP_FontAsset[]', label: 'post-register'
    });
    FONT.fallbackAfter = after;
    FONT.fallbackLength = chk.ok ? chk.length : -1;
    if (!chk.ok) return 'post-check: backing array unreadable: ' + chk.why;
    if (after !== size + 1) {
      return 'post-check: _size is ' + after + ', expected ' + (size + 1);
    }
    if (after > chk.length) {
      return 'post-check: _size ' + after + ' exceeds array length ' + chk.length;
    }
    var idx = -1;
    for (var m = 0; m < after; m++) {
      if (chk.elems[m] === asset) { idx = m; break; }
    }
    if (idx < 0) return 'post-check: the asset is not visible in the list after the write';
    if (klassNameOf(asset) !== 'TMP_FontAsset' && verifyKlassLayout()) {
      return 'post-check: the stored pointer is not a TMP_FontAsset';
    }
    FONT.fallbacks = after;
    FONT.fallbackIndex = idx;
    FONT.fallbackRegistered = true;
    return null;
  }

  /* Undo registerFallback so the feature really can be turned off at runtime.
     Only an exact pointer match is removed, and the tail is compacted, so an
     asset we did not insert (or one the game added later) is never touched.
     Meshes already laid out keep their glyphs; the next SetArraySizes pass is
     what sees the shorter list. */
  function unregisterFallback(asset) {
    if (!asset) return 'no asset to remove';
    var si = callPtr(SLOT_TMP_INSTANCE, [0], 1, 'TMP_Settings.instance');
    if (si.err) return si.err;
    if (!si.v) return 'TMP_Settings.instance is null';
    var list = u32(si.v + OFF_SETTINGS_FALLBACK);
    if (!list) return 'TMP_Settings.fallbackFontAssets is null';
    var items = u32(list + 8), size = u32(list + 12);
    if (!items || !size) return 'fallback list is already empty';
    var arr = readManagedArray(items, { maxLength: 64, label: 'fallback._items' });
    if (!arr.ok) return 'fallback _items is not an array: ' + arr.why;
    if (size > arr.length) return 'fallback _size ' + size + ' > array length ' + arr.length;
    var v = new DataView(mem.buffer);
    for (var i = 0; i < arr.length; i++) {
      if (v.getUint32(arr.base + 4 * i, true) !== asset) continue;
      for (var j = i; j + 1 < size; j++) {
        v.setUint32(arr.base + 4 * j, v.getUint32(arr.base + 4 * (j + 1), true), true);
      }
      v.setUint32(arr.base + 4 * (size - 1), 0, true);
      v.setUint32(list + 12, size - 1, true);
      FONT.fallbacks = size - 1;
      return null;
    }
    return 'our asset is not in the fallback list';
  }

  /* CreateFontAsset builds a bare material: no outline, no face dilation.  Copy
     the two floats that decide visual weight from the material that is actually
     drawing this component, otherwise the Chinese glyphs sit next to Japanese
     ones looking visibly thinner.  Colours are left at the shader defaults. */
  function syncMaterialStyle(dstMat, srcMat) {
    var done = [];
    if (!dstMat || !srcMat) return done;
    var props = [['_FaceDilate', false], ['_OutlineWidth', true]];
    for (var i = 0; i < props.length; i++) {
      var propName = props[i][0], isOutline = props[i][1];
      var s = allocManagedString(propName);
      if (!s) continue;
      var has = callPtr(SLOT_MAT_HAS_PROP, [srcMat, s, 0], 3, 'Material.HasProperty');
      if (has.err || !has.v) continue;
      var gf = callNum(SLOT_MAT_GET_FLOAT, [srcMat, s, 0], 3, 'Material.GetFloat');
      if (gf.err) continue;
      var set = callPtr(SLOT_MAT_SET_FLOAT, [dstMat, s, gf.f, 0], 4, 'Material.SetFloat');
      if (set.err) continue;
      done.push(propName + '=' + gf.f.toFixed(2));
      if (isOutline && gf.f > 0) {
        var kw = allocManagedString('OUTLINE_ON');
        if (kw) callPtr(SLOT_MAT_ENABLE_KW, [dstMat, kw, 0], 3, 'Material.EnableKeyword');
      }
    }
    return done;
  }

  /* ---------- CreateFontAsset: element-table entry + calling convention ----
   *
   * The device proved that a static claim is not enough.  The previous version
   * called slot 0x18AB6 -- CreateFontAsset(Font), a *two* parameter entry --
   * with the nine arguments of the full overload, and the arity guard stopped
   * it.  The element table holds both overloads, with different types:
   *
   *   slot 0x18AB6  CreateFontAsset(Font, MethodInfo*)                 2 params
   *   slot 0x18AB7  CreateFontAsset(Font, int, int, GlyphRenderMode,
   *                   int, int, AtlasPopulationMode, bool,
   *                   MethodInfo*)                                    9 params
   *
   * Both are real bodies here (80 B forwards to 1073 B), and the nine-parameter
   * one stores every argument into a serialized field.  That is what makes the
   * protocol *verifiable after the fact* instead of merely asserted:
   *
   *   arg 0 font            -> m_SourceFontFile          @0x24
   *   arg 2 atlasPadding    -> m_AtlasPadding            @0xB8
   *   arg 3 renderMode      -> m_AtlasRenderMode         @0xBC
   *   arg 4 atlasWidth      -> m_AtlasWidth              @0xB0
   *   arg 5 atlasHeight     -> m_AtlasHeight             @0xB4
   *   arg 6 populationMode  -> m_AtlasPopulationMode     @0x28
   *   arg 7 multiAtlas      -> m_IsMultiAtlasTextures…   @0x9C
   *
   * The hidden MethodInfo* is the ninth parameter.  It is 0 by convention in
   * this build, not by guess: the same body passes `i32.const 0` immediately
   * before its own `call 6907` to FontEngine::LoadFontFace.
   */
  function slotArity(slot) {
    if (!table || slot === null || slot === undefined) return -1;
    try {
      var f = table.get(slot);
      return (typeof f === 'function') ? f.length : -1;
    } catch (e) { return -1; }
  }

  function u8(p) {
    if (!mem || !p || p < 1024 || p + 1 > mem.buffer.byteLength) return -1;
    return new Uint8Array(mem.buffer)[p];
  }

  /* Read back everything the callee was told.  Any mismatch means the slot is
     not the overload we think it is, and the asset is discarded. */
  function checkContract(asset, fontPtr) {
    var got = {
      sourceFont: u32(asset + OFF_FA_SOURCE_FONT),
      atlasPadding: u32(asset + OFF_FA_PADDING_SP),
      renderMode: u32(asset + OFF_FA_RENDER_MODE),
      atlasWidth: u32(asset + OFF_FA_ATLAS_W),
      atlasHeight: u32(asset + OFF_FA_ATLAS_H),
      populationMode: u32(asset + OFF_FA_POP_MODE),
      multiAtlas: u8(asset + OFF_FA_MULTI)
    };
    FONT.contract = got;
    var bad = [];
    if (got.sourceFont !== (fontPtr >>> 0)) {
      bad.push('sourceFont ' + hex4(got.sourceFont) + ' != font ' + hex4(fontPtr));
    }
    if (got.atlasPadding !== FONT_PADDING) bad.push('padding ' + got.atlasPadding + ' != ' + FONT_PADDING);
    if (got.renderMode !== FONT_RENDER_MODE) bad.push('renderMode ' + got.renderMode + ' != ' + FONT_RENDER_MODE);
    if (got.atlasWidth !== FONT_ATLAS) bad.push('width ' + got.atlasWidth + ' != ' + FONT_ATLAS);
    if (got.atlasHeight !== FONT_ATLAS) bad.push('height ' + got.atlasHeight + ' != ' + FONT_ATLAS);
    if (got.populationMode !== 1) bad.push('populationMode ' + got.populationMode + ' != 1 (Dynamic)');
    if (got.multiAtlas !== 1) bad.push('multiAtlas ' + got.multiAtlas + ' != 1');
    return bad;
  }

  /* A rejected candidate owns a Texture2D and a Material; drop them instead of
     leaking one set per attempt.  Destroy is deferred to the end of the frame,
     which is fine because nothing else holds a reference any more. */
  function discardAsset(asset) {
    if (!asset) return;
    callPtr(SLOT_OBJECT_DESTROY, [asset, 0, 0], 3, 'Object.Destroy');
    FONT.createdAsset = 0;
    FONT.asset = 0;
  }

  /* Ask FontEngine to rasterise one glyph through the new asset.  Success is
     "the character table grew" OR "TMP says it has it" - the table is the
     harder evidence, so a lying bool cannot pass this on its own. */
  function glyphProbe(asset, cp) {
    var out = { cp: cp, ok: false, added: 0, why: '' };
    if (!FONT.icalls.TryAddGlyphToTexture) {
      out.why = 'FontEngine::TryAddGlyphToTexture_Internal is not linked';
      return out;
    }
    FONT.icalls.TryAddGlyphToTexture = FONT.icalls.TryAddGlyphToTexture || 0;
    var before = u32(u32(asset + OFF_FA_CHARS) + 12);
    var r = callPtr(SLOT_TMP_HAS_CHAR, [asset, cp, 0, 1, 0], 5, 'TMP_FontAsset.HasCharacter');
    if (r.err) { out.why = r.err; return out; }
    var after = u32(u32(asset + OFF_FA_CHARS) + 12);
    var pages = u32(asset + OFF_FA_ATLASES);
    out.charsBefore = before;
    out.charsAfter = after;
    out.pagesAfter = pages ? u32(pages + 12) : -1;
    out.added = after - before;
    /* ok means "this asset can draw the character": either HasCharacter said
       so, or the character table grew.  A character that an earlier probe
       already rasterised legitimately reports added = 0 and is still ok. */
    out.ok = !!r.v || after > before;
    if (!out.ok) {
      out.why = 'no glyph rasterised for U+' + cp.toString(16).toUpperCase() +
                ' (chars ' + before + ' -> ' + after +
                ', atlas pages ' + out.pagesAfter + ')';
    }
    return out;
  }

  /* Build + validate from one candidate legacy Font.  Returns null on success
     or a human-readable reason.  Never mutates the component: the asset only
     becomes reachable through the fallback list. */
  function createFontAsset(fontPtr, source) {
    if (!alive(fontPtr)) return 'candidate Font has no native object (destroyed?)';
    var np = callPtr(SLOT_GET_NAME, [fontPtr, 0], 2, 'Font.get_name');
    var nm = (!np.err && np.v) ? (readSource(np.v) || '') : '';

    FONT.stage = 'create_tmp_asset';
    var actual = slotArity(SLOT_CREATE_FONT);
    FONT.callable = { slot: hex4(SLOT_CREATE_FONT), expected: 9, actual: actual,
                      shortSlot: hex4(SLOT_CREATE_FONT_SHORT),
                      shortActual: slotArity(SLOT_CREATE_FONT_SHORT) };
    if (actual !== 9) {
      return 'CreateFontAsset slot ' + hex4(SLOT_CREATE_FONT) + ' takes ' + actual +
             ' parameter(s), expected 9: the table does not hold the full overload';
    }

    var mk = callPtr(SLOT_CREATE_FONT, [fontPtr, FONT_SAMPLING, FONT_PADDING,
                                       FONT_RENDER_MODE, FONT_ATLAS, FONT_ATLAS,
                                       1 /* Dynamic */, 1 /* multi-atlas */, 0],
                     9, 'CreateFontAsset');
    if (mk.err) return mk.err;
    if (!mk.v) {
      return 'CreateFontAsset returned null: FontEngine.LoadFontFace(font, ' +
             FONT_SAMPLING + ') failed for "' + nm + '"';
    }
    var asset = mk.v;
    FONT.createdAsset = asset;

    FONT.stage = 'verify_asset';
    var bad = checkContract(asset, fontPtr);
    if (bad.length) {
      discardAsset(asset);
      return 'CreateFontAsset protocol mismatch: ' + bad.join('; ');
    }

    var mat = u32(asset + OFF_TA_MATERIAL);
    if (!mat) { discardAsset(asset); return 'new font asset has no material'; }
    var sh = callPtr(SLOT_MAT_SHADER, [mat, 0], 2, 'Material.get_shader');
    var shader = sh.err ? 0 : sh.v;
    if (!shader) {
      discardAsset(asset);
      return 'new material has no shader (ShaderUtilities.ShaderRef_MobileSDF is null)';
    }

    /* m_AtlasTextures is Texture2D[] (not List<T>) at +0x94, and m_AtlasTexture
       at +0x90 is only filled lazily by the getter, so it is still null right
       after creation - reading it was a bug on its own. */
    var pages = readManagedArray(u32(asset + OFF_FA_ATLASES), {
      maxLength: 8, expected: 'Texture2D[]', label: 'm_AtlasTextures'
    });
    if (!pages.ok) { discardAsset(asset); return 'atlas array unreadable: ' + pages.why; }
    var atlas = pages.length ? pages.elems[0] : 0;
    if (!atlas) { discardAsset(asset); return 'new font asset has no atlas texture'; }

    FONT.stage = 'check_glyphs';
    var probes = [], allOk = true, firstBad = null;
    for (var pi = 0; pi < FONT_PROBES.length; pi++) {
      var g = glyphProbe(asset, FONT_PROBES[pi][1]);
      g.key = FONT_PROBES[pi][0]; g.ch = FONT_PROBES[pi][2];
      probes.push(g);
      if (!g.ok) { allOk = false; if (!firstBad) firstBad = g; }
    }
    var passed = 0;
    for (var pp = 0; pp < probes.length; pp++) if (probes[pp].ok) passed++;
    FONT.glyphCheck = { ok: allOk, passed: passed, total: probes.length,
                        failed: probes.length - passed, probes: probes,
                        list: '\u521B\u7231\u67AB\u6218\u70BC\u9F99' };
    for (var pk = 0; pk < probes.length; pk++) FONT.glyphCheck[probes[pk].key] = probes[pk];
    /* 创 and 爱 are the gate (they are the selection probes as well); the rest
       are reported.  With the full face every one of them has to rasterise, so
       a false here is real evidence rather than a guess. */
    if (!probes[0].ok || !probes[1].ok) {
      discardAsset(asset);
      return 'asset cannot rasterise U+521B/U+7231 from "' + nm + '" (' +
             (probes[0].why || probes[1].why) + ')';
    }
    if (!allOk) {
      try {
        console.warn('[monmusu_cn font] partial glyph coverage: ' +
          probes.filter(function (q) { return !q.ok; })
                .map(function (q) { return q.ch + '(' + q.why + ')'; }).join(', '));
      } catch (e) { /* no console */ }
    }

    FONT.font = fontPtr;
    FONT.source = source || '';
    FONT.fontName = nm;
    FONT.asset = asset;
    FONT.material = mat;
    FONT.shader = shader;
    FONT.atlas = atlas;
    FONT.atlasW = u32(asset + OFF_FA_ATLAS_W);
    FONT.atlasH = u32(asset + OFF_FA_ATLAS_H);

    // match the stroke weight of the text this component is already drawing
    var srcMat = owner ? u32(owner + OFF_TEXT_SHARED_MAT) : 0;
    if (!srcMat && owner) {
      var ownFa = u32(owner + OFF_TEXT_FONTASSET);
      if (ownFa) srcMat = u32(ownFa + OFF_TA_MATERIAL);
    }
    FONT.style = syncMaterialStyle(FONT.material, srcMat);
    return null;
  }

  /* Discovery: UnityEngine.Font objects are the only way to reach the legacy
     face, and Resources.FindObjectsOfTypeAll needs a System.Type for it. */
  /* ================= discovery, in named stages =================
   *
   *   resolve_type -> find_fonts -> read_font_array -> select_font
   *   -> create_tmp_asset -> verify_asset -> check_glyphs
   *   -> register_fallback -> ready
   *
   * fontStatus() reports the stage it stopped in, so a failure on the device
   * is one console line away from the exact call that broke.
   */

  /* Type.GetType is the only way to turn a name into a System.Type here.
     "UnityEngine.Font" lives in TextRenderingModule, so the assembly-qualified
     form is required; the second spelling is a cheap retry. */
  function resolveNamedType(typeNames) {
    var type = 0, err = '';
    for (var t = 0; t < typeNames.length && !type; t++) {
      var sPtr = allocManagedString(typeNames[t]);
      if (!sPtr) return { err: 'cannot allocate the type-name string (monmusu_cn_alloc missing)' };
      var g = callPtr(SLOT_TYPE_GETTYPE, [sPtr, 0], 2, 'Type.GetType');
      if (g.err) return { err: g.err };
      type = g.v;
      if (!type) err = 'Type.GetType("' + typeNames[t] + '") is null';
    }
    if (!type) return { err: err };
    return { type: type };
  }

  function resolveFontType() {
    return resolveNamedType([
      'UnityEngine.Font, UnityEngine.TextRenderingModule',
      'UnityEngine.Font, UnityEngine.TextRenderingModule, Version=0.0.0.0, ' +
      'Culture=neutral, PublicKeyToken=null'
    ]);
  }

  /* Everything the caller needs to decide between "retry later" and "this
     build cannot do it": an empty array is not an error, a bogus array is. */
  function findFonts() {
    var t = resolveFontType();
    if (t.err) return { stage: 'resolve_type', err: t.err, fatal: true };

    FONT.stage = 'find_fonts';
    var r = callPtr(SLOT_FIND_ALL, [t.type, 0], 2, 'FindObjectsOfTypeAll');
    if (r.err) return { stage: 'find_fonts', err: r.err, fatal: true };
    var arr = r.v;
    if (!arr) return { stage: 'find_fonts', err: 'FindObjectsOfTypeAll returned null', fatal: true };

    FONT.stage = 'read_font_array';
    var sptr = u32(FIND_ALL_SPTR);
    FONT.icalls.FindObjectsOfTypeAll = sptr;
    if (!sptr) {
      /* the wrapper caches what resolve_icall returned; 0 means it never
         resolved, so the pointer above came from table slot 0 and cannot be
         trusted no matter how plausible it looks. */
      FONT.arrayPointer = arr;
      return { stage: 'read_font_array',
               err: 'icall not linked: ResourcesAPIInternal::FindObjectsOfTypeAll resolves to 0',
               fatal: true };
    }

    var info = readManagedArray(arr, {
      maxLength: 65536,
      expected: 'Il2CppArray: klass@0, bounds@8 (null = vector), max_length@12, data@16',
      label: 'FindObjectsOfTypeAll'
    });
    FONT.expectedLayout = info.expected;
    FONT.observedLayout = 'FindObjectsOfTypeAll ' + info.observed;
    FONT.arrayPointer = arr;
    FONT.arrayType = info.typeName || (info.ok ? '' : '<not an array>');
    FONT.arrayLength = info.ok ? info.length : -1;

    if (!info.ok) {
      return { stage: 'read_font_array', err: info.why, fatal: true };
    }
    if (info.length === 0) {
      return { stage: 'find_fonts', err: 'no Font is loaded yet (array length 0)', retry: true };
    }
    return { stage: 'find_fonts', elems: info.elems };
  }

  /* Two independent probes, because a name proves nothing and a single bool
     can lie: the carrier keeps the name it always had, and Font.HasCharacter
     is only one of two filters (the other is the real rasterisation test in
     glyphProbe). */
  function probeFontChars(p) {
    var out = { chuang: -1, ai: -1 };
    var c1 = callPtr(SLOT_FONT_HAS_CHAR, [p, CH_PROBE, 0], 3, 'Font.HasCharacter');
    var sp = u32(HAS_CHAR_SPTR);
    FONT.icalls.HasCharacter = sp;
    if (c1.err || !sp) return { chuang: -1, ai: -1, why: c1.err || 'icall not linked' };
    out.chuang = c1.v ? 1 : 0;
    var c2 = callPtr(SLOT_FONT_HAS_CHAR, [p, CH_PROBE2, 0], 3, 'Font.HasCharacter');
    if (!c2.err) out.ai = c2.v ? 1 : 0;
    return out;
  }

  function selectCandidates(elems) {
    var useKlass = verifyKlassLayout();
    var seen = [], cands = [];
    for (var i = 0; i < elems.length; i++) {
      var p = elems[i];
      if (!p || seen.indexOf(p) >= 0) continue;
      seen.push(p);
      if (!alive(p)) continue;                       // destroyed or no native face
      var kn = useKlass ? klassNameOf(p) : '';
      if (useKlass && kn !== 'Font') continue;
      var np = callPtr(SLOT_GET_NAME, [p, 0], 2, 'Font.get_name');
      var nm = (!np.err && np.v) ? (readSource(np.v) || '') : '';
      var cov = probeFontChars(p);
      cands.push({ ptr: p, name: nm, klass: kn,
                   chuang: cov.chuang, ai: cov.ai, why: cov.why || '' });
    }

    var list = [], probes = [];
    for (var a = 0; a < cands.length; a++) {
      var c0 = cands[a];
      var tag = (c0.chuang === 1 && c0.ai === 1) ? ''
              : (c0.chuang < 0 ? ' (unchecked)' : ' (no U+521B/U+7231)');
      list.push(c0.name + tag);
      probes.push({ name: c0.name, chuang: c0.chuang, ai: c0.ai, why: c0.why });
    }
    FONT.fontsSeen = list;
    FONT.probeChars = probes;

    /* Only a face that answers *both* probes may become a TMP source.  An
       "unchecked" candidate is not good enough: the spec for this stage is a
       verified CJK face, not a hopeful one. */
    var ordered = [], rest = [];
    for (var b = 0; b < cands.length; b++) {
      var c = cands[b];
      if (c.chuang !== 1 || c.ai !== 1) continue;
      if (/NewRodin|monmusu_cn|FOT|Liberation/i.test(c.name)) ordered.push(c);
      else rest.push(c);
    }
    ordered = ordered.concat(rest);

    var names = [];
    for (var d = 0; d < ordered.length; d++) names.push(ordered[d].name);
    FONT.candidates = names;
    if (ordered.length) FONT.sourceFontReady = true;
    return ordered.slice(0, FONT_MAX_CANDIDATES);
  }

  /* Hard gate: without these three icalls the managed wrappers would jump
     through a null element-table slot, which corrupts the instance. */
  function fontEngineReady() {
    var probes = [
      ['InitializeFontEngine', ICALL_INIT],
      ['LoadFontFace', ICALL_LOADFACE],
      ['GetFaceInfo', ICALL_FACEINFO]
    ];
    for (var i = 0; i < probes.length; i++) {
      var label = probes[i][0], addr = probes[i][1];
      var lit = cstrAt(addr);
      if (!lit || lit.indexOf(ICALL_PREFIX) !== 0) {
        return 'icall name literal missing at ' + hex4(addr) + ' (different build?)';
      }
      var pr = callPtr(SLOT_RESOLVE_ICALL, [addr], 1, 'resolve_icall');
      if (pr.err) return pr.err;
      FONT.icalls[label] = pr.v || 0;
      if (!pr.v) return 'FontEngine icall not linked: ' + lit;
    }
    /* Optional probe: TMP_FontAsset.HasCharacter(tryAdd:true) rasterises one
       glyph through this entry point.  If it is missing we simply skip that
       extra verification instead of failing the whole install. */
    var opt = cstrAt(ICALL_ADDGLYPH, 512);
    if (opt && opt.indexOf(ICALL_PREFIX) === 0) {
      var op = callPtr(SLOT_RESOLVE_ICALL, [ICALL_ADDGLYPH], 1, 'resolve_icall');
      FONT.icalls.TryAddGlyphToTexture = (!op.err && op.v) ? op.v : 0;
    } else {
      FONT.icalls.TryAddGlyphToTexture = 0;
    }
    return null;
  }

  /* Text components seen before the font was ready: TMP will not re-run glyph
     lookup on its own, so they are marked dirty once the fallback exists. */
  var textOwners = [];

  function noteOwner(p) {
    if (!p) return;
    if (textOwners.indexOf(p) >= 0) return;
    if (textOwners.length >= 32) textOwners.shift();
    textOwners.push(p);
  }

  function refreshTexts() {
    var n = 0;
    for (var i = 0; i < textOwners.length; i++) {
      var p = textOwners[i];
      if (!alive(p)) continue;                       // keep the call off dead objects
      var r = callPtr(SLOT_TMP_SET_HPC, [p, 1, 0], 3, 'TMP_Text.set_havePropertiesChanged');
      if (!r.err) n++;
    }
    return n;
  }

  /* Read-only census of the TMP font assets that are actually loaded.
   *
   * This exists to answer one question that cannot be answered offline: do the
   * UI prefabs (which live in AssetBundles downloaded at run time) reference the
   * one asset inside data.unity3d, or do they carry their own copies?  If more
   * than one object reports the same m_Name, bundles embed copies and an
   * in-place edit of data.unity3d alone would not reach the live UI.
   *
   * Never called automatically; __monmusuCnBridge.fontAssets() only reads. */
  /* Read-only census of the loaded AssetBundles, by name.
   *
   * The font asset exists twice in memory: one copy ships inside the player
   * data, the other inside an AssetBundle.  Which bundle holds it cannot be
   * answered from the asset itself, so this lists what the runtime actually
   * has open.  Nothing is loaded here: FindObjectsOfTypeAll only reports
   * objects that already exist.
   *
   * __monmusuCnBridge.bundles() */
  function bundleCensus() {
    if (!mem || !table) return { error: 'bridge not initialised' };
    var t = resolveNamedType([
      'UnityEngine.AssetBundle, UnityEngine.AssetBundleModule',
      'UnityEngine.AssetBundle, UnityEngine.AssetBundleModule, Version=0.0.0.0, ' +
      'Culture=neutral, PublicKeyToken=null'
    ]);
    if (t.err) return { error: t.err };
    var r = callPtr(SLOT_FIND_ALL, [t.type, 0], 2, 'FindObjectsOfTypeAll(AssetBundle)');
    if (r.err || !r.v) return { error: r.err || 'FindObjectsOfTypeAll returned null' };
    var arr = readManagedArray(r.v, {
      maxLength: 512, expected: 'AssetBundle[]', label: 'AssetBundle[]'
    });
    if (!arr.ok) return { error: arr.why, observed: arr.observed };
    var names = [];
    for (var i = 0; i < arr.length; i++) {
      var p = arr.elems[i];
      if (!p) continue;
      names.push(nameOfPtr(p));
    }
    return { count: names.length, names: names.sort() };
  }

  /* Read-only: UnityEngine.Object name for a pointer that came out of a
     validated structure.  Returns '' on anything unexpected. */
  function nameOfPtr(p) {
    if (!p || !alive(p)) return '';
    var np = callPtr(SLOT_GET_NAME, [p, 0], 2, 'get_name');
    return (!np.err && np.v) ? (readSource(np.v) || '') : '';
  }

  function fontAssetCensus() {
    if (!mem || !table) return { error: 'bridge not initialised' };
    var t = resolveNamedType([
      'TMPro.TMP_FontAsset, Unity.TextMeshPro',
      'TMPro.TMP_FontAsset, Unity.TextMeshPro, Version=0.0.0.0, ' +
      'Culture=neutral, PublicKeyToken=null'
    ]);
    if (t.err) return { error: t.err };
    var r = callPtr(SLOT_FIND_ALL, [t.type, 0], 2, 'FindObjectsOfTypeAll(TMP_FontAsset)');
    if (r.err || !r.v) return { error: r.err || 'FindObjectsOfTypeAll returned null' };
    var arr = readManagedArray(r.v, {
      maxLength: 64, expected: 'TMP_FontAsset[]', label: 'TMP_FontAsset[]'
    });
    if (!arr.ok) return { error: arr.why, observed: arr.observed };
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var p = arr.elems[i];
      if (!p) continue;
      var np = callPtr(SLOT_GET_NAME, [p, 0], 2, 'get_name');
      var mono = u32(p + OFF_FA_POP_MODE);
      var glyphs = u32(p + OFF_FA_GLYPHS);
      var chars = u32(p + OFF_FA_CHARS);
      var pages = u32(p + OFF_FA_ATLASES);
      var atlasPtrs = [];
      if (pages) {
        var texArr = readManagedArray(pages, {
          maxLength: 8, expected: 'Texture2D[]', label: 'm_AtlasTextures'
        });
        if (texArr.ok) atlasPtrs = texArr.elems;
      }
      out.push({
        ptr: p,
        name: (!np.err && np.v) ? readSource(np.v) : '',
        populationMode: POP_MODE[mono] || mono,
        sourceFont: u32(p + OFF_FA_SOURCE_FONT),
        glyphs: glyphs ? u32(glyphs + 12) : -1,
        characters: chars ? u32(chars + 12) : -1,
        atlas: u32(p + OFF_FA_ATLAS_W) + 'x' + u32(p + OFF_FA_ATLAS_H),
        pages: pages ? u32(pages + 12) : -1,
        material: u32(p + OFF_TA_MATERIAL),
        materialName: nameOfPtr(u32(p + OFF_TA_MATERIAL)),
        atlasPtr: atlasPtrs.length ? atlasPtrs[0] : 0,
        atlasName: atlasPtrs.length ? nameOfPtr(atlasPtrs[0]) : ''
      });
    }
    return { count: out.length, arrayType: arr.typeName, assets: out };
  }

  /* Every TMP_Text currently alive, dirtied once.  This is the "regenerate the
     first popup's mesh" answer: the volume popup is laid out before the font
     exists, and it is not necessarily one of the components we recorded, so the
     recorded list alone can miss it.  One pass, right after the install, no new
     hook, no resource loading, no delay guessing. */
  function refreshTypeOf(typeName) {
    if (!typeName) return -1;
    var t = resolveNamedType([
      typeName + ', Unity.TextMeshPro',
      typeName + ', Unity.TextMeshPro, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null'
    ]);
    if (t.err) return -1;
    var r = callPtr(SLOT_FIND_ALL, [t.type, 0], 2, 'FindObjectsOfTypeAll(' + typeName + ')');
    if (r.err || !r.v) return -1;
    var arr = readManagedArray(r.v, { maxLength: 4096, expected: '[]', label: typeName + '[]' });
    if (!arr.ok) return -1;
    var n = 0;
    for (var i = 0; i < arr.length; i++) {
      var p = arr.elems[i];
      if (!p || !alive(p)) continue;
      var q = callPtr(SLOT_TMP_SET_HPC, [p, 1, 0], 3, 'set_havePropertiesChanged');
      if (!q.err) n++;
    }
    return n;
  }

  function refreshAllTexts() {
    if (!FONT.refreshAll || !FONT.asset) return -1;
    /* the base type first (Unity matches derived types as well); the concrete
       one is a fallback in case a build reports exact-type matches only */
    var n = refreshTypeOf('TMPro.TMP_Text');
    if (n > 0) return n;
    var m = refreshTypeOf('TMPro.TextMeshProUGUI');
    return (m >= 0) ? m : n;
  }

  /* ---------- one Chinese font for the translated UI ----------
   *
   * The fallback list only helps for glyphs the Japanese asset lacks, so a
   * single string ends up drawn with two faces.  For components whose string
   * we actually translated, the Chinese asset becomes the *primary* font.
   *
   * TMP_Text.set_font is the game's own setter (slot 0x18CEC, 159 bytes): it
   * assigns m_fontAsset, calls the virtual LoadFontAsset, then flags
   * m_havePropertiesChanged and dirties vertices+layout.  It does NOT touch
   * m_sharedMaterial when the component already has one - TextMeshProUGUI's
   * LoadFontAsset keeps the existing material - so the material is assigned
   * explicitly right after, and then *verified* through the component and the
   * material's own _MainTex.
   *
   * Anything whose current material is not a TMP SDF shader is left alone
   * (custom effects, bitmap shaders): switching those would need per-effect
   * material surgery, which is out of scope for this round.
   */
  /* Components we switched, with what they had before, so the feature can be
     reverted at runtime instead of only stopped. */
  var unifiedOwners = [];

  function rememberUnified(p, font, mat) {
    for (var i = 0; i < unifiedOwners.length; i++) {
      if (unifiedOwners[i].p === p) return;
    }
    if (unifiedOwners.length >= 128) unifiedOwners.shift();
    unifiedOwners.push({ p: p, font: font, mat: mat });
  }

  function restoreUnified() {
    var n = 0;
    for (var i = 0; i < unifiedOwners.length; i++) {
      var u = unifiedOwners[i];
      if (!alive(u.p)) continue;
      if (u.font) callPtr(SLOT_TMP_SET_FONT, [u.p, u.font, 0], 3, 'restore set_font');
      if (u.mat) callPtr(SLOT_TMP_SET_SHARED_MAT, [u.p, u.mat, 0], 3, 'restore material');
      n++;
    }
    unifiedOwners.length = 0;
    FONT.unified = 0;
    FONT.restored = n;
    return n;
  }

  function rollbackOwnerFont(p, oldFont, oldMat) {
    if (oldFont) callPtr(SLOT_TMP_SET_FONT, [p, oldFont, 0], 3, 'rollback set_font');
    if (oldMat) callPtr(SLOT_TMP_SET_SHARED_MAT, [p, oldMat, 0], 3, 'rollback set_fontSharedMaterial');
  }

  function applyFontToOwner(ownerPtr) {
    if (!FONT.enabled || !FONT.unify) return;
    if (!FONT.asset || FONT.state !== 'ready') return;
    var p = ownerPtr >>> 0;
    if (!p || !alive(p)) return;

    var cur = callPtr(SLOT_TMP_GET_FONT, [p, 0], 2, 'TMP_Text.get_font');
    if (cur.err) { FONT.unifyErrors++; return; }
    if (cur.v === FONT.asset) { FONT.unifySame++; return; }   // already unified

    var oldMatR = callPtr(SLOT_TMP_GET_SHARED_MAT, [p, 0], 2, 'get_fontSharedMaterial');
    var oldMat = oldMatR.err ? 0 : oldMatR.v;

    if (oldMat) {
      var sh = callPtr(SLOT_MAT_SHADER, [oldMat, 0], 2, 'Material.get_shader');
      var shName = '';
      if (!sh.err && sh.v) {
        var np = callPtr(SLOT_GET_NAME, [sh.v, 0], 2, 'Shader.get_name');
        if (!np.err && np.v) shName = readSource(np.v) || '';
      }
      if (!/distance\s*field/i.test(shName)) {
        FONT.unifySkipped++;
        if (FONT.skipShaders.length < 8 && FONT.skipShaders.indexOf(shName || '(null)') < 0) {
          FONT.skipShaders.push(shName || '(null)');
        }
        return;
      }
    }

    var r1 = callPtr(SLOT_TMP_SET_FONT, [p, FONT.asset, 0], 3, 'TMP_Text.set_font');
    if (r1.err) { FONT.unifyErrors++; return; }
    var r2 = callPtr(SLOT_TMP_SET_SHARED_MAT, [p, FONT.material, 0], 3, 'set_fontSharedMaterial');
    if (r2.err) { rollbackOwnerFont(p, cur.v, oldMat); FONT.unifyErrors++; return; }

    /* prove it from the component itself: font, material, and the atlas the
       material actually samples.  A font swap without a matching material
       renders garbage, so a mismatch rolls the component back instead. */
    var f2 = callPtr(SLOT_TMP_GET_FONT, [p, 0], 2, 'get_font');
    var m2 = callPtr(SLOT_TMP_GET_SHARED_MAT, [p, 0], 2, 'get_fontSharedMaterial');
    var tex = (m2.err || !m2.v) ? { v: 0 }
            : callPtr(SLOT_MAT_MAIN_TEX, [m2.v, 0], 2, 'Material.get_mainTexture');
    if (f2.v !== FONT.asset || m2.v !== FONT.material || tex.v !== FONT.atlas) {
      rollbackOwnerFont(p, cur.v, oldMat);
      FONT.unifyErrors++;
      FONT.unifyLast = { owner: p, font: f2.v, material: m2.v, texture: tex.v, rolledBack: true };
      return;
    }
    FONT.unified++;
    rememberUnified(p, cur.v, oldMat);
    FONT.unifyLast = { owner: p, font: f2.v, material: m2.v, texture: tex.v, rolledBack: false };
  }

  var fontT0 = 0;

  function finishInstall() {
    FONT.stage = 'register_fallback';
    var reg = registerFallback(FONT.asset);
    if (reg) {
      discardAsset(FONT.asset);
      return fontFail('register_fallback', reg);
    }
    FONT.fallbackRegistered = true;

    FONT.stage = 'ready';
    FONT.state = 'ready';
    FONT.reason = '';   // a previous retry reason is not a current failure
    FONT.ms = fontT0 ? Math.round(nowMs() - fontT0) : -1;
    refreshFontStats();
    FONT.refreshed = refreshTexts();
    FONT.refreshedAll = refreshAllTexts();
    try {
      console.log('[monmusu_cn font] installed dynamic TMP asset from "' + FONT.fontName + '" (' +
                  FONT.source + '): atlas ' + FONT.atlasW + 'x' + FONT.atlasH +
                  ', shader ' + hex4(FONT.shader) + ', fallback slot ' + FONT.fallbacks +
                  ', glyphs ' + (FONT.glyphCheck ? FONT.glyphCheck.passed : 0) +
                    '/' + FONT_PROBES.length +
                  ', style ' + ((FONT.style && FONT.style.join('/')) || 'default') +
                  ', refreshed ' + FONT.refreshed + '+' + FONT.refreshedAll +
                  ', ' + FONT.ms + 'ms');
    } catch (e) { /* no console */ }
    return true;
  }

  function tryInstall() {
    var gate = fontEngineReady();
    if (gate) return fontFail('probe_icalls', gate);

    var f = findFonts();
    if (f.err) {
      if (f.retry) return fontRetry(f.stage, f.err);
      return fontFail(f.stage, f.err);
    }

    FONT.stage = 'select_font';
    var cands = selectCandidates(f.elems);
    if (!cands.length) {
      var names = (FONT.fontsSeen || []).join(', ') || '(none)';
      var why = 'no loaded Font carries U+521B and U+7231: ' + names;
      if (FONT.attempts < FONT_SELECT_ATTEMPTS) return fontRetry('select_font', why);
      return fontFail('select_font', why +
        ' - the LiberationSans carrier in ntd.data is missing or still the Latin face');
    }

    /* Creating an asset is expensive and leaks one Texture2D per attempt, so
       this step never retries: whatever fails here is reported as-is. */
    var last = '';
    for (var i = 0; i < cands.length; i++) {
      FONT.selectedFont = {
        ptr: cands[i].ptr, name: cands[i].name, source: 'FindObjectsOfTypeAll',
        klass: cands[i].klass || 'Font',
        probes: { chuang: cands[i].chuang, ai: cands[i].ai }
      };
      var e = createFontAsset(cands[i].ptr, 'FindObjectsOfTypeAll');
      if (e === null) return finishInstall();
      last = e;
    }
    return fontFail(FONT.stage, last || 'no candidate produced a font asset');
  }


  function installFont(fontPtr) {
    if (!FONT.enabled) return false;
    if (FONT.state === 'ready') return true;
    if (FONT.state === 'failed') return false;
    if (!mem || !table) return fontFail('init', 'bridge not initialised');

    FONT.state = 'trying';
    FONT.stage = 'resolve_type';
    FONT.attempts++;
    FONT.retryAt = nowMs() + FONT_RETRY_MS;
    fontT0 = nowMs();
    try {
      if (fontPtr) {
        /* manual override: the caller proved it has a Font pointer */
        FONT.sourceFontReady = true;
        FONT.selectedFont = { ptr: fontPtr >>> 0, name: '(manual)', source: 'manual', klass: 'Font' };
        var e = createFontAsset(fontPtr >>> 0, 'manual');
        if (e) return fontFail(FONT.stage, e);
        return finishInstall();
      }
      return tryInstall();
    } catch (ex) {
      FONT.ms = fontT0 ? Math.round(nowMs() - fontT0) : -1;
      return fontFail(FONT.stage, 'exception: ' + ((ex && ex.message) || ex));
    }
  }

  /* Called on every translated UI string.  Cheap when ready or failed, and
     rate-limited while trying, because the Font may simply not be loaded yet. */
  function ensureFont() {
    if (!FONT.enabled) return;
    if (FONT.state === 'ready' || FONT.state === 'failed' || FONT.state === 'disabled') return;
    if (FONT.state === 'trying' && nowMs() < FONT.retryAt) return;
    installFont(0);
  }

  function fontStatus() {
    refreshFontStats();
    return {
      enabled: FONT.enabled,
      state: FONT.state,
      stage: FONT.stage,
      reason: FONT.reason,
      attempts: FONT.attempts,
      source: FONT.source,
      legacyFont: { ptr: FONT.font, name: FONT.fontName },
      selectedFont: FONT.selectedFont,
      candidates: FONT.candidates || [],
      /* the five gates the device report has to answer, one line each */
      callable: FONT.callable,
      sourceFontReady: FONT.sourceFontReady,
      createdAsset: FONT.createdAsset,
      fallbackRegistered: FONT.fallbackRegistered,
      glyphCheck: FONT.glyphCheck,
      contract: FONT.contract,
      probeChars: FONT.probeChars || [],
      arrayType: FONT.arrayType,
      arrayPointer: FONT.arrayPointer,
      arrayLength: FONT.arrayLength,
      expectedLayout: FONT.expectedLayout,
      observedLayout: FONT.observedLayout,
      fallbackType: FONT.fallbackType,
      fallbackLength: FONT.fallbackLength,
      fallbackObserved: FONT.fallbackObserved,
      unify: FONT.unify,
      unified: FONT.unified,
      restored: FONT.restored,
      unifySame: FONT.unifySame,
      unifySkipped: FONT.unifySkipped,
      unifyErrors: FONT.unifyErrors,
      skipShaders: FONT.skipShaders || [],
      unifyLast: FONT.unifyLast,
      refreshedAll: FONT.refreshedAll,
      fallbackIndex: FONT.fallbackIndex,
      fallbackAfter: FONT.fallbackAfter,
      grow: FONT.grow,
      growObserved: FONT.growObserved,
      klassLayout: FONT.klassLayout,
      asset: FONT.asset,
      material: FONT.material,
      shader: FONT.shader,
      atlas: FONT.atlas,
      atlasSize: FONT.atlasW + 'x' + FONT.atlasH,
      pages: FONT.pages,
      characters: FONT.characters,
      glyphs: FONT.glyphs,
      fallbackCount: FONT.fallbacks,
      style: FONT.style || [],
      refreshed: FONT.refreshed,
      icalls: FONT.icalls,
      fontsSeen: FONT.fontsSeen || [],
      installMs: FONT.ms
    };
  }

  /* Off switch that actually undoes the install: the asset is pulled back out
     of TMP_Settings.fallbackFontAssets, so no later layout pass can reach it.
     The created asset itself is left to the GC - we cannot destroy a
     ScriptableObject safely from JS. */
  function fontDisable() {
    FONT.enabled = false;
    if (FONT.unified) restoreUnified();
    if (FONT.state === 'ready') {
      var why = unregisterFallback(FONT.asset);
      FONT.state = 'disabled';
      FONT.reason = why ? ('disabled, fallback entry kept: ' + why)
                        : 'disabled after install, fallback entry removed';
    } else {
      FONT.state = 'disabled';
      FONT.fallbacks = 0;
      if (!FONT.reason) FONT.reason = 'disabled before install';
    }
    return true;
  }

  /* ---------- import handlers ---------- */

  /* translation import: null -> forward to original; number -> length */
  function handleTranslate(a, b) {
    var b2 = b >>> 0;
    /* The UI tags are sent as exact values (no pointer packed into them):
       a destination pointer above 128 MiB would otherwise collide with the
       marker bits, and this heap easily grows past that. */
    if (b2 === UI_TAG || b2 === UI_FILL) return handleUi(a, b2);
    if ((b2 & TAG_TRANSLATE) === 0) return null;
    if (!hasTable()) {
      if (storyTraceOn) {
        var noTableSource = null;
        try { noTableSource = readSource(a >>> 0); } catch (e) { /* diagnostic only */ }
        traceStory('story', noTableSource, '', 'no-translation-table');
      }
      return 0;
    }
    var dst = b2 & MASK;
    var src;
    try { src = readSource(a >>> 0); } catch (e) {
      traceStory('story', null, '', 'read-source-exception'); stats.errors++; return 0;
    }
    if (src === null) { traceStory('story', null, '', 'invalid-or-empty-source'); return 0; }

    var t;
    if (dst === 0) {
      t = resolveText(src);
      lastResult = { src: src, tr: t, trace: traceStory('story', src, t,
        t ? '' : (index && index.isKnownStoryValue(src) ? 'known-value' : 'no-story-rule')) };
      if (!t) { stats.misses++; return 0; }
      stats.probes++; stats.hits++;
      if (samples.length < 4) {
        samples.push({
          src: src, dst: t, units: t.length,
          codeUnits: Array.prototype.map.call(t, function (c) {
            return 'U+' + ('0000' + c.charCodeAt(0).toString(16).toUpperCase()).slice(-4);
          })
        });
      }
      return t.length;
    }
    t = (lastResult.src === src) ? lastResult.tr : resolveText(src);
    if (!t) return 0;
    if (!writeChars(dst, t)) {
      if (lastResult.trace && lastResult.src === src) {
        lastResult.trace.write = 'failed'; lastResult.trace.reason = 'writeChars-rejected';
      }
      stats.errors++; return 0;
    }
    if (lastResult.trace && lastResult.src === src) {
      lastResult.trace.write = 'buffer-written'; lastResult.trace.destination = dst;
    }
    stats.fills++;
    return t.length;
  }

  /* UI text import.  Two stages, exactly like the story hook: probe returns the
     length, then the hook allocates a managed string and we fill its chars.
       probe: arg1 = source Il2CppString*, arg2 = UI_TAG
       fill : arg1 = destination chars*,   arg2 = UI_FILL
     The fill stage carries no source pointer, so the pair is remembered. */
  function handleUi(a, b2) {
    if (inUi) { uiStats.skipped++; return 0; }
    inUi = true;
    try {
      var fill = (b2 === UI_FILL);
      if (!fill) {
        var src;
        try { src = readSource(a >>> 0); } catch (e) { uiStats.errors++; return 0; }
        if (src === null) return 0;
        var t = resolveUi(src, owner);
        lastUi = { src: src, tr: t, n: t ? t.length : 0,
          trace: traceStory('tmp-ui', src, t, t ? '' : 'ui-only-no-story-query', owner) };
        if (lastUi.trace) {
          lastUi.trace.rule = uiIndex && uiIndex.stats ? uiIndex.stats.lastRule : 'no-ui-index';
        }
        if (!t) { uiStats.misses++; return 0; }
        uiStats.probes++; uiStats.hits++;
        /* The Japanese UI font cannot draw the simplified glyphs, so make the
           Chinese-capable asset available before this string is laid out. */
        /* record the component either way: fontInfo() is a read-only probe and
           must keep answering even when the installer is switched off (that is
           exactly the configuration the stage-9 asset experiment runs in) */
        noteOwner(owner);
        if (FONT.enabled) {
          ensureFont();
          /* translated string -> primary font, verified font+material+atlas */
          applyFontToOwner(owner);
        }
        if (!uiStats.fontLogged) {
          uiStats.fontLogged = 1;
          try {
            if (typeof console !== 'undefined' && console.log) {
              console.log('[monmusu_cn] first translated UI text; font =', fontInfo(owner));
            }
          } catch (e) { /* diagnostic only */ }
        }
        return t.length;
      }

      var dst = a >>> 0, tr = lastUi.tr || '';
      if (!tr) { uiStats.errors++; return 0; }
      if (tr.length !== lastUi.n) {
        /* only reachable if the probe/fill pair was interleaved, which would
           mean the allocation no longer matches the text */
        uiStats.stale++;
        tr = tr.substring(0, lastUi.n);
      }
      if (!writeChars(dst, tr)) {
        if (lastUi.trace) { lastUi.trace.write = 'failed'; lastUi.trace.reason = 'writeChars-rejected'; }
        uiStats.errors++; return 0;
      }
      if (lastUi.trace) {
        lastUi.trace.write = 'buffer-written'; lastUi.trace.destination = dst;
      }
      uiStats.fills++;
      return tr.length;
    } finally {
      inUi = false;
    }
  }

  /* context import: null -> forward to original; 0 -> handled */
  function handleContext(a, b) {
    var b2 = b >>> 0;
    if ((b2 & TAG_CONTEXT) === 0 || (b2 & TAG_TRANSLATE) || (b2 & UI_TAG)) return null;
    var cmd = b2 & 0xff;
    if (cmd === CMD.UI_OWNER) {
      // arg1 is a Component*, not a string: never read it as one
      owner = a >>> 0;
      uiStats.ownerCalls++;
      return 0;
    }
    if (!index || !ctx) return 0;
    var text = '';
    try { text = readSource(a >>> 0) || ''; } catch (e) { text = ''; }
    stats.contextCalls++;
    try {
      switch (cmd) {
        case CMD.PARSE_NAME:
          ctx.beginParsing(text);
          break;
        case CMD.PARSE_GRID: {
          var scope = ctx.snapshot().scopes;
          // only fill an empty scope; never overwrite an already-known scene
          if (scope.length && !scope[scope.length - 1]) ctx.setCell(text);
          break;
        }
        case CMD.PARSE_END:
          ctx.endParsing();
          break;
        case CMD.PLAYBACK_BEGIN:
          ctx.beginPlayback(text);
          break;
        case CMD.PLAYBACK_END:
          ctx.endPlayback();
          break;
        case CMD.CELL_NAME:
        case CMD.CELL_SHEET:
          ctx.setCell(text);
          break;
        default:
          break;
      }
      if (storyTraceOn) {
        var event = traceStory('context', text, '', 'context-command-' + cmd);
        event.write = 'not-applicable';
      }
    } catch (e) { stats.errors++; }
    return 0;
  }

  function wrapImport(orig) {
    return function (a, b) {
      var r = handleTranslate(a, b);
      if (r === null) { stats.passthrough++; return orig.apply(null, arguments); }
      return r;
    };
  }
  function wrapContextImport(orig) {
    return function (a, b) {
      var r = handleContext(a, b);
      if (r === null) { stats.passthrough++; return orig.apply(null, arguments); }
      return r;
    };
  }

  return {
    setMemory: setMemory, setTable: setTable, setStoryIndex: setStoryIndex,
    setUiIndex: setUiIndex, setTableExport: setTableExport,
    setExports: setExports,
    exportTableFrom: exportTableFrom,
    hasTable: hasTable, readSource: readSource, writeChars: writeChars,
    handle: handleTranslate, handleContext: handleContext,
    wrapImport: wrapImport, wrapContextImport: wrapContextImport,
    stats: stats, uiStats: uiStats, samples: samples, CMD: CMD,
    TAG_TRANSLATE: TAG_TRANSLATE, TAG_CONTEXT: TAG_CONTEXT,
    UI_TAG: UI_TAG, UI_FILL: UI_FILL, MASK: MASK,
    scene: function () { return ctx ? ctx.snapshot() : null; },
    getIndex: function () { return index; }, getContext: function () { return ctx; },
    getUiIndex: function () { return uiIndex; },
    storyTrace: function (on) {
      if (on !== undefined) storyTraceOn = !!on;
      return storyTraceBuf.map(function (r) { return JSON.parse(JSON.stringify(r)); });
    },
    storyTraceClear: function () { storyTraceBuf = []; return 0; },
    storyTraceDump: function () { return storyTraceBuf.map(function (r) { return JSON.stringify(r); }).join('\n'); },
    fontInfo: fontInfo,
    fontLayout: fontLayout,
    fontLayouts: function () { return textOwners.map(function (p) { return fontLayout(p); }); },
    /* runtime TMP font: __monmusuCnBridge.fontStatus() / fontInstall() / fontDisable()
       fontInstall() re-arms after fontDisable(); pass true to also retry a run
       that already ended in 'failed' (the automatic path never retries). */
    fontAssets: fontAssetCensus,
    bundles: bundleCensus,
    fontStatus: fontStatus,
    fontInstall: function (fontPtr, force) {
      FONT.enabled = true;
      if (FONT.state === 'disabled') { FONT.state = 'idle'; FONT.reason = ''; }
      if (force && FONT.state === 'failed') { FONT.state = 'idle'; FONT.reason = ''; }
      return installFont(fontPtr || 0);
    },
    fontDisable: fontDisable,
    /* independent switch for "one Chinese font for the translated UI"; the
       fallback registration stays in place either way */
    fontUnify: function (on) {
      FONT.unify = (on === undefined) ? !FONT.unify : !!on;
      if (!FONT.unify) restoreUnified();
      return FONT.unify;
    },
    fontRefreshAll: function (on) {
      FONT.refreshAll = (on === undefined) ? !FONT.refreshAll : !!on;
      return FONT.refreshAll;
    },
    allocManagedString: allocManagedString,
    pathState: function () { return pathState; },
    applyRuntimeConfig: applyRuntimeConfig,
    runtimeConfig: function () {
      return {
        state: RUNTIME_STATE, why: RUNTIME_WHY,
        branch: RUNTIME ? RUNTIME.branch : null,
        gameBuild: RUNTIME ? RUNTIME.game_build : null,
        wasmSha256: RUNTIME && RUNTIME.wasm ? RUNTIME.wasm.sha256 : null,
        fontEnabled: FONT.enabled
      };
    },

    /* console diagnostics: __monmusuCnBridge.uiProbe() -> the last component's path */
    uiProbe: function (comp) {
      var c = comp || owner;
      if (!c) return '';
      var p = buildPath(c);
      uiMemo.owner = 0; uiMemo.src = null; uiMemo.path = null;
      return p;
    },
    /* resolves a string through the UI index using the live component path */
    uiLookup: function (src, comp) {
      return resolveUi(src, comp || owner) || null;
    },

    /* __monmusuCnBridge.uiTrace(true)  -> start recording, returns the buffer
       __monmusuCnBridge.uiTrace()      -> just read it
       __monmusuCnBridge.uiTrace(false) -> stop
       __monmusuCnBridge.uiTraceClear() -> empty it
       __monmusuCnBridge.uiTraceDump()  -> newline-separated JSON lines (copy-paste)
       Row = {s: source, t: index answered?, p: hierarchy path, r: hit rule,
              w: why it missed, x: the returned translation (first 160 chars)}.
       t === true means "a translation was found", NOT "it is on screen"; use
       x plus __monmusuCnBridge.uiStats (stale / skipped / errors / fills) to tell a
       matcher failure (B) from a write-back failure (C). */
    uiTrace: function (on) {
      if (on !== undefined) uiTraceOn = !!on;
      return uiTraceBuf.slice();
    },
    uiTraceClear: function () { uiTraceBuf = []; return 0; },
    uiTraceDump: function () {
      var out = [];
      for (var i = 0; i < uiTraceBuf.length; i++) {
        out.push(JSON.stringify(uiTraceBuf[i]));
      }
      return out.join('\n');
    }
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { monmusuCnCreateBridge: monmusuCnCreateBridge };
}
