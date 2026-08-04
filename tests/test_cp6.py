#!/usr/bin/env python3
"""CP-6 -- identity when a project directory is moved, copied or deleted.

The answer key is `tests/gold/FASIT-cp6.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

The failure this checkpoint prevents is not an exception. It is a project whose
index quietly starts describing a different folder -- and the two mechanisms
that could do it are an inode the filesystem handed out twice, and a
fingerprint shared by two identical trees. Gates 6 and 17 are those two, and
gates 12 and 13 are the controls that keep them from being satisfied by a
mechanism that always refuses.

Run:
    python3 tests/test_cp6.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import content, graph, identity  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.scan import scan  # noqa: E402
from morpho_homegraph.scope import Scope  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, db_path, initialise, l0_path, new_project, projects)

results, check = reporter(60)


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def make_tree(root, flavour="one"):
    write(os.path.join(root, "a.md"), "the %s tree, see [[b]]\n" % flavour)
    write(os.path.join(root, "b.md"), "leaf of %s\n" % flavour)
    return root


def register(root):
    """A project with L2 and L3 built, and its root's (dev, inode) recorded."""
    project_id, db = new_project()
    l0_db = l0_path()
    l0_db.parent.mkdir(parents=True, exist_ok=True)
    guard0 = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, root, deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    initialise(store, project_id, root)
                    identity.remember_root(store, root)
                    content.build(store, l0, Scope().add(root))
                    graph.build(store, scope_root=root)
            finally:
                guard.release()
    finally:
        guard0.release()
    return project_id


def rescanned_l0(root):
    """L0 refreshed over `root`, held open for the caller."""
    l0_db = l0_path()
    guard = StoreLock(str(l0_db)).acquire()
    store = Store(l0_db, role=L0)
    scan(store, root, deny=())
    return store, guard


def gates_verdict():
    """1, 2, 3 -- the table, and nothing but `exists()` deciding it."""
    with tempfile.TemporaryDirectory(prefix="mhg-cp6-v") as work:
        old = os.path.join(work, "old")
        new = os.path.join(work, "new")
        os.makedirs(old)
        os.makedirs(new)
        check("1  both paths present is a copy",
              identity.verdict(old, new) == identity.COPY,
              identity.verdict(old, new))
        shutil.rmtree(old)
        check("2  the old gone and the new present is a move",
              identity.verdict(old, new) == identity.MOVE,
              identity.verdict(old, new))
        shutil.rmtree(new)
        check("3  both gone is a deletion",
              identity.verdict(old, new) == identity.DELETED,
              identity.verdict(old, new))


