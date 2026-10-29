"""Independent Escape and manual-corner stop, active only for live hands."""
from __future__ import annotations

import ctypes
import sys
import threading

VK_ESCAPE = 0x1B


def _user32():
    if sys.platform != "win32":
        raise RuntimeError("The emergency keyboard adapter requires Windows")
    return ctypes.windll.user32


class EscStopWatcher:
    def __init__(self, service, user32=None, interval=0.03):
        self.service, self.user32, self.interval = service, user32, interval

    def poll(self, was_active=False):
        if not self.service.hand_active():
            return False
        if self.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            self.service.hand_stop("Esc key")
            return False
        if self.service.hand_corner_requested():
            self.service.hand_stop("Manual corner stop")
            return False
        return True

    def run(self, stop: threading.Event):
        if self.user32 is None:
            self.user32 = _user32()
        while not stop.wait(self.interval):
            try:
                self.poll()
            except Exception:
                self.service.hand_stop("Emergency stop watcher failed")
