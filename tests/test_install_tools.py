import hashlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis import install_tools as setup


CANARY = "PRIVATE" + "-SETUP-CANARY"


def requirements(root):
    (root / "requirements-camera.txt").write_text("opencv-contrib-python==4.13.0.92\n")
    (root / "requirements-hand.txt").write_text("mediapipe==1.2.0\nopencv-contrib-python==4.13.0.92\n")
    (root / "requirements-voice.txt").write_text("vosk==0.3.45\nsounddevice==0.5.6\nsrt==3.5.3\n")


def venv_files(root):
    directory = root / ".venv"
    executable = directory / "Scripts" / "python.exe" if os.name == "nt" else directory / "bin" / "python"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fake interpreter; never executed")
    (directory / "pyvenv.cfg").write_text("home = fixture\ninclude-system-site-packages = false\n")


class FakeRun:
    def __init__(self):
        self.calls = []
        self.manifests = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if "venv" in argv:
            venv_files(Path(kwargs["cwd"]))
        if "pip" in argv:
            self.manifests.append(Path(argv[-1]).read_text())
        return SimpleNamespace(returncode=0)


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, *, url=setup.MODEL_URL, length=None):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data)) if length is None else length}

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, data, **options):
        self.data, self.options, self.calls = data, options, []

    def open(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return Response(self.data, **self.options)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project with spaces"
        self.root.mkdir()
        requirements(self.root)
        self.process_guard = patch("subprocess.Popen", side_effect=AssertionError("Real process forbidden in tests"))
        self.network_guard = patch("socket.create_connection", side_effect=AssertionError("Real network forbidden in tests"))
        self.process_guard.start()
        self.network_guard.start()

    def tearDown(self):
        self.network_guard.stop()
        self.process_guard.stop()
        self.temporary.cleanup()

    def run_menu(self, answers, *, run=None, model=None):
        choices, output = iter(answers), []
        result = setup.main(root=self.root, ask=lambda _: next(choices), tell=output.append, run=run or FakeRun(), model_fetch=model or (lambda _: None))
        return result, output

    def test_cancel_before_or_after_summary_makes_no_changes(self):
        before = sorted(path.name for path in self.root.iterdir())
        for answers in [["q"], ["1", "n"], ["2", ""], ["4", "yes"]]:
            run = FakeRun()
            with self.subTest(answers=answers):
                self.assertEqual(self.run_menu(answers, run=run)[0], 0)
                self.assertEqual(run.calls, [])
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)

    def test_invalid_menu_choice_never_starts_a_process(self):
        run = FakeRun()
        self.assertEqual(self.run_menu(["1 & injected"], run=run)[0], 2)
        self.assertEqual(run.calls, [])
        self.assertFalse((self.root / ".venv").exists())

    def test_requirements_reject_options_urls_unpinned_and_conflicting_wheel(self):
        for bad in ["--index-url https://untrusted.invalid", "opencv-contrib-python @ https://untrusted.invalid/wheel", "opencv-python==4.13.0.92", "opencv-contrib-python", "opencv-contrib-python==4.13.0.91", "opencv-contrib-python==4.13.0.92\nopencv-contrib-python==4.13.0.92"]:
            (self.root / "requirements-camera.txt").write_text(bad)
            run = FakeRun()
            with self.subTest(bad=bad):
                self.assertEqual(self.run_menu(["1"], run=run)[0], 2)
                self.assertEqual(run.calls, [])
                self.assertFalse((self.root / ".venv").exists())

    def test_valid_all_choice_has_fixed_argv_and_no_shell(self):
        run, models = FakeRun(), []
        status, output = self.run_menu(["4", "y"], run=run, model=models.append)
        self.assertEqual(status, 0)
        self.assertEqual(models, [self.root])
        self.assertEqual(len(run.calls), 4)
        argv, options = run.calls[-1]
        self.assertEqual(argv[1:5], ["-I", "-m", "pip", "install"])
        for option in ["--isolated", "--only-binary=:all:", "--disable-pip-version-check", "--no-input", "--no-cache-dir"]:
            self.assertIn(option, argv)
        self.assertEqual(argv[argv.index("--index-url") + 1], "https://pypi.org/simple")
        self.assertEqual(options["timeout"], 300)
        self.assertFalse(options["shell"])
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        source = f"srt @ {setup.SRT_SOURCE_URL}#sha256={setup.SRT_SOURCE_SHA256}"
        self.assertEqual(set(run.manifests[0].splitlines()), {"mediapipe==1.2.0", "opencv-contrib-python==4.13.0.92", "sounddevice==0.5.6", "vosk==0.3.45", source})
        self.assertIn("--use-pep517", argv)
        self.assertNotIn("--no-deps", argv)
        self.assertNotIn("--no-build-isolation", argv)
        self.assertEqual(list((self.root / ".venv").glob("jarvis-optional-*.txt")), [])
        self.assertTrue(any("official Google" in line for line in output))

    def test_existing_legitimate_venv_is_reused_without_deleting_files(self):
        venv_files(self.root)
        keep = self.root / ".venv" / "keep.txt"
        keep.write_text("keep")
        run, models = FakeRun(), []
        self.assertEqual(self.run_menu(["3", "y"], run=run, model=models.append)[0], 0)
        self.assertFalse(any("venv" in argv for argv, _ in run.calls))
        self.assertEqual(keep.read_text(), "keep")
        self.assertEqual(models, [])

    def test_provider_python_proxy_and_pip_settings_are_removed(self):
        malicious = {"SYSTEMROOT": "system-fixture", "OPENAI_API_KEY": CANARY, "ANTHROPIC_API_KEY": CANARY, "PIP_INDEX_URL": CANARY, "PIP_EXTRA_INDEX_URL": CANARY, "PIP_CONFIG_FILE": CANARY, "HTTP_PROXY": CANARY, "HTTPS_PROXY": CANARY, "PYTHONPATH": CANARY, "PYTHONHOME": CANARY, "HOME": CANARY, "USERPROFILE": CANARY, "PATH": CANARY}
        with patch.dict(os.environ, malicious, clear=True):
            env = setup.safe_environment(self.root)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(env["SYSTEMROOT"], "system-fixture")
        self.assertNotIn(CANARY, env.values())
        self.assertEqual(set(env), {"SYSTEMROOT", "PIP_CONFIG_FILE", "TEMP", "TMP", "TMPDIR"})

    def test_package_argument_tampering_is_rejected_before_changes(self):
        with self.assertRaises(setup.SetupError):
            setup.install(self.root, "camera", ["--extra-index-url", "https://untrusted.invalid"], run=FakeRun())
        self.assertFalse((self.root / ".venv").exists())

    def test_timeout_is_safe_and_never_downloads_model(self):
        def timeout(argv, **_):
            raise subprocess.TimeoutExpired(argv, 300, output=CANARY, stderr=CANARY)
        models = []
        status, output = self.run_menu(["2", "y"], run=timeout, model=models.append)
        self.assertEqual(status, 2)
        self.assertEqual(models, [])
        self.assertNotIn(CANARY, "\n".join(output))
        self.assertTrue(any("timeout" in line for line in output))

    def test_command_failure_keeps_existing_environment_and_hides_output(self):
        venv_files(self.root)
        keep = self.root / ".venv" / "keep.txt"
        keep.write_text("keep")
        status, output = self.run_menu(["1", "y"], run=lambda *a, **k: SimpleNamespace(returncode=7, stderr=CANARY))
        self.assertEqual(status, 2)
        self.assertEqual(keep.read_text(), "keep")
        self.assertNotIn(CANARY, "\n".join(output))
        self.assertTrue(any("exit code 7" in line for line in output))

    def test_paths_cannot_escape_project(self):
        with self.assertRaises(setup.SetupError):
            setup.local_path(self.root, "..", "elsewhere")

    def test_linked_environment_and_requirement_are_rejected(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        link = self.root / ".venv"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links unavailable for this test account")
        try:
            with self.assertRaises(setup.SetupError):
                setup.environment(self.root, FakeRun())
        finally:
            link.unlink()
        file = self.root / "requirements-camera.txt"
        file.unlink()
        target = outside / "requirements.txt"
        target.write_text("opencv-contrib-python==4.13.0.92")
        file.symlink_to(target)
        with self.assertRaises(setup.SetupError):
            setup.selected_packages(self.root, "camera")

    def test_existing_system_site_packages_environment_is_preserved_and_rejected(self):
        venv_files(self.root)
        config = self.root / ".venv" / "pyvenv.cfg"
        config.write_text("include-system-site-packages = true\n")
        run = FakeRun()
        with self.assertRaises(setup.SetupError):
            setup.environment(self.root, run)
        self.assertTrue(config.exists())
        self.assertEqual(run.calls, [])

    def test_junction_guard_is_exercised_without_os_link_privileges(self):
        original = getattr(Path, "is_junction", lambda _: False)
        with patch.object(Path, "is_junction", lambda path: path.name == ".venv" or original(path), create=True):
            with self.assertRaises(setup.SetupError):
                setup.local_path(self.root, ".venv")

    def test_reparse_attributes_are_rejected_without_junction_api(self):
        venv_files(self.root)
        original = Path.lstat
        def attributes(path, *args, **kwargs):
            if path.name == ".venv":
                return SimpleNamespace(st_file_attributes=0x400, st_mode=stat.S_IFDIR)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", attributes), patch.object(Path, "is_junction", lambda _: False, create=True):
            with self.assertRaises(setup.SetupError):
                setup.local_path(self.root, ".venv")

    def test_only_exact_reviewed_srt_source_is_generated(self):
        packages = setup.selected_packages(self.root, "voice")
        manifest = setup.reviewed_manifest(packages)
        self.assertEqual(sum(" @ " in line for line in manifest), 1)
        self.assertIn(f"srt @ {setup.SRT_SOURCE_URL}#sha256={setup.SRT_SOURCE_SHA256}", manifest)
        for value in ["srt==3.5.2", "srt @ https://untrusted.invalid/source.tar.gz"]:
            with self.subTest(value=value), self.assertRaises(setup.SetupError):
                setup.reviewed_manifest([value])

    def test_camera_retains_wheels_only_without_source_exception(self):
        run = FakeRun()
        self.assertEqual(self.run_menu(["1", "y"], run=run)[0], 0)
        self.assertEqual(run.manifests, ["opencv-contrib-python==4.13.0.92\n"])
        self.assertNotIn("--use-pep517", run.calls[-1][0])

    def test_hardlinked_requirement_is_rejected(self):
        target = Path(self.temporary.name) / "shared.txt"
        file = self.root / "requirements-camera.txt"
        target.hardlink_to(file)
        with self.assertRaises(setup.SetupError):
            setup.selected_packages(self.root, "camera")

    def test_model_is_verified_and_published_from_fixed_https_source(self):
        data = b"synthetic model fixture"
        opener = Opener(data)
        setup.download_model(self.root, opener=opener, expected_sha=hashlib.sha256(data).hexdigest())
        self.assertEqual((self.root / "models" / "hand_landmarker.task").read_bytes(), data)
        self.assertEqual(opener.calls[0][0].full_url, setup.MODEL_URL)
        self.assertEqual(opener.calls[0][1], {"timeout": 10})
        self.assertEqual(list((self.root / "models").glob(".hand-model-*.tmp")), [])

    def test_model_hash_mismatch_never_publishes(self):
        with self.assertRaises(setup.SetupError):
            setup.download_model(self.root, opener=Opener(b"wrong bytes"))
        self.assertFalse((self.root / "models" / "hand_landmarker.task").exists())
        self.assertEqual(list((self.root / "models").glob(".hand-model-*.tmp")), [])

    def test_existing_different_model_is_never_overwritten(self):
        models = self.root / "models"
        models.mkdir()
        target = models / "hand_landmarker.task"
        target.write_bytes(b"keep existing model")
        opener = Opener(b"replacement")
        with self.assertRaises(setup.SetupError):
            setup.download_model(self.root, opener=opener)
        self.assertEqual(target.read_bytes(), b"keep existing model")
        self.assertEqual(opener.calls, [])

    def test_valid_existing_model_is_reused_without_download(self):
        models = self.root / "models"
        models.mkdir()
        data = b"synthetic model fixture"
        (models / "hand_landmarker.task").write_bytes(data)
        opener = Opener(b"not used")
        setup.download_model(self.root, opener=opener, expected_sha=hashlib.sha256(data).hexdigest())
        self.assertEqual(opener.calls, [])

    def test_model_redirect_non_tls_and_size_are_rejected(self):
        for options in [{"url": "https://other.invalid/model"}, {"url": "http://storage.googleapis.com/model"}, {"length": str(setup.MAX_MODEL_BYTES + 1)}]:
            with self.subTest(options=options), self.assertRaises(setup.SetupError):
                setup.download_model(self.root, opener=Opener(b"fixture", **options))
        with self.assertRaises(setup.SetupError):
            setup.NoRedirect().redirect_request(None, None, None, None, None, "http://other.invalid")
        self.assertFalse((self.root / "models" / "hand_landmarker.task").exists())

    def test_model_stream_size_is_enforced_even_when_header_understates_it(self):
        with patch.object(setup, "MAX_MODEL_BYTES", 8):
            with self.assertRaises(setup.SetupError):
                setup.download_model(self.root, opener=Opener(b"more than eight bytes", length="1"))
        self.assertFalse((self.root / "models" / "hand_landmarker.task").exists())

    def test_model_publish_does_not_overwrite_a_file_created_during_download(self):
        data = b"synthetic model fixture"
        def collision(_source, target):
            Path(target).write_bytes(b"preserve concurrent file")
            raise FileExistsError("fixture")
        operation = "rename" if os.name == "nt" else "link"
        with patch.object(setup.os, operation, collision):
            with self.assertRaises(setup.SetupError):
                setup.download_model(self.root, opener=Opener(data), expected_sha=hashlib.sha256(data).hexdigest())
        self.assertEqual((self.root / "models" / "hand_landmarker.task").read_bytes(), b"preserve concurrent file")

    def test_posix_publish_cleanup_failure_rolls_back_ready_model(self):
        data = b"synthetic model fixture"
        original_unlink, original_publish = Path.unlink, setup.publish_model
        failed = []
        def unlink(path, *args, **kwargs):
            if path.name.startswith(".hand-model-") and not failed:
                failed.append(True)
                raise PermissionError("fixture cleanup failure")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", unlink), patch.object(setup, "publish_model", lambda source, target: original_publish(source, target, windows=False)):
            with self.assertRaises(setup.SetupError):
                setup.download_model(self.root, opener=Opener(data), expected_sha=hashlib.sha256(data).hexdigest())
        self.assertFalse((self.root / "models" / "hand_landmarker.task").exists())
        self.assertEqual(list((self.root / "models").glob(".hand-model-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
