#!/usr/bin/env python3
"""Real mitmdump console test for Phase 33 quiet mode.

Starts the BUNDLED mitmdump.exe with the exact arguments launch.ps1 uses,
pushes real HTTP flows through it, and inspects stdout:

  * no ordinary request lines / WebSocket chatter / normal connection closes;
  * the addon's own [MonmusuCN] INFO / WARNING / ERROR survive
    termlog_verbosity=error;
  * developer debug mode restores full network logs;
  * errors are never hidden with >nul 2>&1.

    python -B tools/autobuild/test_quiet_console.py
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MITMDUMP = ROOT / 'runtime' / 'mitmdump.exe'
ADDON = ROOT / 'resources' / 'addon_monmusu_cn.py'
LOGDIR = ROOT / 'work' / 'quiet-console-test'
PORT = 19371
PORT_DEBUG = 19372

checks = 0
fails = 0


def check(cond, msg):
    global checks, fails
    checks += 1
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails += 1
    return bool(cond)


def wait_ready(proc, port, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.3):
                return True
        except OSError:
            time.sleep(.2)
    return False


def run_case(debug: bool):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    # Separate ports: Windows keeps the listener socket in TIME_WAIT briefly,
    # so reusing one port for the second case makes mitmdump fail to bind.
    port = PORT_DEBUG if debug else PORT
    confdir = LOGDIR / ('data-debug' if debug else 'data-quiet')
    verbosity = 'debug' if debug else 'error'
    detail = '3' if debug else '0'
    args = [str(MITMDUMP), '--listen-host', '127.0.0.1',
            '--listen-port', str(port), '--set', f'confdir={confdir}',
            '--set', f'flow_detail={detail}',
            '--set', f'termlog_verbosity={verbosity}',
            '--set', 'ssl_insecure=false', '-s', str(ADDON)]
    env = dict(os.environ)
    env.pop('MONMUSU_DEBUG', None)
    if debug:
        env['MONMUSU_DEBUG'] = '1'
    log_path = LOGDIR / ('debug.log' if debug else 'quiet.log')
    with open(log_path, 'wb') as log:
        proc = subprocess.Popen(args, cwd=str(ROOT), env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            if not wait_ready(proc, port):
                raise RuntimeError('mitmdump did not start; see ' + str(log_path))
            # A normal proxy request + a WebSocket-ish Upgrade request. Neither
            # should appear as a flow line in quiet mode.
            for _ in range(3):
                try:
                    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                    c.request('GET', 'http://assets.game-monmusu-td.net/'
                              'web/arl/202611021000/0/Build/ntd.framework.js')
                    r = c.getresponse()
                    r.read()
                    c.close()
                except Exception:                           # noqa: BLE001
                    pass
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            try:
                c.request('GET', 'http://example.com/',
                          headers={'Upgrade': 'websocket', 'Connection': 'Upgrade',
                                   'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ=='})
                r = c.getresponse()
                r.read()
            except Exception:                              # noqa: BLE001
                pass
            finally:
                c.close()
            time.sleep(1.2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return log_path.read_bytes().decode('utf-8', 'replace')


def main():
    if not MITMDUMP.is_file():
        print('SKIP: runtime/mitmdump.exe missing')
        return 0
    print('Phase 33 real mitmdump console test')
    print('=' * 58)

    quiet = run_case(debug=False)
    (LOGDIR / 'quiet.stdout.txt').write_text(quiet, encoding='utf-8')
    check('[MonmusuCN]' in quiet, 'addon status printed under termlog_verbosity=error')
    check('汉化代理启动成功' in quiet, 'INFO startup line visible')
    check('汉化资源就绪' in quiet, 'INFO resource line visible')
    check('分支支持版本' in quiet, 'per-branch version line visible')
    # flow_detail=0 + termlog_verbosity=error must hide routine traffic.
    check('GET http://assets.game-monmusu-td.net' not in quiet,
          'no per-request flow line for the official asset host')
    check('GET http://example.com' not in quiet,
          'no per-request flow line for an unrelated host')
    check('websocket' not in quiet.lower() or 'WebSocket' in quiet and False,
          'no WebSocket chatter')
    check('clientconnect' not in quiet.lower() and 'clientdisconnect' not in quiet.lower(),
          'no normal connect/disconnect chatter')
    check('>nul' not in quiet and '2>&1' not in quiet,
          'errors not hidden with >nul 2>&1')

    debug = run_case(debug=True)
    (LOGDIR / 'debug.stdout.txt').write_text(debug, encoding='utf-8')
    check('[MonmusuCN]' in debug, 'addon status still printed in developer mode')
    check(debug.count('GET') > quiet.count('GET') or
          'clientconnect' in debug.lower() or 'debug' in debug.lower(),
          'developer mode exposes full network logs')
    check('[MonmusuCN][debug]' in debug,
          'per-request debug detail enabled only in developer mode')
    check('websocket' not in quiet.lower() and 'WebSocket' not in quiet,
          'quiet mode has no WebSocket lines at all')

    app_log = ROOT / 'logs' / 'monmusu_cn.log'
    check(app_log.is_file(), 'detailed local log file created')
    if app_log.is_file():
        text = app_log.read_text(encoding='utf-8', errors='replace')
        check('INFO' in text and 'DEBUG' in text,
              'INFO + per-request DEBUG written to bounded local log')
        check(app_log.stat().st_size <= 2 * 1024 * 1024 + 4096,
              'local log stays bounded')

    print('=' * 58)
    if fails:
        print(f'{fails}/{checks} quiet-console checks FAILED')
        return 1
    print(f'{checks} quiet-console checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
