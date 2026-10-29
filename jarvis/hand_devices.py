"""Optional device adapters. Instantiate only after local permission is granted."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


class WindowsPointer:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("The pointer adapter requires Windows")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        ulong_ptr = wintypes.WPARAM

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HardwareInput(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class InputUnion(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        self.Input, self.MouseInput = Input, MouseInput
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = wintypes.SHORT

    def primary_screen_size(self):
        return int(self.user32.GetSystemMetrics(0)), int(self.user32.GetSystemMetrics(1))

    def position(self):
        point = self.wintypes.POINT()
        if not self.user32.GetCursorPos(self.ctypes.byref(point)):
            raise OSError("Windows pointer position unavailable")
        return point.x, point.y

    def move_to(self, x, y):
        if self.emergency_requested():
            raise OSError("Emergency pointer stop")
        if not self.user32.SetCursorPos(int(x), int(y)):
            raise OSError("Windows rejected pointer movement")

    def _send(self, flags):
        events = (self.Input * len(flags))()
        for event, flag in zip(events, flags):
            event.type = 0
            event.mi = self.MouseInput(0, 0, 0, flag, 0, 0)
        if self.user32.SendInput(len(events), events, self.ctypes.sizeof(self.Input)) != len(events):
            raise OSError("Windows rejected mouse input")

    def click(self, button):
        if button not in {"left", "right"}:
            raise ValueError("Unsupported mouse button")
        if self.emergency_requested():
            raise OSError("Emergency pointer stop")
        self._send((0x0002, 0x0004) if button == "left" else (0x0008, 0x0010))

    def release_all(self):
        self._send((0x0004 | 0x0010,))

    def emergency_requested(self):
        if self.user32.GetAsyncKeyState(0x1B) & 0x8000:
            return True
        return self.corner_requested()

    def corner_requested(self):
        x, y = self.position()
        width, height = self.primary_screen_size()
        return (x <= 2 or x >= width - 3) and (y <= 2 or y >= height - 3)


class MediaPipeHands:
    def __init__(self, model_path):
        path = Path(model_path)
        if not path.is_file() or path.stat().st_size > 12 * 1024 * 1024:
            raise ValueError("A supported local hand model is required")
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Hand model checksum does not match the supported release")
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self.cv2, self.mp = cv2, mp
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.landmarker = vision.HandLandmarker.create_from_options(options)
        self.last_ms = -1
        self.closed = False

    def detect(self, frame, now):
        if self.closed:
            raise RuntimeError("Hand tracker is closed")
        mirrored = self.cv2.flip(frame, 1)
        rgb = self.cv2.cvtColor(mirrored, self.cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        timestamp = max(self.last_ms + 1, int(now * 1000))
        self.last_ms = timestamp
        result = self.landmarker.detect_for_video(image, timestamp)
        return result.hand_landmarks

    def close(self):
        if not self.closed:
            self.closed = True
            self.landmarker.close()


def run_hand_session(source, tracker, controller, stop: threading.Event, emergency_requested=lambda: False, clock=time.monotonic):
    """Exclusive-source runner. A shared camera broker must use its own ownership rules."""
    try:
        while controller.active and not stop.is_set():
            frame_started = clock()
            if emergency_requested():
                controller.stop("Escape or manual corner stop")
                break
            ok, frame = source.read()
            if not ok:
                controller.stop("Camera read failed")
                break
            hands = tracker.detect(frame, clock())
            del frame
            controller.step(hands, clock())
            stop.wait(max(0, 1 / 30 - (clock() - frame_started)))
    except Exception:
        controller.stop("Hand tracking failed")
    finally:
        controller.stop("Hand session ended" if controller.active else controller.reason)
        try:
            tracker.close()
        finally:
            source.release()
