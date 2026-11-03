"""Private-font list markers must not become missing body reservations (1.0.19)."""
import copy
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz
import numpy as np
from fontTools.ttLib import TTFont

from tests.fixed_layout_test import ROOT, parser, renderer, check_fixed

PUA = '\uf0a7'


def prefix_font(body_symbol=PUA):
    path = next(Path(p) for p in ('C:/Windows/Fonts/times.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf') if Path(p).is_file())
    face = TTFont(path)
    for cmap in face['cmap'].tables:
        if cmap.isUnicode():
            cmap.cmap[0xf0a7] = cmap.cmap.get(0x25a0, cmap.cmap[0x2022])
            if body_symbol!=PUA:
                cmap.cmap[ord(body_symbol)] = cmap.cmap[0x2022]
    for name in face['name'].names:
        if name.nameID in (1,4,6):
            name.string = 'SyntheticSymbols'.encode(name.getEncoding())
    buffer = io.BytesIO()
    face.save(buffer)
    face.close()
    return buffer.getvalue()


def prefix_source(path, rotation=0, body_symbol=PUA):
    face = prefix_font(body_symbol)
    with fitz.open() as pdf:
        page = pdf.new_page(width=540,height=400)
        page.insert_text((50,45),'Synthetic private-font list markers.',fontsize=16)
        page.insert_font(fontname='prefix',fontbuffer=face)
        for y,body in ((100,'Plain synthetic list content is translated completely.'),
                       (145,'Repeated symbol in the body: '),
                       (190,'Coordinate: ')):
            page.insert_text((72,y),PUA,fontname='prefix',fontsize=12)
            page.insert_text((92,y),body,fontname='tiro',fontsize=12)
            x = 92+fitz.get_text_length(body,fontname='tiro',fontsize=12)
            if y==145:
                page.insert_text((x,y),body_symbol,fontname='prefix',fontsize=12)
            if y==190:
                page.insert_text((x,y),'28\u00b0 11\' 51.2" N',fontname='tiro',fontsize=12)
        page.insert_text((50,285),'Unchanged following paragraph and graphic.',fontname='tiro',fontsize=12)
        page.draw_rect((350,240,480,310),color=(.2,.4,.6),fill=(.9,.95,1))
        page.set_rotation(rotation)
        pdf.save(path)


def add_legacy_prefix_tokens(document):
    """Emulate only the observed 1.0.18 orphan; no document contents involved."""
    for before,after in zip(document['pages'],document['translated_pages']):
        for block,target in zip(before['blocks'],after['blocks']):
            if not block.get('prefix'):
                continue
            old = block.get('inline_math',[])
            remap = {m['id']:'M'+str(i+1) for i,m in enumerate(old)}
            for value in (block,target):
                value['translation_text'] = re.sub(r'\[\[(M\d+)\]\]',
                    lambda m:'[['+remap[m[1]]+']]',value['translation_text'])
            tokens = [{'id':'M0','kind':'symbol','bbox':block['prefix']['bbox'],
                       'baseline':block['source_baseline'],'text':PUA}]
            tokens += [{**m,'id':remap[m['id']]} for m in old]
            block['inline_math'] = copy.deepcopy(tokens)
            target['inline_math'] = copy.deepcopy(tokens)
    return document


def fixture(path, rotation=0, legacy=False, long=False, parser_module=None,body_symbol=PUA):
    prefix_source(path,rotation,body_symbol)
    module = parser_module or parser
    document = module.parse_document(path,path.parent/(path.stem+'-parsed'))
    document['translated_pages'] = copy.deepcopy(document['pages'])
    for block,target in zip(document['pages'][0]['blocks'],document['translated_pages'][0]['blocks']):
        if not block.get('prefix'):
            continue
        text = block['translation_text']
        target['translation_text'] = text.replace('Plain synthetic list content is translated completely.',
            '合成列表正文完整翻译，不移动项目符号。'*(20 if long else 1))
        target['translation_text'] = target['translation_text'].replace('Repeated symbol in the body:',
            '正文中的同形符号：').replace('Coordinate:','坐标：')
        target['text'] = target['translation_text']
    return add_legacy_prefix_tokens(document) if legacy else document


