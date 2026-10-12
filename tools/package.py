"""Build a deterministic XPI from an explicit source allowlist."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(destination=None):
    manifest = json.loads((ROOT / 'manifest.json').read_text('utf-8'))
    target = Path(destination) if destination else ROOT / 'dist' / f"zhanhTRAN-{manifest['version']}.xpi"
    members = {name: ROOT / name for name in ['manifest.json', 'bootstrap.js', 'LICENSE', 'THIRD_PARTY_NOTICES.md']}
    for path in (ROOT / 'icons').glob('*.png'):
        members['icons/' + path.name] = path
    for path in (ROOT / 'src').rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix not in {'.js', '.py', '.txt', '.xhtml'}:
            continue
        relative = path.relative_to(ROOT / 'src').as_posix()
        prefix = '' if path.suffix == '.xhtml' or relative in {'custom-provider-window.js', 'settings.js'} else 'scripts/'
        members['chrome/content/' + prefix + relative] = path
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(members.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 12, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    with zipfile.ZipFile(target) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert archive.testzip() is None
    return {'path': str(target), 'members': len(members), 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False))
