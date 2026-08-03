#!/usr/bin/env python3
"""One SQLite index per project, and the pragmas that let readers answer.

**One index per project, each in its own directory**, which is what makes a
lock per store path *already* per project (locked decision 12). The finer
per-write barrier solves a problem that does not exist here.

**The directory name is a generated id, never derived from the path.** A path
is not an identity: the same tree gets moved, copied and restored, and CP-6
decides which of those happened from `exists()` on both paths. An id derived
from the path would answer that question before CP-6 got to ask it -- a move
would silently be a new project, and a copy would silently be the same one.
The registry maps `id -> path`, never `path -> id`.

**WAL and `busy_timeout` are set at open, and read back.** They are not a
detail of performance. Search, the view and MCP have to answer while the
service writes, and that is WAL doing it, not the lock. The journal mode is
stored as whatever the pragma *returned* -- on a network filesystem SQLite can
refuse WAL and hand back something else, and a store that recorded the mode it
asked for would report a guarantee it does not have.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
import threading
from pathlib import Path

from .lock import Unguarded, holds

SCHEMA_VERSION = 1

# Milliseconds a reader or writer waits on a locked SQLite page before giving
# up. This is the *page* lock, not the write barrier -- WAL keeps readers off
# it almost always, and the timeout covers the checkpoint moments when it does
# not.
#
# It is passed to `connect`, not set with a pragma, because the two do the same
# thing and one of them can drift from the other. Measured 2026-08-03: this
# value is also `sqlite3.connect`'s own default (`timeout=5.0`), so no edit to
# this file can move it -- which is why `tests/mutate_cp0.py` carries no
# mutation for it and says so. Stated here rather than left implicit anyway: a
# driver default is not a decision until someone writes it down.
BUSY_TIMEOUT_MS = 5000


def data_home() -> Path:
    """Where every project's index lives. `MORPHO_HOMEGRAPH_HOME` overrides."""
    override = os.environ.get("MORPHO_HOMEGRAPH_HOME")
    root = Path(override) if override else Path("~/.local/share/morpho-homegraph")
    return root.expanduser()


def db_path(project_id: str) -> Path:
    return data_home() / project_id / "index.db"


def new_project() -> tuple[str, Path]:
    """Allocate a fresh id and its directory. Returns (id, db path).

    Writes nothing: the caller takes the write barrier and then calls
    `initialise`. Splitting it that way is what keeps "the guard is taken
    before the store is opened" true for every writer including this one.

    Calling this twice for the same path yields two ids and two indexes. That
    is not a bug to fix later: deciding whether a second registration of the
    same path is a copy, a move or a mistake is CP-6's job, and it needs both
    to exist to decide.
    """
    project_id = secrets.token_hex(8)
    path = db_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return project_id, path


def initialise(store: "Store", project_id: str, project_path: str | Path) -> None:
    """Write the two facts that make an index a project. Caller holds the guard."""
    store.set_meta("project_id", project_id)
    store.set_meta("project_path",
                   str(Path(project_path).expanduser().resolve()))


def projects() -> list[tuple[str, str]]:
    """Every registered (id, path), read from each index's own meta table.

    The direction is the point: each directory knows which path it is for.
    There is no path-keyed table anywhere, so nothing can answer "which id is
    this path" without walking every project -- which is what stops a future
    caller from treating a path as an identity by accident.
    """
    # ponytail: linear walk over project directories. Fine at tens of
    # projects; add an index if this is ever hot.
    found = []
    root = data_home()
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        db = entry / "index.db"
        if not db.is_file():
            continue
        try:
            with Store(db, read_only=True) as store:
                path = store.get_meta("project_path")
        except (sqlite3.Error, OSError):
            # OSError as well as sqlite3.Error: `is_file()` above and the open
            # below are two moments, and a project directory can be removed
            # between them. A listing that dies on that races with anyone
            # tidying up.
            continue
        if path:
            found.append((entry.name, path))
    return found


class Store:
    """One project's index.

    `read_only=True` is what a reader needs to keep the promise it makes.
    Opening a store normally is not a read: it sets `journal_mode`, runs
    `migrate()` and commits, and `sqlite3.connect` creates the file if it is
    not there -- so a command that "only ever issues SELECTs" would still
    migrate an out-of-date store, and would create one from a typo.
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = str(path)
        self.read_only = read_only
        if read_only and not os.path.exists(self.path):
            raise FileNotFoundError("no store at %s" % self.path)
        # Refused at open, not at first write. Opening writable already writes
        # -- it sets the journal mode and migrates -- so a check further in
        # would be a check after the fact.
        if not read_only and not holds(self.path):
            raise Unguarded(self.path)
        # `check_same_thread=False` and `writing()` are one decision, not two.
        # The service scans on one thread and answers on another, so Python's
        # own thread guard has to come off -- and the moment it does, the
        # serialisation it was providing has to come back, which is what the
        # lock below is. Removing either alone is a bug: without the flag the
        # service cannot work, without the lock two threads share a cursor.
        self.db = sqlite3.connect(self.path, check_same_thread=False,
                                  timeout=BUSY_TIMEOUT_MS / 1000)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.busy_timeout = self.db.execute("PRAGMA busy_timeout").fetchone()[0]
        # Read the mode, do not claim it. A reader reads what is there; a
        # writer sets WAL and reads back what it actually got.
        row = self.db.execute(
            "PRAGMA journal_mode" if read_only
            else "PRAGMA journal_mode = WAL").fetchone()
        self.journal_mode = (row[0] if row else "unknown").lower()
        # Writes inside one process queue here rather than refusing each
        # other. The file lock refuses; this one waits. See lock.py.
        #
        # Reentrant, because the alternative is a deadlock waiting for its
        # first caller: `migrate()` holds this while creating the table and
        # then calls `set_meta`, which takes it again. It works today only
        # because `migrate` happens to leave the block first, which is a
        # property of one function rather than a rule anyone can follow.
        self._write_lock = threading.RLock()
        if read_only:
            self.db.execute("PRAGMA query_only = ON")
        else:
            self.migrate()

    # -- schema -----------------------------------------------------------

    def migrate(self) -> int:
        """Bring the schema to `SCHEMA_VERSION`. Idempotent. Returns the version."""
        with self.writing():
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL)")
            self.db.commit()
        current = int(self.get_meta("schema_version") or 0)
        if current < SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            current = SCHEMA_VERSION
        return current

    # -- meta -------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.writing():
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
            self.db.commit()

    # -- writing ----------------------------------------------------------

    @contextlib.contextmanager
    def writing(self):
        """Serialise writes within this process, and refuse an unguarded one.

        Every write in the package goes through here, so the service can run
        its scan on one thread and answer a request on another without either
        of them learning about the other.

        The guard is re-checked here as well as at open, because a process can
        release it while a `Store` object is still alive -- and a store handle
        that outlives the guard is exactly the case where a stale object goes
        on writing after another process has taken over.
        """
        if not holds(self.path):
            raise Unguarded(self.path)
        with self._write_lock:
            yield self.db

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
