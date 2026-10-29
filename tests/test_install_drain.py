"""Owned setup jobs must finish termination before another setup step starts."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jarvis import install_tools as setup


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
    _fields_ += [(name, wintypes.DWORD) for name in (
        "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]


class InstallDrainTests(unittest.TestCase):
    def member_job(self, ids=(1, 2), inside=(True, True), handles=(101, 102), assigned=None, query_ok=True):
        class Members(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD), ("ids", ctypes.c_size_t * 4096)]
        job = object.__new__(setup.WindowsJob)
        job.handle = 123
        def query(handle, kind, output, size, returned):
            self.assertEqual((handle, kind, size, returned), (123, 3, ctypes.sizeof(Members), None))
            info = ctypes.cast(output, ctypes.POINTER(Members)).contents
            info.assigned = len(ids) if assigned is None else assigned
            info.listed = len(ids)
            for index, pid in enumerate(ids):
                info.ids[index] = pid
            return query_ok
        membership = iter(inside)
        def contains(handle, parent, output):
            self.assertEqual(parent, 123)
            ctypes.cast(output, ctypes.POINTER(wintypes.BOOL)).contents.value = next(membership)
            return True
        job._k32 = SimpleNamespace(QueryInformationJobObject=query, OpenProcess=Mock(side_effect=handles),
                                   IsProcessInJob=contains, CloseHandle=Mock())
        return job

    def test_member_snapshot_excludes_reused_pid_and_closes_its_handle(self):
        job = self.member_job(inside=(True, False))
        self.assertEqual(job.open_members(), [101])
        job._k32.CloseHandle.assert_called_once_with(102)

    def test_member_snapshot_closes_partial_handles_on_access_failure(self):
        job = self.member_job(handles=(101, 0))
        with patch.object(ctypes, "get_last_error", return_value=5, create=True):
            self.assertIsNone(job.open_members())
        job._k32.CloseHandle.assert_called_once_with(101)

    def test_member_that_already_exited_is_not_a_setup_failure(self):
        job = self.member_job(handles=(101, 0))
        with patch.object(ctypes, "get_last_error", return_value=87, create=True):
            self.assertEqual(job.open_members(), [101])

    def test_incomplete_or_failed_member_query_cannot_report_success(self):
        for job in (self.member_job(assigned=3), self.member_job(query_ok=False)):
            self.assertIsNone(job.open_members())
            job._k32.OpenProcess.assert_not_called()

    def job(self, counts, success=True):
        job = object.__new__(setup.WindowsJob)
        job.handle = 123
        counts = iter(counts)
        def query(handle, kind, output, size, returned):
            self.assertEqual((handle, kind, size, returned), (123, 1, ctypes.sizeof(Accounting), None))
            ctypes.cast(output, ctypes.POINTER(Accounting)).contents.ActiveProcesses = next(counts)
            return success
        job._k32 = SimpleNamespace(QueryInformationJobObject=Mock(side_effect=query),
                                   WaitForSingleObject=Mock(return_value=0), CloseHandle=Mock())
        return job

    def test_waits_for_all_owned_members_after_direct_parent_exits(self):
        job = self.job([2, 1, 0])
        with patch.object(setup.time, "sleep") as sleep:
            self.assertTrue(job.wait_empty([456]))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(job._k32.QueryInformationJobObject.call_count, 3)
        job._k32.WaitForSingleObject.assert_called_once()
        job._k32.CloseHandle.assert_called_once_with(456)

    def test_query_failure_is_controlled_and_contains_no_os_details(self):
        job = self.job([1], success=False)
        self.assertFalse(job.wait_empty([456]))
        job._k32.CloseHandle.assert_called_once_with(456)

    def test_stalled_termination_is_bounded_and_not_success(self):
        job = self.job([1, 1])
        with patch.object(setup.time, "monotonic", side_effect=[0, 0, setup.STOP_SECONDS + 1]), patch.object(setup.time, "sleep"):
            self.assertFalse(job.wait_empty([]))

    def test_zero_active_is_insufficient_until_retained_process_handle_signals(self):
        job = self.job([0])
        job._k32.WaitForSingleObject.return_value = 258  # WAIT_TIMEOUT
        self.assertFalse(job.wait_empty([456]))
        job._k32.QueryInformationJobObject.assert_not_called()
        job._k32.CloseHandle.assert_called_once_with(456)

    def test_unavailable_member_snapshot_cannot_report_success(self):
        self.assertFalse(self.job([]).wait_empty(None))

    def test_end_reaps_direct_process_before_waiting_for_job_to_empty(self):
        events = []
        job = SimpleNamespace(open_members=lambda: events.append("snapshot") or [456],
                              terminate=lambda: events.append("terminate"),
                              wait_empty=lambda members: events.append(("empty", members)) or True)
        process = SimpleNamespace(stdin=None, wait=lambda **kwargs: events.append("reap"))
        self.assertTrue(setup._end(process, job))
        self.assertEqual(events, ["snapshot", "terminate", "reap", ("empty", [456])])

    def test_unsuccessful_drain_refuses_success_timeout_and_cancel_and_always_closes(self):
        for outcome in (None, "timeout", "cancelled"):
            job = Mock()
            process = Mock(returncode=0)
            cancel = Mock()
            cancel.is_set.side_effect = [False, False, outcome == "cancelled"]
            with self.subTest(outcome=outcome), patch.object(setup.os, "name", "nt"), \
                 patch.object(setup.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                 patch.object(setup, "WindowsJob", return_value=job), \
                 patch.object(setup.subprocess, "Popen", return_value=process), \
                 patch.object(setup, "_exited", side_effect=[outcome is None, True]), \
                 patch.object(setup, "_end", return_value=False):
                with self.assertRaisesRegex(setup.SetupError, "could not confirm"):
                    setup.contained(["fixture"], ".", {}, timeout=0 if outcome == "timeout" else 10,
                                    cancel=cancel if outcome == "cancelled" else None)
            job.close.assert_called_once()

    def test_snapshot_exception_still_terminates_owned_job(self):
        job = Mock()
        job.open_members.side_effect = RuntimeError("fixture")
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            setup._end(Mock(), job)
        job.terminate.assert_called_once()

    def test_reap_exception_closes_retained_handles(self):
        job = Mock()
        job.open_members.return_value = [456]
        process = Mock(stdin=None)
        process.wait.side_effect = RuntimeError("fixture")
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            setup._end(process, job)
        job._k32.CloseHandle.assert_called_once_with(456)


if __name__ == "__main__":
    unittest.main()
