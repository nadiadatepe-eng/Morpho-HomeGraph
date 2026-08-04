#!/usr/bin/env python3
"""CP-7: snapshots, the retention window, and the only place allowed to remove.

The answer key is `tests/gold/FASIT-cp7.md`, written before this module.

**The whole checkpoint is one order: take, verify, then release.** CP-6 can say
a project is deleted but not remove it, because the place it was supposed to
live on did not exist. This builds that place -- and the reverse order is data
loss that looks like tidying up, which is why it is `retire()` with a named
failure step rather than three functions a caller is trusted to sequence.

**A snapshot is taken with SQLite's backup API, never with a file copy.**
`index.db` is opened in WAL (CP-0), and a WAL database is not one file: copying
`index.db` alone yields a torn copy missing everything still in `-wal`. The
failure is silent -- the copy opens fine and is merely older than it claims.

**Snapshots live outside the project directory**, in `data_home()/snapshots/`,
because `release_living()` removes that directory and would otherwise delete
the very copies it depends on.

**The floor in the retention rule is for living projects only**, and that is
this module resolving something the answer key left implicit: R5 wants an idle
project to keep a history, while R7 and gate 16 require a *deleted* project's
last snapshot to actually fall out of the window. A floor that applied to both
would make a deleted project's history immortal and the warning a lie. So age
alone decides for a project that is gone, which is what the window is for.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .identity import GONE, LIVING
from .lock import Unguarded, holds
from .store import PROJECT, Store, data_home, db_path

# Not a valid generated id (16 hex), so it cannot collide with a project
# directory -- the same reason `l0` is safe as a name.
SNAPSHOTS = "snapshots"

# Chosen, not derived. Named so they can be changed without being hunted for.
RETAIN_DAYS = 14
RETAIN_FLOOR = 3
WARN_WITHIN_DAYS = 2

DAY = 86400.0


class SnapshotFailed(RuntimeError):
    """One of the three steps stopped. `step` says which, so a caller can tell
    "no copy was made" from "a copy was made and is not trustworthy"."""

    def __init__(self, step: str, detail: object) -> None:
        self.step = step
        super().__init__("snapshot stopped at the %s step: %s" % (step, detail))


class PathGone(RuntimeError):
    """Restoring was refused because the project's directory is still missing."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(
            "%s does not exist, so restoring the index would describe files "
            "that are not there -- and CP-6 would mark it deleted again at the "
            "next pass. Bring the folder back from your own backup first, then "
            "restore" % path)


def snapshots_dir(project_id: str) -> Path:
    return data_home() / SNAPSHOTS / project_id


def prune_guard(project_id: str) -> str:
    """What a writer locks before deleting snapshots.

    The snapshot directory, not the project's store: after `release_living()`
    the project directory is gone, and a lock file cannot be created inside a
    directory that no longer exists. The guard is on the thing being written.
    """
    return str(snapshots_dir(project_id))


def snapshots(project_id: str) -> list[Path]:
    """This project's snapshots, newest first, by mtime.

    By mtime rather than by name: blind spot 1 in the answer key is a clock
    that went backwards on this machine, and a name that sorts wrong sorts
    wrong however it is read. Neither is a fix -- mtime is simply the value
    that needs no parsing and no unparsable-name branch.
    """
    directory = snapshots_dir(project_id)
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.glob("*.db")),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def take(project_id: str) -> Path:
    """Copy the living index into a new snapshot. Returns its path.

    **No guard.** `backup()` reads, and WAL admits readers while the writer
    writes, so a snapshot can be taken while the service is working. Requiring
    the guard here would give the snapshot a limitation it does not need (R10).
    """
    destination = snapshots_dir(project_id)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (datetime.now().strftime("%Y%m%dT%H%M%S_%f") + ".db")
    with Store(db_path(project_id), read_only=True) as store:
        target = sqlite3.connect(str(path))
        try:
            store.db.backup(target)
        finally:
            target.close()
    return path


def verify(snapshot_path: str | Path, project_id: str,
           project_path: str) -> bool:
    """Open the copy again, read-only, and demand the same identity facts.

    `PRAGMA integrity_check` alone is not enough: it answers `ok` for a
    completely empty, valid database. A copy that is intact but of nothing --
    or of another project -- is this function's main target (R4).
    """
    try:
        with Store(snapshot_path, read_only=True) as copy:
            row = copy.db.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return False
            return (copy.get_meta("project_id") == project_id
                    and copy.get_meta("project_path") == project_path)
    except (sqlite3.Error, OSError):
        return False


def release_living(project_id: str) -> None:
    """Remove the project's living store. The caller holds the guard.

    The lock file goes with the directory, which is the one case where that is
    correct: `lock.py` keeps lock files forever so two writers cannot end up
    holding locks on two inodes of the same path, and here the path itself is
    ceasing to exist.
    """
    store_db = db_path(project_id)
    if not holds(str(store_db)):
        raise Unguarded(str(store_db))
    shutil.rmtree(store_db.parent)


