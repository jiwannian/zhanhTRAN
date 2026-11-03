"""Small synthetic layout cases: no paper or personal document is read."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location('regression_'+name, ROOT/'src'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
parser, renderer = load('local_parser'), load('local_pdf')


def fixture(path, rotation=0):
    with fitz.open() as pdf:
        page = pdf.new_page(width=500,height=700)
        page.insert_text((45,48),'Contents',fontname='tibo',fontsize=18)
        for y,prefix,title,number in [(100,'1.','Overview','2'),(132,'1.1','Detailed example','3'),(164,'1.1.1','Nested topic','3')]:
            indent = (len(prefix.split('.'))-1)*12
            page.insert_text((45+indent,y),prefix,fontname='tiro',fontsize=12)
            page.insert_text((105+indent,y),title+'.'*55+number,fontname='tiro',fontsize=12)
        page = pdf.new_page(width=500,height=700)
        page.insert_text((45,55),'1. Examples',fontname='tibo',fontsize=16)
        page.insert_text((45,95),'At 10:51:36, the synthetic timer begins its normal sequence.',fontname='tiro',fontsize=11)
        page.insert_text((65,135),'First line indented, with its own paragraph baseline.',fontname='tiro',fontsize=11)
        page.insert_text((45,151),'The following line returns to the original body margin.',fontname='tiro',fontsize=11)
        page.insert_text((45,195),'-',fontname='tiro',fontsize=11)
        page.insert_text((65,195),'The first synthetic list item keeps its marker.',fontname='tiro',fontsize=11)
        page.insert_text((65,211),'Its continuation retains the hanging indent.',fontname='tiro',fontsize=11)
        # The full coordinate is protected as native artwork, including double primes.
        page.insert_htmlbox(fitz.Rect(45,235,260,260),'Latitude: 28\u00b011\u203251\u2033 N',css='body,div {margin:0;padding:0;font-size:11pt}',scale_low=1)
        page.insert_text((45,290),'A complete paragraph keeps the same source body size.',fontname='tiro',fontsize=11)
        page.insert_text((45,306),'A short final line remains part of that paragraph.',fontname='tiro',fontsize=11)
        page.draw_rect((300,345,455,405),color=(0,.5,0),fill=(.9,1,.9))
        page.insert_text((45,635),'Synthetic overflow paragraph at the bottom of the page.',fontname='tiro',fontsize=11)
        page = pdf.new_page(width=500,height=700)
        page.insert_text((45,60),'Next source page starts independently.',fontname='tiro',fontsize=11)
        for index in range(len(pdf)):
            pdf[index].set_rotation(rotation)
        pdf.set_toc([[1,'Examples',2],[1,'Next source page',3]])
        pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(45,85,240,105),'page':2,'to':fitz.Point(45,48)})
        pdf.save(path)


def translated(document):
    document = copy.deepcopy(document)
    document['translated_pages'] = copy.deepcopy(document['pages'])
    for page in document['translated_pages']:
        for block in page['blocks']:
            if block.get('toc_rows'):
                for row in block['toc_rows']:
                    row['text'] = {'Overview':'概述','Detailed example':'详细示例','Nested topic':'嵌套主题'}.get(row['text'],'目录条目')
            elif block.get('inline_math'):
                block['translation_text'] = '纬度：'+''.join('[['+m['id']+']]' for m in block['inline_math'])
                block['text'] = block['translation_text']
            elif block.get('prefix'):
                block['translation_text'] = ('示例' if block['type']=='heading' else
                    '第一项合成列表内容保持原有项目符号。后续内容保持悬挂缩进。')
                block['text'] = block['translation_text']
            elif block.get('layout_runs'):
                for run in block['layout_runs']:
                    run['text'] = '纬度'
            elif block['text'].startswith('Synthetic overflow'):
                block['text'] = '页尾续排保留字号与所有文字。'*100
            elif block['text'].startswith('Next source'):
                block['text'] = '下一原文页的译文单独另起一页。'
            elif block['text'].startswith('At 10:51:36'):
                block['text'] = '10:51:36，合成计时器开始正常运行。'
            elif block['text'] == 'Contents':
                block['text'] = '目录'
            elif block['type'] != 'formula':
                block['text'] = '完整的合成正文保持原有字号、缩进和段落格式。'
    return document


class LayoutRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-102-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'synthetic.pdf'

    def render(self, rotation=0, order='original-left'):
        fixture(self.source,rotation)
        document = parser.parse_document(self.source,self.root/'parsed')
        return document,renderer.export_pdfs(self.source,translated(document),self.root/'output',order=order)

    def test_toc_coordinates_keep_number_page_columns_and_body_metadata(self):
        document,result = self.render()
        rows = [row for b in document['pages'][0]['blocks'] for row in b.get('toc_rows',[])]
        self.assertEqual(len(rows),3)
        with fitz.open(self.source) as source,fitz.open(result['translated']) as output:
            for row in rows:
                for key in ('number_bbox','page_bbox'):
                    rect = fitz.Rect(row[key]) + (-.1,-.1,.1,.1)
                    self.assertEqual(source[0].get_pixmap(clip=rect).samples,output[0].get_pixmap(clip=rect).samples)
            self.assertIn('概述',output[0].get_text())
        body = document['pages'][1]['blocks']
        self.assertTrue(any(b.get('first_line_indent',0)>10 for b in body))
        self.assertTrue(any(b.get('prefix') for b in body))
        self.assertTrue(all(b['style']['size']>=10.99 for b in body if b['type']=='paragraph'))

    def test_overflow_shrinks_with_text_and_next_page_boundary_unchanged(self):
        document,result=self.render()
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,self.source,translated(document),result)
        with fitz.open(result['translated']) as output:
            self.assertEqual(renderer.normalize(output[1].get_text()).count('页尾续排保留字号与所有文字。'),100)
            self.assertIn('下一原文页',output[2].get_text())
            self.assertEqual(output.get_toc()[1][2],3)
            self.assertEqual(output[0].get_links()[0]['page'],2)

    def test_coordinate_symbols_align_with_label_without_opaque_placeholder(self):
        document,result = self.render()
        self.assertTrue(any(block.get('inline_math') for block in document['pages'][1]['blocks']))
        with fitz.open(result['translated']) as output:
            page = output[1]
            spans = [span for block in page.get_text('dict')['blocks'] if block['type']==0
                     for line in block['lines'] for span in line['spans']]
            symbol = next(span for span in spans if span['text'].startswith('28'))
            label = next(span for span in spans if span['bbox'][0] < symbol['bbox'][0]
                         and 220 < span['bbox'][1] < 270)
            # Font ascenders differ; alignment is determined by the baseline.
            self.assertLess(abs(symbol['origin'][1]-label['origin'][1]), .5)
            placeholder = next(image for image in page.get_image_info()
                               if abs(image['bbox'][0]-symbol['bbox'][0]) < 1)
            pixels = page.get_pixmap(clip=fitz.Rect(placeholder['bbox']), alpha=False)
            opaque_black = sum(all(channel < 30 for channel in pixels.samples[index:index+3])
                               for index in range(0,len(pixels.samples),3))
            self.assertLess(opaque_black/(pixels.width*pixels.height), .55)

    def test_body_start_indents_symbols_and_uniform_region_scale(self):
        document,result=self.render()
        from tests.fixed_layout_test import check_fixed
        check_fixed(self,self.source,translated(document),result)
        blocks=document['pages'][1]['blocks']
        with fitz.open(self.source) as source,fitz.open(result['translated']) as output:
            spans=[s for b in output[1].get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
            timer=next(s for s in spans if s['text'].startswith('10:51:36'))
            self.assertAlmostEqual(timer['origin'][0],45,places=2)
            for b in blocks:
                ps=[p for p in result['report']['placements'] if p['ids']==[b['id']]]
                if not ps:continue
                p=ps[0]
                self.assertAlmostEqual(p['font_size'],b['style']['size']*p['scale'],places=3)
                if b.get('prefix'):
                    self.assertEqual(p['bbox'][0],b['body_left'])
                    marker=next(s for s in spans if s['text'].strip()==b['prefix']['text'])
                    self.assertAlmostEqual(marker['origin'][0],b['prefix']['bbox'][0],places=2)
                if b.get('first_line_indent'):
                    inside=[s for s in spans if fitz.Rect(p['bbox']).contains(fitz.Point(s['origin']))]
                    self.assertAlmostEqual(inside[0]['origin'][0],b['body_left']+b['first_line_indent'],places=2)
            for symbol in ('28','11','51','°','′','″'):
                self.assertEqual(source[1].get_text().count(symbol),output[1].get_text().count(symbol))

    def test_no_continuations_and_both_rotated_original_panels_unchanged(self):
        from tests.fixed_layout_test import check_fixed
        for order,rotation in [('original-left',0),('translated-left',90)]:
            folder=self.root/(order+str(rotation));folder.mkdir()
            source=folder/'synthetic.pdf';fixture(source,rotation)
            doc=translated(parser.parse_document(source,folder/'parsed'))
            result=renderer.export_pdfs(source,doc,folder/'output',order=order)
            check_fixed(self,source,doc,result,order)

    def test_native_extractions_are_cached_per_page(self):
        fixture(self.source)
        with fitz.open(self.source) as pdf:
            page=pdf[1]
            with patch.object(page,'get_text',wraps=page.get_text) as text,patch.object(page,'get_drawings',wraps=page.get_drawings) as drawings:
                parser.pdf_page(page,self.root,[])
            # PyMuPDF's table finder also reads text internally once.
            self.assertLessEqual(text.call_count,3)
            self.assertLessEqual(drawings.call_count,1)

    def test_plain_table_translates_complete_cells_at_their_source_size(self):
        with fitz.open() as pdf:
            page = pdf.new_page(width=400,height=300)
            for x in (40,200,360):
                page.draw_line((x,50),(x,170))
            for y in (50,90,130,170):
                page.draw_line((40,y),(360,y))
            for x,y,text in [(50,75,'Model name'),(210,75,'Score'),(50,115,'Alpha'),(210,115,'95'),(50,155,'Beta'),(210,155,'88')]:
                page.insert_text((x,y),text,fontsize=12)
            pdf.save(self.source)
        document = parser.parse_document(self.source,self.root/'parsed')
        table = next(b for b in document['pages'][0]['blocks'] if b['type']=='table')
        self.assertNotIn('layout_runs',table)
        self.assertEqual(table['cell_styles'][0][0]['size'],12)
        document['translated_pages'] = copy.deepcopy(document['pages'])
        translated_table = next(b for b in document['translated_pages'][0]['blocks'] if b['id']==table['id'])
        translated_table['rows'] = [['合成模型名称','结果分数'],['阿尔法模型','95'],['贝塔模型','88']]
        result = renderer.export_pdfs(self.source,document,self.root/'output')
        with fitz.open(result['translated']) as output, fitz.open(self.source) as original:
            text = renderer.normalize(output[0].get_text())
            self.assertIn('合成模型名称',text)
            self.assertIn('阿尔法模型',text)
            self.assertIn('95',text)
            self.assertEqual(output[0].get_drawings(),original[0].get_drawings())
        self.assertTrue(all(p['font_size']==12 and p['scale']==1 for p in result['report']['placements']))


if __name__ == '__main__':
    unittest.main()
