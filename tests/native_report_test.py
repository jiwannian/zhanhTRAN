"""Cross-process report polling must tolerate unfinished UTF-8/JSON writes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_report', ROOT / 'tools/native_report.py')
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class NativeReportTests(unittest.TestCase):
    def test_partial_write_then_complete_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            for raw in [b'', b'{', b'{"result":', b'{"result":"RUNNING","text":"\xe8']:
                path.write_bytes(raw)
                self.assertIsNone(REPORT.read_report(path))
            path.write_bytes(b'{"result":"PASS"}')
            self.assertEqual(REPORT.read_report(path), {'result': 'PASS'})

    def test_missing_report_is_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(REPORT.read_report(Path(folder) / 'missing.json'))

    def test_running_report_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            path.write_text('{"result":"RUNNING"}', encoding='utf-8')
            self.assertEqual(REPORT.read_report(path), {'result': 'RUNNING'})

    def test_non_write_errors_propagate(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                REPORT.read_report(Path(folder))
