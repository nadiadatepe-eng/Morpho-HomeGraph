#!/usr/bin/env python3
"""CP-2 -- L1, the change journal.

The answer key is `tests/gold/FASIT-cp2.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

Two of these gates exist because homegraph got them wrong for two years and
stayed green the whole time. **`touched` has to be able to fire** -- there,
nothing stored `content_hash`, so the stored hash was NULL for every row, every
rewrite came back as `changed`, and a two-step design that could only ever
produce one of its two answers passed every gate it had. And **a NULL stored
hash must mean `unconfirmed`, never a verdict**, which is the same bug pointed
the right way.

Run:
    python3 tests/test_cp2.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.journal import (  # noqa: E402
    ADDED, CHANGED, REMOVED, TOUCHED, UNCHANGED, UNCONFIRMED)
from morpho_homegraph import scope as scope_mod  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.scan import scan  # noqa: E402
from morpho_homegraph.store import L0, Store, l0_path  # noqa: E402

results, check = reporter(58)


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def pass_over(store, root, scope):
    """One L0 pass, and the journal it produced as `{path: state}`.

    A pass that raises returns empty rather than propagating: the four
    verdicts are meant to partition the rows, and when a mutation makes two of
    them overlap SQLite says so with a UNIQUE violation. That is the schema
    doing its job -- but a harness that dies on it reports a crash, and a
    crash names no gate. Empty means every `state_of` below answers MISSING,
    which reddens the gates that actually describe the damage.
    """
    try:
        summary = scan(store, root, scope)
    except sqlite3.Error as exc:
        return {"journal": {}, "failed": repr(exc)}, {}
    states = {r[0]: r[1] for r in store.db.execute(
        "SELECT path, state FROM journal")}
    return summary, states


def hashes(store):
    return {r[0]: r[1] for r in store.db.execute(
        "SELECT path, content_hash FROM files")}


def main():
    with tempfile.TemporaryDirectory(prefix="mhg-cp2-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        tree = os.path.join(work, "tree")
        inside = os.path.join(tree, "inside")
        outside = os.path.join(tree, "outside")
        # A sibling whose *name* starts with the scope root's name. Without
        # it, "is this path inside the scope" cannot tell a string prefix
        # from a path prefix, and the gate below passes either way -- the
        # same fixture hole CP-0 had between /home and /homegraph.
        lookalike = inside + "-extra"
        os.makedirs(inside)
        os.makedirs(outside)
        os.makedirs(lookalike)
        # CP-15: `journal.build` takes a predicate, not a list of roots.
        # Built with the production `Scope` so gate 3's sibling-prefix
        # case still runs through the code that owns that rule.
        scope = scope_mod.Scope().add(inside).contains

        write(os.path.join(inside, "steady.txt"), "steady\n")
        write(os.path.join(inside, "same-length.json"), '{"d": "2026-08-01"}\n')
        write(os.path.join(inside, "mtime-kept.txt"), "before\n")
        write(os.path.join(inside, "touch-me.txt"), "unchanged content\n")
        write(os.path.join(inside, "doomed.txt"), "goes away\n")
        write(os.path.join(inside, "blind.txt"), "AAAAAAA\n")
        write(os.path.join(outside, "far.json"), '{"d": "2026-08-01"}\n')
        write(os.path.join(lookalike, "near.json"), '{"d": "2026-08-01"}\n')

        db = l0_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        guard = StoreLock(str(db)).acquire()
        try:
            with Store(db, role=L0) as store:
                # -- first pass: nothing to compare against ----------------
                summary, states = pass_over(store, tree, scope)
                files = [p for p, s in states.items() if os.path.isfile(p)]
                check("14 the first pass ever is all added",
                      set(states.values()) == {ADDED}
                      and summary["journal"].get(CHANGED) is None,
                      "states=%s" % sorted(set(states.values())))
                stored = hashes(store)
                # `inside + os.sep`, not `inside`: the lookalike sibling is a
                # string prefix match and not a path one, and this gate had
                # the very bug it exists to catch until the fixture grew the
                # sibling that could show it.
                in_scope_files = [p for p in files
                                  if p.startswith(inside + os.sep)]
                elsewhere = [p for p in files
                             if not p.startswith(inside + os.sep)]
                check("14b new files in scope are hashed on the way in",
                      all(stored[p] for p in in_scope_files)
                      and not any(stored[p] for p in elsewhere),
                      "%d of %d in scope hashed, %d elsewhere hashed"
                      % (sum(1 for p in in_scope_files if stored[p]),
                         len(in_scope_files),
                         sum(1 for p in elsewhere if stored[p])))

                # -- plant every case the rules name -----------------------
                blind = os.path.join(inside, "blind.txt")
                blind_stat = os.stat(blind)
                time.sleep(0.01)

                # R4: same length, different content. Only mtime betrays it.
                write(os.path.join(inside, "same-length.json"),
                      '{"d": "2026-08-02"}\n')
                # R5: different content, mtime put back afterwards.
                kept = os.path.join(inside, "mtime-kept.txt")
                kept_stat = os.stat(kept)
                write(kept, "after, and longer\n")
                os.utime(kept, ns=(kept_stat.st_atime_ns, kept_stat.st_mtime_ns))
                # R2: the case homegraph could never produce.
                os.utime(os.path.join(inside, "touch-me.txt"))
                os.remove(os.path.join(inside, "doomed.txt"))
                write(os.path.join(inside, "fresh.txt"), "new here\n")
                # Outside the scope: differs, and nobody will look.
                write(os.path.join(outside, "far.json"), '{"d": "2026-08-02"}\n')
                write(os.path.join(lookalike, "near.json"),
                      '{"d": "2026-08-02"}\n')
                # R6: same size AND same mtime, different content.
                write(blind, "BBBBBBB\n")
                os.utime(blind, ns=(blind_stat.st_atime_ns,
                                    blind_stat.st_mtime_ns))

                # The audit hook watches the whole second pass: nothing
                # outside the scope may be opened (R7).
                opened = []

                def spy(event, args):
                    if event == "open" and args and isinstance(args[0], str):
                        opened.append(os.path.abspath(args[0]))

                sys.addaudithook(spy)
                summary, states = pass_over(store, tree, scope)

                def state_of(name, where=inside):
                    return states.get(os.path.join(where, name), "MISSING")

                check("1  a new file is added", state_of("fresh.txt") == ADDED,
                      state_of("fresh.txt"))
                check("2  a deleted file is removed",
                      state_of("doomed.txt") == REMOVED, state_of("doomed.txt"))
                check("3  an untouched file is unchanged",
                      state_of("steady.txt") == UNCHANGED,
                      state_of("steady.txt"))
                # An unchanged file keeps the hash it already had. Nothing
                # else in this file sees it: the hash only matters on the
                # *next* pass, and a version that dropped it silently made
                # every later `touched` come back `unconfirmed` -- one pass
                # late, which is where the original bug lived too.
                steady = os.path.join(inside, "steady.txt")
                check("3b an unchanged file keeps its stored hash",
                      bool(hashes(store).get(steady)),
                      "hash %s" % ("kept" if hashes(store).get(steady) else "LOST"))
                check("4  a same-length edit is changed",
                      state_of("same-length.json") == CHANGED,
                      state_of("same-length.json"))
                check("5  an edit with mtime put back is changed",
                      state_of("mtime-kept.txt") == CHANGED,
                      state_of("mtime-kept.txt"))
                # The one homegraph could never reach.
                check("6  a pure touch is touched, and touched can fire",
                      state_of("touch-me.txt") == TOUCHED,
                      state_of("touch-me.txt"))
                check("7  a change outside the scope is unconfirmed",
                      state_of("far.json", outside) == UNCONFIRMED,
                      state_of("far.json", outside))
                check("11 same size and same mtime is the blind spot",
                      state_of("blind.txt") == UNCHANGED,
                      "%s -- known, and wrong" % state_of("blind.txt"))

                beyond = (os.path.abspath(outside), os.path.abspath(lookalike))
                outside_opens = [p for p in opened if p.startswith(beyond)]
                inside_opens = [p for p in opened
                                if p.startswith(os.path.abspath(inside) + os.sep)]
                check("8  no file outside the scope is opened",
                      not outside_opens, "%d opens outside" % len(outside_opens))
                check("9  files inside the scope are opened (control for 8)",
                      len(inside_opens) >= 3, "%d opens inside" % len(inside_opens))

                total = sum(summary["journal"].values())
                l0 = store.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                check("13 every L0 row and every removal has a verdict",
                      total == l0 + summary["journal"].get(REMOVED, 0),
                      "%d journal rows, %d in L0, %d removed"
                      % (total, l0, summary["journal"].get(REMOVED, 0)))

                # -- R3: a NULL stored hash is never a verdict -------------
                # The file has only ever been outside the scope, so nothing
                # was ever hashed for it. Widening the scope to include it and
                # changing it must not produce `changed` out of thin air.
                summary, states = pass_over(store, tree, scope)
                write(os.path.join(outside, "far.json"), '{"d": "2026-08-03"}\n')
                wider = scope_mod.Scope().add(inside).add(outside).contains
                summary, states = pass_over(store, tree, wider)
                check("10 a NULL stored hash gives unconfirmed, not a verdict",
                      states[os.path.join(outside, "far.json")] == UNCONFIRMED,
                      states[os.path.join(outside, "far.json")])

                # -- R8: the journal is replaced, not accumulated ----------
                before = store.db.execute(
                    "SELECT COUNT(*) FROM journal").fetchone()[0]
                summary, states = pass_over(store, tree, scope)
                after = store.db.execute(
                    "SELECT COUNT(*) FROM journal").fetchone()[0]
                check("12 a second journal replaces the first",
                      after <= before and set(states.values()) <= {
                          UNCHANGED, TOUCHED, UNCONFIRMED},
                      "%d -> %d rows, states=%s"
                      % (before, after, sorted(set(states.values()))))
        finally:
            guard.release()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp2():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
