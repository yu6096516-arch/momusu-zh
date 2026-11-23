#!/usr/bin/env python3
"""Positive + negative tests for dynamic Hook resolution (phase 28).

Positive assertions read `build/wasm/resolved_targets.json` and re-check every
required invariant. Negative cases mutate an IN-MEMORY copy of a declaration and
assert that `resolve_targets` raises ResolutionError -- no production file is
ever modified. A separate subprocess test proves build_patch.py refuses a stale
or tampered resolution instead of falling back to historical numbers.

Run after resolve_targets.py. Does not run the unrelated font / UI / launcher
test suites.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import wastool
import resolve_targets as rt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = ROOT / 'build' / 'wasm'
WASM = HERE / 'inputs' / 'ntd.orig.wasm'
SCRIPT = HERE / 'inputs' / 'script.json'
SLOTMAP = HERE / 'inputs' / 'slot2name.json'
TARGETS = HERE / 'hook_targets.json'
RESOLVED = BUILD / 'resolved_targets.json'


class TestFailure(Exception):
    pass


def main():
    results = []

    def ck(cond, name, detail=''):
        results.append({'name': name, 'pass': bool(cond), 'detail': str(detail)})
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f' :: {detail}' if detail else ''))
        if not cond:
            raise TestFailure(f'{name}: {detail}')

    ck(RESOLVED.is_file(), 'resolved_targets.json exists', str(RESOLVED))
    resolved = json.loads(RESOLVED.read_text(encoding='utf-8'))

    # Follow the resolution instead of assuming branch 0's inputs: the same
    # suite has to validate a /1/ (FANZA) build, whose inputs live elsewhere.
    global WASM, SCRIPT, SLOTMAP
    WASM = Path(resolved['inputs']['wasm']['path'])
    SCRIPT = Path(resolved['inputs']['script_json']['path'])
    SLOTMAP = Path(resolved['inputs']['slot2name']['path'])
    ck(WASM.is_file(), 'resolved official Wasm is the file on disk', str(WASM))

    cfg = json.loads(TARGETS.read_text(encoding='utf-8'))
    ck(resolved.get('status') == 'PASS', 'resolution status PASS')

    # ---------------- positive assertions ----------------
    print('=== positive: resolution invariants ===')
    targets = resolved['targets']
    ck(len(targets) == 9, 'exactly 9 Hook targets', len(targets))
    ck(all(t['metadata_hits'] == 1 for t in targets),
       'all 9 targets matched metadata exactly once')
    ck(len({t['func_index'] for t in targets}) == 9,
       'all 9 targets map to distinct Wasm functions')
    ck(len({t['element_slot'] for t in targets}) == 9,
       'all 9 targets map to distinct element slots')
    ck(all(t['status'] == 'PASS' and t['checks']['wasm_type_matches']
           and t['checks']['structure_matches_baseline']
           and t['checks']['patch_position_safe'] for t in targets),
       'type / structure / patch-position checks all PASS')
    h4 = next(t for t in targets if t['id'] == 'H4')
    ck(h4['structure']['top_level_ends'] == 1 and h4['structure']['trailing_end_ok'],
       'AdvScenarioData::Init has a single trailing end for tail insertion')
    by_id = {t['id']: t for t in targets}
    ck(by_id['H5']['structure']['fingerprint_sha256']
       != by_id['H7']['structure']['fingerprint_sha256'],
       'AdvEngine / AdvScenarioThread StartScenario have DISTINCT fingerprints '
       '(their opcode prefixes are identical)')
    for t in targets:
        decl = next(d for d in cfg['targets'] if d['id'] == t['id'])
        for p in decl['required_param_indices']:
            ck(p < len(t['wasm_signature']['params']),
               f"{t['id']} parameter index {p} is valid")

    imports = resolved['imports']
    ck(imports['lookup']['module'] == 'a' and imports['lookup']['field'] == 'nf',
       'translation import identity a.nf')
    ck(imports['context']['module'] == 'a' and imports['context']['field'] == 'md',
       'context import identity a.md')
    ck(imports['lookup']['import_index'] != imports['context']['import_index'],
       'two imports are distinct')
    ck(all(imports[k]['original_call_sites'] == 1
           and imports[k]['protocol_safety']['original_arg2_has_tag_bits'] is False
           for k in ('lookup', 'context')),
       'each import has one original call site and no protocol tag collision')
    ck(all(imports[k]['wasm_signature'] == '(i32,i32)->(i32)'
           for k in ('lookup', 'context')),
       'both imports keep the (i32,i32)->i32 type')

    alloc = resolved['allocator']
    ck(alloc['wrapper']['metadata_hits'] == 1,
       'FastAllocateString wrapper uniquely identified')
    ck([x[0] for x in alloc['wrapper']['disasm']] == ['local.get', 'call', 'end'],
       'wrapper is the 3-instruction thin call wrapper')
    ck(alloc['wasm_signature'] == '(i32)->(i32)', 'allocator type (i32)->i32')
    ck(alloc['layout']['string_length_off'] == 8
       and alloc['layout']['string_chars_off'] == 12,
       'Il2CppString layout operations present')
    ck(alloc['direct_callers'] >= 1 and alloc['in_element_table'] is False,
       'allocator has callers and is outside the element table')

    ap = resolved['appended']
    ck(ap['base'] == ap['imported_function_count'] + ap['defined_function_count'],
       'appended base uses the computed formula, not a literal', ap['base'])
    ck(ap['count'] == 8, '8 appended functions')
    ck(len(ap['exports']) == 3, '3 exports declared')
    ck(set(ap['exports']) == {'monmusu_cn_test', 'monmusu_cn_ui_test',
                              'monmusu_cn_alloc'}, 'export names are first-party names')
    ck(len(set(ap['exports'].values())) == 3, 'exports point to 3 distinct functions')

    # Resolution is bound to the exact input files.
    ck(resolved['inputs']['wasm']['sha256'] == rt.sha256_file(WASM),
       'resolution bound to the official Wasm SHA-256')
    ck(resolved['inputs']['hook_targets']['sha256'] == rt.sha256_file(TARGETS),
       'resolution bound to hook_targets.json SHA-256')

    # ---------------- negative cases ----------------
    print('=== negative: strict rejection (in-memory mutations) ===')
    w = wastool.Wasm(str(WASM))
    slot2name = json.loads(SLOTMAP.read_text(encoding='utf-8'))
    ctx = rt.ModuleContext(w, slot2name)
    methods = json.loads(SCRIPT.read_text(encoding='utf-8'))['ScriptMethod']
    decls = {d['id']: d for d in cfg['targets']}
    imp_decls = {d['id']: d for d in cfg['imports']}
    alloc_decl = cfg['allocator']

    def expect_fail(fn, label):
        try:
            fn()
        except rt.ResolutionError as e:
            ck(True, label, str(e)[:110])
        except Exception as e:                                  # noqa: BLE001
            ck(False, label, f'wrong exception {type(e).__name__}: {e}')
        else:
            ck(False, label, 'resolution unexpectedly SUCCEEDED (unsafe)')

    d = copy.deepcopy(decls['H1'])
    d['method'] = 'Utage.TextData$$DoesNotExist'
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'zero metadata matches -> reject')

    # inject an exact duplicate of the H1 identity with a different Address
    h1m = next(m for m in methods if m['Name'] == decls['H1']['method']
               and m['TypeSignature'] == decls['H1']['type_signature'])
    dup = dict(h1m)
    dup['Address'] = h1m['Address'] + 1
    expect_fail(lambda: rt.resolve_target(ctx, methods + [dup], decls['H1']),
                'two metadata matches -> reject (no best-guess)')

    # A different but same-shaped method must NOT be accepted by fuzzy matching:
    # point a declaration at H2's identity while keeping H1's structural baseline.
    d = copy.deepcopy(decls['H1'])
    for key in ('method', 'type_signature', 'csharp_signature',
                'required_param_indices', 'constraints',
                'expected_params', 'expected_results'):
        d[key] = copy.deepcopy(decls['H2'][key])
    # Everything about the declaration now correctly describes H2 EXCEPT the
    # structural baseline, which is still H1's. A resolver that only checked
    # name + signature would accept this; a safe one must reject it.
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'correct identity but wrong structure -> reject, no fuzzy match')

    d = copy.deepcopy(decls['H1'])
    d['type_signature'] = 'vii'
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'TypeSignature mismatch -> reject')

    d = copy.deepcopy(decls['H1'])
    d['csharp_signature'] = d['csharp_signature'].replace('text', 'renamed')
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'C# calling convention changed -> reject')

    d = copy.deepcopy(decls['H1'])
    d['structure']['fingerprint_sha256'] = '0' * 64
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'structural fingerprint changed -> reject')

    d = copy.deepcopy(decls['H1'])
    d['structure']['instruction_count'] += 1
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'instruction count changed -> reject')

    d = copy.deepcopy(decls['H1'])
    d['required_param_indices'] = [99]
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'required parameter position missing -> reject')

    d = copy.deepcopy(decls['H1'])
    d['constraints']['must_contain_opcodes'] = ['f64.sqrt']
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'required opcode missing -> reject')

    d = copy.deepcopy(decls['H4'])
    d['structure']['top_level_ends'] = 2
    expect_fail(lambda: rt.resolve_target(ctx, methods, d),
                'tail-insertion structure tampered -> reject')

    i = copy.deepcopy(imp_decls['I1'])
    i['field'] = 'renamed_import'
    expect_fail(lambda: rt.resolve_import(ctx, i),
                'import field renamed -> reject')

    i = copy.deepcopy(imp_decls['I1'])
    i['type_signature'] = 'ii'
    expect_fail(lambda: rt.resolve_import(ctx, i),
                'import type changed -> reject')

    i = copy.deepcopy(imp_decls['I1'])
    i['expected_call_sites'] = 2
    expect_fail(lambda: rt.resolve_import(ctx, i),
                'multiple original call sites -> reject')

    i = copy.deepcopy(imp_decls['I1'])
    i['expected_call_site']['second_argument_const'] = 0x40000001
    expect_fail(lambda: rt.resolve_import(ctx, i),
                'original argument collides with protocol tag -> reject')

    a = copy.deepcopy(alloc_decl)
    a['wrapper_method'] = 'System.String$$NoSuchWrapper'
    expect_fail(lambda: rt.resolve_allocator(ctx, methods, a),
                'allocator wrapper not found -> reject')

    a = copy.deepcopy(alloc_decl)
    a['type_signature'] = 'iii'
    expect_fail(lambda: rt.resolve_allocator(ctx, methods, a),
                'allocator type changed -> reject')

    a = copy.deepcopy(alloc_decl)
    a['structure']['fingerprint_sha256'] = '0' * 64
    expect_fail(lambda: rt.resolve_allocator(ctx, methods, a),
                'allocator structure changed -> reject')

    # ---------------- subprocess: build_patch must refuse stale resolution ---
    print('=== negative: build_patch refuses a tampered resolution ===')
    tmp = BUILD / 'test_tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    stale = copy.deepcopy(resolved)
    stale['inputs']['wasm']['sha256'] = '0' * 64
    stale_path = tmp / 'stale_resolved.json'
    stale_path.write_text(json.dumps(stale), encoding='utf-8')
    out_path = tmp / 'should_not_exist.wasm'
    if out_path.exists():
        out_path.unlink()
    proc = subprocess.run(
        [sys.executable, '-B', str(HERE / 'build_patch.py'),
         '--wasm', str(WASM), '--resolved', str(stale_path),
         '--targets', str(TARGETS), '--out', str(out_path)],
        capture_output=True, text=True)
    ck(proc.returncode != 0,
       'build_patch exits non-zero on stale resolution',
       (proc.stderr.strip() or proc.stdout.strip())[-140:])
    ck(not out_path.exists(),
       'no Wasm written when resolution verification fails')
    missing = subprocess.run(
        [sys.executable, '-B', str(HERE / 'build_patch.py'),
         '--wasm', str(WASM), '--resolved', str(tmp / 'does_not_exist.json'),
         '--out', str(out_path)],
        capture_output=True, text=True)
    ck(missing.returncode != 0,
       'build_patch refuses to run without resolved_targets.json')
    ck(not out_path.exists(), 'no Wasm written without a resolution')
    refusal = (missing.stderr + missing.stdout + proc.stderr + proc.stdout).lower()
    ck(('refus' in refusal or 'error' in refusal),
       'refusal is explicit (no silent historical-number fallback)')

    report = {'status': 'PASS',
              'checks': results,
              'passed': sum(1 for r in results if r['pass']),
              'total': len(results)}
    (BUILD / 'RESOLUTION_TEST_REPORT.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"\nRESOLUTION TESTS PASS ({report['passed']}/{report['total']})")
    print('  ->', BUILD / 'RESOLUTION_TEST_REPORT.json')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('RESOLUTION TESTS FAIL:', type(e).__name__, e, file=sys.stderr)
        sys.exit(1)
