#!/usr/bin/env python3
"""M-5: what the search index costs, and what the splitting buys.

The two belong in one tool because they are one trade. Identifier splitting is
mandatory (locked decision 1), and it is paid for in bytes: the tokens are
stored beside the text because SQLite's tokenizers cannot produce them.

**The recall figure is ours, on this corpus.** `TODO.md` carried "+6% in the
predecessor" and that number stays marked as inherited. Here the queries are
built from the corpus itself: every identifier that actually splits becomes a
query in its split form, and the question is how many of them find the file
they came from -- with the `symbols` column, and with it emptied.

Cost is measured by difference, not by estimate: build, VACUUM, measure; empty
the index, VACUUM, measure again.

    python3 tools/m5_search_index.py [path ...]
"""
from __future__ import annotations

import collections
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORD = re.compile(r"[0-9A-Za-z_.\-]{3,}")


def build_project(root):
    from morpho_homegraph import content, graph, identity, scope, search
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
                    content.build(store, l0, chosen)
                    graph.build(store, scope_root=root)
                    started = time.perf_counter()
                    l4 = search.build(store)
                    seconds = time.perf_counter() - started
            finally:
                guard.release()
    finally:
        guard0.release()
    return project_id, db_path(project_id), l4["rows"], seconds


def queries_from(store):
    """Split-form queries from the corpus, kept apart by *why* they split.

    The distinction is the whole point of the number. `snake_case` and
    `dotted.path` are already broken up by unicode61 in the `text` column, so a
    query in split form finds them with or without `symbols`. Only case
    boundaries -- `getUserById` -- need the column that costs bytes, and a
    single blended figure hides which corpus the splitting is worth paying for.
    """
    from morpho_homegraph import search
    by_case, by_separator = collections.Counter(), collections.Counter()
    for (text,) in store.db.execute(
            "SELECT text FROM content WHERE text IS NOT NULL"):
        for word in WORD.findall(text or ""):
            tokens = search.split_identifier(word)
            if len(tokens) < 2 or not all(len(t) > 1 for t in tokens):
                continue
            already = [p for p in search._SEPARATORS.split(word) if p]
            target = by_separator if len(already) > 1 else by_case
            target[" ".join(tokens)] += 1
    return ([q for q, _n in by_case.most_common(30)],
            [q for q, _n in by_separator.most_common(30)])


def compacted_size(store, db):
    """The store's size on disk after VACUUM, with the WAL folded back in.

    `os.path.getsize` alone is a lie in WAL mode: a VACUUM lands in `-wal` and
    the main file does not move until a checkpoint. Measured 2026-08-04 -- the
    first version of this tool reported a *negative* index size.
    """
    store.db.execute("VACUUM")
    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return os.path.getsize(db)


def recall(store, queries):
    from morpho_homegraph import search
    return sum(1 for q in queries if search.content(store, q, limit=1))


def main(argv):
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.store import Store
    from morpho_homegraph import search

    roots = argv[1:] or [os.path.expanduser("~/Morpho-HomeGraph")]
    with tempfile.TemporaryDirectory(prefix="mhg-m5-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        for root in roots:
            if not os.path.isdir(root):
                print("no such directory: %s" % root, file=sys.stderr)
                return 2
            project_id, db, rows, seconds = build_project(root)
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db) as store:
                    with_index = compacted_size(store, db)
                    case_q, sep_q = queries_from(store)
                    queries = case_q + sep_q
                    hits_with = recall(store, case_q), recall(store, sep_q)
                    with store.writing() as sql:
                        sql.execute("UPDATE %s SET symbols = ''" % search.TABLE)
                        sql.commit()
                    hits_without = recall(store, case_q), recall(store, sep_q)
                    with store.writing() as sql:
                        sql.execute("DELETE FROM %s" % search.TABLE)
                        sql.commit()
                    without_index = compacted_size(store, db)
            finally:
                guard.release()
            delta = with_index - without_index
            print("%s\n  L4: %d rows in %.2f s" % (root, rows, seconds))
            print("  store %.2f MB with the index, %.2f MB without: "
                  "**%.2f MB** is the index (%.0f %%)"
                  % (with_index / 1e6, without_index / 1e6, delta / 1e6,
                     100.0 * delta / with_index if with_index else 0))
            for label, qs, got, lost in (
                    ("case boundaries (getUserById)", case_q,
                     hits_with[0], hits_without[0]),
                    ("separators (snake_case, dotted.path)", sep_q,
                     hits_with[1], hits_without[1])):
                if not qs:
                    print("  %-38s no queries of this shape in the corpus"
                          % label)
                    continue
                print("  %-38s %d/%d with symbols, %d without (%+.0f %%)"
                      % (label, got, len(qs), lost,
                         100.0 * (got - lost) / len(qs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
