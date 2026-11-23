#!/usr/bin/env python3
"""Routing tests for resources/addon_monmusu_cn.py (no mitmproxy needed).

Stubs just enough of mitmproxy to import the addon and drives `resolve()` /
`request()` directly. Proves:

  * /0/Build/ and /1/Build/ both resolve, to the SAME shared files but to
    DIFFERENT Wasm and ntd.data;
  * nothing outside the declared prefixes is ever replaced;
  * a widened/inconsistent version_paths is rejected at load time;
  * unknown file names under a valid prefix are not served.

    python -B tools/test_addon_routing.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / 'resources' / 'addon_monmusu_cn.py'
ASSETS = ROOT / 'resources' / 'assets.json'

fails = 0
checks = 0


def ok(msg):
    global checks
    checks += 1
    print(f'  ok   {msg}')


def bad(msg):
    global checks, fails
    checks += 1
    fails += 1
    print(f'  FAIL {msg}')


def check(cond, msg):
    ok(msg) if cond else bad(msg)


def install_stub():
    """Minimal mitmproxy surface: ctx.log.info / http.HTTPFlow / http.Response."""
    class Response:
        @staticmethod
        def make(code, content, headers):
            return types.SimpleNamespace(status_code=code, raw_content=content,
                                         headers=dict(headers))

    class HTTPFlow:
        pass

    http = types.ModuleType('mitmproxy.http')
    http.HTTPFlow = HTTPFlow
    http.Response = Response
    mitm = types.ModuleType('mitmproxy')
    mitm.ctx = types.SimpleNamespace(log=types.SimpleNamespace(info=lambda *a: None))
    mitm.http = http
    sys.modules['mitmproxy'] = mitm
    sys.modules['mitmproxy.http'] = http


def load_addon(path=ADDON):
    install_stub()
    spec = importlib.util.spec_from_file_location('addon_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    addon = mod.addons[0]
    addon.load(None)
    return addon


def main():
    cfg = json.loads(ASSETS.read_text(encoding='utf-8'))
    p0 = cfg['version_paths']['0']
    p1 = cfg['version_paths']['1']
    idx = 'monmusu_cn_story_index.json'
    wasm = 'ntd.wasm.gz'
    data = 'ntd.data.gz'
    loader = 'ntd.loader.js'

    addon = load_addon()

    print('1) both branches are routable')
    for prefix, branch in ((p0, '0'), (p1, '1')):
        check(addon.resolve(prefix + idx) is not None,
              f'{branch}: story index served')
        check(addon.resolve(prefix + loader) is not None,
              f'{branch}: loader served')
        check(addon.resolve(prefix + wasm) is not None,
              f'{branch}: wasm served')
        check(addon.resolve(prefix + data) is not None,
              f'{branch}: ntd.data served')

    print('\n2) shared files are identical, branch-specific files are not')
    check(addon.resolve(p0 + idx)[0] == addon.resolve(p1 + idx)[0],
          'story index bytes shared across branches')
    check(addon.resolve(p0 + loader)[0] == addon.resolve(p1 + loader)[0],
          'loader bytes shared across branches')
    check(addon.resolve(p0 + wasm)[0] != addon.resolve(p1 + wasm)[0],
          'wasm differs per branch')
    check(addon.resolve(p0 + data)[0] != addon.resolve(p1 + data)[0],
          'ntd.data differs per branch')

    print('\n3) the served files are the declared ones')
    declared0 = {a['name']: a['file'] for a in cfg['assets']
                 if a.get('branch') in ('0', 'any')}
    declared1 = {a['name']: a['file'] for a in cfg['assets']
                 if a.get('branch') in ('1', 'any')}
    for name, rel in declared0.items():
        check(addon.resolve(p0 + name)[0] == (ROOT / rel).read_bytes(),
              f'0: {name} -> {rel}')
    for name, rel in declared1.items():
        check(addon.resolve(p1 + name)[0] == (ROOT / rel).read_bytes(),
              f'1: {name} -> {rel}')

    print('\n4) the whitelist is not widened')
    for probe in (p0 + 'ntd.framework.js',
                  p0 + 'StreamingAssets/whatever.bin',
                  p0 + '../Build/ntd.wasm.gz',
                  '/web/arl/202611021000/0/index.html',
                  '/web/arl/202611021000/2/Build/ntd.wasm.gz',
                  '/web/arl/999999999999/0/Build/ntd.wasm.gz',
                  '/other/path/ntd.wasm.gz'):
        check(addon.resolve(probe) is None, f'refused: {probe}')

    print('\n5) suffix assets still match by file name')
    for spec in cfg['suffix_assets']:
        hit = addon.resolve('/assetbundles/ver_deadbeef/webgl_nrm/' + spec['suffix'])
        check(hit is not None and hit[0] == (ROOT / spec['file']).read_bytes(),
              f'suffix {spec["suffix"][:12]}… -> {spec["file"]}')

    print('\n6) requests for other hosts are left alone')
    flow = types.SimpleNamespace(
        request=types.SimpleNamespace(host='example.com', method='GET',
                                      path=p0 + wasm),
        response=None)
    addon.request(flow)
    check(flow.response is None, 'foreign host untouched')
    flow2 = types.SimpleNamespace(
        request=types.SimpleNamespace(
            host=cfg['host'], method='HEAD', path=p1 + wasm + '?v=1'),
        response=None)
    addon.request(flow2)
    check(flow2.response is not None and flow2.response.status_code == 200,
          'HEAD on /1/ wasm answered 200')
    check(flow2.response.headers.get('Cache-Control', '').startswith('no-store'),
          'cache-control forced')

    print('\n7) inconsistent configuration is rejected at load time')
    tmp = ROOT / 'work' / 'assets_routing_test'
    tmp.mkdir(parents=True, exist_ok=True)
    bad_cfg = json.loads(ASSETS.read_text(encoding='utf-8'))
    bad_cfg['version_paths'] = {'0': p0, '1': p0.replace('/0/Build/', '/2/Build/')}
    p = tmp / 'assets.json'
    p.write_text(json.dumps(bad_cfg, ensure_ascii=False), encoding='utf-8')
    try:
        load_addon_with_config(p)
        bad('mismatched branch prefix was accepted')
    except ValueError as e:
        check('points at branch' in str(e) or 'version_paths' in str(e),
              'mismatched branch prefix rejected')

    # Phase 33: the two branches may legitimately sit on DIFFERENT official
    # builds (DMM and FANZA do not always update together). That must be
    # supported, but the resources must never be mixed: each prefix keeps its
    # own Wasm/data table and the other branch's version is not served.
    mixed = json.loads(ASSETS.read_text(encoding='utf-8'))
    mixed['version_paths'] = {'0': p0,
                              '1': p1.replace('202611021000', '202611021001')}
    p.write_text(json.dumps(mixed, ensure_ascii=False), encoding='utf-8')
    try:
        install_stub()
        spec = importlib.util.spec_from_file_location('addon_mixed', ADDON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG = p
        split = mod.addons[0]
        split.load(None)
        p1_new = mixed['version_paths']['1']
        check(split.supported == {'0': '202611021000', '1': '202611021001'},
              'branch-independent versions loaded')
        check(split.resolve(p0 + wasm) is not None
              and split.resolve(p1_new + wasm) is not None,
              'both branch prefixes still served')
        check(split.resolve(p1 + wasm) is None,
              'FANZA old version no longer served after independent update')
        check(split.resolve(p0 + wasm)[0] != split.resolve(p1_new + wasm)[0],
              'branch Wasm still isolated across different build ids')
        check(split.resolve(p0 + wasm)[0] == addon.resolve(p0 + wasm)[0]
              and split.resolve(p1_new + wasm)[0] == addon.resolve(p1 + wasm)[0],
              'each branch kept its own Wasm, none swapped')
    except ValueError as e:
        bad(f'branch-independent versions raised {e}')

    print('')
    if fails:
        print(f'{fails}/{checks} addon routing checks FAILED')
        return 1
    print(f'{checks} addon routing checks passed')
    return 0


def load_addon_with_config(config_path: Path):
    """Load the addon against a temporary assets.json (CONFIG is overridable)."""
    install_stub()
    spec = importlib.util.spec_from_file_location('addon_variant', ADDON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONFIG = config_path
    mod.addons[0].load(None)


if __name__ == '__main__':
    sys.exit(main())
