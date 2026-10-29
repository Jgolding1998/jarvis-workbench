import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
import zipfile

from jarvis import bundle, install_tools

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_windows", ROOT / "scripts" / "build_windows.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

MODEL = b"fixture hand model bytes"
PACKAGES = {"opencv-contrib-python": "4.13.0.92", "mediapipe": "1.2.0", "vosk": "0.3.45", "sounddevice": "0.5.6", "srt": "3.5.3", "numpy": "2.5.4"}


def make_bundle(base, manifest_changes=None, dist_names=None):
    """Create a fake portable layout: runtime/python.exe, vendor/*.dist-info, app/bundle-manifest.json and model."""
    base = Path(base)
    (base / "runtime").mkdir(parents=True)
    (base / "runtime" / "python.exe").write_bytes(b"MZ fixture")
    for name, version in (dist_names or PACKAGES).items():
        dist = base / "vendor" / f"{name.replace('-', '_')}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    app = base / "app"
    (app / "models").mkdir(parents=True)
    (app / "models" / "hand_landmarker.task").write_bytes(MODEL)
    manifest = {"schema": bundle.SCHEMA, "platform": "win-amd64", "python": "3.14.10",
                "features": {"camera": ["opencv-contrib-python"], "hand": ["opencv-contrib-python", "mediapipe"], "voice": ["vosk", "sounddevice", "srt"]},
                "packages": PACKAGES, "hand_model_sha256": hashlib.sha256(MODEL).hexdigest(), "voice_model_included": False,
                "source": {"commit": "a" * 40, "dirty": False}}
    manifest.update(manifest_changes or {})
    (app / bundle.MANIFEST_NAME).write_text(json.dumps(manifest))
    return app, base / "runtime" / "python.exe"


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(path, files, tamper=None):
    """Build a minimal wheel with a correct RECORD unless tamper names a file to corrupt after hashing."""
    files = dict(files)
    files.setdefault("demo-1.0.dist-info/METADATA", b"Name: demo\nVersion: 1.0\n")
    rows = io.StringIO()
    writer = csv.writer(rows)
    for name, data in files.items():
        writer.writerow([name, record_hash(data), len(data)])
    writer.writerow(["demo-1.0.dist-info/RECORD", "", ""])
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data + (b"!" if name == tamper else b""))
        archive.writestr("demo-1.0.dist-info/RECORD", rows.getvalue())
    return path


