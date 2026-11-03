"""Unruled abbreviation rows: source keys and definitions must stay paired."""
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf as fitz

from tests.flow_geometry_test import ROOT, parser, renderer
from tests.fixed_layout_test import check_fixed


LABELS = ('SB','SOP','TAWS','TCSN','TSIB','TTSN','VNPR','VFR')
VALUES = ('Synthetic baseline','Synthetic observation procedure',
          'Synthetic reference sequence and calibration data', 'Test cycle count',
          'Synthetic reference sequence and calibration notes', 'Test elapsed time',
          'Synthetic reference sequence and calibration sites','Visual sample rule')
TRANSLATIONS = ('合成基线','合成观察流程','合成参照序列与校准数据','测试循环计数',
                '合成参照序列与校准记录','测试累计时间','合成参照序列与校准位置','可视样本规则')


def definition_source(path,rotation=0,mode='rows',wrapped=False,styled=False,next_page=False):
    anchors=[]
    with fitz.open() as pdf:
        page=pdf.new_page(width=560,height=480)
        page.insert_text((72,50),'Synthetic abbreviation columns',fontname='tibo',fontsize=16)
        y=100
        for index,(label,value) in enumerate(zip(LABELS,VALUES)):
            anchors.append(y);y+=41.4 if wrapped and index==2 else 20.7
        if mode=='columns':
            page.insert_text((72,100),'\n'.join(LABELS),fontname='tiro',fontsize=12,lineheight=1.725)
            page.insert_text((144,100),'\n'.join(VALUES),fontname='tiro',fontsize=12,lineheight=1.725)
        else:
            for index,(label,value,y) in enumerate(zip(LABELS,VALUES,anchors)):
                page.insert_text((72,y-(.168 if index%2 else 0)),label,fontname='tiro',fontsize=12)
                if styled and index==4:
                    lead="Synthetic coordinate 21\u00b0 12' N; "
                    page.insert_text((144,y),lead,fontname='tiro',fontsize=12)
                    x=144+fitz.get_text_length(lead,fontname='tiro',fontsize=12)
                    page.insert_text((x,y),'emphasized note',fontname='tiit',fontsize=12)
                else:
                    page.insert_text((144,y),value,fontname='tiro',fontsize=12)
                if wrapped and index==2:
                    page.insert_text((144,y+20.7),'A source-wrapped synthetic definition continues.',fontname='tiro',fontsize=12)
        page.insert_text((72,330),'An independent ordinary paragraph stays outside the definition list.',fontname='tiro',fontsize=12)
        page.draw_rect((72,355,490,390),color=(.2,.3,.5),fill=(.9,.94,.98))
        page.insert_text((190,410),'Figure 1. Synthetic independent caption.',fontname='tiit',fontsize=9)
        page.insert_text((72,455),'Synthetic footnote stays separate.',fontname='tiro',fontsize=8)
        page.set_rotation(rotation)
        if next_page:
            page=pdf.new_page(width=560,height=480)
            page.insert_text((72,70),'Synthetic next source page.',fontname='tiro',fontsize=12)
            page.set_rotation(rotation)
            pdf.set_toc([[1,'Synthetic next page',2]])
            pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(72,32,350,52),
                                'page':1,'to':fitz.Point(72,70)})
        pdf.save(path)
    return anchors


def translate(document,cached=False):
    doc=copy.deepcopy(document);doc['translated_pages']=copy.deepcopy(doc['pages'])
    for old,new in zip(doc['pages'][0]['blocks'],doc['translated_pages'][0]['blocks']):
        if old.get('definition'):
            index=LABELS.index(old['definition']['label'])
            text=TRANSLATIONS[index]
            if old.get('inline_math') or old.get('text_styles'):
                text='合成坐标 '+''.join(m+('强调记录' if re.fullmatch(r'\[\[S\d+\]\]',m) else '')
                    for m in re.findall(r'\[\[(?:/?S\d+|M\d+)\]\]',old['translation_text']))
            new['translation_text']=text;new['text']=text
        elif cached and 80<old['bbox'][1]<300:
            labels=[label for label in LABELS if re.search(r'\b'+label+r'\b',old['text'])]
            # Mimic the observed response: row keys and values concatenated
            # into one string, including merged TAWS/TCSN and TSIB/TTSN.
            new['text']=''.join(label+TRANSLATIONS[LABELS.index(label)] for label in labels)
    return doc


