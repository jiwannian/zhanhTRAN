"""Compare the 1.0.1 parser with the working parser on a small synthetic PDF."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_fixture(path):
    with fitz.open() as pdf:
        for page_number in range(3):
            page = pdf.new_page(width=520, height=720)
            page.insert_text((45, 45), f'Synthetic page {page_number + 1}', fontsize=16)
            for index in range(18):
                page.insert_text((45, 76 + index * 33),
                                 f'Synthetic paragraph {index + 1}: a local parsing timing sample with plain text.',
                                 fontsize=10.5)
        pdf.save(path)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--repetitions', type=int, default=4)
    cli.add_argument('--report', type=Path)
    args = cli.parse_args()
    if not 2 <= args.repetitions <= 20:
        cli.error('repetitions must be between 2 and 20')
    with tempfile.TemporaryDirectory(prefix='zhanhtran-benchmark-') as folder:
        folder = Path(folder)
        source = folder / 'synthetic.pdf'
        before = folder / 'before.py'
        before.write_bytes(subprocess.check_output(['git', 'show', 'HEAD:src/local_parser.py'], cwd=ROOT))
        make_fixture(source)
        modules = {'before': load('parser_before', before),
                   'after': load('parser_after', ROOT / 'src/local_parser.py')}
        timings = {'before': [], 'after': []}
        for repetition in range(args.repetitions):
            for version in (('before', 'after') if repetition % 2 == 0 else ('after', 'before')):
                start = time.perf_counter()
                document = modules[version].parse_document(source, folder / f'{version}-{repetition}')
                timings[version].append(round(time.perf_counter() - start, 4))
                assert len(document['pages']) == 3
        old, new = (statistics.median(timings[key]) for key in ('before', 'after'))
        result = {'source': '3-page synthetic digital PDF, 18 short paragraphs per page',
                  'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'seconds': timings, 'median_seconds': {'before': round(old, 4), 'after': round(new, 4)},
                  'speedup': round(old / new, 2)}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
