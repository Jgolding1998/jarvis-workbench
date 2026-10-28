import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from jarvis.actions import Actions, create_boss_sheet


class ActionsTests(unittest.TestCase):
    def test_demo_never_touches_desktop_or_writes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            actions = Actions(output_dir=directory)
            with patch("jarvis.actions.ctypes", side_effect=AssertionError("desktop access")), patch("jarvis.actions.os.startfile", create=True, side_effect=AssertionError("desktop access")):
                for action in ("lock_absence", "lock_camera_fault", "lock_crowd", "boss_alert"):
                    self.assertTrue(actions.run(action).startswith("Simulated"))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            Actions().run("powershell")

    def test_workbook_is_macro_free_synthetic_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = create_boss_sheet(Path(directory) / "Boss Alert.xlsx")
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(len(archive.namelist()), 5)
                for name in archive.namelist():
                    document = archive.read(name)
                    ElementTree.fromstring(document)
                    self.assertNotIn(b"TargetMode=\"External\"", document)
                    self.assertNotIn(b"<f>", document)
                self.assertIn(b"DEMONSTRATION DATA", archive.read("xl/worksheets/sheet1.xml"))

    def test_real_actions_require_launch_permission(self):
        with patch("jarvis.actions.sys.platform", "win32"):
            for action in ("lock_absence", "boss_alert"):
                with self.subTest(action=action), self.assertRaises(PermissionError):
                    Actions(demo=False).run(action)
