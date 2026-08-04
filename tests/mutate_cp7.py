#!/usr/bin/env python3
"""Mutation test for CP-7 -- the copy, the order, the window, and the refusal.

The failure this checkpoint prevents is silent, so most of the mutations below
make the code *more* willing: copy the database file instead of backing it up,
trust a copy without reading it back, release the living store before the copy
has been verified, restore over a folder that is not there.

The other half aims at the failure that looks safe. A verification that always
refuses passes every gate about refusing, and a warning that fires for everyone
passes every gate about firing -- gates 2b and 17c are the controls, and these
mutations are what prove the controls work.

Run:
    python3 tests/mutate_cp7.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the copy ----------------------------------------------------------
    #
    # The dangerous one. `index.db` is in WAL, so a plain file copy silently
    # loses everything not yet checkpointed -- and the copy opens fine.
    ("the snapshot is a plain file copy of index.db",
     "morpho_homegraph/snapshot.py",
     "    with Store(db_path(project_id), read_only=True) as store:\n"
     "        target = sqlite3.connect(str(path))\n"
     "        try:\n"
     "            store.db.backup(target)\n"
     "        finally:\n"
     "            target.close()\n"
     "    return path",
     "    shutil.copy2(db_path(project_id), path)  # mutated: a file copy\n"
     "    return path",
     "1  a snapshot carries what is still only in the WAL"),

    ("snapshots are kept inside the project directory",
     "morpho_homegraph/snapshot.py",
     "    return data_home() / SNAPSHOTS / project_id",
     "    return db_path(project_id).parent / SNAPSHOTS  # mutated",
     "6  snapshots outlive the removal of the project directory"),

    ("the snapshots directory is named like a generated id",
     "morpho_homegraph/snapshot.py",
     'SNAPSHOTS = "snapshots"',
     'SNAPSHOTS = "0123456789abcdef"  # mutated',
     "7  the snapshots directory cannot collide with a project id"),

    # -- verification ------------------------------------------------------
    ("an intact database is accepted whatever it holds",
     "morpho_homegraph/snapshot.py",
     '            return (copy.get_meta("project_id") == project_id\n'
     '                    and copy.get_meta("project_path") == project_path)',
     "            return True  # mutated: intact is enough",
     "3  an empty but structurally valid database is refused"),

    ("only the id is checked, not the path it claims",
     "morpho_homegraph/snapshot.py",
     '            return (copy.get_meta("project_id") == project_id\n'
     '                    and copy.get_meta("project_path") == project_path)',
     '            return copy.get_meta("project_id") == project_id  # mutated',
     "4b a snapshot naming another path is refused"),

    ("only the path is checked, not whose index it is",
     "morpho_homegraph/snapshot.py",
     '            return (copy.get_meta("project_id") == project_id\n'
     '                    and copy.get_meta("project_path") == project_path)',
     '            return copy.get_meta("project_path") == project_path'
     '  # mutated',
     "4  a snapshot of another project is refused"),

    ("the integrity check is dropped",
     "morpho_homegraph/snapshot.py",
     '            row = copy.db.execute("PRAGMA integrity_check").fetchone()\n'
     '            if not row or row[0] != "ok":\n'
     "                return False",
     "            pass  # mutated: no integrity check",
     "5b a snapshot with corrupt data pages is refused"),

    # The control's own mutation: a verification that refuses everything is
    # not a verification, and gates 3, 4 and 5 cannot tell the difference.
    ("verification refuses everything, including a good copy",
     "morpho_homegraph/snapshot.py",
     "    try:\n"
     "        with Store(snapshot_path, read_only=True) as copy:\n"
     '            row = copy.db.execute("PRAGMA integrity_check").fetchone()',
     "    if True:\n"
     "        return False  # mutated: nothing is ever trusted\n"
     "    try:\n"
     "        with Store(snapshot_path, read_only=True) as copy:\n"
     '            row = copy.db.execute("PRAGMA integrity_check").fetchone()',
     "2b a good snapshot verifies"),

    # -- take, verify, release: never two of the three ---------------------
    ("the copy is trusted without being read back",
     "morpho_homegraph/snapshot.py",
     "    if not verify(snapshot_path, project_id, project_path):",
     "    if False:  # mutated: trust the copy",
     "9  a failed verification leaves the living store untouched"),

    ("the living store is released before the copy is verified",
     "morpho_homegraph/snapshot.py",
     "    if not verify(snapshot_path, project_id, project_path):",
     "    release_living(project_id)  # mutated: released before verifying\n"
     "    if not verify(snapshot_path, project_id, project_path):",
     "9  a failed verification leaves the living store untouched"),

    ("a failed copy falls back to an older snapshot and carries on",
     "morpho_homegraph/snapshot.py",
     "    except (sqlite3.Error, OSError) as exc:\n"
     '        raise SnapshotFailed("take", exc) from exc',
     "    except (sqlite3.Error, OSError):  # mutated: carry on regardless\n"
     "        snapshot_path = snapshots(project_id)[0]",
     "8  a failed snapshot leaves the living store untouched"),

    ("the failure does not say which step stopped",
     "morpho_homegraph/snapshot.py",
     '        super().__init__("snapshot stopped at the %s step: %s"'
     " % (step, detail))",
     '        super().__init__("the snapshot failed: %s" % detail)  # mutated',
     "10 the failure names which of the three steps stopped"),

    # -- the guard (R10) ---------------------------------------------------
    ("the living store is removed without the write guard",
     "morpho_homegraph/snapshot.py",
     "    if not holds(str(store_db)):\n"
     "        raise Unguarded(str(store_db))\n"
     "    shutil.rmtree(store_db.parent)",
     "    shutil.rmtree(store_db.parent)  # mutated: no guard",
     "21 a snapshot needs no guard, releasing the store does"),

    ("snapshots are pruned without the write guard",
     "morpho_homegraph/snapshot.py",
     "    guarded = prune_guard(project_id)\n"
     "    if not holds(guarded):\n"
     "        raise Unguarded(guarded)",
     "    pass  # mutated: no guard",
     "21b pruning snapshots without the guard is refused"),

    # -- the window --------------------------------------------------------
    ("the floor protects deleted projects too, so history never expires",
     "morpho_homegraph/snapshot.py",
     "    floor = 0 if state_of(project_id) == GONE else RETAIN_FLOOR",
     "    floor = RETAIN_FLOOR  # mutated",
     "16 a deleted project is warned about before a snapshot falls out"),

    ("nothing ever expires",
     "morpho_homegraph/snapshot.py",
     "        if (now - path.stat().st_mtime) / DAY <= RETAIN_DAYS:\n"
     "            continue",
     "        continue  # mutated: nothing ever expires",
     "12 snapshots older than the window are removed"),

    ("age is not consulted, so the floor becomes a ceiling",
     "morpho_homegraph/snapshot.py",
     "        if (now - path.stat().st_mtime) / DAY <= RETAIN_DAYS:\n"
     "            continue",
     "        pass  # mutated: age is not consulted",
     "14 the floor removes nothing that age would have kept"),

    ("the floor is dropped, so an idle project loses everything",
     "morpho_homegraph/snapshot.py",
     "    for path in found[floor:]:",
     "    for path in found:  # mutated: no floor",
     "13 the floor wins over age: an idle project keeps RETAIN_FLOOR"),

    ("snapshots are ordered oldest first, so the floor keeps the wrong ones",
     "morpho_homegraph/snapshot.py",
     "                  key=lambda p: p.stat().st_mtime, reverse=True)",
     "                  key=lambda p: p.stat().st_mtime)  # mutated",
     "11 snapshots younger than the window are kept"),

    # -- the warning -------------------------------------------------------
    ("the warning is computed from the newest snapshot, not the oldest",
     "morpho_homegraph/snapshot.py",
     "        left = RETAIN_DAYS - (now - found[-1].stat().st_mtime) / DAY",
     "        left = RETAIN_DAYS - (now - found[0].stat().st_mtime) / DAY"
     "  # mutated",
     "16 a deleted project is warned about before a snapshot falls out"),

    ("living projects are warned about as well",
     "morpho_homegraph/snapshot.py",
     "        if state_of(project_id) != GONE:\n"
     "            continue",
     "        pass  # mutated: everyone gets warned",
     "17 a living project losing old history is not warned about"),

    ("the window is ignored, so the warning fires from the first day",
     "morpho_homegraph/snapshot.py",
     "        if left <= within_days:\n"
     "            due.append((project_id, left))",
     "        due.append((project_id, left))  # mutated: always warn",
     "17c a deleted project with fresh snapshots is not warned about yet"),

    ("a project whose store is gone counts as living",
     "morpho_homegraph/snapshot.py",
     "    if not store_db.is_file():\n"
     "        return GONE",
     "    if not store_db.is_file():\n"
     "        return LIVING  # mutated",
     "17b a project with no living store left is warned about"),

    ("the recorded state is never read, so nothing is ever deleted",
     "morpho_homegraph/snapshot.py",
     '            return store.get_meta("state") or LIVING',
     "            return LIVING  # mutated",
     "16 a deleted project is warned about before a snapshot falls out"),

    # -- restoring ---------------------------------------------------------
    ("restoring proceeds over a folder that is not there",
     "morpho_homegraph/snapshot.py",
     "    if not os.path.isdir(project_path):\n"
     "        raise PathGone(project_path)",
     "    pass  # mutated: restore anyway",
     "18 restoring is refused while the path is gone, and names it"),

    ("the refusal does not name the missing path",
     "morpho_homegraph/snapshot.py",
     "        super().__init__(\n"
     '            "%s does not exist, so restoring the index would describe'
     ' files "',
     "        super().__init__(  # mutated\n"
     '            "%.0s the project directory is missing, so restoring the'
     ' index would describe files "',
     "18 restoring is refused while the path is gone, and names it"),

    ("a restored project stays marked deleted",
     "morpho_homegraph/snapshot.py",
     "    with Store(store_db, role=PROJECT) as store:\n"
     '        store.set_meta("state", LIVING)',
     "    pass  # mutated: the state is not reset",
     "19 with the path back, the project is restored and is living again"),

    ("a failed release is not named as the release step",
     "morpho_homegraph/snapshot.py",
     '        raise SnapshotFailed("release", exc) from exc',
     "        raise exc  # mutated: the step is not named",
     "10c a failed release names the release step"),

    ("an unreadable living store is taken for a deleted one",
     "morpho_homegraph/snapshot.py",
     "    except (sqlite3.Error, OSError):\n"
     "        # A store that is there but cannot be read right now is a reason"
     " to",
     "    except (sqlite3.Error, OSError):\n"
     "        return GONE  # mutated\n"
     "        # A store that is there but cannot be read right now is a reason"
     " to",
     "13b an unreadable living store keeps its floor, not loses it"),

    ("restoring installs the newest snapshot without reading it back",
     "morpho_homegraph/snapshot.py",
     "        if verify(candidate, project_id, project_path):\n"
     "            snapshot_path = candidate\n"
     "            break",
     "        snapshot_path = candidate  # mutated: the newest will do\n"
     "        break",
     "19b a torn newest snapshot is skipped for the newest that verifies"),

    ("restoring falls back to installing a snapshot that does not verify",
     "morpho_homegraph/snapshot.py",
     "    else:\n"
     "        raise SnapshotFailed(\n"
     '            "verify", "no snapshot of %s reads back as this project"\n'
     "            % project_id)",
     "    else:\n"
     "        snapshot_path = candidates[0]  # mutated: install it anyway\n"
     "        project_path = claimed_path(snapshot_path)",
     "19c with no snapshot that verifies, nothing is installed"),

    # -- R6: a rule only tests call is a rule that does not run ------------
    ("the retention rule is defined but never called from the package",
     "morpho_homegraph/cli.py",
     "        removed = snapshot.apply_retention(project_id)",
     "        removed = []  # mutated: the rule is never called",
     "15 the retention rule is called from the package, not only tests"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp7.py", prefix="mut7-", timeout=600))
