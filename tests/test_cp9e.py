#!/usr/bin/env python3
"""CP-9E -- the labelled set, and the scoring that turns it into a number.

The answer key is `tests/gold/FASIT-cp9e.md`, written before the set and
before this file (`9b70812`). Gate numbers below are that document's.

The failure this checkpoint prevents is a *number*. A set that grades itself,
a pair pointing at a file the corpus never had, a class label that is my
opinion rather than a property, a recall that counts chunks -- each of them
produces a figure that looks like a measurement and is not one. So gates 5, 7
and 16 are the controls: without them, "the set is refused" and "the set is
perfect" are both satisfied by a validator that always says the same thing.

The corpus is built here without vectors: everything below is about the set
and the arithmetic, and embedding 1091 chunks to check a JSON file would make
the mutation sweep an hour long.

Run:
    python3 tests/test_cp9e.py
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from report import reporter  # noqa: E402

import cp9e_eval as E  # noqa: E402
from morpho_homegraph.store import Store  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(60)


# -- 10, 11, 12, 13, 14, 15, 16: the arithmetic, on synthetic lists ---------

def gates_scoring():
    """No corpus needed, and that is the point -- the maths is checkable."""
    hits = [{"path": "a.py"}, {"path": "a.py"}, {"path": "b.py"},
            {"path": "a.py"}, {"path": "c.py"}]
    check("10 recall counts files: repeated chunks of one file rank once",
          E.rank_files(hits) == ["a.py", "b.py", "c.py"],
          "%s" % E.rank_files(hits))

    eleven = ["f%02d.py" % i for i in range(1, 12)]
    check("11 the cut is ten: a target at rank eleven is a miss",
          E.found(eleven, ["f10.py"]) and not E.found(eleven, ["f11.py"]),
          "rank 10 %s, rank 11 %s" % (E.found(eleven, ["f10.py"]),
                                      E.found(eleven, ["f11.py"])))

    check("12 a pair with several targets is found by any one of them",
          E.found(["b.py"], ["a.py", "b.py"])
          and not E.found(["z.py"], ["a.py", "b.py"]),
          "either %s, neither %s" % (E.found(["b.py"], ["a.py", "b.py"]),
                                     E.found(["z.py"], ["a.py", "b.py"])))

    # 13: a known-answer test on the *scores*, not on the winner. `x` is
    # second in both lists, so 2/62; `a` is first in one and absent from the
    # other, so 1/61. Pinning the order alone was not enough -- a uniform
    # shift of every rank changes every score and almost never the sequence,
    # and the mutation for it survived until the scores were exposed.
    fused = E.rrf([["a", "x", "b"], ["c", "x", "d"]])
    scores = E.rrf_scores([["a", "x", "b"], ["c", "x", "d"]])
    check("13 RRF is 1/(k + rank) with k = 60, rank counted from one",
          E.RRF_K == 60 and fused[0] == "x"
          and abs(scores["x"] - 2.0 / 62) < 1e-12
          and abs(scores["a"] - 1.0 / 61) < 1e-12,
          "x=%.6f (want %.6f), a=%.6f (want %.6f)"
          % (scores["x"], 2.0 / 62, scores["a"], 1.0 / 61))

    # 14: the control that stops 13 from being satisfied by "return the first
    # list". An empty second list must leave the first one's order alone.
    only_fts = ["a", "b", "c"]
    check("14 an empty vector list leaves the lexical order untouched",
          E.rrf([only_fts, []]) == only_fts,
          "%s" % E.rrf([only_fts, []]))

    # 14b: the other direction, and the one R8 is about. A file only the
    # vector list knows must reach the fused answer -- otherwise "fusion" is
    # the lexical list with extra steps, which is what a reranker over an
    # empty shortlist amounts to on class A.
    only_vector = E.score(
        [{"id": "P3", "class": "A", "query": "q", "targets": ["v.py"]}],
        lambda q: ["z.py"], lambda q: ["v.py"])
    cell = only_vector["tally"].get("A", {})
    check("14b a file only the vector list found reaches the fused answer",
          cell.get("fusion") == [1, 1] and cell.get("fts") == [0, 1],
          "fusion %s, fts %s" % (cell.get("fusion"), cell.get("fts")))

    # 15 and 16: three numbers per class, and a scorer that can produce both
    # ends. Without 16, every gate above passes for a scorer that always says
    # "miss" -- and the report would read as a measured zero.
    pairs = [{"id": "P1", "class": "A", "query": "q1", "targets": ["a.py"]},
             {"id": "P2", "class": "B", "query": "q2", "targets": ["b.py"]}]
    perfect = E.score(pairs, lambda q: ["a.py", "b.py"],
                      lambda q: ["a.py", "b.py"])
    nothing = E.score(pairs, lambda q: ["z.py"], lambda q: ["z.py"])
    layers_ok = all(sorted(row) == ["fts", "fusion", "vector"]
                    for row in perfect["tally"].values())
    check("15 the report is three numbers per class and no blended total",
          sorted(perfect["tally"]) == ["A", "B"] and layers_ok,
          "%s" % {k: sorted(v) for k, v in perfect["tally"].items()})
    # The detail reads the tally through `.get`, because a mutation that
    # blends the classes leaves no "A" key -- and a harness that raises here
    # dies before any gate says no. A crash names no gate.
    check("16 a perfect run scores every pair and an all-miss run scores none",
          all(v[0] == v[1] for row in perfect["tally"].values()
              for v in row.values())
          and all(v[0] == 0 for row in nothing["tally"].values()
                  for v in row.values()),
          "perfect %s / nothing %s"
          % (perfect["tally"].get("A", {}).get("vector"),
             nothing["tally"].get("A", {}).get("vector")))


# -- 1, 2, 3: the set on its own -------------------------------------------

def gates_shape(pairs):
    """The set's shape -- asserted *through the validator*, both ways.

    Every gate here says two things: the real set has the property, and a set
    without it is refused. Only the first half was there at the start, and the
    sweep killed nothing: a harness that re-implements the rule tests the set
    and leaves the code that has to enforce it untouched. Measured 2026-08-05,
    four mutations survived on exactly that.
    """
    # No corpus needed for these: every target is in the set, and the two
    # readers below answer without one.
    everything = lambda _target: True          # noqa: E731 -- in corpus
    lexical = lambda _query: ["x.py"]          # noqa: E731 -- FTS finds one

    counts = {}
    for pair in pairs:
        counts[pair["class"]] = counts.get(pair["class"], 0) + 1
    short = [p for p in pairs if p["class"] != "C"][:40] + [
        p for p in pairs if p["class"] == "C"][:19]
    check("1  at least 60 pairs and at least 20 in each class, and a set "
          "with fewer is refused",
          len(pairs) >= 60 and all(counts.get(c, 0) >= 20 for c in "ABC")
          and any("class C has 19" in p
                  for p in E.validate(short, everything, lexical)),
          "%d pairs, %s" % (len(pairs), counts))

    queries = [p["query"] for p in pairs]
    doubled = copy.deepcopy(pairs) + [dict(pairs[0], id="X03")]
    check("2  no duplicate question, and a repeated one is refused",
          len(set(queries)) == len(queries)
          and all(p.get("targets") for p in pairs)
          and any("duplicate query" in p
                  for p in E.validate(doubled, everything, lexical)),
          "%d unique of %d" % (len(set(queries)), len(queries)))

    absolute = [t for p in pairs for t in p["targets"] if os.path.isabs(t)]
    planted = copy.deepcopy(pairs)
    planted[0] = dict(planted[0], targets=["/home/somebody/project/file.py"])
    check("3  every target is project-relative, and an absolute one is refused",
          not absolute
          and any("absolute path" in p
                  for p in E.validate(planted, everything, lexical)),
          "%d absolute: %s" % (len(absolute), absolute[:2]))


# -- 4, 5, 6, 7, 8, 9: the set against the real corpus ----------------------

def gates_against_corpus(store, pairs):
    in_corpus, fts_of, _vector_of = E.readers(store, REPO)

    check("4  every target is a file the corpus actually holds",
          not [p for p in E.validate(pairs, in_corpus, fts_of)
               if "not in the corpus" in p],
          "%s" % ([p for p in E.validate(pairs, in_corpus, fts_of)
                   if "not in the corpus" in p] or "none missing"))

    # 5: the control. A validator that never refuses would satisfy gate 4 for
    # any set at all, and the number it produced would look measured.
    # The planted query is unique on purpose: reusing one of the set's own
    # would also trip the duplicate rule, and the gate would then be green
    # for a reason it does not claim.
    planted = copy.deepcopy(pairs) + [
        {"id": "X01", "class": "B", "query": "busy timeout milliseconds",
         "targets": ["morpho_homegraph/no_such_module.py"]}]
    said = [p for p in E.validate(planted, in_corpus, fts_of)
            if "not in the corpus" in p]
    check("5  a target the corpus does not have refuses the set, not scores 0",
          bool(said), "%s" % (said[:1] or "accepted!"))

    # 6: the mechanical half of the class definition. A and C are not "what I
    # think is a paraphrase" -- they are "the lexical layer scores zero", and
    # that is checked against the index rather than asserted in prose.
    mislabelled = [p for p in E.validate(pairs, in_corpus, fts_of)
                   if "but FTS finds" in p]
    check("6  every class A and C question really does score zero on FTS",
          not mislabelled, "%s" % (mislabelled[:2] or "none mislabelled"))

    # 7: the other control. Without it, gate 6 passes for a validator that
    # never looks at the index.
    planted = copy.deepcopy(pairs)
    planted[0] = {**planted[0], "id": "X02", "class": "A",
                  "query": "busy timeout milliseconds"}
    said = [p for p in E.validate(planted, in_corpus, fts_of)
            if "but FTS finds" in p]
    check("7  a class A pair that FTS does find is refused as mislabelled",
          bool(said), "%s" % (said[:1] or "accepted!"))

    # 8, with its own control for the same reason as 5 and 7: an assertion
    # that a complaint is *absent* is satisfied by deleting the complaint.
    # The plant finds files -- just not its own target, which is the case
    # "FTS finds something" would have let through.
    planted = copy.deepcopy(pairs) + [
        {"id": "X04", "class": "B", "query": "busy timeout milliseconds",
         "targets": ["morpho_homegraph/scan.py"]}]
    check("8  every class B question finds its own target in the ten, and one "
          "that does not is refused",
          not [p for p in E.validate(pairs, in_corpus, fts_of)
               if "class B but FTS does not find" in p]
          and any("class B but FTS does not find" in p
                  for p in E.validate(planted, in_corpus, fts_of)),
          "%s" % ([p for p in E.validate(pairs, in_corpus, fts_of)
                   if "class B but FTS does not find" in p] or "all answer"))

    # 8b: a class nobody defined answers to no threshold, and would still be
    # printed as a fourth row of the table.
    planted = copy.deepcopy(pairs) + [
        {"id": "X05", "class": "D", "query": "an unclassifiable question",
         "targets": ["morpho_homegraph/store.py"]}]
    check("8b a pair labelled with a class outside A, B and C is refused",
          any("is not one of A, B, C" in p
              for p in E.validate(planted, in_corpus, fts_of)),
          "%s" % (E.validate(planted, in_corpus, fts_of)[:1] or "accepted!"))

    # 9: R7, the answer sheet inside the exam. The questions live in a file in
    # this repository and the repository is the corpus, so without the cut
    # every question would match verbatim in the file that holds it.
    rows = {name: store.db.execute(
        "SELECT COUNT(*) FROM content WHERE path LIKE ?",
        ("%" + name,)).fetchone()[0] for name in E.QUESTION_FILES}
    others = store.db.execute(
        "SELECT COUNT(*) FROM content WHERE text IS NOT NULL").fetchone()[0]
    check("9  no file holding questions is part of the corpus they grade",
          not any(rows.values()) and others > 50,
          "%s, %d files in the corpus" % (rows, others))


def main() -> int:
    pairs = E.load()["pairs"]
    gates_scoring()
    gates_shape(pairs)
    with tempfile.TemporaryDirectory(prefix="mhg-cp9e-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        db = E.build_corpus(REPO, with_vectors=False)
        with Store(db, read_only=True) as store:
            gates_against_corpus(store, pairs)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp9e():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
