#!/usr/bin/env python3
"""CP-9E: does the semantic layer actually find anything?

The answer key is `tests/gold/FASIT-cp9e.md` and the threshold was locked
before this file existed (`TODO.md` § CP-9E, `7e75a73`). This tool only
produces the numbers; it does not decide what they mean.

**Three numbers per class, never one blended average.** Class A is the true
paraphrase, where FTS scores zero by construction; class B is lexical, where
FTS is at the ceiling and the only question is whether fusion *loses*
anything; class C is the language crossing. M-5 is why a single average is
forbidden: +47 % and +0 % disappeared into "roughly 15-23 %".

**The set is cut out of the corpus it grades (R7).** The questions live in a
file inside the repository, and the repository *is* the corpus -- so without
the cut every question would match verbatim in the file holding it. An answer
sheet inside the exam.

**The fusion is imported from the package (CP-10).** It was written here
first, because CP-9E ran before the product had one -- and a tool that owns
what it measures grades itself. These numbers are about the merge that ships.

    python3 tools/cp9e_eval.py [path-to-repo]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SET_PATH = os.path.join(REPO, "tests", "gold", "eval-cp9e.json")

# **The fusion is imported, not owned.** It lived here first, because CP-9E ran
# before the product had one -- and a tool that owns the thing it measures
# grades itself. CP-10 moved it into the package, so these numbers are about
# the merge that ships. `RRF_K`, `DEPTH` and `CUT` come from the same place for
# the same reason: a constant copied here could drift from the one in use, and
# then the recall figure would be about neither.
from morpho_homegraph.fusion import (CUT, DEPTH, RRF_K, fuse,  # noqa: E402
                                     rrf_scores)

# Every file that holds questions, cut out of the corpus before it is graded
# (R7). The set is the obvious one. **The harness is the one that is not:** it
# plants questions of its own to prove the validator can refuse, and those
# literals are text in a file inside the repository, so the corpus answers
# them perfectly. Found 2026-08-05 by a gate that could not fail -- a planted
# nonsense question was found lexically, in the file that planted it.
QUESTION_FILES = ("eval-cp9e.json", "test_cp9e.py")


class BrokenSet(RuntimeError):
    """The set is wrong, so no score from it would mean anything (R6)."""


# -- the set ---------------------------------------------------------------

def load(path: str = SET_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate(pairs: list[dict], in_corpus, fts_hits) -> list[str]:
    """Every reason this set cannot be scored. Empty list means it can.

    Refusal, never a zero: a pair pointing at a file the corpus does not hold
    looks exactly like a search layer that cannot find it, and those two are
    fixed in completely different places.
    """
    problems = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for pair in pairs:
        who = pair.get("id", "?")
        counts[pair.get("class", "?")] = counts.get(pair.get("class", "?"), 0) + 1
        # A class nobody defined would be scored and printed as a fourth row,
        # and the per-class minimums would still be satisfied by A, B and C.
        # A label that invents its own class is a label that answers to no
        # threshold.
        if pair.get("class") not in ("A", "B", "C"):
            problems.append("%s: class %r is not one of A, B, C"
                            % (who, pair.get("class")))
        query = pair.get("query", "")
        if query in seen:
            problems.append("%s: duplicate query %r" % (who, query))
        seen.add(query)
        targets = pair.get("targets") or []
        if not targets:
            problems.append("%s: no target at all" % who)
        for target in targets:
            if os.path.isabs(target):
                problems.append("%s: absolute path %s" % (who, target))
            elif not in_corpus(target):
                problems.append("%s: %s is not in the corpus" % (who, target))
        # The mechanical half of the class definition (R3): A and C are
        # "the lexical layer scores zero here", not "I think this is a
        # paraphrase". A pair that does not have that property is mislabelled,
        # and a mislabelled pair would be scored as though it did.
        hits = fts_hits(query)
        if pair.get("class") in ("A", "C") and hits:
            problems.append("%s: class %s but FTS finds %d (%s)"
                            % (who, pair["class"], len(hits), hits[0]))
        # B is the mirror image, and "FTS finds *something*" is not it: a
        # question whose words land in an unrelated file is not a lexical pair,
        # and counting it as one would quietly shrink the set that "the fusion
        # loses none of them" is measured over. The target has to be in the
        # ten that are scored.
        if pair.get("class") == "B" and not found(hits, targets):
            problems.append("%s: class B but FTS does not find %s in ten (%s)"
                            % (who, targets[0] if targets else "-",
                               hits[0] if hits else "nothing at all"))
    for name in ("A", "B", "C"):
        if counts.get(name, 0) < 20:
            problems.append("class %s has %d pairs, the threshold needs 20"
                            % (name, counts.get(name, 0)))
    if len(pairs) < 60:
        problems.append("%d pairs, the threshold needs 60" % len(pairs))
    return problems


# -- scoring ---------------------------------------------------------------

def rank_files(hits: list[dict], limit: int = DEPTH) -> list[str]:
    """A layer's hits as a ranked list of *files*, best first, no repeats.

    R5: the user is looking for a file. Counting chunks would punish the
    layer for a long file being cut into twelve and reward it for a short one
    being cut into one.
    """
    ranked = []
    for hit in hits:
        if hit["path"] not in ranked:
            ranked.append(hit["path"])
        if len(ranked) >= limit:
            break
    return ranked


def found(ranked: list[str], targets: list[str], cut: int = CUT) -> bool:
    """Is any acceptable target inside the first `cut` files? (R4)"""
    return any(target in ranked[:cut] for target in targets)


def score(pairs: list[dict], fts_of, vector_of) -> dict:
    """`{class: {layer: (hits, total)}}` plus the per-pair detail.

    The vector list is asked *independently*, never as a rerank of the lexical
    shortlist (R8). On class A that shortlist is empty, so a reranker cannot
    score above zero there however good the model is -- which is exactly what
    the predecessor measured as 1 of 28.
    """
    tally: dict[str, dict[str, list[int]]] = {}
    detail = []
    for pair in pairs:
        lexical = fts_of(pair["query"])
        vector = vector_of(pair["query"])
        fused = [hit["path"] for hit in
                 fuse({"lexical": lexical, "vector": vector})]
        row = {"id": pair["id"], "class": pair["class"],
               "fts": found(lexical, pair["targets"]),
               "vector": found(vector, pair["targets"]),
               "fusion": found(fused, pair["targets"]),
               # What the layer *did* answer, kept for the misses. A bare
               # 0.33 would be read as "the model cannot cross languages",
               # and this is what tells that apart from "the Norwegian prose
               # about the code outranks the code".
               "top3": vector[:3]}
        detail.append(row)
        counters = tally.setdefault(pair["class"],
                                    {"fts": [0, 0], "vector": [0, 0],
                                     "fusion": [0, 0]})
        for layer in ("fts", "vector", "fusion"):
            counters[layer][0] += 1 if row[layer] else 0
            counters[layer][1] += 1
    return {"tally": tally, "detail": detail}


# -- the corpus ------------------------------------------------------------

def build_corpus(root: str, with_vectors: bool):
    """Build L2, L4 and (optionally) the vectors over `root`. Returns the db.

    Every file holding questions is dropped from L2 before the searchable
    layers are built (R7, and `QUESTION_FILES` for why that is two files and
    not one). It is done here rather than by narrowing the scope, so the
    corpus is otherwise exactly what a user would get.
    """
    from morpho_homegraph import content, embed, graph, identity, scope, search
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
                    with store.writing() as sql:
                        for name in QUESTION_FILES:
                            sql.execute(
                                "DELETE FROM content WHERE path LIKE ?",
                                ("%" + name,))
                        sql.commit()
                    graph.build(store, scope_root=root)
                    search.build(store)
                    if with_vectors:
                        embed.build(store)
            finally:
                guard.release()
    finally:
        guard0.release()
    return db_path(project_id)


def readers(store, root: str):
    """`(in_corpus, fts_of, vector_of)` -- everything the scoring needs.

    Paths are project-relative on the way in and out, so the set never carries
    this machine's layout (R10, CP-PUB).
    """
    from morpho_homegraph import embed, search

    def absolute(relative: str) -> str:
        return os.path.join(root, relative)

    def relative(path: str) -> str:
        return os.path.relpath(path, root)

    def in_corpus(target: str) -> bool:
        return bool(store.db.execute(
            "SELECT 1 FROM content WHERE path = ?",
            (absolute(target),)).fetchone())

    def fts_of(query: str) -> list[str]:
        return [relative(p) for p in
                rank_files(search.content(store, query, limit=DEPTH * 2))]

    def vector_of(query: str) -> list[str]:
        return [relative(p) for p in
                rank_files(embed.search(store, query, limit=DEPTH * 2))]

    return in_corpus, fts_of, vector_of


def report(result: dict) -> None:
    print("\n%-6s %-8s %-8s %-8s %s" % ("class", "FTS", "vector", "fusion",
                                        "what the threshold asks"))
    asks = {"A": "vector/fusion >= 0.50",
            "B": "fusion loses none of FTS",
            "C": "vector/fusion >= 0.50"}
    for name in sorted(result["tally"]):
        row = result["tally"][name]
        print("%-6s %-8s %-8s %-8s %s"
              % (name,
                 "%d/%d" % tuple(row["fts"]),
                 "%d/%d" % tuple(row["vector"]),
                 "%d/%d" % tuple(row["fusion"]),
                 asks.get(name, "")))
    lost = [d["id"] for d in result["detail"] if d["fts"] and not d["fusion"]]
    print("\nfusion loses %d of the pairs FTS finds%s"
          % (len(lost), (": " + ", ".join(lost)) if lost else ""))
    missed = [d["id"] for d in result["detail"]
              if d["class"] in ("A", "C") and not d["vector"]]
    print("vector misses %d of %d in A and C%s"
          % (len(missed),
             sum(1 for d in result["detail"] if d["class"] in ("A", "C")),
             (": " + ", ".join(missed)) if missed else ""))

    # The diagnosis for class C, which decides what a low number *means*.
    # This corpus says the same things twice: English in the code, Norwegian
    # in `TODO.md` and the answer keys. A Norwegian question that lands on the
    # Norwegian prose has crossed nothing, and counting it as a miss (which
    # the set does, on purpose) is only honest if the reason is visible.
    prose = [d for d in result["detail"] if d["class"] == "C" and not d["vector"]]
    landed = [d["id"] for d in prose
              if any(p == "TODO.md" or p.startswith("tests/gold/FASIT")
                     for p in d["top3"])]
    print("of the %d class C misses, %d put Norwegian prose in the top three%s"
          % (len(prose), len(landed), (": " + ", ".join(landed)) if landed else ""))


def main(argv: list[str]) -> int:
    from morpho_homegraph.store import Store

    root = os.path.abspath(argv[1] if len(argv) > 1 else REPO)
    data = load()
    pairs = data["pairs"]
    with tempfile.TemporaryDirectory(prefix="mhg-cp9e-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        db = build_corpus(root, with_vectors=True)
        with Store(db, read_only=True) as store:
            in_corpus, fts_of, vector_of = readers(store, root)
            problems = validate(pairs, in_corpus, fts_of)
            if problems:
                print("REFUSED  the set cannot be scored:", file=sys.stderr)
                for problem in problems:
                    print("  %s" % problem, file=sys.stderr)
                return 2
            print("%d pairs over %d files, set written %s"
                  % (len(pairs),
                     store.db.execute(
                         "SELECT COUNT(*) FROM content WHERE text IS NOT NULL"
                     ).fetchone()[0],
                     data["written"]))
            report(score(pairs, fts_of, vector_of))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
