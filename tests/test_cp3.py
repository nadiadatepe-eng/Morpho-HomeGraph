#!/usr/bin/env python3
"""CP-3 -- the scope selector.

The answer key is `tests/gold/FASIT-cp3.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

The load-bearing gate is 7, and it is a negative control: gate 6 asserts that
a `.gitignore` pattern excludes a file, which is equally satisfied by a scope
that excludes everything, by a walk that found nothing, and by a predicate
that always says no. Gate 7 removes the pattern and demands the same file
comes back.

Run:
    python3 tests/test_cp3.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.scope import (  # noqa: E402
    CHECKED, EXCLUDE, INCLUDE, PARTIAL, UNCHECKED, Scope, from_folder,
    from_repo, gitignored, is_repo, load, parse_gitignore, save)
from morpho_homegraph.store import (  # noqa: E402
    PROJECT, Store, initialise, new_project)

results, check = reporter(58)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def ignores(text, relative, is_dir=False):
    return gitignored(relative, is_dir, parse_gitignore(text))


# -- 1-5, 14-15. the rules and how they compose ----------------------------

def gates_rules(work):
    a = os.path.join(work, "a")
    write(os.path.join(a, "f.txt"))
    write(os.path.join(a, "b", "f.txt"))
    write(os.path.join(a, "c", "f.txt"))
    # A sibling whose name starts with the rule's name. Without it, matching
    # at a separator and matching a string prefix give the same answer, and
    # gate 5 cannot fail -- the same fixture hole CP-0 and CP-2 both had.
    write(os.path.join(work, "ab", "f.txt"))

    scope = Scope().add(a, INCLUDE)
    check("1  an included folder puts its files in scope",
          scope.contains(os.path.join(a, "f.txt")))

    scope.add(os.path.join(a, "b"), EXCLUDE)
    check("2  an excluded subfolder loses, the rest of the outer keeps",
          not scope.contains(os.path.join(a, "b", "f.txt"))
          and scope.contains(os.path.join(a, "c", "f.txt")))

    # The same rule the other way round. A version that only subtracted would
    # pass gate 2 and fail here, which is why both directions are gated.
    other = Scope().add(a, EXCLUDE).add(os.path.join(a, "b"), INCLUDE)
    check("3  an included folder inside an excluded one wins",
          other.contains(os.path.join(a, "b", "f.txt"))
          and not other.contains(os.path.join(a, "c", "f.txt")))

    empty = Scope()
    check("4  no rules means nothing in scope, and that is legal",
          not empty.contains(os.path.join(a, "f.txt"))
          and empty.state(a) == UNCHECKED and empty.rules == [],
          "state=%s" % empty.state(a))

    check("5  a sibling sharing a name prefix is not inside",
          not scope.contains(os.path.join(work, "ab", "f.txt")))

    # Overlap: the same folder reachable by two rules. Membership is a
    # predicate, so each path is asked once -- there is no list to double.
    overlapping = Scope().add(a, INCLUDE).add(os.path.join(a, "b"), INCLUDE)
    every = [os.path.join(root, name)
             for root, _dirs, files in os.walk(a) for name in files]
    picked = [p for p in every if overlapping.contains(p)]
    check("14 overlapping rules do not double-index",
          len(picked) == len(set(picked)) == len(every),
          "%d paths, %d distinct, %d in tree"
          % (len(picked), len(set(picked)), len(every)))

    check("15 the tree's third state is derived from the rules",
          scope.state(a) == PARTIAL
          and scope.state(os.path.join(a, "c")) == CHECKED
          and scope.state(os.path.join(a, "b")) == UNCHECKED,
          "a=%s c=%s b=%s" % (scope.state(a),
                              scope.state(os.path.join(a, "c")),
                              scope.state(os.path.join(a, "b"))))


# -- 6-11. .gitignore, read as a skip list ---------------------------------

def gates_gitignore(work):
    check("6  a .gitignore pattern excludes the file it names",
          ignores("*.log\n", "x.log"))
    # The control. Without it, gate 6 is satisfied by a predicate that says
    # no to everything, by an empty scope, and by a walk that found nothing.
    check("7  the same file without the pattern is not excluded",
          not ignores("*.tmp\n", "x.log"))
    # Every fixture above is a .gitignore with nothing but patterns in it, so
    # nothing could tell "comments are skipped" from "comments never appear".
    # A real one is mostly comments and blank lines.
    # The comment has to be one whose *text is a path that exists*, or the
    # gate cannot fail: `# build` never matches `build` under any rule,
    # because the `#` stays in the pattern. Emacs autosave files are the real
    # case -- `#notes.md#` is a filename, and `#notes.md` is a comment.
    check("7b comments and blank lines are not patterns",
          not ignores("#notes.md\n\n   \n", "#notes.md")
          and ignores("#notes.md\nnotes.md\n", "notes.md"))
    # Last match wins, which is git's rule. Checking the first match instead
    # makes negation silently do nothing, and that looks exactly like a
    # correct run.
    check("8  a later negation puts the file back",
          ignores("*.log\n!keep.log\n", "x.log")
          and not ignores("*.log\n!keep.log\n", "keep.log"))
    check("9  a leading slash anchors the pattern to the root",
          ignores("/build\n", "build")
          and not ignores("/build\n", "src/build"))
    check("10 a trailing slash matches a directory and not a file",
          ignores("logs/\n", "logs", is_dir=True)
          and not ignores("logs/\n", "logs", is_dir=False))
    check("11 ** crosses directories where * does not",
          ignores("a/**/z\n", "a/b/c/z") and not ignores("a/*/z\n", "a/b/c/z"))

    # And the whole path through `from_repo`, not only the matcher: a repo
    # whose .gitignore is unreadable or absent must still produce a scope.
    repo = os.path.join(work, "repo")
    write(os.path.join(repo, ".git", "HEAD"), "ref: refs/heads/main\n")
    write(os.path.join(repo, ".gitignore"), "*.log\n!keep.log\n")
    write(os.path.join(repo, "src", "main.py"))
    scope, patterns = from_repo(repo)
    check("13 a repo is read by its .gitignore, not by the junk filter",
          is_repo(repo) and scope.contains(os.path.join(repo, "src", "main.py"))
          and gitignored("x.log", False, patterns)
          and not gitignored("src/main.py", False, patterns),
          "%d patterns" % len(patterns))

    # R6b: `.git` is out, and the neighbour that merely starts the same is in.
    # Without the second half, a rule matching on characters instead of path
    # separators passes -- and `.github` is the directory it would eat.
    write(os.path.join(repo, ".git", "objects", "ab", "cdef"), "binary\n")
    write(os.path.join(repo, ".github", "workflows", "ci.yml"), "on: push\n")
    check("17 .git is pruned in a repo, .github is not",
          not scope.contains(os.path.join(repo, ".git", "objects", "ab",
                                          "cdef"))
          and not scope.contains(os.path.join(repo, ".git"))
          and scope.contains(os.path.join(repo, ".github", "workflows",
                                          "ci.yml")),
          "%d rules" % len(scope.rules))

    # R6c: the patterns work *through* the predicate, not beside it. Before
    # 2026-08-04 `from_repo` handed them back separately and CP-4 dropped them,
    # which left `gitignored()` with no caller outside this file.
    write(os.path.join(repo, "run.log"), "noise\n")
    write(os.path.join(repo, "keep.log"), "wanted\n")
    write(os.path.join(repo, ".venv", "lib", "x.py"), "import os\n")
    with open(os.path.join(repo, ".gitignore"), "w") as fh:
        fh.write("*.log\n!keep.log\n.venv/\n")
    scope, patterns = from_repo(repo)
    check("18 an ignored file is outside the scope, not merely matched",
          not scope.contains(os.path.join(repo, "run.log"))
          and scope.contains(os.path.join(repo, "src", "main.py")),
          "%d patterns through contains()" % len(patterns))

    # Git never asks this: it does not descend into an ignored directory, so
    # it is never handed the children. A per-path predicate is, and without
    # walking the parents `.venv/` excludes one directory and none of its 304
    # files -- measured on ~/homegraph.
    check("19 a file under an ignored directory is outside too",
          not scope.contains(os.path.join(repo, ".venv", "lib", "x.py"))
          and not scope.contains(os.path.join(repo, ".venv"), is_dir=True),
          ".venv/lib/x.py excluded by .venv/")

    check("20 negation survives the predicate",
          scope.contains(os.path.join(repo, "keep.log"))
          and not scope.contains(os.path.join(repo, "run.log")),
          "!keep.log wins over *.log")

    bare = os.path.join(work, "bare-repo")
    write(os.path.join(bare, ".git", "HEAD"), "ref: refs/heads/main\n")
    write(os.path.join(bare, "only.py"))
    scope, patterns = from_repo(bare)
    check("13b a repo with no .gitignore is a scope with no skip list",
          scope.contains(os.path.join(bare, "only.py")) and patterns == [],
          "%d patterns" % len(patterns))


# -- 12. the junk filter, for folders that are not repos -------------------

def gates_junk(work):
    folder = os.path.join(work, "plain")
    write(os.path.join(folder, "src", "main.py"))
    write(os.path.join(folder, "node_modules", "left-pad", "index.js"))
    write(os.path.join(folder, ".venv", "lib", "thing.py"))
    write(os.path.join(folder, "src", "__pycache__", "main.pyc"))

    scope = from_folder(folder)
    check("12 the junk filter removes what the user did not point at",
          scope.contains(os.path.join(folder, "src", "main.py"))
          and not scope.contains(
              os.path.join(folder, "node_modules", "left-pad", "index.js"))
          and not scope.contains(os.path.join(folder, ".venv", "lib", "thing.py"))
          and not scope.contains(
              os.path.join(folder, "src", "__pycache__", "main.pyc")))
    check("12b the junk filter is not a scope that excludes everything",
          scope.contains(folder) and len(scope.rules) > 1,
          "%d rules" % len(scope.rules))


# -- 16. a scope survives the store ----------------------------------------

def gates_storage(work):
    folder = os.path.join(work, "stored")
    write(os.path.join(folder, "in", "f.txt"))
    write(os.path.join(folder, "out", "f.txt"))
    scope = Scope().add(folder, INCLUDE).add(os.path.join(folder, "out"), EXCLUDE)
    inside = os.path.join(folder, "in", "f.txt")
    outside = os.path.join(folder, "out", "f.txt")

    project_id, store_db = new_project()
    guard = StoreLock(str(store_db)).acquire()
    try:
        with Store(store_db, role=PROJECT) as store:
            initialise(store, project_id, folder)
            save(store, scope)
            back = load(store)
    finally:
        guard.release()

    check("16 a scope answers the same before and after the store",
          back.contains(inside) == scope.contains(inside) is True
          and back.contains(outside) == scope.contains(outside) is False
          and sorted(back.rules) == sorted(scope.rules),
          "%d rules round-tripped" % len(back.rules))

    # 21, R6d: `.gitignore` is re-read, never frozen into the store. A saved
    # scope has to give the *current* answer -- an index answering by a rule
    # the file no longer has is worse than one without the rule.
    repo21 = os.path.join(os.path.dirname(folder), "repo21")
    write(os.path.join(repo21, ".git", "HEAD"), "ref: refs/heads/main\n")
    write(os.path.join(repo21, "a.log"), "x\n")
    with open(os.path.join(repo21, ".gitignore"), "w") as fh:
        fh.write("*.log\n")
    repo_scope, _ = from_repo(repo21)
    project21, db21 = new_project()
    guard21 = StoreLock(str(db21)).acquire()
    try:
        with Store(db21, role=PROJECT) as store:
            initialise(store, project21, repo21)
            save(store, repo_scope)
            was_ignored = not load(store).contains(os.path.join(repo21,
                                                               "a.log"))
            # The user edits the file. Nothing in the store changes.
            with open(os.path.join(repo21, ".gitignore"), "w") as fh:
                fh.write("# nothing ignored any more\n")
            now_included = load(store).contains(os.path.join(repo21, "a.log"))
    finally:
        guard21.release()
    check("21 a saved scope re-reads .gitignore instead of freezing it",
          was_ignored and now_included,
          "ignored before the edit: %s, included after: %s"
          % (was_ignored, now_included))

    # 22: a plain folder has no root and no patterns, so R6c changes nothing
    # for it. Without this, the whole feature could be gated only on repos.
    plain = Scope().add(folder, INCLUDE)
    check("22 a folder scope is unaffected by the pattern machinery",
          plain.contains(inside) and plain.patterns == []
          and plain.root is None,
          "no root, no patterns")

    # A second save with the rule reversed. One save can never show whether
    # the old rules were cleared, and a scope that accumulates keeps a rule
    # the user removed -- which reads as the selector ignoring them.
    narrowed = Scope().add(folder, INCLUDE)
    guard = StoreLock(str(store_db)).acquire()
    failure = None
    try:
        with Store(store_db, role=PROJECT) as store:
            try:
                save(store, narrowed)
                back = load(store)
            except sqlite3.Error as exc:
                # A save that does not clear first collides on the primary
                # key. Correct of the schema, but a crash names no gate, so
                # it is reported as this one failing.
                failure, back = repr(exc), Scope()
    finally:
        guard.release()
    check("16b saving a scope replaces the previous one",
          failure is None and back.contains(outside)
          and sorted(back.rules) == sorted(narrowed.rules),
          failure or "%d rules after the second save" % len(back.rules))


def main():
    with tempfile.TemporaryDirectory(prefix="mhg-cp3-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        tree = os.path.join(work, "tree")
        os.makedirs(tree)
        gates_rules(tree)
        gates_gitignore(tree)
        gates_junk(tree)
        gates_storage(tree)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp3():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
