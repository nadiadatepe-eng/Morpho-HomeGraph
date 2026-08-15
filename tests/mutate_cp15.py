#!/usr/bin/env python3
"""Mutation test for CP-15 -- the scope L1 never had, and the reader it never had.

Two halves, and each has its own way of looking right while doing nothing.

The scope can be too wide (everything gets hashed, and the cheap layer becomes
the expensive one) or too narrow (nothing does, which is the state this
checkpoint found: 0 of 430 189 rows). Gates 3, 4 and 5 are that pair, and 2 is
the one that catches the tempting shortcut -- reading the union out of the
stores instead of off the disk, which is CP-3's bug in a fourth costume.

The reader can act on too much (every project every round, which is what
CP-13 gate 13 forbids) or on too little (nothing, which looks exactly like a
quiet corpus). Gates 10 and 11 are that pair, and 12 guards the line CP-13
gate 3 draws: a project whose guard the service does not hold is reported,
never written.

Run:
    python3 tests/mutate_cp15.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the union (R1, R2) ------------------------------------------------
    ("the union is loaded from the stores instead of read off the disk",
     "morpho_homegraph/service.py",
     "    selected = [chosen_scope(path) for _pid, path in projects()]",
     "    selected = [scope.load(__import__(\"morpho_homegraph.store\","
     " fromlist=[\"Store\"]).Store(db_path(pid), read_only=True))\n"
     "                or chosen_scope(path)  # mutated: trust the store\n"
     "                for pid, path in projects()]",
     "2  CONTROL: the union is recomputed from disk, not loaded"),

    ("the union says yes to everything",
     "morpho_homegraph/service.py",
     "        return any(s.contains(path, is_dir=False) for s in selected)",
     "        return True  # mutated: hash the whole home area",
     "4  CONTROL: a file .gitignore excludes gets no hash"),

    ("the union says no to everything, which is where this checkpoint began",
     "morpho_homegraph/service.py",
     "        return any(s.contains(path, is_dir=False) for s in selected)",
     "        return False  # mutated: back to 0 of 430 189",
     "3  a file inside a registered project's scope gets a hash"),

    ("scan is handed no union at all",
     "morpho_homegraph/cli.py",
     "            summary = scan(store, args.root, service.union_keep())",
     "            summary = scan(store, args.root)  # mutated",
     "3  a file inside a registered project's scope gets a hash"),

    # -- the warm-up is a property, not an accident (R3) -------------------
    ("a new file is not hashed on the way in, so nothing is ever warm",
     "morpho_homegraph/journal.py",
     "        if kind == \"file\" and keep(path):\n"
     "            db.execute(\"UPDATE files_new SET content_hash = ?, "
     "hash_source = ? \"\n"
     "                       \"WHERE path = ?\",\n"
     "                       (content_hash(path), COMPARED, path))",
     "        if False:  # mutated: added files stay unhashed\n"
     "            db.execute(\"UPDATE files_new SET content_hash = ? "
     "WHERE path = ?\",\n"
     "                       (content_hash(path), path))",
     "8  a file added after the switch is changed on its first change"),

    ("an unconfirmed verdict does not store the hash it just computed",
     "morpho_homegraph/journal.py",
     "        return UNCONFIRMED, now",
     "        return UNCONFIRMED, old_hash  # mutated: never warms up",
     "7  the second change is changed, so the warm-up is exactly two"),

    # -- the reader (R4, R5, R6) -------------------------------------------
    ("the sweep stops reading the journal it just wrote",
     "morpho_homegraph/service.py",
     "        moved = [p for (p,) in store.db.execute(\n"
     "            \"SELECT path FROM journal WHERE state <> ?\","
     " (journal.UNCHANGED,))]",
     "        moved = []  # mutated: the sweep learns nothing",
     "10 the sweep updates a watched project that changed while down"),

    # The control's own mutation: read every row, including the unchanged
    # ones, and every sweep updates every project for ever.
    ("the sweep counts unchanged rows as movement",
     "morpho_homegraph/service.py",
     "            \"SELECT path FROM journal WHERE state <> ?\","
     " (journal.UNCHANGED,))]",
     "            \"SELECT path FROM journal\")]  # mutated",
     "11 CONTROL: a sweep whose journal is quiet updates nothing"),

    ("the sweep writes projects whose guard it does not hold",
     "morpho_homegraph/service.py",
     "    return hits & set(watched_ids)",
     "    return hits  # mutated: helpful, and locks the user out",
     "12 an unwatched project with changes is reported, not written and not attempted"),

    ("an unwatched project with changes is passed over in silence",
     "morpho_homegraph/service.py",
     "        out(\"changed  %s  %s has changes and is not watched: \"\n"
     "            \"morphofiles-graph update %s\" % (project_id,"
     " paths[project_id],\n"
     "                                             project_id))",
     "        pass  # mutated: say nothing",
     "12 an unwatched project with changes is reported, not written and not attempted"),

    ("the report does not name the command that fixes it",
     "morpho_homegraph/service.py",
     "        out(\"changed  %s  %s has changes and is not watched: \"\n"
     "            \"morphofiles-graph update %s\" % (project_id,"
     " paths[project_id],\n"
     "                                             project_id))",
     "        out(\"changed  %s\" % project_id)  # mutated: a state, not an act",
     "12 an unwatched project with changes is reported, not written and not attempted"),

    # -- an empty registry is an answer, not an error (gate 16) ------------
    ("a missing predicate is treated as every path being in scope",
     "morpho_homegraph/journal.py",
     "    keep = keep or (lambda _path: False)",
     "    keep = keep or (lambda _path: True)  # mutated",
     "16b CONTROL: a catalogue built with no predicate hashes nothing"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp15.py", prefix="mut15-", timeout=900))
