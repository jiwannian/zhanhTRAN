"""Fractional OCR coordinates, using only synthetic words/pixels and one page."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz
from PIL import Image,ImageDraw,ImageFont

from tests import ocr_shaped_test as shaped

renderer=shaped.renderer
BOX=[262.17444154418735,429.51939797323257,301.7668187027374,438.72084040407276]
ABOVE=[262.17444154418735,422.87391177318136,363.50408478047655,431.5641629578637]
TEXT='（AB/CD/合成）'


def fixture(path,rotation=0,background='white',translated_neighbor=True,blocked=False,
            annotation=False,raster=False,margin_rule=True,text=TEXT):
    bounds=[220,410,380,475]
    box,above=list(BOX),list(ABOVE)
    width,height=595.32,842.04
    if raster:
        width,height=600,842
        bounds=[0,0,width,height]
    region=fitz.Rect(bounds)
    scale=4 if not raster else 1
    light=background=='white';paper=(248,248,248) if light else (16,16,16)
    ink='black' if light else 'white'
    image=Image.new('RGB',(round(region.width*scale),round(region.height*scale)),paper)
    draw=ImageDraw.Draw(image)
    font_path=renderer.cjk_fonts()[0] if renderer.cjk_fonts() else 'C:/Windows/Fonts/arial.ttf'
    font=ImageFont.truetype(str(font_path),max(4,round(4.3*scale)))
    def xy(x,y):return ((x-region.x0)*scale,(y-region.y0)*scale)
    # The adjacent line's OCR box overlaps the target's top edge. Visible ink
    # is separate, as happens with generous character boxes in scanned tables.
    draw.text(xy(above[0]+1,above[1]),'SYNTHETIC LINE ABOVE',font=font,fill=ink)
    draw.text(xy(box[0]+1,box[1]),'(AB/CD/Test)',font=font,fill=ink)
    draw.text(xy(box[0]+1,461.2),'SYNTHETIC NEXT ROW',font=font,fill=ink)
    for a,b in [((260.3,412),(260.3,473)),((375,412),(375,473)),((260.3,452),(375,452))]:
        draw.line((xy(*a),xy(*b)),fill=ink,width=max(1,round(.4*scale)))
    # Fine raster rule in the added rectangle's empty left margin. It blocks
    # opaque rectangular growth but does not touch the shaped glyphs.
    if margin_rule:
        draw.line((xy(box[0]+.3,box[3]+.5),xy(box[0]+.3,451)),fill=ink,width=1)
    if blocked:
        draw.rectangle((*xy(box[0],box[3]),*xy(box[2],box[3]+15)),fill=ink)
        draw.rectangle((*xy(box[2],box[1]),*xy(box[2]+40,box[3]+15)),fill=ink)
    lines=[{'text':'SYNTHETIC LINE ABOVE','bbox':above,'style':{
                'size':(above[3]-above[1])*.72,'bold':False,'family':'serif'}},
           {'text':'(AB/CD/Test)','bbox':box,'style':{
                'size':(box[3]-box[1])*.72,'bold':False,'family':'serif'}},
           {'text':'SYNTHETIC NEXT ROW','bbox':[263.5,461.2,371,468.4],
                'style':{'size':5.184,'bold':False,'family':'serif'}}]
    block={'id':'synthetic-precision','type':'figure','bbox':bounds,'text':'Synthetic table labels','layout_lines':lines}
    pages=[{'number':1,'width':width,'height':height,'blocks':[block]}]
    translated=copy.deepcopy(pages)
    translated[0]['blocks'][0]['layout_lines'][1]['text']=text
    if translated_neighbor:
        translated[0]['blocks'][0]['layout_lines'][0]['text']='相邻合成文字'
    if raster:
        image.save(path)
    else:
        stream=io.BytesIO();image.save(stream,format='PNG')
        with fitz.open() as pdf:
            page=pdf.new_page(width=width,height=height)
            page.insert_text((50,60),'Synthetic OCR coordinate precision.',fontsize=12)
            page.insert_image(region,stream=stream.getvalue(),keep_proportion=False)
            if annotation:
                # Invisible annotations remain protected even without ink.
                a=page.add_rect_annot((box[0],box[1],box[2]+35,box[3]+13))
                a.set_opacity(0);a.update()
            page.set_rotation(rotation);pdf.save(path)
    return {'schema':1,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages':pages,'translated_pages':translated}


class OCRPrecisionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='zhanhtran-precision-112-')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_fractional_overlap_renders_full_label_with_unchanged_or_translated_neighbor(self):
        for neighbor in (False,True):
            with self.subTest(translated_neighbor=neighbor):
                source=self.root/f'{neighbor}.pdf';doc=fixture(source,translated_neighbor=neighbor)
                result=renderer.export_pdfs(source,doc,self.root/str(neighbor))
                self.check_output(source,doc,result)

    def test_coordinate_tolerance_does_not_scale_with_label_width(self):
        for width in (10,100,500):
            source=fitz.Rect(200.1234567,429.51939797323257,200.1234567+width,440)
            candidate=source*fitz.Matrix(1,0,0,1,0,0)
            guard=fitz.Rect(190,425,800,432)
            self.assertFalse(renderer.adds_guard_overlap(candidate,guard,source))
            self.assertTrue(renderer.adds_guard_overlap(source+(0,-.002,0,0),guard,source))
            self.assertTrue(renderer.adds_guard_overlap(source+(0,0,.002,0),guard,source))
        source=fitz.Rect(100,100,200,110)
        self.assertTrue(renderer.adds_guard_overlap(fitz.Rect(100,110,110,120),
                                                   fitz.Rect(100,110.002,100.002,110.004),source))

    def test_different_mixed_glyphs_keep_complete_content_and_source_size(self):
        for index,word in enumerate(('开关','设备','控制','部件','系统','示例','转子','档位','图例')):
            with self.subTest(word=word):
                source=self.root/f'glyph-{index}.pdf'
                doc=fixture(source,text=f'（AB/CD/{word}）')
                result=renderer.export_pdfs(source,doc,self.root/f'glyph-{index}')
                self.check_output(source,doc,result)

    def test_rotations_backgrounds_and_bilingual_orders(self):
        for rotation in (0,90):
            for background in ('white','black'):
                for order in ('original-left','translated-left'):
                    with self.subTest(rotation=rotation,background=background,order=order):
                        folder=self.root/f'{rotation}-{background}-{order}';folder.mkdir()
                        source=folder/'source.pdf';doc=fixture(source,rotation=rotation,background=background)
                        result=renderer.export_pdfs(source,doc,folder/'out',order=order)
                        self.check_output(source,doc,result)
                        with fitz.open(source) as original,fitz.open(result['bilingual']) as dual:
                            width,height=original[0].rect.width,original[0].rect.height
                            x=width if order=='original-left' else 0
                            self.assertIn(renderer.normalize(TEXT),renderer.normalize(dual[0].get_text(clip=(x,0,x+width,height))))

    def test_fixed_region_preserves_external_ink_and_annotation(self):
        for name,options in [('ink',{'blocked':True}),('annotation',{'annotation':True})]:
            source=self.root/(name+'.pdf');doc=fixture(source,**options)
            result=renderer.export_pdfs(source,doc,self.root/name)
            self.check_output(source,doc,result)

    def test_prior_placed_translation_still_blocks_added_lines(self):
        source=self.root/'prior.pdf';doc=fixture(source)
        with fitz.open(source) as original,fitz.open() as candidate:
            page=candidate.new_page(width=595.32,height=842.04)
            units=renderer.page_units(original[0],doc['pages'][0],doc['translated_pages'][0],{})
            item=next(i for i in units if i['ids']==['synthetic-precision-ocr1'])
            placed=[{'bbox':[BOX[0],BOX[1],BOX[2]+35,BOX[3]+12]}]
            self.assertIsNone(renderer.place_ocr_shaped(page,original[0],item,units,placed,(.97,.97,.97),'black'))
            self.assertFalse(page.get_contents())

    def test_faint_label_and_subpixel_invisible_annotation_remain_protected(self):
        import numpy as np
        source=self.root/'guards.pdf';doc=fixture(source)
        with fitz.open(source) as original,fitz.open() as candidate:
            page=candidate.new_page(width=595.32,height=842.04)
            units=renderer.page_units(original[0],doc['pages'][0],doc['translated_pages'][0],{})
            item=next(i for i in units if i['ids']==['synthetic-precision-ocr1'])
            result=renderer.place_ocr_shaped(page,original[0],item,units,[],(.97,.97,.97),'black')
            self.assertIsNotNone(result)
            pix=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=result['bbox'],alpha=True)
            alpha=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
            ys,xs=np.where(alpha>128)
            x,y=next(((x+pix.x+.5)/4,(y+pix.y+.5)/4) for x,y in zip(xs,ys) if (x+pix.x+.5)/4>BOX[2]+1)
            empty=candidate.new_page(width=595.32,height=842.04)
            # The OCR label is known only from metadata; no high-contrast
            # source pixels can protect it. Its rectangle must still block ink.
            guarded={**item,'ocr_guards':[*item['ocr_guards'],[x-.1,y-.1,x+.1,y+1.2]]}
            self.assertIsNone(renderer.place_ocr_shaped(empty,original[0],guarded,[],[],(.97,.97,.97),'black'))
            a=original[0].add_rect_annot((x-.001,y-1,x+.001,y+3));a.set_opacity(0);a.update()
            self.assertIsNone(renderer.place_ocr_shaped(empty,original[0],item,units,[],(.97,.97,.97),'black'))
            self.assertFalse(empty.get_contents())

    def test_roundoff_does_not_block_original_width_shaping(self):
        source=self.root/'original-width.pdf';doc=fixture(source,margin_rule=False)
        with fitz.open(source) as original,fitz.open() as candidate:
            page=candidate.new_page(width=595.32,height=842.04)
            units=renderer.page_units(original[0],doc['pages'][0],doc['translated_pages'][0],{})
            item=next(i for i in units if i['ids']==['synthetic-precision-ocr1'])
            result=renderer.place_ocr_shaped(page,original[0],item,units,[],(.97,.97,.97),'black')
            self.assertIsNotNone(result)
            self.assertEqual(result['wrapped_lines'],2)
            self.assertEqual(result['bbox'][:3],BOX[:3])
            self.assertEqual(renderer.normalize(page.get_text()),renderer.normalize(TEXT))

    def test_raster_export_keeps_full_label_and_untouched_neighbors(self):
        source=self.root/'source.png';doc=fixture(source,raster=True)
        result=shaped.image_renderer.export_images(source,doc,self.root/'image')
        placed=next(p for p in result['report']['placements'] if p['ids']==['synthetic-precision-ocr1'])
        self.assertEqual(placed['fit'],'ocr-shaped-lines')
        self.assertGreater(placed['bbox'][2],BOX[2])
        self.assertEqual(placed['scale'],1)
        with Image.open(source) as original,Image.open(result['translated']) as translated:
            self.assertEqual(original.size,translated.size)
            for area in ((259,411,261,474),(374,411,376,474),(260,451,376,453),(262,460,372,470)):
                self.assertEqual(original.crop(area).convert('RGB').tobytes(),translated.crop(area).convert('RGB').tobytes())
            added=(302,430,313,439)
            self.assertNotEqual(original.crop(added).convert('RGB').tobytes(),translated.crop(added).convert('RGB').tobytes())

    def test_cli_single_response_and_progress(self):
        source=self.root/'cli.pdf';doc=fixture(source)
        document=self.root/'translated.json';document.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
        run=subprocess.run([sys.executable,'-I',str(shaped.ROOT/'src/local_pdf.py'),'--input',str(source),
                            '--document',str(document),'--output',str(self.root/'out'),'--progress'],
                           capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(run.returncode,0,run.stderr+run.stdout)
        self.assertEqual(len(run.stdout.strip().splitlines()),1)
        result=json.loads(run.stdout)
        self.assertTrue(result['ok'])
        self.assertEqual([json.loads(s.removeprefix('ZHANHTRAN_PROGRESS ')) for s in run.stderr.splitlines()],
                         [{'stage':'export','done':1,'total':1}])
        report=json.loads((self.root/'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(source,doc,{**result,'report':report})


if __name__=='__main__':
    unittest.main()
