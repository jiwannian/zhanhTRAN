"""Synthetic short-box expansion: complete text without moving or hiding neighbours."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fit_renderer', ROOT / 'src/local_pdf.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
PARSER_SPEC = importlib.util.spec_from_file_location('fit_parser', ROOT / 'src/local_parser.py')
parser = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(parser)

# Only geometry and character counts come from the diagnostic, not paper content.
BOX = (69.2744, 711.5510, 284.4119, 731.5247)
TEXT = '\u4e2d\u6587\u6392\u7248\u5408\u6210\u6d4b\u8bd5\u6587' * 18 + '\u3002' * 15 + ' ABC 123 '


def fixture(path, obstacle=None, rotation=0):
    with fitz.open() as pdf:
        page = pdf.new_page(width=595.276, height=793.701)
        page.insert_text((BOX[0], BOX[1] + 7.5), 'Synthetic original layout keeps every translated word.', fontsize=7.5)
        page.insert_text((BOX[0], BOX[1] + 17), 'Original second line.', fontsize=7.5)
        page.insert_text((310, 760), 'Other column remains untouched.', fontsize=9)
        if obstacle == 'text':
            page.insert_text((BOX[0], 741), 'Protected neighbouring text', fontsize=9)
        elif obstacle == 'vector':
            page.draw_rect((BOX[0], 733, BOX[2], 751), fill=(0, .6, 0))
        elif obstacle == 'image':
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
            pix.clear_with(40)
            page.insert_image((BOX[0], 733, BOX[2], 751), pixmap=pix, keep_proportion=False)
        page.set_rotation(rotation)
        pdf.save(path)
    block = {'id': 'synthetic-short-box', 'type': 'paragraph', 'bbox': list(BOX),
             'text': 'Synthetic source paragraph.'}
    page = {'number': 1, 'width': 595.276, 'height': 793.701, 'blocks': [block]}
    translated = {**page, 'blocks': [{**block, 'text': TEXT}]}
    return {'schema': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages': [page], 'translated_pages': [translated]}


class PDFFitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-fit-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.pdf'

    def tearDown(self):
        self.temp.cleanup()

    def test_short_box_shrinks_inside_effective_source_lines(self):
        document=fixture(self.source)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,self.source,document,result)
        p=result['report']['placements'][0]
        self.assertLess(p['scale'],1)
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(TEXT),renderer.normalize(pdf[0].get_text()))

    def test_rotated_output_and_both_panel_orders_keep_every_character(self):
        for rotation in [0, 90, 180, 270]:
            for order in ['original-left', 'translated-left']:
                with self.subTest(rotation=rotation, order=order):
                    document = fixture(self.source, rotation=rotation)
                    result = renderer.export_pdfs(self.source, document,
                        self.root / f'output-{rotation}-{order}', order=order)
                    with fitz.open(self.source) as original, fitz.open(result['translated']) as pdf:
                        original_width = original[0].rect.width
                        self.assertEqual(pdf[0].rect, original[0].rect)
                        self.assertEqual(pdf[0].rotation, rotation)
                        self.assertIn(renderer.normalize(TEXT), renderer.normalize(''.join(p.get_text() for p in pdf)))
                    with fitz.open(result['bilingual']) as pdf:
                        self.assertEqual(len(pdf), result['report']['output_pages'])
                        offset = original_width if order=='original-left' else 0
                        text = ''.join(p.get_text(clip=fitz.Rect(offset,0,offset+original_width,p.rect.height)) for p in pdf)
                        self.assertIn(renderer.normalize(TEXT), renderer.normalize(text))
                        self.assertAlmostEqual(pdf[0].rect.width, original_width * 2, places=2)

    def test_text_vector_and_image_neighbours_are_preserved_while_body_continues(self):
        for obstacle in ['text', 'vector', 'image']:
            with self.subTest(obstacle=obstacle):
                document = fixture(self.source, obstacle)
                baseline = self.source.read_bytes()
                folder = self.root / obstacle
                result = renderer.export_pdfs(self.source, document, folder)
                self.assertEqual(self.source.read_bytes(), baseline)
                with fitz.open(self.source) as original, fitz.open(result['translated']) as pdf:
                    rect=fitz.Rect(69,733,285,751)
                    self.assertEqual(original[0].get_pixmap(clip=rect).samples,pdf[0].get_pixmap(clip=rect).samples)
                    self.assertIn(renderer.normalize(TEXT),renderer.normalize(''.join(p.get_text() for p in pdf)))

    def test_unpainted_blocks_and_other_translation_boxes_also_block_growth(self):
        for kind in ['formula', 'figure', 'paragraph', 'table']:
            with self.subTest(kind=kind):
                document = fixture(self.source)
                neighbour = {'id': 'protected', 'type': kind, 'text': '', 'bbox': [69, 733, 285, 751]}
                if kind == 'table':
                    neighbour['rows'] = [['']]
                for key in ['pages', 'translated_pages']:
                    document[key][0]['blocks'].append(copy.deepcopy(neighbour))
                result = renderer.export_pdfs(self.source, document, self.root / kind)
                for placed in result['report']['placements']:
                    if placed['output_page']==1:
                        self.assertFalse(fitz.Rect(placed['bbox']).intersects(fitz.Rect(neighbour['bbox'])))

    def test_table_ocr_native_run_and_inline_math_never_expand_into_whitespace(self):
        for flags in [{'image': True}, {'native_run': True}, {'inline_math': []}, {'expandable': False}]:
            with self.subTest(flags=flags):
                fixture(self.source)
                with fitz.open(self.source) as original, fitz.open(self.source) as pdf:
                    item = {'ids': ['restricted'], 'rect': fitz.Rect(BOX), 'text': TEXT,
                            'size': 9, 'bold': False, 'image': False, 'expandable': True, **flags}
                    report = {'placements': [], 'unplaced': []}
                    try:
                        renderer.place_page(pdf[0], original[0], [item], 1, report)
                    except ValueError as error:
                        self.assertEqual(str(error), 'PDF_TEXT_DOES_NOT_FIT')
                    self.assertTrue(all(p['bbox'] == list(BOX) for p in report['placements']))

    def test_page_edge_and_longer_translation_stay_on_original_page(self):
        from tests.fixed_layout_test import check_fixed
        for near_edge in (False,True):
            document=fixture(self.source)
            block=document['pages'][0]['blocks'][0]
            target=document['translated_pages'][0]['blocks'][0]
            if near_edge:
                block['bbox']=target['bbox']=[69,773,284,793.701]
            target['text']=TEXT*3
            result=renderer.export_pdfs(self.source,document,self.root/str(near_edge))
            check_fixed(self,self.source,document,result)
            with fitz.open(result['translated']) as pdf:
                self.assertIn(renderer.normalize(target['text']),renderer.normalize(pdf[0].get_text()))

    def test_growth_is_capped_and_never_crosses_protected_geometry(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            rect = fitz.Rect(30, 100, 180, 160)
            item = {'rect': rect, 'expandable': True, 'image': False}
            self.assertEqual(renderer.vacant_below(page, item, []).y1, 196)
            for obstacle in [fitz.Rect(40, 158, 80, 200), fitz.Rect(20, 150, 190, 170)]:
                self.assertEqual(renderer.vacant_below(page, item, [obstacle]), rect)
            self.assertEqual(renderer.vacant_below(page, item, [fitz.Rect(40, 185, 80, 200)]).y1, 184)
            self.assertEqual(renderer.vacant_below(page, item, [fitz.Rect(220, 165, 300, 200)]).y1, 196)

    def test_real_parsed_geometry_recovers_without_self_blocking_text_padding(self):
        fixture(self.source)
        document = parser.parse_document(self.source, self.root / 'parsed')
        self.assertEqual(len(document['pages'][0]['blocks']), 2)
        translated = copy.deepcopy(document['pages'])
        translated[0]['blocks'][0]['text'] = TEXT
        document['translated_pages'] = translated
        result = renderer.export_pdfs(self.source, document, self.root / 'rendered')
        self.assertEqual(result['report']['placements'][0]['fit'], 'fixed-region')
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(TEXT), renderer.normalize(''.join(p.get_text() for p in pdf)))

    def test_annotations_and_widgets_are_obstacles_even_without_parsed_blocks(self):
        fixture(self.source)
        with fitz.open(self.source) as pdf:
            page = pdf[0]
            page.add_rect_annot((69, 733, 285, 751))
            widget = fitz.Widget()
            widget.field_name = 'protected-field'
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.rect = fitz.Rect(69, 732, 285, 750)
            page.add_widget(widget)
            obstacles = renderer.layout_obstacles(page, [], [])
            item = {'rect': fitz.Rect(BOX), 'expandable': True, 'image': False}
            self.assertLessEqual(renderer.vacant_below(page, item, obstacles).y1, 732)

    def test_thin_page_bottom_footnote_continues_at_the_source_size(self):
        # A one-line extraction bbox can be shorter than Story's line box.
        with fitz.open() as pdf:
            page = pdf.new_page(width=595.276,height=793.701)
            page.insert_text((189.17,751),'Synthetic footnote, with a reference.',fontsize=6.96)
            pdf.new_page(width=595.276,height=793.701).insert_text((40,60),'Next source page.',fontsize=11)
            pdf.save(self.source)
        blocks = [
            {'id':'footnote','type':'footnote','bbox':[189.17,744.997,533.535,752.758],
             'text':'Synthetic footnote, with a reference.','body_left':189.17,
             'style':{'size':6.96,'family':'sans-serif','line_height':1.35}},
            {'id':'next','type':'paragraph','bbox':[40,48,280,65],'text':'Next source page.','style':{'size':11}}
        ]
        pages = [{'number':i+1,'width':595.276,'height':793.701,'blocks':[block]} for i,block in enumerate(blocks)]
        translated = copy.deepcopy(pages)
        translated[0]['blocks'][0]['text'] = '合成脚注内容保持原有字号，并保留完整的参考说明。'*12
        translated[1]['blocks'][0]['text'] = '下一原文页独立开始。'
        document = {'schema':1,'source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'pages':pages,'translated_pages':translated}
        result = renderer.export_pdfs(self.source,document,self.root/'output')
        placements = [p for p in result['report']['placements'] if p['ids']==['footnote']]
        self.assertTrue(placements)
        self.assertTrue(all(p['source_font_size']==6.96 and 0<p['scale']<1 for p in placements))
        self.assertEqual(result['report']['output_pages'],2)
        self.assertTrue(all(e['continuation_index']==0 for e in result['report']['page_map']))
        with fitz.open(result['translated']) as output:
            self.assertEqual(renderer.normalize(''.join(p.get_text() for p in output)).count('合成脚注内容保持原有字号，并保留完整的参考说明。'),12)
            self.assertIn('下一原文页独立开始。',renderer.normalize(output[-1].get_text()))

    def test_short_heading_uses_the_available_column_without_shrinking(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=500)
            page.insert_text((40,60),'An',fontsize=22)
            pdf.save(self.source)
        block = {'id':'short-heading','type':'heading','bbox':[40,36,70,67],
                 'text':'An','style':{'size':22,'bold':True}}
        pages = [{'number':1,'width':400,'height':500,'blocks':[block]}]
        translated = copy.deepcopy(pages)
        translated[0]['blocks'][0]['text'] = '合成研究结果与讨论'
        document = {'schema':1,'source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'pages':pages,'translated_pages':translated}
        result = renderer.export_pdfs(self.source,document,self.root/'output')
        with fitz.open(result['translated']) as output:
            self.assertEqual(len(output),1)
            self.assertIn('合成研究结果与讨论',renderer.normalize(output[0].get_text()))
        self.assertTrue(all(p['source_font_size']==22 and 0<p['scale']<1 for p in result['report']['placements']))

    def test_footnote_crossing_columns_avoids_body_text_on_continuation(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=500)
            for x,y,text,size in [(30,60,'Left column source text.',11),(220,60,'Right column source text.',11),(100,455,'Synthetic footnote reference.',7)]:
                page.insert_text((x,y),text,fontsize=size)
            pdf.save(self.source)
        blocks = [
            {'id':'left','type':'paragraph','bbox':[30,48,180,65],'text':'Left column source text.','style':{'size':11}},
            {'id':'right','type':'paragraph','bbox':[220,48,370,65],'text':'Right column source text.','style':{'size':11}},
            {'id':'footnote','type':'footnote','bbox':[100,447,370,458],'text':'Synthetic footnote reference.','style':{'size':7}}
        ]
        pages = [{'number':1,'width':400,'height':500,'blocks':blocks}]
        translated = copy.deepcopy(pages)
        for b,text in zip(translated[0]['blocks'],['左栏合成正文。','右栏合成正文完整保留。'*80,'跨栏合成脚注完整保留。'*30]):
            b['text'] = text
        document = {'schema':1,'source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'pages':pages,'translated_pages':translated}
        result = renderer.export_pdfs(self.source,document,self.root/'output')
        placements = result['report']['placements']
        for i,p in enumerate(placements):
            for other in placements[:i]:
                if p['output_page']==other['output_page']:
                    self.assertFalse(fitz.Rect(p['bbox']).intersects(fitz.Rect(other['bbox'])))
        with fitz.open(result['translated']) as output:
            text = renderer.normalize(''.join(p.get_text() for p in output))
            self.assertEqual(text.count('右栏合成正文完整保留。'),80)
            self.assertEqual(text.count('跨栏合成脚注完整保留。'),30)


if __name__ == '__main__':
    unittest.main()
