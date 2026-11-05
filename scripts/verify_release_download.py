"""Verify all custom release asset bytes and provenance, without extracting code."""
import hashlib
from pathlib import Path
import re
import sys
import tarfile
import zipfile


def verify(directory, commit, version="0.5.0"):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a numeric release version")
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected a full immutable commit SHA')
    root = Path(directory)
    expected = {f'tenant_rag-{version}-py3-none-any.whl', f'tenant_rag-{version}.tar.gz',
                'offline-eval.json', 'evidence-verification.txt', 'SOURCE_COMMIT.txt'}
    if {p.name for p in root.iterdir()} != expected | {'SHA256SUMS'}:
        raise ValueError('Unexpected or missing release assets')
    if any(p.is_symlink() or not p.is_file() for p in root.iterdir()):
        raise ValueError('Expected regular release assets')
    hashes = {}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        if not match or match[2] in hashes:
            raise ValueError('Invalid or duplicate checksum entry')
        hashes[match[2]] = match[1]
    if set(hashes) != expected:
        raise ValueError('Incomplete checksum coverage')
    for name, digest in hashes.items():
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Expected regular release assets')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Checksum mismatch: {name}')
    if (root/'SOURCE_COMMIT.txt').read_text().strip() != commit:
        raise ValueError('Release source commit mismatch')
    with zipfile.ZipFile(root/f'tenant_rag-{version}-py3-none-any.whl') as wheel:
        metadata = wheel.read(f'tenant_rag-{version}.dist-info/METADATA').decode()
        if f'\nVersion: {version}\n' not in metadata:
            raise ValueError('Wheel version mismatch')
    with tarfile.open(root/f'tenant_rag-{version}.tar.gz') as source:
        metadata = source.extractfile(f'tenant_rag-{version}/PKG-INFO').read().decode()
        if f'\nVersion: {version}\n' not in metadata:
            raise ValueError('Source version mismatch')
    print('All five asset checksums, exact source commit and package versions verified.')


if __name__ == '__main__':
    verify(*sys.argv[1:])
