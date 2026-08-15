#!/usr/bin/env python3
"""The indexing service: one writer, two clocks.

Every layer in this repo is built by a command a person has to remember to
run. `~/Morpho-HomeGraph` stood with empty layers for five checkpoints because
nobody called the builders (CP-7B), and L0 is only fresh today because a
crontab runs `scan` at :17. **An index that is fresh because somebody
remembered is fresh by accident.** CP-12 made the age visible; this is what
acts on it.

**One writer, and it is this process (locked decision 12).** The guards are
taken at startup and held until the service ends, so while `serve` runs a
person at the keyboard is *refused* `scan`, and `update` against a watched
project. That is not a side effect to apologise for -- a service that dropped
its guard between rounds would be two writers that mostly do not collide, and
"mostly" is not a barrier. The refusal names `serve`, like every other refusal
here names the way back.

**Two clocks, and they do not trigger each other (R5).** The broad one is the
periodic L0 sweep: 8.7-11.8 s of wall time, measured 2026-08-04 when the M-4
cron built the store for the first time. At 30 minutes that is 0.4-0.7 % of
the time. The narrow one is inotify on flagged projects and costs nothing
until something happens. A broad sweep that also rebuilt every project would
make the split between broad and narrow a name and not a design.

**`build_layers` lives here rather than in `cli.py`, and that is R10.**
`cmd_update` takes the guard itself, and `StoreLock.acquire` opens a fresh
descriptor every time (`lock.py`) -- flock from two descriptors in one process
conflicts, so a service holding the project guard and then calling `cmd_update`
would **refuse itself**. `cli._guard` already says as much: "taking it twice in
one process is refused by the kernel like any other second holder." The way out
is one function that *assumes* the guard, called by the command and by the
clock. Writing a second build path inside the service would have solved the
refusal and left two answers to what an update is.

The loop is single-threaded on purpose. `Store` was built for the other shape
(`check_same_thread=False` and a write lock, added in CP-0 for "the service
scans on one thread and answers on another"), and that shape is still
available -- but one loop whose read blocks until the next sweep is due needs
neither a thread nor a join nor a second place for Ctrl-C to land.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import content, graph, identity, journal, scope, search
from .lock import Locked, StoreLock, Unguarded
from .scan import knows, scan
from .store import L0, Store, db_path, l0_path, projects
from .watch import Inotify, InotifyUnavailable, relevant, store_prune

# The broad clock. 11.8 s worst case against 1800 s is 0.7 % of the time; the
# number that matters is the ratio, not the period, so moving one means
# re-reading the other.
SWEEP_SECONDS = 30 * 60

# A burst of writes -- one editor save touching five files, one `git checkout`
# touching five hundred -- becomes one update once this many seconds pass with
# nothing further worth keeping. The predecessor's policy; the lines are new.
DEBOUNCE_SECONDS = 2.0

# Measured 2026-08-04: L0 was 300 MB on disk, 242 MB used, 58 MB free pages --
# **33 %** after two full rewrites, because `scan` replaces the whole table
# every round. `VACUUM` on a copy took 288 MB to 194 MB in 2.6 s. Running it
# every round would be 2.6 s nobody asked for; never running it is the
# predecessor's unbounded table. So: a threshold, below the number we measured
# so it fires before the file doubles.
VACUUM_AT = 0.25

# The meta key that arms inotify for a project. In the store rather than in an
# argument to `serve`, so the choice survives the terminal that made it.
WATCH = "watch"


class Refused(RuntimeError):
    """Why this project cannot be updated, in words that name the way back."""


def stamp() -> str:
    """Now, with the offset written down.

    A naive ISO stamp is read as local time, which is what wrote it -- until
    the store is read in another zone, or on the other side of a daylight
    saving change. Measured 2026-08-05: the same naive string is 7200 s apart
    between UTC and Europe/Oslo, and Oslo's own offset moves an hour between
    January and August. Old stamps keep the only reading they ever had; new
    ones carry their zone.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def chosen_scope(root: str):
    """The scope this folder gets: `.gitignore` for a repo, JUNK otherwise.

    Recomputed on every update and never loaded from the store. A saved scope
    that is reused is CP-3's bug in new clothing -- patterns worked out and
    never applied -- because `.gitignore` gets edited and a folder becomes a
    repo (CP-7B R6).
    """
    if scope.is_repo(root):
        chosen, _patterns = scope.from_repo(root)
        return chosen
    return scope.from_folder(root)


