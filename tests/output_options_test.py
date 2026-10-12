import copy
from pathlib import Path
from pdf_layout_test import PDFLayoutTests, renderer
import image_layout_test


class OutputOptionsTests(PDFLayoutTests):
    def test_selective_pdf_outputs_and_reverse_order(self):
        for kinds in [('translated',), ('bilingual',), ('translated', 'bilingual')]:
            for order in ['original-left', 'translated-left']:
                folder = self.root / ('-'.join(kinds) + order)
                result = renderer.export_pdfs(self.source, self.document, folder, kinds, order)
                self.assertEqual(set(result) - {'report'}, set(kinds))
                self.assertEqual(len(list(folder.glob('*.pdf'))), len(kinds))
                if 'bilingual' in kinds:
                    import pymupdf as fitz
                    with fitz.open(result['bilingual']) as pdf:
                        self.assertEqual(len(pdf), 1)
                        self.assertEqual(tuple(pdf[0].rect), (0, 0, 800, 500))
                        left = pdf[0].get_text(clip=fitz.Rect(0, 0, 400, 500))
                        self.assertIn('Research' if order == 'original-left' else '\u7814\u7a76', left)
                self.assertEqual(self.source.read_bytes(), self.baseline)

    def test_rotated_side_by_side_dimensions_and_panels(self):
        import hashlib
        import pymupdf as fitz
        source = self.root / 'rotated.pdf'
        with fitz.open(self.source) as pdf:
            pdf[0].set_rotation(90)
            pdf.save(source)
        doc = copy.deepcopy(self.document)
        doc['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        result = renderer.export_pdfs(source, doc, self.root / 'rotation')
        with fitz.open(source) as original, fitz.open(result['bilingual']) as pdf:
            self.assertEqual(tuple(pdf[0].rect), (0, 0, 1000, 400))
            left = pdf[0].get_pixmap(clip=fitz.Rect(0, 0, 500, 400))
            self.assertEqual(left.samples, original[0].get_pixmap().samples)

    def test_empty_output_rejected(self):
        with self.assertRaisesRegex(ValueError, 'OUTPUT_OPTIONS_INVALID'):
            renderer.export_pdfs(self.source, self.document, self.root / 'invalid', [])


class ImageOutputOptionsTests(image_layout_test.ImageLayoutTests):
    def test_selective_and_reverse_image_outputs(self):
        from PIL import Image
        source, doc = self.fixture()
        before = source.read_bytes()
        for kinds in [('translated',), ('bilingual',), ('translated', 'bilingual')]:
            for order in ['original-left', 'translated-left']:
                folder = self.root / ('-'.join(kinds) + order)
                result = image_layout_test.renderer.export_images(source, doc, folder, kinds, order)
                self.assertEqual(set(result) - {'report'}, set(kinds))
                self.assertEqual(len(list(folder.glob('*.png'))), len(kinds))
                if 'bilingual' in kinds:
                    with Image.open(source) as original, Image.open(result['bilingual']) as pdf:
                        offset = 0 if order == 'original-left' else original.width
                        panel = pdf.crop((offset, 0, offset + original.width, original.height))
                        self.assertEqual(panel.tobytes(), original.convert('RGBA').tobytes())
                self.assertEqual(source.read_bytes(), before)
