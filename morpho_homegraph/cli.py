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

from . import (backfill, dirfresh, embed, freshness, fusion, search, service,
               snapshot, view)
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
    # directory test. `Path("/a") / "/home/someone"` is `/home/someone` -- joining an
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
        # L2 against the scope, not on its own. Open thread 5: `per_file`
        # counts what L2 holds, which is right for what it is -- a state per
        # file we have read -- but nobody was comparing that against what the
        # scope says should be there. Measured 2026-08-15: 13 files had no row
        # at all before an `update`, 0 of 127 after, so the denominator does
        # not shrink for ever, it shrinks *between updates* and says nothing
        # while it does. The fix is a comparison, not a fifth state (R5).
        # No `or ""` here: `scope_size` already answers `None` for a falsy or
        # missing root, and a second guard for the same case is a second thing
        # to keep in step -- the sweep showed neither could be observed failing.
        in_scope = service.scope_size(store.get_meta("project_path"))
        print("%-14s %d rows (%s unread, %s s)%s"
              % ("l2", rows, store.get_meta("l2_unread") or "?",
                 # Both halves are the same display fallback for a missing
                 # meta value, and neither can be wrong in a way a gate sees.
                 # condition-coverage: display fallback, no observable branch.
                 store.get_meta("l2_seconds") or "?",
                 "" if in_scope is None or in_scope == rows
                 else ", %d in scope: morphofiles-graph update %s"
                 % (in_scope, args.project)))
        print("%-14s %d edges (%s ambiguous, %s outside)"
              % ("l3", edges, store.get_meta("l3_ambiguous") or "?",
                 store.get_meta("l3_outside") or "?"))
        # L4, both halves. Open thread 7, and the same rule as the two lines
        # above: a project that was never embedded read exactly like one that
        # was, because only `search --semantic` said so -- and nobody runs a
        # search to find out whether searching will work.
        #
        # `search.state` rather than a row count, because it already separates
        # the three answers this line needs -- built, never built, and built
        # against an older L2. Counting rows myself would report a stale index
        # as a healthy one, which is the failure this whole line exists to stop.
        lexical, indexed, expected_rows = search.state(store)
        print("%-14s %s (%d/%d rows)%s"
              % ("l4 lexical", lexical, indexed, expected_rows,
                 "" if lexical == "ok"
                 else ": morphofiles-graph update %s" % args.project))
        embedded, expected = embed.coverage(store)
        if not expected:
            print("%-14s nothing to embed yet" % "l4 semantic")
        else:
            print("%-14s %d/%d chunks (%.0f %%)%s"
                  % ("l4 semantic", embedded, expected,
                     100.0 * embedded / expected,
                     "" if embedded == expected
                     else ": morphofiles-graph embed %s" % args.project))
        if not rows:
            print("%-14s not built: morphofiles-graph update %s"
                  % ("", args.project))
    # L1 coverage for this project's scope, read from the shared catalogue.
    # Outside `with` above because the numbers live in L0, not in the project
    # store. **This is CP-7B R8 again, one layer down:** a project where 51 of
    # 4 773 files carry a hash printed nothing at all here, and so read exactly
    # like one where every file does. `compared` and `backfilled` are shown
    # apart because only the first can support `touched` today.
    if l0_path().is_file():
        with Store(l0_path(), read_only=True, role=L0) as l0:
            cover = backfill.coverage(l0, service.union_keep())
        if not cover["migrated"]:
            # "Cannot tell" printed as "0 hashed" would be a worse lie than
            # printing nothing at all. Name the reason and the one command
            # that fixes it.
            print("%-14s unknown: catalogue predates CP-17, "
                  "run morphofiles-graph scan" % "l1")
        else:
            print("%-14s %d/%d hashed (%.0f %%), %d compared, %d backfilled"
                  % ("l1", cover["hashed"], cover["in_scope"],
                     cover["percent"], cover["compared"],
                     cover["backfilled"]))
            if cover["hashed"] < cover["in_scope"]:
                print("%-14s %d cold row(s): morphofiles-graph backfill"
                      % ("", cover["in_scope"] - cover["hashed"]))
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    """CP-23: which directories are behind, counting direct children.

    A reader of `status` learns that 11 of 99 files are stale; a reader of
    this learns *where*. The states and the clocks are CP-12's, from CP-12's
    functions -- this command groups, it does not decide (FASIT-cp23 R2/R3).

    The catalogue is opened read-only alongside the project store for two
    separate reasons, and they fail separately: without it no file can be
    called `stale`, because the comparison that decides it is the one we did
    not make, and without it there is no journal to count pending changes
    from. Both degrade to a named absence rather than to a wrong number.
    """
    store_db = db_path(_resolve(args.project))
    if not store_db.is_file():
        raise SystemExit("%s has no index yet: morphofiles-graph update %s"
                         % (args.project, args.project))
    with Store(store_db, read_only=True) as store:
        root = store.get_meta("project_path")
        l0 = None
        if l0_path().is_file():
            l0 = Store(l0_path(), read_only=True, role=L0)
        try:
            state = freshness.per_file(store, l0)
            ages = freshness.ages(store, l0)
            # The scope, so a change outside this project is not counted as
            # pending *for this project* (FASIT-cp23 blind spot 3), and the
            # paths L2 already holds, so "pending" means the catalogue knows
            # about a file we have not read rather than "the journal moved".
            pending = dirfresh.pending_by_dir(
                l0, service.chosen_scope(root).contains if root else None,
                known=state)
        finally:
            if l0 is not None:
                l0.close()
    rows = dirfresh.per_dir(state, pending)
    shown = 0
    for directory, row in dirfresh.ranked(rows):
        if args.all or row["not_fresh"] or row["pending"]:
            print(dirfresh.describe(directory, row, root))
            shown += 1
    if not shown:
        # Said in words, not by an empty answer. "Nothing printed" is the
        # signal CP-12 gate 5 exists to stop the reader having to interpret.
        print("every directory is fresh: %d files in %d directories"
              % (len(state), len(rows)))
    # R3: the same three clocks every other answer carries, and they are
    # printed whether or not anything is behind.
    print(freshness.describe(ages))
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