def union_keep():
    """What any registered project would index, as one predicate (CP-15 R1/R2).

    **Recomputed from disk, never loaded from the saved `scope` tables.** A
    stored scope that gets reused is CP-3's bug in new clothing: `.gitignore`
    is edited, and a folder becomes a repo. `chosen_scope` reads the tree, and
    that costs one `.gitignore` per registered project per sweep -- against a
    16-second sweep it does not register.

    A predicate rather than a list of roots, because a list cannot say
    `.gitignore` and `node_modules` is exactly where the churn is. Hashing it
    every pass is how the cheap layer becomes the expensive one.

    An empty registry gives a predicate that is false everywhere, which is the
    honest answer: nothing is indexed, so nothing is worth hashing.
    """
    selected = [chosen_scope(path) for _pid, path in projects()]

    def keep(path: str) -> bool:
        return any(s.contains(path, is_dir=False) for s in selected)

    return keep


def scope_size(root: str) -> int | None:
    """How many files the project's scope selects on disk right now.

    `None` when the root is gone or unreadable, which `status` prints as no
    comparison at all -- "the project moved" and "L2 is behind" are different
    facts, and showing the second for the first would send the reader to run
    `update` against a directory that is not there.

    Walked rather than read from the `scope` table, for CP-15 R1's reason: a
    stored scope reused is CP-3's bug in new clothes, because `.gitignore` gets
    edited and a folder becomes a repo. This is the same recomputation
    `union_keep` does, and it costs one walk of one project.
    """
    if not root or not os.path.isdir(root):
        return None
    selected = chosen_scope(root)
    total = 0
    for current, dirs, files in os.walk(root):
        # Do not descend into a directory the scope excludes: for a repo that
        # is `.git`, and for a nested `.gitignore` with `*` it is the whole
        # cache. Walking them anyway would cost more than the count is worth,
        # and `contains` would reject every file individually.
        dirs[:] = [d for d in dirs
                   if selected.contains(os.path.join(current, d), is_dir=True)]
        total += sum(1 for name in files
                     if selected.contains(os.path.join(current, name),
                                          is_dir=False))
    return total


# -- the one definition of an update ---------------------------------------

def build_layers(store: Store, project_id: str) -> dict:
    """Build scope, then L2, then L3, then L4 for one project.

    **The caller holds the guard**, and that is the whole reason this is a
    function rather than the body of `cmd_update` (R10). Refusals are raised
    rather than printed: a command turns one into exit 2, and the service turns
    one into a line and keeps running. A function that printed would have
    decided that for both.

    **The order is not taste.** `graph.build` reads `content`, so a graph built
    first finds nothing to link, writes zero edges and *succeeds* -- and "no
    links in this project" and "the graph ran too early" are then the same
    output (CP-7B R1).

    **L0 is read, never built here.** It is a different store with its own
    guard and its own command. Taking that guard as well would let a project
    update block every catalogue refresh, and the two writers locked decision
    12 rests on would have become one.
    """
    # CP-0's recovery path, and it must stay reachable: a project whose
    # `index.db` was deleted is recreated by this command. The path it was for
    # lived in the file that is gone, so the layers cannot be rebuilt from
    # here, and saying that is the whole of what this branch does.
    root = store.get_meta("project_path")
    if not root:
        store.set_meta("last_update", stamp())
        return {"recreated": True, "root": None}
    if (store.get_meta("state") or identity.LIVING) != identity.LIVING:
        raise Refused("%s is marked deleted and is waiting to be retired -- "
                      "restore it first (see snapshot.restore)" % project_id)
    if not l0_path().is_file():
        raise Refused("the catalogue has not been built: "
                      "morphofiles-graph scan")

    with Store(l0_path(), read_only=True, role=L0) as l0:
        # R4: the old location is refused with where it went, not quietly
        # rebuilt somewhere else. Applying the move is deliberately a different
        # act -- CP-6 refuses to choose between two identical trees, and this
        # would be choosing for the user.
        try:
            root = identity.open_project(project_id, l0)
        except identity.Moved as exc:
            raise Refused(str(exc)) from exc
        if not knows(l0, root):
            raise Refused("the catalogue has never seen %s -- it is older "
                          "than this project: morphofiles-graph scan" % root)
        chosen = chosen_scope(root)
        scope.save(store, chosen)
        l2 = content.build(store, l0, chosen)
        l3 = graph.build(store, scope_root=root)
        l4 = search.build(store)
    store.set_meta("last_update", stamp())
    return {"recreated": False, "root": root, "l2": l2, "l3": l3, "l4": l4}


