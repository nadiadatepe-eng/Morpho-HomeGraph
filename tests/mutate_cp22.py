#!/usr/bin/env python3
"""CP-22 mutation harness -- can the answer gates actually go red?

The answer key is `tests/gold/FASIT-cp22.md`.

**This checkpoint needs its harness more than most.** Gates 1-4 pass by
construction: `search.build` does `DELETE FROM l4` and reinserts, so a
store that went A->B *is* built on B. The gold answer predicted that
before the code existed. A green suite therefore proves nothing on its
own, and the question the harness has to answer is not "does the code
work" but "would this gate notice if it stopped".

Two of the mutations below aim at the **controls** rather than at the
mechanism, because a control that cannot fail is the same failure one
level up: it makes gates 1-4 look protected when they are not.

Run:
    python3 tests/mutate_cp22.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the ranking itself ------------------------------------------------
    # The needle is the real clause, checked before it was written here:
    # `ORDER BY rank, path`. A needle that matches nothing applies nothing
    # and reports "killed" for a mutation that never happened -- this
    # project has been bitten by needles rotting before.
    ("search drops the tie-break, so equal scores come back in rowid order",
     "morpho_homegraph/search.py",
     " ORDER BY rank, path LIMIT ?",
     " ORDER BY rank LIMIT ?  # mutated: ties now fall to insertion order",
     "1 lexical answers identical after a rebuild, and present"),

    # -- the comparison ----------------------------------------------------
    # Aimed at the shared `compare`, which gate 1 and control gate 6 both
    # use. The first version of this mutation aimed at gate 5 and SURVIVED:
    # dropping a row changes membership, so a set comparison still caught
    # it, and nothing in the file tested a pure reordering. The survivor is
    # what produced control gate 6 in its current form.
    ("the comparison becomes a set comparison, so reordering is invisible",
     "tests/test_cp22.py",
     "        return a == b",
     "        return sorted(a or []) == sorted(b or [])  # mutated: set-like",
     "6 CONTROL: a reordering alone turns gate 1 red"),

    # -- the emptiness guard ----------------------------------------------
    ("the corpus stops producing multi-hit answers",
     "tests/test_cp22.py",
     'QUERIES = ("heron", "water", "shallow", "stone")',
     'QUERIES = ("kalligrafi_xyzzy",)  # mutated: nothing matches',
     "3 the answers are not empty, and some have several hits"),

    # -- the fusion actually running --------------------------------------
    #
    # This is the mutation that matters most: it recreates, deliberately,
    # the exact defect the first version of this file shipped with. Gate 2
    # passed 9/9 against `None == None` because `--fused` refused on an
    # unembedded project. If this mutation survives, the fix did not take.
    ("the project is never embedded, so --fused refuses and gate 2 "
     "compares two refusals",
     "tests/test_cp22.py",
     '    codes.append(cli(home, "embed", project_id(home)).returncode)',
     '    pass  # mutated: no embed, so --fused refuses',
     "2 fused answers identical after a rebuild, and present"),

    # -- fairness of the comparison ---------------------------------------
    #
    # The re-embed after `update` is what makes the two stores comparable.
    # Removing it reproduces this checkpoint's one real finding: a store
    # with 5 of 6 files embedded ranks the new file 4th, a fully embedded
    # one ranks it 2nd.
    ("the incremental store is not re-embedded after its corpus changed",
     "tests/test_cp22.py",
     '    codes.append(cli(inc_home, "embed", project_id(inc_home)).returncode)',
     '    pass  # mutated: stale vector coverage on one side',
     "2 fused answers identical after a rebuild, and present"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp22.py", prefix="mhg-mut-cp22-",
                 timeout=900))
