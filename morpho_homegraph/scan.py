#!/usr/bin/env python3
"""L0: metadata about every file, and nothing else.

`stat()` and nothing else, over the whole home area. The ground fact the
design rests on -- 628 862 files in 0.23 s, measured 2026-08-03 -- holds only
as long as this layer never opens a file, so "never opens a file" is a
property with two independent detectors on it rather than a comment.

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

# What a row's `kind` can be. Kept explicit rather than derived from `stat`
# bits at read time, so a caller asking "how many directories" does not have
# to know st_mode.
FILE, DIR, LINK, OTHER = "file", "dir", "link", "other"


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


def walk(root: str | Path):
    """Yield `(path, kind, size, mtime_ns, inode, dev)` for everything under `root`.

    Iterative, not recursive: a home area is deeper than the interpreter's
    stack is tall, and a `RecursionError` halfway through a scan produces a
    count that looks like an answer.

    Unreadable directories are yielded as their own row and not descended
    into. Missing permissions are the normal state of a home directory, and a
    walk that stops at the first one reports a number that is simply wrong.
    """
    root = str(Path(root).expanduser())
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


def scan(store, root: str | Path) -> dict[str, int | float]:
    """Walk `root` into the store's `files` table. Returns a summary.

    The whole walk is one transaction. A half-written L0 is worse than none:
    CP-2 diffs this layer against its previous self, and a truncated scan
    reads as "everything after this point was deleted".
    """
    started = time.perf_counter()
    counted = {"kept": 0, "unreadable": 0}

    def rows():
        # Streamed into `executemany`, not collected first. Measured
        # 2026-08-03: a home area is 729 303 entries, and materialising them
        # costs 113 MB before a single row is written -- for a service that
        # holds the model in memory as well, that is a cost with no purpose.
        for row in walk(root):
            if row[1] == "unreadable":
                counted["unreadable"] += 1
                continue
            counted["kept"] += 1
            yield row

    with store.writing() as db:
        db.execute("DELETE FROM files")
        db.executemany(
            "INSERT OR REPLACE INTO files "
            "(path, kind, size, mtime_ns, inode, dev) VALUES (?, ?, ?, ?, ?, ?)",
            rows())
        db.commit()
    elapsed = time.perf_counter() - started
    store.set_meta("l0_root", str(Path(root).expanduser()))
    store.set_meta("l0_scanned_at", str(time.time()))
    store.set_meta("l0_count", str(counted["kept"]))
    store.set_meta("l0_seconds", "%.3f" % elapsed)
    store.set_meta("l0_unreadable", str(counted["unreadable"]))
    return {"count": counted["kept"], "seconds": elapsed,
            "unreadable": counted["unreadable"]}
