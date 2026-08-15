#!/usr/bin/env python3
"""CP-18 -- nested `.gitignore`, and the denominator that shrinks quietly.

The answer key is `tests/gold/FASIT-cp18.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

Two open threads, and they are one question from two ends: **what is actually
in the scope, and who says so when the answer stops matching the disk.**

Measured before the plan: 30 of 127 L2 rows here (24 %) come from two nested
`.gitignore` files that `scope.py` never read -- `.remember/` with `*` and
`.pytest_cache/` with `*`. The second appeared *after* the thread was written,
because we ran our own tests. Reading every nested file costs 0.005-0.010 s per
project against a 14.98 s sweep, so cost is not the argument either way.

Run:
    python3 tests/test_cp18.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import scope as scope_mod  # noqa: E402
from morpho_homegraph import service  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(64)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=300):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    time.sleep(0.01)
    return path


def fresh_home(work, name):
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, name, "store")
    home = os.path.join(work, name, "home")
    os.makedirs(home, exist_ok=True)
    return home


def repo_at(root, ignore="notes/\n"):
    write(os.path.join(root, ".gitignore"), ignore)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    write(os.path.join(root, ".git", "HEAD"), "ref: refs/heads/main\n")
    return root


# -- 1, 2, 3, 4, 5, 6, 7: the nested patterns ------------------------------

def gates_nested(work):
    root = repo_at(os.path.join(work, "nested", "proj"), ignore="*.log\n")
    cache = os.path.join(root, "cache")
    kept = write(os.path.join(root, "kept.md"), "in scope\n")
    hidden = write(os.path.join(cache, "hidden.md"), "under a nested ignore\n")
    sibling = write(os.path.join(root, "other", "sibling.md"), "no ignore here\n")
    write(os.path.join(cache, ".gitignore"), "*\n")

    scope, _patterns = scope_mod.from_repo(root)
    check("1  a file a nested .gitignore excludes is out of scope",
          not scope.contains(hidden, is_dir=False), hidden)
    check("3  CONTROL: a sibling directory without one is unaffected",
          scope.contains(sibling, is_dir=False), sibling)
    check("4  a nested `*` empties its own directory, not the project",
          scope.contains(kept, is_dir=False), kept)
    check("7  .git is out of scope though no .gitignore names it",
          not scope.contains(os.path.join(root, ".git", "HEAD"), is_dir=False))

    # 2: the control. Remove the nested file and the same path comes back --
    # otherwise gate 1 is green for a scope that drops that directory for any
    # reason at all.
    os.remove(os.path.join(cache, ".gitignore"))
    reread, _ = scope_mod.from_repo(root)
    check("2  CONTROL: without the nested file the same path is in scope",
          reread.contains(hidden, is_dir=False), hidden)

    # 5 and 6: depth is the order, and last match wins across files.
    logs = write(os.path.join(root, "keepdir", "keep.log"), "negated\n")
    other_log = write(os.path.join(root, "keepdir", "drop.log"), "still out\n")
    write(os.path.join(root, "keepdir", ".gitignore"), "!keep.log\n")
    deep, _ = scope_mod.from_repo(root)
    check("5  a negation in a nested file overrides the root pattern",
          deep.contains(logs, is_dir=False), logs)
    check("6  CONTROL: the root's patterns still apply in that directory",
          not deep.contains(other_log, is_dir=False), other_log)

    # 4b: an *anchored* nested pattern. Every case above uses an unanchored
    # one, which matches at any depth -- so a scope that matched nested
    # patterns against the project root instead of the file's own directory
    # gave the same answer, and that mutation survived the first sweep. `/x.md`
    # anchored in `deep/` must match `deep/x.md` and nothing else.
    anchored_dir = os.path.join(root, "anchored")
    hit = write(os.path.join(anchored_dir, "x.md"), "excluded here\n")
    miss = write(os.path.join(root, "x.md"), "same name, root level\n")
    write(os.path.join(anchored_dir, ".gitignore"), "/x.md\n")
    exact, _ = scope_mod.from_repo(root)
    check("4b an anchored nested pattern is relative to its own directory",
          not exact.contains(hit, is_dir=False), hit)
    check("4c CONTROL: the same name outside that directory is untouched",
          exact.contains(miss, is_dir=False), miss)

    # 5b: two nested files on the same path, the deeper one negating. Gate 5
    # only has one nested file, so a chain walked deepest-first still passed
    # it -- the order only shows when two nested files disagree.
    outer = os.path.join(root, "outer")
    inner = os.path.join(outer, "inner")
    buried = write(os.path.join(inner, "keep.txt"), "the deeper rule wins\n")
    write(os.path.join(outer, ".gitignore"), "*.txt\n")
    write(os.path.join(inner, ".gitignore"), "!keep.txt\n")
    ordered, _ = scope_mod.from_repo(root)
    check("5b the deeper of two nested files decides",
          ordered.contains(buried, is_dir=False), buried)


# -- 8, 9: read once per scope, and never cached across builds -------------

def gates_reads(work):
    root = repo_at(os.path.join(work, "reads", "proj"), ignore="*.log\n")
    for n in range(3):
        write(os.path.join(root, "d%d" % n, ".gitignore"), "*\n")
        write(os.path.join(root, "d%d" % n, "f.md"), "x\n")
    write(os.path.join(root, "top.md"), "x\n")

    # Counted, not timed: a gate that measures duration is green on a fast
    # machine for code that reads the file on every call.
    opened = []
    real_open = scope_mod.open_text

    def counting(path):
        opened.append(path)
        return real_open(path)

    scope_mod.open_text = counting
    try:
        scope, _ = scope_mod.from_repo(root)
        after_build = len(opened)
        for _ in range(20):
            scope.contains(os.path.join(root, "d0", "f.md"), is_dir=False)
            scope.contains(os.path.join(root, "top.md"), is_dir=False)
        after_calls = len(opened)
    finally:
        scope_mod.open_text = real_open
    check("8  every nested .gitignore is read once per scope, not per call",
          after_calls == after_build,
          "%d read(s) at build, %d after 40 contains() calls"
          % (after_build, after_calls))
    check("8b the nested files were actually read",
          after_build >= 4, "%d read(s): root + 3 nested" % after_build)

    # 8c: `.git` is not walked for patterns. A repo's own storage can contain
    # a `.gitignore` (a checked-out branch of another project, a submodule's
    # leftovers), and reading it would let git's internals decide what the
    # project indexes. Counting reads is the evidence: the file exists, and
    # the number of reads must not include it.
    write(os.path.join(root, ".git", "modules", ".gitignore"), "*\n")
    opened.clear()
    scope_mod.open_text = counting
    try:
        scope_mod.from_repo(root)
        with_git = len(opened)
    finally:
        scope_mod.open_text = real_open
    check("8c CONTROL: a .gitignore inside .git is not read",
          not any(".git" + os.sep in p for p in opened),
          "read: %s" % [p for p in opened if ".git" in p] or "none")
    check("8d CONTROL: skipping it did not skip the others",
          with_git >= 4, "%d read(s)" % with_git)

    # 9: a file that appears after the scope was built takes effect on the
    # next build. A scope that caches for ever is CP-3's bug again.
    late = write(os.path.join(root, "late", "f.md"), "x\n")
    check("9  PRECONDITION: the path is in scope before the file appears",
          scope_mod.from_repo(root)[0].contains(late, is_dir=False))
    write(os.path.join(root, "late", ".gitignore"), "*\n")
    check("9  CONTROL: a .gitignore added later takes effect on the next build",
          not scope_mod.from_repo(root)[0].contains(late, is_dir=False), late)


# -- 10, 11, 12: status compares L2 against the scope ----------------------

def gates_denominator(work):
    home = fresh_home(work, "denom")
    root = repo_at(os.path.join(home, "proj"), ignore="")
    write(os.path.join(root, "a.md"), "one\n")
    write(os.path.join(root, "b.md"), "two\n")
    project_id = cli("add", root).stdout.split()[0]
    cli("scan", home)
    cli("update", project_id)

    matched = cli("status", project_id).stdout
    line = [ln for ln in matched.splitlines() if ln.startswith("l2")]
    # Gate 10 asks that the comparison *happens*, and gates 11 and 12 decide
    # what it prints. Written first as "the words `in scope` appear", which
    # contradicted gate 11 outright: when the two agree there is nothing to
    # say, and a line that says it anyway is noise on every healthy project.
    # So the evidence for 10 is that the count is correct here -- 3 files, 3
    # rows, no drift reported -- and gate 12 is where the number is printed.
    check("10 status compares L2 rows against the files in scope",
          bool(line) and line[0].startswith("l2             3 rows"),
          line or "absent")
    check("11 CONTROL: when they match, no update is suggested",
          bool(line) and "in scope" not in line[0],
          line or "absent")

    # 12: a file written after the update. L2 no longer covers the scope, and
    # the line has to say so *and* name the command.
    write(os.path.join(root, "c.md"), "three\n")
    cli("scan", home)
    drifted = cli("status", project_id).stdout
    l2_line = [ln for ln in drifted.splitlines() if ln.startswith("l2")]
    check("12 when they differ, the line names the update command",
          bool(l2_line) and "morphofiles-graph update" in l2_line[0],
          l2_line or "absent")

    # 13, 14: `scope_size` itself, tested directly rather than through the
    # printed line. Three mutations survived the first sweep here -- counting
    # every file regardless of scope, and answering for a root that is gone --
    # because the status line only ever showed the *drifted* case, where a
    # wrong count is still a wrong count and the line looks the same.
    # Four, not three: `.gitignore` is itself a file the scope selects, and
    # `.git/` is excluded. Guessing three here made the gate red against
    # correct code -- the count has to be derived the same way the reader
    # would derive it, not assumed from the files the fixture wrote.
    baseline = service.scope_size(root)
    check("13 scope_size counts what the scope selects, not every file",
          baseline == 4,
          "%s (a.md, b.md, c.md, .gitignore; .git excluded)" % baseline)
    ignored_file = write(os.path.join(root, "junk", "d.md"), "four\n")
    write(os.path.join(root, "junk", ".gitignore"), "*\n")
    check("13b CONTROL: a file a nested .gitignore excludes is not counted",
          service.scope_size(root) == baseline,
          "%s after adding %s" % (service.scope_size(root), ignored_file))
    # 13c: an excluded *file* inside an *included* directory. 13b only ever
    # excluded whole directories, and the walk prunes those before counting --
    # so a `total += len(files)` that ignores the scope entirely still passed
    # it. That mutation survived the first sweep. This is the only shape where
    # the per-file check is the thing doing the work.
    write(os.path.join(root, ".gitignore"), "*.log\n")
    write(os.path.join(root, "noisy.log"), "excluded, but its directory is not\n")
    check("13c CONTROL: an excluded file in an included directory is not counted",
          service.scope_size(root) == baseline,
          "%s with noisy.log present" % service.scope_size(root))
    check("14 scope_size answers None for a root that is not there",
          service.scope_size(os.path.join(root, "nowhere")) is None,
          "%r" % service.scope_size(os.path.join(root, "nowhere")))

    # 15: and `status` must not turn that None into drift. A project whose
    # directory was moved or deleted is a different fact from one whose L2 is
    # behind, and telling the reader to run `update` against a directory that
    # is gone sends them at the wrong problem. The mutation dropping the
    # `is None` half survived the first sweep because every fixture above
    # keeps its root.
    import shutil
    shutil.rmtree(root)
    gone = cli("status", project_id).stdout
    gone_l2 = [ln for ln in gone.splitlines() if ln.startswith("l2")]
    check("15 a project whose root is gone is not reported as drift",
          bool(gone_l2) and "in scope" not in gone_l2[0],
          gone_l2 or "absent")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp18-") as work:
        gates_nested(work)
        gates_reads(work)
        gates_denominator(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp18():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
