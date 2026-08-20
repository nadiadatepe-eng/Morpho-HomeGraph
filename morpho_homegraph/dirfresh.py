#!/usr/bin/env python3
"""CP-23: freshness per directory, counting direct children.

The answer key is `tests/gold/FASIT-cp23.md`, written before this module.

**This is the one piece that survived CP-20.** That checkpoint wanted derived
*summaries* per directory, with stable sampling and refresh bubbling to the
root; `tools/m7_summary_ratio.py` measured 8.7x and a median of 2 files per
directory, and the mechanism was shelved as not worth its maintenance. The
sentence left behind was: "freshness counting direct children, with known
pending changes -- CP-12 already has the instinct per layer; per directory it
is a small extension. **Not taken yet.**" This is that extension and nothing
more.

**It derives no text and samples nothing.** Every state here comes from
`freshness.per_file`, which is CP-12's function and CP-12's four states, and
every clock comes from `freshness.ages`. A second way to decide "is this file
fresh" would be a second source for the same fact, and two sources drift --
which is the whole reason CP-19 exists as a note rather than as code.

**Why direct children and not the subtree.** A subtree count turns the home
area into one row saying "798 258 files, a bit of everything": true, and
useless. Direct children make the number one a reader can act on, and they
are also the reason nothing bubbles upward here -- upward aggregation is
precisely the mechanism CP-20 was rejected for, and it carries OpenViking's
own unsolved write amplification with it.

**A directory is a path prefix, not an object.** L2 holds files. `dirname` of
a row is the directory, so a directory holding no rows we have read is absent
from the count rather than present with zeroes -- absence of a directory is
absence of knowledge about it, and printing `0 fresh` for something never
read is the empty layer that looks finished (CP-7B R8).
"""
from __future__ import annotations

import os
from collections import Counter
from types import MappingProxyType

from . import freshness, journal

# What "known pending changes" means, named rather than left to a reader of
# the SQL. These are the L1 verdicts that say the catalogue saw something move
# that L2 has not taken in yet. `unchanged` is not pending -- it is the
# absence of a change -- and `removed` belongs to CP-6, which owns the story
# of a file that is gone. Counting the whole journal instead of these four is
# the mistake gate 7 exists to catch.
PENDING = (journal.ADDED, journal.CHANGED, journal.TOUCHED,
           journal.UNCONFIRMED)


def pending_by_dir(l0_store, keep=None, known=()) -> dict[str, int]:
    """`{directory: pending count}` from L1, counting direct children only.

    **"Pending" is not "the journal moved", and the first version of this
    function got that wrong.** Gate 5 caught it: a freshly built project
    reported every file as pending, because L1's journal describes L0 pass N
    against pass N-1 -- what the *catalogue* saw move -- and after an `update`
    L2 has already taken all of it in. A number that is maximal precisely when
    nothing is wrong is worse than no number.

    So `known` is the set of paths L2 already holds, and pending means **the
    catalogue knows about a file this project has not read**. That is the one
    thing `freshness.per_file` structurally cannot say: it iterates the rows
    L2 has, so a file that was never ingested has no row and no state, and it
    is invisible to every count CP-12 makes. It is the same hole `status`
    names as open thread 5, one level down and per directory.

    A file L2 *does* hold and that has since changed is `stale`, not pending.
    Counting it here as well would be a second source for a fact CP-12 already
    owns, and two sources drift.

    `keep(path)` is the project's scope: L1 describes the whole home area and
    L2 describes one project, so a change outside this project's scope is not
    pending *for this project*.

    A store with no `journal` table -- a read-only open of an L0 built before
    the layer existed -- answers `{}` rather than raising. Degrade and say why
    is the rule for readers (CP-17), and the caller says why by showing no
    pending column at all.
    """
    if l0_store is None:
        return {}
    known = set(known)
    marks = ",".join("?" for _ in PENDING)
    try:
        # Joined against `files` on `kind = 'file'`, and that join is not
        # tidiness. L1's journal holds directories as well -- a directory whose
        # mtime moved is `added` or `changed` like anything else -- and L2 only
        # ever holds files. Without the join every project reports a directory
        # it can never ingest as permanently pending, which is exactly the
        # never-clearing count gate 5 was written to catch, in a second
        # disguise. Found by gate 6h, after the first fix.
        rows = l0_store.db.execute(
            "SELECT j.path FROM journal j JOIN files f ON f.path = j.path "
            "WHERE j.state IN (%s) AND f.kind = 'file'" % marks,
            PENDING).fetchall()
    except Exception:                                    # noqa: BLE001
        # sqlite3.OperationalError for a missing table, sqlite3.DatabaseError
        # for a store we cannot read. Both mean the same thing to the caller:
        # this layer has nothing to say, and it must not take the answer down
        # with it.
        return {}
    counted: Counter[str] = Counter()
    for (path,) in rows:
        if path in known:
            continue
        if keep is not None and not keep(path):
            continue
        counted[os.path.dirname(path)] += 1
    return dict(counted)


