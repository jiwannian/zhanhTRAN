"""Filled-rectangle table borders: synthetic inputs only, one or two pages."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz

from tests import table_flow_test as table_tests

fixture,renderer,parser = table_tests.fixture,table_tests.renderer,table_tests.parser


class FilledBorderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='zhanhtran-filled-grid-')
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.source=self.root/'synthetic.pdf'

    def render(self, **options):
        doc=fixture(self.source,border='tiles',**options)
        before=self.source.read_bytes()
        result=renderer.export_pdfs(self.source,doc,self.root/'out')
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(result['report']['unplaced'],[])
        table_tests.TableFlowTests.check_cells(self,doc,result)
        return doc,result

    def test_snapped_cached_edges_keep_all_physical_border_ink(self):
        doc,result=self.render(shaded=True)
        with fitz.open(self.source) as old,fitz.open(result['translated']) as new:
            self.assertEqual([{k:v for k,v in d.items() if k!='seqno'} for d in old[0].get_drawings()],[{k:v for k,v in d.items() if k!='seqno'} for d in new[0].get_drawings()])
        self.assertEqual(result['report']['output_pages'],1)

    def test_no_growth_keeps_source_drawings_exactly(self):
        doc=fixture(self.source,border='tiles',mode='short')
        result=renderer.export_pdfs(self.source,doc,self.root/'out')
        self.assertNotIn('table_rows',result['report'])
        with fitz.open(self.source) as original,fitz.open(result['translated']) as pdf:
            self.assertEqual(original[0].get_drawings(),pdf[0].get_drawings())

    def test_fresh_parser_and_cached_geometry_both_reflow(self):
        expected=fixture(self.source,border='tiles')
        doc=parser.parse_document(self.source,self.root/'parsed')
        table=next(b for b in doc['pages'][0]['blocks'] if b['type']=='table')
        self.assertEqual(len(table['cells']),4)
        doc['translated_pages']=copy.deepcopy(doc['pages'])
        translated=next(b for b in doc['translated_pages'][0]['blocks'] if b['type']=='table')
        translated['rows']=expected['translated_pages'][0]['blocks'][0]['rows']
        result=renderer.export_pdfs(self.source,doc,self.root/'out')
        table_tests.TableFlowTests.check_cells(self,doc,result)

    def test_many_and_oversized_rows_shrink_and_keep_navigation(self):
        from tests.fixed_layout_test import check_fixed
        for mode in ('many','tall'):
            for rotation in (0,90):
                for order in ('original-left','translated-left'):
                    source=self.root/f'{mode}-{rotation}-{order}.pdf'
                    doc=fixture(source,border='tiles',mode=mode,rotation=rotation,next_page=True)
                    result=renderer.export_pdfs(source,doc,self.root/source.stem,order=order)
                    table_tests.TableFlowTests.check_cells(self,doc,result)
                    check_fixed(self,source,doc,result,order)
                    with fitz.open(result['translated']) as pdf:
                        self.assertEqual(pdf[0].get_links()[0]['page'],0)
                        self.assertEqual(pdf.get_toc()[-1][2],2)

    def test_adjacent_figure_and_footer_pixels_are_unchanged(self):
        doc,result=self.render(mode='many',obstacle=True,height=600)
        region=next(b['bbox'] for b in doc['pages'][0]['blocks'] if b['type']=='figure')
        with fitz.open(self.source) as original,fitz.open(result['translated']) as pdf:
            for box in (region,(200,580,240,600)):
                self.assertEqual(original[0].get_pixmap(clip=box).samples,pdf[0].get_pixmap(clip=box).samples)

    def test_unrelated_artwork_does_not_require_border_reconstruction(self):
        doc=fixture(self.source,border='tiles')
        with fitz.open(self.source) as pdf:
            pdf[0].draw_circle((70,100),4,color=(1,0,0));pdf.saveIncr()
        doc['source_sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        result=renderer.export_pdfs(self.source,doc,self.root/'out')
        table_tests.TableFlowTests.check_cells(self,doc,result)
        with fitz.open(self.source) as old,fitz.open(result['translated']) as new:
            self.assertEqual([{k:v for k,v in d.items() if k!='seqno'} for d in old[0].get_drawings()],[{k:v for k,v in d.items() if k!='seqno'} for d in new[0].get_drawings()])

    def test_incomplete_grid_or_unrelated_corner_is_not_reconstructed(self):
        doc=fixture(self.source,border='tiles')
        cells=doc['pages'][0]['blocks'][0]['cells']
        xs=[cells[0][0][0]]+[c[2] for c in cells[0]]
        ys=[row[0][1] for row in cells]+[cells[-1][0][3]]
        with fitz.open(self.source) as pdf:
            drawings=pdf[0].get_drawings()
            missing=next(i for i,d in enumerate(drawings) if d['rect'].width>100)
            reason={}
            self.assertIsNone(renderer.table_grid(drawings[:missing]+drawings[missing+1:],xs,ys,reason))
            self.assertEqual(reason['reason'],'TABLE_BORDER_INCOMPLETE_HORIZONTAL')
            pdf[0].draw_rect((41.8,80,42.3,80.5),color=None,fill=(1,0,0))
            reason={}
            self.assertIsNone(renderer.table_grid(pdf[0].get_drawings(),xs,ys,reason))
            self.assertEqual(reason['reason'],'TABLE_BORDER_UNRELATED_CORNER')


if __name__=='__main__':
    unittest.main()