def retire(project_id: str) -> Path:
    """Take, verify, then release. Never two of the three.

    Returns the verified snapshot. Raises `SnapshotFailed` naming the step that
    stopped -- and if either of the first two stopped, the living store has not
    been touched. A half-done tidy-up always leaves more, never less.
    """
    with Store(db_path(project_id), read_only=True) as store:
        project_path = store.get_meta("project_path") or ""
    try:
        snapshot_path = take(project_id)
    except (sqlite3.Error, OSError) as exc:
        raise SnapshotFailed("take", exc) from exc
    if not verify(snapshot_path, project_id, project_path):
        raise SnapshotFailed(
            "verify", "%s did not read back as this project" % snapshot_path)
    try:
        release_living(project_id)
    except OSError as exc:
        # The third step names itself too. A release that failed halfway is
        # the one state nothing else here can describe, and a caller that is
        # told only "it failed" cannot tell it from the two harmless ones.
        raise SnapshotFailed("release", exc) from exc
    return snapshot_path


def state_of(project_id: str) -> str:
    """`living` or `deleted`. A project whose living store is gone is deleted:
    it exists only in snapshots, which is precisely what R7 warns about."""
    store_db = db_path(project_id)
    if not store_db.is_file():
        return GONE
    try:
        with Store(store_db, read_only=True) as store:
            return store.get_meta("state") or LIVING
    except (sqlite3.Error, OSError):
        # A store that is there but cannot be read right now is a reason to
        # keep *more* history, not less: `living` gives it the floor. Saying
        # `deleted` would let a transient read failure age its snapshots out.
        return LIVING


def snapshot_projects() -> list[str]:
    """Every project id that has snapshots, living or not."""
    root = data_home() / SNAPSHOTS
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def apply_retention(project_id: str, now: float | None = None) -> list[Path]:
    """Drop snapshots outside the window. Returns the ones removed.

    Everything younger than `RETAIN_DAYS` is kept, and for a living project
    never fewer than `RETAIN_FLOOR` of them -- see the module docstring for why
    the floor stops at the living. **The caller holds the guard**, on the
    snapshot directory (`prune_guard`): deleting is writing.
    """
    guarded = prune_guard(project_id)
    if not holds(guarded):
        raise Unguarded(guarded)
    now = time.time() if now is None else now
    found = snapshots(project_id)
    floor = 0 if state_of(project_id) == GONE else RETAIN_FLOOR
    removed = []
    for path in found[floor:]:
        if (now - path.stat().st_mtime) / DAY <= RETAIN_DAYS:
            continue
        path.unlink()
        removed.append(path)
    return removed


def expiring(within_days: float = WARN_WITHIN_DAYS,
             now: float | None = None) -> list[tuple[str, float]]:
    """Deleted projects about to lose history: `(project_id, days left)`.

    Only projects that live in snapshots alone. A living project that loses its
    oldest snapshot has lost history; a deleted one that loses its snapshots
    has lost itself, and only the second is worth interrupting anyone for.

    The clock is the *oldest* snapshot still holding the project (R7), so the
    warning arrives while every later one is still there.
    """
    now = time.time() if now is None else now
    due = []
    for project_id in snapshot_projects():
        if state_of(project_id) != GONE:
            continue
        found = snapshots(project_id)
        if not found:
            continue
        left = RETAIN_DAYS - (now - found[-1].stat().st_mtime) / DAY
        if left <= within_days:
            due.append((project_id, left))
    return sorted(due, key=lambda row: row[1])


def claimed_path(snapshot_path: str | Path) -> str:
    """The project path a snapshot says it is of; `""` if it cannot be read."""
    try:
        with Store(snapshot_path, read_only=True) as copy:
            return copy.get_meta("project_path") or ""
    except (sqlite3.Error, OSError):
        return ""


def restore(project_id: str, snapshot_path: str | Path | None = None) -> Path:
    """Put a snapshot back as the living index. The caller holds the guard.

    **The newest snapshot that verifies**, not simply the newest: a copy that
    was interrupted while being written is a file in this directory like any
    other, and installing it would make R4 a check that runs everywhere except
    where it decides something. Naming one explicitly still verifies it.

    Refused while the project's directory is missing (R8): an index restored
    over a folder that is not there describes files that do not exist, and
    CP-6 would mark it deleted again at the next pass. Refusing beats choosing
    wrongly, the same house rule CP-6's `Moved` follows.
    """
    store_db = db_path(project_id)
    if not holds(str(store_db)):
        raise Unguarded(str(store_db))
    candidates = ([Path(snapshot_path)] if snapshot_path is not None
                  else snapshots(project_id))
    if not candidates:
        raise FileNotFoundError("no snapshots for %s" % project_id)
    for candidate in candidates:
        # The path half of the verification is read from the same file here,
        # so what it actually proves is the integrity check and the id -- the
        # living store that would have held the expected path is gone, which
        # is the whole reason we are restoring.
        project_path = claimed_path(candidate)
        if verify(candidate, project_id, project_path):
            snapshot_path = candidate
            break
    else:
        raise SnapshotFailed(
            "verify", "no snapshot of %s reads back as this project"
            % project_id)
    if not os.path.isdir(project_path):
        raise PathGone(project_path)
    store_db.parent.mkdir(parents=True, exist_ok=True)
    # A plain copy is right here and wrong in `take`: a snapshot is a single
    # file with no `-wal` beside it, so there is nothing a copy can miss.
    shutil.copy2(snapshot_path, store_db)
    with Store(store_db, role=PROJECT) as store:
        store.set_meta("state", LIVING)
    return store_db
