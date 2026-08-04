#!/usr/bin/env python3
"""CP-7B -- the command that fills a project's own layers.

The answer key is `tests/gold/FASIT-cp7b.md`, written before this file and
before the code it grades (`7808626`). Gate numbers below are that document's.

The failure this checkpoint prevents is not an exception. It is an index that
is empty for a reason nobody can see -- the catalogue predates the project, the
graph ran before the content, the scope was loaded instead of recomputed -- and
every one of those produces the same output as a project with nothing in it.
So the gates that matter most are 5, 6 and 2: they separate "nothing here" from
"nothing ran".

Run:
    python3 tests/test_cp7b.py
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import identity, scope as scope_mod  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.store import Store, db_path, l0_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(60)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=60):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def make_tree(root, flavour="one"):
    write(os.path.join(root, "a.md"), "the %s tree, see [[b]]\n" % flavour)
    write(os.path.join(root, "b.md"), "leaf of %s\n" % flavour)
    return root


def fresh_home(work, name):
    """Its own store for one group of gates. Groups must not inherit an L0."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, name, "store")
    return os.path.join(work, name, "home")


def add(root):
    out = cli("add", root)
    return out.stdout.split()[0] if out.stdout.strip() else ""


def layers(project_id):
    """(scope rules, content rows, edges) as the store holds them."""
    with Store(db_path(project_id), read_only=True) as store:
        return tuple(store.db.execute(
            "SELECT (SELECT COUNT(*) FROM scope),"
            "       (SELECT COUNT(*) FROM content),"
            "       (SELECT COUNT(*) FROM edges)").fetchone())


def status_fields(project_id):
    """`status` output as (text, {label: value}).

    Fields rather than a substring search: every small integer this gate cares
    about also appears in a path, a schema version or a timestamp.
    """
    out = cli("status", project_id).stdout
    fields = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            fields[parts[0]] = parts[1].strip()
    return out, fields


def content_paths(project_id):
    with Store(db_path(project_id), read_only=True) as store:
        return sorted(p for (p,) in store.db.execute(
            "SELECT path FROM content"))


# -- 1, 2, 9, 12, 13, 14, 17, 18 -------------------------------------------

def gates_build(work):
    """A project that gets all three layers, and keeps getting the same ones."""
    home = fresh_home(work, "build")
    root = make_tree(os.path.join(home, "proj"))
    # A repo, so `from_repo` is the branch under test: `.git` out, and the
    # `.gitignore` read.
    os.makedirs(os.path.join(root, ".git", "objects"))
    write(os.path.join(root, ".git", "objects", "loose"), "not content\n")
    write(os.path.join(root, ".gitignore"), "ignored.md\n")
    write(os.path.join(root, "ignored.md"), "should not be read\n")
    cli("scan", home)
    project_id = add(root)

    done = cli("update", project_id)
    rules, rows, edges = layers(project_id)
    check("1  update fills scope, L2 and L3 in one command",
          done.returncode == 0 and rules > 0 and rows > 0 and edges > 0,
          "exit %s: %d scope rules, %d content rows, %d edges"
          % (done.returncode, rules, rows, edges))
    # 2: a graph built before the content it reads writes zero edges and
    # *succeeds*. That is indistinguishable from a project with no links, so
    # the edge count is the only thing that can tell the two apart.
    check("2  the graph has edges, so it ran after the content",
          edges > 0, "%d edges" % edges)
    check("18 a normal update exits 0",
          done.returncode == 0, "exit %s" % done.returncode)

    paths = content_paths(project_id)
    check("9  a repo keeps .git and its .gitignore out of L2",
          not any(os.sep + ".git" + os.sep in p for p in paths)
          and not any(p.endswith("ignored.md") for p in paths),
          "%d rows, none under .git or ignored" % len(paths))

    # 13: the scope that was used is on disk, and reading it back gives the
    # same answers. A scope nobody else can load is a decision this command
    # kept to itself.
    with Store(db_path(project_id), read_only=True) as store:
        loaded = scope_mod.load(store)
    check("13 the scope used is saved, and loads back with the same answers",
          loaded.contains(os.path.join(root, "a.md"), is_dir=False)
          and not loaded.contains(os.path.join(root, "ignored.md"),
                                  is_dir=False),
          "%d rules loaded" % len(loaded.rules))

    # 12: every layer is replaced whole, so two identical runs must be
    # identical. A layer that accumulates shows up here and nowhere else until
    # the store is large.
    with Store(db_path(project_id), read_only=True) as store:
        first = sorted(tuple(r) for r in store.db.execute(
            "SELECT path, sha256 FROM content"))
        first_edges = sorted(tuple(r) for r in store.db.execute(
            "SELECT src, dst, kind, method FROM edges"))
    again = cli("update", project_id)
    with Store(db_path(project_id), read_only=True) as store:
        second = sorted(tuple(r) for r in store.db.execute(
            "SELECT path, sha256 FROM content"))
        second_edges = sorted(tuple(r) for r in store.db.execute(
            "SELECT src, dst, kind, method FROM edges"))
    check("12 a second update with nothing changed gives the same rows",
          again.returncode == 0 and first == second
          and first_edges == second_edges and bool(first),
          "%d rows and %d edges, unchanged" % (len(second), len(second_edges)))

    # 17: the numbers a later reader needs are in `meta`, not only on stdout.
    with Store(db_path(project_id), read_only=True) as store:
        meta = {k: store.get_meta(k) for k in
                ("last_update", "l2_read", "l2_unread", "l3_edges")}
    check("17 update records last_update and the per-layer counts",
          all(meta.values()), ", ".join("%s=%s" % kv for kv in meta.items()))

    # 14: the reason the empty layers went unnoticed for five checkpoints is
    # that `status` never showed them. Read as fields, not as a substring
    # search: "3" appears in a timestamp, a path and a schema number, so
    # `str(rows) in output` is true of almost any output -- measured
    # 2026-08-04, two mutations survived exactly that way.
    other = add(make_tree(os.path.join(home, "untouched"), flavour="two"))
    empty_out, empty_fields = status_fields(other)
    _out, fields = status_fields(project_id)
    rules, rows, edges = layers(project_id)
    check("14 status shows the layers, so an empty index cannot look finished",
          fields.get("scope") == "%d rules" % rules
          and fields.get("l2", "").startswith("%d rows" % rows)
          and fields.get("l3", "").startswith("%d edges" % edges)
          and empty_fields.get("l2", "").startswith("0 rows")
          and "not built" in empty_out,
          "built: %r · empty: %r"
          % (fields.get("l2"), empty_fields.get("l2")))

    # 14b: counted from the rows, not from `meta`. A number written at build
    # time and never checked against the table is how an index that has lost
    # its content goes on reporting it -- and that is the shape of the defect
    # this whole checkpoint came from.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute("DELETE FROM content")
                db.commit()
    finally:
        guard.release()
    _out, emptied = status_fields(project_id)
    with Store(db_path(project_id), read_only=True) as store:
        claimed = store.get_meta("l2_read")
    check("14b the layer counts come from the rows, not from what meta claims",
          emptied.get("l2", "").startswith("0 rows") and claimed == str(rows),
          "meta still says %s read, status says %r"
          % (claimed, emptied.get("l2")))


