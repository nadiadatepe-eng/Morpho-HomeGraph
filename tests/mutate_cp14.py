#!/usr/bin/env python3
"""Mutation test for CP-14 -- equivalence, and the ways it can prove nothing.

An equivalence check has a failure mode the layers under it do not: it can be
**vacuously true**. Compare nothing, exclude everything, look at one side of
the set difference, and every gate about equality goes green for two stores
that share no data at all. So most of the mutations here aim at the comparison
itself rather than at the code it compares -- gate 9 is the control that has
to notice, and gates 8b, 15 and 16 are the ones that catch the narrower forms.

The corpus gets a mutation too. Gate 10 counts the nine axes instead of
trusting the file that claims them, and the way to prove that gate works is to
break an axis -- which is what already happened once for real, when the
"edited with size preserved" strings were 48 and 49 characters long.

Run:
    python3 tests/mutate_cp14.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the only genuinely incremental state (R1, R6) ---------------------
    #
    # The failure this checkpoint exists for: a vector that outlives its text.
    # `store.py` wrote the duty down in CP-9; nothing enforced it until now.
    ("a vector is never dropped when its text is gone",
     "morpho_homegraph/embed.py",
     "    stale = sorted(have - wanted.keys())",
     "    stale = []  # mutated: vectors outlive their text",
     "1  the vector keys are the same set in A->B and in B"),

    ("new text is never embedded, so the updated store is short",
     "morpho_homegraph/embed.py",
     "    todo = sorted(wanted.keys() - have)",
     "    todo = []  # mutated: nothing new is ever added",
     # Gate 3, not 1, and the reason is a property of equivalence itself:
     # BOTH stores are built by the mutated code, so they agree perfectly on
     # having no vectors at all. Only the comparison against the state at A
     # -- built before the corpus moved -- can see it. A defect that is
     # symmetric across the two build paths is invisible to gate 1 by
     # construction, and that is blind spot 6 in the answer key.
     "3  the edited file leaves no vector behind for its old hash"),

    # The chunk-boundary escape hatch: `(sha, 3)` survives a re-cut and holds
    # different text under the same key. R7's own mutation.
    ("a re-cut store keeps its old vectors under the same keys",
     "morpho_homegraph/embed.py",
     "    if store.get_meta(\"embed_chunking\") not in (None, CHUNKING):\n"
     "        stale = sorted(have)\n"
     "        have = set()",
     "    if False:  # mutated: a new cut reuses the old vectors\n"
     "        stale = sorted(have)\n"
     "        have = set()",
     "14b a store whose recorded cut differs re-embeds every vector"),

    # -- the comparison can be vacuous (R2, R3, R8) ------------------------
    ("the comparison finds nothing, ever",
     "tools/cp14_equivalence.py",
     "        if only_a or only_b:\n"
     "            out[name] = {\"only_in_a\": only_a, \"only_in_b\": only_b}",
     "        if False:  # mutated: everything is equivalent\n"
     "            out[name] = {\"only_in_a\": only_a, \"only_in_b\": only_b}",
     "9  CONTROL: the store at A is NOT equivalent to the one at B"),

    ("only one side of the set difference is looked at",
     "tools/cp14_equivalence.py",
     "        only_a, only_b = sorted(a - b), sorted(b - a)",
     "        only_a, only_b = sorted(a - b), []  # mutated: one side only",
     "15 a difference is reported as keys, and from both sides"),

    ("nothing about a project store is read at all",
     "tools/cp14_equivalence.py",
     "            \"vectors\": _rows(db, \"SELECT sha256, ord FROM vectors\"),",
     "            \"vectors\": set(),  # mutated: read nothing",
     "3  the edited file leaves no vector behind for its old hash"),

    ("the exclusion list stops excluding anything, so clocks are compared",
     "tools/cp14_equivalence.py",
     "                     if k not in EXCLUDED_META},\n"
     "        }\n"
     "    finally:\n"
     "        db.close()\n"
     "\n"
     "\n"
     "def catalogue_state",
     "                     if True},  # mutated: nothing is excluded\n"
     "        }\n"
     "    finally:\n"
     "        db.close()\n"
     "\n"
     "\n"
     "def catalogue_state",
     "8  meta is the same set"),

    # The other direction, and the one gate 8b is written for: exclude
    # everything and `meta` is equal for any two stores that exist.
    ("the exclusion list swallows the whole of meta",
     "tools/cp14_equivalence.py",
     "                     if k not in EXCLUDED_META},\n"
     "        }\n"
     "    finally:\n"
     "        db.close()\n"
     "\n"
     "\n"
     "def catalogue_state",
     "                     if False},  # mutated: exclude everything\n"
     "        }\n"
     "    finally:\n"
     "        db.close()\n"
     "\n"
     "\n"
     "def catalogue_state",
     "8b CONTROL: meta still compares the keys that matter"),

    ("the content rows drop the reason, so unread and empty look alike",
     "tools/cp14_equivalence.py",
     "            \"content\": _rows(db, \"SELECT path, sha256, reason "
     "FROM content\"),",
     "            \"content\": _rows(db, \"SELECT path FROM content\"),"
     "  # mutated",
     "4b CONTROL: a content key carries sha256 and reason, not just path"),

    # -- the report is the deliverable, not the boolean (R8) ---------------
    ("every comparison reports equivalence in words",
     "tools/cp14_equivalence.py",
     "    if not diff:\n"
     "        return \"equivalent: nothing differs outside the excluded run "
     "facts\"",
     "    if True:  # mutated: always equivalent in words\n"
     "        return \"equivalent: nothing differs outside the excluded run "
     "facts\"",
     "15 a difference is reported as keys, and from both sides"),

    ("an equivalent pair is reported with no word for it",
     "tools/cp14_equivalence.py",
     "        return \"equivalent: nothing differs outside the excluded run "
     "facts\"",
     "        return \"\"  # mutated: silence instead of an answer",
     "16 CONTROL: two equivalent stores report nothing and that reads as "
     "equivalence"),

    # -- the inert column, and the day it stops being inert (gate 11, 12) --
    #
    # Not a defect: this is the *fix* for open thread 7b, applied as a
    # mutation. Gate 12 exists so that doing it turns the checkpoint red and
    # forces the decision, instead of quietly changing what gate 11 means.
    # This used to be the *fix* for open thread 7b, kept as a mutation so
    # that applying it would turn the checkpoint red and force the decision.
    # CP-15 applied it for real, so the mutation turned around: the defect is
    # now taking the scope away again -- and with it the divergence this
    # answer key predicted, then measured as absent, and CP-15 made true.
    ("scan loses its scope again, so no file is ever hashed",
     "morpho_homegraph/cli.py",
     "            summary = scan(store, args.root, service.union_keep())",
     "            summary = scan(store, args.root)  # mutated: no scope",
     "11 the file edited with size and mtime preserved keeps A's old hash"),

    ("the union says yes to everything, so .gitignore stops counting",
     "morpho_homegraph/service.py",
     "        return any(s.contains(path, is_dir=False) for s in selected)",
     "        return bool(selected)  # mutated: the whole tree is in scope",
     "12 the file that left the scope keeps A's old hash too"),

    # -- the corpus has to really differ on every axis (R4, gate 10) -------
    ("the size-preserving edit stops preserving the size",
     "tests/test_cp14.py",
     "SNEAKY_B = \"9876543210 the second version, exactly this!!!!.\"",
     "SNEAKY_B = \"9876543210 the second version, longer than before.\"",
     "10 CONTROL: every one of the nine axes is really in the corpus"),

    ("the copy axis copies nothing, so one hash never gets two paths",
     "tests/test_cp14.py",
     "    shutil.copyfile(os.path.join(root, \"twin-a.md\"),\n"
     "                    os.path.join(root, \"twin-b.md\"))",
     "    write(os.path.join(root, \"twin-b.md\"), \"different bytes\\n\")"
     "  # mutated",
     "10 CONTROL: every one of the nine axes is really in the corpus"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp14.py", prefix="mut14-", timeout=1800))
