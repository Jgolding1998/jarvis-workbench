"""Local hand-to-pointer control. Importing this module opens no devices."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Callable, Protocol


class Pointer(Protocol):
    def move_to(self, x: int, y: int) -> None: ...
    def click(self, button: str) -> None: ...
    def release_all(self) -> None: ...


@dataclass(frozen=True)
class GestureSettings:
    smoothing: float = 0.55
    dead_zone: float = 0.12
    pinch_down: float = 0.05
    pinch_up: float = 0.085
    click_cooldown: float = 0.6
    clap_close: float = 0.15
    clap_open: float = 0.25
    edge_margin: int = 8
    lowered_gap: float = 2.0
    lowered_release: float = 1.5
    lowered_line: float = 0.6

    def __post_init__(self):
        numbers = (self.smoothing, self.dead_zone, self.pinch_down, self.pinch_up,
                   self.click_cooldown, self.clap_close, self.clap_open,
                   self.lowered_gap, self.lowered_release, self.lowered_line)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in numbers):
            raise ValueError("Gesture settings must be finite numbers")
        if not 0 <= self.smoothing < 1 or not 0 <= self.dead_zone < 0.45:
            raise ValueError("Invalid smoothing or dead zone")
        if not 0 < self.pinch_down < self.pinch_up < self.clap_close < self.clap_open <= 1:
            raise ValueError("Invalid gesture thresholds")
        if not 0.1 <= self.click_cooldown <= 5 or type(self.edge_margin) is not int or not 3 <= self.edge_margin <= 64:
            raise ValueError("Invalid click cooldown or edge margin")
        if not 0.5 <= self.lowered_release < self.lowered_gap <= 10 or not 0.3 <= self.lowered_line <= 0.95:
            raise ValueError("Invalid lowered-hand thresholds")


def _point(landmark):
    try:
        x, y = landmark.x, landmark.y
    except AttributeError:
        x, y = landmark[0], landmark[1]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in (x, y)):
        raise ValueError("Landmarks must use finite normalized coordinates")
    return float(x), float(y)


def _distance(hand, first, second):
    ax, ay = hand[first]
    bx, by = hand[second]
    return math.hypot(ax - bx, ay - by)


def _palm(hand):
    points = [hand[index] for index in (0, 5, 9, 13, 17)]
    return sum(x for x, _ in points) / 5, sum(y for _, y in points) / 5


def _palm_length(hand):
    return max(_distance(hand, 0, 9), 0.02)


class HandController:
    def __init__(self, pointer: Pointer, width: int, height: int, settings=None, on_stop: Callable[[str], None] | None = None):
        if type(width) is not int or type(height) is not int or not 160 <= width <= 32768 or not 160 <= height <= 32768:
            raise ValueError("A valid primary display size is required")
        self.pointer = pointer
        self.width, self.height = width, height
        self.settings = settings or GestureSettings()
        self.on_stop = on_stop
        self.active = False
        self.reason = "Not started"
        self.clicks = 0
        self._last_now = None
        self._needs_release = False
        self._last_sent = None
        self._previous = (width / 2, height / 2)
        self._left = self._right = self._click_ready = self._clap_armed = False
        self._active_palm = self._ignored_palm = None
        self._last_click = float("-inf")
        self._lock = threading.RLock()
        self._notifications = []

    def _notify(self, notifications):
        for reason in notifications:
            if self.on_stop is not None:
                try:
                    self.on_stop(reason)
                except Exception:
                    with self._lock:
                        self.reason = "Stopped; status callback failed"

    def snapshot(self):
        with self._lock:
            return {"active": self.active, "reason": self.reason,
                    "position": self._last_sent if self.active else None, "clicks": self.clicks}

    def start(self, now: float, initial_position=None):
        with self._lock:
            self._start(now, initial_position)

    def stop(self, reason="Stopped locally"):
        with self._lock:
            self._stop(reason)
            notifications, self._notifications = self._notifications, []
        self._notify(notifications)

    def step(self, hands, now: float, emergency_stop=False):
        with self._lock:
            self._step(hands, now, emergency_stop)
            notifications, self._notifications = self._notifications, []
        self._notify(notifications)

    def _clock(self, now):
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("Time must be finite")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("Monotonic time moved backwards")
        self._last_now = now

    def _bound(self, x, y):
        margin = self.settings.edge_margin
        return (max(margin, min(self.width - 1 - margin, x)),
                max(margin, min(self.height - 1 - margin, y)))

    def _start(self, now: float, initial_position=None):
        self._clock(now)
        if self.active:
            return
        if initial_position is None:
            initial_position = (self.width / 2, self.height / 2)
        if len(initial_position) != 2 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in initial_position):
            raise ValueError("Initial pointer position must be finite")
        self._previous = self._bound(*initial_position)
        self._last_sent = None
        self._left = self._right = self._click_ready = self._clap_armed = False
        self._active_palm = self._ignored_palm = None
        self._last_click = float("-inf")
        self.clicks = 0
        self._needs_release = True
        self.active = True
        self.reason = "Tracking; show one open hand before pinching"

    def _stop(self, reason="Stopped locally"):
        was_active = self.active
        self.active = False
        self.reason = reason
        self._left = self._right = self._click_ready = self._clap_armed = False
        if self._needs_release:
            self._needs_release = False
            try:
                self.pointer.release_all()
            except Exception:
                self.reason = "Stopped; pointer release failed"
        if was_active:
            self._notifications.append(self.reason)

    def _step(self, hands, now: float, emergency_stop=False):
        self._clock(now)
        if not self.active:
            return
        if emergency_stop:
            self._stop("Emergency stop")
            return
        try:
            if not isinstance(hands, (list, tuple)) or len(hands) > 2:
                raise ValueError("Expected zero, one, or two hands")
            parsed = []
            for hand in hands:
                if len(hand) != 21:
                    raise ValueError("Expected 21 landmarks per hand")
                parsed.append([_point(landmark) for landmark in hand])
        except (ValueError, TypeError, IndexError, AttributeError):
            self._stop("Tracking data invalid")
            return

        if len(parsed) == 2:
            upper, lower = sorted(parsed, key=lambda hand: _palm(hand)[1])
            upper_palm, lower_palm = _palm(upper), _palm(lower)
            gap = (lower_palm[1] - upper_palm[1]) / max(_palm_length(upper), _palm_length(lower))
            if self._ignored_palm is not None and gap >= self.settings.lowered_release:
                lowered = True
            else:
                lowered = gap >= self.settings.lowered_gap and lower_palm[1] >= self.settings.lowered_line
            if lowered:
                self._clap_armed = False
                swapped = self._active_palm is not None and math.dist(lower_palm, self._active_palm) < math.dist(upper_palm, self._active_palm)
                self._ignored_palm = lower_palm
                if swapped:
                    self._active_palm = upper_palm
                    self._idle("Hands changed places; show one raised open hand")
                    return
                self._track(upper, now, " (lowered hand ignored)")
                return
            self._active_palm = self._ignored_palm = None
            a, b = parsed[0][0], parsed[1][0]
            distance = math.hypot(a[0] - b[0], a[1] - b[1])
            if distance >= self.settings.clap_open:
                self._clap_armed = True
            elif distance <= self.settings.clap_close and self._clap_armed:
                self._stop("Visual clap: hand control stopped")
                return
            self._idle("Two hands seen; bring them together to stop")
            return
        if not parsed:
            self._idle("No hand in view; pointer idle")
            return
        palm = _palm(parsed[0])
        if self._ignored_palm is not None and (self._active_palm is None or math.dist(palm, self._ignored_palm) < math.dist(palm, self._active_palm)):
            self._idle("Lowered hand ignored; raise one open hand to point")
            return
        self._ignored_palm = None
        self._track(parsed[0], now, "")

    def _idle(self, reason):
        self._left = self._right = self._click_ready = False
        self.reason = reason

    def _track(self, hand, now, note):
        self._active_palm = _palm(hand)
        middle = _distance(hand, 4, 12)
        ring = _distance(hand, 4, 16)
        left_onset = not self._left and middle < self.settings.pinch_down
        right_onset = not self._right and ring < self.settings.pinch_down
        if left_onset:
            self._left = True
        elif middle > self.settings.pinch_up:
            self._left = False
        if right_onset:
            self._right = True
        elif ring > self.settings.pinch_up:
            self._right = False
        if middle > self.settings.pinch_up and ring > self.settings.pinch_up:
            self._click_ready = True

        zone = self.settings.dead_zone
        normalized = tuple(max(0.0, min(1.0, (value - zone) / (1 - 2 * zone))) for value in hand[8])
        target = self._bound(normalized[0] * (self.width - 1), normalized[1] * (self.height - 1))
        smoothing = self.settings.smoothing
        current = tuple(smoothing * old + (1 - smoothing) * new for old, new in zip(self._previous, target))
        self._previous = self._bound(*current)
        position = tuple(int(round(value)) for value in self._previous)
        try:
            if position != self._last_sent:
                self.pointer.move_to(*position)
                self._last_sent = position
            button = None
            if self._click_ready and now - self._last_click >= self.settings.click_cooldown:
                if left_onset and not self._right:
                    button = "left"
                elif right_onset and not self._left:
                    button = "right"
            if button is not None:
                self.pointer.click(button)
                self.clicks += 1
                self._last_click = now
                self.reason = button.capitalize() + " click" + note
            else:
                self.reason = "Tracking" + note
        except Exception:
            self._stop("Pointer action failed")


def watch_intent(text):
    if not isinstance(text, str):
        return None
    normalized = " ".join(text.strip().lower().split())
    if normalized in {"watch me", "watchme", "jarvis watch me", "jarvis watchme", "start hand tracking"}:
        return "start_hand_control"
    if normalized in {"stop watching", "stop watching me", "stop hand tracking"}:
        return "stop_hand_control"
    return None
