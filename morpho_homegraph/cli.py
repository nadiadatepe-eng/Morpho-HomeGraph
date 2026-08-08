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
from pathlib import Path

from . import embed, freshness, fusion, search, service, snapshot, view
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
        # The layers, counted from the tables rather than from `meta`: a
        # number written at build time and never checked against the rows is
        # how an index that lost its content goes on reporting it.
        #
        # **This is the line that would have shown the defect.** The project
        # layers were empty for five checkpoints, and nothing in this output
        # said so -- id, path, schema and a timestamp are all true of a store
        # holding nothing at all. An empty index must not be able to look
        # finished (CP-7B R8).
        rules, rows, edges = store.db.execute(
            "SELECT (SELECT COUNT(*) FROM scope),"
            "       (SELECT COUNT(*) FROM content),"
            "       (SELECT COUNT(*) FROM edges)").fetchone()
        print("%-14s %d rules" % ("scope", rules))
        print("%-14s %d rows (%s unread, %s s)"
              % ("l2", rows, store.get_meta("l2_unread") or "?",
                 store.get_meta("l2_seconds") or "?"))
        print("%-14s %d edges (%s ambiguous, %s outside)"
              % ("l3", edges, store.get_meta("l3_ambiguous") or "?",
                 store.get_meta("l3_outside") or "?"))
        if not rows:
            print("%-14s not built: morphofiles-graph update %s"
                  % ("", args.project))
    return 0


