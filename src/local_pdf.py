"""Offline, source-PDF-based translation placement using PyMuPDF's HTML layout."""
import argparse
import hashlib
import html
import json
import math
import re
import statistics
from pathlib import Path
import sys

import pymupdf as fitz

# CLI stdout is a single JSON response, not a dependency diagnostic channel.
fitz.no_recommend_layout()

def checked_rect(value, bounds):
    if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError("PDF_LAYOUT_INVALID")
    rect = fitz.Rect(value)
    if rect.is_empty or not bounds.contains(rect):
        raise ValueError("PDF_LAYOUT_INVALID")
    return rect


def validate_document(source, document):
    if document.get("schema") != 1 or document.get("source_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("PDF_SOURCE_CHANGED")
    before, after = document.get("pages"), document.get("translated_pages")
    if not isinstance(before, list) or not isinstance(after, list) or not before or len(before) != len(after):
        raise ValueError("PDF_TRANSLATION_INCOMPLETE")
    seen = set()
    for page, translated in zip(before, after):
        bounds = fitz.Rect(0, 0, page["width"], page["height"])
        if page["number"] != translated["number"] or len(page["blocks"]) != len(translated["blocks"]):
            raise ValueError("PDF_TRANSLATION_INCOMPLETE")
        for old, new in zip(page["blocks"], translated["blocks"]):
            if old["id"] in seen or (old["id"], old["type"]) != (new["id"], new["type"]):
                raise ValueError("PDF_TRANSLATION_INCOMPLETE")
            seen.add(old["id"])
            checked_rect(old["bbox"], bounds)
            if old["type"] == "table":
                if [len(row) for row in old["rows"]] != [len(row) for row in new["rows"]]:
                    raise ValueError("PDF_TRANSLATION_INCOMPLETE")
                pairs = [(a, b) for row, translated_row in zip(old["rows"], new["rows"]) for a, b in zip(row, translated_row)]
            else:
                pairs = [(old.get("text", ""), new.get("text", ""))]
            if any(a.strip() and not b.strip() for a, b in pairs):
                raise ValueError("PDF_TRANSLATION_INCOMPLETE")


def normalize(text):
    return re.sub(r"\s+", "", text).casefold()


def style_for(page, rect, block):
    spans = [s for b in page.get_text("dict", clip=rect)["blocks"] if b["type"] == 0
             for line in b["lines"] for s in line["spans"] if s["text"].strip()]
    sizes = [s["size"] for s in spans if len(s["text"].strip()) > 1]
    size = max(sizes, default=9) if block.get("type") == "heading" else sorted(sizes or [9])[len(sizes or [9]) // 2]
    bold = block.get("type") == "heading" or any("-b" in s["font"] or "bold" in s["font"].lower() for s in spans)
    return min(24, max(6, size)), bold


def unit(page, block, text, rect=None, image=False):
    rect = fitz.Rect(rect or block["bbox"])
    size, bold = style_for(page, rect, block)
    return {"ids": [block["id"]], "rect": rect, "text": text, "size": size, "bold": bold, "image": image,
            "expandable": block.get("type") == "paragraph"}


def page_units(page, before, after, report):
    units = []
    tables = page.find_tables().tables
    for block, translated in zip(before["blocks"], after["blocks"]):
        if block.get("inline_math"):
            text = translated.get("translation_text", "")
            markers = re.findall(r"\[\[(M\d+)\]\]", text)
            if sorted(markers) != sorted(m["id"] for m in block["inline_math"]):
                raise ValueError("PDF_TRANSLATION_INCOMPLETE")
            units.append({**unit(page, block, text), "inline_math": block["inline_math"]})
            continue
        if block.get("layout_runs"):
            runs = translated.get("layout_runs", [])
            if len(runs) != len(block["layout_runs"]):
                raise ValueError("PDF_TRANSLATION_INCOMPLETE")
            for i, (old, new) in enumerate(zip(block["layout_runs"], runs)):
                if old["text"].strip() and not new["text"].strip():
                    raise ValueError("PDF_TRANSLATION_INCOMPLETE")
                if normalize(old["text"]) != normalize(new["text"]):
                    units.append({**unit(page, {**block, "id": f"{block['id']}-run{i}"}, new["text"], old["bbox"],
                                         old.get("image_text", False)), "native_run": True, "source_text": old["text"]})
            continue
        if block["type"] == "formula":
            report["preserved_formulas"].append(block["id"])
            continue
        if block["type"] == "table":
            if block.get("extraction") != "pymupdf" and any(b["type"] == "figure" and b.get("layout_lines")
                    and fitz.Rect(b["bbox"]).contains(fitz.Rect(block["bbox"])) for b in before["blocks"]):
                report["positioned_image_tables"].append(block["id"])
                continue
            rect = fitz.Rect(block["bbox"])
            table = next((t for t in tables if (fitz.Rect(t.bbox) & rect).get_area() / max(1, rect.get_area()) > .9
                          and len(t.rows) == len(block["rows"])), None)
            cells = block.get("cells") or ([row.cells for row in table.rows] if table else None)
            if not cells:
                report["retained_image_tables"].append(block["id"])
                continue
            for ri, (row, new_row) in enumerate(zip(block["rows"], translated["rows"])):
                for ci, (old, new) in enumerate(zip(row, new_row)):
                    if normalize(old) != normalize(new) and cells[ri][ci]:
                        cell = fitz.Rect(cells[ri][ci]) + (1, 1, -1, -1)
                        if cell.is_empty:
                            raise ValueError("PDF_LAYOUT_INVALID")
                        units.append(unit(page, {**block, "id": f"{block['id']}-r{ri}-c{ci}"}, new, cell,
                                          block.get("extraction") != "pymupdf"))
            continue
        if block["type"] == "figure":
            # PDF extraction can expose fully occluded older artwork below a later image.
            match = re.search(r"-(\d+)\.png$", block.get("asset", ""))
            order = int(match.group(1)) if match else 0
            region = fitz.Rect(block["bbox"])
            occluded = any(b["type"] == "figure" and (m := re.search(r"-(\d+)\.png$", b.get("asset", "")))
                and int(m.group(1)) > order and (fitz.Rect(b["bbox"]) & region).get_area() > region.get_area()*.98
                for b in before["blocks"])
            if occluded:
                continue
            old_lines, new_lines = block.get("layout_lines", []), translated.get("layout_lines", [])
            if old_lines and len(old_lines) == len(new_lines):
                for i, (old, new) in enumerate(zip(old_lines, new_lines)):
                    if normalize(old["text"]) != normalize(new["text"]):
                        units.append(unit(page, {**block, "id": f"{block['id']}-ocr{i}"}, new["text"], old["bbox"], True))
            elif normalize(block.get("text", "")) != normalize(translated.get("text", "")):
                report["unplaced"].append(block["id"] + ":OCR_LINE_TRANSLATIONS_REQUIRED")
            continue
        if normalize(block.get("text", "")) == normalize(translated.get("text", "")):
            continue
        units.append(unit(page, block, translated["text"]))
    protected = [fitz.Rect(b["bbox"]) for b in before["blocks"] if b["type"] == "formula" and not b.get("layout_runs")]
    if any(r.intersects(item["rect"]) for r in protected for item in units):
        raise ValueError("PDF_PROTECTED_FORMULA_OVERLAP")
    return check_overlaps(units)


def check_overlaps(units):
    for i, item in enumerate(units):
        for other in units[:i]:
            overlap = (other["rect"] & item["rect"]).get_area()
            if not item["image"] and not other["image"] and overlap > min(
                    other["rect"].get_area(), item["rect"].get_area()) * .08:
                # Concatenating independently translated source fragments duplicates prose.
                raise ValueError("PDF_OVERLAPPING_TRANSLATIONS")
    return units


def background_for(page, rect):
    pix = page.get_pixmap(clip=rect, alpha=False, colorspace=fitz.csRGB)
    raw, width, height = pix.samples, pix.width, pix.height
    border = {y * width + x for y in range(height) for x in (0, width - 1)}
    border.update(y * width + x for y in (0, height - 1) for x in range(width))
    return tuple(statistics.median(raw[i * 3 + channel] for i in border) / 255 for channel in range(3))


def vacant_below(original, item, protected):
    rect = item["rect"]
    if not item.get("expandable") or item["image"] or item.get("native_run") or "inline_math" in item:
        return rect
    # Keep the anchor, column and readable font floor; borrow at most one box-height.
    bottom = min(original.rect.y1, rect.y1 + min(rect.height, 36))
    for obstacle in protected:
        if obstacle.x0 < rect.x1 and obstacle.x1 > rect.x0 and obstacle.y1 > rect.y1:
            bottom = min(bottom, max(rect.y1, obstacle.y0 - 1))
    return fitz.Rect(rect.x0, rect.y0, rect.x1, bottom)


def layout_obstacles(original, units, protected):
    # Include unparsed text, graphics, images and annotations, not only translated blocks.
    painted = [fitz.Rect(box) for kind, box in original.get_bboxlog() if not kind.endswith("text")]
    # Painted text logs have extra padding: it must not make a paragraph block itself.
    blocks = original.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
    text = [fitz.Rect(span["bbox"]) for block in blocks if block["type"] == 0
            for line in block["lines"] for span in line["spans"]]
    annotations = [annot.rect for annot in original.annots() or ()]
    widgets = [widget.rect for widget in original.widgets() or ()]
    return [*protected, *(item["rect"] for item in units), *text, *painted, *annotations, *widgets]


def place_page(page, original, units, page_number, report, protected=()):
    obstacles = layout_obstacles(original, units, protected) if units else []
    if units:
        # Normalize relative text-position operators before MuPDF rewrites the stream.
        page.clean_contents()
    for item in units:
        # Remove only text: original embedded images, vector graphs and table borders survive.
        page.add_redact_annot(item["rect"], fill=None, cross_out=False)
    if units:
        page.apply_redactions(images=0, graphics=0)
    for item in units:
        rect = item["rect"]
        color = "black"
        if item["image"] or item.get("inline_math"):
            background = background_for(original, rect)
            page.draw_rect(rect, color=None, fill=background, overlay=True)
            if sum(a * b for a, b in zip(background, (.2126, .7152, .0722))) < .5:
                color = "white"
        size = item["size"]
        source_length = len(normalize(item.get("source_text", "")))
        target_length = len(normalize(item["text"]))
        short_run = item.get("native_run", False) and (target_length <= 8 or
            0 < source_length <= 32 and target_length <= max(24, source_length * 3))
        if short_run:
            size = min(size, rect.width / max(1, len(normalize(item["text"]))) * .85, rect.height / 1.25)
        # MuPDF Story performs Unicode shaping, wrapping and bounded font-size adaptation.
        content = "<div>" + html.escape(re.sub(r"\s+", " ", item["text"]).strip()) + "</div>"
        archive = fitz.Archive()
        line_height = 1.12
        for formula in item.get("inline_math", []):
            crop = checked_rect(formula["bbox"], original.rect)
            # Story's fixed leading does not grow to fit tall inline fractions.
            line_height = max(line_height, crop.height / size + .5)
            pix = original.get_pixmap(matrix=fitz.Matrix(4, 4), clip=crop, alpha=True)
            name = formula["id"] + ".png"
            archive.add((pix.tobytes("png"), name))
            image = f'<img src="{name}" style="width:{crop.width / size}em;height:{crop.height / size}em;vertical-align:middle;">'
            content = content.replace("[[" + formula["id"] + "]]", image)
        css = f"body, div {{margin:0;padding:0;}} div {{font-family:serif;color:{color};font-size:{size}pt;line-height:{line_height};font-weight:{'bold' if item['bold'] else 'normal'};}}"
        minimum = min(.5, rect.height * .3, rect.width / max(1, len(item["text"])) * .5) if item["image"] or short_run else min(5, rect.height*.65)
        spare, scale = page.insert_htmlbox(rect, content, css=css, archive=archive, scale_low=min(1, minimum / size))
        fit = "source-box"
        if spare < 0:
            expanded = vacant_below(original, item, obstacles)
            if expanded.height > rect.height + .5:
                spare, scale = page.insert_htmlbox(expanded, content, css=css, archive=archive,
                                                    scale_low=min(1, minimum / size))
                if spare >= 0:
                    rect, fit = expanded, "vacant-below"
        if spare < 0:
            report["unplaced"].append({"page": page_number, "ids": item["ids"], "bbox": list(rect), "reason": "TEXT_DOES_NOT_FIT"})
            raise ValueError("PDF_TEXT_DOES_NOT_FIT")
        report["placements"].append({"page": page_number, "ids": item["ids"], "bbox": list(rect),
                                     "source_bbox": list(item["rect"]), "fit": fit,
                                     "font_size": size * scale, "scale": scale, "image_text": item["image"]})
        obstacles.append(rect)


def error_code(error):
    return str(error) if isinstance(error, ValueError) and re.fullmatch(r"PDF_[A-Z_]+", str(error)) else type(error).__name__


def selected_outputs(outputs, order):
    if not isinstance(outputs, (list, tuple)) or not outputs or len(set(outputs)) != len(outputs) or any(
            kind not in ('translated', 'bilingual') for kind in outputs) or order not in ('original-left', 'translated-left'):
        raise ValueError('OUTPUT_OPTIONS_INVALID')
    return tuple(outputs)


def side_by_side(original, translated, order):
    bilingual = fitz.open()
    for i in range(len(original)):
        width, height = original[i].rect.width, original[i].rect.height
        page = bilingual.new_page(width=width * 2, height=height)
        for side, source in enumerate([original, translated] if order == 'original-left' else [translated, original]):
            # show_pdf_page uses unrotated coordinates. Bake the source rotation into placement.
            rotation = source[i].rotation
            source[i].set_rotation(0)
            try:
                page.show_pdf_page(fitz.Rect(side * width, 0, (side + 1) * width, height), source, i, rotate=-rotation)
            finally:
                source[i].set_rotation(rotation)
    toc = original.get_toc()
    if toc:
        bilingual.set_toc(toc)
    return bilingual


def export_pdfs(source, document, output_dir, outputs=('translated', 'bilingual'), order='original-left'):
    outputs = selected_outputs(outputs, order)
    source = Path(source).resolve(strict=True)
    folder = Path(output_dir).resolve()
    if folder == source or source in folder.parents:
        raise ValueError("PDF_OUTPUT_INVALID")
    validate_document(source, document)
    folder.mkdir(parents=True, exist_ok=True)
    name = source.stem[:64].rstrip(" .")
    paths = {kind: folder / (name + '-' + kind + '.pdf') for kind in outputs}
    if source in paths.values() or any(p.exists() for p in paths.values()):
        raise ValueError("PDF_OUTPUT_EXISTS")
    temps = {key: path.with_suffix(".pdf.tmp") for key, path in paths.items()}
    report = {"source_sha256": document["source_sha256"], "mode": "source-layout/side-by-side", "order": order,
              "placements": [], "unplaced": [], "preserved_formulas": [], "retained_image_tables": [], "positioned_image_tables": []}
    current_page = None
    try:
        with fitz.open(source) as original, fitz.open(source) as translated:
            if original.needs_pass or len(original) != len(document["pages"]):
                raise ValueError("PDF_SOURCE_INVALID")
            for i, (before, after) in enumerate(zip(document["pages"], document["translated_pages"])):
                current_page = i + 1
                rotation = original[i].rotation
                original[i].set_rotation(0)
                translated[i].set_rotation(0)
                if abs(original[i].rect.width - before["width"]) > .1 or abs(original[i].rect.height - before["height"]) > .1:
                    raise ValueError("PDF_LAYOUT_INVALID")
                units = page_units(original[i], before, after, report)
                protected = [fitz.Rect(block["bbox"]) for block in before["blocks"]]
                place_page(translated[i], original[i], units, i + 1, report, protected)
                original[i].set_rotation(rotation)
                translated[i].set_rotation(rotation)
            current_page = None
            if report["unplaced"]:
                raise ValueError("PDF_OCR_TRANSLATION_INCOMPLETE")
            translated.subset_fonts()
            if 'translated' in outputs:
                translated.save(temps["translated"], garbage=4, deflate=True)
            if 'bilingual' in outputs:
                with side_by_side(original, translated, order) as bilingual:
                    bilingual.set_metadata({**original.metadata, "title": original.metadata.get("title", name) + " - bilingual"})
                    bilingual.save(temps["bilingual"], garbage=4, deflate=True)
        for key, path in paths.items():
            with fitz.open(temps[key]) as reopened:
                expected = len(document["pages"])
                if len(reopened) != expected:
                    raise ValueError("PDF_OUTPUT_INVALID")
            temps[key].replace(path)
        report["pages"] = len(document["pages"])
        report["files"] = {key: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "bytes": path.stat().st_size} for key, path in paths.items()}
        (folder / "pdf-layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**{key: str(path) for key, path in paths.items()}, "report": report}
    except Exception as error:
        for path in list(temps.values()) + list(paths.values()):
            path.unlink(missing_ok=True)
        failure = {**report, "error": error_code(error), "failed_page": current_page}
        (folder / "pdf-layout-failure.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--input", required=True)
    cli.add_argument("--document", required=True)
    cli.add_argument("--output", required=True)
    cli.add_argument('--outputs', default='translated,bilingual')
    cli.add_argument('--order', default='original-left', choices=['original-left', 'translated-left'])
    args = cli.parse_args()
    try:
        path = Path(args.document)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("PDF_DOCUMENT_TOO_LARGE")
        result = export_pdfs(args.input, json.loads(path.read_text("utf-8")), args.output, args.outputs.split(','), args.order)
        print(json.dumps({'ok': True, **{k: v for k, v in result.items() if k != 'report'}}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": error_code(error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
