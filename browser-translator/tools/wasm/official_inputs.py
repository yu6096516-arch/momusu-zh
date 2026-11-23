#!/usr/bin/env python3
"""Manage the controlled official inputs under `tools/wasm/inputs/`.

Two entry points, matching the only two supported developer invocations:

  ensure_local()          offline default. Reuses valid metadata; regenerates
                          it from the LOCAL raw Wasm/Data when it is missing.
                          Never checks the network, never checks a version,
                          never downloads anything.

  update(version)         download exactly one explicit official build, derive
                          ALL IL2CPP metadata from those files, then atomically
                          switch `inputs/`. A partial/failed download never
                          touches the existing usable inputs.

Only the files genuinely needed for Wasm patching and IL2CPP metadata
extraction are fetched: ntd.wasm.gz, ntd.data.gz, ntd.loader.js. No
AssetBundles, audio, illustrations, ablist.json or framework JS.

No version auto-discovery, no CDN scanning, no update detector, no cache DB.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
# The inputs root can be redirected so an *automatic* rebuild never overwrites the
# verified developer inputs (tools/autobuild/ does exactly that). Both the inputs
# and the scratch build tree are redirectable: a candidate build must stay inside
# its own temporary directory and never touch tools/wasm/inputs/ or build/wasm/.
INPUTS_ROOT = Path(os.environ.get('MONMUSU_INPUTS_ROOT') or HERE)
INPUTS = INPUTS_ROOT / 'inputs'          # branch 0 (DMM), historical path
BUILD = Path(os.environ.get('MONMUSU_BUILD_ROOT') or (ROOT / 'build' / 'wasm'))
STAGE = BUILD / 'inputs_stage'
BRANCHES = ('0', '1')                    # 0 = DMM, 1 = FANZA


def auto_build_guard():
    """Fail closed if an automated rebuild is about to touch developer inputs.

    `MONMUSU_AUTO_BUILD=1` is set by tools/autobuild/build_worker.py. Requiring a
    redirect here means a forgotten/mis-ordered environment variable can at worst
    abort the build; it can never silently replace the verified release inputs.
    """
    if os.environ.get('MONMUSU_AUTO_BUILD') != '1':
        return
    need(INPUTS_ROOT.resolve() != HERE.resolve(),
         'auto-build requires MONMUSU_INPUTS_ROOT to point outside tools/wasm/ '
         '(refusing to overwrite the verified developer inputs)')
    need(BUILD.resolve() != (ROOT / 'build' / 'wasm').resolve(),
         'auto-build requires MONMUSU_BUILD_ROOT to point to its own scratch tree')


def inputs_dir(branch: str = '0') -> Path:
    """One input set per resource branch, so derived files can never mix.

    Branch 0 keeps the historical `tools/wasm/inputs/` path; branch 1 gets its
    own sibling directory. A rebuild always reads one directory end to end.
    """
    validate_branch(branch)
    return INPUTS if branch == '0' else INPUTS_ROOT / f'inputs-{branch}'


def stage_dir(branch: str = '0') -> Path:
    return BUILD / ('inputs_stage' if branch == '0'
                    else f'inputs_stage-{branch}')

DUMPER_DIR = ROOT / 'tools' / 'il2cppdumper'
DUMPER = DUMPER_DIR / 'Il2CppDumper.exe'

HOST = 'https://assets.game-monmusu-td.net'
# Full prefix in front of `/<version>/<branch>/Build/`. Overridable only so the
# update flow can be exercised against a local server in tests.
ARL_BASE = os.environ.get('MONMUSU_ARL_BASE') or (HOST + '/web/arl')
DOWNLOAD_FILES = ('ntd.wasm.gz', 'ntd.data.gz', 'ntd.loader.js')
VERSION_RE = re.compile(r'^\d{10,16}$')

REQUIRED_METADATA = ('script.json', 'dump.cs', 'il2cpp.h',
                     'slot2name.json', 'global-metadata.dat')
DUMPER_OUTPUTS = ('script.json', 'dump.cs', 'il2cpp.h', 'stringliteral.json')


class InputError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise InputError(msg)


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for c in iter(lambda: fh.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def log(msg):
    print(f'[official_inputs] {msg}')


# --------------------------------------------------------------------------
# version / URL
# --------------------------------------------------------------------------
def validate_version(version: str) -> str:
    v = (version or '').strip()
    if not VERSION_RE.fullmatch(v):
        raise InputError(
            f'invalid official version {version!r}. Expected a numeric build id '
            f'(e.g. 202611021000). Usage:\n'
            f'  rebuild_wasm.bat\n'
            f'  rebuild_wasm.bat --update 202611021000')
    return v


def validate_branch(branch: str) -> str:
    """0 = DMM, 1 = FANZA. Nothing else is accepted."""
    b = ('0' if branch is None else str(branch)).strip()
    if b not in BRANCHES:
        raise InputError(
            f'invalid resource branch {branch!r}. Allowed values: '
            f'0 (DMM, default) or 1 (FANZA). Usage:\n'
            f'  rebuild_wasm.bat --update 202611021000\n'
            f'  rebuild_wasm.bat --update 202611021000 1')
    return b


def build_url(version: str, branch: str = '0') -> str:
    return f'{ARL_BASE}/{version}/{validate_branch(branch)}/Build/'


# --------------------------------------------------------------------------
# download (only called by explicit --update)
# --------------------------------------------------------------------------
def curl(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ['curl', '-fSL', '--retry', '2', '--retry-delay', '2',
         '--connect-timeout', '30', '--max-time', '900',
         '-o', str(dest), url],
        capture_output=True, text=True)
    need(proc.returncode == 0,
         f'download failed for {url}\n{proc.stderr.strip() or proc.stdout.strip()}')
    need(dest.is_file() and dest.stat().st_size > 0,
         f'download produced an empty file: {url}')


def validate_downloads(dl: Path):
    """Prove every downloaded file is a complete, well-formed official asset."""
    for f in DOWNLOAD_FILES:
        p = dl / f
        need(p.is_file() and p.stat().st_size > 0, f'missing download {f}')
    wg, dg = dl / 'ntd.wasm.gz', dl / 'ntd.data.gz'
    need(wg.read_bytes()[:2] == b'\x1f\x8b', 'ntd.wasm.gz is not gzip')
    need(dg.read_bytes()[:2] == b'\x1f\x8b', 'ntd.data.gz is not gzip')
    try:
        wasm = gzip.decompress(wg.read_bytes())
        data = gzip.decompress(dg.read_bytes())
    except Exception as e:
        raise InputError(f'truncated/corrupt gzip download: {type(e).__name__}: {e}')
    need(wasm[:8] == b'\x00asm\x01\x00\x00\x00',
         'decompressed ntd.wasm.gz has no Wasm magic')
    need(data[:16] == b'UnityWebData1.0\x00',
         'decompressed ntd.data.gz has no UnityWebData1.0 header')
    loader = (dl / 'ntd.loader.js').read_bytes()
    need(b'createUnityInstance' in loader,
         'ntd.loader.js does not look like the official Unity loader')
    return wasm, data


# --------------------------------------------------------------------------
# metadata generation (no network; uses the supplied wasm + data only)
# --------------------------------------------------------------------------
def find_dotnet_root() -> str:
    cands = []
    if os.environ.get('IL2CPP_DOTNET_ROOT'):
        cands.append(Path(os.environ['IL2CPP_DOTNET_ROOT']))
    if os.environ.get('DOTNET_ROOT'):
        cands.append(Path(os.environ['DOTNET_ROOT']))
    cands.append(Path.home() / '.dotnet8')
    which = shutil.which('dotnet')
    if which:
        cands.append(Path(which).parent)
    for c in cands:
        if (c / 'dotnet.exe').is_file() or (c / 'dotnet').is_file():
            return str(c)
    raise InputError(
        'Il2CppDumper requires a .NET 6+ runtime but none was found. '
        'Set IL2CPP_DOTNET_ROOT (or DOTNET_ROOT) to a folder containing '
        'dotnet.exe. Refusing to reuse metadata from a different build.')


def extract_global_metadata(data_path: Path, stage: Path) -> Path:
    # Unique scratch dir OUTSIDE the stage: the container may hold hundreds of
    # entries, and we never recursively delete in the middle of a build. Keeping
    # it out of stage/ also means atomic_switch cannot carry it into inputs/.
    extracted = stage.parent / f'_extracted-{os.getpid()}-{int(time.time())}'
    proc = subprocess.run(
        [sys.executable, '-B', str(HERE / 'extract_metadata.py'),
         str(data_path), str(extracted)],
        capture_output=True, text=True, cwd=str(ROOT))
    need(proc.returncode == 0,
         f'extract_metadata.py failed: {proc.stderr.strip() or proc.stdout.strip()}')
    src = extracted / 'global-metadata.dat'
    need(src.is_file() and src.stat().st_size > 1024,
         'global-metadata.dat was not extracted from the official Data')
    dst = stage / 'global-metadata.dat'
    shutil.copy2(src, dst)
    return dst


def run_dumper(wasm: Path, metadata: Path, stage: Path):
    need(DUMPER.is_file(),
         f'Il2CppDumper is missing at {DUMPER}. Cannot generate IL2CPP metadata '
         f'for a new official build; refusing to reuse old-version metadata.')
    dotnet_root = find_dotnet_root()
    # Unique scratch dir OUTSIDE the stage: Il2CppDumper emits ~130 DummyDll
    # files. Never bulk-delete them here (a sandbox/policy hook can abort the
    # whole build on a large recursive delete), and never carry them into
    # inputs/ via atomic_switch. Each run gets a fresh directory instead.
    out = stage.parent / f'_dump-{os.getpid()}-{int(time.time())}'
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['DOTNET_ROOT'] = dotnet_root
    env['DOTNET_ROLL_FORWARD'] = 'LatestMajor'
    proc = subprocess.run(
        [str(DUMPER), str(wasm), str(metadata), str(out)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=env, cwd=str(DUMPER_DIR), timeout=900)
    # Il2CppDumper ends with Console.ReadKey(), which throws under redirected
    # stdin AFTER all files are written. Verify the outputs instead of the exit
    # code, and never silently accept missing/invalid metadata.
    for f in DUMPER_OUTPUTS:
        need((out / f).is_file() and (out / f).stat().st_size > 1024,
             f'Il2CppDumper did not produce {f}. output:\n'
             f'{(proc.stdout or "")[-1500:]}\n{(proc.stderr or "")[-1500:]}')
    head = (out / 'script.json').read_bytes()[:4096]
    need(b'ScriptMethod' in head, 'script.json has no ScriptMethod array')
    dcs_head = (out / 'dump.cs').read_bytes()[:200000]
    need(b'// RVA:' in dcs_head, 'dump.cs has no RVA annotations')
    log(f'Il2CppDumper produced all metadata (exit={proc.returncode}, '
        f'tail ReadKey diagnostic ignored after output verification)')
    for f in DUMPER_OUTPUTS:
        shutil.copy2(out / f, stage / f)
    # Deliberately left in place: the scratch tree belongs to this job and is
    # discarded as a whole. No recursive delete in the middle of a build.
    return dotnet_root


def build_slotmap(stage: Path):
    sys.path.insert(0, str(HERE))
    import symmap
    m = symmap.build_map(str(stage / 'dump.cs'))
    need(len(m) > 10000, f'slot2name.json looks incomplete ({len(m)} entries)')
    (stage / 'slot2name.json').write_text(
        json.dumps(m, ensure_ascii=False), encoding='utf-8')
    return len(m)


def write_build_info(stage: Path, version: str, source: str, extra=None,
                     branch: str = '0'):
    branch = validate_branch(branch)
    files = {}
    for p in sorted(stage.iterdir()):
        if p.is_file() and not p.name.startswith('_'):
            files[p.name] = {'bytes': p.stat().st_size, 'sha256': sha_file(p)}
    info = {
        'version': version,
        'game_build': f'{version}/{branch}',
        'branch': branch,
        'source': source,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'official_build_url': build_url(version, branch),
        'downloaded_official_files': list(DOWNLOAD_FILES),
        'files': files,
    }
    if extra:
        info.update(extra)
    (stage / 'build_info.json').write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return info


def build_stage_from(wasm_bytes: bytes, data_bytes: bytes, dl: Path,
                     version: str, source: str,
                     branch: str = '0') -> Path:
    """Populate this branch's stage with raw inputs + fresh metadata. No network."""
    branch = validate_branch(branch)
    STAGE = stage_dir(branch)
    if STAGE.exists():
        # A retry may find a previous stage. Give this attempt its own directory
        # instead of recursively deleting the old one (which can contain the
        # dumper's ~130 generated files). The old one is just scratch.
        STAGE = STAGE.with_name(STAGE.name + f'-{os.getpid()}-{int(time.time())}')
    STAGE.mkdir(parents=True)
    # Canonical names inside inputs/: the Wasm archive is kept under the
    # ntd.orig.* prefix so ensure_local() can find it again next time.
    shutil.copy2(dl / 'ntd.wasm.gz', STAGE / 'ntd.orig.wasm.gz')
    shutil.copy2(dl / 'ntd.data.gz', STAGE / 'ntd.data.gz')
    shutil.copy2(dl / 'ntd.loader.js', STAGE / 'ntd.loader.js')
    (STAGE / 'ntd.orig.wasm').write_bytes(wasm_bytes)
    (STAGE / 'ntd.data').write_bytes(data_bytes)
    log(f'official Wasm {len(wasm_bytes)} B, Data {len(data_bytes)} B')

    metadata = extract_global_metadata(STAGE / 'ntd.data', STAGE)
    log(f'global-metadata.dat {metadata.stat().st_size} B extracted from official Data')
    dotnet_root = run_dumper(STAGE / 'ntd.orig.wasm', metadata, STAGE)
    slots = build_slotmap(STAGE)
    log(f'slot2name.json rebuilt from the SAME build dump.cs ({slots} slots)')
    # The unpacked Data is large and only needed during this step.
    (STAGE / 'ntd.data').unlink(missing_ok=True)
    write_build_info(STAGE, version, source,
                     {'dotnet_root': dotnet_root, 'slot_map_entries': slots},
                     branch=branch)
    return STAGE


