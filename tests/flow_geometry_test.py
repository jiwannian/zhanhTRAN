"""Synthetic whitespace/header/table column regressions; no paper input."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location('geometry_'+name,ROOT/'src'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

parser, renderer = load('local_parser'), load('local_pdf')


def fixture(path, rotation=0, cached=True, overflow=False):
    with fitz.open() as pdf:
        page = pdf.new_page(width=595.32,height=842.04)
        page.insert_text((279.65,54),'2024 April sample date for layout checks.',fontname='heit',fontsize=8.04)
        page.insert_text((149.42,74),' ',fontname='heit',fontsize=14)
        page.insert_text((72.024,140),'Synthetic body spans the page and establishes a single full width column.',fontname='tiro',fontsize=12)
        page.insert_text((72.024,162),'Short continuation.',fontname='tiro',fontsize=12)
        page.draw_rect((70,490,550,550),color=(0,0,0))
        page.insert_text((299,520),'Synthetic table column',fontname='tiro',fontsize=12)
        blocks = [
            {'id':'synthetic-header','type':'paragraph','bbox':[149.42,44,490,82],
             'text':'2024 April sample date for layout checks.',
             'style':{'size':8.04,'italic':True,'family':'sans-serif','line_height':2.597},
             'body_left':149.42,'first_line_indent':130.23},
            {'id':'synthetic-body','type':'paragraph','bbox':[72.024,127,540,166],
             'text':'Synthetic body with a short continuation.','style':{'size':12}},
            {'id':'synthetic-table','type':'table','bbox':[70,490,550,550],
             'extraction':'pymupdf','rows':[['Synthetic table column']],
             'cells':[[[70,490,550,550]]],'style':{'size':12}},
        ]
        if not cached:
            blocks[0].update(parser.native_metadata(page,blocks[0]))
        page.set_rotation(rotation)
        pages=[{'number':1,'width':595.32,'height':842.04,'blocks':blocks}]
        if overflow:
            page=pdf.new_page(width=595.32,height=842.04)
            page.insert_text((72,60),'Synthetic next source page.',fontsize=12)
            page.set_rotation(rotation)
            pages.append({'number':2,'width':595.32,'height':842.04,'blocks':[
                {'id':'synthetic-next','type':'paragraph','bbox':[72,47,300,64],
                 'text':'Synthetic next source page.','style':{'size':12}}]})
        pdf.save(path)
    translated=copy.deepcopy(pages)
    translated[0]['blocks'][0]['text']='2024年4月15日，合成测试页眉日期。'
    translated[0]['blocks'][1]['text']='合成正文保持原有字号和单栏宽度。'*(300 if overflow else 2)
    if overflow:
        translated[1]['blocks'][0]['text']='下一原文页单独开始。'
    return {'schema':1,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages':pages,'translated_pages':translated}


class FlowGeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zhanhtran-geometry-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_whitespace_line_does_not_supply_indent_leading_or_font_size(self):
        document=fixture(self.root/'source.pdf',cached=False)
        header=document['pages'][0]['blocks'][0]
        self.assertAlmostEqual(header['body_left'],279.65,places=2)
        self.assertEqual(header['first_line_indent'],0)
        self.assertEqual(len(header['source_lines']),1)
        self.assertAlmostEqual(header['style']['size'],8.04,places=3)
        self.assertEqual(header['style']['line_height'],1.35)

    def test_source_body_crossing_candidate_gutter_is_not_split_by_table_text(self):
        source=self.root/'source.pdf'
        document=fixture(source)
        with fitz.open(source) as pdf:
            units=renderer.page_units(pdf[0],document['pages'][0],document['translated_pages'][0],{})
            columns,_=renderer.body_columns(pdf[0],units)
            self.assertEqual(len(columns),1)
            self.assertGreater(columns[0][1],530)

    def test_true_two_columns_stay_separate_even_with_full_width_large_heading(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=600,height=800)
            page.insert_text((40,50),'WIDE SYNTHETIC DISPLAY TITLE',fontsize=26)
            for x in (40,320):
                for y in (100,118,136):
                    page.insert_text((x,y),'Short separate column text.',fontsize=11)
            columns,_=renderer.body_columns(page,[])
            self.assertEqual(len(columns),2)
            self.assertLessEqual(columns[0][1],310)
            self.assertEqual(columns[1][0],320)

    def test_cached_and_fresh_geometry_export_at_source_size_without_protrusion(self):
        for cached in (True,False):
            for rotation,order in ((0,'original-left'),(90,'translated-left')):
                with self.subTest(cached=cached,rotation=rotation):
                    folder=self.root/f'{cached}-{rotation}';folder.mkdir()
                    source=folder/'source.pdf';document=fixture(source,cached=cached,rotation=rotation)
                    result=renderer.export_pdfs(source,document,folder/'out',order=order)
                    self.assertEqual(result['report']['unplaced'],[])
                    self.assertEqual(result['report']['output_pages'],1)
                    with fitz.open(source) as original,fitz.open(result['translated']) as pdf:
                        original[0].set_rotation(0);pdf[0].set_rotation(0)
                        spans=[s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                               for line in b['lines'] for s in line['spans']]
                        header=[s for s in spans if s['bbox'][1]<100 and s['text'].strip()]
                        self.assertTrue(header)
                        self.assertAlmostEqual(header[0]['origin'][0],279.65,places=2)
                        self.assertTrue(all(abs(s['size']-8.04)<.001 for s in header))
                        self.assertTrue(all(s['bbox'][2]<=536 for s in header))
                        self.assertIn('2024年4月15日，合成测试页眉日期。',renderer.normalize(pdf[0].get_text()))
                        table=fitz.Rect(69,489,551,551)
                        self.assertEqual(original[0].get_pixmap(clip=table).samples,pdf[0].get_pixmap(clip=table).samples)
                    self.assertTrue(all(p['scale']==1 for p in result['report']['placements']))

    def test_normal_size_spanning_header_does_not_merge_genuine_columns(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=600,height=800)
            page.insert_text((40,50),'A normal size synthetic running header extends across both columns on the page.',fontsize=11)
            for x in (40,320):
                for y in (100,118,136):
                    page.insert_text((x,y),'Short separate column text.',fontsize=11)
            columns,_=renderer.body_columns(page,[])
            self.assertEqual(len(columns),2)
            self.assertLessEqual(columns[0][1],310)
            self.assertEqual(columns[1][0],320)

    def test_long_text_stays_fixed_and_next_source_page_remains_separate(self):
        source=self.root/'source.pdf';doc=fixture(source,overflow=True)
        result=renderer.export_pdfs(source,doc,self.root/'out')
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,source,doc,result)
        with fitz.open(result['translated']) as pdf:
            self.assertEqual(renderer.normalize(pdf[0].get_text()).count('合成正文保持原有字号和单栏宽度。'),300)
            self.assertIn('下一原文页单独开始。',renderer.normalize(pdf[1].get_text()))

    def test_old_whitespace_anchors_are_recovered_from_source_not_scaled(self):
        source=self.root/'source.pdf';doc=fixture(source)
        doc['pages'][0]['blocks'][0]['first_line_indent']=-10
        result=renderer.export_pdfs(source,doc,self.root/'out')
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,source,doc,result)
        header=next(p for p in result['report']['placements'] if p['ids']==['synthetic-header'])
        self.assertGreaterEqual(header['ink_bbox'][0],279.65-.01)


if __name__=='__main__':
    unittest.main()
