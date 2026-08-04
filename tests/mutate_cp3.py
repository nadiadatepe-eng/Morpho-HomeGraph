#!/usr/bin/env python3
"""Mutation test for CP-3 -- the scope selector.

A scope predicate is easy to gate badly, because two useless answers pass
most gates: one that says yes to everything, and one that says no to
everything. Half the mutations below are exactly those, aimed at the gates
that are supposed to notice.

Run:
    python3 tests/mutate_cp3.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- .git is pruned in a repo too (R6b, added after CP-4 measured it) --
    #
    # Removing this leaves every gate green except 17, and puts 52 % of L2
    # back -- measured on ~/homegraph 2026-08-04. A defect that makes the
    # layer *bigger* is the kind nothing else notices.
    ("a repo's own .git stays in the scope",
     "morpho_homegraph/scope.py",
     "    scope = Scope().add(root, INCLUDE).add(os.path.join(root, \".git\"), EXCLUDE)",
     "    scope = Scope().add(root, INCLUDE)  # mutated",
     "17 .git is pruned in a repo, .github is not"),

    # The prefix trap, in the scope this time: `.github` starts with `.git`,
    # and a rule that compares characters rather than path segments eats it.
    ("the .git exclusion matches on characters, not path segments",
     "morpho_homegraph/scope.py",
     "def _under(path: str, root: str) -> bool:",
     "def _under(path: str, root: str) -> bool:\n"
     "    return path.startswith(root)  # mutated",
     "17 .git is pruned in a repo, .github is not"),

    # -- the innermost rule wins, in both directions ----------------------
    ("the outermost rule wins instead of the innermost",
     "morpho_homegraph/scope.py",
     "                if best is None or len(rule_path) > len(best[0]):",
     "                if best is None or len(rule_path) < len(best[0]):  # mutated",
     "2  an excluded subfolder loses, the rest of the outer keeps"),

    ("the rule only subtracts, it never adds back",
     "morpho_homegraph/scope.py",
     "        return rule is not None and rule[1] == INCLUDE",
     "        return not any(m == EXCLUDE for _p, m in self.rules  # mutated\n"
     "                       if _under(str(path), _p))",
     "3  an included folder inside an excluded one wins"),

    ("a rule matches on string prefix, not at a separator",
     "morpho_homegraph/scope.py",
     '    return path == root or path.startswith(root.rstrip("/") + os.sep)',
     "    return path.startswith(root)  # mutated: /ab counts as inside /a",
     "5  a sibling sharing a name prefix is not inside"),

    # -- the two useless answers ------------------------------------------
    ("everything is in scope",
     "morpho_homegraph/scope.py",
     "        rule = self.decides(str(path))\n"
     "        return rule is not None and rule[1] == INCLUDE",
     "        return True  # mutated: yes to everything",
     "2  an excluded subfolder loses, the rest of the outer keeps"),

    ("nothing is in scope",
     "morpho_homegraph/scope.py",
     "        rule = self.decides(str(path))\n"
     "        return rule is not None and rule[1] == INCLUDE",
     "        return False  # mutated: no to everything",
     "1  an included folder puts its files in scope"),

    # -- the third state is derived, and it is three states ---------------
    ("a folder with disagreeing descendants is not partial",
     "morpho_homegraph/scope.py",
     "        if any((m == INCLUDE) != mine for _p, m in below):\n"
     "            return PARTIAL",
     "        if False:  # mutated: only two states\n"
     "            pass",
     "15 the tree's third state is derived from the rules"),

    ("every folder is partial",
     "morpho_homegraph/scope.py",
     "        if any((m == INCLUDE) != mine for _p, m in below):\n"
     "            return PARTIAL",
     "        if True:  # mutated: everything is partial\n"
     "            return PARTIAL",
     "15 the tree's third state is derived from the rules"),

    # -- .gitignore ------------------------------------------------------
    #
    # First match instead of last: negation silently stops working, every
    # other pattern behaves, and the run looks correct.
    ("the first matching pattern decides, not the last",
     "morpho_homegraph/scope.py",
     "        if hit:\n"
     "            verdict = not negated\n"
     "    return verdict",
     "        if hit:\n"
     "            return not negated  # mutated: first match wins\n"
     "    return verdict",
     "8  a later negation puts the file back"),

    ("negation is read as an ordinary pattern",
     "morpho_homegraph/scope.py",
     '        negated = line.startswith("!")',
     "        negated = False  # mutated: ! is just a character",
     "8  a later negation puts the file back"),

    ("anchoring is ignored, every pattern floats",
     "morpho_homegraph/scope.py",
     '        anchored = line.startswith("/") or "/" in line.rstrip("/")',
     "        anchored = False  # mutated: nothing is anchored",
     "9  a leading slash anchors the pattern to the root"),

    ("a directory-only pattern matches files too",
     "morpho_homegraph/scope.py",
     "        if dir_only and not is_dir:\n"
     "            continue",
     "        if False:  # mutated: the trailing slash means nothing\n"
     "            continue",
     "10 a trailing slash matches a directory and not a file"),

    ("* crosses directory separators like ** does",
     "morpho_homegraph/scope.py",
     '    if pattern.count("/") != value.count("/"):\n'
     "        return False",
     "    if False:  # mutated: * spans separators\n"
     "        return False",
     "11 ** crosses directories where * does not"),

    ("comments and blank lines become patterns",
     "morpho_homegraph/scope.py",
     '        if not line.strip() or line.lstrip().startswith("#"):\n'
     "            continue",
     "        if False:  # mutated: every line is a pattern\n"
     "            continue",
     "7b comments and blank lines are not patterns"),

    # -- the junk filter --------------------------------------------------
    ("the junk filter excludes nothing",
     "morpho_homegraph/scope.py",
     "            if name in JUNK:",
     "            if False:  # mutated: keep every build directory",
     "12 the junk filter removes what the user did not point at"),

    ("the junk filter excludes the folder itself",
     "morpho_homegraph/scope.py",
     "            if name in JUNK:\n"
     "                scope.add(os.path.join(current, name), EXCLUDE)",
     "            if True:  # mutated: exclude everything walked\n"
     "                scope.add(os.path.join(current, name), EXCLUDE)",
     # Gate 12, not 12b: the folder's own rule is added before the walk, so
     # it survives and 12b stays green. What goes is `src`, and with it the
     # file the user actually pointed at.
     "12 the junk filter removes what the user did not point at"),

    # -- storage ----------------------------------------------------------
    ("saving a scope keeps whatever was there before",
     "morpho_homegraph/scope.py",
     '        db.execute("DELETE FROM scope")',
     "        pass  # mutated: rules accumulate across saves",
     "16b saving a scope replaces the previous one"),

    ("a repo with no .gitignore gets one from somewhere",
     "morpho_homegraph/scope.py",
     "    except OSError:\n"
     "        patterns = []",
     "    except OSError:\n"
     '        patterns = parse_gitignore("*\\n")  # mutated: skip everything',
     "13b a repo with no .gitignore is a scope with no skip list"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp3.py", prefix="mut3-", timeout=600))
