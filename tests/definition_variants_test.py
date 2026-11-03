"""Small synthetic mixed-case/spaced abbreviation columns; never read a paper."""
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf as fitz

from tests.definition_rows_test import parser, renderer
from tests.fixed_layout_test import check_fixed


GROUPS = (
    (('ATC','Synthetic control station','合成控制站'),
     ('ATPL','Synthetic transport permit','合成运输许可'),
     ('ATS','Synthetic traffic service','合成交通服务'),
     ('B. S.','Synthetic calendar standard','合成历法标准'),
     ('C of A','Synthetic inspection certificate','合成检验证书'),
     ('CAAN','Synthetic regional authority','合成地区机构'),
     ('CFIT','Synthetic sample condition','合成样本条件')),
    (('ICAO','Synthetic international organisation','合成国际组织'),
     ('IFR','Synthetic instrument rule','合成仪表规则'),
     ('IP','Synthetic teaching role','合成教学角色'),
     ('Kg','Synthetic mass unit','合成质量单位'),
     ('Kts','Synthetic speed unit','合成速度单位'),
     ('LH','Synthetic left side','合成左侧'),
     ('LT','Synthetic local timer','合成本地计时')),
    (('MOC','Synthetic change record','合成变更记录'),
     ('MoCTCA','Synthetic regional calibration and sample authority','合成地区校准与样本机构'),
     ('Mtrs','Synthetic distance unit','合成距离单位'),
     ('N/A','Synthetic unavailable value','合成不可用值'),
     ('NTSB','Synthetic research board','合成研究委员会')))
UNITS = ((('kHz','Synthetic frequency unit','合成频率单位'),
          ('ft','Synthetic length unit','合成长度单位'),
          ('m','Synthetic metric unit','合成公制单位'),
          ('m/s','Synthetic rate unit','合成速率单位'),
          ('v2','Synthetic version tag','合成版本标记')),)


def body_x(label):
    return 141 if label in ('MoCTCA','Mtrs') else 144


def variant_source(path,rotation=0,split=False,groups=GROUPS):
    with fitz.open() as pdf:
        for number,records in enumerate(groups,1):
            page=pdf.new_page(width=560,height=480)
            page.insert_text((72,50),f'Synthetic abbreviation variants {number}',fontname='tibo',fontsize=16)
            for index,(label,value,_) in enumerate(records):
                y=100+index*20.7
                if split and ' ' in label:
                    x=72
                    for part in label.split():
                        # No encoded whitespace: the source's positioned
                        # word gaps must still belong to the complete key.
                        page.insert_text((x,y),part,fontname='tibo' if part.endswith('.') else 'tiro',fontsize=12)
                        x+=fitz.get_text_length(part,fontname='tibo' if part.endswith('.') else 'tiro',fontsize=12)+3
                else:
                    page.insert_text((72,y-(.168 if index%2 else 0)),label,fontname='tiro',fontsize=12)
                page.insert_text((body_x(label),y),value,fontname='tiro',fontsize=12)
            page.insert_text((72,310),'An ordinary paragraph remains separate from all abbreviation rows.',fontname='tiro',fontsize=12)
            page.draw_rect((72,340,490,385),color=(.2,.3,.5),fill=(.9,.94,.98))
            page.insert_text((180,410),'Figure 1. Independent synthetic caption.',fontname='tiit',fontsize=9)
            page.insert_text((72,455),'Synthetic footnote stays separate.',fontname='tiro',fontsize=8)
            page.set_rotation(rotation)
        if len(groups)>1:
            pdf.set_toc([[1,'Synthetic final page',len(groups)]])
            pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(72,32,390,52),
                               'page':len(groups)-1,'to':fitz.Point(72,100)})
        pdf.save(path)


def legacy_detector(page,blocks,detect):
    """Partial 1.0.22-like cache: uppercase rows known, other keys untyped.

    Exact archived 1.0.22 is separately used for release A/B, not required
    in a source-only checkout. Do not guess apart partially covered blocks.
    """
    rows=[b for b in detect(page,blocks) if b.get('definition') and
          re.fullmatch(r'[A-Z][A-Z0-9]*(?:[-/+&.][A-Z0-9]+)*',b['definition']['label'])]
    def touched(old,row):return fitz.Rect(old['bbox']).intersects(fitz.Rect(row['bbox']))
    while rows:
        bad=[]
        for old in blocks:
            if not any(touched(old,row) for row in rows):continue
            chars=[c for line in parser.native_text_rows(page,old['bbox']) for span in line['spans']
                   for c in span['chars'] if c['c'].strip()]
            if any(not any(parser.inside(c['bbox'],row['bbox']) for row in rows) for c in chars):bad.append(old)
        if not bad:break
        rows=[row for row in rows if not any(touched(old,row) for old in bad)]
    return parser.reading_order([b for b in blocks if not any(touched(b,row) for row in rows)]+rows)


