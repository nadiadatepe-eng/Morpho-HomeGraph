#!/usr/bin/env python3
"""M-6: what the hash key buys, and what the vectors cost.

The measurement CP-9 owes (`tests/gold/FASIT-cp9.md`, last section). R2 keys
vectors on `content.sha256` precisely so that a second run is nearly free, and
that claim is worth a number rather than a sentence.

**The plan, written before the run** (house rule 1, so the threshold is not
chosen to make the result pass):

  * **What is measured:** the first embedding of a tree, then a second one with
    nothing changed, then the vectors' cost on disk by difference.
  * **What the answer is for:** if the second run costs more than a tenth of
    the first, the hash key is not delivering what R2 claims and CP-9's design
    has to be looked at again -- an `update` would then be re-paying the
    embedding, which is the failure the key exists to prevent.
  * **What it is not:** a measurement of whether the search finds anything.
    That is CP-9E.

Cost is measured by difference with a VACUUM at each end, the way M-5 does it:
`os.path.getsize` alone reports nonsense in WAL mode, and the first version of
M-5 printed a *negative* index size before that was fixed.

    python3 tools/m6_embed_reuse.py [path ...]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_project(root):
    """Everything up to but not including the embedding: L0, scope, L2, L3."""
    from morpho_homegraph import content, graph, identity, scope
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.scan import scan
    from morpho_homegraph.store import (L0, PROJECT, Store, db_path,
                                        initialise, l0_path, new_project)

    root = os.path.abspath(os.path.expanduser(root))
    project_id, db = new_project()
    l0_db = l0_path()
    l0_db.parent.mkdir(parents=True, exist_ok=True)
    guard0 = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, root, deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    initialise(store, project_id, root)
                    identity.remember_root(store, root)
                    chosen = (scope.from_repo(root)[0] if scope.is_repo(root)
                              else scope.from_folder(root))
                    l2 = content.build(store, l0, chosen)
                    graph.build(store, scope_root=root)
            finally:
                guard.release()
    finally:
        guard0.release()
    return project_id, db_path(project_id), l2["read"]


def compacted_size(store, db):
    """Size on disk after VACUUM, with the WAL folded back in (see M-5)."""
    store.db.execute("VACUUM")
    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return os.path.getsize(db)


def main(argv):
    from morpho_homegraph import embed
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.store import Store

    roots = argv[1:] or [os.path.expanduser("~/Morpho-HomeGraph")]
    with tempfile.TemporaryDirectory(prefix="mhg-m6-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        for root in roots:
            if not os.path.isdir(root):
                print("no such directory: %s" % root, file=sys.stderr)
                return 2
            project_id, db, files = build_project(root)
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db) as store:
                    before = compacted_size(store, db)
                    started = time.perf_counter()
                    first = embed.build(store)
                    first_seconds = time.perf_counter() - started
                    after = compacted_size(store, db)
                    started = time.perf_counter()
                    second = embed.build(store)
                    second_seconds = time.perf_counter() - started
            finally:
                guard.release()

            rate = first["embedded"] / first_seconds if first_seconds else 0
            print("%s\n  L2: %d files read" % (root, files))
            print("  first run       %d chunks in %.1f s  ->  %.1f chunks/s"
                  % (first["embedded"], first_seconds, rate))
            print("  second run      %d chunks in %.2f s  (%d reused, %d "
                  "removed)" % (second["embedded"], second_seconds,
                                second["reused"], second["removed"]))
            print("  reuse           second run is %.1f %% of the first"
                  % (100.0 * second_seconds / first_seconds
                     if first_seconds else 0))
            print("  vectors on disk %.2f MB (store %.2f -> %.2f MB), "
                  "%.0f B/chunk"
                  % ((after - before) / 1e6, before / 1e6, after / 1e6,
                     (after - before) / first["chunks"]
                     if first["chunks"] else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
