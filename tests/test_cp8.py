#!/usr/bin/env python3
"""CP-8 -- lexical search over L2, and name search over L0.

The answer key is `tests/gold/FASIT-cp8.md`, written before this file and
before the code it grades (`f12a993`). Gate numbers below are that document's.

The failure this checkpoint prevents is a search that answers. Zero hits from
an index that was never built looks exactly like zero hits from a corpus
without the word, and a `LIKE` fallback for a missing FTS5 answers *worse* in a
form nobody can see. So gates 9, 10 and 17 are the load-bearing ones, and gates
11 and 18 are the controls that stop them from being satisfied by a command
that always refuses.

Run:
    python3 tests/test_cp8.py
"""
from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import search  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.store import Store, db_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(60)


def cli(*argv, timeout=60):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        class TimedOut:
            returncode, stdout = 124, ""
            stderr = "timed out: the command never returned"
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def corpus(root):
    """A small tree with the shapes the rules are about."""
    write(os.path.join(root, "plain.md"),
          "the quick brown fox jumps over the lazy dog\n")
    # The identifier file: the words "get user by id" appear nowhere as words.
    write(os.path.join(root, "handlers.py"),
          "def getUserById(conn):\n    return conn.fetch()\n")
    write(os.path.join(root, "shapes.py"),
          "snake_case_name = 1\nHTTPServer = 2\nutf8Decode = 3\n"
          "kebab = 'kebab-case-name'\ndotted = 'dotted.path.here'\n")
    write(os.path.join(root, "ranked.md"),
          "fox fox fox fox fox fox and more fox\n")
    return root


def project(root):
    added = cli("add", root)
    return added.stdout.split()[0] if added.stdout.strip() else ""


def found(out):
    """Paths from a search command's stdout, in the order printed."""
    paths = []
    for line in out.splitlines():
        for word in line.split():
            if os.sep in word and not word.endswith(":"):
                paths.append(word)
                break
    return paths


# -- 1, 2, 4, 12, 13, 18 ---------------------------------------------------

def gates_search(home, project_id):
    hit = cli("search", "--project", project_id, "quick brown")
    check("1  a word in the text is found, and the path is returned",
          hit.returncode == 0
          and any(p.endswith("plain.md") for p in found(hit.stdout)),
          "exit %s: %s" % (hit.returncode, found(hit.stdout)))

    # 2: the main gate. "get user by id" appears nowhere in that file as
    # words -- only inside the identifier. SQLite's own tokenizers cannot do
    # this, which is why the splitting happens when the row is written.
    split = cli("search", "--project", project_id, "user by id")
    check("2  a query for user by id finds a file holding only getUserById",
          split.returncode == 0
          and any(p.endswith("handlers.py") for p in found(split.stdout)),
          "exit %s: %s" % (split.returncode, found(split.stdout)))

    # 4: without this, the mandatory splitting is invisible and its value can
    # be neither defended nor falsified.
    check("4  a hit says whether it came from the text or from the symbols",
          "symbols" in split.stdout and "text" in hit.stdout,
          "%r / %r" % (split.stdout.strip()[:40], hit.stdout.strip()[:40]))

    # 12: a user typing FTS5 syntax means the characters, not the operators.
    for query in ("foo AND (bar", "*", '"unbalanced', "NEAR(a b)"):
        odd = cli("search", "--project", project_id, query)
        # Exit 0 and no traceback, not "exit 0 or 1": a query handed to FTS5
        # as syntax raises OperationalError, which exits 1 -- so accepting 1
        # accepted the very failure this gate is for (measured 2026-08-04,
        # the mutation survived).
        if odd.returncode != 0 or "Traceback" in odd.stderr:
            check("12 FTS5 syntax in a query is text, not syntax", False,
                  "%r exited %s: %s" % (query, odd.returncode,
                                        (odd.stderr.strip() or "-")[-60:]))
            break
    else:
        check("12 FTS5 syntax in a query is text, not syntax", True,
              "four syntax-shaped queries, none crashed")

    # 13: bm25 puts the denser match first, and the order does not drift.
    first = cli("search", "--project", project_id, "fox")
    second = cli("search", "--project", project_id, "fox")
    order = found(first.stdout)
    check("13 the best match ranks first and the order is stable",
          order and order[0].endswith("ranked.md")
          and order == found(second.stdout),
          "%s" % [os.path.basename(p) for p in order])

    check("18 an ordinary search exits 0",
          hit.returncode == 0, "exit %s" % hit.returncode)


