"""Source paragraph continuity; synthetic PDFs only, one or two pages each."""
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

import pymupdf as fitz

from tests.flow_geometry_test import ROOT, parser, renderer
from tests.fixed_layout_test import check_fixed


TEXT = '合成正文连续排版，不按句号或模型换行拆成独立文本框。'*4
NEXT = '下一段保留原文的段落边界，不接在上一段句末。'
SOURCE = ('Synthetic sample notes describe the calibration sequence and the reference readings. '
          'The next sentence continues in the same paragraph without an artificial break. '
          'All measured values are retained, and the operator records the complete result. '
          'These notes use only invented data for a compact layout check. '
          'Short translated fragments must share their original paragraph region, '
          'rather than occupy one tiny fixed box for each source line.')


def wrap(text, width=468, indent=0):
    lines, current = [], ''
    font = fitz.Font('tiro')
    for word in text.split():
        candidate = (current+' '+word).strip()
        if current and font.text_length(candidate,fontsize=12)>width-(indent if not lines else 0):
            lines.append(current); current = word
        else:
            current = candidate
    return lines+[current]


def paragraph_source(path, rotation=0, next_page=False, indented=False, protected=False, last_label='C.'):
    lines = wrap(SOURCE,indent=24 if indented else 0)
    with fitz.open() as pdf:
        page = pdf.new_page(width=612,height=792)
        page.insert_text((72,80),'Synthetic paragraph continuity',fontname='tibo',fontsize=16)
        for i,line in enumerate(lines):
            x,y = 72+(24 if indented and i==0 else 0),150+i*20.7
            if protected:
                # Each old line starts again at M0 and S0. Rejoining a cache
                # must rebase both marker namespaces without losing content.
                first = "Coordinate 21\u00b0 12' N; "
                page.insert_text((x,y),first,fontname='tiro',fontsize=12)
                x += fitz.Font('tiro').text_length(first,fontsize=12)
                emphasis = 'reference emphasis '
                page.insert_text((x,y),emphasis,fontname='tiit',fontsize=12)
                x += fitz.Font('tiit').text_length(emphasis,fontsize=12)
                rest = wrap('sample data remain in the same continuous paragraph for all measured trials.',width=540-x)[0]
                page.insert_text((x,y),rest,fontname='tiro',fontsize=12)
            else:
                page.insert_text((x,y),line,fontname='tiro',fontsize=12)
        bottom = 150+(len(lines)-1)*20.7
        # A genuine paragraph break, followed by a tiny terminal label. The
        # latter is source content, not a list with an empty translated body.
        tail = [wrap('Synthetic following paragraph covers the three reference sites and all of the '
                     'independent reference groups A, B and site')[0]+' site']
        for i,line in enumerate(tail+[last_label]):
            page.insert_text((72,bottom+26.7+i*20.7),line,fontname='tiro',fontsize=12)
        page.draw_rect((72,490,540,575),color=(.2,.3,.5),fill=(.9,.94,.98))
        page.insert_text((210,595),'Figure 1. Synthetic chart.',fontname='tiit',fontsize=9)
        page.insert_text((72,725),'Synthetic separate footnote.',fontname='tiro',fontsize=8)
        page.set_rotation(rotation)
        if next_page:
            page = pdf.new_page(width=612,height=792)
            page.insert_text((72,80),'Synthetic next page.',fontname='tiro',fontsize=12)
            page.set_rotation(rotation)
            pdf.set_toc([[1,'Synthetic next page',2]])
            pdf[0].insert_link({'kind':fitz.LINK_GOTO,'from':fitz.Rect(72,65,310,85),
                                'page':1,'to':fitz.Point(72,80)})
        pdf.save(path)
    return len(lines)


def translate(document, cached=False):
    doc = copy.deepcopy(document)
    doc['translated_pages'] = copy.deepcopy(doc['pages'])
    for page in doc['translated_pages']:
        for b in page['blocks']:
            y = b['bbox'][1]
            if 130<y<400:
                if cached:
                    field = 'translation_text' if 'translation_text' in b else 'text'
                    markers = re.findall(r'\[\[(?:/?S\d+|M\d+)\]\]',b.get(field,''))
                    styled = ''.join(m+('强调' if re.fullmatch(r'\[\[S\d+\]\]',m) else '') for m in markers)
                    b[field] = '连续的合成译文。'+styled+'继续正文。'
                    b['text'] = b[field]
                else:
                    b['text'] = TEXT[:30]+'\n\n'+TEXT[30:65]+'\n'+TEXT[65:] if y<160 else NEXT
            elif b['text'].startswith('Figure 1'):
                b['text'] = '图1：独立图注保留位置。'
            elif b['text'].startswith('Synthetic separate'):
                b['text'] = '合成脚注保持原位。'
            elif b['text'].startswith('Synthetic next'):
                b['text'] = '下一页仍另起。'
    return doc


