"""Same-format raster delivery, reusing the positioned PDF text renderer offline."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pymupdf as fitz
from PIL import Image

# Gecko launches with -I: load only the packaged sibling, never cwd modules.
spec = importlib.util.spec_from_file_location('zhanhtran_pdf_layout', Path(__file__).with_name('local_pdf.py'))
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)
FORMATS = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'tif': 'TIFF', 'tiff': 'TIFF', 'bmp': 'BMP', 'webp': 'WEBP'}
Image.MAX_IMAGE_PIXELS = 50_000_000


def render_frame(image, before, after, number, report):
    if image.size != (before['width'], before['height']):
        raise ValueError('IMAGE_LAYOUT_INVALID')
    bounds = fitz.Rect(0, 0, image.width, image.height)
    for old, new in zip(before['blocks'], after['blocks']):
        if old.get('layout_lines'):
            lines = new.get('layout_lines', [])
            if len(lines) != len(old['layout_lines']):
                raise ValueError('IMAGE_TRANSLATION_INCOMPLETE')
            for source_line, target_line in zip(old['layout_lines'], lines):
                layout.checked_rect(source_line['bbox'], bounds)
                if source_line['text'].strip() and not target_line.get('text', '').strip():
                    raise ValueError('IMAGE_TRANSLATION_INCOMPLETE')
    with fitz.open() as original:
        page = original.new_page(width=image.width, height=image.height)
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        page.insert_image(page.rect, stream=stream.getvalue())
        with fitz.open(stream=original.tobytes(), filetype='pdf') as translated:
            units = layout.page_units(page, before, after, report)
            # Raster pages have no native font spans; infer size from the OCR line height.
            units = [{**item, 'size': max(6, min(48, item['rect'].height * .72))} for item in units]
            layout.place_page(translated[0], page, units, number, report)
            if report['unplaced']:
                raise ValueError('IMAGE_TRANSLATION_INCOMPLETE')
            pix = translated[0].get_pixmap(alpha=False)
            rendered = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            if rendered.size != image.size:
                raise ValueError('IMAGE_LAYOUT_INVALID')
            # Preserve exact source colors/alpha outside translated text rectangles.
            result = image.copy()
            for item in units:
                rect = item['rect'].irect
                box = (rect.x0, rect.y0, rect.x1, rect.y1)
                result.paste(rendered.crop(box).convert('RGBA'), box)
            return result


def save_frames(path, frames, fmt, info, durations):
    mode = 'RGB' if fmt in ('JPEG', 'BMP') else 'RGBA'
    images = [frame.convert(mode) for frame in frames]
    options = {}
    if fmt == 'JPEG':
        options.update(quality=95, subsampling=0)
    if fmt == 'WEBP':
        options['lossless'] = True
    if fmt == 'TIFF':
        options['compression'] = 'tiff_deflate'
    if info.get('dpi'):
        options['dpi'] = info['dpi']
    if len(images) > 1:
        if fmt not in ('TIFF', 'PNG', 'WEBP'):
            raise ValueError('IMAGE_FORMAT_INVALID')
        options.update(save_all=True, append_images=images[1:])
        if fmt in ('PNG', 'WEBP'):
            options.update(duration=durations, loop=info.get('loop', 0))
    images[0].save(path, format=fmt, **options)
    with Image.open(path) as reopened:
        if reopened.format != fmt or getattr(reopened, 'n_frames', 1) != len(images):
            raise ValueError('IMAGE_OUTPUT_INVALID')
        for n, frame in enumerate(images):
            reopened.seek(n)
            reopened.load()
            if reopened.size != frame.size:
                raise ValueError('IMAGE_OUTPUT_INVALID')


def export_images(source, document, output_dir, outputs=('translated', 'bilingual'), order='original-left'):
    outputs = layout.selected_outputs(outputs, order)
    source = Path(source).resolve(strict=True)
    extension = source.suffix[1:].lower()
    fmt = FORMATS.get(extension)
    if not fmt:
        raise ValueError('IMAGE_FORMAT_INVALID')
    layout.validate_document(source, document)
    folder = Path(output_dir).resolve()
    if folder == source or source in folder.parents:
        raise ValueError('IMAGE_OUTPUT_INVALID')
    folder.mkdir(parents=True, exist_ok=True)
    name = source.stem[:64].rstrip(' .')
    paths = {kind: folder / (name + '-' + kind + source.suffix) for kind in outputs}
    if any(path.exists() for path in paths.values()):
        raise ValueError('IMAGE_OUTPUT_EXISTS')
    temps = {kind: path.with_name(path.name + '.tmp') for kind, path in paths.items()}
    report = {'source_sha256': document['source_sha256'], 'mode': 'source-layout/side-by-side', 'order': order,
              'placements': [], 'unplaced': [], 'preserved_formulas': [], 'retained_image_tables': [], 'positioned_image_tables': []}
    current_frame = None
    try:
        translated, bilingual, durations = [], [], []
        total_pixels = 0
        with Image.open(source) as image:
            if image.format != fmt:
                raise ValueError('IMAGE_FORMAT_INVALID')
            if getattr(image, 'n_frames', 1) != len(document['pages']):
                raise ValueError('IMAGE_SOURCE_INVALID')
            info = dict(image.info)
            for n, (before, after) in enumerate(zip(document['pages'], document['translated_pages'])):
                current_frame = n + 1
                image.seek(n)
                total_pixels += image.width * image.height
                if image.width * image.height > 25_000_000 or total_pixels > 50_000_000:
                    raise ValueError('IMAGE_PIXEL_LIMIT')
                frame = image.convert('RGBA')
                rendered = render_frame(frame, before, after, n+1, report)
                if 'translated' in outputs:
                    translated.append(rendered)
                if 'bilingual' in outputs:
                    pair = Image.new('RGBA', (frame.width * 2, frame.height))
                    first, second = (frame, rendered) if order == 'original-left' else (rendered, frame)
                    pair.paste(first, (0, 0))
                    pair.paste(second, (frame.width, 0))
                    bilingual.append(pair)
                durations.append(image.info.get('duration', 100))
        for kind, frames in [('translated', translated), ('bilingual', bilingual)]:
            if kind in outputs:
                save_frames(temps[kind], frames, fmt, info, durations)
        for kind, path in paths.items():
            temps[kind].replace(path)
        report['frames'] = len(document['pages'])
        report['format'] = fmt
        report['files'] = {kind: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'bytes': path.stat().st_size} for kind, path in paths.items()}
        (folder / 'image-layout-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return {**{kind: str(path) for kind, path in paths.items()}, 'report': report}
    except Exception as error:
        for path in list(temps.values()) + list(paths.values()):
            path.unlink(missing_ok=True)
        (folder / 'image-layout-failure.json').write_text(json.dumps({**report, 'failed_frame': current_frame,
            'error': error_code(error)}, ensure_ascii=False, indent=2), encoding='utf-8')
        raise


def error_code(error):
    import re
    return str(error) if isinstance(error, ValueError) and re.fullmatch(r'(PDF|IMAGE)_[A-Z_]+', str(error)) else type(error).__name__


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--input', required=True)
    cli.add_argument('--document', required=True)
    cli.add_argument('--output', required=True)
    cli.add_argument('--outputs', default='translated,bilingual')
    cli.add_argument('--order', default='original-left', choices=['original-left', 'translated-left'])
    args = cli.parse_args()
    try:
        path = Path(args.document)
        if path.stat().st_size > 64*1024*1024:
            raise ValueError('IMAGE_DOCUMENT_TOO_LARGE')
        result = export_images(args.input, json.loads(path.read_text('utf-8')), args.output, args.outputs.split(','), args.order)
        print(json.dumps({'ok': True, **{k: v for k, v in result.items() if k != 'report'}}))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': error_code(error)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
