#!/usr/bin/env python3
"""The scope selector: one question, asked millions of times.

**Is this path in scope?** The checkbox tree, "add a git repo" and the junk
filter are all ways of producing the rules that answer it.

**The innermost rule wins** (locked decision 6, the same rule as CSS
specificity), and it wins in both directions: `/a` included with `/a/b`
excluded puts `/a/b/d` out, and `/a` excluded with `/a/b` included puts
`/a/b/d` in. A rule that only worked one way would be an ordering, not a rule.

**No rule means no.** Default-out is what makes an empty selection a legal
answer rather than an error -- and the opposite default would turn adding your
first project into an action that widens the scope from nothing to the whole
home area.

**Membership is a predicate, never a list.** That is how "the inner is
subtracted from the outer" becomes true without anyone subtracting anything,
and it is why overlapping selections cannot double-index: each path is asked
once and gets one answer.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

INCLUDE, EXCLUDE = "include", "exclude"
CHECKED, UNCHECKED, PARTIAL = "checked", "unchecked", "partial"

# Directories that are build output or a package manager's working area. Only
# applied to folders that are *not* git repos -- a repo has `.gitignore` for
# this, and two filters over one folder is two places to change when the
# answer comes out wrong (locked decision 3 leaves the choice to the user, so
# this list only ever removes noise the user did not mean to point at).
#
# Hand-made, and it will go out of date: `node_modules` and `.venv` are true
# today, and the next ecosystem will have another name. A list to maintain,
# not a rule that generalises.
JUNK = ("node_modules", ".venv", "venv", ".git", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".tox", "target", "dist", "build", ".next", ".cache")


def _under(path: str, root: str) -> bool:
    """Is `path` at or below `root`? Matched at a separator, never as text.

    `/ab` is not under `/a`. The same mistake as CP-0's mount lookup and
    CP-2's scope check, both of which cost a mutation round to find.
    """
    return path == root or path.startswith(root.rstrip("/") + os.sep)


class Scope:
    """The rules for one project, and the answers derived from them."""

    def __init__(self, rules: list[tuple[str, str]] | None = None) -> None:
        # (path, mode) in no particular order: the decision sorts by depth,
        # so the caller never has to keep them ordered and cannot break the
        # rule by adding one in the wrong place.
        self.rules: list[tuple[str, str]] = list(rules or [])

    def add(self, path: str, mode: str = INCLUDE) -> "Scope":
        resolved = str(Path(path).expanduser().resolve())
        self.rules = [(p, m) for p, m in self.rules if p != resolved]
        self.rules.append((resolved, mode))
        return self

    # -- the one question -------------------------------------------------

    def decides(self, path: str) -> tuple[str, str] | None:
        """The innermost rule covering `path`, or None when nothing does."""
        best = None
        for rule_path, mode in self.rules:
            if _under(path, rule_path):
                if best is None or len(rule_path) > len(best[0]):
                    best = (rule_path, mode)
        return best

    def contains(self, path: str) -> bool:
        rule = self.decides(str(path))
        return rule is not None and rule[1] == INCLUDE

    # -- the tree's third state -------------------------------------------

    def state(self, path: str) -> str:
        """`checked`, `unchecked` or `partial` for a directory in the tree.

        Derived, never stored: a stored `partial` becomes untrue the moment a
        rule below it changes, and a checkbox tree that lies about its own
        descendants is worse than one without the third state.
        """
        path = str(path)
        mine = self.contains(path)
        below = [(p, m) for p, m in self.rules
                 if p != path and _under(p, path)]
        if any((m == INCLUDE) != mine for _p, m in below):
            return PARTIAL
        return CHECKED if mine else UNCHECKED


# -- .gitignore, read as a skip list ---------------------------------------

def parse_gitignore(text: str) -> list[tuple[str, bool, bool, bool]]:
    """`(pattern, negated, anchored, dir_only)` for each usable line.

    What git's format is actually used for -- comments, blank lines, `!`
    negation, `/` anchoring, a `/` suffix for directory-only, and `*`, `?`,
    `**`. Not a reimplementation of git: nested `.gitignore` files below the
    repo root are not read, and that limit is in the answer key rather than
    waiting to be discovered.
    """
    out = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = line.startswith("/") or "/" in line.rstrip("/")
        out.append((line.lstrip("/"), negated, anchored, dir_only))
    return out


def gitignored(relative: str, is_dir: bool,
               patterns: list[tuple[str, bool, bool, bool]]) -> bool:
    """Does the last matching pattern exclude `relative`?

    Last match wins, which is git's rule and the reason `!keep.log` after
    `*.log` works. Checking the first match instead would make negation
    silently do nothing -- a defect that looks exactly like a correct run.
    """
    verdict = False
    for pattern, negated, anchored, dir_only in patterns:
        if dir_only and not is_dir:
            continue
        if anchored:
            hit = _fnmatch_path(relative, pattern)
        else:
            # Unanchored patterns match at any depth, which is why a bare
            # `node_modules` in a .gitignore covers every one of them.
            hit = any(_fnmatch_path(part, pattern) or fnmatch.fnmatch(part, pattern)
                      for part in _suffixes(relative))
        if hit:
            verdict = not negated
    return verdict


def _suffixes(relative: str) -> list[str]:
    parts = relative.split("/")
    return ["/".join(parts[i:]) for i in range(len(parts))]


def _fnmatch_path(value: str, pattern: str) -> bool:
    """fnmatch, except `*` does not cross a separator and `**` does."""
    if "**" in pattern:
        head, _, tail = pattern.partition("**")
        head, tail = head.strip("/"), tail.strip("/")
        if head and not (value == head or value.startswith(head + "/")):
            return False
        return not tail or value == tail or value.endswith("/" + tail)
    if pattern.count("/") != value.count("/"):
        return False
    return fnmatch.fnmatch(value, pattern)


def from_repo(root: str) -> tuple[Scope, list[tuple[str, bool, bool, bool]]]:
    """A scope that includes `root`, plus its root `.gitignore` as a skip list."""
    root = str(Path(root).expanduser().resolve())
    scope = Scope().add(root, INCLUDE)
    path = os.path.join(root, ".gitignore")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            patterns = parse_gitignore(fh.read())
    except OSError:
        patterns = []
    return scope, patterns


def from_folder(root: str) -> Scope:
    """A scope that includes `root` and excludes the junk directories in it.

    Only for folders that are not repos: a repo's `.gitignore` says the same
    thing, and applying both would mean two places to change when the answer
    comes out wrong.
    """
    root = str(Path(root).expanduser().resolve())
    scope = Scope().add(root, INCLUDE)
    for current, dirs, _files in os.walk(root):
        for name in list(dirs):
            if name in JUNK:
                scope.add(os.path.join(current, name), EXCLUDE)
                dirs.remove(name)
    return scope


def is_repo(root: str) -> bool:
    return os.path.isdir(os.path.join(os.path.expanduser(root), ".git"))


# -- storage ---------------------------------------------------------------

def save(store, scope: Scope) -> None:
    with store.writing() as db:
        db.execute("DELETE FROM scope")
        db.executemany("INSERT INTO scope (path, mode) VALUES (?, ?)",
                       scope.rules)
        db.commit()


def load(store) -> Scope:
    return Scope([(r[0], r[1]) for r in store.db.execute(
        "SELECT path, mode FROM scope")])
