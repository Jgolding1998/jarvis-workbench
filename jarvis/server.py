from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socket
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import planner
from .actions import Actions
from .handcontrol import CameraBroker, CameraBusy, HandControl, UserError
from .hand_control import watch_intent
from .presence import Presence
from .onboarding import Onboarding, OnboardingError
from .credential_store import CredentialError
from .provider_client import ProviderError
from .budget import BudgetError

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml"),
          "/style.css": ("style.css", "text/css; charset=utf-8"), "/setup": ("setup.html", "text/html; charset=utf-8"),
          "/setup.js": ("setup.js", "text/javascript; charset=utf-8"), "/setup.css": ("setup.css", "text/css; charset=utf-8"),
          "/setup-catalog.json": ("setup-catalog.json", "application/json"),
          "/quickstart-catalog.json": ("quickstart-catalog.json", "application/json"),
          "/provider-catalog.json": ("provider-catalog.json", "application/json")}


class Service:
    def __init__(self, actions=None, clock=time.monotonic, hand_allowed=False, hand_runtime=None):
        self.clock = clock
        self.actions = actions or Actions()
        self.presence = Presence()
        self.camera = CameraBroker()
        self.hand_runtime = hand_runtime
        self.hand = HandControl(allowed=hand_allowed, engine_available=hand_runtime is not None and hand_runtime.available)
        self.lock = threading.RLock()
        self.events = deque(maxlen=40)
        self.demo_people = 1
        self.voice = "Disabled"
        self.action_error = None
        self.started = clock()
        self._presence_lease = self._hand_lease = None
        if hand_runtime is not None:
            hand_runtime.bind(self.camera, self._hand_closed)

    def _record(self, message):
        self.events.appendleft({"at": round(self.clock() - self.started, 1), "message": message})

    def _run(self, actions):
        for action in actions:
            try:
                self._record(self.actions.run(action))
            except Exception:
                self.action_error = "Desktop action failed. Check Windows permissions; the workstation may still be unlocked."
                self._record("Action failed: " + action + ". Check local permissions.")

    def command(self, command, source="typed"):
        if not isinstance(command, str):
            raise ValueError("Command must be text")
        text = " ".join(command.lower().split())
        intent = watch_intent(text)
        with self.lock:
            if intent == "start_hand_control":
                self._hand_start(source)
                self._record("Command: watch me")
                return
            if intent == "stop_hand_control" or text == "stop mouse":
                self.hand_stop("stop mouse")
                return
            was_active = self.presence.active
            if text == "active":
                if self.hand.mode == "watching":
                    raise CameraBusy("Stop hand control and wait for its camera to close first")
                if not was_active and self.camera.owner == "presence":
                    raise CameraBusy("The presence camera is still closing. Wait before restarting it.")
                self._presence_lease = self.camera.claim("presence")
            try:
                self.presence.command(command, self.clock())
            except ValueError:
                if not was_active and self.camera.release("presence", self._presence_lease):
                    self._presence_lease = None
                raise
            if not self.presence.active and self.camera.release("presence", self._presence_lease):
                self._presence_lease = None
            if text == "stop":
                self.hand_stop("Stop and disarm", disarm=True)
            if not was_active or not self.presence.active:
                self.action_error = None
            self._record("Command: " + text)

    def _hand_start(self, source):
        if self.presence.active:
            raise CameraBusy("The presence guard owns the camera. Stop it explicitly before using hand control.")
        if self.hand.mode == "watching":
            if source == "voice" and not self.hand.voice_armed:
                raise UserError("Arm voice watch me in the dashboard first")
            return
        if self.hand_runtime is not None and self.hand_runtime.running():
            raise CameraBusy("The hand camera is still closing. Wait before restarting it.")
        if self._hand_lease is not None and self.camera.owner == "hand":
            raise CameraBusy("The hand camera did not close. Restart Jarvis before using it again.")
        lease = self.camera.claim("hand")
        previous = self.hand.__dict__.copy()
        try:
            self.hand.start(source)
            self._hand_lease = lease
            self.hand_runtime.start(lease, move_cursor=self.hand.move_cursor)
        except Exception:
            self.hand.__dict__.update(previous)
            if self.camera.release("hand", lease):
                self._hand_lease = None
            raise

    def hand_stop(self, reason, record=True, disarm=False):
        with self.lock:
            stopped = self.hand.stop(reason, disarm=disarm)
            if self.hand_runtime is not None:
                self.hand_runtime.stop(reason)
            if self._hand_lease is not None and (self.hand_runtime is None or not self.hand_runtime.running()):
                if self.camera.release("hand", self._hand_lease):
                    self._hand_lease = None
            if stopped and record:
                self._record("Hand control stopped: " + reason)

    def _hand_closed(self, lease, reason, source_closed):
        with self.lock:
            if self._hand_lease == lease:
                self.hand.stop(reason)
                self.hand.last_stop = reason
                if source_closed:
                    self._hand_lease = None
                self._record("Hand camera closed: " + reason if source_closed else reason)

    def hand_active(self):
        with self.lock:
            return self.hand.mode == "watching"

    def hand_corner_requested(self):
        return self.hand_runtime.corner_requested() if self.hand_runtime is not None else False

    def hand_arm(self, voice, move_cursor):
        with self.lock:
            self.hand.arm(voice, move_cursor)
            self._record("Hand control settings updated locally")

    def hand_practice(self, event):
        if type(event) is not dict or event.get("type") not in ("start", "stop", "pointer", "click", "clap"):
            raise ValueError("Unknown practice event")
        with self.lock:
            if event["type"] in ("start", "stop"):
                if set(event) != {"type"}:
                    raise ValueError("Unexpected fields")
                if self.hand_runtime is not None and self.hand_runtime.running():
                    raise UserError("Wait for the live hand camera to close before practicing")
                self.hand.practice(event["type"] == "start")
                self._record("Hand practice " + ("started" if event["type"] == "start" else "stopped"))
                return
            if set(event) - {"type", "x", "y", "button"}:
                raise ValueError("Unexpected fields")
            self.hand.gesture(event)
            if self.hand.mode == "off":
                self._record("Clap stopped hand practice")

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

    def acquire_presence_camera(self):
        with self.lock:
            if not self.presence.active:
                return None
            if self._presence_lease is None:
                self._presence_lease = self.camera.claim("presence")
            return self._presence_lease if self.camera.engage(self._presence_lease) else None

    def presence_camera_closed(self, lease):
        with self.lock:
            if self.camera.closed(lease) and self._presence_lease == lease:
                self._presence_lease = None

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
            return self.presence.active and self.camera.owner == "presence"

    def shutdown(self):
        self.command("stop")
        if self.hand_runtime is not None:
            self.hand_runtime.join(2)

    def snapshot(self):
        with self.lock:
            hand = self.hand.snapshot()
            if self.hand_runtime is not None:
                runtime = self.hand_runtime.snapshot()
                hand["state"], hand["reason"] = runtime["state"], runtime["reason"]
                if hand["mode"] == "watching":
                    hand.update({key: value for key, value in runtime.items() if key in {"cursor", "clicks"}})
            return {**self.presence.snapshot(self.clock()), "demo": self.actions.demo,
                    "allow_lock": self.actions.demo or self.actions.allow_lock,
                    "allow_sheet": self.actions.demo or self.actions.allow_sheet,
                    "voice": self.voice, "events": list(self.events), "action_error": self.action_error,
                    "hand": hand, "camera_owner": self.camera.owner,
                    "uptime": round(self.clock() - self.started)}

