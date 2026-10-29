"""Persistent local request reservations, separate from a provider's actual bill."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
from functools import wraps
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading
import time

from . import install_tools

RESERVE_MICRO_USD = 10_000
PRICING_CHECKED = "2026-10-28"
VALID_UNTIL = "2026-11-27"
PAID = frozenset({"openai", "anthropic"})
FREE = frozenset({"nvidia", "ollama", "none", "offline"})
MAX_DB_BYTES = 1024 * 1024
MAX_MONTHS = 1200
_CREATE = "CREATE TABLE allowance (month TEXT PRIMARY KEY NOT NULL, reserved_micro_usd INTEGER NOT NULL CHECK(reserved_micro_usd >= 0), paid_requests INTEGER NOT NULL CHECK(paid_requests >= 0)) WITHOUT ROWID"
_ERROR = "The local paid allowance cannot approve this request. Check the limit and pricing date. Preserve existing allowance history when repairing local data."
# Closing a raw file descriptor can drop SQLite's POSIX locks in another thread.
_PROCESS_LOCK = threading.RLock()


def _serialized(method):
    @wraps(method)
    def call(*args, **kwargs):
        with _PROCESS_LOCK:
            return method(*args, **kwargs)
    return call


class BudgetError(ValueError):
    def __init__(self):
        super().__init__(_ERROR)


class BudgetLedger:
    def __init__(self, root, now=None):
        self.root = Path(root).absolute()
        self._now = now if callable(now) else (lambda: now) if now is not None else lambda: datetime.now(timezone.utc)

    def _stamp(self):
        value = self._now()
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise BudgetError()
            return value.astimezone(timezone.utc)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BudgetError()
        return datetime.fromtimestamp(value, timezone.utc)

    @staticmethod
    def _limit(limit_usd):
        if type(limit_usd) is not int or not 0 <= limit_usd <= 100:
            raise BudgetError()
        return limit_usd * 1_000_000

    def _path(self, *, create=False, check_header=True):
        directory = install_tools.local_path(self.root, "runtime")
        if create:
            directory.mkdir(exist_ok=True)
            directory = install_tools.local_path(self.root, "runtime")
        if directory.exists() and not directory.is_dir():
            raise BudgetError()
        path = install_tools.local_path(self.root, "runtime", "budget.sqlite3")
        for suffix in ("", "-journal", "-wal", "-shm"):
            for attempt in range(3):
                try:
                    candidate = install_tools.local_path(self.root, "runtime", "budget.sqlite3" + suffix)
                    break
                except install_tools.SetupError:
                    if suffix != "-journal" or attempt == 2:
                        raise
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if not suffix and info.st_nlink == 2:
                deadline = time.monotonic() + 1
                while info.st_nlink == 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                    info = candidate.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > MAX_DB_BYTES or suffix in {"-wal", "-shm"}):
                raise BudgetError()
            if not suffix and check_header:
                with candidate.open("rb") as stream:
                    header = stream.read(20)
                if header[:16] != b"SQLite format 3\0" or header[18:20] != b"\x01\x01":
                    raise BudgetError()
        return path

    @staticmethod
    def _publish(temporary, target, *, windows=None):
        windows = os.name == "nt" if windows is None else windows
        if windows:
            os.rename(temporary, target)
        else:
            os.link(temporary, target, follow_symlinks=False)
            temporary.unlink()

    def _initialize(self, target):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".budget-init-", suffix=".sqlite3", delete=False) as stream:
                temporary = Path(stream.name)
            install_tools.local_path(self.root, "runtime", temporary.name)
            info = temporary.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BudgetError()
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = install_tools.local_path(self.root, "runtime", temporary.name + suffix)
                if sidecar.exists():
                    raise BudgetError()
            with closing(self._connection(temporary, write=True)) as db:
                db.execute("BEGIN IMMEDIATE")
                self._rows(db, initialize=True)
                db.execute("COMMIT")
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            install_tools.local_path(self.root, "runtime", temporary.name)
            if not temporary.is_file() or temporary.stat().st_nlink != 1:
                raise BudgetError()
            self._path()
            try:
                self._publish(temporary, target)
            except OSError:
                if not target.exists():
                    raise
                temporary.unlink(missing_ok=True)
            self._path()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _connection(path, *, write):
        mode = "rw" if write else "ro"
        db = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise BudgetError()
            db.execute("PRAGMA synchronous=FULL" if write else "PRAGMA query_only=ON")
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def _rows(db, *, initialize=False):
        schema = db.execute("SELECT type,name,sql FROM sqlite_master LIMIT 2").fetchall()
        if not schema and initialize:
            db.execute(_CREATE)
            schema = [("table", "allowance", _CREATE)]
        if len(schema) != 1 or schema[0][:2] != ("table", "allowance") or schema[0][2] != _CREATE:
            raise BudgetError()
        if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise BudgetError()
        rows = db.execute("SELECT month,reserved_micro_usd,paid_requests FROM allowance LIMIT ?", (MAX_MONTHS + 1,)).fetchall()
        if len(rows) > MAX_MONTHS:
            raise BudgetError()
        for month, reserved, count in rows:
            if (not isinstance(month, str) or re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", month) is None
                    or month.startswith("0000") or type(reserved) is not int or type(count) is not int
                    or not 0 <= reserved <= 100_000_000 or not 0 <= count <= 10_000
                    or reserved != count * RESERVE_MICRO_USD):
                raise BudgetError()
        return {month: (reserved, count) for month, reserved, count in rows}

    @staticmethod
    def _snapshot(month, limit_usd, reserved=0, count=0):
        return {"month": month, "limit_usd": limit_usd, "reserved_usd": reserved / 1_000_000,
                "remaining_usd": max(0, limit_usd * 1_000_000 - reserved) / 1_000_000,
                "paid_requests": count, "reserve_per_request_usd": RESERVE_MICRO_USD / 1_000_000,
                "kind": "local_allowance", "provider_limit_required": True,
                "pricing_checked": PRICING_CHECKED, "valid_until": VALID_UNTIL}

    @_serialized
    def snapshot(self, limit_usd):
        try:
            self._limit(limit_usd)
            stamp = self._stamp()
            month = f"{stamp.year:04d}-{stamp.month:02d}"
            path = self._path()
            if not path.exists():
                return self._snapshot(month, limit_usd)
            with closing(self._connection(path, write=False)) as db:
                db.execute("BEGIN")
                rows = self._rows(db)
                reserved, count = rows.get(month, (0, 0))
                self._path(check_header=False)
                return self._snapshot(month, limit_usd, reserved, count)
        except Exception:
            raise BudgetError() from None

    @_serialized
    def reserve(self, provider, limit_usd):
        try:
            limit = self._limit(limit_usd)
            if type(provider) is not str or provider not in PAID | FREE:
                raise BudgetError()
            if provider in FREE:
                return self.snapshot(limit_usd)
            stamp = self._stamp()
            if not limit or stamp.date() > date.fromisoformat(VALID_UNTIL):
                raise BudgetError()
            month = f"{stamp.year:04d}-{stamp.month:02d}"
            path = self._path(create=True)
            if not path.exists():
                self._initialize(path)
            self._path()
            with closing(self._connection(path, write=True)) as db:
                db.execute("BEGIN IMMEDIATE")
                self._path(check_header=False)
                rows = self._rows(db)
                reserved, count = rows.get(month, (0, 0))
                if reserved + RESERVE_MICRO_USD > limit:
                    raise BudgetError()
                db.execute("INSERT INTO allowance(month,reserved_micro_usd,paid_requests) VALUES(?,?,?) ON CONFLICT(month) DO UPDATE SET reserved_micro_usd=excluded.reserved_micro_usd,paid_requests=excluded.paid_requests",
                           (month, reserved + RESERVE_MICRO_USD, count + 1))
                self._path(check_header=False)
                db.execute("COMMIT")
                self._path(check_header=False)
                return self._snapshot(month, limit_usd, reserved + RESERVE_MICRO_USD, count + 1)
        except Exception:
            raise BudgetError() from None
