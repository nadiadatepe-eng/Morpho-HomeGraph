#!/usr/bin/env python3
"""M-2: what a snapshot of one project costs on disk.

The question CP-7 owes an answer to, and it decides one thing: whether R1's
full copy is affordable per project per snapshot. Timeshift's hardlink trick is
ruled out already -- one SQLite file changes in its entirety, so a hardlink has
nothing to share -- which leaves "the file is small enough that copying it is
not a decision" as the only way the design stands.

Builds a real index for the tree given (L0, scope, L2, L3), takes a snapshot,
and reports both sizes plus the number of snapshots that fit in the window.
Everything goes in a temporary store: this measures, it does not register.

    python3 tools/m2_snapshot_size.py [path ...]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def measure(root: str) -> dict:
    from morpho_homegraph import content, graph, identity, scope, snapshot
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.scan import scan
    from morpho_homegraph.store import (L0, PROJECT, Store, db_path,
                                        initialise, l0_path, new_project)

    root = os.path.abspath(os.path.expanduser(root))
    # The scope a real caller would get, not a bare `Scope().add(root)`.
    # Measured 2026-08-04 on `~/homegraph`: the bare one indexes `.git` and
    # everything `.gitignore` names, 1 729 rows against 175, and the snapshot
    # size that follows from it is a number for a configuration nobody runs.
    if scope.is_repo(root):
        chosen, _patterns = scope.from_repo(root)
    else:
        chosen = scope.from_folder(root)
    project_id, db = new_project()
    l0_db = l0_path()
    l0_db.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    guard0 = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, root, deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    initialise(store, project_id, root)
                    identity.remember_root(store, root)
                    content.build(store, l0, chosen)
                    graph.build(store, scope_root=root)
            finally:
                guard.release()
    finally:
        guard0.release()
    built = time.monotonic() - started

    with Store(db_path(project_id), read_only=True) as store:
        rows = store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        edges = store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    started = time.monotonic()
    path = snapshot.take(project_id)
    took = time.monotonic() - started
    return {
        "root": root,
        "rows": rows,
        "edges": edges,
        "index": os.path.getsize(db_path(project_id)),
        "snapshot": os.path.getsize(path),
        "build_seconds": built,
        "snapshot_seconds": took,
        "window": snapshot.RETAIN_DAYS,
        "floor": snapshot.RETAIN_FLOOR,
    }


def main(argv: list[str]) -> int:
    roots = argv[1:] or [os.path.expanduser("~/homegraph")]
    with tempfile.TemporaryDirectory(prefix="mhg-m2-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        for root in roots:
            if not os.path.isdir(root):
                print("no such directory: %s" % root, file=sys.stderr)
                return 2
            found = measure(root)
            mb = found["snapshot"] / 1e6
            print("%s\n  %d content rows, %d edges, built in %.2f s"
                  % (found["root"], found["rows"], found["edges"],
                     found["build_seconds"]))
            print("  index %.2f MB, snapshot %.2f MB, taken in %.3f s"
                  % (found["index"] / 1e6, mb, found["snapshot_seconds"]))
            # A daily snapshot is the shape CP-13 will ask for; the window and
            # the floor say how many of them are on disk at once.
            print("  one a day for %d days: %.2f MB held"
                  % (found["window"], mb * found["window"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
