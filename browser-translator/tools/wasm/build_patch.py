"""Generate a patched ntd.wasm for the WebGL build.

Two modes:

  translate (default)
      Installs  monmusu_cn_test(str) -> str  at the entry of
      Utage.TextData::.ctor(string).  The hook asks JavaScript (through a
      repurposed existing import) for a translation, then materialises it as a
      REAL managed IL2CPP string via il2cpp::vm::String::NewSize.  The original
      string object is never touched.

  overwrite
      The original ASCII PoC: rewrites matching strings in place.
      Kept as an independent rollback target.

PHASE 28 CHANGE
===============
All function indices, element slots, import indices and the allocator index are
now read from `build/wasm/resolved_targets.json`, produced by
`tools/wasm/resolve_targets.py` from IL2CPP method identity + Wasm structure.
There is NO hard-coded production index and NO fallback to historical indices:
if resolution is missing, stale or fails verification, this script aborts.

The hook bodies, protocol tags, section-rebuild code and appended-function
numbering formula are UNCHANGED from the browser-verified implementation.

Index stability: no import is added and no existing function index moves.  We
only (a) append defined functions at the end of the index space and (b) insert
instructions at the top of nine existing bodies.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import wastool
import resolve_targets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_WASM = HERE / 'inputs' / 'ntd.orig.wasm'
DEFAULT_RESOLVED = ROOT / 'build' / 'wasm' / 'resolved_targets.json'
DEFAULT_TARGETS = HERE / 'hook_targets.json'
DEFAULT_OUT = ROOT / 'build' / 'wasm' / 'ntd.patched.wasm'

# ---- Il2CppString layout (verified from String::get_Length / get_Chars) ----
STR_LEN_OFF = 8
STR_CHARS_OFF = 12
TEXT_PARAM = 1                 # the string parameter of TextData::.ctor

# Protocol constants: these are call-convention markers, NOT function indices.
TAG = 0x40000000               # translation marker OR-ed into the 2nd argument
CTX_TAG = 0x20000000           # scene-context marker
UI_TAG = 0x10000000            # UI-text marker (exact value, no pointer payload)
UI_FILL = 0x08000000           # UI fill stage marker
CMD_PARSE_NAME, CMD_PARSE_GRID, CMD_PARSE_END = 1, 2, 3
CMD_PLAYBACK_BEGIN, CMD_PLAYBACK_END = 4, 5
CMD_CELL_NAME, CMD_CELL_SHEET = 6, 7
CMD_UI_OWNER = 8               # arg1 = TMP_Text instance (needed for the UI path)

# Historical numbers, retained ONLY for the regression test (test_wasm_build.py).
# No production code path reads this table.
HISTORICAL_REFERENCE = {
    'H1': {'slot': 0xB8E8, 'func': 4603},
    'H2': {'slot': 0x15E0, 'func': 694},
    'H3': {'slot': 0x18CE6, 'func': 65961},
    'H4': {'slot': 0x1443, 'func': 107830},
    'H5': {'slot': 0xA9F3, 'func': 102370},
    'H6': {'slot': 0xB24D, 'func': 106421},
    'H7': {'slot': 0xB278, 'func': 106420},
    'H8': {'slot': 0xB252, 'func': 106414},
    'H9': {'slot': 0x138D, 'func': 8644},
    'lookup_import': 336, 'context_import': 228, 'allocator': 1702,
    'appended_base': 135660,
}


# ---------------- tiny assembler ----------------
def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sleb(v):
    out = bytearray()
    more = True
    while more:
        b = v & 0x7f
        v >>= 7
        if (v == 0 and not (b & 0x40)) or (v == -1 and (b & 0x40)):
            more = False
        else:
            b |= 0x80
        out.append(b)
    return bytes(out)


def i32(v):   return b'\x41' + sleb(v)
def lget(i):  return b'\x20' + uleb(i)
def lset(i):  return b'\x21' + uleb(i)
def gget(i):  return b'\x23' + uleb(i)
def gset(i):  return b'\x24' + uleb(i)
def call(i):  return b'\x10' + uleb(i)
def add():    return b'\x6a'
def or_():    return b'\x72'
def shl():    return b'\x74'
def eqz():    return b'\x45'
def ne():     return b'\x47'
def drop():   return b'\x1a'
def ret():    return b'\x0f'
def end():    return b'\x0b'
def block():  return b'\x02\x40'
def if_():    return b'\x04\x40'
def br_if():  return b'\x0d\x00'


def load32(off):  return b'\x28' + uleb(2) + uleb(off)
def load16u(off): return b'\x2f' + uleb(1) + uleb(off)
def store32(off): return b'\x36' + uleb(2) + uleb(off)
def store16(off): return b'\x3b' + uleb(1) + uleb(off)


def hook_translate(alloc_idx, lookup_idx, tag):
    """(param $s i32) (result i32)"""
    c = bytearray()
    c += uleb(1) + uleb(2) + b'\x7f'          # 1 decl: two i32 locals
    # if (!s) return s;
    c += lget(0) + eqz() + if_() + lget(0) + ret() + end()
    # n = lookup(s, TAG)          -> probe for a translation
    c += lget(0) + i32(tag) + call(lookup_idx) + lset(1)
    # if (!n) return s;
    c += lget(1) + eqz() + if_() + lget(0) + ret() + end()
    # out = NewSize(n)            -> real managed allocation
    c += lget(1) + call(alloc_idx) + lset(2)
    # lookup(s, TAG | out+12)     -> JS writes the UTF-16 payload in place
    c += (lget(0) + lget(2) + i32(STR_CHARS_OFF) + add() + i32(tag) + or_()
          + call(lookup_idx) + drop())
    c += lget(2) + end()
    return bytes(c)


def hook_ui(alloc_idx, lookup_idx, ctx_import, ui_tag, fill_tag, cmd_owner):
    """(param $s i32) (param $owner i32) (result i32)

    Same two-step protocol as the story hook (probe for the length, allocate a real
    managed string, then let JS write the UTF-16 payload into its chars).  The extra
    leading context call hands the owning TMP_Text to JS so it can rebuild the UI
    hierarchy path for path-scoped translations.
    """
    c = bytearray()
    c += uleb(1) + uleb(2) + b'\x7f'          # 1 decl: two i32 locals
    c += lget(0) + eqz() + if_() + lget(0) + ret() + end()
    # JS needs the component, not just the string, to resolve the hierarchy path
    c += lget(1) + i32(CTX_TAG | cmd_owner) + call(ctx_import) + drop()
    # n = lookup(s, UI_TAG)  -> probe
    c += lget(0) + i32(ui_tag) + call(lookup_idx) + lset(1)
    c += lget(1) + eqz() + if_() + lget(0) + ret() + end()
    # out = NewSize(n)
    c += lget(1) + call(alloc_idx) + lset(2)
    # lookup(out+12, UI_FILL) -> JS writes the payload into the new string.
    # The tag is sent alone: a destination pointer packed into the same word
    # would collide with the marker bits once the heap passes 128 MiB.
    c += (lget(2) + i32(STR_CHARS_OFF) + add() + i32(fill_tag)
          + call(lookup_idx) + drop())
    c += lget(2) + end()
    return bytes(c)


def helper_alloc_string(alloc_idx):
    """(param $len i32) (result i32) -> Il2CppString*

    il2cpp::vm::String::NewSize is a direct call target inside the module but it
    has no entry in the exported element table, so JavaScript cannot reach it.
    This wrapper exposes it; the caller writes the UTF-16 payload into chars+12
    immediately afterwards, exactly like the hooks do.
    """
    c = bytearray()
    c += uleb(0)
    c += lget(0) + call(alloc_idx) + end()
    return bytes(c)


def hook_overwrite(patterns, replacement):
    """(param $s i32) (result i32) - rewrites in place, returns the same pointer."""
    c = bytearray()
    c += uleb(0)
    c += lget(0) + eqz() + if_() + lget(0) + ret() + end()
    for src, dst in patterns:
        c += block()
        c += lget(0) + load32(STR_LEN_OFF) + i32(len(src)) + ne() + br_if()
        for i, ch in enumerate(src):
            c += lget(0) + load16u(STR_CHARS_OFF + 2 * i) + i32(ord(ch)) + ne() + br_if()
        for i, ch in enumerate(dst):
            c += lget(0) + i32(ord(ch)) + store16(STR_CHARS_OFF + 2 * i)
        c += lget(0) + i32(len(dst)) + store32(STR_LEN_OFF)
        c += lget(0) + ret() + end()
    c += lget(0) + end()
    return bytes(c)


def stub_allocator(global_idx):
    """Bump allocator used ONLY by local test builds (no runtime required)."""
    c = bytearray()
    c += uleb(1) + uleb(1) + b'\x7f'          # 1 decl: one i32 local
    c += gget(global_idx) + lset(1)
    c += lget(1) + lget(0) + store32(STR_LEN_OFF)
    c += (lget(1) + lget(0) + i32(1) + shl() + add() + i32(20) + add()
          + gset(global_idx))
    c += lget(1) + end()
    return bytes(c)


# ---------------- scene-context hooks ----------------
def hook_parse_begin(ctx_import):
    """(param $scenarioData i32) -> (); reads Name @+8 and DataGrid.Name @ [[+12]+12]."""
    c = bytearray()
    c += uleb(1) + uleb(1) + b'\x7f'
    c += lget(0) + load32(8) + i32(CTX_TAG | CMD_PARSE_NAME) + call(ctx_import) + drop()
    c += lget(0) + load32(12) + lset(1)
    c += lget(1) + if_()
    c += lget(1) + load32(12) + i32(CTX_TAG | CMD_PARSE_GRID) + call(ctx_import) + drop()
    c += end()
    c += end()
    return bytes(c)


def hook_noarg(cmd, ctx_import):
    """() -> ()"""
    c = bytearray()
    c += uleb(0)
    c += i32(0) + i32(CTX_TAG | cmd) + call(ctx_import) + drop()
    c += end()
    return bytes(c)


def hook_playback(ctx_import):
    """(param $label i32) -> ()"""
    c = bytearray()
    c += uleb(0)
    c += lget(0) + i32(CTX_TAG | CMD_PLAYBACK_BEGIN) + call(ctx_import) + drop()
    c += end()
    return bytes(c)


def hook_cell(ctx_import):
    """(param $command i32) -> (); reads RowData.Grid.Name / SheetName with guards."""
    c = bytearray()
    c += uleb(1) + uleb(1) + b'\x7f'
    c += lget(0) + load32(8) + eqz() + if_() + ret() + end()      # RowData null
    c += lget(0) + load32(8) + load32(8) + lset(1)                  # StringGridRow.Grid @+8
    c += lget(1) + eqz() + if_() + ret() + end()                    # Grid null
    c += lget(1) + load32(12) + i32(CTX_TAG | CMD_CELL_NAME) + call(ctx_import) + drop()
    c += lget(1) + load32(16) + i32(CTX_TAG | CMD_CELL_SHEET) + call(ctx_import) + drop()
    c += end()
    return bytes(c)


# ---------------- section rebuilding ----------------
def code_entry_bounds(w):
    st, sz = w.sec(10)
    p = st
    n, p = w.uleb(p)
    starts = []
    for _ in range(n):
        starts.append(p)
        size, q = w.uleb(p)
        p = q + size
    return st, sz, n, starts


def append_global(w, init_value):
    st, sz = w.sec(6)
    p = st
    count, q = w.uleb(p)
    return uleb(count + 1) + w.buf[q:st + sz] + b'\x7f\x01' + i32(init_value) + end()


def rebuild(w, payloads):
    out = bytearray(w.buf[:8])
    for sid, start, size in w.sections:
        payload = payloads.get(sid, w.buf[start:start + size])
        out.append(sid)
        out += uleb(len(payload))
        out += payload
    return bytes(out)


def vt(name):
    return {'i32': 0x7f, 'i64': 0x7e, 'f32': 0x7d, 'f64': 0x7c}[name]


def resolved_sig(rec):
    return ([vt(x) for x in rec['wasm_signature']['params']],
            [vt(x) for x in rec['wasm_signature']['results']])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wasm', default=str(DEFAULT_WASM))
    ap.add_argument('--resolved', default=str(DEFAULT_RESOLVED),
                    help='resolved_targets.json produced by resolve_targets.py')
    ap.add_argument('--targets', default=str(DEFAULT_TARGETS))
    ap.add_argument('--mode', choices=['translate', 'overwrite'], default='translate')
    ap.add_argument('--patterns', default=str(HERE / 'patterns.json'))
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--test-export', default='monmusu_cn_test')
    ap.add_argument('--ui-test-export', default='monmusu_cn_ui_test',
                    help='export name for the UI hook (local execution test only)')
    ap.add_argument('--string-export', default='monmusu_cn_alloc',
                    help='export name for the managed-string allocator helper')
    ap.add_argument('--no-scene-hooks', action='store_true',
                    help='omit the story scene-context hooks (rollback)')
    ap.add_argument('--no-ui-hooks', action='store_true',
                    help='omit the TMP UI text hooks (rollback)')
    ap.add_argument('--test-stub-alloc', action='store_true',
                    help='use a bump allocator instead of the real IL2CPP allocator '
                         '(local execution test only)')
    args = ap.parse_args()

    out_path = args.out
    wasm_path = Path(args.wasm)
    resolved_path = Path(args.resolved)
    targets_path = Path(args.targets)
    if not resolved_path.is_file():
        sys.exit('ERROR: resolved_targets.json missing -> run resolve_targets.py. '
                 'Refusing to fall back to historical function indices.')
    if not targets_path.is_file():
        sys.exit('ERROR: hook_targets.json missing')

    R = json.loads(resolved_path.read_text(encoding='utf-8'))
    targets_cfg = json.loads(targets_path.read_text(encoding='utf-8'))
    w = wastool.Wasm(str(wasm_path))

    # ---- re-prove that the resolution still describes THIS exact wasm ----
    try:
        resolve_targets.verify_resolution(
            w, R, targets_cfg, wasm_path, targets_path)
    except resolve_targets.ResolutionError as e:
        sys.exit(f'ERROR: resolution verification failed: {e}')
    if R.get('status') != 'PASS':
        sys.exit('ERROR: resolution status is not PASS -> refusing to patch')
    if any(t.get('required', True) and t.get('status') != 'PASS'
           for t in R['targets']):
        sys.exit('ERROR: a required Hook target is not PASS -> refusing to patch')

    T = {t['id']: t for t in R['targets']}
    LOOKUP_IMPORT = R['imports']['lookup']['import_index']
    CONTEXT_IMPORT = R['imports']['context']['import_index']
    ALLOC_FUNC = R['allocator']['func_index']

    # ---- locate & verify the primary target (numbers come from resolution) ----
    h1 = T['H1']
    f = h1['func_index']
    def_idx = h1['def_index']
    if f != w.table_slot_to_func(h1['element_slot']):
        sys.exit('ERROR: H1 element slot does not map to the resolved function')
    if w.func_index_to_type(f) != tuple(resolved_sig(h1)):
        sys.exit(f'ERROR: unexpected H1 signature {w.func_index_to_type(f)}')
    body, bsize, decls, local_end = w.func_body(f)
    orig_body = w.buf[body:body + bsize]
    # The old fixed `i32.const 0x64E5A8` marker is replaced by the resolver's
    # full normalized structural fingerprint (verify_resolution above).
    if h1['structure']['static_ref_count'] <= 0:
        sys.exit('ERROR: H1 has no static data reference -> structure changed')
    print(f'target: {h1["method"]}  slot {h1["element_slot"]:#x}  '
          f'global {f}  def {def_idx}  body {bsize}B  (resolved dynamically)')

    # ---- build hook bodies ----
    new_funcs = []          # list of (typeidx, body)
    type_i32_i32 = next(i for i, (p, r) in enumerate(w.types) if p == [0x7f] and r == [0x7f])

    if args.mode == 'translate':
        if w.func_index_to_type(ALLOC_FUNC) != ([0x7f], [0x7f]):
            sys.exit('ERROR: allocator signature changed - refusing to patch')
        if w.func_index_to_type(LOOKUP_IMPORT) != ([0x7f, 0x7f], [0x7f]):
            sys.exit('ERROR: lookup import signature changed - refusing to patch')
        if args.test_stub_alloc:
            stub_idx = w.num_imported_funcs + len(w.func_types) + len(new_funcs)
            new_funcs.append((type_i32_i32, stub_allocator(1)))
            alloc_idx = stub_idx
            print(f'allocator: STUB bump allocator (global 1) at {alloc_idx}')
        else:
            alloc_idx = ALLOC_FUNC
            print(f'allocator: il2cpp::vm::String::NewSize at global {alloc_idx} '
                  f'(managed, GC-tracked)')
        hook_body = hook_translate(alloc_idx, LOOKUP_IMPORT, TAG)
        print(f'lookup: import {LOOKUP_IMPORT} ({w.imports[LOOKUP_IMPORT]["name"]}), '
              f'tag {TAG:#x}')
    else:
        cfg = json.load(open(args.patterns, encoding='utf-8'))
        replacement = cfg['replacement']
        patterns = []
        for t in cfg['targets']:
            src, dst = t['text'], t.get('replace_with', replacement)
            if len(dst) > len(src):
                sys.exit(f'ERROR: replacement longer than source for {t["id"]}')
            patterns.append((src, dst))
        hook_body = hook_overwrite(patterns, replacement)
        print(f'overwrite mode: {len(patterns)} patterns -> {replacement!r}')

    # ---- scene-context helpers (kept separate from the translation import) ----
    scene = {}
    if args.mode == 'translate' and not args.no_scene_hooks:
        if w.func_index_to_type(CONTEXT_IMPORT) != ([0x7f, 0x7f], [0x7f]):
            sys.exit('ERROR: context import signature changed - refusing to patch')
        type_i32_void = next(i for i, (p, r) in enumerate(w.types)
                             if p == [0x7f] and r == [])
        type_void_void = next(i for i, (p, r) in enumerate(w.types)
                              if p == [] and r == [])
        base = w.num_imported_funcs + len(w.func_types)
        specs = [
            ('parse_begin', type_i32_void, hook_parse_begin(CONTEXT_IMPORT)),
            ('parse_end', type_void_void, hook_noarg(CMD_PARSE_END, CONTEXT_IMPORT)),
            ('playback', type_i32_void, hook_playback(CONTEXT_IMPORT)),
            ('playback_end', type_void_void, hook_noarg(CMD_PLAYBACK_END, CONTEXT_IMPORT)),
            ('cell', type_i32_void, hook_cell(CONTEXT_IMPORT)),
        ]
        for name, tidx, body in specs:
            scene[name] = base + len(new_funcs)
            new_funcs.append((tidx, body))
        print(f'scene helpers: {[(k, v) for k, v in scene.items()]} '
              f'via import {CONTEXT_IMPORT} ({w.imports[CONTEXT_IMPORT]["name"]})')

    # ---- UI text hooks (TMP_Text entry points) ----
    ui = {}
    if args.mode == 'translate' and not args.no_ui_hooks:
        type_i32_i32_i32 = next(i for i, (p, r) in enumerate(w.types)
                                if p == [0x7f, 0x7f] and r == [0x7f])
        if args.test_stub_alloc:
            alloc_for_ui = alloc_idx
        else:
            alloc_for_ui = ALLOC_FUNC
        ui['hook'] = w.num_imported_funcs + len(w.func_types) + len(new_funcs)
        hook_body_ui = hook_ui(alloc_for_ui, LOOKUP_IMPORT, CONTEXT_IMPORT,
                               UI_TAG, UI_FILL, CMD_UI_OWNER)
        new_funcs.append((type_i32_i32_i32, hook_body_ui))
        # the fill stage must carry UI_FILL alone: a destination pointer packed
        # into the same word collides with the marker bits once the heap passes
        # 128 MiB, which this game reaches easily
        if i32(UI_TAG | UI_FILL) in hook_body_ui:
            sys.exit('ERROR: ui fill stage still packs the marker bits')
        for what, val in (('probe', UI_TAG), ('fill', UI_FILL),
                          ('owner', CTX_TAG | CMD_UI_OWNER)):
            if i32(val) not in hook_body_ui:
                sys.exit(f'ERROR: ui hook is missing the {what} tag {val:#x}')
        print(f'ui hook: global {ui["hook"]} via import {LOOKUP_IMPORT} '
              f'(probe tag {UI_TAG:#x}, fill tag {UI_FILL:#x})')

    # ---- managed-string helper for the runtime TMP font installer ----
    # No new import, no index shift: appended like every other helper.
    font = {}
    if args.mode == 'translate' and not args.no_ui_hooks:
        alloc_for_str = alloc_idx if args.test_stub_alloc else ALLOC_FUNC
        font['alloc'] = w.num_imported_funcs + len(w.func_types) + len(new_funcs)
        new_funcs.append((type_i32_i32, helper_alloc_string(alloc_for_str)))
        print(f'string helper: {args.string_export} at global {font["alloc"]} '
              f'(NewSize global {alloc_for_str})')

    hook_idx = w.num_imported_funcs + len(w.func_types) + len(new_funcs)
    new_funcs.append((type_i32_i32, hook_body))
    # The numbering formula is unchanged. Cross-check it against the resolver
    # only when the full (non-rollback) configuration is being built.
    full_build = (args.mode == 'translate' and not args.no_scene_hooks
                  and not args.no_ui_hooks and not args.test_stub_alloc)
    if full_build:
        expected = {item['id']: item['func_index']
                    for item in R['appended']['functions']}
        if hook_idx != expected['translate_hook']:
            sys.exit(f'ERROR: computed hook index {hook_idx} != resolved '
                     f"{expected['translate_hook']}")
        if len(new_funcs) != R['appended']['count']:
            sys.exit(f'ERROR: computed {len(new_funcs)} appended functions, '
                     f"resolution declares {R['appended']['count']}")
    print(f'hook function global index = {hook_idx}  ({len(hook_body)} bytes, '
          f'{len(new_funcs)} appended function(s))')

    # ---- function section ----
    fst, fsz = w.sec(3)
    p = fst
    fcount, q = w.uleb(p)
    func_payload = bytearray(uleb(fcount + len(new_funcs)) + w.buf[q:fst + fsz])
    for tidx, _ in new_funcs:
        func_payload += uleb(tidx)

    # ---- code section ----
    cst, csz, ccount, starts = code_entry_bounds(w)
    prefix = lget(TEXT_PARAM) + call(hook_idx) + lset(TEXT_PARAM)

    # Every Hook site now comes from resolved_targets.json. The slot is used
    # ONLY as a cross-check; the decision key is the resolved function body.
    SCENE_LOGIC = {'parse_begin', 'playback', 'playback_end', 'cell'}
    UI_LOGIC = {'ui'}

    def site_records(logics):
        out = []
        for rec in R['targets']:
            if rec['patch_logic'] not in logics:
                continue
            gi = rec['func_index']
            if gi != w.table_slot_to_func(rec['element_slot']):
                sys.exit(f'ERROR: {rec["id"]} slot/function mismatch at build time')
            if w.func_index_to_type(gi) != tuple(resolved_sig(rec)):
                sys.exit(f'ERROR: {rec["id"]} signature changed at build time: '
                         f'{w.func_index_to_type(gi)}')
            out.append(rec)
        return out

    site_prefixes = {}
    if scene:
        for rec in site_records(SCENE_LOGIC):
            sd = rec['def_index']
            if sd in site_prefixes or sd == def_idx:
                sys.exit(f'ERROR: duplicate body index {sd} for {rec["id"]}')
            site_prefixes[sd] = (rec['patch_logic'], rec['method'])
        print(f'scene signatures verified on {len(site_prefixes)} functions')

    ui_sites = {}
    if ui:
        for rec in site_records(UI_LOGIC):
            sd = rec['def_index']
            if sd == def_idx or sd in site_prefixes:
                sys.exit(f'ERROR: UI site {rec["id"]} collides with another hook site')
            ui_sites[sd] = (rec['patch_logic'], rec['method'])
        if len(ui_sites) != 2:
            sys.exit(f'ERROR: expected 2 distinct UI bodies, got {len(ui_sites)}')
        print(f'ui signatures verified on {len(ui_sites)} distinct functions '
              f'({", ".join(lbl for _, lbl in ui_sites.values())})')

    def site_code(sd, logic):
        # Dispatch on the DECLARED patch logic, never on a hard-coded slot.
        if logic == 'parse_begin':
            return lget(0) + call(scene['parse_begin']), call(scene['parse_end'])
        if logic == 'playback':
            return lget(1) + call(scene['playback']), None
        if logic == 'playback_end':
            return call(scene['playback_end']), None
        if logic == 'cell':
            return lget(0) + call(scene['cell']), None
        return b'', None

    code_payload = bytearray(uleb(ccount + len(new_funcs)))
    for i, s0 in enumerate(starts):
        size, q2 = w.uleb(s0)
        localdecl = w.buf[q2:local_end] if i == def_idx else None
        # recompute local-declaration end for every modified function
        if i in site_prefixes or i in ui_sites or i == def_idx:
            nl, q3 = w.uleb(q2)
            for _ in range(nl):
                _cnt, q3 = w.uleb(q3)
                q3 += 1                                      # valtype byte
            ld_end = q3
            localdecl = w.buf[q2:ld_end]
            instr = w.buf[ld_end:q2 + size]
        else:
            instr = w.buf[q2:q2 + size]

        if i == def_idx:
            newbody = localdecl + prefix + instr
        elif i in ui_sites:
            logic, label = ui_sites[i]
            # translate the incoming string, keeping the component for the path
            pre = lget(1) + lget(0) + call(ui['hook']) + lset(1)
            newbody = localdecl + pre + instr
            head = newbody[len(localdecl):len(localdecl) + len(pre)]
            if head != bytes(pre):
                sys.exit(f'ERROR: ui prologue not applied to {label}')
            if newbody[len(localdecl) + len(pre):] != instr:
                sys.exit(f'ERROR: ui prologue damaged the body of {label}')
            print(f'  ui hook in {label}: {size} -> {len(newbody)} bytes')
        elif i in site_prefixes:
            logic, label = site_prefixes[i]
            pre, suf = site_code(i, logic)
            if suf:
                # insert immediately before the function's final `end`
                fglob = i + w.num_imported_funcs
                ins = wastool.decode(w, fglob)
                last = ins[-1]
                if last[1] != 'end' or last[0] + last[3] != ld_end + len(instr):
                    sys.exit(f'ERROR: {label} has no single trailing end; refusing to patch')
                cut = last[0] - ld_end
                newbody = localdecl + pre + instr[:cut] + suf + instr[cut:]
            else:
                newbody = localdecl + pre + instr
            print(f'  scene hook in {label}: {size} -> {len(newbody)} bytes')
        else:
            nxt = starts[i + 1] if i + 1 < len(starts) else cst + csz
            code_payload += w.buf[s0:nxt]
            continue
        code_payload += uleb(len(newbody)) + newbody
        print(f'patched body of def {i}: {size} -> {len(newbody)} bytes '
              f'(+{len(newbody) - size})')
    for _, b in new_funcs:
        code_payload += uleb(len(b)) + b

    payloads = {3: bytes(func_payload), 10: bytes(code_payload)}

    if args.mode == 'translate' and args.test_stub_alloc:
        payloads[6] = append_global(w, 0x01400000)

    # ---- optional exports so tests can call the hooks directly ----
    extra = []
    if args.test_export:
        extra.append((args.test_export, hook_idx))
    if ui.get('hook') and args.ui_test_export:
        extra.append((args.ui_test_export, ui['hook']))
    if font.get('alloc'):
        extra.append((args.string_export, font['alloc']))
    if full_build and dict(extra) != R['appended']['exports']:
        sys.exit(f'ERROR: exports {dict(extra)} != resolved '
                 f"{R['appended']['exports']}")
    if extra:
        est, esz = w.sec(7)
        p = est
        ecount, q = w.uleb(p)
        payload = uleb(ecount + len(extra)) + w.buf[q:est + esz]
        for name, idx in extra:
            nb = name.encode()
            payload += uleb(len(nb)) + nb + b'\x00' + uleb(idx)
        payloads[7] = payload

    out = rebuild(w, payloads)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    open(out_path, 'wb').write(out)
    print(f'\nwrote {out_path}')
    print(f'  size   {len(out)} (original {len(w.buf)}, delta {len(out) - len(w.buf)})')
    print(f'  sha256 {hashlib.sha256(out).hexdigest()}')

    if out_path.endswith('.wasm'):
        gz = out_path + '.gz'
        with gzip.GzipFile(gz, 'wb', compresslevel=9, mtime=0) as fh:
            fh.write(out)
        print(f'  gz     {gz} ({os.path.getsize(gz)} bytes)')


if __name__ == '__main__':
    main()
