import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_windows_metadata", ROOT / "scripts/build_windows.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

HOME = b"public-build-home"
USER = b"public-build-user"
DATA = HOME + b" " + USER
TARGET = "vendor/numpy/__config__.py"


def record(data=DATA):
    return {TARGET: {"sha256": hashlib.sha256(data).hexdigest(), "wheel_filename": "fixture.whl",
                     "wheel_sha256": "a" * 64}}


class PublicBuildMetadataTests(unittest.TestCase):
    def audit(self, files, needles, metadata=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            classified = []
            with patch.object(builder, "PUBLIC_BUILD_METADATA", metadata) if metadata is not None else contextlib.nullcontext():
                findings = builder.audit_tree(root, needles, public_metadata=metadata, classified=classified)
            return findings, classified

    def test_reviewed_three_records_are_bound_to_current_manifest_wheels(self):
        manifest = json.loads(builder.MANIFEST_PATH.read_text())
        result = builder.reviewed_public_metadata(manifest)
        self.assertEqual(set(result), {"vendor/contourpy/util/_build_config.py", TARGET,
                                      "vendor/numpy-2.5.4.dist-info/DELVEWHEEL"})
        self.assertEqual(len({item["wheel_sha256"] for item in result.values()}), 2)
        result[TARGET]["sha256"] = "b" * 64
        self.assertNotEqual(result[TARGET], builder.PUBLIC_BUILD_METADATA[TARGET])

    def test_changed_or_unknown_wheel_pin_fails_before_any_build_step(self):
        original = json.loads(builder.MANIFEST_PATH.read_text())
        for field, replacement in (("sha256", "f" * 64), ("filename", "unreviewed.whl")):
            manifest = copy.deepcopy(original)
            pin = next(item for item in manifest["wheels"] if item["name"] == "numpy")
            pin[field] = replacement
            with self.subTest(field=field), patch.object(builder, "tools_python") as tools:
                with self.assertRaisesRegex(builder.BuildError, "wheel pins changed"):
                    builder.stage_bundle(Path("unused"), manifest, {}, {}, None, None)
                tools.assert_not_called()

    def test_exact_file_classifies_only_home_and_user_collisions(self):
        needles = [("build home path", HOME), ("build user name", USER)]
        findings, classified = self.audit({TARGET: DATA}, needles, record())
        self.assertEqual(findings, [])
        self.assertEqual(classified, [{"path": TARGET, "sha256": hashlib.sha256(DATA).hexdigest(),
                                      "labels": ["build home path", "build user name"]}])
        self.assertNotIn(HOME.decode(), json.dumps(classified))
        self.assertNotIn(USER.decode(), json.dumps(classified))

    def test_default_audit_and_other_paths_keep_identity_guards(self):
        needles = [("build home path", HOME), ("build user name", USER)]
        self.assertEqual(len(self.audit({TARGET: DATA}, needles)[0]), 2)
        findings, _ = self.audit({TARGET: DATA, "vendor/other.py": DATA}, needles, record())
        self.assertEqual(findings, ["vendor/other.py: contains build home path",
                                    "vendor/other.py: contains build user name"])

    def test_caller_cannot_add_an_unreviewed_path_or_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(builder.BuildError, "Unreviewed public"):
                builder.audit_tree(directory, [], public_metadata=record())
        findings = []
        builder.scan_bytes(TARGET, DATA, [("build home path", HOME)], findings, public_metadata=record())
        self.assertIn(TARGET + ": contains build home path", findings)

    def test_single_byte_modification_is_rejected_even_without_identity_match(self):
        for changed in (DATA + b"!", b"entirely different public text"):
            findings, classified = self.audit({TARGET: changed}, [("build home path", HOME)], record())
            self.assertIn(TARGET + ": reviewed public build metadata bytes changed", findings)
            self.assertEqual(classified, [])

    def test_missing_reviewed_metadata_fails_closed(self):
        findings, _ = self.audit({}, [], record())
        self.assertEqual(findings, [TARGET + ": reviewed public build metadata is missing"])

    def test_email_secret_environment_and_configured_needles_still_block_exact_bytes(self):
        needles = [(label, HOME) for label in ("git or configured email", "secret-like environment value",
                                              "configured audit value")]
        findings, classified = self.audit({TARGET: DATA}, needles, record())
        self.assertEqual(len(findings), 3)
        self.assertEqual(classified, [])

    def test_every_key_pattern_still_blocks_exact_reviewed_bytes(self):
        samples = {"nvidia-style key": b"nvapi-" + b"x" * 40, "openai-style key": b"sk-" + b"p" * 40,
                   "aws access key": b"AKIA" + b"A" * 16, "github token": b"ghp_" + b"g" * 36,
                   "google api key": b"AIza" + b"G" * 35, "slack token": b"xoxb-" + b"s" * 15,
                   "private key block": b"-" * 5 + b"BEGIN " + b"PRIVATE KEY" + b"-" * 5}
        self.assertEqual(set(samples), set(builder.SECRET_PATTERNS))
        for label, data in samples.items():
            with self.subTest(pattern=label):
                findings, _ = self.audit({TARGET: data}, [], record(data))
                self.assertIn(TARGET + ": matches " + label + " pattern", findings)

    def test_nested_zip_members_never_inherit_public_metadata_classification(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(TARGET, DATA)
        findings, classified = self.audit({TARGET: DATA, "vendor/archive.zip": buffer.getvalue()},
                                         [("build home path", HOME)], record())
        self.assertIn("vendor/archive.zip!" + TARGET + ": contains build home path", findings)
        self.assertEqual(len(classified), 1)

    def test_provenance_records_only_public_hashes_paths_and_narrow_policy(self):
        rows = builder.public_metadata_provenance(builder.PUBLIC_BUILD_METADATA)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["eligible_identity_labels"], ["build home path", "build user name"])
            self.assertEqual(len(row["sha256"]), 64)
        self.assertNotIn(HOME.decode(), json.dumps(rows))
        self.assertNotIn(USER.decode(), json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
