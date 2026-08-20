#!/usr/bin/env python3
"""R7 for CP-23: is a per-directory freshness view saying anything new?

Run **before** CP-23 exists, for the same reason `m7_summary_ratio.py` ran
before CP-20: the idea is only worth its maintenance if the number is.

The question is not "does grouping work" -- grouping always works. It is
whether directories are *mixed*: whether a directory's direct children hold
more than one of CP-12's four states. A tree where nearly every directory is
one colour makes the per-directory view a rewriting of the per-file view,
and then CP-23 is CP-20's fate.

    python3 tools/m8_dir_mixture.py [project ...]
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho_homegraph import freshness  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, db_path, l0_path, projects)


def measure(project_id: str) -> dict:
    path = db_path(project_id)
    with Store(path, read_only=True, role=PROJECT) as store:
        l0 = None
        if l0_path().is_file():
            l0 = Store(l0_path(), read_only=True, role=L0)
        try:
            state = freshness.per_file(store, l0)
        finally:
            if l0 is not None:
                l0.close()
    per_dir = defaultdict(Counter)
    for file_path, value in state.items():
        per_dir[os.path.dirname(file_path)][value] += 1
    mixed = sum(1 for c in per_dir.values() if len(c) > 1)
    not_fresh = sum(1 for c in per_dir.values()
                    if sum(v for k, v in c.items() if k != freshness.FRESH))
    return {"files": len(state), "dirs": len(per_dir), "mixed": mixed,
            "not_fresh_dirs": not_fresh,
            "states": Counter(state.values())}


def main(argv: list[str]) -> int:
    ids = argv[1:] or [project_id for project_id, _path in projects()]
    print("%-20s %7s %7s %7s %9s  %s"
          % ("project", "files", "dirs", "mixed", "not-fresh", "states"))
    for project_id in ids:
        try:
            got = measure(project_id)
        except Exception as err:                       # noqa: BLE001
            print("%-20s  unreadable: %s" % (project_id, err))
            continue
        print("%-20s %7d %7d %7d %9d  %s"
              % (project_id, got["files"], got["dirs"], got["mixed"],
                 got["not_fresh_dirs"], dict(got["states"])))
        if got["dirs"]:
            print("%-20s  mixed %.1f %% of directories"
                  % ("", 100.0 * got["mixed"] / got["dirs"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
