import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.hand_devices import MediaPipeHands, WindowsPointer


class WindowsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.user32 = Mock()
        self.user32.GetSystemMetrics.side_effect = lambda code: {0: 1920, 1: 1080}[code]
        self.user32.GetAsyncKeyState.return_value = 0
        self.user32.SetCursorPos.return_value = 1
        self.user32.GetCursorPos.side_effect = self.position
        self.flags = []
        self.user32.SendInput.side_effect = self.send
        with patch("jarvis.hand_devices.os.name", "nt"), patch("ctypes.WinDLL", return_value=self.user32, create=True):
            self.pointer = WindowsPointer()

    def position(self, point):
        point._obj.x, point._obj.y = 500, 500
        return True

    def send(self, count, events, size):
        self.flags.append([event.mi.dwFlags for event in events])
        return count

    def test_left_right_and_release_use_bounded_named_events(self):
        self.pointer.click("left")
        self.pointer.click("right")
        self.pointer.release_all()
        self.assertEqual(self.flags, [[2, 4], [8, 16], [20]])
        with self.assertRaises(ValueError):
            self.pointer.click("middle")

    def test_partial_click_or_failed_move_is_reported(self):
        self.user32.SendInput.side_effect = lambda count, events, size: 1
        with self.assertRaises(OSError):
            self.pointer.click("left")
        self.user32.SetCursorPos.return_value = 0
        with self.assertRaises(OSError):
            self.pointer.move_to(100, 100)

    def test_global_escape_checked_without_other_keyboard_access(self):
        self.user32.GetAsyncKeyState.return_value = 0x8000
        self.assertTrue(self.pointer.emergency_requested())
        self.user32.GetAsyncKeyState.assert_called_once_with(0x1B)
        self.user32.GetCursorPos.assert_not_called()

    def test_manual_corner_is_emergency_stop(self):
        def corner(point):
            point._obj.x, point._obj.y = 0, 0
            return True
        self.user32.GetCursorPos.side_effect = corner
        self.assertTrue(self.pointer.emergency_requested())

    def test_escape_blocks_move_and_click_before_input_is_sent(self):
        self.user32.GetAsyncKeyState.return_value = 0x8000
        for action in (lambda: self.pointer.move_to(100, 100), lambda: self.pointer.click("left")):
            with self.assertRaises(OSError):
                action()
        self.user32.SetCursorPos.assert_not_called()
        self.user32.SendInput.assert_not_called()
        self.pointer.release_all()
        self.assertEqual(self.flags, [[20]])

    def test_wrong_model_rejected_before_runtime_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "untrusted.task"
            path.write_bytes(b"synthetic invalid model")
            with self.assertRaisesRegex(ValueError, "checksum"):
                MediaPipeHands(path)


if __name__ == "__main__":
    unittest.main()
