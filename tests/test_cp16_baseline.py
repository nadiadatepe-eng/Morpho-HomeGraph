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
BASELINE_TOTAL, BASELINE_MISSED = 102, 65


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
