"""Synthetic archive fixtures for the release verifier, never executed code."""
import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import tarfile
import unittest
import zipfile
from scripts.verify_release_download import verify


class ReleaseAssetsTests(unittest.TestCase):
    version = "0.5.0"

    def verify(self, directory, commit):
        verify(directory, commit, self.version)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sha = 'a'*40
        with zipfile.ZipFile(self.root/f'tenant_rag-{self.version}-py3-none-any.whl', 'w') as z:
            z.writestr(f'tenant_rag-{self.version}.dist-info/METADATA', f'Name: tenant-rag\nVersion: {self.version}\n')
        with tarfile.open(self.root/f'tenant_rag-{self.version}.tar.gz', 'w:gz') as t:
            raw = f'Name: tenant-rag\nVersion: {self.version}\n'.encode()
            member = tarfile.TarInfo(f'tenant_rag-{self.version}/PKG-INFO')
            member.size = len(raw)
            t.addfile(member, io.BytesIO(raw))
        (self.root/'offline-eval.json').write_text('{}')
        (self.root/'evidence-verification.txt').write_text('fixture only')
        (self.root/'SOURCE_COMMIT.txt').write_text(self.sha+'\n')
        self.refresh()

    def refresh(self):
        paths = sorted(p for p in self.root.iterdir() if p.name != 'SHA256SUMS')
        (self.root/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in paths))

    def test_valid_complete_assets(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.verify(self.root, self.sha)

    def test_wrong_source_and_invalid_expected_sha(self):
        for sha in ('b'*40, 'main', '../bad'):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                self.verify(self.root, sha)

    def test_altered_asset(self):
        (self.root/'offline-eval.json').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            self.verify(self.root, self.sha)

    def test_missing_and_extra_assets(self):
        (self.root/'unexpected').write_text('x')
        with self.assertRaises(ValueError):
            self.verify(self.root, self.sha)
        (self.root/'unexpected').unlink()
        (self.root/'offline-eval.json').unlink()
        with self.assertRaises(ValueError):
            self.verify(self.root, self.sha)

    def test_duplicate_missing_and_path_checksums(self):
        p = self.root/'SHA256SUMS'; original = p.read_text()
        for content in (original+original.splitlines()[0]+'\n', original.split('\n',1)[1], original.replace('offline-eval.json','../outside')):
            p.write_text(content)
            with self.assertRaises(ValueError):
                self.verify(self.root, self.sha)

    def test_package_version_mismatch(self):
        with zipfile.ZipFile(self.root/f'tenant_rag-{self.version}-py3-none-any.whl', 'w') as z:
            z.writestr(f'tenant_rag-{self.version}.dist-info/METADATA', 'Name: tenant-rag\nVersion: 0.4.0\n')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'Wheel version'):
            self.verify(self.root, self.sha)

    def test_checksum_symlink_rejected(self):
        p=self.root/'SHA256SUMS'; content=p.read_text();p.unlink()
        target=Path(self.tmp.name).parent/(self.root.name+'-checksums')
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        target.write_text(content)
        p.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.verify(self.root, self.sha)

    def test_invalid_version(self):
        for version in ("../0.5.1", "v0.5.1", "0.5.1\n", "0.5", "0.5.1/extra"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'version'):
                verify(self.root, self.sha, version)

    def test_wrong_expected_version(self):
        with self.assertRaisesRegex(ValueError, 'assets'):
            verify(self.root, self.sha, "0.6.0")

    def test_source_version_mismatch(self):
        with tarfile.open(self.root/f'tenant_rag-{self.version}.tar.gz', 'w:gz') as t:
            raw = b'Name: tenant-rag\nVersion: 0.4.0\n'
            member = tarfile.TarInfo(f'tenant_rag-{self.version}/PKG-INFO')
            member.size = len(raw)
            t.addfile(member, io.BytesIO(raw))
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'Source version'):
            self.verify(self.root, self.sha)


class Release051AssetsTests(ReleaseAssetsTests):
    version = "0.5.1"


class LegacyReleaseVerifierTests(ReleaseAssetsTests):
    def verify(self, directory, commit):
        # Existing release workflows omit the version argument.
        verify(directory, commit)
