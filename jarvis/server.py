from __future__ import annotations

import argparse
import hmac
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .actions import Actions
from .presence import Presence

WEB = Path(__file__).resolve().parent.parent / "web"


class Service:
    def __init__(self, actions=None, clock=time.monotonic):
        self.clock = clock
        self.actions = actions or Actions()
        self.presence = Presence()
        self.lock = threading.RLock()
        self.events = deque(maxlen=40)
        self.demo_people = 1
        self.voice = "Disabled"
        self.action_error = None
        self.started = clock()

    def _record(self, message):
        self.events.appendleft({"at": round(self.clock() - self.started, 1), "message": message})

    def _run(self, actions):
        for action in actions:
            try:
                self._record(self.actions.run(action))
            except Exception:
                self.action_error = "Desktop action failed. Check Windows permissions; the workstation may still be unlocked."
                self._record("Action failed: " + action + ". Check local permissions.")

    def command(self, command):
        with self.lock:
            was_active = self.presence.active
            self.presence.command(command, self.clock())
            if not was_active or not self.presence.active:
                self.action_error = None
            self._record("Command: " + command.strip().lower())

    def arm(self, lock, sheet):
        if type(lock) is not bool or type(sheet) is not bool:
            raise ValueError("Arming values must be booleans")
        if lock and sheet:
            raise ValueError("Choose privacy lock or the spreadsheet demo")
        if not self.actions.demo and ((lock and not self.actions.allow_lock) or (sheet and not self.actions.allow_sheet)):
            raise ValueError("This action was not enabled at launch")
        with self.lock:
            self.presence.arm(lock, sheet)
            self._record("Action permissions updated locally")

    def observe(self, people):
        with self.lock:
            self._run(self.presence.observe(people, self.clock()))

    def camera_fault(self):
        with self.lock:
            self._run(self.presence.fail("Camera unavailable", self.clock()))

    def tick(self):
        with self.lock:
            if self.actions.demo and self.presence.active and self.demo_people is not None:
                self._run(self.presence.observe(self.demo_people, self.clock()))
            self._run(self.presence.tick(self.clock()))

    def voice_status(self, text):
        with self.lock:
            self.voice = text

    def is_active(self):
        with self.lock:
            return self.presence.active

    def snapshot(self):
        with self.lock:
            return {**self.presence.snapshot(self.clock()), "demo": self.actions.demo,
                    "allow_lock": self.actions.demo or self.actions.allow_lock,
                    "allow_sheet": self.actions.demo or self.actions.allow_sheet,
                    "voice": self.voice, "events": list(self.events), "action_error": self.action_error,
                    "uptime": round(self.clock() - self.started)}


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


def make_handler(service, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _trusted(self):
            if len(self.headers.get_all("Host", [])) != 1 or len(self.headers.get_all("Origin", [])) > 1:
                return False
            host = self.headers.get("Host", "")
            expected = "127.0.0.1:" + str(self.server.server_address[1])
            origin = self.headers.get("Origin")
            return host == expected and (origin is None or origin == "http://" + expected)

        def _send(self, status, data, kind="application/json"):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._trusted():
                self._send(403, {"error": "Untrusted host or origin"})
                return
            if self.path == "/api/session":
                self._send(200, {"token": token})
            elif self.path == "/api/status":
                self._send(200, service.snapshot())
            elif self.path in {"/", "/app.js", "/style.css"}:
                name = "index.html" if self.path == "/" else self.path[1:]
                kind = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name]
                self._send(200, (WEB / name).read_bytes(), kind)
            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            supplied = self.headers.get("X-Jarvis-Token", "")
            if not self._trusted() or len(self.headers.get_all("X-Jarvis-Token", [])) != 1 or not hmac.compare_digest(supplied, token):
                self._send(403, {"error": "Local session authorization required"})
                return
            try:
                if self.headers.get("Transfer-Encoding") is not None or len(self.headers.get_all("Content-Length", [])) != 1 or len(self.headers.get_all("Content-Type", [])) != 1:
                    raise ValueError("Ambiguous framing")
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 2048 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Send a small JSON request")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/command":
                    service.command(data.get("command"))
                elif self.path == "/api/arm":
                    service.arm(data.get("lock"), data.get("sheet"))
                elif self.path == "/api/demo" and service.actions.demo:
                    people = data.get("people")
                    if people is not None and (type(people) is not int or not 0 <= people <= 20):
                        raise ValueError("Invalid demo count")
                    with service.lock:
                        service.demo_people = people
                else:
                    self._send(404, {"error": "Not found"})
                    return
            except (ValueError, TypeError, AttributeError):
                self._send(400, {"error": "Invalid request or action not enabled"})
                return
            self._send(200, service.snapshot())

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Local Jarvis dashboard. Default: simulation only.")
    parser.add_argument("--demo", action="store_true", help="Explicit simulation mode (also the default)")
    parser.add_argument("--camera", action="store_true", help="Use a local webcam after activation")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--allow-lock", action="store_true", help="Permit UI arming of real Windows locks")
    parser.add_argument("--allow-spreadsheet", action="store_true", help="Permit UI arming of the synthetic workbook demo")
    parser.add_argument("--voice-model", type=Path, help="Local Vosk model directory; microphone activation only")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.demo and args.camera:
        parser.error("Choose demo or camera mode")
    if not args.camera and (args.allow_lock or args.allow_spreadsheet):
        parser.error("Real actions require camera mode")
    if args.voice_model and not args.voice_model.is_dir():
        parser.error("Voice model directory does not exist")
    service = Service(Actions(demo=not args.camera, allow_lock=args.allow_lock, allow_sheet=args.allow_spreadsheet))
    server = LocalHTTPServer(("127.0.0.1", args.port), make_handler(service, secrets.token_urlsafe(32)))
    stop = threading.Event()

    def ticker():
        while not stop.wait(0.2):
            service.tick()

    threading.Thread(target=ticker, daemon=True).start()
    if args.camera:
        from .devices import run_camera
        threading.Thread(target=run_camera, args=(service, stop, args.camera_index), daemon=True).start()
    if args.voice_model:
        from .devices import run_voice
        threading.Thread(target=run_voice, args=(service, stop, args.voice_model), daemon=True).start()
    print("Jarvis: http://127.0.0.1:" + str(server.server_address[1]))
    print("Simulation only" if service.actions.demo else "Camera mode. Actions are disarmed.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
