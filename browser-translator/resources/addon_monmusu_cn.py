"""Fixed Unity asset replacement for Monmusu TD Chinese. No traffic dumps.

Two resource branches are supported, each with its own official build id:

    /web/arl/202611021000/0/Build/   DMM
    /web/arl/202611021000/1/Build/   FANZA

Only these exact prefixes are ever touched. Each asset is declared by its bare
file name and either a specific branch or "any"; the addon expands that into
one lookup table per prefix, so a shared file is served under both branches and
a branch-specific file (the Wasm, ntd.data) is never served to the other one.
Everything outside the declared prefixes passes through untouched.

Phase 33 adds:
  * a self-contained status channel that survives `termlog_verbosity=error`
    (mitmproxy's own log is silenced so WebSocket pings and request lines stop
    drowning the console; our lines go straight to stdout and to logs/);
  * detection of a game build we do not ship yet. It never substitutes the old
    Wasm for a new build: traffic passes through untouched while a worker
    process rebuilds the patch in the background, and the new artifacts are
    swapped in atomically only after every structural gate passed;
  * an opt-in build runtime (runtime/toolchain.json). Without it the addon is
    still fully functional for the shipped build and says so explicitly.

Nothing about a request's headers, cookies, query string or body is ever
logged. Only the version/branch facts derived from the URL path are.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from mitmproxy import ctx, http

ROOT = Path(__file__).resolve().parent.parent
# Module-level so tests can point the addon at a temporary manifest. Production
# always uses the checked-in resources/assets.json.
CONFIG = ROOT / 'resources' / 'assets.json'

# 12-digit official build id in practice; 10..16 tolerated because
# tools/wasm/official_inputs.py uses the same range. Branch is STRICTLY 0/1:
# any other value is ignored rather than guessed.
_VERSION = r'\d{10,16}'
PREFIX_RE = re.compile(r'^/web/arl/(' + _VERSION + r')/([01])/Build/$')
ARL_RE = re.compile(r'^/web/arl/(' + _VERSION + r')/([01])/')
NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')
STORE = {'0': 'DMM', '1': 'FANZA'}
# The only host this addon is allowed to touch. A manifest pointing anywhere
# else is rejected at load time.
OFFICIAL_HOST = 'assets.game-monmusu-td.net'

LOG_DIR = ROOT / 'logs'
LOG_FILE = LOG_DIR / 'monmusu_cn.log'
LOG_MAX = 2 * 1024 * 1024          # one rollover, then truncate: never unbounded
WORK_DIR = ROOT / 'work' / 'autobuild'
TOOLCHAIN = ROOT / 'runtime' / 'toolchain.json'
WORKER = ROOT / 'tools' / 'autobuild' / 'build_worker.py'

BUILD_ATTEMPTS = 2                 # one retry, then stop until the next launch
RETRY_DELAY = 300                  # seconds between attempts
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Any accidental credential-like text is scrubbed before it reaches a log.
_SCRUB = re.compile(
    r'(?i)\b(token|cookie|authorization|session|auth|sid|dmm_[a-z_]*|'
    r'login_[a-z_]*|access_key|secret)=([^\s&;"\']+)')
_QUERY = re.compile(r'(?i)([?&](?:token|cookie|auth|sig|session|key)=)[^&\s]+')
# HTTP header form: "Authorization: Bearer xxx", "Cookie: session=xxx".
_HEADER = re.compile(
    r'(?i)\b(authorization|cookie|set-cookie|x-dmm-token|proxy-authorization)'
    r'(\s*:\s*)([^\r\n]+)')


def _scrub(text: str) -> str:
    text = _SCRUB.sub(lambda m: m.group(1) + '=<redacted>', text)
    text = _HEADER.sub(lambda m: m.group(1) + m.group(2) + '<redacted>', text)
    return _QUERY.sub(lambda m: m.group(1) + '<redacted>', text)


class Reporter:
    """Console + file status channel that does not depend on mitmproxy logging.

    mitmproxy is started with `termlog_verbosity=error`, which drops
    ctx.log.info/warn. Those messages are exactly what a player needs, so they
    are printed directly instead; ctx.log is kept for the developer mode where
    mitmproxy's own output is wanted.
    """

    def __init__(self, log_file: Path = LOG_FILE, echo: bool = True,
                 debug: bool = False):
        self.log_file = log_file
        self.echo = echo
        self.debug = debug
        self._lock = threading.Lock()
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.echo = True

    def _append(self, level: str, msg: str, trace: str | None = None):
        line = f'{datetime.now().isoformat(timespec="seconds")} {level:5s} {_scrub(msg)}\n'
        if trace:
            line += _scrub(trace)
        try:
            with self._lock:
                if self.log_file.is_file() and self.log_file.stat().st_size > LOG_MAX:
                    backup = self.log_file.with_suffix('.log.1')
                    try:
                        if backup.is_file():
                            backup.unlink()
                        self.log_file.rename(backup)
                    except OSError:
                        pass
                with open(self.log_file, 'a', encoding='utf-8', errors='replace') as fh:
                    fh.write(line)
        except OSError:
            pass

    def _print(self, text: str):
        if not self.echo:
            return
        try:
            print(text, flush=True)
        except (UnicodeEncodeError, OSError, ValueError):
            try:
                print(text.encode('ascii', 'replace').decode('ascii'), flush=True)
            except Exception:                                  # noqa: BLE001
                pass

    def status(self, msg: str):
        self._print(f'[MonmusuCN] {_scrub(msg)}')
        self._append('INFO', msg)

    def warn(self, msg: str):
        self._print(f'[MonmusuCN] {_scrub(msg)}')
        self._append('WARN', msg)

    def error(self, msg: str, exc: BaseException | None = None):
        self._print(f'[MonmusuCN] 错误：{_scrub(msg)}')
        trace = None
        if exc is not None:
            trace = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._append('ERROR', msg, trace)

    def detail(self, msg: str):
        """Per-request noise. File only, unless developer debug mode is on."""
        self._append('DEBUG', msg)
        if self.debug:
            self._print(f'[MonmusuCN][debug] {_scrub(msg)}')
            try:
                ctx.log.info(_scrub(msg))
            except Exception:                                  # noqa: BLE001
                pass


def _read_config() -> dict:
    return json.loads(Path(CONFIG).read_text(encoding='utf-8'))


class MonmusuChinese:
    def __init__(self):
        self.report = Reporter(debug=os.environ.get('MONMUSU_DEBUG') == '1')
        self.host = ''
        self.prefixes = []              # [(branch, prefix)]
        self.by_prefix = {}
        self.suffix = []
        self.supported = {}             # branch -> version
        self.seen = set()               # (version, branch) already announced
        self.jobs = {}                  # (version, branch) -> job dict
        self._queue = queue.Queue()     # worker results (produced off-loop)
        self._toolchain = None
        self._toolchain_checked = False

    # ------------------------------------------------------------------ load
    def load(self, loader):
        self.report.status('汉化代理启动成功')
        self._apply_payload(self._read_payload())

    def _read_payload(self) -> dict:
        """Parse assets.json and load every declared file.

        Pure I/O only: safe to call from a background thread. A rebuild reload
        happens off the request thread so ~150 MB of reads never stall flows.
        """
        config = _read_config()
        host = config['host']
        if host != OFFICIAL_HOST:
            raise ValueError(f'assets.json host must be {OFFICIAL_HOST}, got {host!r}')

        paths = config['version_paths']
        if not isinstance(paths, dict) or not paths:
            raise ValueError('version_paths must be a non-empty object')
        prefixes = []
        supported = {}
        for branch, prefix in paths.items():
            m = PREFIX_RE.match(prefix)
            if not m:
                raise ValueError(f'version_paths[{branch!r}] is not a '
                                 f'/web/arl/<version>/<branch>/Build/ prefix: {prefix!r}')
            if m.group(2) != str(branch):
                raise ValueError(f'version_paths[{branch!r}] points at branch '
                                 f'{m.group(2)}')
            # Two branches may legitimately sit on different builds (phase 33).
            # What must never happen is one branch's files being served under
            # the other branch's prefix; that is structural here (one table per
            # prefix), and each branch has its own Wasm/data entries.
            prefixes.append((str(branch), prefix))
            supported[str(branch)] = m.group(1)
        prefixes.sort()

        by_prefix = {prefix: {} for _b, prefix in prefixes}
        for spec in config['assets']:
            name = spec['name']
            if not NAME_RE.match(name):
                raise ValueError(f'asset name must be a bare file name: {name!r}')
            branch = str(spec.get('branch', 'any'))
            if branch != 'any' and branch not in paths:
                raise ValueError(f'{name}: unknown branch {branch!r}')
            payload = self.read_asset(spec)
            for b, prefix in prefixes:
                if branch in ('any', b):
                    by_prefix[prefix][name] = payload

        suffix = [(spec['suffix'], self.read_asset(spec))
                  for spec in config['suffix_assets']]
        return dict(host=host, prefixes=prefixes, by_prefix=by_prefix,
                    suffix=suffix, supported=supported)

    @staticmethod
    def read_asset(spec):
        path = (ROOT / spec['file']).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('resource outside application folder')
        return path.read_bytes(), spec

    def _apply_payload(self, payload):
        self.host = payload['host']
        self.prefixes = payload['prefixes']
        self.by_prefix = payload['by_prefix']
        self.suffix = payload['suffix']
        self.supported = payload['supported']
        counts = ', '.join(f'{b}:{len(self.by_prefix[p])}' for b, p in self.prefixes)
        self.report.status(f'汉化资源就绪：{counts}，'
                           f'{len(self.suffix)} 个按后缀匹配的资源包')
        for b, v in sorted(self.supported.items()):
            self.report.status(f'{STORE.get(b, "?")} 分支支持版本：{v}')

    # --------------------------------------------------------------- request
    def request(self, flow: http.HTTPFlow):
        if flow.request.host != self.host or flow.request.method not in ('GET', 'HEAD'):
            return
        # Query strings can carry account material; never touch or log them.
        path = flow.request.path.split('?', 1)[0]

        self._drain()
        self._inspect(path)

        found = self.resolve(path)
        if found is None:
            return
        body, spec = found
        headers = {
            'Content-Type': spec['content_type'],
            'Content-Length': str(len(body)),
            'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
            'Pragma': 'no-cache', 'Accept-Ranges': 'none',
        }
        if spec.get('encoding'):
            headers['Content-Encoding'] = spec['encoding']
        if spec.get('cors'):
            headers['Access-Control-Allow-Origin'] = '*'
        response = http.Response.make(200, b'', headers)
        response.raw_content = body if flow.request.method == 'GET' else b''
        response.headers['Content-Length'] = str(len(body))
        flow.response = response

    def resolve(self, path):
        for _branch, prefix in self.prefixes:
            if path.startswith(prefix):
                hit = self.by_prefix[prefix].get(path[len(prefix):])
                if hit is not None:
                    return hit
        # Content-addressed bundles: the suffix IS the hash of the shipped
        # bytes, so a match is by definition byte-identical regardless of the
        # build id that requested it. No old Wasm can leak through this path.
        for suffix, asset in self.suffix:
            if path.endswith(suffix):
                return asset
        return None

    # ------------------------------------------------------- version detect
    def _inspect(self, path: str):
        m = ARL_RE.match(path)
        if not m:
            return
        version, branch = m.group(1), m.group(2)
        key = (version, branch)
        current = self.supported.get(branch)

        if key not in self.seen:
            self.seen.add(key)
            store = STORE[branch]
            if current == version:
                self.report.status(f'检测到 {store} / {version}（已支持）')
            elif current:
                self.report.status(f'检测到 {store} / {version}（本地已支持 {current}）')
            else:
                self.report.status(f'检测到 {store} / {version}')
            if current != version:
                self.report.warn('发现未支持的新版本：官方资源直通，不注入旧补丁')

        # Called on every request, but _start_build is single-flight and has
        # its own retry cooldown, so this cannot spawn duplicate builders.
        # Keeping the call outside `seen` is what makes retries possible at all.
        if current != version:
            self._start_build(version, branch)
        self.report.detail(f'{path} -> {current or "unknown"}/{branch}')

    # ------------------------------------------------------------- building
    def _load_toolchain(self):
        if self._toolchain_checked:
            return self._toolchain
        self._toolchain_checked = True
        candidates = []
        if TOOLCHAIN.is_file():
            try:
                candidates.append(json.loads(TOOLCHAIN.read_text(encoding='utf-8')))
            except Exception as exc:                            # noqa: BLE001
                self.report.error(f'{TOOLCHAIN.name} 无法解析，自动构建已禁用', exc)
        env_python = os.environ.get('MONMUSU_BUILD_PYTHON')
        if env_python:
            candidates.append({'python': env_python, 'source': 'environment'})
        for cand in candidates:
            python = cand.get('python') or ''
            path = Path(python) if python else None
            if path and not path.is_absolute():
                path = ROOT / path
            if not path or not path.is_file():
                self.report.warn(f'构建运行时不可用：{python or "(empty)"}')
                continue
            self._toolchain = dict(cand, python=str(path))
            break
        return self._toolchain

    def _start_build(self, version: str, branch: str):
        key = (version, branch)
        job = self.jobs.get(key)
        now = time.time()
        if job is not None:
            if job['state'] in ('running', 'disabled', 'done'):
                return
            if job['state'] == 'failed':
                if now < job['retry_at'] or job['attempts'] >= BUILD_ATTEMPTS:
                    return
        tool = self._load_toolchain()
        if not tool:
            if job is None:
                self.jobs[key] = dict(state='disabled', attempts=0, retry_at=0,
                                      reason='no build runtime')
                self.report.warn('本发行包未附带构建运行时，无法自动重建；'
                                 '已直通官方资源，请等待维护者更新')
            return
        if not WORKER.is_file():
            if job is None:
                self.jobs[key] = dict(state='disabled', attempts=0, retry_at=0,
                                      reason='build_worker.py missing')
                self.report.warn('构建脚本缺失，自动重建已禁用；官方资源直通')
            return

        attempts = (job['attempts'] if job else 0) + 1
        job_dir = WORK_DIR / f'{version}-{branch}'
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.report.error('无法创建构建临时目录', exc)
            return
        log_path = job_dir / f'worker-{attempts}.log'
        cmd = [tool['python'], '-B', str(WORKER),
               '--version', version, '--branch', branch,
               '--work', str(job_dir), '--attempt', str(attempts)]
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        env['MONMUSU_AUTO_BUILD'] = '1'
        if tool.get('dotnet'):
            dotnet = Path(tool['dotnet'])
            env['IL2CPP_DOTNET_ROOT'] = str(dotnet if dotnet.is_absolute()
                                            else ROOT / dotnet)
        if isinstance(tool.get('env'), dict):
            # Only loopback ARL overrides are honoured (local test servers).
            for name, value in tool['env'].items():
                if name == 'MONMUSU_ARL_BASE' and _loopback(value):
                    env[name] = str(value)
        self.report.status(f'正在后台准备新版本构建（第 {attempts} 次）…')
        try:
            log = open(log_path, 'wb')
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                    stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=NO_WINDOW)
        except OSError as exc:
            self.report.error('无法启动构建进程', exc)
            self.jobs[key] = dict(state='failed', attempts=attempts,
                                  retry_at=now + RETRY_DELAY, reason=str(exc))
            return
        state = dict(state='running', attempts=attempts, retry_at=0,
                     proc=proc, log=log, dir=job_dir, started=now)
        self.jobs[key] = state
        threading.Thread(target=self._wait, args=(key, state, log),
                         daemon=True).start()

    def _wait(self, key, state, log):
        """Background thread: wait for the worker, then reload resources OFF-loop."""
        rc = state['proc'].wait()
        try:
            log.close()
        except Exception:                                       # noqa: BLE001
            pass
        result = None
        try:
            result = json.loads((state['dir'] / 'result.json').read_text(encoding='utf-8'))
        except Exception:                                       # noqa: BLE001
            result = None
        if result is None:
            result = dict(status='failed',
                          reason=f'worker exited {rc} without writing result.json')
        result['returncode'] = rc
        result.setdefault('version', key[0])
        result.setdefault('branch', key[1])
        if result.get('activated'):
            try:
                result['payload'] = self._read_payload()
            except Exception as exc:                            # noqa: BLE001
                result['activated'] = False
                result['reason'] = f'新构建已写入但重新加载失败：{exc}'
                result['payload'] = None
        self._queue.put(result)

    # --------------------------------------------------------------- reload
    def _drain(self):
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                return
            key = (pending.get('version'), pending.get('branch'))
            job = self.jobs.get(key)
            if job is not None:
                job['state'] = 'done' if pending.get('activated') else 'failed'
                job['retry_at'] = 0 if pending.get('activated') else time.time() + RETRY_DELAY
            payload = pending.pop('payload', None)
            if not pending.get('activated'):
                reason = pending.get('reason') or '构建未通过校验'
                self.report.warn(f'新版本构建未完成：{reason}')
                self.report.warn('旧版仍可直接使用；未知版本保持官方资源直通')
                continue
            if payload is None:
                self.report.error('新构建已写入但资源重载结果缺失')
                continue
            self._apply_payload(payload)
            store = STORE.get(str(pending.get('branch')), '?')
            self.report.status(f'{store} / {pending.get("version")} 新补丁已构建完成')
            self.report.warn(f'校验级别：{pending.get("verification", "static")}'
                             '（静态结构校验；尚未经浏览器实机验证）')
            self.report.status('请刷新游戏页面以应用汉化')

    # -------------------------------------------------------------- cleanup
    def running(self):
        pass  # startup is reported from load(), which also runs on reloads

    def done(self):
        for job in self.jobs.values():
            proc = job.get('proc')
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass


def _loopback(value) -> bool:
    """Only a local test server may replace the official download base."""
    s = str(value)
    return (s.startswith('http://127.0.0.1:') or s.startswith('http://localhost:')
            or s.startswith('http://[::1]:'))


addons = [MonmusuChinese()]
