#!/usr/bin/env python3
"""CP-7 -- snapshots, the retention window, and the order the three steps run in.

The answer key is `tests/gold/FASIT-cp7.md`, written before this file and before
the code it grades (`fb14c8e`). Gate numbers below are that document's.

The failure this checkpoint prevents is not an exception. It is a project that
is removed from the living store while the copy that was supposed to hold it is
torn, empty, or of something else -- and every one of those looks like a
successful tidy-up from the outside. So gates 8 and 9 are the ones that matter
most: they assert what is *still there* after a failure, not what was raised.

Run:
    python3 tests/test_cp7.py
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import content, graph, identity, snapshot  # noqa: E402
from morpho_homegraph.lock import StoreLock, Unguarded  # noqa: E402
from morpho_homegraph.scan import scan  # noqa: E402
from morpho_homegraph.scope import Scope  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, db_path, initialise, l0_path, new_project)

results, check = reporter(60)

DAY = 86400.0


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
    """A project with L2 and L3 built. Same helper as CP-6's, same reason:
    a snapshot of an empty index would pass gates that a real one must earn."""
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


def plant(project_id, ages_in_days, now=None):
    """Real snapshots, aged with `utime`. Returns them oldest last.

    Real rather than fabricated files: a planted empty file would be removed by
    a retention rule that never looked at anything, and gates 11-14 would pass
    for a mechanism that deletes whatever it finds.
    """
    now = time.time() if now is None else now
    made = []
    for age in ages_in_days:
        path = snapshot.take(project_id)
        stamp = now - age * DAY
        os.utime(path, (stamp, stamp))
        made.append(path)
    return made


def hold_prune_guard(project_id):
    """The guard a pruner takes -- on the snapshot directory, not the store."""
    return StoreLock(snapshot.prune_guard(project_id)).acquire()


# -- 1, 2, 3, 4, 5, 7, 20 --------------------------------------------------

def gates_take_and_verify(work):
    """Taking a copy, and refusing one that is intact but of nothing."""
    home = os.path.join(work, "take")
    root = make_tree(os.path.join(home, "proj"))
    project_id = register(root)
    db = db_path(project_id)

    # 1: the store is left *open* with a committed write that has not been
    # checkpointed. That is the state a running service is in almost always,
    # and it is the state in which `shutil.copy` of index.db loses data
    # silently -- the copy opens fine and is merely older than it claims.
    guard = StoreLock(str(db)).acquire()
    try:
        store = Store(db, role=PROJECT)
        store.set_meta("wal_marker", "only in the wal")
        wal = str(db) + "-wal"
        still_in_wal = os.path.isfile(wal) and os.path.getsize(wal) > 0
        snap = snapshot.take(project_id)
        store.close()
    finally:
        guard.release()

    with Store(snap, read_only=True) as copy:
        marker = copy.get_meta("wal_marker")
        integrity = copy.db.execute("PRAGMA integrity_check").fetchone()[0]
        rows = copy.db.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    check("1  a snapshot carries what is still only in the WAL",
          still_in_wal and marker == "only in the wal",
          "wal held content: %s, marker %r" % (still_in_wal, marker))
    check("2  the snapshot opens read-only and is a valid database",
          integrity == "ok" and rows > 0,
          "integrity %s, %d content rows" % (integrity, rows))

    project_path = os.path.realpath(root)
    check("2b a good snapshot verifies",
          snapshot.verify(snap, project_id, project_path), str(snap.name))

    # 3: `integrity_check` answers `ok` for a completely empty database, so a
    # verification built on it alone accepts a copy of nothing.
    empty = os.path.join(work, "empty.db")
    sqlite3.connect(empty).close()
    check("3  an empty but structurally valid database is refused",
          not snapshot.verify(empty, project_id, project_path), "empty.db")

    check("4  a snapshot of another project is refused",
          not snapshot.verify(snap, "0" * 16, project_path), "wrong project_id")
    check("4b a snapshot naming another path is refused",
          not snapshot.verify(snap, project_id, "/somewhere/else"),
          "wrong project_path")

    # 5: torn at the tail, which is what a full disk or a killed copier leaves.
    torn = os.path.join(work, "torn.db")
    shutil.copy2(snap, torn)
    with open(torn, "r+b") as fh:
        fh.truncate(os.path.getsize(torn) // 2)
    check("5  a truncated snapshot is refused",
          not snapshot.verify(torn, project_id, project_path),
          "%d bytes" % os.path.getsize(torn))

    # 5b: the truncation above is also caught by the identity read, so it
    # proves nothing about `integrity_check` -- measured 2026-08-04, dropping
    # that check left gate 5 green. This one keeps the header and `meta`
    # readable and destroys the pages behind them, which is what a bad sector
    # or a half-written page leaves. Only `integrity_check` sees it.
    corrupt = os.path.join(work, "corrupt.db")
    shutil.copy2(snap, corrupt)
    with Store(corrupt, read_only=True) as copy:
        page = copy.db.execute("PRAGMA page_size").fetchone()[0]
    with open(corrupt, "r+b") as fh:
        # Three pages: the header, the schema, and `meta`'s own root page.
        # Two is one too few -- measured 2026-08-04, `meta` becomes unreadable
        # and the gate would then be passed by the identity check again.
        keep = 3 * page
        fh.seek(keep)
        fh.write(b"\xa5" * (os.path.getsize(corrupt) - keep))
    with Store(corrupt, read_only=True) as copy:
        meta_survived = copy.get_meta("project_id") == project_id
    check("5b a snapshot with corrupt data pages is refused",
          meta_survived
          and not snapshot.verify(corrupt, project_id, project_path),
          "meta still readable: %s" % meta_survived)

    # 7: the directory holding every project's snapshots sits beside the
    # project directories, so its name must be one no generated id can be.
    ids = [new_project()[0] for _ in range(5)]
    generated = all(len(i) == 16 and all(c in "0123456789abcdef" for c in i)
                    for i in ids)
    check("7  the snapshots directory cannot collide with a project id",
          generated and not (len(snapshot.SNAPSHOTS) == 16
                             and all(c in "0123456789abcdef"
                                     for c in snapshot.SNAPSHOTS)),
          "%r beside 16-hex ids" % snapshot.SNAPSHOTS)

    # 20: a snapshot is a copy, not a reading of what the copy means.
    dguard = StoreLock(str(db)).acquire()
    try:
        identity.mark_deleted(project_id)
        after = snapshot.take(project_id)
    finally:
        dguard.release()
    with Store(after, read_only=True) as copy:
        state = copy.get_meta("state")
    check("20 a deleted project is deleted inside its snapshot",
          state == identity.GONE, "state %r" % state)


# -- 6, 8, 9, 10, 21 -------------------------------------------------------

def gates_order(work):
    """Take, verify, release -- and never two of the three."""
    home = os.path.join(work, "order")

    # 8: the copy cannot be written. Nothing may happen to the living store.
    root = make_tree(os.path.join(home, "fails-take"))
    project_id = register(root)
    snapshot.take(project_id)          # so the directory exists to be sealed
    sealed = snapshot.snapshots_dir(project_id)
    os.chmod(sealed, 0o500)
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.retire(project_id)
            step, message = "none", "retire returned"
        except snapshot.SnapshotFailed as exc:
            step, message = exc.step, str(exc)
    finally:
        guard.release()
        os.chmod(sealed, 0o700)
    check("8  a failed snapshot leaves the living store untouched",
          step == "take" and os.path.isfile(db_path(project_id)),
          "stopped at %s, index still on disk: %s"
          % (step, os.path.isfile(db_path(project_id))))
    check("10 the failure names which of the three steps stopped",
          "take" in message, message[:70])

    # 9: the copy is written but does not verify. The most dangerous of the
    # three, because there *is* a file on disk that looks like a snapshot.
    root = make_tree(os.path.join(home, "fails-verify"))
    project_id = register(root)
    honest = snapshot.verify
    snapshot.verify = lambda *a, **k: False
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.retire(project_id)
            step, message = "none", "retire returned"
        except snapshot.SnapshotFailed as exc:
            step, message = exc.step, str(exc)
    finally:
        snapshot.verify = honest
        guard.release()
    with_index = os.path.isfile(db_path(project_id))
    check("9  a failed verification leaves the living store untouched",
          step == "verify" and with_index,
          "stopped at %s, index still on disk: %s" % (step, with_index))
    check("10b the failure names the verification step",
          "verif" in message, message[:70])

    # 10c: the third step names itself too. `release_living` is the only one
    # that can fail with the copy already good, and a caller told only "it
    # failed" cannot tell that from the two harmless cases.
    root = make_tree(os.path.join(home, "fails-release"))
    project_id = register(root)

    def refuses(*_args, **_kwargs):
        raise OSError("device or resource busy")

    honest_rmtree = snapshot.shutil.rmtree
    snapshot.shutil.rmtree = refuses
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.retire(project_id)
            step, message = "none", "retire returned"
        except snapshot.SnapshotFailed as exc:
            step, message = exc.step, str(exc)
        except Exception as exc:  # noqa: BLE001
            # Caught broadly on purpose: a failure that arrives as a bare
            # OSError has not named its step, and that is this gate's answer
            # rather than a broken harness.
            step, message = type(exc).__name__, str(exc)
    finally:
        snapshot.shutil.rmtree = honest_rmtree
        guard.release()
    check("10c a failed release names the release step",
          step == "release" and "release" in message
          and os.path.isfile(db_path(project_id)),
          "stopped at %s" % step)

    # 21: a snapshot is a read and needs no guard; removing the living store
    # is a write and is refused without one.
    root = make_tree(os.path.join(home, "unguarded"))
    project_id = register(root)
    try:
        taken = snapshot.take(project_id)         # no guard held here
        unguarded_take = os.path.isfile(taken)
    except Unguarded:
        # Caught rather than allowed to escape: a snapshot that demands the
        # guard is a wrong answer, not a broken harness, and a crash is not a
        # gate saying no.
        unguarded_take = False
    try:
        snapshot.release_living(project_id)
        refused = "released without the guard"
    except Unguarded:
        refused = "Unguarded"
    check("21 a snapshot needs no guard, releasing the store does",
          unguarded_take and refused == "Unguarded"
          and os.path.isfile(db_path(project_id)),
          "snapshot taken unguarded: %s, release: %s"
          % (unguarded_take, refused))

    # 21b: pruning is writing too (R10), and its guard is the one a released
    # project can still take -- the project directory may be gone by then.
    try:
        snapshot.apply_retention(project_id)
        pruned = "pruned without the guard"
    except Unguarded:
        pruned = "Unguarded"
    check("21b pruning snapshots without the guard is refused",
          pruned == "Unguarded", pruned)

    # 6: the whole order, run through. The snapshots must outlive the project
    # directory -- if they lived inside it, this very step would delete them.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        kept = snapshot.retire(project_id)
    finally:
        guard.release()
    check("6  snapshots outlive the removal of the project directory",
          os.path.isfile(kept) and not os.path.exists(db_path(project_id).parent)
          and snapshot.snapshots_dir(project_id) in kept.parents,
          "%s kept, project directory gone" % kept.name)


# -- 11, 12, 13, 14, 15 ----------------------------------------------------

def gates_retention(work):
    """Age decides, with a count as the floor beneath it."""
    home = os.path.join(work, "retain")
    now = time.time()

    root = make_tree(os.path.join(home, "mixed"))
    project_id = register(root)
    plant(project_id, [1, 2, 3, 30, 40], now=now)
    guard = hold_prune_guard(project_id)
    try:
        removed = snapshot.apply_retention(project_id, now=now)
    finally:
        guard.release()
    left = snapshot.snapshots(project_id)
    ages = sorted(round((now - p.stat().st_mtime) / DAY) for p in left)
    check("11 snapshots younger than the window are kept",
          ages == [1, 2, 3], "ages left: %s" % ages)
    check("12 snapshots older than the window are removed",
          len(removed) == 2 and all(not p.exists() for p in removed),
          "%d removed" % len(removed))

    # 13: a project nobody has touched for a year still has a history. Pure
    # age would have emptied it.
    root = make_tree(os.path.join(home, "idle"))
    idle = register(root)
    plant(idle, [365, 400, 500], now=now)
    guard = hold_prune_guard(idle)
    try:
        removed = snapshot.apply_retention(idle, now=now)
    finally:
        guard.release()
    check("13 the floor wins over age: an idle project keeps RETAIN_FLOOR",
          not removed and len(snapshot.snapshots(idle)) == snapshot.RETAIN_FLOOR,
          "%d kept, %d removed" % (len(snapshot.snapshots(idle)), len(removed)))

    # 14: the floor is a minimum, never a cap. Five young snapshots stay five.
    root = make_tree(os.path.join(home, "busy"))
    busy = register(root)
    plant(busy, [0, 1, 2, 3, 4], now=now)
    guard = hold_prune_guard(busy)
    try:
        removed = snapshot.apply_retention(busy, now=now)
    finally:
        guard.release()
    check("14 the floor removes nothing that age would have kept",
          not removed and len(snapshot.snapshots(busy)) == 5,
          "%d kept" % len(snapshot.snapshots(busy)))

    # 13b: a living store that cannot be read *right now* must not be taken for
    # a deleted one -- that would drop its floor and age its whole history out
    # on a permission error. Four old snapshots: with the floor, three stay.
    root = make_tree(os.path.join(home, "unreadable"))
    unreadable = register(root)
    plant(unreadable, [365, 400, 500, 600], now=now)
    os.chmod(db_path(unreadable), 0o000)
    guard = hold_prune_guard(unreadable)
    try:
        removed = snapshot.apply_retention(unreadable, now=now)
    finally:
        guard.release()
        os.chmod(db_path(unreadable), 0o600)
    check("13b an unreadable living store keeps its floor, not loses it",
          len(removed) == 1
          and len(snapshot.snapshots(unreadable)) == snapshot.RETAIN_FLOOR,
          "%d removed, %d kept"
          % (len(removed), len(snapshot.snapshots(unreadable))))

    # 15: R6. homegraph's `apply_retention()` was documented in three places as
    # the reason a table had a ceiling, and was called from tests only. Every
    # real installation grew without a bound, with every gate green. So this
    # reads the *package*, not this file.
    #
    # Parsed, not grepped. Measured 2026-08-04: with the call removed and only
    # the docstring that *explains* the rule left behind, a text search still
    # found `apply_retention(` and the gate stayed green -- which is the
    # homegraph failure exactly, one level up. A call is a call node.
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    callers = []
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py") or name == "snapshot.py":
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (func.attr if isinstance(func, ast.Attribute)
                      else getattr(func, "id", ""))
            if called == "apply_retention":
                callers.append(name)
                break
    check("15 the retention rule is called from the package, not only tests",
          bool(callers), "called from %s" % (", ".join(callers) or "nowhere"))


# -- 16, 17 ----------------------------------------------------------------

def gates_warning(work):
    """The warning is for projects that live only in snapshots."""
    home = os.path.join(work, "warn")
    now = time.time()

    # Two shapes of "deleted", because they are two branches: CP-6 marked this
    # one and left its store in place...
    root = make_tree(os.path.join(home, "condemned"))
    condemned = register(root)
    plant(condemned, [13, 3], now=now)
    guard = StoreLock(str(db_path(condemned))).acquire()
    try:
        identity.mark_deleted(condemned)
    finally:
        guard.release()

    # ...and this one has been through `retire`, so it has no living store at
    # all. Both live only in snapshots, and both have to be warned about.
    root = make_tree(os.path.join(home, "released"))
    released = register(root)
    plant(released, [13, 3], now=now)
    guard = StoreLock(str(db_path(released))).acquire()
    try:
        snapshot.retire(released)
    finally:
        guard.release()

    # 17: a living project losing its oldest history has lost history, not
    # itself. Warning here would make the warning mean nothing. Set up before
    # the warning is read, so gate 17 is asked at a moment when the mechanism
    # is demonstrably firing for someone -- otherwise "not warned about" is
    # also true of a warning that never fires at all.
    root = make_tree(os.path.join(home, "living"))
    living = register(root)
    plant(living, [1, 2, 3, 12, 13], now=now)

    warned = dict(snapshot.expiring(now=now))
    check("17 a living project losing old history is not warned about",
          living not in warned and condemned in warned,
          "%d warned, the living one among them: %s"
          % (len(warned), living in warned))
    check("17b a project with no living store left is warned about",
          released in warned,
          "%.1f days left" % warned.get(released, float("nan")))

    # 17c: the control on the window itself. Without it, a warning that fires
    # for every deleted project passes gates 16, 17 and 17b.
    root = make_tree(os.path.join(home, "fresh"))
    fresh = register(root)
    plant(fresh, [1], now=now)
    guard = StoreLock(str(db_path(fresh))).acquire()
    try:
        identity.mark_deleted(fresh)
    finally:
        guard.release()
    check("17c a deleted project with fresh snapshots is not warned about yet",
          fresh not in dict(snapshot.expiring(now=now)),
          "%d days of window left" % (snapshot.RETAIN_DAYS - 1))

    # The warning is computed from the *oldest* snapshot still holding the
    # project (R7), so it arrives while every later one is still there -- and
    # the same clock is asked both questions: does it fire now, and has
    # nothing been lost yet now.
    before = len(snapshot.snapshots(condemned))
    guard = hold_prune_guard(condemned)
    try:
        later = snapshot.apply_retention(condemned, now=now + 2 * DAY)
    finally:
        guard.release()
    left = snapshot.snapshots(condemned)
    check("16 a deleted project is warned about before a snapshot falls out",
          condemned in warned and round(warned[condemned], 1) == 1.0
          and before == 2 and len(later) == 1 and len(left) == 1,
          "%.1f days left, %d of %d snapshots gone two days later"
          % (warned.get(condemned, float("nan")), len(later), before))


# -- 18, 19 ----------------------------------------------------------------

def gates_restore(work):
    """Restoration is refused while the folder it describes is missing."""
    home = os.path.join(work, "restore")
    root = make_tree(os.path.join(home, "proj"))
    project_id = register(root)
    with Store(db_path(project_id), read_only=True) as store:
        project_path = store.get_meta("project_path")
        rows_before = store.db.execute(
            "SELECT COUNT(*) FROM content").fetchone()[0]

    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        snapshot.retire(project_id)
    finally:
        guard.release()
    shutil.rmtree(root)

    db_path(project_id).parent.mkdir(parents=True, exist_ok=True)
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.restore(project_id)
            refusal = "restored anyway"
        except snapshot.PathGone as exc:
            refusal = str(exc)
    finally:
        guard.release()
    check("18 restoring is refused while the path is gone, and names it",
          project_path in refusal and not db_path(project_id).is_file(),
          refusal[:70])

    # 19: the supported route -- the user brings the folder back from their own
    # backup, and *then* the index is restored.
    make_tree(root)
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        restored = snapshot.restore(project_id)
    finally:
        guard.release()
    with Store(restored, read_only=True) as store:
        rows_after = store.db.execute(
            "SELECT COUNT(*) FROM content").fetchone()[0]
        state = store.get_meta("state")
    living = {pid for pid, _p in identity.living_projects()}
    check("19 with the path back, the project is restored and is living again",
          rows_after == rows_before and rows_after > 0
          and state == identity.LIVING and project_id in living,
          "%d content rows, state %r" % (rows_after, state))

    # 19b: the newest snapshot is a file like any other, and a copy that was
    # interrupted while being written is newest of all. Installing it would
    # make R4 a check that runs everywhere except where it decides something.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        newest = snapshot.take(project_id)
    finally:
        guard.release()
    with open(newest, "r+b") as fh:
        fh.truncate(os.path.getsize(newest) // 2)
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.restore(project_id)
            outcome = "restored"
        except Exception as exc:  # noqa: BLE001
            outcome = type(exc).__name__
    finally:
        guard.release()
    with Store(db_path(project_id), read_only=True) as store:
        integrity = store.db.execute("PRAGMA integrity_check").fetchone()[0]
        rows = store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    check("19b a torn newest snapshot is skipped for the newest that verifies",
          outcome == "restored" and integrity == "ok" and rows == rows_before,
          "%s: integrity %s, %d rows" % (outcome, integrity, rows))

    # 19c: and when none of them verifies, nothing is installed. Refusing is
    # the only answer that leaves the user something to work with.
    keep_one = snapshot.snapshots(project_id)
    for path in keep_one[1:]:
        path.unlink()
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        try:
            snapshot.restore(project_id)
            step = "restored a snapshot that does not verify"
        except snapshot.SnapshotFailed as exc:
            step = exc.step
        except Exception as exc:  # noqa: BLE001
            step = type(exc).__name__
    finally:
        guard.release()
    with Store(db_path(project_id), read_only=True) as store:
        rows = store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    check("19c with no snapshot that verifies, nothing is installed",
          step == "verify" and rows == rows_before,
          "%s, living index still holds %d rows" % (step, rows))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp7-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        gates_take_and_verify(work)
        gates_order(work)
        gates_retention(work)
        gates_warning(work)
        gates_restore(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp7():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
