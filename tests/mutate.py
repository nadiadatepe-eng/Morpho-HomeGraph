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
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDTH = 52

# Never copied into a mutation tree, by any harness.
ALWAYS_IGNORED = ("__pycache__", ".git", ".venv")


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