# -- 3, 5 ------------------------------------------------------------------

def gates_splitting(home, project_id):
    """The splitting table, and what it is worth in recall."""
    cases = {
        "getUserById": ["get", "user", "by", "id"],
        "snake_case_name": ["snake", "case", "name"],
        "kebab-case-name": ["kebab", "case", "name"],
        "dotted.path.here": ["dotted", "path", "here"],
        "HTTPServer": ["http", "server"],
        "utf8Decode": ["utf", "8", "decode"],
    }
    wrong = {raw: search.split_identifier(raw)
             for raw, want in cases.items()
             if search.split_identifier(raw) != want}
    check("5  the splitting table holds for every shape",
          not wrong, "wrong: %s" % (wrong or "none"))

    # 3: the measurement the answer key demands. The predecessor's "+6%" is
    # inherited and stays marked as inherited; this is our own corpus.
    queries = ["user by id", "snake case name", "http server", "utf 8 decode",
               "kebab case name", "dotted path here"]
    # Counted as *hits*, not as output: "no matches for ..." is printed on
    # stdout too, and counting non-empty output made the gate green whatever
    # the index held (measured 2026-08-04, 6 of 6 both ways).
    with_symbols = sum(
        1 for q in queries
        if found(cli("search", "--project", project_id, q).stdout))
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute("UPDATE %s SET symbols = ''" % search.TABLE)
                db.commit()
    except sqlite3.Error as exc:
        # Red, not a traceback: if the index is not there to clear, this gate
        # has no measurement to make, and a harness that dies here takes every
        # later gate with it -- a crash names no gate.
        check("3  identifier splitting measurably improves recall", False,
              "the index could not be cleared: %s" % exc)
        guard.release()
        return with_symbols, with_symbols, len(queries)
    finally:
        guard.release()
    without = sum(
        1 for q in queries
        if found(cli("search", "--project", project_id, q).stdout))
    check("3  identifier splitting measurably improves recall",
          with_symbols > without,
          "%d of %d queries answer with splitting, %d without"
          % (with_symbols, len(queries), without))
    return with_symbols, without, len(queries)


# -- 6, 7, 8 ---------------------------------------------------------------

def gates_names(work, home, project_id):
    """Names come from L0 and know no scope (locked decision 7)."""
    outside = write(os.path.join(work, "outside", "wandering-note.md"),
                    "a sentence that lives outside every scope\n")
    cli("scan", work)
    by_name = cli("search", "--names", "wandering")
    check("6  a file outside every scope is found by name",
          by_name.returncode == 0
          and any(p.endswith("wandering-note.md") for p in found(by_name.stdout)),
          "exit %s: %s" % (by_name.returncode, found(by_name.stdout)))

    by_text = cli("search", "--project", project_id, "outside every scope")
    check("7  ...and is not found by content, because it is out of scope",
          by_text.returncode == 0
          and not any(p.endswith("wandering-note.md")
                      for p in found(by_text.stdout)),
          "exit %s: %s" % (by_text.returncode, found(by_text.stdout)))
    assert os.path.isfile(outside)


def gates_names_without_projects(work):
    """8 -- name search is an L0 reader and needs no project at all."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "bare", "store")
    tree = os.path.join(work, "bare", "home")
    write(os.path.join(tree, "solitary-file.md"), "nothing registered here\n")
    cli("scan", tree)
    alone = cli("search", "--names", "solitary")
    check("8  name search answers with no project registered at all",
          alone.returncode == 0
          and any(p.endswith("solitary-file.md") for p in found(alone.stdout)),
          "exit %s: %s" % (alone.returncode, found(alone.stdout)))


# -- 9, 10, 11, 14, 16 -----------------------------------------------------

def gates_honesty(work):
    """An index that cannot answer says so. And one that can, does."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "honest", "store")
    home = corpus(os.path.join(work, "honest", "home"))
    cli("scan", home)
    project_id = project(home)
    cli("update", project_id)

    # 11: the control first. Without it, everything below is satisfied by a
    # command that reports "stale" whenever it finds nothing.
    nothing = cli("search", "--project", project_id, "zygomorphic")
    check("11 zero hits on a complete index is exit 0 and says so",
          nothing.returncode == 0 and "no matches" in nothing.stdout.lower(),
          "exit %s: %r" % (nothing.returncode, nothing.stdout.strip()[:40]))

    # 14: a reader answers while a writer holds the guard. WAL is what makes
    # that true; a search that took the guard would be blocked by every
    # indexing run -- which is exactly when someone is searching.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        during = cli("search", "--project", project_id, "quick")
    finally:
        guard.release()
    check("14 search answers while another process holds the project guard",
          during.returncode == 0 and found(during.stdout),
          "exit %s: %d hits" % (during.returncode, len(found(during.stdout))))

    # 16: the layer is replaced whole, like every layer below it.
    os.remove(os.path.join(home, "plain.md"))
    cli("scan", home)
    cli("update", project_id)
    gone = cli("search", "--project", project_id, "quick brown")
    check("16 a file removed from L2 is gone from the hits after update",
          gone.returncode == 0
          and not any(p.endswith("plain.md") for p in found(gone.stdout)),
          "exit %s: %s" % (gone.returncode, found(gone.stdout)))


