#!/usr/bin/env python3
"""Mutation test for CP-2 -- L1, and the verdict that could never fire.

The two mutations that matter most here rebuild homegraph's actual bug: stop
storing `content_hash`, and let a NULL stored hash count as a verdict. Both
leave a working journal with plausible numbers in it. Both make one of the six
states unreachable, which no count of rows can see.

Run:
    python3 tests/mutate_cp2.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- homegraph's bug, rebuilt -----------------------------------------
    #
    # Nothing stores a hash, so the stored hash is NULL for every row, and
    # `touched` can never be produced. Every other state still works and the
    # tally still adds up.
    ("new files in scope are never hashed",
     "morpho_homegraph/journal.py",
     '            db.execute("UPDATE files_new SET content_hash = ? WHERE path = ?",\n'
     "                       (content_hash(path), path))",
     "            pass  # mutated: nothing is ever hashed on the way in",
     "6  a pure touch is touched, and touched can fire"),

    ("a NULL stored hash counts as changed",
     "morpho_homegraph/journal.py",
     "    if old_hash is None:",
     "    if False:  # mutated: no stored hash means changed",
     "10 a NULL stored hash gives unconfirmed, not a verdict"),

    ("the unchanged rows lose their hash on the way through",
     "morpho_homegraph/journal.py",
     '        "UPDATE files_new SET content_hash = ("\n'
     '        "  SELECT o.content_hash FROM files o WHERE o.path = files_new.path)"\n'
     '        " WHERE path IN (SELECT path FROM journal WHERE state = ?)",\n'
     "        (UNCHANGED,))",
     "        \"SELECT 1\", ())  # mutated: the hash is dropped each pass",
     # Gate 3b, not 6: the loss only shows on the *next* pass, so within one
     # run the touch still resolves. That one-pass delay is what made this
     # class of bug survive two years in homegraph.
     "3b an unchanged file keeps its stored hash"),

    # -- the cheap test has to be both halves ------------------------------
    ("only size decides whether to look closer",
     "morpho_homegraph/journal.py",
     '        "WHERE n.size = o.size AND n.mtime_ns = o.mtime_ns", (UNCHANGED,))',
     '        "WHERE n.size = o.size", (UNCHANGED,))  # mutated: mtime ignored',
     "4  a same-length edit is changed"),

    ("only mtime decides whether to look closer",
     "morpho_homegraph/journal.py",
     '        "WHERE n.size = o.size AND n.mtime_ns = o.mtime_ns", (UNCHANGED,))',
     '        "WHERE n.mtime_ns = o.mtime_ns", (UNCHANGED,))  # mutated: size ignored',
     "5  an edit with mtime put back is changed"),

    # -- the scope is a boundary, not a suggestion -------------------------
    ("everything is in scope",
     "morpho_homegraph/journal.py",
     "    for root in scope:\n"
     "        if path == root or path.startswith(root.rstrip(\"/\") + \"/\"):\n"
     "            return True\n"
     "    return False",
     "    return True  # mutated: read whatever we like",
     "8  no file outside the scope is opened"),

    ("nothing is in scope",
     "morpho_homegraph/journal.py",
     "    for root in scope:\n"
     "        if path == root or path.startswith(root.rstrip(\"/\") + \"/\"):\n"
     "            return True\n"
     "    return False",
     "    return False  # mutated: never read anything",
     "6  a pure touch is touched, and touched can fire"),

    ("scope matches on string prefix, not at a separator",
     "morpho_homegraph/journal.py",
     '        if path == root or path.startswith(root.rstrip("/") + "/"):',
     "        if path.startswith(root):  # mutated: /a/inside2 counts as /a/inside",
     "8  no file outside the scope is opened"),

    # -- the states themselves --------------------------------------------
    ("a difference outside the scope is called changed",
     "morpho_homegraph/journal.py",
     "    if kind != \"file\" or not in_scope(path, scope):\n"
     "        # Something differs and nobody is going to read the file. Saying\n"
     "        # `changed` would be a guess wearing a confident name.\n"
     "        return UNCONFIRMED, old_hash",
     "    if kind != \"file\" or not in_scope(path, scope):\n"
     "        return CHANGED, old_hash  # mutated: guess, confidently",
     "7  a change outside the scope is unconfirmed"),

    ("every candidate is changed, hash or no hash",
     "morpho_homegraph/journal.py",
     "    return (TOUCHED if now == old_hash else CHANGED), now",
     "    return CHANGED, now  # mutated: touched never happens",
     "6  a pure touch is touched, and touched can fire"),

    ("a removed file leaves no trace",
     "morpho_homegraph/journal.py",
     '        "INSERT INTO journal (path, state) SELECT o.path, ? FROM files o "\n'
     '        "LEFT JOIN files_new n ON n.path = o.path WHERE n.path IS NULL",\n'
     "        (REMOVED,))",
     '        "SELECT 1", ())  # mutated: deletions are silent',
     "2  a deleted file is removed"),

    ("a new file is not distinguished from an old one",
     "morpho_homegraph/journal.py",
     '        "INSERT INTO journal (path, state) SELECT n.path, ? FROM files_new n "\n'
     '        "LEFT JOIN files o ON o.path = n.path WHERE o.path IS NULL", (ADDED,))',
     '        "SELECT 1", ())  # mutated: nothing is ever added',
     "1  a new file is added"),

    # -- the journal is replaced, like L0 ---------------------------------
    ("the journal accumulates across passes",
     "morpho_homegraph/journal.py",
     '    db.execute("DELETE FROM journal")',
     "    pass  # mutated: verdicts pile up",
     # Gate 1, not 12: a journal that never clears keeps the previous pass's
     # `added` row for a file that is now merely unchanged, so the wrong
     # verdict shows up before the row count does.
     "1  a new file is added"),

    # -- the previous pass has to survive long enough to be compared ------
    #
    # Replacing `files` before the diff destroys the only thing the journal
    # has to compare against. Every row then looks new.
    ("L0 is replaced before L1 has read it",
     "morpho_homegraph/scan.py",
     "        tally = journal.build(store, scope or [])\n"
     '        db.execute("DELETE FROM files")',
     '        db.execute("DELETE FROM files")  # mutated: diff against nothing\n'
     "        tally = journal.build(store, scope or [])",
     "3  an untouched file is unchanged"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp2.py", prefix="mut2-", timeout=600))
