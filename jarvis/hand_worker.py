"""Lazy local hand worker. Leases survive until the physical camera closes."""
from __future__ import annotations

import hashlib
import importlib.util
import threading
import time
from pathlib import Path

from .hand_control import HandController
from .hand_devices import MODEL_SHA256, MediaPipeHands, WindowsPointer
from .handcontrol import CameraBusy, UserError


class MemoryPointer:
    """Observation output; never reads or moves the operating-system pointer."""
    def move_to(self, x, y):
        pass

    def click(self, button):
        pass

    def release_all(self):
        pass


def open_hand_camera(index):
    import cv2
    return cv2.VideoCapture(index, cv2.CAP_DSHOW)


class LiveHandWorker:
    def __init__(self, model_path, camera_index=0, pointer_factory=WindowsPointer,
                 source_factory=open_hand_camera, tracker_factory=MediaPipeHands,
                 clock=time.monotonic, available=None):
        self.model_path = Path(model_path)
        self.camera_index = camera_index
        self.pointer_factory, self.source_factory, self.tracker_factory = pointer_factory, source_factory, tracker_factory
        self.clock = clock
        self.available = self._ready() if available is None else bool(available)
        self.broker = self.on_closed = None
        self.lock = threading.RLock()
        self.thread = self.controller = self.pointer = self.stop_event = None
        self.closed = threading.Event()
        self.closed.set()
        self.state, self.reason, self.lease, self.move_cursor = "off", None, None, False

    def _ready(self):
        try:
            return (self.model_path.is_file() and self.model_path.stat().st_size <= 12 * 1024 * 1024
                    and hashlib.sha256(self.model_path.read_bytes()).hexdigest() == MODEL_SHA256
                    and importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("mediapipe") is not None)
        except (OSError, ImportError, ValueError):
            return False

    def bind(self, broker, on_closed):
        self.broker, self.on_closed = broker, on_closed

    def running(self):
        return not self.closed.is_set()

    def start(self, lease, move_cursor=False):
        if not self.available:
            raise UserError("Install the hand packages and verified model, then restart Jarvis")
        with self.lock:
            if not self.closed.is_set():
                raise CameraBusy("The hand camera is starting or still closing. Wait for it to close.")
            self.closed.clear()
            self.stop_event = threading.Event()
            self.lease, self.move_cursor = lease, move_cursor
            self.state, self.reason = "starting", None
            self.thread = threading.Thread(target=self._run, args=(lease, self.stop_event, move_cursor), name="jarvis-hands", daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.closed.set()
                raise

    def _controller_stopped(self, reason):
        with self.lock:
            self.reason, self.state = reason, "stopping"
            if self.stop_event is not None:
                self.stop_event.set()

    def _run(self, lease, stop, move_cursor):
        source = tracker = controller = None
        reason, source_closed, engaged = "Hand session ended", True, False
        try:
            if not self.broker.engage(lease):
                raise UserError("Camera reservation expired")
            engaged = True
            tracker = self.tracker_factory(self.model_path)
            if stop.is_set():
                return
            pointer = self.pointer_factory() if move_cursor else MemoryPointer()
            size = pointer.primary_screen_size() if move_cursor else (1920, 1080)
            initial = pointer.position() if move_cursor else None
            controller = HandController(pointer, *size, on_stop=self._controller_stopped)
            controller.start(self.clock(), initial)
            with self.lock:
                self.pointer, self.controller = pointer, controller
            if stop.is_set():
                return
            source = self.source_factory(self.camera_index)
            source_closed = False
            if hasattr(source, "isOpened") and not source.isOpened():
                raise OSError("Camera unavailable")
            if hasattr(source, "set"):
                source.set(3, 640)
                source.set(4, 480)
            with self.lock:
                self.state = "tracking"
            while controller.active and not stop.is_set():
                started = self.clock()
                ok, frame = source.read()
                if not ok:
                    reason = "Hand camera read failed"
                    break
                if stop.is_set() or not controller.active:
                    del frame
                    break
                hands = tracker.detect(frame, self.clock())
                del frame
                controller.step(hands, self.clock())
                if not controller.active:
                    reason = controller.reason
                stop.wait(max(0, 1 / 30 - (self.clock() - started)))
        except Exception:
            reason = "Hand tracking could not start or failed. Check the model and camera."
        finally:
            if controller is not None:
                if controller.active:
                    controller.stop(self.reason or reason)
                reason = controller.reason
            try:
                if tracker is not None:
                    tracker.close()
            except Exception:
                reason = "Hand tracker cleanup failed"
            try:
                if source is not None:
                    source.release()
                    source_closed = True
            except Exception:
                reason = "Camera could not close. Restart Jarvis before changing camera modes."
            lease_released = engaged and source_closed and self.broker.closed(lease)
            with self.lock:
                self.reason, self.state = reason, ("off" if lease_released else "fault")
                self.controller = self.pointer = None
            try:
                if self.on_closed is not None:
                    self.on_closed(lease, reason, lease_released)
            finally:
                self.closed.set()

    def stop(self, reason):
        with self.lock:
            controller = self.controller
            if self.stop_event is not None:
                self.stop_event.set()
            if not self.closed.is_set():
                self.state, self.reason = "stopping", reason
        if controller is not None:
            controller.stop(reason)

    def join(self, timeout=2):
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def corner_requested(self):
        with self.lock:
            pointer = self.pointer if self.move_cursor and self.state == "tracking" else None
        return pointer.corner_requested() if pointer is not None else False

    def snapshot(self):
        with self.lock:
            controller, state, reason = self.controller, self.state, self.reason
        if controller is None:
            return {"state": state, "reason": reason, "cursor": None}
        snapshot = controller.snapshot()
        position = snapshot["position"]
        cursor = None if position is None else {"x": position[0] / (controller.width - 1), "y": position[1] / (controller.height - 1)}
        return {"state": state, "reason": snapshot["reason"], "cursor": cursor, "clicks": snapshot["clicks"]}