class SetupContext:
    """Local setup wizard state: catalog, ignored runtime directory, optional exit hook."""

    def __init__(self, catalog=None, runtime_dir=None, on_exit=None, root=None, onboarding=None):
        self.catalog = catalog or planner.load_catalog()
        self.root = Path(root or planner.ROOT)
        self.runtime_dir = Path(runtime_dir or self.root / "runtime")
        self.on_exit = on_exit
        self.write_lock = threading.Lock()
        self.onboarding = onboarding or Onboarding(self.root)


HEADER_SECONDS = 5.0
BODY_SECONDS = 10.0
MAX_CONNECTIONS = 16
_BUSY = b'{"error":"Server busy"}'
BUSY_RESPONSE = (b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: " + str(len(_BUSY)).encode()
                 + b"\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                 b"Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\nConnection: close\r\n\r\n" + _BUSY)


class SocketDeadline:
    """Shuts a connection down at a fixed time unless cancelled. It never fires after cancel() returns."""

    def __init__(self, connection, seconds):
        self.connection, self.lock = connection, threading.Lock()
        self.cancelled = self.expired = False
        self.timer = threading.Timer(seconds, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def _expire(self):
        with self.lock:
            if self.cancelled:
                return
            self.expired = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def cancel(self):
        with self.lock:
            self.cancelled = True
        self.timer.cancel()
        return self.expired


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            try:
                request.sendall(BUSY_RESPONSE)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()

    def handle_error(self, request, address):
        pass


def make_handler(service, token, setup=None):
    setup = setup or SetupContext()

    class Handler(BaseHTTPRequestHandler):
        def handle_one_request(self):
            self._header_deadline = SocketDeadline(self.connection, HEADER_SECONDS)
            try:
                super().handle_one_request()
            finally:
                self._header_deadline.cancel()

        def parse_request(self):
            try:
                accepted = super().parse_request()
            finally:
                expired = self._header_deadline.cancel()
            if expired:
                self.close_connection = True
                return False
            if accepted and self.request_version == "HTTP/0.9":
                self.send_error(400)
                return False
            return accepted

        def version_string(self):
            return "Jarvis"

        def log_message(self, *args):
            pass

        def send_error(self, code, message=None, explain=None):
            status = 405 if code == 501 else code
            public = {400: "Invalid request", 403: "Request rejected", 404: "Not found",
                      405: "Method not allowed", 408: "Request timed out", 414: "Request too large",
                      431: "Request headers too large"}.get(status, "Request failed")
            self.close_connection = True
            if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            self._send(status, {"error": public})

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
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            if not self._trusted():
                self._send(403, {"error": "Untrusted host or origin"})
                return
            if self.path == "/api/session":
                self._send(200, {"token": token})
            elif self.path == "/api/status":
                self._send(200, service.snapshot())
            elif self.path == "/api/setup/diagnostics":
                report = planner.diagnostics(setup.root)
                report.update({"local": True, "mode": "demo" if service.actions.demo else "camera",
                               "hand_allowed": service.hand.allowed, "can_exit": setup.on_exit is not None})
                self._send(200, report)
            elif self.path == "/api/setup/config":
                self._send(200, {"answers": planner.load_config(setup.runtime_dir, setup.catalog)})
            elif self.path == "/api/quickstart":
                self._send(200, setup.onboarding.snapshot())
            elif self.path in STATIC and (WEB / STATIC[self.path][0]).is_file():
                name, kind = STATIC[self.path]
                self._send(200, (WEB / name).read_bytes(), kind)
            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            supplied = self.headers.get("X-Jarvis-Token", "")
            if not self._trusted() or len(self.headers.get_all("X-Jarvis-Token", [])) != 1 or not supplied.isascii() or not hmac.compare_digest(supplied, token):
                self._send(403, {"error": "Local session authorization required"})
                return
            try:
                if self.headers.get("Transfer-Encoding") is not None or len(self.headers.get_all("Content-Length", [])) != 1 or len(self.headers.get_all("Content-Type", [])) != 1:
                    raise ValueError("Ambiguous framing")
                declared = self.headers.get("Content-Length", "")
                if not declared.isascii() or not declared.isdecimal() or len(declared) > 6:
                    raise ValueError("Invalid length")
                length = int(declared)
                limit = 20000 if self.path.startswith("/api/quickstart/") else 8192 if self.path == "/api/setup/apply" else 2048
                if not 1 <= length <= limit or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Send a small JSON request")
                body_deadline = SocketDeadline(self.connection, BODY_SECONDS)
                try:
                    raw = self.rfile.read(length)
                finally:
                    expired = body_deadline.cancel()
                if expired:
                    self.close_connection = True
                    return
                data = planner.parse_json(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                if self.path.startswith("/api/quickstart/"):
                    manager = setup.onboarding
                    action = self.path.removeprefix("/api/quickstart/")
                    expected = {"save": {"choices"}, "key": {"provider", "key"},
                                "key/delete": {"provider"}, "test": {"provider", "consent"},
                                "chat": {"provider", "prompt", "consent"},
                                "install": {"consent"}, "launch": {"consent"}}
                    if action not in expected:
                        self._send(404, {"error": "Not found"})
                        return
                    if set(data) != expected[action]:
                        raise ValueError("Unexpected fields")
                    if action == "save":
                        result = manager.save(data["choices"])
                    elif action == "key":
                        result = manager.save_key(data["provider"], data["key"])
                    elif action == "key/delete":
                        result = manager.delete_key(data["provider"])
                    elif action in {"chat", "test"}:
                        result = manager.chat(data["provider"], data.get("prompt", "Reply with the word ready."), data["consent"])
                    elif action == "install":
                        result = manager.start_install(data["consent"])
                    else:
                        result = manager.launch(data["consent"])
                    self._send(200, result)
                    return
                if self.path == "/api/setup/apply":
                    if set(data) != {"answers"}:
                        raise ValueError("Unexpected fields")
                    answers = planner.validate_answers(data["answers"], setup.catalog)
                    if not answers["install"]:
                        raise UserError("Nothing to apply when you chose not to install")
                    with setup.write_lock:
                        planner.save_config(answers, setup.runtime_dir)
                    self._send(200, {"saved": True, "path": "runtime/" + planner.CONFIG_NAME,
                                     "plan": planner.build_plan(answers, setup.catalog)})
                    return
                if self.path == "/api/setup/exit" and setup.on_exit is not None:
                    self._send(200, {"stopped": True})
                    setup.on_exit()
                    return
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
                elif self.path == "/api/hand/arm":
                    if set(data) != {"voice", "move_cursor"}:
                        raise ValueError("Unexpected fields")
                    service.hand_arm(data["voice"], data["move_cursor"])
                elif self.path == "/api/hand/practice":
                    service.hand_practice(data)
                else:
                    self._send(404, {"error": "Not found"})
                    return
            except BudgetError:
                self._send(400, {"error": "Paid AI is paused. Review the local allowance and pricing check.", "code": "budget_unavailable"})
                return
            except (UserError, OnboardingError, CredentialError, ProviderError) as error:
                self._send(400, {"error": str(error)})
                return
            except (ValueError, TypeError, AttributeError, OSError, OverflowError, RecursionError):
                self._send(400, {"error": "Invalid request or action not enabled"})
                return
            self._send(200, service.snapshot())

    return Handler


def build_parser():
    parser = argparse.ArgumentParser(description="Local Jarvis dashboard. Default: simulation only.")
    parser.add_argument("--demo", action="store_true", help="Explicit simulation mode (also the default)")
    parser.add_argument("--camera", action="store_true", help="Use a local webcam after activation")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--allow-lock", action="store_true", help="Permit UI arming of real Windows locks")
    parser.add_argument("--allow-spreadsheet", action="store_true", help="Permit UI arming of the synthetic workbook demo")
    parser.add_argument("--allow-hand-control", action="store_true", help="Permit voice watch me and real cursor movement after UI arming (Windows)")
    parser.add_argument("--hand-model", type=Path, default=Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task", help="Verified local MediaPipe hand model")
    parser.add_argument("--voice-model", type=Path, help="Local Vosk model directory; microphone activation only")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def check_args(parser, args):
    if args.demo and args.camera:
        parser.error("Choose demo or camera mode")
    if args.demo and args.allow_hand_control:
        parser.error("Demo mode does not open a hand camera; omit --demo for live hand control")
    if not args.camera and (args.allow_lock or args.allow_spreadsheet):
        parser.error("Real actions require camera mode")
    if args.allow_hand_control and sys.platform != "win32":
        parser.error("Real hand control is Windows only; practice mode works without this flag")
    if args.voice_model and not args.voice_model.is_dir():
        parser.error("Voice model directory does not exist")
    if not 0 <= args.camera_index <= 9:
        parser.error("Camera index must be 0 to 9")


def serve(args, open_path=None, opener=None):
    """Run the local server until interrupted or the wizard's exit route is used."""
    hand_runtime = None
    if args.allow_hand_control:
        from .hand_worker import LiveHandWorker
        hand_runtime = LiveHandWorker(args.hand_model, args.camera_index)
    service = Service(Actions(demo=not args.camera, allow_lock=args.allow_lock, allow_sheet=args.allow_spreadsheet),
                      hand_allowed=args.allow_hand_control, hand_runtime=hand_runtime)
    holder = {}

    def request_exit():
        threading.Thread(target=holder["server"].shutdown, daemon=True).start()

    server = LocalHTTPServer(("127.0.0.1", args.port), make_handler(service, secrets.token_urlsafe(32), SetupContext(on_exit=request_exit)))
    holder["server"] = server
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
    if args.allow_hand_control:
        from .hand_adapter import EscStopWatcher
        threading.Thread(target=EscStopWatcher(service).run, args=(stop,), daemon=True).start()
    url = "http://127.0.0.1:" + str(server.server_address[1])
    print("Jarvis: " + url + (open_path or ""))
    print("Live hand control is available but idle; pointer movement is disarmed." if args.allow_hand_control else
          "Simulation only" if service.actions.demo else "Camera mode. Actions are disarmed.")
    if opener is not None:
        opener(url + (open_path or "/"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        service.shutdown()
        server.server_close()
    print("Jarvis stopped.")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    check_args(parser, args)
    serve(args)


if __name__ == "__main__":
    main()
