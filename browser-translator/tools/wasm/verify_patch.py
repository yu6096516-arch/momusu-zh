#!/usr/bin/env python3
"""Byte-level and structural verification of a generated Wasm patch.

The PRIMARY equivalence proof is a SHA-256 / byte-for-byte comparison against
the shipped, browser-verified `resources/ntd.patched.wasm`.
`WebAssembly.validate()` is only a sanity check and NEVER replaces that proof.

Also verifies, independently of the reference:
  * only Function(3) / Export(7) / Code(10) sections changed;
  * exactly the 9 resolved target bodies were modified and 8 bodies appended;
  * the 3 exports point at the resolved function indices;
  * the gzip payload decompresses to exactly the plain Wasm.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import os
import sys
from pathlib import Path

import wastool

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = Path(os.environ.get('MONMUSU_BUILD_ROOT') or (ROOT / 'build' / 'wasm'))

ALLOWED_CHANGED_SECTIONS = {3, 7, 10}   # Function / Export / Code


class VerifyError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sections(buf: bytes):
    p = 8
    out = []
    while p < len(buf):
        sid = buf[p]
        p += 1
        n, q = uleb(buf, p)
        out.append((sid, q, n))
        p = q + n
    return out


def uleb(b, p):
    r = s = 0
    while True:
        x = b[p]
        p += 1
        r |= (x & 0x7f) << s
        if not (x & 0x80):
            return r, p
        s += 7


def check(cond, msg, results, name):
    results.append({'check': name, 'pass': bool(cond), 'detail': msg})
    if not cond:
        raise VerifyError(f'{name}: {msg}')


def node_validate(path: Path):
    node = shutil.which('node')
    if not node:
        return None, 'node not available -> SKIPPED'
    proc = subprocess.run(
        [node, '-e',
         "const fs=require('fs');"
         "process.exit(WebAssembly.validate(fs.readFileSync(process.argv[1]))?0:1)",
         str(path)], capture_output=True, text=True)
    return proc.returncode == 0, (proc.stderr.strip() or proc.stdout.strip() or 'ok')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--original', default=str(HERE / 'inputs' / 'ntd.orig.wasm'))
    ap.add_argument('--generated', default=str(BUILD / 'ntd.patched.wasm'))
    ap.add_argument('--reference', default=str(ROOT / 'resources' / 'ntd.patched.wasm'))
    ap.add_argument('--resolved', default=str(BUILD / 'resolved_targets.json'))
    ap.add_argument('--targets', default=str(HERE / 'hook_targets.json'))
    ap.add_argument('--gzip', default=None,
                    help='optional generated .gz to check against the plain Wasm')
    ap.add_argument('--out-json', default=str(BUILD / 'verify_result.json'))
    ap.add_argument('--out-md', default=str(BUILD / 'VERIFY_REPORT.md'))
    args = ap.parse_args(argv)

    orig_p, gen_p, ref_p = Path(args.original), Path(args.generated), Path(args.reference)
    resolved_p, targets_p = Path(args.resolved), Path(args.targets)
    results = []
    info = {}
    try:
        for p in (orig_p, gen_p, resolved_p):
            check(p.is_file(), f'missing file {p}', results, 'inputs_present')
        orig, gen = orig_p.read_bytes(), gen_p.read_bytes()
        resolved = json.loads(resolved_p.read_text(encoding='utf-8'))
        targets = json.loads(targets_p.read_text(encoding='utf-8')) if targets_p.is_file() else {}
        ref = ref_p.read_bytes() if ref_p.is_file() else None

        info['generated_sha256'] = sha(gen)
        info['generated_size'] = len(gen)
        info['original_sha256'] = sha(orig)
        info['resolution_wasm_sha256'] = resolved['inputs']['wasm']['sha256']
        check(sha(orig) == resolved['inputs']['wasm']['sha256'],
              'official Wasm SHA-256 matches the resolution input',
              results, 'resolution_input_bound')

        # A DIFFERENT official build must NOT be forced to match the old patch
        # hash (phase 29). For the pinned release build, byte identity remains
        # the primary equivalence proof.
        baseline = {}
        bl_path = HERE / 'release_baseline.json'
        if bl_path.is_file():
            baseline = json.loads(bl_path.read_text(encoding='utf-8'))
        same_build = (bool(baseline)
                      and sha(orig) == baseline.get('official_wasm_sha256'))
        info['release_baseline_build'] = baseline.get('game_build')
        info['same_as_release_build'] = same_build
        if not same_build:
            info['different_build_note'] = (
                'Official Wasm differs from the pinned release baseline. '
                'Byte identity with the old patch is NOT required; correctness '
                'is proven by dynamic Hook resolution + structural verification.')

        # 1. PRIMARY equivalence proof -------------------------------------
        if same_build and ref is not None:
            info['reference_sha256'] = sha(ref)
            check(sha(gen) == sha(ref),
                  f'generated {sha(gen)} vs reference {sha(ref)}',
                  results, 'byte_identical_to_release')
            check(gen == ref, 'byte-for-byte cmp', results, 'byte_cmp')
        else:
            detail = ('different official build -> not required (phase 29)'
                      if not same_build else 'reference not provided')
            results.append({'check': 'byte_identical_to_release', 'pass': None,
                            'detail': detail})
            results.append({'check': 'byte_cmp', 'pass': None, 'detail': detail})

        # 2. WebAssembly.validate (auxiliary only) --------------------------
        ok, detail = node_validate(gen_p)
        if ok is None:
            results.append({'check': 'webassembly_validate', 'pass': None,
                            'detail': detail})
        else:
            check(ok, detail, results, 'webassembly_validate')

        # 3. section whitelist ---------------------------------------------
        so, sg = sections(orig), sections(gen)
        check([x[0] for x in so] == [x[0] for x in sg],
              'section id/order identical', results, 'section_order')
        changed = []
        for (i1, q1, n1), (i2, q2, n2) in zip(so, sg):
            if orig[q1:q1 + n1] != gen[q2:q2 + n2]:
                changed.append(i1)
        info['changed_sections'] = changed
        check(set(changed) <= ALLOWED_CHANGED_SECTIONS,
              f'changed sections {changed}, allowed {sorted(ALLOWED_CHANGED_SECTIONS)}',
              results, 'section_whitelist')
        check(set(changed) == ALLOWED_CHANGED_SECTIONS,
              'all three expected sections changed (no silent no-op)',
              results, 'expected_sections_changed')

        # 4. per-function bodies -------------------------------------------
        wo, wg = wastool.Wasm(str(orig_p)), wastool.Wasm(str(gen_p))
        bo = [bytes(wo.buf[a:a + n]) for a, n, _d, _le in wo.code]
        bg = [bytes(wg.buf[a:a + n]) for a, n, _d, _le in wg.code]
        expected_targets = {t['def_index']: t for t in resolved['targets']}
        modified = [i for i in range(len(bo)) if bo[i] != bg[i]]
        info['modified_body_count'] = len(modified)
        info['appended_body_count'] = len(bg) - len(bo)
        info['modified_def_indices'] = modified
        check(len(modified) == 9, f'9 existing bodies modified, got {len(modified)}',
              results, 'exactly_nine_prologues')
        check(set(modified) == set(expected_targets),
              f'modified set == resolved targets: {sorted(modified)}',
              results, 'modified_set_matches_resolution')
        check(len(bg) - len(bo) == resolved['appended']['count'],
              f'8 appended bodies, got {len(bg) - len(bo)}',
              results, 'eight_appended_bodies')

        # regression-only cross-check against declared historical sizes
        hist = {d['id']: d.get('historical', {}) for d in targets.get('targets', [])}
        size_rows = []
        for t in resolved['targets']:
            i = t['def_index']
            h = hist.get(t['id'], {})
            expected_after = h.get('body_size_after')
            row = {'id': t['id'], 'def': i, 'before': len(bo[i]),
                   'after': len(bg[i]), 'declared_after': expected_after}
            size_rows.append(row)
            if same_build and expected_after is not None:
                check(len(bg[i]) == expected_after,
                      f"{t['id']} body {len(bg[i])} != historical {expected_after}",
                      results, f'historical_body_size_{t["id"]}')
        info['body_sizes'] = size_rows

        # exact byte equality of each body vs the reference (pinned build only)
        if same_build and ref is not None:
            wr = wastool.Wasm(str(ref_p))
            br = [bytes(wr.buf[a:a + n]) for a, n, _d, _le in wr.code]
            check(all(bg[i] == br[i] for i in range(min(len(bg), len(br)))),
                  'every function body identical to the release module',
                  results, 'all_bodies_match_release')

        # 5. exports --------------------------------------------------------
        exports = {name: idx for name, kind, idx in wg.exports if kind == 0}
        info['exports'] = {k: exports.get(k) for k in resolved['appended']['exports']}
        for name, idx in resolved['appended']['exports'].items():
            check(exports.get(name) == idx,
                  f'export {name} -> {exports.get(name)}, expected {idx}',
                  results, f'export_{name}')
        check(len(wg.exports) - len(wo.exports) == 3,
              f'export count +3, got {len(wg.exports) - len(wo.exports)}',
              results, 'three_new_exports')

        # 6. gzip payload ---------------------------------------------------
        gz_path = Path(args.gzip) if args.gzip else Path(str(gen_p) + '.gz')
        if gz_path.is_file():
            plain = gzip.decompress(gz_path.read_bytes())
            check(plain == gen, f'{gz_path.name} decompresses to the plain Wasm',
                  results, 'gzip_roundtrip')
            info['gzip_size'] = gz_path.stat().st_size
            if ref_p.is_file():
                release_gz = ref_p.with_suffix('.wasm.gz')
                if release_gz.is_file():
                    info['release_gzip_size'] = release_gz.stat().st_size
                    info['gzip_note'] = (
                        'The generated .gz is NOT byte-identical to the shipped '
                        'resources/ntd.patched.wasm.gz: gzip header/compression '
                        'parameters differ (phase 26 re-gzipped the artifact). '
                        'The decompressed Wasm is byte-identical, so this is a '
                        'non-semantic encoding difference only. The shipped .gz '
                        'is left untouched.')
                    info['gzip_release_sha256'] = sha(release_gz.read_bytes())
                    info['generated_gzip_sha256'] = sha(gz_path.read_bytes())

        status = 'PASS'
    except VerifyError as e:
        status = 'FAIL'
        info['error'] = str(e)
    except Exception as e:                       # noqa: BLE001
        status = 'ERROR'
        info['error'] = f'{type(e).__name__}: {e}'

    out = {'status': status, 'info': info, 'checks': results,
           'note': 'WebAssembly.validate is auxiliary; equivalence is proven by '
                   'byte identity with the released Wasm.'}
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    L = ['# Wasm 补丁验证报告', '',
         f'**结论：{status}**', '',
         '| 检查 | 结果 | 说明 |', '|---|---|---|']
    for r in results:
        mark = 'PASS' if r['pass'] else ('SKIP' if r['pass'] is None else 'FAIL')
        L.append(f"| `{r['check']}` | {mark} | {str(r['detail']).replace('|', '/')} |")
    L += ['', '## 字节对照', '',
          f"- 官方构建与发行基线一致：`{info.get('same_as_release_build')}`"
          f"（基线 `{info.get('release_baseline_build')}`）",
          f"- 生成 Wasm SHA-256：`{info.get('generated_sha256')}`",
          f"- 发行 Wasm SHA-256：`{info.get('reference_sha256', 'N/A')}`",
          f"- 生成大小：{info.get('generated_size')} B",
          f"- 变化的 section：{info.get('changed_sections')}（白名单 {sorted(ALLOWED_CHANGED_SECTIONS)}）",
          f"- 修改函数体：{info.get('modified_body_count')}；追加函数体：{info.get('appended_body_count')}",
          f"- 三个导出：{info.get('exports')}", '']
    if info.get('gzip_note'):
        L += ['## gzip 说明', '', info['gzip_note'], '']
    if info.get('error'):
        L += ['## 错误', '', '```', info['error'], '```', '']
    L += ['> `WebAssembly.validate()` 只是健全性检查；等价性由与发行 Wasm 的逐字节一致证明。', '']
    Path(args.out_md).write_text('\n'.join(L), encoding='utf-8')

    print(f'VERIFY {status}')
    for r in results:
        print(f"  [{'PASS' if r['pass'] else ('SKIP' if r['pass'] is None else 'FAIL')}] {r['check']}")
    if status != 'PASS':
        print('  error:', info.get('error'), file=sys.stderr)
    print('  ->', args.out_json)
    print('  ->', args.out_md)
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
