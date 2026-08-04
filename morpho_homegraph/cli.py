#!/usr/bin/env python3
"""`morphofiles-graph` -- the command. The project is Morpho-HomeGraph.

Three commands at CP-0, and the split between them is the write barrier:
`add` and `update` are writers and take the process guard; `status` is a
reader and never touches it. A reader that took the guard would be blocked by
every running service, which is precisely the failure WAL is here to avoid.

Exit codes: **0** did the work, **1** ran and found a problem, **2** did not
run. A refusal is 2. A shell loop can tell those apart; it cannot tell them
apart from a message.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import snapshot
from .lock import Locked, StoreLock
from .scan import scan
from .store import (L0, Store, data_home, db_path, initialise, l0_path,
                    new_project, projects)


def _resolve(value: str) -> str:
    """A project id from an id or a path. Refuses ambiguity rather than picking.

    Two projects can share a path -- `add` twice is a legal thing to do, and
    CP-6 is what decides whether the second one was a copy. Until then, a path
    that names two indexes is a question, not an answer.
    """
    # A directory without an index.db counts as a known id: that is a store
    # whose database was deleted or has not been written yet, and refusing to
    # name it would make the recovery path unreachable.
    #
    # `parent == data_home()` is what makes this an id test rather than a
    # directory test. `Path("/a") / "/home/nadi"` is `/home/nadi` -- joining an
    # absolute path throws the left side away -- so without this, any existing
    # directory passed as an argument would be accepted as an id, the registry
    # would never be consulted, and `update` would put an index.db inside the
    # user's own folder.
    candidate = db_path(value).parent
    if candidate.parent == data_home() and candidate.is_dir():
        return value
    wanted = str(Path(value).expanduser().resolve())
    hits = [pid for pid, path in projects() if path == wanted]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit("no project for %s (add it first)" % value)
    raise SystemExit("%s is indexed %d times: %s -- name the id"
                     % (value, len(hits), ", ".join(hits)))


def _guard(store_db: Path) -> StoreLock:
    """Take the session guard. Held for the lifetime of this process, not the write.

    Acquired here and released in a `finally`, deliberately not with `with`:
    the guard is per session, so the acquisition and the release are at the
    two ends of the command, not around one block. Taking it twice in one
    process is refused by the kernel like any other second holder, which is
    correct and is also what a `with` around each write would produce.
    """
    return StoreLock(str(store_db)).acquire()


# -- commands --------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser()
    if not target.is_dir():
        raise SystemExit("not a directory: %s" % target)
    project_id, store_db = new_project()
    barrier = _guard(store_db)
    try:
        with Store(store_db) as store:
            initialise(store, project_id, target)
    finally:
        barrier.release()
    print("%s  %s" % (project_id, target.resolve()))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Reader. Takes no lock, creates no store, answers while a writer writes."""
    if not args.project:
        rows = projects()
        for pid, path in rows:
            print("%s  %s" % (pid, path))
        if not rows:
            print("no projects yet: morphofiles-graph add <dir>")
        # L0 is shared, so it is not one of the projects and would otherwise
        # never be visible: a catalogue nobody can see the age of is a
        # catalogue nobody notices has gone stale.
        if l0_path().is_file():
            with Store(l0_path(), read_only=True, role=L0) as store:
                print("l0                %s entries, %s s, root %s"
                      % (store.get_meta("l0_count"),
                         store.get_meta("l0_seconds"),
                         store.get_meta("l0_root")))
        else:
            print("l0                not built: morphofiles-graph scan")
        return 0
    store_db = db_path(_resolve(args.project))
    if not store_db.is_file():
        # The state gate 8b plants: a project whose database is gone. A reader
        # says so; it does not create one, and it does not show a traceback
        # for a condition the user can fix with one command.
        raise SystemExit("%s has no index yet: morphofiles-graph update %s"
                         % (args.project, args.project))
    with Store(store_db, read_only=True) as store:
        for label, value in (
                ("id", store.get_meta("project_id")),
                ("path", store.get_meta("project_path")),
                ("schema", store.get_meta("schema_version")),
                ("journal_mode", store.journal_mode),
                ("busy_timeout", "%s ms" % store.busy_timeout),
                ("last update", store.get_meta("last_update") or "never")):
            print("%-14s %s" % (label, value))
    return 0


