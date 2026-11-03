"""Ink-aligned OCR labels. Every word, image and source PDF is synthetic."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from tests.ocr_shaped_test import ROOT, image_renderer, renderer


BOX = [234.83343984430488, 170.60000610351562,
       253.82304999611594, 177.24507592429578]
LOWER = [234.83343984430488, 176.77042807995434,
         256.1967512650923, 183.41549790073447]
BOUNDS = [220, 155, 287, 195]
SIZE = 4.784450270961709
TEXT = '7号控制器'


def fixture(path, rotation=0, background='white', translated_neighbor=False,
            raster=False, blocked=False, narrow=False, text=TEXT):
    """Two labels with overlapping OCR bearings, but separate visible ink."""
    region = fitz.Rect([0, 0, 600, 400] if raster else BOUNDS)
    width, height = (600, 400) if raster else (595.32, 842.04)
    scale = 1 if raster else 8
    paper = (248, 248, 248) if background == 'white' else (16, 16, 16)
    ink = 'black' if background == 'white' else 'white'
    image = Image.new('RGB', (round(region.width * scale), round(region.height * scale)), paper)
    draw = ImageDraw.Draw(image)

    def xy(x, y):
        return ((x-region.x0)*scale, (y-region.y0)*scale)

    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', max(4, round(4*scale)))
    draw.text(xy(BOX[0]+.3, BOX[1]+.5), 'UNIT#7', font=font, fill=ink, anchor='lt')
    draw.text(xy(LOWER[0]+.3, LOWER[1]+1.5), 'CODE#4', font=font, fill=ink, anchor='lt')
    # Graphic content must not be erased by an opaque enlarged rectangle.
    for a, b in (((BOX[0]-1, BOX[1]-1), (BOX[0]-1, LOWER[3]+2)),
                 ((BOX[2]+6, BOX[1]-1), (BOX[2]+6, LOWER[3]+2)),
                 ((BOX[0]-1, LOWER[3]+2), (BOX[2]+6, LOWER[3]+2))):
        draw.line((xy(*a), xy(*b)), fill=ink, width=max(1, round(.3*scale)))
    if blocked:
        draw.rectangle((*xy(BOX[2], BOX[1]), *xy(BOX[2]+5, LOWER[3]+1)), fill=ink)
    bounds = list(BOX) if narrow else list(region)
    block = {'id': 'synthetic-ink', 'type': 'figure', 'bbox': bounds,
             'text': 'UNIT#7\nCODE#4', 'layout_lines': [
                 {'text': 'UNIT#7', 'bbox': list(BOX),
                  'style': {'size': SIZE, 'bold': False, 'family': 'serif'}},
                 {'text': 'CODE#4', 'bbox': list(LOWER),
                  'style': {'size': SIZE, 'bold': False, 'family': 'serif'}}]}
    if raster:
        image.save(path)
    else:
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        with fitz.open() as pdf:
            page = pdf.new_page(width=width, height=height)
            page.insert_text((50, 60), 'Synthetic adjacent OCR labels.', fontsize=12)
            page.insert_image(region, stream=stream.getvalue(), keep_proportion=False)
            page.set_rotation(rotation)
            pdf.save(path)
    pages = [{'number': 1, 'width': width, 'height': height, 'blocks': [block]}]
    translated = copy.deepcopy(pages)
    translated[0]['blocks'][0]['layout_lines'][0]['text'] = text
    if translated_neighbor:
        translated[0]['blocks'][0]['layout_lines'][1]['text'] = '4号'
    return {'schema': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages': pages, 'translated_pages': translated}


class InkAlignedOCRTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-ink-116-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_adjacent_mixed_label_fits_without_changing_source_size_or_anchor(self):
        source = self.root/'source.pdf'
        document = fixture(source)
        self.check_output(source, document, renderer.export_pdfs(source, document, self.root/'out'))

    def test_light_dark_rotations_panel_orders_and_translated_neighbor(self):
        for background in ('white', 'black'):
            for rotation in (0, 90):
                for neighbor in (False, True):
                    for order in ('original-left', 'translated-left'):
                        with self.subTest(background=background, rotation=rotation, neighbor=neighbor, order=order):
                            folder = self.root/f'{background}-{rotation}-{neighbor}-{order}'
                            folder.mkdir()
                            source = folder/'source.pdf'
                            document = fixture(source, rotation=rotation, background=background,
                                               translated_neighbor=neighbor)
                            result = renderer.export_pdfs(source, document, folder/'out', order=order)
                            self.check_output(source, document, result)
                            with fitz.open(source) as original, fitz.open(result['bilingual']) as dual:
                                w, h = original[0].rect.width, original[0].rect.height
                                x = w if order == 'original-left' else 0
                                self.assertIn(TEXT, renderer.normalize(dual[0].get_text(clip=(x, 0, x+w, h))))
                                x = 0 if order == 'original-left' else w
                                self.assertNotIn(TEXT, dual[0].get_text(clip=(x, 0, x+w, h)))

    def test_normal_metrics_and_rectangular_growth_still_reject_unsafe_candidates(self):
        source = self.root/'metrics.pdf'
        document = fixture(source)
        with fitz.open(source) as original, fitz.open() as candidate:
            page = candidate.new_page(width=595.32, height=842.04)
            item = renderer.page_units(original[0], document['pages'][0], document['translated_pages'][0], {})[0]
            background = renderer.background_for(original[0], item['rect'])
            self.assertFalse(list(renderer.vacant_ocr_frames(original[0], item, [item], [], background)))
            rect = item['rect']
            bottom = min(BOUNDS[3], rect.y1+max(rect.height, SIZE*2.24))
            for width in (rect.width, rect.width+max(rect.width, SIZE*4)):
                self.assertIsNone(renderer.shaped_ocr_at_width(page, original[0], item, [item], [],
                                  background, 'black', TEXT, fitz.Rect(BOUNDS), bottom, width))
            self.assertFalse(page.get_contents())

    def test_ink_boundaries_not_font_metrics_keep_accents_descenders_and_punctuation(self):
        # Compare the new clipped form to the SAME glyphs translated with no
        # clipping at all. Several DPI grids detect any lost faint/edge pixels.
        for text in ('7Ágj控制器', 'Ágj中文（测试）', 'Épq示例℃′″'):
            for bold, italic in ((False, False), (True, False), (False, True)):
                with self.subTest(text=text, bold=bold, italic=italic), \
                        fitz.open() as original, fitz.open() as candidate, fitz.open() as expected:
                    src = original.new_page(width=600, height=400)
                    page = candidate.new_page(width=600, height=400)
                    control = expected.new_page(width=600, height=400)
                    rect = fitz.Rect(60.375, 80.25, 300.375, 110.25)
                    item = {'rect': rect, 'size': 12, 'text': text, 'bold': bold, 'ocr_guards': [],
                            'style': {'family': 'serif', 'italic': italic}}
                    bounds, bottom, width = src.rect, 160, rect.width
                    result = renderer.shaped_ocr_at_width(page, src, item, [], [], (1, 1, 1),
                                                         'black', text, bounds, bottom, width, align_ink=True)
                    self.assertIsNotNone(result)
                    shaped = {**item, 'indent': 0, 'style': {**item['style'], 'line_height': 1.5}}
                    story = renderer.story_for(shaped, src)
                    probe_top = 36
                    more, _ = story.place(fitz.Rect(0, probe_top, width, probe_top+bottom-rect.y0+36))
                    self.assertFalse(more)
                    preview_bounds = fitz.Rect(0, 0, width, probe_top+bottom-rect.y0+72)
                    with fitz.open(stream=renderer.story_pdf(story, preview_bounds), filetype='pdf') as preview:
                        def first_origin(p):
                            return next(s['origin'] for b in p.get_text('dict')['blocks'] if b['type'] == 0
                                        for line in b['lines'] for s in line['spans'])
                        old_origin, new_origin = first_origin(preview[0]), first_origin(page)
                        shift = fitz.Matrix(1, 0, 0, 1, new_origin[0]-old_origin[0], new_origin[1]-old_origin[1])
                        control.show_pdf_page(preview_bounds*shift, preview, 0, keep_proportion=False)
                    self.assertEqual(renderer.normalize(page.get_text()), renderer.normalize(text))
                    spans = [s for b in page.get_text('dict')['blocks'] if b['type'] == 0
                             for line in b['lines'] for s in line['spans']]
                    self.assertTrue(all(abs(s['size']-12) < .001 for s in spans))
                    # Compare fallback styles with the uncut reference, too:
                    # some special symbols use a regular system fallback.
                    reference_spans = [s for b in control.get_text('dict')['blocks'] if b['type'] == 0
                                       for line in b['lines'] for s in line['spans']]
                    self.assertEqual([s['flags'] for s in spans], [s['flags'] for s in reference_spans])
                    for dpi in (72, 96, 144, 288, 576):
                        matrix = fitz.Matrix(dpi/72, dpi/72)
                        a = page.get_pixmap(matrix=matrix, alpha=True)
                        b = control.get_pixmap(matrix=matrix, alpha=True)
                        mask = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, 4)[:, :, 3]
                        reference = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, 4)[:, :, 3]
                        changed = np.where(mask != reference)
                        self.assertTrue(np.array_equal(mask, reference),
                                        f'ink difference at {dpi} dpi: count={len(changed[0])}, '
                                        f'max={np.abs(mask.astype(int)-reference).max()}, '
                                        f'y={sorted(set(changed[0]))}, x={sorted(set(changed[1]))}')

    def test_fixed_region_preserves_ink_and_bounds_with_excessive_text(self):
        for name,options in [('ink',{'blocked':True}),('bounds',{'narrow':True}),('long',{'text':TEXT*25})]:
            source=self.root/f'{name}.pdf';doc=fixture(source,**options)
            result=renderer.export_pdfs(source,doc,self.root/name)
            self.check_output(source,doc,result)

    def test_transparent_annotations_widgets_native_text_and_prior_placements_remain_guards(self):
        for kind in ('annotation', 'widget', 'native', 'prior', 'ocr'):
            with self.subTest(kind=kind):
                source = self.root/f'{kind}.pdf'
                document = fixture(source)
                with fitz.open(source) as original, fitz.open() as candidate:
                    area = fitz.Rect(BOX[2]+.05, BOX[1], BOX[2]+5, LOWER[3]+1)
                    page = candidate.new_page(width=595.32, height=842.04)
                    if kind == 'annotation':
                        annotation = original[0].add_rect_annot(area)
                        annotation.set_opacity(0)
                        annotation.update()
                    elif kind == 'widget':
                        widget = fitz.Widget()
                        widget.field_type, widget.field_name, widget.rect = fitz.PDF_WIDGET_TYPE_TEXT, 'synthetic', area
                        original[0].add_widget(widget)
                    elif kind == 'native':
                        original[0].insert_text((area.x0, area.y0+4.5), 'X', fontsize=5, color=(.97, .97, .97))
                    item = renderer.page_units(original[0], document['pages'][0], document['translated_pages'][0], {})[0]
                    if kind == 'ocr':
                        item['ocr_guards'].append(list(area))
                    placed = [{'bbox': list(area)}] if kind == 'prior' else []
                    self.assertIsNone(renderer.place_ocr_shaped(page, original[0], item, [item], placed,
                                      renderer.background_for(original[0], item['rect']), 'black'))
                    self.assertFalse(page.get_contents())

    def test_raster_export_copies_full_label_but_preserves_neighbor_and_graphics(self):
        source = self.root/'source.png'
        document = fixture(source, raster=True)
        result = image_renderer.export_images(source, document, self.root/'out')
        placed = result['report']['placements'][0]
        self.assertEqual(placed['alignment'], 'glyph-ink-top')
        self.assertEqual(placed['scale'], 1)
        self.assertEqual(placed['font_size'], SIZE)
        with Image.open(source) as before, Image.open(result['translated']) as after:
            self.assertEqual(before.size, after.size)
            for area in ((234, 178, 257, 184), (259, 169, 262, 186), (232, 169, 234, 187)):
                self.assertEqual(before.crop(area).convert('RGB').tobytes(), after.crop(area).convert('RGB').tobytes())
            area = (254, 171, 257, 176)
            self.assertNotEqual(before.crop(area).convert('RGB').tobytes(), after.crop(area).convert('RGB').tobytes())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), document['source_sha256'])

    def test_cli_has_one_final_json_and_independent_progress(self):
        source = self.root/'source.pdf'
        document = fixture(source)
        path = self.root/'document.json'
        path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        run = subprocess.run([sys.executable, '-I', str(ROOT/'src/local_pdf.py'), '--input', str(source),
                              '--document', str(path), '--output', str(self.root/'out'), '--progress'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        result = json.loads(run.stdout)
        self.assertTrue(result['ok'])
        events = [json.loads(line.removeprefix('ZHANHTRAN_PROGRESS ')) for line in run.stderr.splitlines()]
        self.assertEqual(events, [{'stage': 'export', 'done': 1, 'total': 1}])
        report = json.loads((self.root/'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(source, document, {**result, 'report': report})


if __name__ == '__main__':
    unittest.main()