# -- the watch flag ---------------------------------------------------------

def is_watched(store: Store) -> bool:
    return (store.get_meta(WATCH) or "") == "1"


def set_watch(store: Store, on: bool) -> None:
    """Arm or disarm inotify for this project. Caller holds the guard."""
    store.set_meta(WATCH, "1" if on else "0")


def watched_projects() -> list[tuple[str, str]]:
    """Every registered (id, path) whose `watch` flag is set.

    Read from each store's own meta, like `projects()` -- there is no
    path-keyed table anywhere and this must not become the first one.
    """
    found = []
    for project_id, path in projects():
        try:
            with Store(db_path(project_id), read_only=True) as store:
                if is_watched(store):
                    found.append((project_id, path))
        except (sqlite3.Error, OSError):
            # A store can be removed between `projects()` listing it and this
            # open. A listing that dies on that races with anyone tidying up.
            continue
    return found


# -- what an event is worth -------------------------------------------------

def is_layout(path: str) -> bool:
    """Does this path decide *which files belong*, rather than hold content?

    homegraph hit this once: **"a layout change is not a file change".** An
    edited `.gitignore` and a folder becoming a repo change the membership of
    every file around them while changing the content of none. The trap is that
    `.gitignore` is frequently a file L2 would drop anyway, so a filter that
    asks "would this have landed in L2" throws away the one event that changes
    what L2 is.

    `.git` counts when it is *itself* created or removed -- the marker
    appearing is a folder becoming a repo. What happens inside `.git` is a
    git command's own churn and is pruned from the watch entirely.

    Nested `.gitignore` files are treated as layout here even though `scope.py`
    does not read them (open thread 6). That costs a needless rebuild on a rare
    edit; the other direction would miss a real one once `scope.py` learns to
    read them.
    """
    base = os.path.basename(path.rstrip(os.sep))
    return base in (".gitignore", ".git")


def keeper(project_scope, stores):
    """The predicate that decides whether an event begins a burst.

    Three questions in order, and the order is the point: a store write is
    never kept (R2, the self-trigger guard), a layout change is always kept
    (R4), and everything else is kept only if the scope would have indexed it.
    """
    ignore = [os.path.abspath(str(s)) for s in stores]
    # The directories that *hold* the stores, not just the store files.
    #
    # **CP-15 needed this and CP-13's gate 6 found it.** For inotify the two
    # guards are `relevant` (belt) and `store_prune` (braces) -- the kernel is
    # simply never asked about a pruned directory. The journal has no kernel
    # to prune: `scan` walks everything, so `.../store/l0` and
    # `.../store/<id>` arrive as ordinary changed directories, and `relevant`
    # lets them through because they are neither the db path nor prefixed by
    # it. One update then wrote the store, the next sweep saw the directory
    # move, and the service updated for ever.
    holders = [os.path.dirname(p) for p in ignore]

    def keep(path: str) -> bool:
        if not relevant(path, ignore):
            return False
        if any(path == d or path.startswith(d + os.sep) for d in holders):
            return False
        if is_layout(path):
            return True
        return project_scope.contains(path)

    return keep


