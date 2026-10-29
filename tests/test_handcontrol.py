import json
import queue
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from jarvis.devices import run_voice
from jarvis.hand_adapter import EscStopWatcher
from jarvis.hand_worker import LiveHandWorker
from jarvis.handcontrol import CameraBroker, CameraBusy, HandControl, UserError
from jarvis.server import Service, build_parser, check_args


def hand(wrist=0.5, middle=(0.8, 0.7), ring=(0.8, 0.2)):
    points = [(0.5, 0.5)] * 21
    points[0], points[4], points[8], points[12], points[16] = (wrist, 0.75), (0.5, 0.5), (0.5, 0.4), middle, ring
    return points


class ImmediateRuntime:
    available = True

    def __init__(self):
        self.live = False
        self.starts = 0
        self.corner = False

    def bind(self, broker, callback):
        self.broker, self.callback = broker, callback

    def start(self, lease, move_cursor=False):
        self.lease, self.live = lease, True
        self.starts += 1
        self.broker.engage(lease)

    def stop(self, reason):
        if self.live:
            self.live = False
            self.broker.closed(self.lease)
            self.callback(self.lease, reason, True)

    def running(self):
        return self.live

    def snapshot(self):
        return {"state": "tracking" if self.live else "off", "reason": None, "cursor": None}

    def corner_requested(self):
        return self.corner

    def join(self, timeout):
        pass


def live_service(allowed=True):
    return Service(hand_allowed=allowed, hand_runtime=ImmediateRuntime())


class StateTests(unittest.TestCase):
    def test_boot_off_disarmed_and_no_worker_started(self):
        service = live_service()
        self.assertEqual(service.snapshot()["hand"]["mode"], "off")
        self.assertFalse(service.hand.voice_armed or service.hand.move_cursor)
        self.assertEqual(service.hand_runtime.starts, 0)
        self.assertIsNone(service.camera.owner)

    def test_typed_and_voice_starts_both_require_launch_permission(self):
        service = live_service(allowed=False)
        for source in ("typed", "voice"):
            with self.assertRaises(UserError):
                service.command("watch me", source=source)
            self.assertEqual(service.hand.mode, "off")
            self.assertIsNone(service.camera.owner)
        self.assertEqual(service.hand_runtime.starts, 0)

    def test_voice_needs_separate_local_arming(self):
        service = live_service()
        with self.assertRaises(UserError):
            service.command("watch me", source="voice")
        service.hand_arm(True, False)
        service.command("watch me", source="voice")
        self.assertEqual(service.hand.mode, "watching")
        service.shutdown()

    def test_repeat_watch_is_idempotent_and_alias_is_supported(self):
        service = live_service()
        service.command("watchme")
        service.command("watch me")
        self.assertEqual(service.hand_runtime.starts, 1)
        service.shutdown()

    def test_pointer_permission_changes_require_restart_of_hand_session(self):
        service = live_service()
        service.command("watch me")
        self.assertFalse(service.hand.move_cursor)
        with self.assertRaises(UserError):
            service.hand_arm(False, True)
        service.command("stop mouse")
        service.hand_arm(False, True)
        service.command("watch me")
        self.assertTrue(service.hand.move_cursor)
        service.shutdown()

    def test_practice_clap_leaves_active_armed_presence_guard_alone(self):
        service = live_service()
        service.arm(True, False)
        service.command("active")
        service.hand_practice({"type": "start"})
        service.hand_practice({"type": "clap"})
        self.assertTrue(service.presence.active)
        self.assertTrue(service.presence.lock_armed)
        self.assertEqual(service.camera.owner, "presence")
        self.assertEqual(service.hand_runtime.starts, 0)

    def test_camera_modes_are_mutually_exclusive_and_guard_not_disarmed(self):
        service = live_service()
        service.arm(True, False)
        service.command("active")
        with self.assertRaises(CameraBusy):
            service.command("watch me")
        self.assertTrue(service.presence.active and service.presence.lock_armed)
        service.command("inactive")
        service.command("watch me")
        with self.assertRaises(CameraBusy):
            service.command("active")
        service.shutdown()

    def test_stop_mouse_and_global_stop_have_distinct_scope(self):
        service = live_service()
        service.arm(True, False)
        service.hand_arm(True, True)
        service.command("watch me")
        service.command("stop watching")
        self.assertTrue(service.presence.lock_armed)
        self.assertTrue(service.hand.voice_armed and service.hand.move_cursor)
        service.command("watch me")
        service.command("stop")
        self.assertFalse(service.presence.lock_armed or service.hand.voice_armed or service.hand.move_cursor)

    def test_practice_never_starts_runtime_and_invalid_events_rejected(self):
        service = live_service()
        service.hand_practice({"type": "start"})
        service.hand_practice({"type": "pointer", "x": 0.2, "y": 0.4})
        service.hand_practice({"type": "click"})
        self.assertEqual(service.snapshot()["hand"]["clicks"], 1)
        self.assertEqual(service.hand_runtime.starts, 0)
        for event in ({"type": "pointer", "x": float("nan"), "y": 0}, {"type": "pointer", "x": True, "y": 0}, {"type": "start", "extra": 1}):
            with self.assertRaises(ValueError):
                service.hand_practice(event)

    def test_lease_cannot_release_before_device_closed_and_stale_close_is_ignored(self):
        broker = CameraBroker()
        hand_lease = broker.claim("hand")
        self.assertTrue(broker.engage(hand_lease))
        self.assertFalse(broker.release("hand", hand_lease))
        with self.assertRaises(CameraBusy):
            broker.claim("presence")
        self.assertTrue(broker.closed(hand_lease))
        presence_lease = broker.claim("presence")
        self.assertFalse(broker.closed(hand_lease))
        self.assertEqual(broker.owner, "presence")
        self.assertTrue(broker.release("presence", presence_lease))

    def test_presence_lease_waits_for_physical_close(self):
        service = live_service()
        service.command("active")
        lease = service.acquire_presence_camera()
        service.command("inactive")
        self.assertEqual(service.camera.owner, "presence")
        with self.assertRaises(CameraBusy):
            service.command("watch me")
        service.presence_camera_closed(lease)
        service.command("watch me")
        service.shutdown()


