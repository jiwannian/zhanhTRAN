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
os.sys.path.insert(0,str(ROOT))  # Shared synthetic fixture modules live in tests/.
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
toc_spec = importlib.util.spec_from_file_location('toc_fixture', ROOT/'tests/toc_layout_test.py')
toc_fixture = importlib.util.module_from_spec(toc_spec)
toc_spec.loader.exec_module(toc_fixture)
for name,rotation in [('toc.pdf',0),('toc-rotated.pdf',90)]:
    toc_fixture.fixture(fixtures/name,texts=['目录测试','下一条目'],height=400,start=310,rotation=rotation,next_page=True)
ocr_spec = importlib.util.spec_from_file_location('ocr_fixture', ROOT/'tests/ocr_layout_test.py')
ocr_fixture = importlib.util.module_from_spec(ocr_spec)
ocr_spec.loader.exec_module(ocr_fixture)
for name,rotation in [('ocr-label.pdf',0),('ocr-label-rotated.pdf',90)]:
    ocr_fixture.fixture(fixtures/name,rotation=rotation)
tight_fixtures = []
for name, background, rotation in [('ocr-tight-light.pdf', 'white', 0), ('ocr-tight-dark.pdf', 'black', 90)]:
    document = ocr_fixture.fixture(fixtures/name, text='合成地图标签', narrow=True,
                                   label_box=ocr_fixture.OCR8_BOX, background=background, rotation=rotation)
    document_path = fixtures/(name+'.json')
    document_path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    tight_fixtures.append({'source':str(fixtures/name), 'document':str(document_path)})
