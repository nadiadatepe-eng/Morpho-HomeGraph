#!/usr/bin/env python3
"""Mutation test for CP-16 -- the detector, and the debt it names.

Two halves, and the first one has a trap the other checkpoints do not.

**Mutating the detector also trips gate 6.** That gate pins the copy against
its source byte for byte, so every needle aimed at `condition_coverage.py`
turns two gates red at once. That is fine and it is checked: the driver kills
a mutation when the *expected* gate is among the red ones, not when it is the
only one. What it would not survive is a needle whose only killer is gate 6 --
that would prove the attribution works and say nothing about the detector. So
each of the first three names a behavioural gate (8, 10, 11), and if the
detector ever stops earning it, the mutation is reported as misattributed
rather than killed.

The second half is the point of the checkpoint. `watch.relevant` and the two
`Store` guards had no needle before today; both sit in code that has already
produced a real defect (CP-13 gate 6, and locked decision #12). A needle here
is the debt actually being paid, not tracked.

Run:
    python3 tests/mutate_cp16.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the detector can say no (R5) --------------------------------------
    ("the detector finds gaps and then drops them on the floor",
     "tests/condition_coverage.py",
     "            gaps.append((n, text))",
     "            pass  # mutated: every condition looks aimed at",
     "8  an unaimed compound condition fails and names the line"),

    ("an empty waiver becomes a silent opt-out",
     "tests/condition_coverage.py",
     "            if reason:\n"
     "                return reason\n"
     "    return None",
     "            return reason or 'mutated: empty waiver accepted'\n"
     "    return None",
     "10 a waiver with a reason clears it, an empty one does not"),

    ("the waiver window shrinks to the condition's own line",
     "tests/condition_coverage.py",
     "    for n in (lineno - 1, lineno - 2, lineno - 3):",
     "    for n in (lineno - 1,):  # mutated: no room for a comprehension",
     "11 a waiver two lines above the condition still counts"),

    # The control's own mutation: report everything, aimed at or not. Without
    # gate 14 this passes gate 8 while making the tool useless.
    ("the detector reports conditions somebody did aim at",
     "tests/condition_coverage.py",
     "            if text in needles:\n"
     "                continue",
     "            if False:  # mutated: needles no longer count\n"
     "                continue",
     "14 CONTROL: an aimed-at condition is not reported"),

    # -- the debt this checkpoint pays down (R4) ---------------------------
    ("relevant() stops refusing the store file itself",
     "morpho_homegraph/watch.py",
     '        if p == db or p.startswith(db + "-") or p.startswith(db + "."):',
     '        if p.startswith(db + "-") or p.startswith(db + "."):  # mutated',
     "12 relevant() refuses the store, its -wal and its .lock, and only those"),

    ("relevant() stops refusing the store's dot-siblings",
     "morpho_homegraph/watch.py",
     '        if p == db or p.startswith(db + "-") or p.startswith(db + "."):',
     '        if p == db or p.startswith(db + "-"):  # mutated: .lock is relevant',
     "12 relevant() refuses the store, its -wal and its .lock, and only those"),

    ("relevant() refuses everything, not just the store",
     "morpho_homegraph/watch.py",
     '        if p == db or p.startswith(db + "-") or p.startswith(db + "."):',
     "        if True:  # mutated: nothing is relevant",
     "12 relevant() refuses the store, its -wal and its .lock, and only those"),

    ("the read-only guard forgets it is only for read-only opens",
     "morpho_homegraph/store.py",
     "        if read_only and not os.path.exists(self.path):",
     "        if not os.path.exists(self.path):  # mutated: writers cannot create",
     "13 the Store guards: read-only needs the file, writable needs the lock"),

    ("the write guard stops asking whether the lock is held",
     "morpho_homegraph/store.py",
     "        if not read_only and not holds(self.path):",
     "        if not read_only:  # mutated: holding the lock is not enough",
     "13 the Store guards: read-only needs the file, writable needs the lock"),

    ("the write guard applies to readers too",
     "morpho_homegraph/store.py",
     "        if not read_only and not holds(self.path):",
     "        if not holds(self.path):  # mutated: readers must hold the lock",
     "13 the Store guards: read-only needs the file, writable needs the lock"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp16.py", prefix="mhg-mut-cp16-"))
