import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent


class BuildToolsLockTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "scripts" / "windows-build-tools.lock").read_text(encoding="ascii")
        self.entries = {}
        for block in self.text.split("\n\n"):
            if not block or block.startswith("#"):
                continue
            lines = block.splitlines()
            pin = re.fullmatch(r"([a-z0-9][a-z0-9-]*)==([0-9A-Za-z.!+_-]+) \\", lines[0])
            self.assertIsNotNone(pin, "Build tools require exact version pins")
            name, version = pin.groups()
            self.assertNotIn(name, self.entries, "Duplicate build-tool requirement")
            hashes = []
            for index, line in enumerate(lines[1:]):
                digest = re.fullmatch(r"    --hash=sha256:([0-9a-f]{64})( \\)?", line)
                self.assertIsNotNone(digest, "Only SHA256 wheel hash flags are allowed")
                self.assertEqual(bool(digest[2]), index < len(lines) - 2, "Invalid requirement continuation")
                hashes.append(digest[1])
            self.assertTrue(hashes, "Every build dependency needs a wheel hash")
            self.assertEqual(len(hashes), len(set(hashes)), "Duplicate wheel hash")
            self.entries[name] = (version, hashes)

    def test_lock_has_only_exact_pins_and_hashes_without_external_sources(self):
        self.assertGreater(len(self.entries), 2)
        self.assertNotIn("pip", self.entries)
        self.assertNotIn("://", self.text)
        self.assertNotIn("@", self.text)
        flags = re.findall(r"--[a-z-]+", self.text)
        self.assertTrue(flags)
        self.assertEqual(set(flags), {"--hash"})

    def test_manifest_build_tool_versions_match_the_lock(self):
        manifest = json.loads((ROOT / "scripts" / "windows_bundle_manifest.json").read_text(encoding="utf-8"))
        for name, version in manifest["build_tools"].items():
            self.assertIn(name, self.entries)
            self.assertEqual(self.entries[name][0], version)

    def test_signature_verification_transitives_are_explicitly_locked(self):
        required = {"cryptography", "cffi", "pycparser", "tuf", "securesystemslib", "rfc3161-client", "sigstore-models", "sigstore-rekor-types",
                    "pydantic", "pydantic-core", "requests", "urllib3", "certifi", "idna"}
        self.assertTrue(required <= self.entries.keys())


if __name__ == "__main__":
    unittest.main()
