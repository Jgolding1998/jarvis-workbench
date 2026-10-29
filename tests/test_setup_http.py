import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from jarvis import planner
from jarvis.server import LocalHTTPServer, Service, SetupContext, make_handler

TOKEN = "-".join(("setup", "test", "session"))


class SetupHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.exits = []
        self.service = Service(clock=lambda: 0)
        context = SetupContext(root=self.root, on_exit=lambda: self.exits.append(True))
        self.server = LocalHTTPServer(("127.0.0.1", 0), make_handler(self.service, TOKEN, context))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, method="GET", body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection(self.host, timeout=3)
        values = {"Content-Type": "application/json"}
        values.update(headers or {})
        payload = raw if raw is not None else (None if body is None else json.dumps(body))
        connection.request(method, path, payload, values)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def post(self, path, body, raw=None):
        return self.request(path, "POST", body, {"X-Jarvis-Token": TOKEN, "Origin": "http://" + self.host}, raw)

    def answers(self, **device):
        data = planner.default_answers(planner.load_catalog())
        data["device"].update(device)
        return data

    def test_wizard_assets_are_served_with_csp(self):
        for path, kind in (("/setup", "text/html"), ("/setup.js", "text/javascript"), ("/setup.css", "text/css"), ("/setup-catalog.json", "application/json")):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(kind))
            self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
            self.assertTrue(body)

    def test_provider_catalog_is_served_as_json(self):
        status, headers, body = self.request("/provider-catalog.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertTrue(json.loads(body)["providers"])

    def test_diagnostics_are_local_only_and_non_secret(self):
        status, _, body = self.request("/api/setup/diagnostics")
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(report["local"] and report["can_exit"])
        self.assertEqual(set(report["packages"]), {"cv2", "vosk", "sounddevice", "mediapipe"})
        self.assertNotRegex(body.decode().lower(), "token|password|secret|users\\\\")
        self.assertEqual(self.request("/api/setup/diagnostics", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/setup/diagnostics", headers={"Host": "localhost"})[0], 403)

    def test_apply_saves_config_and_arms_nothing(self):
        data = self.answers(microphone=True)
        data.update(voice="offline_vosk", preset="creator", features=["hand_control"])
        data["preferences"]["hand_move_cursor"] = True
        status, _, body = self.post("/api/setup/apply", {"answers": data})
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result["path"], "runtime/setup-config.json")
        self.assertIn("--voice-model models/voice-en", result["plan"]["launch_command"])
        saved = json.loads((self.root / "runtime" / "setup-config.json").read_text())
        self.assertEqual(saved["answers"]["voice"], "offline_vosk")
        self.assertEqual(json.loads(self.request("/api/setup/config")[2])["answers"], saved["answers"])
        self.assertTrue(json.loads(self.request("/api/setup/diagnostics")[2])["config_saved"])
        state = json.loads(self.request("/api/status")[2])
        self.assertFalse(state["active"] or state["lock_armed"] or state["sheet_armed"])
        self.assertEqual(state["hand"]["mode"], "off")
        self.assertFalse(state["hand"]["move_cursor"] or state["hand"]["voice_armed"])
        self.assertIsNone(state["camera_owner"])

    def test_apply_requires_session_and_same_origin(self):
        body = {"answers": self.answers()}
        self.assertEqual(self.request("/api/setup/apply", "POST", body)[0], 403)
        self.assertEqual(self.request("/api/setup/apply", "POST", body, {"X-Jarvis-Token": TOKEN, "Origin": "https://attacker.example"})[0], 403)
        self.assertFalse((self.root / "runtime").exists())

    def test_bad_token_and_unsupported_method_do_not_echo_plan_contents(self):
        private = "SYNTHETIC_PRIVATE_PLAN"
        body = {"answers": {**self.answers(), "private": private}}
        status, headers, response = self.request("/api/setup/apply", "POST", body, {"X-Jarvis-Token": "\u00e9"})
        self.assertEqual(status, 403)
        self.assertNotIn(private.encode(), response)
        self.assertFalse((self.root / "runtime").exists())
        status, headers, response = self.request("/api/setup/apply?value=" + private, "OPTIONS")
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(response), {"error": "Method not allowed"})
        self.assertNotIn(private.encode(), response)
        self.assertEqual(headers["Server"], "Jarvis")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_apply_rejects_unsafe_payloads(self):
        good = self.answers()
        cases = [{"answers": {**good, "__proto__": {"x": 1}}}, {"answers": {**good, "api_key": "sk-test"}},
                 {"answers": good, "extra": True}, {"answers": {**good, "budget_usd": 1e309}}, {"answers": {**good, "preset": "../../etc"}}]
        for case in cases:
            self.assertEqual(self.post("/api/setup/apply", case)[0], 400, case)
        raw = '{"answers": ' + json.dumps(good) + ', "answers": ' + json.dumps(good) + "}"
        self.assertEqual(self.post("/api/setup/apply", None, raw)[0], 400)
        self.assertEqual(self.post("/api/setup/apply", None, '{"answers": {"budget_usd": NaN}}')[0], 400)
        self.assertEqual(self.post("/api/setup/apply", {"answers": good, "pad": "x" * 9000})[0], 400)
        self.assertFalse((self.root / "runtime").exists())

    def test_declined_install_cannot_be_applied(self):
        data = self.answers()
        data["install"] = False
        status, _, body = self.post("/api/setup/apply", {"answers": data})
        self.assertEqual(status, 400)
        self.assertIn("chose not to install", json.loads(body)["error"])
        self.assertFalse((self.root / "runtime").exists())

    def test_extreme_numbers_and_nested_json_return_controlled_errors(self):
        self.post("/api/hand/practice", {"type": "start"})
        huge = 10 ** 400
        self.assertEqual(self.post("/api/hand/practice", {"type": "pointer", "x": huge, "y": 0})[0], 400)
        data = self.answers()
        data["budget_usd"] = huge
        self.assertEqual(self.post("/api/setup/apply", {"answers": data})[0], 400)
        self.assertEqual(self.post("/api/setup/apply", None, "[" * 4000 + "]" * 4000)[0], 400)
        self.assertEqual(self.request("/api/status")[0], 200)
        self.assertFalse((self.root / "runtime").exists())

    def test_corrupt_saved_config_does_not_break_loading(self):
        target = self.root / "runtime" / planner.CONFIG_NAME
        target.parent.mkdir()
        for raw in ("[" * 4000 + "]" * 4000, json.dumps({"schema": planner.CONFIG_SCHEMA, "answers": {**self.answers(), "budget_usd": 10 ** 400}})):
            target.write_text(raw, encoding="utf-8")
            status, _, body = self.request("/api/setup/config")
            self.assertEqual(status, 200)
            self.assertIsNone(json.loads(body)["answers"])

    def test_exit_requires_token_then_stops(self):
        self.assertEqual(self.request("/api/setup/exit", "POST", {})[0], 403)
        self.assertEqual(self.exits, [])
        status, _, body = self.post("/api/setup/exit", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["stopped"])
        self.assertEqual(self.exits, [True])

    def test_hand_practice_over_http_keeps_presence_guard(self):
        self.post("/api/arm", {"lock": True, "sheet": False})
        self.post("/api/command", {"command": "active"})
        self.assertEqual(self.post("/api/hand/practice", {"type": "pointer", "x": 0.5, "y": 0.5})[0], 400)
        self.assertEqual(self.post("/api/hand/practice", {"type": "start"})[0], 200)
        state = json.loads(self.post("/api/hand/practice", {"type": "pointer", "x": 0.25, "y": 0.75})[2])
        self.assertEqual(state["hand"]["cursor"], {"x": 0.25, "y": 0.75})
        self.assertEqual(self.post("/api/hand/practice", {"type": "pointer", "x": 2, "y": 0})[0], 400)
        state = json.loads(self.post("/api/hand/practice", {"type": "clap"})[2])
        self.assertEqual(state["hand"]["mode"], "off")
        self.assertEqual(state["hand"]["last_stop"], "clap")
        self.assertTrue(state["active"] and state["lock_armed"])
        self.assertEqual(state["camera_owner"], "presence")

    def test_watch_me_reports_missing_tracking_truthfully(self):
        status, _, body = self.post("/api/command", {"command": "watch me"})
        self.assertEqual(status, 400)
        self.assertIn("--allow-hand-control", json.loads(body)["error"])
        state = json.loads(self.request("/api/status")[2])
        self.assertEqual(state["hand"]["mode"], "off")
        self.assertIsNone(state["camera_owner"])

    def test_hand_arming_needs_launch_permission(self):
        status, _, body = self.post("/api/hand/arm", {"voice": True, "move_cursor": False})
        self.assertEqual(status, 400)
        self.assertIn("--allow-hand-control", json.loads(body)["error"])
        self.assertEqual(self.post("/api/hand/arm", {"voice": False, "move_cursor": False, "x": 1})[0], 400)
        self.assertEqual(self.post("/api/hand/arm", {"voice": False, "move_cursor": False})[0], 200)


if __name__ == "__main__":
    unittest.main()
