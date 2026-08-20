#!/usr/bin/env python3
"""CP-23 mutation harness -- can the per-directory gates actually go red?

The answer key is `tests/gold/FASIT-cp23.md`.

**Grouping a dict by `dirname` always works, so a green suite proves very
little here.** The gold answer said that before the code existed, and it was
right twice over: the two real defects this checkpoint produced were both in
what *counts as pending*, not in the grouping, and both were caught by
controls rather than by inspection.

  1. The first version counted every journal move as pending, so a freshly
     built project reported everything pending -- a number that is maximal
     precisely when nothing is wrong. Gate 5 caught it.
  2. The fix, once `known` was subtracted, still counted **directories**,
     which L2 can never ingest, so one row stayed pending for ever. Gate 6h
     caught that one.

Half the mutations below therefore aim at the controls, because a control
that cannot fail makes the mechanism gates look protected when they are not.

Run:
    python3 tests/mutate_cp23.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- direct children, the load-bearing rule ---------------------------
    #
    # Roll the subtree up, which is exactly the mechanism CP-20 was rejected
    # for. The numbers stay plausible -- the root simply gets bigger -- which
    # is why gate 2 asserts the fixture's totals rather than "it grouped".
    ("the grouping walks up, so a directory holds its grandchildren",
     "morpho_homegraph/dirfresh.py",
     "        grouped.setdefault(os.path.dirname(path), Counter())[value] += 1",
     "        for part in (os.path.dirname(path),"
     " os.path.dirname(os.path.dirname(path))):\n"
     "            grouped.setdefault(part, Counter())[value] += 1"
     "  # mutated: subtree",
     "2  CONTROL: no directory holds its grandchildren's files"),

    # -- pending: the first real defect, recreated -------------------------
    #
    # Drop the `known` subtraction. Every file the catalogue saw is pending
    # again, including all the ones L2 has already read, and a fresh project
    # reports itself maximally behind.
    ("pending stops subtracting what L2 already holds",
     "morpho_homegraph/dirfresh.py",
     "        if path in known:\n            continue",
     "        pass  # mutated: every journal move counts as pending",
     "5  CONTROL: a project that is read, current and embedded is clean"),

    # -- pending: the second real defect, recreated ------------------------
    #
    # Drop the `kind = 'file'` join. Directories come back as pending, L2 can
    # never ingest one, and the count never clears -- gate 5's failure in a
    # second disguise, which is why 6h exists as well as 5.
    ("pending counts directories, which L2 can never ingest",
     "morpho_homegraph/dirfresh.py",
     "            \"SELECT j.path FROM journal j JOIN files f ON f.path = j.path \"\n"
     "            \"WHERE j.state IN (%s) AND f.kind = 'file'\" % marks,",
     "            \"SELECT path FROM journal WHERE state IN (%s)\" % marks,"
     "  # mutated: directories too",
     "6h CONTROL: it stops being pending once update has read it"),

    # -- pending: the opposite failure ------------------------------------
    #
    # A count that can never fire is as useless as one that never clears, and
    # the two fixes above both push in that direction. This mutation is the
    # guard on the guard.
    ("pending is switched off entirely",
     "morpho_homegraph/dirfresh.py",
     "    known = set(known)",
     "    return {}  # mutated: nothing is ever pending",
     "6g a fresh directory behind only by an unread file is pending"),

    # -- which L1 states are pending --------------------------------------
    #
    # `unchanged` is not a change and `removed` belongs to CP-6. Summing the
    # whole journal gives a bigger number that looks entirely reasonable.
    ("every journal state counts as pending, including unchanged",
     "morpho_homegraph/dirfresh.py",
     "PENDING = (journal.ADDED, journal.CHANGED, journal.TOUCHED,\n"
     "           journal.UNCONFIRMED)",
     "PENDING = (journal.ADDED, journal.CHANGED, journal.TOUCHED,\n"
     "           journal.UNCONFIRMED, journal.UNCHANGED, journal.REMOVED)"
     "  # mutated",
     "7  CONTROL: a journal with nothing pending gives no pending"),

    # -- the scope --------------------------------------------------------
    ("the project's scope is ignored, so the whole home area counts",
     "morpho_homegraph/dirfresh.py",
     "        if keep is not None and not keep(path):\n            continue",
     "        pass  # mutated: no scope",
     "6b a pending change outside the project's scope is not counted"),

    # -- the four states, always present ----------------------------------
    #
    # A zero that disappears teaches the reader that a missing count means
    # fine, which is `freshness.tally`'s reason for existing one level down.
    ("a state at zero is dropped from the row instead of shown",
     "morpho_homegraph/dirfresh.py",
     "        states = {name: counted.get(name, 0) for name in\n"
     "                  (freshness.FRESH, freshness.STALE, freshness.UNREAD,\n"
     "                   freshness.UNEMBEDDED)}",
     "        states = dict(counted)  # mutated: zeroes vanish",
     "3b every directory names all four states, zero included"),

    # -- the order --------------------------------------------------------
    #
    # Alphabetical looks tidy and makes the reader scan for the answer. The
    # tie-break is what makes two runs against an unchanged store agree.
    ("the ranking is alphabetical instead of worst-first",
     "morpho_homegraph/dirfresh.py",
     "    return sorted(rows.items(),\n"
     "                  key=lambda item: (-item[1][\"not_fresh\"],\n"
     "                                    -item[1][\"pending\"], item[0]))",
     "    return sorted(rows.items())  # mutated: alphabetical",
     "9  worst first, and equal rows fall in path order"),

    # -- absence ----------------------------------------------------------
    #
    # A directory we have read nothing in, printed as `0 fresh`, is the empty
    # layer that looks finished -- CP-7B R8, one floor down.
    ("the clean run prints nothing instead of saying it is clean",
     "morpho_homegraph/cli.py",
     '        print("every directory is fresh: %d files in %d directories"\n'
     '              % (len(state), len(rows)))',
     '        pass  # mutated: silence where a sentence belongs',
     "5  CONTROL: a project that is read, current and embedded is clean"),

    # -- the clocks -------------------------------------------------------
    #
    # CP-12 gate 5 one floor up: if the age only appeared when something were
    # behind, "no age" would become a signal the reader has to interpret.
    ("the age line is dropped from the answer",
     "morpho_homegraph/cli.py",
     "    print(freshness.describe(ages))\n    return 0",
     "    return 0  # mutated: no clocks",
     "10 the age line is printed even when every directory is fresh"),

    # -- the state behind the count ---------------------------------------
    ("the line gives a fraction without naming which state is behind",
     "morpho_homegraph/dirfresh.py",
     "    for name in (freshness.STALE, freshness.UNREAD, freshness.UNEMBEDDED):\n"
     "        if row[\"states\"].get(name):\n"
     "            parts.append(\"%d %s\" % (row[\"states\"][name], name))",
     "    pass  # mutated: the fraction alone",
     "R6 the line names the state behind the count, not just a fraction"),

    # -- the three-way filter on what gets printed ------------------------
    #
    # `args.all or row["not_fresh"] or row["pending"]` is the one compound
    # condition CP-23 adds, and `condition_coverage.py` named it the moment it
    # was written. Three disjuncts, three needles, because a fixture that
    # supplies the same value along two axes lets a dropped one pass.
    ("the --all flag stops widening the listing",
     "morpho_homegraph/cli.py",
     '        if args.all or row["not_fresh"] or row["pending"]:',
     '        if row["not_fresh"] or row["pending"]:  # mutated: --all is dead',
     "8b --all lists every directory L2 holds rows for"),

    ("a directory that is only behind, with nothing pending, is not listed",
     "morpho_homegraph/cli.py",
     '        if args.all or row["not_fresh"] or row["pending"]:',
     '        if args.all or row["pending"]:  # mutated: not_fresh dropped',
     "4  a change makes its own directory unfresh, not its sibling"),

    ("a directory that is only pending, and otherwise fresh, is not listed",
     "morpho_homegraph/cli.py",
     '        if args.all or row["not_fresh"] or row["pending"]:',
     '        if args.all or row["not_fresh"]:  # mutated: pending dropped',
     "6g a fresh directory behind only by an unread file is pending"),

    # -- gate 11, the one that can say "do not build this" ------------------
    #
    # Added after an audit found gate 11 had no check at all: the answer key
    # named twelve gates and the run reported eleven. The measurement HAD been
    # taken, so nothing was wrong with the result -- it was simply unenforced,
    # which is the silent omission that reads as coverage. These needles exist
    # so the enforcement cannot rot back to that state.
    ("the measurement tool is gone, leaving R7 as a claim in prose",
     "tools/m8_dir_mixture.py",
     "def measure(project_id: str) -> dict:",
     "def _renamed_away(project_id: str) -> dict:  # mutated: tool broken",
     "11b the measurement runs and answers the mixture question"),

    # The control catches this one rather than 11b, and that attribution is
    # correct: dropping the word from the header means even the empty-store
    # run stops saying "mixed", which is what the control asserts. Recorded
    # as the control's kill instead of forced onto 11b -- a needle whose
    # `expected` is wrong teaches the reader to distrust the attribution.
    ("the tool stops reporting mixture and reports only totals",
     "tools/m8_dir_mixture.py",
     '    print("%-20s %7s %7s %7s %9s  %s"\n'
     '          % ("project", "files", "dirs", "mixed", "not-fresh", "states"))',
     '    print("%-20s %7s %7s %7s %9s  %s"\n'
     '          % ("project", "files", "dirs", "-", "not-fresh", "states"))'
     '  # mutated: mixture unreported',
     "11b CONTROL: a header with no project is not a measurement"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp23.py", prefix="mhg-mut-cp23-",
                 timeout=900))
