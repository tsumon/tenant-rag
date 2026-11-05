"""Explicit setup-only downloads from pinned official sources, with hash checks.

No inference or private data is sent. No Python/pickle model files are fetched.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def verify(path, meta):
    if path.stat().st_size != meta['size']:
        return False
    algorithm = 'sha256' if 'lfs' in meta or 'digest' in meta else 'sha1'
    digest = hashlib.new(algorithm)
    if algorithm == 'sha1':
        digest.update(f"blob {meta['size']}\0".encode())
    with path.open('rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            digest.update(block)
    expected = (meta['lfs']['sha256'] if 'lfs' in meta else
                meta['digest'].removeprefix('sha256:') if 'digest' in meta else meta['blobId'])
    return digest.hexdigest() == expected


def fetch(url, destination, meta):
    if destination.is_file() and verify(destination, meta):
        print('Already verified:', destination.name, flush=True)
        return
    if destination.exists():
        raise RuntimeError(f'Refusing to overwrite an unverified file: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as dst:
            temporary = Path(dst.name)
            with urllib.request.urlopen(url, timeout=180) as src:
                for block in iter(lambda: src.read(1024 * 1024), b''):
                    dst.write(block)
        if not verify(temporary, meta):
            raise RuntimeError(f'Official asset hash mismatch: {destination.name}')
        temporary.replace(destination)
        print('Downloaded and verified:', destination.name, flush=True)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'model')
    parser.add_argument('--wheel-only', action='store_true')
    args = parser.parse_args()
    release = json.loads((ROOT / 'release-manifest.json').read_text())
    for asset in release['assets']:
        fetch(asset['browser_download_url'], ROOT / 'wheel' / asset['name'], asset)
    if args.wheel_only:
        return
    manifest = json.loads((ROOT / 'model-manifest.json').read_text())
    base = f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}/"
    for name, meta in manifest['files'].items():
        fetch(base + name, args.model_dir / name, meta)


if __name__ == '__main__':
    main()
