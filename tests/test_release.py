import json
from pathlib import Path
import tempfile
import unittest

from tools.release_metadata import release_metadata, verify_manifest


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "packarr/src/packarr").mkdir(parents=True)
        (self.root / "packarr/config.yaml").write_text(
            'version: "1.2.3"\nimage: ghcr.io/example/packarr\narch:\n  - amd64\n  - aarch64\nstartup: services\n')
        (self.root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
        (self.root / "packarr/src/packarr/__init__.py").write_text('__version__ = "1.2.3"\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_one_version_drives_both_home_assistant_and_registry(self):
        self.assertEqual(release_metadata(self.root), {"image": "ghcr.io/example/packarr", "version": "1.2.3"})

    def test_mismatched_runtime_version_cannot_be_published(self):
        (self.root / "packarr/src/packarr/__init__.py").write_text('__version__ = "1.2.2"\n')
        with self.assertRaisesRegex(ValueError, "disagree"):
            release_metadata(self.root)

    def test_missing_registry_image_cannot_trigger_device_build(self):
        p = self.root / "packarr/config.yaml"
        p.write_text(p.read_text().replace('image: ghcr.io/example/packarr\n', ''))
        with self.assertRaises(ValueError):
            release_metadata(self.root)

    def test_both_runtime_platforms_are_required(self):
        good = {"manifests": [{"platform": {"os": "linux", "architecture": arch}} for arch in ["amd64", "arm64"]]}
        self.assertTrue(verify_manifest(good))
        good["manifests"].pop()
        with self.assertRaises(ValueError):
            verify_manifest(good)

    def test_home_assistant_architectures_cannot_drift(self):
        p = self.root / "packarr/config.yaml"
        p.write_text(p.read_text().replace('  - aarch64\n', ''))
        with self.assertRaisesRegex(ValueError, "architectures"):
            release_metadata(self.root)
