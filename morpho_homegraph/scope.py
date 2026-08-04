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

    def __init__(self, rules: list[tuple[str, str]] | None = None,
                 patterns: list[tuple[str, bool, bool, bool]] | None = None,
                 root: str | None = None) -> None:
        # (path, mode) in no particular order: the decision sorts by depth,
        # so the caller never has to keep them ordered and cannot break the
        # rule by adding one in the wrong place.
        self.rules: list[tuple[str, str]] = list(rules or [])
        # The repo's `.gitignore`, held *inside* the predicate rather than
        # beside it. `from_repo` used to return `(scope, patterns)` and CP-4
        # passed only the scope on, which left `gitignored()` with no caller
        # outside `tests/` and 550 rows in L2 that the repo's own ignore file
        # excludes. A predicate with an attachment is two predicates, and one
        # of them gets forgotten.
        self.patterns = list(patterns or [])
        self.root = root

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

    def contains(self, path: str, is_dir: bool | None = None) -> bool:
        """Is `path` in scope -- by the rules **and** by `.gitignore`?

        `is_dir` is passed in where the caller already knows it (L0 stores the
        kind), and looked up otherwise. It matters because a `logs/` pattern
        excludes the directory and not a file of the same name.
        """
        path = str(path)
        rule = self.decides(path)
        if rule is None or rule[1] != INCLUDE:
            return False
        if not (self.patterns and self.root):
            return True
        if not _under(path, self.root):
            return True
        relative = os.path.relpath(path, self.root)
        if is_dir is None:
            is_dir = os.path.isdir(path)
        return not ignored_with_parents(relative, is_dir, self.patterns)

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


def ignored_with_parents(relative: str, is_dir: bool, patterns) -> bool:
    """Is `relative`, or any directory above it, ignored?

    **Git never asks this, and that is the point.** It does not descend into an
    ignored directory, so it is never handed the children -- the question does
    not come up. A predicate answering per path does get handed them, and
    without walking the parents, `.venv/` in a `.gitignore` excludes exactly
    one directory and none of the 304 files under it. Measured on ~/homegraph
    2026-08-04, that was most of what CP-4 still called `binary`.
    """
    parts = relative.split("/")
    for depth in range(1, len(parts)):
        if gitignored("/".join(parts[:depth]), True, patterns):
            return True
    return gitignored(relative, is_dir, patterns)


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
    """A scope that includes `root`, minus `.git`, plus its root `.gitignore`.

    **`.git` is excluded explicitly, because `.gitignore` never mentions it.**
    Locked decision 3 makes `.gitignore` the skip list for repos, and that is
    still true -- but it is a list of what the *user* wanted git to ignore, and
    nobody writes their own repository's storage into it. Measured 2026-08-04
    on `~/homegraph`: without this line, 896 of 1 729 L2 rows are git objects,
    52 % of the layer, and every count downstream needs a footnote.

    The data is not lost, which is what makes pruning the right layer for
    this: L0 still holds every path under `.git`, so anything that later wants
    git's own history -- co-change edges, say -- reads it there. The scope only
    decides what is read *as content*.

    `from_folder` has excluded `.git` since CP-3 through `JUNK`. The asymmetry
    was the accident, not the rule.
    """
    root = str(Path(root).expanduser().resolve())
    patterns = read_gitignore(root)
    scope = Scope(patterns=patterns, root=root)
    scope.add(root, INCLUDE).add(os.path.join(root, ".git"), EXCLUDE)
    return scope, patterns


def read_gitignore(root: str) -> list[tuple[str, bool, bool, bool]]:
    """The repo root's `.gitignore`, or an empty list when there is none.

    Read every time rather than stored: a scope loaded from the store has to
    give the same answer as one built from disk, and freezing the patterns
    would make a saved scope untrue the moment the user edits the file. An
    index answering by a rule the file no longer has is worse than one without
    the rule.
    """
    try:
        with open(os.path.join(root, ".gitignore"),
                  encoding="utf-8", errors="replace") as fh:
            return parse_gitignore(fh.read())
    except OSError:
        return []


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
    """Persist the rules, and the repo root -- but never the patterns.

    The root is what `load` needs in order to find `.gitignore` again. The
    patterns themselves are re-read every time; see `read_gitignore`.
    """
    with store.writing() as db:
        db.execute("DELETE FROM scope")
        db.executemany("INSERT INTO scope (path, mode) VALUES (?, ?)",
                       scope.rules)
        db.commit()
    store.set_meta("scope_repo_root", scope.root or "")


def load(store) -> Scope:
    root = store.get_meta("scope_repo_root") or None
    return Scope([(r[0], r[1]) for r in store.db.execute(
        "SELECT path, mode FROM scope")],
        patterns=read_gitignore(root) if root else None, root=root)
