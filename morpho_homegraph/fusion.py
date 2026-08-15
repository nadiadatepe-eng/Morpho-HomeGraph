#!/usr/bin/env python3
"""CP-10: merging the lexical list and the semantic one.

The answer key is `tests/gold/FASIT-cp10.md`, written before this module.

**Ranks, never scores.** `bm25()` is index-relative and negative; cosine is 0
to 1 and positive. Nothing converts between them that is not invented, so the
only honest common ground is the position each layer put a file in. The
predecessor's trap is recorded in `TODO.md`: a raw score of -999 put the same
node first whatever happened, and a fusion that compared scores passed its
gate because no test case made the two schemes disagree.

**This is the fusion CP-9E measured.** It was written in the measuring tool
first, because the product had none -- and a tool that owns the thing it
measures grades itself. It lives here now and `tools/cp9e_eval.py` imports it,
so the numbers in `TODO.md` § CP-9E are about the code that ships. `k = 60` is
the value those numbers were produced with, which is what makes it a constant
here rather than a knob ([[rrf-k60-one-list-decides-everything]]).

**A hit says which lists found it.** Two routes to one answer, and an answer
that does not say which one ran leaves every mutation that switches one off
alive -- measured in CP-5 and written down as its own rule.
"""
from __future__ import annotations

# Reciprocal rank fusion is Cormack, Clarke and Buettcher, "Reciprocal Rank
# Fusion outperforms Condorcet and individual Rank Learning Methods" (SIGIR
# 2009). The method is theirs; k = 60 is their reported value and is kept
# rather than tuned, because a k chosen against our own 63-pair eval set would
# fit that set instead of measuring the merge.
#
# Reciprocal rank fusion's constant. At 60, one list's rank-1 contribution is
# 1/61 while two neighbouring ranks differ by 0.000264 -- so any list that is
# added decides everything it touches, and a changed k makes CP-9E's three
# verdicts numbers about a different merge.
RRF_K = 60

# How deep each layer is asked, and how many are shown. Deeper than the cut on
# purpose: a file at rank twelve in one list can be lifted into the top ten by
# the other, and that lift is the whole mechanism. Two lists already trimmed to
# ten would have thrown it away before the merge started.
DEPTH = 50
CUT = 10


def rrf_scores(lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """`{path: score}` where score is the sum of `1 / (k + rank)`, rank from 1.

    Separate from the ordering below so a gate can pin the *arithmetic* rather
    than the winner. An order test is nearly blind here: shifting every rank
    by one changes every score and almost never the sequence, so a rank
    counted from zero looked like an equivalent mutant until this function
    existed (CP-9E, 2026-08-05).
    """
    score: dict[str, float] = {}
    for ranked in lists:
        for position, path in enumerate(ranked, start=1):
            score[path] = score.get(path, 0.0) + 1.0 / (k + position)
    return score


def fuse(lists: dict[str, list[str]], k: int = RRF_K) -> list[dict]:
    """Merge named ranked lists. Best first, and each hit says where it came from.

    `lists` is `{"lexical": [path, ...], "vector": [path, ...]}`; the names are
    carried through to `found_by` so the answer can name the route that
    produced it.

    **Ties break by best rank, then by path (R7), and the first step is not
    decoration.** It was removed here once, on the argument that an equal sum
    implies an equal multiset of ranks. That argument is false, and the
    counterexample is small: ranks 3 and 45 give `1/63 + 1/105`, ranks 10 and
    30 give `1/70 + 1/90`, and both are exactly `8/315`. The file that was
    third somewhere should win that, not the one whose name sorts first --
    reasoning about the arithmetic instead of computing it is what put the
    wrong rule in this docstring for an afternoon.

    **A path repeated inside one list counts once.** Otherwise its score would
    carry two contributions while `found_by` recorded one rank, and the number
    and the provenance would disagree about the same hit.
    """
    lists = {name: list(dict.fromkeys(ranked))
             for name, ranked in lists.items()}
    score = rrf_scores(list(lists.values()), k)
    found_by: dict[str, dict[str, int]] = {}
    best: dict[str, int] = {}
    for name, ranked in lists.items():
        for position, path in enumerate(ranked, start=1):
            found_by.setdefault(path, {})[name] = position
            best[path] = min(best.get(path, position), position)
    return [{"path": path, "score": score[path], "found_by": found_by[path]}
            for path in sorted(score, key=lambda p: (-score[p], best[p], p))]


def route(found_by: dict[str, int]) -> str:
    """`lexical`, `vector` or `both` -- the one word a reader needs."""
    return "both" if len(found_by) > 1 else next(iter(found_by))
