"""Ordinary conditional prose must flow as a paragraph, not fixed math labels.

All text and source PDFs are synthetic. Source documents contain only 1-2 pages.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz

from tests.flow_geometry_test import ROOT, parser, renderer


SOURCE = 'Sample readings AB 21 for CD fresh reference words in sequence EF if GH is correct.'
TEXT = '如果检验正确，AB 21、CD、EF 与 GH 的样本读数遵循新的参照顺序。'
FOLLOWING = '后续合成段落保持原字号并向下顺延。'
NEXT = '下一原文页从新的译文页组开始。'


def words(text):
    return ' '.join(text.split())


def source_pdf(path, rotation=0, overflow=False, indented=False):
    with fitz.open() as pdf:
        page = pdf.new_page(width=595.32, height=842.04)
        font = next((p for p in ('C:/Windows/Fonts/times.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf') if Path(p).is_file()), None)
        font_args = {'fontname': 'source-serif', 'fontfile': font} if font else {'fontname': 'tiro'}
        page.insert_text((72, 112), 'Synthetic conditional prose fixture.', fontsize=12, **font_args)
        baseline = 718 if overflow else 168
        if indented:
            page.insert_text((92, baseline), 'Sample readings AB 21 for CD fresh reference words', fontsize=12, **font_args)
            page.insert_text((72, baseline+14), 'in sequence EF if GH is correct.', fontsize=12, **font_args)
        else:
            page.insert_text((72, baseline), SOURCE, fontsize=12, **font_args)
        page.insert_text((72, baseline+(42 if indented else 28)),
                         'Synthetic following paragraph.', fontsize=12, **font_args)
        page.set_rotation(rotation)
        if overflow:
            page = pdf.new_page(width=595.32, height=842.04)
            page.insert_text((72, 112), 'Synthetic next source page.', fontsize=12, **font_args)
            page.set_rotation(rotation)
            pdf.set_toc([[1, 'Synthetic next source page', 2]])
            pdf[0].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(72, 95, 280, 116),
                               'page': 1, 'to': fitz.Point(72, 112)})
        pdf.save(path)


def translate(document, overflow=False, repeats=None):
    document = copy.deepcopy(document)
    document['translated_pages'] = copy.deepcopy(document['pages'])
    for page in document['translated_pages']:
        for block in page['blocks']:
            text = words(block['text'])
            if text.startswith('Sample readings'):
                # Also drives the archived parser in A/B checks. It must really
                # parse the source, rather than being given repaired metadata.
                if block.get('layout_runs'):
                    for run in block['layout_runs']:
                        run['text'] = '条件检验示例'
                else:
                    block['text'] = TEXT * (repeats if repeats is not None else (60 if overflow else 2))
            elif text.startswith('Synthetic following'):
                block['text'] = FOLLOWING
            elif text.startswith('Synthetic next'):
                block['text'] = NEXT
    return document


def fixture(path, rotation=0, overflow=False, indented=False, parser_module=None,
            create_source=True, repeats=None):
    if create_source:
        source_pdf(path, rotation=rotation, overflow=overflow, indented=indented)
    parsed = (parser_module or parser).parse_document(path, path.parent / (path.stem+'-parsed'))
    return translate(parsed, overflow=overflow, repeats=repeats)


class ConditionalProseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-conditional-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check_output(self,source,document,result,order='original-left',overflow=False):
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,source,document,result,order)
        block=next(b for b in document['pages'][0]['blocks'] if words(b['text']).startswith('Sample readings'))
        self.assertNotIn('layout_runs',block)
        self.assertNotIn('inline_math',block)
        p=next(p for p in result['report']['placements'] if p['ids']==[block['id']])
        self.assertEqual(p['source_font_size'],12)
        expected=next(b['text'] for b in document['translated_pages'][0]['blocks'] if b['id']==block['id'])
        with fitz.open(result['translated']) as output:
            text=renderer.normalize(''.join(p.get_text() for p in output))
            self.assertEqual(text.count(renderer.normalize(TEXT)),expected.count(TEXT))
            self.assertEqual(text.count(FOLLOWING),1)
            if overflow:
                self.assertEqual(len(output),2)
                self.assertIn(NEXT,renderer.normalize(output[1].get_text()))
                self.assertEqual(output[0].get_links()[0]['page'],1)
                self.assertEqual(output.get_toc()[0][2],2)

    def test_numbers_and_abbreviations_remain_one_paragraph(self):
        document = fixture(self.root/'source.pdf')
        block = next(b for b in document['pages'][0]['blocks'] if words(b['text']).startswith('Sample readings'))
        self.assertEqual(block['type'], 'paragraph')
        self.assertEqual(words(block['text']), SOURCE)
        self.assertNotIn('layout_runs', block)
        self.assertNotIn('inline_math', block)
        self.assertEqual(block['style']['size'], 12)

    def test_digits_parentheses_and_line_breaks_are_not_math_evidence(self):
        for text in ('Retain the 21 reference readings if the trial is complete.',
                     'Retain the reference readings (AB) if the trial is complete.',
                     'Retain the reference readings\nif the trial is complete.',
                     'Keep samples from 10:51:36 if the timer is active.'):
            with self.subTest(text=text), fitz.open() as pdf:
                page=pdf.new_page()
                page.insert_text((72,168), text, fontname='tiro', fontsize=12)
                box=list(page.get_text('blocks')[0][:4])
                self.assertEqual(parser.native_layout_runs(page, box, 'paragraph'), [])

    def test_real_conditional_relations_still_have_protected_native_runs(self):
        for operator in ('=', '<', '>', '\u2264', '\u2265', '\u2260', '\u2248', '\u2208', '\u2209'):
            with self.subTest(operator=operator), fitz.open() as pdf:
                page=pdf.new_page()
                page.insert_text((72,168), 'x = 1 if searching', fontsize=12)
                box=list(page.get_text('blocks')[0][:4])
                # Supply each relation through the PDF extraction interface,
                # without depending on the host font's Unicode coverage.
                class Page:
                    rect=page.rect
                    def get_text(self, form, **options):
                        data=page.get_text(form, **options)
                        for block in data['blocks']:
                            for line in block.get('lines', []):
                                for span in line['spans']:
                                    if 'text' in span:
                                        span['text']=span['text'].replace('=', operator)
                                    for char in span.get('chars', []):
                                        if char['c']=='=':
                                            char['c']=operator
                        return data
                runs=parser.native_layout_runs(Page(), box, 'paragraph')
                self.assertEqual([r['text'] for r in runs], ['if searching'])
                self.assertGreater(runs[0]['bbox'][0], 72)

    def test_math_font_and_italic_variable_routes_do_not_require_if(self):
        for font in ('MathFont', 'AdvTimes-i'):
            with self.subTest(font=font), fitz.open() as pdf:
                page=pdf.new_page()
                page.insert_text((72,168), 'x', fontsize=12)
                page.insert_text((100,168), 'when active', fontsize=12)
                class Page:
                    rect=page.rect
                    def get_text(self, form, **options):
                        data=page.get_text(form, **options)
                        for block in data['blocks']:
                            for line in block.get('lines', []):
                                for span in line['spans']:
                                    value=span.get('text', ''.join(c['c'] for c in span.get('chars', [])))
                                    if value=='x':
                                        span['font']=font
                        return data
                runs=parser.native_layout_runs(Page(), [70,150,300,175], 'paragraph')
                self.assertEqual([r['text'] for r in runs], ['when active'])

    def test_parsed_conditional_translation_wraps_and_pushes_following_text(self):
        source=self.root/'source.pdf'
        document=fixture(source, repeats=4)
        result=renderer.export_pdfs(source, document, self.root/'out')
        self.check_output(source, document, result)

    def test_original_indentation_survives_complete_paragraph_translation(self):
        source=self.root/'source.pdf'
        document=fixture(source, indented=True, repeats=4)
        block=next(b for b in document['pages'][0]['blocks'] if words(b['text']).startswith('Sample readings'))
        self.assertAlmostEqual(block['body_left'], 72, places=3)
        self.assertAlmostEqual(block['first_line_indent'], 20, places=3)
        result=renderer.export_pdfs(source, document, self.root/'out')
        self.check_output(source, document, result)
        with fitz.open(result['translated']) as pdf:
            spans=[s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                   for line in b['lines'] for s in line['spans'] if s['text'].startswith('如果检验')]
            self.assertAlmostEqual(spans[0]['origin'][0], 92, places=3)

    def test_rotated_bilingual_continuations_and_navigation(self):
        for rotation in (0,90):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    folder=self.root/f'{rotation}-{order}';folder.mkdir()
                    source=folder/'source.pdf'
                    document=fixture(source, rotation=rotation, overflow=True)
                    result=renderer.export_pdfs(source, document, folder/'out', order=order)
                    self.check_output(source, document, result, order=order, overflow=True)

    def test_parser_and_exporter_cli_keep_one_json_and_independent_progress(self):
        source=self.root/'source.pdf';source_pdf(source)
        proc=subprocess.run([sys.executable,'-I',str(ROOT/'src/local_parser.py'),'--input',str(source),
            '--output',str(self.root/'parsed'),'--progress'],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)
        document=translate(json.loads((self.root/'parsed/document.json').read_text('utf-8')))
        path=self.root/'document.json';path.write_text(json.dumps(document),encoding='utf-8')
        proc=subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),'--input',str(source),
            '--document',str(path),'--output',str(self.root/'out'),'--progress'],
            capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        result=json.loads(proc.stdout)
        self.assertTrue(result['ok'])
        self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)
        result['report']=json.loads((self.root/'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(source,document,result)


if __name__=='__main__':
    unittest.main()
