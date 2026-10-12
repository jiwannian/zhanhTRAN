"""Synthetic, positioned image output; no provider calls or user documents."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_image', ROOT / 'src/local_image.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class ImageLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, ext='png', count=1):
        source = self.root / ('source.' + ext)
        frames = []
        for i in range(count):
            image = Image.new('RGB', (320, 160), (255, 255, 255-i*20))
            draw = ImageDraw.Draw(image)
            draw.text((20, 20), 'Hello', fill='black', font_size=22)
            draw.rectangle((220, 100, 290, 140), fill='red')
            frames.append(image)
        options = {'save_all': True, 'append_images': frames[1:]} if count > 1 else {}
        frames[0].save(source, **options)
        pages = [{'number': i+1, 'width': 320, 'height': 160, 'blocks': [{
            'id': f'p{i+1}-figure', 'type': 'figure', 'asset': f'figures/image-{i+1}.png',
            'text': 'Hello', 'bbox': [0, 0, 320, 160],
            'layout_lines': [{'text': 'Hello', 'bbox': [18, 18, 100, 48]}]}]} for i in range(count)]
        doc = {'schema': 1, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'pages': pages}
        doc['translated_pages'] = copy.deepcopy(pages)
        for page in doc['translated_pages']:
            page['blocks'][0]['text'] = '你好'
            page['blocks'][0]['layout_lines'][0]['text'] = '你好'
        return source, doc

    def test_formats_same_suffix_decodable_and_source_untouched(self):
        for ext, fmt in [('png', 'PNG'), ('jpg', 'JPEG'), ('jpeg', 'JPEG'), ('bmp', 'BMP'), ('webp', 'WEBP'), ('tif', 'TIFF'), ('tiff', 'TIFF')]:
            with self.subTest(ext=ext):
                source, doc = self.fixture(ext)
                before = source.read_bytes()
                result = renderer.export_images(source, doc, self.root / ext)
                for kind, size in [('translated', (320,160)), ('bilingual',(640,160))]:
                    path = Path(result[kind])
                    self.assertEqual(path.suffix, source.suffix)
                    with Image.open(path) as image:
                        self.assertEqual(image.format, fmt)
                        self.assertEqual(image.size, size)
                        image.load()
                        rgb = image.convert('RGB')
                        self.assertGreater(rgb.getpixel((250,120))[0], 220)
                        self.assertLess(rgb.getpixel((250,120))[1], 40)
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(len(result['report']['placements']), 1)
                self.assertGreater(result['report']['placements'][0]['font_size'], 15)

    def test_tiff_preserves_all_frames(self):
        source, doc = self.fixture('tiff', count=2)
        result = renderer.export_images(source, doc, self.root / 'out')
        for kind in ['translated','bilingual']:
            with Image.open(result[kind]) as image:
                self.assertEqual(image.n_frames, 2)
                image.seek(1)
                self.assertEqual(image.getpixel((0,0))[:3], (255,255,235))

    def test_transparent_nontext_pixels_and_bilingual_source_panel_are_exact(self):
        source, doc = self.fixture()
        with Image.open(source) as image:
            rgba = image.convert('RGBA')
        rgba.putpixel((250,120), (80,160,240,128))
        rgba.save(source)
        doc['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        result = renderer.export_images(source, doc, self.root/'alpha')
        with Image.open(result['translated']) as translated:
            self.assertEqual(translated.getpixel((250,120)), (80,160,240,128))
        with Image.open(result['bilingual']) as bilingual:
            self.assertEqual(bilingual.crop((0,0,320,160)).tobytes(), rgba.tobytes())

    def test_previous_outputs_never_overwritten(self):
        source, doc = self.fixture()
        result = renderer.export_images(source, doc, self.root / 'out')
        prior = Path(result['translated']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'IMAGE_OUTPUT_EXISTS'):
            renderer.export_images(source, doc, self.root / 'out')
        self.assertEqual(Path(result['translated']).read_bytes(), prior)

    def test_changed_source_and_bad_geometry_fail_without_files(self):
        source, doc = self.fixture()
        for field, value, error in [('source_sha256', 'bad', 'PDF_SOURCE_CHANGED'),
                                    ('translated_pages', [], 'PDF_TRANSLATION_INCOMPLETE')]:
            altered = {**doc, field: value}
            with self.assertRaisesRegex(ValueError, error):
                renderer.export_images(source, altered, self.root / 'invalid')
        altered = copy.deepcopy(doc)
        altered['pages'][0]['width'] = 340
        with self.assertRaisesRegex(ValueError, 'IMAGE_LAYOUT_INVALID'):
            renderer.export_images(source, altered, self.root / 'geometry')
        self.assertFalse(list(self.root.glob('**/*-translated.png')))

    def test_missing_line_translation_is_not_silently_delivered(self):
        source, doc = self.fixture()
        doc['translated_pages'][0]['blocks'][0].pop('layout_lines')
        with self.assertRaisesRegex(ValueError, 'IMAGE_TRANSLATION_INCOMPLETE'):
            renderer.export_images(source, doc, self.root / 'out')
        self.assertFalse(list((self.root/'out').glob('*-*.png')))

    def test_blank_line_or_invalid_line_geometry_is_rejected(self):
        source, doc = self.fixture()
        blank = copy.deepcopy(doc)
        blank['translated_pages'][0]['blocks'][0]['layout_lines'][0]['text'] = ''
        with self.assertRaisesRegex(ValueError, 'IMAGE_TRANSLATION_INCOMPLETE'):
            renderer.export_images(source, blank, self.root / 'blank')
        bad = copy.deepcopy(doc)
        bad['pages'][0]['blocks'][0]['layout_lines'][0]['bbox'] = [-1,0,99,48]
        with self.assertRaisesRegex(ValueError, 'PDF_LAYOUT_INVALID'):
            renderer.export_images(source, bad, self.root / 'bad')

    def test_extension_must_match_actual_format(self):
        source, doc = self.fixture()
        renamed = source.with_suffix('.jpg')
        source.rename(renamed)
        with self.assertRaisesRegex(ValueError, 'IMAGE_FORMAT_INVALID'):
            renderer.export_images(renamed, doc, self.root / 'out')

    def test_output_frame_count_and_pixel_limit(self):
        source, doc = self.fixture()
        doc['pages'] *= 2
        doc['translated_pages'] *= 2
        doc['pages'][1] = copy.deepcopy(doc['pages'][1])
        doc['translated_pages'][1] = copy.deepcopy(doc['translated_pages'][1])
        for pages in [doc['pages'], doc['translated_pages']]:
            pages[1]['number'] = 2
            pages[1]['blocks'][0]['id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'IMAGE_SOURCE_INVALID'):
            renderer.export_images(source, doc, self.root / 'out')

    def test_cli_success_and_safe_failure(self):
        source, doc = self.fixture()
        path = self.root/'translated.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        arguments = ['local_image.py','--input',str(source),'--document',str(path),'--output',str(self.root/'out')]
        output = io.StringIO()
        with patch('sys.argv', arguments), contextlib.redirect_stdout(output):
            self.assertEqual(renderer.main(), 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        output = io.StringIO()
        with patch('sys.argv', arguments), contextlib.redirect_stdout(output):
            self.assertEqual(renderer.main(), 2)
        self.assertEqual(json.loads(output.getvalue())['error'], 'IMAGE_OUTPUT_EXISTS')
        self.assertEqual(renderer.error_code(RuntimeError('private document text')), 'RuntimeError')

    def test_image_budget_rejects_large_frames_without_allocating_pixels(self):
        source, doc = self.fixture()
        with patch.object(renderer.Image, 'open') as opened:
            image = opened.return_value.__enter__.return_value
            image.format = 'PNG'
            image.n_frames = 1
            image.info = {}
            image.width, image.height = 10000, 10000
            with self.assertRaisesRegex(ValueError, 'IMAGE_PIXEL_LIMIT'):
                renderer.export_images(source, doc, self.root/'out')


if __name__ == '__main__':
    unittest.main()
