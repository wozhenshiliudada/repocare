"""Check the local kit's recorded file bytes. This is integrity checking, not a signature."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'delivery-manifest.json').read_text(encoding='utf-8'))
for row in manifest['files']:
    target = root / row['path']
    assert target.resolve().is_relative_to(root), 'Invalid recorded path'
    assert target.is_file() and not target.is_symlink(), 'Missing or linked delivery file'
    assert target.stat().st_size == row['bytes'], 'Delivery file size changed: ' + row['path']
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    assert digest.hexdigest() == row['sha256'], 'Delivery file changed: ' + row['path']
print(json.dumps({'verified_files': len(manifest['files']), 'model_requests': 0,
                  'signed': False, 'platform': manifest['platform']}))
