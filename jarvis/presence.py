from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    absence_seconds: float = 30.0
    camera_timeout_seconds: float = 5.0
    crowd_seconds: float = 2.0
    clear_crowd_seconds: float = 5.0

    def __post_init__(self):
        if any(not math.isfinite(value) or value <= 0 for value in self.__dict__.values()):
            raise ValueError("Policy durations must be positive and finite")


class Presence:
    def __init__(self, policy: Policy | None = None):
        self.policy = policy or Policy()
        self.active = False
        self.lock_armed = False
        self.sheet_armed = False
        self.people = None
        self.last_frame = None
        self.started = None
        self.last_now = None
        self.absent_since = None
        self.crowd_since = None
        self.clear_since = None
        self.crowd_fired = False
        self.lock_fired = False
        self.established = False
        self.camera_error = None

    def _clock(self, now):
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("Time must be finite")
        if self.last_now is not None and now < self.last_now:
            raise ValueError("Monotonic clock moved backwards")
        self.last_now = now

    def arm(self, lock: bool, sheet: bool):
        if type(lock) is not bool or type(sheet) is not bool:
            raise ValueError("Arming values must be booleans")
        self.lock_armed, self.sheet_armed = lock, sheet

    def command(self, command: str, now: float):
        self._clock(now)
        if not isinstance(command, str):
            raise ValueError("Command must be text")
        command = command.strip().lower()
        if command == "active":
            if not self.active:
                self.active = True
                self.started = now
                self.last_frame = None
                self.people = None
                self.absent_since = None
                self.crowd_since = None
                self.clear_since = None
                self.crowd_fired = False
                self.lock_fired = False
                self.established = False
                self.camera_error = None
        elif command in ("inactive", "stop"):
            self.active = False
            self.lock_armed = False
            self.sheet_armed = False
            self.people = None
            self.last_frame = None
            self.absent_since = None
            self.crowd_since = None
            self.camera_error = None
            self.established = False
        else:
            raise ValueError("Use active, inactive, or stop")

    def observe(self, people: int, now: float):
        self._clock(now)
        if type(people) is not int or not 0 <= people <= 20:
            raise ValueError("People count must be an integer from 0 to 20")
        if not self.active:
            return []
        self.last_frame = now
        self.people = people
        self.camera_error = None
        if people >= 1:
            self.established = True
            self.absent_since = None
            self.lock_fired = False
        elif self.absent_since is None:
            self.absent_since = now
        if people >= 2:
            self.clear_since = None
            if self.crowd_since is None:
                self.crowd_since = now
        else:
            self.crowd_since = None
            if self.clear_since is None:
                self.clear_since = now
            if now - self.clear_since >= self.policy.clear_crowd_seconds:
                self.crowd_fired = False
        return self._evaluate(now)

    def fail(self, reason: str, now: float):
        self._clock(now)
        self.camera_error = "Camera unavailable"
        self.people = None
        self.crowd_since = None
        return self._evaluate(now)

    def tick(self, now: float):
        self._clock(now)
        return self._evaluate(now)

    def _evaluate(self, now):
        if not self.active:
            return []
        actions = []
        reference = self.last_frame if self.last_frame is not None else self.started
        stale = reference is not None and now - reference > self.policy.camera_timeout_seconds
        if stale:
            self.camera_error = "Camera unavailable"
            self.people = None
            self.crowd_since = None
            if self.lock_armed and not self.lock_fired:
                self.lock_fired = True
                actions.append("lock_camera_fault")
        elif self.established and self.absent_since is not None:
            if now - self.absent_since > self.policy.absence_seconds and self.lock_armed and not self.lock_fired:
                self.lock_fired = True
                actions.append("lock_absence")
        if not stale and self.crowd_since is not None:
            if now - self.crowd_since >= self.policy.crowd_seconds and (self.sheet_armed or self.lock_armed) and not self.crowd_fired:
                self.crowd_fired = True
                if self.lock_armed:
                    self.lock_fired = True
                    actions.append("lock_crowd")
                else:
                    actions.append("boss_alert")
        return actions

    def snapshot(self, now):
        reference = self.last_frame if self.last_frame is not None else self.started
        stale = self.active and reference is not None and now - reference > self.policy.camera_timeout_seconds
        return {
            "active": self.active, "lock_armed": self.lock_armed, "sheet_armed": self.sheet_armed,
            "people": self.people, "camera": "fault" if self.camera_error or stale else ("receiving" if self.last_frame is not None else "waiting"),
            "established": self.established,
            "absent_seconds": round(now - self.absent_since, 1) if self.active and self.established and self.absent_since is not None else 0,
            "lock_fired": self.lock_fired,
        }
