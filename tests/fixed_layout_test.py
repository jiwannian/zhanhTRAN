"""1.0.18 fixed-region acceptance. Only 1-3 page synthetic source PDFs."""
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw

from tests.flow_geometry_test import ROOT, parser, renderer


def check_fixed(case, source, document, result, order='original-left'):
    report = result['report']
    case.assertEqual(report['unplaced'], [])
    case.assertEqual(report['source_pages'], report['output_pages'])
    case.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), document['source_sha256'])
    case.assertEqual(report['page_map'], [{'source_page':i+1,'output_page':i+1,'continuation_index':0}
                                         for i in range(len(document['pages']))])
    with fitz.open(source) as original, fitz.open(result['translated']) as output:
        case.assertEqual(len(output),len(original))
        for index in range(len(output)):
            case.assertEqual(output[index].rect,original[index].rect)
            case.assertEqual(output[index].rotation,original[index].rotation)
        for entry in report['placements']:
            case.assertEqual(entry['fit'],'fixed-region')
            case.assertEqual(entry['bbox'],entry['fixed_bbox'])
            case.assertGreater(entry['scale'],0)
            case.assertLessEqual(entry['scale'],1)
            case.assertAlmostEqual(entry['font_size'],entry['source_font_size']*entry['scale'],places=7)
            case.assertTrue((fitz.Rect(entry['fixed_bbox'])+(-.001,-.001,.001,.001)).contains(fitz.Rect(entry['ink_bbox'])))
        for index in range(len(output)):
            original[index].set_rotation(0)
            output[index].set_rotation(0)
            # Inspect painted pixels too. bboxlog ignores PDF form clipping,
            # so its off-crop source text is not evidence of visible overflow.
            old = original[index].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
            new = output[index].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
            a = np.frombuffer(old.samples,dtype=np.uint8).reshape(old.height,old.width,3)
            b = np.frombuffer(new.samples,dtype=np.uint8).reshape(new.height,new.width,3)
            outside = np.ones((old.height,old.width),dtype=bool)
            for p in report['placements']:
                if p['page']==index+1:
                    box = (fitz.Rect(p['fixed_bbox'])+(-.6,-.6,.6,.6))*fitz.Matrix(2,2)
                    box = box.irect & fitz.IRect(0,0,old.width,old.height)
                    outside[box.y0:box.y1,box.x0:box.x1] = False
            # Adding an ICC-backed text form can round antialias gray by two
            # level. No moved/missing/new ink or perceptible colour change.
            case.assertLessEqual(np.abs(a[outside].astype(int)-b[outside].astype(int)).max(initial=0),2)
    if 'bilingual' in result:
        with fitz.open(source) as original,fitz.open(result['bilingual']) as bilingual:
            case.assertEqual(len(original),len(bilingual))
            for i in range(len(original)):
                width,height = original[i].rect.width,original[i].rect.height
                x = 0 if order=='original-left' else width
                # Isolate the panel onto the source pixel grid. Directly
                # clipping a fractional-width right panel changes image
                # downsampling phase even after a display-matrix translation.
                with fitz.open() as panel:
                    page=panel.new_page(width=width,height=height)
                    page.show_pdf_page(page.rect,bilingual,i,clip=fitz.Rect(x,0,x+width,height),keep_proportion=False)
                    a=np.frombuffer(page.get_pixmap().samples,dtype=np.uint8).astype(int)
                b=np.frombuffer(original[i].get_pixmap().samples,dtype=np.uint8).astype(int)
                case.assertEqual(a.shape,b.shape)
                case.assertLessEqual(np.abs(a-b).max(initial=0),2)


