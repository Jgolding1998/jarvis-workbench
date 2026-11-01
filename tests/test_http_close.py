"""Real loopback transport regressions, with no app/device/provider instances.

Only the HTTP handler and server are used. The injected runtime cannot operate
anything; setup is disabled. Bodies and response markers are synthetic.
"""
import http.client
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from jarvis import server as transport

TOKEN = "transport-regression-fixture"
MARKER = b"SYNTHETIC-NOT-FOR-RESPONSE"


class LingerBoundsTests(unittest.TestCase):
    def test_large_clock_rounding_never_extends_the_socket_timeout(self):
        request = MagicMock()
        request.recv.return_value = b"x" * 4096
        # IEEE 754 subtraction at this uptime exceeds .1 without the clamp.
        self.assertGreater((100000.0 + .1) - 100000.0, .1)
        with patch.object(transport.time, "monotonic", return_value=100000.0):
            transport._linger_discard(request)
        self.assertEqual(request.recv.call_count, 4)
        self.assertEqual(request.settimeout.call_count, 4)
        self.assertTrue(all(0 < call.args[0] <= .1 for call in request.settimeout.call_args_list))
        request.shutdown.assert_called_once_with(socket.SHUT_WR)


class StubRuntime:
    _status_instance = "0123456789abcdef"

    def __init__(self):
        self.calls = []
        self.actions = SimpleNamespace(demo=True)

    def snapshot(self):
        self.calls.append("snapshot")
        return {"synthetic": True}

    def __getattr__(self, name):
        raise AssertionError("Unexpected runtime access: " + name)


class HTTPResponseCloseTests(unittest.TestCase):
    def setUp(self):
        self.runtime = StubRuntime()
        self.server = transport.LocalHTTPServer(
            ("127.0.0.1", 0), transport.make_handler(
                self.runtime, TOKEN, SimpleNamespace(enabled=False)))
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def opening(self, method="POST", path="/api/command", *, host=None, token=None,
                length=2048, extra=()):
        headers = [f"{method} {path} HTTP/1.1", f"Host: {host or self.host}",
                   "Content-Type: application/json", f"Content-Length: {length}"]
        if token is not None:
            headers.append("X-Jarvis-Token: " + token)
        return ("\r\n".join([*headers, *extra]) + "\r\n\r\n").encode("ascii")

    def assert_response(self, connection, expected, message):
        response = http.client.HTTPResponse(connection)
        response.begin()
        body = response.read()
        self.assertEqual(response.status, expected)
        self.assertEqual(json.loads(body), {"error": message})
        self.assertEqual(response.getheader("Content-Length"), str(len(body)))
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertNotIn(MARKER, body)
        return body

    def delayed_rejection(self, expected, message, **options):
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(self.opening(**options))
            # Previously the response was lost on Windows when this body arrived
            # after full close. 80ms is deliberately later than the old 50ms drain.
            time.sleep(.08)
            client.sendall((MARKER + b"x" * 2048)[:2048])
            self.assert_response(client, expected, message)
        self.assertEqual(self.runtime.calls, [])

    def wait_for_slots(self, count=transport.MAX_CONNECTIONS, timeout=1):
        """Observe worker cleanup; never retry or replace a failed HTTP request."""
        deadline = time.monotonic() + timeout
        while True:
            acquired = 0
            try:
                while acquired < count and self.server.connections.acquire(blocking=False):
                    acquired += 1
                if acquired == count:
                    return
            finally:
                for _ in range(acquired):
                    self.server.connections.release()
            if time.monotonic() >= deadline:
                self.fail("HTTP worker slots were not released within the cleanup bound")
            time.sleep(.005)

    def test_unauthorized_post_preserves_403_after_delayed_body(self):
        self.delayed_rejection(403, "Local session authorization required")

    def test_foreign_host_preserves_403_after_delayed_body(self):
        self.delayed_rejection(403, "Local session authorization required",
                               host="foreign.invalid", token=TOKEN)

    def test_foreign_origin_preserves_403_after_delayed_body(self):
        self.delayed_rejection(403, "Local session authorization required", token=TOKEN,
                               extra=("Origin: https://foreign.invalid",))

    def test_unknown_get_preserves_404_after_delayed_body(self):
        self.delayed_rejection(404, "Not found", method="GET", path="/synthetic-missing")

    def test_unsupported_put_preserves_405_after_delayed_body(self):
        self.delayed_rejection(405, "Method not allowed", method="PUT", token=TOKEN)

    def test_duplicate_framing_preserves_400_and_never_calls_runtime(self):
        self.delayed_rejection(400, "Invalid request or action not enabled", token=TOKEN,
                               extra=("Content-Length: 3",))

    def test_oversized_authorized_body_still_refuses_before_runtime(self):
        self.delayed_rejection(400, "Invalid request or action not enabled", token=TOKEN,
                               length=2049)

    def test_saturated_admission_preserves_503_after_delayed_body(self):
        self.server.connections = threading.BoundedSemaphore(0)
        self.delayed_rejection(503, "Server busy", token=TOKEN)

    def test_discarded_pipeline_is_not_interpreted_or_reflected(self):
        pipeline = (b"{}\r\nGET /api/status HTTP/1.1\r\nHost: " + self.host.encode()
                    + b"\r\n\r\n" + MARKER)
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(self.opening(length=len(pipeline)))
            time.sleep(.08)
            client.sendall(pipeline)
            self.assert_response(client, 403, "Local session authorization required")
        self.wait_for_slots()
        self.assertEqual(self.runtime.calls, [])

    def test_no_eof_client_cannot_hold_a_response_worker(self):
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(self.opening())
            self.assert_response(client, 403, "Local session authorization required")
            started = time.monotonic()
            self.wait_for_slots(timeout=.7)
            self.assertLess(time.monotonic() - started, .7)
        self.assertEqual(self.runtime.calls, [])

    def test_trickling_bytes_do_not_extend_the_absolute_cleanup_deadline(self):
        stopped = threading.Event()
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(self.opening())
            self.assert_response(client, 403, "Local session authorization required")

            def trickle():
                try:
                    while not stopped.wait(.015):
                        client.sendall(b"x")
                except OSError:
                    pass

            sender = threading.Thread(target=trickle, daemon=True)
            sender.start()
            try:
                self.wait_for_slots(timeout=.7)
            finally:
                stopped.set()
                sender.join(1)
        self.assertFalse(sender.is_alive())
        self.assertEqual(self.runtime.calls, [])

    def test_all_sixteen_stalled_connections_keep_admission_bounded_and_recover(self):
        clients = []
        try:
            for _ in range(transport.MAX_CONNECTIONS):
                client = socket.create_connection(self.server.server_address, timeout=2)
                client.sendall(b"G")
                clients.append(client)
            deadline = time.monotonic() + 1
            while self.server.connections.acquire(blocking=False):
                self.server.connections.release()
                if time.monotonic() >= deadline:
                    self.fail("The sixteen fixture connections were not admitted")
                time.sleep(.005)
            # Exercise real admission and recovery without coupling them to a
            # client sleep near the separate 100ms delayed-body linger bound.
            with socket.create_connection(self.server.server_address, timeout=2) as client:
                client.sendall(self.opening())
                self.assert_response(client, 503, "Server busy")
                self.assertEqual(client.recv(1), b"")
            self.assertEqual(self.runtime.calls, [])
        finally:
            for client in clients:
                client.close()
        self.wait_for_slots()
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request("GET", "/api/status")
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())),
                             (200, {"synthetic": True}))
        finally:
            connection.close()
        self.assertEqual(self.runtime.calls, ["snapshot"])


