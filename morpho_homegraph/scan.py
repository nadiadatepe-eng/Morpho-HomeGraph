#!/usr/bin/env python3
"""L0: metadata about every file, and nothing else.

`stat()` and nothing else, over the whole home area. The ground fact the
design rests on -- 729 343 entries in 5.13 s warm / 7.51 s cold, measured by
M-1 on 2026-08-03 -- holds only as long as this layer never opens a file, so
"never opens a file" is a property with two independent detectors on it rather
than a comment. (The inherited "628 862 files in 0.23 s" this docstring used to
quote does not reproduce; M-1 replaced it.)

**Directories are opened; files are not.** `os.scandir` calls `opendir`, and a
walk that does not is not a walk. `entry.stat(follow_symlinks=False)` is
`fstatat` on an already-open directory descriptor, which never touches the
file. That distinction is the rule -- a gate forbidding every `openat` would
be red on correct code.

Symlinks are recorded and never followed. Following them gives cycles and
double counting, and both of those look like ordinary numbers in a report.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import journal
from .store import L0


class WrongStore(RuntimeError):
    """L0 was aimed at a store that is not the shared L0 store."""


class DeniedRoot(ValueError):
    """The root to scan is itself on the deny-list."""

# What a row's `kind` can be. Kept explicit rather than derived from `stat`
# bits at read time, so a caller asking "how many directories" does not have
# to know st_mode.
FILE, DIR, LINK, OTHER = "file", "dir", "link", "other"

# The deny-list, as data and in one place. Short on purpose: L0 covers the
# whole home area so that name search does, and every entry here is a place
# the user cannot then find anything in.
#
# The cloud drives are listed while empty. Mounted later, descending them is a
# *network* operation that can hang or pull files down, and by then the scan is
# already running. `~/.cache` is 281 150 of 729 343 entries, measured 08-02:
# four fifths of the walk is browser and model cache nobody searches.
#
# `node_modules`, `.git`, `.venv` and friends deliberately do NOT belong here.
# They are CP-3's scope filter. L0 must still be able to answer "this file
# exists" about a file inside one.
DEFAULT_DENY = (
    "~/GoogleDrive",
    "~/OneDrive",
    "~/.cache",
    "~/.local/share/Trash",
    "~/snap",
)


def _normalise_root(root: str | Path) -> str:
    """Absolute, `~`-expanded, no trailing separator -- except at the filesystem
    root, where the separator *is* the path.

    Its own function so the "/" case has somewhere to be gated. Inline, the
    only way to observe it was to walk the whole filesystem, and a property
    that expensive to check is a property nobody checks.
    """
    return str(Path(root).expanduser()).rstrip(os.sep) or os.sep


def _expand(deny) -> tuple[str, ...]:
    """Absolute, `~`-expanded, trailing separators stripped -- once, not per entry."""
    return tuple(str(Path(d).expanduser()).rstrip(os.sep) for d in deny)


def _denied(path: str, deny: tuple[str, ...]) -> bool:
    """True if `path` is a denied directory or lives under one.

    The `+ os.sep` is the whole rule: a bare `startswith` would swallow
    `~/.cachexyz` along with `~/.cache`, and that mistake removes rows, which
    looks exactly like a deny-list that works.
    """
    return any(path == d or path.startswith(d + os.sep) for d in deny)


def _kind(entry: os.DirEntry) -> str:
    # follow_symlinks=False throughout: a link is its own thing, not a second
    # copy of what it points at.
    if entry.is_symlink():
        return LINK
    if entry.is_dir(follow_symlinks=False):
        return DIR
    if entry.is_file(follow_symlinks=False):
        return FILE
    return OTHER


def walk(root: str | Path, deny=DEFAULT_DENY):
    """Yield `(path, kind, size, mtime_ns, inode, dev)` for everything under `root`.

    Iterative, not recursive: a home area is deeper than the interpreter's
    stack is tall, and a `RecursionError` halfway through a scan produces a
    count that looks like an answer.

    Unreadable directories are yielded as their own row and not descended
    into. Missing permissions are the normal state of a home directory, and a
    walk that stops at the first one reports a number that is simply wrong.

    `deny` prunes: a denied path yields no row and is never descended into.
    Pruning rather than filtering afterwards is the point -- a filter has
    already paid for the entries it drops. Pass `deny=()` to scan everything;
    that is legal and must give the same count as before the list existed.
    Pruned paths are counted through the `("!pruned", ...)` marker row so a
    deny-list that fires zero times cannot look like one that works.
    """
    root = _normalise_root(root)
    deny = _expand(deny)
    if _denied(root, deny):
        # Otherwise: zero rows written over a full store, which is data loss
        # wearing the shape of a successful scan.
        raise DeniedRoot("the root to scan is on the deny-list: %s" % root)
    pending = [root]
    seen_dirs: set[tuple[int, int]] = set()
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except (PermissionError, OSError):
            # Reported by the caller through `unreadable`; the row for the
            # directory itself was already emitted by its parent.
            yield ("!" + current, "unreadable", 0, 0, 0, 0)
            continue
        for entry in entries:
            if _denied(entry.path, deny):
                yield ("!" + entry.path, "pruned", 0, 0, 0, 0)
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                # Deleted between listing and stat. A home area is alive, and
                # a walk that raises here fails more often the busier the
                # machine is.
                continue
            kind = _kind(entry)
            yield (entry.path, kind, st.st_size, st.st_mtime_ns,
                   st.st_ino, st.st_dev)
            if kind == DIR:
                # (dev, inode) rather than the path: a bind mount reaches the
                # same directory by two names, and descending both counts
                # every file under it twice.
                key = (st.st_dev, st.st_ino)
                if key not in seen_dirs:
                    seen_dirs.add(key)
                    pending.append(entry.path)


def scan(store, root: str | Path,
         keep=None, deny=DEFAULT_DENY) -> dict:
    """Walk `root` into the store's `files` table. Returns a summary.

    The whole walk is one transaction. A half-written L0 is worse than none:
    CP-2 diffs this layer against its previous self, and a truncated scan
    reads as "everything after this point was deleted".

    Refuses a project store outright. L0 is shared -- one copy for the whole
    home area, 204.8 MB of it measured 2026-08-03 -- and a scan that landed in
    a project store would be that much duplicated data that nothing reads,
    growing once per project.
    """
    if store.role != L0:
        raise WrongStore(
            "L0 belongs in the shared store, not in a %r one: %s"
            % (store.role, store.path))
    started = time.perf_counter()
    counted = {"kept": 0, "unreadable": 0, "pruned": 0}

    def rows():
        # Streamed into `executemany`, not collected first. Measured
        # 2026-08-03: a home area is 729 303 entries, and materialising them
        # costs 113 MB before a single row is written -- for a service that
        # holds the model in memory as well, that is a cost with no purpose.
        for row in walk(root, deny):
            if row[1] == "unreadable":
                counted["unreadable"] += 1
                continue
            if row[1] == "pruned":
                counted["pruned"] += 1
                continue
            counted["kept"] += 1
            yield row

    with store.writing() as db:
        # Into a staging table first, so the previous pass is still there when
        # L1 diffs against it. Replacing `files` in place would destroy the
        # only thing the journal has to compare with.
        db.execute("DROP TABLE IF EXISTS files_new")
        db.execute("CREATE TABLE files_new AS SELECT * FROM files WHERE 0")
        db.executemany(
            "INSERT OR REPLACE INTO files_new "
            "(path, kind, size, mtime_ns, inode, dev) VALUES (?, ?, ?, ?, ?, ?)",
            rows())
        tally = journal.build(store, keep)
        db.execute("DELETE FROM files")
        db.execute("INSERT INTO files SELECT * FROM files_new")
        db.execute("DROP TABLE files_new")
        db.commit()
    elapsed = time.perf_counter() - started
    store.set_meta("l0_root", str(Path(root).expanduser()))
    store.set_meta("l0_scanned_at", str(time.time()))
    store.set_meta("l0_count", str(counted["kept"]))
    store.set_meta("l0_seconds", "%.3f" % elapsed)
    store.set_meta("l0_unreadable", str(counted["unreadable"]))
    store.set_meta("l0_pruned", str(counted["pruned"]))
    store.set_meta("l1_tally", repr(tally))
    return {"count": counted["kept"], "seconds": elapsed,
            "unreadable": counted["unreadable"], "pruned": counted["pruned"],
            "journal": tally}


def knows(l0_store, root: str) -> bool:
    """Has this catalogue ever seen `root`? (CP-7B R3.)

    A project whose tree L0 has never walked would get an empty L2 -- and an
    empty L2 reads exactly like a project with nothing in scope. The two are
    told apart here rather than left to the caller to notice, because only one
    of them is a fact about the user's files.

    The root's own row is enough: `scan` records directories as well as files,
    so a catalogued empty folder answers yes and is allowed to produce zero
    content rows. Asking for a *file* under the root would refuse it, which is
    the same wrong answer one step further along.
    """
    root = str(Path(root).expanduser().resolve()).rstrip(os.sep)
    return bool(l0_store.db.execute(
        "SELECT 1 FROM files WHERE path = ? OR path LIKE ? LIMIT 1",
        (root, root + os.sep + "%")).fetchone())