# -- fragmentation ----------------------------------------------------------

def fragmentation(db) -> float:
    """The fraction of L0's pages that are free. 0.0 when the store is empty."""
    free = db.execute("PRAGMA freelist_count").fetchone()[0]
    total = db.execute("PRAGMA page_count").fetchone()[0]
    return free / total if total else 0.0


def maybe_vacuum(store: Store, threshold: float = VACUUM_AT) -> float | None:
    """`VACUUM` when free pages pass `threshold`; the fraction it acted on.

    `None` when it did not run, which is the common case and has to stay so --
    see `VACUUM_AT`. The commit is not decoration: `VACUUM` cannot run inside a
    transaction, and the sweep that just replaced the whole table left one open.
    """
    free = fragmentation(store.db)
    if free < threshold:
        return None
    with store.writing() as db:
        db.commit()
        db.execute("VACUUM")
    return free


# -- the loop ---------------------------------------------------------------

def _take(paths, out) -> list[StoreLock] | None:
    """Every guard, or none of them and the refusal already reported (R1).

    Nothing is opened until all of them are held, so a refused start leaves no
    store behind -- CP-0 gate 8b, one floor up.
    """
    held: list[StoreLock] = []
    for db in paths:
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        try:
            held.append(StoreLock(str(db)).acquire())
        except Locked as exc:
            for lock in held:
                lock.release()
            out("REFUSED  %s\n(the service is the only writer while it runs: "
                "stop it, or wait for it)" % exc)
            return None
    return held


def _sweep(root: str, out) -> tuple[dict, list[str]]:
    """The broad clock: rebuild L0, look at the file, and read the journal.

    Project layers are still not rebuilt here wholesale (CP-13 gate 13). What
    changed in CP-15 is that the sweep now *reads* what it learned: the paths
    the journal says moved come back, and the caller decides which projects
    they belong to.
    """
    with Store(l0_path(), role=L0) as store:
        summary = scan(store, root, union_keep())
        moved = [p for (p,) in store.db.execute(
            "SELECT path FROM journal WHERE state <> ?", (journal.UNCHANGED,))]
        # Logged every sweep, not only when it fires: measured 2026-08-09 the
        # ratio sat at 19,41 % against a 25 % threshold, and two readings a day
        # apart cannot tell equilibrium from a slow creep. The decision to keep
        # `VACUUM_AT` where it is rests on a curve nobody was recording.
        free = fragmentation(store.db)
        freed = maybe_vacuum(store)
    out("sweep    %d entries in %.2f s, %d moved, %.2f %% free%s"
        % (summary["count"], summary["seconds"], len(moved), free * 100,
           "" if freed is None else "  (VACUUM at %.0f %% free)" % (freed * 100)))
    return summary, moved


def _react_to_sweep(moved, roots, keeps, paths, watched_ids, out) -> set[str]:
    """Which projects the sweep's own findings call for, and what to say.

    **This is the only thing the broad half can tell the narrow one, and it
    closes CP-13 blind spot 1 (R5).** inotify knows nothing about what changed
    while the service was down; the journal does, so the first sweep after a
    restart catches up instead of waiting for the next save.

    **A project whose guard we do not hold is reported, never written (R6).**
    A service that takes a guard to be helpful is a service that locks a
    person out of their own project, and CP-13 gate 3 says that must not
    happen.
    """
    hits = _hits(((p, 0) for p in moved), roots, keeps)
    unwatched = sorted(hits - set(watched_ids))
    for project_id in unwatched:
        out("changed  %s  %s has changes and is not watched: "
            "morphofiles-graph update %s" % (project_id, paths[project_id],
                                             project_id))
    return hits & set(watched_ids)


