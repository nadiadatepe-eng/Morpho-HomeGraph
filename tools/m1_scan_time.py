#!/usr/bin/env python3
"""M-1: what an L0 pass over the whole home area costs.

The claim this tool was written to test was that metadata is nearly free --
"628 862 files in 0.23 s", inherited from the plan. **It did not survive the
measurement.** Run 2026-08-03: 729 343 entries in 5.13 s warm, 7.51 s cold, and
`find` over the same tree takes 1.12 s. The 0.23 s figure reproduces in no form
and its origin is unknown. The conclusion holds on the corrected numbers -- 7.5 s
for the whole home area is still nearly free next to 220-317 s to vectorise one
project -- but the number below is the measured one, not the quoted one.

A warm number answers "how expensive is the second scan". The one that decides
whether the periodic refresh in CP-13 is affordable is the cold one, taken
after the page cache has been dropped, which needs root:

    sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
    python3 tools/m1_scan_time.py --label cold

Run without dropping the cache and the label says so. A cold number reported
as warm, or the reverse, is worse than no number: it gets quoted.

Reports peak RSS as well as time, because a walk over 600 000 entries that
materialises them costs memory no timing figure says anything about.

    python3 tools/m1_scan_time.py [--label warm|cold] [path]
"""
from __future__ import annotations

import os
import resource
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho_homegraph.scan import DEFAULT_DENY, walk  # noqa: E402


def main(argv: list[str]) -> int:
    # Everything printed is also appended to this file. A cold number costs a
    # cache drop and a terminal that can ask for a password, and losing one
    # because it only ever existed in a scrollback is a needless second run.
    log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m1-runs.txt")
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    label = "unlabelled"
    if "--label" in argv:
        i = argv.index("--label")
        label = argv[i + 1]
        del argv[i:i + 2]
    root = os.path.expanduser(argv[0] if argv else "~")

    started = time.perf_counter()
    rows = unreadable = 0
    kinds: dict[str, int] = {}
    pruned = 0
    for _path, kind, *_rest in walk(root):
        if kind == "unreadable":
            unreadable += 1
            continue
        if kind == "pruned":
            # Counted, but not a row: `find` with the same prunes does not
            # print them either, and the difference below is the confirmation.
            pruned += 1
            continue
        rows += 1
        kinds[kind] = kinds.get(kind, 0) + 1
    elapsed = time.perf_counter() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    say("root            %s" % root)
    say("cache           %s" % label)
    say("entries         %d  (%s)"
          % (rows, ", ".join("%s %d" % kv for kv in sorted(kinds.items()))))
    say("unreadable dirs %d" % unreadable)
    say("pruned paths    %d  (deny-list fired this many times)" % pruned)
    say("time            %.2f s  ->  %.0f entries/s" % (elapsed, rows / elapsed))
    say("peak rss        %.0f MB  (counting only, nothing stored)" % rss)

    # `find` over the same tree, as the independent count. It is a second
    # opinion, not a gate: the home area changes between the two passes, and
    # a difference of a few entries is the tree moving, not a bug.
    #
    # Its *time* is only comparable in a warm run. In a cold one the walk
    # above has just pulled the whole tree into the page cache, so `find`
    # runs warm no matter what the label says -- only one of the two can be
    # the cold pass, and it is the one being measured.
    # `find` must be given the same prunes, or the comparison stops being a
    # confirmation and becomes a difference of 300 000 that someone will quote.
    # `-prune` on each denied path, then `-o -print` for everything else.
    prunes: list[str] = []
    for d in DEFAULT_DENY:
        expanded = os.path.expanduser(d).rstrip(os.sep)
        if prunes:
            prunes.append("-o")
        prunes += ["-path", expanded, "-prune"]
    argv_find = ["find", root, "-mindepth", "1"]
    argv_find += (prunes + ["-o", "-print"]) if prunes else []
    started = time.perf_counter()
    proc = subprocess.run(argv_find, capture_output=True, text=True)
    found = len([ln for ln in proc.stdout.splitlines() if ln])
    note = " -- warm, the walk above filled the cache" if label == "cold" else ""
    say("find            %d entries in %.2f s  (difference %+d)%s"
          % (found, time.perf_counter() - started, rows - found, note))
    say("                same deny-list applied: %s" % ", ".join(DEFAULT_DENY))
    if label == "cold":
        say("\nthe walk is the cold pass; every number after it is warm.")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n---\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
