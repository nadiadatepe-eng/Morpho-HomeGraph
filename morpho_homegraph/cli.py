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

from .lock import Locked, StoreLock
from .store import Store, data_home, db_path, initialise, new_project, projects


def _note(msg: str) -> None:
    print("note: %s" % msg, file=sys.stderr)


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
    """Take the process guard. Held for the lifetime of this process, not the write.

    Acquired here and released in a `finally`, deliberately not with `with`:
    the guard is per process, so the acquisition and the release are at the
    two ends of the command, not around one block. `with` on an already-held
    lock re-acquires it and the process is refused by its own lock file --
    which is exactly what happened the first time this was written.
    """
    return StoreLock(str(store_db), on_stale=_note).acquire()


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


def cmd_update(args: argparse.Namespace) -> int:
    """Writer. Nothing to scan until CP-1; the barrier is what CP-0 builds."""
    store_db = db_path(_resolve(args.project))
    try:
        barrier = _guard(store_db)
    except Locked as exc:
        # Three facts, because a caller can act on all three and on none of a
        # message that says only "busy": who holds it, that they own writing,
        # and that there is no queue and no hand-off to ask for.
        print("REFUSED  %s\n(waiting is not offered, and asking that process "
              "to do the job is not built: re-run when it is done)" % exc,
              file=sys.stderr)
        return 2
    try:
        with Store(store_db) as store:
            store.set_meta("last_update",
                           datetime.now().isoformat(timespec="seconds"))
    finally:
        barrier.release()
    print("updated %s" % store_db.parent.name)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
