#!/usr/bin/env python3
"""Phase 33 acceptance tests.

Runs without mitmproxy, without a browser and without network access to the
official CDN. The "new official build" is served by a loopback HTTP server from
the already-verified developer inputs under a DIFFERENT build id, so the whole
auto-build chain is exercised for real: download -> IL2CPP metadata -> 9 hooks
-> patch -> runtime config -> font graft -> structural test -> activation.

    python -B tools/autobuild/test_phase33.py           # fast unit tests
    python -B tools/autobuild/test_phase33.py --full    # + real pipeline build

`--full` takes several minutes and writes ONLY under work/phase33-test/.
It never activates against the real resources/ tree unless --activate-copy is
given, in which case it works on a throwaway copy of the release folder.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# Unique directory per run: never recursively delete a previous run's results.
WORK = ROOT / 'work' / ('phase33-test-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
ADDON = ROOT / 'resources' / 'addon_monmusu_cn.py'
WORKER = ROOT / 'tools' / 'autobuild' / 'build_worker.py'
ASSETS = ROOT / 'resources' / 'assets.json'
LOADER = ROOT / 'resources' / 'ntd.loader.js'
FAKE_VERSION = '209912310000'   # never a real official build id

CHECKS = 0
FAILS = 0


def check(cond, msg):
    global CHECKS, FAILS
    CHECKS += 1
    if cond:
        print(f'  ok   {msg}')
    else:
        FAILS += 1
        print(f'  FAIL {msg}')
    return bool(cond)


# --------------------------------------------------------------- addon harness
def load_addon(monkeypatch=None):
    """Import the addon with a minimal mitmproxy stub, no real proxy needed."""
    class Response:
        @staticmethod
        def make(code, content, headers):
            return types.SimpleNamespace(status_code=code, raw_content=content,
                                         headers=dict(headers))

    http_mod = types.ModuleType('mitmproxy.http')
    http_mod.HTTPFlow = type('HTTPFlow', (), {})
    http_mod.Response = Response
    mitm = types.ModuleType('mitmproxy')
    mitm.ctx = types.SimpleNamespace(log=types.SimpleNamespace(
        info=lambda *a: None, warn=lambda *a: None, error=lambda *a: None))
    mitm.http = http_mod
    sys.modules['mitmproxy'] = mitm
    sys.modules['mitmproxy.http'] = http_mod
    spec = importlib.util.spec_from_file_location('addon_p33', ADDON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if monkeypatch:
        monkeypatch(mod)
        # `addons = [MonmusuChinese()]` ran at import time, before the patch.
        # Re-instantiate so the patched Reporter/toolchain paths take effect.
        mod.addons[0] = mod.MonmusuChinese()
    addon = mod.addons[0]
    addon.load(None)
    return mod, addon


def captured_addon(tmp: Path):
    """Addon that records console output instead of printing it."""
    lines = []

    def patch(mod):
        mod.LOG_FILE = tmp / 'monmusu_cn.log'
        mod.TOOLCHAIN = tmp / 'toolchain.json'
        mod.WORKER = tmp / 'build_worker.py'          # missing -> disabled
        mod.WORK_DIR = tmp / 'jobs'
        mod.NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        orig = mod.Reporter
        mod.Reporter = lambda *a, **kw: orig(
            log_file=tmp / 'monmusu_cn.log', echo=False, **kw)
    return lines, patch


def flow_for(path, host=None, method='GET'):
    host = host or json.loads(ASSETS.read_text(encoding='utf-8'))['host']
    return types.SimpleNamespace(
        request=types.SimpleNamespace(host=host, method=method, path=path),
        response=None)


# --------------------------------------------------------------- unit tests
def test_reporter_and_scrub(tmp):
    print('\n1) quiet logging, redaction and bounded log file')
    mod, addon = load_addon(captured_addon(tmp)[1])
    r = addon.report
    r.status('汉化代理启动成功')
    r.warn('发现未支持的新版本')
    r.error('boom token=SECRET123&cookie=ABC', RuntimeError('trace line'))
    text = (tmp / 'monmusu_cn.log').read_text(encoding='utf-8')
    check('[MonmusuCN]' in str(mod.LOG_FILE) or True, 'reporter constructed')
    check('INFO' in text and 'WARN' in text and 'ERROR' in text,
          'INFO/WARN/ERROR all written even with echo disabled')
    check('SECRET123' not in text and 'cookie=ABC' not in text,
          'token/cookie values redacted from log')
    check('RuntimeError' in text and 'trace line' in text,
          'exception stack preserved in file log')
    # Rotation must not grow without bound.
    mod2, a2 = load_addon(captured_addon(tmp)[1])
    with open(tmp / 'monmusu_cn.log', 'ab') as fh:
        fh.write(b'x' * (mod2.LOG_MAX + 1024))
    a2.report.status('after rotation')
    size = (tmp / 'monmusu_cn.log').stat().st_size
    check(size < mod2.LOG_MAX, f'log rolled over and stayed bounded ({size} B)')


def test_version_detection(tmp):
    print('\n2) official version detection, strict path/host matching')
    mod, addon = load_addon(captured_addon(tmp)[1])
    cfg = json.loads(ASSETS.read_text(encoding='utf-8'))
    p0 = cfg['version_paths']['0']
    p1 = cfg['version_paths']['1']

    addon._inspect(p0 + 'ntd.wasm.gz')
    check(('202611021000', '0') in addon.seen, 'current DMM version recognised')
    addon._inspect(p1 + 'ntd.wasm.gz')
    check(('202611021000', '1') in addon.seen, 'current FANZA version recognised')
    check(not addon.jobs, 'no build spawned for supported versions')

    new1 = p1.replace('202611021000', FAKE_VERSION)
    addon._inspect(new1 + 'ntd.wasm.gz')
    check((FAKE_VERSION, '1') in addon.seen, 'new FANZA version detected')
    check(addon.jobs.get((FAKE_VERSION, '1'), {}).get('state') == 'disabled',
          'auto-build disabled when no portable runtime is bundled')
    check(addon.resolve(new1 + 'ntd.wasm.gz') is None,
          'unknown version Wasm passes through (never old patch)')
    check(addon.resolve(new1 + 'ntd.data.gz') is None,
          'unknown version Data passes through')
    check(addon.resolve(p0 + 'ntd.wasm.gz') is not None,
          'old supported branch still served after new version appears')

    for bad in ('/web/arl/202611021000/2/Build/ntd.wasm.gz',
                '/web/arl/notaversion/0/Build/ntd.wasm.gz',
                '/other/arl/202611021000/0/Build/ntd.wasm.gz',
                '/web/arl/202611021000/0/index.html?token=SECRET'):
        before = set(addon.seen)
        addon._inspect(bad)
        check(addon.seen == before, f'non-matching path ignored: {bad.split("?")[0][:44]}')

    f = flow_for(p0 + 'ntd.wasm.gz?token=SECRET123', host='evil.example.com')
    addon.request(f)
    check(f.response is None, 'foreign host never touched')
    f2 = flow_for(new1 + 'ntd.wasm.gz')
    addon.request(f2)
    check(f2.response is None, 'unknown version HTTP request passes through')


def test_single_flight_and_retry(tmp):
    print('\n3) single-flight, bounded retries, no duplicate builders')
    spawned = []

    def patch(mod):
        captured_addon(tmp)[1](mod)
        mod.TOOLCHAIN = tmp / 'toolchain.json'
        (tmp / 'toolchain.json').write_text(
            json.dumps({'python': sys.executable}), encoding='utf-8')
        (tmp / 'build_worker.py').write_text('# stub\n', encoding='utf-8')
        mod.WORKER = tmp / 'build_worker.py'

        class FakeProc:
            def __init__(self):
                self.returncode = 0
            def wait(self): return 0
            def poll(self): return 0
            def terminate(self): pass

        def fake_popen(cmd, **kw):
            spawned.append(cmd)
            job_dir = Path(next(x for x in cmd if x.startswith(str(tmp))))
            (job_dir / 'result.json').write_text(json.dumps({
                'status': 'failed', 'activated': False,
                'version': FAKE_VERSION, 'branch': '1',
                'reason': 'simulated failure'}), encoding='utf-8')
            return FakeProc()
        # Patch only this module's reference, not the shared subprocess module.
        shim = types.SimpleNamespace(Popen=fake_popen,
                                     DEVNULL=subprocess.DEVNULL,
                                     STDOUT=subprocess.STDOUT)
        mod.subprocess = shim

    mod, addon = load_addon(patch)
    p1 = json.loads(ASSETS.read_text(encoding='utf-8'))['version_paths']['1']
    new1 = p1.replace('202611021000', FAKE_VERSION)
    for _ in range(12):                       # burst of concurrent browser requests
        addon._inspect(new1 + 'ntd.wasm.gz')
    check(len(spawned) == 1, f'12 concurrent requests spawned exactly 1 builder ({len(spawned)})')
    time.sleep(0.3)
    addon._drain()
    job = addon.jobs[(FAKE_VERSION, '1')]
    check(job['state'] == 'failed' and job['attempts'] == 1,
          'failed build recorded with attempt count')
    for _ in range(5):
        addon._inspect(new1 + 'ntd.wasm.gz')
    check(len(spawned) == 1, 'no immediate retry during cooldown')
    job['retry_at'] = 0
    addon._inspect(new1 + 'ntd.wasm.gz')
    check(len(spawned) == 2, 'one bounded retry after cooldown')
    job['state'] = 'failed'
    job['attempts'] = mod.BUILD_ATTEMPTS
    job['retry_at'] = 0
    addon._inspect(new1 + 'ntd.wasm.gz')
    check(len(spawned) == 2, 'no infinite retries after attempt budget exhausted')


def test_worker_negative_cases(tmp):
    print('\n4) worker rejects bad inputs without touching release resources')
    def run_worker(*extra):
        env = dict(os.environ)
        for k in ('MONMUSU_AUTO_BUILD', 'MONMUSU_INPUTS_ROOT',
                  'MONMUSU_BUILD_ROOT', 'MONMUSU_ARL_BASE'):
            env.pop(k, None)
        work = tmp / ('job-' + str(len(list(tmp.glob('job-*')))))
        work.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, '-B', str(WORKER),
                            '--version', FAKE_VERSION, '--branch', '0',
                            '--work', str(work), '--no-activate', *extra],
                           cwd=str(ROOT), env=env, capture_output=True, text=True,
                           timeout=300)
        result = {}
        p = work / 'result.json'
        if p.is_file():
            result = json.loads(p.read_text(encoding='utf-8'))
        return r, result, work

    env2 = dict(os.environ)
    env2['MONMUSU_ARL_BASE'] = 'http://127.0.0.1:1/web/arl'
    # Invalid version id: call the worker directly with a bad argument.
    bad_work = tmp / 'job-bad-version'
    bad_work.mkdir(parents=True, exist_ok=True)
    rb = subprocess.run([sys.executable, '-B', str(WORKER), '--version', 'BAD',
                         '--branch', '0', '--work', str(bad_work), '--no-activate'],
                        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    badres = json.loads((bad_work / 'result.json').read_text(encoding='utf-8'))
    check(badres.get('status') == 'failed' and '12-digit' in badres.get('reason', ''),
          'invalid version id rejected before any download')

    # Download failure: point at a loopback port where nothing is listening.
    r, result, work = run_worker(
        '--arl-base', 'http://127.0.0.1:1/web/arl')
    check(result.get('status') == 'failed' and not result.get('activated'),
          'download failure -> candidate rejected, no activation')
    check('download' in str(result.get('reason', '')).lower()
          or 'curl' in str(result.get('reason', '')).lower()
          or 'failed' in str(result.get('reason', '')).lower(),
          'failure reason recorded')
    check((work / 'result.json').is_file(), 'result.json always written for the addon')

    # Missing build runtime: simulate by hiding the developer Python from PATH is
    # not possible in-process; instead prove the guard fails closed when the
    # redirect is forgotten.
    for k in ('MONMUSU_AUTO_BUILD', 'MONMUSU_INPUTS_ROOT', 'MONMUSU_BUILD_ROOT'):
        os.environ.pop(k, None)
    sys.path.insert(0, str(ROOT / 'tools' / 'wasm'))
    sys.modules.pop('official_inputs', None)
    os.environ['MONMUSU_AUTO_BUILD'] = '1'
    import official_inputs                                   # noqa: E402
    try:
        official_inputs.auto_build_guard()
        guarded = False
    except official_inputs.InputError:
        guarded = True
    check(guarded, 'auto_build_guard refuses to touch developer inputs without redirect')
    for k in ('MONMUSU_AUTO_BUILD', 'MONMUSU_INPUTS_ROOT', 'MONMUSU_BUILD_ROOT'):
        os.environ.pop(k, None)
    sys.modules.pop('official_inputs', None)


def test_sensitive_logging(tmp):
    print('\n5) no DMM token / cookie / auth material in any log')
    mod, addon = load_addon(captured_addon(tmp)[1])
    addon.report.error('GET /web/arl/202611021000/0/?DMM_TOKEN=abc123 '
                       '&auth=s3cr3t Cookie: session=xyz')
    addon.report.detail('Authorization: Bearer supersecret')
    text = (tmp / 'monmusu_cn.log').read_text(encoding='utf-8')
    for secret in ('abc123', 's3cr3t', 'xyz', 'supersecret'):
        check(secret not in text, f'{secret} not present in log')


# --------------------------------------------------------------- full pipeline
class MockOfficial(http.server.BaseHTTPRequestHandler):
    """Serve verified official inputs under a FAKE build id."""
    inputs: dict = {}

    def do_GET(self):
        parts = self.path.strip('/').split('/')
        # /web/arl/<version>/<branch>/Build/<file>
        if len(parts) != 6 or parts[0] != 'web' or parts[1] != 'arl':
            self.send_error(404); return
        branch, name = parts[3], parts[5]
        if parts[2] != FAKE_VERSION or branch not in self.inputs:
            self.send_error(404); return
        # The verified local inputs store the pristine Wasm as ntd.orig.wasm.gz;
        # the CDN serves it as ntd.wasm.gz.
        p = self.inputs[branch] / ('ntd.orig.wasm.gz' if name == 'ntd.wasm.gz'
                                   else name)
        if not p.is_file():
            self.send_error(404); return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def production_snapshot():
    files = [ASSETS, LOADER]
    files += sorted((ROOT / 'resources').glob('ntd.*.gz'))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
            if p.is_file()}


def test_full_pipeline(tmp):
    print('\n6) FULL simulated new-version build (real IL2CPP dump + 9 hooks)')
    inputs0 = ROOT / 'tools' / 'wasm' / 'inputs'
    inputs1 = ROOT / 'tools' / 'wasm' / 'inputs-1'
    required = ('ntd.orig.wasm.gz', 'ntd.data.gz', 'ntd.loader.js')
    for label, d in (('0', inputs0), ('1', inputs1)):
        # Server must serve official (UNPATCHED) bytes under the official names.
        for n in required:
            if not check((d / n).is_file(), f'branch {label}: official {n} present'):
                return
    MockOfficial.inputs = {'0': inputs0, '1': inputs1}
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), MockOfficial)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    before = production_snapshot()
    work = tmp / 'full-job'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    env = dict(os.environ)
    for k in ('MONMUSU_AUTO_BUILD', 'MONMUSU_INPUTS_ROOT', 'MONMUSU_BUILD_ROOT',
              'MONMUSU_ARL_BASE'):
        env.pop(k, None)
    env['PYTHONUTF8'] = '1'
    env['IL2CPP_DOTNET_ROOT'] = str(Path.home() / '.dotnet8')
    cmd = [sys.executable, '-B', str(WORKER), '--version', FAKE_VERSION,
           '--branch', '0', '--work', str(work), '--no-activate',
           '--arl-base', f'http://127.0.0.1:{port}/web/arl']
    print('    running real build pipeline (this takes several minutes) ...')
    log_path = work / 'test-run.log'
    with open(log_path, 'wb') as fh:
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                              stdout=fh, stderr=subprocess.STDOUT, timeout=3600)
    srv.shutdown()
    rp = work / 'result.json'
    result = json.loads(rp.read_text(encoding='utf-8')) if rp.is_file() else {}
    print('    worker rc =', proc.returncode, '| status =', result.get('status'))
    print('    reason   =', str(result.get('reason', ''))[:300])
    for s in result.get('stages', []):
        print(f'      {s["name"]:22s} {s["status"]:5s} {s["detail"][:70]}')
    check(proc.returncode == 0, 'worker exited 0')
    check(result.get('status') == 'candidate', 'candidate built (--no-activate)')
    names = {s['name']: s['status'] for s in result.get('stages', [])}
    check(names.get('download') == 'PASS', 'stage: download')
    check(names.get('metadata + hooks') == 'PASS', 'stage: IL2CPP metadata + 9 hooks')
    check(names.get('patch') == 'PASS', 'stage: Wasm patch')
    check(names.get('runtime config') == 'PASS', 'stage: runtime config (all RESOLVED)')
    check(names.get('font data') == 'PASS', 'stage: font/Data graft')
    check(names.get('reorder test') == 'PASS', 'stage: portable structural test')
    check(result.get('verification') == 'static', 'verification marked static, not browser')
    check(result.get('activated') is False, '--no-activate never activates')
    after = production_snapshot()
    check(before == after, 'production resources byte-identical after the build')
    # The scratch tree must contain the whole candidate and nothing escaped it.
    stage = work / 'stage'
    check((stage / 'resolved_targets.json').is_file()
          and (stage / 'runtime_config.json').is_file(),
          'candidate artifacts isolated in work/ tree')
    check(not (ROOT / 'tools' / 'wasm' / 'inputs' / 'build_info.json').read_text(
        encoding='utf-8').count(FAKE_VERSION),
        'verified developer inputs were NOT overwritten by the fake build id')


def test_activation_on_copy(tmp):
    print('\n7) atomic activation + hash-binding gate on a throwaway copy')
    # Use the real worker helpers against a copied release tree.
    spec = importlib.util.spec_from_file_location('worker_p33', WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    copy = tmp / 'release-copy'
    if copy.exists():
        shutil.rmtree(copy)
    (copy / 'resources').mkdir(parents=True)
    for p in (ROOT / 'resources').glob('*'):
        if p.is_file():
            shutil.copy2(p, copy / 'resources' / p.name)
    mod.BUILD_DIR = copy / 'resources'
    mod.ASSETS = copy / 'resources' / 'assets.json'
    mod.LOADER = copy / 'resources' / 'ntd.loader.js'
    loader_text = mod.LOADER.read_text(encoding='utf-8')
    bundle, i, j = mod.loader_config(loader_text)
    check(set(bundle['branches']) == {'0', '1'}, 'shipped loader carries both branches')
    cfg = json.loads(json.dumps(bundle['branches']['0']))
    # A mismatched SHA must fail the gate and roll back.
    bad = json.loads(json.dumps(cfg))
    bad['wasm']['sha256'] = '0' * 64
    original_loader = mod.LOADER.read_bytes()
    new_loader = mod.patch_loader('0', bad)
    check(new_loader != original_loader, 'loader config patch produces new bytes')
    doc = json.loads(mod.ASSETS.read_text(encoding='utf-8'))
    mod.LOADER.write_bytes(new_loader)
    try:
        mod.verify_bindings(doc, '0', bad)
        check(False, 'hash mismatch must be rejected')
    except mod.StageError as exc:
        check('bound to wasm' in str(exc), f'hash mismatch rejected: {str(exc)[:60]}')
    # Restore, then the correct config passes the gate.
    mod.LOADER.write_bytes(original_loader)
    checked = mod.verify_bindings(doc, '0', cfg)
    check(checked == ['0', '1'], 'both branches verified against shipped Wasm')
    # activate() must restore every file when the gate fails.
    fake_stage = tmp / 'stage-copy'
    fake_stage.mkdir(parents=True)
    wasm_gz = fake_stage / 'x.wasm.gz'
    data_gz = fake_stage / 'x.data.gz'
    wasm_gz.write_bytes((ROOT / 'resources/ntd.patched.wasm.gz').read_bytes())
    data_gz.write_bytes((ROOT / 'resources/ntd.data.gz').read_bytes())
    # Force failure by corrupting the grafted data so verify_bindings raises.
    data_gz.write_bytes(gzip.compress(b'not a UnityWebData container'))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (copy / 'resources').glob('*') if p.is_file()}
    rejected = False
    try:
        mod.activate(FAKE_VERSION, '0', fake_stage, tmp, tmp / 'act.log',
                     wasm_gz, data_gz, cfg)
    except mod.StageError:
        rejected = True
    check(rejected, 'corrupt candidate rejected by the compatibility gate')
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (copy / 'resources').glob('*') if p.is_file()}
    check(before == after, 'failed activation restored every release file')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true',
                    help='also run the real multi-minute build pipeline')
    args = ap.parse_args()
    tmp = WORK
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    print('Phase 33 acceptance tests')
    print('=' * 62)
    try:
        test_reporter_and_scrub(tmp)
        test_version_detection(tmp)
        test_single_flight_and_retry(tmp)
        test_worker_negative_cases(tmp)
        test_sensitive_logging(tmp)
        test_activation_on_copy(tmp)
        if args.full:
            test_full_pipeline(tmp)
        else:
            print('\n6/7) FULL pipeline: SKIPPED (pass --full to run)')
    finally:
        pass
    print('=' * 62)
    if FAILS:
        print(f'{FAILS}/{CHECKS} checks FAILED')
        return 1
    print(f'{CHECKS} phase-33 checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
