#!/usr/bin/env python3
"""Mutation test for CP-5 -- L3, and the claim that a `partial` answer means something.

The defect this layer exists to prevent does not crash and does not change a
count: it is an answer that looks as certain as any other while resting on a
guess. So the mutations below mostly make the graph *more* confident -- a note
that always says complete, a derived edge promoted to stated, a default
`method` that lets an unexamined edge pass for a stated one.

The other half aims at identity. A graph keyed on paths behaves identically to
one keyed on content until two files hold the same bytes, which is the only
reason the fixture has twins in it.

Run:
    python3 tests/mutate_cp5.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the provenance verdict -------------------------------------------
    #
    # The reassuring answer is the invisible one: nothing fails, no count
    # moves, and every answer reads as if a file had stated it.
    ("the note never reports a guess",
     "morpho_homegraph/graph.py",
     "        if edge[\"confidence\"] < 1.0 or edge[\"method\"] not in STATED:\n"
     "            return PROV_PARTIAL",
     "        if False:  # mutated: everything looks stated\n"
     "            return PROV_PARTIAL",
     "8  one derived edge makes the answer partial"),

    ("the note always reports a guess",
     "morpho_homegraph/graph.py",
     "    return PROV_COMPLETE",
     "    return PROV_PARTIAL  # mutated: nothing is ever certain",
     "16 a note that cannot say complete is caught"),

    # Confidence alone, without the method check: a derived edge given 1.0 by
    # a future caller would read as stated.
    ("only the number is checked, not how the edge came to exist",
     "morpho_homegraph/graph.py",
     "        if edge[\"confidence\"] < 1.0 or edge[\"method\"] not in STATED:",
     "        if edge[\"confidence\"] < 1.0:  # mutated",
     "8c the method decides, not only the number"),

    ("a resolved-by-name link is recorded as stated",
     "morpho_homegraph/graph.py",
     "                    edge(db, sha, target, method=method,\n"
     "                         confidence=DERIVED_CONFIDENCE)",
     "                    edge(db, sha, target, method=MD_WIKILINK_PATH,"
     "  # mutated\n"
     "                         confidence=1.0)",
     "8b a.md's own answer is partial -- its one edge is derived"),

    # -- the edge contract -------------------------------------------------
    ("method gets a default, so an unexamined edge looks stated",
     "morpho_homegraph/graph.py",
     "def edge(db, src: str, dst: str, *, method: str, confidence: float,",
     "def edge(db, src: str, dst: str, *, method: str = MD_INLINE,  # mutated\n"
     "         confidence: float = 1.0,",
     "4  edge() without method= is refused by Python itself"),

    ("confidence is stored as handed over, unchecked",
     "morpho_homegraph/graph.py",
     "    if not 0.0 <= confidence <= 1.0:\n"
     "        raise BadEdge(\"confidence must be in [0, 1], got %r\""
     " % (confidence,))",
     "    if False:  # mutated\n"
     "        raise BadEdge(\"unreachable\")",
     "5  a confidence outside [0, 1] is refused (-0.1)"),

    # -- re-assertion ------------------------------------------------------
    ("a re-assertion is dropped because the edge already exists",
     "morpho_homegraph/graph.py",
     "    if row is None or row[0] < confidence:",
     "    if row is None:  # mutated: first writer wins forever",
     "10 re-asserting a derived edge turns partial into complete"),

    ("the read path answers from the first edge only",
     "morpho_homegraph/graph.py",
     "    return {\"nodes\": sorted(seen - {node}), \"edges\": used,\n"
     "            \"provenance\": provenance_note(used)}",
     "    return {\"nodes\": sorted(seen - {node}), \"edges\": used,\n"
     "            \"provenance\": provenance_note(used[:1])}  # mutated",
     "11c a guess further out still makes the answer partial"),

    # -- identity is content, not path -------------------------------------
    #
    # Every gate except 1 and 2 behaves the same either way. The twins in the
    # fixture exist for this pair alone.
    ("nodes are paths again, so identical files are two things",
     "morpho_homegraph/graph.py",
     "    return sorted(p for (p,) in store.db.execute(\n"
     "        \"SELECT path FROM content WHERE sha256 = ?\", (node,)))",
     "    return [node]  # mutated: a node is its own single path",
     "1  two files with identical bytes are one node with two paths"),

    ("every read file is its own node, duplicates included",
     "morpho_homegraph/graph.py",
     "        \"SELECT DISTINCT sha256 FROM content WHERE sha256 IS NOT NULL\"))",
     "        \"SELECT sha256 FROM content WHERE sha256 IS NOT NULL\"))"
     "  # mutated",
     "2  nodes are never more than the files that were read"),

    ("a file that could not be read becomes a node anyway",
     "morpho_homegraph/graph.py",
     "        \"SELECT path, sha256, text FROM content WHERE text IS NOT NULL\"",
     "        \"SELECT path, sha256, text FROM content\"  # mutated",
     "3  a file L2 could not read has no node"),

    # -- R9: narrowing by the linker's own suffix --------------------------
    #
    # Without it the layer produced zero edges on ~/.claude/memory, where 150
    # `.md` and 150 `.embedding` share all 150 stems. With a version that
    # prefers `.md` instead of *the source's* suffix, gate 17 still passes --
    # which is what gate 18 is for.
    ("an ambiguous stem is refused instead of narrowed",
     "morpho_homegraph/graph.py",
     "    same = [sha for sha, suffix in candidates if suffix == source_suffix]\n"
     "    if len(same) == 1:\n"
     "        return same[0], MD_WIKILINK_SAME_EXT",
     "    return None, None  # mutated: no narrowing at all",
     "17 an ambiguous stem resolves to the linker's own suffix"),

    ("narrowing prefers .md rather than the linking file's own suffix",
     "morpho_homegraph/graph.py",
     "    same = [sha for sha, suffix in candidates if suffix == source_suffix]",
     "    same = [sha for sha, suffix in candidates if suffix == \".md\"]"
     "  # mutated",
     "18 the same rule sends an .embedding link to the .embedding"),

    ("narrowing picks a winner instead of refusing what is left",
     "morpho_homegraph/graph.py",
     "    if len(same) == 1:\n"
     "        return same[0], MD_WIKILINK_SAME_EXT",
     "    if same:  # mutated: first of whatever is left\n"
     "        return sorted(same)[0], MD_WIKILINK_SAME_EXT",
     "19 narrowing without uniqueness is still refused"),

    ("a narrowed edge is recorded as if the name had been unique",
     "morpho_homegraph/graph.py",
     "        return same[0], MD_WIKILINK_SAME_EXT",
     "        return same[0], MD_WIKILINK_UNIQUE  # mutated",
     "20 the narrowed edge names its own method"),

    # -- ambiguity is refused, not resolved --------------------------------
    ("an ambiguous name picks a winner",
     "morpho_homegraph/graph.py",
     "    candidates = by_name.get(raw, set())\n"
     "    if len(candidates) == 1:",
     "    candidates = by_name.get(raw, set())\n"
     "    if candidates:  # mutated: any candidate will do",
     "9  an ambiguous [[name]] produces no edge, and is counted"),

    # -- the layer is replaced, and it belongs to a project ----------------
    ("the graph keeps whatever the last build left",
     "morpho_homegraph/graph.py",
     "        db.execute(\"DELETE FROM edges\")",
     "        pass  # mutated: nothing is cleared",
     "13 a rebuild replaces the graph, it does not accumulate"),

    ("the graph lands in whatever store it is handed",
     "morpho_homegraph/graph.py",
     "    if store.role != PROJECT:",
     "    if False:  # mutated",
     "14 a graph aimed at the L0 store is refused by role"),

    # There is deliberately NO mutation for "the build writes around the
    # guard", and the reason is worth more than the mutation would be.
    # Measured 2026-08-04: replacing `store.writing()` with `store.db` still
    # raises `Unguarded`, because the *store* refuses the connection when the
    # guard is not held. No edit to this file can write around it, so a
    # mutation here would be aimed at a property that holds one layer down.
    # Gate 15 stays as a regression gate for that property; it is not
    # decoration, it is simply not `graph.py`'s to break. Same reasoning as
    # `mutate_cp0.py` carries for `BUSY_TIMEOUT_MS`.

    # -- one verdict, one function -----------------------------------------
    #
    # A second copy in a read path is the failure the answer key names, and it
    # is invisible from the outside: this one agrees with the original on
    # every input the other gates try.
    ("the read path grows its own copy of the verdict",
     "morpho_homegraph/graph.py",
     "            \"provenance\": provenance_note(used)}",
     "            \"provenance\": (PROV_PARTIAL  # mutated: a second copy\n"
     "                            if any(e[\"confidence\"] < 1.0 for e in used)\n"
     "                            else PROV_COMPLETE)}",
     "12 the provenance verdict is produced in exactly one function"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp5.py", prefix="mut5-", timeout=600))
