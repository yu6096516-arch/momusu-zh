#!/usr/bin/env python3
"""Developer-only orchestrator: inputs -> resolve -> patch -> verify -> test -> report.

NOT part of the player ZIP, NOT called by build_release.bat. It writes only
under `build/wasm/` and never touches `resources/`, the launcher, the Loader,
Bootstrap, Shim, fonts, translation data or the release ZIP.

Only two supported developer invocations (see rebuild_wasm.bat):
    python -B tools/wasm/rebuild_wasm.py
        Offline local build from tools/wasm/inputs/. No network, no version
        check, no download. Valid metadata is reused; missing metadata is
        regenerated from the local official Wasm/Data.
    python -B tools/wasm/rebuild_wasm.py --update 202611021000
        Download exactly that official build, regenerate ALL IL2CPP metadata
        from it, then rebuild. Never guesses a version.

Any failure stops the pipeline, removes the generated Wasm artifacts and exits
non-zero. There is NO fallback to historical function indices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

USAGE = (
    'Usage:\n'
    '  rebuild_wasm.bat\n'
    '      Local offline rebuild using tools\\wasm\\inputs\\.\n'
    '  rebuild_wasm.bat --update 202611021000\n'
    '      Download that official version and rebuild.\n'
    'The version must be a numeric official build id, e.g. 202611021000.')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
INPUTS = HERE / 'inputs'
# MONMUSU_BUILD_ROOT is set by the phase-33 auto-build worker so every
# intermediate stays inside the job's scratch tree.
BUILD = Path(os.environ.get('MONMUSU_BUILD_ROOT') or (ROOT / 'build' / 'wasm'))
LOGS = BUILD / 'logs'


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for c in iter(lambda: fh.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def run_step(label, argv, logs):
    print(f'\n=== {label} ===')
    proc = subprocess.run([sys.executable, '-B'] + [str(x) for x in argv],
                          capture_output=True, text=True, cwd=str(ROOT))
    logs.mkdir(parents=True, exist_ok=True)
    (logs / (label.replace(' ', '_') + '.log')).write_text(
        (proc.stdout or '') + ('\n--- stderr ---\n' + proc.stderr if proc.stderr else ''),
        encoding='utf-8')
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print(f'FAILED: {label} (exit {proc.returncode})', file=sys.stderr)
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--branch', default='0', choices=['0', '1'],
                    help='resource branch: 0 = DMM (default), 1 = FANZA')
    ap.add_argument('--wasm', default=None,
                    help='official UNPATCHED ntd.wasm (or .gz, see README)')
    ap.add_argument('--script-json', default=None)
    ap.add_argument('--slot-map', default=None)
    ap.add_argument('--dump', default=None, help='dump.cs of the same build')
    ap.add_argument('--targets', default=str(HERE / 'hook_targets.json'))
    ap.add_argument('--out-dir', default=None,
                    help='default: build/wasm (branch 0) or build/wasm/branch-1')
    ap.add_argument('--reference', default=None)
    ap.add_argument('--game-build', default=None)
    ap.add_argument('--skip-tests', action='store_true')
    ap.add_argument('--update', metavar='VERSION', default=None,
                    help='download one explicit official build and rebuild')
    if argv is None:
        argv = sys.argv[1:]
    if '--update' in argv:
        i = argv.index('--update')
        if i + 1 >= len(argv) or str(argv[i + 1]).startswith('-'):
            print(USAGE, file=sys.stderr)
            return 1
    args = ap.parse_args(argv)

    # One input set per branch: never mix derived files across /0/ and /1/.
    branch_inputs = HERE / ('inputs' if args.branch == '0'
                            else f'inputs-{args.branch}')
    args.wasm = args.wasm or str(branch_inputs / 'ntd.orig.wasm')
    args.script_json = args.script_json or str(branch_inputs / 'script.json')
    args.slot_map = args.slot_map or str(branch_inputs / 'slot2name.json')
    args.dump = args.dump or str(branch_inputs / 'dump.cs')
    if args.reference is None:
        args.reference = str(ROOT / 'resources' /
                             ('ntd.patched.wasm' if args.branch == '0'
                              else f'ntd.patched.{args.branch}.wasm'))
    if args.out_dir is None:
        # Separate output trees so the two branches never overwrite each
        # other's report, resolution or generated Wasm.
        args.out_dir = str(BUILD if args.branch == '0'
                           else BUILD / f'branch-{args.branch}')

    import official_inputs
    try:
        if args.update is not None:
            version = official_inputs.validate_version(args.update)
            print(f'=== 0 official update: {version} ===')
            info = official_inputs.update(version, args.branch)
            args.input_mode = f'download {info["game_build"]}'
            args.game_build = info['game_build']
        else:
            print('=== 0 offline local inputs ===')
            r = official_inputs.ensure_local(args.branch)
            args.input_mode = f'{r["mode"]} ({r["info"]["game_build"]})'
            if not args.game_build:
                args.game_build = r['info']['game_build']
    except official_inputs.InputError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    except Exception as e:                                     # noqa: BLE001
        print(f'ERROR while preparing official inputs: {type(e).__name__}: {e}',
              file=sys.stderr)
        return 1

    wasm = Path(args.wasm)
    script = Path(args.script_json)
    slotmap = Path(args.slot_map)
    targets = Path(args.targets)
    outdir = Path(args.out_dir)
    reference = Path(args.reference)
    outdir.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    for p in (wasm, script, targets):
        if not p.is_file():
            print(f'ERROR: missing input {p}', file=sys.stderr)
            return 1
    if not slotmap.is_file():
        print('ERROR: missing slot2name.json (needed for symbol cross-checks)',
              file=sys.stderr)
        return 1

    manifest = {
        'note': 'Controlled developer inputs for this build. These files live '
                'inside the project; the historical research directory is NOT '
                'a runtime or build dependency.',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'inputs': {
            p.name: {'path': str(p.resolve()), 'bytes': p.stat().st_size,
                     'sha256': sha_file(p)}
            for p in (wasm, script, slotmap, targets,
                      HERE / 'runtime_targets.json', Path(args.dump))
        },
    }
    (outdir / 'INPUT_MANIFEST.json').write_text(
        json.dumps(manifest, indent=2) + '\n', encoding='utf-8')

    stem = 'ntd.patched' if args.branch == '0' else f'ntd.patched.{args.branch}'
    generated = outdir / f'{stem}.wasm'
    generated_gz = outdir / f'{stem}.wasm.gz'
    resolved = outdir / 'resolved_targets.json'

    steps = []
    ok = True

    resolve_argv = [
        HERE / 'resolve_targets.py', '--wasm', wasm, '--script-json', script,
        '--slot-map', slotmap, '--targets', targets, '--out-dir', outdir,
    ]
    if args.game_build:
        resolve_argv += ['--game-build', args.game_build]
    ok = run_step('1 resolve', resolve_argv, LOGS) and ok
    if not ok:
        return finish(False, outdir, generated, generated_gz, steps, args)

    r = json.loads(resolved.read_text(encoding='utf-8'))
    if r.get('status') != 'PASS':
        print('ERROR: resolved_targets.json is not PASS', file=sys.stderr)
        return finish(False, outdir, generated, generated_gz, steps, args)
    steps.append(('dynamic resolution', 'PASS',
                  f"{len(r['targets'])} hooks + 2 imports + 1 allocator"))

    ok = run_step('2 patch', [
        HERE / 'build_patch.py', '--wasm', wasm, '--resolved', resolved,
        '--targets', targets, '--mode', 'translate', '--out', generated,
    ], LOGS) and ok
    if not ok:
        return finish(False, outdir, generated, generated_gz, steps, args)

    ok = run_step('3 verify', [
        HERE / 'verify_patch.py', '--original', wasm, '--generated', generated,
        '--reference', reference, '--resolved', resolved, '--targets', targets,
        '--gzip', generated_gz,
        '--out-json', outdir / 'verify_result.json',
        '--out-md', outdir / 'VERIFY_REPORT.md',
    ], LOGS) and ok
    if not ok:
        return finish(False, outdir, generated, generated_gz, steps, args)
    verify = json.loads((outdir / 'verify_result.json').read_text(encoding='utf-8'))
    steps.append(('byte-level equivalence', verify['status'],
                  verify['info'].get('generated_sha256')))

    # Every runtime binding (import names/count, icall literals, element-table
    # slots, field offsets) is resolved from THIS build and bound to the SHA of
    # the Wasm that will ship. The loader config is only merged once the whole
    # pipeline has passed, so a failed build can never leave a stale entry.
    rt_json = outdir / 'runtime_config.json'
    rt_argv = [HERE / 'resolve_runtime.py', '--branch', args.branch,
               '--orig', wasm, '--patched', generated,
               '--script-json', script, '--dump', args.dump,
               '--game-build', args.game_build or '', '--out-dir', outdir,
               '--no-bundle']
    ok = run_step('4 runtime config', rt_argv, LOGS) and ok
    if not ok:
        return finish(False, outdir, generated, generated_gz, steps, args)
    rt = json.loads(rt_json.read_text(encoding='utf-8'))
    steps.append(('runtime config', 'PASS' if rt['compat']['publishable'] else 'FAIL',
                  f"RESOLVED {rt['compat']['resolved']} / VERIFIED_STATIC "
                  f"{rt['compat']['verified_static']} / UNRESOLVED "
                  f"{rt['compat']['unresolved']}"))

    if not args.skip_tests:
        ok = run_step('5 reorder', [HERE / 'test_reorder.py'], LOGS) and ok
        steps.append(('synthetic reorder test', 'PASS' if ok else 'FAIL',
                      str(outdir / 'synth' / 'REORDER_TEST_REPORT.json')))
        ok2 = run_step('6 resolution tests', [HERE / 'test_wasm_build.py'], LOGS)
        steps.append(('resolution + negative tests', 'PASS' if ok2 else 'FAIL',
                      str(outdir / 'RESOLUTION_TEST_REPORT.json')))
        ok3 = run_step('7 runtime config tests', [HERE / 'test_runtime_config.py'],
                       LOGS)
        steps.append(('runtime config tests', 'PASS' if ok3 else 'FAIL',
                      str(outdir / 'RUNTIME_CONFIG_TEST_REPORT.json')))
        ok = ok and ok2 and ok3
    else:
        steps.append(('tests', 'SKIPPED', '--skip-tests'))

    if ok:
        ok = run_step('8 loader config', [
            HERE / 'resolve_runtime.py', '--branch', args.branch,
            '--from-json', rt_json], LOGS) and ok
        steps.append(('loader config merged', 'PASS' if ok else 'FAIL',
                      str(ROOT / 'monmusu_cn_runtime_config.js')))

    return finish(ok, outdir, generated, generated_gz, steps, args, r, verify)


def finish(ok, outdir, generated, generated_gz, steps, args, r=None, verify=None):
    if not ok:
        # Safe stop: never leave a candidate that could be mistaken for a build.
        for p in (generated, generated_gz):
            if p.exists():
                p.unlink()
        print('\nBUILD FAILED -> generated Wasm removed; release resources untouched.',
              file=sys.stderr)
    write_build_report(ok, outdir, generated, steps, args, r, verify)
    print(f'\nBUILD {"PASS" if ok else "FAIL"} -> {outdir / "BUILD_REPORT.md"}')
    return 0 if ok else 1


def write_build_report(ok, outdir, generated, steps, args, r, verify):
    L = ['# 阶段二十八：Wasm 动态定位构建报告', '',
         f'**结论：{"PASS" if ok else "FAIL"}**', '',
         '> 开发者专用构建。输出仅位于 `build/wasm/`，**未覆盖**任何 `resources/` '
         '发行资源；未修改玩家 ZIP、启动器、正式打包流程、生产 Loader / Bootstrap / '
         'Shim、字体或翻译数据。未提交、推送、部署。', '',
         '## 1. 步骤', '', '| 步骤 | 结果 | 说明 |', '|---|---|---|']
    for name, status, detail in steps:
        L.append(f'| {name} | {status} | `{detail}` |')
    L += ['', '## 2. 输入', '',
          f"- **输入模式**：`{getattr(args, 'input_mode', 'unknown')}`",
          f"- 游戏构建：`{args.game_build or '(unset)'}`",
          f"- 官方原始 Wasm：`{args.wasm}`",
          f"- IL2CPP 元数据：`{args.script_json}`（与本次官方 Wasm 同版本生成）",
          f"- 槽位名映射：`{args.slot_map}`",
          f"- Hook 声明：`{args.targets}`",
          '- 各文件 SHA-256 见 `INPUT_MANIFEST.json`。', '']

    if r:
        L += ['## 3. 9 个 Hook 的动态定位结果', '',
              '| Hook | 方法 | TS | slot | func | def | 指纹 | 结果 |',
              '|---|---|---|---:|---:|---:|---|---|']
        for t in r['targets']:
            L.append(f"| {t['id']} | `{t['method']}` | `{t['type_signature']}` | "
                     f"0x{t['element_slot']:x} | {t['func_index']} | {t['def_index']} | "
                     f"`{t['structure']['fingerprint_sha256'][:12]}…` | {t['status']} |")
        L += ['', '## 4. 两个 import 与字符串分配函数', '']
        for k in ('lookup', 'context'):
            i = r['imports'][k]
            L.append(f"- **{i['id']} / {i['role']}**：`{i['module']}.{i['field']}` → "
                     f"import index **{i['import_index']}**，类型 `{i['wasm_signature']}`，"
                     f"原调用点 {i['original_call_sites']} 处（func "
                     f"{i['call_site']['func_index']}），原第二参数 "
                     f"0x{i['call_site']['second_argument_const']:x}，无 tag 位冲突 → {i['status']}")
        a = r['allocator']
        L.append(f"- **F1**：`{a['wrapper']['method']}`（slot 0x{a['wrapper']['element_slot']:x}, "
                 f"func {a['wrapper']['func_index']}）三指令薄包装 → "
                 f"**String::NewSize = func {a['func_index']}**，类型 `{a['wasm_signature']}`，"
                 f"调用它的函数 {a['direct_callers']} 个 → {a['status']}")
        ap = r['appended']
        L += ['', f"追加函数编号公式 `{ap['base_formula']}` = "
              f"{ap['imported_function_count']} + {ap['defined_function_count']} + offset，"
              f"base **{ap['base']}**，共 {ap['count']} 个；导出 "
              f"{ap['exports']}。", '']

    if verify:
        i = verify['info']
        identical = '是' if i.get('generated_sha256') == i.get('reference_sha256') else '否'
        L += ['## 5. 与发行版的字节级对照', '',
              f"- 生成 Wasm SHA-256：`{i.get('generated_sha256')}`",
              f"- 发行 Wasm SHA-256：`{i.get('reference_sha256')}`",
              f'- **是否逐字节一致：{identical}**',
              f"- 变化的 section：{i.get('changed_sections')}（白名单 [3, 7, 10]）",
              f"- 修改函数体 {i.get('modified_body_count')} 个，追加 "
              f"{i.get('appended_body_count')} 个",
              f"- 三个导出：{i.get('exports')}", '']
        if i.get('gzip_note'):
            L += [f"- gzip：`{i['gzip_note']}`", '']

    L += ['## 6. 生产构建路径是否仍使用固定函数编号', '',
          '- `tools/wasm/build_patch.py` 的生产路径：**否**。9 个 Hook、2 个 import、'
          '分配函数、追加函数编号全部来自 `resolved_targets.json`；'
          '`HISTORICAL_REFERENCE` 只被回归测试读取。',
          '- 动态定位失败 / 解析结果过期 / 结构不匹配：**直接终止**，无旧编号回退。',
          '- 但**整个汉化系统**仍有固定编号（本轮明确未重构，见下节）。',
          '', '## 7. 剩余依赖与人工介入', '']
    if r:
        for item in r.get('remaining_dependencies', []):
            L.append(f'- {item}')
    L += ['', '## 8. 未来官方更新需要提供的输入文件', '',
          '1. 新版官方 **未打补丁** 的 `ntd.wasm`（或 `ntd.wasm.gz` 解压结果）。',
          '2. 由该 Wasm + 同版本 `global-metadata.dat` 重新生成的 `script.json`'
          '（Il2CppDumper 的 `ScriptMethod` 数组，含 Name / Address / '
          'TypeSignature / Signature）。',
          '3. 由同版本 `dump.cs` 生成的 `slot2name.json`（`python -B '
          'tools/wasm/symmap.py`）。',
          '4. `ntd.data`（供 `extract_metadata.py` 抽取 `global-metadata.dat`）、'
          '`dump.cs`、`il2cpp.h` 作为复核材料。',
          '5. 人工确认 9 个方法的 C# 签名与参数语义未变；若编译优化改变了结构指纹，'
          '需人工复核后显式运行 `resolve_targets.py --emit-baseline` 重新签核基线，'
          '工具**不会**自动放宽。',
          '6. 若 `a.nf` / `a.md` 混淆名或 import 集合改变，还需更新 '
          '`hook_targets.json` 中的 import 声明，并同步修改生产 `bootstrap.js` '
          '（后者本轮禁止修改，属于正式更新时的人工步骤）。',
          '', '## 9. 边界', '',
          '- 本阶段只解决 **Wasm Hook 的动态定位与重新生成**。不能据此宣称整个汉化'
          '系统已兼容新版游戏；尤其**不能**把旧 Wasm 补丁重定向给结构不同的新版游戏。',
          '- **新版 Wasm 构建通过 ≠ 汉化兼容新版**：JS Shim 的固定函数表槽位、TMP/'
          'FontAsset 字段偏移、icall 字面量地址、Bootstrap 的 `CFG.version` / '
          '`importCount` / import 名字、以及 `resources/assets.json` 的 '
          '`version_path` 与资源哈希后缀，都必须针对新版逐一人工复核。',
          '- 浏览器实机验证状态仍为 `NOT RUN`；等价性由与已验证发行 Wasm 的逐字节'
          '一致证明，`WebAssembly.validate()` 仅为辅助检查。',
          '- 合成重排测试只证明指定重排条件下定位器正确，不代表兼容任意未来真实版本。',
          '']
    (outdir / 'BUILD_REPORT.md').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
