"""Tight raster-label regressions; only small, wholly synthetic documents."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ocr_renderer', ROOT / 'src/local_pdf.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
IMAGE_SPEC = importlib.util.spec_from_file_location('ocr_image_renderer', ROOT / 'src/local_image.py')
image_renderer = importlib.util.module_from_spec(IMAGE_SPEC)
IMAGE_SPEC.loader.exec_module(image_renderer)

BOX = [286.72471388020176, 217.17860968707922, 304.02586662302775, 223.21819728666406]
OCR8_BOX = [120.39082435268139, 246.1686301650864, 151.04725289698715, 253.4161352845882]
BOUNDS = [80.32499694824219, 177.719970703125, 530.1549682617188, 401.989990234375]


def fixture(path, text='合成标签', blocked=False, rotation=0, background='white', narrow=False,
            textured=False, label_box=None):
    """Only diagnostic geometry is reused, never source pixels or source text."""
    width, height = 595.32, 842.04
    region = fitz.Rect(BOUNDS)
    label_box = list(label_box or BOX)
    image = Image.new('RGB', (1800, 900), background)
    if textured:
        import numpy as np
        pixels = np.asarray(image).copy()
        noise = np.random.default_rng(105).integers(-24, 25, pixels.shape[:2], dtype=np.int16)
        pixels = np.clip(pixels.astype(np.int16) + noise[:, :, None], 0, 255).astype('uint8')
        image = Image.fromarray(pixels, 'RGB')
    draw = ImageDraw.Draw(image)
    sx, sy = image.width / region.width, image.height / region.height
    def xy(x, y):
        return ((x - region.x0) * sx, (y - region.y0) * sy)
    color = 'white' if background == 'black' else 'black'
    font = ImageFont.truetype('C:/Windows/Fonts/times.ttf', 14)
    draw.text(xy(label_box[0]+.5, label_box[1]+.5), 'SourceAB', font=font, fill=color)
    if blocked:
        # Adjacent chart grid/marker ink that is deliberately not OCR metadata.
        draw.rectangle((*xy(label_box[2], label_box[1]), *xy(label_box[2]+35, label_box[3]+20)), fill=color)
        draw.rectangle((*xy(label_box[0], label_box[3]), *xy(label_box[2], label_box[3]+20)), fill=color)
    else:
        draw.line([xy(200,270),xy(450,270)], fill=color, width=3)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    with fitz.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        page.insert_text((50,60),'Synthetic raster label geometry regression.',fontsize=12)
        page.insert_image(region, stream=stream.getvalue(), keep_proportion=False)
        page.set_rotation(rotation)
        pdf.save(path)
    bounds = list(label_box) if narrow else list(BOUNDS)
    block = {'id': 'synthetic-chart', 'type': 'figure', 'bbox': bounds, 'text': 'SourceAB',
             'layout_lines': [{'text': 'SourceAB', 'bbox': label_box}]}
    pages = [{'number': 1, 'width': width, 'height': height, 'blocks': [block]}]
    after = copy.deepcopy(pages)
    after[0]['blocks'][0]['text'] = text
    after[0]['blocks'][0]['layout_lines'][0]['text'] = text
    return {'schema': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages': pages, 'translated_pages': after}


class OCRLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-105-ocr-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.pdf'

    def render(self, **kwargs):
        document = fixture(self.source, **kwargs)
        return document, renderer.export_pdfs(self.source, document, self.root / 'output')

    def test_four_character_label_shrinks_only_inside_source_region(self):
        document=fixture(self.source,)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed_ocr
        check_fixed_ocr(self,self.source,document,result)

    def test_cjk_label_uses_fixed_frame_when_no_safe_margin(self):
        document=fixture(self.source,text='合成地图标签',narrow=True,label_box=OCR8_BOX)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed_ocr
        check_fixed_ocr(self,self.source,document,result)

    def test_tight_cjk_label_does_not_overwrite_neighbor_or_high_contrast_chart_ink(self):
        for background, rotation, order in [('white',0,'original-left'),('black',90,'translated-left')]:
            with self.subTest(background=background, rotation=rotation):
                source = self.root / (background+'.pdf')
                document = fixture(source, text='合成地图标签', blocked=True,
                                   label_box=OCR8_BOX, background=background,
                                   textured=True, rotation=rotation)
                neighbor = [134.65668714062565,253.81877445789385,151.3507818924753,261.0662795773957]
                for pages in (document['pages'],document['translated_pages']):
                    pages[0]['blocks'][0]['layout_lines'].append({'text':'99','bbox':neighbor})
                    pages[0]['blocks'][0]['layout_lines'].append({
                        'text':'88','bbox':[OCR8_BOX[2],OCR8_BOX[1],OCR8_BOX[2]+35,OCR8_BOX[3]+20]})
                result = renderer.export_pdfs(source,document,self.root/background,order=order)
                placed = result['report']['placements'][0]
                self.assertEqual(placed['fit'],'fixed-region')
                self.assertEqual(placed['bbox'],OCR8_BOX)
                self.assertGreater(placed['scale'],0)
                self.assertLessEqual(placed['scale'],1)
                with fitz.open(source) as original, fitz.open(result['translated']) as output:
                    self.assertIn('合成地图标签',renderer.normalize(output[0].get_text()))
                    original[0].set_rotation(0)
                    output[0].set_rotation(0)
                    clip = fitz.Rect(neighbor)+(0,.25,0,0)
                    self.assertEqual(original[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=clip).samples,
                                     output[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=clip).samples)

    def test_tracking_does_not_bypass_mixed_script_shaping_or_excessive_width(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            for text in ['合成地图标签测试','合成12标签','合成标签。','한글라벨','合成かな']:
                item = {'text':text,'rect':fitz.Rect(OCR8_BOX),'size':5.218203686041292,'bold':False}
                before = page.get_contents()
                self.assertIsNone(renderer.place_ocr_tracking(page,item,'black'))
                self.assertEqual(page.get_contents(),before)

    def test_tracking_uses_original_bold_face_and_refuses_invalid_font_metrics(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            item = {'text':'合成地图标签','rect':fitz.Rect(OCR8_BOX),'size':5.218203686041292,'bold':True}
            spacing = renderer.place_ocr_tracking(page,item,'white')
            self.assertIsNotNone(spacing)
            spans = [s for b in page.get_text('dict')['blocks'] if b['type']==0
                     for line in b['lines'] for s in line['spans']]
            self.assertEqual(renderer.normalize(''.join(s['text'] for s in spans)),'合成地图标签')
            self.assertTrue(all('Bold' in s['font'] and s['color']==0xffffff for s in spans))
            self.assertTrue(all(abs(s['size']-item['size'])<.001 for s in spans))
            for options in [{'style':{'italic':True}}, {'size':10}, {'text':'甲'}]:
                before = list(page.get_contents())
                self.assertIsNone(renderer.place_ocr_tracking(page,{**item,**options},'black'))
                self.assertEqual(page.get_contents(),before)
            with patch.object(renderer,'cjk_fonts',return_value=None):
                self.assertIsNone(renderer.place_ocr_tracking(page,item,'black'))

    def test_textured_ocr_near_fit_uses_tighter_tracking_at_source_size(self):
        document = fixture(self.source)
        region = fitz.Rect(BOUNDS)
        # Near-fit CJK width is smaller than even the minimum right-space
        # allowance. A full-size, single-glyph label must fit without growing.
        document['translated_pages'][0]['blocks'][0]['layout_lines'][0]['text'] = '甲'
        result = renderer.export_pdfs(self.source, document, self.root/'near-fit')
        placed = result['report']['placements'][0]
        self.assertEqual(placed['fit'], 'fixed-region')
        self.assertEqual(placed['scale'], 1)
        self.assertEqual(placed['bbox'][:2], BOX[:2])
        self.assertAlmostEqual(placed['font_size'], (BOX[3]-BOX[1])*.72, places=5)

    def test_image_only_labels_do_not_inherit_an_unrelated_page_font(self):
        document = fixture(self.source)
        block = document['pages'][0]['blocks'][0]
        block['style'] = {'size': 30, 'bold': True}
        with fitz.open(self.source) as pdf:
            units = renderer.page_units(pdf[0], document['pages'][0], document['translated_pages'][0], {})
        self.assertAlmostEqual(units[0]['size'], (BOX[3]-BOX[1])*.72, places=5)
        self.assertFalse(units[0]['bold'])

    def test_cached_ocr_results_get_distinct_scaled_source_sizes(self):
        spec = importlib.util.spec_from_file_location('ocr_style_parser',ROOT/'src/local_parser.py')
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        image = Image.new('RGB',(200,100),'white')
        lines = [{'text':'SourceAB','bbox':[10,10,70,20],'confidence':.99}]
        with patch.object(parser,'ocr_lines',return_value=lines) as ocr, \
             patch.object(parser,'image_tables',return_value=[]),patch.object(parser,'ruled_tables',return_value=[]):
            first = parser.image_blocks(image,self.root,'one.png',[0,0,100,50],[])[0]
            second = parser.image_blocks(image,self.root,'two.png',[0,0,200,100],[])[0]
        self.assertEqual(ocr.call_count,1)
        self.assertAlmostEqual(first['layout_lines'][0]['style']['size'],3.6)
        self.assertAlmostEqual(second['layout_lines'][0]['style']['size'],7.2)

    def test_unchanged_ocr_label_and_cached_style_are_kept_as_guards(self):
        document = fixture(self.source)
        before = document['pages'][0]['blocks'][0]
        after = document['translated_pages'][0]['blocks'][0]
        before['layout_lines'][0]['style'] = {'size': 4.2, 'bold': False}
        for block in (before, after):
            block['layout_lines'].append({'text': '99', 'bbox': [BOX[2], BOX[1], BOX[2]+12, BOX[3]+15]})
        with fitz.open(self.source) as pdf:
            units = renderer.page_units(pdf[0], document['pages'][0], document['translated_pages'][0], {})
            self.assertEqual(units[0]['size'], 4.2)
            frames = list(renderer.vacant_ocr_frames(pdf[0], units[0], units, [], (1,1,1)))
        self.assertTrue(all(frame.x1 <= BOX[2]+.001 for frame in frames))

    def test_unrecognised_chart_ink_is_not_borrowed(self):
        document=fixture(self.source,text='合成图标签',blocked=True)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed_ocr
        check_fixed_ocr(self,self.source,document,result)

    def test_pdf_textured_label_does_not_borrow_any_margin(self):
        document=fixture(self.source,textured=True)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed_ocr
        check_fixed_ocr(self,self.source,document,result)

    def test_textured_expansion_stops_before_an_untranslated_neighboring_label(self):
        document = fixture(self.source, textured=True)
        for pages in (document['pages'], document['translated_pages']):
            block = pages[0]['blocks'][0]
            block['layout_lines'].append({'text':'99', 'bbox':[BOX[2]+.1, BOX[1], BOX[2]+12, BOX[3]]})
        with fitz.open(self.source) as pdf:
            units = renderer.page_units(pdf[0], document['pages'][0],
                                        document['translated_pages'][0], {})
            frames = list(renderer.vacant_ocr_frames(pdf[0], units[0], units, [], (1,1,1)))
        self.assertTrue(all(frame.x1 <= BOX[2]+.101 for frame in frames))

    def test_pdf_label_does_not_borrow_space_below_source_anchor(self):
        document=fixture(self.source,)
        result=renderer.export_pdfs(self.source,document,self.root/'output')
        from tests.fixed_layout_test import check_fixed_ocr
        check_fixed_ocr(self,self.source,document,result)

    def test_same_coordinates_on_other_source_pages_do_not_block_growth(self):
        document = fixture(self.source)
        report = {'placements':[{'page':1,'output_page':1,'bbox':list(fitz.Rect(BOUNDS))}],
                  'unplaced':[]}
        with fitz.open(self.source) as original,fitz.open(self.source) as output:
            units = renderer.page_units(original[0],document['pages'][0],document['translated_pages'][0],report)
            renderer.place_page(output[0],original[0],units,2,report)
        self.assertEqual(report['placements'][-1]['fit'],'ocr-vacant')

    def test_narrow_and_excessive_ocr_translations_stay_in_fixed_region(self):
        from tests.fixed_layout_test import check_fixed_ocr
        for name,options in [('figure-boundary',{'narrow':True,'text':'合成图标签'}),('too-long',{'text':'合成标签'*200})]:
            document=fixture(self.source,**options)
            result=renderer.export_pdfs(self.source,document,self.root/name)
            check_fixed_ocr(self,self.source,document,result)

    def test_missing_ocr_translation_is_rejected(self):
        document = fixture(self.source, text=' ')
        with self.assertRaisesRegex(ValueError, 'PDF_TRANSLATION_INCOMPLETE'):
            renderer.export_pdfs(self.source, document, self.root/'output')

    def test_dark_background_and_both_rotated_panel_orders(self):
        for rotation, order in [(0,'original-left'), (90,'translated-left')]:
            with self.subTest(rotation=rotation):
                document = fixture(self.source, rotation=rotation, background='black')
                result = renderer.export_pdfs(self.source, document, self.root/str(rotation), order=order)
                with fitz.open(self.source) as original, fitz.open(result['bilingual']) as pdf:
                    width, height = original[0].rect.width, original[0].rect.height
                    offset = width if order=='original-left' else 0
                    self.assertIn('合成标签', renderer.normalize(pdf[0].get_text(clip=fitz.Rect(offset,0,offset+width,height))))

    def test_raster_output_copies_the_entire_expanded_translated_region(self):
        source = self.root/'synthetic.png'
        image = Image.new('RGB',(320,160),'white')
        draw = ImageDraw.Draw(image)
        draw.text((40,40),'A',fill='black',font_size=9)
        image.save(source)
        box = [40,40,57.30115274282599,46.03958759958484]
        block = {'id':'image-label','type':'figure','bbox':[0,0,320,160],'text':'SourceAB',
                 'layout_lines':[{'text':'SourceAB','bbox':box}]}
        pages = [{'number':1,'width':320,'height':160,'blocks':[block]}]
        after = copy.deepcopy(pages)
        after[0]['blocks'][0]['text'] = '合成标签'
        after[0]['blocks'][0]['layout_lines'][0]['text'] = '合成标签'
        document = {'schema':1,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                    'pages':pages,'translated_pages':after}
        result = image_renderer.export_images(source,document,self.root/'image-out')
        self.assertEqual(result['report']['placements'][0]['fit'],'ocr-vacant')
        with Image.open(result['translated']) as output:
            self.assertEqual(output.size,image.size)
            self.assertEqual(output.getpixel((250,120))[:3],(255,255,255))


if __name__ == '__main__':
    unittest.main()