def _ages(store=None) -> str:
    """The line every answer ends with: how old is each layer it read (R1).

    Opens the catalogue itself rather than taking it as an argument, because
    every answering command needs it and none of them otherwise has it open.
    A missing catalogue is reported as `never`, not left out -- "I did not
    read that layer" and "that layer was never built" are different facts.
    """
    if l0_path().is_file():
        with Store(l0_path(), read_only=True, role=L0) as l0:
            return freshness.describe(freshness.ages(store, l0))
    return freshness.describe(freshness.ages(store))


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
    """Writer. Builds this project's own layers: scope, then L2, then L3.

    **The work is `service.build_layers`, and that is CP-13 R10.** The guard,
    the refusal text and the exit code are this command's; what an update *is*
    belongs to one function, because the service builds the same layers under
    a guard it already holds and `StoreLock.acquire` would refuse a second
    take in the same process (see `_guard`). Two build paths would have been
    two answers to what `update` does, and the second one is the one nobody
    reads.

    Every refusal exits 2 and names the command that fixes it: an index that
    is empty for a reason nobody can see is CP-7B's whole subject.
    """
    project_id = _resolve(args.project)
    store_db = db_path(project_id)
    # The guard before anything else, including before the store is opened:
    # CP-0 gate 8b requires that a *refused* writer leaves no store behind, and
    # opening one is already a write. It also puts every check inside the same
    # guard as the writes they decide on.
    barrier = _guard_or_refuse(store_db)
    if barrier is None:
        return 2
    try:
        with Store(store_db) as store:
            try:
                built = service.build_layers(store, project_id)
            except service.Refused as exc:
                print("REFUSED  %s" % exc, file=sys.stderr)
                return 2
    finally:
        barrier.release()

    if built["recreated"]:
        # CP-0's recovery path, and it must stay reachable: a project whose
        # `index.db` was deleted is recreated here -- `status` says so in as
        # many words. The path it was for lived in the file that is gone.
        print("%s  index recreated, but it has no recorded path: "
              "morphofiles-graph add <dir> to register it again" % project_id)
        return 0
    l2, l3, l4 = built["l2"], built["l3"], built["l4"]
    print("%s  %s\nL2  %d read, %d unread\nL3  %d edges (%d ambiguous, "
          "%d outside)\nL4  %d rows indexed"
          % (project_id, built["root"], l2["read"], l2["unread"], l3["edges"],
             l3["ambiguous"], l3["outside"], l4["rows"]))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Writer. Arm or disarm inotify for one project (CP-13 R3).

    A command rather than a flag on `add`, because a flag on `add` would be
    dead on this machine from the day it was written: the one real project
    (`3247e1fc8204aa01`) was registered on 2026-08-04, and nothing that only
    happens at registration can ever reach it.
    """
    project_id = _resolve(args.project)
    store_db = db_path(project_id)
    barrier = _guard_or_refuse(store_db)
    if barrier is None:
        return 2
    try:
        with Store(store_db) as store:
            service.set_watch(store, not args.off)
            root = store.get_meta("project_path") or "(no recorded path)"
    finally:
        barrier.release()
    print("%s  %s  %s" % (project_id, "unwatched" if args.off else "watched",
                          root))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """The service. Holds every guard it needs and blocks until Ctrl-C.

    Exit 2 when a guard is refused -- another process is already the writer,
    and this one does not queue behind it any more than `update` does.
    """
    try:
        return service.serve(scan_root=args.root,
                             sweep_seconds=args.sweep * 60,
                             debounce=args.debounce)
    except KeyboardInterrupt:
        # The guards are already released: `serve` releases in a `finally`, so
        # by the time this lands there is nothing left to undo (R9).
        print("stopped")
        return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Reader. Two questions, and they are not the same one.

    `--names` answers from L0 and therefore covers the whole home area;
    everything else answers from a project's L2 and covers only what the user
    pointed at. That is locked decision 7 -- metadata everywhere, content where
    you point -- and it is why a name search does not need a project to exist.

    Exit 1, not 0, when the index cannot answer: zero hits from an index that
    was never built looks exactly like zero hits from a corpus without the
    word, and only one of those is an answer.
    """
    # Two answer modes at once is a command that did not run, so it exits 2
    # rather than 1 -- the split this file's docstring sets out. Resolving it
    # by branch order would answer a question the user did not ask, and look
    # right while doing it.
    if args.semantic and args.fused:
        print("REFUSED  --semantic answers from the vectors alone and --fused "
              "merges both lists: pick one", file=sys.stderr)
        return 2
    if args.names and (args.semantic or args.fused):
        print("REFUSED  --names searches the catalogue's paths; --semantic and "
              "--fused search one project: pick one", file=sys.stderr)
        return 2
    if args.names:
        if not l0_path().is_file():
            print("REFUSED  the catalogue has not been built: "
                  "morphofiles-graph scan", file=sys.stderr)
            return 2
        with Store(l0_path(), read_only=True, role=L0) as l0:
            hits = search.names(l0, args.query)
            # The age is the whole of CP-12 R3, and it closes a measured
            # failure: on 2026-08-04 a search for `fasit-cp8` found the
            # predecessor's file and not ours, because the catalogue had last
            # been walked before ours was written -- and the answer said
            # nothing. A name search reads one layer; it says how old it is.
            age = freshness.describe(freshness.ages(l0_store=l0))
        for hit in hits:
            print("%-6s %s" % (hit["kind"], hit["path"]))
        if not hits:
            print("no matches for %r in the catalogue" % args.query)
        print(age)
        return 0

    if not args.project:
        raise SystemExit("which project? morphofiles-graph search --project "
                         "<id|path> <query>  (or --names for the catalogue)")
    store_db = db_path(_resolve(args.project))
    if not store_db.is_file():
        raise SystemExit("%s has no index yet: morphofiles-graph update %s"
                         % (args.project, args.project))
    if args.fused:
        return _fused(args, store_db)
    if args.semantic:
        return _semantic(args, store_db)
    with Store(store_db, read_only=True) as store:
        condition, indexed, expected = search.state(store)
        if condition != "ok":
            print("REFUSED  the search index is %s (%d rows indexed, %d in "
                  "L2) -- morphofiles-graph update %s. Answering would look "
                  "like 'no matches'" % (condition, indexed, expected,
                                         args.project), file=sys.stderr)
            return 1
        hits = search.content(store, args.query)
        age = _ages(store)
    for hit in hits:
        print("%-8s %s" % (hit["where"], hit["path"]))
    if not hits:
        print("no matches for %r in %d indexed files" % (args.query, indexed))
    print(age)
    return 0


