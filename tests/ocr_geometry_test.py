"""OCR text-height geometry; all words, pixels and PDFs are synthetic."""
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from tests.local_parser_test import parser
from tests.ocr_layout_test import renderer, image_renderer, ROOT

QUAD = [[100, 80], [162, 148], [136, 171], [74, 104]]
RAW_BOX = [74, 80, 162, 171]
BOUNDS = [280, 480, 460, 660]
SOURCE = 'REGULATE'
TEXT = '调节'


def figure_image(background='black'):
    paper = (32, 40, 48) if background == 'black' else (246, 246, 246)
    ink = 'white' if background == 'black' else 'black'
    image = Image.new('RGB', (500, 500), paper)
    draw = ImageDraw.Draw(image)
    # Real graphic content blocks right/down expansion of an inflated font.
    draw.rectangle((165, 78, 220, 235), fill=ink)
    draw.rectangle((73, 174, 220, 235), fill=ink)
    draw.rectangle((260, 280, 330, 330), fill=(30, 150, 210))
    letters = Image.new('RGBA', image.size)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 25)
    # A synthetic curved dial word. Its outer box is not its font height.
    for i, char in enumerate(SOURCE):
        theta = math.radians(171 - i*10.1)
        x, y = 169+80*math.cos(theta), 77+80*math.sin(theta)
        glyph = Image.new('RGBA', (60, 60))
        ImageDraw.Draw(glyph).text((30, 30), char, font=font, fill=ink, anchor='mm')
        glyph = glyph.rotate(math.degrees(theta)-90, resample=Image.Resampling.BICUBIC)
        letters.alpha_composite(glyph, (round(x-30), round(y-30)))
    # Keep the generated letters inside their detector's original region.
    image.paste(letters.crop(RAW_BOX), RAW_BOX, letters.crop(RAW_BOX))
    return image


def fixture(path, rotation=0, background='black', raster=False, parser_module=None, text=TEXT, create_source=True):
    module = parser_module or parser
    image = figure_image(background)
    bounds = [0, 0, 500, 500] if raster else list(BOUNDS)
    with tempfile.TemporaryDirectory(prefix='synthetic-ocr-geometry-') as temp:
        # Use the real OCR-result conversion, not a precomputed corrected size.
        engine = Mock(return_value=([(QUAD, SOURCE, .99)], None))
        with patch.object(module, '_OCR', engine), patch.object(module, '_IMAGE_CACHE', {}), \
                patch.object(module, 'image_tables', return_value=[]), \
                patch.object(module, 'ruled_tables', return_value=[]):
            blocks = module.image_blocks(image, Path(temp), 'figure.png', bounds, [])
    blocks[0]['id'] = 'synthetic-quad'
    if raster:
        if create_source:
            image.save(path)
        width, height = image.size
    else:
        width, height = 595.32, 842.04
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        if create_source:
            with fitz.open() as pdf:
                page = pdf.new_page(width=width, height=height)
                page.insert_text((50, 60), 'Synthetic curved OCR measurement.', fontsize=12)
                page.insert_image(bounds, stream=stream.getvalue(), keep_proportion=False)
                page.set_rotation(rotation)
                pdf.save(path)
    pages = [{'number': 1, 'width': width, 'height': height, 'blocks': blocks}]
    translated = copy.deepcopy(pages)
    translated[0]['blocks'][0]['text'] = text
    translated[0]['blocks'][0]['layout_lines'][0]['text'] = text
    return {'schema': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages': pages, 'translated_pages': translated}


def real_ocr_fixture(path, rotation=0):
    """One small rasterised PDF for the packaged real parser / native bridge."""
    image = Image.new('RGB', (640, 480), 'white')
    strip = Image.new('RGBA', (420, 100))
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 52)
    ImageDraw.Draw(strip).text((20, 20), 'SAMPLE', fill='black', font=font)
    strip = strip.rotate(-40, expand=True, resample=Image.Resampling.BICUBIC)
    image.paste(strip, (80, 25), strip)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    with fitz.open() as pdf:
        page = pdf.new_page(width=320, height=240)
        page.insert_image(page.rect, stream=stream.getvalue(), keep_proportion=False)
        page.set_rotation(rotation)
        pdf.save(path)


class OCRGeometryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-115-geometry-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_ocr_retains_detector_quad_without_extra_recognition(self):
        engine = Mock(return_value=([(QUAD, SOURCE, .99)], None))
        with patch.object(parser, '_OCR', engine):
            result = parser.ocr_lines(Image.new('RGB', (240, 240)))
        engine.assert_called_once()
        self.assertEqual(engine.call_args.kwargs, {})
        self.assertEqual(result[0]['quad'], QUAD)
        self.assertEqual(result[0]['bbox'], RAW_BOX)
        json.dumps(result, allow_nan=False)

    def test_curved_word_uses_crop_height_not_outer_box_height(self):
        source = self.root/'curved.pdf'
        doc = fixture(source)
        line = doc['pages'][0]['blocks'][0]['layout_lines'][0]
        expected = max(math.dist(QUAD[0], QUAD[3]), math.dist(QUAD[1], QUAD[2]))*.36*.72
        self.assertAlmostEqual(line['style']['size'], expected, places=6)
        self.assertEqual(line['quad'], [[280+x*.36, 480+y*.36] for x, y in QUAD])
        self.check_output(source, doc, renderer.export_pdfs(source, doc, self.root/'out'))

    def test_cached_image_reuses_ocr_but_scales_quad_and_size_per_placement(self):
        image = figure_image()
        engine = Mock(return_value=([(QUAD, SOURCE, .99)], None))
        with patch.object(parser, '_OCR', engine), patch.object(parser, '_IMAGE_CACHE', {}), \
                patch.object(parser, 'image_tables', return_value=[]), \
                patch.object(parser, 'ruled_tables', return_value=[]):
            first = parser.image_blocks(image, self.root, 'first.png', BOUNDS, [])[0]
            second = parser.image_blocks(image, self.root, 'second.png', [20, 30, 380, 120], [])[0]
            engine.assert_called_once()
            self.assertEqual(first['ocr_lines'], second['ocr_lines'])
            self.assertEqual(first['ocr_lines'][0]['quad'], QUAD)
            self.assertEqual(second['layout_lines'][0]['quad'], [[20+x*.72, 30+y*.18] for x, y in QUAD])
            expected = max(math.hypot(26*.72, 24*.18), math.hypot(26*.72, 23*.18))*.72
            self.assertAlmostEqual(second['layout_lines'][0]['style']['size'], expected, places=6)
            self.assertNotEqual(first['layout_lines'][0]['style']['size'], second['layout_lines'][0]['style']['size'])

    def test_horizontal_single_letter_and_legacy_boxes_keep_previous_sizes(self):
        for width in (5, 100):
            line = {'text': 'I' if width == 5 else 'SAMPLE', 'bbox': [10, 20, 10+width, 40]}
            expected = 20*.5*.72
            for geometry in ({}, {'quad': [[10, 20], [10+width, 20], [10+width, 40], [10, 40]]}):
                result = parser.ocr_layout_line({**line, **geometry}, [0, 0, 500, 500], 2, .5)
                self.assertAlmostEqual(result['style']['size'], expected, places=6)
                self.assertEqual(result['style']['bold'], False)
        for quad in ([[0, 0]]*4, [[0, float('nan')]]*4):
            result = parser.ocr_layout_line({'text': 'legacy', 'bbox': [0, 0, 100, 10], 'quad': quad}, BOUNDS, 1, 1)
            self.assertNotIn('quad', result)
            self.assertEqual(result['style']['size_source'], 'ocr-axis-box')
            self.assertAlmostEqual(result['style']['size'], 7.2)

    def test_vertical_crop_axis_is_selected_before_nonuniform_page_scaling(self):
        line = {'text': 'VERTICAL', 'bbox': [10, 20, 30, 120],
                'quad': [[10, 20], [30, 20], [30, 120], [10, 120]]}
        result = parser.ocr_layout_line(line, [0, 0, 500, 500], 1.5, .2)
        self.assertAlmostEqual(result['style']['size'], 30*.72)
        self.assertEqual(line['quad'], [[10, 20], [30, 20], [30, 120], [10, 120]])

    def test_source_only_measurement_ignores_translated_length_and_style(self):
        for index, text in enumerate(('设备', '控制装置', 'AB调节')):
            source = self.root/f'{index}.pdf'
            doc = fixture(source, text=text)
            doc['translated_pages'][0]['blocks'][0]['layout_lines'][0]['style']['size'] = .1
            self.check_output(source, doc, renderer.export_pdfs(source, doc, self.root/str(index)))

    def test_rotations_polarities_and_bilingual_orders_keep_full_text_and_size(self):
        for rotation in (0, 90):
            for background in ('white', 'black'):
                for order in ('original-left', 'translated-left'):
                    with self.subTest(rotation=rotation, background=background, order=order):
                        folder = self.root/f'{rotation}-{background}-{order}'
                        folder.mkdir()
                        source = folder/'synthetic.pdf'
                        doc = fixture(source, rotation=rotation, background=background)
                        result = renderer.export_pdfs(source, doc, folder/'out', order=order)
                        self.check_output(source, doc, result)
                        with fitz.open(source) as original, fitz.open(result['bilingual']) as dual:
                            width, height = original[0].rect.width, original[0].rect.height
                            x = width if order == 'original-left' else 0
                            self.assertIn(TEXT, renderer.normalize(dual[0].get_text(clip=(x, 0, x+width, height))))

    def test_long_ocr_translation_shrinks_without_geometry_change(self):
        source=self.root/'overflow.pdf';doc=fixture(source,text='固定区域内缩小并保留完整内容。'*8)
        result=renderer.export_pdfs(source,doc,self.root/'out')
        p=self.check_output(source,doc,result)
        self.assertLess(p['scale'],.5)

    def test_raster_delivery_keeps_geometry_derived_size_and_untouched_artwork(self):
        source = self.root/'raster.png'
        doc = fixture(source, raster=True)
        result = image_renderer.export_images(source, doc, self.root/'out')
        placed = result['report']['placements'][0]
        self.assertEqual(placed['scale'], 1)
        self.assertEqual(placed['font_size'], doc['pages'][0]['blocks'][0]['layout_lines'][0]['style']['size'])
        with Image.open(source) as original, Image.open(result['translated']) as output:
            self.assertEqual(original.size, output.size)
            for area in ((165, 78, 220, 235), (73, 174, 220, 235), (260, 280, 330, 330)):
                self.assertEqual(original.crop(area).convert('RGB').tobytes(), output.crop(area).convert('RGB').tobytes())

    def test_real_ocr_parser_and_fresh_export_cli_keep_optional_geometry(self):
        source = self.root/'real-ocr.pdf'
        real_ocr_fixture(source)
        run = subprocess.run([sys.executable, '-I', str(ROOT/'src/local_parser.py'), '--input', str(source),
                              '--output', str(self.root/'parsed'), '--progress'], capture_output=True,
                             text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        self.assertTrue(json.loads(run.stdout)['ok'])
        events = [json.loads(s.removeprefix('ZHANHTRAN_PROGRESS ')) for s in run.stderr.splitlines()]
        self.assertEqual(events[-1]['done'], events[-1]['total'])
        doc = json.loads((self.root/'parsed/document.json').read_text('utf-8'))
        line = next(line for b in doc['pages'][0]['blocks'] if b['type'] == 'figure'
                    for line in b.get('layout_lines', []) if line['text'] == 'SAMPLE')
        self.assertEqual(line['style']['size_source'], 'ocr-quad-height')
        self.assertEqual(len(line['quad']), 4)
        self.assertLess(line['style']['size'], (line['bbox'][3]-line['bbox'][1])*.5)
        doc['translated_pages'] = copy.deepcopy(doc['pages'])
        for block in doc['translated_pages'][0]['blocks']:
            for target in block.get('layout_lines', []):
                if target['text'] == 'SAMPLE':
                    target['text'] = TEXT
        path = self.root/'translated.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        run = subprocess.run([sys.executable, '-I', str(ROOT/'src/local_pdf.py'), '--input', str(source),
                              '--document', str(path), '--output', str(self.root/'out'), '--progress'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        result = json.loads(run.stdout)
        self.assertTrue(result['ok'])
        with fitz.open(result['translated']) as pdf:
            self.assertIn(TEXT, pdf[0].get_text())
        report = json.loads((self.root/'out/pdf-layout-report.json').read_text('utf-8'))
        self.assertAlmostEqual(report['placements'][0]['font_size'], line['style']['size'])
        self.assertEqual(report['placements'][0]['scale'], 1)


if __name__ == '__main__':
    unittest.main()
