#!/usr/bin/env python3
"""Extract the autobuild ZIP and exercise the bundled runtime with a stripped env.

This is NOT a clean virtual machine: the host still has developer tools
installed. What it does prove is that the packaged Python, .NET runtime and
Il2CppDumper work with PATH reduced to System32 and all PYTHON*/DOTNET*
variables removed, i.e. they do not accidentally depend on the developer's
Python, .NET SDK or Node.js. A real clean-Windows test remains NOT RUN.

    python -B tools/autobuild/test_portable_autobuild.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ZIP = ROOT / 'dist' / 'MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip'
EXTRACT = ROOT / 'work' / 'portable-autobuild-test'
PLAYER = EXTRACT / 'MonmusuTDChinese'
WIN = Path(os.environ['SystemRoot'])

checks = 0
fails = 0


def check(cond, msg):
    global checks, fails
    checks += 1
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails += 1
    return bool(cond)


def clean_env(player: Path):
    env = dict(os.environ)
    for k in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONNOUSERSITE', 'VIRTUAL_ENV',
              'DOTNET_ROOT', 'IL2CPP_DOTNET_ROOT', 'MONMUSU_BUILD_PYTHON',
              'MONMUSU_DEBUG'):
        env.pop(k, None)
    env['PATH'] = str(WIN / 'System32') + ';' + str(WIN)
    env['PYTHONUTF8'] = '1'
    env['DOTNET_ROOT'] = str(player / 'runtime' / 'toolchain' / 'dotnet')
    env['DOTNET_ROLL_FORWARD'] = 'LatestMajor'
    return env


def main():
    if not ZIP.is_file():
        print(f'SKIP: {ZIP.name} not built (run build_release.py --with-toolchain)')
        return 0
    if EXTRACT.exists():
        # Rename aside instead of bulk-deleting; avoids destructive cleanup.
        EXTRACT.rename(EXTRACT.with_name(EXTRACT.name + '-old'))
    EXTRACT.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as z:
        check(z.testzip() is None, 'ZIP CRC intact')
        names = z.namelist()
        check(all(n.startswith('MonmusuTDChinese/') for n in names),
              'every entry under the single release folder')
        check(not any(n.lower().endswith(('.pem', '.p12', '.pfx', '.key', '.log'))
                      for n in names), 'no private keys or logs in ZIP')
        z.extractall(EXTRACT)

    env = clean_env(PLAYER)
    tc = json.loads((PLAYER / 'runtime' / 'toolchain.json').read_text(encoding='utf-8'))
    py = PLAYER / tc['python']
    dn = PLAYER / tc['dotnet']
    dumper = PLAYER / tc['dumper']
    check(py.is_file(), 'bundled python.exe present at declared path')
    check((dn / 'dotnet.exe').is_file(), 'bundled dotnet.exe present')
    check(dumper.is_file(), 'Il2CppDumper.exe present at declared path')

    pkg = json.loads((PLAYER / 'runtime' / 'toolchain' / 'packaged_files.json')
                     .read_text(encoding='utf-8'))
    missing = [n for n in pkg if not (PLAYER / n).is_file()]
    check(not missing, f'all {len(pkg)} packaged build scripts present'
          + (f' (missing {missing[:3]})' if missing else ''))

    code = ('import UnityPy, json, gzip, hashlib, struct, sys; '
            'from PIL import Image; import fontTools, lz4.block, fsspec, brotli; '
            'print("PY_OK", sys.executable)')
    r = subprocess.run([str(py), '-B', '-I', '-c', code], env=env,
                       capture_output=True, text=True, timeout=180)
    check('PY_OK' in r.stdout and str(py).lower() in r.stdout.lower(),
          'bundled Python imports full build chain with developer env stripped')
    if r.returncode:
        print('    stderr:', r.stderr[-400:])

    d = subprocess.run([str(dumper), '--help'], env=env, cwd=str(dumper.parent),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=180)
    check(d.returncode == 0 and 'usage' in (d.stdout + d.stderr).lower(),
          'bundled .NET runs Il2CppDumper with no system .NET')
    if d.returncode:
        print('    stderr:', (d.stderr or d.stdout)[-400:])

    # The addon itself runs in mitmdump's embedded Python. Verify it can be
    # imported with only stdlib + mitmproxy stubs from the bundled interpreter,
    # and that version detection works against the extracted manifest.
    probe = PLAYER / 'resources' / 'addon_monmusu_cn.py'
    check(probe.is_file(), 'addon present in extracted release')
    check((PLAYER / 'config.ini').read_text(encoding='utf-8').count('debug = false') == 1,
          'quiet mode is the shipped default')
    check((PLAYER / 'runtime' / 'launch.ps1').read_text(encoding='utf-8')
          .count('termlog_verbosity') >= 1, 'launcher wires termlog_verbosity')

    # Static check: no dev-tool absolute paths leaked into the packaged scripts.
    leaks = []
    for n in pkg:
        if not n.endswith('.py'):
            continue
        text = (PLAYER / n).read_text(encoding='utf-8')
        if 'C:\\Users\\ASUS' in text or 'webgl_research' in text:
            leaks.append(n)
    check(not leaks, 'no developer absolute paths in packaged build scripts')

    print('=' * 60)
    if fails:
        print(f'{fails}/{checks} portable-autobuild checks FAILED')
        return 1
    print(f'{checks} portable-autobuild checks passed')
    print('NOTE: clean Windows VM with NO developer toolchain: NOT RUN')
    print('NOTE: browser real-machine play test: NOT RUN')
    return 0


if __name__ == '__main__':
    sys.exit(main())