def _semantic(args: argparse.Namespace, store_db: Path) -> int:
    """Reader. Meaning rather than words, and always with its own coverage.

    **The coverage line prints every time (CP-9 R9), and this is where CP-9
    deliberately differs from CP-8.** A half-embedded project is the *normal*
    state after R3, not an error -- so it answers. But nobody may read three
    hits as "everything there is" without being told how far the run got.

    Nothing embedded at all is the other case, and it is house rule 6 rather
    than R9: a layer that was never built is missing, and zero hits from it is
    not an answer. Exit 1, the same code CP-8 gives an index it cannot trust.
    """
    with Store(store_db, read_only=True) as store:
        try:
            embedded, expected = embed.coverage(store)
            if not embedded:
                print("REFUSED  nothing is embedded in this project yet (%d "
                      "chunks in L2) -- morphofiles-graph embed %s. Answering "
                      "would look like 'no matches'" % (expected, args.project),
                      file=sys.stderr)
                return 1
            hits = embed.search(store, args.query)
            age = _ages(store)
        except embed.Refused as exc:
            print("REFUSED  %s" % exc, file=sys.stderr)
            return 2
    for hit in hits:
        print("%.3f  %s" % (hit["score"], hit["path"]))
    if not hits:
        print("no matches for %r" % args.query)
    print("%d of %d chunks embedded" % (embedded, expected))
    print(age)
    return 0


