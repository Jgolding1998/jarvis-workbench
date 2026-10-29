import math
import threading
import unittest

from jarvis.hand_control import GestureSettings, HandController, watch_intent
from jarvis.hand_devices import run_hand_session


def hand(index=(0.5, 0.4), middle=(0.8, 0.7), ring=(0.8, 0.2), wrist=0.5):
    points = [(0.5, 0.5)] * 21
    points[0] = (wrist, 0.75)
    points[4], points[8], points[12], points[16] = (0.5, 0.5), index, middle, ring
    return points


class FakePointer:
    def __init__(self):
        self.events = []

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def click(self, button):
        self.events.append(("click", button))

    def release_all(self):
        self.events.append(("release",))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.pointer = FakePointer()
        self.controller = HandController(self.pointer, 1920, 1080)

    def start(self):
        self.controller.start(0, (960, 540))

    def clicks(self):
        return [event for event in self.pointer.events if event[0] == "click"]

    def test_construction_and_inactive_frames_do_nothing(self):
        self.controller.step([hand()], 0)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.pointer.events, [])

    def test_original_index_mapping_and_smoothing(self):
        self.start()
        self.controller.step([hand(index=(1, 1))], 1)
        expected_x = round(0.55 * 960 + 0.45 * (1919 - 8))
        expected_y = round(0.55 * 540 + 0.45 * (1079 - 8))
        self.assertEqual(self.pointer.events[-1], ("move", expected_x, expected_y))

    def test_all_movement_stays_inside_primary_display_safe_margin(self):
        self.start()
        for now in range(1, 100):
            self.controller.step([hand(index=(0, 1) if now % 2 else (1, 0))], now)
        for event in self.pointer.events:
            if event[0] == "move":
                self.assertTrue(8 <= event[1] <= 1911)
                self.assertTrue(8 <= event[2] <= 1071)

    def test_one_left_click_for_held_middle_pinch(self):
        self.start()
        self.controller.step([hand()], 1)
        for now in (2, 3, 4):
            self.controller.step([hand(middle=(0.5, 0.5))], now)
        self.assertEqual(self.clicks(), [("click", "left")])

    def test_ring_pinch_right_click(self):
        self.start()
        self.controller.step([hand()], 1)
        self.controller.step([hand(ring=(0.5, 0.5))], 2)
        self.assertEqual(self.clicks(), [("click", "right")])

    def test_pinch_held_at_start_requires_release_before_click(self):
        self.start()
        self.controller.step([hand(middle=(0.5, 0.5))], 1)
        self.controller.step([hand(middle=(0.5, 0.5))], 2)
        self.assertEqual(self.clicks(), [])
        self.controller.step([hand()], 3)
        self.controller.step([hand(middle=(0.5, 0.5))], 4)
        self.assertEqual(self.clicks(), [("click", "left")])

    def test_shared_cooldown_blocks_left_then_right_burst(self):
        self.start()
        self.controller.step([hand()], 1)
        self.controller.step([hand(middle=(0.5, 0.5))], 2)
        self.controller.step([hand()], 2.1)
        self.controller.step([hand(ring=(0.5, 0.5))], 2.2)
        self.assertEqual(self.clicks(), [("click", "left")])
        self.controller.step([hand()], 2.7)
        self.controller.step([hand(ring=(0.5, 0.5))], 2.8)
        self.assertEqual(self.clicks(), [("click", "left"), ("click", "right")])

    def test_hysteresis_prevents_jitter_reclick(self):
        self.start()
        self.controller.step([hand()], 1)
        self.controller.step([hand(middle=(0.5, 0.5))], 2)
        self.controller.step([hand(middle=(0.56, 0.5))], 3)
        self.controller.step([hand(middle=(0.5, 0.5))], 4)
        self.assertEqual(len(self.clicks()), 1)

    def test_ambiguous_double_pinch_never_double_clicks(self):
        self.start()
        self.controller.step([hand()], 1)
        self.controller.step([hand(middle=(0.5, 0.5), ring=(0.5, 0.5))], 2)
        self.assertEqual(self.clicks(), [])

    def test_visual_clap_stops_before_pointer_actions(self):
        notifications = []
        self.controller.on_stop = notifications.append
        self.start()
        self.controller.step([hand(wrist=0.2), hand(wrist=0.8)], 1)
        self.assertEqual(self.pointer.events, [])
        self.controller.step([hand(wrist=0.45), hand(wrist=0.55)], 2)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.pointer.events, [("release",)])
        self.assertEqual(notifications, ["Visual clap: hand control stopped"])

    def test_two_hands_starting_close_do_not_stop_until_open_then_close(self):
        self.start()
        self.controller.step([hand(wrist=0.45), hand(wrist=0.55)], 1)
        self.assertTrue(self.controller.active)
        self.controller.step([hand(wrist=0.2), hand(wrist=0.8)], 2)
        self.controller.step([hand(wrist=0.45), hand(wrist=0.55)], 3)
        self.assertFalse(self.controller.active)

    def test_no_hand_idles_and_reacquisition_requires_open_pinch(self):
        self.start()
        self.controller.step([hand()], 1)
        before = list(self.pointer.events)
        self.controller.step([], 2)
        self.assertEqual(self.pointer.events, before)
        self.controller.step([hand(middle=(0.5, 0.5))], 3)
        self.assertEqual(self.clicks(), [])

    def test_explicit_stop_releases_once_and_prevents_later_actions(self):
        self.start()
        self.controller.stop()
        self.controller.stop()
        self.controller.step([hand()], 1)
        self.assertEqual(self.pointer.events, [("release",)])

    def test_emergency_stop_is_processed_before_invalid_hands(self):
        self.start()
        self.controller.step(None, 1, emergency_stop=True)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.controller.reason, "Emergency stop")

    def test_bad_landmarks_stop_and_release(self):
        bad_inputs = [None, [hand()] * 3, [[(0, 0)]], [[(float("nan"), 0)] * 21], [[(-0.1, 0)] * 21], [[(True, 0)] * 21]]
        for value in bad_inputs:
            with self.subTest(value=value):
                pointer = FakePointer()
                controller = HandController(pointer, 1920, 1080)
                controller.start(0)
                controller.step(value, 1)
                self.assertFalse(controller.active)
                self.assertEqual(pointer.events, [("release",)])

    def test_pointer_failure_stops_and_releases(self):
        self.start()
        self.pointer.move_to = lambda x, y: (_ for _ in ()).throw(OSError("fixture"))
        self.controller.step([hand()], 1)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.pointer.events, [("release",)])

    def test_stop_callback_runs_outside_controller_lock(self):
        acquired = threading.Event()
        threads = []
        def callback(reason):
            def other_thread():
                with self.controller._lock:
                    acquired.set()
            thread = threading.Thread(target=other_thread)
            threads.append(thread)
            thread.start()
            self.assertTrue(acquired.wait(0.5))
        self.controller.on_stop = callback
        self.start()
        self.controller.stop()
        for thread in threads:
            thread.join()

    def test_stop_serializes_with_inflight_move(self):
        entered, release = threading.Event(), threading.Event()
        original = self.pointer.move_to
        def blocking_move(x, y):
            entered.set()
            release.wait(1)
            original(x, y)
        self.pointer.move_to = blocking_move
        self.start()
        moving = threading.Thread(target=lambda: self.controller.step([hand()], 1))
        moving.start()
        self.assertTrue(entered.wait(0.5))
        stopping = threading.Thread(target=self.controller.stop)
        stopping.start()
        release.set()
        moving.join(1)
        stopping.join(1)
        before = list(self.pointer.events)
        self.controller.step([hand()], 2)
        self.assertEqual(self.pointer.events, before)
        self.assertFalse(self.controller.active)

    def test_invalid_clock_and_settings_rejected(self):
        for now in (True, float("nan"), float("inf"), "1"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.controller.start(now)
        for config in ({"smoothing": 1}, {"pinch_down": 0.1}, {"clap_open": 0.1}, {"edge_margin": 0}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                GestureSettings(**config)

    def test_watch_intent_is_exact_and_does_not_route_guard_stop(self):
        self.assertEqual(watch_intent("  Jarvis   WATCH me "), "start_hand_control")
        self.assertEqual(watch_intent("watchme"), "start_hand_control")
        self.assertEqual(watch_intent("stop hand tracking"), "stop_hand_control")
        for value in ("active", "stop", "stop watching and delete files", None):
            self.assertIsNone(watch_intent(value))


class FakeSource:
    def __init__(self, frames=2):
        self.frames = frames
        self.released = 0
        self.reads = 0

    def read(self):
        self.reads += 1
        self.frames -= 1
        return self.frames >= 0, object()

    def release(self):
        self.released += 1


class FakeTracker:
    def __init__(self, results=()):
        self.results = iter(results)
        self.closed = 0

    def detect(self, frame, now):
        return next(self.results, [])

    def close(self):
        self.closed += 1


class SessionTests(unittest.TestCase):
    def run_session(self, source, tracker, emergency=lambda: False, stop=None):
        pointer = FakePointer()
        controller = HandController(pointer, 1920, 1080)
        controller.start(0)
        ticks = iter(range(1, 100))
        run_hand_session(source, tracker, controller, stop or threading.Event(), emergency, clock=lambda: next(ticks))
        self.assertEqual(source.released, 1)
        self.assertEqual(tracker.closed, 1)
        self.assertFalse(controller.active)
        self.assertEqual(pointer.events[-1], ("release",))
        return controller, pointer

    def test_visual_clap_releases_session_resources(self):
        source = FakeSource()
        tracker = FakeTracker([[hand(wrist=0.2), hand(wrist=0.8)], [hand(wrist=0.45), hand(wrist=0.55)]])
        controller, pointer = self.run_session(source, tracker)
        self.assertIn("Visual clap", controller.reason)
        self.assertEqual(pointer.events, [("release",)])

    def test_escape_stops_before_camera_read(self):
        source = FakeSource()
        self.run_session(source, FakeTracker(), emergency=lambda: True)
        self.assertEqual(source.reads, 0)

    def test_camera_read_failure_stops_and_releases(self):
        self.run_session(FakeSource(frames=0), FakeTracker())

    def test_tracker_failure_always_releases_camera(self):
        tracker = FakeTracker()
        tracker.detect = lambda frame, now: (_ for _ in ()).throw(RuntimeError("fixture"))
        self.run_session(FakeSource(), tracker)

    def test_already_stopped_event_releases_without_read(self):
        stop = threading.Event()
        stop.set()
        source = FakeSource()
        self.run_session(source, FakeTracker(), stop=stop)
        self.assertEqual(source.reads, 0)


if __name__ == "__main__":
    unittest.main()
