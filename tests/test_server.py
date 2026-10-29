import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from jarvis.actions import Actions
from jarvis.server import LocalHTTPServer, Service, SetupContext, make_handler


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
        with socket.create_connection(server.server_address, timeout=3) as extra:
            extra.sendall(b"GET /api/status HTTP/1.1\r\nHost: " + host + b"\r\n\r\n")
            response = http.client.HTTPResponse(extra)
            response.begin()
            self.assertEqual(response.status, 503)
            self.assertEqual(json.loads(response.read()), {"error": "Server busy"})
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
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
