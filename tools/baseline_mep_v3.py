"""Freeze the executable baseline before V3 edits; no Revit model changes."""
import hashlib
import json
import zipfile
from pathlib import Path


def capture(root, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'source.zip'
    if archive.exists():
        raise FileExistsError('La línea base ya existe: ' + str(archive))
    sources = sorted(p for p in root.rglob('*') if p.is_file()
                     and p.suffix in ('.py', '.yaml', '.xaml', '.json', '.csv')
                     and not any(v in p.parts for v in ('__pycache__', 'data', 'artifacts', 'baselines', 'docs')))
    manifest = {}
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
        for p in sources:
            name = p.relative_to(root).as_posix()
            content = p.read_bytes()
            z.writestr(name, content)
            manifest[name] = hashlib.sha256(content).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    return {'files': len(manifest), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}


if __name__ == '__main__':
    base = Path(__file__).resolve().parents[1]
    print(json.dumps(capture(base, base / 'tests/baselines/v3-h0-20261106')))
