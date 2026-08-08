#!/usr/bin/env python3
"""One mutation driver for every harness. Not a harness itself.

Borrowed from homegraph's `tests/mutate.py` (registered in the borrow ledger),
where it replaced 22 drifted copies of the same loop. Three of the ways they
had drifted are the reason it is one file here from the start: a missing
`CRASH-ONLY` block in 8 of 22, two different summary formats that a script
could read as "439 killed, of 0 mutations", and `.git` copied into the
mutation tree 442 times a sweep.

A harness uses it like this, and otherwise owns only its `MUTATIONS` list:

    from mutate import run
    if __name__ == "__main__":
        sys.exit(run(MUTATIONS, "test_cp0.py", prefix="mut0-", timeout=600))

Run this file directly to audit every harness's needles against the current
source in seconds:

    python3 tests/mutate.py

**Why that mode exists, measured 2026-08-08:** 4 of 309 needles no longer
matched the source they named. CP-10 moved the fusion into the package and
left CP-9E's two needles pointing at `tools/cp9e_eval.py`; CP-12 edited the
two lines CP-7B and CP-10 had pinned. `run()` scores a missing needle as a
survivor, which is right -- but only after the full sweep, and the sweep is
900 s per harness. So the recorded "0 survivors" in `TODO.md` stayed true on
the page and false on disk for three days. A check that costs seconds is one
that gets run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDTH = 52

# Never copied into a mutation tree, by any harness. `node_modules` arrived
# with CP-9 and weighs 252 MB: copying it once per mutation is gigabytes of
# I/O to change one line of Python, and no mutation ever edits it.
ALWAYS_IGNORED = ("__pycache__", ".git", ".venv", "node_modules")

# ...which leaves the mutated tree without the embedding library, so the
# driver says where the real one is. It belongs here rather than in a harness:
# only the driver knows the unmutated tree, and a path written inside a test
# would be one machine's home directory.
os.environ.setdefault("MHG_NODE_MODULES", os.path.join(ROOT, "node_modules"))


def run_suite(tree, test_file, timeout):
    """Run one test script in a mutated tree. Return the set of red checks.

    `<timeout>` and `<crash> ...` are in the same set on purpose: the caller
    tells them from a real gate refusal by the prefix, and that distinction is
    the whole point of the harness. A crash is not a gate saying no.

    The name is read from `report.py`'s `FAILED\\t<name>` line, never from the
    formatted report -- see that module for why no whitespace split works.
    """
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", test_file)],
            capture_output=True, text=True, cwd=tree, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAILED\t"):
            red.add(line[len("FAILED\t"):])
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red


def run(mutations, test_file, *, prefix, timeout=300, ignore=()):
    """Run each mutation against `test_file`. Return 1 if any survived or crashed."""
    survived, killed, misattributed, crashes = [], [], [], []
    patterns = ALWAYS_IGNORED + tuple(ignore)

    for name, rel, needle, repl, expected in mutations:
        tree = tempfile.mkdtemp(prefix=prefix)
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns(*patterns))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            with open(target, encoding="utf-8") as fh:
                src = fh.read()
            if needle not in src:
                # Counts as a survivor, not as skipped: a needle that is not
                # there means the mutation was never tried, and an untried
                # mutation is evidence of nothing.
                print("SKIP      %-*s needle missing in %s" % (WIDTH, name, rel))
                survived.append((name, "needle missing"))
                continue
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(needle, repl, 1))

            red = run_suite(work, test_file, timeout)
            crashed = any(r.startswith("<crash>") or r == "<timeout>" for r in red)
            gate_red = [r for r in red
                        if not r.startswith("<crash>") and r != "<timeout>"]

            if not red:
                print("SURVIVED  %-*s suite still green" % (WIDTH, name))
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-*s -> %s" % (WIDTH, name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-*s -> %s (expected %r)"
                      % (WIDTH, name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-*s -> %s" % (WIDTH, name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-*s unclassified" % (WIDTH, name))
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(mutations)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


# -- the needle audit -------------------------------------------------------

def check() -> int:
    """Every harness's needles, matched against the source they name.

    Two ways a needle rots, and both are silent:

    **Gone.** The code moved and the mutation is never applied. `run()` calls
    that a survivor, correctly, but only after the sweep it was supposed to
    make cheap.

    **Doubled.** `run()` uses `replace(needle, repl, 1)`, so a needle that
    matches twice mutates whichever copy comes first in the file -- which may
    be a function no gate in that harness is looking at. `mutate_cp7b.py`
    documents having reached into a `try:` block on purpose to avoid exactly
    that, which is the evidence it happens.

    Reports both and returns 1 if either is found. Runs no tests and copies no
    trees: this is the check you can afford before every commit.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    gone, doubled, total = [], [], 0
    for name in sorted(os.listdir(here)):
        if not (name.startswith("mutate_") and name.endswith(".py")):
            continue
        module = __import__(name[:-3])
        for label, rel, needle, _repl, _expected in getattr(module,
                                                            "MUTATIONS", []):
            total += 1
            try:
                with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                    src = fh.read()
            except OSError as exc:
                gone.append((name, label, "%s: %s" % (rel, exc.strerror)))
                continue
            hits = src.count(needle)
            if hits == 0:
                gone.append((name, label, "no match in %s" % rel))
            elif hits > 1:
                doubled.append((name, label, "%d matches in %s" % (hits, rel)))
    for heading, rows in (("MISSING -- never applied, scored as a survivor",
                           gone),
                          ("AMBIGUOUS -- replace(..., 1) picks the first",
                           doubled)):
        if rows:
            print("%s:" % heading)
            for harness, label, why in rows:
                print("  %-16s %-46s %s" % (harness, label[:46], why))
    print("%d needles checked, %d missing, %d ambiguous"
          % (total, len(gone), len(doubled)))
    return 1 if (gone or doubled) else 0


if __name__ == "__main__":
    sys.exit(check())
