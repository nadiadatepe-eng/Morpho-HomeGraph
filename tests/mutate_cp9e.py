#!/usr/bin/env python3
"""Mutation test for CP-9E -- a measurement that runs and reports a number.

Nothing here raises. A validator that accepts everything still prints a table;
a recall that counts chunks still prints a fraction; a set left inside its own
corpus prints a *better* fraction. Every mutation below leaves a tool that
exits 0 with a figure somebody would put in a document.

Two are the controls. "the validator accepts anything" is what gates 4, 6 and
8 would be satisfied by if 5 and 7 were not there, and "every pair scores a
hit" is what the whole table would be satisfied by without gate 16.

"rank counted from zero" is the one worth reading twice. It survived the
first sweep and looked like an equivalent mutant: at k = 60 a uniform shift
changes every score and almost never the order, so no ordering gate can see
it. That was true of the *gate*, not of the code -- the fix was to expose the
scores and pin them, and the mutation dies on arithmetic instead. An
"equivalent mutant" is sometimes a test that only looks at the wrong end.

Run:
    python3 tests/mutate_cp9e.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the set can be scored at all (R6) ---------------------------------
    ("a target the corpus does not hold is tolerated",
     "tools/cp9e_eval.py",
     "            elif not in_corpus(target):",
     "            elif False:  # mutated: score it anyway",
     "5  a target the corpus does not have refuses the set, not scores 0"),

    ("the validator accepts anything",
     "tools/cp9e_eval.py",
     "    problems = []",
     "    return []  # mutated: nothing is ever wrong\n"
     "    problems = []",
     "5  a target the corpus does not have refuses the set, not scores 0"),

    # -- the class is a property, not my opinion (R3) ----------------------
    ("a paraphrase the lexical layer finds is left in class A",
     "tools/cp9e_eval.py",
     '        if pair.get("class") in ("A", "C") and hits:',
     "        if False:  # mutated: my label is the label",
     "7  a class A pair that FTS does find is refused as mislabelled"),

    ("a class B question that misses its own target is left in class B",
     "tools/cp9e_eval.py",
     '        if pair.get("class") == "B" and not found(hits, targets):',
     "        if False:  # mutated: it is lexical because I said so",
     "8  every class B question finds its own target in the ten, and one "
     "that does not is refused"),

    ("finding any file at all is enough to call a pair lexical",
     "tools/cp9e_eval.py",
     '        if pair.get("class") == "B" and not found(hits, targets):',
     '        if pair.get("class") == "B" and not hits:  # mutated',
     "8  every class B question finds its own target in the ten, and one "
     "that does not is refused"),

    ("a class nobody defined is scored anyway",
     "tools/cp9e_eval.py",
     '        if pair.get("class") not in ("A", "B", "C"):',
     "        if False:  # mutated: any label will do",
     "8b a pair labelled with a class outside A, B and C is refused"),

    # -- the set is big enough to read the band (threshold, 08-05) ---------
    ("twenty per class is not required",
     "tools/cp9e_eval.py",
     '        if counts.get(name, 0) < 20:',
     "        if False:  # mutated: any size will do",
     "1  at least 60 pairs and at least 20 in each class, and a set "
     "with fewer is refused"),

    ("a duplicate question is counted twice",
     "tools/cp9e_eval.py",
     "        if query in seen:",
     "        if False:  # mutated: ask the same thing twice",
     "2  no duplicate question, and a repeated one is refused"),

    ("an absolute path in a target is accepted",
     "tools/cp9e_eval.py",
     "            if os.path.isabs(target):",
     "            if False:  # mutated: this machine's layout is fine",
     "3  every target is project-relative, and an absolute one is refused"),

    # -- recall counts files, and stops at ten (R5) ------------------------
    ("the same file counts once per chunk",
     "tools/cp9e_eval.py",
     "        if hit[\"path\"] not in ranked:\n"
     "            ranked.append(hit[\"path\"])",
     "        if True:  # mutated: twelve chunks, twelve entries\n"
     "            ranked.append(hit[\"path\"])",
     "10 recall counts files: repeated chunks of one file rank once"),

    ("the cut is the whole list instead of ten",
     "tools/cp9e_eval.py",
     "    return any(target in ranked[:cut] for target in targets)",
     "    return any(target in ranked for target in targets)  # mutated",
     "11 the cut is ten: a target at rank eleven is a miss"),

    ("only the first target of a pair counts",
     "tools/cp9e_eval.py",
     "    return any(target in ranked[:cut] for target in targets)",
     "    return targets[0] in ranked[:cut]  # mutated: one answer only",
     "12 a pair with several targets is found by any one of them"),

    # -- the fusion (R9) ---------------------------------------------------
    ("the fusion constant is not the one it was measured for",
     "tools/cp9e_eval.py",
     "RRF_K = 60",
     "RRF_K = 5  # mutated: another merge entirely",
     "13 RRF is 1/(k + rank) with k = 60, rank counted from one"),

    ("rank is counted from zero",
     "tools/cp9e_eval.py",
     "        for position, path in enumerate(ranked, start=1):\n"
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "        for position, path in enumerate(ranked):  # mutated\n"
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "13 RRF is 1/(k + rank) with k = 60, rank counted from one"),

    ("the fusion keeps only the lexical list",
     "tools/cp9e_eval.py",
     "        fused = rrf([lexical, vector])",
     "        fused = lexical  # mutated: the vector list is dropped",
     "14b a file only the vector list found reaches the fused answer"),

    ("a list contributes its position rather than its reciprocal",
     "tools/cp9e_eval.py",
     "            score[path] = score.get(path, 0.0) + 1.0 / (k + position)",
     "            score[path] = score.get(path, 0.0) + position  # mutated",
     "13 RRF is 1/(k + rank) with k = 60, rank counted from one"),

    ("an empty second list reverses the first",
     "tools/cp9e_eval.py",
     "    return sorted(score, key=lambda p: (-score[p], best[p], p))",
     "    return sorted(score, key=lambda p: (score[p], best[p], p))  # mutated",
     "14 an empty vector list leaves the lexical order untouched"),

    # -- the report (R2), and the control that stops it being decoration ---
    ("the three classes are blended into one total",
     "tools/cp9e_eval.py",
     '        counters = tally.setdefault(pair["class"],',
     '        counters = tally.setdefault("all",  # mutated: one number',
     "15 the report is three numbers per class and no blended total"),

    ("every pair is scored as a hit",
     "tools/cp9e_eval.py",
     '               "vector": found(vector, pair["targets"]),',
     '               "vector": True,  # mutated: the layer always wins',
     "16 a perfect run scores every pair and an all-miss run scores none"),

    # -- the set is not part of the corpus it grades (R7) ------------------
    #
    # The one nobody would notice: with the cut removed every question matches
    # verbatim in the file that holds it, and every number goes *up*.
    ("the answer sheet is left inside the exam",
     "tools/cp9e_eval.py",
     '                                "DELETE FROM content WHERE path LIKE ?",',
     '                                "SELECT ? -- mutated: keep the questions",',
     "9  no file holding questions is part of the corpus they grade"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp9e.py", prefix="mut9e-", timeout=900))
