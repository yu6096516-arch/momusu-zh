#!/usr/bin/env python3
"""Synthetic function/slot reorder test for resolve_targets.py.

Builds a small, VALID standalone Wasm module that contains:
  * two repurposable imports (a.nf / a.md), each with exactly one call site;
  * a thin FastAllocateString wrapper calling a managed string allocator;
  * one Hook target method with control flow, a memory access and a call.

Module B is produced from A by inserting ONE unrelated function at the front
of the function index space AND at the front of the element table. Every
reference is repaired: element entries, exports, all `call` operands to
defined functions, IL2CPP Addresses and the slot->name map. `WebAssembly.validate`
must still accept B, which proves the transformation preserves semantics.

Then resolve_targets.py is run on B using the structural baselines signed off
for A, and the historical numbers are deliberately corrupted. The resolver
must still find the correct NEW slot/function, with an unchanged normalized
fingerprint. This proves the locator does not rely on historical ids.

This test only proves the locator under THIS specific reordering condition.
It says nothing about compatibility with arbitrary future game builds.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import os
import sys
from pathlib import Path

import wastool
import resolve_targets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = Path(os.environ.get('MONMUSU_BUILD_ROOT') or (ROOT / 'build' / 'wasm')) / 'synth'


# --------------------------------------------------------------------------
# tiny Wasm writer
# --------------------------------------------------------------------------
def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def sleb(v):
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        if (v == 0 and not (b & 0x40)) or (v == -1 and (b & 0x40)):
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


def vec(items):
    return uleb(len(items)) + b''.join(items)


def name(s):
    b = s.encode()
    return uleb(len(b)) + b


def section(sid, payload):
    return bytes([sid]) + uleb(len(payload)) + payload


def i32c(v):
    return b'\x41' + sleb(v)


def lget(i):
    return b'\x20' + uleb(i)


def call(i):
    return b'\x10' + uleb(i)


def load32(off=0):
    return b'\x28' + uleb(2) + uleb(off)


def load8u(off=0):
    return b'\x2d' + uleb(0) + uleb(off)


def store32(off):
    return b'\x36' + uleb(2) + uleb(off)


def store16(off):
    return b'\x3b' + uleb(1) + uleb(off)


def store8(off):
    return b'\x3a' + uleb(0) + uleb(off)


END, DROP, NOP, EQZ = b'\x0b', b'\x1a', b'\x01', b'\x45'
IF_VOID = b'\x04\x40'


def body(instr, locals_=()):
    decl = uleb(len(locals_)) + b''.join(uleb(c) + bytes([t]) for c, t in locals_)
    return decl + instr + END


# functype helpers
T_III = b'\x60\x02\x7f\x7f\x01\x7f'          # (i32,i32)->i32
T_VIII = b'\x60\x03\x7f\x7f\x7f\x00'         # (i32,i32,i32)->()
T_II = b'\x60\x01\x7f\x01\x7f'               # (i32)->i32
T_VOID = b'\x60\x00\x00'                     # ()->()
TYPES = [T_III, T_VIII, T_II, T_VOID]
T_I_IMPORT, T_I_TARGET, T_I_ALLOC, T_I_VOID = 0, 1, 2, 3
T_I_III_IMPORT = T_I_IMPORT   # the thin wrapper reuses the (i32,i32)->i32 type


def allocator_body():
    # (i32)->i32 ; writes length@8, NUL@12, reads a static, calls import 0.
    return body(
        lget(0) + i32c(0) + store32(8) +
        lget(0) + i32c(0) + store16(12) +
        i32c(0x120000) + load32(0) + DROP +
        i32c(1) + i32c(1) + call(0) + DROP +
        lget(0))


def fastalloc_body(alloc_global):
    return body(lget(0) + call(alloc_global))


def target_body(alloc_global):
    return body(
        lget(0) + load8u(0) + EQZ + IF_VOID +
        lget(1) + call(alloc_global) + DROP + END +
        lget(0) + i32c(1) + store8(16))


def caller_md_body():
    return body(i32c(2) + i32c(2) + call(1) + DROP)


def trivial_body():
    return body(NOP)


def build_module(reordered: bool):
    """Return (wasm_bytes, metadata, slotmap, facts)."""
    imported = 2
    if reordered:
        # inserted(2), allocator(3), fastalloc(4), target(5), caller_md(6), unrelated(7)
        g_insert, g_alloc, g_fast, g_target, g_md, g_unrel = 2, 3, 4, 5, 6, 7
        defined = [T_I_VOID, T_I_ALLOC, T_I_III_IMPORT, T_I_TARGET, T_I_VOID, T_I_VOID]
        bodies = [trivial_body(), allocator_body(), fastalloc_body(g_alloc),
                  target_body(g_alloc), caller_md_body(), trivial_body()]
        elem_funcs = [g_insert, g_fast, g_target, g_md, g_unrel]   # slots 1..5
        slotmap = {'1': 'Demo.Inserted', '2': 'Demo.FastAlloc',
                   '3': 'Demo.Target', '4': 'Demo.CallerMd', '5': 'Demo.Unrelated'}
        addr_fast, addr_target = 2, 3
    else:
        # allocator(2), fastalloc(3), target(4), caller_md(5), unrelated(6)
        g_alloc, g_fast, g_target, g_md, g_unrel = 2, 3, 4, 5, 6
        g_insert = None
        defined = [T_I_ALLOC, T_I_III_IMPORT, T_I_TARGET, T_I_VOID, T_I_VOID]
        bodies = [allocator_body(), fastalloc_body(g_alloc),
                  target_body(g_alloc), caller_md_body(), trivial_body()]
        elem_funcs = [g_fast, g_target, g_md, g_unrel]             # slots 1..4
        slotmap = {'1': 'Demo.FastAlloc', '2': 'Demo.Target',
                   '3': 'Demo.CallerMd', '4': 'Demo.Unrelated'}
        addr_fast, addr_target = 1, 2

    imports = [
        name('a') + name('nf') + b'\x00' + uleb(T_I_IMPORT),
        name('a') + name('md') + b'\x00' + uleb(T_I_IMPORT),
    ]
    secs = [
        section(1, vec(TYPES)),
        section(2, vec(imports)),
        section(3, vec([uleb(t) for t in defined])),
        section(4, vec([b'\x70\x00' + uleb(16)])),          # funcref, min 16
        section(5, vec([b'\x00' + uleb(1)])),               # memory min 1
        section(7, vec([
            name('mem') + b'\x02' + uleb(0),
            name('tbl') + b'\x01' + uleb(0),
            name('target') + b'\x00' + uleb(g_target),
        ])),
        section(9, vec([uleb(0) + i32c(1) + END + vec([uleb(f) for f in elem_funcs])])),
        section(10, vec([uleb(len(b)) + b for b in bodies])),
    ]
    wasm = b'\x00asm\x01\x00\x00\x00' + b''.join(secs)
    metadata = {'ScriptMethod': [
        {'Address': addr_target, 'Name': 'Demo.Target$$Method',
         'TypeSignature': 'viii', 'Signature': 'void Demo_Target__Method (...);'},
        {'Address': addr_fast, 'Name': 'System.String$$FastAllocateString',
         'TypeSignature': 'iii', 'Signature': 'int32_t Demo_FastAllocateString (...);'},
    ]}
    facts = {
        'reordered': reordered, 'imported': imported, 'defined': len(defined),
        'base': imported + len(defined), 'target_func': g_target,
        'target_slot': addr_target, 'allocator_func': g_alloc,
        'fastalloc_func': g_fast, 'caller_md_func': g_md,
        'elem_funcs': elem_funcs,
    }
    return wasm, metadata, slotmap, facts


def targets_template():
    return {
        'schema': 1, 'game_build': 'SYNTHETIC',
        'targets': [{
            'id': 'H1', 'method': 'Demo.Target$$Method', 'type_signature': 'viii',
            'patch_logic': 'translate', 'patch_position': 'prologue',
            'expected_params': ['i32', 'i32', 'i32'], 'expected_results': [],
            'required_param_indices': [0, 1], 'constraints': {},
            'required': True, 'structure': {},
            'historical': {'slot': 'CORRUPTED', 'func_index': -1},
        }],
        'imports': [
            {'id': 'I1', 'role': 'translation channel', 'module': 'a', 'field': 'nf',
             'type_signature': 'iii', 'expected_call_sites': 1,
             'expected_call_site': {}, 'historical': {'import_index': -1},
             'required': True},
            {'id': 'I2', 'role': 'context channel', 'module': 'a', 'field': 'md',
             'type_signature': 'iii', 'expected_call_sites': 1,
             'expected_call_site': {}, 'historical': {'import_index': -1},
             'required': True},
        ],
        'allocator': {
            'id': 'F1', 'role': 'synthetic allocator',
            'wrapper_method': 'System.String$$FastAllocateString',
            'wrapper_type_signature': 'iii', 'type_signature': 'ii',
            'not_in_element_table': True, 'required': True,
            'wrapper_structure': {}, 'structure': {},
            'historical': {'func_index': -1},
        },
        'appended_functions': [
            {'id': 'ui_hook', 'type_signature': 'iii', 'export': 'synth_ui'},
            {'id': 'string_alloc_helper', 'type_signature': 'ii', 'export': 'synth_alloc'},
            {'id': 'translate_hook', 'type_signature': 'ii', 'export': 'synth_test'},
        ],
        'exports': {'synth_ui': 'ui_hook', 'synth_alloc': 'string_alloc_helper',
                    'synth_test': 'translate_hook'},
        'remaining_dependencies': [],
    }


def write_case(tag, wasm, metadata, slotmap, targets):
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    (d / 'module.wasm').write_bytes(wasm)
    (d / 'script.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (d / 'slot2name.json').write_text(json.dumps(slotmap, indent=2), encoding='utf-8')
    (d / 'hook_targets.json').write_text(json.dumps(targets, indent=2), encoding='utf-8')
    return d


def cli(d, extra=None):
    argv = ['--wasm', str(d / 'module.wasm'),
            '--script-json', str(d / 'script.json'),
            '--slot-map', str(d / 'slot2name.json'),
            '--targets', str(d / 'hook_targets.json'),
            '--out-dir', str(d / 'out'),
            '--game-build', 'SYNTHETIC']
    return resolve_targets.main(argv + (extra or []))


def validate(path):
    node = shutil.which('node')
    if not node:
        return None
    p = subprocess.run([node, '-e',
                        "const fs=require('fs');process.exit(WebAssembly.validate("
                        "fs.readFileSync(process.argv[1]))?0:1)", str(path)],
                       capture_output=True, text=True)
    return p.returncode == 0, p.stderr.strip() or p.stdout.strip()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks = []

    def ck(cond, label, detail=''):
        checks.append((label, bool(cond), detail))
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f' :: {detail}' if detail else ''))
        if not cond:
            raise AssertionError(f'{label}: {detail}')

    wa, ma, sa, fa = build_module(False)
    wb, mb, sb, fb = build_module(True)
    da = write_case('A', wa, ma, sa, targets_template())
    print('=== module validity ===')
    va, va_msg = validate(da / 'module.wasm')
    ck(va is not False, 'module A WebAssembly.validate', str(va_msg))
    db_tmp = OUT / 'B'
    db_tmp.mkdir(parents=True, exist_ok=True)
    (db_tmp / 'module.wasm').write_bytes(wb)
    vb, vb_msg = validate(db_tmp / 'module.wasm')
    ck(vb is not False, 'module B WebAssembly.validate (reorder kept valid)', str(vb_msg))
    ck(wa != wb, 'module B is genuinely a different binary')

    print('=== baseline A (explicit sign-off equivalent) ===')
    ck(cli(da, ['--emit-baseline']) == 0, 'emit baselines from module A')
    signed = json.loads((da / 'hook_targets.json').read_text(encoding='utf-8'))
    ck(bool(signed['targets'][0]['structure']), 'A target fingerprint recorded')
    ck(cli(da) == 0, 'resolve module A')
    ra = json.loads((da / 'out' / 'resolved_targets.json').read_text(encoding='utf-8'))

    # Module B reuses A's signed baselines with CORRUPTED historical ids.
    db = write_case('B', wb, mb, sb, signed)
    print('=== resolve reordered module B ===')
    ck(cli(db) == 0, 'resolve module B with A baselines (must PASS)')
    rb = json.loads((db / 'out' / 'resolved_targets.json').read_text(encoding='utf-8'))

    ta, tb = ra['targets'][0], rb['targets'][0]
    print('=== assertions ===')
    ck(ta['func_index'] == fa['target_func'] and tb['func_index'] == fb['target_func'],
       'target function index follows the reorder',
       f"A={ta['func_index']} B={tb['func_index']}")
    ck(ta['element_slot'] == fa['target_slot'] and tb['element_slot'] == fb['target_slot'],
       'target element slot follows the reorder',
       f"A={ta['element_slot']} B={tb['element_slot']}")
    ck(tb['element_slot'] != ta['element_slot'] and tb['func_index'] != ta['func_index'],
       'resolution actually changed (not a stale copy)')
    ck(ta['structure']['fingerprint_sha256'] == tb['structure']['fingerprint_sha256'],
       'normalized fingerprint is stable under function reorder',
       ta['structure']['fingerprint_sha256'][:12])
    ck(ta['metadata_hits'] == tb['metadata_hits'] == 1,
       'exactly one metadata match in both modules')
    ck(rb['targets'][0]['historical']['func_index'] == -1
       and rb['targets'][0]['historical']['slot'] == 'CORRUPTED',
       'historical ids were deliberately corrupted -> not consulted')
    ck(ra['allocator']['func_index'] == fa['allocator_func']
       and rb['allocator']['func_index'] == fb['allocator_func'],
       'allocator re-resolved via the thin wrapper',
       f"A={ra['allocator']['func_index']} B={rb['allocator']['func_index']}")
    ck(ra['allocator']['structure']['fingerprint_sha256']
       == rb['allocator']['structure']['fingerprint_sha256'],
       'allocator fingerprint stable under reorder')
    ck(ra['imports']['lookup']['call_site']['func_index'] == fa['allocator_func']
       and rb['imports']['lookup']['call_site']['func_index'] == fb['allocator_func'],
       'I1 call-site function re-resolved')
    ck(ra['imports']['context']['call_site']['func_index'] == fa['caller_md_func']
       and rb['imports']['context']['call_site']['func_index'] == fb['caller_md_func'],
       'I2 call-site function re-resolved')
    ck(ra['appended']['base'] == fa['base'] and rb['appended']['base'] == fb['base'],
       'appended function base recomputed from the new module',
       f"A={ra['appended']['base']} B={rb['appended']['base']}")
    ck(rb['appended']['base'] == ra['appended']['base'] + 1,
       'one inserted function shifts the appended base by exactly 1')
    ck(rb['status'] == 'PASS' and all(t['status'] == 'PASS' for t in rb['targets']),
       'all required targets PASS on the reordered module')

    # Wasm parser sanity: all call targets valid in B.
    w = wastool.Wasm(str(db / 'module.wasm'))
    total = w.num_imported_funcs + len(w.func_types)
    bad = []
    for di, (bs, size, _d, _le) in enumerate(w.code):
        for _off, mn, imm, _ln in wastool.decode(w, di + w.num_imported_funcs):
            if mn == 'call' and int(imm) >= total:
                bad.append((di, int(imm)))
    ck(not bad, 'every call operand in module B is in range', str(bad[:3]))

    report = {
        'status': 'PASS', 'module_A': fa, 'module_B': fb,
        'resolved_A': {'target_func': ta['func_index'], 'slot': ta['element_slot'],
                       'allocator': ra['allocator']['func_index'],
                       'base': ra['appended']['base'],
                       'fingerprint': ta['structure']['fingerprint_sha256']},
        'resolved_B': {'target_func': tb['func_index'], 'slot': tb['element_slot'],
                       'allocator': rb['allocator']['func_index'],
                       'base': rb['appended']['base'],
                       'fingerprint': tb['structure']['fingerprint_sha256']},
        'checks': [{'name': n, 'pass': p, 'detail': d} for n, p, d in checks],
        'scope_note': 'Only this specific reordering condition is proven. '
                      'It does NOT imply compatibility with any future real game build.',
    }
    (OUT / 'REORDER_TEST_REPORT.json').write_text(
        json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('\nREORDER TEST PASS ->', OUT / 'REORDER_TEST_REPORT.json')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('REORDER TEST FAIL:', type(e).__name__, e, file=sys.stderr)
        sys.exit(1)
