"""Developer-only build: join the production loader and package an explicit allowlist."""
import gzip
import hashlib
import json
import sys
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
# The generated runtime config goes first so the bootstrap can hand the entry
# for the served branch to the bridge. It is per branch (DMM /0/, FANZA /1/) and
# bound to the SHA-256 of the patched Wasm that ships next to it.
ENGINES = ('monmusu_cn_runtime_config.js',
           'monmusu_cn_story_index.js', 'monmusu_cn_ui_index.js',
           'monmusu_cn_shim.js', 'monmusu_cn_bootstrap.js')
RUNTIME_JS = ROOT / 'monmusu_cn_runtime_config.js'
MARK_BEGIN = '/*RUNTIME_JSON_BEGIN*/'
MARK_END = '/*RUNTIME_JSON_END*/'
ARCHIVE = ROOT / 'dist/MonmusuTDChinese-v0.1.0-win-x64.zip'
# Optional second edition: identical release + the portable build runtime.
ARCHIVE_AUTO = ROOT / 'dist/MonmusuTDChinese-v0.1.0-win-x64-autobuild.zip'
TOOLCHAIN_JSON = ROOT / 'runtime' / 'toolchain.json'
TOOLCHAIN_DIR = ROOT / 'runtime' / 'toolchain'
# CPython's stdlib and .NET's shared framework ship a handful of data/doc
# files with unusual extensions (archspec CPU microarchitecture tables, .rst,
# .typed stubs, activation scripts). Allow the measured set explicitly; any
# NEW extension aborts packaging instead of being silently shipped.
TOOLCHAIN_ALLOWED_SUFFIXES = {
    '.py', '.json', '.txt', '.dll', '.pyd', '.exe', '.pdb', '.so', '.md',
    '.pyi', '.cfg', '.ini', '.xml', '.cab', '.rst', '.css', '.typed',
    '.iobj', '.ipdb', '.tpk', '.c', '.ctypes', '.ps1', '.bat', '.fish',
    '.csh', '.5-zen4', '.04-broadwell', '.04-zen3', '.04-ampere1',
    '.04-ampere1a', '.04-neoverse_n2', '.04-neoverse_v2', '.04-alderlake',
    '.04-neoverse_v3ae', '.04-arrowlake_s', '.04-olympus', '',
}
TOOLCHAIN_ALLOWED_NAMES = {'python313._pth', 'Il2CppDumper.runtimeconfig.json'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build_loader():
    data = (ROOT / 'loader_base.js').read_bytes()
    for source in ENGINES:
        data += b'\n;\n' + (ROOT / source).read_bytes()
    (ROOT / 'resources/ntd.loader.js').write_bytes(data)
    # Exact wire/plain consistency is also checked by the full translation audit.
    for name in ('monmusu_cn_story_index.json', 'monmusu_cn_ui_index.json'):
        if gzip.decompress((ROOT / ('resources/' + name + '.gz')).read_bytes()) != (ROOT / ('resources/' + name)).read_bytes():
            raise ValueError('stale compressed index: ' + name)
    if gzip.decompress((ROOT / 'resources/ntd.patched.wasm.gz').read_bytes()) != (ROOT / 'resources/ntd.patched.wasm').read_bytes():
        raise ValueError('stale compressed Wasm')
    # Same check for the FANZA branch: the two branches ship different Wasm, and
    # a .gz that does not match its plain file would break exactly one store
    # while the other still looked fine.
    for stem in ('ntd.patched.1.wasm',):
        plain = ROOT / ('resources/' + stem)
        packed = ROOT / ('resources/' + stem + '.gz')
        if not packed.is_file() or not plain.is_file():
            raise ValueError('missing branch-1 resource: ' + stem)
        if gzip.decompress(packed.read_bytes()) != plain.read_bytes():
            raise ValueError('stale compressed resource: ' + stem)
    for name in ('ntd.data.gz', 'ntd.data.1.gz'):
        p = ROOT / ('resources/' + name)
        if not p.is_file() or p.stat().st_size < 1024:
            raise ValueError('missing resource: ' + name)
        if gzip.decompress(p.read_bytes())[:15] != b'UnityWebData1.0':
            raise ValueError('not a UnityWebData container: ' + name)


def runtime_config():
    if not RUNTIME_JS.is_file():
        raise ValueError('missing generated runtime config: ' + RUNTIME_JS.name +
                         ' (run rebuild_wasm.bat)')
    text = RUNTIME_JS.read_text(encoding='utf-8')
    i, j = text.find(MARK_BEGIN), text.find(MARK_END)
    if i < 0 or j < i:
        raise ValueError('runtime config is damaged: JSON markers missing')
    return json.loads(text[i + len(MARK_BEGIN):j])


def check_runtime_config(manifest):
    """Every shipped branch must carry the config that was resolved for ITS Wasm.

    This is the release gate for phase 32: a stale entry, a branch built from the
    other store's inputs, or an unresolved critical binding must stop the ZIP
    rather than ship a loader that calls into the wrong element-table slots.
    """
    cfg = runtime_config()
    branches = cfg.get('branches') or {}
    checked = []
    for spec in manifest['assets']:
        if spec['name'] != 'ntd.wasm.gz':
            continue
        branch = str(spec.get('branch'))
        entry = branches.get(branch)
        if not entry:
            raise ValueError(f'branch {branch}: no runtime config entry '
                             f'(run: rebuild_wasm.bat --update <build> {branch})')
        if not (entry.get('compat') or {}).get('publishable'):
            fails = (entry.get('compat') or {}).get('failures') or []
            raise ValueError(f'branch {branch}: runtime config has unresolved bindings: '
                             + ', '.join(f.get('id', '?') for f in fails[:6]))
        shipped = sha(gzip.decompress((ROOT / spec['file']).read_bytes()))
        bound = (entry.get('wasm') or {}).get('sha256')
        if shipped != bound:
            raise ValueError(f'branch {branch}: runtime config is bound to wasm '
                             f'{str(bound)[:16]} but the packaged wasm is {shipped[:16]}')
        checked.append(branch)
    if not checked:
        raise ValueError('no ntd.wasm.gz declared in assets.json')
    return checked


def toolchain_names():
    """Strict allowlist for the optional portable build runtime.

    Only files under runtime/toolchain/ may be added, and only with known-safe
    extensions. Anything suspicious (private keys, logs, caches, developer
    input trees) aborts the packaging instead of silently shipping.
    """
    if not TOOLCHAIN_JSON.is_file() or not TOOLCHAIN_DIR.is_dir():
        raise ValueError('portable toolchain missing; run '
                         'python -B tools/autobuild/build_toolchain.py first')
    doc = json.loads(TOOLCHAIN_JSON.read_text(encoding='utf-8'))
    test = doc.get('self_test') or {}
    if not all(test.get(k, '').startswith('OK') for k in ('python', 'dotnet', 'dumper')):
        raise ValueError('portable toolchain self-test did not pass: ' + str(test))
    names = ['runtime/toolchain.json']
    for p in TOOLCHAIN_DIR.rglob('*'):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        parts = [x.lower() for x in Path(rel).parts]
        if any(x in {'data', '.venv', 'work', 'ref', '__pycache__', 'profile'}
               for x in parts):
            raise ValueError('private/developer directory in toolchain: ' + rel)
        if (p.suffix.lower() not in TOOLCHAIN_ALLOWED_SUFFIXES
                and p.name not in TOOLCHAIN_ALLOWED_NAMES):
            raise ValueError('unexpected toolchain file type: ' + rel)
        if p.suffix.lower() in {'.pem', '.p12', '.pfx', '.key', '.log', '.pyc'}:
            raise ValueError('private/log file in toolchain: ' + rel)
        names.append(rel)
    if not (TOOLCHAIN_DIR / 'python' / 'python.exe').is_file():
        raise ValueError('toolchain python.exe missing')
    packaged_file = TOOLCHAIN_DIR / 'packaged_files.json'
    if not packaged_file.is_file():
        raise ValueError('toolchain packaged_files.json missing')
    for rel in json.loads(packaged_file.read_text(encoding='utf-8')):
        if not rel.startswith(('tools/wasm/', 'tools/fonts/',
                               'tools/autobuild/', 'tools/il2cppdumper/')):
            raise ValueError('build file outside the allowlisted tools dirs: ' + rel)
        if not (ROOT / rel).is_file():
            raise ValueError('packaged build file missing: ' + rel)
        names.append(rel)
    return names


def build_release(with_toolchain=False):
    manifest = json.loads((ROOT / 'resources/assets.json').read_text(encoding='utf-8'))
    # Gate BEFORE writing the loader: a config that cannot ship must never end up
    # embedded in resources/ntd.loader.js, otherwise the next audit reads a loader
    # that is stale by construction (this really happened while testing the gate).
    checked = check_runtime_config(manifest)
    build_loader()
    names = ['启动汉化.bat', 'config.ini', 'README.txt',
             'runtime/mitmdump.exe', 'runtime/launch.ps1',
             'runtime/provenance.json', 'resources/assets.json', 'resources/addon_monmusu_cn.py']
    names += [a['file'] for a in manifest['assets'] + manifest['suffix_assets']]
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / 'licenses').rglob('*') if p.is_file()]
    if with_toolchain:
        names += toolchain_names()
    names = sorted(set(names))
    archive = ARCHIVE_AUTO if with_toolchain else ARCHIVE
    integrity = {}
    for name in names:
        p = (ROOT / name).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file():
            raise ValueError('missing or invalid release file: ' + name)
        if any(x.lower() in {'data', '.venv', 'work', 'ref', '__pycache__', 'profile'} for x in Path(name).parts):
            raise ValueError('private/developer directory: ' + name)
        if p.suffix.lower() in {'.pem', '.p12', '.pfx', '.key', '.log'}:
            raise ValueError('private/log file: ' + name)
        integrity[name] = {'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}
    runtime = json.loads((ROOT / 'runtime/provenance.json').read_text())
    expected = runtime.get('executableSha256') or runtime.get('executable_sha256')
    if expected and integrity['runtime/mitmdump.exe']['sha256'] != expected:
        raise ValueError('runtime checksum mismatch')
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in names:
            z.write(ROOT / name, 'MonmusuTDChinese/' + name)
    with zipfile.ZipFile(archive) as z:
        if z.testzip():
            raise ValueError('ZIP CRC failed')
        if set(z.namelist()) != {'MonmusuTDChinese/' + n for n in names}:
            raise ValueError('ZIP allowlist mismatch')
        for name, item in integrity.items():
            if sha(z.read('MonmusuTDChinese/' + name)) != item['sha256']:
                raise ValueError('ZIP resource hash mismatch: ' + name)
    report = {'archive': archive.name, 'bytes': archive.stat().st_size,
              'sha256': sha(archive.read_bytes()), 'files': integrity}
    report['runtimeConfigBranches'] = checked
    report['withToolchain'] = bool(with_toolchain)
    suffix = '_autobuild' if with_toolchain else ''
    (archive.parent / ('release_manifest' + suffix + '.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, ensure_ascii=False))
    print('ZIP CRC, allowlist and all', len(names), 'file hashes: PASS')
    print('runtime config bound to the shipped wasm for branches:', ', '.join(checked))
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a not in ('--loader-only', '--with-toolchain')]
    if unknown:
        print('unknown arguments: ' + ' '.join(unknown), file=sys.stderr)
        return 1
    if '--with-toolchain' in argv:
        result = build_release(with_toolchain=True)
        return 0 if result else 1
    if '--loader-only' in argv:
        # Used by the auto-update worker after it swapped the artifacts: rebuild
        # resources/ntd.loader.js so the embedded runtime config matches the Wasm
        # that is now in place. Same gate, no ZIP.
        manifest = json.loads((ROOT / 'resources/assets.json').read_text(encoding='utf-8'))
        checked = check_runtime_config(manifest)
        build_loader()
        print('loader rebuilt; runtime config bound to wasm branches:',
              ', '.join(checked))
        return 0
    build_release()
    return 0


if __name__ == '__main__':
    sys.exit(main())
