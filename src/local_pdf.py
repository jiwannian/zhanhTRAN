"""Offline, source-PDF-based translation placement using PyMuPDF's HTML layout."""
import argparse
import hashlib
import html
import json
import math
import re
import statistics
import io
import copy
import importlib.util
from functools import lru_cache
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
    if rect.is_empty or not (bounds+(-.001,-.001,.001,.001)).contains(rect):
        raise ValueError("PDF_LAYOUT_INVALID")
    return rect if bounds.contains(rect) else rect & bounds


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
            try:
                checked_rect(old["bbox"], bounds)
            except ValueError as error:
                error.layout_failure = {'failed_page':page['number'],'region':old['id']}
                raise
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


CJK_TEXT = re.compile(r'[\u2e80-\ua4cf\uac00-\ud7af\uf900-\ufaff\ufe10-\ufe1f\ufe30-\ufe6f\uff01-\uff60\uffe0-\uffe6\U00020000-\U0003134f]+')
CJK_IDEOGRAPHS = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]+')
_CJK_SUBSETS = {}


@lru_cache(maxsize=1)
def cjk_fonts():
    # Reuse installed fonts; no font download or machine path is stored in PDFs.
    for regular, bold in [('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/msyhbd.ttc'),
                          ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
                          ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc','/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc')]:
        if Path(regular).is_file():
            return Path(regular), Path(bold) if Path(bold).is_file() else Path(regular)
    return None


def cjk_subset(font, characters):
    cached, buffer = _CJK_SUBSETS.get(font,(set(),None))
    if characters <= cached:
        return buffer
    characters = characters | cached
    try:
        from fontTools import subset
    except ImportError:
        # Older parser environments remain usable before dependency updates.
        # MuPDF's PDF font subset discards Unicode cmap entries and cannot be
        # reused as a shaping font; fall back to the installed full font.
        buffer = font.read_bytes()
    else:
        options = subset.Options()
        options.font_number = 0
        options.drop_tables.extend(['MERG','meta','BASE','GDEF','GPOS','GSUB','JSTF','kern'])
        options.layout_features = []
        options.layout_closure = False
        options.hinting = False
        # Only horizontal CJK runs use this font; Latin shaping stays in the
        # source family. Avoid traversing thousands of unrelated layout rules.
        face = subset.load_font(str(font),options,lazy=True)
        try:
            subsetter = subset.Subsetter(options=options)
            subsetter.populate(unicodes=[ord(char) for char in characters])
            subsetter.subset(face)
            stream = io.BytesIO()
            face.save(stream)
            buffer = stream.getvalue()
        finally:
            face.close()
    _CJK_SUBSETS[font] = characters,buffer
    return buffer


def prepare_fonts(document):
    def strings(value):
        if isinstance(value,str):
            yield value
        elif isinstance(value,dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value,list):
            for child in value:
                yield from strings(child)
    characters = set(''.join(CJK_TEXT.findall(''.join(strings(document.get('translated_pages',[]))))))
    if characters and cjk_fonts():
        for font in cjk_fonts():
            cjk_subset(font,characters)


@lru_cache(maxsize=8)
def oblique_font(buffer):
    """Synthetic CJK oblique: transform outlines as well as font metadata."""
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    face = TTFont(io.BytesIO(buffer))
    try:
        if 'glyf' not in face:
            raise ValueError('PDF_GLYPH_MISSING')
        glyphs = face.getGlyphSet()
        transformed = {}
        for name in face.getGlyphOrder():
            recording = DecomposingRecordingPen(glyphs)
            glyphs[name].draw(recording)
            pen = TTGlyphPen(None)
            recording.replay(TransformPen(pen,(1,0,.21255656,1,0,0)))
            transformed[name] = pen.glyph()
        for name,glyph in transformed.items():
            face['glyf'][name] = glyph
        face['head'].macStyle |= 2
        face['OS/2'].fsSelection = (face['OS/2'].fsSelection | 1) & ~64
        face['post'].italicAngle = -12
        stream = io.BytesIO()
        face.save(stream)
        return stream.getvalue()
    finally:
        face.close()


def translated_html(text, archive, italic=False):
    text = html.escape(re.sub(r'\s+', ' ', text).strip())
    fonts = cjk_fonts() if CJK_TEXT.search(text) else None
    if not fonts:
        return text, ''
    css = ''
    characters = set(''.join(CJK_TEXT.findall(text)))
    for font, weight in zip(fonts,('normal','bold')):
        name = 'cjk-'+weight+('-italic' if italic else '')+'.ttf'
        # Subset once per document / glyph set instead of reparsing two large
        # system font collections for every paragraph and preview.
        buffer = cjk_subset(font,characters)
        archive.add((oblique_font(buffer) if italic else buffer,name))
        css += f'@font-face{{font-family:zhanhtran-cjk;src:url("{name}");font-weight:{weight};font-style:{"italic" if italic else "normal"};}}'
    # Only CJK runs use the common fallback. Latin text keeps its source family.
    return CJK_TEXT.sub(lambda m:'<span style="font-family:zhanhtran-cjk">'+m.group()+'</span>',text), css


def style_for(page, rect, block):
    if block.get('style'):
        return block['style']['size'], block['style'].get('bold', False)
    spans = [s for b in page.get_text("dict", clip=rect)["blocks"] if b["type"] == 0
             for line in b["lines"] for s in line["spans"] if s["text"].strip()]
    sizes = [s["size"] for s in spans if len(s["text"].strip()) > 1]
    size = max(sizes, default=9) if block.get("type") == "heading" else sorted(sizes or [9])[len(sizes or [9]) // 2]
    bold = block.get("type") == "heading" or any("-b" in s["font"] or "bold" in s["font"].lower() for s in spans)
    return size, bold


def unit(page, block, text, rect=None, image=False):
    rect = fitz.Rect(rect or block["bbox"])
    size, bold = style_for(page, rect, block)
    if image and block.get('type')!='table' and not block.get('style'):
        size = max(.1,min(48,rect.height*.72))
    return {"ids": block.get('source_ids',[block["id"]]), "rect": rect, "text": text, "size": size, "bold": bold, "image": image,
            "expandable": block.get("type") == "paragraph", 'kind': block.get('type'),
            'style': block.get('style', {}), 'body_left': block.get('body_left', rect.x0),
            'source_text': block.get('text',''),
            'indent': block.get('first_line_indent', 0), 'prefix': block.get('prefix'),
            **({'definition':block['definition']} if block.get('definition') else {}),
            'source_lines': block.get('source_lines', [])}


def filled_table_grid(drawings, xs, ys, rejection):
    """Recognise tiled, filled-rectangle borders without erasing other artwork.

    Some PDF writers paint rules as strips and separate squares at crossings.
    Table extraction snaps their coordinates (up to 3 pt), so validate the
    complete physical grid before accepting those snapped cell boundaries.
    Keep the actual rectangle widths, colours and offsets when rows grow.
    """
    bounds = fitz.Rect(xs[0],ys[0],xs[-1],ys[-1])
    selected, tiles, horizontal, vertical, corners = [],[],[],[],[]
    def fail(reason, box=None):
        rejection.update(reason=reason)
        if box is not None:
            rejection['bbox'] = list(box)
        return None
    def edge(value, edges, tolerance=3):
        matches = [i for i,v in enumerate(edges) if abs(value-v)<=tolerance]
        return matches[0] if len(matches)==1 else None
    for drawing in drawings:
        box = fitz.Rect(drawing['rect'])
        if not (box.x1>=bounds.x0-3 and box.x0<=bounds.x1+3
                and box.y1>=bounds.y0-3 and box.y0<=bounds.y1+3):
            continue
        if (not (bounds+(-3,-3,3,3)).contains(box) or drawing.get('layer')
                or drawing.get('type')!='f' or len(drawing['items'])!=1
                or drawing['items'][0][0]!='re' or drawing.get('fill') is None):
            return fail('TABLE_BORDER_UNSUPPORTED_PATH',box)
        r = fitz.Rect(drawing['items'][0][1])
        if r.is_empty:
            return fail('TABLE_BORDER_EMPTY_RECTANGLE',r)
        xi,xj,yi,yj = edge(r.x0,xs),edge(r.x1,xs),edge(r.y0,ys),edge(r.y1,ys)
        if None in (xi,xj,yi,yj):
            return fail('TABLE_BORDER_NOT_ON_GRID',r)
        tile = {'x0':r.x0,'x1':r.x1,'y0':yi,'y1':yj,'dy0':r.y0-ys[yi],
                'dy1':r.y1-ys[yj],'fill':drawing['fill'],
                'opacity':drawing.get('fill_opacity',1),'rect':r}
        if xi<xj and yi<yj:
            # Cell shading must end at cell boundaries, not arbitrary artwork.
            if any(abs(v-e)>.75 for v,e in zip(r,(xs[xi],ys[yi],xs[xj],ys[yj]))):
                return fail('TABLE_BORDER_UNSUPPORTED_FILL',r)
        else:
            if tile['opacity']!=1:
                return fail('TABLE_BORDER_TRANSLUCENT_RULE',r)
            if yi==yj and xi<xj and r.height<=2 and r.width>3*r.height:
                horizontal.append((yi,tile))
            elif xi==xj and yi<yj and r.width<=2 and r.height>3*r.width:
                vertical.append((xi,tile))
            elif xi==xj and yi==yj and max(r.width,r.height)<=2:
                corners.append((xi,yi,tile))
            else:
                return fail('TABLE_BORDER_UNSUPPORTED_RECTANGLE',r)
        tiles.append(tile)
        selected.append(drawing)
    if not horizontal or not vertical:
        return fail('TABLE_BORDER_NO_COMPLETE_GRID')
    # One physical axis per extracted grid edge; reject parallel artwork or
    # double rules rather than silently merging them into a single border.
    actual_x,actual_y = [],[]
    for edges,rules,axis,target in ((xs,vertical,0,actual_x),(ys,horizontal,1,actual_y)):
        for index in range(len(edges)):
            centres = [(t['rect'][axis]+t['rect'][axis+2])/2 for i,t in rules if i==index]
            if not centres or max(centres)-min(centres)>.1:
                return fail('TABLE_BORDER_AMBIGUOUS_AXIS')
            target.append(statistics.median(centres))
    for xi,yi,tile in corners:
        r=tile['rect']
        if (abs((r.x0+r.x1)/2-actual_x[xi])>.1
                or abs((r.y0+r.y1)/2-actual_y[yi])>.1
                or not any(i==xi and t['fill']==tile['fill'] for i,t in vertical)
                or not any(i==yi and t['fill']==tile['fill'] for i,t in horizontal)):
            return fail('TABLE_BORDER_UNRELATED_CORNER',r)
    def covers(intervals, start, end):
        cursor=start
        for a,b in sorted(intervals):
            if a>cursor+.05:
                return False
            cursor=max(cursor,b)
            if cursor>=end-.05:
                return True
        return False
    for yi in range(len(ys)):
        intervals=[(t['rect'].x0,t['rect'].x1) for i,t in horizontal if i==yi]
        intervals += [(t['rect'].x0,t['rect'].x1) for _,j,t in corners if j==yi]
        if not covers(intervals,actual_x[0],actual_x[-1]):
            return fail('TABLE_BORDER_INCOMPLETE_HORIZONTAL')
    for xi in range(len(xs)):
        intervals=[(t['rect'].y0,t['rect'].y1) for i,t in vertical if i==xi]
        intervals += [(t['rect'].y0,t['rect'].y1) for j,_,t in corners if j==xi]
        if not covers(intervals,actual_y[0],actual_y[-1]):
            return fail('TABLE_BORDER_INCOMPLETE_VERTICAL')
    padding=max([.25,*[max(bounds.x0-t['rect'].x0,bounds.y0-t['rect'].y0,
                          t['rect'].x1-bounds.x1,t['rect'].y1-bounds.y1)+.25 for t in tiles]])
    erase=bounds+(-padding,-padding,padding,padding)
    if any(d not in selected and erase.contains(fitz.Rect(d['rect'])) for d in drawings):
        return fail('TABLE_BORDER_ADJACENT_ARTWORK')
    insets=[(min(t['rect'].x0 for i,t in vertical if i==xi),
             max(t['rect'].x1 for i,t in vertical if i==xi)) for xi in range(len(xs))]
    y_ink=[(min(t['rect'].y0 for i,t in horizontal if i==yi),
            max(t['rect'].y1 for i,t in horizontal if i==yi)) for yi in range(len(ys))]
    rejection.clear()
    return {'horizontal':[],'vertical':[],'fills':[],'tiles':tiles,'x_ink':insets,
            'y_ink':y_ink,'padding':padding,'bounds':bounds}


def table_grid(drawings, xs, ys, rejection=None):
    rejection = rejection if rejection is not None else {}
    grid = stroked_table_grid(drawings,xs,ys)
    return grid if grid is not None else filled_table_grid(drawings,xs,ys,rejection)


def stroked_table_grid(drawings, xs, ys):
    """Recognise only self-contained ruled grids; retain each source pen/fill."""
    bounds = fitz.Rect(xs[0], ys[0], xs[-1], ys[-1])
    horizontal, vertical, fills, recognised = [], [], [], []
    padding = .25
    def edge(value, edges):
        return next((i for i, coordinate in enumerate(edges) if abs(value-coordinate)<.75), None)
    for drawing in drawings:
        box = fitz.Rect(drawing['rect'])
        # Intersections of zero-height / zero-width line rectangles are empty.
        if not (box.x1 >= bounds.x0-.1 and box.x0 <= bounds.x1+.1
                and box.y1 >= bounds.y0-.1 and box.y0 <= bounds.y1+.1):
            continue
        if not (bounds+(-.75,-.75,.75,.75)).contains(box) or drawing.get('layer'):
            return None
        recognised.append(drawing)
        pen = {key:drawing.get(key) for key in ('color','width','dashes','lineCap','lineJoin','stroke_opacity')}
        padding = max(padding, (pen['width'] or 0)/2+.25)
        segments = []
        for item in drawing['items']:
            if item[0]=='l':
                segments.append((item[1],item[2]))
            elif item[0]=='re':
                r = item[1]
                if drawing.get('fill') is not None:
                    xi, xj, yi, yj = edge(r.x0,xs),edge(r.x1,xs),edge(r.y0,ys),edge(r.y1,ys)
                    if None in (xi,xj,yi,yj):
                        return None
                    fills.append({'x0':xi,'x1':xj,'y0':yi,'y1':yj,
                                  'fill':drawing['fill'],'opacity':drawing.get('fill_opacity',1)})
                segments.extend([(r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)])
            else:
                return None
        if drawing.get('fill') is not None and any(i[0]!='re' for i in drawing['items']):
            return None
        if drawing.get('color') is None:
            continue
        for a,b in segments:
            if abs(a.y-b.y)<.1:
                yi, x0, x1 = edge(a.y,ys),edge(min(a.x,b.x),xs),edge(max(a.x,b.x),xs)
                if None in (yi,x0,x1):
                    return None
                horizontal.append({'edge':yi,'x0':x0,'x1':x1,'pen':pen})
            elif abs(a.x-b.x)<.1:
                xi, y0, y1 = edge(a.x,xs),edge(min(a.y,b.y),ys),edge(max(a.y,b.y),ys)
                if None in (xi,y0,y1):
                    return None
                vertical.append({'edge':xi,'y0':y0,'y1':y1,'pen':pen})
            else:
                return None
    if not horizontal and not vertical:
        return None
    erase = bounds+(-padding,-padding,padding,padding)
    # A nearby independent path must not be removed by the grid's padded
    # contained-graphics redaction, even if it is just outside the last rule.
    if any(d not in recognised and erase.contains(fitz.Rect(d['rect'])) for d in drawings):
        return None
    return {'horizontal':horizontal,'vertical':vertical,'fills':fills,'padding':padding,'bounds':bounds}


def native_table_flow_item(page, block, translated, drawings, rejection=None):
    """Build a fixed-column table flow only for complete rectangular native grids."""
    cells = block.get('cells') or []
    old_rows, new_rows = block.get('rows') or [], translated.get('rows') or []
    if (block.get('extraction') != 'pymupdf' or not cells or len(cells) != len(old_rows)
            or len(new_rows) != len(old_rows) or not cells[0]):
        return None
    column_count = len(cells[0])
    if any(len(row) != column_count or any(box is None for box in row) for row in cells):
        return None
    if any(len(row) != column_count for row in old_rows+new_rows):
        return None
    rect = fitz.Rect(block['bbox'])
    x_edges = [cells[0][0][0]]
    for box in cells[0]:
        if not box or abs(box[0]-x_edges[-1]) > .75:
            return None
        x_edges.append(box[2])
    if abs(x_edges[0]-rect.x0) > 3 or abs(x_edges[-1]-rect.x1) > 3:
        return None
    row_edges = [cells[0][0][1]]
    for ri, row in enumerate(cells):
        top = row_edges[-1]
        bottoms = []
        for ci, box in enumerate(row):
            if not box:
                return None
            if abs(box[0]-x_edges[ci]) > .75 or abs(box[2]-x_edges[ci+1]) > .75 or abs(box[1]-top) > .75:
                return None
            bottoms.append(box[3])
        if max(bottoms)-min(bottoms) > .75:
            return None
        row_edges.append(max(bottoms))
    if abs(row_edges[0]-rect.y0) > 3 or abs(row_edges[-1]-rect.y1) > 3:
        return None
    grid = table_grid(drawings, x_edges, row_edges, rejection)
    if grid is None:
        return None
    rect = grid['bounds']
    # A raster, form or annotation must never be erased/repainted as a cell.
    if any(fitz.Rect(image['bbox']).intersects(rect) for image in page.get_image_info()):
        return None
    if any(obj.rect.intersects(rect) for obj in [*(page.annots() or ()),*(page.widgets() or ())]):
        return None
    row_specs = []
    styles = block.get('cell_styles') or []
    for ri, (old, new) in enumerate(zip(old_rows, new_rows)):
        cells_for_row = []
        for ci, (before, after) in enumerate(zip(old, new)):
            if before.strip() and not after.strip():
                raise ValueError('PDF_TRANSLATION_INCOMPLETE')
            style = (styles[ri][ci] if ri < len(styles) and ci < len(styles[ri]) else None) or block.get('style', {'size':9})
            source_rect = fitz.Rect(cells[ri][ci])
            content_rect = source_rect + (1, 1, -1, -1)
            if grid.get('x_ink'):
                content_rect.x0=max(content_rect.x0,grid['x_ink'][ci][1]+.75)
                content_rect.x1=min(content_rect.x1,grid['x_ink'][ci+1][0]-.75)
            if content_rect.is_empty:
                return None
            cell_block = {'id': f"{block['id']}-r{ri}-c{ci}", 'type':'paragraph', 'bbox':list(content_rect),
                          'style':style, 'first_line_indent':0}
            cells_for_row.append({**unit(page, cell_block, after, content_rect),
                                  'source_text':before, 'table_row':ri, 'table_column':ci,
                                  'source_cell':source_rect,
                                  'border_padding':(
                                      max(1,grid['y_ink'][ri][1]-row_edges[ri]+.75),
                                      max(1,row_edges[ri+1]-grid['y_ink'][ri+1][0]+.75))
                                      if grid.get('y_ink') else (1,1)})
        row_specs.append({'source_y0':row_edges[ri], 'source_y1':row_edges[ri+1],
                          'cells':cells_for_row})
    header_rows = block.get('table_header_rows',1 if len(row_specs)>1 else 0)
    if not isinstance(header_rows,int) or not 0 <= header_rows < len(row_specs):
        return None
    return {'ids':[block['id']], 'rect':rect, 'text':'', 'size':block.get('style',{}).get('size',9),
            'bold':False, 'image':False, 'expandable':False, 'kind':'table-flow',
            'body_left':rect.x0, 'source_text':'', 'table_id':block['id'],
            'table_x_edges':x_edges, 'table_rows':row_specs,
            'table_grid':grid, 'table_header_rows':header_rows,
            'table_bbox':rect, 'style':block.get('style',{}),
            'table_changed':any(normalize(c['source_text'])!=normalize(c['text']) for row in row_specs for c in row['cells'])}


def page_units(page, before, after, report, fixed=False):
    units = []
    tables = None
    candidates = {}
    drawings = None
    for block, translated in zip(before['blocks'],after['blocks']):
        if (not fixed and block['type']=='table' and block.get('extraction')=='pymupdf'
                and not block.get('layout_runs') and not block.get('inline_math')):
            if drawings is None:
                drawings = page.get_drawings()
            rejection = {}
            candidate = native_table_flow_item(page,block,translated,drawings,rejection)
            if candidate:
                prepare_table(candidate,page,report,before['number'])
                candidates[block['id']] = candidate
            elif rejection:
                report.setdefault('table_rejections',[]).append(
                    {'page':before['number'],'table_id':block['id'],**rejection})
    table_flow = any(item['table_grows'] for item in candidates.values())
    for block, translated in zip(before["blocks"], after["blocks"]):
        if block.get('toc_rows'):
            rows = translated.get('toc_rows', [])
            if len(rows) != len(block['toc_rows']):
                raise ValueError('PDF_TRANSLATION_INCOMPLETE')
            for index, (old, new) in enumerate(zip(block['toc_rows'], rows)):
                if not new.get('text', '').strip():
                    raise ValueError('PDF_TRANSLATION_INCOMPLETE')
                # Every row participates: an unchanged row must move with a
                # preceding wrapped title rather than remain as an obstacle.
                rect = fitz.Rect(old['title_bbox'])
                if not fixed:
                    rect.x1 = old['page_bbox'][0] - old['style']['size']*.3
                units.append({**unit(page, {**block, 'id': block['id']+'-toc'+str(index), 'style': old['style']}, new['text'], rect),
                              'toc': old, 'redact_rect': rect if fixed else fitz.Rect(old['bbox']),
                              'body_left': rect.x0 if fixed else old['bbox'][0], 'indent': 0,
                              'source_baseline':old.get('baseline'), 'alignment':'left'})
            continue
        if block.get("inline_math") or (fixed and block.get('text_styles')):
            text = translated.get("translation_text", "")
            markers = re.findall(r"\[\[(M\d+)\]\]", text)
            if sorted(markers) != sorted(m["id"] for m in block.get("inline_math",[])):
                if fixed:
                    report['unplaced'].append({'page':before['number'],'ids':[block['id']],
                                               'bbox':block['bbox'],'reason':'PDF_TRANSLATION_INCOMPLETE'})
                raise ValueError("PDF_TRANSLATION_INCOMPLETE")
            units.append({**unit(page, block, text), "inline_math": block.get("inline_math",[]),
                          'text_styles':block.get('text_styles',[])})
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
            if table_flow and block['id'] in candidates:
                units.append(candidates[block['id']])
                continue
            rect = fitz.Rect(block["bbox"])
            if not block.get('cells') and tables is None:
                tables = page.find_tables().tables
            table = next((t for t in tables or [] if (fitz.Rect(t.bbox) & rect).get_area() / max(1, rect.get_area()) > .9
                          and len(t.rows) == len(block["rows"])), None)
            cells = block.get("cells") or ([row.cells for row in table.rows] if table else None)
            if not cells:
                if fixed and any(normalize(a)!=normalize(b) for row,new_row in zip(block['rows'],translated['rows']) for a,b in zip(row,new_row)):
                    raise ValueError('PDF_LAYOUT_INVALID')
                report["retained_image_tables"].append(block["id"])
                continue
            if fixed and (len(cells)!=len(block['rows']) or any(len(c)!=len(r) for c,r in zip(cells,block['rows']))):
                raise ValueError('PDF_LAYOUT_INVALID')
            for ri, (row, new_row) in enumerate(zip(block["rows"], translated["rows"])):
                for ci, (old, new) in enumerate(zip(row, new_row)):
                    if fixed and normalize(old)!=normalize(new) and not cells[ri][ci]:
                        report['unplaced'].append({'page':before['number'],'ids':[f"{block['id']}-r{ri}-c{ci}"],
                                                   'bbox':block['bbox'],'reason':'PDF_LAYOUT_INVALID'})
                        raise ValueError('PDF_LAYOUT_INVALID')
                    if normalize(old) != normalize(new) and cells[ri][ci]:
                        cell = fitz.Rect(cells[ri][ci]) + (1, 1, -1, -1)
                        if cell.is_empty:
                            raise ValueError("PDF_LAYOUT_INVALID")
                        cell_block = {**block, "id": f"{block['id']}-r{ri}-c{ci}"}
                        content = (block.get('cell_content') or [[None for _ in row] for row in block['rows']])[ri][ci] or {}
                        if fixed:
                            cell_block.update(content)
                            if content.get('prefix'):
                                cell.x0 = max(cell.x0,content.get('body_left',content['prefix']['bbox'][2]))
                        if block.get('cell_styles'):
                            cell_block['style'] = block['cell_styles'][ri][ci]
                        units.append({**unit(page, cell_block, new, cell,block.get('extraction')!='pymupdf'),
                                      **({k:content[k] for k in ('inline_math','text_styles','source_baseline','alignment') if k in content} if fixed else {}),
                                      'table_cell':True,'source_cell':fitz.Rect(cells[ri][ci]),
                                      'table_id':block['id'],'table_row':ri,'table_column':ci})
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
                    if old['text'].strip() and not new.get('text', '').strip():
                        raise ValueError('PDF_TRANSLATION_INCOMPLETE')
                    if normalize(old["text"]) != normalize(new["text"]):
                        bounds = checked_rect(old['bbox'], page.rect)
                        line = {**block, 'id': f"{block['id']}-ocr{i}", 'style': old.get('style', {})}
                        units.append({**unit(page, line, new['text'], bounds, True),
                                      'ocr_bounds': block['bbox'],
                                      'ocr_guards': [other['bbox'] for j, other in enumerate(old_lines) if j != i]})
            elif normalize(block.get("text", "")) != normalize(translated.get("text", "")):
                report["unplaced"].append(block["id"] + ":OCR_LINE_TRANSLATIONS_REQUIRED")
            continue
        field = 'translation_text' if 'translation_text' in block else 'text'
        if normalize(block.get(field, '')) == normalize(translated.get(field, '')):
            # Only movable body text needs to be repainted on a growing-table
            # page. Unchanged running labels keep their native glyphs/anchors.
            label = (block['bbox'][1]>page.rect.height*.9 and re.fullmatch(
                r'(?:Page\s+)?\d+\s*(?:of\s+\d+)?',block.get('text','').strip(),re.I))
            if not table_flow or label or block['bbox'][1]<24:
                continue
        units.append(unit(page, block, translated[field]))
    protected = [fitz.Rect(b["bbox"]) for b in before["blocks"] if b["type"] == "formula" and not b.get("layout_runs")]
    if any(r.intersects(item["rect"]) for r in protected for item in units):
        raise ValueError("PDF_PROTECTED_FORMULA_OVERLAP")
    if fixed:
        # Explicit subregions (cells, OCR, TOC and native runs) take precedence
        # over their enclosing block. Ordinary prose uses effective geometry.
        blocks = {b['id']:b for b in before['blocks']}
        for item in units:
            block = blocks.get(item['ids'][0])
            if block and block.get('fixed_bbox') and not item['image']:
                item['rect'] = checked_rect(block['fixed_bbox'],page.rect)
                item.update(source_baseline=block.get('source_baseline'),alignment=block.get('alignment','left'))
            elif item.get('native_run') or (item.get('table_cell') and not item['image']):
                metadata = fixed_parser().native_metadata(page,{'bbox':list(item['rect'])})
                item['style'] = metadata.get('style',{}) if item.get('native_run') and metadata.get('style') else {**metadata.get('style',{}),**item.get('style',{})}
                item['source_baseline'] = metadata.get('source_baseline')
                item['alignment'] = metadata.get('alignment','left')
                item['body_left'],item['indent'] = item['rect'].x0,0
                if item.get('native_run'):
                    item['size'] = metadata.get('style',{}).get('size',item['size'])
                    item['bold'] = item['style'].get('bold',False)
            elif item['image']:
                item['body_left'],item['indent'] = item['rect'].x0,0
        # Validate before any output is painted, including malformed caches.
        for item in units:
            try:
                checked_rect(list(item['rect']),page.rect)
            except ValueError as error:
                report['unplaced'].append({'page':before['number'],'ids':item['ids'],
                                           'bbox':list(item['rect']),'reason':str(error)})
                raise
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


def adds_guard_overlap(frame, guard, source_rect):
    """Compare overlap edges in points, not areas that magnify roundoff.

    PDF matrix / intersection operations can round page coordinates. A tiny
    edge drift multiplied by a label's width must not become a new collision.
    The 0.001 pt allowance only covers geometry precision; pixel-ink checks
    still apply independently and real extensions into guards remain blocked.
    """
    overlap = frame & guard
    return not overlap.is_empty and not (source_rect+(-.001,-.001,.001,.001)).contains(overlap)


def vacant_ocr_frames(original, item, units, placements, background):
    """Borrow verified blank pixels to the right/below a tight OCR glyph box.

    Do not treat the containing raster image as a solid obstacle, and do not
    assume that unrecognised pixels are blank: chart lines, symbols and any
    other ink in the added area block expansion. Unchanged OCR labels also
    remain guards. The source anchor and inferred/cached size never change.
    """
    import numpy as np
    rect, size = item['rect'], item['size']
    bounds = checked_rect(item['ocr_bounds'], original.rect)
    if not bounds.contains(rect):
        return
    outer = fitz.Rect(rect.x0, rect.y0,
                      min(bounds.x1, rect.x1 + max(rect.width, size * 4)),
                      min(bounds.y1, rect.y1 + max(rect.height, size * 2.24)))
    guards = [fitz.Rect(box) for box in item['ocr_guards']]
    guards += [other['rect'] for other in units if other is not item]
    guards += [fitz.Rect(p['bbox']) for p in placements]
    guards += [fitz.Rect(s['bbox']) for b in original.get_text('dict', clip=outer,
               flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks'] if b['type'] == 0
               for line in b['lines'] for s in line['spans']]
    guards += [a.rect for a in original.annots() or ()]
    guards += [w.rect for w in original.widgets() or ()]
    # Render once per label only after its exact source box failed to fit.
    scale = 4
    pix = original.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=outer,
                              alpha=False, colorspace=fitz.csRGB)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    # A global median comparison treats every textured map/photo pixel as ink.
    # Only high-contrast pixels that can plausibly be opaque glyph/chart strokes
    # block expansion; low-contrast image texture is not itself an obstruction.
    # This still rejects the solid, high-contrast chart marks in the synthetic
    # blocked-layout regression while allowing tiny safe margins on imagery.
    ink = np.any(np.abs(pixels.astype(np.int16) - np.array(background) * 255) > 96, axis=2)
    x = (np.arange(pix.width) + pix.x + .5) / scale
    y = (np.arange(pix.height) + pix.y + .5) / scale
    ink &= ~((y[:, None] >= rect.y0) & (y[:, None] < rect.y1)
             & (x[None, :] >= rect.x0) & (x[None, :] < rect.x1))
    def growth(limit):
        return sorted({min(limit, value) for value in (.25, .5, 1, 2, 4, 8, 16, limit) if limit > .01})
    widths, heights = growth(outer.x1 - rect.x1), growth(outer.y1 - rect.y1)
    # Prefer retaining a single source line; only wrap if the right-hand blank
    # area is insufficient. Never expand left or above the source anchor.
    additions = [(dx, 0) for dx in widths]
    additions += [(dx, dy) for dy in heights for dx in [0, *widths]]
    for dx, dy in additions:
        frame = rect + (0, 0, dx, dy)
        if any(adds_guard_overlap(frame,guard,rect) for guard in guards):
            continue
        if ink[(y >= frame.y0) & (y < frame.y1)][:, (x >= frame.x0) & (x < frame.x1)].any():
            continue
        yield frame


def place_ocr_tracking(page, item, color):
    """Fit a near-width CJK label by moving glyph origins, never scaling glyphs.

    MuPDF's HTML layout ignores CSS letter-spacing. Use the same CJK fallback
    font at the same source point size and explicitly position each glyph. Only
    a small (at most .045 em per gap) reduction is allowed, and only within the
    original OCR rectangle. Mixed scripts and missing glyphs keep normal shaping.
    """
    text = re.sub(r'\s+', ' ', item['text']).strip()
    fonts = cjk_fonts()
    if not fonts or not 2 <= len(text) <= 32 or not CJK_IDEOGRAPHS.fullmatch(text) or item.get('style', {}).get('italic'):
        return None
    font = fitz.Font(fontbuffer=cjk_subset(fonts[1 if item['bold'] else 0], set(text)))
    if not all(font.has_glyph(ord(char), fallback=False) for char in text):
        return None
    rect, size = item['rect'], item['size']
    advances = [font.glyph_advance(ord(char)) * size for char in text]
    deficit = sum(advances) - rect.width
    spacing = max(0, deficit + .01) / (len(text) - 1)
    height = (font.ascender - font.descender) * size
    if deficit <= 0 or spacing > size * .045 or height > rect.height:
        return None
    baseline = rect.y0 + (rect.height - height) / 2 + font.ascender * size
    writer = fitz.TextWriter(page.rect)
    x = rect.x0
    for char, advance in zip(text, advances):
        writer.append((x, baseline), char, font=font, fontsize=size)
        x += advance - spacing
    writer.write_text(page, color=(1, 1, 1) if color == 'white' else (0, 0, 0))
    return -spacing


def place_ocr_shaped(page, original, item, units, placements, background, color):
    """Place a short CJK or mixed-script label, checking its shaped ink.

    The HTML rectangle can include a wide empty tail below a short second
    line. That empty tail is not permission to cover neighbouring map labels,
    nor should it prevent using genuinely vacant space on the left. Keep the
    source size/anchor, shape complete text, and test glyphs before committing.
    Try the original width first, then the same bounded rightward allowance as
    rectangular growth. A fine rule can block wrapping but leave a single line
    clear; empty font bearings must not block that line on neighbouring boxes.
    """
    text = re.sub(r'\s+', ' ', item['text']).strip()
    rect, size = item['rect'],item['size']
    # Pure CJK labels need the same glyph-level clearance as mixed labels:
    # overlapping OCR bearings can forbid a blank rectangular extension.
    if not 2<=len(text)<=96 or not CJK_TEXT.search(text) or item.get('inline_math'):
        return None
    bounds = checked_rect(item['ocr_bounds'],original.rect)
    bottom = min(bounds.y1,rect.y1+max(rect.height,size*2.24))
    if not bounds.contains(rect) or bottom<=rect.y0:
        return None
    right = min(bounds.x1,rect.x1+max(rect.width,size*4))
    widths = [rect.width]
    if right>rect.x1+.01:
        widths.append(right-rect.x0)
    for width in widths:
        result = shaped_ocr_at_width(page,original,item,units,placements,background,color,
                                     text,bounds,bottom,width)
        if result is not None:
            return result
    # Leave a small amount of leading inside the same anchored frame when a
    # tall glyph is too close to the preceding OCR box. This changes neither
    # glyph scale nor the frame's left/top anchor, and must pass every guard.
    limit = min(1,size*.15)
    for padding in sorted({min(limit,value) for value in (.25,.5,limit)}):
        result = shaped_ocr_at_width(page,original,item,units,placements,background,color,
                                     text,bounds,bottom,widths[-1],padding)
        if result is not None:
            return result
    # Mixed fonts can include tall, empty ascender/descender bearings even
    # when their visible glyphs fit the original line. Only after the normal
    # placements fail, align the complete ink inside the same anchored frame.
    # This changes the baseline, not glyph scale, leading or collision rules.
    for width in widths:
        result = shaped_ocr_at_width(page,original,item,units,placements,background,color,
                                     text,bounds,bottom,width,align_ink=True)
        if result is not None:
            return result
    return None


def shaped_ocr_at_width(page, original, item, units, placements, background, color,
                        text, bounds, bottom, width, top_padding=0, align_ink=False):
    """Commit only a complete, unscaled candidate with verified ink clearance."""
    import numpy as np
    rect,size = item['rect'],item['size']
    shaped = {**item,'indent':0,'style':{**item.get('style',{}),
              'line_height':max(1.5,item.get('style',{}).get('line_height',1.12))}}
    story = story_for(shaped,original,color=color)
    probe_top = size*3
    more,_ = story.place(fitz.Rect(0,probe_top,width,probe_top+bottom-rect.y0+size*3))
    if more:
        return None
    preview_bounds = fitz.Rect(0,0,width,probe_top+bottom-rect.y0+size*6)
    with fitz.open(stream=story_pdf(story,preview_bounds),filetype='pdf') as shaped_pdf:
        lines = [line for b in shaped_pdf[0].get_text('dict')['blocks'] if b['type']==0
                 for line in b['lines'] if any(s['text'].strip() for s in line['spans'])]
        spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
        if not spans or normalize(''.join(s['text'] for s in spans))!=normalize(text):
            return None
        y0,y1 = min(s['bbox'][1] for s in spans),max(s['bbox'][3] for s in spans)
        if align_ink:
            # Measure every rendered glyph, including punctuation and accents,
            # not font metrics or a threshold that can discard faint strokes.
            # Keep half a point on both edges: low-DPI italic hinting can
            # extend beyond the high-resolution outline's antialias fringe.
            # The unchanged guard checks still have to accept this baseline.
            pix = shaped_pdf[0].get_pixmap(matrix=fitz.Matrix(8,8),alpha=True)
            alpha = np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
            rows = np.flatnonzero(alpha.any(axis=1))
            if not len(rows):
                return None
            y0 = max(0,(pix.y+int(rows[0])-4)/8)
            y1 = min(preview_bounds.y1,(pix.y+int(rows[-1])+5)/8)
        if y1-y0+top_padding>bottom-rect.y0+.001 or any(s['bbox'][0]<-.01 or s['bbox'][2]>width+.01 for s in spans):
            return None
        destination = fitz.Rect(rect.x0,rect.y0+top_padding,rect.x0+width,rect.y0+top_padding+y1-y0)
        shift = fitz.Matrix(1,0,0,1,rect.x0,rect.y0+top_padding-y0)
        line_boxes = [fitz.Rect(line['bbox'])*shift for line in lines]
        if align_ink:
            # The form clips only empty vertical bearings; report its visible
            # line extent rather than reserving discarded, unpainted margins.
            line_boxes = [box & destination for box in line_boxes]
        # Known text, annotations and earlier translations retain rectangular
        # protection even when faint. Check actual candidate ink against those
        # rectangles; font bearings contain no ink and can overlap OCR boxes.
        guards = [fitz.Rect(box) for box in item['ocr_guards']]
        guards += [other['rect'] for other in units if other is not item]
        guards += [fitz.Rect(box) for p in placements for box in p.get('occupied_bboxes',[p['bbox']])]
        guards += [fitz.Rect(s['bbox']) for b in original.get_text('dict',clip=destination,
                   flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks'] if b['type']==0
                   for line in b['lines'] for s in line['spans']]
        guards += [a.rect for a in original.annots() or ()]
        guards += [w.rect for w in original.widgets() or ()]
        conflicts = [guard for guard in guards if any(adds_guard_overlap(line+(-.25,-.25,.25,.25),guard,rect)
                     for line in line_boxes)]
        with fitz.open() as candidate:
            ink_page = candidate.new_page(width=original.rect.width,height=original.rect.height)
            ink_page.show_pdf_page(destination,shaped_pdf,0,clip=fitz.Rect(0,y0,width,y1),keep_proportion=False)
            # Rasterise both surfaces on the same coordinate grid. The candidate
            # alpha mask is glyph ink, not a rectangular/opaque background.
            matrix = fitz.Matrix(4,4)
            clip = (destination+(-.5,-.5,.5,.5)) & bounds
            mask_pix = ink_page.get_pixmap(matrix=matrix,clip=clip,alpha=True)
            old_pix = original.get_pixmap(matrix=matrix,clip=clip,alpha=False,colorspace=fitz.csRGB)
            mask = np.frombuffer(mask_pix.samples,dtype=np.uint8).reshape(mask_pix.height,mask_pix.width,4)[:,:,3]>8
            # Include a quarter-point clearance without treating the empty
            # second-line tail as occupied. Never relax the ink threshold.
            padded = np.pad(mask,1)
            mask = np.logical_or.reduce([padded[dy:dy+mask.shape[0],dx:dx+mask.shape[1]]
                                        for dy in range(3) for dx in range(3)])
            pixels = np.frombuffer(old_pix.samples,dtype=np.uint8).reshape(old_pix.height,old_pix.width,3)
            high_contrast = np.any(np.abs(pixels.astype(np.int16)-np.array(background)*255)>96,axis=2)
            x = (np.arange(old_pix.width)+old_pix.x+.5)/4
            y = (np.arange(old_pix.height)+old_pix.y+.5)/4
            outside = ~((y[:,None]>=rect.y0)&(y[:,None]<rect.y1)&(x[None,:]>=rect.x0)&(x[None,:]<rect.x1))
            for guard in conflicts:
                # Intersect pixel footprints, not just their centres, so even
                # a subpixel invisible annotation still guards its rectangle.
                blocked = ((y[:,None]+.125>guard.y0)&(y[:,None]-.125<guard.y1)
                           &(x[None,:]+.125>guard.x0)&(x[None,:]-.125<guard.x1))
                if (mask & outside & blocked).any():
                    return None
            # The PDF form is clipped to the anchored frame: no new ink can
            # borrow space left/above it. Raster rounding and the clearance
            # dilation can nevertheless sample there (e.g. a pixel centre
            # 0.05 pt left of a fractional source edge). Such pixels can be
            # the source glyph's own antialias fringe, not an obstruction in
            # added space. Match rectangular growth's sampling domain while
            # retaining the full footprint check for all known guards above.
            borrowed = outside & (x[None,:]>=rect.x0) & (y[:,None]>=rect.y0)
            if (mask & high_contrast & borrowed).any():
                return None
            page.show_pdf_page(page.rect,candidate,0)
    return {'bbox':[rect.x0,rect.y0,max(rect.x1,max(b.x1 for b in line_boxes)),max(rect.y1,destination.y1)],
            'occupied_bboxes':[list(rect),*[list(b) for b in line_boxes]],
            'line_bboxes':[list(b) for b in line_boxes],'wrapped_lines':len(lines),
            'top_padding':top_padding,**({'alignment':'glyph-ink-top'} if align_ink else {})}


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
        # Fixed artwork labels must fit at their source size; body overflow uses flow_source_page.
        archive = fitz.Archive()
        text, fonts_css = translated_html(item['text'],archive)
        content = '<div>'+text+'</div>'
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
        css = fonts_css+f"body, div {{margin:0;padding:0;}} div {{font-family:serif;color:{color};font-size:{size}pt;line-height:{line_height};font-weight:{'bold' if item['bold'] else 'normal'};}}"
        spare, scale = page.insert_htmlbox(rect, content, css=css, archive=archive,
                                           scale_low=1)
        fit = "source-box"
        tracking = None
        shaped = None
        if spare < 0:
            if item.get('ocr_bounds'):
                placed_here = [p for p in report['placements'] if p['page'] == page_number
                               and p.get('output_page', page.number + 1) == page.number + 1]
                for expanded in vacant_ocr_frames(original, item, units, placed_here, background):
                    spare, scale = page.insert_htmlbox(expanded, content, css=css, archive=archive, scale_low=1)
                    if spare >= 0:
                        rect, fit = expanded, 'ocr-vacant'
                        break
        if spare < 0 and item.get('ocr_bounds'):
            tracking = place_ocr_tracking(page, item, color)
            if tracking is not None:
                spare, scale, fit = 0, 1, 'ocr-tracking'
        if spare < 0 and item.get('ocr_bounds'):
            shaped = place_ocr_shaped(page,original,item,units,placed_here,background,color)
            if shaped is not None:
                rect = fitz.Rect(shaped['bbox'])
                spare,scale,fit = 0,1,'ocr-shaped-lines'
        if spare < 0:
            expanded = vacant_below(original, item, obstacles)
            if expanded.height > rect.height + .5:
                spare, scale = page.insert_htmlbox(expanded, content, css=css, archive=archive,
                                                    scale_low=1)
                if spare >= 0:
                    rect, fit = expanded, "vacant-below"
        if spare < 0:
            if item.get('table_cell'):
                measured = {**item,'style':{**item.get('style',{}),'family':'serif','line_height':1.12,'italic':False},'indent':0}
                probe = story_for(measured,original)
                more,filled = probe.place(fitz.Rect(0,0,rect.width,max(10000,len(item['text'])*item['size']*2)))
                table_failure(report,page_number,item,'TABLE_GRID_NOT_REFLOWABLE',
                              rect.height,fitz.Rect(filled).height if not more else None)
            report["unplaced"].append({"page": page_number, "ids": item["ids"], "bbox": list(rect),
                                       'font_size': size,
                                       "reason": 'OCR_LABEL_NO_SAFE_SPACE' if item.get('ocr_bounds') else 'TEXT_DOES_NOT_FIT'})
            raise ValueError("PDF_TEXT_DOES_NOT_FIT")
        placement = {"page": page_number, "ids": item["ids"], "bbox": list(rect),
                     "source_bbox": list(item["rect"]), "fit": fit,
                     "font_size": size * scale, "scale": scale, "image_text": item["image"]}
        if fit == 'ocr-tracking':
            placement['letter_spacing_pt'] = tracking
        if shaped is not None:
            placement.update(shaped)
        report["placements"].append(placement)
        obstacles.append(rect)


def story_for(item, original, color='black'):
    size = item['size']
    archive = fitz.Archive()
    # Transparent reservations are replaced with source vector fragments after shaping.
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0,0,1,1), True)
    pix.clear_with()
    blank = pix.tobytes('png')
    archive.add((blank, 'blank.png'))
    text, fonts_css = translated_html(item['text'],archive)
    leading = item.get('style', {}).get('line_height', 1.35)
    for token in item.get('inline_math', []):
        crop = checked_rect(token['bbox'], original.rect)
        leading = max(leading, crop.height/size+.4)
        alignment = 'middle'
        if token.get('kind') == 'symbol':
            descent = crop.y1-token.get('baseline', crop.y1-size*.25)
            alignment = f'{-descent}pt'
        image = f'<img id="{token["id"]}" src="blank.png" style="width:{crop.width}pt;height:{crop.height}pt;vertical-align:{alignment};">'
        text = text.replace('[['+token['id']+']]', image)
    family = item.get('style', {}).get('family', 'serif')
    css = (fonts_css+f'body,div {{margin:0;padding:0;}} div {{font-family:{family};font-size:{size}pt;color:{color};'
           f'line-height:{leading};font-weight:{"bold" if item["bold"] else "normal"};'
           f'font-style:{"italic" if item.get("style",{}).get("italic") else "normal"};'
           f'text-indent:{item.get("indent",0)}pt;}}')
    return fitz.Story('<div>'+text+'</div>', user_css=css, archive=archive)


def native_fragment(page, original, crop, destination):
    if fitz.Rect(crop).is_empty or fitz.Rect(destination).is_empty:
        return
    page.show_pdf_page(destination, original.parent, original.number, clip=crop, keep_proportion=False)


def story_pdf(story, bounds):
    stream = io.BytesIO()
    writer = fitz.DocumentWriter(stream)
    device = writer.begin_page(bounds)
    story.draw(device)
    writer.end_page()
    writer.close()
    return stream.getvalue()


def paint_story(page, original, story, item, frame, report=None, page_number=None):
    positions = []
    tokens = {token['id']: token for token in item.get('inline_math', [])}
    def position(value):
        rect = fitz.Rect(value.rect)
        if value.id in tokens and rect.width > .1 and rect.height > .1:
            positions.append((tokens[value.id], rect))
    story.element_positions(position)
    with fitz.open(stream=story_pdf(story, original.rect), filetype='pdf') as overlay:
        placeholder_boxes = [fitz.Rect(image['bbox']) for image in overlay[0].get_image_info()]
        if len(placeholder_boxes) != len(positions):
            raise ValueError('PDF_INLINE_SYMBOL_INVALID')
        spans = [span for block in overlay[0].get_text('dict')['blocks'] if block['type'] == 0
                 for line in block['lines'] for span in line['spans']]
        for span in spans:
            if span['bbox'][0] < frame.x0-.5 or span['bbox'][2] > frame.x1+.5:
                if report is not None:
                    report['unplaced'].append({'page':page_number,'ids':item['ids'],
                        'bbox':list(frame),'source_bbox':list(item['rect']),
                        'font_size':item['size'],'reason':'FLOW_HORIZONTAL_OVERFLOW',
                        'overflow_bbox':list(span['bbox']),
                        **({'available_height':frame.height,'required_height':item['required_height'],
                            'table_row':item['table_row'],'table_column':item['table_column']}
                           if 'table_row' in item else {})})
                raise ValueError('PDF_TEXT_DOES_NOT_FIT')
        page.show_pdf_page(page.rect, overlay, 0)
    for (token, _), rect in zip(positions, placeholder_boxes):
        native_fragment(page, original, token['bbox'], rect)
    return spans


def toc_story(item, original, page_number, report):
    """Measure the shaped baseline, not the source glyph bbox's height."""
    story = story_for(item, original)
    rect = item['rect']
    story.place(fitz.Rect(rect.x0, 0, rect.x1, original.rect.height))
    with fitz.open(stream=story_pdf(story, original.rect), filetype='pdf') as preview:
        spans = [s for b in preview[0].get_text('dict')['blocks'] if b['type'] == 0
                 for line in b['lines'] for s in line['spans']]
    if not spans:
        report['unplaced'].append({'page': page_number, 'ids': item['ids'], 'bbox': list(rect), 'reason': 'TOC_TITLE_TOO_WIDE'})
        raise ValueError('PDF_TEXT_DOES_NOT_FIT')
    baseline = spans[0]['origin'][1]
    story.reset()
    return story, baseline


def decorate_toc(page, original, item, spans, first_piece, more):
    row, rect = item['toc'], item['rect']
    box = fitz.Rect(spans[0]['bbox'])
    for span in spans[1:]:
        box |= fitz.Rect(span['bbox'])
    baseline = row.get('baseline', row['page_bbox'][3]-item['size']*.3)
    for key, draw, target_baseline in [('number_bbox', first_piece, spans[0]['origin'][1]),
                                       ('page_bbox', not more, spans[-1]['origin'][1])]:
        if draw and row.get(key):
            crop = fitz.Rect(row[key])
            shift = target_baseline-baseline
            destination = crop + (0,shift,0,shift)
            native_fragment(page, original, crop, destination)
            box |= destination
    if not more:
        last_baseline = spans[-1]['origin'][1]
        end = max(s['bbox'][2] for s in spans if abs(s['origin'][1]-last_baseline)<.1)
        font = 'tibo' if item['bold'] else 'tiro'
        dot_width = fitz.get_text_length('.', fontname=font, fontsize=item['size'])
        count = max(0, int((rect.x1-end-item['size']*.35)/dot_width))
        if count:
            # Redaction can remove a page's font resources while MuPDF still
            # caches their names. Reusing that name with insert_text then
            # produces an unresolved font that some readers silently omit.
            # An independent overlay gives the leaders their own resources.
            with fitz.open() as overlay:
                leader = overlay.new_page(width=page.rect.width,height=page.rect.height)
                leader.insert_text((rect.x1-count*dot_width,last_baseline),'.'*count,
                                   fontname=font,fontsize=item['size'],color=(0,0,0))
                page.show_pdf_page(page.rect,overlay,0)
    # Reserve all columns, even on middle fragments without a number/page label.
    box.x0, box.x1 = row['bbox'][0], max(row['bbox'][2],row['page_bbox'][2])
    return box


def body_columns(original, units, protected=()):
    native = [b for b in original.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks'] if b['type']==0]
    sizes = [s['size'] for b in native for line in b['lines'] for s in line['spans']]
    typical = statistics.median(sizes) if sizes else 11
    geometry = list(units)
    for block in native:
        text = ' '.join(s['text'] for line in block['lines'] for s in line['spans'])
        rect = fitz.Rect(block['bbox'])
        center = (rect.tl+rect.br)/2
        if not text.strip() or any(fitz.Rect(box).contains(center) for box in protected):
            continue
        if max((s['size'] for line in block['lines'] for s in line['spans']),default=0)>typical*1.25 or re.match(r'^(?:Page\s+)?\d+\s*(?:of\s+\d+)?$',text.strip(),re.I):
            continue
        geometry.append({'kind':'paragraph','body_left':block['bbox'][0],'rect':fitz.Rect(block['bbox'])})
    anchors = []
    for item in sorted(geometry,key=lambda i:i['body_left']):
        if item.get('kind') != 'paragraph' or item['rect'].y0 < 24:
            continue
        x = item['body_left']
        if not anchors or x-anchors[-1] > original.rect.width*.28:
            anchors.append(x)
    anchors = sorted(anchors) or [min((item['body_left'] for item in units), default=36)]
    # Collapse nearby paragraph/list indents without merging separate source columns.
    lefts = []
    for x in anchors:
        if not lefts or x-lefts[-1] > original.rect.width*.28:
            lefts.append(x)
    # Indented headers and table columns are not evidence of a page gutter.
    # A body line spanning a proposed gutter disproves that split. Use actual
    # line boxes, not multi-column block unions or wide display headings.
    line_boxes = []
    for block in native:
        for line in block['lines']:
            spans = [s for s in line['spans'] if s['text'].strip()]
            box = fitz.Rect(line['bbox'])
            if (spans and max(s['size'] for s in spans) <= typical*1.25
                    and not any(fitz.Rect(area).contains((box.tl+box.br)/2) for area in protected)):
                line_boxes.append(box)
    def supported_column(x):
        crossing = any(box.x0 < x-original.rect.width*.15 and box.x1 > x+typical
                       for box in line_boxes)
        if not crossing:
            return True
        # A normal-size spanning header may sit above two genuine columns.
        # Preserve that split when separate body lines run alongside each other.
        left_rows = [box for box in line_boxes if box.x0 < x-original.rect.width*.15
                     and box.x1 < x-4 and box.width > typical*3]
        right_rows = [box for box in line_boxes if abs(box.x0-x) < typical*2
                      and box.width > typical*3]
        return any(min(a.y1,b.y1)-max(a.y0,b.y0) > 0 for a in left_rows for b in right_rows)
    lefts = [x for index,x in enumerate(lefts) if index == 0 or supported_column(x)]
    margin = max(12, min(lefts[0], original.rect.width*.1))
    columns = []
    for index,left in enumerate(lefts):
        boundary = lefts[index+1]-10 if index+1 < len(lefts) else original.rect.width-margin
        right = boundary
        if index+1 < len(lefts):
            members = [item for item in geometry if left <= item['body_left'] < boundary]
            right = min(boundary, max((item['rect'].x1 for item in members), default=boundary))
        # A short final line or heading does not establish a narrow column.
        # Keep substantial source column extents, otherwise use the space up to
        # the next column / page margin so normal translated words can wrap.
        if right-left < (boundary-left)*.5:
            right = boundary
        right = max(left + 1, right)
        columns.append((left, right))
    return columns, margin


def free_frames(left, right, start, bottom, obstacles):
    y = start
    for rect in sorted((r for r in obstacles if r.x0 < right and r.x1 > left and r.y1 > start and r.y0 < bottom), key=lambda r:r.y0):
        if rect.y0-2 > y:
            yield fitz.Rect(left,y,right,min(bottom,rect.y0-2))
        y = max(y,rect.y1+2)
        if y >= bottom:
            return
    if y < bottom:
        yield fitz.Rect(left,y,right,bottom)


def table_failure(report, page_number, item, reason, available, required):
    report['unplaced'].append({'page':page_number,'ids':item['ids'],
        'bbox':list(item['rect']),'source_bbox':list(item.get('source_cell',item['rect'])),
        'font_size':item['size'],'reason':reason,
        'available_height':available,'required_height':required})
    raise ValueError('PDF_TEXT_DOES_NOT_FIT')


def prepare_table(table, original, report, page_number):
    """Measure with the same Story, padding and styles used for final output."""
    grows = False
    for ri,row in enumerate(table['table_rows']):
        row['index'] = ri
        source_height = row['source_y1']-row['source_y0']
        heights = [source_height]
        for item in row['cells']:
            required = 0
            item['padding_top'],item['padding_bottom'] = item.get('border_padding',(1,1))
            if item['text'].strip():
                story = story_for(item, original)
                height = max(source_height,item['size']*2)
                for _ in range(20):
                    probe_top = item['size']*3
                    more,filled = story.place(fitz.Rect(0,probe_top,item['rect'].width,probe_top+height))
                    if not more:
                        # HTML leading may be shorter than actual font ascent
                        # (notably mixed CJK + Charis). Reserve glyph bearings,
                        # not merely the CSS line-box height.
                        filled = fitz.Rect(filled)
                        with fitz.open(stream=story_pdf(story,fitz.Rect(0,0,item['rect'].width,
                                                probe_top+height+item['size']*3)),filetype='pdf') as preview:
                            spans = [s for b in preview[0].get_text('dict')['blocks'] if b['type']==0
                                     for line in b['lines'] for s in line['spans'] if s['text'].strip()]
                        if spans:
                            item['padding_top'] += max(0,probe_top-min(s['bbox'][1] for s in spans))
                            item['padding_bottom'] += max(0,max(s['bbox'][3] for s in spans)-filled.y1)
                        required = filled.height+item['padding_top']+item['padding_bottom']+.05
                        story.reset()
                        break
                    story.reset()
                    height *= 2
                else:
                    table_failure(report,page_number,item,'TABLE_CELL_MEASUREMENT_FAILED',source_height-2,height)
            item['required_height'] = required
            heights.append(required)
        row['height'] = max(heights)
        grows |= row['height'] > source_height+.1
    table['table_grows'] = grows and table['table_changed']


def table_stories(row, original):
    return [story_for(item,original) if item['text'].strip() else None for item in row['cells']]


def draw_table_grid(page, table, row, y, height):
    xs, grid, ri = table['table_x_edges'],table['table_grid'],row['index']
    for tile in grid.get('tiles',[]):
        a,b=tile['y0'],tile['y1']
        if a==b and a in (ri,ri+1):
            anchor=y if a==ri else y+height
            top,bottom=anchor+tile['dy0'],anchor+tile['dy1']
        elif a<=ri<b:
            top=y+(tile['dy0'] if a==ri else 0)
            bottom=y+height+(tile['dy1'] if b==ri+1 else 0)
        else:
            continue
        page.draw_rect((tile['x0'],top,tile['x1'],bottom),color=None,
                       fill=tile['fill'],fill_opacity=tile['opacity'],overlay=True)
    for fill in grid['fills']:
        if fill['y0'] <= ri < fill['y1']:
            page.draw_rect((xs[fill['x0']],y,xs[fill['x1']],y+height),
                           color=None,fill=fill['fill'],fill_opacity=fill['opacity'],overlay=True)
    def line(a,b,pen):
        page.draw_line(a,b,color=pen['color'],width=pen['width'],dashes=pen['dashes'],
                       lineCap=max(pen['lineCap'] or (0,)),lineJoin=pen['lineJoin'] or 0,
                       stroke_opacity=pen['stroke_opacity'] or 0,overlay=True)
    for stroke in grid['vertical']:
        if stroke['y0']<=ri<stroke['y1']:
            x = xs[stroke['edge']]
            line((x,y),(x,y+height),stroke['pen'])
    for stroke in grid['horizontal']:
        if stroke['edge'] in (ri,ri+1):
            yy = y if stroke['edge']==ri else y+height
            line((xs[stroke['x0']],yy),(xs[stroke['x1']],yy),stroke['pen'])


def paint_table_segment(page, original, table, row, stories, y, height, page_number, report,
                        fragment_index=0, repeated_header=False):
    xs = table['table_x_edges']
    draw_table_grid(page,table,row,y,height)
    more, progressed = False, False
    for ci,(item,story) in enumerate(zip(row['cells'],stories)):
        frame = fitz.Rect(item['rect'].x0,y+item['padding_top'],item['rect'].x1,y+height-item['padding_bottom'])
        spans, pending = [], False
        if story is not None:
            pending,filled = story.place(frame)
            if fitz.Rect(filled).height>.1:
                spans = paint_story(page,original,story,item,frame,report,page_number)
                progressed = True
            if not pending:
                stories[ci] = None
        more |= pending
        report['placements'].append({'page':page_number,'output_page':page.number+1,
            'ids':item['ids'],'bbox':[xs[ci],y,xs[ci+1],y+height],
            'source_bbox':list(item['source_cell']),'fit':'table-flow','font_size':item['size'],
            'scale':1,'image_text':False,'table_id':table['table_id'],'table_row':row['index'],
            'table_column':ci,'fragment_index':fragment_index,'repeated_header':repeated_header,
            'continued':bool(pending),'text_present':bool(spans),
            'wrapped_lines':len({round(s['origin'][1],2) for s in spans if s['text'].strip()})})
    report.setdefault('table_rows',[]).append({'table_id':table['table_id'],'source_page':page_number,
        'source_row':row['index'],'output_page':page.number+1,'fragment_index':fragment_index,
        'bbox':[xs[0],y,xs[-1],y+height],'height':height,'required_height':row['height'],
        'source_height':row['source_y1']-row['source_y0'],'continued':bool(more),
        'repeated_header':repeated_header})
    return more,progressed


def paint_table_flow(target, original, table, page_number, report, first, part, y, top, bottom,
                     obstacles, new_page):
    """Share the body page group: move whole rows; split only oversized rows."""
    rows, xs = table['table_rows'],table['table_x_edges']
    header_count = table['table_header_rows']
    header_height = sum(row['height'] for row in rows[:header_count])
    # A repeated header must leave at least one natural line for its body.
    min_line = max((c['size']*c.get('style',{}).get('line_height',1.35)+c['padding_top']+c['padding_bottom']+.1
                    for row in rows[header_count:] for c in row['cells'] if c['text'].strip()),default=2.1)
    if header_height+min_line > bottom-top:
        table_failure(report,page_number,rows[0]['cells'][0],'TABLE_HEADER_LEAVES_NO_BODY_SPACE',
                      bottom-top,header_height+min_line)
    def available():
        occupied = [fitz.Rect(p['bbox']) for p in report['placements']
                    if p['page']==page_number and p.get('output_page',first+1)==first+part+1]
        return list(free_frames(xs[0],xs[-1],y,bottom,[*(obstacles if part==0 else []),*occupied]))
    def continuation(repeat):
        nonlocal part,y
        part = new_page()
        y = top
        if repeat:
            for header in rows[:header_count]:
                stories = table_stories(header,original)
                more,_ = paint_table_segment(target[first+part],original,table,header,stories,y,
                    header['height'],page_number,report,repeated_header=True)
                if more:
                    table_failure(report,page_number,header['cells'][0],'TABLE_HEADER_INCOMPLETE',
                                  header['height'],header['height'])
                y += header['height']
    # Keep the initial header with at least the first line / complete first row.
    first_body = rows[header_count]['height'] if header_count<len(rows) else 0
    initial_need = header_height+(first_body if first_body<=bottom-top-header_height else min_line)
    frames = available()
    frame = next((f for f in frames if f.height+.01>=initial_need),None)
    if frame is None:
        continuation(False)
    else:
        y = frame.y0
    for ri,row in enumerate(rows):
        stories = table_stories(row,original)
        fragment = 0
        fresh_height = bottom-top-(header_height if ri>=header_count else 0)
        while True:
            frames = available()
            whole = next((f for f in frames if f.height+.01>=row['height']),None)
            # Normal rows are indivisible. An oversized row may use a shorter
            # frame, but only if at least one of its cells can advance a line.
            oversized = row['height']>fresh_height+.01
            frame = whole if fragment==0 and whole is not None else None
            if frame is None and oversized:
                frame = next((f for f in frames if f.height>=min_line),None)
            if frame is None:
                continuation(ri>=header_count)
                frames = available()
                frame = next((f for f in frames if f.height+.01>=row['height']),None)
                if frame is None and oversized:
                    frame = next((f for f in frames if f.height>=min_line),None)
                if frame is None:
                    table_failure(report,page_number,row['cells'][0],'TABLE_ROW_NO_SAFE_FRAME',
                                  max((f.height for f in frames),default=0),row['height'])
            # Re-place is non-consuming until draw(). Measure the remaining
            # natural lines so the last fragment does not waste a full page.
            remaining = []
            for cell,story in zip(row['cells'],stories):
                if story is not None:
                    _,filled = story.place(fitz.Rect(0,0,cell['rect'].width,row['height']))
                    remaining.append(fitz.Rect(filled).height+cell['padding_top']+cell['padding_bottom']+.05)
            needed = max([row['source_y1']-row['source_y0'],*remaining])
            height = needed if not oversized else min(frame.height,needed)
            y = frame.y0
            more,progressed = paint_table_segment(target[first+part],original,table,row,stories,y,height,
                page_number,report,fragment_index=fragment)
            y += height
            if not more:
                break
            if not oversized or not progressed or part>=1000:
                cell = next((c for c,s in zip(row['cells'],stories) if s is not None),row['cells'][0])
                table_failure(report,page_number,cell,'TABLE_CELL_NO_PROGRESS',height-2,cell['required_height']-2)
            fragment += 1
            continuation(ri>=header_count)
    return part,y


def flow_source_page(target, original, units, before, report):
    first = len(target)
    target.insert_pdf(original.parent, from_page=original.number, to_page=original.number, links=False)
    page = target[first]
    if units:
        page.clean_contents()
        for item in units:
            if item.get('kind')=='table-flow':
                padding = item['table_grid']['padding']
                page.add_redact_annot(item['rect']+(-padding,-padding,padding,padding),
                                      fill=None,cross_out=False)
        # Only supported, self-contained grid paths are removed here. No white
        # cover, image redaction or intersecting-graphics deletion is used.
        if any(item.get('kind')=='table-flow' for item in units):
            page.apply_redactions(images=0,graphics=1,text=1)
        for item in units:
            page.add_redact_annot(item.get('redact_rect',item['rect']), fill=None, cross_out=False)
        page.apply_redactions(images=0, graphics=0)
    flow, fixed = [], []
    for item in units:
        is_body = (item.get('kind') in ('paragraph','heading','footnote','table-flow') or item.get('toc')) and not (item.get('native_run') or item['image'])
        # Running heads and page labels remain anchored outside the body flow.
        if not item.get('toc') and ((item.get('kind') == 'paragraph' and item['rect'].y0 < 24) or (item['rect'].y0 > original.rect.height*.9 and re.match(r'^(?:Page\s+)?\d+\s*(?:of\s+\d+)?$',item.get('source_text','').strip(),re.I))):
            is_body = False
        (flow if is_body else fixed).append(item)
    for item in fixed:
        place_page(page, original, [item], before['number'], report)
    if not flow:
        return [{'source_page': before['number'], 'output_page': first+1, 'continuation_index': 0}]
    non_body = [b['bbox'] for b in before['blocks'] if b.get('type') in ('table','figure','formula')]
    columns, margin = body_columns(original, sorted(flow,key=lambda i:i['body_left']), non_body)
    removed = [item['rect']+(-item['table_grid']['padding'],-item['table_grid']['padding'],
                            item['table_grid']['padding'],item['table_grid']['padding'])
               if item.get('kind')=='table-flow' else item.get('redact_rect',item['rect']) for item in flow]
    def covered(rect):
        return any(r.contains(fitz.Point((rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2)) for r in removed)
    text = [fitz.Rect(s['bbox']) for b in original.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks'] if b['type']==0
            for line in b['lines'] for s in line['spans'] if not covered(fitz.Rect(s['bbox']))]
    artwork = [fitz.Rect(box) for kind,box in original.get_bboxlog() if not kind.endswith('text') and not covered(fitz.Rect(box))]
    protected = [fitz.Rect(b['bbox']) for b in before['blocks'] if not b.get('toc_rows') and not covered(fitz.Rect(b['bbox']))]
    obstacles = [*text,*artwork,*protected,*(item['rect'] for item in fixed),
                 *(a.rect for a in original.annots() or ()),*(w.rect for w in original.widgets() or ())]
    top = max(12,min(margin,original.rect.height*.06,min(i['rect'].y0 for i in flow)))
    bottom = original.rect.height-max(12,min(margin,original.rect.height*.06))
    part, cursors, previous = 0, {}, {}
    mapping = [{'source_page': before['number'], 'output_page': first+1, 'continuation_index': 0}]
    def new_page():
        nonlocal part,cursors
        part += 1
        if part>1000:
            raise ValueError('PDF_TEXT_DOES_NOT_FIT')
        target.new_page(width=original.rect.width,height=original.rect.height)
        mapping.append({'source_page':before['number'],'output_page':first+part+1,'continuation_index':part})
        cursors = {}
        return part
    for item in flow:
        column = min(range(len(columns)),key=lambda i:abs(columns[i][0]-item['body_left']))
        left, right = columns[column]
        left = item['body_left']
        source_y, source_end = item['rect'].y0, item['rect'].y1
        if item.get('kind')=='table-flow':
            # A spanning table advances every source column it covers. Preserve
            # the original source gap before and after it, including unchanged
            # following paragraphs and subsequent tables.
            touched = [i for i,(a,b) in enumerate(columns) if a<item['rect'].x1 and b>item['rect'].x0] or [column]
            y = max([source_y if part==0 else top,*[cursors.get(i,top)+
                max(0,source_y-previous.get(i,source_y)) for i in touched]])
            part,y = paint_table_flow(target,original,item,before['number'],report,first,part,y,top,bottom,obstacles,new_page)
            for i in touched:
                cursors[i],previous[i] = y,source_end
            continue
        if item.get('toc'):
            left, right = item['rect'].x0, item['rect'].x1
            story, baseline_offset = toc_story(item, original, before['number'], report)
            source_y = item['toc'].get('baseline',item['rect'].y1-item['size']*.3)-baseline_offset
            source_end = source_y+item.get('style',{}).get('line_height',1.35)*item['size']
        else:
            story = story_for(item, original)
        if item.get('kind') == 'footnote':
            # Footnotes have their own margins and often a box shorter than one
            # translated line. They must wrap / continue at the original size.
            left, right = item['body_left'], item['rect'].x1
        if not item.get('toc') and item.get('kind') == 'heading' and item['rect'].width > original.rect.width*.6:
            right = original.rect.width-margin
        gap = max(0,source_y-previous.get(column,source_y))
        y = max(source_y if part==0 else top, cursors.get(column,top)+gap)
        more, first_piece = True, True
        while more:
            progressed = False
            # Footnotes and full-width headings can cross body columns. Reserve
            # text already placed on this output page, including continuations.
            occupied = [fitz.Rect(p['bbox']) for p in report['placements']
                        if p['page']==before['number'] and p.get('output_page',first+1)==first+part+1]
            for frame in free_frames(left,right,y,bottom,[*(obstacles if part==0 else []),*occupied]):
                if frame.height < item['size']*.8:
                    continue
                more, filled = story.place(frame)
                filled = fitz.Rect(filled)
                if filled.height > .1:
                    spans = paint_story(target[first+part], original, story, item, frame, report, before['number'])
                    box = fitz.Rect(frame.x0,frame.y0,frame.x1,filled.y1)
                    if item.get('toc'):
                        box = decorate_toc(target[first+part], original, item, spans, first_piece, more)
                    if first_piece and item.get('prefix'):
                        crop = fitz.Rect(item['prefix']['bbox'])
                        destination = fitz.Rect(crop)
                        destination.y0 += frame.y0-item['rect'].y0
                        destination.y1 += frame.y0-item['rect'].y0
                        native_fragment(target[first+part],original,crop,destination)
                    report['placements'].append({'page': before['number'], 'output_page': first+part+1, 'ids': item['ids'],
                        'bbox': list(box), 'source_bbox': list(item.get('redact_rect',item['rect'])),
                        'fit': 'toc-continuation' if item.get('toc') and part else 'toc-columns' if item.get('toc') else 'continuation' if part else 'source-flow',
                        'font_size': item['size'], 'scale': 1, 'image_text': False})
                    y = filled.y1
                    first_piece, progressed = False, True
                if not more:
                    break
            if more:
                if part >= 1000 or not progressed and part>0 and y<=top+.1 and not cursors:
                    report['unplaced'].append({'page': before['number'], 'ids': item['ids'], 'reason': 'TEXT_DOES_NOT_FIT'})
                    raise ValueError('PDF_TEXT_DOES_NOT_FIT')
                new_page()
                y = top
        cursors[column] = y
        previous[column] = source_end
    return mapping


def error_code(error):
    return str(error) if isinstance(error, ValueError) and re.fullmatch(r"PDF_[A-Z_]+", str(error)) else type(error).__name__


def selected_outputs(outputs, order):
    if not isinstance(outputs, (list, tuple)) or not outputs or len(set(outputs)) != len(outputs) or any(
            kind not in ('translated', 'bilingual') for kind in outputs) or order not in ('original-left', 'translated-left'):
        raise ValueError('OUTPUT_OPTIONS_INVALID')
    return tuple(outputs)


def side_by_side(original, translated, order, page_map=None):
    bilingual = fitz.open()
    page_map = page_map or [{'source_page':i+1,'output_page':i+1,'continuation_index':0} for i in range(len(original))]
    for entry in page_map:
        i, output = entry['source_page']-1, entry['output_page']-1
        width, height = original[i].rect.width, original[i].rect.height
        page = bilingual.new_page(width=width * 2, height=height)
        for side, source in enumerate([original, translated] if order == 'original-left' else [translated, original]):
            if source is original and entry['continuation_index']:
                continue
            number = i if source is original else output
            # show_pdf_page uses unrotated coordinates. Bake the source rotation into placement.
            rotation = source[number].rotation
            source[number].set_rotation(0)
            try:
                if source[number].get_contents():
                    page.show_pdf_page(fitz.Rect(side * width, 0, (side + 1) * width, height), source, number, rotate=-rotation)
            finally:
                source[number].set_rotation(rotation)
    return bilingual


def navigation(original, target, page_map, report, bilingual=False, order='original-left'):
    # MuPDF returns link/outline coordinates in rotated display space. Layout
    # placements are unrotated; normalise both ends while remapping navigation.
    source_rotations = [page.rotation for page in original]
    target_rotations = [page.rotation for page in target]
    matrices = [page.rotation_matrix for page in original]
    widths = [page.rect.width for page in original]
    try:
        for page in original:
            page.set_rotation(0)
        for page in target:
            page.set_rotation(0)
        _navigation(original,target,page_map,report,bilingual,order,matrices,widths)
    finally:
        for page,rotation in zip(original,source_rotations):
            page.set_rotation(rotation)
        for page,rotation in zip(target,target_rotations):
            page.set_rotation(rotation)


def _navigation(original, target, page_map, report, bilingual, order, matrices, widths):
    starts = {entry['source_page']-1:entry['output_page']-1 for entry in page_map if not entry['continuation_index']}
    def destination(number, point):
        output, point = starts[number], fitz.Point(point or (0,0))
        if not bilingual:
            for placed in report['placements']:
                if placed['page']==number+1 and fitz.Rect(placed['source_bbox']).contains(point):
                    old, new = fitz.Rect(placed['source_bbox']),fitz.Rect(placed['bbox'])
                    output = placed.get('output_page',output+1)-1
                    point = fitz.Point(new.x0+point.x-old.x0,new.y0+point.y-old.y0)
                    break
        else:
            point = point*matrices[number]
            if order=='translated-left':
                point.x += widths[number]
        return output,point
    toc = []
    for level,title,page,*details in original.get_toc(simple=False):
        if page>0 and page-1 in starts:
            output, point = destination(page-1,details[0].get('to',(0,0)) if details else (0,0))
            toc.append([level,title,output+1,{'kind':fitz.LINK_GOTO,'page':output,'to':point,'zoom':0}])
        else:
            toc.append([level,title,-1])
    if toc:
        target.set_toc(toc)
    for number in range(len(original)):
        for link in original[number].get_links():
            value = {key:val for key,val in link.items() if key not in ('xref','id')}
            if value.get('kind')==fitz.LINK_GOTO:
                if value.get('page') not in starts:
                    continue
                value['page'],value['to'] = destination(value['page'],value.get('to'))
            if bilingual:
                rect = fitz.Rect(value['from'])*matrices[number]
                if order=='translated-left':
                    rect += (widths[number],0,widths[number],0)
                value['from'] = rect
            target[starts[number]].insert_link(value)


@lru_cache(maxsize=1)
def fixed_parser():
    # Gecko launches in isolated Python mode: resolve only the packaged sibling,
    # never a module from cwd / PYTHONPATH. The image renderer doesn't call this.
    spec = importlib.util.spec_from_file_location('zhanhtran_fixed_metadata',Path(__file__).with_name('local_parser.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_definition_blocks(page,before,after,parser):
    """Recover only unambiguously labelled old glossary translations locally.

    A previous paragraph merger may have sent several definition rows as one
    unit. Exact source keys can delimit that response, but missing/repeated
    keys, extra leading content or old styled reservations cannot be guessed.
    Fail with the original region rather than silently lose a definition.
    """
    planned = parser.native_definition_blocks(page,before['blocks'])
    old = {b['id']:b for b in before['blocks']}
    targets = {b['id']:b for b in after['blocks']}
    new_rows = [b for b in planned if b['id'] not in old]
    portions = {}
    def incomplete(region):
        error = ValueError('PDF_TRANSLATION_INCOMPLETE')
        error.layout_failure = {'failed_page':before['number'],'region':region}
        raise error
    def key_pattern(label):
        # Keep case/punctuation exact while accepting source word spaces that
        # a model removed (B. S. -> B.S., C of A -> CofA).
        return r'\s*'.join(re.escape(p) for p in re.split(r'\s+',label.strip()))
    def row_pattern(row,block,field):
        label = row['definition']['label']
        def bounded(value):
            return key_pattern(value)+(r'(?![A-Za-z0-9])' if value[-1].isalnum() else '')
        patterns = [bounded(label)]
        prefix = block.get('prefix')
        if (field=='translation_text' and prefix
                and parser.inside(prefix['bbox'],row['definition']['label_bbox'])):
            # 1.0.22 mistook B. in B. S. for a list marker, so its request
            # contained only S. and the value. Do not keep that residual key
            # in the newly independent value column.
            start = re.match(r'^'+key_pattern(prefix['text']),label)
            tail = label[start.end():].strip() if start else ''
            match = re.match(r'^\s*'+key_pattern(tail),block[field]) if tail else None
            if (match and re.sub(r'\s+','',block[field][match.end():])==
                    re.sub(r'\s+','',row['text'].split('\n',1)[1])):
                patterns.append(bounded(tail))
        return r'(?:'+'|'.join(patterns)+r')'
    for key,block in old.items():
        rows = [r for r in new_rows if key in r['definition']['body_source_ids']]
        if not rows:
            continue
        field = 'translation_text' if 'translation_text' in block else 'text'
        text = targets[key].get(field,'')
        if not text.strip() or block.get('inline_math') or block.get('text_styles'):
            incomplete(key)
        if len(rows)==1:
            row = rows[0]
            if parser.inside(row['definition']['label_bbox'],block['bbox']):
                label = row['definition']['label']
                source_body = row['text'].split('\n',1)[1]
                # If the value itself mentions the key, stripping a lone
                # leading key from an old response would be ambiguous.
                if re.search(r'(?<![A-Za-z0-9])'+key_pattern(label)+r'(?![A-Za-z0-9])',source_body):
                    incomplete(key)
                pattern = r'^\s*'+row_pattern(row,block,field)+r'\s*[:：]?\s*'
                text = re.sub(pattern,'',text,count=1)
            portions[(key,row['id'])] = text
        else:
            starts = []
            for row in rows:
                pattern = r'(?<![A-Za-z0-9])'+row_pattern(row,block,field)
                found = list(re.finditer(pattern,text))
                if len(found)!=1:
                    incomplete(key)
                starts.append(found[0])
            if text[:starts[0].start()].strip() or any(a.end()>b.start() for a,b in zip(starts,starts[1:])):
                incomplete(key)
            for index,(row,match) in enumerate(zip(rows,starts)):
                part = text[match.end():starts[index+1].start() if index+1<len(starts) else len(text)]
                portions[(key,row['id'])] = re.sub(r'^\s*[:：]?\s*','',part)
    result = []
    for block in planned:
        if block['id'] in old:
            result.append(targets[block['id']]);continue
        text = '\n'.join(portions[(key,block['id'])] for key in block['definition']['body_source_ids'])
        if not text.strip():
            incomplete(block['definition']['source_ids'][0])
        block.update(parser.native_metadata(page,block))
        result.append({**block,'text':text,'translation_text':text})
    return {**before,'blocks':planned},{**after,'blocks':result}


def fixed_page_metadata(page, before, after):
    """Upgrade schema-1 caches locally, without OCR or a document reparse."""
    before, after = copy.deepcopy(before), copy.deepcopy(after)
    parser = fixed_parser()
    analysis = parser.PageAnalysis(page)
    before,after = fixed_definition_blocks(analysis,before,after,parser)
    def substitute(text,tokens,region):
        for token in tokens:
            marker = '[['+token['id']+']]'
            pattern = r'\s*'.join(re.escape(v) for v in re.split(r'\s+',token['text']))
            text,count = re.subn(pattern,lambda _:marker,text,count=1)
            if count!=1:
                error = ValueError('PDF_INLINE_SYMBOL_INVALID')
                error.layout_failure = {'failed_page':before['number'],'region':region}
                raise error
        return text
    for block, target in zip(before['blocks'],after['blocks']):
        if block['type']=='table' and block.get('cells') and not block.get('layout_runs'):
            contents = block.get('cell_content') or [[None for _ in row] for row in block['rows']]
            for ri,(cells,values) in enumerate(zip(block['cells'],block['rows'])):
                for ci,(cell,value) in enumerate(zip(cells,values)):
                    if cell and value.strip() and not contents[ri][ci] and normalize(value)!=normalize(target['rows'][ri][ci]):
                        info = parser.native_cell_content(analysis,cell,value)
                        if info:
                            # Already translated old caches cannot acquire new
                            # paired style tags retroactively. Native symbols
                            # can be recovered by their exact source content.
                            info.pop('text_styles',None)
                            target['rows'][ri][ci] = substitute(target['rows'][ri][ci],info.get('inline_math',[]),f"{block['id']}-r{ri}-c{ci}")
                            contents[ri][ci] = info
            block['cell_content'] = contents
            continue
        if block['type'] in ('figure','table','formula'):
            continue
        previous_prefix = block.get('prefix')
        metadata = parser.native_metadata(analysis,block)
        for key in ('fixed_bbox','source_baseline','alignment','body_left','first_line_indent','source_lines',
                    'source_baselines','prefix','flow_blocked','source_first_word_width'):
            if key in metadata:
                block[key] = metadata[key]
        if not previous_prefix and metadata.get('prefix',{}).get('kind')=='number':
            # A pre-1.0.21 cache translated the native number as body text.
            # Preserve it once in its original gutter, not again in the body.
            label = metadata['prefix']['text']
            pattern = r'^\s*'+re.escape(label)+r'(?![\d.:])\s*'
            field = 'translation_text' if 'translation_text' in block else 'text'
            source,count = re.subn(pattern,'',block.get(field,''),count=1)
            if count!=1:
                error = ValueError('PDF_TRANSLATION_INCOMPLETE')
                error.layout_failure = {'failed_page':before['number'],'region':block['id']}
                raise error
            block['translation_text'] = source
            text = target.get(field,target.get('text',''))
            target['translation_text'] = re.sub(pattern,'',text,count=1)
        if (block.get('prefix') and not metadata.get('prefix')
                and block.get('text','').strip()==block['prefix']['text']
                and not block.get('translation_text','').strip()):
            # Older caches classified a wrapped terminal "C." as an empty
            # list. Source-confirmed content now belongs to its paragraph.
            block.pop('prefix')
            block.pop('translation_text',None)
            if not target.get('translation_text','').strip():
                target.pop('translation_text',None)
        if metadata.get('prefix'):
            # 1.0.18 could register a private-font bullet as an inline symbol
            # after removing it from translation_text. Recover only this exact
            # source-confirmed prefix case, never a lost body/math reservation.
            prefix = metadata['prefix']
            prefix_rect = checked_rect(prefix['bbox'],page.rect)
            texts = (block.get('translation_text',block.get('text','')),
                     target.get('translation_text',target.get('text','')))
            retained = []
            for token in block.get('inline_math',[]):
                marker = '[['+token['id']+']]'
                if (token.get('kind')=='symbol'
                        and re.fullmatch(r'[\ue000-\uf8ff]+',token.get('text',''))
                        and token['text']==prefix['text']
                        and all(marker not in text for text in texts)
                        and (prefix_rect+(-.1,-.1,.1,.1)).contains(checked_rect(token['bbox'],page.rect))):
                    continue
                retained.append(token)
            if 'inline_math' in block:
                block['inline_math'] = retained
        if not block.get('style'):
            block['style'] = metadata.get('style',{})
        if block.get('toc_rows') or block.get('layout_runs'):
            continue
        source_text = block.get('translation_text',metadata.get('translation_text',block.get('text','')))
        protected = parser.protect_native_symbols(analysis,block,
            {'translation_text':source_text,'inline_math':block.get('inline_math',[])})
        previous = {t['id'] for t in block.get('inline_math',[])}
        added = [t for t in protected.get('inline_math',[]) if t['id'] not in previous]
        if added:
            text = target.get('translation_text',target.get('text',''))
            target['translation_text'] = substitute(text,added,block['id'])
            block.update(protected)
    return fixed_join_paragraphs(analysis,before,after,parser)


def fixed_join_paragraphs(page,before,after,parser):
    """Recover line-wise schema-1 caches without another model request.

    Each cached line is validated before IDs are rebased, so a missing token
    or broken paired style cannot be accidentally supplied by a neighbour.
    """
    targets = {b['id']:b for b in after['blocks']}
    sources,translated = [],[]
    def incomplete(region):
        error = ValueError('PDF_TRANSLATION_INCOMPLETE')
        error.layout_failure = {'failed_page':before['number'],'region':region}
        raise error
    for members in parser.native_paragraph_groups(before['blocks']):
        if len(members)==1:
            sources.append(members[0]); translated.append(targets[members[0]['id']]); continue
        block = parser.join_native_paragraph(page,members)
        tokens,styles,old_parts,new_parts = [],[],[],[]
        structured = any(b.get('inline_math') or b.get('text_styles') for b in members)
        for old in members:
            new = targets[old['id']]
            field = 'translation_text' if 'translation_text' in old else 'text'
            source,text = old.get(field,''),new.get(field,'')
            expected = sorted(t['id'] for t in old.get('inline_math',[]))
            if old.get(field,'').strip() and not text.strip():
                incomplete(old['id'])
            if sorted(re.findall(r'\[\[(M\d+)\]\]',text))!=expected:
                incomplete(old['id'])
            stack,seen = [],[]
            for key in re.findall(r'\[\[(/?S\d+)\]\]',text):
                if key.startswith('/'):
                    if not stack or stack.pop()!=key[1:]:
                        incomplete(old['id'])
                else:
                    stack.append(key); seen.append(key)
            if stack or sorted(seen)!=sorted(s['id'] for s in old.get('text_styles',[])):
                incomplete(old['id'])
            mapping = {}
            for token in old.get('inline_math',[]):
                key = 'M'+str(len(tokens)); mapping[token['id']] = key
                tokens.append({**token,'id':key})
            for style in old.get('text_styles',[]):
                key = 'S'+str(len(styles)); mapping[style['id']] = key
                styles.append({**style,'id':key})
            def rebase(value):
                return re.sub(r'\[\[(/?)(M\d+|S\d+)\]\]',
                    lambda m:'[['+m[1]+mapping.get(m[2],m[2])+']]',value)
            old_parts.append(rebase(source)); new_parts.append(rebase(text))
        block['source_ids'] = [i for b in members for i in b.get('source_ids',[b['id']])]
        for key in ('translation_text','inline_math','text_styles'):
            block.pop(key,None)
        target = {**targets[members[0]['id']], 'text':'\n'.join(new_parts)}
        if structured or block.get('prefix'):
            block.update(translation_text='\n'.join(old_parts),inline_math=tokens,text_styles=styles)
            target['translation_text'] = target['text']
        else:
            target.pop('translation_text',None)
        sources.append(block); translated.append(target)
    return {**before,'blocks':sources},{**after,'blocks':translated}


@lru_cache(maxsize=1)
def fixed_fallback_fonts():
    files = ('C:/Windows/Fonts/times.ttf','C:/Windows/Fonts/arial.ttf',
             'C:/Windows/Fonts/seguisym.ttf','C:/Windows/Fonts/seguiemj.ttf',
             'C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/msjh.ttc','C:/Windows/Fonts/simsun.ttc',
             '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
             '/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf')
    return [(Path(p),fitz.Font(fontfile=p)) for p in files if Path(p).is_file()]


@lru_cache(maxsize=4)
def fixed_font_coverage(path):
    return fitz.Font(fontfile=str(path))


def fixed_markup(item, original, scale, archive):
    """One scale per region, but absolute indents and relative source styles."""
    text = item['text']
    # A model/cache line separator is a soft wrap inside this source
    # paragraph. Do not turn it into an artificial CJK word boundary: MuPDF
    # can otherwise move the entire next sentence to an unnecessarily short
    # new line. Real paragraph boundaries already have separate fixed units.
    text = re.sub(r'\s*[\r\n]\s*',lambda m: '' if (
        (m.start()>0 and CJK_TEXT.fullmatch(text[m.start()-1]))
        or (m.end()<len(text) and CJK_TEXT.fullmatch(text[m.end()]))) else ' ',text)
    tokens = {t['id']:t for t in item.get('inline_math',[])}
    styles = {s['id']:s for s in item.get('text_styles',[])}
    if any(not isinstance(s.get('size'),(int,float)) or not math.isfinite(s['size']) or s['size']<=0 for s in styles.values()):
        raise ValueError('PDF_LAYOUT_INVALID')
    expected = sorted(tokens)
    if sorted(re.findall(r'\[\[(M\d+)\]\]',text)) != expected:
        raise ValueError('PDF_TRANSLATION_INCOMPLETE')
    seen, stack, content, css = [], [], [], ''
    base = fitz.Font('tiro' if item.get('style',{}).get('family','serif')=='serif' else 'helv')
    fallback = {}
    def prose(value):
        nonlocal css
        current_style = styles[stack[-1]] if stack else item.get('style',{})
        markup, fonts = translated_html(value,archive,italic=current_style.get('italic',False))
        css += fonts
        # MuPDF has broad fallback, but checking explicit cmap coverage avoids
        # accepting a .notdef white box as a successful layout.
        for char in set(value):
            cjk = bool(CJK_TEXT.fullmatch(char))
            covered = (fixed_font_coverage(cjk_fonts()[0]).has_glyph(ord(char),fallback=False)
                       if cjk and cjk_fonts() else base.has_glyph(ord(char),fallback=False))
            if char.isspace() or covered:
                continue
            face = next(((p,font) for p,font in fixed_fallback_fonts() if font.has_glyph(ord(char),fallback=False)),None)
            if face is None:
                raise ValueError('PDF_GLYPH_MISSING')
            p,font = face
            name = 'fallback-'+str(fixed_fallback_fonts().index(face))+'.ttf'
            fallback.setdefault(name,(p,set()))[1].add(char)
            markup = markup.replace(html.escape(char),'<span style="font-family:'+name[:-4]+'">'+html.escape(char)+'</span>')
        return markup
    for part in re.split(r'(\[\[(?:M\d+|/?S\d+)\]\])',text):
        marker = re.fullmatch(r'\[\[(M\d+|/?S\d+)\]\]',part)
        if not marker:
            # translated_html strips boundaries; keep a single source word
            # separator around inline style / symbol reservations.
            content.append((' ' if part[:1].isspace() else '')+prose(part)+(' ' if part[-1:].isspace() else ''))
            continue
        key = marker[1]
        if key.startswith('M'):
            token = tokens.get(key)
            if token is None:
                raise ValueError('PDF_TRANSLATION_INCOMPLETE')
            crop = checked_rect(token['bbox'],original.rect)
            descent = crop.y1-token.get('baseline',crop.y1-item['size']*.25)
            content.append(f'<img id="{key}" src="blank.png" style="width:{crop.width*scale:.12g}pt;'
                           f'height:{crop.height*scale:.12g}pt;vertical-align:{-descent*scale:.12g}pt;">')
        elif key.startswith('/'):
            if not stack or stack.pop()!=key[1:]:
                raise ValueError('PDF_TRANSLATION_INCOMPLETE')
            content.append('</span>')
        else:
            if key not in styles or key in seen:
                raise ValueError('PDF_TRANSLATION_INCOMPLETE')
            seen.append(key)
            stack.append(key)
            style = styles[key]
            content.append(f'<span style="font-size:{style["size"]*scale:.12g}pt;font-family:{style.get("family","serif")};'
                           f'font-weight:{"bold" if style.get("bold") else "normal"};'
                           f'font-style:{"italic" if style.get("italic") else "normal"};">')
    if stack or sorted(seen)!=sorted(styles):
        raise ValueError('PDF_TRANSLATION_INCOMPLETE')
    for name,(p,chars) in fallback.items():
        archive.add((cjk_subset(p,chars),name))
        css += f'@font-face{{font-family:{name[:-4]};src:url("{name}");}}'
    return ''.join(content),css


def fixed_story(item, original, scale, color):
    archive = fitz.Archive()
    pix = fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,1,1),True)
    pix.clear_with()
    archive.add((pix.tobytes('png'),'blank.png'))
    markup,fonts = fixed_markup(item,original,scale,archive)
    style = item.get('style',{})
    leading = style.get('line_height',1.35)
    if not isinstance(leading,(int,float)) or not math.isfinite(leading) or leading<=0:
        raise ValueError('PDF_LAYOUT_INVALID')
    css = fonts+('body,div {margin:0;padding:0;} div {'
        f'font-size:{item["size"]*scale:.12g}pt;font-family:{style.get("family","serif")};color:{color};'
        f'line-height:{leading};text-align:{item.get("alignment","left")};'
        f'font-weight:{"bold" if item["bold"] else "normal"};font-style:{"italic" if style.get("italic") else "normal"};'
        f'text-indent:{item.get("indent",0):.12g}pt;overflow-wrap:anywhere;}}')
    return fitz.Story('<div>'+markup+'</div>',user_css=css,archive=archive)


def fixed_ink(page):
    # fz_bound_text expands the outline by exactly one point, independent of
    # font size. Undo that guard instead of fitting font ascender whitespace.
    boxes = []
    for kind,value in page.get_bboxlog():
        if kind in ('fill-text','stroke-text'):
            box = fitz.Rect(value)+(1,1,-1,-1)
            if not box.is_empty:
                boxes.append(box)
    return boxes


def fixed_candidate(original, item, scale, color):
    rect,size = item['rect'],item['size']*scale
    if not math.isfinite(size) or size<=0 or not math.isfinite(scale) or scale<=0:
        raise ValueError('PDF_LAYOUT_NUMERIC_INVALID')
    # A generous unpainted probe exposes overflow; it is never used as output
    # clipping. Only glyph outlines inside the original region can be accepted.
    margin = max(item['size']*3,4)
    left = item.get('body_left',rect.x0)-rect.x0
    if item.get('alignment','left')!='left':
        left = 0
    if not math.isfinite(left) or left<-.001 or left>=rect.width:
        raise ValueError('PDF_LAYOUT_INVALID')
    bounds = fitz.Rect(0,0,rect.width+2*margin,rect.height+2*margin+item['size']*4)
    frame = fitz.Rect(margin+left,margin,bounds.width-margin, bounds.height-margin)
    story = fixed_story(item,original,scale,color)
    more,filled = story.place(frame)
    if more:
        return None
    token_order = []
    def position(value):
        if value.id and value.id.startswith('M') and fitz.Rect(value.rect).width>0 and value.id not in token_order:
            token_order.append(value.id)
    story.element_positions(position)
    data = story_pdf(story,bounds)
    with fitz.open(stream=data,filetype='pdf') as preview:
        page = preview[0]
        spans = [s for b in page.get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
        if any(char[1]==0 and not chr(char[0]).isspace() for span in page.get_texttrace() for char in span['chars']):
            raise ValueError('PDF_GLYPH_MISSING')
        images = page.get_image_info()
        if len(images)!=len(item.get('inline_math',[])) or len(token_order)!=len(images):
            raise ValueError('PDF_INLINE_SYMBOL_INVALID')
        ink = fixed_ink(page)+[fitz.Rect(i['bbox']) for i in images]
        if not ink:
            raise ValueError('PDF_LAYOUT_NUMERIC_INVALID')
        union = fitz.Rect(ink[0])
        for box in ink[1:]:
            union |= box
        # Horizontal anchors and absolute indents stay put. Vertical baseline
        # is retained when possible, clamped only to fit actual ink, not em boxes.
        x = rect.x0-margin
        if item.get('alignment')=='center':
            x += (rect.x0+rect.x1-union.x0-union.x1)/2-x
        elif item.get('alignment')=='right':
            x = rect.x1-union.x1
        elif union.x0+x<rect.x0:
            # Italic / accented glyphs may have a negative left bearing.
            # Align their ink, rather than shrinking forever for that bearing.
            x = rect.x0-union.x0
        minimum, maximum = rect.y0-union.y0, rect.y1-union.y1
        if minimum>maximum+.0001 or union.x0+x<rect.x0-.0001 or union.x1+x>rect.x1+.0001:
            return None
        baseline = item.get('source_baseline')
        if baseline is not None and (not isinstance(baseline,(int,float)) or not math.isfinite(baseline)):
            raise ValueError('PDF_LAYOUT_INVALID')
        natural = baseline-spans[0]['origin'][1] if baseline is not None and spans else minimum
        if not math.isfinite(natural):
            raise ValueError('PDF_LAYOUT_NUMERIC_INVALID')
        y = min(max(natural,minimum),maximum)
        return {'data':data,'bounds':bounds,'offset':(x,y),'ink_bbox':list(union+(x,y,x,y)),
                'token_boxes':{key:list(fitz.Rect(i['bbox'])+(x,y,x,y)) for key,i in zip(token_order,images)},
                'scale':scale,'font_size':size}


def fit_fixed(original,item,color):
    candidate = fixed_candidate(original,item,1,color)
    if candidate is not None:
        return candidate
    high,low = 1.,.5
    while True:
        candidate = fixed_candidate(original,item,low,color)
        if candidate is not None:
            break
        high,low = low,low/2
        # This is a finite-number guard, not a font size floor.
        if low==0 or item['size']*low==0:
            raise ValueError('PDF_LAYOUT_NUMERIC_INVALID')
    while high-low>.001:
        middle = (low+high)/2
        probe = fixed_candidate(original,item,middle,color)
        if probe is None:
            high = middle
        else:
            low,candidate = middle,probe
    return candidate


def place_fixed_page(page,original,units,page_number,report):
    """PDF-only layout. No growth, flow, tracking, clipping or continuation."""
    prepared = []
    for item in units:
        try:
            if (not isinstance(item['size'],(int,float)) or not math.isfinite(item['size']) or item['size']<=0
                    or not isinstance(item.get('indent',0),(int,float)) or not math.isfinite(item.get('indent',0))
                    or item.get('alignment','left') not in ('left','center','right','justify')
                    or item.get('style',{}).get('family','serif') not in ('serif','sans-serif','monospace')):
                raise ValueError('PDF_LAYOUT_INVALID')
            color = 'black'
            background = background_for(original,item['rect']) if item['image'] else None
            if background and sum(a*b for a,b in zip(background,(.2126,.7152,.0722)))<.5:
                color = 'white'
            prepared.append((item,fit_fixed(original,item,color),background))
        except ValueError as error:
            report['unplaced'].append({'page':page_number,'ids':item['ids'],'bbox':list(item['rect']),
                                      'source_font_size':item['size'],'reason':str(error)})
            raise
    if units:
        page.clean_contents()
        for item in units:
            page.add_redact_annot(item.get('redact_rect',item['rect']),fill=None,cross_out=False)
        page.apply_redactions(images=0,graphics=0)
    for item,candidate,background in prepared:
        if background is not None:
            page.draw_rect(item['rect'],color=None,fill=background,overlay=True)
        # Native vector math is removed only from its old fragment, not from
        # neighbouring graphics, borders, captions or the rest of the page.
        for token in item.get('inline_math',[]):
            crop = checked_rect(token['bbox'],original.rect)
            page.draw_rect(crop,color=None,fill=background_for(original,crop),overlay=True)
        with fitz.open(stream=candidate['data'],filetype='pdf') as overlay:
            x,y = candidate['offset']
            destination = candidate['bounds']+(x,y,x,y)
            page.show_pdf_page(destination,overlay,0,keep_proportion=False)
        for token in item.get('inline_math',[]):
            native_fragment(page,original,token['bbox'],fitz.Rect(candidate['token_boxes'][token['id']]))
        report['placements'].append({'page':page_number,'output_page':page_number,'continuation_index':0,
            'ids':item['ids'],'bbox':list(item['rect']),'fixed_bbox':list(item['rect']),
            'source_bbox':list(item['rect']),'ink_bbox':candidate['ink_bbox'],'fit':'fixed-region',
            'source_font_size':item['size'],'font_size':candidate['font_size'],'scale':candidate['scale'],
            'alignment':item.get('alignment','left'),'image_text':item['image'],
            'source_line_count':len(item.get('source_lines',[])),
            **({'definition':item['definition']} if item.get('definition') else {}),
            **{k:item[k] for k in ('table_id','table_row','table_column') if k in item},
            **({'text_styles':[{**s,'actual_size':s['size']*candidate['scale']} for s in item['text_styles']]}
               if item.get('text_styles') else {}),
            **({'protected':[{**t,'output_bbox':candidate['token_boxes'][t['id']]} for t in item['inline_math']]}
               if item.get('inline_math') else {})})


def export_pdfs(source, document, output_dir, outputs=('translated', 'bilingual'), order='original-left', on_progress=None):
    outputs = selected_outputs(outputs, order)
    source = Path(source).resolve(strict=True)
    folder = Path(output_dir).resolve()
    if folder == source or source in folder.parents:
        raise ValueError("PDF_OUTPUT_INVALID")
    validate_document(source, document)
    prepare_fonts(document)
    folder.mkdir(parents=True, exist_ok=True)
    name = source.stem[:64].rstrip(" .")
    paths = {kind: folder / (name + '-' + kind + '.pdf') for kind in outputs}
    if source in paths.values() or any(p.exists() for p in paths.values()):
        raise ValueError("PDF_OUTPUT_EXISTS")
    temps = {key: path.with_suffix(".pdf.tmp") for key, path in paths.items()}
    report = {"source_sha256": document["source_sha256"], "mode": "fixed-regions/shrink-to-fit", "order": order,
              "placements": [], "unplaced": [], "preserved_formulas": [], "retained_image_tables": [], "positioned_image_tables": [], 'page_map': []}
    current_page = None
    try:
        with fitz.open(source) as original, fitz.open() as translated:
            if original.needs_pass or len(original) != len(document["pages"]):
                raise ValueError("PDF_SOURCE_INVALID")
            for i, (before, after) in enumerate(zip(document["pages"], document["translated_pages"])):
                current_page = i + 1
                rotation = original[i].rotation
                original[i].set_rotation(0)
                if abs(original[i].rect.width - before["width"]) > .1 or abs(original[i].rect.height - before["height"]) > .1:
                    raise ValueError("PDF_LAYOUT_INVALID")
                before,after = fixed_page_metadata(original[i],before,after)
                units = page_units(original[i], before, after, report, fixed=True)
                translated.insert_pdf(original,from_page=i,to_page=i,links=False)
                place_fixed_page(translated[i],original[i],units,i+1,report)
                report['page_map'].append({'source_page':i+1,'output_page':i+1,'continuation_index':0})
                translated[i].set_rotation(rotation)
                original[i].set_rotation(rotation)
                if on_progress:
                    on_progress(i+1,len(document['pages']))
            current_page = None
            if report["unplaced"]:
                raise ValueError("PDF_OCR_TRANSLATION_INCOMPLETE")
            translated.subset_fonts()
            translated.set_metadata(original.metadata)
            navigation(original,translated,report['page_map'],report)
            if 'translated' in outputs:
                translated.save(temps["translated"], garbage=4, deflate=True)
            if 'bilingual' in outputs:
                with side_by_side(original, translated, order, report['page_map']) as bilingual:
                    bilingual.set_metadata({**original.metadata, "title": original.metadata.get("title", name) + " - bilingual"})
                    navigation(original,bilingual,report['page_map'],report,True,order)
                    bilingual.save(temps["bilingual"], garbage=4, deflate=True)
        for key, path in paths.items():
            with fitz.open(temps[key]) as reopened:
                expected = len(report['page_map'])
                if len(reopened) != expected:
                    raise ValueError("PDF_OUTPUT_INVALID")
            temps[key].replace(path)
        report["pages"] = len(document["pages"])
        report['source_pages'] = len(document['pages'])
        report['output_pages'] = len(report['page_map'])
        report["files"] = {key: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "bytes": path.stat().st_size} for key, path in paths.items()}
        (folder / "pdf-layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**{key: str(path) for key, path in paths.items()}, "report": report}
    except Exception as error:
        for path in list(temps.values()) + list(paths.values()):
            path.unlink(missing_ok=True)
        failure = {**report, "error": error_code(error), "failed_page": current_page}
        (folder / "pdf-layout-failure.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        if isinstance(error, ValueError) and current_page and not getattr(error,'layout_failure',None):
            entry = next((p for p in report['unplaced'] if isinstance(p,dict)),{})
            error.layout_failure = {'failed_page':current_page,'region':next(iter(entry.get('ids',[])),None)}
        raise


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--input", required=True)
    cli.add_argument("--document", required=True)
    cli.add_argument("--output", required=True)
    cli.add_argument('--outputs', default='translated,bilingual')
    cli.add_argument('--order', default='original-left', choices=['original-left', 'translated-left'])
    cli.add_argument('--progress', action='store_true')
    args = cli.parse_args()
    try:
        path = Path(args.document)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("PDF_DOCUMENT_TOO_LARGE")
        callback = (lambda done,total: print('ZHANHTRAN_PROGRESS '+json.dumps({'stage':'export','done':done,'total':total}), file=sys.stderr, flush=True)) if args.progress else None
        result = export_pdfs(args.input, json.loads(path.read_text("utf-8")), args.output, args.outputs.split(','), args.order, callback)
        print(json.dumps({'ok': True, **{k: v for k, v in result.items() if k != 'report'}}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": error_code(error), **getattr(error,'layout_failure',{})}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
