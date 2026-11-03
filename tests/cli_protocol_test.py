"""Fresh CLI processes must return JSON even without optional layout packages."""
import hashlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('protocol_parser', ROOT / 'src/local_parser.py')
parser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parser)
WRAPPER = '''
import importlib.util, runpy, sys
import pymupdf as fitz
fitz._recommend_layout = True
fitz._get_layout = None
original = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: None if name == 'pymupdf.layout' else original(name, *args, **kwargs)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


class CLIProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-cli-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.pdf'
        with fitz.open() as pdf:
            pdf.new_page(width=400, height=500).insert_text((30, 60), 'Synthetic source paragraph.', fontsize=12)
            pdf.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, script, source, document=None, progress=False):
        args = [str(ROOT / 'src' / script), '--input', str(source), '--output', str(self.root / script)]
        if progress:
            args.append('--progress')
        if document is not None:
            file = self.root / (script + '.json')
            file.write_text(json.dumps(document), encoding='utf-8')
            args += ['--document', str(file)]
        env = {**os.environ, 'PYMUPDF_SUGGEST_LAYOUT_ANALYZER': '1', 'PYTHONUTF8': '1'}
        result = subprocess.run([sys.executable, '-I', '-c', WRAPPER, *args],
                                capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        value = json.loads(result.stdout)
        self.assertTrue(value['ok'])
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        if progress:
            events = [json.loads(line.removeprefix('ZHANHTRAN_PROGRESS ')) for line in result.stderr.splitlines()
                      if line.startswith('ZHANHTRAN_PROGRESS ')]
            self.assertTrue(events)
            self.assertEqual(events[-1]['done'], events[-1]['total'])
            self.assertEqual(len(events), len(result.stderr.splitlines()))
        else:
            self.assertEqual(result.stderr, '')
        return value

    def test_parser_stdout_is_one_json_object(self):
        self.run_cli('local_parser.py', self.source)

    def test_progress_uses_stderr_without_changing_stdout_contract(self):
        self.run_cli('local_parser.py', self.source, progress=True)
        document = parser.parse_document(self.source, self.root / 'parsed')
        document['translated_pages'] = document['pages']
        self.run_cli('local_pdf.py', self.source, document, progress=True)

    def test_pdf_export_stdout_is_one_json_object(self):
        document = parser.parse_document(self.source, self.root / 'parsed')
        document['translated_pages'] = document['pages']
        result = self.run_cli('local_pdf.py', self.source, document)
        self.assertTrue(Path(result['translated']).is_file())
        self.assertTrue(Path(result['bilingual']).is_file())

    def test_tight_ocr_label_exports_at_original_size_in_fresh_process(self):
        spec = importlib.util.spec_from_file_location('cli_ocr_fixture', ROOT/'tests/ocr_layout_test.py')
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        source = self.root/'tight-label.pdf'
        document = fixture.fixture(source, text='合成地图标签', narrow=True, label_box=fixture.OCR8_BOX)
        result = self.run_cli('local_pdf.py', source, document, progress=True)
        report = json.loads((self.root/'local_pdf.py/pdf-layout-report.json').read_text('utf-8'))
        placed = report['placements'][0]
        self.assertEqual(placed['fit'], 'fixed-region')
        self.assertEqual(placed['bbox'], fixture.OCR8_BOX)
        self.assertGreater(placed['scale'],0)
        self.assertLessEqual(placed['scale'],1)
        with fitz.open(result['translated']) as pdf:
            self.assertIn('合成地图标签', ''.join(pdf[0].get_text().split()))

    def test_image_export_stdout_is_one_json_object(self):
        source = self.root / 'source.png'
        image = Image.new('RGB', (500, 140), 'white')
        ImageDraw.Draw(image).text((20, 30), 'Hello world.', fill='black', font_size=30)
        image.save(source)
        line = {'text': 'Hello world.', 'bbox': [20, 20, 350, 80]}
        page = {'number': 1, 'width': 500, 'height': 140, 'blocks': [
            {'id': 'line', 'type': 'paragraph', 'text': line['text'], 'bbox': line['bbox'], 'layout_lines': [line]}]}
        document = {'schema': 1, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'pages': [page], 'translated_pages': [page]}
        result = self.run_cli('local_image.py', source, document)
        self.assertTrue(Path(result['translated']).is_file())
        self.assertTrue(Path(result['bilingual']).is_file())

    def test_layout_failure_returns_safe_region_in_one_json_object(self):
        document = parser.parse_document(self.source,self.root/'parsed')
        block = document['pages'][0]['blocks'][0]
        block['layout_runs'] = [{'text':'Synthetic','bbox':[30,48,31,49]}]
        block['style']['size']=float('nan')
        document['translated_pages'] = copy.deepcopy(document['pages'])
        document['translated_pages'][0]['blocks'][0]['layout_runs'][0]['text'] = '完整译文'*20
        path = self.root/'translated.json'
        path.write_text(json.dumps(document),encoding='utf8')
        proc = subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),'--input',str(self.source),
                               '--document',str(path),'--output',str(self.root/'failed')],capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(proc.returncode,2)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        self.assertEqual(proc.stderr,'')
        result = json.loads(proc.stdout)
        self.assertEqual(result['error'],'PDF_LAYOUT_INVALID')
        self.assertEqual(result['failed_page'],1)
        self.assertEqual(result['region'],block['id']+'-run0')


if __name__ == '__main__':
    unittest.main()
