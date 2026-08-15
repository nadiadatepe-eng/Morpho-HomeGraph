#!/usr/bin/env python3
"""Mutation test for CP-17 -- the cold rows, and the provenance that guards them.

The needles fall in three groups, and the middle one is the reason the
checkpoint exists.

**Scope (1-3).** Backfill that hashes everything, or nothing, or ignores
`.gitignore`. These are the cheap mistakes and the controls catch them.

**Provenance (4-7).** A hash taken by backfill claims less than one taken by a
comparison, and the whole value of CP-17 is that the two stay apart. Every
needle here makes them look alike in a different way: stamp `compared` on a
backfill, drop the source entirely, let a real comparison keep saying
`backfilled`, or leave a hash with no source at all. If one of these survives,
the column is decoration and a later reader will trust a hash that supports
nothing.

**The ceiling and the isolation (8-10).** A backfill that writes the journal is
claiming a pass happened; one that ignores `--max-files` spends ten minutes
nobody asked for.

Run:
    python3 tests/mutate_cp17.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- scope -------------------------------------------------------------
    ("backfill hashes rows outside every registered scope",
     "morpho_homegraph/backfill.py",
     "        if keep(path)]",
     "        ]  # mutated: the scope no longer decides",
     "2  CONTROL: a cold row outside every project stays untouched"),

    ("backfill takes the whole catalogue, hash or no hash",
     "morpho_homegraph/backfill.py",
     "        \"WHERE content_hash IS NULL AND kind = 'file' ORDER BY path\")",
     "        \"WHERE kind = 'file' ORDER BY path\")  # mutated: rehash everything",
     "9  backfill is idempotent: the second run hashes 0 files"),

    # A directory has no bytes to hash, so `content_hash` returns None for it
    # and the row stays cold -- gate 10's invariant holds either way. What
    # actually changes is the count, which is why gate 9 is the honest
    # expectation here. Naming gate 10 was wrong on the first sweep and came
    # back `misattrib`: the harness reporting which gate really said no is the
    # mechanism working, not a nuisance to route around.
    ("backfill hashes directories as well as files",
     "morpho_homegraph/backfill.py",
     "        \"WHERE content_hash IS NULL AND kind = 'file' ORDER BY path\")",
     "        \"WHERE content_hash IS NULL ORDER BY path\")  # mutated: kind ignored",
     "9  backfill is idempotent: the second run hashes 0 files"),

    # -- provenance: the heart of the checkpoint ---------------------------
    ("a backfilled hash claims it came from a comparison",
     "morpho_homegraph/backfill.py",
     "                (digest, BACKFILLED, path))",
     "                (digest, 'compared', path))  # mutated: forged evidence",
     "4  a backfilled row is marked backfilled"),

    ("backfill stores the hash and forgets to say where it came from",
     "morpho_homegraph/backfill.py",
     "                \"UPDATE files SET content_hash = ?, hash_source = ? \"\n"
     "                \"WHERE path = ? AND content_hash IS NULL\",\n"
     "                (digest, BACKFILLED, path))",
     "                \"UPDATE files SET content_hash = ? \"\n"
     "                \"WHERE path = ? AND content_hash IS NULL\",\n"
     "                (digest, path))  # mutated: no provenance at all",
     "10 CONTROL: no row carries a hash without a source, or the reverse"),

    ("a real comparison stops upgrading the provenance",
     "morpho_homegraph/journal.py",
     "                   (digest, COMPARED if digest is not None else None, path))",
     "                   (digest, None, path))  # mutated: comparison says nothing",
     "6  a backfilled row that then changes becomes changed and compared"),

    ("the added branch calls its own hash a backfill",
     "morpho_homegraph/journal.py",
     "                       (content_hash(path), COMPARED, path))",
     "                       (content_hash(path), 'backfilled', path))",
     "5  CONTROL: a row hashed by a real pass is marked compared"),

    ("an unchanged row loses the provenance it already had",
     "morpho_homegraph/journal.py",
     "        \"  hash_source = (\"\n"
     "        \"  SELECT o.hash_source FROM files o WHERE o.path = files_new.path)\"",
     "        \"  hash_source = NULL\"  # mutated: provenance dropped each pass",
     "7  CONTROL: a backfilled row that does not change stays backfilled"),

    # -- isolation and the ceiling -----------------------------------------
    ("backfill writes a journal row, claiming a pass happened",
     "morpho_homegraph/backfill.py",
     "        report[\"hashed\"] += 1",
     "        store.db.execute(\"INSERT OR REPLACE INTO journal (path, state) \"\n"
     "                         \"VALUES (?, 'touched')\", (path,))\n"
     "        report[\"hashed\"] += 1",
     "8  backfill writes no journal row: the tally is unchanged"),

    ("the limit is measured but never enforced",
     "morpho_homegraph/backfill.py",
     "    if max_files is not None and len(pending) > max_files:",
     "    if False:  # mutated: --max-files is decoration",
     "14 above the limit backfill refuses and names the reason"),

    # Gate 13 fires before 13b, and that ordering is correct rather than
    # unlucky: a `--dry-run` that hashes has already told the truth about the
    # count *and* done the work, so the first gate to notice is the one
    # reading the preview. 13b remains the control that the store is untouched;
    # it is simply second in line.
    ("a dry run hashes after all",
     "morpho_homegraph/backfill.py",
     "    if dry_run:\n        return report",
     "    if False:  # mutated: --dry-run does the work anyway\n        return report",
     "13 backfill states file count and bytes before hashing"),

    # -- the L4 lines (open thread 7) --------------------------------------
    ("the lexical line reports a stale index as healthy",
     "morpho_homegraph/cli.py",
     "        lexical, indexed, expected_rows = search.state(store)",
     "        lexical, indexed, expected_rows = 'ok', 0, 0  # mutated: never asked",
     "19 status reports the lexical index state and its row count"),

    ("the semantic line is dropped when nothing is embedded",
     "morpho_homegraph/cli.py",
     "        if not expected:\n"
     "            print(\"%-14s nothing to embed yet\" % \"l4 semantic\")",
     "        if True:  # mutated: an unembedded project says nothing at all\n"
     "            pass",
     "20 status reports embedding coverage, and names the command when short"),

    # -- the coverage line -------------------------------------------------
    ("status counts backfilled hashes as compared",
     "morpho_homegraph/backfill.py",
     "        if source == BACKFILLED:\n"
     "            backfilled += 1\n"
     "        else:\n"
     "            compared += 1",
     "        compared += 1  # mutated: the split disappears",
     "11 status reports hashed/in_scope and counts backfilled separately"),

    ("status counts every row, in scope or not",
     "morpho_homegraph/backfill.py",
     "        if not keep(path):\n            continue\n        in_scope += 1",
     "        in_scope += 1  # mutated: the denominator is the whole disk",
     "11 status reports hashed/in_scope and counts backfilled separately"),

    # -- the migration (needles the condition detector asked for) ----------
    # Both of these break `Store.__init__` itself -- a duplicate-column
    # `ALTER` and a `no such table` both raise inside `migrate()`, so the very
    # first command that opens a store dies. Gate 1 is therefore the honest
    # expectation: it is the first gate that opens one. Naming 16 and 17 came
    # back `misattrib` twice, and the harness was right both times -- a
    # migration that cannot open a store never reaches the gate that inspects
    # its columns.
    ("the migration adds the column even when it is already there",
     "morpho_homegraph/store.py",
     "                if present and column not in present:",
     "                if present:  # mutated: added unconditionally",
     "1  backfill gives a cold in-scope row a hash"),

    ("the column probe stops looking at the column names",
     "morpho_homegraph/backfill.py",
     '    return "hash_source" in {row[1] for row in store.db.execute(\n'
     '        "PRAGMA table_info(files)").fetchall()}',
     '    return bool({row[1] for row in store.db.execute(\n'
     '        "PRAGMA table_info(files)").fetchall()})  # mutated: any column',
     "18 a catalogue without the column reports unknown, not a crash"),

    ("the migration runs against a table that does not exist yet",
     "morpho_homegraph/store.py",
     "                if present and column not in present:",
     "                if column not in present:  # mutated: no table check",
     "1  backfill gives a cold in-scope row a hash"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp17.py", prefix="mhg-mut-cp17-"))
