#!/usr/bin/env python3
"""M-7 -- how many summaries would a per-directory scheme actually save?

CP-20 borrows OpenViking's idea that a summary should describe a *directory*
rather than a file, on the argument that this makes the index proportional
to the tree rather than to the file count. The harvest report put that as
"the difference between roughly 450 000 summaries and a few thousand".

**That number was never measured. This script measures it.**

The answer decides whether CP-20 is worth building, so it runs before the
checkpoint rather than after -- the CP-19 lesson, which was written up as
`test-the-premise-before-writing-the-spec` after a checkpoint was specified
against a premise that turned out to be false.

Run:
    python3 tools/m7_summary_ratio.py [root ...]
"""
from __future__ import annotations

import collections
import os
import sys

# Directories that no scheme would summarise. Deliberately short: a longer
# list flatters the result, and the point of the measurement is to be
# talked out of the checkpoint if the number does not support it.
SKIP = frozenset((".git", "node_modules", "__pycache__", ".venv",
                  ".mypy_cache", ".pytest_cache"))

# OpenViking's default: above this many direct children, sampling engages.
SAMPLE_SIZE = 32


def walk(root: str) -> tuple[int, int, collections.Counter]:
    files = dirs = 0
    per_dir: collections.Counter = collections.Counter()
    for cur, dnames, fnames in os.walk(root):
        dnames[:] = [d for d in dnames if d not in SKIP]
        dirs += 1
        files += len(fnames)
        per_dir[cur] = len(fnames)
    return files, dirs, per_dir


def report(root: str) -> dict:
    files, dirs, per_dir = walk(root)
    vals = sorted(per_dir.values())
    median = vals[len(vals) // 2] if vals else 0
    over = sum(1 for v in vals if v > SAMPLE_SIZE)
    return {
        "root": root,
        "files": files,
        "dirs": dirs,
        "ratio": files / dirs if dirs else 0.0,
        "median_files_per_dir": median,
        "dirs_over_sample_size": over,
        "pct_over": 100.0 * over / dirs if dirs else 0.0,
    }


def main(argv: list[str]) -> int:
    roots = argv[1:] or [os.path.expanduser("~")]
    rows = [report(r) for r in roots]
    for r in rows:
        print("%-40s files=%-8s dirs=%-7s ratio=%5.1fx  median=%-3s  "
              "dirs>%d=%s (%.1f%%)"
              % (r["root"], "{:,}".format(r["files"]),
                 "{:,}".format(r["dirs"]), r["ratio"],
                 r["median_files_per_dir"], SAMPLE_SIZE,
                 "{:,}".format(r["dirs_over_sample_size"]), r["pct_over"]))
    print()
    print("Read this as: a per-directory scheme writes `dirs` summaries where")
    print("a per-file scheme writes `files`. The saving is the ratio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