# -- 3, 16 -----------------------------------------------------------------

def gates_guards(work):
    """Two stores, two guards -- and only one of them is this command's."""
    home = fresh_home(work, "guards")
    root = make_tree(os.path.join(home, "proj"))
    cli("scan", home)
    project_id = add(root)

    # 3: the hard form of R2. A running L0 refresh holds the L0 guard for as
    # long as it takes; an update that took it too would be blocked by every
    # scan, and the two writers of decision 12 would have become one.
    guard = StoreLock(str(l0_path())).acquire()
    try:
        during = cli("update", project_id)
    finally:
        guard.release()
    rules, rows, edges = layers(project_id)
    check("3  update succeeds while another process holds the L0 guard",
          during.returncode == 0 and rows > 0,
          "exit %s, %d content rows" % (during.returncode, rows))

    # 16: its own store is a different matter -- one writer per project.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        clash = cli("update", project_id)
    finally:
        guard.release()
    check("16 a second update on the same project is refused, not queued",
          clash.returncode == 2 and "REFUSED" in clash.stderr,
          "exit %s: %s" % (clash.returncode, clash.stderr.strip()[:40]))
    assert edges >= 0 and rules >= 0


# -- 4, 5, 6, 7, 8 ---------------------------------------------------------

def gates_refusals(work):
    """Every way the layers can be empty for a reason, said out loud."""
    home = fresh_home(work, "refuse")
    root = make_tree(os.path.join(home, "proj"))
    cli("scan", home)
    project_id = add(root)

    # 5: an L0 that predates the project. The tree is on disk and the
    # catalogue has never seen it, so an update would write an empty L2 that
    # reads exactly like "nothing in scope".
    elsewhere = make_tree(os.path.join(home, "elsewhere"), flavour="other")
    cli("scan", elsewhere)
    stale = cli("update", project_id)
    check("5  an L0 that does not know the project root is refused",
          stale.returncode == 2 and "scan" in (stale.stdout + stale.stderr),
          "exit %s: %s"
          % (stale.returncode, (stale.stdout + stale.stderr).strip()[:50]))

    # 6: the control. Without it, a command that refuses whenever L2 would be
    # empty passes gate 5 -- and an empty folder is a legitimate project.
    empty = os.path.join(home, "empty-project")
    os.makedirs(empty)
    cli("scan", home)
    empty_id = add(empty)
    done = cli("update", empty_id)
    _rules, rows, _edges = layers(empty_id)
    check("6  a catalogued but empty folder is 0 rows and exit 0, not a refusal",
          done.returncode == 0 and rows == 0,
          "exit %s, %d content rows" % (done.returncode, rows))

    # 7: moved. The fingerprint CP-6 needs comes from L2, so this also proves
    # the layer this command builds is the one recognition reads.
    cli("update", project_id)
    before = content_paths(project_id)
    moved_to = os.path.join(home, "moved-here")
    shutil.move(root, moved_to)
    cli("scan", home)
    moved = cli("update", project_id)
    check("7  a moved project is refused, naming where it went, nothing written",
          moved.returncode == 2
          and moved_to in (moved.stdout + moved.stderr)
          and content_paths(project_id) == before,
          "exit %s, %d rows untouched"
          % (moved.returncode, len(content_paths(project_id))))
    shutil.move(moved_to, root)
    cli("scan", home)

    # 8: deleted. The project is queued for `retire`, so building layers in it
    # is work that gets thrown away -- and it changes what the next snapshot
    # holds.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        identity.mark_deleted(project_id)
    finally:
        guard.release()
    gone = cli("update", project_id)
    check("8  a deleted project is refused, and the refusal names the way back",
          gone.returncode == 2
          and "restore" in (gone.stdout + gone.stderr).lower(),
          "exit %s: %s"
          % (gone.returncode, (gone.stdout + gone.stderr).strip()[:50]))

    # 4: no catalogue at all.
    fresh_root = make_tree(os.path.join(home, "no-l0"), flavour="three")
    fresh_id = add(fresh_root)
    os.remove(l0_path())
    missing = cli("update", fresh_id)
    check("4  a missing L0 is refused, naming the command that builds it",
          missing.returncode == 2
          and "scan" in (missing.stdout + missing.stderr),
          "exit %s: %s"
          % (missing.returncode, (missing.stdout + missing.stderr).strip()[:50]))


