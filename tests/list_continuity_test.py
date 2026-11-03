"""Numbered items and raised-glyph line pitch; 1-2 page synthetic PDFs only."""
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf as fitz

from tests.flow_geometry_test import ROOT, parser, renderer
from tests.fixed_layout_test import check_fixed
from tests.paragraph_flow_test import SOURCE, wrap


LIST_TEXT = '合成列表正文连续排版，不因首行编号或句末而提前另起一行。'*4
RAISED_TEXT = '合成正文按主要文字的基线识别续行，不用上标字形的顶部作为段落间距。'*4


def list_source(path, rotation=0, labels=('1','2','4','12'), protected=False, next_page=False):
    with fitz.open() as pdf:
        page = pdf.new_page(width=612,height=792)
        page.insert_text((72,60),'Synthetic numbered item continuity',fontname='tibo',fontsize=16)
        for n,label in enumerate(labels):
            y = 120+n*130
            # Cover both aligned markers and slightly offset fonts that can
            # become different extraction lines on the same visual row.
            lines = wrap(SOURCE.split('Short translated')[0].strip(),width=447)
            # Emit the first row after its continuations, as some generators
            # do. Visual order is unchanged; extraction cannot be mistaken
            # for semantic paragraph grouping supplied by the PDF writer.
            for i in list(range(1,len(lines)))+[0]:
                line=lines[i]
                if i==0:page.insert_text((72,y-(.168 if n>=2 else 0)),label,fontname='tiro',fontsize=12)
                x = 93
                if protected:
                    first = "Coordinate 21\u00b0 12' N; "
                    page.insert_text((x,y+i*20.7),first,fontname='tiro',fontsize=12)
                    x += fitz.get_text_length(first,fontname='tiro',fontsize=12)
                    emphasis = 'reference emphasis '
                    page.insert_text((x,y+i*20.7),emphasis,fontname='tiit',fontsize=12)
                    x += fitz.get_text_length(emphasis,fontname='tiit',fontsize=12)
                    line = wrap(SOURCE,width=540-x)[0]
                page.insert_text((x,y+i*20.7),line,fontname='tiro',fontsize=12)
        page.draw_rect((72,650,540,690),color=(.2,.3,.5),fill=(.9,.94,.98))
        page.insert_text((210,707),'Figure 1. Separate synthetic caption.',fontname='tiit',fontsize=9)
        page.insert_text((72,755),'Synthetic independent footnote.',fontname='tiro',fontsize=8)
        page.set_rotation(rotation)
        if next_page:
            page = pdf.new_page(width=612,height=792)
            page.insert_text((72,80),'Synthetic next page.',fontname='tiro',fontsize=12)
            page.set_rotation(rotation)
            pdf.set_toc([[1,'Synthetic next page',2]])
            pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(72,43,360,65),
                                'page':1,'to':fitz.Point(72,80)})
        pdf.save(path)
    return len(lines)


def raised_source(path, rotation=0):
    """5 rows: fourth has a raised degree, fifth has the short tail."""
    with fitz.open() as pdf:
        page = pdf.new_page(width=612,height=400)
        page.insert_text((72,60),'Synthetic raised glyph baseline fixture',fontname='tibo',fontsize=16)
        full = wrap(SOURCE)[0]
        for i in range(5):
            y = 120+i*20.7
            if i==3:
                first = 'Synthetic equipment; the reading is 0.0'
                page.insert_text((72,y),first,fontname='tiro',fontsize=12)
                x = 72+fitz.get_text_length(first,fontname='tiro',fontsize=12)
                page.insert_text((x,y-2.2),'\u00b0',fontname='tiro',fontsize=12)
                x += fitz.get_text_length('\u00b0',fontname='tiro',fontsize=12)
                rest = wrap(' E, the reference data remain continuous and include every value.',width=540-x)[0]
                page.insert_text((x,y),rest,fontname='tiro',fontsize=12)
            else:
                page.insert_text((72,y),full if i<4 else 'with the short synthetic tail.',fontname='tiro',fontsize=12)
        page.insert_text((72,260),'A genuine next source paragraph stays separate.',fontname='tiro',fontsize=12)
        page.set_rotation(rotation)
        pdf.save(path)