class BoundedShutdownUnitTests(unittest.TestCase):
    def test_discard_stops_at_sixteen_kib_without_requiring_eof(self):
        request = MagicMock()
        request.recv.side_effect = lambda size: b"x" * size
        with patch.object(transport.time, "monotonic", return_value=1):
            transport._linger_discard(request)
        request.shutdown.assert_called_once_with(socket.SHUT_WR)
        self.assertEqual([call.args[0] for call in request.recv.call_args_list], [4096] * 4)
        self.assertEqual(sum(call.args[0] for call in request.recv.call_args_list), 16384)

    def test_each_read_uses_remaining_absolute_deadline_not_a_shorter_fixed_timeout(self):
        request = MagicMock()
        request.recv.return_value = b"x"
        with patch.object(transport.time, "monotonic", side_effect=[1, 1, 1.05, 1.101]):
            transport._linger_discard(request)
        self.assertEqual(request.recv.call_count, 2)
        timeouts = [call.args[0] for call in request.settimeout.call_args_list]
        self.assertAlmostEqual(timeouts[0], .1)
        self.assertAlmostEqual(timeouts[1], .05)

    def test_eof_and_socket_errors_stop_discard_without_interpreting_data(self):
        for effect in (b"", socket.timeout(), ConnectionResetError()):
            with self.subTest(effect=type(effect).__name__):
                request = MagicMock()
                if isinstance(effect, bytes):
                    request.recv.return_value = effect
                else:
                    request.recv.side_effect = effect
                transport._linger_discard(request)
                request.recv.assert_called_once()

    def test_failed_half_close_does_not_start_reads(self):
        request = MagicMock()
        request.shutdown.side_effect = OSError()
        transport._linger_discard(request)
        request.recv.assert_not_called()

    def test_base_socket_close_runs_even_when_discard_raises(self):
        request = MagicMock()
        server = transport.LocalHTTPServer(("127.0.0.1", 0), object)
        try:
            with patch.object(transport, "_linger_discard", side_effect=RuntimeError("fixture")):
                with self.assertRaises(RuntimeError):
                    server.shutdown_request(request)
            request.close.assert_called_once_with()
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
