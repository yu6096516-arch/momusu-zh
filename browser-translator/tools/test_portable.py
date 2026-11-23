"""Developer smoke test: extracted player bundle, clean PATH, no trust-store changes."""
import ctypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / '.build/独立 解压 测试'
PLAYER = TEST / 'MonmusuTDChinese'
WIN = Path(os.environ['SystemRoot'])
PS = str(WIN / 'System32/WindowsPowerShell/v1.0/powershell.exe')
PORT = 19180


def wait_port(proc):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('bundled mitmdump exited: ' + str(proc.returncode))
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=.3):
                return
        except OSError:
            time.sleep(.2)
    raise RuntimeError('bundled service timed out')


def run():
    # Each run starts without a CA or an existing extracted runtime.
    target = TEST.resolve()
    assert target.is_relative_to(ROOT.resolve() / '.build') and target.name == '独立 解压 测试'
    if target.exists():
        shutil.rmtree(target)
    env = dict(os.environ)
    for name in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'):
        env.pop(name, None)
    env['PATH'] = str(WIN / 'System32') + ';' + str(WIN)
    env['PYTHONNOUSERSITE'] = '1'
    with zipfile.ZipFile(ROOT / 'dist/MonmusuTDChinese-v0.1.0-win-x64.zip') as z:
        assert not any('/data/' in n.lower() or n.lower().endswith(('.pem','.pfx','.p12','.key','.log')) for n in z.namelist())
        assert 'MonmusuTDChinese/初始化证书.bat' not in z.namelist()
        assert 'MonmusuTDChinese/runtime/init_certificate.py' not in z.namelist()
        assert z.testzip() is None
        z.extractall(TEST)
    ca = PLAYER / 'data/mitmproxy-ca-cert.pem'
    # Unit-level command checks against the EXTRACTED launch.ps1; no browser opens.
    check = subprocess.run([PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/test_launcher.ps1'),'-Root',str(PLAYER)],
                           env=env, capture_output=True, timeout=30)
    assert check.returncode == 0, check.stderr.decode(errors='replace')
    commands = json.loads(check.stdout)
    # Validate how Windows parses custom spaces/quotes. No command shell executes it.
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argc = ctypes.c_int()
    argv = parse('browser.exe ' + commands['customCommandLine'], ctypes.byref(argc))
    try:
        assert [argv[i] for i in range(argc.value)] == [
            'browser.exe','--proxy-server=http://127.0.0.1:9999','--title=中文 two words',
            '--literal=& echo nope','https://play.games.dmm.com/game/monmusutd']
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv,ctypes.c_void_p))
    config = PLAYER / 'config.ini'
    defaults = config.read_bytes()
    custom = '[network]\nlisten_port = {port}\nupstream_port =\n[browser]\ntype = custom\npath = "{exe}"\nargs =\n'
    try:
        config.write_text(custom.format(port='invalid',exe=PLAYER/'runtime/mitmdump.exe'),encoding='utf-8')
        bad = subprocess.run([PS,'-NoProfile','-File',str(PLAYER/'runtime/launch.ps1')],
                             env=env,capture_output=True,timeout=15)
        assert bad.returncode == 1
        config.write_text(custom.format(port=PORT,exe=PLAYER/'runtime/mitmdump.exe'),encoding='utf-8')
        with socket.socket() as busy:
            busy.bind(('127.0.0.1',PORT));busy.listen()
            occupied = subprocess.run([PS,'-NoProfile','-File',str(PLAYER/'runtime/launch.ps1')],
                                      env=env,capture_output=True,timeout=15)
            assert occupied.returncode == 1
        (ROOT / 'work/portable_config_errors.log').write_bytes(bad.stdout+bad.stderr+occupied.stdout+occupied.stderr)
    finally:
        config.write_bytes(defaults)
    manifest = json.loads((PLAYER / 'resources/assets.json').read_text())
    log = open(ROOT / 'work/portable_service.log', 'wb')
    proc = subprocess.Popen([str(PLAYER / 'runtime/mitmdump.exe'), '--listen-host','127.0.0.1',
                             '--listen-port',str(PORT),'--set','confdir='+str(PLAYER/'data'),
                             '--set','ssl_insecure=false','--set','upstream_cert=false',
                             '--set','flow_detail=0','-s',str(PLAYER / 'resources/addon_monmusu_cn.py')],
                            env=env, cwd=TEST, stdout=log, stderr=subprocess.STDOUT)
    checked = 0
    try:
        wait_port(proc)
        assert ca.is_file() and (PLAYER / 'data/mitmproxy-ca.pem').is_file()
        cert = subprocess.run([PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/test_certificate.ps1'),
                               '-Root',str(PLAYER)],env=env,capture_output=True,timeout=30)
        (ROOT / 'work/certificate_test.log').write_bytes(cert.stdout+cert.stderr)
        assert cert.returncode == 0, cert.stderr.decode(errors='replace')
        certificate_results = json.loads(cert.stdout.decode('utf-8',errors='replace').strip().splitlines()[-1])
        context = ssl.create_default_context(cafile=str(ca))
        # assets.json declares each wire file by bare name plus a resource
        # branch (0 = DMM, 1 = FANZA, "any" = shared). Exercise every declared
        # (branch, file) pair, plus the suffix-matched bundles.
        requests = []
        for item in manifest['assets']:
            branch = str(item.get('branch', 'any'))
            for b, prefix in manifest['version_paths'].items():
                if branch in ('any', str(b)):
                    requests.append((prefix + item['name'], item))
        for item in manifest['suffix_assets']:
            requests.append(('/test/' + item['suffix'], item))
        for path, item in requests:
            # The CA is trusted ONLY by this test client. HTTPS validation remains on.
            client = http.client.HTTPSConnection('127.0.0.1',PORT,context=context,timeout=30)
            client.set_tunnel(manifest['host'],443)
            client.request('GET',path)
            response = client.getresponse()
            body = response.read()
            assert response.status == 200, (path,response.status)
            assert body == (PLAYER / item['file']).read_bytes(), path
            assert response.getheader('Content-Encoding') == item.get('encoding')
            assert response.getheader('Content-Type') == item['content_type']
            if item.get('cors'):
                assert response.getheader('Access-Control-Allow-Origin') == '*'
            client.close()
            checked += 1
        head_item = next(i for i in manifest['assets']
                         if i['name'] == 'ntd.wasm.gz' and str(i['branch']) == '1')
        head_path = manifest['version_paths']['1'] + head_item['name']
        client = http.client.HTTPSConnection('127.0.0.1',PORT,context=context,timeout=10)
        client.set_tunnel(manifest['host'],443)
        client.request('HEAD',head_path)
        response = client.getresponse()
        assert response.status == 200 and response.read() == b''
        assert int(response.getheader('Content-Length')) == (PLAYER / head_item['file']).stat().st_size
        client.close()
    finally:
        subprocess.run([str(WIN/'System32/taskkill.exe'),'/PID',str(proc.pid),'/T','/F'],
                       capture_output=True,timeout=15)
        proc.wait(timeout=15)
        log.close()
    result = dict(status='PASS', extractedPath=PLAYER.relative_to(ROOT).as_posix(),
                  cleanPath=True, launcherChecks=commands['checks'], windowsArgumentParsing='PASS',
                  localCertificateGenerated=True, systemCertificateStoreModified=False,
                  certificateChecks=certificate_results['checks'], nativeWindowsImport='NOT RUN (mocked)',
                  invalidPort='PASS', occupiedPort='PASS',
                  verifiedTlsResources=checked, head='PASS', browser='NOT RUN', consoleClose='NOT RUN')
    (ROOT / 'work/portable_test.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    run()
