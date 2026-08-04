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

SCHEMA_VERSION = 7

# Two roles, two schemas, and the split is the point. **L0 is shared**: it
# describes the whole home area, it is identical for every project, and it
# measured 204.8 MB on 2026-08-03 -- one copy per project would be that number
# times the number of projects, of byte-identical data. So it lives once, in
# its own store with its own session guard, and project stores hold the layers
# that actually differ per project.
#
# The role is explicit rather than inferred from the path because it decides
# which tables exist. A single schema everywhere would give every project a
# `files` table that must never be filled -- and nothing would stop CP-2 from
# filling it by accident, which is the kind of mistake that looks like data.
L0, PROJECT = "l0", "project"

SCHEMA = {
    # `meta` is in both: every store has to be able to say what it is.
    "meta": (L0, PROJECT,
             "CREATE TABLE IF NOT EXISTS meta ("
             "  key TEXT PRIMARY KEY,"
             "  value TEXT NOT NULL)"),
    # L0 (CP-1). The path is the key here, and that is not the rule CP-6
    # forbids: *a project's* path must never key an id, so that nobody can
    # treat a path as an identity without meeting the ambiguity CP-6 decides.
    # L0 is a path index by definition -- it answers "what does the filesystem
    # look like", and nothing else would serve as its key.
    #
    # mtime in nanoseconds as an integer: `st_mtime` is a float and loses
    # precision on large timestamps, and CP-2 has to tell "same size,
    # different mtime" from "same both" for real rather than by rounding
    # introduced here.
    "files": (L0,
              "CREATE TABLE IF NOT EXISTS files ("
              "  path TEXT PRIMARY KEY,"
              "  kind TEXT NOT NULL,"
              "  size INTEGER NOT NULL,"
              "  mtime_ns INTEGER NOT NULL,"
              "  inode INTEGER NOT NULL,"
              "  dev INTEGER NOT NULL,"
              # CP-2 (L1). NULL means "never hashed", which happens for
              # anything outside the chosen scope -- and it is the one value
              # that must never be read as "unchanged". homegraph stored
              # nothing here at all, so its stored hash was NULL for every
              # row, every rewrite came back as `changed`, and the two-step
              # design was decoration from the day it was written.
              "  content_hash TEXT)"),
    # CP-3: the scope, per project. The first thing a project store holds
    # besides `meta`, and the right place for it -- a scope is per project,
    # while L0 and L1 are shared.
    "scope": (PROJECT,
              "CREATE TABLE IF NOT EXISTS scope ("
              "  path TEXT PRIMARY KEY,"
              "  mode TEXT NOT NULL)"),
    # L1 (CP-2): what changed between this L0 pass and the previous one.
    # Replaced whole on every scan, like `files` -- a journal that accumulates
    # is one where "the previous pass" has stopped meaning anything.
    "journal": (L0,
                "CREATE TABLE IF NOT EXISTS journal ("
                "  path TEXT PRIMARY KEY,"
                "  state TEXT NOT NULL)"),
    # L2 (CP-4): the content of what the scope selected. Per *project*, not
    # shared: L0 and L1 describe the home area and are the same for everyone,
    # while content exists only for one scope. Two projects over the same disk
    # have different L2, and the shared store has no business holding either.
    #
    # Every path in `L0 ∩ scope ∩ kind=file` gets a row, including the ones
    # that could not be read -- those carry a `reason` and no `text`. Without
    # that, "not read" and "does not exist" are the same absence, and telling
    # them apart is what this layer is for. `reason IS NULL` iff
    # `text IS NOT NULL`; `tests/test_cp4.py` gate 2 checks the XOR rather
    # than trusting the writer.
    "content": (PROJECT,
                "CREATE TABLE IF NOT EXISTS content ("
                "  path TEXT PRIMARY KEY,"
                "  size INTEGER NOT NULL,"
                "  mtime_ns INTEGER NOT NULL,"
                "  sha256 TEXT,"
                "  text TEXT,"
                "  reason TEXT)"),
    # L3 (CP-5). Only the edges: a *node* is a distinct `content.sha256`, and
    # its paths are the `content` rows carrying that hash. Identity is the
    # content (locked decision 1), so two files with identical bytes are one
    # node with two paths -- and a `nodes` table would be that same fact
    # written down twice, with the copy free to drift.
    #
    # `method` and `confidence` are NOT NULL because an edge that does not say
    # how it came to exist is indistinguishable from one the file stated, and
    # that is the difference the whole layer is for. `(src, dst, kind)` is the
    # key so re-asserting an edge upgrades it in place rather than leaving the
    # derived version beside the stated one.
    "edges": (PROJECT,
              "CREATE TABLE IF NOT EXISTS edges ("
              "  src TEXT NOT NULL,"
              "  dst TEXT NOT NULL,"
              "  kind TEXT NOT NULL,"
              "  method TEXT NOT NULL,"
              "  confidence REAL NOT NULL,"
              "  PRIMARY KEY (src, dst, kind))"),
}

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


def l0_path() -> Path:
    """The one shared L0 store. `l0` is not a valid generated id (16 hex), so
    it cannot collide with a project directory."""
    return data_home() / L0 / "index.db"


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

    def __init__(self, path: str | Path, read_only: bool = False,
                 role: str = PROJECT) -> None:
        self.path = str(path)
        self.read_only = read_only
        self.role = role
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
            for roles_and_ddl in SCHEMA.values():
                *roles, ddl = roles_and_ddl
                if self.role in roles:
                    self.db.execute(ddl)
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
