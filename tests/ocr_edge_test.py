"""Fractional source-edge antialiasing; synthetic glyphs/pixels only."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz
from PIL import Image, ImageDraw

from tests import ocr_cjk_test as cjk

renderer = cjk.renderer
BOX = cjk.BOX
TEXT = '测试字符合成'


def fixture(path, background='white', rotation=0, raster=False, edge='left',
            text=TEXT, blocked=False, right_blocker=False):
    document = cjk.fixture(path, background=background, rotation=rotation, raster=raster,
                           text=text, blocked=blocked, right_blocker=right_blocker)
    rect = fitz.Rect(BOX)
    # A synthetic source-glyph stroke straddles the fractional edge. No chart
    # or paper pixels are reused. Its outside remnant is never borrowed space.
    stroke = fitz.Rect(rect.x0 - .20, rect.y0 + 2, rect.x0 + .20, rect.y1 - .75)
    if edge == 'top':
        stroke = fitz.Rect(rect.x0 + 4, rect.y0 - .20, rect.x0 + 17, rect.y0 + .20)
    if raster:
        with Image.open(path) as original:
            image = original.convert('RGB')
        draw = ImageDraw.Draw(image)
        draw.rectangle(tuple(stroke), fill='black' if background == 'white' else 'white')
        image.save(path)
    else:
        with fitz.open(path) as pdf:
            page = pdf[0]
            page.set_rotation(0)
            ink = (0, 0, 0) if background == 'white' else (1, 1, 1)
            page.draw_rect(stroke, color=None, fill=ink)
            page.set_rotation(rotation)
            data = pdf.tobytes()
        path.write_bytes(data)
    document['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return document


class OCREdgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-edge-114-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_source_edge_label_shrinks_without_escaping_fixed_region(self):
        source = self.root / 'edge.pdf'
        doc = fixture(source)
        result = renderer.export_pdfs(source, doc, self.root / 'out')
        placed = self.check_output(source, doc, result)
        self.assertGreater(placed['scale'],0)
        self.assertLess(placed['scale'],1)

    def test_left_and_top_edges_light_dark_rotation_and_panel_order(self):
        for edge in ('left', 'top'):
            for background in ('white', 'black'):
                for rotation, order in ((0, 'original-left'), (90, 'translated-left')):
                    with self.subTest(edge=edge, background=background, rotation=rotation, order=order):
                        name = f'{edge}-{background}-{rotation}'
                        source = self.root / (name + '.pdf')
                        doc = fixture(source, edge=edge, background=background, rotation=rotation)
                        result = renderer.export_pdfs(source, doc, self.root / name, order=order)
                        self.check_output(source, doc, result)
                        with fitz.open(source) as original, fitz.open(result['bilingual']) as dual:
                            w, h = original[0].rect.width, original[0].rect.height
                            x = w if order == 'original-left' else 0
                            self.assertIn(renderer.normalize(TEXT), renderer.normalize(dual[0].get_text(clip=(x, 0, x + w, h))))

    def test_different_cjk_shapes_and_punctuation_keep_full_text(self):
        for i, text in enumerate(('测试字符合成', '中文边界示例', '《边缘文字》', '测试（边缘）')):
            with self.subTest(text=text):
                source = self.root / f'shapes-{i}.pdf'
                doc = fixture(source, text=text)
                result = renderer.export_pdfs(source, doc, self.root / str(i))
                self.check_output(source, doc, result)

    def test_changed_fractional_pixel_grid_keeps_text_size_and_real_anchor(self):
        for index, (dx, dy) in enumerate(((-.22, 0), (-.08, .18), (.10, 0), (.27, .18), (.43, 0))):
            with self.subTest(dx=dx, dy=dy):
                source = self.root / f'shift-{index}.pdf'
                doc = fixture(source)
                with fitz.open(source) as original, fitz.open() as shifted:
                    page = shifted.new_page(width=original[0].rect.width, height=original[0].rect.height)
                    page.show_pdf_page(original[0].rect + (dx, dy, dx, dy), original, 0)
                    data = shifted.tobytes()
                source.write_bytes(data)
                for pages in (doc['pages'], doc['translated_pages']):
                    for block in pages[0]['blocks']:
                        block['bbox'] = list(fitz.Rect(block['bbox']) + (dx, dy, dx, dy))
                        for line in block['layout_lines']:
                            line['bbox'] = list(fitz.Rect(line['bbox']) + (dx, dy, dx, dy))
                doc['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
                result = renderer.export_pdfs(source, doc, self.root / str(index))
                self.assertEqual(result['report']['unplaced'], [])
                placed = next(p for p in result['report']['placements'] if p['ids'] == ['synthetic-cjk-ocr1'])
                line = doc['pages'][0]['blocks'][0]['layout_lines'][1]
                self.assertGreater(placed['scale'],0)
                self.assertLessEqual(placed['scale'],1)
                self.assertEqual(placed['source_bbox'], line['bbox'])
                self.assertEqual(placed['bbox'][:2], line['bbox'][:2])
                with fitz.open(result['translated']) as output:
                    spans = [s for b in output[0].get_text('dict', clip=placed['bbox'])['blocks'] if b['type'] == 0
                             for row in b['lines'] for s in row['spans']]
                    self.assertEqual(renderer.normalize(''.join(s['text'] for s in spans)), TEXT)
                    self.assertAlmostEqual(min(s['bbox'][0] for s in spans), line['bbox'][0], places=3)
                    self.assertTrue(all(abs(s['size'] - placed['font_size']) < .001 for s in spans))
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), doc['source_sha256'])

    def test_fixed_region_does_not_enter_right_or_lower_ink(self):
        source=self.root/'blocked.pdf';doc=fixture(source,blocked=True)
        result=renderer.export_pdfs(source,doc,self.root/'out')
        self.check_output(source,doc,result)

    def test_invisible_guard_in_new_glyph_footprint_is_not_relaxed(self):
        source = self.root / 'invisible.pdf'
        doc = fixture(source)
        with fitz.open(source) as original, fitz.open() as candidate:
            page = candidate.new_page(width=595.32, height=842.04)
            units = renderer.page_units(original[0], doc['pages'][0], doc['translated_pages'][0], {})
            item = next(i for i in units if i['ids'] == ['synthetic-cjk-ocr1'])
            # Blocks any added glyph outside the erased rectangle, even with
            # no visible ink. Known guards retain the full footprint check.
            guard = [BOX[0] - .1, BOX[1] - .1, BOX[2] + 50, BOX[3] + 20]
            annot = original[0].add_rect_annot(guard)
            annot.set_opacity(0)
            annot.update()
            self.assertIsNone(renderer.place_ocr_shaped(page, original[0], item, units, [], (.97, .97, .97), 'black'))
            self.assertFalse(page.get_contents())

    def test_raster_output_keeps_new_ink_and_adjacent_pixels(self):
        source = self.root / 'edge.png'
        doc = fixture(source, raster=True)
        result = cjk.shaped.image_renderer.export_images(source, doc, self.root / 'out')
        placed = next(p for p in result['report']['placements'] if p['ids'] == ['synthetic-cjk-ocr1'])
        self.assertEqual(placed['fit'], 'ocr-shaped-lines')
        self.assertEqual(placed['scale'], 1)
        with Image.open(source) as original, Image.open(result['translated']) as output:
            self.assertEqual(original.size, output.size)
            for area in ((259, 351, 261, 429), (377, 351, 379, 429), (260, 412, 379, 414), (262, 416, 372, 426)):
                self.assertEqual(original.crop(area).convert('RGB').tobytes(), output.crop(area).convert('RGB').tobytes())

    def test_cli_reports_complete_output_and_single_json(self):
        source = self.root / 'cli.pdf'
        doc = fixture(source)
        path = self.root / 'document.json'
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        run = subprocess.run([sys.executable, '-I', str(cjk.shaped.ROOT / 'src/local_pdf.py'), '--input', str(source),
                              '--document', str(path), '--output', str(self.root / 'out'), '--progress'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        result = json.loads(run.stdout)
        self.assertTrue(result['ok'])
        self.assertEqual([json.loads(line.removeprefix('ZHANHTRAN_PROGRESS ')) for line in run.stderr.splitlines()],
                         [{'stage': 'export', 'done': 1, 'total': 1}])
        report = json.loads((self.root / 'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(source, doc, {**result, 'report': report})


if __name__ == '__main__':
    unittest.main()
