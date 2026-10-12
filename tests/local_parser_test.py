import importlib.util
import contextlib
import io
import json
import copy
import itertools
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_parser", ROOT / "src/local_parser.py")
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def fixtures(folder):
    folder = Path(folder)
    image = Image.new("RGB", (1200, 650), "white")
    draw = ImageDraw.Draw(image)
    fonts = [os.environ.get("ZHANHTRAN_TEST_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
             "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    font_path = next((p for p in fonts if p and Path(p).is_file()), None)
    if font_path is None:
        raise RuntimeError("Install a CJK test font or set ZHANHTRAN_TEST_FONT")
    font = ImageFont.truetype(font_path, 40)
    draw.text((50, 30), "Figure 1: Accuracy results", font=font, fill="black")
    draw.text((50, 100), "Model A: 95 percent", font=font, fill="black")
    draw.text((50, 160), "中文识别 测试结果", font=font, fill="black")
    for x in [40, 580, 1140]:
        draw.line((x, 260, x, 590), fill="black", width=3)
    for y in [260, 370, 480, 590]:
        draw.line((40, y, 1140, y), fill="black", width=3)
    for x, y, text in [(65, 280, "Model"), (610, 280, "Score"), (65, 390, "Alpha"),
                        (610, 390, "95"), (65, 500, "Beta"), (610, 500, "88")]:
        draw.text((x, y), text, font=font, fill="black")
    image.save(folder / "figure.png")
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((40, 45), "Research Paper", fontsize=22)
    page.insert_text((40, 85), "Introduction", fontsize=16)
    page.insert_textbox((40, 110, 280, 230), "Left column first.\nResults are reproducible.", fontsize=11)
    page.insert_textbox((320, 110, 560, 230), "Right column second.\nMethods and controls.", fontsize=11)
    for x in [40, 300, 560]:
        page.draw_line((x, 260), (x, 380))
    for y in [260, 300, 340, 380]:
        page.draw_line((40, y), (560, y))
    for x, y, text in [(50, 285, "Model"), (310, 285, "Score"), (50, 325, "Alpha"),
                        (310, 325, "95"), (50, 365, "Beta"), (310, 365, "88")]:
        page.insert_text((x, y), text, fontsize=12)
    page.insert_image((40, 420, 560, 705), filename=str(folder / "figure.png"))
    page.insert_text((40, 760), "1. Footnote: p < 0.05", fontsize=8)
    doc.save(folder / "digital.pdf")
    scan = fitz.open()
    page = scan.new_page(width=600, height=325)
    page.insert_image(page.rect, filename=str(folder / "figure.png"))
    scan.save(folder / "scan.pdf")
    searchable = fitz.open(folder / "scan.pdf")
    page = searchable[0]
    for x, y, text in [(25, 38, "Figure 1: Accuracy results"), (25, 73, "Model A: 95 percent"),
                       (33, 163, "Model"), (305, 163, "Score"), (33, 218, "Alpha"),
                       (305, 218, "95"), (33, 273, "Beta"), (305, 273, "88")]:
        page.insert_text((x, y), text, fontsize=19, render_mode=3)
    searchable.save(folder / "searchable.pdf")
    encrypted = fitz.open(folder / "digital.pdf")
    encrypted.save(folder / "encrypted.pdf", encryption=fitz.PDF_ENCRYPT_AES_256,
                   owner_pw="owner", user_pw="secret")
    (folder / "broken.pdf").write_bytes(b"not a document")


class LocalParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        fixtures(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.output = self.folder / self._testMethodName
        self.output.mkdir()

    def parse(self, name):
        return parser.parse_document(self.folder / name, self.output / (name + "-out"))

    def test_digital_text_layout_tables_images_and_footnotes(self):
        result = self.parse("digital.pdf")
        text = result["markdown"]
        self.assertIn("Research Paper", text)
        self.assertLess(text.index("Left column"), text.index("Right column"))
        self.assertIn("Footnote", text)
        tables = [b for p in result["pages"] for b in p["blocks"] if b["type"] == "table"]
        self.assertTrue(any("Alpha" in str(t["rows"]) for t in tables))
        self.assertTrue(result["assets"])
        self.assertIn("Accuracy", text)
        self.assertIn("中文", text)
        self.assertTrue((Path(result["output_dir"]) / "document.json").exists())

    def test_expanded_native_regions_merge_until_stable_in_every_input_order(self):
        blocks = [{"id": f"b{i}", "type": "paragraph", "text": text, "bbox": box}
                  for i, (text, box) in enumerate([
                      ("Alpha", [0, 0, 100, 10]), ("Beta", [60, 60, 100, 100]),
                      ("Gamma", [0, 5, 70, 65])])]
        baseline = copy.deepcopy(blocks)
        with fitz.open() as pdf:
            page = pdf.new_page(width=300, height=200)
            for point, text in [((3, 8), "Alpha"), ((65, 85), "Beta"), ((5, 45), "Gamma")]:
                page.insert_text(point, text, fontsize=6)
            for ordered in itertools.permutations(blocks):
                with self.subTest(order=[b["id"] for b in ordered]):
                    merged = parser.coalesce_native_blocks(page, ordered)
                    self.assertEqual(len(merged), 1)
                    self.assertEqual(merged[0]["bbox"], [0, 0, 100, 100])
                    self.assertEqual(merged[0]["source_fragments"], 3)
                    for text in ["Alpha", "Beta", "Gamma"]:
                        self.assertEqual(merged[0]["text"].count(text), 1)
        self.assertEqual(blocks, baseline)

    def test_coalescing_preserves_separate_columns_and_non_native_blocks(self):
        blocks = [{"id": "left", "type": "paragraph", "text": "Left", "bbox": [0, 0, 90, 30]},
                  {"id": "right", "type": "paragraph", "text": "Right", "bbox": [110, 0, 200, 30]},
                  {"id": "ocr", "type": "paragraph", "extraction": "ocr", "text": "OCR", "bbox": [0, 0, 90, 30]},
                  {"id": "math", "type": "formula", "text": "x", "bbox": [0, 0, 90, 30]}]
        with fitz.open() as pdf:
            page = pdf.new_page()
            result = parser.coalesce_native_blocks(page, blocks)
        self.assertEqual(sorted(result, key=lambda b: b["id"]), sorted(blocks, key=lambda b: b["id"]))

    def test_scanned_pdf_ocr_and_ruled_table(self):
        result = self.parse("scan.pdf")
        self.assertIn("Accuracy", result["markdown"])
        self.assertIn("中文", result["markdown"])
        self.assertTrue(any(b["type"] == "table" for b in result["pages"][0]["blocks"]))
        table = next(b for b in result["pages"][0]["blocks"] if b["type"] == "table")
        self.assertEqual(table["rows"], [["Model", "Score"], ["Alpha", "95"], ["Beta", "88"]])
        self.assertTrue(result["pages"][0]["ocr"])

    def test_standalone_image(self):
        result = self.parse("figure.png")
        self.assertIn("percent", result["markdown"])
        self.assertTrue(result["assets"])

    def test_searchable_scan_does_not_duplicate_body_or_table_cells(self):
        result = self.parse("searchable.pdf")
        self.assertEqual(result["markdown"].count("Accuracy results"), 1)
        self.assertEqual(result["markdown"].count("Alpha"), 1)
        self.assertEqual(result["markdown"].count("Beta"), 1)
        self.assertTrue(result["assets"])

    def test_mixed_math_span_is_not_a_formula_block(self):
        block = {"type": 0, "bbox": (10, 10, 200, 40), "lines": [{"bbox": (10, 10, 200, 40),
                 "spans": [{"text": "Results: ", "size": 11, "font": "Arial"},
                           {"text": "x + y", "size": 11, "font": "MathFont"}]}]}
        class Page:
            number = 0
            rect = fitz.Rect(0, 0, 600, 800)
            def get_text(self, format, **_options):
                result = {"blocks": [{**block, "lines": [{**block["lines"][0], "spans": [
                    {"text": "The experimental results are ", "size": 11, "font": "Arial"},
                    block["lines"][0]["spans"][1]]}]}]}
                if format == "rawdict":
                    for line in result["blocks"][0]["lines"]:
                        for span in line["spans"]:
                            span["chars"] = [{"c": char, "bbox": [10+i*5, 10, 15+i*5, 22]} for i, char in enumerate(span["text"])]
                return result
            def find_tables(self):
                return type("Tables", (), {"tables": []})()
        blocks, _ = parser.pdf_page(Page(), self.output, [])
        self.assertEqual(blocks[0]["type"], "paragraph")

    def test_native_header_mixed_title_and_equation_layout_runs(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 35), "Paper header", fontsize=9)
            page.insert_text((370, 35), "1", fontsize=9)
            header = parser.native_layout_runs(page, [30, 24, 390, 39], "paragraph")
            self.assertEqual([r["text"] for r in header], ["Paper header"])
            page.insert_text((30, 65), "FULL LENGTH ARTICLE", fontsize=8)
            page.insert_text((30, 90), "Research strategy", fontsize=20)
            title = parser.native_layout_runs(page, [30, 50, 390, 105], "heading")
            self.assertEqual(len(title), 2)
            page.insert_text((30, 150), "x = 1 if searching", fontsize=9)
            equation = parser.native_layout_runs(page, [30, 130, 250, 160], "paragraph")
            self.assertEqual([r["text"] for r in equation], ["if searching"])
            self.assertGreater(equation[0]["bbox"][0], 30)
            for i, value in enumerate(["Input: state vector", "Initialize policy", "for each iteration", "Take action", "min log clip", "Return trained policy"]):
                page.insert_text((30, 210+i*15), value, fontsize=9)
            algorithm = parser.native_layout_runs(page, [25, 190, 300, 310], "table")
            self.assertTrue(any("Return trained policy" in r["text"] for r in algorithm))
            self.assertFalse(any("min log clip" in r["text"] for r in algorithm))

    def test_italic_headings_and_affiliations_are_prose_not_formulas(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 100), "2.1. Environment modeling", fontname="tiit", fontsize=9)
            page.insert_text((30, 150), "School of Engineering, Beihang University", fontname="tiit", fontsize=9)
            class Page:
                rect = page.rect
                def get_text(self, format, **options):
                    data = page.get_text(format, **options)
                    for b in data["blocks"]:
                        for line in b.get("lines", []):
                            for span in line["spans"]:
                                span["font"] = "AdvTimes-i"
                    return data
                def get_texttrace(self):
                    return page.get_texttrace()
            for box in [[29, 88, 180, 105], [29, 138, 220, 155]]:
                self.assertFalse(parser.native_formula_only(Page(), box))
                self.assertEqual(parser.native_layout_runs(Page(), box, "paragraph"), [])

    def test_long_prose_keeps_inline_italic_variable_glyphs(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 100), "Note that the reward remains continuous ", fontsize=9)
            page.insert_text((200, 115), "n", fontname="tiit", fontsize=9)
            page.insert_text((30, 130), "while the episode is active. " * 6, fontsize=9)
            class Page:
                rect = page.rect
                def get_text(self, format, **options):
                    data = page.get_text(format, **options)
                    for b in data["blocks"]:
                        for line in b.get("lines", []):
                            for span in line["spans"]:
                                if span["font"] == "Times-Italic":
                                    span["font"] = "AdvTimes-i"
                    return data
                def get_texttrace(self):
                    return page.get_texttrace()
            inline = parser.native_inline_math(Page(), [29, 85, 399, 135], "paragraph")
            self.assertIn("reward remains", inline["translation_text"])
            self.assertIn("[[M0]]", inline["translation_text"])
            self.assertEqual(len(inline["inline_math"]), 1)
            self.assertEqual(parser.native_layout_runs(Page(), [29, 85, 399, 135], "paragraph"), [])

    def test_inline_math_groups_subscripts_without_changing_source_symbols(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 100), "The velocity ", fontsize=10)
            page.insert_text((86, 100), "V", fontname="tiit", fontsize=10)
            page.insert_text((92, 103), "wind", fontsize=6)
            page.insert_text((106, 100), " remains constant throughout the flight region.", fontsize=10)
            value = parser.native_inline_math(page, [29, 88, 399, 109], "paragraph")
            self.assertEqual(len(value["inline_math"]), 1)
            self.assertIn("[[M0]] remains constant", value["translation_text"])
            self.assertNotIn("wind", value["translation_text"])
            self.assertLess(value["inline_math"][0]["bbox"][0], 90)
            self.assertGreater(value["inline_math"][0]["bbox"][2], 100)

    def test_inline_vector_minus_and_adjacent_digits_are_preserved_together(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 100), "The angle is constrained between", fontsize=10)
            page.draw_line((178, 97), (184, 97))
            page.insert_text((186, 100), "45 and 45 degrees during the flight.", fontsize=10)
            value = parser.native_inline_math(page, [29, 88, 399, 104], "paragraph")
            self.assertTrue(value)
            self.assertLessEqual(value["inline_math"][0]["bbox"][0], 178)
            self.assertGreater(value["inline_math"][0]["bbox"][2], 195)

    def test_overlapping_native_fragments_reconstruct_words_before_translation(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 80), "Improving the airb", fontsize=10)
            end = 30 + fitz.get_text_length("Improving the airb", fontsize=10)
            page.insert_text((end, 80), "orne endurance is critical.", fontsize=10)
            page.insert_text((30, 95), "Operational capabilities improve.", fontsize=10)
            blocks = [
                {"type": "paragraph", "bbox": [30, 68, 170, 99], "text": "Improving the airb\nOperational capabilities"},
                {"type": "paragraph", "bbox": [100, 68, 290, 99], "text": "orne endurance"},
                {"type": "paragraph", "bbox": [300, 120, 380, 160], "text": "Other column"},
            ]
            merged = parser.coalesce_native_blocks(page, blocks)
            self.assertEqual(len(merged), 2)
            self.assertIn("airborne endurance", merged[0]["text"])
            self.assertEqual(merged[0]["text"].count("Operational"), 1)
            self.assertEqual(merged[0]["source_fragments"], 2)
            self.assertEqual(merged[1], blocks[2])

    def test_merge_keeps_superscripts_and_plain_font_formula_is_protected(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 80), "The area is 10\u00b2 m\u00b2.", fontname="tiro", fontsize=10)
            blocks = [{"type": "paragraph", "text": "x", "bbox": [29, 68, 200, 85]},
                      {"type": "paragraph", "text": "y", "bbox": [50, 68, 200, 85]}]
            merged = parser.coalesce_native_blocks(page, blocks)
            self.assertIn("10\u00b2 m\u00b2", merged[0]["text"])
            page.insert_text((30, 140), "E = mc2", fontname="tiro", fontsize=11)
            self.assertTrue(parser.native_formula_only(page, [29, 125, 200, 145]))

    def test_table_ocr_does_not_spawn_shell_or_tesseract(self):
        __import__("img2table.document")
        warnings = []
        with Image.open(self.folder / "figure.png") as image, \
                patch("img2table.ocr.TesseractOCR", side_effect=AssertionError("external OCR")) as external, \
                patch("subprocess.Popen", side_effect=AssertionError("subprocess")):
            parser.image_tables(image, warnings)
        external.assert_not_called()
        self.assertEqual(warnings, [])

    def test_grid_fallback_and_table_engine_failure(self):
        lines = [{"text": text, "bbox": [x, y, x + 100, y + 50]} for x, y, text in [
            (65, 280, "Model"), (610, 280, "Score"), (65, 390, "Alpha"),
            (610, 390, "95"), (65, 500, "Beta"), (610, 500, "88")]]
        with Image.open(self.folder / "figure.png") as image:
            table = parser.ruled_tables(image, lines)[0]
            self.assertEqual(table["rows"], [["Model", "Score"], ["Alpha", "95"], ["Beta", "88"]])
            with patch("img2table.document.Image.extract_tables", side_effect=RuntimeError("failure")):
                warnings = []
                self.assertEqual(parser.image_tables(image, warnings), [])
                self.assertEqual(warnings, ["Table OCR unavailable: RuntimeError"])
        self.assertEqual(parser.ruled_tables(Image.new("RGB", (200, 200), "white"), []), [])
        self.assertEqual(parser.markdown_table([]), "")

    def test_cli_status_and_private_error(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["parser", "--input", str(self.folder / "figure.png"),
                                        "--output", str(self.output / "cli")]), contextlib.redirect_stdout(output):
            self.assertEqual(parser.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        for error, code in [(ValueError("INVALID_PDF"), "INVALID_PDF"), (RuntimeError("private document text"), "RuntimeError")]:
            output = io.StringIO()
            with patch.object(sys, "argv", ["parser", "--input", "private.pdf", "--output", "out"]), \
                    patch.object(parser, "parse_document", side_effect=error), contextlib.redirect_stdout(output):
                self.assertEqual(parser.main(), 2)
            self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error": code})

    def test_pixel_limit_rejects_before_decoding(self):
        huge = type("HugeImage", (), {"width": 5001, "height": 5000})()
        with self.assertRaisesRegex(ValueError, "IMAGE_PIXEL_LIMIT"):
            parser.image_blocks(huge, self.output, "huge.png", [0, 0, 1, 1], [])

    def test_invalid_encrypted_empty_and_limits(self):
        for name in ["broken.pdf", "encrypted.pdf", "missing.pdf"]:
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.parse(name)
        with self.assertRaises(ValueError):
            parser.parse_document(self.folder / "digital.pdf", self.folder / "limit", max_pages=0)
        self.assertEqual(parser.markdown_table([["a|b", None], ["c\nd", "1"]]).count("|"), 9)
        self.assertEqual(parser.reading_order([]), [])

    def test_existing_output_and_unsupported_file_are_rejected(self):
        self.parse("figure.png")
        with self.assertRaisesRegex(ValueError, "OUTPUT_NOT_EMPTY"):
            self.parse("figure.png")
        wrong = self.folder / "input.exe"
        wrong.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_FILE"):
            parser.parse_document(wrong, self.folder / "wrong-out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
