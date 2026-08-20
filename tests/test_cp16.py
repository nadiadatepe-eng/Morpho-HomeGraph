#!/usr/bin/env python3
"""CP-16 -- the detector for conditions nobody aims at.

The answer key is `tests/gold/FASIT-cp16.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

**What is being graded is a borrowed tool plus the debt it names.** Gates 1-11
and 14 grade `tests/condition_coverage.py`: that it runs, that its attribution
is falsifiable rather than a sentence, that it diverges from its source only
in the package name, and -- gate 14, the control -- that it stays quiet about
conditions somebody *did* aim at. Gates 12 and 13 grade the opposite thing:
two compound conditions that now have needles pointed at them, chosen because
both sit in code that has already produced a real defect.

**Gate 5 can report SKIPPED, and that is deliberate.** The source lives in a
sibling repository this one does not depend on. An attribution that silently
passes when it cannot be checked is the sentence R1 refuses; one that fails
when the neighbour is absent would make this suite depend on a checkout it
does not own. So it says so, out loud, and does not count as passed.

Run:
    python3 tests/test_cp16.py
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import store as store_mod  # noqa: E402
from morpho_homegraph import watch as watch_mod  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402

results, check = reporter(64)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tests", "condition_coverage.py")
SOURCE = os.path.expanduser("~/homegraph/tests/condition_coverage.py")

# Measured 2026-08-09, before the code, with a parameterised copy of the
# source run against this package. FASIT R3: a ratchet, not a blocker.
BASELINE_TOTAL, BASELINE_MISSED = 102, 68


def run_tool(*args):
    proc = subprocess.run([sys.executable, TOOL, *args],
                          capture_output=True, text=True, cwd=ROOT)
    return proc.returncode, proc.stdout + proc.stderr


# -- the tool itself (gates 1-3) ------------------------------------------

def gates_tool():
    rc, out = run_tool("--all")
    check("1  the detector exists and runs over the whole package",
          rc == 0 and os.path.isfile(TOOL), "rc=%d" % rc)

    tail = [ln for ln in out.splitlines() if "compound condition" in ln]
    numbers = []
    if tail:
        numbers = [int(w) for w in tail[-1].replace("(s)", "").split()
                   if w.isdigit()]
    check("2  it reports a total and an unaimed count, both computed",
          len(numbers) == 2 and numbers[0] > numbers[1] > 0,
          tail[-1] if tail else "no summary line")

    # Gate 3 lives in `tests/test_cp16_baseline.py`, not here. Found by the
    # needle sweep: it asserts a package-wide *count*, so every mutation that
    # removes a compound condition turns it red before the behavioural gate
    # gets to speak -- three needles aimed at gate 13 were reported as killed
    # by gate 3. A tripwire on the repository and a test of behaviour cannot
    # share a suite that mutation grades.


# -- the attribution (gates 4-6) ------------------------------------------

# The second tool borrowed from the same predecessor, carried over 2026-08-20.
# It gets the same treatment as `condition_coverage.py` rather than a sentence
# of credit, because the local contract says a borrowed file's hash is
# re-hashed by a gate. Named 4b/5b: the answer key numbers requirements, and
# splitting one across two files is refinement rather than a new gate.
MUTCOV = os.path.join(ROOT, "tests", "mutation_coverage.py")
MUTCOV_SOURCE = os.path.expanduser("~/homegraph/tests/mutation_coverage.py")


def gates_second_borrow():
    if not os.path.isfile(MUTCOV):
        return
    text = open(MUTCOV, encoding="utf-8").read()
    found = re.findall(r"\b[0-9a-f]{64}\b", text)
    check("4b the second borrowed tool declares a sha256 for its source",
          len(found) == 1, (found[0][:16] + "...") if found else "none")
    if not os.path.isfile(MUTCOV_SOURCE):
        # SKIPPED, never PASS -- an attribution nobody can falsify is exactly
        # what gate 5 refuses for the first borrow.
        check("5b the second tool's declared sha256 matches its source", False,
              "SKIPPED: %s absent -- unverifiable here" % MUTCOV_SOURCE)
        return
    real = hashlib.sha256(open(MUTCOV_SOURCE, "rb").read()).hexdigest()
    check("5b the second tool's declared sha256 matches its source",
          bool(found) and real == found[0],
          "declared %s / actual %s"
          % ((found[0][:12] if found else "none"), real[:12]))


def gates_attribution():
    text = open(TOOL, encoding="utf-8").read()
    found = re.findall(r"\b[0-9a-f]{64}\b", text)
    declared = found[0] if found else ""
    check("4  the copy declares a sha256 for its source",
          len(found) == 1, declared[:16] + "..." if declared else "none")

    if not os.path.isfile(SOURCE):
        # SKIPPED, never PASS: an attribution nobody can falsify is the
        # sentence R1 refuses. Counted as a failure so the report cannot be
        # read as "the hash checked out".
        check("5  the declared sha256 matches the real source file", False,
              "SKIPPED: %s absent -- attribution unverifiable here" % SOURCE)
        real = None
    else:
        real = hashlib.sha256(open(SOURCE, "rb").read()).hexdigest()
        check("5  the declared sha256 matches the real source file",
              real == declared, "declared %s / actual %s"
              % (declared[:12], real[:12]))

    # Gate 6: R2 as re-decided -- every differing line must be reproduced by
    # substituting the package name back. An allowlist of constants would
    # have been red against a copy with no logic change at all.
    if real is None:
        check("6  the copy diverges from its source in the package name only",
              False, "SKIPPED: source absent")
        return
    # Compare from the first line after each module docstring: the docstring
    # is the one named exception, and the two differ in length there.
    def body(lines):
        end = lines.index('"""', 1) if '"""' in lines[1:] else 0
        return lines[end + 1:]

    a = body(text.splitlines())
    b = body(open(SOURCE, encoding="utf-8").read().splitlines())
    offenders = [(i, x.strip()[:60]) for i, (x, y) in enumerate(zip(a, b))
                 if x != y and x.replace("morpho_homegraph", "homegraph") != y]
    check("6  the copy diverges from its source in the package name only",
          not offenders and len(a) == len(b),
          "offenders=%s len %d/%d" % (offenders[:2], len(a), len(b)))


# -- what it reports, and what it stays quiet about (gates 7-11, 14) ------

def gates_reporting(work):
    pkg = os.path.join(work, "morpho_homegraph")
    os.makedirs(pkg)
    subprocess.run(["git", "init", "-q", work], check=True)
    subprocess.run(["git", "-C", work, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", work, "config", "user.name", "t"], check=True)
    tool_dir = os.path.join(work, "tests")
    os.makedirs(tool_dir)
    src = open(TOOL, encoding="utf-8").read()
    src = src.replace('HERE = os.path.dirname(os.path.abspath(__file__))',
                      'HERE = %r' % tool_dir)
    src = src.replace('ROOT = os.path.dirname(HERE)', 'ROOT = %r' % work)
    open(os.path.join(tool_dir, "cc.py"), "w", encoding="utf-8").write(src)
    open(os.path.join(tool_dir, "mutate_x.py"), "w", encoding="utf-8").write("")

    def write(name, text):
        """Write and stage. `git diff HEAD` is blind to untracked files, so a
        fixture that only writes measures nothing -- found by gate 8 going
        green for the wrong reason on the first run."""
        open(os.path.join(pkg, name), "w", encoding="utf-8").write(text)
        subprocess.run(["git", "-C", work, "add", "-A"], check=True)

    def tool(*args):
        p = subprocess.run([sys.executable, os.path.join(tool_dir, "cc.py"),
                            *args], capture_output=True, text=True, cwd=work)
        return p.returncode, p.stdout + p.stderr

    write("plain.py", "def f(a):\n    return a + 1\n")
    subprocess.run(["git", "-C", work, "commit", "-qm", "base"], check=True)

    # Real changed lines, none of them compound: green because it looked and
    # found nothing, not because there was nothing to look at.
    write("plain.py", "def f(a):\n    return a + 2\n\n\ndef h(x):\n    return x\n")
    rc, out = tool("--since", "HEAD")
    check("7  a diff with real changed lines but no compound condition passes",
          rc == 0 and "no changed lines" not in out,
          out.strip().splitlines()[-1][:60] if out.strip() else "")

    write("gap.py", "def g(a, b):\n    return a and b\n")
    rc, out = tool("--since", "HEAD")
    check("8  an unaimed compound condition fails and names the line",
          rc == 1 and "return a and b" in out and "gap.py" in out,
          "rc=%d" % rc)

    # Gate 9 / 14: the same line, now aimed at by a needle. The control that
    # keeps gate 8 from being green because everything is reported.
    open(os.path.join(tool_dir, "mutate_x.py"), "w", encoding="utf-8").write(
        '("aimed", "morpho_homegraph/gap.py", "    return a and b", "x", "1")\n')
    rc, out = tool("--since", "HEAD")
    check("9  a condition whose line a mutate_*.py carries counts as aimed at",
          rc == 0, "rc=%d" % rc)
    check("14 CONTROL: an aimed-at condition is not reported",
          "return a and b" not in out, out.strip()[:60])

    # Gates 10, 11: waivers.
    open(os.path.join(tool_dir, "mutate_x.py"), "w", encoding="utf-8").write("")
    write("gap.py", "def g(a, b):\n    # condition-coverage:\n"
                    "    return a and b\n")
    rc, _ = tool("--since", "HEAD")
    empty_refused = rc == 1
    write("gap.py", "def g(a, b):\n"
                    "    # condition-coverage: b is a constant here\n"
                    "    return a and b\n")
    rc_ok, _ = tool("--since", "HEAD")
    check("10 a waiver with a reason clears it, an empty one does not",
          empty_refused and rc_ok == 0,
          "empty rc=%d / reasoned rc=%d" % (1 if empty_refused else 0, rc_ok))

    write("gap.py", "def g(a, b):\n"
                    "    # condition-coverage: two lines above\n"
                    "    x = 1\n"
                    "    return a and b and x\n")
    rc, _ = tool("--since", "HEAD")
    check("11 a waiver two lines above the condition still counts", rc == 0,
          "rc=%d" % rc)


# -- the debt this checkpoint actually pays down (gates 12, 13) -----------

def gates_debt(work):
    """`watch.relevant` and the `Store` guards, with all operands exercised.

    Both were chosen because a fixture hole here has already cost something:
    CP-13 gate 6 found the service updating for ever over its own store
    directory, and locked decision #12 rests on the second.
    """
    db = os.path.join(work, "l0", "index.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    ignore = [db]
    # Each operand of `p == db or p.startswith(db + "-") or p.startswith(db + ".")`
    # gets an input only it refuses, so dropping any one of the three shows up.
    exact = not watch_mod.relevant(db, ignore)
    wal = not watch_mod.relevant(db + "-wal", ignore)
    lock = not watch_mod.relevant(db + ".lock", ignore)
    other = watch_mod.relevant(os.path.join(work, "notes.md"), ignore)
    check("12 relevant() refuses the store, its -wal and its .lock, and only those",
          exact and wal and lock and other,
          "exact=%s wal=%s lock=%s other=%s" % (exact, wal, lock, other))

    # Each operand of the two guards gets an input only it refuses:
    #   read_only and not exists   -> read-only open of a missing file
    #   not read_only and not holds -> writable open without the lock
    # plus the two positive controls, so dropping either operand shows up.
    def refuses(path, expected, **kw):
        """True when opening `path` raises exactly `expected`.

        Catching `Exception` rather than the one class: a mutation that swaps
        which guard fires would otherwise escape this fixture and take the
        suite down in front of the gate instead of turning it red.
        """
        try:
            store_mod.Store(path, role=store_mod.L0, **kw)
            return False
        except Exception as exc:
            return isinstance(exc, expected)

    missing = os.path.join(work, "nope", "index.db")
    ro_missing = refuses(missing, FileNotFoundError, read_only=True)

    live = os.path.join(work, "live", "index.db")
    os.makedirs(os.path.dirname(live), exist_ok=True)
    unguarded = refuses(live, store_mod.Unguarded)

    # Every open below is caught. A mutation that makes one of them raise must
    # turn this gate red, not kill the suite in front of it -- two of these
    # were reported as CRASH-ONLY on the first sweep, and a crash names no
    # gate.
    def opens(**kw):
        try:
            with store_mod.Store(live, role=store_mod.L0, **kw) as st:
                if kw.get("read_only"):
                    return st.get_meta("k") == "v"
                st.set_meta("k", "v")
                return True
        except Exception:
            return False

    lock = StoreLock(live).acquire()
    try:
        created = opens() and os.path.exists(live)
        ro_held = opens(read_only=True)
    finally:
        lock.release()
    # The one the first sweep let through: reading while the lock was held
    # cannot tell "the write guard is for writers" from "the write guard is
    # for everyone". Released, it can.
    ro_free = opens(read_only=True)

    check("13 the Store guards: read-only needs the file, writable needs the lock",
          ro_missing and unguarded and created and ro_held and ro_free,
          "ro_missing=%s unguarded=%s created=%s ro_held=%s ro_free=%s"
          % (ro_missing, unguarded, created, ro_held, ro_free))


def gates_needles():
    """Gate 15 is discharged by `tests/mutate_cp16.py`, not from here.

    Running the mutation sweep inside the suite it mutates is how a harness
    ends up grading a copy of itself; the driver runs in its own tree.
    """
    have = os.path.isfile(os.path.join(ROOT, "tests", "mutate_cp16.py"))
    check("15 the needle file exists (run it separately: mutate_cp16.py)",
          have, "python3 tests/mutate_cp16.py")


def main() -> int:
    gates_tool()
    gates_attribution()
    gates_second_borrow()
    with tempfile.TemporaryDirectory(prefix="mhg-cp16-") as work:
        gates_reporting(os.path.join(work, "repo"))
    with tempfile.TemporaryDirectory(prefix="mhg-cp16b-") as work:
        gates_debt(work)
    gates_needles()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp16():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