# -- 15, 17 ----------------------------------------------------------------

    # 10: rows in L2 that the index has never seen. Silence here would be a
    # lie by omission -- the corpus does hold the word.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute(
                    "INSERT INTO content (path, size, mtime_ns, sha256, text,"
                    " reason) VALUES ('/tmp/unindexed.md', 5, 1, 'x',"
                    " 'zygomorphic flower', NULL)")
                db.commit()
    finally:
        guard.release()
    stale = cli("search", "--project", project_id, "zygomorphic")
    check("10 an under-filled index reports instead of returning silence",
          stale.returncode == 1
          and ("stale" in (stale.stdout + stale.stderr).lower()
               or "update" in (stale.stdout + stale.stderr).lower()),
          "exit %s: %r" % (stale.returncode,
                           (stale.stdout + stale.stderr).strip()[:60]))

    # 9: no index at all.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute("DROP TABLE IF EXISTS %s" % search.TABLE)
                db.commit()
    finally:
        guard.release()
    missing = cli("search", "--project", project_id, "quick")
    check("9  a missing index reports and exits 1, not zero hits",
          missing.returncode == 1
          and "update" in (missing.stdout + missing.stderr).lower(),
          "exit %s: %r" % (missing.returncode,
                           (missing.stdout + missing.stderr).strip()[:60]))


def gates_wiring():
    """The caller in the package, and the fallback that must not exist."""
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    called = set()
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py") or name == "search.py":
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "build" \
                        and isinstance(func.value, ast.Name) \
                        and func.value.id == "search":
                    called.add(name)
    check("15 update builds L4: search.build has a caller in the package",
          bool(called), "called from %s" % (", ".join(sorted(called)) or "nowhere"))

    # 17: FTS5 missing must be a refusal. A `LIKE` fallback would answer -- and
    # answer worse, in a way no gate and no user can see.
    #
    # Read per function, not per module: the answer key says "no LIKE fallback
    # in the module", and the module turned out to also hold the *name* search,
    # where LIKE over L0's paths is the design rather than a fallback -- L0 has
    # no FTS index and locked decision 7 says it should not need one. So the
    # check is aimed at the content path, where a fallback would actually hide.
    with open(os.path.join(package, "search.py"), encoding="utf-8") as fh:
        source = fh.read()
    likes = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name not in (
                "content", "build", "state", "as_query"):
            continue
        # The docstring is skipped: `build` says "replaced whole, like L2 and
        # L3", and English prose is not a fallback. Measured 2026-08-04 --
        # that sentence alone made this gate red.
        body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(node.body[0].value, ast.Constant)
                                 and isinstance(node.body[0].value.value, str)
                                 ) else node.body
        for statement in body:
            likes += [n.value for n in ast.walk(statement)
                      if isinstance(n, ast.Constant)
                      and isinstance(n.value, str)
                      and " like " in n.value.lower()]

    class Refuses:
        """A connection whose FTS5 is not there."""

        def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("no such module: fts5")

    check("17 without FTS5 the search refuses, and there is no LIKE fallback",
          not search.fts5_available(Refuses()) and not likes,
          "%d LIKE strings in search.py" % len(likes))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp8-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        home = corpus(os.path.join(work, "home"))
        cli("scan", work)
        project_id = project(home)
        built = cli("update", project_id)
        if built.returncode != 0:
            check("0  the project builds before anything is searched", False,
                  "update exited %s: %s"
                  % (built.returncode, built.stderr.strip()[:60]))
        else:
            gates_search(home, project_id)
            gates_names(work, home, project_id)
            gates_splitting(home, project_id)
        gates_names_without_projects(work)
        gates_honesty(work)
        gates_wiring()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp8():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