def check_fixed_ocr(case,source,document,result):
    check_fixed(case,source,document,result,result['report'].get('order','original-left'))
    with fitz.open(result['translated']) as pdf:
        for before,after in zip(document['pages'],document['translated_pages']):
            page=pdf[before['number']-1]
            page.set_rotation(0)
            text=renderer.normalize(page.get_text())
            for block,target in zip(before['blocks'],after['blocks']):
                for i,(line,new) in enumerate(zip(block.get('layout_lines',[]),target.get('layout_lines',[]))):
                    if renderer.normalize(line['text'])==renderer.normalize(new['text']):
                        continue
                    p=next(p for p in result['report']['placements'] if p['ids']==[block['id']+'-ocr'+str(i)])
                    case.assertEqual(p['fixed_bbox'],line['bbox'])
                    case.assertIn(renderer.normalize(new['text']),text)
                    case.assertAlmostEqual(p['source_font_size'],line.get('style',{}).get('size',min(48,max(.1,(line['bbox'][3]-line['bbox'][1])*.72))),places=5)
    placements=result['report']['placements']
    return next((p for p in placements if p['ids'][0] in ('synthetic-cjk-ocr1','synthetic-precision-ocr1',
                  'synthetic-ink-ocr0','synthetic-mixed-ocr0')),placements[0])


def caption_source(path, rotation=0):
    image = Image.new('RGB',(480,140),'#ecf3fa')
    draw = ImageDraw.Draw(image)
    draw.line((16,120,460,120),fill='navy',width=2)
    draw.line((16,120,16,14),fill='navy',width=2)
    draw.line((16,100,90,45,190,75,300,30,460,24),fill='red',width=3)
    buffer = io.BytesIO(); image.save(buffer,format='PNG')
    with fitz.open() as pdf:
        page = pdf.new_page(width=560,height=720)
        page.insert_text((72,70),'Synthetic fixed-layout example',fontname='tibo',fontsize=16)
        page.insert_text((92,120),'A source paragraph above two images has its own original region.',fontname='tiro',fontsize=12)
        page.insert_text((72,135),'The continuation returns to the left margin without moving captions.',fontname='tiro',fontsize=12)
        page.insert_image((72,175,488,330),stream=buffer.getvalue())
        page.insert_text((184,350),'Figure 1. Synthetic first chart.',fontname='tiit',fontsize=9)
        page.insert_image((72,380,488,535),stream=buffer.getvalue())
        page.insert_text((180,555),'Figure 2. Synthetic second chart.',fontname='tiit',fontsize=9)
        page.insert_text((72,645),'Synthetic footnote stays below the two charts.',fontname='tiro',fontsize=8)
        page.insert_text((180,685),' ',fontsize=12)
        page.insert_text((481,685),'7',fontname='tiro',fontsize=7)
        page.set_rotation(rotation)
        pdf.save(path)


def caption_document(path,rotation=0):
    caption_source(path,rotation)
    # These chart images deliberately contain no language; actual OCR is tested
    # separately. Avoid loading an OCR model for an image preservation check.
    with patch.object(parser,'image_blocks',return_value=[]):
        document = parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    document['translated_pages'] = copy.deepcopy(document['pages'])
    for b in document['translated_pages'][0]['blocks']:
        if b['text'].startswith('A source'):
            b['text'] = '正文只在自己的原区域内换行并统一缩小，不占用图片、图注和脚注的位置。'*35
        elif b['text'].startswith('Figure 1'):
            b['text'] = '图1：第一张合成图的说明，保持原区域居中。'
        elif b['text'].startswith('Figure 2'):
            b['text'] = '图2：第二张合成图的说明，保持原区域居中。'
        elif b['text'].startswith('Synthetic footnote'):
            b['text'] = '合成脚注固定在图注下方，不移动到其他位置。'
    return document


