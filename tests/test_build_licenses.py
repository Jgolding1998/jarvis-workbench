import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_windows_licenses", ROOT / "scripts/build_windows.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
TEXT = b"Synthetic license fixture.\n"


def supplement(name="fixture.txt"):
    return {"file": name, "sha256": hashlib.sha256(TEXT).hexdigest(),
            "url": "https://www.gnu.org/licenses/gpl-3.0.txt"}


class BuildLicenseTests(unittest.TestCase):
    def test_all_six_checked_in_texts_match_manifest_and_need_no_network(self):
        items = json.loads(builder.MANIFEST_PATH.read_text())["license_supplements"]
        self.assertEqual(len(items), 6)
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download", side_effect=AssertionError("Network forbidden")):
            for item in items:
                target = builder.fetch_license_supplement(item, Path(directory) / "cache")
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), item["sha256"])
                self.assertEqual(target.read_bytes(), (ROOT / "scripts/licenses" / item["file"]).read_bytes())
        self.assertIn(b"scripts/licenses/* -text\n", (ROOT / ".gitattributes").read_bytes().replace(b"\r\n", b"\n"))

    def test_only_missing_copy_uses_original_fixed_url_and_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root, item = Path(directory), supplement()
            with patch.object(builder, "download", return_value=root / "cached.txt") as download:
                result = builder.fetch_license_supplement(item, root / "cache", source_dir=root / "missing")
            self.assertEqual(result, root / "cached.txt")
            download.assert_called_once_with(item["url"], root / "cache/fixture.txt", item["sha256"], 1024 * 1024)

    def test_tampered_local_copy_fails_without_fallback_or_cache_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download") as download:
            root = Path(directory)
            (root / "fixture.txt").write_bytes(TEXT + b"changed")
            with self.assertRaisesRegex(builder.BuildError, "SHA-256"):
                builder.fetch_license_supplement(supplement(), root / "cache", source_dir=root)
            download.assert_not_called()
            self.assertFalse((root / "cache").exists())

    def test_nonfiles_links_reparse_points_and_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download") as download:
            root, original = Path(directory), Path.lstat
            target = root / "fixture.txt"
            target.write_bytes(TEXT)
            variants = [dict(st_mode=stat.S_IFDIR), dict(st_mode=stat.S_IFLNK),
                        dict(st_mode=stat.S_IFREG, st_file_attributes=0x400), dict(st_mode=stat.S_IFREG, st_nlink=2)]
            for change in variants:
                fields = dict(st_mode=stat.S_IFREG, st_file_attributes=0, st_nlink=1, st_size=len(TEXT))
                fields.update(change)
                def lookup(path):
                    return SimpleNamespace(**fields) if path == target else original(path)
                with self.subTest(change=change), patch.object(Path, "lstat", lookup), self.assertRaises(builder.BuildError):
                    builder.fetch_license_supplement(supplement(), root / "cache", source_dir=root)
            download.assert_not_called()

    def test_oversized_local_copy_is_rejected_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download") as download:
            root = Path(directory)
            (root / "fixture.txt").write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(builder.BuildError, "byte limit"):
                builder.fetch_license_supplement(supplement(), root / "cache", source_dir=root)
            download.assert_not_called()

    def test_invalid_manifest_filename_hash_or_url_cannot_select_a_path(self):
        variants = [{**supplement(), "file": name} for name in ("../fixture.txt", "C:/fixture.txt", "a/b.txt", "a\\b.txt", "..txt", "x.py")]
        variants += [{**supplement(), "sha256": "0"}, {**supplement(), "url": "https://example.invalid/license.txt"}]
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download") as download:
            for item in variants:
                with self.subTest(item=item), self.assertRaisesRegex(builder.BuildError, "Invalid license"):
                    builder.fetch_license_supplement(item, Path(directory) / "cache", source_dir=directory)
            download.assert_not_called()

    def test_unsafe_cache_target_or_parent_fails_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.txt").write_bytes(TEXT)
            cache = root / "cache"
            cache.mkdir()
            target = cache / "fixture.txt"
            target.mkdir()
            with self.assertRaisesRegex(builder.BuildError, "cache target"):
                builder.fetch_license_supplement(supplement(), cache, source_dir=root)
            target.rmdir()
            original = Path.lstat
            def lookup(path):
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400) if path == cache else original(path)
            with patch.object(Path, "lstat", lookup), self.assertRaisesRegex(builder.BuildError, "cache path"):
                builder.fetch_license_supplement(supplement(), cache, source_dir=root)
            self.assertFalse(target.exists())

    def test_cache_uses_verified_buffer_and_rechecks_source_on_every_use(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "download") as download:
            root = Path(directory)
            source = root / "fixture.txt"
            source.write_bytes(TEXT)
            cache = root / "cache"
            cache.mkdir()
            (cache / "fixture.txt").write_bytes(b"corrupted cache")
            reader = builder._checked_license_bytes
            def read_then_change(path, digest):
                data = reader(path, digest)
                source.write_bytes(b"changed after verification")
                return data
            with patch.object(builder, "_checked_license_bytes", side_effect=read_then_change):
                result = builder.fetch_license_supplement(supplement(), cache, source_dir=root)
            self.assertEqual(result.read_bytes(), TEXT)
            with self.assertRaisesRegex(builder.BuildError, "SHA-256"):
                builder.fetch_license_supplement(supplement(), cache, source_dir=root)
            download.assert_not_called()
            self.assertEqual(result.read_bytes(), TEXT)


if __name__ == "__main__":
    unittest.main()
