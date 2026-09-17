"""Build a source-only handoff; never include runtime configuration or data."""
from pathlib import Path
import difflib
import hashlib
import json
import subprocess
import tarfile
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
gis = root / 'tochka-gis/current'
source_archive = root / 'tochka-gis.tar.gz'
new_files = [
    'integration-routes.ts', 'integration-openapi.ts', 'lib/integration-config.ts',
    'tests/integration-config.test.ts',
    'prisma/migrations/20260916090000_integration_api/migration.sql',
]
existing = ['server.ts', 'prisma/schema.prisma', 'lib/db.ts']
payload = {p.name: p.read_bytes() for p in (root / 'handoff/gis-integration').glob('*.md')}
manifest = {'source_archive_sha256': hashlib.file_digest(source_archive.open('rb'), 'sha256').hexdigest(),
            'source': 'Provided source archive, not an upstream Git revision', 'files': {}}
patch = ''
with tarfile.open(source_archive) as original:
    for name in existing:
        stream = original.extractfile('tochka-gis/current/' + name)
        if stream is None:
            raise RuntimeError('Missing baseline: ' + name)
        before = stream.read()
        after = (gis / name).read_bytes()
        payload['baseline/' + name] = before
        payload['reference/' + name] = after
        patch += ''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                                            fromfile='a/' + name, tofile='b/' + name))
for name in new_files:
    payload['files/' + name] = (gis / name).read_bytes()
payload['patches/existing-files.patch'] = patch.encode()
# Verify this patch against only the selected original files in a disposable directory.
with tempfile.TemporaryDirectory(prefix='gis-handoff-check-') as temporary:
    base = Path(temporary)
    for name in existing:
        destination = base / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload['baseline/' + name])
    applied = subprocess.run(['patch', '-p1', '--batch'], input=patch, text=True, cwd=base, capture_output=True)
    if applied.returncode:
        raise RuntimeError(applied.stdout + applied.stderr)
    for name in existing:
        assert (base / name).read_bytes() == payload['reference/' + name], name
validation = ['Patch applied to baseline and matches reference: PASS']
for command in [['npx', 'bun', 'run', 'typecheck'], ['npx', 'bun', 'test', 'tests/integration-config.test.ts']]:
    result = subprocess.run(command, cwd=gis, capture_output=True, text=True)
    validation.append('$ ' + ' '.join(command) + '\n' + result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(validation[-1])
payload['validation.txt'] = '\n\n'.join(validation).encode()
for name, content in sorted(payload.items()):
    manifest['files'][name] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
payload['MANIFEST.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
output = root / 'handoff/gis-integration-codex-20260916.zip'
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, content in sorted(payload.items()):
        archive.writestr('gis-integration-handoff/' + name, content)
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(payload)
print(str(output))
print('Files:', len(payload), 'Bytes:', output.stat().st_size)
print('SHA-256:', hashlib.file_digest(output.open('rb'), 'sha256').hexdigest())
