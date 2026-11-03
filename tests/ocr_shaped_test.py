"""Mixed-script OCR labels: shape-only growth, never user-paper fixtures."""
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pymupdf as fitz
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('shaped_renderer',ROOT/'src/local_pdf.py')
renderer=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(renderer)
IMAGE_SPEC=importlib.util.spec_from_file_location('shaped_image',ROOT/'src/local_image.py')
image_renderer=importlib.util.module_from_spec(IMAGE_SPEC);IMAGE_SPEC.loader.exec_module(image_renderer)

# Diagnostic geometry only. All words, pixels, neighbouring lines and icons are synthetic.
BOX=[321.63054836134194,236.10265083244502,369.8916586439619,244.15543429855813]
BOUNDS=[80.32499694824219,177.719970703125,530.1549682617188,401.989990234375]
TEXT='SYNTHPARK水上乐园'


def fixture(path,background='gray',rotation=0,blocked=False,guard=False,narrow=False,
            text=TEXT,bold=False,italic=False,raster=False,texture=True):
    region=fitz.Rect(BOUNDS)
    rgb={'gray':(110,116,108),'white':(248,248,248),'black':(16,20,16)}[background]
    if raster:
        width,height=600,440
        box=[120,160,120+(BOX[2]-BOX[0]),160+(BOX[3]-BOX[1])]
        bounds=[0,0,width,height];region=fitz.Rect(bounds)
        image=Image.new('RGB',(width,height),rgb)
    else:
        width,height=595.32,842.04;box=list(BOX);bounds=list(BOUNDS)
        image=Image.new('RGB',(1800,900),rgb)
    if texture:
        noise=np.random.default_rng(110).integers(-16,17,(image.height,image.width,1),dtype=np.int16)
        image=Image.fromarray(np.clip(np.asarray(image).astype(np.int16)+noise,0,255).astype('uint8'))
    draw=ImageDraw.Draw(image);sx=image.width/region.width;sy=image.height/region.height
    def xy(x,y):return ((x-region.x0)*sx,(y-region.y0)*sy)
    def rectangle(area,fill):draw.rectangle((*xy(*area[:2]),*xy(*area[2:])),fill=fill)
    ink='black' if background=='white' else 'white'
    font_path=renderer.cjk_fonts()[0] if renderer.cjk_fonts() else 'C:/Windows/Fonts/arial.ttf'
    font=ImageFont.truetype(str(font_path),max(6,round(4*sy)))
    draw.text(xy(box[0]+.5,box[1]+.5),'SYNTHPARK Pool',font=font,fill=ink)
    # A map pin to the right, and another label below the right end, prevent
    # rectangular expansion but leave the short second line's area clear.
    right=[box[2]+.4,box[1]-1,box[2]+10,box[3]+8]
    tail=[box[0]+33,box[3]+5.8,box[0]+54,box[3]+12]
    rectangle(right,ink);rectangle(tail,ink)
    # High-contrast texture directly below the source box, between the two
    # candidate lines. It must survive, not be painted over with a white box.
    texture_dot=[box[0]+8.5,box[3]+.05,box[0]+8.7,box[3]+.2]
    if not raster:
        rectangle(texture_dot,ink)
    blocked_box=[box[0],box[3],box[2],box[3]+20]
    if blocked:rectangle(blocked_box,ink)
    block={'id':'synthetic-mixed','type':'figure','bbox':list(box) if narrow else bounds,
           'text':'SYNTHPARK Pool','layout_lines':[{'text':'SYNTHPARK Pool','bbox':box,
            'style':{'size':(box[3]-box[1])*.72,'bold':bold,'italic':italic,'family':'serif'}}]}
    if guard:
        block['layout_lines'].append({'text':'99','bbox':[box[0],box[3]+1,box[0]+25,box[3]+13]})
    else:
        block['layout_lines'].append({'text':'99','bbox':tail})
    if raster:
        image.save(path)
    else:
        data=io.BytesIO();image.save(data,format='PNG')
        with fitz.open() as pdf:
            page=pdf.new_page(width=width,height=height)
            page.insert_text((50,60),'Synthetic mixed OCR label placement.',fontsize=12)
            page.insert_image(region,stream=data.getvalue(),keep_proportion=False)
            page.set_rotation(rotation);pdf.save(path)
    pages=[{'number':1,'width':width,'height':height,'blocks':[block]}]
    after=copy.deepcopy(pages);after[0]['blocks'][0]['text']=text
    after[0]['blocks'][0]['layout_lines'][0]['text']=text
    return {'schema':1,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages':pages,'translated_pages':after}


class ShapedOCRTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zhanhtran-mixed-110-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source.pdf'

    def check_output(self,source,document,result):
        from tests.fixed_layout_test import check_fixed_ocr
        return check_fixed_ocr(self,source,document,result)

    def test_mixed_label_wraps_without_covering_empty_tail_or_shrinking(self):
        document=fixture(self.source)
        result=renderer.export_pdfs(self.source,document,self.root/'out')
        self.check_output(self.source,document,result)

    def test_light_dark_and_rotations_in_both_panel_orders(self):
        for background in ('white','black'):
            for rotation in (0,90):
                for order in ('original-left','translated-left'):
                    with self.subTest(background=background,rotation=rotation,order=order):
                        path=self.root/f'{background}-{rotation}-{order}.pdf'
                        document=fixture(path,background=background,rotation=rotation)
                        result=renderer.export_pdfs(path,document,path.with_suffix(''),order=order)
                        self.check_output(path,document,result)
                        with fitz.open(result['translated']) as pdf,fitz.open(result['bilingual']) as dual,fitz.open(path) as original:
                            self.assertEqual(pdf[0].rotation,rotation)
                            pdf[0].set_rotation(0)
                            spans=[s for b in pdf[0].get_text('dict',clip=result['report']['placements'][0]['bbox'])['blocks']
                                   if b['type']==0 for line in b['lines'] for s in line['spans']]
                            self.assertTrue(all(s['color']==(0 if background=='white' else 0xffffff) for s in spans))
                            w,h=original[0].rect.width,original[0].rect.height
                            x=w if order=='original-left' else 0
                            self.assertIn(renderer.normalize(TEXT),renderer.normalize(dual[0].get_text(clip=(x,0,x+w,h))))

    def test_fixed_text_does_not_borrow_chart_ink_neighbor_or_extra_space(self):
        for name,options in [('ink',{'blocked':True}),('neighbor',{'guard':True}),('bound',{'narrow':True}),('long',{'text':TEXT*25})]:
            path=self.root/(name+'.pdf');doc=fixture(path,**options)
            result=renderer.export_pdfs(path,doc,self.root/name)
            self.check_output(path,doc,result)

    def test_independent_prior_placement_blocks_new_glyphs(self):
        document=fixture(self.source)
        with fitz.open(self.source) as original,fitz.open() as candidate:
            page=candidate.new_page(width=595.32,height=842.04)
            item=renderer.page_units(original[0],document['pages'][0],document['translated_pages'][0],{})[0]
            placed=[{'bbox':[BOX[0],BOX[3]+1,BOX[0]+22,BOX[3]+12]}]
            self.assertIsNone(renderer.place_ocr_shaped(page,original[0],item,[item],placed,(.43,.45,.42),'white'))
            self.assertFalse(page.get_contents())

    def test_annotation_and_widget_are_guarded_even_when_transparent(self):
        for kind in ('annotation','widget'):
            with self.subTest(kind=kind):
                path=self.root/(kind+'.pdf');document=fixture(path)
                with fitz.open(path) as original,fitz.open() as candidate:
                    area=fitz.Rect(BOX[0],BOX[3]+1,BOX[0]+22,BOX[3]+13)
                    if kind=='annotation':
                        annot=original[0].add_rect_annot(area);annot.set_opacity(0);annot.update()
                    else:
                        widget=fitz.Widget();widget.field_type=fitz.PDF_WIDGET_TYPE_TEXT
                        widget.field_name='synthetic';widget.rect=area;original[0].add_widget(widget)
                    page=candidate.new_page(width=595.32,height=842.04)
                    item=renderer.page_units(original[0],document['pages'][0],document['translated_pages'][0],{})[0]
                    self.assertIsNone(renderer.place_ocr_shaped(page,original[0],item,[item],[],(.43,.45,.42),'white'))
                    self.assertFalse(page.get_contents())

    def test_cli_uses_one_json_response_and_independent_progress_events(self):
        document=fixture(self.source);path=self.root/'translated.json'
        path.write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        result=subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),
            '--input',str(self.source),'--document',str(path),'--output',str(self.root/'out'),
            '--progress'],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual(len(result.stdout.strip().splitlines()),1)
        response=json.loads(result.stdout);self.assertTrue(response['ok'])
        events=[json.loads(line.removeprefix('ZHANHTRAN_PROGRESS ')) for line in result.stderr.splitlines()]
        self.assertEqual(events,[{'stage':'export','done':1,'total':1}])
        report=json.loads((self.root/'out/pdf-layout-report.json').read_text('utf-8'))
        self.check_output(self.source,document,{**response,'report':report})

    def test_bold_mixed_label_keeps_its_style(self):
        document=fixture(self.source,bold=True,text='SYNTHPARK合成公园')
        result=renderer.export_pdfs(self.source,document,self.root/'out')
        self.check_output(self.source,document,result)
        with fitz.open(result['translated']) as pdf:
            spans=[s for b in pdf[0].get_text('dict',clip=result['report']['placements'][0]['bbox'])['blocks']
                   if b['type']==0 for line in b['lines'] for s in line['spans']]
            self.assertTrue(all(s['flags']&16 for s in spans))

    def test_raster_delivery_copies_expanded_glyphs_and_keeps_neighbor_pixels(self):
        path=self.root/'source.png';document=fixture(path,raster=True,texture=False)
        result=image_renderer.export_images(path,document,self.root/'out')
        placed=result['report']['placements'][0]
        self.assertEqual(placed['fit'],'ocr-shaped-lines')
        self.assertEqual(placed['scale'],1)
        with Image.open(path) as original,Image.open(result['translated']) as translated:
            self.assertEqual(original.size,translated.size)
            box=document['pages'][0]['blocks'][0]['layout_lines'][0]['bbox']
            tail=(int(box[0]+33),int(box[3]+6),int(box[0]+54),int(box[3]+12))
            self.assertEqual(original.crop(tail).tobytes(),translated.convert('RGB').crop(tail).tobytes())
            region=(int(box[0]),int(box[3]+2),int(box[0]+20),int(box[3]+12))
            self.assertNotEqual(original.crop(region).tobytes(),translated.convert('RGB').crop(region).tobytes())


if __name__=='__main__':unittest.main()