def symbol_source(path, ascii_quotes=False):
    from fontTools.ttLib import TTFont
    source_font = next(p for p in ('C:/Windows/Fonts/times.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf') if Path(p).is_file())
    face = TTFont(source_font)
    # A deliberately nonstandard ToUnicode encoding: U+201F renders the
    # original apostrophe, reproducing the publisher-font case without a paper.
    if not ascii_quotes:
        for table in face['cmap'].tables:
            if table.isUnicode() and 0x27 in table.cmap:
                table.cmap[0x201f] = table.cmap[0x27]
    stream = io.BytesIO(); face.save(stream); face.close()
    with fitz.open() as pdf:
        page = pdf.new_page(width=540,height=400)
        page.insert_text((72,55),'Synthetic protected coordinate list.',fontsize=16)
        page.insert_font(fontname='prime',fontbuffer=stream.getvalue())
        x,y = 92,110
        page.insert_text((72,y),'-',fontname='tiro',fontsize=12)
        values = [('Latitude: ','tiro'),('28\u00b0 ','prime'),('11','tiro'),
                  (("'" if ascii_quotes else '\u201f')+' ','prime'),
                  ('51.2','tiro'),(('"' if ascii_quotes else '\u201f\u201f')+' N','prime')]
        for text,font in values:
            page.insert_text((x,y),text,fontname=font,fontsize=12)
            x += (fitz.Font(fontbuffer=stream.getvalue()) if font=='prime' else fitz.Font(font)).text_length(text,fontsize=12)
        page.insert_text((92,150),'Longitude: 112 degrees for another synthetic list entry.',fontname='tiro',fontsize=12)
        pdf.save(path)


def symbol_document(path,legacy=False,ascii_quotes=False):
    symbol_source(path,ascii_quotes)
    doc = parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    doc['translated_pages'] = copy.deepcopy(doc['pages'])
    for old,new in zip(doc['pages'][0]['blocks'],doc['translated_pages'][0]['blocks']):
        if old.get('prefix'):
            token = old['inline_math'][0]
            new['translation_text'] = '纬度说明：这是足够长的合成列表内容，用来检查坐标与正文统一缩小。'*10+'[['+token['id']+']]'
            if legacy:
                new['translation_text'] = new['translation_text'].replace('[['+token['id']+']]',token['text'])
                old.pop('inline_math')
                old['translation_text'] = old['text'].lstrip('- ').strip()
                for key in ('fixed_bbox','source_baseline','alignment'):
                    old.pop(key,None)
    return doc


class FixedLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zhanhtran-fixed-118-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_body_two_images_captions_footnote_page_number(self):
        source = self.root/'captions.pdf'
        document = caption_document(source)
        result = renderer.export_pdfs(source,document,self.root/'output')
        check_fixed(self,source,document,result)
        with fitz.open(source) as original,fitz.open(result['translated']) as output:
            for box in ((72,175,488,330),(72,380,488,535),(479,676,490,689)):
                self.assertEqual(original[0].get_pixmap(clip=box).samples,output[0].get_pixmap(clip=box).samples)
            text = renderer.normalize(output[0].get_text())
            self.assertEqual(text.count('正文只在自己的原区域内换行并统一缩小，不占用图片、图注和脚注的位置。'),35)
        body = next(p for p in result['report']['placements'] if p['source_font_size']==12)
        self.assertLess(body['scale'],.5)
        captions = [p for p in result['report']['placements'] if p['source_font_size']==9]
        self.assertEqual(len(captions),2)
        self.assertTrue(all(p['alignment']=='center' for p in captions))
        for caption in captions:
            self.assertAlmostEqual((caption['ink_bbox'][0]+caption['ink_bbox'][2])/2,
                                  (caption['fixed_bbox'][0]+caption['fixed_bbox'][2])/2,delta=.5)

    def test_rotated_two_orders_have_no_continuations(self):
        for rotation in (90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    source = self.root/f'captions-{rotation}-{order}.pdf'
                    document = caption_document(source,rotation)
                    result = renderer.export_pdfs(source,document,self.root/source.stem,order=order)
                    check_fixed(self,source,document,result,order)

    def test_list_coordinate_special_quote_multifont_and_old_cache(self):
        for legacy in (False,True):
            source = self.root/f'symbol-{legacy}.pdf'
            document = symbol_document(source,legacy)
            block = next(b for b in document['pages'][0]['blocks'] if b.get('prefix'))
            result = renderer.export_pdfs(source,document,self.root/source.stem)
            check_fixed(self,source,document,result)
            placement = next(p for p in result['report']['placements'] if p['ids']==[block['id']])
            self.assertLess(placement['scale'],1)
            token = placement['protected'][0]
            self.assertEqual(token['text'].count('\u201f'),3)
            self.assertAlmostEqual(fitz.Rect(token['output_bbox']).width/fitz.Rect(token['bbox']).width,placement['scale'],places=4)
            with fitz.open(source) as original,fitz.open(result['translated']) as output:
                a=np.frombuffer(original[0].get_pixmap(clip=block['prefix']['bbox']).samples,dtype=np.uint8).astype(int)
                b=np.frombuffer(output[0].get_pixmap(clip=block['prefix']['bbox']).samples,dtype=np.uint8).astype(int)
                self.assertLessEqual(np.abs(a-b).max(initial=0),1)
                text = output[0].get_text()
                self.assertEqual(text.count('\u201f'),3)
                self.assertNotIn('[[M',text)
                self.assertNotIn('\ufffd',text)
                for font in output[0].get_texttrace():
                    self.assertFalse(any(c[1]==0 for c in font['chars'] if not chr(c[0]).isspace()))

    def test_ascii_coordinate_quotes_protect_seconds_and_hemisphere(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                source = self.root/f'ascii-symbol-{legacy}.pdf'
                document = symbol_document(source,legacy=legacy,ascii_quotes=True)
                result = renderer.export_pdfs(source,document,self.root/source.stem)
                check_fixed(self,source,document,result)
                protected = [t for p in result['report']['placements'] for t in p.get('protected',[])]
                self.assertEqual(len(protected),1)
                self.assertEqual(renderer.normalize(protected[0]['text']),renderer.normalize('28\u00b0 11\' 51.2" N'))
                with fitz.open(result['translated']) as output:
                    text = output[0].get_text()
                    self.assertIn('51.2',text)
                    self.assertIn('"n',renderer.normalize(text))
                    self.assertNotIn('[[M',text)
                    self.assertFalse(any(c[1]==0 for run in output[0].get_texttrace()
                                         for c in run['chars'] if not chr(c[0]).isspace()))

    def test_no_shrink_light_shrink_and_maximum_ratio(self):
        source = self.root/'sizing.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=400)
            page.insert_text((40,60),'A source sentence has ample room here.',fontsize=12)
            pdf.save(source)
        with fitz.open(source) as pdf:
            original = pdf[0]
            rect = fitz.Rect(40,80,220,110)
            item = renderer.unit(original,{'id':'size','bbox':list(rect),'style':{'size':12}},'Short')
            self.assertEqual(renderer.fit_fixed(original,item,'black')['scale'],1)
            # Determine a lightly overflowing width without assuming a host font.
            item['text']='A synthetic fixed-region line'
            full = renderer.fixed_candidate(original,item,1,'black')
            self.assertIsNotNone(full)
            width = fitz.Rect(full['ink_bbox']).width
            item['rect'] = fitz.Rect(40,80,40+width*.94,94)
            item['body_left'] = 40
            best = renderer.fit_fixed(original,item,'black')
            self.assertGreater(best['scale'],.8)
            self.assertLess(best['scale'],1)
            self.assertIsNone(renderer.fixed_candidate(original,item,min(1,best['scale']+.0011),'black'))
            item['text']='合成超长区域内缩字。'*900
            best = renderer.fit_fixed(original,item,'black')
            self.assertLess(best['scale'],.05)
            self.assertGreater(best['scale'],0)

    def test_mixed_styles_share_scale_and_keep_relative_sizes(self):
        source = self.root/'styles.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=500,height=300)
            x=50
            for text,font,size in [('Plain source words ','tiro',12),('bold emphasis ','tibo',12),
                                   ('italic small words','tiit',9)]:
                page.insert_text((x,100),text,fontname=font,fontsize=size)
                x += fitz.get_text_length(text,fontname=font,fontsize=size)
            pdf.save(source)
        document = parser.parse_document(source,self.root/'parsed')
        block = document['pages'][0]['blocks'][0]
        self.assertTrue(block.get('text_styles'))
        document['translated_pages'] = copy.deepcopy(document['pages'])
        target = document['translated_pages'][0]['blocks'][0]
        target['translation_text'] = block['translation_text'].replace('Plain source words','这是统一段落的较长普通正文'*8).replace('bold emphasis','粗体强调'*8).replace('italic small words','斜体小字'*8)
        result = renderer.export_pdfs(source,document,self.root/'output')
        check_fixed(self,source,document,result)
        ratio = result['report']['placements'][0]['scale']
        self.assertLess(ratio,1)
        with fitz.open(result['translated']) as pdf:
            spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
            bold = next(s for s in spans if '粗体' in s['text'])
            italic = next(s for s in spans if '斜体' in s['text'])
            self.assertTrue(bold['flags']&16)
            self.assertTrue(italic['flags']&2)
            self.assertAlmostEqual(italic['size']/bold['size'],.75,places=4)

    def test_missing_symbols_styles_and_invalid_numbers_are_atomic_failures(self):
        for kind in ('symbol','bad-size','missing-glyph','bad-coordinate'):
            source = self.root/f'bad-{kind}.pdf'
            document = symbol_document(source)
            old = next(b for b in document['pages'][0]['blocks'] if b.get('prefix'))
            target = next(b for b in document['translated_pages'][0]['blocks'] if b['id']==old['id'])
            if kind=='symbol':
                target['translation_text']=target['translation_text'].replace('[[M0]]','')
            elif kind=='bad-size':
                old['style']['size']=float('nan')
            elif kind=='missing-glyph':
                target['translation_text']+='\U0010fffd'
            else:
                old['inline_math'][0]['bbox'][0]=-1
            out = self.root/source.stem
            with self.assertRaises(ValueError):
                renderer.export_pdfs(source,document,out)
            self.assertFalse(list(out.glob('*.pdf')))
            self.assertFalse(list(out.glob('*.tmp')))
            self.assertTrue((out/'pdf-layout-failure.json').is_file())

    def test_native_table_protects_coordinates_and_mixed_cell_styles(self):
        source=self.root/'table-styles.pdf'
        with fitz.open() as pdf:
            page=pdf.new_page(width=540,height=400)
            page.insert_text((72,50),'Synthetic mixed table cells and coordinates.',fontsize=16)
            for x in (72,200,480):page.draw_line((x,90),(x,186))
            for y in (90,122,154,186):page.draw_line((72,y),(480,y))
            for y,a,b in ((110,'Label','Description'),(142,'Styled',''),(174,'Coordinate',"28\u00b0 11' 51'' N")):
                page.insert_text((76,y),a,fontname='tiro',fontsize=12)
                if b:page.insert_text((204,y),b,fontname='tiro',fontsize=12)
            x=204
            for value,font,size in [('Plain ','tiro',12),('bold ','tibo',12),('small italic','tiit',9)]:
                page.insert_text((x,142),value,fontname=font,fontsize=size)
                x+=fitz.get_text_length(value,fontname=font,fontsize=size)
            pdf.save(source)
        doc=parser.parse_document(source,self.root/'parsed')
        table=next(b for b in doc['pages'][0]['blocks'] if b['type']=='table')
        self.assertTrue(table['cell_content'][1][1]['text_styles'])
        self.assertEqual(len(table['cell_content'][2][1]['inline_math']),1)
        doc['translated_pages']=copy.deepcopy(doc['pages'])
        target=next(b for b in doc['translated_pages'][0]['blocks'] if b['type']=='table')
        target['rows'][1][1]=table['cell_content'][1][1]['translation_text'].replace('Plain','合成普通文本'*15).replace('bold','粗体'*15).replace('small italic','小号斜体'*15)
        target['rows'][2][1]='坐标内容保持原字形：'*15+'[[M0]]'
        result=renderer.export_pdfs(source,doc,self.root/'out')
        check_fixed(self,source,doc,result)
        cells=[p for p in result['report']['placements'] if p.get('table_id')==table['id']]
        self.assertEqual(len(cells),2)
        mixed=next(p for p in cells if p['table_row']==1)
        self.assertLess(mixed['scale'],1)
        self.assertTrue(mixed['text_styles'])
        with fitz.open(result['translated']) as pdf:
            text=renderer.normalize(pdf[0].get_text())
            self.assertNotIn('[[',text)
            self.assertIn("28\u00b011'51''n",text)
            self.assertEqual(text.count('小号斜体'),15)


if __name__=='__main__':
    unittest.main()
