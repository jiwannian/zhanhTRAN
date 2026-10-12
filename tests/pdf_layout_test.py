"""PDF output must preserve source geometry, artwork and bilingual page pairing."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_pdf", ROOT / "src/local_pdf.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
parser_spec = importlib.util.spec_from_file_location("local_parser", ROOT / "src/local_parser.py")
parser = importlib.util.module_from_spec(parser_spec)
parser_spec.loader.exec_module(parser)


class PDFLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zhanhtran pdf ")
        self.root = Path(self.temp.name)
        self.source = self.root / "original.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 50), "Research results", fontsize=18)
            page.insert_textbox((30, 90, 180, 160), "First column text.\nResults are accurate.", fontsize=11)
            page.insert_textbox((220, 90, 370, 160), "Second column text.", fontsize=11)
            page.draw_rect((30, 220, 180, 300), color=(1, 0, 0), fill=(0, 0.6, 0))
            page.insert_text((220, 240), "E = mc2", fontsize=14)
            pdf.set_toc([[1, "Research results", 1]])
            pdf.save(self.source)
        self.baseline = self.source.read_bytes()
        with fitz.open(self.source) as pdf:
            blocks = [{"id": f"b{i}", "type": "paragraph", "text": "\n".join(
                "".join(s["text"] for s in l["spans"]) for l in b["lines"]), "bbox": list(b["bbox"])}
                for i, b in enumerate(pdf[0].get_text("dict")["blocks"]) if b["type"] == 0]
        translated = copy.deepcopy(blocks)
        for block in translated[:-1]:
            block["text"] = "\u7814\u7a76\u7ed3\u679c\u51c6\u786e\u3002"
        blocks[-1]["type"] = translated[-1]["type"] = "formula"
        self.document = {"schema": 1, "source_sha256": hashlib.sha256(self.baseline).hexdigest(),
            "pages": [{"number": 1, "width": 400, "height": 500, "blocks": blocks}],
            "translated_pages": [{"number": 1, "width": 400, "height": 500, "blocks": translated}]}

    def tearDown(self):
        self.temp.cleanup()

    def render(self, document=None):
        return renderer.export_pdfs(self.source, document or self.document, self.root / "output")

    def test_native_page_geometry_artwork_formula_and_selectable_chinese(self):
        result = self.render()
        self.assertEqual(self.source.read_bytes(), self.baseline)
        with fitz.open(result["translated"]) as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertEqual(tuple(pdf[0].rect), (0, 0, 400, 500))
            self.assertIn("\u7814\u7a76", pdf[0].get_text())
            self.assertNotIn("First column", pdf[0].get_text())
            self.assertIn("E = mc2", pdf[0].get_text())
            self.assertTrue(any(d.get("fill") == (0, 0.6000000238418579, 0) for d in pdf[0].get_drawings()))
            self.assertEqual(pdf.get_toc()[0][2], 1)
        self.assertEqual(result["report"]["unplaced"], [])

    def test_bilingual_side_by_side_unchanged_source_and_translation(self):
        result = self.render()
        with fitz.open(self.source) as original, fitz.open(result["bilingual"]) as bilingual:
            self.assertEqual(len(bilingual), 1)
            left = fitz.Rect(0, 0, 400, 500)
            self.assertEqual(bilingual[0].get_pixmap(clip=left).samples, original[0].get_pixmap().samples)
            self.assertIn("Research results", bilingual[0].get_text(clip=left))
            self.assertIn("\u7814\u7a76", bilingual[0].get_text(clip=fitz.Rect(400, 0, 800, 500)))
            self.assertEqual(tuple(bilingual[0].rect), (0, 0, 800, 500))

    def test_content_stream_is_cleaned_before_redaction(self):
        with fitz.open(self.source) as original, fitz.open(self.source) as pdf:
            page = pdf[0]
            report = {"placements": [], "unplaced": []}
            units = [renderer.unit(original[0], self.document["pages"][0]["blocks"][0], "\u7814\u7a76")]
            with patch.object(page, "clean_contents", wraps=page.clean_contents) as clean:
                renderer.place_page(page, original[0], units, 1, report)
            clean.assert_called_once()
            self.assertNotIn("Research results", page.get_text())
            self.assertIn("Second column text", page.get_text())

    def test_coherent_paragraph_places_preserved_inline_math_at_readable_size(self):
        block = self.document["pages"][0]["blocks"][1]
        block["translation_text"] = "The result [[M0]] remains accurate."
        block["inline_math"] = [{"id": "M0", "bbox": [40, 92, 55, 104]}]
        translated = self.document["translated_pages"][0]["blocks"][1]
        translated["translation_text"] = "\u7ed3\u679c [[M0]] \u4fdd\u6301\u51c6\u786e\u3002"
        result = self.render()
        with fitz.open(result["translated"]) as pdf:
            self.assertIn("\u4fdd\u6301\u51c6\u786e", pdf[0].get_text())
            self.assertTrue(pdf[0].get_images())
        self.assertEqual(len([x for x in result["report"]["placements"] if x["ids"] == [block["id"]]]), 1)

    def test_tall_inline_fraction_does_not_overlap_the_following_line(self):
        with fitz.open(self.source) as original, fitz.open(self.source) as pdf:
            page = pdf[0]
            item = {"ids": ["fraction"], "rect": fitz.Rect(30, 320, 180, 440),
                    "text": "\u4e2d\u6587" * 8 + " [[M0]] " + "\u4e2d\u6587" * 20,
                    "size": 11, "bold": False, "image": False,
                    "inline_math": [{"id": "M0", "bbox": [220, 225, 260, 245]}]}
            renderer.place_page(page, original[0], [item], 1, {"placements": [], "unplaced": []})
            blocks = page.get_text("dict")["blocks"]
            images = [fitz.Rect(b["bbox"]) for b in blocks if b["type"] == 1]
            spans = [fitz.Rect(s["bbox"]) for b in blocks if b["type"] == 0
                     for line in b["lines"] for s in line["spans"] if "\u4e2d" in s["text"]]
            self.assertEqual(len(images), 1)
            self.assertGreater(len(spans), 1)
            self.assertFalse(any((image & span).get_area() > 0.01 for image in images for span in spans))

    def test_incomplete_or_wrong_source_is_rejected_without_success_pdf(self):
        for change in [lambda d: d.update(source_sha256="0" * 64),
                       lambda d: d["translated_pages"][0]["blocks"].pop(),
                       lambda d: d["translated_pages"][0]["blocks"][0].update(text="")]:
            document = copy.deepcopy(self.document)
            change(document)
            with self.assertRaises(ValueError):
                self.render(document)
            self.assertFalse(list((self.root / "output").glob("*.pdf")))

    def test_overlapping_unreconstructed_native_translations_are_rejected(self):
        doc = copy.deepcopy(self.document)
        a = doc["pages"][0]["blocks"][1]
        b = {**a, "id": "fragment", "bbox": [a["bbox"][0]+30, a["bbox"][1], a["bbox"][2], a["bbox"][3]]}
        doc["pages"][0]["blocks"].append(b)
        doc["translated_pages"][0]["blocks"].append({**b, "text": "\u7b2c\u4e8c\u4e2a\u7247\u6bb5"})
        with self.assertRaisesRegex(ValueError, "PDF_OVERLAPPING_TRANSLATIONS"):
            self.render(doc)
        self.assertFalse(list((self.root / "output").glob("*.pdf")))

    def test_expanded_fragment_group_exports_layout_preserving_pdf_pair(self):
        source = self.root / "fragments.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=300, height=200)
            for point, text in [((3, 8), "Alpha"), ((65, 85), "Beta"), ((5, 45), "Gamma")]:
                page.insert_text(point, text, fontsize=6)
            page.draw_rect((180, 100, 250, 150), fill=(0, .6, 0))
            pdf.save(source)
        pristine = source.read_bytes()
        blocks = [{"id": f"b{i}", "type": "paragraph", "text": text, "bbox": box}
                  for i, (text, box) in enumerate([
                      ("Alpha", [0, 0, 100, 10]), ("Beta", [60, 60, 100, 100]),
                      ("Gamma", [0, 5, 70, 65])])]
        with fitz.open(source) as pdf:
            merged = parser.coalesce_native_blocks(pdf[0], blocks)
        self.assertEqual(len(merged), 1)
        for text in ["Alpha", "Beta", "Gamma"]:
            self.assertEqual(merged[0]["text"].count(text), 1)
        page = {"number": 1, "width": 300, "height": 200, "blocks": merged}
        translated = {**page, "blocks": [{**b, "text": "\u5408\u5e76\u540e\u7684\u5b8c\u6574\u8bd1\u6587\u3002"} for b in merged]}
        document = {"schema": 1, "source_sha256": hashlib.sha256(pristine).hexdigest(),
                    "pages": [page], "translated_pages": [translated]}
        result = renderer.export_pdfs(source, document, self.root / "fragments-output")
        self.assertEqual(source.read_bytes(), pristine)
        self.assertEqual(len(result["report"]["placements"]), 1)
        with fitz.open(source) as original, fitz.open(result["translated"]) as target, fitz.open(result["bilingual"]) as bilingual:
            self.assertEqual(len(target), 1)
            self.assertEqual(len(bilingual), 1)
            self.assertEqual(target[0].rect, original[0].rect)
            self.assertIn("\u5b8c\u6574\u8bd1\u6587", target[0].get_text())
            self.assertNotIn("Alpha", target[0].get_text())
            self.assertEqual(original[0].get_pixmap().samples, bilingual[0].get_pixmap(clip=fitz.Rect(0, 0, 300, 200)).samples)
            self.assertEqual(target[0].get_pixmap().samples, bilingual[0].get_pixmap(clip=fitz.Rect(300, 0, 600, 200)).samples)
            clip = fitz.Rect(170, 90, 260, 160)
            self.assertEqual(original[0].get_pixmap(clip=clip).samples, target[0].get_pixmap(clip=clip).samples)

    def test_tiny_adjacent_ocr_labels_fit_without_being_concatenated(self):
        before = {"id": "cover", "type": "figure", "bbox": [220, 320, 280, 400], "text": "No.9\nSeptember 2026",
                  "layout_lines": [{"text": "No.9", "bbox": [245, 330, 251, 332.2]},
                                   {"text": "September 2026", "bbox": [239, 332, 251, 334]}]}
        after = copy.deepcopy(before)
        after["text"] = "\u7b2c9\u671f\n2026\u5e749\u6708"
        after["layout_lines"][0]["text"] = "\u7b2c9\u671f"
        after["layout_lines"][1]["text"] = "2026\u5e749\u6708"
        self.document["pages"][0]["blocks"].append(before)
        self.document["translated_pages"][0]["blocks"].append(after)
        result = self.render()
        placements = [p for p in result["report"]["placements"] if p["ids"][0].startswith("cover")]
        self.assertEqual(len(placements), 2)
        with fitz.open(result["translated"]) as pdf:
            self.assertIn("\u7b2c9\u671f", pdf[0].get_text())
            self.assertIn("2026\u5e749\u6708", pdf[0].get_text())

    def test_narrow_native_prose_run_fits_without_touching_adjacent_formula(self):
        self.source = self.root / "algorithm.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 50), "If", fontsize=8)
            page.insert_text((40, 50), "E = mc2", fontsize=8)
            pdf.save(self.source)
        with fitz.open(self.source) as pdf:
            rect = list(pdf[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"])
        block = {"id": "algorithm", "type": "formula", "text": "If E = mc2", "bbox": [30, 40, 100, 55],
                 "layout_runs": [{"text": "If", "bbox": rect}]}
        translated = copy.deepcopy(block)
        translated["layout_runs"][0]["text"] = "\u5982\u679c"
        self.document = {"schema": 1, "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "pages": [{"number": 1, "width": 400, "height": 500, "blocks": [block]}],
            "translated_pages": [{"number": 1, "width": 400, "height": 500, "blocks": [translated]}]}
        result = self.render()
        self.assertLess(result["report"]["placements"][0]["font_size"], 5)
        with fitz.open(self.source) as original, fitz.open(result["translated"]) as pdf:
            self.assertIn("\u5982\u679c", pdf[0].get_text())
            self.assertIn("E = mc2", pdf[0].get_text())
            clip = fitz.Rect(39, 40, 100, 55)
            self.assertEqual(original[0].get_pixmap(clip=clip).samples, pdf[0].get_pixmap(clip=clip).samples)

    def test_long_native_run_still_rejects_unreadable_overflow(self):
        before = self.document["pages"][0]["blocks"][0]
        after = self.document["translated_pages"][0]["blocks"][0]
        before["layout_runs"] = [{"text": "Research results", "bbox": [30, 35, 36, 45]}]
        after["layout_runs"] = [{"text": "\u5b57" * 1000, "bbox": [30, 35, 36, 45]}]
        with self.assertRaisesRegex(ValueError, "PDF_TEXT_DOES_NOT_FIT"):
            self.render()
        self.assertFalse(list((self.root / "output").glob("*.pdf")))

    def test_expanded_short_native_run_stays_inside_its_source_bounds(self):
        self.source = self.root / "short-run.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 50), "process", fontsize=8)
            page.insert_text((64, 50), "E = mc2", fontsize=8)
            pdf.save(self.source)
        with fitz.open(self.source) as pdf:
            rect = list(pdf[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"])
        block = {"id": "prose", "type": "formula", "text": "process E = mc2", "bbox": [30, 40, 110, 55],
                 "layout_runs": [{"text": "process", "bbox": rect}]}
        translated = copy.deepcopy(block)
        text = "\u8fc7\u7a0b\u4e2d\u9ad8\u5ea6\u7684\u53d8\u5316\u7387"
        translated["layout_runs"][0]["text"] = text
        self.document = {"schema": 1, "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "pages": [{"number": 1, "width": 400, "height": 500, "blocks": [block]}],
            "translated_pages": [{"number": 1, "width": 400, "height": 500, "blocks": [translated]}]}
        result = self.render()
        self.assertLess(result["report"]["placements"][0]["font_size"], 5)
        with fitz.open(self.source) as original, fitz.open(result["translated"]) as pdf:
            self.assertIn(text, pdf[0].get_text())
            clip = fitz.Rect(63, 40, 110, 55)
            self.assertEqual(original[0].get_pixmap(clip=clip).samples, pdf[0].get_pixmap(clip=clip).samples)

    def test_bad_geometry_and_output_overwrite_are_rejected(self):
        doc = copy.deepcopy(self.document)
        doc["pages"][0]["blocks"][0]["bbox"][0] = float("nan")
        with self.assertRaises(ValueError):
            self.render(doc)
        with self.assertRaises(ValueError):
            renderer.export_pdfs(self.source, self.document, self.source)

    def test_existing_deliverables_are_never_deleted(self):
        result = self.render()
        original = Path(result["translated"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "PDF_OUTPUT_EXISTS"):
            self.render()
        self.assertEqual(Path(result["translated"]).read_bytes(), original)

    def test_positioned_image_text_and_scanned_table_cells(self):
        source = self.document["pages"][0]
        translated = self.document["translated_pages"][0]
        before = {"id": "scan", "type": "figure", "bbox": [30, 220, 180, 300], "text": "Label",
                  "layout_lines": [{"text": "Label", "bbox": [40, 240, 130, 265]}]}
        after = {**before, "text": "\u56fe\u4e2d\u6587\u5b57", "layout_lines": [{"text": "\u56fe\u4e2d\u6587\u5b57", "bbox": [40, 240, 130, 265]}]}
        source["blocks"].append(before)
        translated["blocks"].append(after)
        table = {"id": "table", "type": "table", "bbox": [220, 320, 370, 380], "rows": [["Label", "95"]],
                 "extraction": "rapidocr", "cells": [[[220, 320, 300, 380], [300, 320, 370, 380]]]}
        source["blocks"].append(table)
        translated["blocks"].append({**table, "rows": [["\u6807\u7b7e", "95"]]})
        result = self.render()
        with fitz.open(result["translated"]) as pdf:
            self.assertIn("\u56fe\u4e2d\u6587\u5b57", pdf[0].get_text())
            self.assertIn("\u6807\u7b7e", pdf[0].get_text())
        self.assertTrue(any(p["image_text"] for p in result["report"]["placements"]))

    def test_equation_only_replaces_prose_label_not_formula(self):
        self.document["pages"][0]["blocks"][0]["layout_runs"] = [{"text": "Research results", "bbox": self.document["pages"][0]["blocks"][0]["bbox"]}]
        self.document["translated_pages"][0]["blocks"][0]["layout_runs"] = [{"text": "\u7814\u7a76\u7ed3\u679c", "bbox": self.document["pages"][0]["blocks"][0]["bbox"]}]
        result = self.render()
        self.assertTrue(any(p["ids"] == ["b0-run0"] for p in result["report"]["placements"]))
        with fitz.open(self.source) as original, fitz.open(result["translated"]) as pdf:
            clip = fitz.Rect(210, 220, 300, 250)
            self.assertEqual(original[0].get_pixmap(clip=clip).samples, pdf[0].get_pixmap(clip=clip).samples)

    def test_image_without_positioned_translation_fails_closed(self):
        self.document["pages"][0]["blocks"].append({"id": "image", "type": "figure", "text": "Label", "bbox": [30, 220, 180, 300]})
        self.document["translated_pages"][0]["blocks"].append({"id": "image", "type": "figure", "text": "\u6807\u7b7e", "bbox": [30, 220, 180, 300]})
        with self.assertRaisesRegex(ValueError, "PDF_OCR_TRANSLATION_INCOMPLETE"):
            self.render()
        self.assertFalse(list((self.root / "output").glob("*.pdf")))

    def test_unfittable_translation_does_not_publish_a_truncated_pdf(self):
        self.document["translated_pages"][0]["blocks"][0]["text"] = "\u5b57" * 30000
        with self.assertRaisesRegex(ValueError, "PDF_TEXT_DOES_NOT_FIT"):
            self.render()
        self.assertFalse(list((self.root / "output").glob("*.pdf")))

    def test_failure_report_records_safe_error_code_and_page(self):
        self.document["pages"][0]["blocks"][1]["bbox"] = list(self.document["pages"][0]["blocks"][0]["bbox"])
        with self.assertRaisesRegex(ValueError, "PDF_OVERLAPPING_TRANSLATIONS"):
            self.render()
        failure = json.loads((self.root / "output/pdf-layout-failure.json").read_text("utf-8"))
        self.assertEqual(failure["error"], "PDF_OVERLAPPING_TRANSLATIONS")
        self.assertEqual(failure["failed_page"], 1)
        self.assertFalse(list((self.root / "output").glob("*.pdf")))
        self.assertEqual(self.source.read_bytes(), self.baseline)

    def test_failure_report_does_not_expose_unexpected_exception_text(self):
        with patch.object(renderer, "page_units", side_effect=RuntimeError("private document text or key")):
            with self.assertRaises(RuntimeError):
                self.render()
        raw = (self.root / "output/pdf-layout-failure.json").read_text("utf-8")
        self.assertEqual(json.loads(raw)["error"], "RuntimeError")
        self.assertNotIn("private document", raw)

    def test_placement_cannot_erase_a_protected_formula(self):
        self.document["pages"][0]["blocks"][1]["bbox"] = [30, 85, 300, 250]
        with self.assertRaisesRegex(ValueError, "PDF_PROTECTED_FORMULA_OVERLAP"):
            self.render()

    def test_hidden_ocr_runs_cover_underlying_image_text(self):
        old = {"id": "scan-table", "type": "table", "bbox": [30, 220, 180, 300], "rows": [["Label"]],
               "layout_runs": [{"text": "Label", "bbox": [40, 240, 130, 265], "image_text": True}]}
        new = copy.deepcopy(old)
        new["layout_runs"][0]["text"] = "\u6807\u7b7e"
        self.document["pages"][0]["blocks"].append(old)
        self.document["translated_pages"][0]["blocks"].append(new)
        result = self.render()
        self.assertTrue(next(p for p in result["report"]["placements"] if p["ids"] == ["scan-table-run0"])["image_text"])

    def test_image_labels_keep_the_source_background_color(self):
        before = {"id": "colored-label", "type": "figure", "bbox": [30, 220, 180, 300], "text": "Label",
                  "layout_lines": [{"text": "Label", "bbox": [40, 240, 130, 265]}]}
        after = copy.deepcopy(before)
        after["layout_lines"][0]["text"] = "\u6807\u7b7e"
        self.document["pages"][0]["blocks"].append(before)
        self.document["translated_pages"][0]["blocks"].append(after)
        result = self.render()
        with fitz.open(self.source) as original, fitz.open(result["translated"]) as pdf:
            clip = fitz.Rect(120, 250, 127, 258)
            self.assertEqual(original[0].get_pixmap(clip=clip).samples, pdf[0].get_pixmap(clip=clip).samples)

    def test_rotated_source_uses_unrotated_text_coordinates(self):
        with fitz.open(self.source) as pdf:
            pdf[0].set_rotation(90)
            pdf.save(self.root / "rotated.pdf")
        self.source = self.root / "rotated.pdf"
        self.document["source_sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.render()
        with fitz.open(result["translated"]) as pdf:
            self.assertEqual(pdf[0].rotation, 90)
            self.assertEqual(tuple(pdf[0].rect), (0, 0, 500, 400))

    def test_page_dimensions_and_duplicate_ids_are_checked(self):
        for change in [lambda d: d["pages"][0].update(width=401),
                       lambda d: d["pages"][0]["blocks"][1].update(id="b0"),
                       lambda d: d.update(translated_pages=[]),
                       lambda d: d["translated_pages"][0]["blocks"][0].update(id="unknown"),
                       lambda d: d["pages"][0]["blocks"][0].update(bbox=[-1, 0, 2, 3])]:
            document = copy.deepcopy(self.document)
            change(document)
            with self.assertRaises(ValueError):
                self.render(document)

    def test_empty_and_unchanged_image_tables_keep_original_artwork(self):
        table = {"id": "graph", "type": "table", "bbox": [30, 220, 180, 300], "rows": [["95"]]}
        self.document["pages"][0]["blocks"].append(table)
        self.document["translated_pages"][0]["blocks"].append(copy.deepcopy(table))
        result = self.render()
        self.assertIn("graph", result["report"]["retained_image_tables"])
        with self.assertRaisesRegex(ValueError, "PDF_TRANSLATION_INCOMPLETE"):
            self.document["translated_pages"][0]["blocks"][-1]["rows"] = []
            self.render()


if __name__ == "__main__":
    unittest.main()
