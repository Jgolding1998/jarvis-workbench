import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from types import SimpleNamespace

from jarvis import launcher, planner

CMD = planner.ROOT / "Start-Jarvis.cmd"


class LauncherTests(unittest.TestCase):
    def test_camera_dashboard_offers_both_optional_actions_without_arming(self):
        from jarvis import desktop
        from jarvis.server import Service
        with patch.dict(sys.modules, {"cv2": SimpleNamespace()}), patch("jarvis.desktop.sys.platform", "win32"), \
                patch("jarvis.server.check_args"), patch("jarvis.server.serve") as serve, \
                patch("jarvis.actions.Actions.run", side_effect=AssertionError("No desktop action")):
            self.assertEqual(desktop.main(["--feature", "camera"]), 0)
            args = serve.call_args.args[0]
            self.assertTrue(args.camera and args.allow_lock and args.allow_spreadsheet)
            service = Service()
            self.assertFalse(service.presence.active or service.presence.lock_armed or service.presence.sheet_armed)

    def test_desktop_app_never_runs_setup_and_watches_its_parent_only_when_asked(self):
        from jarvis import desktop
        with patch.dict(sys.modules, {"cv2": SimpleNamespace()}), patch("jarvis.desktop.sys.platform", "win32"),                 patch("jarvis.server.check_args"), patch("jarvis.server.serve") as serve:
            self.assertEqual(desktop.main(["--feature", "camera"]), 0)
            self.assertEqual(serve.call_args.kwargs, {"setup_enabled": False, "parent_input": None})
            pipe = io.BytesIO()
            with patch("jarvis.desktop.sys.stdin", SimpleNamespace(buffer=pipe)):
                self.assertEqual(desktop.main(["--feature", "camera", "--parent-watch"]), 0)
            self.assertIs(serve.call_args.kwargs["parent_input"], pipe)
            serve.reset_mock()
            with patch("jarvis.desktop.sys.stdin", None), redirect_stdout(io.StringIO()):
                self.assertEqual(desktop.main(["--feature", "camera", "--parent-watch"]), 2)
            serve.assert_not_called()

    def test_version_gate(self):
        self.assertFalse(launcher.python_ok((3, 10, 9)))
        self.assertTrue(launcher.python_ok((3, 11, 0)))
        self.assertIn("https://www.python.org/downloads/", launcher.python_help())

    def test_old_python_gets_help_and_starts_nothing(self):
        output = io.StringIO()
        with patch("jarvis.launcher.python_ok", return_value=False), patch("jarvis.server.serve") as serve, redirect_stdout(output):
            self.assertEqual(launcher.main(["--no-browser"]), 1)
        serve.assert_not_called()
        self.assertIn("https://www.python.org/downloads/", output.getvalue())
        self.assertIn("Nothing was installed", output.getvalue())

    def test_check_mode_starts_nothing(self):
        with patch("jarvis.server.serve") as serve, redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--check"]), 0)
        serve.assert_not_called()

    def test_end_to_end_wizard_apply_and_exit(self):
        """Starts the real launcher on a free port, drives it over HTTP, and exits through the wizard."""
        results = {}

        def drive(url):
            parts = urlsplit(url)
            host = parts.netloc

            def call(path, method="GET", body=None, token=None):
                connection = http.client.HTTPConnection(host, timeout=3)
                headers = {"Content-Type": "application/json", "Origin": "http://" + host}
                if token:
                    headers["X-Jarvis-Token"] = token
                connection.request(method, path, None if body is None else json.dumps(body), headers)
                response = connection.getresponse()
                data = response.status, response.read()
                connection.close()
                return data

            for _ in range(50):
                try:
                    results["page"] = call(parts.path)
                    break
                except OSError:
                    time.sleep(0.05)
            token = json.loads(call("/api/session")[1])["token"]
            results["diagnostics"] = json.loads(call("/api/setup/diagnostics")[1])
            results["apply"] = call("/api/setup/apply", "POST", {"answers": planner.default_answers(planner.load_catalog())}, token)
            results["status"] = json.loads(call("/api/status")[1])
            results["exit"] = call("/api/setup/exit", "POST", {}, token)

        opened = []

        def opener(url):
            opened.append(url)
            threading.Thread(target=drive, args=(url,), daemon=True).start()

        with tempfile.TemporaryDirectory() as directory, patch("jarvis.planner.ROOT", Path(directory)), redirect_stdout(io.StringIO()) as output:
            worker = threading.Thread(target=lambda: results.setdefault("code", launcher.main(["--port", "0"], opener=opener)))
            worker.start()
            worker.join(15)
            self.assertFalse(worker.is_alive(), "launcher did not exit after the wizard exit route")
            files = sorted(str(path.relative_to(directory)) for path in Path(directory).rglob("*") if path.is_file())
        self.assertEqual(results["code"], 0)
        self.assertEqual(len(opened), 1)
        self.assertRegex(opened[0], r"^http://127\.0\.0\.1:\d+/setup$")
        self.assertEqual(results["page"][0], 200)
        self.assertEqual(results["diagnostics"]["mode"], "demo")
        self.assertEqual(results["apply"][0], 200)
        self.assertEqual(files, [os.path.join("runtime", "setup-config.json")])
        self.assertFalse(results["status"]["active"] or results["status"]["lock_armed"] or results["status"]["sheet_armed"])
        self.assertEqual(results["exit"][0], 200)
        self.assertIn("Jarvis stopped.", output.getvalue())
        self.assertNotIn("token", output.getvalue().lower())


class StartScriptTests(unittest.TestCase):
    def test_script_is_safe_and_points_to_official_python(self):
        data = CMD.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "Start-Jarvis.cmd must use CRLF line endings")
        text = data.decode("ascii").lower()
        self.assertIn("https://www.python.org/downloads/", text)
        self.assertIn("-m jarvis.launcher", text)
        for banned in ("executionpolicy", "bypass", "runas", "winget", "pip install", "invoke-webrequest", "curl", "bitsadmin",
                       "powershell", "reg add", "setx", "schtasks", "start-process", "api_key", "token"):
            self.assertNotIn(banned, text, banned)

    @unittest.skipUnless(sys.platform == "win32", "Windows command script")
    def test_missing_python_shows_official_link(self):
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        env = {"PATH": system, "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "JARVIS_NO_PAUSE": "1"}
        result = subprocess.run(["cmd.exe", "/d", "/c", str(CMD)], env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("https://www.python.org/downloads/", result.stdout)
        self.assertIn("Nothing was installed or changed.", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "Windows command script")
    def test_found_python_runs_launcher_check(self):
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        env = {"PATH": os.path.dirname(sys.executable) + os.pathsep + system, "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "JARVIS_NO_PAUSE": "1"}
        result = subprocess.run(["cmd.exe", "/d", "/c", str(CMD), "--check"], env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("prerequisites look ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