def gates_move(work):
    """4, 7, 8, 9, 10, 13 -- a real move, recognised and applied."""
    home = os.path.join(work, "home")
    old = make_tree(os.path.join(home, "proj"))
    project_id = register(old)

    with Store(db_path(project_id), read_only=True) as store:
        edges_before = store.db.execute(
            "SELECT src, dst, method, confidence FROM edges").fetchall()
        hashes_before = sorted(h for (h,) in store.db.execute(
            "SELECT sha256 FROM content WHERE sha256 IS NOT NULL"))

    new = os.path.join(home, "moved-here")
    shutil.move(old, new)
    l0, guard = rescanned_l0(home)
    try:
        answer = identity.recognise(project_id, l0)
        check("4  a move is recognised by the inode, and the path is named",
              answer["verdict"] == identity.MOVE and answer["path"] == new
              and answer["by"] == "inode",
              "%s by %s -> %s"
              % (answer["verdict"], answer["by"], answer["path"]))

        # 13: the inode path must not be the only way in. Forget the inode and
        # the fingerprint alone has to find it -- otherwise gate 4 is
        # satisfied by a mechanism with one route and no fallback.
        pguard = StoreLock(str(db_path(project_id))).acquire()
        try:
            with Store(db_path(project_id), role=PROJECT) as store:
                keep_dev = store.get_meta("root_dev")
                keep_inode = store.get_meta("root_inode")
                store.set_meta("root_dev", "")
                store.set_meta("root_inode", "")
        finally:
            pguard.release()
        without_inode = identity.recognise(project_id, l0)
        pguard = StoreLock(str(db_path(project_id))).acquire()
        try:
            with Store(db_path(project_id), role=PROJECT) as store:
                store.set_meta("root_dev", keep_dev or "")
                store.set_meta("root_inode", keep_inode or "")
        finally:
            pguard.release()
        check("13 the fingerprint alone finds it when the inode cannot",
              without_inode["verdict"] == identity.MOVE
              and without_inode["path"] == new
              and without_inode["by"] == "fingerprint",
              "%s by %s -> %s"
              % (without_inode["verdict"], without_inode["by"],
                 without_inode["path"]))
    finally:
        guard.release()
        l0.close()

    # 10: while the store still points at the vanished path, opening it must
    # say where the project went. An empty answer and "it is over there" look
    # identical to a caller, and only one of them is true (house rule 6). This
    # has to run *before* the move is applied -- afterwards the project is
    # present and returning normally is the right answer.
    l0b, guardb = rescanned_l0(home)
    try:
        try:
            identity.open_project(project_id, l0b)
            fired = "returned normally"
        except identity.Moved as exc:
            fired = exc.new
        except Exception as exc:  # noqa: BLE001
            fired = type(exc).__name__
    finally:
        guardb.release()
        l0b.close()
    check("10 the old path is refused with where it went, not emptied",
          fired == new, "Moved -> %s" % fired)

    pguard = StoreLock(str(db_path(project_id))).acquire()
    try:
        rewritten = identity.apply_move(project_id, new)
    finally:
        pguard.release()
    with Store(db_path(project_id), read_only=True) as store:
        check("7  the project now points at where it went",
              store.get_meta("project_path") == new,
              store.get_meta("project_path") or "unset")
        edges_after = store.db.execute(
            "SELECT src, dst, method, confidence FROM edges").fetchall()
        paths_after = [p for (p,) in store.db.execute(
            "SELECT path FROM content")]
        hashes_after = sorted(h for (h,) in store.db.execute(
            "SELECT sha256 FROM content WHERE sha256 IS NOT NULL"))
    # R6 is what CP-5's identity choice buys: nothing an edge holds is a path.
    check("8  a move loses no edge and changes no provenance",
          bool(edges_before) and sorted(edges_before) == sorted(edges_after),
          "%d edges before, %d after"
          % (len(edges_before), len(edges_after)))
    check("9  the recorded paths follow, the hashes do not move",
          bool(paths_after) and rewritten == len(paths_after)
          and all(p.startswith(new + os.sep) for p in paths_after)
          and hashes_before == hashes_after,
          "%d paths rewritten, %d hashes unchanged"
          % (rewritten, len(hashes_after)))
    # 18: the inode must be refreshed by the move, or the *next* move falls
    # back to the fingerprint and the cheap route quietly stops being used.
    second = os.path.join(home, "moved-again")
    shutil.move(new, second)
    l0c, guardc = rescanned_l0(home)
    try:
        again = identity.recognise(project_id, l0c)
        check("18 a second move is still found by the inode",
              again["verdict"] == identity.MOVE and again["path"] == second
              and again["by"] == "inode",
              "%s by %s" % (again["verdict"], again["by"]))
    finally:
        guardc.release()
        l0c.close()
    shutil.move(second, new)
    return home, project_id, new


def gates_ambiguity(work):
    """6, 12 -- two identical trees, and the control that keeps 6 honest."""
    home = os.path.join(work, "amb")
    old = make_tree(os.path.join(home, "proj"))
    project_id = register(old)
    # Two copies of the same content, and the original gone. Identical trees
    # *are* identical -- choosing between them would move a whole project's
    # index into the wrong folder.
    shutil.copytree(old, os.path.join(home, "copy-a"))
    shutil.copytree(old, os.path.join(home, "copy-b"))
    shutil.rmtree(old)
    l0, guard = rescanned_l0(home)
    try:
        answer = identity.recognise(project_id, l0)
        check("6  two identical trees are refused, not chosen between",
              answer["verdict"] == identity.AMBIGUOUS
              and answer["path"] is None and len(answer["candidates"]) == 2,
              "%s, %d candidates"
              % (answer["verdict"], len(answer["candidates"])))
    finally:
        guard.release()
        l0.close()

    # 12: with one of the two made different, exactly one answer must come
    # back. Without this, a mechanism that always refuses passes gate 6.
    write(os.path.join(home, "copy-b", "extra.md"), "not a twin any more\n")
    l0, guard = rescanned_l0(home)
    try:
        answer = identity.recognise(project_id, l0)
        check("12 with the trees no longer identical, exactly one answer",
              answer["verdict"] == identity.MOVE
              and answer["path"] == os.path.join(home, "copy-a"),
              "%s -> %s" % (answer["verdict"], answer["path"]))
    finally:
        guard.release()
        l0.close()