def translate(document,groups=GROUPS,cached=False):
    doc=copy.deepcopy(document);doc['translated_pages']=copy.deepcopy(doc['pages'])
    for page,target,records in zip(doc['pages'],doc['translated_pages'],groups):
        values={label:text for label,_,text in records}
        for source,new in zip(page['blocks'],target['blocks']):
            if source.get('definition'):
                text=values[source['definition']['label']]
                new['translation_text']=text;new['text']=text
            elif cached and 80<source['bbox'][1]<280:
                text=''
                for index,(label,_,value) in enumerate(records):
                    if parser.inside((body_x(label),96+index*20.7,body_x(label)+4,100+index*20.7),source['bbox']):
                        # The old B. list prefix was excluded from the
                        # translation request, leaving S. in the body text.
                        prefix=source.get('prefix',{}).get('text','')
                        key=label[len(prefix):].strip() if prefix and label.startswith(prefix) else label
                        text+=key+value
                field='translation_text' if 'translation_text' in source else 'text'
                new[field]=text;new['text']=text
    return doc


def fixture(path,rotation=0,cached=False,split=False,groups=GROUPS):
    variant_source(path,rotation,split,groups)
    if cached:
        detect=parser.native_definition_blocks
        with patch.object(parser,'native_definition_blocks',side_effect=lambda p,b:legacy_detector(p,b,detect)):
            document=parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    else:document=parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    return translate(document,groups,cached)


def check_variants(case,source,document,result,order='original-left',groups=GROUPS):
    check_fixed(case,source,document,result,order)
    placements=[p for p in result['report']['placements'] if p.get('definition')]
    case.assertEqual([p['definition']['label'] for p in placements],[r[0] for group in groups for r in group])
    with fitz.open(source) as before,fitz.open(result['translated']) as after:
        for number,records in enumerate(groups,1):
            a,b=before[number-1],after[number-1];a.set_rotation(0);b.set_rotation(0)
            for index,(label,_,text) in enumerate(records):
                placement=next(p for p in placements if p['page']==number and p['definition']['label']==label)
                case.assertEqual(placement['bbox'][0],body_x(label))
                case.assertEqual(placement['font_size'],12)
                box=fitz.Rect(placement['definition']['label_bbox'])
                aa=np.frombuffer(a.get_pixmap(clip=box).samples,dtype=np.uint8).astype(int)
                bb=np.frombuffer(b.get_pixmap(clip=box).samples,dtype=np.uint8).astype(int)
                case.assertLessEqual(np.abs(aa-bb).max(initial=0),2)
                case.assertEqual(renderer.normalize(b.get_text(clip=box+(-.1,-.1,.1,.1))),renderer.normalize(label))
                body=b.get_text(clip=fitz.Rect(placement['bbox'])+(-.1,-.1,.1,.1))
                case.assertEqual(renderer.normalize(body),renderer.normalize(text))
                spans=[s for block in b.get_text('dict',clip=fitz.Rect(placement['bbox']))['blocks']
                       if block['type']==0 for line in block['lines'] for s in line['spans']]
                case.assertTrue(all(abs(s['origin'][1]-(100+index*20.7))<.02 for s in spans))
            case.assertFalse(any(c[1]==0 for s in b.get_texttrace() for c in s['chars'] if not chr(c[0]).isspace()))


class DefinitionVariantTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='zhanhtran-definitions-123-')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_spaced_dotted_mixed_case_and_unit_keys_keep_source_rows_and_columns(self):
        source=self.root/'variants.pdf';doc=fixture(source)
        for page,records in zip(doc['pages'],GROUPS):
            rows=[b for b in page['blocks'] if b.get('definition')]
            self.assertEqual([b['definition']['label'] for b in rows],[r[0] for r in records])
            self.assertEqual([b['translation_text'] for b in rows],[r[1] for r in records])
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_partial_cache_restores_residual_list_key_and_merged_mtrs_without_mutation(self):
        source=self.root/'cached.pdf';doc=fixture(source,cached=True)
        b=next(b for b in doc['pages'][0]['blocks'] if b.get('prefix',{}).get('text')=='B.')
        self.assertTrue(b['translation_text'].startswith('S.'))
        self.assertTrue(any('MoCTCA' in b['text'] and 'Mtrs' in b['text'] for b in doc['pages'][2]['blocks']))
        snapshot=copy.deepcopy(doc)
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))
        self.assertEqual(doc,snapshot)

    def test_cached_key_spacing_is_not_required_in_the_model_response(self):
        source=self.root/'spaces.pdf';doc=fixture(source,cached=True)
        for page in doc['translated_pages']:
            for block in page['blocks']:
                for field in ('text','translation_text'):
                    if field in block:block[field]=block[field].replace('C of A','CofA').replace('B. S.','B.S.')
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_source_key_split_between_fonts_without_encoded_spaces_remains_whole(self):
        source=self.root/'split.pdf';doc=fixture(source,split=True)
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_long_key_uses_repeated_column_even_when_gutter_is_under_two_em(self):
        source=self.root/'narrow.pdf';doc=fixture(source,groups=(GROUPS[2],))
        row=next(b for b in doc['pages'][0]['blocks'] if b.get('definition',{}).get('label')=='MoCTCA')
        self.assertLess(row['definition']['body_bbox'][0]-row['definition']['label_bbox'][2],24)
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'),groups=(GROUPS[2],))

    def test_lowercase_and_single_character_units_need_no_uppercase_whitelist(self):
        source=self.root/'units.pdf';doc=fixture(source,groups=UNITS)
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'),groups=UNITS)

    def test_isolated_variant_can_use_already_recognised_cache_rows_as_column_anchors(self):
        groups=((GROUPS[0][0],GROUPS[0][4],GROUPS[0][2]),)
        source=self.root/'anchors.pdf';doc=fixture(source,cached=True,groups=groups)
        self.assertEqual(sum(bool(b.get('definition')) for b in doc['pages'][0]['blocks']),2)
        check_variants(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'),groups=groups)

    def test_missing_duplicate_reordered_or_empty_cached_mtrs_is_not_guessed(self):
        for kind in ('missing','duplicate','reordered','empty'):
            with self.subTest(kind=kind):
                source=self.root/(kind+'.pdf');doc=fixture(source,cached=True)
                b=next(b for b in doc['translated_pages'][2]['blocks'] if 'MoCTCA' in b['text'] and 'Mtrs' in b['text'])
                if kind=='missing':b['text']=b['text'].replace('Mtrs','')
                elif kind=='duplicate':b['text']+='Mtrs重复'
                elif kind=='reordered':b['text']='Mtrs合成距离单位MoCTCA合成地区校准与样本机构'
                else:b['text']=b['text'][:b['text'].index('Mtrs')+4]
                with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE') as error:
                    renderer.export_pdfs(source,doc,self.root/kind)
                self.assertEqual(error.exception.layout_failure,{'failed_page':3,'region':b['id']})
                self.assertFalse(list((self.root/kind).glob('*.pdf')))

    def test_regular_prose_columns_single_key_and_indented_list_are_not_glossaries(self):
        for mode in ('prose','columns','single','list'):
            with self.subTest(mode=mode),fitz.open() as pdf:
                page=pdf.new_page(width=600,height=400)
                if mode=='single':
                    page.insert_text((72,100),'Kg',fontsize=12);page.insert_text((144,100),'Synthetic isolated title',fontsize=12)
                elif mode=='list':
                    for i in range(3):page.insert_text((72,100+i*20),'A. A normal complete list sentence with no key/value columns.',fontsize=12)
                else:
                    for x in ((40,330) if mode=='columns' else (72,)):
                        for i in range(3):page.insert_text((x,100+i*20),'Mixed case prose remains a complete paragraph.',fontsize=12)
                blocks=[{'type':'paragraph','bbox':list(b[:4]),'text':b[4]} for b in page.get_text('blocks')]
                self.assertTrue(all(not b.get('definition') for b in parser.native_definition_blocks(page,blocks)))

    def test_rotated_three_page_variants_both_orders_navigation_and_source_unchanged(self):
        for rotation in (0,90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    source=self.root/f'{rotation}-{order}.pdf';doc=fixture(source,rotation)
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    result=renderer.export_pdfs(source,doc,self.root/source.stem,order=order)
                    check_variants(self,source,doc,result,order)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
                    with fitz.open(result['translated']) as pdf:
                        self.assertEqual(len(pdf),3);self.assertEqual(pdf.get_toc()[0][2],3)
                        self.assertEqual(pdf[0].get_links()[0]['page'],2)

    def test_long_mtrs_shrinks_only_the_value_leaving_native_key_and_next_row_alone(self):
        source=self.root/'long.pdf';doc=fixture(source,groups=(GROUPS[2],))
        b=next(b for b in doc['translated_pages'][0]['blocks'] if b.get('definition',{}).get('label')=='Mtrs')
        b['translation_text']='极长合成距离释义完整保留，不挤占下一行。'*60
        result=renderer.export_pdfs(source,doc,self.root/'output');check_fixed(self,source,doc,result)
        placement=next(p for p in result['report']['placements'] if p.get('definition',{}).get('label')=='Mtrs')
        self.assertLess(placement['scale'],.5)
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(b['translation_text']),renderer.normalize(pdf[0].get_text()))

    def test_corrupt_key_case_and_old_style_markers_still_fail_without_partial_files(self):
        for mode in ('case','styles'):
            with self.subTest(mode=mode):
                source=self.root/(mode+'.pdf');doc=fixture(source,cached=mode=='styles')
                if mode=='case':
                    b=next(b for b in doc['pages'][0]['blocks'] if b.get('definition',{}).get('label')=='C of A')
                    b['definition']['label']='C OF A';code='PDF_LAYOUT_INVALID'
                else:
                    b=next(b for b in doc['pages'][2]['blocks'] if 'MoCTCA' in b['text'] and 'Mtrs' in b['text'])
                    b['text_styles']=[{'id':'S0','size':12,'italic':True,'family':'serif'}];code='PDF_TRANSLATION_INCOMPLETE'
                with self.assertRaisesRegex(ValueError,code):renderer.export_pdfs(source,doc,self.root/mode)
                self.assertFalse(list((self.root/mode).glob('*.pdf')))


if __name__=='__main__':unittest.main()
