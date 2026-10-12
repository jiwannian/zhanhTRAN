"""Isolated Zotero host and loopback-only provider; never installs into the daily profile."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
import time
import uuid
import zipfile
import pymupdf as fitz
from native_report import read_report

ROOT = Path(__file__).resolve().parents[1]
qa = ROOT/'qa'/('run-'+uuid.uuid4().hex)
profile, data, fixtures = qa/'profile', qa/'data', qa/'fixtures'
for folder in [profile/'extensions', data, fixtures]:
    folder.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('fixtures', ROOT/'tests/local_parser_test.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.fixtures(fixtures)
fit_spec = importlib.util.spec_from_file_location('fit_fixture', ROOT/'tests/pdf_fit_test.py')
fit_fixture = importlib.util.module_from_spec(fit_spec)
fit_spec.loader.exec_module(fit_fixture)
fit_fixture.fixture(fixtures/'fit.pdf')
fit_fixture.fixture(fixtures/'fit-rotated.pdf', rotation=90)
with fitz.open(fixtures/'digital.pdf') as pdf:
    pdf[0].set_rotation(90)
    pdf.save(fixtures/'rotated.pdf')
for name, count in [('timeout.pdf', 4), ('timeout-single.pdf', 1)]:
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        for i in range(count):
            page.insert_text((40, 60+i*90), 'Synthetic timeout paragraph '+str(i), fontsize=12)
        pdf.save(fixtures/name)
requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content = body['messages'][-1]['content']
        units = json.loads(content) if content.startswith('[') else None
        requests.append({'path': self.path, 'model': body.get('model'),
                         'ids': [unit['id'] for unit in units] if units is not None else []})
        count = sum(request['model'] == body.get('model') for request in requests)
        if body.get('model') == 'fixture-retry' and count <= 5:
            self.send_response(503)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if (body.get('model') in ['fixture-timeout-exhaust', 'fixture-timeout-cancel'] or
            (body.get('model') == 'fixture-timeout-retry' and count <= 5) or
            (body.get('model') == 'fixture-timeout-shrink' and len(units) > 2)):
            self.send_response(524)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if body.get('model') == 'fixture-cancel':
            time.sleep(8)
        # Deterministic compact text keeps long table/figure fixtures in their original boxes.
        translation = json.dumps([
            {**unit, 'text': fit_fixture.TEXT if unit['text'].startswith('Synthetic original layout') else
                unit['text'] if unit['text'].strip().isdigit() or unit['text'].startswith('Other column') else '译文 '+unit['text']}
            for unit in units], ensure_ascii=False) if units is not None else '你好，世界。'
        response = {'content': [{'type': 'text', 'text': translation}], 'stop_reason': 'end_turn'} if self.path.endswith('/messages') else {
            'choices': [{'finish_reason': 'stop', 'message': {'content': translation}}]}
        payload = json.dumps(response, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if body.get('model') != 'fixture-cancel':
                raise

    def do_GET(self):
        if self.path != '/qa-counts':
            self.send_error(404)
            return
        payload = json.dumps({model: sum(r['model'] == model for r in requests)
                              for model in ['fixture-retry', 'fixture-cancel', 'fixture-timeout-retry',
                                            'fixture-timeout-shrink', 'fixture-timeout-exhaust',
                                            'fixture-timeout-cancel']}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
def manifest(name, identifier):
    return {'manifest_version': 2, 'name': name, 'version': '1.0.0', 'applications': {'zotero': {
        'id': identifier, 'strict_min_version': '9.0', 'strict_max_version': '10.*',
        'update_url': 'https://fixture.invalid/update.json'}}}
with zipfile.ZipFile(profile/'extensions'/'zhanhtran-qa@local.invalid.xpi', 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('manifest.json', json.dumps(manifest('zhanhTRAN QA', 'zhanhtran-qa@local.invalid')))
    archive.writestr('bootstrap.js', (ROOT/'tools/native-verifier.js').read_bytes())
config = {'xpi': str(ROOT/'dist/zhanhTRAN-1.0.1.xpi'), 'fixtures': str(fixtures),
          'provider': 'http://127.0.0.1:'+str(server.server_port), 'python': str(Path(os.sys.executable))}
(profile/'native-config.json').write_text(json.dumps(config), encoding='utf-8')
prefs = {'extensions.autoDisableScopes': 0, 'extensions.enabledScopes': 15, 'extensions.startupScanScopes': 15,
         'extensions.sideloadScopes': 15, 'extensions.zotero.dataDir': str(data), 'extensions.zotero.useDataDir': True,
         'extensions.zotero.firstRun2': False, 'extensions.zotero.sync.autoSync': False, 'app.update.auto': False,
         'extensions.update.enabled': False, 'browser.shell.checkDefaultBrowser': False,
         'toolkit.telemetry.enabled': False, 'xpinstall.signatures.required': False,
         'toolkit.startup.max_resumed_crashes': -1}
(profile/'user.js').write_text('\n'.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});' for k, v in prefs.items()), encoding='utf-8')
command = [os.environ.get('ZHANHTRAN_ZOTERO', r'C:\Program Files\Zotero\zotero.exe'), '-no-remote', '-profile', str(profile), '-datadir', str(data)]
print('COMMAND='+subprocess.list2cmdline(command), flush=True)
print('QA_ROOT='+str(qa), flush=True)
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = 0
with (qa/'stdout.txt').open('w', encoding='utf-8') as stdout, (qa/'stderr.txt').open('w', encoding='utf-8') as stderr:
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, startupinfo=startup,
                               env={**os.environ, 'MOZ_DISABLE_LAUNCHER_PROCESS': '1',
                                    'MOZ_DISABLE_SAFE_MODE_KEY': '1'})
    try:
        deadline = time.monotonic()+600
        report = None
        while time.monotonic() < deadline:
            report = read_report(profile/'native-result.json')
            if report and report['result'] != 'RUNNING':
                break
            # Gecko can exit its launcher successfully while the isolated host restarts.
            if process.poll() not in (None, 0):
                raise RuntimeError('Isolated host exited with '+str(process.returncode)+'; see '+str(qa))
            time.sleep(1)
        if not report or report['result'] == 'RUNNING':
            raise RuntimeError('Isolated host timed out; see '+str(qa))
        report['xpi_sha256'] = hashlib.sha256((ROOT/'dist/zhanhTRAN-1.0.1.xpi').read_bytes()).hexdigest()
        report['requests'] = requests
        (qa/'host-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['result'] == 'PASS':
            assert sum(r['model'] == 'fixture-retry' for r in requests) == 6
            assert sum(r['model'] == 'fixture-cancel' for r in requests) == 1
            expected = {'fixture-timeout-retry': [1]*6, 'fixture-timeout-shrink': [4, 2, 2],
                        'fixture-timeout-exhaust': [4, 2, 1, 1, 1, 1], 'fixture-timeout-cancel': [4]}
            for model, widths in expected.items():
                assert [len(r['ids']) for r in requests if r['model'] == model] == widths, model
            assert [r['ids'] for r in requests if r['model'] == 'fixture-timeout-shrink'] == [
                ['u0', 'u1', 'u2', 'u3'], ['u0', 'u1'], ['u2', 'u3']]
        for case in report.get('reliability', []):
            case['source_sha256'] = hashlib.sha256(Path(case['sourcePath']).read_bytes()).hexdigest()
            for output in case['outputs']:
                path = Path(output['path'])
                output['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                with fitz.open(case['sourcePath']) as original, fitz.open(path) as pdf:
                    assert len(pdf) == len(original)
                    assert abs(pdf[0].rect.width-original[0].rect.width*(2 if '-bilingual' in path.stem else 1)) < .1
                    assert abs(pdf[0].rect.height-original[0].rect.height) < .1
                    output['dimensions'] = [pdf[0].rect.width, pdf[0].rect.height]
                    if case['model'].startswith('fixture-timeout-'):
                        assert '译文' in pdf[0].get_text()
                        pdf[0].get_pixmap().save(qa/(case['model']+'-'+path.stem+'.png'))
        for case in report.get('checks', []):
            source = Path(case['sourcePath'])
            case['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            for output in case['outputs']:
                path = Path(output['path'])
                output['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.suffix == '.pdf':
                    with fitz.open(source) as original, fitz.open(path) as pdf:
                        bilingual = '-bilingual' in path.stem
                        assert len(pdf) == len(original)
                        assert abs(pdf[0].rect.width-original[0].rect.width*(2 if bilingual else 1)) < .1
                        assert abs(pdf[0].rect.height-original[0].rect.height) < .1
                        output['dimensions'] = [pdf[0].rect.width, pdf[0].rect.height]
                        if bilingual:
                            offset = 0 if case['order'] == 'original-left' else original[0].rect.width
                            rect = fitz.Rect(offset, 0, offset+original[0].rect.width, original[0].rect.height)
                            # Align fractional-width right panels to the source raster origin.
                            matrix = fitz.Matrix(1, 0, 0, 1, -offset, 0)
                            assert pdf[0].get_pixmap(matrix=matrix, clip=rect).samples == original[0].get_pixmap().samples
                            output['originalPanelExact'] = True
                        if case['name'].startswith('fit'):
                            assert ''.join(fit_fixture.TEXT.split()) in ''.join(pdf[0].get_text().split())
                            output['expandedTextComplete'] = True
                            png = qa/(path.parent.name+'-'+path.stem+'.png')
                            pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(png)
                            output['rendered'] = str(png)
                        pdf[0].get_pixmap(matrix=fitz.Matrix(.7,.7)).save(qa/(str(len(case['name']))+'-'+path.stem+'.png'))
        (ROOT/'logs/native-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if report['result'] != 'PASS':
            raise RuntimeError('Native verification failed')
    finally:
        cleanup = '$p=Get-CimInstance Win32_Process -Filter "Name=\'zotero.exe\'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($env:ZHANHTRAN_QA_PROFILE)}; foreach($i in $p){taskkill.exe /PID $i.ProcessId /T /F | Out-Null}'
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', cleanup],
                       env={**os.environ, 'ZHANHTRAN_QA_PROFILE': str(profile)}, capture_output=True, timeout=30)
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        server.shutdown()
