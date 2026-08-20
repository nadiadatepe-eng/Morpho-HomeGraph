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
    # Found by an independent recheck that sabotaged the tool in a clean
    # clone and watched gate 11b stay green: it asked whether a percentage was
    # printed, not whether mixture was computed. `0.0 %` answered the wrong
    # question perfectly. This needle is that sabotage, kept.
    ("the mixture count is hardcoded to zero, so the tool measures nothing",
     "tools/m8_dir_mixture.py",
     "    mixed = sum(1 for c in per_dir.values() if len(c) > 1)",
     "    mixed = 0  # mutated: mixture never found",
     "11b the measurement runs and answers the mixture question"),

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

    # -- checks the coverage map showed nothing aimed at --------------------
    #
    # Added 2026-08-20 after `mutation_coverage.py` was ported and put CP-23
    # at 48 %, below the repository median, on this checkpoint's own new code.
    # The list below is that map's UNCOVERED entries, minus the ones a needle
    # cannot reach honestly (see the waivers at the end of this file).

    # Gate 1 and gate 3 both watch the grouping key. The subtree mutation
    # above lands on gate 2; this one drops the grouping entirely, so every
    # file lands in one bucket and the per-directory view stops existing.
    ("every file is grouped under one key, so directories vanish",
     "morpho_homegraph/dirfresh.py",
     "        grouped.setdefault(os.path.dirname(path), Counter())[value] += 1",
     "        grouped.setdefault('', Counter())[value] += 1"
     "  # mutated: one bucket",
     "1  a file two levels down is counted in its own directory"),

    # Gate 3 is the "one source, two views" gate: the per-directory sums must
    # equal what CP-12 says for the same files. Miscounting by one preserves
    # the shape and breaks the identity.
    ("a state is counted twice, so the sums stop matching CP-12",
     "morpho_homegraph/dirfresh.py",
     "        states = {name: counted.get(name, 0) for name in\n"
     "                  (freshness.FRESH, freshness.STALE, freshness.UNREAD,\n"
     "                   freshness.UNEMBEDDED)}",
     "        states = {name: counted.get(name, 0) * 2 for name in\n"
     "                  (freshness.FRESH, freshness.STALE, freshness.UNREAD,\n"
     "                   freshness.UNEMBEDDED)}  # mutated: double-counted",
     "3  the per-directory states sum to the per-file tally"),

    # Gate 6 names *which* L1 states are pending. The earlier needle widens
    # PENDING; this one narrows it, because a set that is too small fails in
    # the opposite direction and gate 7 cannot see it.
    ("only `added` counts as pending, so real changes are missed",
     "morpho_homegraph/dirfresh.py",
     "PENDING = (journal.ADDED, journal.CHANGED, journal.TOUCHED,\n"
     "           journal.UNCONFIRMED)",
     "PENDING = (journal.ADDED,)  # mutated: the other three go silent",
     "6  pending counts the four L1 states that are actually pending"),

    # Gate 6i's mechanism: the `kind = 'file'` join. The earlier needle drops
    # the whole join clause; this one keeps the join and flips the kind, which
    # is the subtler way to get the same wrong answer.
    ("the kind filter selects directories instead of files",
     "morpho_homegraph/dirfresh.py",
     "\"WHERE j.state IN (%s) AND f.kind = 'file'\" % marks,",
     "\"WHERE j.state IN (%s) AND f.kind = 'dir'\" % marks,"
     "  # mutated: exactly backwards",
     "6i CONTROL: a directory in the journal is not a pending file"),

    # Gates 6c and 6d: degrade rather than raise. A reader that throws on a
    # catalogue built before the journal existed takes the whole answer down.
    ("a catalogue with no journal raises instead of degrading",
     "morpho_homegraph/dirfresh.py",
     "    except Exception:                                    # noqa: BLE001",
     "    except ZeroDivisionError:  # mutated: no longer catches the real error",
     "6c a catalogue with no journal answers nothing, and does not raise"),

    # Gate 6d: no catalogue at all. The needle returns a wrong *value* rather
    # than raising -- the first version returned `l0_store.db` on a `None`,
    # which is an AttributeError, and the harness correctly reported CRASH
    # instead of a kill. **A crash is not a gate saying no**, and a needle
    # that produces one tests the harness rather than the gate.
    ("no catalogue at all answers with a phantom count instead of nothing",
     "morpho_homegraph/dirfresh.py",
     "    if l0_store is None:\n        return {}",
     "    if l0_store is None:\n"
     "        return {'phantom': 1}  # mutated: invents a pending directory",
     "6d no catalogue at all answers nothing"),

    # Gate 8: absence. A directory we have read nothing in must not be
    # present with zeroes -- the empty layer that looks finished.
    ("directories with no rows are invented and filled with zeroes",
     "morpho_homegraph/dirfresh.py",
     "    grouped: dict[str, Counter[str]] = {}\n"
     "    for path, value in state.items():",
     "    grouped: dict[str, Counter[str]] = {'invented': Counter()}\n"
     "    for path, value in state.items():  # mutated: a phantom directory",
     "8  a directory with no rows is absent, not present with zeroes"),

    # Gate 9b: the tie-break is what makes two runs agree. Sorting on an
    # unstable key reorders equal rows between runs.
    ("the tie-break is dropped, so equal rows can reorder between runs",
     "morpho_homegraph/dirfresh.py",
     "                  key=lambda item: (-item[1][\"not_fresh\"],\n"
     "                                    -item[1][\"pending\"], item[0]))",
     "                  key=lambda item: (-item[1][\"not_fresh\"],\n"
     "                                    -item[1][\"pending\"],"
     " -len(item[0])))  # mutated: length, not path",
     "9  worst first, and equal rows fall in path order"),

    # Gate 4b: the count beside the state. "1 stale" is the actionable half;
    # a bare state name tells the reader nothing about how much.
    ("the line names the state but drops the number",
     "morpho_homegraph/dirfresh.py",
     "            parts.append(\"%d %s\" % (row[\"states\"][name], name))",
     "            parts.append(\"%s\" % name)  # mutated: no count",
     "4b the changed directory says stale, and says how many"),

    # Gate 11a: the tool must be reachable by the name the answer key gives.
    ("the measurement tool is looked for under the wrong name",
     "tests/test_cp23.py",
     '    tool = os.path.join(REPO, "tools", "m8_dir_mixture.py")',
     '    tool = os.path.join(REPO, "tools", "m8_gone.py")  # mutated',
     "11a the R7 measurement is a tool that can be re-run, not a claim"),

]

