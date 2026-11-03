"""Offline PDF/image layout, OCR and table extraction. No network or credentials."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import re
import unicodedata
import copy

import numpy as np
import pymupdf as fitz
from PIL import Image

# CLI stdout is a single JSON response, not a dependency diagnostic channel.
fitz.no_recommend_layout()
Image.MAX_IMAGE_PIXELS = 25_000_000
_OCR = None
_IMAGE_CACHE = {}


class PageAnalysis:
    """One text extraction and one lazy geometry scan per page, shared by regions."""
    def __init__(self, page):
        self.page = page
        self.native = page.get_text('dict')
        self.raw = None
        self.clips = {}
        self.drawings = self.trace = self.tables = None

    def __getattr__(self, name):
        return getattr(self.page, name)

    def get_text(self, kind, clip=None, **options):
        if kind not in ('dict', 'rawdict') or options:
            return self.page.get_text(kind, clip=clip, **options)
        if kind == 'dict' and clip is None:
            return self.native
        if self.raw is None:
            self.raw = self.page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
        rect = fitz.Rect(clip) if clip is not None else self.rect
        key = tuple(rect)
        cached = self.clips.get((kind, key))
        if cached is not None:
            return cached
        if kind == 'dict':
            raw = self.get_text('rawdict', clip=rect)
            blocks = []
            for block in raw['blocks']:
                lines = []
                for line in block['lines']:
                    spans = [{**span, 'text': ''.join(char['c'] for char in span['chars'])}
                             for span in line['spans']]
                    for span in spans:
                        span.pop('chars', None)
                    lines.append({**line, 'spans': spans})
                blocks.append({**block, 'lines': lines})
            result = {'blocks': blocks}
            self.clips[(kind, key)] = result
            return result
        x0, y0, x1, y1 = key
        blocks = []
        for block in self.raw['blocks']:
            bx0, by0, bx1, by1 = block['bbox']
            if block['type'] != 0 or bx0 >= x1 or bx1 <= x0 or by0 >= y1 or by1 <= y0:
                continue
            lines = []
            for line in block['lines']:
                lx0, ly0, lx1, ly1 = line['bbox']
                if lx0 >= x1 or lx1 <= x0 or ly0 >= y1 or ly1 <= y0:
                    continue
                spans = []
                for span in line['spans']:
                    sx0, sy0, sx1, sy1 = span.get('bbox', line['bbox'])
                    if sx0 >= x1 or sx1 <= x0 or sy0 >= y1 or sy1 <= y0:
                        continue
                    chars = [c for c in span['chars'] if x0 <= (c['bbox'][0]+c['bbox'][2])/2 <= x1
                             and y0 <= (c['bbox'][1]+c['bbox'][3])/2 <= y1]
                    if not chars:
                        continue
                    box = [min(c['bbox'][0] for c in chars), min(c['bbox'][1] for c in chars),
                           max(c['bbox'][2] for c in chars), max(c['bbox'][3] for c in chars)]
                    spans.append({**span, 'bbox': box, 'chars': chars})
                if spans:
                    box = [min(s['bbox'][0] for s in spans), min(s['bbox'][1] for s in spans),
                           max(s['bbox'][2] for s in spans), max(s['bbox'][3] for s in spans)]
                    lines.append({**line, 'bbox': list(box), 'spans': spans})
            if lines:
                blocks.append({**block, 'lines': lines})
        result = {'blocks': blocks}
        self.clips[(kind, key)] = result
        return result

    def get_drawings(self):
        if self.drawings is None:
            self.drawings = self.page.get_drawings() if hasattr(self.page, 'get_drawings') else []
        return self.drawings

    def get_texttrace(self):
        if self.trace is None:
            self.trace = self.page.get_texttrace() if hasattr(self.page, 'get_texttrace') else []
        return self.trace

    def find_tables(self):
        if self.tables is None:
            self.tables = self.page.find_tables(paths=self.get_drawings()) if isinstance(self.page, fitz.Page) else self.page.find_tables()
        return self.tables


def native_row_span(line):
    return max(line['spans'],key=lambda s:sum(bool(c['c'].strip()) for c in s['chars']))


def native_row_baseline(line):
    # Glyph bboxes rise for accents/superscripts and vary by font. Their top
    # edge is not the line pitch. Ignore blank/raised leading spans.
    span = native_row_span(line)
    return span.get('origin',(0,line['bbox'][3]-span['size']*.3))[1]


def native_text_rows(page, box):
    """Visible horizontal rows shared by metadata and definition detection."""
    raw = page.get_text('rawdict', clip=fitz.Rect(box) + (-.1, -.1, .1, .1))
    lines = [line for b in raw['blocks'] if b['type'] == 0 for line in b['lines']]
    rows = []
    for line in lines:
        # PDF generators often leave a displaced whitespace-only text run. It
        # has a real bbox but is not a paragraph line, font or indent anchor.
        if not any(c['c'].strip() for s in line['spans'] for c in s['chars']):
            continue
        # Trim whitespace geometry, including spaces embedded in a visible
        # span. Displaced blank runs are not layout anchors or usable space.
        visible = [c for s in line['spans'] for c in s['chars'] if c['c'].strip()]
        line = {**line, 'bbox': [min(c['bbox'][0] for c in visible), min(c['bbox'][1] for c in visible),
                                max(c['bbox'][2] for c in visible), max(c['bbox'][3] for c in visible)]}
        # A number and its text can be separate extraction lines with a tiny
        # baseline offset (different fonts). Rejoin that visual row before
        # finding its marker/body gutter, not by exact rounded y equality.
        row = next((r for r in reversed(rows)
                    if tuple(r.get('dir',(1,0)))==tuple(line.get('dir',(1,0)))
                    and abs(native_row_baseline(r)-native_row_baseline(line))<=
                        min(native_row_span(r)['size'],native_row_span(line)['size'])*.12),None)
        if row is not None:
            row['spans'].extend(line['spans'])
            row['spans'].sort(key=lambda s:s['bbox'][0])
            row['bbox'] = list(fitz.Rect(row['bbox']) | fitz.Rect(line['bbox']))
        else:
            rows.append({**line,'spans':list(line['spans'])})
    return sorted(rows,key=lambda line:(native_row_baseline(line),line['bbox'][0]))


def native_metadata(page, block):
    if block.get('definition'):
        row = block['definition']
        bounds = fitz.Rect(block['bbox'])+(-.1,-.1,.1,.1)
        if (not bounds.contains(fitz.Rect(row['label_bbox'])) or not bounds.contains(fitz.Rect(row['body_bbox']))
                or fitz.Rect(row['label_bbox']).x1>=fitz.Rect(row['body_bbox']).x0
                or not block.get('text','').startswith(row['label']+'\n')):
            raise ValueError('PDF_LAYOUT_INVALID')
        def value(box):
            return ''.join(c['c'] for line in native_text_rows(page,box) for span in line['spans'] for c in span['chars'])
        if (re.sub(r'\s+','',value(row['label_bbox']))!=re.sub(r'\s+','',row['label'])
                or re.sub(r'\s+','',value(row['body_bbox']))!=re.sub(r'\s+','',block['text'].split('\n',1)[1])):
            raise ValueError('PDF_LAYOUT_INVALID')
        result = native_metadata(page,{'bbox':row['body_bbox']})
        result.update(prefix={'text':row['label'],'bbox':row['label_bbox'],'kind':'definition'},
                      translation_text=block['text'].split('\n',1)[1],flow_blocked=True)
        return result
    lines = native_text_rows(page,block['bbox'])
    spans = [s for line in lines for s in line['spans'] if any(c['c'].strip() for c in s['chars'])]
    if not spans:
        return {}
    weighted = sorted((s['size'], len(s['chars'])) for s in spans)
    count = sum(n for _, n in weighted)
    running = 0
    size = weighted[-1][0]
    for value, n in weighted:
        running += n
        if running >= count/2:
            size = value
            break
    main = max(spans, key=lambda s: len(s['chars']))
    def bold(s):
        return bool(s.get('flags', 0) & 16 or re.search(r'bold|-b', s['font'], re.I))
    def italic(s):
        return bool(s.get('flags', 0) & 2 or re.search(r'italic|-i$|-bi$', s['font'], re.I))
    baselines = [native_row_baseline(line) for line in lines]
    leading = [b-a for a,b in zip(baselines, baselines[1:]) if size*.8 < b-a < size*3]
    style = {'size': size, 'bold': sum(len(s['chars']) for s in spans if bold(s)) > count/2,
             'italic': sum(len(s['chars']) for s in spans if italic(s)) > count/2,
             'family': 'serif' if main.get('flags', 0) & 4 else 'sans-serif',
             'line_height': float(np.median(leading))/size if leading else 1.35}
    rows = []
    for line in lines:
        chars = [c for s in line['spans'] for c in s['chars']]
        text = ''.join(c['c'] for c in chars)
        match = re.match(r'^(?P<label>.*?)(?P<dots>[.\u2026\u00b7]{3,}\s*)(?P<page>\d+|[ivxlcdm]+)\s*$', text, re.I)
        if not match:
            continue
        label = match.group('label')
        prefix = re.match(r'^\s*\d+(?:\.\d+)*\.?\s*', label)
        start = prefix.end() if prefix else len(label)-len(label.lstrip())
        end = len(label.rstrip())
        if start >= end:
            continue
        def box(a, b):
            result = fitz.Rect(chars[a]['bbox'])
            for char in chars[a+1:b]:
                result |= fitz.Rect(char['bbox'])
            return list(result)
        rows.append({'text': label[start:end], 'bbox': list(line['bbox']), 'title_bbox': box(start,end),
                     'number': label[:start].strip(), 'number_bbox': box(0,start) if start else None,
                     'page_text': match.group('page'), 'page_bbox': box(*match.span('page')),
                     'leader_bbox': box(*match.span('dots')), 'style': style, 'baseline': chars[-1]['origin'][1]})
    if rows and len(rows) == len(lines):
        return {'style': style, 'toc_rows': rows, 'source_lines': [list(line['bbox']) for line in lines]}
    first_chars = [c for s in lines[0]['spans'] for c in s['chars']]
    first_text = ''.join(c['c'] for c in first_chars)
    prefix = re.match(r'^\s*(?:[\u2022\u25aa\u25ab\u25a0\u25a1\u25e6\uf0a7\uf0b7\-\u2013\u2014]|\([a-zA-Z0-9]+\)|[a-zA-Z][.)])\s*', first_text)
    numbered = False
    if prefix is None:
        candidate = re.match(r'^\s*\d{1,4}[.)]?\s*',first_text)
        if candidate and candidate.end()<len(first_chars) and first_text[candidate.end():].strip():
            label_end = len(candidate.group().rstrip())
            body = next((c for c in first_chars[candidate.end():] if c['c'].strip()),None)
            gap = body['bbox'][0]-first_chars[label_end-1]['bbox'][2] if body else 0
            # Bare numbers need a physical marker gutter. A normal word-space
            # after a year/count, a time or a decimal is not a list prefix.
            punctuated = candidate.group().rstrip().endswith(('.',')'))
            if body and (gap>=size*.6 or (punctuated and gap>=size*.18)) \
                    and first_text[candidate.end()] not in '.:0123456789':
                prefix,numbered = candidate,True
    if prefix and not first_text[prefix.end():].strip():
        # A wrapped final label such as "C." is content, not an empty list.
        prefix = None
    if prefix and prefix.end()<len(first_chars) and not prefix.group()[-1].isspace():
        if first_chars[prefix.end()]['bbox'][0]-first_chars[prefix.end()-1]['bbox'][2] < size*.4:
            prefix = None
    result = {'style': style, 'source_lines': [list(line['bbox']) for line in lines],
              'source_baselines':baselines,'source_baseline': baselines[0], 'alignment': 'left'}
    word = re.search(r'\S+',first_text[prefix.end():] if prefix else first_text)
    if word:
        start,end = word.span()
        if prefix:
            start,end = start+prefix.end(),end+prefix.end()
        # CJK can wrap between characters; an English word is indivisible.
        if re.match(r'[\u2e80-\ua4cf\uac00-\ud7af\uf900-\ufaff]',first_chars[start]['c']):
            end = start+1
        result['source_first_word_width'] = max(c['bbox'][2] for c in first_chars[start:end])-first_chars[start]['bbox'][0]
    if any(tuple(line.get('dir',(1,0)))!=(1,0) for line in lines) or any(
            s.get('flags',0)&8 or re.search(r'courier|mono',s['font'],re.I) for s in spans):
        result['flow_blocked'] = True
    if prefix:
        marker_chars = first_chars[:prefix.end()]
        box = fitz.Rect(marker_chars[0]['bbox'])
        for char in marker_chars[1:]:
            box |= fitz.Rect(char['bbox'])
        result['prefix'] = {'text': prefix.group().strip(), 'bbox': list(box)}
        if numbered:
            result['prefix']['kind'] = 'number'
        result['translation_text'] = first_text[prefix.end():] + ''.join('\n'+''.join(c['c'] for s in line['spans'] for c in s['chars']) for line in lines[1:])
        first_body = [c for c in first_chars[prefix.end():] if c['c'].strip()]
        left = first_body[0]['bbox'][0] if first_body else box.x1
        # The marker has its own fixed region and must never be redacted as
        # part of the translated body.
        lines[0] = {**lines[0], 'bbox': [left, lines[0]['bbox'][1], lines[0]['bbox'][2], lines[0]['bbox'][3]]}
    else:
        left = min(line['bbox'][0] for line in (lines[1:] or lines))
    result['body_left'] = left
    result['first_line_indent'] = lines[0]['bbox'][0]-left
    result['source_lines'] = [list(line['bbox']) for line in lines]
    result['fixed_bbox'] = [min(line['bbox'][0] for line in lines), min(line['bbox'][1] for line in lines),
                            max(line['bbox'][2] for line in lines), max(line['bbox'][3] for line in lines)]
    value = '\n'.join(''.join(c['c'] for s in line['spans'] for c in s['chars']) for line in lines).strip()
    if re.match(r'^(?:fig(?:ure)?[. ]|table\s)\s*\d', value, re.I):
        result['alignment'] = 'center'
    elif re.fullmatch(r'(?:Page\s+)?\d+\s*(?:of\s+\d+)?', value, re.I):
        result['alignment'] = 'right'
    return result


def protect_native_symbols(page, block, inline):
    """Keep coordinate glyphs and private font encodings out of model/font conversion."""
    raw = page.get_text('rawdict', clip=fitz.Rect(block['bbox']) + (-.1,-.1,.1,.1))
    formulas = list(inline.get('inline_math', []))
    rows = [[c for span in line['spans'] for c in span['chars']]
            for native in raw['blocks'] if native['type'] == 0 for line in native['lines']]
    text = inline.get('translation_text', block.get('translation_text',
        '\n'.join(''.join(c['c'] for c in chars) for chars in rows)))
    prefix = fitz.Rect(block['prefix']['bbox']) if block.get('prefix') else None
    added = False
    # U+201F is used as a single-prime glyph by some publisher fonts. Keep
    # the complete coordinate, including spaces and split font spans, opaque.
    prime = r"[\u2032\u2018\u2019\u201b\u201f\u02b9\u02bc']"
    second = r"[\u0022\u2033\u201c\u201d\u201f\u02ba\u2032\u2018\u2019']{1,2}"
    pattern = re.compile(r"\d{1,3}\s*[\u00b0\u00ba]\s*\d{1,2}\s*"+prime+
                         r"(?:\s*\d{1,2}(?:[.,]\d+)?\s*"+second+r")?(?:\s*[NSEW](?![A-Za-z]))?|[\ue000-\uf8ff]+")
    for chars in rows:
        value = ''.join(c['c'] for c in chars)
        for match in pattern.finditer(value):
            rect = fitz.Rect(chars[match.start()]['bbox'])
            for char in chars[match.start()+1:match.end()]:
                rect |= fitz.Rect(char['bbox'])
            # A list marker is already retained outside the body frame. It is
            # not an inline reservation, even when the same PUA glyph appears
            # again in the body. Only register a token after replacing text.
            if (prefix is not None and (prefix+(-.1,-.1,.1,.1)).contains(rect)) or match.group() not in text:
                continue
            used = {token['id'] for token in formulas}
            index = 0
            while 'M'+str(index) in used:
                index += 1
            token = {'id': 'M'+str(index), 'bbox': list(rect), 'kind': 'symbol',
                     'baseline': chars[match.start()]['origin'][1], 'text': match.group()}
            text = text.replace(match.group(), '[['+token['id']+']]', 1)
            formulas.append(token)
            added = True
    if not added:
        return inline
    return {'translation_text': text, 'inline_math': formulas}


def native_text_styles(page, block):
    """Keep mixed prose styles in one translation request, with paired markers.

    Formula reservations already carry their original appearance. Only wrap
    non-main prose runs still present in the continuous translation text.
    """
    text = block.get('translation_text', block.get('text', ''))
    main = block.get('style', {})
    styles, cursor = [], 0
    raw = page.get_text('rawdict', clip=fitz.Rect(block['bbox'])+(-.1,-.1,.1,.1))
    for native in raw['blocks']:
        for line in native.get('lines', []):
            for span in line['spans']:
                value = ''.join(c['c'] for c in span['chars']).strip()
                if not value or not re.search(r'[A-Za-z\u3400-\u9fff]{2}', value):
                    continue
                style = {'size': span['size'], 'bold': bool(span.get('flags', 0)&16 or re.search(r'bold|-b',span['font'],re.I)),
                         'italic': bool(span.get('flags',0)&2 or re.search(r'italic|-i$|-bi$',span['font'],re.I)),
                         'family': 'serif' if span.get('flags',0)&4 else 'sans-serif'}
                match = re.search(re.escape(value).replace(r'\ ',r'\s+'), text[cursor:])
                if not match:
                    continue
                a, b = cursor+match.start(), cursor+match.end()
                cursor = b
                if (abs(style['size']-main.get('size',style['size']))<.01
                        and all(style[k]==main.get(k,False if k!='family' else 'serif') for k in ('bold','italic','family'))):
                    continue
                name = 'S'+str(len(styles))
                styles.append({'id': name, **style})
                opening, closing = '[['+name+']]', '[[/'+name+']]'
                text = text[:a]+opening+text[a:b]+closing+text[b:]
                cursor += len(opening)+len(closing)
    return {'translation_text':text,'text_styles':styles} if styles else {}


def native_cell_content(page, cell, text):
    block = {'bbox':cell,'text':text,**native_metadata(page,{'bbox':cell})}
    # Cell topology, not paragraph whitespace, supplies its fixed rectangle.
    # A cell list marker is kept native by the PDF renderer as a separate box.
    block.update(protect_native_symbols(page,block,{}))
    block.update(native_text_styles(page,block))
    if not block.get('inline_math') and not block.get('text_styles'):
        return None
    return {key:block[key] for key in ('translation_text','inline_math','text_styles','source_baseline','alignment',
                                      'prefix','body_left','first_line_indent') if key in block}


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
        reference = max(spans,key=lambda s:len(s['chars']))
        shifted = abs(span.get('origin',(0,0))[1]-reference.get('origin',(0,0))[1]) > size*.15
        math = bool(re.search(r"math|sym|AdvPS", span["font"], re.I)) or (span["size"] < size * .85 and (shifted or not re.search(r'[A-Za-z]{3}', value))) or (
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
    # Dates, counts, parentheses and wrapped lines occur in ordinary conditional
    # prose. They do not justify splitting it into fixed equation-label boxes.
    equation = (len(text)<160 and re.search(r"\bif\b", text) and re.search(r"[=<>\u2264\u2265\u2260\u2248\u2208\u2209]", text)) or (
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


def native_definition_blocks(page, blocks):
    """Recognise repeated unruled acronym/definition columns before prose flow.

    A physical gutter and repeated aligned key/value rows are required;
    case, spaces and terminal dots are not reliable abbreviation boundaries.
    An acronym in normal prose is not a definition list. Consume only fully
    covered source blocks so detection cannot erase unrelated source content.
    The native key is retained as a prefix and only the definition is sent to
    translation. Rows remain separate even if their right edges are ragged.
    """
    eligible = [b for b in blocks if b.get('type') in ('paragraph','heading','footnote')
                and b.get('extraction','pymupdf')=='pymupdf'
                and not any(b.get(k) for k in ('definition','toc_rows','layout_runs'))]
    if not eligible:
        return list(blocks)
    rows = native_text_rows(page,page.rect)
    def chars(line):
        return [c for s in line['spans'] for c in s['chars']]
    def box(values):
        return [min(c['bbox'][0] for c in values),min(c['bbox'][1] for c in values),
                max(c['bbox'][2] for c in values),max(c['bbox'][3] for c in values)]
    def owners(values):
        return [b for b in eligible if any(inside(c['bbox'],b['bbox']) for c in values)]
    def covered(values, regions):
        return all(any(inside(c['bbox'],r) for r in regions) for c in values if c['c'].strip())
    known = [b['bbox'] for b in blocks if b.get('definition')]
    def label_text(values,size):
        text,previous = '',None
        for char in values:
            # Some writers encode a key's word spaces only as positioning
            # between text runs. Keep them in the key, never in the value.
            if (previous and previous['c'].strip() and char['c'].strip()
                    and char['bbox'][0]-previous['bbox'][2]>size*.18):
                text += ' '
            text += char['c'];previous = char
        return re.sub(r'\s+',' ',text).strip()
    candidates = []
    for index,line in enumerate(rows):
        if tuple(line.get('dir',(1,0)))!=(1,0):
            continue
        all_chars = chars(line)
        visible = [(i,c) for i,c in enumerate(all_chars) if c['c'].strip()]
        size = native_row_span(line)['size']
        # A long key (MoCTCA) leaves less than two em before the established
        # value column. Consider all real gutters, then require repeated
        # column alignment; a key's internal spaces are not the first gutter.
        for gap in range(1,len(visible)):
            width = visible[gap][1]['bbox'][0]-visible[gap-1][1]['bbox'][2]
            if width<size*.6:
                continue
            key = [c for _,c in visible[:gap]]
            label = label_text(all_chars[visible[0][0]:visible[gap-1][0]+1],size)
            if not (1<=len(re.sub(r'\s+','',label))<=24 and len(label.split())<=4
                    and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 .()/+&%_-]*',label)
                    and re.search(r'[A-Za-z]',label)):
                continue
            body = all_chars[visible[gap][0]:]
            if (body[0]['bbox'][0]-key[0]['bbox'][0]<size*2.5
                    or not re.search(r'[A-Za-z\u3400-\u9fff]{2}', ''.join(c['c'] for c in body))):
                continue
            source = owners(key+body)
            if not covered(key+body,[b['bbox'] for b in source]+known):
                continue
            candidates.append({'index':index,'label':label,'label_bbox':box(key),'body_bbox':box(body),
                               'lines':[line],'key_chars':key,'body_chars':[body],
                               'size':size,'owners':source,'gutter':width})
    selected = {}
    for row in candidates:
        peers = {other['index'] for other in candidates if other['index']!=row['index']
                 and abs(other['label_bbox'][0]-row['label_bbox'][0])<=row['size']*.2
                 and abs(other['body_bbox'][0]-row['body_bbox'][0])<=row['size']*.5
                 and abs(other['size']-row['size'])<.05
                 and row['size']*.8<=abs(native_row_baseline(other['lines'][0])-
                                           native_row_baseline(row['lines'][0]))<=row['size']*4}
        if peers and covered(row['key_chars']+[c for values in row['body_chars'] for c in values],
                             [b['bbox'] for b in row['owners']]):
            # Existing correctly parsed rows still anchor old-cache upgrades,
            # but must not be replaced or counted twice. Pick one split only.
            rank = (len(peers),row['gutter'])
            old = selected.get(row['index'])
            if old is None or rank>old[0]:
                selected[row['index']] = (rank,row)
    accepted = [value[1] for _,value in sorted(selected.items())]
    for row in accepted:
        # Definitions may have source-wrapped continuation lines under the
        # value column. Never include the next native key or another column.
        previous = row['lines'][0]
        for line in rows[row['index']+1:]:
            distance = native_row_baseline(line)-native_row_baseline(previous)
            values = chars(line)
            if not (row['size']*.8<=distance<=row['size']*2.05
                    and abs(line['bbox'][0]-row['body_bbox'][0])<=row['size']*.2
                    and tuple(line.get('dir',(1,0)))==(1,0)
                    and abs(native_row_span(line)['size']-row['size'])<.05
                    and covered(values,[b['bbox'] for b in eligible])):
                break
            row['lines'].append(line);row['body_chars'].append(values)
            row['body_bbox'] = list(fitz.Rect(row['body_bbox']) | fitz.Rect(box(values)))
            row['owners'] += [b for b in owners(values) if all(b is not old for old in row['owners'])]
            previous = line
    # A partly covered native block is not disposable. Remove its candidate
    # rows as well, rather than swallowing a heading or unrecognised text.
    while accepted:
        affected = [b for b in eligible if any(any(b is old for old in r['owners']) for r in accepted)]
        bad = [b for b in affected if not covered(
            [c for line in native_text_rows(page,b['bbox']) for c in chars(line)],
            [r['label_bbox'] for r in accepted]+[r['body_bbox'] for r in accepted])]
        if not bad:
            break
        accepted = [r for r in accepted if not any(any(b is old for old in r['owners']) for b in bad)]
    consumed = [b for r in accepted for b in r['owners']]
    result = [b for b in blocks if all(b is not old for old in consumed)]
    for index,row in enumerate(accepted):
        body_text = '\n'.join(''.join(c['c'] for c in line).strip() for line in row['body_chars'])
        bounds = list(fitz.Rect(row['label_bbox']) | fitz.Rect(row['body_bbox']))
        source_ids = [b['id'] for b in row['owners'] if 'id' in b]
        body_owners = owners([c for line in row['body_chars'] for c in line])
        definition = {'label':row['label'],'label_bbox':row['label_bbox'],'body_bbox':row['body_bbox'],
                      'source_ids':source_ids,'body_source_ids':[b['id'] for b in body_owners if 'id' in b]}
        block = {'type':'paragraph','text':row['label']+'\n'+body_text,'bbox':bounds,
                 'extraction':'pymupdf','definition':definition}
        if source_ids:
            block['id'] = source_ids[0]+'-def'+str(index)
        result.append(block)
    return reading_order(result)


def native_paragraph_groups(blocks):
    """Join soft-wrapped prose, not every PDF block or sentence boundary.

    Some writers emit each visual line as a separate block. A continuation
    must follow a full line at the source column edge, with matching body
    style, indentation and baseline pitch. A list item may own continuation
    lines, but a new marker always starts another group. Blank lines, short
    final lines, captions, columns and intervening artwork remain boundaries.
    The resulting rectangle is only the union of these source text lines.
    """
    ordered = reading_order(blocks)
    def eligible(b):
        text = b.get('text','').strip()
        return (b.get('type')=='paragraph' and b.get('extraction','pymupdf')=='pymupdf'
                and b.get('source_lines') and b.get('style',{}).get('size',0)>0
                and b.get('alignment','left') in ('left','justify')
                and not any(b.get(k) for k in ('toc_rows','layout_runs','flow_blocked'))
                and (b.get('prefix') or not re.match(r'^(?:\d+[.)]|\d+(?:\.\d+)+\.?)\s+\S',text))
                and not re.match(r'^(?:fig(?:ure)?[. ]|table\s)\s*\d',text,re.I)
                and not (re.search(r'[=\u222b\u2211\u221a]',text)
                         and len(re.findall(r'[A-Za-z]{3,}',text))<8))
    candidates = [b for b in ordered if eligible(b)]
    def same_style(a,b):
        sa,sb = a['style'],b['style']
        return (abs(sa['size']-sb['size'])<.05
                and all(sa.get(k,d)==sb.get(k,d) for k,d in
                        (('bold',False),('italic',False),('family','serif'))))
    def step(a,b):
        return (b.get('source_baselines') or [line[1] for line in b['source_lines']])[0]-(
            a.get('source_baselines') or [line[1] for line in a['source_lines']])[-1]
    groups = []
    for b in ordered:
        if not eligible(b) or b.get('prefix') or not groups or not eligible(groups[-1][-1]):
            groups.append([b]); continue
        group = groups[-1]
        a = group[-1]
        size = a['style']['size']
        left = min(x.get('body_left',x['bbox'][0]) for x in group)
        next_left = b.get('body_left',b['bbox'][0])
        # A displaced first line may be a first-line indent; after that the
        # body anchor cannot wander. Never bridge independent columns.
        aligned = abs(next_left-left)<=size*.15 or (
            not group[0].get('prefix') and len(group)==1 and len(a['source_lines'])==1
            and 0<left-next_left<=size*3)
        if not same_style(a,b) or not aligned or abs(b.get('first_line_indent',0))>size*.15:
            groups.append([b]); continue
        peers = [p for p in candidates if same_style(a,p)
                 and abs(p.get('body_left',p['bbox'][0])-min(left,next_left))<=size*.15]
        right = max([a['bbox'][2],b['bbox'][2]]+[p['bbox'][2] for p in peers])
        width = right-min(left,next_left)
        # A long next word explains ragged right edges in a narrow column.
        # Use its actual source glyph width, not sentence punctuation or a
        # guessed font metric, to recognise that it could not fit above.
        wrap_gap = max(size*2,width*.04,b.get('source_first_word_width',0)+size*.25)
        full = (a['source_lines'][-1][2]>=right-wrap_gap
                and a['source_lines'][-1][2]-a['source_lines'][-1][0]>=size*12)
        pitches = [y-x for p in peers
                   for values in [p.get('source_baselines') or [line[1] for line in p['source_lines']]]
                   for x,y in zip(values,values[1:])]
        pitches += [step(x,y) for x,y in zip(peers,peers[1:]) if 0<step(x,y)<=size*2.05]
        pitches = sorted(p for p in pitches if size*.9<=p<=size*2.05)
        pitch = float(np.median(pitches[:max(1,(len(pitches)+3)//4)])) if pitches else size*1.35
        distance = step(a,b)
        rect = fitz.Rect(a.get('fixed_bbox',a['bbox'])) | fitz.Rect(b.get('fixed_bbox',b['bbox']))
        # A paragraph must not absorb a figure, cell, label or another column.
        obstacles = any(p is not b and all(p is not m for m in group)
                        and (fitz.Rect(p.get('fixed_bbox',p['bbox'])) & rect).get_area()>.1
                        for p in ordered)
        if full and size*.9<=distance<=min(size*2.05,pitch*1.15) and not obstacles:
            group.append(b)
        else:
            groups.append([b])
    return groups


def join_native_paragraph(page, members):
    """Re-read style/indent anchors once for the complete source paragraph."""
    rect = fitz.Rect(members[0]['bbox'])
    for b in members[1:]:
        rect |= fitz.Rect(b['bbox'])
    text = '\n'.join(b['text'].strip() for b in members)
    text = re.sub(r'(?<=\w)-\s*\n\s*(?=[a-z])','',text)
    block = {**members[0],'bbox':list(rect),'text':text,
             'source_fragments':sum(b.get('source_fragments',1) for b in members)}
    block.update(native_metadata(page,block))
    return block


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
                      "quad": points.astype(float).tolist(),
                      "bbox": [float(points[:, 0].min()), float(points[:, 1].min()),
                               float(points[:, 0].max()), float(points[:, 1].max())]})
    return reading_order(lines)


def ocr_layout_line(line, bbox, sx, sy):
    """Estimate size from the detector's rectified text height, not its AABB.

    A tilted/curved word can have a nearly square outer box even though its
    glyphs are small. Match RapidOCR's crop axes (including its tall-crop
    rotation), then measure the height in the placed image's page coordinates.
    This is source-only measurement, never a fit-to-translation font reduction.
    Old schema-1 lines without quadrilaterals retain their previous behaviour.
    """
    height = (line['bbox'][3] - line['bbox'][1]) * sy
    result = {**line, 'bbox': [bbox[0]+line['bbox'][0]*sx, bbox[1]+line['bbox'][1]*sy,
                             bbox[0]+line['bbox'][2]*sx, bbox[1]+line['bbox'][3]*sy]}
    size_source = 'ocr-axis-box'
    if 'quad' in line:
        points = np.asarray(line['quad'], dtype=float)
        if points.shape == (4, 2) and np.isfinite(points).all():
            def extent(p, a, b, c, d):
                return max(float(np.linalg.norm(p[a]-p[b])), float(np.linalg.norm(p[c]-p[d])))
            width, raw_height = extent(points, 0, 1, 2, 3), extent(points, 0, 3, 1, 2)
            placed = points * (sx, sy)
            if width > 0 and raw_height > 0:
                # Choose the recognition axis before nonuniform image scaling.
                # A lone narrow glyph (e.g. I) does not establish vertical text.
                rotated = raw_height >= width*1.5 and len(line['text'].strip()) > 1
                height = extent(placed, 0, 1, 2, 3) if rotated else extent(placed, 0, 3, 1, 2)
                result['quad'] = (placed + (bbox[0], bbox[1])).tolist()
                size_source = 'ocr-quad-height'
            else:
                result.pop('quad')
        else:
            result.pop('quad')
    result['style'] = {'size': max(.1, min(48, height*.72)), 'bold': False,
                       'family': 'serif', 'size_source': size_source}
    return result


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
    key = (image.size, hashlib.sha256(image.convert('RGB').tobytes()).digest())
    if key not in _IMAGE_CACHE:
        lines = ocr_lines(image)
        tables = image_tables(image, warnings, lines) or ruled_tables(image, lines)
        if len(_IMAGE_CACHE) >= 32:
            _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)))
        _IMAGE_CACHE[key] = (lines, tables)
    lines, tables = copy.deepcopy(_IMAGE_CACHE[key])
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
              "layout_lines": [ocr_layout_line(line, bbox, sx, sy)
                               for line in lines if not already_native(line)]}
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
    page = page if isinstance(page, PageAnalysis) else PageAnalysis(page)
    native = page.get_text("dict")
    text_blocks = [b for b in native["blocks"] if b["type"] == 0]
    visible = "".join(s["text"] for b in text_blocks for line in b["lines"] for s in line["spans"]).strip()
    if len(visible) < 20:
        return image_blocks(page_image(page), output, f"figures/page-{page.number + 1}.png", page.rect, warnings), True
    blocks = []
    for table in page.find_tables().tables:
        rows = [[str(value or "") for value in row] for row in table.extract()]
        blocks.append({"type": "table", "bbox": list(table.bbox), "rows": rows, "extraction": "pymupdf",
                       'table_header_rows':1 if table.header and not table.header.external and len(rows)>1 else 0,
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
    blocks = native_definition_blocks(page, blocks)
    for block in blocks:
        if block["type"] not in {"figure", "formula"}:
            block.update(native_metadata(page, block))
    blocks = [join_native_paragraph(page,group) if len(group)>1 else group[0]
              for group in native_paragraph_groups(blocks)]
    for block in blocks:
        if block["type"] not in {"figure", "formula"}:
            if block['type'] == 'table' and block.get('cells'):
                spans = [s for b in page.get_text('dict', clip=fitz.Rect(block['bbox']))['blocks']
                         if b['type'] == 0 for line in b['lines'] for s in line['spans']]
                # Ordinary data cells can use their full cell rectangle. Keep
                # the prose-run path for algorithms and mathematical artwork.
                math = any(re.search(r'math|sym|AdvPS', s['font'], re.I)
                           or re.search(r'[=\u222b\u2211\u221a\ue000-\uf8ff]', s['text']) for s in spans)
                if not math:
                    block['cell_styles'] = [[native_metadata(page, {'bbox': cell}).get('style', block.get('style', {'size': 9}))
                                             if cell else None for cell in row] for row in block['cells']]
                    contents = [[native_cell_content(page,cell,value) if cell and value.strip() else None
                                 for cell,value in zip(cells,values)] for cells,values in zip(block['cells'],block['rows'])]
                    if any(any(row) for row in contents):
                        block['cell_content'] = contents
                    continue
            if block.get('toc_rows'):
                continue
            inline = protect_native_symbols(page, block, {} if block.get('prefix') else native_inline_math(page, block["bbox"], block["type"]))
            runs = [] if inline or block.get('prefix') else native_layout_runs(page, block["bbox"], block["type"])
            if inline:
                block.update(inline)
            elif runs:
                block["layout_runs"] = runs
            elif native_formula_only(page, block.get('fixed_bbox',block['bbox']) if block.get('prefix') else block['bbox']):
                block["type"] = "formula"
            if block['type'] not in ('formula','table','figure') and not block.get('layout_runs'):
                block.update(native_text_styles(page, block))
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


def parse_document(path, output_dir, max_pages=500, on_progress=None):
    _IMAGE_CACHE.clear()
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
            if on_progress:
                on_progress(0, len(document))
            for page in document:
                rotation = page.rotation
                page.set_rotation(0)
                blocks, used_ocr = pdf_page(page, output, warnings)
                pages.append({"number": page.number + 1, "width": page.rect.width,
                              "height": page.rect.height, "rotation": rotation, "ocr": used_ocr, "blocks": blocks})
                if on_progress:
                    on_progress(len(pages), len(document))
    else:
        with Image.open(path) as image:
            if getattr(image, "n_frames", 1) > max_pages:
                raise ValueError("PAGE_LIMIT_EXCEEDED")
            for n in range(getattr(image, "n_frames", 1)):
                image.seek(n)
                blocks = image_blocks(image, output, f"figures/image-{n + 1}.png", [0, 0, image.width, image.height], warnings)
                pages.append({"number": n + 1, "width": image.width, "height": image.height, "ocr": True, "blocks": blocks})
                if on_progress:
                    on_progress(len(pages), getattr(image, 'n_frames', 1))
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
    cli.add_argument('--progress', action='store_true')
    args = cli.parse_args()
    try:
        callback = (lambda done,total: print('ZHANHTRAN_PROGRESS '+json.dumps({'stage':'parse','done':done,'total':total}), file=sys.stderr, flush=True)) if args.progress else None
        result = parse_document(args.input, args.output, on_progress=callback)
        print(json.dumps({"ok": True, "pages": len(result["pages"]), "warnings": result["warnings"]}))
        return 0
    except Exception as error:
        # Do not expose document contents or local filenames on stderr.
        code = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({"ok": False, "error": code if len(code) < 80 else type(error).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