def _update(project_id: str, root: str, out) -> None:
    """The narrow clock's write. A refusal is a line, never the end of the run."""
    started = time.monotonic()
    try:
        with Store(db_path(project_id)) as store:
            result = build_layers(store, project_id)
    except Refused as exc:
        out("REFUSED  %s" % exc)
        return
    except Unguarded as exc:
        # The service is writing a project whose guard it does not hold, which
        # means it was never in `watched` when the guards were taken. Naming
        # the project beats a traceback: the run continues for every other
        # project, and the line says which one to restart for.
        out("REFUSED  %s (restart the service to pick it up)" % exc)
        return
    if result["recreated"]:
        out("update   %s  index recreated, but it has no recorded path"
            % project_id)
        return
    out("update   %s  %s  L2 %d read / %d unread  L3 %d edges  L4 %d rows"
        "  in %.2f s"
        % (project_id, root, result["l2"]["read"], result["l2"]["unread"],
           result["l3"]["edges"], result["l4"]["rows"],
           time.monotonic() - started))


def serve(*, scan_root: str = "~", sweep_seconds: float = SWEEP_SECONDS,
          debounce: float = DEBOUNCE_SECONDS,
          source=None, out=print, clock=time.monotonic, sleep=time.sleep,
          rounds: int | None = None) -> int:
    """Hold the guards, sweep on one clock, react on the other. Blocks.

    `source`, `clock`, `sleep` and `rounds` are injected so a gate can drive a
    whole round without waiting for a real one: a service that could only be
    tested by living for thirty minutes would be a service nobody tests.

    Returns 2 when a guard was refused, 0 otherwise. Ctrl-C propagates out of
    `source.read` and every guard is released on the way (R9).
    """
    watched = watched_projects()
    store_paths = [l0_path()] + [db_path(pid) for pid, _ in watched]
    held = _take(store_paths, out)
    if held is None:
        return 2
    # Printed before the first sweep, and that order is load-bearing: the
    # sweep takes seconds, and anything waiting to find out whether this
    # process is the writer would otherwise wait for a catalogue it does not
    # care about. A gate waits on this line too.
    out("serving  %d guard(s) held, %d watched project(s), sweep every "
        "%.0f min" % (len(held), len(watched), sweep_seconds / 60))

    owned = None
    try:
        if source is None:
            try:
                source = _arm(watched, store_paths, out)
            except InotifyUnavailable as exc:
                # R7: the narrow half is missing, and taking the broad half
                # down with it would be the service failing at the job it can
                # still do.
                out("unwatched  %s\n(%d project(s) will only be seen by the "
                    "%.0f min sweep)" % (exc, len(watched), sweep_seconds / 60))
                source = _Silent(sleep)
            owned = source
        # Two views of the same projects. Events may only ever reach a project
        # whose guard we hold, so `roots` is the watched ones -- but the
        # sweep's journal covers the whole catalogue, and R6 says an unwatched
        # project with changes is *reported*, which needs its scope too.
        everyone = projects()
        keeps = {pid: keeper(chosen_scope(path), store_paths)
                 for pid, path in everyone}
        roots = sorted(((path, pid) for pid, path in watched),
                       key=lambda pair: len(pair[0]), reverse=True)
        all_roots = sorted(((path, pid) for pid, path in everyone),
                           key=lambda pair: len(pair[0]), reverse=True)
        paths = dict(everyone)
        watched_ids = {pid for pid, _path in watched}

        next_sweep = clock()
        done = 0
        while rounds is None or done < rounds:
            done += 1
            batch = source.read(max(0.0, next_sweep - clock()))
            hits: set[str] = set()
            if clock() >= next_sweep:
                _summary, moved = _sweep(scan_root, out)
                next_sweep = clock() + sweep_seconds
                # What the broad half tells the narrow one (R5). A watched
                # project that changed while the service was down is invisible
                # to inotify and plain in the journal.
                hits |= _react_to_sweep(moved, all_roots, keeps, paths,
                                        watched_ids, out)
            hits |= _hits(batch, roots, keeps)
            if not hits:
                if batch:
                    # Events arrived and none was worth keeping. Reading again
                    # at once would spin a core as fast as the kernel delivers
                    # them; one debounce caps the wake rate. Nothing is lost --
                    # inotify queues what we did not read.
                    sleep(debounce)
                continue
            # Coalesce: keep waiting until a full debounce window is quiet of
            # kept events, so one save touching five files is one update.
            while True:
                more = _hits(source.read(debounce), roots, keeps)
                if not more:
                    break
                hits |= more
            for pid in sorted(hits):
                path = paths[pid]
                _update(pid, path, out)
                # The scope may be exactly what changed (R4), so the predicate
                # that decides the *next* burst is rebuilt from the tree we
                # just read, not from the one we started with.
                keeps[pid] = keeper(chosen_scope(path), store_paths)
        return 0
    finally:
        if owned is not None:
            owned.close()
        for lock in held:
            lock.release()