def check_prefixes(case, source, document, result, order='original-left'):
    check_fixed(case,source,document,result,order)
    with fitz.open(source) as original,fitz.open(result['translated']) as output:
        original[0].set_rotation(0)
        output[0].set_rotation(0)
        for block in document['pages'][0]['blocks']:
            if block.get('prefix'):
                box = block['prefix']['bbox']
                a = np.frombuffer(original[0].get_pixmap(clip=box).samples,dtype=np.uint8).astype(int)
                b = np.frombuffer(output[0].get_pixmap(clip=box).samples,dtype=np.uint8).astype(int)
                case.assertLessEqual(np.abs(a-b).max(initial=0),2)
        text = renderer.normalize(output[0].get_text())
        case.assertIn('合成列表正文完整翻译，不移动项目符号。',text)
        case.assertIn('正文中的同形符号：',text)
        case.assertIn('坐标：',text)
        case.assertIn('28\u00b011\'51.2"n',text)
        case.assertNotIn('[[M',text)
        case.assertFalse(any(c[1]==0 for s in output[0].get_texttrace()
                             for c in s['chars'] if not chr(c[0]).isspace()))
    protected = [t for p in result['report']['placements'] for t in p.get('protected',[])]
    case.assertEqual(len(protected),2)  # Body icon and coordinate, not three list markers.
    case.assertTrue(all(fitz.Rect(t['bbox']).x0>=92 for t in protected))


class PrefixSymbolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-prefix-119-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_parser_only_registers_symbols_present_in_body_text(self):
        source = self.root/'markers.pdf'
        document = fixture(source)
        blocks = [b for b in document['pages'][0]['blocks'] if b.get('prefix')]
        self.assertEqual(len(blocks),3)
        for b in blocks:
            self.assertEqual(b['type'],'paragraph')  # Marker font name contains Symbols.
            ids = re.findall(r'\[\[(M\d+)\]\]',b['translation_text'])
            self.assertEqual(ids,[m['id'] for m in b.get('inline_math',[])])
            self.assertTrue(all(fitz.Rect(m['bbox']).x0>=92 for m in b.get('inline_math',[])))
        self.assertFalse(blocks[0].get('inline_math'))
        self.assertEqual(blocks[1]['inline_math'][0]['text'],PUA)
        self.assertIn('51.2',blocks[2]['inline_math'][0]['text'])
        # Ignore the marker font for prose classification, not real equations
        # in the body. The old formula protection must still apply there.
        formula = self.root/'formula.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=300)
            page.insert_text((50,45),'Synthetic equation classification fixture.',fontsize=12)
            page.insert_font(fontname='prefix',fontbuffer=prefix_font())
            page.insert_text((72,100),PUA,fontname='prefix',fontsize=12)
            page.insert_text((92,100),'x = y + z',fontname='tiro',fontsize=12)
            pdf.save(formula)
        equation = next(b for b in parser.parse_document(formula,self.root/'formula-parsed')['pages'][0]['blocks'] if b.get('prefix'))
        self.assertEqual(equation['type'],'formula')

    def test_fresh_parse_exports_with_native_markers_and_body_symbols(self):
        source = self.root/'fresh.pdf'
        document = fixture(source,long=True)
        before = json.dumps(document,ensure_ascii=False)
        result = renderer.export_pdfs(source,document,self.root/'output')
        check_prefixes(self,source,document,result)
        self.assertEqual(json.dumps(document,ensure_ascii=False),before)
        self.assertTrue(any(p['scale']<.5 for p in result['report']['placements']))

    def test_118_cached_orphans_are_recovered_without_changing_translation(self):
        source = self.root/'legacy.pdf'
        document = fixture(source,legacy=True)
        snapshot = copy.deepcopy(document)
        result = renderer.export_pdfs(source,document,self.root/'output')
        check_prefixes(self,source,document,result)
        self.assertEqual(document,snapshot)
        with fitz.open(source) as pdf:
            before,after = renderer.fixed_page_metadata(pdf[0],document['pages'][0],document['translated_pages'][0])
            for b in before['blocks']:
                if b.get('prefix'):
                    self.assertNotIn('M0',[m['id'] for m in b.get('inline_math',[])])

    def test_rotation_and_both_bilingual_orders_stay_fixed(self):
        for rotation in (90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    source = self.root/f'rotate-{rotation}-{order}.pdf'
                    document = fixture(source,rotation,legacy=True)
                    result = renderer.export_pdfs(source,document,self.root/source.stem,order=order)
                    check_prefixes(self,source,document,result,order)

    def test_real_missing_body_tokens_are_not_reclassified_as_prefixes(self):
        for kind in ('source-only','missing-both','not-symbol','bad-box'):
            with self.subTest(kind=kind):
                source = self.root/f'bad-{kind}.pdf'
                document = fixture(source,legacy=kind in ('not-symbol','bad-box'))
                block = next(b for b in document['pages'][0]['blocks'] if 'Coordinate:' in b.get('text',''))
                target = next(b for b in document['translated_pages'][0]['blocks'] if b['id']==block['id'])
                if kind in ('source-only','missing-both'):
                    marker = '[['+block['inline_math'][0]['id']+']]'
                    target['translation_text'] = target['translation_text'].replace(marker,'')
                    if kind=='missing-both':block['translation_text'] = block['translation_text'].replace(marker,'')
                elif kind=='not-symbol':
                    block['inline_math'][0]['kind']='math'
                else:
                    block['inline_math'][0]['bbox'][0]=-1
                out = self.root/source.stem
                with self.assertRaises(ValueError):renderer.export_pdfs(source,document,out)
                self.assertFalse(list(out.glob('*.pdf')))
                self.assertFalse(list(out.glob('*.tmp')))

    def test_prefix_token_that_was_requested_must_still_be_returned(self):
        source = self.root/'requested.pdf'
        document = fixture(source,legacy=True)
        block = next(b for b in document['pages'][0]['blocks'] if b.get('prefix'))
        block['translation_text'] += ' [[M0]]'
        with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE'):
            renderer.export_pdfs(source,document,self.root/'output')

    def test_symbol_registration_does_not_reuse_noncontiguous_ids(self):
        source = self.root/'ids.pdf'
        document = fixture(source)
        block = next(b for b in document['pages'][0]['blocks'] if 'Coordinate:' in b.get('text',''))
        with fitz.open(source) as pdf:
            result = parser.protect_native_symbols(pdf[0],block,{
                'translation_text':block['text']+' [[M1]]',
                'inline_math':[{'id':'M1','kind':'math','bbox':[400,330,420,345]}]})
        self.assertEqual([m['id'] for m in result['inline_math']],['M1','M0'])
        self.assertIn('[[M0]]',result['translation_text'])
        self.assertIn('[[M1]]',result['translation_text'])

    def test_isolated_cli_uses_packaged_sibling_metadata_for_cached_prefixes(self):
        source = self.root/'cli.pdf'
        document = fixture(source,legacy=True)
        path = self.root/'translated.json'
        path.write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        result = subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),
            '--input',str(source),'--document',str(path),'--output',str(self.root/'output'),
            '--progress'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['ok'])
        self.assertEqual(len(result.stdout.strip().splitlines()),1)
        self.assertIn('ZHANHTRAN_PROGRESS ',result.stderr)
        report = json.loads((self.root/'output/pdf-layout-report.json').read_text('utf-8'))
        check_prefixes(self,source,document,{**value,'report':report})


if __name__=='__main__':
    unittest.main()