class FakePointer:
    def __init__(self):
        self.events = []
        self.moved = threading.Event()
        self.clicked = {"left": threading.Event(), "right": threading.Event()}

    def primary_screen_size(self):
        return 1920, 1080

    def position(self):
        return 960, 540

    def move_to(self, x, y):
        self.events.append(("move", x, y))
        self.moved.set()

    def click(self, button):
        self.events.append(("click", button))
        self.clicked[button].set()

    def release_all(self):
        self.events.append(("release",))

    def corner_requested(self):
        return False


class FakeSource:
    def __init__(self):
        self.frames = queue.Queue()
        self.read_entered = threading.Event()
        self.release_entered = threading.Event()
        self.allow_release = threading.Event()
        self.allow_release.set()
        self.released = 0
        self.release_failure = False
        self.on_read = lambda: None

    def read(self):
        self.read_entered.set()
        frame = self.frames.get(timeout=2)
        self.on_read()
        return frame is not None, frame

    def release(self):
        self.release_entered.set()
        self.allow_release.wait(2)
        if self.release_failure:
            raise OSError("fixture")
        self.released += 1


class FakeTracker:
    def __init__(self):
        self.closed = 0
        self.detected = threading.Event()

    def detect(self, frame, now):
        self.detected.set()
        return frame

    def close(self):
        self.closed += 1


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.pointer, self.source, self.tracker = FakePointer(), FakeSource(), FakeTracker()
        self.logical_time = 0
        def advance():
            self.logical_time += 1
        self.source.on_read = advance
        self.pointer_factory_calls = 0
        def pointer_factory():
            self.pointer_factory_calls += 1
            return self.pointer
        self.worker = LiveHandWorker("synthetic-model", pointer_factory=pointer_factory,
                                     source_factory=lambda index: self.source, tracker_factory=lambda path: self.tracker,
                                     clock=lambda: self.logical_time, available=True)
        self.service = Service(hand_allowed=True, hand_runtime=self.worker)

    def tearDown(self):
        self.source.allow_release.set()
        self.service.hand_stop("test cleanup")
        self.source.frames.put(None)
        self.worker.join(3)
        self.assertFalse(self.worker.thread is not None and self.worker.thread.is_alive())

    def start(self, pointer=False):
        self.service.hand_arm(False, pointer)
        self.service.command("watch me")
        self.assertTrue(self.source.read_entered.wait(1))

    def wait_closed(self):
        self.assertTrue(self.worker.closed.wait(2))

    def test_observation_start_never_constructs_real_pointer(self):
        self.start()
        self.assertEqual(self.pointer_factory_calls, 0)
        self.assertEqual(self.pointer.events, [])

    def test_model_failure_never_constructs_pointer_or_opens_camera(self):
        self.worker.tracker_factory = lambda path: (_ for _ in ()).throw(ValueError("synthetic model failure"))
        self.service.hand_arm(False, True)
        self.service.command("watch me")
        self.wait_closed()
        self.assertEqual(self.pointer_factory_calls, 0)
        self.assertFalse(self.source.read_entered.is_set())
        self.assertIsNone(self.service.camera.owner)
        self.assertIn("failed", self.service.hand.last_stop)

    def test_live_middle_and_ring_pinches_use_the_real_engine(self):
        self.start(pointer=True)
        for frame in ([hand()], [hand(middle=(0.5, 0.5))], [hand()], [hand(ring=(0.5, 0.5))]):
            self.source.frames.put(frame)
        self.assertTrue(self.pointer.clicked["right"].wait(1))
        clicks = [event for event in self.pointer.events if event[0] == "click"]
        self.assertEqual(clicks, [("click", "left"), ("click", "right")])

    def test_visual_clap_stops_worker_and_preserves_guard_arming(self):
        self.service.arm(True, False)
        self.start(pointer=True)
        self.source.frames.put([hand(wrist=0.2), hand(wrist=0.8)])
        self.source.frames.put([hand(wrist=0.45), hand(wrist=0.55)])
        self.wait_closed()
        self.assertEqual(self.pointer.events, [("release",)])
        self.assertTrue(self.service.presence.lock_armed)
        self.assertIn("Visual clap", self.service.hand.last_stop)
        self.assertEqual(self.source.released, 1)
        self.assertEqual(self.tracker.closed, 1)
        self.assertIsNone(self.service.camera.owner)

    def test_local_stop_retains_lease_until_capture_has_closed(self):
        self.start(pointer=True)
        self.source.allow_release.clear()
        self.service.hand_stop("local stop")
        self.source.frames.put(None)
        self.assertTrue(self.source.release_entered.wait(1))
        self.assertEqual(self.service.camera.owner, "hand")
        with self.assertRaises(CameraBusy):
            self.service.command("active")
        with self.assertRaises(CameraBusy):
            self.service.command("watch me")
        self.source.allow_release.set()
        self.wait_closed()
        self.service.command("active")
        self.assertTrue(self.service.presence.active)

    def test_failed_camera_close_keeps_lease_and_blocks_restart(self):
        self.start()
        self.source.release_failure = True
        self.service.hand_stop("local stop")
        self.source.frames.put(None)
        self.wait_closed()
        self.assertEqual(self.service.camera.owner, "hand")
        self.assertEqual(self.worker.state, "fault")
        with self.assertRaises(CameraBusy):
            self.service.command("watch me")
        with self.assertRaises(CameraBusy):
            self.service.command("active")

    def test_independent_escape_stops_control_while_camera_read_waits(self):
        self.start(pointer=True)
        user32 = types.SimpleNamespace(GetAsyncKeyState=lambda key: 0x8000)
        EscStopWatcher(self.service, user32).poll()
        self.assertFalse(self.worker.controller.active)
        self.assertEqual(self.pointer.events, [("release",)])
        self.assertEqual(self.service.hand.last_stop, "Esc key")

    def test_manual_corner_watcher_stops_without_disarming_guard(self):
        self.service.arm(True, False)
        self.start(pointer=True)
        self.pointer.corner_requested = lambda: True
        user32 = types.SimpleNamespace(GetAsyncKeyState=lambda key: 0)
        EscStopWatcher(self.service, user32).poll()
        self.assertFalse(self.worker.controller.active)
        self.assertTrue(self.service.presence.lock_armed)


