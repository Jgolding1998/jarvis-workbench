import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SiteBuildTests(unittest.TestCase):
    def test_output_with_private_or_unexpected_files_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            private = output / "private.env"
            private.write_text("fixture")
            with self.assertRaises(ValueError):
                builder.build(output)
            self.assertEqual(private.read_text(), "fixture")
            self.assertFalse((output / "index.html").exists())

    def test_hosted_export_contains_only_selected_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = builder.build(Path(directory))
            self.assertEqual({path.name for path in output.iterdir()}, set(builder.PAGES + builder.SETUP))
            content = (output / "setup.html").read_text(encoding="utf-8")
            self.assertIn('name="jarvis-mode" content="hosted"', content)
            self.assertNotIn('name="jarvis-mode" content="auto"', content)
            self.assertEqual(content, (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn('id="wizard"', content)
            self.assertIn('id="g-hand-open"', (output / "practice.html").read_text(encoding="utf-8"))
            self.assertFalse((output / "runtime").exists())
            self.assertFalse((output / "jarvis").exists())
            for name in ("index.html", "setup.html", "privacy.html"):
                self.assertIn('http-equiv="Content-Security-Policy"', (output / name).read_text(encoding="utf-8"))

    def test_source_directories_are_not_valid_output(self):
        for path in (ROOT, ROOT.parent, ROOT / "web", ROOT / "website"):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                builder.build(path)


if __name__ == "__main__":
    unittest.main()
