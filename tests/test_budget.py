from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from jarvis import budget


NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def process_reservations(root, attempts, queue):
    ledger = budget.BudgetLedger(root, now=NOW)
    accepted = 0
    for _ in range(attempts):
        try:
            ledger.reserve("openai", 1)
            accepted += 1
        except budget.BudgetError:
            pass
    queue.put(accepted)


def first_process_reservations(root, barrier, queue):
    ledger = budget.BudgetLedger(root, now=NOW)
    barrier.wait(timeout=20)
    accepted = 0
    failures = []
    for _ in range(10):
        try:
            ledger.reserve("openai",1)
            accepted += 1
        except budget.BudgetError as error:
            cause = error.__context__
            trace = cause.__traceback__ if cause is not None else error.__traceback__
            lines = []
            while trace is not None:
                lines.append((Path(trace.tb_frame.f_code.co_filename).name, trace.tb_lineno))
                trace = trace.tb_next
            failures.append((type(cause).__name__, getattr(cause, "sqlite_errorcode", None), lines))
    queue.put((accepted, failures))


def crash_before_publication(root):
    with patch.object(budget.BudgetLedger,"_publish",side_effect=lambda *args:os._exit(7)):
        budget.BudgetLedger(root,now=NOW).reserve("openai",1)


def try_other_process_transaction(path, queue):
    try:
        with closing(sqlite3.connect(Path(path).as_uri()+"?mode=rw",uri=True,timeout=0.2,isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("ROLLBACK")
        queue.put(0)
    except sqlite3.Error as error:
        queue.put(getattr(error,"sqlite_errorcode",-1))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = NOW
        self.ledger = budget.BudgetLedger(self.root, now=lambda:self.now)

    def tearDown(self):
        self.temporary.cleanup()

    @property
    def path(self):
        return self.root / "runtime/budget.sqlite3"

    def test_missing_snapshot_and_free_calls_create_no_files(self):
        state = self.ledger.snapshot(4)
        self.assertEqual(state, {"month":"2026-10", "limit_usd":4,"reserved_usd":0.0,"remaining_usd":4.0,
                                 "paid_requests":0,"reserve_per_request_usd":0.01,"kind":"local_allowance",
                                 "provider_limit_required":True,"pricing_checked":"2026-10-28","valid_until":"2026-11-27"})
        for provider in ("none","offline","ollama","nvidia"):
            self.assertEqual(self.ledger.reserve(provider,4), state)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_zero_budget_blocks_paid_calls_without_creating_a_ledger(self):
        for provider in ("openai","anthropic"):
            with self.assertRaises(budget.BudgetError):
                self.ledger.reserve(provider,0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reservations_persist_and_free_calls_do_not_consume_them(self):
        self.assertEqual(self.ledger.reserve("openai",1)["reserved_usd"],0.01)
        restored = budget.BudgetLedger(self.root,now=NOW)
        state = restored.reserve("anthropic",1)
        self.assertEqual(state["paid_requests"],2)
        self.assertEqual(state["remaining_usd"],0.98)
        for provider in ("nvidia","ollama","none"):
            self.assertEqual(restored.reserve(provider,1),state)
        self.assertEqual(self.ledger.snapshot(0)["remaining_usd"],0)
        self.assertEqual(self.ledger.snapshot(0)["reserved_usd"],0.02)

    def test_failed_provider_attempt_keeps_its_reservation(self):
        self.ledger.reserve("openai",1)
        try:
            raise TimeoutError("Synthetic provider failure, no network")
        except TimeoutError:
            pass
        self.assertEqual(budget.BudgetLedger(self.root,now=NOW).snapshot(1)["paid_requests"],1)

    def test_schema_and_rows_contain_only_month_and_counters(self):
        self.ledger.reserve("anthropic",1)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(allowance)")],
                             ["month","reserved_micro_usd","paid_requests"])
            self.assertEqual(db.execute("SELECT * FROM allowance").fetchall(),[("2026-10",10000,1)])
        before = self.path.read_bytes()
        self.ledger.snapshot(1)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual([path.name for path in self.path.parent.iterdir()],["budget.sqlite3"])

    def test_month_rollover_uses_utc_and_preserves_previous_records(self):
        self.ledger.reserve("openai",1)
        self.now = datetime(2026,11,1,0,30,tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(self.ledger.snapshot(1)["month"],"2026-10")
        self.assertEqual(self.ledger.snapshot(1)["paid_requests"],1)
        self.now = datetime(2026,11,1,tzinfo=timezone.utc)
        self.assertEqual(self.ledger.snapshot(1)["paid_requests"],0)
        self.assertEqual(self.ledger.reserve("openai",1)["paid_requests"],1)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM allowance").fetchone()[0],2)

    def test_pricing_expiry_is_inclusive_and_does_not_block_free_modes(self):
        self.now = datetime(2026,11,27,23,59,59,tzinfo=timezone.utc)
        self.ledger.reserve("openai",1)
        self.now = datetime(2026,11,28,tzinfo=timezone.utc)
        for provider in ("openai","anthropic"):
            with self.assertRaises(budget.BudgetError):
                self.ledger.reserve(provider,1)
        self.assertEqual(self.ledger.reserve("nvidia",1)["paid_requests"],1)

    def test_limits_provider_and_clock_errors_have_a_fixed_message(self):
        messages = []
        for limit in (True,-1,101,0.5,"1",None,float("nan")):
            with self.assertRaises(budget.BudgetError) as failure:
                self.ledger.snapshot(limit)
            messages.append(str(failure.exception))
        for provider in (None,[],"private-provider-canary"):
            with self.assertRaises(budget.BudgetError) as failure:
                self.ledger.reserve(provider,1)
            messages.append(str(failure.exception))
        with patch.object(self.ledger,"_now",side_effect=OSError("private-clock-canary")):
            with self.assertRaises(budget.BudgetError) as failure:
                self.ledger.snapshot(1)
            messages.append(str(failure.exception))
        self.assertEqual(len(set(messages)),1)
        self.assertNotIn("canary",json.dumps(messages))

    def test_thread_reservations_cannot_exceed_the_allowance(self):
        self.ledger.reserve("openai",1)
        def attempt(_):
            try:
                budget.BudgetLedger(self.root,now=NOW).reserve("anthropic",1)
                return 1
            except budget.BudgetError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = sum(pool.map(attempt,range(160)))
        state = self.ledger.snapshot(1)
        self.assertEqual(accepted,99)
        self.assertEqual((state["paid_requests"],state["reserved_usd"],state["remaining_usd"]),(100,1.0,0.0))
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)

    def test_path_validation_keeps_the_transaction_locked_against_another_process(self):
        self.ledger.reserve("openai",1)
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        original = self.ledger._rows
        def while_transaction_active(db,**kwargs):
            worker = context.Process(target=try_other_process_transaction,args=(str(self.path),queue))
            try:
                worker.start()
                worker.join(timeout=10)
                self.assertEqual(worker.exitcode,0)
                self.assertEqual(queue.get(timeout=3) & 0xFF,sqlite3.SQLITE_BUSY)
            finally:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
            return original(db,**kwargs)
        try:
            with patch.object(self.ledger,"_rows",side_effect=while_transaction_active):
                self.assertEqual(self.ledger.reserve("openai",1)["paid_requests"],2)
        finally:
            queue.close()

    def test_another_manager_cannot_read_headers_while_a_writer_is_connected(self):
        self.ledger.reserve("openai",1)
        second = budget.BudgetLedger(self.root,now=NOW)
        writer_entered, release_writer = threading.Event(), threading.Event()
        reader_entered, early_header = threading.Event(), threading.Event()
        original_rows, original_open = self.ledger._rows, Path.open
        def pause_writer(db,**kwargs):
            writer_entered.set()
            if not release_writer.wait(timeout=5):
                raise RuntimeError("Test writer was not released")
            return original_rows(db,**kwargs)
        def track_open(path,*args,**kwargs):
            if path == self.path and writer_entered.is_set() and not release_writer.is_set():
                early_header.set()
            return original_open(path,*args,**kwargs)
        def read_second():
            reader_entered.set()
            return second.snapshot(1)
        with patch.object(self.ledger,"_rows",side_effect=pause_writer), patch.object(Path,"open",track_open), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(self.ledger.reserve,"openai",1)
            try:
                self.assertTrue(writer_entered.wait(timeout=3))
                reader = pool.submit(read_second)
                self.assertTrue(reader_entered.wait(timeout=3))
                self.assertFalse(early_header.wait(timeout=0.15))
                self.assertFalse(reader.done())
            finally:
                release_writer.set()
            self.assertEqual(writer.result(timeout=5)["paid_requests"],2)
            self.assertEqual(reader.result(timeout=5)["paid_requests"],2)

    def test_process_reservations_share_the_same_allowance(self):
        self.ledger.reserve("openai",1)
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        workers = [context.Process(target=process_reservations,args=(str(self.root),45,queue)) for _ in range(4)]
        try:
            for process in workers:
                process.start()
            for process in workers:
                process.join(timeout=30)
                self.assertEqual(process.exitcode,0)
            self.assertEqual(sum(queue.get(timeout=3) for _ in workers),99)
            self.assertEqual(self.ledger.snapshot(1)["paid_requests"],100)
        finally:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            queue.close()

    def test_six_simultaneous_first_processes_all_reserve_under_the_limit(self):
        for round_number in range(10):
            with self.subTest(round=round_number), tempfile.TemporaryDirectory() as directory:
                self._first_process_batch(Path(directory))

    def _first_process_batch(self, root):
        context = multiprocessing.get_context("spawn")
        queue, barrier = context.Queue(), context.Barrier(6)
        workers = [context.Process(target=first_process_reservations,args=(str(root),barrier,queue)) for _ in range(6)]
        try:
            for process in workers:
                process.start()
            for process in workers:
                process.join(timeout=30)
                self.assertEqual(process.exitcode,0)
            self.assertEqual([queue.get(timeout=3) for _ in workers],[(10, [])]*6)
            self.assertEqual(budget.BudgetLedger(root,now=NOW).snapshot(1)["paid_requests"],60)
            self.assertEqual([path.name for path in (root/"runtime").iterdir()],["budget.sqlite3"])
        finally:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            queue.close()

    def test_crash_before_publication_leaves_no_target_and_next_use_can_initialize(self):
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=crash_before_publication,args=(str(self.root),))
        worker.start()
        worker.join(timeout=30)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        self.assertEqual(worker.exitcode,7)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.ledger.snapshot(1)["paid_requests"],0)
        self.assertEqual(self.ledger.reserve("openai",1)["paid_requests"],1)

    def test_non_windows_publication_does_not_overwrite_or_delete_a_winner(self):
        source, target = self.root/"complete.tmp", self.root/"published.db"
        source.write_bytes(b"complete-initial-ledger")
        budget.BudgetLedger._publish(source,target,windows=False)
        self.assertFalse(source.exists())
        self.assertEqual(target.stat().st_nlink,1)
        source.write_bytes(b"losing-initializer")
        with self.assertRaises(FileExistsError):
            budget.BudgetLedger._publish(source,target,windows=False)
        self.assertEqual(target.read_bytes(),b"complete-initial-ledger")
        target.unlink()
        with patch.object(Path,"unlink",side_effect=OSError("private-cleanup-canary")):
            with self.assertRaises(OSError):
                budget.BudgetLedger._publish(source,target,windows=False)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(),b"losing-initializer")

    def test_windows_sharing_error_can_use_a_complete_concurrent_winner(self):
        def another_creator_wins(temporary,target):
            self.assertFalse(target.exists())
            target.write_bytes(temporary.read_bytes())
            raise PermissionError("Synthetic Windows destination sharing error")
        with patch.object(self.ledger,"_publish",side_effect=another_creator_wins):
            self.assertEqual(self.ledger.reserve("openai",1)["paid_requests"],1)
        self.assertEqual([path.name for path in self.path.parent.iterdir()],["budget.sqlite3"])

    def test_disappearing_journal_path_is_rechecked_but_unsafe_paths_still_fail(self):
        original = budget.install_tools.local_path
        checks = []
        def transient(root,*parts):
            if parts[-1] == "budget.sqlite3-journal":
                checks.append(True)
                if len(checks) == 1:
                    raise budget.install_tools.SetupError("Synthetic disappearing journal")
            return original(root,*parts)
        with patch.object(budget.install_tools,"local_path",side_effect=transient):
            self.assertEqual(self.ledger.reserve("openai",1)["paid_requests"],1)
        checks.clear()
        def persistent(root,*parts):
            if parts[-1] == "budget.sqlite3-journal":
                checks.append(True)
                raise budget.install_tools.SetupError("Synthetic unsafe journal path")
            return original(root,*parts)
        with patch.object(budget.install_tools,"local_path",side_effect=persistent):
            with self.assertRaises(budget.BudgetError):
                self.ledger.reserve("openai",1)
        self.assertEqual(len(checks),3)
        self.assertEqual(self.ledger.snapshot(1)["paid_requests"],1)

    def test_existing_corrupt_empty_or_foreign_database_is_never_reset(self):
        self.path.parent.mkdir()
        for content in (b"",b"private-corrupt-canary"):
            self.path.write_bytes(content)
            for action in (lambda:self.ledger.snapshot(1),lambda:self.ledger.reserve("openai",1)):
                with self.assertRaises(budget.BudgetError) as failure:
                    action()
                self.assertNotIn("canary",str(failure.exception))
                self.assertEqual(self.path.read_bytes(),content)
        self.path.unlink()
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("CREATE TABLE unrelated (private_value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)
        self.assertEqual(self.path.read_bytes(),before)

    def test_invalid_counter_relationship_fails_closed(self):
        self.ledger.reserve("openai",1)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("UPDATE allowance SET reserved_micro_usd=1")
        for action in (lambda:self.ledger.snapshot(1),lambda:self.ledger.reserve("anthropic",1)):
            with self.assertRaises(budget.BudgetError):
                action()

    def test_hardlinked_database_and_journal_are_rejected(self):
        outside = self.root / "outside-canary"
        outside.write_bytes(b"private-canary")
        self.path.parent.mkdir()
        self.path.hardlink_to(outside)
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)
        self.path.unlink()
        journal = Path(str(self.path)+"-journal")
        journal.hardlink_to(outside)
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)
        self.assertEqual(outside.read_bytes(),b"private-canary")

    def test_wal_shm_and_wal_mode_are_rejected_without_replacing_files(self):
        self.ledger.reserve("openai",1)
        for suffix in ("-wal","-shm"):
            sidecar = Path(str(self.path)+suffix)
            sidecar.write_bytes(b"private-canary")
            with self.assertRaises(budget.BudgetError):
                self.ledger.snapshot(1)
            self.assertEqual(sidecar.read_bytes(),b"private-canary")
            sidecar.unlink()
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)

    def test_linked_runtime_and_reparse_paths_are_rejected(self):
        with patch.object(budget.install_tools,"local_path",side_effect=ValueError("private-path-canary")):
            with self.assertRaises(budget.BudgetError) as failure:
                self.ledger.snapshot(1)
            self.assertNotIn("canary",str(failure.exception))
        target = self.root / "other"
        target.mkdir()
        try:
            (self.root/"runtime").symlink_to(target,target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires OS permission")
        with self.assertRaises(budget.BudgetError):
            self.ledger.reserve("openai",1)

    def test_extra_schema_objects_cannot_reset_allowance_counters(self):
        self.ledger.reserve("openai",1)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("CREATE TRIGGER sqliteXreset AFTER UPDATE ON allowance BEGIN UPDATE allowance SET reserved_micro_usd=10000,paid_requests=1; END")
        before = self.path.read_bytes()
        for action in (lambda:self.ledger.snapshot(1),lambda:self.ledger.reserve("openai",1)):
            with self.assertRaises(budget.BudgetError):
                action()
        self.assertEqual(self.path.read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
