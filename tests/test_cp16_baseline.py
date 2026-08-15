#!/usr/bin/env python3
"""CP-16 gate 3 -- the baseline, alone, on purpose.

Split out of `tests/test_cp16.py` on 2026-08-09, and the reason is the whole
value of the file. Gate 3 asserts a **package-wide count**: 102 compound
conditions, 68 of them unaimed. Every mutation that removes a compound
condition changes that count, so inside the graded suite gate 3 turned red
first and three needles aimed at gate 13 came back as `misattrib`. The gate
was not wrong; it was in the wrong room.

**A tripwire on the repository and a test of behaviour cannot share a suite
that mutation grades.** The first is true of the code as it stands today and
moves whenever anyone edits anything; the second is true of what the code
does. Mixing them makes the first shadow the second.

The ratchet is unchanged: drift means a new compound condition arrived without
a needle, and the answer is to write the needle or explain the number in
`TODO.md` -- never to edit the constants here quietly.

Run:
    python3 tests/test_cp16_baseline.py
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report import reporter  # noqa: E402

results, check = reporter(64)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tests", "condition_coverage.py")

# Measured 2026-08-09 **before** the code: 102 / 68.
# Re-baselined the same day to 102 / 65, and the three are named rather than
# absorbed -- CP-16 R4 pointed needles at `watch.relevant` and both `Store`
# guards. Debt paid, not re-labelled. Never edit this pair without writing the
# reason in TODO.md; a number that moves quietly is the thing the gate exists
# to prevent.
#
# 2026-08-15, CP-17: 102 / 65 -> 105 / 66. Three new compound conditions
# (`backfill.backfill`'s ceiling check and two in `coverage`), and the
# detector immediately found a fourth in `Store.migrate` that had nothing
# aimed at it. **The debt was paid, not carried:** needles were written for
# all of them, and the migration pair turned out to be mutually redundant --
# `PRAGMA table_info` already returns nothing for a table the role does not
# have, so the role gate could not be observed failing and was removed. One
# condition fewer than the naive count, and the one that stayed is aimed at.
#
# 105 / 66 -> 106 / 67 the same day: `backfill._has_hash_source` is a probe the
# real catalogue asked for -- `status` opens L0 read-only, never migrates, and
# the coverage query died with `no such column` on a store built before today.
# The needle for it is in `mutate_cp17.py`, aimed at gate 18, so the *missed*
# count does not move with the total: one condition more, none of it unaimed.
# Debt paid the same hour it was taken.
#
# 2026-08-15, CP-18: 106 / 66 -> 111 / 64. Five new compound conditions (the
# nested-`.gitignore` chain, `scope_size`, the L2/scope comparison), and the
# missed count went **down by two** while the total rose by five. That is not
# an accounting trick: CP-18's sweep found two guards that could not be
# observed failing and they were deleted rather than gated (`or ""` in front of
# a `scope_size` that already answers `None`; a duplicated match loop in
# `_last_match` folded back into `_hits`). Two of the five are waived at the
# line with a reason -- a display fallback, and a `or {}` whose removal raises
# before any gate can speak.
BASELINE_TOTAL, BASELINE_MISSED = 111, 64


def main() -> int:
    proc = subprocess.run([sys.executable, TOOL, "--all"],
                          capture_output=True, text=True, cwd=ROOT)
    tail = [ln for ln in proc.stdout.splitlines() if "compound condition" in ln]
    numbers = [int(w) for w in tail[-1].replace("(s)", "").split()
               if w.isdigit()] if tail else []
    check("3  the baseline measured before the code still holds",
          numbers[:2] == [BASELINE_TOTAL, BASELINE_MISSED],
          "measured %s, baseline [%d, %d]%s"
          % (numbers[:2], BASELINE_TOTAL, BASELINE_MISSED,
             "" if numbers[:2] == [BASELINE_TOTAL, BASELINE_MISSED]
             else " -- write the needle, or explain it in TODO.md"))

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_cp16_baseline():
    assert main() == 0, "see the printed report above"


if __name__ == "__main__":
    sys.exit(main())