class LaunchTests(unittest.TestCase):
    def test_hand_only_launch_without_presence_camera(self):
        parser = build_parser()
        with patch("jarvis.server.sys.platform", "win32"):
            args = parser.parse_args(["--allow-hand-control"])
            check_args(parser, args)
        self.assertFalse(args.camera)
        self.assertEqual(args.hand_model.name, "hand_landmarker.task")
        for flags in (["--demo", "--allow-hand-control"], ["--allow-lock"]):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                check_args(parser, parser.parse_args(flags))
        with patch("jarvis.server.sys.platform", "linux"), patch("sys.stderr"), self.assertRaises(SystemExit):
            check_args(parser, parser.parse_args(["--allow-hand-control"]))


class VoiceTests(unittest.TestCase):
    def recognize(self, service, text, stop_during_recognition=False):
        captured, stop = {}, threading.Event()
        class Recognizer:
            def __init__(self, model, rate, grammar):
                captured["grammar"] = json.loads(grammar)
            def AcceptWaveform(self, block):
                if block == b"end":
                    stop.set()
                    return False
                return True
            def Result(self):
                if stop_during_recognition:
                    stop.set()
                return json.dumps({"text": text})
        class Stream:
            def __init__(self, callback, **kwargs):
                self.callback = callback
            def __enter__(self):
                self.callback(b"\0" * 8, 4, None, None)
                self.callback(b"end", 1, None, None)
                return self
            def __exit__(self, *args):
                pass
        vosk = types.SimpleNamespace(SetLogLevel=lambda level: None, Model=lambda path: object(), KaldiRecognizer=Recognizer)
        with patch.dict(sys.modules, {"vosk": vosk, "sounddevice": types.SimpleNamespace(RawInputStream=Stream)}):
            run_voice(service, stop, "synthetic-model")
        return captured["grammar"]

    def test_watch_me_requires_both_launch_and_voice_arming(self):
        disabled = live_service(allowed=False)
        self.assertEqual(self.recognize(disabled, "watch me"), ["active", "[unk]"])
        enabled = live_service()
        self.recognize(enabled, "watch me")
        self.assertEqual(enabled.hand.mode, "off")
        enabled.hand_arm(True, False)
        self.recognize(enabled, "watch me")
        self.assertEqual(enabled.hand.mode, "watching")
        enabled.shutdown()

    def test_command_recognized_after_stop_is_discarded(self):
        for text in ("active", "watch me"):
            with self.subTest(text=text):
                service = live_service()
                service.hand_arm(True, False)
                self.recognize(service, text, stop_during_recognition=True)
                self.assertFalse(service.presence.active)
                self.assertEqual(service.hand.mode, "off")
                self.assertIsNone(service.camera.owner)
                self.assertFalse(any(event["message"].startswith("Command") for event in service.events))

    def test_lost_microphone_reopens_without_reloading_the_model(self):
        for failure in ("silent", "error"):
            with self.subTest(failure=failure):
                service, stop, loads, opens, statuses = live_service(), threading.Event(), [], [], []
                service.voice_status = statuses.append
                class Recognizer:
                    def __init__(self, model, rate, grammar):
                        pass
                    def AcceptWaveform(self, block):
                        if block == b"end":
                            stop.set()
                            return False
                        return True
                    def Result(self):
                        return json.dumps({"text": "active"})
                class Stream:
                    def __init__(self, callback, **kwargs):
                        self.callback = callback
                        opens.append(kwargs)
                    def __enter__(self):
                        if len(opens) == 1:
                            if failure == "error":
                                raise OSError("synthetic device loss")
                            return self
                        self.callback(b"\0" * 3200, 1600, None, None)
                        self.callback(b"end", 1, None, None)
                        return self
                    def __exit__(self, *args):
                        pass
                vosk = types.SimpleNamespace(SetLogLevel=lambda level: None, Model=lambda path: loads.append(path), KaldiRecognizer=Recognizer)
                with patch.dict(sys.modules, {"vosk": vosk, "sounddevice": types.SimpleNamespace(RawInputStream=Stream)}), patch("jarvis.devices.STALL_SECONDS", 0.05):
                    run_voice(service, stop, "synthetic-model", retry_seconds=0.01)
                self.assertEqual(len(loads), 1)
                self.assertEqual(len(opens), 2)
                self.assertEqual(opens[0]["blocksize"] / opens[0]["samplerate"], 0.1)
                self.assertIn("Microphone stopped; retrying. Typed commands still work", statuses)
                self.assertEqual(statuses[-1], "Listening locally")
                self.assertTrue(service.presence.active)
                service.shutdown()

    def test_voice_stop_interrupts_microphone_retry(self):
        service, stop = live_service(), threading.Event()
        class Stream:
            def __init__(self, callback, **kwargs):
                pass
            def __enter__(self):
                threading.Timer(0.05, stop.set).start()
                raise OSError("synthetic missing microphone")
            def __exit__(self, *args):
                pass
        vosk = types.SimpleNamespace(SetLogLevel=lambda level: None, Model=lambda path: object(), KaldiRecognizer=lambda *args: object())
        started = time.monotonic()
        with patch.dict(sys.modules, {"vosk": vosk, "sounddevice": types.SimpleNamespace(RawInputStream=Stream)}):
            run_voice(service, stop, "synthetic-model", retry_seconds=30)
        self.assertLess(time.monotonic() - started, 2)