def per_dir(state: dict[str, str],
            pending: dict[str, int] = MappingProxyType({})) -> dict[str, dict]:
    """`{directory: {states, total, not_fresh, pending}}`, direct children.

    Takes the per-file state rather than the store, because the store is
    where a second implementation would creep in: this function cannot decide
    a file is fresh, it can only count decisions CP-12 already made.

    Every one of CP-12's four states is present in each row even at zero, for
    the same reason `freshness.tally` guarantees it: a count that disappears
    when it is zero teaches the reader that a missing count means fine, and
    then a missing count for a *broken* directory reads as fine too.

    `pending` defaults to an immutable empty mapping rather than to `None`
    with an `or {}` inside. That guard was a compound condition
    `condition_coverage.py` named as unaimed, and it deserved to be: every
    real caller passes a mapping, so no fixture can make the two halves
    differ, and a needle would kill the gate by raising rather than by any
    gate saying no. Removing it is the answer CP-16 prescribes for a branch
    nothing can observe -- delete rather than waive.
    """
    grouped: dict[str, Counter[str]] = {}
    for path, value in state.items():
        grouped.setdefault(os.path.dirname(path), Counter())[value] += 1
    out = {}
    for directory, counted in grouped.items():
        states = {name: counted.get(name, 0) for name in
                  (freshness.FRESH, freshness.STALE, freshness.UNREAD,
                   freshness.UNEMBEDDED)}
        total = sum(counted.values())
        out[directory] = {
            "states": states,
            "total": total,
            # `.get` rather than `[...]`: the four-key guarantee is this
            # function's own promise, and a promise that raises when it is
            # broken takes the whole answer down instead of letting the gate
            # that checks it say no. Measured by the mutation harness, which
            # reported a crash where it should have reported a killed gate.
            "not_fresh": total - states.get(freshness.FRESH, 0),
            "pending": pending.get(directory, 0),
        }
    return out


def ranked(rows: dict[str, dict]) -> list[tuple[str, dict]]:
    """Worst first, ties broken on path (R8).

    Alphabetical asks the reader to scan; sorted by how much is not fresh,
    the first row is the answer. The tie-break on path is not decoration: it
    is what makes two runs against an unchanged store print the same order,
    the same instinct as CP-20's stable sampling with none of the sampling.
    """
    return sorted(rows.items(),
                  key=lambda item: (-item[1]["not_fresh"],
                                    -item[1]["pending"], item[0]))


def describe(directory: str, row: dict, root: str | None = None) -> str:
    """One line per directory, and it names *which* state is behind the count.

    "3 of 7 fresh" does not tell the reader whether the rest is unread, stale
    or unembedded -- and those have three different commands. R6: the states
    that are non-zero are named, and a fully fresh directory says so in
    words rather than by an empty tail.
    """
    shown = directory
    if root:
        try:
            shown = os.path.relpath(directory, root)
        except ValueError:
            # Different drives on Windows, and a path we cannot make relative
            # is still a path we can print whole.
            shown = directory
        if shown == ".":
            shown = "."
    parts = ["%d/%d fresh" % (row["states"].get(freshness.FRESH, 0),
                              row["total"])]
    for name in (freshness.STALE, freshness.UNREAD, freshness.UNEMBEDDED):
        if row["states"].get(name):
            parts.append("%d %s" % (row["states"][name], name))
    if row["pending"]:
        # Readable, but behind: the phrasing is the CP-20 sentence this whole
        # module came from, kept because it is the fact a reader acts on.
        parts.append("%d pending" % row["pending"])
    return "%-40s %s" % (shown, ", ".join(parts))
