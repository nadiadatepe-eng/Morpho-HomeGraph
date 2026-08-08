#!/usr/bin/env python3
"""Two stores, compared as sets. CP-14's instrument.

**Sets, never counts (R2).** Counts are equal the moment one node is swapped
for another, which is exactly what a broken incremental path produces. Every
comparison below is `set - set`, both ways.

**What is deliberately not compared, and why (R3).** The list is short because
a comparison that excludes too much passes for anything:

  * `project_id` -- generated per registration, and CP-6 exists precisely so
    that nothing derives it from a path.
  * the clocks (`l0_scanned_at`, `last_update`, `embed_at`) and the durations
    (`*_seconds`), which are facts about the run.
  * `embed_processes` -- 1 on a run that embedded, 0 on one that reused
    everything. Also about the run.
  * the `journal` table, which describes a **transition** and not a state. A
    store built on B from nothing has, correctly, a different one.

Everything else is compared, including the four layers that are rebuilt whole
today (`content`, `edges`, L4, `scope`). They are equal by construction now --
`DELETE` then `INSERT` -- and comparing them is what makes this catch the day
one of them becomes incremental. A comparison that skipped them would be
silent on exactly that day.

**Differences are reported as keys, not as a verdict (R8).** "Not equivalent"
is not something anyone can act on.

Run:
    python3 tools/cp14_equivalence.py <store-a>/index.db <store-b>/index.db
"""
from __future__ import annotations

import sqlite3
import sys

# Facts about the run rather than about the state. Each one is here for a
# reason that can be stated in a sentence; anything that needs a paragraph
# probably belongs in the comparison instead.
EXCLUDED_META = frozenset((
    "project_id",
    "l0_scanned_at", "last_update", "embed_at",
    "l0_seconds", "l2_seconds", "l3_seconds", "l4_seconds", "embed_seconds",
    "embed_processes",
))


def _rows(db, sql, *args):
    try:
        return {tuple(r) for r in db.execute(sql, args)}
    except sqlite3.Error:
        # A table that does not exist in this store is reported as an empty
        # set rather than as a crash: the two roles have different schemas,
        # and an L0 handed in by mistake should produce a diff, not a stack.
        return set()


def project_state(path: str) -> dict[str, set]:
    """The comparable state of one project store, keyed by what it is."""
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return {
            # `reason` is in the key: "read and empty" and "not read" are
            # different states, and CP-4 exists to keep them apart.
            "content": _rows(db, "SELECT path, sha256, reason FROM content"),
            "edges": _rows(db, "SELECT src, dst, kind, method, confidence "
                               "FROM edges"),
            "scope": _rows(db, "SELECT path, mode FROM scope"),
            "l4": _rows(db, "SELECT path FROM l4"),
            "vectors": _rows(db, "SELECT sha256, ord FROM vectors"),
            "vector_bytes": _rows(db, "SELECT sha256, ord, vector "
                                      "FROM vectors"),
            "meta": {(k, v) for k, v in
                     _rows(db, "SELECT key, value FROM meta")
                     if k not in EXCLUDED_META},
        }
    finally:
        db.close()


def catalogue_state(path: str) -> dict[str, set]:
    """The comparable state of an L0 store.

    `content_hash` is split out from the rest of the row on purpose: it is the
    one column that is a function of the *history* rather than of the disk
    (`journal.build` carries it forward, and `resolve_candidate` keeps the old
    one for anything out of scope), so it is the one column where a divergence
    is expected and has to be readable on its own.
    """
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return {
            "files": _rows(db, "SELECT path, kind, size, mtime_ns FROM files"),
            "hashes": _rows(db, "SELECT path, content_hash FROM files"),
            "meta": {(k, v) for k, v in
                     _rows(db, "SELECT key, value FROM meta")
                     if k not in EXCLUDED_META},
        }
    finally:
        db.close()


def compare(left: dict[str, set], right: dict[str, set]) -> dict[str, dict]:
    """`{name: {"only_in_a": [...], "only_in_b": [...]}}` for what differs.

    Names that match on both sides are left out entirely, so an empty result
    *is* the equivalence claim -- and a non-empty one names the keys.
    """
    out = {}
    for name in sorted(set(left) | set(right)):
        a, b = left.get(name, set()), right.get(name, set())
        only_a, only_b = sorted(a - b), sorted(b - a)
        if only_a or only_b:
            out[name] = {"only_in_a": only_a, "only_in_b": only_b}
    return out


def report(diff: dict[str, dict], limit: int = 5) -> str:
    """R8: which keys differ, in words, capped so a wide diff stays readable."""
    if not diff:
        return "equivalent: nothing differs outside the excluded run facts"
    lines = []
    for name, sides in diff.items():
        lines.append("%s: %d only in A, %d only in B"
                     % (name, len(sides["only_in_a"]), len(sides["only_in_b"])))
        for side in ("only_in_a", "only_in_b"):
            for key in sides[side][:limit]:
                lines.append("    %-10s %s" % (side, key))
            if len(sides[side]) > limit:
                lines.append("    %-10s ... and %d more"
                             % (side, len(sides[side]) - limit))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: cp14_equivalence.py <a>/index.db <b>/index.db",
              file=sys.stderr)
        return 2
    left, right = argv[1], argv[2]
    reader = (catalogue_state if "/l0/" in left else project_state)
    diff = compare(reader(left), reader(right))
    print(report(diff))
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