# -- 10, 11 ----------------------------------------------------------------

def gates_scope(work):
    """The scope is chosen by what the folder is, and recomputed every run."""
    home = fresh_home(work, "scope")

    # 10: no `.git`, so `from_folder` and its JUNK list decide.
    plain = make_tree(os.path.join(home, "plain"))
    write(os.path.join(plain, "node_modules", "dep.md"), "vendored\n")
    cli("scan", home)
    plain_id = add(plain)
    cli("update", plain_id)
    check("10 a folder without .git excludes the junk directories",
          not any(os.sep + "node_modules" + os.sep in p
                  for p in content_paths(plain_id))
          and any(p.endswith("a.md") for p in content_paths(plain_id)),
          "%d rows, none vendored" % len(content_paths(plain_id)))

    # 11: R6. A scope that is loaded and reused rather than recomputed is
    # CP-3's bug in new clothing -- patterns that were worked out and never
    # applied. Editing `.gitignore` between two runs is what tells them apart.
    repo = make_tree(os.path.join(home, "repo"), flavour="repo")
    os.makedirs(os.path.join(repo, ".git"))
    write(os.path.join(repo, "notes.md"), "kept at first\n")
    cli("scan", home)
    repo_id = add(repo)
    cli("update", repo_id)
    first = [os.path.basename(p) for p in content_paths(repo_id)]
    write(os.path.join(repo, ".gitignore"), "notes.md\n")
    cli("scan", home)
    cli("update", repo_id)
    second = [os.path.basename(p) for p in content_paths(repo_id)]
    # By name, not by count. The count is a bad proxy here and said so on the
    # first run: creating `.gitignore` adds a file to the scope in the same
    # pass that removes `notes.md`, so 3 rows became 3 rows while the contents
    # changed completely.
    check("11 editing .gitignore between runs changes what L2 holds",
          "notes.md" in first and "notes.md" not in second
          and "a.md" in second,
          "%s -> %s" % (first, second))


# -- 15 --------------------------------------------------------------------

def gates_callers():
    """Every layer builder has a caller in the package, read as call nodes."""
    # Text search is what let this defect live for five checkpoints: with the
    # call gone and only the docstring explaining the rule left behind, a grep
    # still finds the name. Measured 2026-08-04 on CP-7's gate 15.
    wanted = {"from_repo": "scope.py", "from_folder": "scope.py",
              "save": "scope.py", "content.build": "content.py",
              "graph.build": "graph.py"}
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    called: dict[str, set[str]] = {name: set() for name in wanted}
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            seen = set()
            if isinstance(func, ast.Attribute):
                seen.add(func.attr)
                if isinstance(func.value, ast.Name):
                    seen.add("%s.%s" % (func.value.id, func.attr))
            elif isinstance(func, ast.Name):
                seen.add(func.id)
            for key in wanted:
                if key in seen:
                    called[key].add(name)
    missing = [key for key, definer in wanted.items()
               if not (called[key] - {definer})]
    check("15 every layer builder is called from the package, not only tests",
          not missing, "uncalled: %s" % (", ".join(missing) or "none"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp7b-") as work:
        gates_build(work)
        gates_guards(work)
        gates_refusals(work)
        gates_scope(work)
        gates_callers()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp7b():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
