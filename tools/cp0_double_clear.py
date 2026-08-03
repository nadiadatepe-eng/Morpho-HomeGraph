#!/usr/bin/env python3
"""Reproduce the double-clear window in the borrowed lock. Deterministic.

**Exit 0 = the window is closed. Exit 1 = it is open.** It exits 1 today, on
purpose: this is a measurement of a known hole, not a gate that has to be
green. It is a tool rather than a check in `tests/test_cp0.py` for exactly
that reason -- a red gate in the suite would make every later checkpoint
unrunnable to record one fact.

What it plants, found by codex 2026-08-03:

    stale lock on disk (holder crashed)
    A: _create fails -> reads holder -> decides stale -> unlink -> _create
       -> reads back its own nonce -> HOLDS
    B: _create failed and B decided stale *before* A cleared -> B's unlink
       now removes A's live lock -> B's _create succeeds -> B reads back its
       own nonce -> HOLDS

Both processes hold. The nonce read-back does not close this: it only catches
the case where the loser's write lands *before* the winner reads back. A's
read-back happened first and A never looks again.

The steps below are not a simulation of `acquire()`; they are the calls
`acquire()` makes, in the order it makes them, once the liveness decision has
been taken. That decision is what the two contenders take independently, and
nothing between it and the unlink re-checks it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho_homegraph.lock import StoreLock, _liveness, _read  # noqa: E402


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-race-") as work:
        store = os.path.join(work, "index.db")
        lock_path = store + ".lock"
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": dead_pid(), "start": 1, "created": "crashed",
                       "nonce": "orphan", "store": store}, fh)

        a, b = StoreLock(store), StoreLock(store)

        # Both contenders read the same orphan and both decide it is stale.
        # This is the only decision either of them makes about the file.
        a_holder, b_holder = _read(lock_path), _read(lock_path)
        a_live, _ = _liveness(a_holder)
        b_live, _ = _liveness(b_holder)
        if a_live or b_live:
            print("planted lock did not read as stale -- nothing measured")
            return 2

        # A acts on its decision and completes the whole take.
        os.unlink(lock_path)
        a.held = a._create() and _read(lock_path).get("nonce") == a.nonce

        # B acts on the decision it took before A cleared.
        os.unlink(lock_path)
        b.held = b._create() and _read(lock_path).get("nonce") == b.nonce

        both = a.held and b.held
        print("A holds: %s\nB holds: %s\nlock file names: %s"
              % (a.held, b.held, _read(lock_path).get("nonce")))
        print("\nwindow is %s" % ("OPEN -- two writers hold the guard" if both
                                  else "closed"))
        return 1 if both else 0


if __name__ == "__main__":
    sys.exit(main())
