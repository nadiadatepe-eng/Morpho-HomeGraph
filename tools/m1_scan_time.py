#!/usr/bin/env python3
"""M-1: what an L0 pass over the whole home area costs.

The ground fact the whole design rests on is that metadata is nearly free --
628 862 files in 0.23 s. **That is a warm-cache number**, and a warm number
answers the question "how expensive is the second scan". The one that decides
whether the periodic refresh in CP-13 is affordable is the cold one, taken
after the page cache has been dropped, which needs root:

    sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
    python3 tools/m1_scan_time.py --label cold

Run without dropping the cache and the label says so. A cold number reported
as warm, or the reverse, is worse than no number: it gets quoted.

Reports peak RSS as well as time, because a walk over 600 000 entries that
materialises them costs memory the 0.23 s figure says nothing about.

    python3 tools/m1_scan_time.py [--label warm|cold] [path]
"""
from __future__ import annotations

import os
import resource
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho_homegraph.scan import walk  # noqa: E402


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
    for _path, kind, *_rest in walk(root):
        if kind == "unreadable":
            unreadable += 1
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
    started = time.perf_counter()
    proc = subprocess.run(["find", root, "-mindepth", "1"],
                          capture_output=True, text=True)
    found = len([ln for ln in proc.stdout.splitlines() if ln])
    note = " -- warm, the walk above filled the cache" if label == "cold" else ""
    say("find            %d entries in %.2f s  (difference %+d)%s"
          % (found, time.perf_counter() - started, rows - found, note))
    if label == "cold":
        say("\nthe walk is the cold pass; every number after it is warm.")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n---\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
