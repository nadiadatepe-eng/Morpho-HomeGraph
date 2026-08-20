#!/usr/bin/env python3
"""Which gates does an answer key name that no check reports?

`mutation_coverage.py` asks which *checks* nobody aimed a mutation at.
`condition_coverage.py` asks which *branches* nobody aimed a mutation at.
This asks the question one step earlier, from the answer key's side: **which
requirements did we write down and then not gate at all.**

**The axis exists because it caught something the other two could not.**
CP-23's `FASIT-cp23.md` listed twelve numbered gates and `test_cp23.py`
reported eleven. Gate 11 -- the measurement that can say *this checkpoint was
not worth building* -- had no check. The measurement had in fact been taken
and written into `TODO.md`, so the result was sound and every other tool was
green: a mutation map cannot miss a check that does not exist, and a condition
map only sees code that was written. A requirement met in fact and unenforced
still reads as coverage, and that is the failure this file exists to end.

**It reads the run, not the source.** Grepping for `check(` reports zero for
`test_cp22.py`, which builds its names in a loop and gates ten -- so a source
scan would have declared the healthiest module the emptiest. Each module is
executed and the gate numbers it actually prints are parsed from its report
lines. That costs a full suite run, which is why this is a tool you invoke
rather than a gate in the suite.

An answer key with no numbered gate list is skipped rather than reported at
zero: `FASIT-cp22.md` states its gates in prose, and reporting it as uncovered
would be a false positive that trains the reader to ignore the output.

**One known false positive, measured 2026-08-20 and named rather than
suppressed.** `FASIT-cp15.md` gate 14 reads "CP-14 gate 11 is rewritten" -- a
requirement about *another checkpoint's* gate, satisfied in `test_cp14.py`
where that gate lives and passes. This tool matches within one stem, so it
reports gate 14 as ungated. Teaching it to follow "CP-n gate m" prose would
make it a parser of English, which is a worse tool; the honest shape is a map
with one entry a reader has to know about, not a silent special case. Gate 15
is the same sentence about CP-14 gate 12 and happens to collide with a number
`test_cp15.py` does report, which is exactly why the exception is written down
here rather than trusted to be noticed twice.

Run:
    python3 tests/gate_coverage.py [cp23 cp12 ...]      # default: all
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# A gate line as `report.py` prints it: `PASS  11b ...` or `FAIL  4  ...`.
# The number is what an answer key numbers, and the suffix letter is ours --
# `11a`, `11b`, `11c` all satisfy the key's gate 11, because splitting one
# requirement into several checks is refinement, not a gap.
REPORTED = re.compile(r"^(?:PASS|FAIL|SKIP)\s+(\d+)")


def key_gates(path: str) -> set[int]:
    """The numbered gates under an answer key's `## Gatene` heading."""
    body = open(path, encoding="utf-8").read()
    match = re.search(r"^## Gatene\n(.*?)(?=^## )", body, re.S | re.M)
    if not match:
        return set()
    return {int(n) for n in re.findall(r"^(\d+)\.", match.group(1), re.M)}


def reported_gates(test: str) -> tuple[set[int], int]:
    """Run the module; return the gate numbers it printed and its exit code."""
    try:
        proc = subprocess.run([sys.executable, test], capture_output=True,
                              text=True, cwd=ROOT, timeout=1800)
    except subprocess.TimeoutExpired:
        return set(), 124
    found = set()
    for line in proc.stdout.splitlines():
        hit = REPORTED.match(line)
        if hit:
            found.add(int(hit.group(1)))
    return found, proc.returncode


def stems(argv: list[str]) -> list[str]:
    if argv:
        return argv
    found = []
    for path in glob.glob(os.path.join(HERE, "gold", "FASIT-*.md")):
        found.append(os.path.basename(path)[len("FASIT-"):-len(".md")])
    return sorted(found)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    gaps = 0
    for stem in stems(argv):
        key = os.path.join(HERE, "gold", "FASIT-%s.md" % stem)
        test = os.path.join(HERE, "test_%s.py" % stem)
        if not (os.path.isfile(key) and os.path.isfile(test)):
            continue
        want = key_gates(key)
        if not want:
            # Prose rather than a numbered list. Named, not counted as zero:
            # a false positive here is worse than a miss, because it teaches
            # the reader to skim past the real ones.
            print("%-8s answer key states its gates in prose -- not mapped"
                  % stem)
            continue
        got, code = reported_gates(test)
        missing = sorted(want - got)
        gaps += len(missing)
        print("%-8s key %2d gates, run reports %2d%s%s"
              % (stem, len(want), len(got),
                 "" if code == 0 else "  (exit %d)" % code,
                 "" if not missing
                 else "  UNGATED: %s" % ", ".join(str(n) for n in missing)))
    print("\n%d requirement(s) named by an answer key with no check reporting "
          "them" % gaps)
    # Deliberately not an exit code: this is a map, like the other two
    # coverage tools, and a number that fails CI would get a waiver list
    # instead of an answer.
    return 0


if __name__ == "__main__":
    sys.exit(main())
