"""Local permissions, practice state, and exclusive physical camera leases."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass


class UserError(ValueError):
    """A rejection whose message is safe to show in the dashboard."""


class CameraBusy(UserError):
    pass


@dataclass(frozen=True)
class CameraLease:
    owner: str
    generation: int


class CameraBroker:
    LABELS = {"presence": "the presence guard", "hand": "hand control"}

    def __init__(self):
        self._lock = threading.Lock()
        self._lease = None
        self._engaged = False
        self._generation = 0

    @property
    def owner(self):
        with self._lock:
            return self._lease.owner if self._lease else None

    def claim(self, owner):
        if owner not in self.LABELS:
            raise ValueError("Unknown camera owner")
        with self._lock:
            if self._lease is not None:
                if self._lease.owner != owner:
                    raise CameraBusy("The camera is in use by " + self.LABELS[self._lease.owner] + ". Stop it and wait for the camera to close first.")
                return self._lease
            self._generation += 1
            self._lease = CameraLease(owner, self._generation)
            return self._lease

    def engage(self, lease):
        with self._lock:
            if self._lease != lease or self._engaged:
                return False
            self._engaged = True
            return True

    def release(self, owner, lease=None):
        """Cancel an idle reservation; open devices must call closed after release."""
        with self._lock:
            if self._lease is None:
                return True
            if self._lease.owner != owner or (lease is not None and self._lease != lease) or self._engaged:
                return False
            self._lease = None
            return True

    def closed(self, lease):
        with self._lock:
            if self._lease != lease:
                return False
            self._engaged = False
            self._lease = None
            return True


class HandControl:
    def __init__(self, allowed=False, engine_available=False):
        self.allowed, self.engine_available = allowed, engine_available
        self.mode = "off"
        self.voice_armed = self.move_cursor = False
        self.cursor = None
        self.clicks = 0
        self.last_stop = None

    def arm(self, voice, move_cursor):
        if type(voice) is not bool or type(move_cursor) is not bool:
            raise ValueError("Hand control settings must be booleans")
        if (voice or move_cursor) and not self.allowed:
            raise UserError("Restart with --allow-hand-control before arming hand control")
        if self.mode == "watching" and move_cursor != self.move_cursor:
            raise UserError("Stop hand control before changing the real pointer permission")
        self.voice_armed, self.move_cursor = voice, move_cursor

    def start(self, source):
        if not self.allowed:
            raise UserError("Live hand control needs the --allow-hand-control launch permission")
        if source == "voice" and not self.voice_armed:
            raise UserError("Arm voice watch me in the dashboard first")
        if not self.engine_available:
            raise UserError("Install the hand packages and verified model, then restart Jarvis")
        self.mode, self.cursor, self.clicks, self.last_stop = "watching", None, 0, None

    def practice(self, on):
        if type(on) is not bool:
            raise ValueError("Practice must be true or false")
        if on and self.mode == "watching":
            raise UserError("Stop live hand control before practicing")
        self.mode, self.cursor, self.clicks = ("practice" if on else "off"), None, 0

    def stop(self, reason, disarm=False):
        was = self.mode
        self.mode, self.cursor = "off", None
        if disarm:
            self.voice_armed = self.move_cursor = False
        if was != "off":
            self.last_stop = reason
        return was != "off"

    def gesture(self, event):
        if type(event) is not dict or event.get("type") not in ("pointer", "click", "clap"):
            raise ValueError("Unknown gesture event")
        if self.mode != "practice":
            raise UserError("Synthetic gestures are available in practice mode only")
        kind = event["type"]
        if kind == "clap":
            self.stop("clap")
        elif kind == "pointer":
            x, y = event.get("x"), event.get("y")
            if any(type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value) for value in (x, y)):
                raise ValueError("Pointer coordinates must be between 0 and 1")
            self.cursor = (round(float(x), 4), round(float(y), 4))
        else:
            if event.get("button", "left") not in {"left", "right"}:
                raise ValueError("Click button must be left or right")
            self.clicks += 1
        return []

    def snapshot(self):
        return {"mode": self.mode, "allowed": self.allowed, "tracking_available": self.engine_available,
                "voice_armed": self.voice_armed, "move_cursor": self.move_cursor,
                "cursor": None if self.cursor is None else {"x": self.cursor[0], "y": self.cursor[1]},
                "clicks": self.clicks, "last_stop": self.last_stop}