def atomic_switch(stage: Path, branch: str = '0'):
    """Directory-level switch: either the whole new inputs set lands, or none."""
    branch = validate_branch(branch)
    INPUTS = inputs_dir(branch)
    # With MONMUSU_INPUTS_ROOT redirected, INPUTS lives in a fresh job tree
    # whose parent may not exist yet. Directory.rename does not create parents.
    INPUTS.parent.mkdir(parents=True, exist_ok=True)
    stamp = f'{os.getpid()}-{int(time.time())}'
    backup = BUILD / (f'inputs_previous-{stamp}' if branch == '0'
                      else f'inputs_previous-{branch}-{stamp}')
    had_old = INPUTS.exists()
    if had_old:
        INPUTS.rename(backup)
    try:
        stage.rename(INPUTS)
    except Exception:
        if had_old and backup.exists() and not INPUTS.exists():
            backup.rename(INPUTS)
        raise
    # The previous inputs are kept as a rollback copy. Do NOT recursively
    # delete them here: a failed switch must be recoverable, and a bulk delete
    # in the middle of a build can be aborted by sandbox policy.
    log(f'{INPUTS.name}/ switched atomically to the new official build')


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------
def update(version: str, branch: str = '0') -> dict:
    auto_build_guard()
    v = validate_version(version)
    branch = validate_branch(branch)
    url = build_url(v, branch)
    log(f'downloading official build {v}/{branch} from {url}')
    dl = BUILD / ('download_tmp' if branch == '0'
                  else f'download_tmp-{branch}')
    if dl.exists():
        shutil.rmtree(dl)
    dl.mkdir(parents=True)
    # Nothing under inputs/ is touched until EVERY file is downloaded,
    # decompressed and validated.
    for f in DOWNLOAD_FILES:
        curl(url + f, dl / f)
        log(f'  {f}: {(dl / f).stat().st_size} B')
    wasm, data = validate_downloads(dl)
    stage = build_stage_from(wasm, data, dl, v, 'download', branch)
    atomic_switch(stage, branch)
    return json.loads((inputs_dir(branch) / 'build_info.json')
                      .read_text(encoding='utf-8'))


