import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from jarvis.devices import run_camera
from jarvis.handcontrol import CameraBusy
from jarvis.server import Service


class FakeActions:
    demo, allow_lock, allow_sheet = False, True, False

    def __init__(self):
        self.runs = []

    def run(self, action):
        self.runs.append(action)
        return "Synthetic action: " + action


class Frame:
    shape = (480, 640, 3)

    def __init__(self, people):
        self.people = people


class World:
    """Synthetic camera, clock and stop signal. Each read takes 0.05 s; each wait advances the clock by its timeout."""

    def __init__(self, script, until=120, on_wait=None):
        self.now, self.script, self.until, self.on_wait = 0.0, script, until, on_wait
        self.reads, self.captures, self.stopped = 0, [], False
        self.service = Service(FakeActions(), clock=lambda: self.now)
        self.faults = 0
        original = self.service.camera_fault
        def camera_fault():
            self.faults += 1
            original()
        self.service.camera_fault = camera_fault
        world = self

        class Capture:
            def __init__(self, device):
                self.released = 0
                world.captures.append(self)

            def isOpened(self):
                return True

            def read(self):
                world.now += 0.05
                world.reads += 1
                people = world.script(world.reads, world.now)
                return (False, None) if people is None else (True, Frame(people))

            def release(self):
                self.released += 1

        class Detector:
            def setSVMDetector(self, value):
                pass

            def detectMultiScale(self, frame, **options):
                return [object()] * frame.people, None

        self.cv2 = types.SimpleNamespace(VideoCapture=Capture, HOGDescriptor=Detector,
                                         HOGDescriptor_getDefaultPeopleDetector=lambda: None, resize=lambda frame, size: frame)

    def is_set(self):
        return self.stopped or self.now >= self.until

    def wait(self, timeout):
        self.now += timeout
        if self.on_wait is not None:
            self.on_wait(self)
        return self.is_set()

    def run(self):
        self.service.arm(True, False)
        self.service.command("active")
        with patch.dict(sys.modules, {"cv2": self.cv2}):
            run_camera(self.service, self)


class CameraDeviceTests(unittest.TestCase):
    def test_restart_does_not_depend_on_the_clock_tick_being_different(self):
        def on_wait(world):
            if world.faults and len(world.captures) == 1:
                world.service.command("inactive")
                world.service.command("active")
            if len(world.captures) == 2:
                world.stopped = True
        world = World(lambda read, now: None if len(world.captures) == 1 else 1, until=10, on_wait=on_wait)
        world.service.clock = lambda: 0
        world.run()
        self.assertEqual(len(world.captures),2)
        self.assertEqual([capture.released for capture in world.captures],[1,1])

    def test_alternating_failed_and_empty_reads_still_lock_for_absence(self):
        world = World(lambda read, now: 1 if read == 1 else (None if read % 2 else 0), until=45)
        world.run()
        self.assertIn("lock_absence", world.service.actions.runs)
        self.assertEqual(world.faults, 0)
        self.assertEqual(len(world.captures), 1)

    def test_valid_read_before_the_threshold_resets_the_failure_streak(self):
        world = World(lambda read, now: 1 if read % 12 == 0 else None, until=20)
        world.run()
        self.assertEqual(world.faults, 0)
        self.assertEqual(len(world.captures), 1)
        self.assertEqual(world.service.actions.runs, [])

    def test_persistent_failure_faults_once_releases_camera_and_waits_for_inactive(self):
        def on_wait(world):
            world.service.tick()
            if world.now >= 20 and world.service.presence.active and len(world.captures) == 1:
                world.service.command("inactive")
                world.service.arm(True, False)
                world.service.command("active")
            if len(world.captures) == 2:
                world.stopped = True
        world = World(lambda read, now: None if len(world.captures) == 1 else 1, on_wait=on_wait)
        world.run()
        first, second = world.captures
        self.assertEqual(world.faults, 1)
        self.assertEqual(first.released, 1)
        self.assertEqual(world.reads, 13)
        self.assertEqual(world.service.actions.runs, ["lock_camera_fault"])
        self.assertEqual(second.released, 1)
        self.assertIsNone(world.service.camera.owner)

    def test_fault_latch_holds_through_an_active_session_without_reopening(self):
        world = World(lambda read, now: None, until=60, on_wait=lambda world: world.service.tick())
        world.run()
        self.assertEqual((world.faults, len(world.captures), world.reads), (1, 1, 12))
        self.assertTrue(world.service.presence.active)
        self.assertEqual(world.service.snapshot()["camera"], "fault")

    def test_restart_cannot_slip_in_before_a_read_fault_is_reported(self):
        def script(read, now):
            if read == 12:
                world.service.command("inactive")
                with self.assertRaises(CameraBusy):
                    world.service.command("active")
            return None
        world = World(script, until=5)
        world.run()
        self.assertEqual((world.faults, len(world.captures)), (1, 1))
        self.assertIsNone(world.service.camera.owner)

    def assert_fault_precedes_lease_close(self, world):
        closed, seen = world.service.presence_camera_closed, []
        def presence_camera_closed(lease):
            closed(lease)
            if not seen:
                world.service.command("inactive")
                world.service.command("active")
                seen.append(world.faults)
        world.service.presence_camera_closed = presence_camera_closed
        world.on_wait = lambda world: seen.append(world.service.snapshot()["camera"]) if len(seen) == 1 else None
        world.run()
        self.assertEqual(seen[:2], [1, "waiting"])

    def test_read_fault_is_not_attributed_to_a_session_restarted_after_close(self):
        self.assert_fault_precedes_lease_close(World(lambda read, now: None if read <= 12 else 1, until=3))

    def test_open_failure_is_not_attributed_to_a_session_restarted_after_close(self):
        world = World(lambda read, now: 1, until=3)
        Capture = world.cv2.VideoCapture
        world.cv2.VideoCapture = type("Unopened", (Capture,), {"isOpened": lambda self: len(world.captures) > 1})
        self.assert_fault_precedes_lease_close(world)

    def test_stop_during_failures_returns_promptly_and_releases_everything(self):
        service, stop, captures = Service(FakeActions()), threading.Event(), []
        class Capture:
            def __init__(self, device):
                self.released = 0
                captures.append(self)
            def isOpened(self):
                return True
            def read(self):
                return False, None
            def release(self):
                self.released += 1
        cv2 = types.SimpleNamespace(VideoCapture=Capture, HOGDescriptor=lambda: types.SimpleNamespace(setSVMDetector=lambda value: None),
                                    HOGDescriptor_getDefaultPeopleDetector=lambda: None)
        service.command("active")
        with patch.dict(sys.modules, {"cv2": cv2}):
            thread = threading.Thread(target=run_camera, args=(service, stop), daemon=True)
            thread.start()
            time.sleep(0.4)
            started = time.monotonic()
            stop.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual([capture.released for capture in captures], [1])
        service.command("inactive")
        self.assertIsNone(service.camera.owner)


if __name__ == "__main__":
    unittest.main()
