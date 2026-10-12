"""Packaging tests use disposable outputs, never a user's installed plugin."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package_tool', ROOT / 'tools/package.py')
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_reproducible_complete_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = [Path(directory) / name for name in ['first.xpi', 'second.xpi']]
            result = package.build(first)
            package.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(result['sha256'], hashlib.sha256(first.read_bytes()).hexdigest())
            with zipfile.ZipFile(first) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['name'], 'zhanhTRAN')
                self.assertEqual(manifest['version'], '1.0.1')
                self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
                self.assertIn('LICENSE', archive.namelist())
                self.assertIn('chrome/content/scripts/output-settings.js', archive.namelist())
                self.assertIn('chrome/content/scripts/vendor/eventsource-parser.LICENSE.txt', archive.namelist())
                self.assertTrue(all(not n.startswith(('tests/', 'logs/', 'qa/')) for n in archive.namelist()))
                for source in (ROOT / 'src').rglob('*'):
                    if not source.is_file() or source.suffix not in {'.js', '.py', '.txt', '.xhtml'}:
                        continue
                    relative = source.relative_to(ROOT / 'src').as_posix()
                    prefix = '' if source.suffix == '.xhtml' or relative in {'custom-provider-window.js', 'settings.js'} else 'scripts/'
                    self.assertEqual(archive.read('chrome/content/' + prefix + relative), source.read_bytes())