def legacy_parse(source, output):
    """Old linewise cache shape, including its unprotected numeric marker."""
    original = parser.native_metadata
    def metadata(page,block):
        result = original(page,block)
        result.pop('source_baselines',None)
        if result.get('prefix',{}).get('kind')=='number':
            result.pop('prefix');result.pop('translation_text',None)
            result['body_left'] = 72
            result['first_line_indent'] = 0
            result['fixed_bbox'][0] = 72
            result['source_lines'][0][0] = 72
        return result
    with patch.object(parser,'native_metadata',side_effect=metadata),patch.object(
            parser,'native_paragraph_groups',side_effect=lambda blocks:[[b] for b in blocks]):
        return parser.parse_document(source,output)


def translate(document, cached=False, kind='list'):
    doc = copy.deepcopy(document);doc['translated_pages'] = copy.deepcopy(doc['pages'])
    for old,new in zip(doc['pages'][0]['blocks'],doc['translated_pages'][0]['blocks']):
        if not 95<old['bbox'][1]<(610 if kind=='list' else 220):
            continue
        if cached or old.get('inline_math') or old.get('text_styles'):
            value = old.get('translation_text',old['text'])
            markers = re.findall(r'\[\[(?:/?S\d+|M\d+)\]\]',value)
            prefix = re.match(r'^\s*(\d+[.)]?)\s*',value) if not old.get('prefix') else None
            text = (prefix[1] if prefix else '')+'合成译文连续排版。'
            text += ''.join(m+('强调' if re.fullmatch(r'\[\[S\d+\]\]',m) else '') for m in markers)
            text += '正文不会在句末强制分行。'
            field = 'translation_text' if 'translation_text' in old else 'text'
            new[field] = text;new['text'] = text
        else:
            value = LIST_TEXT if kind=='list' else RAISED_TEXT
            field = 'translation_text' if 'translation_text' in old else 'text'
            new[field] = value[:21]+'\n\n'+value[21:53]+'\n'+value[53:]
            new['text'] = new[field]
    return doc


def fixture(path, rotation=0, cached=False, protected=False, next_page=False, kind='list', labels=('1','2','4','12')):
    if kind=='list':list_source(path,rotation,labels,protected,next_page)
    else:raised_source(path,rotation)
    doc = legacy_parse(path,path.parent/(path.stem+'-parsed')) if cached else parser.parse_document(
        path,path.parent/(path.stem+'-parsed'))
    return translate(doc,cached,kind)


def check_continuity(test,source,document,result,order='original-left',kind='list',cached=False):
    check_fixed(test,source,document,result,order)
    body = [p for p in result['report']['placements'] if 95<p['bbox'][1]<(610 if kind=='list' else 220)]
    test.assertEqual(len(body),4 if kind=='list' else 1)
    test.assertTrue(all(p['source_line_count']>=3 for p in body))
    with fitz.open(source) as original,fitz.open(result['translated']) as pdf:
        original[0].set_rotation(0);pdf[0].set_rotation(0)
        for p in body:
            test.assertEqual(p['scale'],1)
            lines = [line for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines']
                if p['bbox'][1]-.1<line['bbox'][1]<p['bbox'][3]+.1 and line['bbox'][0]>=p['bbox'][0]-.1]
            test.assertTrue(lines)
            if not cached:
                test.assertGreater(max(s['bbox'][2] for s in lines[0]['spans']),500)
                test.assertIn(renderer.normalize(LIST_TEXT if kind=='list' else RAISED_TEXT),
                              renderer.normalize(pdf[0].get_text()))
            if kind=='list':
                test.assertAlmostEqual(p['bbox'][0],93,places=2)
                test.assertAlmostEqual(lines[0]['spans'][0]['origin'][0],93,places=2)
                test.assertTrue(all(line['bbox'][0]>=92.9 for line in lines))
        if kind=='list':
            # The number gutter is outside the translated paragraph region.
            # Check its source pixels, position and unique text occurrence.
            for i,label in enumerate(('1','2','4','12')):
                clip=fitz.Rect(70,105+i*130,90,125+i*130)
                a=np.frombuffer(original[0].get_pixmap(clip=clip).samples,dtype=np.uint8).astype(int)
                b=np.frombuffer(pdf[0].get_pixmap(clip=clip).samples,dtype=np.uint8).astype(int)
                test.assertLessEqual(np.abs(a-b).max(initial=0),2)
                test.assertEqual(pdf[0].get_text(clip=clip).strip(),label)
            markers=[s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines']
                     for s in line['spans'] if s['text'].strip() in ('1','2','4','12')]
            test.assertEqual(len(markers),4)
        test.assertFalse(any(c[1]==0 for s in pdf[0].get_texttrace() for c in s['chars'] if not chr(c[0]).isspace()))


class ListContinuityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='zhanhtran-continuity-121-')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_bare_numbers_with_baseline_offset_are_native_prefixes_and_items_join(self):
        source=self.root/'list.pdf';count=list_source(source)
        doc=parser.parse_document(source,self.root/'parsed')
        blocks=[b for b in doc['pages'][0]['blocks'] if 95<b['bbox'][1]<610]
        self.assertEqual(len(blocks),4)
        for b,label in zip(blocks,('1','2','4','12')):
            self.assertEqual(b['prefix']['text'],label)
            self.assertEqual(b['prefix']['kind'],'number')
            self.assertEqual(len(b['source_lines']),count)
            self.assertEqual(len(b['source_baselines']),count)
            self.assertTrue(all(abs(y-x-20.7)<.01 for x,y in zip(b['source_baselines'],b['source_baselines'][1:])))
            self.assertEqual(b['body_left'],93)
            self.assertEqual(b['first_line_indent'],0)
            self.assertFalse(b['translation_text'].lstrip().startswith(label))

    def test_fresh_list_body_fills_width_without_repainting_native_marker(self):
        source=self.root/'list.pdf';doc=fixture(source)
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_continuity(self,source,doc,result)

    def test_cached_numbered_items_join_and_remove_duplicate_number_without_mutating_input(self):
        source=self.root/'cache.pdf';doc=fixture(source,cached=True)
        snapshot=copy.deepcopy(doc)
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_continuity(self,source,doc,result,cached=True)
        self.assertTrue(all(len(p['ids'])>=3 for p in result['report']['placements'] if 95<p['bbox'][1]<610))
        self.assertEqual(doc,snapshot)

    def test_cached_model_omitted_number_still_uses_exact_native_number(self):
        source=self.root/'cache.pdf';doc=fixture(source,cached=True)
        for b in doc['translated_pages'][0]['blocks']:
            for field in ('text','translation_text'):
                if field in b:b[field]=re.sub(r'^\s*\d+\s*','',b[field])
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_continuity(self,source,doc,result,cached=True)

    def test_raised_bbox_does_not_split_fresh_or_cached_continuous_baselines(self):
        for cached in (False,True):
            with self.subTest(cached=cached):
                source=self.root/f'raised-{cached}.pdf';doc=fixture(source,cached=cached,kind='raised')
                result=renderer.export_pdfs(source,doc,self.root/source.stem)
                check_continuity(self,source,doc,result,kind='raised',cached=cached)
                if not cached:
                    block=next(b for b in doc['pages'][0]['blocks'] if 95<b['bbox'][1]<130)
                    self.assertEqual(len(block['source_lines']),5)
                    self.assertTrue(all(abs(y-x-20.7)<.01 for x,y in zip(block['source_baselines'],block['source_baselines'][1:])))

    def test_dotted_parenthesized_letter_and_bullet_items_own_continuations_not_next_item(self):
        for labels in (('1.','2)','(3)','A.'),('-','B)','(c)','12')):
            with self.subTest(labels=labels):
                source=self.root/('list-'+str(len(list(self.root.glob('*.pdf'))))+'.pdf')
                count=list_source(source,labels=labels)
                doc=parser.parse_document(source,self.root/(source.stem+'-parsed'))
                body=[b for b in doc['pages'][0]['blocks'] if 95<b['bbox'][1]<610]
                self.assertEqual(len(body),4)
                self.assertEqual([b['prefix']['text'] for b in body],list(labels))
                self.assertTrue(all(len(b['source_lines'])==count and b['body_left']==93 for b in body))

    def test_year_count_time_and_decimal_are_not_numbered_prefixes(self):
        for text in ('2024 synthetic observations remain ordinary prose.','12 samples remain ordinary prose.',
                     '12:30 synthetic sample starts at the original margin.','1.25 is an ordinary decimal measurement.'):
            with self.subTest(text=text),fitz.open() as pdf:
                page=pdf.new_page();page.insert_text((72,120),text,fontname='tiro',fontsize=12)
                box=page.get_text('blocks')[0][:4]
                self.assertNotIn('prefix',parser.native_metadata(page,{'bbox':box,'text':text}))

    def test_list_cache_rebases_every_symbol_and_style_and_rejects_missing_markers(self):
        source=self.root/'protected.pdf';doc=fixture(source,cached=True,protected=True)
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_continuity(self,source,doc,result,cached=True)
        p=next(p for p in result['report']['placements'] if 95<p['bbox'][1]<130)
        self.assertEqual(len(p['protected']),p['source_line_count'])
        self.assertEqual(len(p['text_styles']),p['source_line_count'])
        self.assertEqual(len({m['id'] for m in p['protected']}),len(p['protected']))
        for token in ('[[M0]]','[[/S0]]'):
            bad=copy.deepcopy(doc)
            b=next(b for b in bad['translated_pages'][0]['blocks'] if 95<b['bbox'][1]<130)
            b['translation_text']=b['translation_text'].replace(token,'')
            out=self.root/('bad'+str(len(token)))
            with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE'):
                renderer.export_pdfs(source,bad,out)
            self.assertFalse(list(out.glob('*.pdf')))

    def test_fresh_protected_lists_preserve_coordinates_and_emphasis(self):
        source=self.root/'protected.pdf';doc=fixture(source,protected=True)
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_continuity(self,source,doc,result,cached=True)
        p=next(p for p in result['report']['placements'] if 95<p['bbox'][1]<130)
        self.assertEqual(len(p['protected']),p['source_line_count'])
        self.assertEqual(len(p['text_styles']),p['source_line_count'])

    def test_long_item_shrinks_as_one_region_without_scaling_number_or_adjacent_caption(self):
        source=self.root/'long.pdf';doc=fixture(source)
        first=next(b for b in doc['translated_pages'][0]['blocks'] if 95<b['bbox'][1]<130)
        first['translation_text']=LIST_TEXT*20
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_fixed(self,source,doc,result)
        body=[p for p in result['report']['placements'] if 95<p['bbox'][1]<610]
        self.assertEqual(len(body),4)
        self.assertLess(body[0]['scale'],.5)
        self.assertTrue(all(p['scale']==1 for p in body[1:]))
        with fitz.open(source) as old,fitz.open(result['translated']) as new:
            self.assertIn(renderer.normalize(LIST_TEXT*20),renderer.normalize(new[0].get_text()))
            clip=fitz.Rect(70,105,90,125)
            a=np.frombuffer(old[0].get_pixmap(clip=clip).samples,dtype=np.uint8).astype(int)
            b=np.frombuffer(new[0].get_pixmap(clip=clip).samples,dtype=np.uint8).astype(int)
            self.assertLessEqual(np.abs(a-b).max(initial=0),2)

    def test_rotations_orders_source_bytes_page_map_and_navigation(self):
        for rotation in (0,90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    source=self.root/f'list-{rotation}-{order}.pdf';doc=fixture(source,rotation,next_page=True)
                    before=hashlib.sha256(source.read_bytes()).hexdigest()
                    result=renderer.export_pdfs(source,doc,self.root/source.stem,order=order)
                    check_continuity(self,source,doc,result,order)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
                    with fitz.open(result['translated']) as pdf:
                        self.assertEqual(len(pdf),2)
                        self.assertEqual(pdf.get_toc()[0][2],2)
                        self.assertEqual(pdf[0].get_links()[0]['page'],1)

    def test_cached_number_mismatch_fails_with_exact_region_no_partial_file(self):
        source=self.root/'bad.pdf';doc=fixture(source,cached=True)
        b=next(b for b in doc['pages'][0]['blocks'] if 95<b['bbox'][1]<130)
        field='translation_text' if 'translation_text' in b else 'text'
        b[field]=re.sub(r'^\s*1\s*','8 ',b[field])
        with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE') as error:
            renderer.export_pdfs(source,doc,self.root/'output')
        self.assertEqual(error.exception.layout_failure,{'failed_page':1,'region':b['id']})
        self.assertFalse(list((self.root/'output').glob('*.pdf')))

    def test_isolated_cached_cli_retains_single_json_and_stderr_progress(self):
        source=self.root/'cli.pdf';doc=fixture(source,cached=True)
        path=self.root/'translated.json';path.write_text(json.dumps(doc),encoding='utf-8')
        proc=subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),'--input',str(source),
            '--document',str(path),'--output',str(self.root/'output'),'--progress'],
            capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)


if __name__=='__main__':unittest.main()