def gates_empty(work):
    """19 -- an empty project has nothing to go on, and must say so.

    Blind spot 3 in the answer key: every empty tree has the same shape, so a
    fingerprint over nothing would match the first empty directory it met.
    """
    home = os.path.join(work, "empty")
    old = os.path.join(home, "proj")
    os.makedirs(old)
    project_id = register(old)
    os.makedirs(os.path.join(home, "another-empty"))
    os.rmdir(old)
    l0, guard = rescanned_l0(home)
    try:
        answer = identity.recognise(project_id, l0)
        check("19 an empty project matches no empty directory",
              answer["verdict"] == identity.DELETED and answer["path"] is None,
              "%s -> %s" % (answer["verdict"], answer["path"]))
    finally:
        guard.release()
        l0.close()


def gates_inode_reuse(work):
    """17 -- an inode hit is a candidate, never a proof."""
    home = os.path.join(work, "reuse")
    old = make_tree(os.path.join(home, "proj"))
    project_id = register(old)
    with Store(db_path(project_id), read_only=True) as store:
        dev = store.get_meta("root_dev")
    # A stranger's folder, and the project told that the stranger carries the
    # inode it is looking for. A filesystem really does hand the same number
    # out again; here it is planted rather than waited for.
    stranger = make_tree(os.path.join(home, "stranger"), flavour="other")
    shutil.rmtree(old)
    st = os.stat(stranger)
    pguard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id), role=PROJECT) as store:
            store.set_meta("root_dev", str(st.st_dev))
            store.set_meta("root_inode", str(st.st_ino))
    finally:
        pguard.release()
    l0, guard = rescanned_l0(home)
    try:
        answer = identity.recognise(project_id, l0)
        check("17 an inode hit with the wrong content is not a move",
              answer["verdict"] == identity.DELETED
              and answer["path"] is None,
              "%s -> %s" % (answer["verdict"], answer["path"]))
    finally:
        guard.release()
        l0.close()
    assert dev is not None


def gates_registry_and_deletion(work):
    """11, 14, 15, 16 -- copies, the registry's direction, and marking."""
    home = os.path.join(work, "reg")
    first = make_tree(os.path.join(home, "one"))
    second = make_tree(os.path.join(home, "two"), flavour="second")
    id_one = register(first)
    id_two = register(second)
    check("11 a copy is two projects with two ids, neither refused",
          id_one != id_two
          and {id_one, id_two} <= {pid for pid, _p in projects()},
          "%s and %s" % (id_one[:8], id_two[:8]))

    # R1: nothing anywhere is keyed on a path. A `path` column that is a
    # PRIMARY KEY or UNIQUE would be a path-keyed identity, which is the one
    # thing the registry may not grow.
    offenders = []
    for pid, _path in projects():
        with Store(db_path(pid), read_only=True) as store:
            for (sql,) in store.db.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table'"):
                if not sql:
                    continue
                for line in sql.splitlines():
                    stripped = line.strip().lower()
                    if stripped.startswith("project_path") or (
                            "project_path" in stripped
                            and ("primary key" in stripped
                                 or "unique" in stripped)):
                        offenders.append((pid, line.strip()))
    check("14 no table keys anything on a project's path",
          not offenders, "%d offending declarations" % len(offenders))

    shutil.rmtree(second)
    l0, guard = rescanned_l0(home)
    try:
        one = identity.recognise(id_two, l0)
        swept = identity.recognise_all(l0)
        # 16: the sweep is a loop over the single-project function. A second
        # implementation is how one of them starts answering something else.
        check("16 the sweep and the single answer agree on the same state",
              swept[id_two] == one
              and swept[id_one]["verdict"] == identity.PRESENT,
              "%s == %s" % (swept[id_two]["verdict"], one["verdict"]))
    finally:
        guard.release()
        l0.close()

    check("3b the deleted project is reported deleted, not missing",
          one["verdict"] == identity.DELETED, one["verdict"])

    dguard = StoreLock(str(db_path(id_two))).acquire()
    try:
        identity.mark_deleted(id_two)
    finally:
        dguard.release()
    living = {pid for pid, _p in identity.living_projects()}
    check("15 marking removes it from listings and leaves the store alone",
          id_two not in living and id_one in living
          and os.path.isfile(db_path(id_two)),
          "%d living, the index file still on disk" % len(living))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp6-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        gates_verdict()
        gates_move(work)
        gates_ambiguity(work)
        gates_empty(work)
        gates_inode_reuse(work)
        gates_registry_and_deletion(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp6():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