# CP-13B R3/R4: one row per refusal that actually happened. M-4 used to ask
# "how often does the barrier refuse under real use" by staging a collision
# from cron every hour; with CP-13 there is one permanent writer, so that cron
# would have refused itself every time and filled the series with ones. What
# is worth counting now is how often *a person* wants to write while the
# service holds the guard -- and that is exactly here, where the refusal is.
REFUSALS = "refusals.tsv"


def _record_refusal(command: str, store_db: Path, holder: dict) -> None:
    """Append one row about a refusal that just happened. Never raises.

    **R5, and it is the rule that matters most here:** a measurement may not
    block the thing it measures. An unwritable tally, a full disk, a directory
    where the file should be -- the refusal still happens, still exits 2, and
    still names the holder. Anything else turns an instrument into a second
    failure mode, on the path that is already the unhappy one.

    **R6, the denominator:** the holder's pid is in the row. Without it a row
    cannot be told from a row about ourselves, and a zero with no denominator
    is not a measurement -- the lesson M-4 was designed around.
    """
    try:
        path = data_home() / REFUSALS
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%s\n"
                     % (service.stamp(), command, store_db,
                        holder.get("pid", "?")))
    except OSError:
        pass


def _guard_or_refuse(store_db: Path, command: str) -> StoreLock | None:
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

    `command` is passed rather than read off `sys.argv`, so the row says which
    command was refused even when the CLI is driven as a library.
    """
    try:
        return _guard(store_db)
    except Locked as exc:
        print("REFUSED  %s\n(waiting is not offered, and asking that process "
              "to do the job is not built: re-run when it is done)" % exc,
              file=sys.stderr)
        _record_refusal(command, store_db, exc.holder)
        return None


def cmd_backfill(args: argparse.Namespace) -> int:
    """Writer against the shared L0 store. CP-17.

    Its own command rather than a step inside `scan` (R1): the cheap pass has
    to stay cheap, and a round that silently hashes a quarter of a gigabyte the
    first time it meets a new scope is what M-3 already ruled out for
    embedding.
    """
    store_db = l0_path()
    if not store_db.is_file():
        raise SystemExit("no catalogue yet: morphofiles-graph scan")
    keep = service.union_keep()

    # `--dry-run` reads, so it takes no guard: refusing to say how much work
    # there is because someone else is writing would be a refusal with no
    # reason behind it.
    if args.dry_run:
        with Store(store_db, read_only=True, role=L0) as store:
            report = backfill.backfill(store, keep, dry_run=True)
        print("%d file(s), %d byte(s) would be hashed"
              % (report["files"], report["bytes"]))
        return 0

    barrier = _guard_or_refuse(store_db, "backfill")
    if barrier is None:
        return 2
    try:
        with Store(store_db, role=L0) as store:
            report = backfill.backfill(store, keep, max_files=args.max_files)
    finally:
        barrier.release()
    if report["refused"]:
        # Exit 3, not 1: the work did not fail, it was declined on a limit the
        # caller set. A script can tell "too big, raise the limit" from "the
        # hashing broke" without parsing the message.
        print(report["refused"], file=sys.stderr)
        return 3
    print("%d file(s) hashed of %d cold, %d byte(s)%s"
          % (report["hashed"], report["files"], report["bytes"],
             "" if not report["unreadable"]
             else ", %d unreadable and left cold" % report["unreadable"]))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Writer, against the shared L0 store rather than a project's.

    Its own guard, on its own store path: a project update and an L0 refresh
    are different writers and must not block each other. That falls out of
    the guard being per store path, which is why decision 12 needed no
    revisiting when L0 moved out.
    """
    store_db = l0_path()
    store_db.parent.mkdir(parents=True, exist_ok=True)
    barrier = _guard_or_refuse(store_db, "scan")
    if barrier is None:
        return 2
    try:
        with Store(store_db, role=L0) as store:
            # CP-15 R1: the union of every registered project's scope, worked
            # out from the trees themselves. Before this, `scan` passed
            # nothing and `content_hash` was NULL for all 430 189 rows.
            summary = scan(store, args.root, service.union_keep())
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
    barrier = _guard_or_refuse(store_db, "update")
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
    barrier = _guard_or_refuse(store_db, "watch")
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
    barrier = _guard_or_refuse(store_db, "embed")
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
    barrier = _guard_or_refuse(Path(snapshot.prune_guard(project_id)),
                               "snapshot")
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

    # CP-23. `--all` because the default answers the question a reader has --
    # what is behind -- and a full listing is the rarer one.
    p_stale = sub.add_parser(
        "stale", help="which directories are behind, by direct children")
    p_stale.add_argument("project", help="project id or path")
    p_stale.add_argument("--all", action="store_true",
                         help="list fresh directories too")
    p_stale.set_defaults(func=cmd_stale)

    p_update = sub.add_parser("update", help="write to a project's index")
    p_update.add_argument("project", help="project id or path")
    p_update.set_defaults(func=cmd_update)

    p_scan = sub.add_parser("scan", help="refresh L0, the shared catalogue")
    p_scan.add_argument("root", nargs="?", default="~",
                        help="what to catalogue (default: the home area)")
    p_scan.set_defaults(func=cmd_scan)

    p_backfill = sub.add_parser(
        "backfill", help="hash in-scope rows a scan never had to read (CP-17)")
    p_backfill.add_argument(
        "--dry-run", action="store_true",
        help="say how many files and bytes, and hash nothing")
    p_backfill.add_argument(
        "--max-files", type=int, default=None,
        help="refuse rather than hash more than this many files")
    p_backfill.set_defaults(func=cmd_backfill)

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
