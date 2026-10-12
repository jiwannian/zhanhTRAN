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

    def test_short_box_complete_text_uses_only_bounded_vertical_whitespace(self):
        document = fixture(self.source)
        baseline = self.source.read_bytes()
        result = renderer.export_pdfs(self.source, document, self.root / 'output')
        self.assertEqual(self.source.read_bytes(), baseline)
        placed = result['report']['placements'][0]
        self.assertEqual(placed['source_bbox'], list(BOX))
        self.assertEqual(placed['fit'], 'vacant-below')
        self.assertEqual(placed['bbox'][:3], list(BOX[:3]))
        self.assertGreater(placed['bbox'][3], BOX[3])
        self.assertLessEqual(placed['bbox'][3], BOX[3] + BOX[3] - BOX[1])
        self.assertGreaterEqual(placed['font_size'], 4.999)
        with fitz.open(self.source) as original, fitz.open(result['translated']) as pdf:
            self.assertEqual(pdf[0].rect, original[0].rect)
            extracted = pdf[0].get_text(clip=fitz.Rect(placed['bbox']))
            self.assertEqual(renderer.normalize(extracted), renderer.normalize(TEXT))
            region = fitz.Rect(300, 690, 590, 770)
            self.assertEqual(pdf[0].get_pixmap(clip=region).samples,
                             original[0].get_pixmap(clip=region).samples)
            for block in pdf[0].get_text('dict')['blocks']:
                if block['type'] == 0:
                    for line in block['lines']:
                        for span in line['spans']:
                            self.assertTrue(pdf[0].rect.contains(fitz.Rect(span['bbox'])))
        with fitz.open(self.source) as original, fitz.open(result['bilingual']) as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertEqual(pdf[0].get_pixmap(clip=original[0].rect).samples,
                             original[0].get_pixmap().samples)

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
                        self.assertIn(renderer.normalize(TEXT), renderer.normalize(pdf[0].get_text()))
                    with fitz.open(result['bilingual']) as pdf:
                        self.assertEqual(len(pdf), 1)
                        self.assertIn(renderer.normalize(TEXT), renderer.normalize(pdf[0].get_text()))
                        self.assertAlmostEqual(pdf[0].rect.width, original_width * 2, places=2)

    def test_text_vector_and_image_neighbours_block_growth_and_publish_nothing(self):
        for obstacle in ['text', 'vector', 'image']:
            with self.subTest(obstacle=obstacle):
                document = fixture(self.source, obstacle)
                baseline = self.source.read_bytes()
                folder = self.root / obstacle
                with self.assertRaisesRegex(ValueError, 'PDF_TEXT_DOES_NOT_FIT'):
                    renderer.export_pdfs(self.source, document, folder)
                self.assertEqual(self.source.read_bytes(), baseline)
                self.assertFalse(list(folder.glob('*.pdf*')))

    def test_unpainted_blocks_and_other_translation_boxes_also_block_growth(self):
        for kind in ['formula', 'figure', 'paragraph', 'table']:
            with self.subTest(kind=kind):
                document = fixture(self.source)
                neighbour = {'id': 'protected', 'type': kind, 'text': '', 'bbox': [69, 733, 285, 751]}
                if kind == 'table':
                    neighbour['rows'] = [['']]
                for key in ['pages', 'translated_pages']:
                    document[key][0]['blocks'].append(copy.deepcopy(neighbour))
                with self.assertRaisesRegex(ValueError, 'PDF_TEXT_DOES_NOT_FIT'):
                    renderer.export_pdfs(self.source, document, self.root / kind)

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

    def test_page_edge_and_huge_translation_still_fail_instead_of_truncating(self):
        for near_edge in [False, True]:
            with self.subTest(near_edge=near_edge):
                document = fixture(self.source)
                block = document['pages'][0]['blocks'][0]
                translated = document['translated_pages'][0]['blocks'][0]
                if near_edge:
                    block['bbox'] = translated['bbox'] = [69, 773, 284, 793.701]
                else:
                    translated['text'] = TEXT * 100
                with self.assertRaisesRegex(ValueError, 'PDF_TEXT_DOES_NOT_FIT'):
                    renderer.export_pdfs(self.source, document, self.root / str(near_edge))

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
        self.assertEqual(result['report']['placements'][0]['fit'], 'vacant-below')
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(TEXT), renderer.normalize(pdf[0].get_text()))

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


if __name__ == '__main__':
    unittest.main()
