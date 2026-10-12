"""Offline PDF/image layout, OCR and table extraction. No network or credentials."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import re
import unicodedata

import numpy as np
import pymupdf as fitz
from PIL import Image

Image.MAX_IMAGE_PIXELS = 25_000_000
_OCR = None


def native_inline_math(page, box, kind):
    """Translate continuous prose with opaque placeholders for original math artwork."""
    if kind not in {"paragraph", "footnote"}:
        return {}
    raw = page.get_text("rawdict", clip=fitz.Rect(box) + (-.1, -.1, .1, .1))
    spans = [s for b in raw["blocks"] if b["type"] == 0 for line in b["lines"] for s in line["spans"]]
    visible = " ".join("".join(c["c"] for c in s["chars"]) for s in spans)
    if len(re.findall(r"[A-Za-z]{3,}", visible)) < 8:
        return {}
    sizes = [s["size"] for s in spans if len(s["chars"]) > 2]
    size = float(np.median(sizes)) if sizes else 9
    connectors = {"if", "of", "in", "on", "as", "by", "to", "is", "it", "we", "be", "an", "do", "or", "no"}
    rows, characters = [], []
    for span in spans:
        value = "".join(c["c"] for c in span["chars"]).strip()
        math = bool(re.search(r"math|sym|AdvPS", span["font"], re.I)) or span["size"] < size * .85 or (
            bool(span.get("flags", 0) & 2 or re.search(r"-i$|-bi$", span["font"], re.I))
            and bool(re.fullmatch(r"[A-Za-z]{1,2}", value)) and value.lower() not in connectors)
        characters.extend({**char, "math": math} for char in span["chars"])
    # Publishers also draw minus signs, accents and operators as vector paths.
    if hasattr(page, "get_drawings"):
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing["rect"]) + (-.2, -.2, .2, .2)
            if fitz.Rect(box).contains(rect) and rect.width < size * 3 and rect.height < size * 1.4:
                characters.append({"c": "", "bbox": list(rect), "math": True})
    changed = True
    while changed:
        changed = False
        for char in characters:
            if char["math"] or not re.fullmatch(r"[\d./+\-]", char["c"]):
                continue
            rect = fitz.Rect(char["bbox"]) + (-size * .35, -size * .2, size * .35, size * .2)
            if any(other["math"] and rect.intersects(fitz.Rect(other["bbox"])) for other in characters):
                char["math"], changed = True, True
    for char in characters:
        y = (char["bbox"][1] + char["bbox"][3]) / 2
        row = next((r for r in rows if abs(r["y"] - y) < size * .7), None)
        if row is None:
            row = {"y": y, "chars": []}
            rows.append(row)
        row["chars"].append(char)
    formulas, lines = [], []
    for row in sorted(rows, key=lambda r: r["y"]):
        text, previous, formula = "", None, None
        for char in sorted(row["chars"], key=lambda c: c["bbox"][0]):
            gap = char["bbox"][0] - previous["bbox"][2] if previous else 0
            if char["c"].isspace():
                if not text.endswith(" "):
                    text += " "
                previous = char
                continue
            if char["math"]:
                if formula and gap < size * .45 and (previous["math"] or previous["c"].isspace()):
                    formula["bbox"] = list(fitz.Rect(formula["bbox"]) | fitz.Rect(char["bbox"]))
                else:
                    formula = {"id": f"M{len(formulas)}", "bbox": list(char["bbox"])}
                    formulas.append(formula)
                    text += (" " if gap > size * .18 else "") + "[[" + formula["id"] + "]]"
            else:
                formula = None
                text += (" " if gap > size * .18 and not text.endswith(" ") else "") + char["c"]
            previous = char
        lines.append(text.strip())
    if not formulas:
        return {}
    text = "\n".join(lines).translate(str.maketrans({"\uFB00": "ff", "\uFB01": "fi", "\uFB02": "fl", "\uFB03": "ffi", "\uFB04": "ffl"}))
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", text)
    return {"translation_text": re.sub(r"\s+", " ", text), "inline_math": formulas}


def native_layout_runs(page, box, kind):
    """Keep equation glyphs / code indentation; translate only their prose labels."""
    native = [b for b in page.get_text("dict", clip=fitz.Rect(box) + (-.1, -.1, .1, .1))["blocks"] if b["type"] == 0]
    lines = [line for block in native for line in block["lines"]]
    text = "\n".join("".join(s["text"] for s in line["spans"]) for line in lines)
    if box[1] < 50 and box[2]-box[0] > page.rect.width*.7 and box[3]-box[1] < 18:
        return [{"text": "".join(s["text"] for s in line["spans"]), "bbox": list(line["bbox"])}
                for line in lines if re.search(r"[A-Za-z]{3}", "".join(s["text"] for s in line["spans"]))]
    sizes = [s["size"] for line in lines for s in line["spans"] if s["text"].strip()]
    if kind == "heading" and len(lines)>1 and sizes and min(sizes)<max(sizes)*.65:
        first = lines[0]
        rest = fitz.Rect(lines[1]["bbox"])
        for line in lines[2:]:
            rest |= fitz.Rect(line["bbox"])
        return [{"text": "".join(s["text"] for s in first["spans"]), "bbox": list(first["bbox"])},
                {"text": " ".join("".join(s["text"] for s in line["spans"]) for line in lines[1:]), "bbox": list(rest)}]
    algorithm = kind == "table"
    math_spans = [s for line in lines for s in line["spans"] if re.search(r"math|sym|AdvPS", s["font"], re.I)]
    connectors = {"if", "of", "in", "on", "as", "by", "to", "is", "it", "we", "be", "an", "do", "or", "no"}
    italic_variables = [s for line in lines for s in line["spans"] if re.search(r"-i$|-bi$", s["font"], re.I)
                        and re.fullmatch(r"[A-Za-z]{1,2}", s["text"].strip()) and s["text"].strip().lower() not in connectors]
    equation = (len(text)<160 and re.search(r"\bif\b", text) and re.search(r"\d|[()=]|\n", text)) or (
        math_spans or italic_variables)
    if not (algorithm or equation):
        return []
    if not algorithm and native_inline_math(page, box, kind):
        return []
    runs = []
    excluded = {"exp", "sin", "cos", "tan", "log", "min", "max", "clip", "wind", "windmax", "total", "step", "init", "scale", "updraft", "ascend", "explore", "efficiency", "climb"}
    raw = page.get_text("rawdict", clip=fitz.Rect(box) + (-.1, -.1, .1, .1))
    for line in [line for block in raw["blocks"] if block["type"] == 0 for line in block["lines"]]:
        spans = line["spans"]
        size = max((s["size"] for s in spans), default=9)
        current = None
        for span in spans:
            chars = span["chars"]
            value = "".join(char["c"] for char in chars)
            prose = span["size"]>size*.85 and not re.search(r"math|sym|AdvPS", span["font"], re.I)
            if re.search(r"-i$|-bi$", span["font"], re.I) and not re.search(r"[A-Za-z\uFB00-\uFB06]{3}", value) and value.strip().lower() not in connectors:
                prose = False
            if current and value.isspace():
                current["text"] += " "
                continue
            words = list(re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z\uFB00-\uFB06]{2,}(?![A-Za-z0-9_])", value)) if prose else []
            if not words and current:
                runs.append(current)
                current = None
            offset = 0
            for match in words:
                word = unicodedata.normalize("NFKC", match.group())
                gap = value[offset:match.start()]
                if word.lower() in excluded or word.isupper() or (gap.strip() and current):
                    if current:
                        runs.append(current)
                        current = None
                if word.lower() not in excluded and not word.isupper():
                    rect = fitz.Rect(chars[match.start()]["bbox"])
                    for char in chars[match.start()+1:match.end()]:
                        rect |= fitz.Rect(char["bbox"])
                    if current:
                        current["text"] += gap + word
                        current["bbox"] = list(fitz.Rect(current["bbox"]) | rect)
                    else:
                        current = {"text": word, "bbox": list(rect)}
                offset = match.end()
            if value[offset:].strip() and current:
                runs.append(current)
                current = None
        if current:
            runs.append(current)
    hidden = [fitz.Rect(span["bbox"]) for span in page.get_texttrace() if span.get("type") == 3] if hasattr(page, "get_texttrace") else []
    return [{**run, "text": run["text"].strip(), "image_text": any(r.intersects(fitz.Rect(run["bbox"])) for r in hidden)}
            for run in runs if run["text"].strip()]


def native_formula_only(page, box):
    if native_inline_math(page, box, "paragraph"):
        return False
    spans = [s for b in page.get_text("dict", clip=fitz.Rect(box))['blocks'] if b['type'] == 0
             for line in b['lines'] for s in line['spans'] if s['text'].strip()]
    text = " ".join(s['text'] for s in spans)
    font_math = any(re.search(r"math|sym|AdvPS|-i$|-bi$", s['font'], re.I) for s in spans)
    plain_equation = bool(re.search(r"[=\u222b\u2211\u221a]", text)) and not re.search(r"[A-Za-z]{3}", text)
    return len(text)<160 and (font_math or plain_equation) and not native_layout_runs(page, box, "table")


def markdown_table(rows):
    width = max((len(row) for row in rows), default=0)
    if not width:
        return ""
    def cell(value):
        return str(value or "").replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")
    lines = ["| " + " | ".join(cell(row[i]) if i < len(row) else "" for i in range(width)) + " |" for row in rows]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def reading_order(blocks):
    """Whitespace XY cut: separate full-width bands, then independent columns."""
    if len(blocks) < 2:
        return list(blocks)
    for axis in (1, 0):
        ordered = sorted(blocks, key=lambda b: b["bbox"][axis])
        edge = ordered[0]["bbox"][axis + 2]
        for index, block in enumerate(ordered[1:], 1):
            start = block["bbox"][axis]
            if start - edge > (6 if axis == 1 else 16):
                return reading_order(ordered[:index]) + reading_order(ordered[index:])
            edge = max(edge, block["bbox"][axis + 2])
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))


def inside(box, region):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def coalesce_native_blocks(page, blocks):
    """Repair PDF text fragments before asking a model to translate each region."""
    groups, other = [], []
    for block in blocks:
        if block.get("extraction", "pymupdf") != "pymupdf" or block["type"] not in {"paragraph", "heading", "footnote"}:
            other.append(block)
            continue
        rect = fitz.Rect(block["bbox"])
        members = [block]
        # Enlarging a region can intersect groups that did not match its original bounds.
        while True:
            matches = [g for g in groups if (g["rect"] & rect).get_area()
                       > min(g["rect"].get_area(), rect.get_area()) * .08]
            if not matches:
                break
            groups = [g for g in groups if g not in matches]
            for group in matches:
                members.extend(group["members"])
                rect |= group["rect"]
        groups.append({"rect": rect, "members": members})
    result = []
    for group in groups:
        block = group["members"][0]
        if len(group["members"]) == 1 and not block.get("source_fragments"):
            result.append(block)
            continue
        raw = page.get_text("rawdict", clip=group["rect"] + (-.1, -.1, .1, .1))
        lines = {}
        for native in raw["blocks"]:
            if native["type"] != 0:
                continue
            for line in native["lines"]:
                for span in line["spans"]:
                    for char in span["chars"]:
                        if not char["c"].isspace():
                            lines.setdefault(round(char["origin"][1], 1), []).append((char, span["size"]))
        parts = []
        for _, chars in sorted(lines.items()):
            row, last = "", None
            for char, size in sorted(chars, key=lambda pair: pair[0]["bbox"][0]):
                if last and char["bbox"][0] - last[2] > size * .18:
                    row += " "
                row += char["c"]
                last = char["bbox"]
            parts.append(row)
        text = "\n".join(parts).strip()
        text = text.translate(str.maketrans({"\uFB00": "ff", "\uFB01": "fi", "\uFB02": "fl", "\uFB03": "ffi", "\uFB04": "ffl"}))
        text = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", text)
        result.append({**block, "bbox": list(group["rect"]), "type": "paragraph", "text": text,
                       "source_fragments": sum(b.get("source_fragments", 1) for b in group["members"])})
    return reading_order(other + result)


def ocr_lines(image):
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    result, _ = _OCR(np.asarray(image.convert("RGB")))
    lines = []
    for polygon, text, confidence in result or []:
        points = np.asarray(polygon)
        lines.append({"text": text, "confidence": float(confidence),
                      "bbox": [float(points[:, 0].min()), float(points[:, 1].min()),
                               float(points[:, 0].max()), float(points[:, 1].max())]})
    return reading_order(lines)


def image_tables(image, warnings, lines=None):
    from img2table.document import Image as TableImage
    from img2table.ocr._types import OCRData, OCRInstance

    class LocalOCR(OCRInstance):
        def of(self, document):
            records = {}
            for page, pixels in enumerate(document.images):
                words = lines if lines is not None and len(document.images) == 1 else ocr_lines(Image.fromarray(pixels))
                records[page] = [{"id": f"word_{page}_{i}", "parent": f"line_{page}_{i}",
                                  "value": line["text"], "confidence": int(line["confidence"] * 100),
                                  **dict(zip(("x1", "y1", "x2", "y2"), map(int, line["bbox"])))}
                                 for i, line in enumerate(words)]
            return OCRData(records=records)

    try:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        tables = TableImage(buffer.getvalue()).extract_tables(ocr=LocalOCR(), min_confidence=30)
        return [{"type": "table", "bbox": [t.bbox.x1, t.bbox.y1, t.bbox.x2, t.bbox.y2],
                 "rows": [[str(c.value or "") for c in row] for row in t.content.values()],
                 "cells": [[[c.bbox.x1, c.bbox.y1, c.bbox.x2, c.bbox.y2] for c in row] for row in t.content.values()],
                 "extraction": "img2table+rapidocr"} for t in tables]
    except Exception as error:
        warnings.append("Table OCR unavailable: " + type(error).__name__)
        return []


def image_blocks(image, output, asset, bbox, warnings, native_lines=(), native_tables=()):
    if image.width * image.height > 25_000_000:
        raise ValueError("IMAGE_PIXEL_LIMIT")
    path = output / asset
    path.parent.mkdir(exist_ok=True)
    image.convert("RGB").save(path, format="PNG")
    lines = ocr_lines(image)
    tables = image_tables(image, warnings, lines) or ruled_tables(image, lines)
    sx, sy = (bbox[2] - bbox[0]) / image.width, (bbox[3] - bbox[1]) / image.height
    def scaled_box(box):
        return [bbox[0] + box[0] * sx, bbox[1] + box[1] * sy,
                bbox[0] + box[2] * sx, bbox[1] + box[3] * sy]
    def normalized(text):
        return "".join(c for c in text.casefold() if c.isalnum())
    def already_native(line):
        box = scaled_box(line["bbox"])
        text = normalized(line["text"])
        for native in native_lines:
            other = normalized(native["text"])
            overlap = max(box[0], native["bbox"][0]) < min(box[2], native["bbox"][2]) \
                and max(box[1], native["bbox"][1]) < min(box[3], native["bbox"][3])
            if text and other and overlap and (text in other or other in text):
                return True
        return False
    selected = [line for line in lines if not already_native(line)
                and not any(inside(line["bbox"], t["bbox"]) for t in tables)]
    text = "\n".join(line["text"] for line in selected)
    figure = {"type": "figure", "asset": asset, "text": text, "bbox": list(bbox),
              "extraction": "rapidocr", "ocr_lines": lines,
              "layout_lines": [{**line, "bbox": scaled_box(line["bbox"])} for line in lines if not already_native(line)]}
    scaled = [{**t, "bbox": scaled_box(t["bbox"]),
               "cells": [[scaled_box(cell) if cell else None for cell in row] for row in t.get("cells", [])]} for t in tables
              if not any(inside(scaled_box(t["bbox"]), region) for region in native_tables)]
    return [figure] + scaled


def ruled_tables(image, lines):
    """Use OpenCV line masks and PyMuPDF's cell topology for sparse ruled tables."""
    import cv2
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    segments = []
    for horizontal in (True, False):
        length = max(60, (image.width if horizontal else image.height) // 8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1) if horizontal else (1, length))
        detected = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(detected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if horizontal and w >= length:
                segments.append(((x, y + h / 2), (x + w, y + h / 2)))
            elif not horizontal and h >= length:
                segments.append(((x + w / 2, y), (x + w / 2, y + h)))
    if not 4 <= len(segments) <= 2000:
        return []
    horizontal = sorted({round(start[1], 1) for start, end in segments if abs(start[1] - end[1]) < 2})
    vertical = sorted({round(start[0], 1) for start, end in segments if abs(start[0] - end[0]) < 2})
    if len(horizontal) < 2 or len(vertical) < 2:
        return []
    rows, cells = [], []
    for top, bottom in zip(horizontal, horizontal[1:]):
        row, boxes = [], []
        for left, right in zip(vertical, vertical[1:]):
            words = [line["text"] for line in lines if left <= (line["bbox"][0] + line["bbox"][2]) / 2 <= right
                     and top <= (line["bbox"][1] + line["bbox"][3]) / 2 <= bottom]
            row.append(" ".join(words))
            boxes.append([left, top, right, bottom])
        rows.append(row)
        cells.append(boxes)
    if not any(any(cell for cell in row) for row in rows):
        return []
    return [{"type": "table", "bbox": [vertical[0], horizontal[0], vertical[-1], horizontal[-1]],
             "rows": rows, "cells": cells, "extraction": "opencv-grid+rapidocr"}]


def page_image(page):
    scale = min(2, (12_000_000 / max(1, page.rect.width * page.rect.height)) ** 0.5)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def pdf_page(page, output, warnings):
    native = page.get_text("dict")
    text_blocks = [b for b in native["blocks"] if b["type"] == 0]
    visible = "".join(s["text"] for b in text_blocks for line in b["lines"] for s in line["spans"]).strip()
    if len(visible) < 20:
        return image_blocks(page_image(page), output, f"figures/page-{page.number + 1}.png", page.rect, warnings), True
    blocks = []
    for table in page.find_tables().tables:
        rows = [[str(value or "") for value in row] for row in table.extract()]
        blocks.append({"type": "table", "bbox": list(table.bbox), "rows": rows, "extraction": "pymupdf",
                       "cells": [[list(cell) if cell else None for cell in row.cells] for row in table.rows]})
    native_tables = [b["bbox"] for b in blocks]
    native_lines = [{"bbox": line["bbox"], "text": "".join(s["text"] for s in line["spans"])}
                    for block in text_blocks for line in block["lines"]]
    # Image tables also mask an existing PDF OCR layer, avoiding duplicate cell paragraphs.
    for index, block in enumerate(b for b in native["blocks"] if b["type"] == 1):
        with Image.open(io.BytesIO(block["image"])) as image:
            blocks.extend(image_blocks(image, output, f"figures/p{page.number + 1}-{index + 1}.png", block["bbox"],
                                       warnings, native_lines, native_tables))
    regions = [b["bbox"] for b in blocks if b["type"] == "table"]
    sizes = [s["size"] for b in text_blocks for line in b["lines"] for s in line["spans"] if s["text"].strip()]
    body_size = float(np.median(sizes)) if sizes else 11
    for block in text_blocks:
        lines = [line for line in block["lines"] if not any(inside(line["bbox"], region) for region in regions)]
        text = "\n".join("".join(s["text"] for s in line["spans"]) for line in lines).strip()
        if not text:
            continue
        spans = [s for line in lines for s in line["spans"]]
        size = max(s["size"] for s in spans)
        kind = "heading" if size > body_size * 1.25 else "paragraph"
        if size < body_size * 0.85 and block["bbox"][1] > page.rect.height * 0.8:
            kind = "footnote"
        if all("math" in s["font"].lower() for s in spans if s["text"].strip()) \
                or (text.startswith("$$") and text.endswith("$$")) \
                or (text.startswith("\\[") and text.endswith("\\]")):
            kind = "formula"
        blocks.append({"type": kind, "text": text, "bbox": list(block["bbox"]),
                       "level": 1 if size > body_size * 1.7 else 2, "extraction": "pymupdf"})
    blocks = coalesce_native_blocks(page, blocks)
    for block in blocks:
        if block["type"] not in {"figure", "formula"}:
            inline = native_inline_math(page, block["bbox"], block["type"])
            runs = [] if inline else native_layout_runs(page, block["bbox"], block["type"])
            if inline:
                block.update(inline)
            elif runs:
                block["layout_runs"] = runs
            elif native_formula_only(page, block["bbox"]):
                block["type"] = "formula"
    return reading_order(blocks), False


def document_markdown(pages):
    sections = []
    for page in pages:
        sections.append("## Page " + str(page["number"]))
        for block in page["blocks"]:
            if block["type"] == "table":
                sections.append(markdown_table(block["rows"]))
            elif block["type"] == "figure":
                sections.append(f"![{block['id']}]({block['asset']})\n\n{block['text']}")
            else:
                prefix = "#" * block.get("level", 2) + " " if block["type"] == "heading" else ""
                sections.append(prefix + block["text"])
    return "\n\n".join(sections)


def parse_document(path, output_dir, max_pages=500):
    path = Path(path).resolve(strict=True)
    output = Path(output_dir).resolve()
    if not isinstance(max_pages, int) or not 1 <= max_pages <= 500:
        raise ValueError("PAGE_LIMIT_INVALID")
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("FILE_SIZE_LIMIT")
    if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        raise ValueError("UNSUPPORTED_FILE")
    if output == path or path in output.parents:
        raise ValueError("OUTPUT_INVALID")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("OUTPUT_NOT_EMPTY")
    pages, warnings = [], []
    if path.suffix.lower() == ".pdf":
        try:
            document = fitz.open(path)
        except Exception as error:
            raise ValueError("INVALID_PDF") from error
        with document:
            if document.needs_pass:
                raise ValueError("PDF_PASSWORD_REQUIRED")
            if not 1 <= len(document) <= max_pages:
                raise ValueError("PAGE_LIMIT_EXCEEDED")
            for page in document:
                rotation = page.rotation
                page.set_rotation(0)
                blocks, used_ocr = pdf_page(page, output, warnings)
                pages.append({"number": page.number + 1, "width": page.rect.width,
                              "height": page.rect.height, "rotation": rotation, "ocr": used_ocr, "blocks": blocks})
    else:
        with Image.open(path) as image:
            if getattr(image, "n_frames", 1) > max_pages:
                raise ValueError("PAGE_LIMIT_EXCEEDED")
            for n in range(getattr(image, "n_frames", 1)):
                image.seek(n)
                blocks = image_blocks(image, output, f"figures/image-{n + 1}.png", [0, 0, image.width, image.height], warnings)
                pages.append({"number": n + 1, "width": image.width, "height": image.height, "ocr": True, "blocks": blocks})
    assets = []
    for page in pages:
        for index, block in enumerate(page["blocks"]):
            block["id"] = f"p{page['number']}-b{index + 1}"
            if block["type"] == "figure":
                assets.append({"path": block["asset"], "media_type": "image/png"})
    result = {"schema": 1, "source_name": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "output_dir": str(output), "pages": pages, "assets": assets, "warnings": warnings,
              "markdown": document_markdown(pages)}
    (output / "document.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "source.md").write_text(result["markdown"], encoding="utf-8")
    return result


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--input", required=True)
    cli.add_argument("--output", required=True)
    args = cli.parse_args()
    try:
        result = parse_document(args.input, args.output)
        print(json.dumps({"ok": True, "pages": len(result["pages"]), "warnings": result["warnings"]}))
        return 0
    except Exception as error:
        # Do not expose document contents or local filenames on stderr.
        code = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({"ok": False, "error": code if len(code) < 80 else type(error).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