def fixture(path, rotation=0, cached=False, indented=False, protected=False, next_page=False, last_label='C.'):
    paragraph_source(path,rotation,next_page,indented,protected,last_label)
    if cached:
        # Exact pre-repair line-wise structure, without embedding a baseline
        # parser or any user document in the fixtures.
        with patch.object(parser,'native_paragraph_groups',side_effect=lambda blocks:[[b] for b in blocks]):
            doc = parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    else:
        doc = parser.parse_document(path,path.parent/(path.stem+'-parsed'))
    return translate(doc,cached)


def check_paragraph(test,source,document,result,order='original-left',cached=False):
    check_fixed(test,source,document,result,order)
    placements = [p for p in result['report']['placements'] if 130<p['bbox'][1]<400]
    test.assertEqual(len(placements),2)
    test.assertGreater(placements[0]['source_line_count'],3)
    test.assertEqual(placements[1]['source_line_count'],2)
    test.assertEqual(placements[0]['scale'],1)
    if cached:
        test.assertGreater(len(placements[0]['ids']),1)
    else:
        test.assertEqual(len(placements[0]['ids']),1)
    with fitz.open(source) as original,fitz.open(result['translated']) as output:
        original[0].set_rotation(0);output[0].set_rotation(0)
        test.assertEqual(original[0].get_pixmap(clip=(72,490,540,575)).samples,
                         output[0].get_pixmap(clip=(72,490,540,575)).samples)
        lines = [line for b in output[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines']
                 if placements[0]['bbox'][1]-.1<line['bbox'][1]<placements[0]['bbox'][3]+.1]
        test.assertTrue(lines)
        # The first new line consumes the source paragraph's column width,
        # not merely the independently translated first source line.
        test.assertGreater(max(s['bbox'][2] for s in lines[0]['spans']),500)
        test.assertTrue(all(abs(s['size']-12)<.001 for line in lines for s in line['spans']))
        if not cached:
            test.assertIn(renderer.normalize(TEXT),renderer.normalize(output[0].get_text()))
            test.assertIn(NEXT,renderer.normalize(output[0].get_text()))


class ParagraphFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zhanhtran-paragraph-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_visual_line_blocks_become_one_translation_unit_per_source_paragraph(self):
        source = self.root/'source.pdf'
        count = paragraph_source(source)
        document = parser.parse_document(source,self.root/'parsed')
        body = [b for b in document['pages'][0]['blocks'] if 130<b['bbox'][1]<400]
        self.assertEqual(len(body),2)
        self.assertEqual(body[0]['source_fragments'],count)
        self.assertEqual(len(body[0]['source_lines']),count)
        self.assertEqual(' '.join(body[0]['text'].split()),SOURCE)
        self.assertTrue(body[1]['text'].endswith('C.'))
        self.assertNotIn('prefix',body[1])

    def test_sentence_end_and_model_newlines_do_not_force_new_output_lines(self):
        source = self.root/'source.pdf';document = fixture(source)
        result = renderer.export_pdfs(source,document,self.root/'out')
        check_paragraph(self,source,document,result)

    def test_old_linewise_cache_is_joined_without_mutating_input_or_requesting_translation(self):
        source = self.root/'source.pdf';document = fixture(source,cached=True)
        original = copy.deepcopy(document)
        result = renderer.export_pdfs(source,document,self.root/'out')
        check_paragraph(self,source,document,result,cached=True)
        self.assertEqual(document,original)

    def test_first_line_indent_is_preserved_instead_of_becoming_another_column(self):
        source = self.root/'source.pdf';document = fixture(source,indented=True)
        block = next(b for b in document['pages'][0]['blocks'] if 130<b['bbox'][1]<160)
        self.assertAlmostEqual(block['first_line_indent'],24,places=3)
        result = renderer.export_pdfs(source,document,self.root/'out')
        check_paragraph(self,source,document,result)
        with fitz.open(result['translated']) as pdf:
            spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines']
                     for s in line['spans'] if s['text'].startswith(TEXT[:8])]
            self.assertAlmostEqual(spans[0]['origin'][0],96,places=3)

    def test_uniform_shrink_is_for_the_whole_paragraph_and_does_not_expand(self):
        source = self.root/'source.pdf';document = fixture(source)
        block = next(b for b in document['translated_pages'][0]['blocks'] if 130<b['bbox'][1]<160)
        block['text'] = TEXT*15
        result = renderer.export_pdfs(source,document,self.root/'out')
        check_fixed(self,source,document,result)
        p = next(p for p in result['report']['placements'] if p['ids']==[block['id']])
        self.assertLess(p['scale'],.5)
        with fitz.open(result['translated']) as pdf:
            self.assertIn(renderer.normalize(block['text']),renderer.normalize(pdf[0].get_text()))

    def test_cached_symbol_and_paired_style_ids_are_rebased_and_rendered(self):
        source = self.root/'source.pdf';document = fixture(source,cached=True,protected=True)
        result = renderer.export_pdfs(source,document,self.root/'out')
        p = next(p for p in result['report']['placements'] if 130<p['bbox'][1]<160)
        count = len(p['ids'])
        self.assertGreater(count,3)
        self.assertEqual(len(p['protected']),count)
        self.assertEqual(len({t['id'] for t in p['protected']}),count)
        self.assertEqual(len(p['text_styles']),count)
        self.assertEqual(len({s['id'] for s in p['text_styles']}),count)
        with fitz.open(result['translated']) as pdf:
            self.assertEqual(pdf[0].get_text().count("21\u00b0 12' N"),count)
            spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                     for line in b['lines'] for s in line['spans']]
            self.assertEqual(renderer.normalize(pdf[0].get_text()).count('强调'),count)
            self.assertTrue(any(s['flags']&2 and '强调' in s['text'] for s in spans))

    def test_fresh_paragraph_retains_inline_symbols_and_styles_in_one_translation_unit(self):
        source = self.root/'source.pdf';count = paragraph_source(source,protected=True)
        document = parser.parse_document(source,self.root/'parsed')
        body = next(b for b in document['pages'][0]['blocks'] if 130<b['bbox'][1]<160)
        self.assertEqual(len(body['inline_math']),count)
        self.assertEqual(len(body['text_styles']),count)
        document['translated_pages'] = copy.deepcopy(document['pages'])
        target = next(b for b in document['translated_pages'][0]['blocks'] if b['id']==body['id'])
        target['translation_text'] = re.sub(r'(?<=\]\])[^\[]*|^[^\[]+', '合成正文',body['translation_text'])
        result = renderer.export_pdfs(source,document,self.root/'out')
        p = next(p for p in result['report']['placements'] if p['ids']==[body['id']])
        self.assertEqual(len(p['protected']),count)
        self.assertEqual(len(p['text_styles']),count)

    def test_legacy_wrapped_letter_is_content_not_an_empty_list(self):
        source = self.root/'source.pdf';document = fixture(source,cached=True)
        for field in ('pages','translated_pages'):
            b = next(b for b in document[field][0]['blocks'] if b['text']=='C.' or b['bbox'][1]>260 and b['bbox'][1]<290)
            b.update(text='C.',translation_text='',prefix={'text':'C.','bbox':b['bbox']})
        result = renderer.export_pdfs(source,document,self.root/'out')
        placements = [p for p in result['report']['placements'] if 240<p['bbox'][1]<290]
        self.assertEqual(len(placements),1)
        self.assertEqual(placements[0]['source_line_count'],2)
        with fitz.open(result['translated']) as pdf:
            self.assertIn('C.',pdf[0].get_text())

    def test_each_genuine_column_reassembles_without_crossing_the_gutter(self):
        source = self.root/'columns.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=612,height=792)
            for x in (40,330):
                for i,line in enumerate(wrap(SOURCE,width=210)):
                    page.insert_text((x,100+i*20.7),line,fontname='tiro',fontsize=12)
            pdf.save(source)
        document = parser.parse_document(source,self.root/'parsed')
        blocks = document['pages'][0]['blocks']
        self.assertEqual(len(blocks),2)
        self.assertGreater(len(blocks[0]['source_lines']),5)
        self.assertGreater(len(blocks[1]['source_lines']),5)
        self.assertLess(blocks[0]['bbox'][2],260)
        self.assertGreaterEqual(blocks[1]['bbox'][0],330)

    def test_missing_cached_symbols_or_styles_cannot_be_healed_by_a_neighbor(self):
        source = self.root/'source.pdf';document = fixture(source,cached=True,protected=True)
        for marker in ('[[M0]]','[[/S0]]'):
            with self.subTest(marker=marker):
                broken = copy.deepcopy(document)
                b = next(b for b in broken['translated_pages'][0]['blocks'] if 130<b['bbox'][1]<160)
                b['translation_text'] = b['translation_text'].replace(marker,'')
                folder = self.root/str(len(marker))
                with self.assertRaisesRegex(ValueError,'PDF_TRANSLATION_INCOMPLETE') as failed:
                    renderer.export_pdfs(source,broken,folder)
                self.assertEqual(failed.exception.layout_failure,{'failed_page':1,'region':b['id']})
                self.assertEqual(list(folder.glob('*.pdf')),[])

    def test_true_breaks_lists_columns_captions_and_code_stay_separate(self):
        for variant in ('short','gap','indent','list','caption','column','code','math','obstacle'):
            with self.subTest(variant=variant),fitz.open() as pdf:
                page = pdf.new_page(width=612,height=792)
                text = wrap(SOURCE)[0]
                first = text if variant!='short' else 'A short complete paragraph.'
                second = 'Another independent source region.'
                font = 'cour' if variant=='code' else 'tiro'
                page.insert_text((72,150),first,fontname=font,fontsize=12)
                x,y = (330 if variant=='column' else 96 if variant=='indent' else 72),(
                    180 if variant=='gap' else 170.7)
                if variant=='list': second = '- '+second
                if variant=='caption': second = 'Figure 1. '+second
                if variant=='math': second = 'x = 1 if searching'
                page.insert_text((x,y),second,fontname=font,fontsize=12)
                blocks = []
                for b in page.get_text('dict')['blocks']:
                    block = {'type':'paragraph','bbox':list(b['bbox']),
                             'text':'\n'.join(''.join(s['text'] for s in line['spans']) for line in b['lines'])}
                    block.update(parser.native_metadata(page,block));blocks.append(block)
                if variant=='obstacle':
                    blocks.append({'type':'figure','bbox':[72,153,540,157]})
                self.assertTrue(all(len(g)==1 for g in parser.native_paragraph_groups(blocks)))

    def test_rotations_bilingual_orders_source_bytes_and_navigation(self):
        for rotation in (0,90,180,270):
            for order in ('original-left','translated-left'):
                with self.subTest(rotation=rotation,order=order):
                    folder = self.root/f'{rotation}-{order}';folder.mkdir()
                    source = folder/'source.pdf';document = fixture(source,rotation,next_page=True)
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    result = renderer.export_pdfs(source,document,folder/'out',order=order)
                    check_paragraph(self,source,document,result,order)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
                    with fitz.open(result['translated']) as pdf:
                        self.assertEqual(len(pdf),2)
                        self.assertEqual(pdf[0].get_links()[0]['page'],1)
                        self.assertEqual(pdf.get_toc()[0][2],2)

    def test_isolated_cli_keeps_single_json_and_progress(self):
        source = self.root/'source.pdf';paragraph_source(source)
        proc = subprocess.run([sys.executable,'-I',str(ROOT/'src/local_parser.py'),'--input',str(source),
            '--output',str(self.root/'parsed'),'--progress'],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)
        doc = translate(json.loads((self.root/'parsed/document.json').read_text('utf-8')))
        file = self.root/'translated.json';file.write_text(json.dumps(doc),encoding='utf-8')
        proc = subprocess.run([sys.executable,'-I',str(ROOT/'src/local_pdf.py'),'--input',str(source),
            '--document',str(file),'--output',str(self.root/'out'),'--progress'],
            capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()),1)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertIn('ZHANHTRAN_PROGRESS',proc.stderr)


if __name__=='__main__':
    unittest.main()
