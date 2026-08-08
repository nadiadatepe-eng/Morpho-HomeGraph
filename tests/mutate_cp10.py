#!/usr/bin/env python3
"""Mutation test for CP-10 -- a merge that runs and orders wrongly.

None of these raise. A fusion that keeps only the lexical list answers every
lexical question perfectly; one that lets the best rank decide returns the same
files in a worse order; one that says "both" about everything prints a column
that is always right. Every mutation below leaves a command that exits 0.

**Three of them moved here from `mutate_cp9e.py` with the code.** CP-9E
measured a fusion that lived in its own tool; CP-10 moved it into the package,
and the mutations for the arithmetic followed it. One mechanism, one owner --
two copies of a needle drift, and the sweep then proves whichever copy it
happens to read.

Run:
    python3 tests/mutate_cp10.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the arithmetic (R1, R2), moved here from CP-9E --------------------
    ("the fusion constant is not the one CP-9E measured with",
     "morpho_homegraph/fusion.py",
     "RRF_K = 60",
     "RRF_K = 5  # mutated: another merge entirely",
     "1  RRF is 1/(k + rank), k = 60, rank from one, on known values"),

    ("rank is counted from zero",
     "morpho_homegraph/fusion.py",
     "        for position, path in enumerate(ranked, start=1):\n"
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "        for position, path in enumerate(ranked):  # mutated\n"
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "1  RRF is 1/(k + rank), k = 60, rank from one, on known values"),

    ("a list contributes its position rather than its reciprocal",
     "morpho_homegraph/fusion.py",
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "            score[path] = score.get(path, 0.0) + position  # mutated",
     "1  RRF is 1/(k + rank), k = 60, rank from one, on known values"),

    # -- ranks, never scores (R1) ------------------------------------------
    #
    # This is the shape the predecessor shipped: whoever is highest in *a*
    # list wins, so one strong list decides everything. It returns the same
    # files, in the order a score comparison would have produced.
    ("the best single rank decides instead of the sum",
     "morpho_homegraph/fusion.py",
     "            for path in sorted(score, key=lambda p: (-score[p], best[p], p))]",
     "            for path in sorted(score,  # mutated: one list decides\n"
     "                               key=lambda p: (best[p], p))]",
     "3  a file ranked in both lists beats one ranked first in only one"),

    ("the order is by path, so the merge decides nothing",
     "morpho_homegraph/fusion.py",
     "            for path in sorted(score, key=lambda p: (-score[p], best[p], p))]",
     "            for path in sorted(score)]  # mutated: alphabetical",
     "2  ranks decide, and the score order would have been different"),

    ("the best-rank tie-break is dropped, so the alphabet decides",
     "morpho_homegraph/fusion.py",
     "            for path in sorted(score, key=lambda p: (-score[p], best[p], p))]",
     "            for path in sorted(score, key=lambda p: (-score[p], p))]  # mutated",
     "4  an exact tie breaks by best rank, then by path, and holds still"),

    ("a path repeated inside one list is scored twice",
     "morpho_homegraph/fusion.py",
     "    lists = {name: list(dict.fromkeys(ranked))\n"
     "             for name, ranked in lists.items()}",
     "    pass  # mutated: the score counts it twice, found_by once",
     "4b a path repeated inside one list is counted once"),

    # -- the answer names its route (R4) -----------------------------------
    ("only the last list that found a file is recorded",
     "morpho_homegraph/fusion.py",
     "            found_by.setdefault(path, {})[name] = position",
     "            found_by[path] = {name: position}  # mutated",
     "5  every hit says which lists found it, and at which rank"),

    ("every hit claims both routes found it",
     "morpho_homegraph/fusion.py",
     '    return "both" if len(found_by) > 1 else next(iter(found_by))',
     '    return "both"  # mutated: the column is always right',
     "6  a file only the vector list found reaches the answer, and says so"),

    # -- deeper than the cut (R8) ------------------------------------------
    ("the lists are fetched exactly as deep as they are shown",
     "morpho_homegraph/fusion.py",
     "DEPTH = 50",
     "DEPTH = 10  # mutated: nothing can be lifted into the ten",
     "15a each list is fetched deeper than the ten that are shown"),

    ("the command asks for ten and merges those",
     "morpho_homegraph/cli.py",
     "                       search.content(store, args.query, limit=fusion.DEPTH)]",
     "                       search.content(store, args.query, limit=10)]  # mutated",
     "15b both retrieval calls in the command ask for that depth"),

    # -- half a fusion is not a fusion (R6) --------------------------------
    ("a project without vectors is answered lexically and called fused",
     "morpho_homegraph/cli.py",
     "            if not embedded:\n"
     '                print("REFUSED  nothing is embedded in this project yet (%d "\n'
     '                      "chunks in L2) -- morphofiles-graph embed %s. Half a "\n'
     '                      "fusion is not one" % (chunks, args.project),\n'
     "                      file=sys.stderr)\n"
     "                return 1",
     "            if False:  # mutated: answer with the list we have\n"
     "                pass",
     "10 without vectors the fused search refuses and names embed"),

    ("a missing lexical index is fused over anyway",
     "morpho_homegraph/cli.py",
     '        condition, indexed, expected = search.state(store)\n'
     '        if condition != "ok":\n'
     '            print("REFUSED  the lexical index is %s (%d rows, %d in L2) -- "',
     '        condition, indexed, expected = search.state(store)\n'
     "        if False:  # mutated: half an index will do\n"
     '            print("REFUSED  the lexical index is %s (%d rows, %d in L2) -- "',
     "11 without a built L4 the fused search refuses and names update"),

    # -- both lists reach the answer ---------------------------------------
    ("the vector list is dropped before the merge",
     "morpho_homegraph/cli.py",
     "            vector = [hit[\"path\"] for hit in\n"
     "                      embed.search(store, args.query, limit=fusion.DEPTH)]",
     "            vector = []  # mutated: lexical with extra steps",
     "14 a paraphrase the lexical layer cannot answer is answered fused"),

    ("the lexical list is dropped before the merge",
     "morpho_homegraph/cli.py",
     "            lexical = [hit[\"path\"] for hit in\n"
     "                       search.content(store, args.query, limit=fusion.DEPTH)]",
     "            lexical = []  # mutated: the vector list alone",
     "7  a file only the lexical list found is not lost in the merge"),

    # -- CP-9E's verdict is respected (R5) ---------------------------------
    #
    # The control that matters most: CP-9E measured class C into the band, so
    # nothing is switched on. A fusion that made itself the default would be
    # a decision taken by code rather than by a number.
    ("fusion quietly becomes the default search",
     "morpho_homegraph/cli.py",
     "    if args.fused:",
     "    if True:  # mutated: everyone gets the merge",
     "13 the plain search is unchanged and needs no vectors at all"),

    ("two answer modes at once are resolved instead of refused",
     "morpho_homegraph/cli.py",
     "    if args.semantic and args.fused:",
     "    if False:  # mutated: whichever branch is first wins",
     "13b asking for two answer modes at once is refused, not resolved"),

    # -- the coverage line (R9 of CP-9, one layer up) ----------------------
    ("the fused answer stops saying how much is embedded",
     "morpho_homegraph/cli.py",
     # CP-12 put `print(age)` between these two lines, and this needle went
     # from mutating the coverage line to matching nothing at all -- scored as
     # a survivor, in a report that said 0. `python3 tests/mutate.py` is the
     # check that now catches that in seconds.
     '    print("%d of %d chunks embedded" % (embedded, chunks))\n'
     "    print(age)\n"
     "    return 0\n"
     "\n"
     "\n"
     "def cmd_embed",
     "    print(age)  # mutated: no coverage line\n"
     "    return 0\n"
     "\n"
     "\n"
     "def cmd_embed",
     "16 the fused answer states the vector layer's coverage"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp10.py", prefix="mut10-", timeout=900))
