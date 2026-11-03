"""Small synthetic TOCs, including glyph-height boxes shorter than a line box."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('toc_renderer', ROOT/'src/local_pdf.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def fixture(path, texts=None, height=842.04, start=153.02, rotation=0, next_page=False):
    """Use only diagnostic geometry; all words and numbering are synthetic."""
    texts = texts or ['概述','合成目录条目','下一目录条目']
    width, size = 595.32, 11.04
    blocks = []
    with fitz.open() as pdf:
        page = pdf.new_page(width=width,height=height)
        for i in range(len(texts)):
            baseline = start+i*24.6
            number = '' if i==0 else '1.' if i==1 else '1.'+str(i)
            indent = 0 if i<2 else 16
            left = 81.024+indent
            title_left = left if not number else left+27
            title = 'Synthetic overview' if i==0 else 'Synthetic topic '+str(i)
            font = 'tibo' if i<2 else 'tiro'
            page.insert_text((left,baseline),number,fontname=font,fontsize=size)
            page.insert_text((title_left,baseline),title,fontname=font,fontsize=size)
            page.insert_text((title_left+120,baseline),'.'*60,fontname=font,fontsize=size)
            page.insert_text((534.6,baseline),str(i+2),fontname=font,fontsize=size)
            row = {'text':title,'number':number,'page_text':str(i+2),
                   'bbox':[left,baseline-9.84,542.615,baseline+2.76],
                   'title_bbox':[title_left,baseline-9.84,title_left+110,baseline+2.38],
                   'number_bbox':[left,baseline-9.84,left+22,baseline+2.76] if number else None,
                   'page_bbox':[534.6,baseline-9.84,540.12,baseline+2.38],
                   'leader_bbox':[title_left+120,baseline-9.84,534.6,baseline+2.38],
                   'style':{'size':size,'bold':i<2,'italic':False,'family':'serif','line_height':1.35},
                   'baseline':baseline}
            blocks.append({'id':f'toc-{i}','type':'paragraph','bbox':row['bbox'],
                           'text':title+number+str(i+2),'toc_rows':[row],'style':row['style']})
        page.set_rotation(rotation)
        if next_page:
            page = pdf.new_page(width=width,height=height)
            page.insert_text((40,60),'Synthetic next source page.',fontsize=11)
            page.set_rotation(rotation)
            pdf.set_toc([[1,'Synthetic next page',2]])
            pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(81,40,200,55),'page':1,'to':fitz.Point(40,48)})
        pdf.save(path)
    pages = [{'number':1,'width':width,'height':height,'blocks':blocks}]
    if next_page:
        pages.append({'number':2,'width':width,'height':height,'blocks':[{
            'id':'next-source','type':'paragraph','bbox':[40,48,330,65],
            'text':'Synthetic next source page.','style':{'size':11}}]})
    translated = copy.deepcopy(pages)
    for b,text in zip(translated[0]['blocks'],texts):
        b['toc_rows'][0]['text'] = text if text is not None else b['toc_rows'][0]['text']
    if next_page:
        translated[1]['blocks'][0]['text'] = '下一原文页单独开始。'
    return {'schema':1,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages':pages,'translated_pages':translated}


class TOCLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-toc-104-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'synthetic.pdf'

    def render(self, **options):
        order = options.pop('order','original-left')
        doc = fixture(self.source,**options)
        result = renderer.export_pdfs(self.source,doc,self.root/'output',order=order)
        return doc,result

    def test_thin_toc_rows_fit_at_original_size_baseline_and_columns(self):
        doc,result = self.render()
        self.assertEqual(result['report']['output_pages'],1)
        self.assertEqual(result['report']['unplaced'],[])
        with fitz.open(self.source) as original,fitz.open(result['translated']) as pdf:
            spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                     for line in b['lines'] for s in line['spans']]
            for old,new in zip(doc['pages'][0]['blocks'],doc['translated_pages'][0]['blocks']):
                row,text = old['toc_rows'][0],new['toc_rows'][0]['text']
                title = next(s for s in spans if text in s['text'])
                self.assertAlmostEqual(title['origin'][0],row['title_bbox'][0],places=2)
                self.assertAlmostEqual(title['origin'][1],row['baseline'],places=2)
                self.assertAlmostEqual(title['size'],row['style']['size'],places=2)
                fonts = renderer.cjk_fonts()
                if row['style']['bold'] and fonts and fonts[0]!=fonts[1]:
                    self.assertTrue(title['flags']&16)
                for key in ('number_bbox','page_bbox'):
                    if row.get(key):
                        box = fitz.Rect(row[key])
                        self.assertEqual(original[0].get_pixmap(clip=box).samples,pdf[0].get_pixmap(clip=box).samples)

    def test_long_title_shrinks_and_leaves_all_other_toc_columns_fixed(self):
        doc,result=self.render(texts=['完整的合成目录标题保留所有文字。'*6,None,'后续条目。'])
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,self.source,doc,result)
        self.assertLess(result['report']['placements'][0]['scale'],1)
        with fitz.open(self.source) as original,fitz.open(result['translated']) as pdf:
            self.assertEqual(renderer.normalize(pdf[0].get_text()).count('完整的合成目录标题保留所有文字。'),6)
            for block in doc['pages'][0]['blocks']:
                row=block['toc_rows'][0]
                for field in ('number_bbox','leader_bbox','page_bbox'):
                    if row.get(field):
                        self.assertEqual(original[0].get_pixmap(clip=row[field]).samples,pdf[0].get_pixmap(clip=row[field]).samples)

    def test_leaders_have_resolvable_fonts_after_source_redaction(self):
        doc,result = self.render()
        with fitz.open(result['translated']) as pdf:
            spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                     for line in b['lines'] for s in line['spans']]
            leaders = [s for s in spans if len(s['text'])>3 and set(s['text'])=={'.'}]
            self.assertEqual(len(leaders),3)
            for row,leader in zip(doc['pages'][0]['blocks'],leaders):
                self.assertEqual(leader['font'],'Times-Bold' if row['style']['bold'] else 'Times-Roman')
                self.assertAlmostEqual(leader['origin'][1],row['toc_rows'][0]['baseline'],places=2)
                self.assertLess(leader['bbox'][2],row['toc_rows'][0]['page_bbox'][0])
                # An extractable string alone is insufficient: missing resource
                # references can be invisible in standards-compliant readers.
                resources = {font[3] for font in pdf[0].get_fonts(full=True)}
                self.assertIn(leader['font'],resources)

    def test_long_toc_stays_on_source_page_and_keeps_navigation(self):
        doc,result=self.render(texts=['合成目录固定区域完整保留。'*100,'下一条目'],height=400,start=310,next_page=True)
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,self.source,doc,result)
        with fitz.open(result['translated']) as pdf:
            self.assertEqual(renderer.normalize(pdf[0].get_text()).count('合成目录固定区域完整保留。'),100)
            self.assertIn('下一原文页单独开始。',renderer.normalize(pdf[1].get_text()))
            self.assertEqual(pdf.get_toc()[0][2],2)
            self.assertEqual(pdf[0].get_links()[0]['page'],1)

    def test_rotated_toc_and_both_panel_orders_keep_all_translated_text(self):
        for rotation,order in [(0,'original-left'),(90,'translated-left')]:
            with self.subTest(rotation=rotation,order=order):
                doc = fixture(self.source,rotation=rotation)
                result = renderer.export_pdfs(self.source,doc,self.root/(order+str(rotation)),order=order)
                with fitz.open(result['bilingual']) as pdf,fitz.open(self.source) as original:
                    width,height = original[0].rect.width,original[0].rect.height
                    x = width if order=='original-left' else 0
                    text = renderer.normalize(pdf[0].get_text(clip=fitz.Rect(x,0,x+width,height)))
                    for block in doc['translated_pages'][0]['blocks']:
                        self.assertIn(block['toc_rows'][0]['text'],text)

    def test_cached_font_subset_keeps_unicode_cmap_and_weight(self):
        fonts = renderer.cjk_fonts()
        if not fonts:
            self.skipTest('No installed CJK font')
        characters = set('合成文字目录')
        for font in fonts:
            data = renderer.cjk_subset(font,characters)
            self.assertEqual(data,renderer.cjk_subset(font,characters))
            face = fitz.Font(fontbuffer=data)
            self.assertTrue(all(face.has_glyph(ord(char)) for char in characters))
            self.assertLess(len(data),font.stat().st_size)


if __name__ == '__main__':
    unittest.main()
