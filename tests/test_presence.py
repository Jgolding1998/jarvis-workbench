import unittest

from jarvis.presence import Policy, Presence


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.guard = Presence()

    def active(self, lock=False, sheet=False):
        self.guard.arm(lock, sheet)
        self.guard.command("active", 0)
        self.guard.observe(1, 0)

    def test_boot_is_inactive_and_disarmed(self):
        self.assertFalse(self.guard.active)
        self.assertFalse(self.guard.lock_armed)
        self.assertFalse(self.guard.sheet_armed)
        self.assertEqual(self.guard.observe(0, 0), [])

    def test_absence_strictly_more_than_thirty_seconds(self):
        self.active(lock=True)
        self.guard.observe(0, 1)
        for now in range(2, 32):
            self.assertEqual(self.guard.observe(0, now), [])
        self.assertEqual(self.guard.observe(0, 31.001), ["lock_absence"])
        self.assertEqual(self.guard.observe(0, 32), [])

    def test_reentry_resets_absence_timer(self):
        self.active(lock=True)
        for now in range(1, 20):
            self.guard.observe(0, now)
        self.guard.observe(1, 20)
        for now in range(21, 52):
            self.assertEqual(self.guard.observe(0, now), [])
        self.assertEqual(self.guard.observe(0, 51.001), ["lock_absence"])

    def test_observation_without_arming_does_not_lock(self):
        self.active()
        for now in range(1, 100):
            self.assertEqual(self.guard.observe(0, now), [])

    def test_no_presence_established_never_claims_owner_left(self):
        self.guard.arm(True, False)
        self.guard.command("active", 0)
        for now in range(100):
            self.assertEqual(self.guard.observe(0, now), [])
        self.assertFalse(self.guard.established)

    def test_stale_camera_fails_closed_after_five_seconds(self):
        self.active(lock=True)
        self.assertEqual(self.guard.tick(5), [])
        self.assertEqual(self.guard.tick(5.001), ["lock_camera_fault"])
        self.assertIsNone(self.guard.people)
        self.assertEqual(self.guard.tick(6), [])

    def test_camera_fault_is_distinct_from_absence(self):
        self.active(lock=True)
        self.assertEqual(self.guard.fail("device disconnected", 1), [])
        self.assertIsNone(self.guard.people)
        self.assertIsNone(self.guard.absent_since)
        self.assertEqual(self.guard.tick(6), ["lock_camera_fault"])

    def test_start_without_frames_uses_fault_policy(self):
        self.guard.arm(True, False)
        self.guard.command("active", 0)
        self.assertEqual(self.guard.tick(6), ["lock_camera_fault"])

    def test_repeated_active_does_not_extend_fault_deadline(self):
        self.active(lock=True)
        self.guard.command("active", 4)
        self.assertEqual(self.guard.tick(6), ["lock_camera_fault"])

    def test_extra_person_requires_sustained_observation(self):
        self.active(sheet=True)
        self.assertEqual(self.guard.observe(2, 1), [])
        self.assertEqual(self.guard.observe(1, 2), [])
        self.assertEqual(self.guard.observe(2, 3), [])
        self.assertEqual(self.guard.observe(2, 4.9), [])
        self.assertEqual(self.guard.observe(2, 5), ["boss_alert"])
        self.assertEqual(self.guard.observe(2, 6), [])

    def test_extra_person_latch_rearms_only_after_clear_window(self):
        self.active(sheet=True)
        self.guard.observe(2, 1)
        self.guard.observe(2, 3)
        self.guard.observe(1, 4)
        self.guard.observe(1, 8)
        self.guard.observe(2, 9)
        self.assertEqual(self.guard.observe(2, 11), [])
        self.guard.observe(1, 12)
        self.guard.observe(1, 17)
        self.guard.observe(2, 18)
        self.assertEqual(self.guard.observe(2, 20), ["boss_alert"])

    def test_privacy_lock_is_extra_person_action_when_armed(self):
        self.active(lock=True)
        self.guard.observe(2, 1)
        self.assertEqual(self.guard.observe(2, 3), ["lock_crowd"])

    def test_stop_disarms_both_actions(self):
        self.active(lock=True)
        self.guard.command("stop", 1)
        self.assertFalse(self.guard.active)
        self.assertFalse(self.guard.lock_armed)
        self.assertFalse(self.guard.sheet_armed)
        self.assertEqual(self.guard.tick(100), [])

    def test_reactivation_does_not_reuse_an_old_absence(self):
        self.active(lock=True)
        self.guard.observe(0, 1)
        self.guard.command("stop", 2)
        self.guard.command("active", 40)
        self.assertIsNone(self.guard.absent_since)
        self.assertFalse(self.guard.established)
        self.assertFalse(self.guard.lock_armed)

    def test_stop_clears_old_camera_state(self):
        self.active()
        self.guard.command("stop", 1)
        state = self.guard.snapshot(2)
        self.assertIsNone(state["people"])
        self.assertEqual(state["camera"], "waiting")
        self.assertEqual(state["absent_seconds"], 0)

    def test_invalid_people_counts_rejected(self):
        for count in (-1, 21, 1.5, True, "2", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                Presence().observe(count, 0)

    def test_unknown_commands_rejected(self):
        for command in ("open shell", "activate", None, 1):
            with self.subTest(command=command), self.assertRaises(ValueError):
                Presence().command(command, 0)

    def test_invalid_clock_rejected(self):
        for now in (float("nan"), float("inf"), True, "1"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                Presence().tick(now)
        self.guard.tick(2)
        with self.assertRaises(ValueError):
            self.guard.tick(1)

    def test_invalid_policy_rejected(self):
        for seconds in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                Policy(absence_seconds=seconds)

    def test_arming_does_not_accept_truthy_strings(self):
        with self.assertRaises(ValueError):
            self.guard.arm("false", False)