class BundleModuleTests(unittest.TestCase):
    def test_valid_bundle_reports_ready_features_for_its_own_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python = make_bundle(directory)
            self.assertTrue(bundle.is_bundled(app, executable=python))
            for feature in bundle.FEATURES:
                self.assertTrue(bundle.feature_ready(app, feature, executable=python), feature)
            info = bundle.runtime_info(app, executable=python)
            self.assertTrue(info["bundled"])
            self.assertEqual(info["bundle_python"], "3.14.10")
            self.assertTrue(info["hand_model_included"])
            self.assertFalse(info["voice_model_included"])
            self.assertEqual(info["source_commit"], "a" * 12)

    def test_other_interpreters_are_not_treated_as_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            app, _python = make_bundle(directory)
            other = Path(directory) / "elsewhere.exe"
            other.write_bytes(b"MZ")
            self.assertFalse(bundle.is_bundled(app, executable=other))
            self.assertFalse(bundle.feature_ready(app, "camera", executable=other))
            self.assertFalse(bundle.runtime_info(app, executable=other)["bundled"])

    def test_plain_checkout_is_not_bundled(self):
        self.assertFalse(bundle.is_bundled(ROOT))
        self.assertFalse(any(bundle.feature_ready(ROOT, feature) for feature in bundle.FEATURES))
        self.assertEqual(bundle.runtime_info(ROOT)["features"], {feature: False for feature in bundle.FEATURES})

    def test_runtime_info_never_contains_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python = make_bundle(directory)
            text = json.dumps(bundle.runtime_info(app, executable=python))
            self.assertNotIn(str(Path(directory)), text)
            self.assertNotIn(Path(directory).name, text)
            self.assertNotIn(str(Path.home()), text)

    def test_tampered_model_or_missing_package_disables_only_that_feature(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python = make_bundle(directory, dist_names={k: v for k, v in PACKAGES.items() if k != "vosk"})
            (app / "models" / "hand_landmarker.task").write_bytes(MODEL + b"x")
            self.assertTrue(bundle.feature_ready(app, "camera", executable=python))
            self.assertFalse(bundle.feature_ready(app, "hand", executable=python))
            self.assertFalse(bundle.feature_ready(app, "voice", executable=python))
            self.assertFalse(bundle.feature_ready(app, "microphone", executable=python))
            self.assertFalse(bundle.runtime_info(app, executable=python)["hand_model_included"])

    def test_wrong_package_version_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python = make_bundle(directory, dist_names={**PACKAGES, "opencv-contrib-python": "4.12.0"})
            self.assertFalse(bundle.feature_ready(app, "camera", executable=python))

    def test_invalid_manifests_are_rejected(self):
        cases = [{"schema": "other/1"}, {"extra": 1}, {"voice_model_included": True}, {"platform": "linux"},
                 {"features": {"hand": ["mediapipe", "missing"]}}, {"hand_model_sha256": "abc"},
                 {"features": {"hand": ["mediapipe"]}, "hand_model_sha256": None}, {"source": {"commit": "x", "dirty": False}},
                 {"packages": {"Bad Name": "1"}}]
        for change in cases:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                app, python = make_bundle(directory, change)
                with self.assertRaises(bundle.BundleError):
                    bundle.load_manifest(app)
                self.assertFalse(bundle.is_bundled(app, executable=python))

    def test_duplicate_keys_and_non_finite_numbers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app, python = make_bundle(directory)
            path = app / bundle.MANIFEST_NAME
            text = path.read_text()
            path.write_text(text[:-1] + ', "schema": "jarvis-windows-bundle/1"}')
            self.assertFalse(bundle.is_bundled(app, executable=python))
            path.write_text(text.replace('"dirty": false', '"dirty": NaN'))
            self.assertFalse(bundle.is_bundled(app, executable=python))
            path.write_bytes(b"{" + b" " * bundle.MAX_MANIFEST_BYTES + b"}")
            self.assertFalse(bundle.is_bundled(app, executable=python))
            path.write_bytes(b"[" * 1200 + b"0" + b"]" * 1200)
            with self.assertRaises(bundle.BundleError):
                bundle.load_manifest(app)
            self.assertFalse(bundle.is_bundled(app, executable=python))


class BuildManifestTests(unittest.TestCase):
    def test_real_build_tools_lock_is_accepted_by_the_installer_validator(self):
        manifest = builder.load_build_manifest(install_tools=install_tools)
        self.assertEqual(builder.validate_tools_lock(builder.TOOLS_LOCK_PATH, manifest["build_tools"]), builder.sha256_file(builder.TOOLS_LOCK_PATH))

    def test_reviewed_build_manifest_matches_app_pins(self):
        manifest = builder.load_build_manifest(builder.MANIFEST_PATH, install_tools)
        self.assertTrue(manifest["python"]["url"].startswith("https://www.python.org/ftp/python/"))
        self.assertEqual(manifest["hand_model"]["sha256"], install_tools.MODEL_SHA256)
        for name, version in install_tools.PACKAGES.items():
            self.assertEqual(manifest["_versions"][name], version)
        self.assertEqual([item["name"] for item in manifest["sdists"]], ["srt"])

    def test_unofficial_sources_and_pin_drift_are_rejected(self):
        original = json.loads(builder.MANIFEST_PATH.read_text(encoding="utf-8"))

        def mutated(change):
            data = json.loads(json.dumps(original))
            change(data)
            return data

        cases = [lambda d: d["python"].update(url="https://mirror.example/python-3.14.10-embed-amd64.zip"),
                 lambda d: d["python"].update(sha256="0" * 63),
                 lambda d: d["python"].update(pth_name="python313._pth"),
                 lambda d: d["sdists"].append({"name": "other", "version": "1", "url": "https://files.pythonhosted.org/packages/x.tar.gz", "sha256": "0" * 64}),
                 lambda d: d["hand_model"].update(url="https://example.com/model.task"),
                 lambda d: next(w for w in d["wheels"] if w["name"] == "mediapipe").update(version="0.10.0"),
                 lambda d: d["features"].update(voice=["vosk", "unpinned"])]
        for index, change in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "manifest.json"
                path.write_text(json.dumps(mutated(change)))
                with self.assertRaises((builder.BuildError, KeyError)):
                    builder.load_build_manifest(path, install_tools)

    def test_pth_isolates_the_import_path(self):
        pin = builder.load_build_manifest(builder.MANIFEST_PATH)["python"]
        lines = builder.pth_text(pin).splitlines()
        self.assertEqual(lines, [pin["stdlib_zip"], ".", "../app", "../vendor"])
        self.assertFalse(any("site" in line for line in lines))

    def test_launcher_uses_bundled_isolated_python(self):
        text = (ROOT / builder.LAUNCHER).read_text(encoding="utf-8")
        self.assertIn('"%~dp0runtime\\python.exe" -I -m jarvis.launcher %*', text)
        self.assertNotIn("pip", text.lower())
        self.assertNotIn("PYTHONPATH", text)


class SourceSelectionTests(unittest.TestCase):
    INCLUDE = ["jarvis/*.py", "web/*", "docs/*.md", "LICENSE", "README.md"]

    def test_tracked_allowlist_and_reviewed_extras(self):
        tracked = ["jarvis/server.py", "web/app.js", "tests/test_server.py", "website/index.html", "README.md", "jarvis/sub/deep.py"]
        files = builder.select_app_files(tracked, ["jarvis/bundle.py"], self.INCLUDE, ["jarvis/bundle.py"])
        self.assertEqual(files, ["README.md", "jarvis/bundle.py", "jarvis/server.py", "web/app.js"])

    def test_unreviewed_new_module_stops_the_build_until_shipped_or_deferred(self):
        with self.assertRaisesRegex(builder.BuildError, "jarvis/quickstart.py"):
            builder.select_app_files(["jarvis/server.py"], ["jarvis/quickstart.py"], self.INCLUDE, [])
        self.assertEqual(builder.select_app_files(["jarvis/server.py"], ["jarvis/quickstart.py"], self.INCLUDE, [], deferred=["jarvis/quickstart.py"]),
                         ["jarvis/server.py"])
        with self.assertRaises(builder.BuildError):
            builder.select_app_files([], ["jarvis/quickstart.py"], self.INCLUDE, ["jarvis/quickstart.py"], deferred=["jarvis/quickstart.py"])

    def test_ignored_or_out_of_scope_extras_are_refused(self):
        with self.assertRaisesRegex(builder.BuildError, "ignored"):
            builder.select_app_files([], [], self.INCLUDE, [".env"])
        with self.assertRaisesRegex(builder.BuildError, "allowlist"):
            builder.select_app_files([], ["tests/new.py"], self.INCLUDE, ["tests/new.py"])

    def test_missing_tracked_file_is_an_error(self):
        with self.assertRaises(builder.BuildError):
            builder.select_app_files(["jarvis/server.py"], [], self.INCLUDE, [], exists=lambda _path: False)

    def test_forbidden_paths(self):
        bad = ["app/.env", "app/.env.local", "app/runtime/setup-config.json", "app/models/voice-en/am/final.mdl", "app/jarvis/__pycache__/x.pyc",
               "app/tests/test_server.py", "vendor/pkg/module.pyc", "app/private.pem", "app/.git/config", "app/work/notes.md", "vendor/x/id_rsa"]
        for path in bad:
            self.assertIsNotNone(builder.forbidden_reason(path), path)
        good = ["app/models/hand_landmarker.task", "vendor/certifi/cacert.pem", "vendor/numpy/_core/tests/test_x.py", "runtime/python.exe", "app/web/app.js"]
        for path in good:
            self.assertIsNone(builder.forbidden_reason(path), path)


class AuditTests(unittest.TestCase):
    def test_generic_credential_canaries_are_detected_everywhere(self):
        needles = [("build home path", b"canary-home-0f3a"), ("build home path", "canary-home-0f3a".encode("utf-16-le"))]
        fake_key = "sk-" + "Q" * 20 + "canary" + "7" * 20
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Jarvis-Windows"
            (root / "app").mkdir(parents=True)
            (root / "app" / "clean.txt").write_text("nothing to see")
            (root / "app" / "home.txt").write_text("built in C:/canary-home-0F3A/x")
            (root / "vendor").mkdir()
            (root / "vendor" / "wide.dll").write_bytes("canary-home-0f3a".encode("utf-16-le"))
            (root / "app" / "key.json").write_text(json.dumps({"key": fake_key}))
            (root / "vendor" / "nvidia.dll").write_bytes(("nvapi-" + "N" * 40).encode())
            (root / "app" / "block.txt").write_text("-----BEGIN " + "PRIVATE KEY-----")
            with zipfile.ZipFile(root / "runtime.zip", "w") as nested:
                nested.writestr("inner.txt", "AKIA" + "Z" * 16)
            findings = builder.audit_tree(root, needles)
        text = "\n".join(findings)
        for expected in ("app/home.txt: contains build home path", "vendor/wide.dll: contains build home path", "app/key.json: matches openai-style key",
                         "vendor/nvidia.dll: matches nvidia-style key", "app/block.txt: matches private key block", "runtime.zip!inner.txt: matches aws access key"):
            self.assertIn(expected, text)
        self.assertNotIn("clean.txt", text)

    def test_clean_tree_has_no_findings_and_secret_values_come_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "app" / "README.md").write_text("Jarvis")
            self.assertEqual(builder.audit_tree(root, [("x", b"canary-value-9b1d")]), [])
        labels = {label for label, _needle in builder.audit_needles(["canary@example.invalid", "canary-extra-value"])}
        self.assertIn("build home path", labels)
        self.assertIn("git or configured email", labels)
        self.assertIn("configured audit value", labels)