def _guard_or_refuse(store_db: Path) -> StoreLock | None:
    """The guard, or None with the refusal already printed to stderr.

    One function for both writers rather than the same six lines in each.
    That is not only tidiness: with two copies, a mutation aimed at the
    refusal hits whichever one the file happens to define first, and the
    other command's gate stays green while the mutation is recorded as
    surviving. Measured 2026-08-03 -- two CP-0 mutations went from killed to
    survived the moment `scan` was added with its own copy.

    Three facts, because a caller can act on all three and on none of a
    message that says only "busy": who holds it, that they own writing, and
    that there is no queue and no hand-off to ask for.
    """
    try:
        return _guard(store_db)
    except Locked as exc:
        print("REFUSED  %s\n(waiting is not offered, and asking that process "
              "to do the job is not built: re-run when it is done)" % exc,
              file=sys.stderr)
        return None


def cmd_scan(args: argparse.Namespace) -> int:
    """Writer, against the shared L0 store rather than a project's.

    Its own guard, on its own store path: a project update and an L0 refresh
    are different writers and must not block each other. That falls out of
    the guard being per store path, which is why decision 12 needed no
    revisiting when L0 moved out.
    """
    store_db = l0_path()
    store_db.parent.mkdir(parents=True, exist_ok=True)
    barrier = _guard_or_refuse(store_db)
    if barrier is None:
        return 2
    try:
        with Store(store_db, role=L0) as store:
            summary = scan(store, args.root)
    finally:
        barrier.release()
    print("%d entries in %.2f s (%d unreadable directories)"
          % (summary["count"], summary["seconds"], summary["unreadable"]))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Writer. Nothing to scan until CP-1; the barrier is what CP-0 builds."""
    store_db = db_path(_resolve(args.project))
    barrier = _guard_or_refuse(store_db)
    if barrier is None:
        return 2
    try:
        with Store(store_db) as store:
            store.set_meta("last_update",
                           datetime.now().isoformat(timespec="seconds"))
    finally:
        barrier.release()
    print("updated %s" % store_db.parent.name)
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Writer. Takes one snapshot, then applies the retention window.

    **This is the retention rule's production caller**, and CP-7's answer key
    makes it a gate: homegraph's `apply_retention()` was documented in three
    places as the reason a table had a ceiling, and was called from tests only.
    Every real installation grew without a bound, with every gate green.

    The two guards are different on purpose. Taking the snapshot needs none --
    it reads, and WAL admits readers. Pruning writes, and its guard is on the
    snapshot directory rather than the project's store, because the project's
    store is exactly what may already have been released.
    """
    project_id = _resolve(args.project)
    if not db_path(project_id).is_file():
        # Same refusal `status` gives: a condition the user fixes with one
        # command should not arrive as a traceback.
        raise SystemExit("%s has no index to snapshot: morphofiles-graph "
                         "update %s" % (args.project, args.project))
    path = snapshot.take(project_id)
    barrier = _guard_or_refuse(Path(snapshot.prune_guard(project_id)))
    if barrier is None:
        return 2
    try:
        removed = snapshot.apply_retention(project_id)
    finally:
        barrier.release()
    print("%s\n%d expired snapshot(s) removed" % (path, len(removed)))
    for pid, days in snapshot.expiring():
        print("WARNING  %s lives only in snapshots and falls out of history "
              "in %.1f days" % (pid, days))
    return 0


# -- entry point -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morphofiles-graph",
        description="Morpho-HomeGraph: metadata everywhere, content where you point.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="register a directory as a project")
    p_add.add_argument("path")
    p_add.set_defaults(func=cmd_add)

    p_status = sub.add_parser("status", help="what a project's index holds")
    p_status.add_argument("project", nargs="?", help="project id or path")
    p_status.set_defaults(func=cmd_status)

    p_update = sub.add_parser("update", help="write to a project's index")
    p_update.add_argument("project", help="project id or path")
    p_update.set_defaults(func=cmd_update)

    p_scan = sub.add_parser("scan", help="refresh L0, the shared catalogue")
    p_scan.add_argument("root", nargs="?", default="~",
                        help="what to catalogue (default: the home area)")
    p_scan.set_defaults(func=cmd_scan)

    p_snap = sub.add_parser("snapshot",
                            help="snapshot a project and prune the window")
    p_snap.add_argument("project", help="project id or path")
    p_snap.set_defaults(func=cmd_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