# Deliberately unmutated, with the reason, rather than left looking forgotten.
# `mutation_coverage.py` will keep reporting these as UNCOVERED, which is the
# honest output: the map is a map, not a score.
#
#   `0  the project builds before anything is asked` -- a guard that reports
#       setup failure and returns early. Any mutation that trips it also
#       trips whichever gate the fixture was built for, so a needle here
#       would only ever produce a misattribution.
#   `12 CONTROL: an ordinary run exits 0` -- every mutation in this file that
#       breaks a command already turns it red as a side effect; aiming one at
#       it deliberately would pin an implementation detail rather than test
#       the property.
#   `6e`/`6f` -- both are already the assertion the `known`-subtraction needle
#       above kills through gate 5 and gate 6h. A third needle at the same
#       line would be a second copy of an existing kill.
WAIVED = (
    "0  the project builds before anything is asked",
    "12 CONTROL: an ordinary run exits 0",
    "6e CONTROL: pending still fires for a file L2 has never read",
    "6f CONTROL: nothing is pending when L2 already holds it all",
    # `11d the number names the tool that produced it` -- the needle for this
    # SURVIVED twice and was withdrawn rather than kept as decoration. It
    # asserts a fact about `TODO.md`, which is git-ignored by design, so the
    # harness's copied tree has no such file and gate 11d SKIPs there: no
    # mutation of the source can turn a skipped gate red. Mutating the check
    # itself only ever weakens it, which makes the suite greener, not redder.
    # The gate is real where it runs -- it is the split of a conjunction that
    # let an unattributed number pass -- but it is unmutatable by construction
    # in this harness, and saying so is better than a needle that lies.
    "11d the number names the tool that produced it, so it is re-runnable",
)


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp23.py", prefix="mhg-mut-cp23-",
                 timeout=900))