class _Silent:
    """An event source that never has anything, for a machine without inotify.

    It takes the loop's own `sleep` rather than reaching for `time.sleep`. The
    difference is not style: the loop's first read asks for the whole sweep
    interval, so a `_Silent` with a hard-wired sleep parks the process for
    thirty minutes and cannot be driven by a gate at all. Measured 2026-08-08,
    where it turned a mutation into `<timeout>` instead of a verdict.
    """

    def __init__(self, sleep=time.sleep):
        self._sleep = sleep

    def read(self, timeout):
        if timeout:
            self._sleep(timeout)
        return []

    def close(self):
        pass


def _prune_for(watched, store_paths):
    """One prune predicate covering every watched tree at once.

    **The prune asks the scope, and nothing else asks anything.** A watch is
    per directory, so `.git` alone is thousands of them and every git command
    is a flood -- but `.git` is already out of scope for a repo, and `JUNK` is
    already out of scope for a folder. A second exclusion list here would put
    the corpus rule in two places, and the predecessor's note is the one this
    borrows: "so the exclusion rule lives in exactly one place (the corpus
    classifier), not two."

    **One predicate for all of them, not one per tree, and that is a fix
    rather than a tidy.** `Inotify` keeps a single `_prune` field -- it was
    written for one tree -- and re-consults it when a directory appears at
    runtime. Handing `add_tree` a per-project closure leaves the *last*
    project's scope deciding for every project's new directories, which is
    wrong the moment a second project is watched and silent when it is.

    A directory being pruned does not hide it: its *parent* is still watched,
    so `.git` appearing is still reported. That is what keeps R4 true while
    the inside of `.git` stays unwatched.
    """
    stores = [str(p) for p in store_paths]
    rules = [(path, store_prune(path, stores), chosen_scope(path))
             for _pid, path in watched]

    def prune(directory: str) -> bool:
        for root, by_store, selected in rules:
            if directory == root or directory.startswith(
                    root.rstrip(os.sep) + os.sep):
                return (by_store(directory)
                        or not selected.contains(directory, is_dir=True))
        # Under no watched root at all. Nothing there is ours to watch, and
        # `add_tree` arms each root itself before asking, so this cannot
        # prune away the thing it was called for.
        return True

    return prune


def _arm(watched, store_paths, out):
    """One inotify instance, watching only the flagged projects (R3)."""
    source = Inotify()
    prune = _prune_for(watched, store_paths)
    for project_id, path in watched:
        armed = source.add_tree(path, prune)
        out("watching %s  %s  (%d directories)" % (project_id, path, armed))
    return source


def _hits(batch, roots, keeps) -> set[str]:
    """The projects a batch of events is worth updating.

    Longest root first, so a project nested inside another is credited to the
    inner one rather than to whichever was registered first.
    """
    found = set()
    for path, _mask in batch:
        for root, project_id in roots:
            if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
                if keeps[project_id](path):
                    found.add(project_id)
                break
    return found