geometry_spec = importlib.util.spec_from_file_location('geometry_fixture', ROOT/'tests/flow_geometry_test.py')
mixed_spec = importlib.util.spec_from_file_location('mixed_fixture',ROOT/'tests/ocr_shaped_test.py')
mixed_fixture = importlib.util.module_from_spec(mixed_spec)
mixed_spec.loader.exec_module(mixed_fixture)
mixed_fixtures = []
for background in ('white','black'):
    for rotation in (0,90):
        name = f'ocr-mixed-{background}-{rotation}.pdf'
        document = mixed_fixture.fixture(fixtures/name,background=background,rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        mixed_fixtures.append({'source':str(fixtures/name),'background':background,'rotation':rotation})
from tests import ocr_ink_test as ink_fixture
ink_fixtures = []
for background in ('white', 'black'):
    for rotation in (0, 90):
        for neighbor in (False, True):
            name = f'ocr-ink-{background}-{rotation}-{neighbor}.pdf'
            document = ink_fixture.fixture(fixtures/name, background=background, rotation=rotation,
                                           translated_neighbor=neighbor)
            (fixtures/(name+'.json')).write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
            ink_fixtures.append({'source':str(fixtures/name), 'background':background,
                                 'rotation':rotation, 'translatedNeighbor':neighbor})
precision_spec = importlib.util.spec_from_file_location('precision_fixture',ROOT/'tests/ocr_precision_test.py')
precision_fixture = importlib.util.module_from_spec(precision_spec)
precision_spec.loader.exec_module(precision_fixture)
precision_fixtures = []
for background in ('white','black'):
    for rotation in (0,90):
        name = f'ocr-precision-{background}-{rotation}.pdf'
        document = precision_fixture.fixture(fixtures/name,background=background,rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        precision_fixtures.append({'source':str(fixtures/name),'background':background,'rotation':rotation})
name = 'ocr-precision-tall-glyph.pdf'
document = precision_fixture.fixture(fixtures/name,text='（AB/CD/系统）')
(fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
precision_fixtures.append({'source':str(fixtures/name),'background':'white','rotation':0,'glyphPadding':True})
cjk_spec = importlib.util.spec_from_file_location('cjk_fixture',ROOT/'tests/ocr_cjk_test.py')
cjk_fixture = importlib.util.module_from_spec(cjk_spec)
cjk_spec.loader.exec_module(cjk_fixture)
cjk_fixtures = []
for background in ('white','black'):
    for rotation in (0,90):
        name = f'ocr-cjk-{background}-{rotation}.pdf'
        document = cjk_fixture.fixture(fixtures/name,background=background,rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        cjk_fixtures.append({'source':str(fixtures/name),'background':background,'rotation':rotation,'lines':1})
name = 'ocr-cjk-wrap.pdf'
document = cjk_fixture.fixture(fixtures/name,right_blocker=True)
(fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
cjk_fixtures.append({'source':str(fixtures/name),'background':'white','rotation':0,'lines':2})
edge_spec = importlib.util.spec_from_file_location('edge_fixture',ROOT/'tests/ocr_edge_test.py')
edge_fixture = importlib.util.module_from_spec(edge_spec)
edge_spec.loader.exec_module(edge_fixture)
edge_fixtures = []
for edge in ('left','top'):
    for background in ('white','black'):
        rotation = 90 if edge=='top' else 0
        name = f'ocr-edge-{edge}-{background}-{rotation}.pdf'
        document = edge_fixture.fixture(fixtures/name,edge=edge,background=background,rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        edge_fixtures.append({'source':str(fixtures/name),'background':background,'rotation':rotation,'edge':edge})
name = 'ocr-edge-wrap.pdf'
document = edge_fixture.fixture(fixtures/name,right_blocker=True)
(fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
edge_fixtures.append({'source':str(fixtures/name),'background':'white','rotation':0,'edge':'left'})
from tests import ocr_geometry_test as quad_fixture
quad_fixtures = []
for background in ('white', 'black'):
    for rotation in (0, 90):
        name = f'ocr-quad-{background}-{rotation}.pdf'
        document = quad_fixture.fixture(fixtures/name, background=background, rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        quad_fixtures.append({'source':str(fixtures/name), 'background':background, 'rotation':rotation})
detected_quad_fixtures = []
for rotation in (0, 90):
    name = f'ocr-detected-quad-{rotation}.pdf'
    quad_fixture.real_ocr_fixture(fixtures/name, rotation=rotation)
    detected_quad_fixtures.append({'source':str(fixtures/name), 'rotation':rotation})
from tests import conditional_prose_test as conditional_fixture
conditional_fixtures = []
for rotation in (0, 90):
    for overflow in (False, True):
        name = f'conditional-prose-{rotation}-{overflow}.pdf'
        conditional_fixture.source_pdf(fixtures/name, rotation=rotation, overflow=overflow)
        conditional_fixtures.append({'source':str(fixtures/name), 'rotation':rotation, 'overflow':overflow})
geometry_fixture = importlib.util.module_from_spec(geometry_spec)
geometry_spec.loader.exec_module(geometry_fixture)
geometry_fixtures = []
for cached in (True,False):
    for rotation in (0,90):
        name = f'flow-geometry-{cached}-{rotation}.pdf'
        document = geometry_fixture.fixture(fixtures/name,cached=cached,rotation=rotation)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        geometry_fixtures.append({'source':str(fixtures/name),'cached':cached,'rotation':rotation})
table_spec = importlib.util.spec_from_file_location('table_fixture',ROOT/'tests/table_flow_test.py')
table_fixture = importlib.util.module_from_spec(table_spec)
table_spec.loader.exec_module(table_fixture)
table_fixtures = []
for mode in ('grow','many','tall'):
    for rotation in (0,90):
        name = f'table-flow-{mode}-{rotation}.pdf'
        document = table_fixture.fixture(fixtures/name,mode=mode,rotation=rotation,next_page=True,shaded=True)
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        table_fixtures.append({'source':str(fixtures/name),'mode':mode,'rotation':rotation})
for mode in ('grow','many','tall'):
    for rotation in (0,90):
        name = f'table-tiles-{mode}-{rotation}.pdf'
        document = table_fixture.fixture(fixtures/name,mode=mode,rotation=rotation,
                                          next_page=True,shaded=True,border='tiles')
        (fixtures/(name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        table_fixtures.append({'source':str(fixtures/name),'mode':mode,'rotation':rotation,'border':'tiles'})
from tests import fixed_layout_test as fixed_fixture
caption_fixtures,symbol_fixtures=[],[]
for rotation in (0,90):
    source=fixtures/f'fixed-captions-{rotation}.pdf'
    fixed_fixture.caption_source(source,rotation)
    caption_fixtures.append({'source':str(source),'rotation':rotation})
    source=fixtures/f'fixed-symbols-{rotation}.pdf'
    fixed_fixture.symbol_source(source)
    if rotation:
        with fitz.open(source) as pdf:
            pdf[0].set_rotation(rotation);pdf.saveIncr()
    symbol_fixtures.append({'source':str(source),'rotation':rotation})
from tests import prefix_symbols_test as prefix_fixture
prefix_fixtures,cached_prefix_fixtures=[],[]
for rotation in (0,90):
    source=fixtures/f'prefix-symbols-{rotation}.pdf'
    prefix_fixture.prefix_source(source,rotation)
    prefix_fixtures.append({'source':str(source),'rotation':rotation})
    source=fixtures/f'cached-prefix-symbols-{rotation}.pdf'
    document=prefix_fixture.fixture(source,rotation,legacy=True)
    (fixtures/(source.name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
    cached_prefix_fixtures.append({'source':str(source),'rotation':rotation})
from tests import paragraph_flow_test as paragraph_fixture
paragraph_fixtures,cached_paragraph_fixtures=[],[]
for rotation in (0,90):
    source=fixtures/f'paragraph-flow-{rotation}.pdf'
    paragraph_fixture.paragraph_source(source,rotation,next_page=True)
    paragraph_fixtures.append({'source':str(source),'rotation':rotation})
    source=fixtures/f'cached-paragraph-flow-{rotation}.pdf'
    document=paragraph_fixture.fixture(source,rotation,cached=True,next_page=True)
    (fixtures/(source.name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
    cached_paragraph_fixtures.append({'source':str(source),'rotation':rotation})
from tests import list_continuity_test as continuity_fixture
numbered_fixtures,cached_numbered_fixtures,raised_fixtures,cached_raised_fixtures=[],[],[],[]
for kind,fresh,cached in (('list',numbered_fixtures,cached_numbered_fixtures),
                          ('raised',raised_fixtures,cached_raised_fixtures)):
    for rotation in (0,90):
        source=fixtures/f'continuity-{kind}-{rotation}.pdf'
        if kind=='list':continuity_fixture.list_source(source,rotation,next_page=True)
        else:continuity_fixture.raised_source(source,rotation)
        fresh.append({'source':str(source),'rotation':rotation,'kind':kind})
        source=fixtures/f'cached-continuity-{kind}-{rotation}.pdf'
        document=continuity_fixture.fixture(source,rotation,cached=True,kind=kind,next_page=kind=='list')
        (fixtures/(source.name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        cached.append({'source':str(source),'rotation':rotation,'kind':kind})
from tests import definition_rows_test as definition_fixture
definition_fixtures,cached_definition_fixtures,column_definition_fixtures,wrapped_definition_fixtures,styled_definition_fixtures=[],[],[],[],[]
for mode,group in (('fresh',definition_fixtures),('cached',cached_definition_fixtures),
                   ('columns',column_definition_fixtures),('wrapped',wrapped_definition_fixtures),
                   ('styled',styled_definition_fixtures)):
    for rotation in (0,90):
        source=fixtures/f'definition-{mode}-{rotation}.pdf'
        if mode=='cached':
            document=definition_fixture.fixture(source,rotation,cached=True,next_page=True)
            (fixtures/(source.name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        else:
            definition_fixture.definition_source(source,rotation,mode='columns' if mode=='columns' else 'rows',
                wrapped=mode=='wrapped',styled=mode=='styled',next_page=True)
        group.append({'source':str(source),'rotation':rotation,'styled':mode=='styled'})
from tests import definition_variants_test as variant_fixture
variant_fixtures,cached_variant_fixtures,split_key_fixtures,unit_definition_fixtures=[],[],[],[]
for mode,group in (('mixed',variant_fixtures),('cached',cached_variant_fixtures),
                   ('split',split_key_fixtures),('units',unit_definition_fixtures)):
    for rotation in (0,90):
        source=fixtures/f'definition-variant-{mode}-{rotation}.pdf'
        groups=variant_fixture.UNITS if mode=='units' else variant_fixture.GROUPS
        if mode=='cached':
            document=variant_fixture.fixture(source,rotation,cached=True,groups=groups)
            (fixtures/(source.name+'.json')).write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        else:variant_fixture.variant_source(source,rotation,split=mode=='split',groups=groups)
        group.append({'source':str(source),'rotation':rotation,'units':mode=='units'})
fixture_parser = fixtures/'tight-fixture-parser.py'
fixture_parser.write_text('''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');p.add_argument('--progress',action='store_true');a=p.parse_args()
d=json.loads(Path(a.input+'.json').read_text('utf-8'));folder=Path(a.output);folder.mkdir(parents=True,exist_ok=True);d['output_dir']=str(folder);(folder/'document.json').write_text(json.dumps(d),encoding='utf-8');print(json.dumps({'ok':True}))
''', encoding='utf-8')
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
SHORT_TRANSLATIONS = {
    'Research Paper': '研究论文', 'Introduction': '引言',
    'Left column first.\nResults are reproducible.': '左栏先读。\n结果可复现。',
    'Right column second.\nMethods and controls.': '右栏后读。\n方法与控制。',
    'Model': '模型', 'Score': '分数', 'Alpha': '甲', 'Beta': '乙',
    '1. Footnote: p < 0.05': '1. 脚注: p < 0.05',
    'Figure 1: Accuracy results': '图1：准确率结果',
    'Model A: 95 percent': '模型A：95%',
    'Synthetic overview': '合成目录标题保持原字号并正确续排。'*60,
    'Synthetic topic 1': '下一目录条目',
    'Synthetic next source page.': '下一原文页单独开始。',
            'SourceAB': '合成图标签',
            'Synthetic raster label geometry regression.': 'Synthetic raster label geometry regression.',
}


def fixture_translation(text):
    if text.startswith('Synthetic original layout'):
        return fit_fixture.TEXT
    if text in SHORT_TRANSLATIONS:
        return SHORT_TRANSLATIONS[text]
    if text.strip().isdigit() or text.startswith('Other column'):
        return text
    if text.startswith('Synthetic'):
        return '译文 ' + text
    return text


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
            {**unit, 'text': fixture_translation(unit['text'])}
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
package_version = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))['version']
xpi_path = ROOT/'dist'/f'zhanhTRAN-{package_version}.xpi'
config = {'xpi': str(xpi_path), 'version': package_version, 'fixtures': str(fixtures),
          'provider': 'http://127.0.0.1:'+str(server.server_port), 'python': str(Path(os.sys.executable)),
          'tightFixtures':tight_fixtures, 'tightFixtureParser':str(fixture_parser),
          'geometryFixtures':geometry_fixtures,'tableFixtures':table_fixtures,
          'mixedFixtures':mixed_fixtures,'precisionFixtures':precision_fixtures,'cjkFixtures':cjk_fixtures,
          'edgeFixtures':edge_fixtures, 'inkFixtures':ink_fixtures,
          'quadFixtures':quad_fixtures,'detectedQuadFixtures':detected_quad_fixtures,
          'conditionalFixtures':conditional_fixtures,'captionFixtures':caption_fixtures,'symbolFixtures':symbol_fixtures,
          'prefixFixtures':prefix_fixtures,'cachedPrefixFixtures':cached_prefix_fixtures,
          'paragraphFixtures':paragraph_fixtures,'cachedParagraphFixtures':cached_paragraph_fixtures,
          'numberedFixtures':numbered_fixtures,'cachedNumberedFixtures':cached_numbered_fixtures,
          'raisedFixtures':raised_fixtures,'cachedRaisedFixtures':cached_raised_fixtures,
          'definitionFixtures':definition_fixtures,'cachedDefinitionFixtures':cached_definition_fixtures,
          'columnDefinitionFixtures':column_definition_fixtures,'wrappedDefinitionFixtures':wrapped_definition_fixtures,
          'styledDefinitionFixtures':styled_definition_fixtures,
          'variantDefinitionFixtures':variant_fixtures,'cachedVariantDefinitionFixtures':cached_variant_fixtures,
          'splitKeyFixtures':split_key_fixtures,'unitDefinitionFixtures':unit_definition_fixtures,
          'layoutGroups':[v for v in os.environ.get('ZHANHTRAN_QA_GROUPS','').split(',') if v],
          'layoutOnly':os.environ.get('ZHANHTRAN_QA_LAYOUT_ONLY')=='1'}
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
        report['xpi_sha256'] = hashlib.sha256(xpi_path.read_bytes()).hexdigest()
        report['requests'] = requests
        (qa/'host-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['result'] == 'PASS':
            assert report['pluginVersion'] == package_version
            from unittest import TestCase
            expected_counts={'tightOCR':4,'mixedOCR':8,'inkOCR':16,'precisionOCR':10,
                'cjkOCR':10,'edgeOCR':10,'quadOCR':8,'flowGeometry':8,'tableFlow':24,
                'detectedQuadOCR':4,'conditionalProse':8,'fixedCaptions':4,'fixedSymbols':4,
                'prefixSymbols':4,'cachedPrefixSymbols':4,'paragraphFlow':4,'cachedParagraphFlow':4,
                'numberedFlow':4,'cachedNumberedFlow':4,'raisedBaseline':4,'cachedRaisedBaseline':4,
                'definitionRows':4,'cachedDefinitionRows':4,'columnDefinitions':4,'wrappedDefinitions':4,'styledDefinitions':4,
                'variantDefinitions':4,'cachedVariantDefinitions':4,'splitDefinitionKeys':4,'unitDefinitions':4}
            if config['layoutGroups']:
                assert set(config['layoutGroups'])<=set(expected_counts)
                expected_counts={k:v for k,v in expected_counts.items() if k in config['layoutGroups']}
            for category,count in expected_counts.items():
                assert len(report[category])==count,(category,len(report[category]))
                for case in report[category]:
                    assert case['complete'] and case['fixed']
                    document=json.loads(Path(case['document']).read_text('utf-8'))
                    result={'report':case['report'],**case['files']}
                    fixed_fixture.check_fixed(TestCase(),Path(case['source']),document,result,case['order'])
                    if category=='tableFlow':
                        table_fixture.TableFlowTests().check_cells(document,result)
                    elif category in ('tightOCR','mixedOCR','inkOCR','precisionOCR','cjkOCR','edgeOCR','quadOCR'):
                        fixed_fixture.check_fixed_ocr(TestCase(),Path(case['source']),document,result)
                    elif category=='conditionalProse':
                        conditional_fixture.ConditionalProseTests().check_output(Path(case['source']),document,result,
                            order=case['order'],overflow=case['overflow'])
                    elif category=='fixedSymbols':
                        with fitz.open(case['files']['translated']) as pdf:
                            assert pdf[0].get_text().count('\u201f')==3
                    elif category in ('prefixSymbols','cachedPrefixSymbols'):
                        prefix_fixture.check_prefixes(TestCase(),Path(case['source']),document,result,case['order'])
                    elif category in ('paragraphFlow','cachedParagraphFlow'):
                        paragraph_fixture.check_paragraph(TestCase(),Path(case['source']),document,result,
                            case['order'],cached=category=='cachedParagraphFlow')
                    elif category in ('numberedFlow','cachedNumberedFlow','raisedBaseline','cachedRaisedBaseline'):
                        continuity_fixture.check_continuity(TestCase(),Path(case['source']),document,result,
                            case['order'],kind=case['kind'],cached=category.startswith('cached'))
                    elif category in ('definitionRows','cachedDefinitionRows','columnDefinitions','wrappedDefinitions','styledDefinitions'):
                        definition_fixture.check_definitions(TestCase(),Path(case['source']),document,result,
                            case['order'],styled=case['styled'])
                    elif category in ('variantDefinitions','cachedVariantDefinitions','splitDefinitionKeys','unitDefinitions'):
                        variant_fixture.check_variants(TestCase(),Path(case['source']),document,result,case['order'],
                            groups=variant_fixture.UNITS if case['units'] else variant_fixture.GROUPS)
                    elif category=='detectedQuadOCR':
                        with fitz.open(case['files']['translated']) as pdf:
                            assert '示例' in pdf[0].get_text()
            if not config['layoutOnly']:
                assert sum(r['model'] == 'fixture-retry' for r in requests) == 6
                assert sum(r['model'] == 'fixture-cancel' for r in requests) == 1
            expected = {} if config['layoutOnly'] else {'fixture-timeout-retry': [1]*6, 'fixture-timeout-shrink': [4, 2, 2],
                        'fixture-timeout-exhaust': [4, 2, 1, 1, 1, 1], 'fixture-timeout-cancel': [4]}
            for model, widths in expected.items():
                assert [len(r['ids']) for r in requests if r['model'] == model] == widths, model
            if not config['layoutOnly']:
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
                        assert '译文' in ''.join(page.get_text() for page in pdf)
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
                            # Extract only the translated panel before joining
                            # continuation pages; the source panel can otherwise
                            # interrupt the string in translated-left order.
                            offset = original[0].rect.width if bilingual and case['order']=='original-left' else 0
                            panel = fitz.Rect(offset,0,offset+original[0].rect.width,original[0].rect.height)
                            text = ''.join(page.get_text(clip=panel) if bilingual else page.get_text() for page in pdf)
                            assert ''.join(fit_fixture.TEXT.split()) in ''.join(text.split())
                            output['expandedTextComplete'] = True
                            png = qa/(path.parent.name+'-'+path.stem+'.png')
                            pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(png)
                            output['rendered'] = str(png)
                        if case['name'].startswith('toc'):
                            width,height = original[0].rect.width,original[0].rect.height
                            offset = width if bilingual and case['order']=='original-left' else 0
                            panel = fitz.Rect(offset,0,offset+width,height)
                            text = ''.join(page.get_text(clip=panel) if bilingual else page.get_text() for page in pdf)
                            assert ''.join(text.split()).count('合成目录标题保持原字号并正确续排。')==60
                            assert '下一原文页单独开始。' in ''.join(text.split())
                            assert len(pdf)==len(original)
                            output['tocTextComplete'] = True
                        if case['name'].startswith('ocr-label'):
                            width,height = original[0].rect.width,original[0].rect.height
                            offset = width if bilingual and case['order']=='original-left' else 0
                            panel = fitz.Rect(offset,0,offset+width,height)
                            text = ''.join(page.get_text(clip=panel) if bilingual else page.get_text() for page in pdf)
                            assert '合成图标签' in ''.join(text.split())
                            assert len(pdf)==len(original)
                            output['ocrLabelComplete'] = True
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
