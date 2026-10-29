import http.client
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from jarvis.actions import Actions
from jarvis.server import LocalHTTPServer, Service, SetupContext, make_handler


class ServeWiringTests(unittest.TestCase):
    """serve() with every socket, device thread and credential store replaced."""

    def run_serve(self, flags):
        from jarvis import server
        captured, listening = {}, threading.Event()

        class FakeServer:
            server_address = ("127.0.0.1", 0)
            def __init__(self, address, handler):
                pass
            def serve_forever(self):
                raise KeyboardInterrupt
            def server_close(self):
                pass

        def handler(service, token, setup):
            captured["setup"] = setup
        with patch.object(server, "LocalHTTPServer", FakeServer), patch.object(server, "make_handler", handler),                 patch.object(server, "Onboarding") as onboarding, patch("jarvis.devices.run_voice", lambda *args: listening.set()), patch("builtins.print"):
            server.serve(server.build_parser().parse_args(flags))
        self.assertIs(captured["setup"].onboarding, onboarding.return_value)
        return onboarding.call_args.kwargs["read_aloud"], listening.wait(2 if "--voice-model" in flags else 0.2)

    def test_read_aloud_is_refused_only_when_the_voice_command_listener_runs(self):
        self.assertEqual(self.run_serve(["--demo", "--port", "0"]), (True, False))
        self.assertEqual(self.run_serve(["--demo", "--port", "0", "--voice-model", "."]), (False, True))


