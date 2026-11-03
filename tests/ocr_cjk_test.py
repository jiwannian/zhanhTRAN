"""Pure-CJK OCR growth with overlapping boxes; synthetic words and pixels only."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from tests import ocr_shaped_test as shaped

renderer = shaped.renderer
# Only the reported rectangle geometry is reused, never source text or pixels.
BOX = [262.17444154418735, 382.4898033267162, 287.00355942327803, 391.18005451139857]
ABOVE = [262.84549878416277, 375.8443171266649, 367.5304282203291, 383.000994572874]
TEXT = '合成标签示例'


def fixture(path, rotation=0, background='white', translated_neighbor=True,
            text=TEXT, right_blocker=False, blocked=False, annotation=False,
            narrow=False, raster=False, bold=False, italic=False):
    bounds = [220, 350, 385, 430]
    width, height = (600, 842) if raster else (595.32, 842.04)
    if raster:
        bounds = [0, 0, width, height]
    region = fitz.Rect(bounds)
    scale = 1 if raster else 4
    light = background == 'white'
    paper = (248, 248, 248) if light else (16, 16, 16)
    ink = 'black' if light else 'white'
    image = Image.new('RGB', (round(region.width * scale), round(region.height * scale)), paper)
    draw = ImageDraw.Draw(image)
    font_path = renderer.cjk_fonts()[0] if renderer.cjk_fonts() else 'C:/Windows/Fonts/arial.ttf'
    font = ImageFont.truetype(str(font_path), max(4, round(4.2 * scale)))

    def xy(x, y):
        return ((x - region.x0) * scale, (y - region.y0) * scale)

    draw.text(xy(ABOVE[0] + 1, ABOVE[1]), 'SYNTHETIC LINE ABOVE', font=font, fill=ink)
    draw.text(xy(BOX[0] + 1, BOX[1]), 'Sample', font=font, fill=ink)
    draw.text(xy(BOX[0] + 1, 417.2), 'SYNTHETIC NEXT ROW', font=font, fill=ink)
    for a, b in [((260.3, 352), (260.3, 428)), ((378, 352), (378, 428)),
                 ((260.3, 412.5), (378, 412.5))]:
        draw.line((xy(*a), xy(*b)), fill=ink, width=max(1, round(.4 * scale)))
    # A synthetic mark below an empty font bearing blocks opaque rectangular
    # growth, while leaving the intended shaped line path clear.
    margin_x = BOX[2] - 1 if right_blocker else BOX[0] + .3
    draw.line((xy(margin_x, BOX[3] + .5), xy(margin_x, 408)), fill=ink, width=1)
    if right_blocker:
        draw.rectangle((*xy(BOX[2] + 1, BOX[1] + 1.5), *xy(BOX[2] + 65, BOX[3] + 20)), fill=ink)
    if blocked:
        draw.rectangle((*xy(BOX[0], BOX[3] + .5), *xy(BOX[2] + 65, BOX[3] + 25)), fill=ink)
        draw.rectangle((*xy(BOX[2] + .5, BOX[1] + 1.5), *xy(BOX[2] + 65, BOX[3] + 25)), fill=ink)
    lines = [
        {'text': 'SYNTHETIC LINE ABOVE', 'bbox': list(ABOVE),
         'style': {'size': (ABOVE[3] - ABOVE[1]) * .72, 'bold': False, 'family': 'serif'}},
        {'text': 'Sample', 'bbox': list(BOX),
         'style': {'size': (BOX[3] - BOX[1]) * .72, 'bold': bold, 'italic': italic, 'family': 'serif'}},
        {'text': 'SYNTHETIC NEXT ROW', 'bbox': [263.5, 417.2, 371, 424.4],
         'style': {'size': 5.184, 'bold': False, 'family': 'serif'}},
    ]
    block = {'id': 'synthetic-cjk', 'type': 'figure', 'bbox': list(BOX) if narrow else bounds,
             'text': 'Synthetic CJK layout labels', 'layout_lines': lines}
    pages = [{'number': 1, 'width': width, 'height': height, 'blocks': [block]}]
    translated = copy.deepcopy(pages)
    translated[0]['blocks'][0]['layout_lines'][1]['text'] = text
    if translated_neighbor:
        translated[0]['blocks'][0]['layout_lines'][0]['text'] = '相邻合成行'
    if raster:
        image.save(path)
    else:
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        with fitz.open() as pdf:
            page = pdf.new_page(width=width, height=height)
            page.insert_text((50, 60), 'Synthetic pure-CJK OCR placement.', fontsize=12)
            page.insert_image(region, stream=stream.getvalue(), keep_proportion=False)
            if annotation:
                annot = page.add_rect_annot((BOX[0], BOX[1], BOX[2] + 65, BOX[3] + 25))
                annot.set_opacity(0)
                annot.update()
            page.set_rotation(rotation)
            pdf.save(path)
    return {'schema': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages': pages, 'translated_pages': translated}


class CJKShapedOCRTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-cjk-113-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_cjk_label_stays_fixed_beside_unchanged_or_translated_box(self):
        for neighbor in (False,True):
            source=self.root/f'neighbor-{neighbor}.pdf';doc=fixture(source,translated_neighbor=neighbor)
            result=renderer.export_pdfs(source,doc,self.root/str(neighbor))
            self.check_output(source,doc,result)

    def test_punctuation_and_different_pure_cjk_labels_are_complete(self):
        for i, text in enumerate(('合成标签示例', '《合成标签》', '测试（样本）', '样本一二三四', '合成，文字。')):
            with self.subTest(text=text):
                source = self.root / f'words-{i}.pdf'
                doc = fixture(source, text=text)
                result = renderer.export_pdfs(source, doc, self.root / str(i))
                self.check_output(source, doc, result)

    def test_fixed_region_does_not_enter_right_blocker(self):
        source=self.root/'wrap.pdf';doc=fixture(source,right_blocker=True)
        result=renderer.export_pdfs(source,doc,self.root/'wrap')
        self.check_output(source,doc,result)

    def test_rotations_backgrounds_and_bilingual_orders(self):
        for rotation in (0, 90):
            for background in ('white', 'black'):
                for order in ('original-left', 'translated-left'):
                    with self.subTest(rotation=rotation, background=background, order=order):
                        name = f'{rotation}-{background}-{order}'
                        source = self.root / (name + '.pdf')
                        doc = fixture(source, rotation=rotation, background=background)
                        result = renderer.export_pdfs(source, doc, self.root / name, order=order)
                        self.check_output(source, doc, result)
                        with fitz.open(source) as original, fitz.open(result['bilingual']) as dual:
                            width, height = original[0].rect.width, original[0].rect.height
                            x = width if order == 'original-left' else 0
                            self.assertIn(TEXT, renderer.normalize(dual[0].get_text(clip=(x, 0, x + width, height))))

    def test_fixed_region_avoids_ink_annotation_bounds_and_shrinks_long_text(self):
        for name,options in [('ink',{'blocked':True}),('annotation',{'annotation':True}),('bounds',{'narrow':True}),('long',{'text':TEXT*25})]:
            source=self.root/(name+'.pdf');doc=fixture(source,**options)
            result=renderer.export_pdfs(source,doc,self.root/name)
            self.check_output(source,doc,result)

    def test_metadata_only_guard_and_prior_translation_block_added_glyphs(self):
        source = self.root / 'guard.pdf'
        doc = fixture(source)
        with fitz.open(source) as original, fitz.open() as candidate:
            page = candidate.new_page(width=595.32, height=842.04)
            units = renderer.page_units(original[0], doc['pages'][0], doc['translated_pages'][0], {})
            item = next(i for i in units if i['ids'] == ['synthetic-cjk-ocr1'])
            guard = [BOX[0], BOX[1], BOX[2] + 65, BOX[3] + 25]
            guarded = {**item, 'ocr_guards': [*item['ocr_guards'], guard]}
            self.assertIsNone(renderer.place_ocr_shaped(page, original[0], guarded, [], [], (.97, .97, .97), 'black'))
            self.assertIsNone(renderer.place_ocr_shaped(page, original[0], item, units, [{'bbox': guard}], (.97, .97, .97), 'black'))
            self.assertFalse(page.get_contents())

    def test_bold_keeps_source_style_and_size(self):
        source = self.root / 'bold.pdf'
        doc = fixture(source, bold=True)
        result = renderer.export_pdfs(source, doc, self.root / 'bold')
        placed = self.check_output(source, doc, result)
        with fitz.open(result['translated']) as pdf:
            spans = [s for b in pdf[0].get_text('dict', clip=placed['bbox'])['blocks'] if b['type'] == 0
                     for line in b['lines'] for s in line['spans']]
            self.assertTrue(all(s['flags'] & 16 for s in spans))

    def test_raster_export_keeps_full_cjk_and_neighbor_pixels(self):
        source = self.root / 'source.png'
        doc = fixture(source, raster=True)
        result = shaped.image_renderer.export_images(source, doc, self.root / 'image')
        placed = next(p for p in result['report']['placements'] if p['ids'] == ['synthetic-cjk-ocr1'])
        self.assertEqual(placed['fit'], 'ocr-shaped-lines')
        self.assertEqual(placed['scale'], 1)
        with Image.open(source) as original, Image.open(result['translated']) as translated:
            self.assertEqual(original.size, translated.size)
            for area in ((259, 351, 261, 429), (377, 351, 379, 429), (260, 412, 379, 414), (262, 416, 372, 426)):
                self.assertEqual(original.crop(area).convert('RGB').tobytes(), translated.crop(area).convert('RGB').tobytes())
            added = (288, 384, 299, 390)
            self.assertNotEqual(original.crop(added).convert('RGB').tobytes(), translated.crop(added).convert('RGB').tobytes())

    def test_cli_reports_one_json_and_completed_page_progress(self):
        source = self.root / 'cli.pdf'
        doc = fixture(source)
        document = self.root / 'translated.json'
        document.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        run = subprocess.run([sys.executable, '-I', str(shaped.ROOT / 'src/local_pdf.py'),
                              '--input', str(source), '--document', str(document),
                              '--output', str(self.root / 'out'), '--progress'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        response = json.loads(run.stdout)
        self.assertTrue(response['ok'])
        self.assertEqual([json.loads(line.removeprefix('ZHANHTRAN_PROGRESS ')) for line in run.stderr.splitlines()],
                         [{'stage': 'export', 'done': 1, 'total': 1}])
        report = json.loads((self.root / 'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(source, doc, {**response, 'report': report})


if __name__ == '__main__':
    unittest.main()
