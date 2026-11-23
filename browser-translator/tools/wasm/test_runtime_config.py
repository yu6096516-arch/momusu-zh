"""Tests for the runtime-binding resolver (phase 32).

Positive: both branches must resolve every binding, and branch 0 must reproduce
exactly the numbers the shim used to carry by hand (that is the regression
proof: the new derivation agrees with the shipped, browser-tested build).

Negative: every way a future build could make a binding ambiguous or wrong must
be reported as UNRESOLVED instead of guessed.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import resolve_runtime as rt

REPORT = ROOT / 'build' / 'wasm' / 'RUNTIME_CONFIG_TEST_REPORT.json'
checks = []


def ck(ok, label, detail=''):
    checks.append({'check': label, 'pass': bool(ok), 'detail': str(detail)[:300]})
    print(('PASS  ' if ok else 'FAIL  ') + label + (f'   [{detail}]' if detail else ''))
    return ok


def expect_fail(fn, label, must_contain=None):
    try:
        fn()
    except Exception as exc:                                  # noqa: BLE001
        text = f'{type(exc).__name__}: {exc}'
        ok = (must_contain is None) or (must_contain in text)
        return ck(ok, label, text[:170])
    return ck(False, label, 'no error raised')


BRANCH = {}
for b, d in (('0', 'inputs'), ('1', 'inputs-1')):
    BRANCH[b] = {
        'dir': HERE / d,
        'orig': HERE / d / 'ntd.orig.wasm',
        'script': HERE / d / 'script.json',
        'dump': HERE / d / 'dump.cs',
        'patched': ROOT / 'resources' / ('ntd.patched.wasm' if b == '0'
                                         else f'ntd.patched.{b}.wasm'),
    }

TARGETS = json.loads((HERE / 'runtime_targets.json').read_text(encoding='utf-8'))

# The numbers the shim carried before phase 32, for branch 0 only. Kept here as
# a regression oracle; they do not participate in resolution.
LEGACY_SLOTS = {
    'get_transform': 0x0f58, 'get_parent': 0x103e, 'get_name': 0x116b,
    'type_gettype': 0x19b6f, 'find_all': 0x15397, 'font_has_char': 0x1659d,
    'create_font': 0x18ab7, 'create_font_short': 0x18ab6,
    'object_destroy': 0x15459, 'tmp_instance': 0x18c44, 'mat_shader': 0x150a6,
    'mat_has_prop': 0x150af, 'mat_get_float': 0x150d2, 'mat_set_float': 0x150c8,
    'mat_enable_kw': 0x150b1, 'mat_main_tex': 0x150a9, 'tmp_has_char': 0x18ac6,
    'tmp_set_hpc': 0x18d68, 'tmp_get_font': 0x18ceb, 'tmp_set_font': 0x18cec,
    'tmp_get_shared_mat': 0x18ced, 'tmp_set_shared_mat': 0x18cee,
    'object_gettype': 0x914, 'array_create': 0xbd4,
}
LEGACY_ICALLS = {
    'InitializeFontEngine': 0x064b8c, 'LoadFontFace': 0x05ef61,
    'GetFaceInfo': 0x06953b, 'TryAddGlyphToTexture': 0x06808b,
    'FindObjectsOfTypeAll': 0x59f91, 'HasCharacter': 0x6108a,
}
LEGACY_CACHE = {'FindObjectsOfTypeAll': 0x786738, 'HasCharacter': 0x77ad14}


def resolve(branch, targets=None, patched=None, dump=None):
    spec = BRANCH[branch]
    orig = None
    for key in ('orig', 'patched', 'script', 'dump'):
        if not spec[key].is_file():
            raise SystemExit(f'missing input: {spec[key]} (run rebuild_wasm.bat first)')
    orig = rt.View(spec['orig'])
    patched_view = rt.View(patched or spec['patched'])
    dump_map = rt.parse_dump(dump or spec['dump'])
    return rt.build_config(branch, f'202611021000/{branch}', patched_view, orig,
                           dump_map, targets or TARGETS, spec['script'])


def resolve_strict(branch, **kw):
    """Resolution that treats any UNRESOLVED entry as an error, the way the
    build pipeline does (it refuses to patch when a binding is missing)."""
    cfg, slots, failures = resolve(branch, **kw)
    if failures:
        raise rt.ResolveError('; '.join(f'{i}: {r}' for i, r, _ in failures))
    return cfg


def main():
    print('=== A. positive: both branches resolve every binding ===')
    cfgs = {}
    for branch in ('0', '1'):
        try:
            cfg, slots, failures = resolve(branch)
        except rt.ResolveError as exc:
            ck(False, f'branch {branch} resolves', str(exc))
            continue
        cfgs[branch] = cfg
        ck(not failures, f'branch {branch}: no unresolved binding',
           f'{len(failures)} failure(s): {[f[0] for f in failures][:6]}')
        ck(cfg['compat']['publishable'], f'branch {branch}: publishable')
        ck(all(e['status'] == rt.RESOLVED for e in slots),
           f'branch {branch}: all {len(slots)} slots RESOLVED')

    if '0' not in cfgs:
        return finish()

    print()
    print('=== B. branch 0 reproduces the shipped, browser-tested numbers ===')
    bad = []
    for sid, want in LEGACY_SLOTS.items():
        got = cfgs['0']['slots'][sid].get('slot')
        if got != want:
            bad.append((sid, hex(want), hex(got) if got is not None else None))
    ck(not bad, f'{len(LEGACY_SLOTS)} element-table slots match the legacy table', bad[:5])
    bad = []
    for iid, want in LEGACY_ICALLS.items():
        got = cfgs['0']['icalls'][iid].get('literal')
        if got != want:
            bad.append((iid, hex(want), hex(got) if got is not None else None))
    ck(not bad, f'{len(LEGACY_ICALLS)} icall literals match', bad[:5])
    bad = [(k, hex(v), hex(cfgs['0']['icalls'][k].get('cache_ptr') or 0))
           for k, v in LEGACY_CACHE.items()
           if cfgs['0']['icalls'][k].get('cache_ptr') != v]
    ck(not bad, 'both icall cache pointers match', bad)
    ck(cfgs['0']['icalls']['resolve_icall']['slot'] == 0xd9b,
       'resolve_icall slot matches the legacy 0xd9b')
    ck(cfgs['0']['imports']['translation']['index'] == 336
       and cfgs['0']['imports']['context']['index'] == 228
       and cfgs['0']['imports']['count'] == 540,
       'import names / indices / count match the legacy CFG')

    print()
    print('=== C. the two branches really do differ (this is why phase 32 exists) ===')
    if '1' in cfgs:
        moved = [k for k in LEGACY_SLOTS
                 if cfgs['0']['slots'][k]['slot'] != cfgs['1']['slots'][k]['slot']]
        same = [k for k in LEGACY_SLOTS if k not in moved]
        ck(len(moved) == 19, f'{len(moved)} of 24 slots move between /0/ and /1/', moved[:4])
        ck(sorted(same) == ['array_create', 'get_name', 'get_parent',
                            'get_transform', 'object_gettype'],
           'the 5 slots that do not move are the 4 low ones + array_create', same)
        ck(all(cfgs['0']['slots'][k]['slot'] == LEGACY_SLOTS[k] for k in same),
           'a stale /0/ table is wrong for exactly the 19 that move')
        ck(cfgs['0']['icalls']['LoadFontFace']['literal']
           == cfgs['1']['icalls']['LoadFontFace']['literal'],
           'icall literals are identical across branches (data section is shared)')

    print()
    print('=== D. negative: ambiguity and mismatch are refused, never guessed ===')

    def clash(targets, target_id, mutate):
        t = copy.deepcopy(targets)
        for entry in t['slots']:
            if entry['id'] == target_id:
                mutate(entry)
        return t

    amb = clash(TARGETS, 'type_gettype', lambda e: e.pop('native_params'))
    expect_fail(lambda: resolve_strict('0', targets=amb),
                'two overloads with no native_params -> reject', 'overloads')

    wrong = clash(TARGETS, 'get_transform',
                  lambda e: e.update(type_signature='iiiii'))
    expect_fail(lambda: resolve_strict('0', targets=wrong),
                'declared TypeSignature that the method does not have -> reject', 'ScriptMethod')

    arity = clash(TARGETS, 'find_all', lambda e: e.update(arity=3))
    expect_fail(lambda: resolve_strict('0', targets=arity),
                'shim arity that disagrees with the signature -> reject', 'arity')

    ic = copy.deepcopy(TARGETS)
    for entry in ic['icalls']:
        if entry['id'] == 'LoadFontFace':
            entry['literal'] = entry['literal'].replace('FromFont', 'FromNothing')
    expect_fail(lambda: resolve_strict('0', targets=ic),
                'icall literal that no longer exists -> reject', 'not present')

    ic2 = copy.deepcopy(TARGETS)
    for entry in ic2['icalls']:
        if entry['id'] == 'FindObjectsOfTypeAll':
            entry['slot'] = 'get_transform'      # wrong wrapper for the cache read
    expect_fail(lambda: resolve_strict('0', targets=ic2),
                'cache pointer read from the wrong wrapper -> reject', None)

    expect_fail(lambda: resolve('0', patched=BRANCH['1']['patched']),
                'patched wasm from the other branch -> reject', 'section')

    print()
    print('=== E. negative: dump.cs field layout changes are refused ===')
    fd = copy.deepcopy(TARGETS)
    for entry in fd['fields']:
        if entry['id'] == 'tmp_text_font_asset':
            entry['field'] = 'm_fontAssetRenamed'
    expect_fail(lambda: resolve_strict('0', targets=fd),
                'field renamed in a new TMP version -> reject', 'dump.cs')

    print()
    print('=== F. the generated loader config is complete and bound ===')
    bundle_path = ROOT / 'monmusu_cn_runtime_config.js'
    if bundle_path.is_file():
        bundle = rt.load_bundle(bundle_path)
        ck(set(bundle['branches']) >= {'0', '1'},
           'config carries both branches', sorted(bundle['branches']))
        import hashlib
        for b in ('0', '1'):
            entry = bundle['branches'][b]
            wasm = ROOT / 'resources' / ('ntd.patched.wasm' if b == '0'
                                         else f'ntd.patched.{b}.wasm')
            if not wasm.is_file():
                ck(False, f'branch {b}: packaged wasm present')
                continue
            real = hashlib.sha256(wasm.read_bytes()).hexdigest()
            ck(entry['wasm']['sha256'] == real,
               f'branch {b}: config is bound to the packaged wasm', real[:16])
            ck(entry['compat']['publishable'],
               f'branch {b}: config says publishable')
    else:
        ck(False, 'monmusu_cn_runtime_config.js exists')

    print()
    print('=== G. a stale config is refused by the release gate ===')
    sys.path.insert(0, str(ROOT / 'tools'))
    try:
        import build_release as br
        manifest = json.loads((ROOT / 'resources/assets.json').read_text(encoding='utf-8'))
        checked = br.check_runtime_config(manifest)
        ck(checked == ['0', '1'], 'release gate accepts both branches', checked)
        broken = copy.deepcopy(manifest)
        for spec in broken['assets']:
            if spec['name'] == 'ntd.wasm.gz' and str(spec.get('branch')) == '1':
                spec['file'] = 'resources/ntd.patched.wasm.gz'   # the DMM wasm
        expect_fail(lambda: br.check_runtime_config(broken),
                    'branch 1 shipping the /0/ wasm -> release refused', 'bound to wasm')
        full = br.runtime_config()
        orig_loader = br.runtime_config

        def without_branch_one():
            shrunk = copy.deepcopy(full)
            shrunk['branches'].pop('1', None)
            return shrunk

        br.runtime_config = without_branch_one
        try:
            expect_fail(lambda: br.check_runtime_config(manifest),
                        'a shipped branch without a config entry -> release refused',
                        'no runtime config')
        finally:
            br.runtime_config = orig_loader
    except Exception as exc:                                   # noqa: BLE001
        ck(False, 'release gate checks ran', f'{type(exc).__name__}: {exc}')

    return finish()


def finish():
    total = len(checks)
    passed = sum(1 for c in checks if c['pass'])
    status = 'PASS' if passed == total and total else 'FAIL'
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'status': status, 'passed': passed,
                                  'total': total, 'checks': checks},
                                 ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print()
    print(f'RUNTIME CONFIG TESTS: {status} ({passed}/{total}) -> {REPORT}')
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