class WheelInstallTests(unittest.TestCase):
    def test_a_smoke_input_inside_work_is_refused_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            archive = work / "smoke" / "preserve.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"preserve")
            with self.assertRaisesRegex(builder.BuildError, "outside"):
                builder.main(["--smoke-only", str(archive), "--work", str(work)])
            self.assertEqual(archive.read_bytes(), b"preserve")

    def test_built_srt_python_must_match_the_verified_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.tar.gz"
            payload = {"srt.py": b"VALUE = 1\n", "srt_tools/__init__.py": b""}
            with tarfile.open(source, "w:gz") as archive:
                for name, content in {**payload, "srt_tools/tests/test_source_only.py": b"source tests are not runtime modules\n"}.items():
                    member = tarfile.TarInfo("srt-3.5.3/" + name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            wheel = Path(directory) / "srt.whl"
            for changed in (False, True):
                with zipfile.ZipFile(wheel, "w") as archive:
                    for name, content in payload.items():
                        archive.writestr(name, b"VALUE = 2\n" if changed and name == "srt.py" else content)
                if changed:
                    with self.assertRaisesRegex(builder.BuildError, "differs"):
                        builder.verify_srt_payload(source, wheel)
                else:
                    builder.verify_srt_payload(source, wheel)

    def test_cleanup_refuses_a_resolved_target_outside_the_work_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / "work"
            work.mkdir()
            outside = base / "outside"
            outside.mkdir()
            marker = outside / "preserve.txt"
            marker.write_text("preserve")
            with self.assertRaises(builder.BuildError):
                builder.clean_dir(work / ".." / "outside", work)
            self.assertEqual(marker.read_text(), "preserve")

    def test_wheel_is_verified_against_record_and_scripts_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = make_wheel(Path(directory) / "demo-1.0-py3-none-any.whl",
                               {"demo/__init__.py": b"x = 1\n", "demo-1.0.data/scripts/demo.exe": b"MZ", "demo-1.0.data/platlib/demo_ext.pyd": b"MZ"})
            target = Path(directory) / "vendor"
            dist, files = builder.install_wheel(wheel, target)
            self.assertEqual(dist, "demo-1.0.dist-info")
            self.assertTrue((target / "demo" / "__init__.py").is_file())
            self.assertTrue((target / "demo_ext.pyd").is_file())
            self.assertFalse(any("scripts" in name for name in files))
            with self.assertRaises(builder.BuildError):
                builder.install_wheel(wheel, target)

    def test_tampered_or_traversing_wheels_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            tampered = make_wheel(Path(directory) / "t.whl", {"demo/__init__.py": b"x = 1\n"}, tamper="demo/__init__.py")
            with self.assertRaisesRegex(builder.BuildError, "RECORD"):
                builder.install_wheel(tampered, Path(directory) / "a")
            traversal = make_wheel(Path(directory) / "u.whl", {"../evil.py": b"x"})
            with self.assertRaisesRegex(builder.BuildError, "Unsafe"):
                builder.install_wheel(traversal, Path(directory) / "b")
            self.assertFalse((Path(directory) / "evil.py").exists())

    def test_safe_member_rejects_absolute_and_drive_paths(self):
        for name in ("/etc/passwd", "C:/x", "a\\b", "a/../b", ""):
            with self.assertRaises(builder.BuildError):
                builder.safe_member(name)
        self.assertEqual(builder.safe_member("a/b.py"), ("a", "b.py"))


if __name__ == "__main__":
    unittest.main()