def fixture(path,rotation=0,cached=False,mode='rows',wrapped=False,styled=False,next_page=False):
    definition_source(path,rotation,mode,wrapped,styled,next_page)
    if cached:
        with patch.object(parser,'native_definition_blocks',side_effect=lambda page,blocks:list(blocks)):
            document=parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    else:document=parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    return translate(document,cached)


def check_definitions(case,source,document,result,order='original-left',styled=False):
    check_fixed(case,source,document,result,order)
    placements=[p for p in result['report']['placements'] if p.get('definition')]
    case.assertEqual(len(placements),len(LABELS))
    case.assertEqual([p['definition']['label'] for p in placements],list(LABELS))
    with fitz.open(source) as old,fitz.open(result['translated']) as new:
        old[0].set_rotation(0);new[0].set_rotation(0)
        for index,p in enumerate(placements):
            case.assertAlmostEqual(p['bbox'][0],144,places=3)
            label=fitz.Rect(p['definition']['label_bbox'])
            a=np.frombuffer(old[0].get_pixmap(clip=label).samples,dtype=np.uint8).astype(int)
            b=np.frombuffer(new[0].get_pixmap(clip=label).samples,dtype=np.uint8).astype(int)
            case.assertLessEqual(np.abs(a-b).max(initial=0),2)
            case.assertEqual(new[0].get_text(clip=label+(-.1,-.1,.1,.1)).strip(),LABELS[index])
            region=new[0].get_text(clip=fitz.Rect(p['bbox'])+(-.1,-.1,.1,.1))
            if styled and index==4:
                case.assertIn("21\u00b012'n",renderer.normalize(region))
                case.assertIn('强调记录',renderer.normalize(region))
            else:
                case.assertEqual(renderer.normalize(region),renderer.normalize(TRANSLATIONS[index]))
                case.assertTrue(all(word not in region for word in LABELS))
            case.assertFalse(any(c[1]==0 for s in new[0].get_texttrace() for c in s['chars'] if not chr(c[0]).isspace()))
        spans=[s for b in new[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
        for label in LABELS:
            case.assertEqual(sum(s['text'].strip()==label for s in spans),1,label)
        # TTSN's definition must be on its own source row, not after TSIB.
        a=next(p for p in placements if p['definition']['label']=='TSIB')
        b=next(p for p in placements if p['definition']['label']=='TTSN')
        case.assertLess(a['bbox'][3],b['bbox'][1])


class DefinitionRowTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='zhanhtran-definitions-122-')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_fresh_parse_preserves_every_key_row_and_description_column(self):
        source=self.root/'rows.pdf';doc=fixture(source)
        rows=[b for b in doc['pages'][0]['blocks'] if b.get('definition')]
        self.assertEqual([r['definition']['label'] for r in rows],list(LABELS))
        for row,value in zip(rows,VALUES):
            self.assertEqual(row['translation_text'],value)
            self.assertEqual(row['prefix']['kind'],'definition')
            self.assertEqual(len(row['source_lines']),1)
            self.assertEqual(row['body_left'],144)
            self.assertTrue(row['flow_blocked'])
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_columnwise_native_blocks_are_paired_by_row_not_read_as_separate_prose_columns(self):
        source=self.root/'columns.pdf';doc=fixture(source,mode='columns')
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_source_wrapped_value_stays_inside_its_own_key_and_next_row_does_not_join(self):
        source=self.root/'wrapped.pdf';doc=fixture(source,wrapped=True)
        row=next(b for b in doc['pages'][0]['blocks'] if b.get('definition',{}).get('label')=='TAWS')
        self.assertEqual(len(row['source_lines']),2)
        self.assertIn('source-wrapped',row['translation_text'])
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_cached_merged_rows_are_split_only_at_exact_native_keys_and_input_unchanged(self):
        source=self.root/'cached.pdf';doc=fixture(source,cached=True)
        self.assertTrue(any('TSIB' in b['text'] and 'TTSN' in b['text'] for b in doc['pages'][0]['blocks']))
        snapshot=copy.deepcopy(doc)
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))
        self.assertEqual(doc,snapshot)

    def test_cached_single_row_model_can_omit_the_native_key_without_losing_definition(self):
        source=self.root/'cached.pdf';doc=fixture(source,cached=True)
        b=next(b for b in doc['translated_pages'][0]['blocks'] if b['text'].startswith('SB'))
        b['text']=b['text'][2:]
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'))

    def test_missing_repeated_reordered_keys_and_empty_body_fail_not_silently_drop_ttsn(self):
        for kind in ('missing','repeated','reordered','empty','leading'):
            with self.subTest(kind=kind):
                source=self.root/(kind+'.pdf');doc=fixture(source,cached=True)
                b=next(b for b in doc['translated_pages'][0]['blocks'] if 'TSIB' in b['text'] and 'TTSN' in b['text'])
                if kind=='missing':b['text']=b['text'].replace('TTSN','')
                elif kind=='repeated':b['text']+='TTSN额外内容'
                elif kind=='reordered':b['text']='TTSN'+TRANSLATIONS[5]+'TSIB'+TRANSLATIONS[4]
                elif kind=='empty':b['text']='TSIB'+TRANSLATIONS[4]+'TTSN'
                else:b['text']='多余开头'+b['text']
                out=self.root/kind
                with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE') as error:
                    renderer.export_pdfs(source,doc,out)
                self.assertEqual(error.exception.layout_failure,{'failed_page':1,'region':b['id']})
                self.assertFalse(list(out.glob('*.pdf')))
                self.assertFalse(list(out.glob('*.tmp')))

    def test_fresh_coordinates_and_mixed_styles_stay_paired_inside_the_correct_value(self):
        source=self.root/'styles.pdf';doc=fixture(source,styled=True)
        row=next(b for b in doc['pages'][0]['blocks'] if b.get('definition',{}).get('label')=='TSIB')
        self.assertEqual(len(row['inline_math']),1)
        self.assertEqual(len(row['text_styles']),1)
        check_definitions(self,source,doc,renderer.export_pdfs(source,doc,self.root/'output'),styled=True)

    def test_missing_fresh_body_symbol_cannot_be_healed_by_another_row(self):
        source=self.root/'missing.pdf';doc=fixture(source,styled=True)
        b=next(b for b in doc['translated_pages'][0]['blocks'] if b.get('inline_math'))
        b['translation_text']=b['translation_text'].replace('[[M0]]','')
        with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE'):
            renderer.export_pdfs(source,doc,self.root/'output')
        self.assertFalse(list((self.root/'output').glob('*.pdf')))

    def test_legacy_styled_merge_is_not_guessed_or_silently_deprived_of_markers(self):
        source=self.root/'legacy-styled.pdf';doc=fixture(source,cached=True)
        b=next(b for b in doc['pages'][0]['blocks'] if 'TSIB' in b['text'] and 'TTSN' in b['text'])
        b['text_styles']=[{'id':'S0','size':12,'italic':True,'family':'serif'}]
        with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE') as error:
            renderer.export_pdfs(source,doc,self.root/'output')
        self.assertEqual(error.exception.layout_failure,{'failed_page':1,'region':b['id']})
        self.assertFalse(list((self.root/'output').glob('*.pdf')))

    def test_corrupted_row_geometry_or_label_cannot_be_accepted_as_complete(self):
        for kind in ('label','box','source-body'):
            with self.subTest(kind=kind):
                source=self.root/(kind+'.pdf');doc=fixture(source)
                b=next(b for b in doc['pages'][0]['blocks'] if b.get('definition',{}).get('label')=='TTSN')
                if kind=='label':b['definition']['label']='TTXX'
                elif kind=='box':b['definition']['body_bbox'][2]-=40
                else:b['text']=b['text'].replace('elapsed','omitted')
                with self.assertRaisesRegex(ValueError,'PDF_LAYOUT_INVALID'):
                    renderer.export_pdfs(source,doc,self.root/kind)
                self.assertFalse(list((self.root/kind).glob('*.pdf')))

    def test_acronyms_in_plain_prose_and_single_wide_title_are_not_definition_lists(self):
        for kind in ('prose','single','columns'):
            with self.subTest(kind=kind),fitz.open() as pdf:
                page=pdf.new_page(width=600,height=500)
                if kind=='prose':
                    for i,text in enumerate(('ABC begins an ordinary source paragraph with no key/value gutter.',
                                             'DEF continues the normal prose column with a standard word space.')):
                        page.insert_text((72,100+i*20.7),text,fontname='tiro',fontsize=12)
                elif kind=='single':
                    page.insert_text((72,100),'ABC',fontname='tiro',fontsize=12)
                    page.insert_text((144,100),'An isolated wide synthetic display title.',fontname='tiro',fontsize=12)
                else:
                    for x in (40,330):
                        for i in range(3):page.insert_text((x,100+i*20.7),'The genuine prose column is separate.',fontname='tiro',fontsize=12)
                blocks=[{'type':'paragraph','bbox':list(b[:4]),'text':b[4]} for b in page.get_text('blocks')]
                self.assertTrue(all(not b.get('definition') for b in parser.native_definition_blocks(page,blocks)))

    def test_partly_covered_native_block_is_never_dropped_or_truncated(self):
        source=self.root/'partial.pdf';definition_source(source)
        with fitz.open(source) as pdf:
            box=fitz.Rect(65,80,510,345)
            block={'id':'partial','type':'paragraph','bbox':list(box),'text':pdf[0].get_text(clip=box)}
            result=parser.native_definition_blocks(parser.PageAnalysis(pdf[0]),[block])
            self.assertEqual(result,[block])

    def test_rotations_both_orders_source_unchanged_page_count_navigation(self):
        for rotation in (0,90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    source=self.root/f'{rotation}-{order}.pdf';doc=fixture(source,rotation,next_page=True)
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    result=renderer.export_pdfs(source,doc,self.root/source.stem,order=order)
                    check_definitions(self,source,doc,result,order)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
                    with fitz.open(result['translated']) as pdf:
                        self.assertEqual(len(pdf),2)
                        self.assertEqual(pdf.get_toc()[0][2],2)
                        self.assertEqual(pdf[0].get_links()[0]['page'],1)

    def test_long_definition_only_shrinks_its_own_value_not_key_or_other_rows(self):
        source=self.root/'long.pdf';doc=fixture(source)
        row=next(b for b in doc['translated_pages'][0]['blocks'] if b.get('definition',{}).get('label')=='TTSN')
        row['translation_text']='极长合成释义仍完整保留，不能挤进其他条目。'*60
        result=renderer.export_pdfs(source,doc,self.root/'output')
        check_fixed(self,source,doc,result)
        p=next(p for p in result['report']['placements'] if p.get('definition',{}).get('label')=='TTSN')
        self.assertLess(p['scale'],.5)
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(row['translation_text']),renderer.normalize(pdf[0].get_text()))

    def test_isolated_parser_and_cached_export_keep_single_json_and_progress_protocol(self):
        source=self.root/'cli.pdf';doc=fixture(source,cached=True)
        path=self.root/'translated.json';path.write_text(json.dumps(doc),encoding='utf-8')
        for script,args in [('local_parser.py',['--output',str(self.root/'fresh-parsed')]),
                            ('local_pdf.py',['--document',str(path),'--output',str(self.root/'output')])]:
            proc=subprocess.run([sys.executable,'-I',str(ROOT/'src'/script),'--input',str(source),*args,'--progress'],
                capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertEqual(len(proc.stdout.strip().splitlines()),1)
            self.assertTrue(json.loads(proc.stdout)['ok'])
            self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)


if __name__=='__main__':unittest.main()
