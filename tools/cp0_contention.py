#!/usr/bin/env python3
"""N real processes race for one store's guard. Exactly one may win.

**Exit 0 = one winner. Exit 1 = none or several.**

Replaces `cp0_double_clear.py`, which measured a window in the borrowed
lock-file scheme: two writers both judging the same orphaned lock stale, one
clearing it and taking it, the other's already-decided `unlink` then removing
the live lock. That scheme is gone (see `tests/gold/FASIT-cp0.md`, "R4 og R5
er byttet ut"), and with it both the orphan and the window. There is nothing
left to plant.

What can still go wrong is plainer and worth a tool of its own: several
processes asking the kernel at the same instant. `tests/test_cp0.py` gate 9
contends two processes, one after the other, which is contention but not a
race. Here every contender blocks on the same start file and is released
together, so they arrive inside the same scheduling window.

Not a gate in the suite: it spawns processes and depends on timing, so a
green run is evidence and a red one is a reason to look, not a checkpoint
that failed.

    python3 tools/cp0_contention.py [contenders]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONTENDER = """
import os, sys, time
sys.path.insert(0, %r)
from morpho_homegraph.lock import Locked, StoreLock
store, gate = sys.argv[1], sys.argv[2]
# Everyone spins on the same file, so the release is one moment rather than a
# staggered start that would let each contender find the guard already free.
while not os.path.exists(gate):
    time.sleep(0.001)
try:
    lock = StoreLock(store).acquire()
except Locked:
    print("refused", flush=True)
else:
    print("won", flush=True)
    time.sleep(0.5)
    lock.release()
""" % REPO


def main(count: int) -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-contend-") as work:
        store = os.path.join(work, "index.db")
        gate = os.path.join(work, "go")
        procs = [subprocess.Popen(
            [sys.executable, "-c", CONTENDER, store, gate],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=REPO) for _ in range(count)]
        open(gate, "w").close()
        outs = [p.communicate(timeout=60) for p in procs]

    won = sum(1 for out, _ in outs if out.strip() == "won")
    refused = sum(1 for out, _ in outs if out.strip() == "refused")
    broke = [err.strip().splitlines()[-1:] for out, err in outs
             if out.strip() not in ("won", "refused")]
    print("contenders %d -> won %d, refused %d, neither %d"
          % (count, won, refused, len(broke)))
    for line in broke:
        print("  %s" % line)
    print("\n%s" % ("one winner" if won == 1 and not broke
                    else "GUARD IS NOT EXCLUSIVE"))
    return 0 if won == 1 and not broke else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    sys.exit(main(int(args[0]) if args else 8))
