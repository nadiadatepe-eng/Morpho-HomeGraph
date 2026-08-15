#!/usr/bin/env python3
"""Mutation test for CP-18 -- nested `.gitignore`, and the L2/scope comparison.

Three groups.

**The chain (1-6).** The whole risk in reading several `.gitignore` files is
that one of them speaks for the wrong subtree: a nested `*` that empties the
project, a nested rule that leaks sideways, or a chain that forgets the root
once a nested file exists. Each needle breaks the chain in one of those ways.

**Reading once (7-8).** The rule is that a scope is I/O when it is *built* and
never on lookup. A needle that moves the read into `contains` is caught by
counting opens, not by timing -- a timing gate is green on a fast machine.

**The comparison (9-12).** `status` now says when L2 is behind the scope. The
needles make it lie in both directions: never mention drift, or mention it on a
healthy project.

Run:
    python3 tests/mutate_cp18.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the chain ---------------------------------------------------------
    ("nested .gitignore files are collected but never consulted",
     "morpho_homegraph/scope.py",
     "        for base in sorted(self.nested, key=len):\n"
     "            if _under(path, base):\n"
     "                chain.append((base, self.nested[base]))",
     "        # mutated: only the root's patterns are ever used\n"
     "        pass",
     "1  a file a nested .gitignore excludes is out of scope"),

    ("nested patterns are matched against the project root, not their own",
     "morpho_homegraph/scope.py",
     "            relative = os.path.relpath(path, base)\n"
     "            decision = _last_match(relative, is_dir, patterns)",
     "            relative = os.path.relpath(path, self.root)  # mutated\n"
     "            decision = _last_match(relative, is_dir, patterns)",
     "4b an anchored nested pattern is relative to its own directory"),

    ("a nested file applies to the whole project, not its subtree",
     "morpho_homegraph/scope.py",
     "        for base in sorted(self.nested, key=len):\n"
     "            if _under(path, base):",
     "        for base in sorted(self.nested, key=len):\n"
     "            if True:  # mutated: no subtree check",
     "3  CONTROL: a sibling directory without one is unaffected"),

    ("the deepest file is consulted first, so the root wins ties",
     "morpho_homegraph/scope.py",
     "        for base in sorted(self.nested, key=len):",
     "        for base in sorted(self.nested, key=len, reverse=True):",
     "5b the deeper of two nested files decides"),

    ("a file matching nothing counts as a decision to keep it",
     "morpho_homegraph/scope.py",
     "            decision = _last_match(relative, is_dir, patterns)\n"
     "            if decision is not None:\n"
     "                verdict = decision",
     "            decision = _last_match(relative, is_dir, patterns)\n"
     "            verdict = bool(decision)  # mutated: None means keep",
     "6  CONTROL: the root's patterns still apply in that directory"),

    ("the root's own .gitignore drops out of the chain",
     "morpho_homegraph/scope.py",
     "        chain = [(self.root, self.patterns)]",
     "        chain = []  # mutated: only nested files decide",
     "6  CONTROL: the root's patterns still apply in that directory"),

    # -- reading once ------------------------------------------------------
    ("the walk skips .git, so a repo's own storage supplies patterns",
     "morpho_homegraph/scope.py",
     "        dirs[:] = [d for d in dirs if d not in skip]",
     "        dirs[:] = list(dirs)  # mutated: .git is walked too",
     "8c CONTROL: a .gitignore inside .git is not read"),

    # REMOVED after the sweep: collecting the root's own `.gitignore` a second
    # time as a "nested" one is an **equivalent mutant**, not a defect a gate
    # could catch. `_pattern_chain` would then yield `(root, patterns)` twice,
    # and applying the same patterns to the same relative path twice reaches the
    # same verdict -- the only cost is one wasted read. Writing a gate that
    # asserted the read *count* would pin an implementation detail rather than a
    # behaviour, and the next honest refactor would break it. Recorded in
    # FASIT-cp18 as a blind spot instead.

    # -- the comparison ----------------------------------------------------
    ("status never compares L2 against the scope",
     "morpho_homegraph/cli.py",
     '        in_scope = service.scope_size(store.get_meta("project_path"))',
     "        in_scope = None  # mutated: no comparison is ever made",
     "12 when they differ, the line names the update command"),

    ("a missing project root is reported as drift",
     "morpho_homegraph/cli.py",
     '                 "" if in_scope is None or in_scope == rows',
     '                 "" if in_scope == rows  # mutated: None means drift',
     "15 a project whose root is gone is not reported as drift"),

    ("a healthy project is told to run update",
     "morpho_homegraph/cli.py",
     '                 "" if in_scope is None or in_scope == rows',
     '                 "" if in_scope is None  # mutated: always report drift',
     "11 CONTROL: when they match, no update is suggested"),

    ("scope_size answers for a root that is not there",
     "morpho_homegraph/service.py",
     "    if not root or not os.path.isdir(root):\n        return None",
     "    if False:  # mutated: a missing root is walked anyway\n        return None",
     "14 scope_size answers None for a root that is not there"),

    # -- the conditions the detector asked for -----------------------------
    # REMOVED after the sweep, both for the same reason CP-17 removed the
    # migration's role gate: **a guard that cannot be observed failing is a
    # guard that decides nothing.** `cli.py` had `get_meta(...) or ""` in front
    # of a `scope_size` that already answers `None` for a falsy root -- two
    # checks, one decision, neither killable. The `or ""` is gone.
    #
    # `Scope.nested = dict(nested or {})` looked like the same shape and is
    # **not**: `from_folder` and the store loader both construct a Scope with
    # no nested patterns, so `None` genuinely arrives and `dict(None)` would
    # raise. It stays, and it stays unaimed -- the honest note is that it is
    # exercised by every non-repo scope in the suite rather than by a needle.

    ("an unanchored pattern stops matching at any depth",
     "morpho_homegraph/scope.py",
     "    return any(_fnmatch_path(part, pattern) or fnmatch.fnmatch(part, pattern)\n"
     "               for part in _suffixes(relative))",
     "    return _fnmatch_path(relative, pattern)  # mutated: anchored anyway",
     "6  CONTROL: the root's patterns still apply in that directory"),

    ("scope_size counts files the scope excludes",
     "morpho_homegraph/service.py",
     "        total += sum(1 for name in files\n"
     "                     if selected.contains(os.path.join(current, name),\n"
     "                                          is_dir=False))",
     "        total += len(files)  # mutated: the scope no longer filters",
     "13c CONTROL: an excluded file in an included directory is not counted"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp18.py", prefix="mhg-mut-cp18-"))
