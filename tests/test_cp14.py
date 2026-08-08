#!/usr/bin/env python3
"""CP-14 -- equivalence: an updated index against one built from nothing.

The answer key is `tests/gold/FASIT-cp14.md`, written before this file and
before the code it grades (`5738a88`), **and corrected in place the same
evening** (`4f27582`) when its predicted divergence turned out not to exist.
Gate numbers below are that document's, after the correction.

What the correction changed: `files.content_hash` is not an incremental axis,
because no production caller ever gives `scan` a scope and the column is
therefore NULL everywhere -- 0 of 430 189 rows on the real store. That leaves
**`vectors` as the only genuinely incremental state this checkpoint can
test**, and the four rebuilt-whole layers as gates that are free today and
load-bearing the day one of them stops being rebuilt whole.

The corpus differs on nine axes, and gate 10 counts them rather than trusting
this docstring: an axis written wrong is an axis that is not tested, and it
looks exactly like one that passes.

Run:
    python3 tests/test_cp14.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from report import reporter  # noqa: E402

from cp14_equivalence import (  # noqa: E402
    catalogue_state, compare, project_state, report)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(64)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(home, *argv, timeout=600):
    """One command against the store rooted at `home`."""
    env = dict(os.environ, MORPHO_HOMEGRAPH_HOME=home)
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO, env=env,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(body)
    return path


def project_db(home):
    """The one project index under `home`; `l0` is not a project id."""
    for name in sorted(os.listdir(home)):
        if name != "l0" and os.path.isfile(os.path.join(home, name,
                                                        "index.db")):
            return os.path.join(home, name, "index.db")
    return ""


def build(home, corpus, register=True):
    """Catalogue, register, fill the layers, embed. Returns the exit codes."""
    codes = [cli(home, "scan", corpus).returncode]
    if register:
        added = cli(home, "add", corpus)
        codes.append(added.returncode)
    project = os.path.basename(os.path.dirname(project_db(home)))
    codes.append(cli(home, "update", project).returncode)
    codes.append(cli(home, "embed", project).returncode)
    return codes


# -- the corpus, and the nine axes it has to differ on ---------------------

# Equal length, different bytes. The first attempt was 48 against 49
# characters, and gate 10 is what said so -- an axis written wrong is an axis
# that is not tested and looks exactly like one that passes.
#
# **There is deliberately no `assert` here.** One was added after that first
# miss, and the mutation sweep showed what it cost: breaking the axis then
# raised `AssertionError` at import, the suite died before printing anything,
# and the verdict became "detected only by a crash" -- which names no gate.
# A defensive check that fires earlier than the gate replaces a red gate with
# a stack trace. Gate 10 is the check.
SNEAKY_A = "0123456789 the first version, exactly this long."
SNEAKY_B = "9876543210 the second version, exactly this!!!!."


def corpus_a(root):
    write(os.path.join(root, ".gitignore"), "notes/\n")
    write(os.path.join(root, "a.md"), "alpha, and it points at [[edited]]\n")
    write(os.path.join(root, "gone.md"), "this one disappears in B\n")
    write(os.path.join(root, "edited.md"), "beta, the first version\n")
    write(os.path.join(root, "sneaky.md"), SNEAKY_A + "\n")
    write(os.path.join(root, "moved-from.md"), "the text that moves\n")
    write(os.path.join(root, "twin-a.md"), "identical bytes on two paths\n")
    write(os.path.join(root, "linker.md"), "see [[a]]\n")
    write(os.path.join(root, "dropped", "keep.md"), "in scope in A only\n")
    return root


def corpus_b(root):
    """A -> B along all nine axes. Returns what gate 10 checks."""
    facts = {}
    # 1 added
    write(os.path.join(root, "added.md"), "a file that was not there\n")
    facts["added"] = os.path.isfile(os.path.join(root, "added.md"))
    # 2 removed
    os.unlink(os.path.join(root, "gone.md"))
    facts["removed"] = not os.path.exists(os.path.join(root, "gone.md"))
    # 3 edited, size and mtime both move
    before = os.stat(os.path.join(root, "edited.md"))
    write(os.path.join(root, "edited.md"),
          "beta, the second version, and it is longer than the first\n")
    after = os.stat(os.path.join(root, "edited.md"))
    facts["edited"] = before.st_size != after.st_size
    # 4 edited with size AND mtime preserved -- L1's documented blind spot
    sneaky = os.path.join(root, "sneaky.md")
    was = os.stat(sneaky)
    write(sneaky, SNEAKY_B + "\n")
    os.utime(sneaky, ns=(was.st_atime_ns, was.st_mtime_ns))
    now = os.stat(sneaky)
    facts["silent_edit"] = (was.st_size == now.st_size
                            and was.st_mtime_ns == now.st_mtime_ns
                            and SNEAKY_A != SNEAKY_B)
    # 5 moved
    os.rename(os.path.join(root, "moved-from.md"),
              os.path.join(root, "moved-to.md"))
    facts["moved"] = (os.path.isfile(os.path.join(root, "moved-to.md"))
                      and not os.path.exists(os.path.join(root,
                                                          "moved-from.md")))
    # 6 copied: two paths, one hash, which CP-5 makes one node
    shutil.copyfile(os.path.join(root, "twin-a.md"),
                    os.path.join(root, "twin-b.md"))
    facts["copied"] = (open(os.path.join(root, "twin-a.md"), "rb").read()
                       == open(os.path.join(root, "twin-b.md"), "rb").read())
    # 7 a file that gets a `reason` instead of text
    write(os.path.join(root, "binary.bin"), b"\x00\x01\x02\x00garbage", "wb")
    facts["unreadable"] = os.path.isfile(os.path.join(root, "binary.bin"))
    # 8 out of scope: the layout changes, not the file
    write(os.path.join(root, ".gitignore"), "notes/\ndropped/\n")
    facts["left_scope"] = "dropped/" in open(
        os.path.join(root, ".gitignore")).read()
    # 9 an edge moves
    write(os.path.join(root, "linker.md"), "see [[edited]]\n")
    facts["edge_moved"] = "[[edited]]" in open(
        os.path.join(root, "linker.md")).read()
    return facts


# -- the gates -------------------------------------------------------------

def gates(work):
    corpus = corpus_a(os.path.join(work, "corpus"))
    inc = os.path.join(work, "inc")
    fresh = os.path.join(work, "fresh")

    codes_a = build(inc, corpus)
    if not check("0  the A build succeeds before anything is compared",
                 codes_a == [0, 0, 0, 0], "exit codes %s" % (codes_a,)):
        return
    # The state at A, kept as files so R5's control has something to be
    # unequal to without paying for a third embedding run.
    at_a = shutil.copytree(inc, os.path.join(work, "at-a"))

    axes = corpus_b(corpus)
    check("10 CONTROL: every one of the nine axes is really in the corpus",
          all(axes.values()),
          "missing: %s" % ([k for k, v in axes.items() if not v] or "none"))

    check("0b the B builds succeed",
          build(inc, corpus, register=False) == [0, 0, 0]
          and build(fresh, corpus) == [0, 0, 0, 0])

    inc_p, fresh_p, a_p = (project_db(inc), project_db(fresh),
                           project_db(at_a))
    diff = compare(project_state(inc_p), project_state(fresh_p))

    # 1, 2, 3: the vectors, which are the only state that survives a rebuild.
    check("1  the vector keys are the same set in A->B and in B",
          "vectors" not in diff, report({k: diff[k] for k in ("vectors",)
                                         if k in diff})[:110])
    check("2  the vector bytes agree for every key both hold",
          "vector_bytes" not in diff,
          "" if "vector_bytes" not in diff else
          "the model is not deterministic here, or a vector is stale: %s"
          % report({"vector_bytes": diff["vector_bytes"]})[:90])
    stale = _vectors_only_in(a_p, fresh_p)
    check("3  the edited file leaves no vector behind for its old hash",
          not _vectors_only_in(inc_p, fresh_p) and bool(stale),
          "%d hash(es) died with corpus A, %d survive wrongly"
          % (len(stale), len(_vectors_only_in(inc_p, fresh_p))))

    # 4-8: free today, and the point is that they stop being free silently.
    for gate, name in ((4, "content"), (5, "edges"), (6, "l4"),
                       (7, "scope"), (8, "meta")):
        check("%-2d %s is the same set" % (gate, name), name not in diff,
              report({name: diff[name]})[:110] if name in diff else "")

    # 4b: gate 4 compares whatever `project_state` decided a content row is.
    # Reduce that to the path alone and "read and empty" stops being
    # distinguishable from "not read" -- the difference CP-4 exists for -- and
    # gate 4 goes on passing. So the shape of the key is graded too.
    #
    # Every field below is computed defensively, because `check`'s detail
    # argument is evaluated *before* the condition can short-circuit: an
    # earlier version wrote `sum(1 for r in rows if r[2])` there, and the
    # mutation that shrinks the key to one column raised `IndexError` while
    # building the message for a gate that was about to go red anyway. Third
    # time today that something in front of a gate turned red into a crash.
    rows = project_state(fresh_p)["content"]
    widths = {len(r) for r in rows}
    unread = sum(1 for r in rows if len(r) == 3 and r[2])
    hashed = sum(1 for r in rows if len(r) == 3 and r[1])
    check("4b CONTROL: a content key carries sha256 and reason, not just path",
          widths == {3} and unread > 0 and hashed > 0,
          "%d rows, key widths %s, %d unread, %d hashed"
          % (len(rows), sorted(widths), unread, hashed))

    # 8b: the exclusion list is what makes gate 8 mean anything. Excluding one
    # key too many is invisible; excluding everything makes `meta` equal for
    # any two stores at all, and gate 8 would go on passing.
    compared = {k for k, _v in project_state(inc_p)["meta"]}
    check("8b CONTROL: meta still compares the keys that matter",
          {"project_path", "schema_version", "embed_chunking",
           "embed_model"} <= compared,
          "compared: %d keys" % len(compared))

    # 9: the control for the whole comparison. With A on one side, every
    # exclusion still in force, it must come out unequal -- otherwise gates
    # 1-8 are green for a comparison that ignores everything.
    control = compare(project_state(a_p), project_state(fresh_p))
    check("9  CONTROL: the store at A is NOT equivalent to the one at B",
          bool(control), "differs in: %s" % (sorted(control) or "nothing"))

    # 11, 12, 13: L0.
    l0_inc = os.path.join(inc, "l0", "index.db")
    l0_fresh = os.path.join(fresh, "l0", "index.db")
    hashes = _hashes(l0_inc) | _hashes(l0_fresh)
    check("11 files.content_hash is inert: no production path ever writes it",
          hashes == {None},
          "distinct values: %s" % sorted(str(h)[:12] for h in hashes))
    inert = _scan_passes_no_scope()
    check("12 the moment scan is given a scope this gate goes red, by design",
          inert,
          "" if inert else "a caller now passes a scope: content_hash starts "
          "being written, and gate 11 has to be decided again")
    l0diff = compare(catalogue_state(l0_inc), catalogue_state(l0_fresh))
    check("13 the catalogue rows are the same set",
          "files" not in l0diff,
          report({"files": l0diff["files"]})[:110] if "files" in l0diff else "")

    # 14: the vector layer's identity, which is not in the vector key.
    check("14 embed_chunking, embed_model and embed_dim agree",
          not _meta_differs(inc_p, fresh_p,
                            ("embed_chunking", "embed_model", "embed_dim")))

    # 14b: R7's real content, and gate 14 alone cannot reach it -- both stores
    # run the same constant, so they agree on `embed_chunking` whatever the
    # code does with it. What has to be true is that a store whose *recorded*
    # cut differs re-embeds everything: `(sha, 3)` after a re-cut is different
    # text under a key that has not changed, and nothing in the keys can see
    # it. So the recorded value is falsified by hand and the run is read.
    project = os.path.basename(os.path.dirname(inc_p))
    _set_meta(inc_p, "embed_chunking", "999/9")
    recut = cli(inc, "embed", project)
    after = project_state(inc_p)
    check("14b a store whose recorded cut differs re-embeds every vector",
          recut.returncode == 0 and "0 reused" in recut.stdout
          and after["vectors"] == project_state(fresh_p)["vectors"],
          "%r" % (recut.stdout.strip().splitlines() or [""])[-1][:60])

    # 15, 16: the report itself.
    # Both sides named, not one. A comparison that only computes `a - b` is
    # blind to a key the *rebuilt* store has and the updated one lacks --
    # which is the direction an incremental path that forgets to add things
    # fails in, and the control below would still be green for it.
    told = report(control)
    check("15 a difference is reported as keys, and from both sides",
          "only_in_a" in told and "only_in_b" in told and ":" in told,
          told.splitlines()[0][:70])
    check("16 CONTROL: two equivalent stores report nothing and that reads "
          "as equivalence",
          not diff and "equivalent" in report(diff),
          report(diff)[:70])


def _vectors_only_in(left, right):
    diff = compare(project_state(left), project_state(right))
    return diff.get("vectors", {}).get("only_in_a", [])


def _set_meta(db_path, key, value):
    """Falsify one recorded fact, so a gate can read what the code does next.

    Writing straight to the file rather than through the CLI on purpose:
    there is no command for "pretend the chunk boundaries moved", and adding
    one so a test could call it would be a production feature that exists for
    the test.
    """
    import sqlite3
    db = sqlite3.connect(db_path)
    try:
        db.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, value))
        db.commit()
    finally:
        db.close()


def _hashes(l0_db):
    import sqlite3
    db = sqlite3.connect("file:%s?mode=ro" % l0_db, uri=True)
    try:
        return {h for (h,) in db.execute("SELECT DISTINCT content_hash "
                                         "FROM files")}
    finally:
        db.close()


def _meta_differs(left, right, keys):
    a = dict(project_state(left)["meta"])
    b = dict(project_state(right)["meta"])
    return [k for k in keys if a.get(k) != b.get(k)]


def _scan_passes_no_scope():
    """True while no caller in the package hands `scan` a scope.

    Read as call nodes, not as text: this file's own prose says `scan(store,`
    several times, and a grep would count it. When someone does start passing
    one, `content_hash` begins to be written and gate 11 stops being true --
    so this gate is what makes that a red check rather than a silent change
    of meaning.
    """
    import ast

    import morpho_homegraph
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py") or name == "scan.py":
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (func.attr if isinstance(func, ast.Attribute)
                     else getattr(func, "id", ""))
            if named != "scan":
                continue
            if len(node.args) > 2 or any(kw.arg == "scope"
                                         for kw in node.keywords):
                return False
    return True


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp14-") as work:
        gates(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp14():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
