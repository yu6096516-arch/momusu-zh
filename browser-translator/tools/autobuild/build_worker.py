"""Rebuild the Chinese patch for one official build, offline from the addon's view.

This is the process the addon spawns when the game asks for a build id we do not
ship yet. It never runs inside mitmproxy: a failure here must not take the proxy
down, and the download + IL2CPP dump take minutes.

    python -B tools/autobuild/build_worker.py --version 202611161000 --branch 1 \
        --work work/autobuild/202611161000-1

Stages, in order. Every one of them must pass before anything is activated:

  1 download   the three official files for exactly that <version>/<branch>
  2 metadata   global-metadata.dat -> Il2CppDumper -> script.json / dump.cs
  3 patch      9 hooks located dynamically, patch built, structurally verified
  4 runtime    element-table slots, icall literals, field offsets resolved from
               *this* build and bound to the SHA-256 of the patch
  5 font data  the cn25 payload grafted into the new branch's own ntd.data,
               gated on the recorded object identities
  6 activate   swap files + version_paths atomically, with a rollback copy

Exit code 0 means "candidate is in place"; 2 means "candidate rejected, old
release untouched". Both cases write result.json for the addon to read.

What this cannot do: prove the patched game behaves correctly. Activation only
means the structure matched. `verification` therefore says "static", never
"browser", until a human plays it.

The worker deliberately does NOT call `build_release.py --loader-only`: that
path reads loader_base.js and the five engine sources, which are developer-only
and absent from the player ZIP. Instead it rewrites the RUNTIME_JSON block that
is already embedded in resources/ntd.loader.js and re-checks every branch's
SHA-256 binding itself.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WASM_DIR = ROOT / 'tools' / 'wasm'
BUILD_DIR = ROOT / 'resources'
ASSETS = BUILD_DIR / 'assets.json'
LOADER = BUILD_DIR / 'ntd.loader.js'
TOOLS = ROOT / 'tools'
MARK_BEGIN = '/*RUNTIME_JSON_BEGIN*/'
MARK_END = '/*RUNTIME_JSON_END*/'

# Files that activation may replace. Anything else is left alone.
ACTIVATION_FILES = ('ntd.patched.wasm.gz', 'ntd.patched.1.wasm.gz',
                    'ntd.data.gz', 'ntd.data.1.gz', 'ntd.loader.js', 'assets.json')


class StageError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise StageError(msg)


def log(msg):
    print(f'[worker] {msg}', flush=True)


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run(argv, log_path: Path, label: str, timeout=3600):
    """Run a helper tool; its output goes to the job log, not the console."""
    with open(log_path, 'ab') as fh:
        fh.write(f'\n===== {label}: {" ".join(str(a) for a in argv)} =====\n'.encode())
        fh.flush()
        proc = subprocess.run([str(a) for a in argv], cwd=str(ROOT),
                              stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                              timeout=timeout)
        fh.write(f'===== {label}: exit {proc.returncode} =====\n'.encode())
    if proc.returncode != 0:
        tail = log_path.read_bytes()[-2500:].decode('utf-8', 'replace')
        raise StageError(f'{label} failed (exit {proc.returncode}):\n{tail}')
    return proc.returncode


def python_cmd(*args):
    return [sys.executable, '-B', *args]


# --------------------------------------------------------------------------- stages
def stage_download(version, branch, work, log_path, arl_base=None):
    """Download one official build into a scratch tree that belongs to this job.

    Every redirect MUST be in the environment before `official_inputs` is
    imported: that module resolves its inputs/build roots at import time. Setting
    them afterwards is a silent no-op and would download straight over the
    verified developer inputs (tools/wasm/inputs/). `auto_build_guard()` in that
    module is the second line of defence and fails closed if we ever regress.
    """
    root = work / 'inputs-root'
    scratch = work / 'build'
    os.environ['MONMUSU_AUTO_BUILD'] = '1'
    os.environ['MONMUSU_INPUTS_ROOT'] = str(root)
    os.environ['MONMUSU_BUILD_ROOT'] = str(scratch)
    if arl_base:
        os.environ['MONMUSU_ARL_BASE'] = arl_base
    # Child processes (rebuild_wasm.py, resolve_runtime.py, ...) inherit this
    # environment, so the whole pipeline stays inside the job's scratch tree.
    sys.modules.pop('official_inputs', None)
    sys.path.insert(0, str(WASM_DIR))
    import official_inputs                                   # noqa: E402

    info = official_inputs.update(version, branch)
    inputs = official_inputs.inputs_dir(branch)
    for name in ('script.json', 'dump.cs', 'slot2name.json',
                 'ntd.orig.wasm', 'ntd.data.gz', 'build_info.json'):
        need((inputs / name).is_file(), f'metadata missing after download: {name}')
    need(info.get('game_build') == f'{version}/{branch}',
         f'download reported {info.get("game_build")}, expected {version}/{branch}')
    need(inputs.resolve().is_relative_to(root.resolve()),
         'download escaped the job scratch tree')
    return inputs


def stage_patch(version, branch, inputs, work, log_path):
    """Run the phase-28..32 pipeline. Version-specific oracle tests are skipped.

    `test_runtime_config.py` asserts the *known* release's slot numbers; it is a
    regression oracle for the shipped build, not a property of an arbitrary new
    build, so running it here would either fail always or approve anything. The
    structural checks that do generalise (hook resolution, Wasm typing, patch
    verification, runtime-config resolution) all run inside rebuild_wasm.
    """
    out = work / 'stage'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    args = [WASM_DIR / 'rebuild_wasm.py', '--branch', branch,
            '--wasm', inputs / 'ntd.orig.wasm',
            '--script-json', inputs / 'script.json',
            '--slot-map', inputs / 'slot2name.json',
            '--dump', inputs / 'dump.cs',
            '--out-dir', out,
            '--game-build', f'{version}/{branch}',
            '--reference', work / 'no-reference.wasm',   # absent on purpose
            '--skip-tests']
    run(python_cmd(*args), log_path, 'patch pipeline')
    resolved = json.loads((out / 'resolved_targets.json').read_text(encoding='utf-8'))
    need(resolved.get('status') == 'PASS', 'hook resolution did not pass')
    hooks = resolved.get('targets') or []
    need(len(hooks) == 9, f'expected 9 hooks, got {len(hooks)}')
    config = json.loads((out / 'runtime_config.json').read_text(encoding='utf-8'))
    compat = config.get('compat') or {}
    need(compat.get('publishable') is True,
         'runtime config has unresolved bindings: '
         + ', '.join(f.get('id', '?') for f in compat.get('failures', [])))
    need(str(config.get('branch')) == str(branch), 'runtime config branch mismatch')
    stem = 'ntd.patched' if branch == '0' else f'ntd.patched.{branch}'
    wasm = out / f'{stem}.wasm'
    wasm_gz = out / f'{stem}.wasm.gz'
    need(wasm.is_file() and wasm_gz.is_file(), 'patched Wasm artifacts missing')
    need(gzip.decompress(wasm_gz.read_bytes()) == wasm.read_bytes(),
         'staged Wasm gzip does not match the plain Wasm')
    need(sha256(wasm) == config['wasm']['sha256'],
         'runtime config is not bound to the Wasm that was just built')
    return out, wasm_gz, config, resolved


def stage_font_data(branch, inputs, stage, work, log_path):
    """Graft the cn25 payload into the new build's own data container.

    This is the one stage that can legitimately refuse: `graft_branch_data.py`
    verifies that the target container still holds the exact objects (by
    SHA-256) the payload was derived from. A new official build that re-packaged
    the font is therefore *not* silently overwritten with an old font.

    `--out` is the PLAIN container path. graft_branch_data.py writes that file
    plus `<out>.gz`; the gzip is the artifact the addon actually serves. Passing
    a `.gz` name as `--out` would put a raw UnityWebData container into
    resources/ntd.data.gz and break the branch.
    """
    raw = work / 'official.data'
    with gzip.open(inputs / 'ntd.data.gz', 'rb') as fh:
        raw.write_bytes(fh.read())
    plain = stage / ('ntd.data' if branch == '0' else f'ntd.data.{branch}')
    out = plain.with_name(plain.name + '.gz')
    source = BUILD_DIR / ('ntd.data.gz' if branch == '0'
                          else f'ntd.data.{branch}.gz')
    run(python_cmd(ROOT / 'tools' / 'fonts' / 'graft_branch_data.py',
                   '--official', raw, '--source', source, '--out', plain),
        log_path, 'font payload graft')
    report = json.loads((stage / 'graft_report.json').read_text(encoding='utf-8'))
    need(report.get('status') == 'PASS', 'graft report is not PASS')
    need(out.is_file(), 'graft did not produce the gzip container')
    need(gzip.decompress(out.read_bytes())[:15] == b'UnityWebData1.0',
         'grafted gzip is not a UnityWebData container')
    return out, report


# ----------------------------------------------------------------------- activation
def read_manifest():
    return json.loads(ASSETS.read_text(encoding='utf-8'))


def released(spec: dict) -> Path:
    """Absolute path of a declared asset. Always resolve relative to BUILD_DIR,
    never to a hard-coded ROOT, so the gate checks the bytes that were ACTUALLY
    swapped in (and can be pointed at a copy in tests).
    """
    rel = Path(spec['file'])
    return rel if rel.is_absolute() else BUILD_DIR / rel.name


def write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + '.act')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def loader_config(loader_text: str) -> tuple[dict, int, int]:
    i, j = loader_text.find(MARK_BEGIN), loader_text.find(MARK_END)
    need(i >= 0 and j > i, 'resources/ntd.loader.js has no RUNTIME_JSON block')
    return json.loads(loader_text[i + len(MARK_BEGIN):j]), i, j


def patch_loader(branch: str, cfg: dict) -> bytes:
    """Replace only branch <branch> of the embedded runtime config.

    Keeping the rest of the loader byte-for-byte avoids depending on
    loader_base.js and the five engine sources, which are developer-only files
    and are deliberately NOT part of the player ZIP.
    """
    text = LOADER.read_text(encoding='utf-8')
    bundle, i, j = loader_config(text)
    branches = bundle.setdefault('branches', {})
    branches[str(branch)] = cfg
    ordered = {k: branches[k] for k in sorted(branches)}
    body = json.dumps({'schema': bundle.get('schema', 1), 'branches': ordered},
                      ensure_ascii=False, indent=1)
    return (text[:i + len(MARK_BEGIN)] + body + text[j:]).encode('utf-8')


def verify_bindings(manifest: dict, branch: str, new_cfg: dict):
    """Release gate, re-implemented without any developer-only source file."""
    text = LOADER.read_text(encoding='utf-8')
    bundle, _i, _j = loader_config(text)
    branches = bundle.get('branches') or {}
    checked = []
    for spec in manifest['assets']:
        if spec['name'] != 'ntd.wasm.gz':
            continue
        b = str(spec.get('branch'))
        entry = branches.get(b)
        need(entry is not None, f'branch {b}: no runtime config entry in the loader')
        need((entry.get('compat') or {}).get('publishable') is True,
             f'branch {b}: runtime config has UNRESOLVED bindings')
        import hashlib
        shipped = hashlib.sha256(
            gzip.decompress(released(spec).read_bytes())).hexdigest()
        bound = (entry.get('wasm') or {}).get('sha256')
        need(shipped == bound,
             f'branch {b}: loader config bound to wasm {str(bound)[:16]} '
             f'but packaged wasm is {shipped[:16]}')
        checked.append(b)
    need(str(branch) in checked, f'branch {branch} missing from the loader config')
    need(branches.get(str(branch), {}).get('wasm', {}).get('sha256')
         == new_cfg['wasm']['sha256'], 'new branch config was not written')
    for spec in manifest['assets']:
        if spec['name'] != 'ntd.data.gz':
            continue
        p = released(spec)
        need(p.is_file() and gzip.decompress(p.read_bytes())[:15] == b'UnityWebData1.0',
             f'{spec["file"]} is not a UnityWebData container')
    return checked


def activate(version, branch, stage, work, log_path, wasm_gz, data_gz, cfg):
    """Swap the new artifacts in, or restore everything and re-raise.

    Order matters: files first, manifest last. Every replaced file is backed up
    first, and any exception restores all of them, so a failed activation can
    never leave a half-new release behind.
    """
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = work / f'backup-{stamp}'
    backup.mkdir(parents=True)

    stem = 'ntd.patched' if branch == '0' else f'ntd.patched.{branch}'
    wasm_name = f'{stem}.wasm.gz'
    data_name = 'ntd.data.gz' if branch == '0' else f'ntd.data.{branch}.gz'
    need(wasm_gz.is_file() and data_gz.is_file(), 'staged artifacts missing')

    doc = read_manifest()
    paths = doc['version_paths']
    prefix = paths.get(str(branch), '')
    head, _, tail = prefix.partition(f'/{branch}/Build/')
    need(head and tail == '', f'cannot derive the prefix for branch {branch}: {prefix!r}')
    base = head.rsplit('/', 1)[0]
    paths[str(branch)] = f'{base}/{version}/{branch}/Build/'
    # The two branches may now sit on different build ids. `version_paths` is
    # the authoritative per-branch map; the legacy scalar `version` is kept
    # only when both branches still agree, otherwise it becomes null and the
    # explicit branch_versions map takes over. No reader depends on the scalar.
    versions = {b: p.split('/')[3] for b, p in paths.items()}
    doc['branch_versions'] = versions
    doc['version'] = (version if len(set(versions.values())) == 1 else None)
    doc['_comment_version'] = ('version_paths / branch_versions are authoritative; '
                               'version is a legacy scalar, null when branches differ')

    new_loader = patch_loader(branch, cfg)
    replacements = {
        BUILD_DIR / wasm_name: wasm_gz.read_bytes(),
        BUILD_DIR / data_name: data_gz.read_bytes(),
        LOADER: new_loader,
        ASSETS: (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
    }
    for dst in replacements:
        need(dst.name in ACTIVATION_FILES, f'refusing to replace {dst.name}')

    saved = []
    for dst in replacements:
        if dst.is_file():
            keep = backup / dst.name
            shutil.copy2(dst, keep)
            saved.append((keep, dst))
    try:
        for dst, data in replacements.items():
            write_atomic(dst, data)
        # Gate AFTER the swap: it validates exactly the bytes now on disk.
        checked = verify_bindings(doc, branch, cfg)
        log(f'bindings verified for branches: {", ".join(checked)}')
    except Exception:
        for keep, dst in saved:
            try:
                shutil.copy2(keep, dst)
            except OSError:
                pass
        raise
    return backup, checked


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version', required=True)
    ap.add_argument('--branch', required=True, choices=['0', '1'])
    ap.add_argument('--work', required=True)
    ap.add_argument('--attempt', default='1')
    ap.add_argument('--arl-base', default=None,
                    help='local test server only; official_inputs accepts it as '
                         'an ARL_BASE override for offline simulation')
    ap.add_argument('--no-activate', action='store_true',
                    help='stop after validation (used by tests)')
    args = ap.parse_args(argv)

    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / f'worker-{args.attempt}.log'
    started = time.time()
    result = {'status': 'failed', 'activated': False, 'version': args.version,
              'branch': args.branch, 'attempt': args.attempt,
              'verification': 'none', 'stages': [], 'reason': ''}

    def stage(name, status, detail=''):
        result['stages'].append(dict(name=name, status=status, detail=str(detail)[:400]))
        log(f'{name}: {status} {detail}')

    try:
        # Validate INSIDE the try so every outcome still writes result.json:
        # the addon treats a missing result.json as a crashed worker.
        need(len(args.version) == 12 and args.version.isdigit(),
             f'--version must be a 12-digit official build id, got {args.version!r}')
        doc = read_manifest()
        current = doc['version_paths'].get(str(args.branch), '')
        if f'/{args.version}/{args.branch}/Build/' in current:
            result.update(status='already', reason=f'{args.version}/{args.branch} '
                                                   f'is already supported')
            stage('already supported', 'SKIP', current)
            return finish(result, work, log_path, started)

        inputs_dir = stage_download(args.version, args.branch, work, log_path,
                                    args.arl_base)
        stage('download', 'PASS', 'ntd.wasm.gz + ntd.data.gz + ntd.loader.js')

        stage_dir, wasm_gz, config, resolved = stage_patch(
            args.version, args.branch, inputs_dir, work, log_path)
        stage('metadata + hooks', 'PASS', f'{len(resolved["targets"])} hooks')
        stage('patch', 'PASS', sha256(wasm_gz)[:16] + ' (gz)')
        stage('runtime config', 'PASS',
              f'RESOLVED {config["compat"]["resolved"]} / '
              f'VERIFIED_STATIC {config["compat"]["verified_static"]} / '
              f'UNRESOLVED {config["compat"]["unresolved"]}')

        data_gz, graft = stage_font_data(args.branch, inputs_dir, stage_dir, work, log_path)
        stage('font data', 'PASS', f'{graft["size"]} B')

        # Structural reorder test is version independent; the rest of the test
        # suite is a regression oracle for the shipped build, not for this one.
        run(python_cmd(WASM_DIR / 'test_reorder.py'), log_path, 'reorder test')
        stage('reorder test', 'PASS', 'synthetic module')

        if args.no_activate:
            result.update(status='candidate', reason='--no-activate')
            result['verification'] = 'static'
            stage('activate', 'SKIP', '--no-activate')
            return finish(result, work, log_path, started)

        _backup, checked = activate(args.version, args.branch, stage_dir, work,
                                    log_path, wasm_gz, data_gz, config)
        result.update(status='activated', activated=True, verification='static',
                      wasm_sha256=config['wasm']['sha256'],
                      bound_branches=checked)
        stage('activate', 'PASS', f'{args.version}/{args.branch}')
        return finish(result, work, log_path, started)
    except StageError as exc:
        result['reason'] = str(exc)
        stage('aborted', 'FAIL', str(exc)[:200])
        return finish(result, work, log_path, started, code=2)
    except Exception as exc:                                    # noqa: BLE001
        result['reason'] = f'{type(exc).__name__}: {exc}'
        result['trace'] = traceback.format_exc()[-4000:]
        stage('aborted', 'FAIL', result['reason'])
        return finish(result, work, log_path, started, code=2)


def finish(result, work, log_path, started, code=0):
    result['seconds'] = round(time.time() - started, 1)
    result['finished'] = datetime.now(timezone.utc).isoformat()
    tmp = work / 'result.json.new'
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(tmp, work / 'result.json')
    log(f'{result["status"]}: {result.get("reason") or "ok"}')
    return code


if __name__ == '__main__':
    sys.exit(main())
