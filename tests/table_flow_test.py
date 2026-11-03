"""Native ruled-table reflow, using only one-to-three-page synthetic PDFs."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('table_renderer',ROOT/'src/local_pdf.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
PARSER_SPEC = importlib.util.spec_from_file_location('table_parser',ROOT/'src/local_parser.py')
parser = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(parser)

MIXED = '符合合成设备规则（WXYZ）要求'


def fixture(path, mode='grow', rotation=0, next_page=False, header_rows=1, shaded=False,
            obstacle=False, second_table=False, height=400, border='strokes'):
    """12 pt / 24.12 pt source rows; no source words or pixels from a paper."""
    count = 4 if mode!='many' else 9
    xs, ys = [40,204.4867935180664,382.6736019897461], [80+i*24.12 for i in range(count+1)]
    source_rows = [['Synthetic ID','Synthetic description']]
    source_rows += [[f'Item {i}',f'Synthetic device {i}'] for i in range(1,count)]
    styles = [[{'size':12,'family':'serif','bold':ri==0,'italic':False,'line_height':1.12}
               for _ in range(2)] for ri in range(count)]
    cells = [[[xs[ci],ys[ri],xs[ci+1],ys[ri+1]] for ci in range(2)] for ri in range(count)]
    table = {'id':'synthetic-table','type':'table','extraction':'pymupdf',
             'bbox':[xs[0],ys[0],xs[-1],ys[-1]],'rows':source_rows,'cells':cells,
             'cell_styles':styles,'style':{'size':12},'table_header_rows':header_rows}
    blocks = [table]
    footer_y = height-8
    with fitz.open() as pdf:
        page = pdf.new_page(width=440,height=height)
        def grid(block, shade=False):
            edges = [r[0][1] for r in block['cells']]+[block['cells'][-1][0][3]]
            if shade:
                page.draw_rect((xs[0],edges[0],xs[-1],edges[1]),color=None,fill=(.88,.93,.98))
            if border=='tiles':
                # Synthetic word-processor encoding: separate thin rectangles
                # and crossing squares, with cached table-coordinate snapping.
                physical_x=[xs[0]+.92,*xs[1:-1],xs[-1]-.92]
                physical_y=[edges[0]+.08,*edges[1:-1],edges[-1]-.08]
                half=.24
                def tile(box):
                    page.draw_rect(box,color=None,fill=(.12,.25,.4))
                for y in physical_y:
                    for x in physical_x:
                        tile((x-half,y-half,x+half,y+half))
                    for a,b in zip(physical_x,physical_x[1:]):
                        tile((a+half,y-half,b-half,y+half))
                for x in physical_x:
                    for a,b in zip(physical_y,physical_y[1:]):
                        tile((x-half,a+half,x+half,b-half))
            else:
                for x in xs:
                    page.draw_line((x,block['bbox'][1]),(x,block['bbox'][3]),width=.6,color=(.12,.25,.4))
                for yi,y in enumerate(edges):
                    page.draw_line((xs[0],y),(xs[-1],y),width=1.2 if yi in (0,len(edges)-1) else .5,
                                   color=(.12,.25,.4))
            for ri,row in enumerate(block['rows']):
                for ci,text in enumerate(row):
                    box=block['cells'][ri][ci]
                    page.insert_text((box[0]+2,box[1]+16),text,fontname='tibo' if ri==0 else 'tiro',fontsize=12)
        grid(table,shaded)
        if second_table:
            other=copy.deepcopy(table);other['id']='second-table'
            offset=ys[-1]-ys[0]+22
            other['bbox']=[xs[0],ys[0]+offset,xs[-1],ys[-1]+offset]
            other['cells']=[[[b[0],b[1]+offset,b[2],b[3]+offset] for b in row] for row in cells]
            grid(other);blocks.append(other)
        tail_y=blocks[-1]['bbox'][3]+22
        page.insert_text((40,tail_y),'Unchanged following body text.',fontname='tiro',fontsize=12)
        blocks.append({'id':'synthetic-tail','type':'paragraph','bbox':[40,tail_y-13,360,tail_y+4],
                       'text':'Unchanged following body text.','style':{'size':12,'family':'serif','line_height':1.35},
                       'body_left':40,'first_line_indent':0})
        page.insert_text((210,footer_y),'1',fontsize=10)
        if obstacle:
            figure=[40,ys[-1]+45,383,ys[-1]+85]
            page.draw_rect(figure,color=(1,0,0),fill=(.8,.9,.7))
            blocks.insert(1,{'id':'synthetic-figure','type':'figure','bbox':figure,'text':''})
        page.set_rotation(rotation)
        pages=[{'number':1,'width':440,'height':height,'blocks':blocks}]
        if next_page:
            page=pdf.new_page(width=440,height=height)
            page.insert_text((40,60),'Synthetic next source page.',fontsize=12)
            page.set_rotation(rotation)
            pages.append({'number':2,'width':440,'height':height,'blocks':[{'id':'synthetic-next',
                'type':'paragraph','bbox':[40,47,340,64],'text':'Synthetic next source page.',
                'style':{'size':12,'family':'serif'}}]})
        pdf.set_toc([[1,'Synthetic table row',1,ys[-2]+2]]+([[1,'Synthetic next',2]] if next_page else []))
        # Internal destination is the final data row, which can move to a continuation.
        pdf[0].set_rotation(0)
        pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(40,30,160,45),
                          'page':0,'to':fitz.Point(xs[0]+3,ys[-2]+3)})
        pdf[0].set_rotation(rotation)
        pdf.save(path)
    translated=copy.deepcopy(pages)
    for block in translated[0]['blocks']:
        if block['type']=='table':
            block['rows'][0]=['合成编号','合成设备说明']
            for ri in range(1,count):
                block['rows'][ri]=[f'编号{ri}',MIXED]
                if mode=='many':
                    block['rows'][ri][1]=f'第{ri}行合成设备说明。'*18
                if mode=='tall' and ri==1:
                    block['rows'][ri][1]='超高单行自然换行测试。'*200
            if mode=='short':
                block['rows']=[['编号','说明']]+[[f'编号{i}','短文'] for i in range(1,count)]
    if next_page:
        translated[1]['blocks'][0]['text']='下一原文页单独开始。'
    return {'schema':1,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pages':pages,'translated_pages':translated}


class TableFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zhanhtran-table-109-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'synthetic.pdf'

    def render(self, **options):
        order=options.pop('order','original-left')
        document=fixture(self.source,**options)
        before=self.source.read_bytes()
        result=renderer.export_pdfs(self.source,document,self.root/'out',order=order)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(result['report']['unplaced'],[])
        return document,result

    def check_cells(self,document,result):
        report=result['report']
        self.assertEqual(report['unplaced'],[])
        self.assertEqual(report['output_pages'],len(document['pages']))
        self.assertTrue(all(p['continuation_index']==0 for p in report['page_map']))
        self.assertNotIn('table_rows',report)
        with fitz.open(result['translated']) as pdf:
            for bi,(old,block) in enumerate(zip(document['pages'][0]['blocks'],document['translated_pages'][0]['blocks'])):
                if block['type']!='table':continue
                for ri,row in enumerate(block['rows']):
                    for ci,text in enumerate(row):
                        if renderer.normalize(text)==renderer.normalize(old['rows'][ri][ci]):continue
                        p=next(p for p in report['placements'] if p.get('table_id')==block['id'] and p['table_row']==ri and p['table_column']==ci)
                        cell=fitz.Rect(old['cells'][ri][ci])+(1,1,-1,-1)
                        self.assertEqual(p['bbox'],list(cell))
                        self.assertTrue((cell+(-.001,-.001,.001,.001)).contains(fitz.Rect(p['ink_bbox'])))
                        self.assertGreater(p['scale'],0);self.assertLessEqual(p['scale'],1)
                        page=pdf[p['output_page']-1];page.set_rotation(0)
                        actual=page.get_text(clip=cell+(-.1,-.1,.1,.1))
                        self.assertEqual(renderer.normalize(actual),renderer.normalize(text))

    def test_mixed_cell_shrinks_without_growing_rows_or_moving_tail(self):
        document,result=self.render(shaded=True)
        self.check_cells(document,result)
        self.assertTrue(all(p['source_font_size']==12 for p in result['report']['placements']))
        self.assertTrue(any(p['scale']<1 for p in result['report']['placements']))
        self.assertFalse(any(p['ids']==['synthetic-tail'] for p in result['report']['placements']))
        with fitz.open(self.source) as source,fitz.open(result['translated']) as pdf:
            self.assertEqual(source[0].get_drawings(),pdf[0].get_drawings())

    def test_short_cells_retain_original_vector_grid(self):
        document,result=self.render(mode='short')
        self.assertNotIn('table_rows',result['report'])
        with fitz.open(self.source) as source,fitz.open(result['translated']) as pdf:
            self.assertEqual(source[0].get_drawings(),pdf[0].get_drawings())

    def test_unchanged_table_does_not_trigger_repainting_from_fallback_metrics(self):
        document=fixture(self.source)
        document['translated_pages']=copy.deepcopy(document['pages'])
        # The source itself fits. A fallback line-height estimate must not
        # trigger table flow when none of its cells has been translated.
        for row in document['pages'][0]['blocks'][0]['cell_styles']:
            for style in row:
                style['line_height']=3
        result=renderer.export_pdfs(self.source,document,self.root/'out')
        self.assertEqual(result['report']['placements'],[])
        with fitz.open(self.source) as source,fitz.open(result['translated']) as pdf:
            self.assertEqual(source[0].get_pixmap().samples,pdf[0].get_pixmap().samples)

    def test_many_rows_stay_on_original_page_and_preserve_every_cell(self):
        document,result=self.render(mode='many',next_page=True)
        self.check_cells(document,result)
        self.assertEqual(result['report']['output_pages'],2)
        self.assertTrue(any(p['scale']<.5 for p in result['report']['placements']))

    def test_extremely_long_cell_shrinks_with_original_columns_aligned(self):
        document,result=self.render(mode='tall')
        self.check_cells(document,result)
        p=next(p for p in result['report']['placements'] if p.get('table_row')==1 and p['table_column']==1)
        self.assertLess(p['scale'],.2)
        self.assertEqual(p['output_page'],1)

    def test_empty_unchanged_cell_and_distinct_cell_styles(self):
        document=fixture(self.source)
        table=document['pages'][0]['blocks'][0]
        table['cell_styles'][2][1]['italic']=True
        document['translated_pages'][0]['blocks'][0]['rows'][2][0]=''
        table['rows'][2][0]=''
        result=renderer.export_pdfs(self.source,document,self.root/'out')
        self.check_cells(document,result)
        self.assertFalse(any(p.get('table_row')==2 and p.get('table_column')==0 for p in result['report']['placements']))
        with fitz.open(result['translated']) as pdf:
            cell=fitz.Rect(table['cells'][2][1])+(1,1,-1,-1)
            spans=[s for b in pdf[0].get_text('dict',clip=cell)['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
            self.assertTrue(all(s['flags']&2 for s in spans))

    def test_two_tables_and_following_body_keep_original_positions(self):
        document,result=self.render(second_table=True,height=600)
        self.check_cells(document,result)
        self.assertFalse(any(p['ids']==['synthetic-tail'] for p in result['report']['placements']))
        with fitz.open(self.source) as old,fitz.open(result['translated']) as new:
            self.assertEqual([{k:v for k,v in d.items() if k!='seqno'} for d in old[0].get_drawings()],[{k:v for k,v in d.items() if k!='seqno'} for d in new[0].get_drawings()])

    def test_figure_and_page_footer_stay_untouched(self):
        document,result=self.render(mode='many',obstacle=True,height=600)
        figure=fitz.Rect(next(b['bbox'] for b in document['pages'][0]['blocks'] if b['type']=='figure'))
        with fitz.open(self.source) as original,fitz.open(result['translated']) as pdf:
            self.assertEqual(original[0].get_pixmap(clip=figure).samples,pdf[0].get_pixmap(clip=figure).samples)
            self.assertEqual(original[0].get_pixmap(clip=fitz.Rect(200,580,240,600)).samples,
                             pdf[0].get_pixmap(clip=fitz.Rect(200,580,240,600)).samples)
        self.assertTrue(all(not fitz.Rect(p['bbox']).intersects(figure) for p in result['report']['placements'] if p['output_page']==1))

    def test_rotations_orders_blank_original_side_next_page_and_navigation(self):
        for rotation in (0,90):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    folder=self.root/f'{rotation}-{order}';folder.mkdir()
                    source=folder/'source.pdf'
                    doc=fixture(source,mode='many',rotation=rotation,next_page=True)
                    result=renderer.export_pdfs(source,doc,folder/'out',order=order)
                    mapping=result['report']['page_map']
                    with fitz.open(source) as original,fitz.open(result['translated']) as pdf,fitz.open(result['bilingual']) as dual:
                        self.assertEqual(pdf[-1].rotation,rotation)
                        self.assertEqual(pdf.get_toc()[-1][2],len(pdf))
                        for entry in mapping:
                            if entry['continuation_index']:
                                width,height=original[0].rect.width,original[0].rect.height
                                side=0 if order=='original-left' else 1
                                pix=dual[entry['output_page']-1].get_pixmap(clip=(side*width,0,(side+1)*width,height))
                                self.assertTrue(all(c==255 for c in pix.samples))
                        # The link to the final data row moves with that row.
                        link=pdf[0].get_links()[0]
                        final=next(p for p in result['report']['placements'] if p.get('table_row')==8 and p['table_column']==0)
                        self.assertEqual(link['page']+1,final['output_page'])
                        point=link['to']*pdf[link['page']].derotation_matrix
                        self.assertTrue(fitz.Rect(final['bbox']).contains(point))
                        self.assertEqual(pdf.get_toc()[0][2],final['output_page'])
                        self.assertEqual(mapping[-1]['continuation_index'],0)

    def test_fresh_parser_preserves_cell_styles_and_optional_header_metadata(self):
        document=fixture(self.source)
        parsed=parser.parse_document(self.source,self.root/'parsed')
        table=next(b for b in parsed['pages'][0]['blocks'] if b['type']=='table')
        self.assertIn(table['table_header_rows'],(0,1))
        self.assertTrue(all(abs(c['size']-12)<.001 for row in table['cell_styles'] for c in row))
        translated=copy.deepcopy(parsed['pages'])
        target=next(b for b in translated[0]['blocks'] if b['type']=='table')
        target['rows']=copy.deepcopy(document['translated_pages'][0]['blocks'][0]['rows'])
        parsed['translated_pages']=translated
        result=renderer.export_pdfs(self.source,parsed,self.root/'out')
        self.check_cells(parsed,result)

    def test_missing_changed_cell_geometry_fails_atomically(self):
        document=fixture(self.source)
        document['pages'][0]['blocks'][0]['cells'][1][0]=None
        with self.assertRaisesRegex(ValueError,'PDF_LAYOUT_INVALID'):
            renderer.export_pdfs(self.source,document,self.root/'out')
        report=json.loads((self.root/'out/pdf-layout-failure.json').read_text('utf-8'))
        self.assertEqual(report['unplaced'][0]['ids'],['synthetic-table-r1-c0'])
        self.assertFalse(list((self.root/'out').glob('*.pdf')))

    def test_no_header_metadata_does_not_repaint_or_repeat_headers(self):
        document,result=self.render(mode='many',header_rows=0)
        self.check_cells(document,result)
        self.assertNotIn('table_rows',result['report'])

    def test_overheight_header_shrinks_in_its_original_cell(self):
        document=fixture(self.source)
        document['translated_pages'][0]['blocks'][0]['rows'][0][1]='超长表头合成文字。'*200
        result=renderer.export_pdfs(self.source,document,self.root/'out')
        self.check_cells(document,result)
        p=next(p for p in result['report']['placements'] if p.get('table_row')==0 and p['table_column']==1)
        self.assertLess(p['scale'],.2)

    def test_adjacent_vector_ink_is_not_approved_for_grid_redaction(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=300,height=300)
            for x in (40,100,160):
                page.draw_line((x,50),(x,100),width=1)
            for y in (50,75,100):
                page.draw_line((40,y),(160,y),width=1)
            page.draw_line((50,100.6),(80,100.6),width=.1,color=(1,0,0))
            self.assertIsNone(renderer.table_grid(page.get_drawings(),[40,100,160],[50,75,100]))


if __name__=='__main__':
    unittest.main()