def local_metadata_valid(branch: str = '0') -> bool:
    INPUTS = inputs_dir(branch)
    info_p = INPUTS / 'build_info.json'
    wasm = INPUTS / 'ntd.orig.wasm'
    if not info_p.is_file() or not wasm.is_file():
        return False
    try:
        info = json.loads(info_p.read_text(encoding='utf-8'))
        if info.get('files', {}).get('ntd.orig.wasm', {}).get('sha256') != sha_file(wasm):
            return False
    except Exception:
        return False
    for f in REQUIRED_METADATA:
        p = INPUTS / f
        if not p.is_file() or p.stat().st_size < 1024:
            return False
    try:
        head = (INPUTS / 'script.json').read_bytes()[:4096]
        if b'ScriptMethod' not in head:
            return False
    except Exception:
        return False
    return True


def ensure_local(branch: str = '0') -> dict:
    """Offline default. No network, no version check, no download."""
    auto_build_guard()
    branch = validate_branch(branch)
    INPUTS = inputs_dir(branch)
    if not INPUTS.is_dir():
        raise InputError(
            f'missing local inputs directory {INPUTS}. Run:\n'
            f'  rebuild_wasm.bat --update <official version>')
    wasm_gz = INPUTS / 'ntd.orig.wasm.gz'
    data_gz = INPUTS / 'ntd.data.gz'
    for p in (wasm_gz, data_gz):
        if not p.is_file():
            raise InputError(
                f'missing local official resource {p.name}. Local rebuild is '
                f'offline-only; run:\n'
                f'  rebuild_wasm.bat --update <official version>')
    wasm = INPUTS / 'ntd.orig.wasm'
    if not wasm.is_file():
        log('ntd.orig.wasm missing -> decompressing local ntd.orig.wasm.gz')
        b = gzip.decompress(wasm_gz.read_bytes())
        need(b[:8] == b'\x00asm\x01\x00\x00\x00', 'local ntd.orig.wasm.gz is invalid')
        wasm.write_bytes(b)

    if local_metadata_valid(branch):
        info = json.loads((INPUTS / 'build_info.json').read_text(encoding='utf-8'))
        # build_info lists the unpacked Data which we intentionally drop.
        log(f'reusing valid metadata for build {info.get("game_build")} '
            f'(Wasm sha {sha_file(wasm)[:12]}…) - no regeneration needed')
        return {'mode': 'local-reuse', 'info': info}

    log('metadata missing/stale -> regenerating from LOCAL official resources')
    dl = BUILD / ('local_raw' if branch == '0' else f'local_raw-{branch}')
    if dl.exists():
        shutil.rmtree(dl)
    dl.mkdir(parents=True)
    shutil.copy2(wasm_gz, dl / 'ntd.wasm.gz')
    shutil.copy2(data_gz, dl / 'ntd.data.gz')
    need((INPUTS / 'ntd.loader.js').is_file(),
         'missing local ntd.loader.js. Run:\n'
         '  rebuild_wasm.bat --update <official version>')
    shutil.copy2(INPUTS / 'ntd.loader.js', dl / 'ntd.loader.js')
    wasm_b, data_b = validate_downloads(dl)
    version = _version_from_info(branch)
    stage = build_stage_from(wasm_b, data_b, dl, version,
                             'local-regeneration', branch)
    atomic_switch(stage, branch)
    info = json.loads((INPUTS / 'build_info.json').read_text(encoding='utf-8'))
    log('metadata regenerated locally and bound to the current Wasm hash')
    return {'mode': 'local-regenerated', 'info': info}


def _version_from_info(branch: str = '0') -> str:
    p = inputs_dir(branch) / 'build_info.json'
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding='utf-8'))['version']
        except Exception:
            pass
    # Fall back to the pinned release baseline version (still offline, no
    # network lookup). This only labels the build_info file.
    baseline = HERE / 'release_baseline.json'
    if baseline.is_file():
        try:
            return json.loads(baseline.read_text(encoding='utf-8'))['version']
        except Exception:
            pass
    return '202611021000'


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--update', metavar='VERSION', default=None)
    ap.add_argument('--branch', default='0', choices=list(BRANCHES),
                    help='resource branch: 0 = DMM (default), 1 = FANZA')
    ap.add_argument('--ensure-local', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.update is not None:
            info = update(args.update, args.branch)
            print('OK update', info['game_build'])
            return 0
        if args.ensure_local:
            r = ensure_local(args.branch)
            print('OK', r['mode'], r['info']['game_build'])
            return 0
        ap.print_help()
        return 1
    except InputError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    except Exception as e:                                     # noqa: BLE001
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