def _fused(args: argparse.Namespace, store_db: Path) -> int:
    """Reader. Both lists, merged on rank, and each hit says which found it.

    **A fusion with one list is not a fusion (CP-10 R6).** If either layer is
    missing this refuses and names the command that fixes it, because
    answering with the surviving list would look exactly like a working merge
    -- the silent degradation house rule 6 exists for.

    **Opt-in, not the default.** CP-9E decided that with numbers rather than
    taste: paraphrase and lexical cleared their thresholds, cross-language
    landed in the band, and the rule written before the measurement says
    measure again before switching anything on.
    """
    with Store(store_db, read_only=True) as store:
        condition, indexed, expected = search.state(store)
        if condition != "ok":
            print("REFUSED  the lexical index is %s (%d rows, %d in L2) -- "
                  "morphofiles-graph update %s. Half a fusion is not one"
                  % (condition, indexed, expected, args.project),
                  file=sys.stderr)
            return 1
        try:
            embedded, chunks = embed.coverage(store)
            if not embedded:
                print("REFUSED  nothing is embedded in this project yet (%d "
                      "chunks in L2) -- morphofiles-graph embed %s. Half a "
                      "fusion is not one" % (chunks, args.project),
                      file=sys.stderr)
                return 1
            lexical = [hit["path"] for hit in
                       search.content(store, args.query, limit=fusion.DEPTH)]
            vector = [hit["path"] for hit in
                      embed.search(store, args.query, limit=fusion.DEPTH)]
            age = _ages(store)
        except embed.Refused as exc:
            print("REFUSED  %s" % exc, file=sys.stderr)
            return 2
    hits = fusion.fuse({"lexical": lexical, "vector": vector})
    for hit in hits[:fusion.CUT]:
        print("%-8s %s" % (fusion.route(hit["found_by"]), hit["path"]))
    if not hits:
        print("no matches for %r" % args.query)
    print("%d of %d chunks embedded" % (embedded, chunks))
    print(age)
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    """Writer, and deliberately not part of `update` (CP-9 R3).

    M-3 measured first-open embedding at 219.9 s and 317.1 s on two of the
    smallest trees in the home area, against a 60-second threshold written
    down before the measurement. So the design changed: `update` finishes
    without it, the project is usable, CP-8 answers lexically, and this runs
    when the user asks for it.

    Every refusal exits 2 and names what is missing. A missing model must
    never become a zero vector -- a semantic layer that answers badly without
    saying so is the failure this whole checkpoint is about.
    """
    project_id = _resolve(args.project)
    store_db = db_path(project_id)
    if not store_db.is_file():
        raise SystemExit("%s has no index yet: morphofiles-graph update %s"
                         % (args.project, args.project))
    barrier = _guard_or_refuse(store_db)
    if barrier is None:
        return 2
    try:
        with Store(store_db) as store:
            tally = embed.build(store)
    except embed.Refused as exc:
        print("REFUSED  %s" % exc, file=sys.stderr)
        return 2
    finally:
        barrier.release()
    print("%s\n%d chunks embedded, %d reused, %d removed (%d chunks in L2)"
          % (project_id, tally["embedded"], tally["reused"], tally["removed"],
             tally["chunks"]))
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    """Reader. Writes one self-contained folder: page, engine and data.

    **It builds nothing.** An empty L2 is refused with the command that fills
    it -- an empty picture and "your project is empty" look the same, and only
    one of them is true.

    The folder is self-contained because the alternative is a page that reaches
    back into the repository for its JavaScript: that works on the machine it
    was built on and nowhere else.
    """
    project_id = _resolve(args.project)
    store_db = db_path(project_id)
    if not store_db.is_file():
        raise SystemExit("%s has no index yet: morphofiles-graph update %s"
                         % (args.project, args.project))
    out = Path(args.out).expanduser() if args.out else data_home() / "view" / project_id
    with Store(store_db, read_only=True) as store:
        root = store.get_meta("project_path") or ""
        # The picture carries CP-12's freshness, from the same function the
        # text answers use. Two views of one fact, never two computations of it.
        if l0_path().is_file():
            with Store(l0_path(), read_only=True, role=L0) as l0:
                state = freshness.per_file(store, l0)
                ages = freshness.ages(store, l0)
        else:
            state, ages = freshness.per_file(store), freshness.ages(store)
        try:
            tally = view.write(store, root, out,
                               Path(__file__).resolve().parent.parent / "view",
                               state=state, ages=ages)
        except view.NothingToDraw as exc:
            print("REFUSED  %s -- morphofiles-graph update %s"
                  % (exc, args.project), file=sys.stderr)
            return 1
    print("%s\n%d nodes, %d edges (%d folders, %d files, %d type buckets)\n%s\n%s\n"
          "open it with:  python3 -m http.server --directory %s"
          % (out, tally["nodes"], tally["edges"], tally["dir"], tally["file"],
             tally["bucket"],
             "  ".join("%s %d" % (name, count)
                       for name, count in sorted(tally["states"].items())),
             freshness.describe(ages), out))
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

    p_search = sub.add_parser("search", help="lexical search over L2, or L0 names")
    p_search.add_argument("query")
    p_search.add_argument("--project", help="project id or path")
    p_search.add_argument("--names", action="store_true",
                          help="search the catalogue's paths instead (whole home area)")
    p_search.add_argument("--semantic", action="store_true",
                          help="search by meaning, over the embedded chunks")
    p_search.add_argument("--fused", action="store_true",
                          help="merge the lexical and semantic lists on rank")
    p_search.set_defaults(func=cmd_search)

    p_embed = sub.add_parser(
        "embed", help="embed a project's content (its own command: M-3)")
    p_embed.add_argument("project", help="project id or path")
    p_embed.set_defaults(func=cmd_embed)

    p_view = sub.add_parser("view", help="write the drawable graph and the page")
    p_view.add_argument("project", help="project id or path")
    p_view.add_argument("--out", help="where to write it (default: beside the store)")
    p_view.set_defaults(func=cmd_view)

    p_snap = sub.add_parser("snapshot",
                            help="snapshot a project and prune the window")
    p_snap.add_argument("project", help="project id or path")
    p_snap.set_defaults(func=cmd_snapshot)

    p_watch = sub.add_parser("watch", help="arm inotify for a project")
    p_watch.add_argument("project", help="project id or path")
    p_watch.add_argument("--off", action="store_true",
                         help="disarm it again")
    p_watch.set_defaults(func=cmd_watch)

    p_serve = sub.add_parser(
        "serve", help="the indexing service: sweep L0, react to watched projects")
    p_serve.add_argument("root", nargs="?", default="~",
                         help="what the sweep catalogues (default: the home area)")
    p_serve.add_argument("--sweep", type=float,
                         default=service.SWEEP_SECONDS / 60,
                         help="minutes between full L0 sweeps (default: %d)"
                              % (service.SWEEP_SECONDS / 60))
    p_serve.add_argument("--debounce", type=float,
                         default=service.DEBOUNCE_SECONDS,
                         help="seconds of quiet that end a burst (default: %.1f)"
                              % service.DEBOUNCE_SECONDS)
    p_serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