class ServerTests(unittest.TestCase):
    def test_versionless_requests_have_controlled_json_errors(self):
        marker = "-".join(("fixture", "request", "only")).encode("ascii")
        for target in (b"/", b"/?value=" + marker):
            with socket.create_connection(self.server.server_address, timeout=3) as connection:
                connection.sendall(b"GET " + target + b"\r\n\r\n")
                response = http.client.HTTPResponse(connection)
                response.begin()
                body = response.read()
                self.assertEqual(response.status, 400)
                self.assertEqual(json.loads(body), {"error": "Invalid request"})
                self.assertNotIn(marker, body)
                self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(self.request("/api/status")[0], 200)

    @classmethod
    def setUpClass(cls):
        cls.service = Service()
        cls.server = LocalHTTPServer(("127.0.0.1", 0), make_handler(cls.service, "test-session-only"))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = "127.0.0.1:" + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, timeout=3)
        values = {"Content-Type": "application/json"}
        values.update(headers or {})
        connection.request(method, path, None if body is None else json.dumps(body), values)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_local_dashboard_has_csp_and_no_external_dependencies(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(b"https://", body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_dns_rebinding_host_rejected(self):
        status, _, _ = self.request("/api/session", headers={"Host":"attacker.example"})
        self.assertEqual(status, 403)

    def test_foreign_origin_cannot_read_session_or_mutate(self):
        self.assertEqual(self.request("/api/session", headers={"Origin":"https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/command", "POST", {"command":"active"}, {"Origin":"https://attacker.example", "X-Jarvis-Token":"test-session-only"})[0], 403)

    def test_post_requires_session_token(self):
        self.assertEqual(self.request("/api/command", "POST", {"command":"active"})[0], 403)
        self.assertEqual(self.request("/api/command", "POST", {"command":"active"}, {"X-Jarvis-Token":"wrong"})[0], 403)

    def test_non_ascii_session_token_returns_403_without_mutation(self):
        before = self.service.presence.active
        command = "stop" if before else "active"
        status, headers, body = self.request("/api/command", "POST", {"command": command}, {"X-Jarvis-Token": "\u00e9"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "Local session authorization required"})
        self.assertEqual(self.service.presence.active, before)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/status")[0], 200)

    def test_unsupported_methods_have_fixed_json_headers_and_no_reflection(self):
        private = "SYNTHETIC_PRIVATE_MARKER"
        for method in ("OPTIONS", "TRACE", private):
            with self.subTest(method=method):
                status, headers, body = self.request("/?api_key=" + private, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(json.loads(body), {"error": "Method not allowed"})
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["Server"], "Jarvis")
                self.assertNotIn(private.encode(), body)
                self.assertNotIn(method.encode(), body)

    def test_regular_response_does_not_disclose_python_banner(self):
        for path in ("/", "/api/status", "/does-not-exist"):
            status, headers, body = self.request(path)
            self.assertEqual(headers["Server"], "Jarvis")
            self.assertNotIn("Python", headers["Server"])

    def test_head_error_has_headers_without_body(self):
        status, headers, body = self.request("/", method="HEAD")
        self.assertEqual(status, 405)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Server"], "Jarvis")
        self.assertIn("Content-Security-Policy", headers)

    def test_same_origin_activation_works(self):
        status, _, body = self.request("/api/command", "POST", {"command":"active"}, {"Origin":"http://"+self.host,"X-Jarvis-Token":"test-session-only"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["active"])

    def test_no_arbitrary_file_route(self):
        self.assertEqual(self.request("/../jarvis/server.py")[0], 404)
        self.assertEqual(self.request("/%2e%2e/.env")[0], 404)

    def test_oversized_request_rejected(self):
        self.assertEqual(self.request("/api/command", "POST", {"command":"x"*3000}, {"X-Jarvis-Token":"test-session-only"})[0], 400)

    def test_arming_rejects_invalid_types(self):
        self.assertEqual(self.request("/api/arm", "POST", {"lock":"false", "sheet":False}, {"X-Jarvis-Token":"test-session-only"})[0], 400)

    def test_shell_command_rejected(self):
        self.assertEqual(self.request("/api/command", "POST", {"command":"powershell.exe"}, {"X-Jarvis-Token":"test-session-only"})[0], 400)

    def test_real_launch_permissions_enforced(self):
        service = Service(Actions(demo=False))
        with self.assertRaises(ValueError):
            service.arm(True, False)
        with self.assertRaises(ValueError):
            service.arm(False, True)

    def test_actions_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            Service().arm(True, True)

    def raw_headers(self, headers, method="GET"):
        connection = http.client.HTTPConnection(self.host, timeout=3)
        connection.putrequest(method, "/api/session" if method == "GET" else "/api/command", skip_host=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        return status

    def test_duplicate_host_and_origin_rejected(self):
        self.assertEqual(self.raw_headers([("Host", self.host), ("Host", self.host)]), 403)
        self.assertEqual(self.raw_headers([("Host", self.host), ("Origin", "http://"+self.host), ("Origin", "https://attacker.example")]), 403)

    def test_ambiguous_post_framing_rejected(self):
        base = [("Host",self.host),("X-Jarvis-Token","test-session-only"),("Content-Type","application/json")]
        self.assertEqual(self.raw_headers(base + [("Content-Length","2"),("Content-Length","2")], "POST"), 400)
        self.assertEqual(self.raw_headers(base + [("Content-Length","2"),("Transfer-Encoding","chunked")], "POST"), 400)

    def test_connection_gets_short_timeout(self):
        with patch("jarvis.server.ThreadingHTTPServer.get_request") as get_request:
            connection = unittest.mock.Mock()
            get_request.return_value = connection, ("127.0.0.1", 1234)
            LocalHTTPServer.get_request(self.server)
            connection.settimeout.assert_called_once_with(3)

    def test_action_failure_surfaces_without_success_claim(self):
        service = Service(clock=lambda: 0)
        service.arm(True, False)
        service.command("active")
        with patch.object(service.actions, "run", side_effect=OSError("denied")):
            service._run(["lock_absence"])
        self.assertIn("may still be unlocked", service.snapshot()["action_error"])
        self.assertIn("Action failed", service.snapshot()["events"][0]["message"])


def drip(address, opening, drip_line, interval=0.1):
    """Send a request opening, then one more line per interval. Returns seconds until the server closes and what it sent."""
    connection = socket.create_connection(address, timeout=5)
    stopped, received = threading.Event(), []

    def send():
        try:
            connection.sendall(opening)
            while not stopped.wait(interval):
                connection.sendall(drip_line)
        except OSError:
            pass

    started = time.monotonic()
    threading.Thread(target=send, daemon=True).start()
    try:
        while time.monotonic() - started < 15:
            chunk = connection.recv(4096)
            if not chunk:
                break
            received.append(chunk)
    except OSError:
        pass
    finally:
        elapsed = time.monotonic() - started
        stopped.set()
        connection.close()
    return elapsed, b"".join(received)


class SlowOnboarding:
    def snapshot(self):
        return {}

    def chat(self, provider, prompt, consent):
        time.sleep(0.6)
        return {"reply": "synthetic reply after a slow provider"}


class ProtocolLimitTests(unittest.TestCase):
    def start(self, setup=None):
        server = LocalHTTPServer(("127.0.0.1", 0), make_handler(Service(), "test-session-only", setup))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        return server, ("127.0.0.1:" + str(server.server_address[1])).encode()

    def test_header_deadline_is_absolute_for_a_slow_drip_client(self):
        server, host = self.start()
        with patch("jarvis.server.HEADER_SECONDS", 0.3):
            elapsed, response = drip(server.server_address, b"GET /api/status HTTP/1.1\r\nHost: " + host + b"\r\n", b"X-Drip: a\r\n")
        self.assertLess(elapsed, 3)
        self.assertNotIn(b"200 OK", response)

    def test_slow_body_is_dropped_before_any_action(self):
        server, host = self.start()
        opening = (b"POST /api/command HTTP/1.1\r\nHost: " + host + b"\r\nX-Jarvis-Token: test-session-only\r\n"
                   b"Content-Type: application/json\r\nContent-Length: 2000\r\n\r\n")
        with patch("jarvis.server.BODY_SECONDS", 0.3):
            elapsed, response = drip(server.server_address, opening, b" ")
        self.assertLess(elapsed, 3)
        self.assertEqual(response, b"")

    def test_header_timer_stops_before_a_slow_provider_reply(self):
        server, host = self.start(SetupContext(onboarding=SlowOnboarding()))
        body = json.dumps({"provider": "ollama", "prompt": "hello", "consent": True})
        with patch("jarvis.server.HEADER_SECONDS", 0.1), patch("jarvis.server.BODY_SECONDS", 0.1):
            connection = http.client.HTTPConnection(host.decode(), timeout=5)
            connection.request("POST", "/api/quickstart/chat", body, {"Content-Type": "application/json", "X-Jarvis-Token": "test-session-only"})
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())), (200, {"reply": "synthetic reply after a slow provider"}))
            connection.close()

    def test_connection_admission_limit_sends_a_controlled_busy_reply(self):
        with patch("jarvis.server.MAX_CONNECTIONS", 2):
            server, host = self.start()
        idle = [socket.create_connection(server.server_address, timeout=3) for _ in range(2)]
        time.sleep(0.2)
        try:
            for _ in range(8):
                with socket.create_connection(server.server_address, timeout=3) as extra:
                    extra.sendall(b"GET /api/status HTTP/1.1\r\nHost: " + host + b"\r\n\r\n")
                    response = http.client.HTTPResponse(extra)
                    response.begin()
                    self.assertEqual(response.status, 503)
                    self.assertEqual(json.loads(response.read()), {"error": "Server busy"})
                    self.assertEqual(response.getheader("Cache-Control"), "no-store")
                    self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        finally:
            for connection in idle:
                connection.close()
        deadline = time.monotonic() + 5
        while True:
            connection = http.client.HTTPConnection(host.decode(), timeout=3)
            connection.request("GET", "/api/status")
            status = connection.getresponse().status
            connection.close()
            if status == 200 or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual(status, 200)

    def test_busy_reply_drains_only_a_bounded_amount_after_half_close(self):
        with patch("jarvis.server.MAX_CONNECTIONS", 0):
            server, _ = self.start()
        request = MagicMock()
        request.recv.return_value = b"x" * 4096
        server.process_request(request, ("127.0.0.1", 10000))
        self.assertEqual(request.recv.call_count,4)
        request.shutdown.assert_any_call(socket.SHUT_WR)
        request.settimeout.assert_called_once_with(0.05)
        request.close.assert_called_once_with()


class DesktopServeTests(unittest.TestCase):
    """The desktop app's server: no setup wizard, parent-watch shutdown, and an exclusive port."""

    def run_serve(self, parent_input=None, **options):
        from jarvis import server
        stopped, created = threading.Event(), []

        class FakeServer:
            server_address = ("127.0.0.1", 0)
            def __init__(self, address, handler):
                created.append(self)
            def serve_forever(self):
                if not stopped.wait(5):
                    raise KeyboardInterrupt
            def shutdown(self):
                stopped.set()
            def server_close(self):
                pass

        captured = {}
        def handler(service, token, setup):
            captured["setup"] = setup
        with patch.object(server, "LocalHTTPServer", FakeServer), patch.object(server, "make_handler", handler), \
                patch.object(server, "Onboarding") as onboarding, patch("builtins.print"):
            server.serve(server.build_parser().parse_args(["--demo", "--port", "0"]), parent_input=parent_input, **options)
        return captured["setup"], onboarding, stopped.is_set()

    def test_setup_exit_closes_the_desktop_child_it_started(self):
        setup, onboarding, _ = self.run_serve()
        self.assertTrue(setup.enabled)
        onboarding.return_value.close.assert_called_once_with()

    def test_desktop_mode_builds_no_onboarding_and_stops_at_parent_end_of_file(self):
        setup, onboarding, stopped = self.run_serve(io.BytesIO(b"ignored"), setup_enabled=False)
        self.assertFalse(setup.enabled)
        self.assertIsNone(setup.onboarding)
        onboarding.assert_not_called()
        self.assertTrue(stopped, "End of the parent pipe did not stop the server")

    def test_parent_watch_tolerates_a_broken_pipe(self):
        from jarvis.server import watch_parent
        calls = []
        class Broken:
            def read(self, size):
                raise OSError("pipe broken")
        watch_parent(Broken(), lambda: calls.append(True))
        self.assertEqual(calls, [True])

    def test_setup_window_reports_setup_available(self):
        httpd = LocalHTTPServer(("127.0.0.1", 0), make_handler(Service(clock=lambda: 0), "t" * 32, SetupContext(onboarding=MagicMock())))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
            connection.request("GET", "/api/setup/diagnostics")
            self.assertTrue(json.loads(connection.getresponse().read())["setup_available"])
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)

    def test_disabled_setup_context_never_reads_setup_state(self):
        with patch("jarvis.server.Onboarding", side_effect=AssertionError("No onboarding in the desktop app")):
            self.assertIsNone(SetupContext(enabled=False).onboarding)

    def test_desktop_routes_hide_setup_but_keep_status_actions_and_exit(self):
        exits = threading.Event()
        service = Service(clock=lambda: 0)
        httpd = LocalHTTPServer(("127.0.0.1", 0), make_handler(service, "t" * 32, SetupContext(enabled=False, on_exit=exits.set)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host = "127.0.0.1:%d" % httpd.server_address[1]

        def call(method, path, body=None):
            connection = http.client.HTTPConnection(host, timeout=3)
            headers = {"Content-Type": "application/json", "X-Jarvis-Token": "t" * 32, "Origin": "http://" + host}
            connection.request(method, path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        try:
            for path in ("/setup", "/setup.js", "/setup.css", "/setup-catalog.json", "/quickstart-catalog.json",
                         "/provider-catalog.json", "/api/quickstart", "/api/setup/config"):
                self.assertEqual(call("GET", path)[0], 404, path)
            for path, body in (("/api/quickstart/save", {"choices": {}}), ("/api/quickstart/install", {"consent": True}),
                               ("/api/quickstart/launch", {"consent": True}), ("/api/quickstart/key", {"provider": "openai", "key": "x"}),
                               ("/api/setup/apply", {"answers": {}})):
                self.assertEqual(call("POST", path, body)[0], 404, path)
            self.assertEqual(call("GET", "/")[0], 200)
            self.assertEqual(call("GET", "/api/status")[0], 200)
            status, body = call("GET", "/api/setup/diagnostics")
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["setup_available"])
            self.assertEqual(call("POST", "/api/command", {"command": "active"})[0], 200)
            self.assertTrue(service.presence.active)
            self.assertEqual(call("POST", "/api/setup/exit", {})[0], 200)
            self.assertTrue(exits.wait(3))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)

    def test_a_second_server_cannot_take_the_same_port(self):
        first = LocalHTTPServer(("127.0.0.1", 0), make_handler(Service(clock=lambda: 0), "t" * 32, SetupContext(enabled=False)))
        thread = threading.Thread(target=first.serve_forever, daemon=True)
        thread.start()
        port = first.server_address[1]
        try:
            with self.assertRaises(OSError):
                LocalHTTPServer(("127.0.0.1", port), make_handler(Service(clock=lambda: 0), "t" * 32, SetupContext(enabled=False))).server_close()
            if sys.platform == "win32":
                self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
                self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
                intruder = socket.socket()
                intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    with self.assertRaises(OSError):
                        intruder.bind(("127.0.0.1", port))
                finally:
                    intruder.close()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/api/status")
            self.assertEqual(connection.getresponse().status, 200)
            connection.close()
        finally:
            first.shutdown()
            first.server_close()
            thread.join(3)

    def test_posix_keeps_address_reuse(self):
        from jarvis import server
        if sys.platform == "win32":
            self.assertFalse(server.LocalHTTPServer.allow_reuse_address)
        else:
            self.assertTrue(server.LocalHTTPServer.allow_reuse_address)


CHILD = """import sys
sys.path.insert(0, sys.argv[1])
from jarvis import server
server.serve(server.build_parser().parse_args(["--demo", "--port", "0"]), setup_enabled=False, parent_input=sys.stdin.buffer)
"""


class DesktopProcessTests(unittest.TestCase):
    """A real demo-mode server process (no camera, microphone or actions) with a parent-watch pipe."""

    def start(self):
        import subprocess
        repo = str(Path(__file__).resolve().parents[1])
        env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR"}}
        child = subprocess.Popen([sys.executable, "-I", "-u", "-c", CHILD, repo], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.stdin.close)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait(5)))
        line = child.stdout.readline()
        self.assertTrue(line.startswith("Jarvis: http://127.0.0.1:"), line)
        return child, line.split("http://", 1)[1].strip()

    def test_parent_end_of_file_stops_the_desktop_server_gracefully(self):
        child, host = self.start()
        connection = http.client.HTTPConnection(host, timeout=3)
        connection.request("GET", "/setup")
        self.assertEqual(connection.getresponse().status, 404)
        connection.close()
        child.stdin.close()
        self.assertEqual(child.wait(10), 0)
        self.assertIn("Jarvis stopped.", child.stdout.read())

    def test_exit_route_stops_the_desktop_server(self):
        child, host = self.start()
        connection = http.client.HTTPConnection(host, timeout=3)
        connection.request("GET", "/api/session")
        token = json.loads(connection.getresponse().read())["token"]
        connection.request("POST", "/api/setup/exit", "{}", {"Content-Type": "application/json", "X-Jarvis-Token": token})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        self.assertEqual(child.wait(10), 0)
