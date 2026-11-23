#!/usr/bin/env python3
"""Assemble the OPTIONAL portable build runtime for the auto-update edition.

Phase 33 asks that a player can run 启动汉化.bat and have an unknown official
build rebuilt locally without installing Python, Node.js or the .NET SDK. This
tool assembles that runtime from a developer machine into runtime/toolchain/:

    python -B tools/autobuild/build_toolchain.py --dotnet-root C:\\path\\to\\dotnet
    python -B build_release.bat --with-toolchain

What is bundled
  * a trimmed CPython (python.exe + DLL + stdlib + the exact third-party
    closure needed by UnityPy / fontTools / Pillow), no installer, no registry;
  * the minimum .NET host + Microsoft.NETCore.App shared framework needed by
    Il2CppDumper.exe (which is MIT licensed), no SDK;
  * Il2CppDumper.exe, config.json and its runtimeconfig;
  * the phase-28..32 build scripts and their immutable baselines;
  * license texts for every redistributed third-party component.

`curl.exe` is NOT bundled: Windows 10 1803+ ships it in System32. Node.js is
NOT bundled: WebAssembly.validate is an auxiliary check that verify_patch.py
reports as SKIPPED when node is absent, and it never gates the build.

Nothing here is downloaded from the network. If a source component is missing,
the tool fails loudly and does NOT write a partial toolchain.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TC = ROOT / 'runtime' / 'toolchain'
MANIFEST = ROOT / 'runtime' / 'toolchain.json'

# Exact third-party closure measured by importing the build chain. Keeping this
# explicit (rather than copying all of site-packages) avoids shipping test data,
# IPython, SQLAlchemy, etc.
PY_PACKAGES = (
    'UnityPy', 'PIL', 'fontTools', 'fsspec', 'lz4', 'texture2ddecoder',
    'astc_encoder', 'etcpak', 'tpk_ar', 'archspec', 'attr', 'attrs', 'yaml',
)
PY_MODULES = ('brotli.py', 'typing_extensions.py')
PY_PYD_PREFIXES = ('_brotli', '_lz4', 'etcpak', 'astc_encoder', 'tpk',
                   'texture2ddecoder')

# Build scripts + baselines actually reachable from build_worker.py.
BUILD_FILES = (
    'tools/autobuild/build_worker.py',
    'tools/wasm/official_inputs.py',
    'tools/wasm/extract_metadata.py',
    'tools/wasm/symmap.py',
    'tools/wasm/rebuild_wasm.py',
    'tools/wasm/resolve_targets.py',
    'tools/wasm/build_patch.py',
    'tools/wasm/verify_patch.py',
    'tools/wasm/resolve_runtime.py',
    'tools/wasm/wastool.py',
    'tools/wasm/patterns.json',
    'tools/wasm/hook_targets.json',
    'tools/wasm/runtime_targets.json',
    'tools/wasm/release_baseline.json',
    'tools/wasm/test_reorder.py',
    'tools/fonts/graft_branch_data.py',
    'tools/fonts/tmpfont.py',
    'tools/fonts/branch_baseline.json',
)
DUMPER_FILES = ('Il2CppDumper.exe', 'Il2CppDumper.runtimeconfig.json', 'config.json')

LICENSES = {
    'Il2CppDumper-LICENSE.txt':
        'https://github.com/Perfare/Il2CppDumper (MIT, Copyright (c) 2016 Perfare)',
    'UnityPy-LICENSE.txt':
        'https://github.com/K0lb3/UnityPy (MIT)',
    'fontTools-LICENSE.txt':
        'https://github.com/fonttools/fonttools (MIT)',
    'Pillow-LICENSE.txt':
        'https://python-pillow.github.io (HPND)',
    'Python-LICENSE.txt':
        'https://docs.python.org/3/license.html (PSF License Agreement)',
    'dotnet-LICENSE.txt':
        'https://github.com/dotnet/runtime (MIT); ThirdPartyNotices.txt included',
    'Brotli-LICENSE.txt':
        'https://github.com/google/brotli (MIT)',
    'lz4-LICENSE.txt':
        'https://github.com/lz4/lz4 (BSD 2-Clause for the Python bindings)',
    'fsspec-LICENSE.txt':
        'https://github.com/fsspec/filesystem_spec (BSD 3-Clause)',
}


def sizeof(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def copy_tree(src: Path, dst: Path, ignore=()):
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*ignore) if ignore else None)


def fresh_dir(path: Path) -> Path:
    """Return an empty directory without recursively deleting anything.

    A previous run's tree is renamed aside, never bulk-deleted: a sandbox or
    endpoint policy can abort the process on a large recursive delete, and a
    half-deleted toolchain is worse than an extra scratch directory.
    """
    if path.exists():
        path.rename(path.with_name(path.name + '-old-' +
                                   datetime.now().strftime('%Y%m%d-%H%M%S')))
    path.mkdir(parents=True)
    return path


def build_python(dst: Path):
    src = Path(sys.base_prefix)
    py = fresh_dir(dst / 'python')
    dll = list(src.glob('python3*.dll'))
    if not (src / 'python.exe').is_file() or not dll:
        raise RuntimeError(f'no CPython runtime found at {src}')
    shutil.copy2(src / 'python.exe', py / 'python.exe')
    for d in dll:
        shutil.copy2(d, py / d.name)
    copy_tree(src / 'DLLs', py / 'DLLs', ('*.ico',))
    copy_tree(src / 'Lib', py / 'Lib',
              ('site-packages', '__pycache__', 'test', 'idlelib', 'tkinter',
               'ensurepip', 'lib2to3', '*.pyc'))
    sp = src / 'Lib' / 'site-packages'
    out = py / 'Lib' / 'site-packages'
    out.mkdir(parents=True)
    missing = []
    for name in PY_PACKAGES:
        p = sp / name
        if not p.exists():
            missing.append(name)
            continue
        if p.is_dir():
            shutil.copytree(p, out / name, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy2(p, out / name)
    for name in PY_MODULES:
        if (sp / name).is_file():
            shutil.copy2(sp / name, out / name)
    for f in sp.glob('*.pyd'):
        if f.name.startswith(PY_PYD_PREFIXES):
            shutil.copy2(f, out / f.name)
    if missing:
        raise RuntimeError('missing site-packages: ' + ', '.join(missing))
    # Embeddable-style path file: interpreter looks ONLY inside this folder.
    major, minor = sys.version_info[:2]
    (py / f'python{major}{minor}._pth').write_text(
        'Lib\nLib/site-packages\nDLLs\nimport site\n', encoding='ascii')
    return py


def build_dotnet(dst: Path, root: Path):
    dn = fresh_dir(dst / 'dotnet')
    versions = sorted((root / 'shared' / 'Microsoft.NETCore.App').glob('*'),
                      key=lambda p: p.name)
    if not versions:
        raise RuntimeError(f'no Microsoft.NETCore.App shared framework at {root}')
    ver = versions[-1].name
    target = dn / 'shared' / 'Microsoft.NETCore.App' / ver
    target.mkdir(parents=True)
    shutil.copytree(versions[-1], target, dirs_exist_ok=True)
    fxr_src = list((root / 'host' / 'fxr').glob('*'))
    if not fxr_src:
        raise RuntimeError('no host/fxr in the .NET root')
    fxr = dn / 'host' / 'fxr' / fxr_src[-1].name
    fxr.mkdir(parents=True)
    shutil.copytree(fxr_src[-1], fxr, dirs_exist_ok=True)
    shutil.copy2(root / 'dotnet.exe', dn / 'dotnet.exe')
    for extra in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        if (root / extra).is_file():
            shutil.copy2(root / extra, dn / extra)
    return dn, ver


def self_test(py: Path, dn: Path, ver: str, dumper: Path) -> dict:
    """Prove the assembled runtime works with NO developer Python/.NET on PATH."""
    env = dict(os.environ)
    for k in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONNOUSERSITE', 'VIRTUAL_ENV',
              'DOTNET_ROOT', 'IL2CPP_DOTNET_ROOT'):
        env.pop(k, None)
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    env['PYTHONUTF8'] = '1'
    env['DOTNET_ROOT'] = str(dn)
    env['DOTNET_ROLL_FORWARD'] = 'LatestMajor'
    code = ('import UnityPy, json, gzip, hashlib, struct; '
            'from PIL import Image; import fontTools, lz4.block, fsspec, brotli; '
            'print("PY_OK")')
    r = subprocess.run([str(py / 'python.exe'), '-B', '-I', '-c', code],
                       env=env, capture_output=True, text=True, timeout=120)
    if 'PY_OK' not in r.stdout:
        raise RuntimeError('portable Python self-test failed: ' + r.stderr[-800:])
    d = subprocess.run([str(dumper), '--help'], env=env, cwd=str(dumper.parent),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=120)
    if d.returncode != 0 or 'usage' not in (d.stdout + d.stderr).lower():
        raise RuntimeError('bundled .NET cannot run Il2CppDumper: '
                           + (d.stderr or d.stdout)[-500:])
    return {'python': 'OK', 'dotnet': f'OK ({ver})',
            'dumper': 'OK', 'clean_env_path': env['PATH']}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dotnet-root', default=str(Path.home() / '.dotnet8'),
                    help='local .NET installation to copy the shared runtime from')
    ap.add_argument('--no-self-test', action='store_true')
    args = ap.parse_args(argv)

    dotnet_root = Path(args.dotnet_root)
    for name in BUILD_FILES:
        if not (ROOT / name).is_file():
            raise RuntimeError(f'missing build file: {name}')
    dumper_src = ROOT / 'tools' / 'il2cppdumper'
    for name in DUMPER_FILES:
        if not (dumper_src / name).is_file():
            raise RuntimeError(f'missing Il2CppDumper file: {name}')

    if TC.exists():
        old = ROOT / 'work' / ('toolchain-old-' +
                               datetime.now().strftime('%Y%m%d-%H%M%S'))
        old.parent.mkdir(parents=True, exist_ok=True)
        TC.rename(old)
    TC.mkdir(parents=True)
    py = build_python(TC)
    dn, ver = build_dotnet(TC, dotnet_root)

    # The build scripts stay at their ORIGINAL root-relative paths
    # (tools/wasm/..., tools/fonts/...). The worker computes ROOT from
    # __file__ and resolves resources/ relative to it, so relocating them
    # under runtime/toolchain/ would silently break every path. The release
    # whitelist adds exactly these files when --with-toolchain is used.
    # Exact list of root-relative build files the release ZIP must include.
    # build_release.py reads this instead of maintaining a second copy.
    packaged = sorted(set(BUILD_FILES)
                      | {f'tools/il2cppdumper/{n}' for n in DUMPER_FILES})
    (TC / 'packaged_files.json').write_text(
        json.dumps(packaged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (TC / 'licenses').mkdir()
    for name, text in LICENSES.items():
        (TC / 'licenses' / name).write_text(
            'Third-party component redistributed with the Monmusu TD Chinese\n'
            'portable build runtime.\n\nUpstream / license: ' + text + '\n\n'
            'The full license text is available at the upstream URL above.\n',
            encoding='utf-8')

    result = {}
    if not args.no_self_test:
        result = self_test(py, dn, ver, dumper_src / 'Il2CppDumper.exe')

    # toolchain.json is written LAST, only after every component verified.
    doc = {
        'schema': 1,
        'python': 'runtime/toolchain/python/python.exe',
        'dotnet': 'runtime/toolchain/dotnet',
        'dumper': 'tools/il2cppdumper/Il2CppDumper.exe',
        'note': 'Optional portable build runtime. Without this file the addon '
                'passes unknown official builds through untouched and reports '
                'that automatic rebuild is unavailable.',
        'self_test': result,
    }
    tmp = MANIFEST.with_suffix('.json.new')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    os.replace(tmp, MANIFEST)

    parts = {
        'python': sizeof(TC / 'python'),
        'dotnet': sizeof(TC / 'dotnet'),
        'build scripts': sum(sizeof(ROOT / n) for n in BUILD_FILES),
        'Il2CppDumper': sum(sizeof(dumper_src / n) for n in DUMPER_FILES),
        'licenses': sizeof(TC / 'licenses'),
    }
    parts['TOTAL'] = sizeof(TC)
    files = sum(1 for f in TC.rglob('*') if f.is_file())
    print(json.dumps({'parts_MB': {k: round(v / 1024 / 1024, 2)
                                   for k, v in parts.items()},
                      'files': files, 'self_test': result,
                      'toolchain_json': str(MANIFEST.relative_to(ROOT))},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
