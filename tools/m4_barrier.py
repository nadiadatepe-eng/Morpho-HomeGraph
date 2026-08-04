#!/usr/bin/env python3
"""M-4: how often the write barrier refuses, under real use.

Ordered 2026-08-04 by the question "why not Postgres". The answer turns on one
number we do not have, and this is the tool that accumulates it.

**What is counted is refusals, not waiting.** `lock.py` line 34 is explicit:
"It refuses; it does not queue." `LOCK_EX | LOCK_NB`, and the loser gets
`Locked` with the holder's pid. The wait is always zero, so there is no write
queue in seconds to measure. One refusal is precisely what Postgres would turn
into a concurrent transaction -- it is the gain from switching, in the right
unit.

**Two independent signals, because one of them can be silent:**

  * `refused` -- the CLI's own answer. `_guard_or_refuse` prints REFUSED and
    exits 2, which *is* the barrier saying no.
  * `flock_eagain` -- what the kernel saw. It catches a refusal that never
    reached an exit code, and it is the reason this runs under strace at all.

**`--seccomp-bpf`, not plain strace.** Measured 2026-08-04 on a 20 355-entry
scan: 0.52 s untraced, 0.52 s with seccomp filtering, 1.95 s with ordinary
strace. A 3.75x tax on the thing being measured would change the answer.

**The exposure denominator is recorded with every row**, because a zero from an
instrument that was never exposed is not a measurement. `seconds` is how long
the guard was held; the barrier can only refuse someone during those seconds.
Sum the column before quoting the count.

`ptrace_scope=1` on this machine, so strace has to be the *parent* -- attaching
to a service that is already running needs root, and nothing here does.

    python3 tools/m4_barrier.py [--root ~]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TALLY = Path.home() / ".local/state/morpho-homegraph/m4-barrier.tsv"
COLUMNS = ("when", "command", "exit", "seconds", "refused", "flock_eagain",
           "projects")


def traced(args: list[str]) -> tuple[int, float, int]:
    """Run one CLI command under strace. Returns (exit, seconds, EAGAIN count)."""
    with tempfile.NamedTemporaryFile(prefix="m4-flock-", suffix=".log",
                                     delete=False) as handle:
        log = handle.name
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["strace", "--seccomp-bpf", "-f", "-e", "trace=flock", "-o", log,
             sys.executable, "-m", "morpho_homegraph.cli", *args],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        seconds = time.monotonic() - started
        eagain = 0
        with open(log, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # Only flock lines: EAGAIN from any other syscall is not this
                # barrier, and counting it would inflate the number that
                # decides an architecture.
                if "flock(" in line and ("EAGAIN" in line
                                         or "EWOULDBLOCK" in line):
                    eagain += 1
    finally:
        os.unlink(log)
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode, seconds, eagain


def record(row: dict) -> None:
    TALLY.parent.mkdir(parents=True, exist_ok=True)
    fresh = not TALLY.exists()
    with open(TALLY, "a", encoding="utf-8") as fh:
        if fresh:
            fh.write("\t".join(COLUMNS) + "\n")
        fh.write("\t".join(str(row[name]) for name in COLUMNS) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="~",
                        help="what L0 catalogues (default: the home area)")
    args = parser.parse_args(argv)

    from morpho_homegraph import identity

    # The scan first: L0 is the shared store and the long-held guard, so it is
    # the writer anything else in this account would collide with.
    work = [("scan", ["scan", args.root])]
    living = identity.living_projects()
    # Every living project, snapshotted and pruned. Zero of them is a real
    # answer and is recorded as one -- `add <dir>` is what changes it, and this
    # picks the project up on the next run without being edited.
    work += [("snapshot", ["snapshot", pid]) for pid, _path in living]

    for label, argv_for_cli in work:
        code, seconds, eagain = traced(argv_for_cli)
        record({
            "when": datetime.now().isoformat(timespec="seconds"),
            "command": label,
            "exit": code,
            "seconds": "%.2f" % seconds,
            "refused": 1 if code == 2 else 0,
            "flock_eagain": eagain,
            "projects": len(living),
        })
    return 0


if __name__ == "__main__":
    sys.exit(main())
