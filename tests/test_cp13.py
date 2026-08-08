#!/usr/bin/env python3
"""CP-13 -- the indexing service.

The answer key is `tests/gold/FASIT-cp13.md`, written before this file and
before the code it grades (`63b80ee`). Gate numbers below are that document's.

Two failure shapes, and neither is an exception. One is a service that takes
every guard on the machine and calls that "being the only writer" -- gate 3 is
the control for it. The other is a watch that fires on everything and calls
that "noticing changes" -- gate 10 is the control for it. Both look like a
working service from the outside, which is why half of these are controls.

The event policy is driven with a scripted source rather than by writing files
and hoping: a gate that waits for the kernel is a gate that is flaky on a busy
machine, and `serve` takes `source`, `clock`, `sleep` and `rounds` precisely so
that a round can be run without one. The kernel side is exercised separately,
where it is the subject (gate 11).

Run:
    python3 tests/test_cp13.py
"""
from __future__ import annotations

import ast
import os
import selectors
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import service, watch  # noqa: E402
from morpho_homegraph.lock import holds  # noqa: E402
from morpho_homegraph.store import Store, db_path, l0_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(62)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=60):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def fresh_home(work, name, store_inside=None):
    """Its own store for one group of gates. Groups must not inherit an L0.

    `store_inside` puts the store *under* the given directory instead of
    beside it. That is not a variation for its own sake: with the store
    outside every watched tree, `_hits` drops a store path before `keep` is
    ever asked, and the self-trigger guard is never exercised. The mutation
    "store writes are treated as corpus changes" survived a sweep because of
    exactly that, 2026-08-08.
    """
    root = store_inside or os.path.join(work, name)
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(root, "store")
    home = os.path.join(work, name, "home")
    os.makedirs(home, exist_ok=True)
    return home


def make_repo(root, flavour="one"):
    """A git repo, so `from_repo` is the branch under test and `.gitignore`
    is a file the scope actually consults.

    **`.gitignore` ignores itself**, and that is the whole point of the
    fixture. A `.gitignore` the scope would have indexed anyway is kept by
    `contains()` whether or not `is_layout()` exists, so a gate built on it
    grades nothing -- which is what the answer key warned about ("the trap is
    that `.gitignore` is frequently a file L2 would drop anyway") and what the
    first version of this file did anyway.
    """
    write(os.path.join(root, "a.md"), "the %s tree, see [[b]]\n" % flavour)
    write(os.path.join(root, "b.md"), "leaf of %s\n" % flavour)
    write(os.path.join(root, ".gitignore"), ".gitignore\nnotes/\n")
    write(os.path.join(root, "notes", "ignored.md"), "not indexed\n")
    os.makedirs(os.path.join(root, ".git", "objects"), exist_ok=True)
    write(os.path.join(root, ".git", "objects", "loose"), "not content\n")
    return root


def add(root):
    out = cli("add", root)
    return out.stdout.split()[0] if out.stdout.strip() else ""


def rows(project_id, table):
    with Store(db_path(project_id), read_only=True) as store:
        return store.db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


class Scripted(watch.Source):
    """One batch of events per `read`, then nothing for ever.

    `timeout` is ignored on purpose: the loop's own clock is injected too, so
    a gate decides when the sweep is due rather than waiting for it.
    """

    def __init__(self, batches):
        self.batches = [list(b) for b in batches]
        self.reads = 0

    def read(self, timeout):
        self.reads += 1
        return self.batches.pop(0) if self.batches else []

    def close(self):
        pass


def run_rounds(batches, rounds, *, sweep_seconds=10 ** 6):
    """Drive `serve` over a scripted source. Returns (exit code, lines).

    An exception out of `serve` becomes a line rather than the end of the
    suite. A run that dies takes every gate after it down and names none of
    them, which is the same "detected only by a crash" verdict this whole
    checkpoint is written to avoid -- and it is worth exactly as little in a
    test harness as it is in the service.
    """
    lines = []
    source = Scripted(batches)
    try:
        code = service.serve(scan_root=os.environ.get("MHG_TEST_ROOT", "~"),
                             sweep_seconds=sweep_seconds, debounce=0.0,
                             source=source, out=lines.append,
                             sleep=lambda _s: None, rounds=rounds)
    except Exception as exc:  # noqa: BLE001 -- turning a crash into a verdict
        lines.append("CRASHED  %s: %s" % (type(exc).__name__, exc))
        code = -1
    return code, lines


def updates(lines):
    return [ln for ln in lines if ln.startswith("update ")]


def sweeps(lines):
    return [ln for ln in lines if ln.startswith("sweep ")]


# -- 1, 2, 3, 4, 17, 20: the guards ----------------------------------------

def gates_guards(work):
    """`serve` is the only writer while it runs -- and only of what it watches."""
    home = fresh_home(work, "guards")
    watched_root = make_repo(os.path.join(home, "watched"))
    other_root = make_repo(os.path.join(home, "other"), "two")
    watched_id, other_id = add(watched_root), add(other_root)
    ok = check("21 watch arms an already registered project",
               cli("watch", watched_id).returncode == 0
               and _meta(watched_id, "watch") == "1",
               "flag=%r" % _meta(watched_id, "watch"))
    # 21b: the other direction, and it had no gate at all until 2026-08-08 --
    # the mutation "watch only ever writes 1" walked straight through a sweep.
    # A flag that can be set and not cleared is a project you cannot stop
    # watching without editing the store by hand.
    cli("watch", other_id)
    off = cli("watch", other_id, "--off")
    check("21b CONTROL: watch --off clears the flag again",
          off.returncode == 0 and _meta(other_id, "watch") == "0",
          "flag=%r" % _meta(other_id, "watch"))
    if not ok:
        return

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "morpho_homegraph.cli", "serve", home,
         "--sweep", "10000"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=REPO, env=env, stdin=subprocess.DEVNULL)
    try:
        banner = _await(proc, "serving", seconds=30)
        if not check("4b the service says it is holding the guards before "
                     "it sweeps", bool(banner), banner or "no banner"):
            return
        scanned = cli("scan", home)
        check("1  scan is refused while the service runs, and names it",
              scanned.returncode == 2 and "serve" not in scanned.stdout
              and "owns writing" in scanned.stderr,
              "rc=%s %r" % (scanned.returncode, scanned.stderr.strip()[:70]))
        blocked = cli("update", watched_id)
        check("2  update on a watched project is refused the same way",
              blocked.returncode == 2 and "owns writing" in blocked.stderr,
              "rc=%s %r" % (blocked.returncode, blocked.stderr.strip()[:70]))
        # The control. Without it, 1 and 2 are equally green for a service
        # that takes every guard on the machine and indexes nothing.
        allowed = cli("update", other_id, timeout=180)
        check("3  CONTROL: an unwatched project still updates while it runs",
              allowed.returncode == 0,
              "rc=%s %r" % (allowed.returncode,
                            (allowed.stderr or allowed.stdout).strip()[:70]))
        # Gate 4: a second service is refused, and leaves nothing behind.
        second = cli("serve", home, timeout=60)
        check("4  a second service is refused, exit 2, no store left behind",
              second.returncode == 2 and "owns writing" in second.stdout
              and not os.path.exists(os.path.join(
                  os.environ["MORPHO_HOMEGRAPH_HOME"], "l0", "index.db-wal2")),
              "rc=%s" % second.returncode)
    finally:
        proc.send_signal(2)  # SIGINT -- the same thing Ctrl-C sends
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
    # **The child outlives a killed suite, and that is not hypothetical.**
    # This `finally` covers every way the suite *returns*; it covers none of
    # the ways it is *killed* -- and a mutation run that hits its timeout is
    # killed. Three `serve` processes were found alive on 2026-08-08 with
    # their temp stores long deleted, from runs that had been stopped that
    # way. Harmless in themselves, but a service that outlives its suite holds
    # guards and confuses every later process check.
    #
    # Not fixed with a signal handler here on purpose: the reliable form is
    # for the *driver* to kill the process group, which is a change to
    # `mutate.py` and therefore a change to every harness. Written down rather
    # than half-done -- `pgrep -af 'morpho_homegraph.cli serve /tmp/'` finds
    # them, and they die with a plain `kill`.
    check("20 CONTROL: a normal start and Ctrl-C exits 0",
          proc.returncode == 0, "rc=%s" % proc.returncode)
    # 17b, and it is weaker than it looks on its own: the kernel drops a dead
    # process's flocks regardless, so this cannot fail for a missing
    # `release()`. It grades the signal path -- that SIGINT ends the service
    # rather than leaving it running -- and gate 17 grades the release.
    check("17b after Ctrl-C the store is writable again from outside",
          cli("scan", home, timeout=120).returncode == 0)


def _meta(project_id, key):
    with Store(db_path(project_id), read_only=True) as store:
        return store.get_meta(key)


def _await(proc, marker, seconds):
    """The first line containing `marker`, or "" if it never comes.

    Waits on the descriptor, never on `readline()`. A service that prints two
    lines and then blocks for its next sweep leaves `readline()` blocked for
    ever, and the deadline above it is only consulted *between* reads -- so
    the loop that looks bounded never comes back. That is what turned the
    "starts silently" mutation into `<timeout>` instead of a red gate,
    measured 2026-08-08.
    """
    deadline = time.monotonic() + seconds
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return ""
            if not selector.select(timeout=0.5):
                continue
            line = proc.stdout.readline()
            if not line:
                return ""
            if marker in line:
                return line.strip()
        return ""
    finally:
        selector.close()


# -- 5, 6, 7, 8, 9, 10, 12, 13, 18: the two clocks -------------------------

def gates_clocks(work):
    """What begins a burst, what does not, and what the sweep leaves alone.

    **The store sits inside the watched project here**, which is the case R2
    is written for -- somebody registers `~`, and the index the service writes
    is suddenly part of the corpus it watches. With the store outside, `_hits`
    drops a store path before `keep` is asked and gate 6 grades nothing.
    """
    root = os.path.join(work, "clocks", "home", "proj")
    home = fresh_home(work, "clocks", store_inside=root)
    os.environ["MHG_TEST_ROOT"] = home
    make_repo(root)
    project_id = add(root)
    cli("watch", project_id)

    a, b = os.path.join(root, "a.md"), os.path.join(root, "b.md")
    ignored = os.path.join(root, "notes", "ignored.md")
    gitignore = os.path.join(root, ".gitignore")
    store_file = str(l0_path())

    # Round 1 sweeps and nothing else: the source has nothing to give.
    code, lines = run_rounds([[]], rounds=1)
    # Counted as zero rather than read blindly. A service that never sweeps
    # leaves no L0 at all, and opening one that is not there raises
    # `FileNotFoundError` -- which takes the suite down and names no gate,
    # exactly what this gate is here to prevent one floor down.
    catalogued = 0
    if l0_path().is_file():
        with Store(l0_path(), read_only=True, role="l0") as l0:
            catalogued = l0.db.execute(
                "SELECT COUNT(*) FROM files").fetchone()[0]
    check("12 the periodic sweep rebuilds L0 with no event at all",
          code == 0 and catalogued > 0 and len(sweeps(lines)) == 1,
          "%d catalogued, %d sweep line(s)" % (catalogued, len(sweeps(lines))))
    # 13 was "the sweep builds no project layers at all" until CP-15, and CP-15
    # deliberately made that false: the sweep now updates a watched project the
    # journal says changed, which is the only thing the broad half can tell the
    # narrow one (CP-15 R5) and the one way a project catches up on changes
    # made while the service was down. **Re-decided, not deleted** -- what has
    # to stay true is that the sweep builds only what the journal *names*, and
    # a second sweep over a settled corpus therefore builds nothing.
    _, settled = run_rounds([[]], rounds=1)
    check("13 CONTROL: a second sweep over a settled corpus builds nothing",
          not updates(settled) and bool(sweeps(settled)),
          "%d update line(s) on the settled sweep" % len(updates(settled)))

    # The control for 12, and it is the one that catches a sweep on every
    # round: gate 12 counts one sweep in one round, which is also what a
    # service with no clock at all produces.
    _, lines = run_rounds([[], []], rounds=2)
    check("12b CONTROL: a second round with the sweep not due does not sweep",
          len(sweeps(lines)) == 1, "%d sweep line(s)" % len(sweeps(lines)))

    # A burst that spans two reads is still one update. Two reads rather than
    # one batch on purpose: five events in a single batch are coalesced by the
    # set, and would be one update even with no debounce window at all.
    code, lines = run_rounds(
        [[], [(a, 0), (b, 0)], [(a, 0), (b, 0)], []], rounds=3)
    check("5  a burst spanning two reads is one update, not one per read",
          len(updates(lines)) == 1, "%d update line(s)" % len(updates(lines)))
    check("18 the round names the project, the layers and the time",
          bool(updates(lines)) and project_id in updates(lines)[0]
          and "L2" in updates(lines)[0] and " s" in updates(lines)[0],
          (updates(lines) or [""])[0][:78])

    # R2: the service's own writes are events too, and here they are inside
    # the watched tree and inside the scope -- so `relevant()` is the only
    # thing between a store write and a rebuild that writes the store again.
    check("6a the store really is inside the watched tree and in scope",
          store_file.startswith(root + os.sep)
          and service.chosen_scope(root).contains(store_file),
          store_file[len(root):])
    _, lines = run_rounds([[], [(store_file, 0), (store_file + "-wal", 0)]],
                          rounds=2)
    check("6  the service's own store writes never begin a burst",
          not updates(lines), "%d update line(s)" % len(updates(lines)))
    # The control for 6, and it is the whole reason 6 is not free: a loop that
    # never triggers anything passes 6 perfectly.
    _, lines = run_rounds([[], [(a, 0)], []], rounds=2)
    check("7  CONTROL: one corpus path does trigger exactly one update",
          len(updates(lines)) == 1, "%d update line(s)" % len(updates(lines)))

    # R4: layout is not content. `.gitignore` is excluded from L2 here by its
    # own `notes/` rule being irrelevant to it -- what matters is that the
    # scope predicate is not the only thing consulted.
    _, lines = run_rounds([[], [(gitignore, 0)], []], rounds=2)
    check("8  an edited .gitignore triggers an update",
          len(updates(lines)) == 1, "%d update line(s)" % len(updates(lines)))
    _, lines = run_rounds([[], [(os.path.join(root, ".git"), 0)], []], rounds=2)
    check("9  a folder becoming a repo (.git appears) triggers an update",
          len(updates(lines)) == 1, "%d update line(s)" % len(updates(lines)))
    # The control for 8 and 9. Both are green for a watch that fires on
    # everything, and a watch that fires on everything is a rebuild per
    # keystroke in any excluded directory.
    _, lines = run_rounds([[], [(ignored, 0)], []], rounds=2)
    check("10 CONTROL: a change the scope excludes triggers nothing",
          not updates(lines), "%d update line(s)" % len(updates(lines)))

    # 17: checked in-process, because a subprocess proves nothing here. The
    # kernel drops a dead process's flocks whether or not `serve` released
    # them, so the subprocess form of this gate stays green with the `finally`
    # deleted -- measured 2026-08-08, when that mutation was killed by an
    # unrelated gate instead. `holds()` is this process's own bookkeeping,
    # and it only clears if `release()` actually ran.
    still_held = [p for p in (l0_path(), db_path(project_id)) if holds(str(p))]
    check("17 serve releases every guard on the way out, in this process",
          not still_held, "still held: %s" % [os.path.basename(str(p))
                                              for p in still_held])
    del os.environ["MHG_TEST_ROOT"]


# -- 11: only the flagged projects are watched -----------------------------

def gates_inotify(work):
    """The narrow half, against the real kernel -- that is its whole subject."""
    home = fresh_home(work, "inotify")
    on_root = make_repo(os.path.join(home, "armed"))
    off_root = make_repo(os.path.join(home, "unarmed"), "two")
    on_id, off_id = add(on_root), add(off_root)
    cli("watch", on_id)

    listed = service.watched_projects()
    check("11a only the flagged project is listed as watched",
          [pid for pid, _p in listed] == [on_id],
          "listed=%s" % [pid for pid, _p in listed])
    # The control for 11a, and it is not a formality: `watched_projects`
    # returning nothing at all would pass 11a and leave the service watching
    # a machine on which every project is registered.
    check("11b CONTROL: the unflagged project is still registered",
          off_id in [pid for pid, _p in service.projects()],
          "registered ids=%s" % [pid for pid, _p in service.projects()])

    # Armed through the service's own entry point, so the gate grades the
    # path production uses rather than a second one written for the test.
    try:
        source = service._arm(listed, [l0_path(), db_path(on_id)],
                              lambda _line: None)
    except watch.InotifyUnavailable as exc:
        # Refused rather than passed: a gate that can only be exercised on
        # some machines is a gate that reports coverage it does not have.
        check("11 the flagged tree is armed and the unflagged one is not",
              False, "inotify unavailable, so this proved nothing: %s" % exc)
        return
    try:
        armed = set(source._wd2path.values())
    finally:
        source.close()
    check("11 the flagged tree is armed and the unflagged one is not",
          any(d.startswith(on_root) for d in armed)
          and not any(d.startswith(off_root) for d in armed)
          and not any((os.sep + ".git") in d for d in armed),
          "%d directories armed" % len(armed))

    # 11c: `Inotify` keeps one `_prune` field and re-consults it when a
    # directory appears at runtime, so a per-project closure would leave the
    # last project's scope deciding for the first project's new directories.
    # Tested on the predicate rather than through the kernel: waiting for a
    # real event to arrive is what makes a gate flaky on a busy machine.
    cli("watch", off_id)
    both = service.watched_projects()
    prune = service._prune_for(both, [l0_path()])
    check("11c one prune covers every watched tree, not the last one only",
          len(both) == 2
          and not prune(on_root) and not prune(off_root)
          and prune(os.path.join(on_root, "notes"))
          and prune(os.path.join(off_root, ".git")),
          "watched=%d" % len(both))


# -- 14, 15: fragmentation --------------------------------------------------

def gates_vacuum(work):
    """`VACUUM` on a threshold, and the control that it is not every round."""
    home = fresh_home(work, "vacuum")
    root = make_repo(os.path.join(home, "proj"))
    add(root)
    l0_path().parent.mkdir(parents=True, exist_ok=True)
    from morpho_homegraph.lock import StoreLock
    guard = StoreLock(str(l0_path())).acquire()
    try:
        with Store(l0_path(), role="l0") as store:
            with store.writing() as db:
                db.executemany(
                    "INSERT INTO files (path, kind, size, mtime_ns, inode, dev)"
                    " VALUES (?, 'file', 0, 0, 0, 0)",
                    [("/p/%d" % i,) for i in range(40000)])
                db.commit()
                db.execute("DELETE FROM files WHERE rowid % 4 != 0")
                db.commit()
            before = store.db.execute("PRAGMA page_count").fetchone()[0]
            free_pages = store.db.execute(
                "PRAGMA freelist_count").fetchone()[0]
            truth = free_pages / before
            free = service.fragmentation(store.db)
            # 14b: worked out from the pragmas here rather than taken from the
            # function under test. Gate 15 used to derive its own threshold as
            # `fragmentation(...) + 0.5`, which is green for a `fragmentation`
            # that always answers 1.0 -- and that mutation survived a sweep on
            # 2026-08-08 for exactly that reason.
            check("14b fragmentation is free pages over all pages, not a "
                  "constant",
                  abs(free - truth) < 1e-9 and 0.0 < truth < 1.0,
                  "%.4f from the function, %.4f from the pragmas (%d/%d)"
                  % (free, truth, free_pages, before))
            # The control: the same store, a threshold nothing can meet.
            check("15 CONTROL: under the threshold VACUUM does not run",
                  service.maybe_vacuum(store, threshold=1.01) is None
                  and store.db.execute(
                      "PRAGMA page_count").fetchone()[0] == before,
                  "free=%.2f, pages unchanged at %d" % (free, before))
            acted = service.maybe_vacuum(store, threshold=0.0)
            after = store.db.execute("PRAGMA page_count").fetchone()[0]
            check("14 over the threshold VACUUM runs and the file shrinks",
                  acted is not None and after < before,
                  "%d -> %d pages, acted on %.2f free"
                  % (before, after, acted or -1))
    finally:
        guard.release()


# -- 16: a machine without inotify -----------------------------------------

def gates_no_inotify(work):
    """R7: the narrow half missing must not take the broad half down."""
    home = fresh_home(work, "noinotify")
    os.environ["MHG_TEST_ROOT"] = home
    root = make_repo(os.path.join(home, "proj"))
    project_id = add(root)
    cli("watch", project_id)

    lines = []
    real = service.Inotify

    def refuse():
        raise watch.InotifyUnavailable("no libc inotify on this platform")

    service.Inotify = refuse
    # The refusal escaping is the failure this gate is about, so it is caught
    # here and turned into a red check. Letting it propagate would end the
    # suite with a traceback, and a traceback names no gate.
    code, escaped = None, ""
    try:
        code = service.serve(scan_root=home, sweep_seconds=10 ** 6,
                             out=lines.append, sleep=lambda _s: None,
                             rounds=1)
    except Exception as exc:  # noqa: BLE001 -- the point is that nothing may
        escaped = "%s: %s" % (type(exc).__name__, exc)
    finally:
        service.Inotify = real
        del os.environ["MHG_TEST_ROOT"]
    check("16 without inotify the service says so, sweeps, and exits 0",
          not escaped and code == 0
          and any(ln.startswith("unwatched") for ln in lines)
          and len(sweeps(lines)) == 1,
          escaped or "rc=%s, %d sweep line(s)" % (code, len(sweeps(lines))))


# -- 19: one definition of an update ---------------------------------------

def gates_one_update():
    """The command and the clock call the same builder, read as call nodes.

    Text search is what let CP-7B's defect live for five checkpoints: with the
    call gone and only the docstring explaining the rule left behind, a grep
    still finds the name. This file's own prose names `cmd_update` several
    times, which is exactly the false positive an `ast` pass does not have.
    """
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    calls: dict[str, set[str]] = {}
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        seen = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                seen.add(func.attr)
            elif isinstance(func, ast.Name):
                seen.add(func.id)
        calls[name] = seen
    check("19a the command builds through service.build_layers",
          "build_layers" in calls.get("cli.py", set()))
    check("19b the service builds through the same function",
          "build_layers" in calls.get("service.py", set()))
    # The other half of R10, and the one that would have been silent: calling
    # `cmd_update` from the service takes a guard this process already holds,
    # and `StoreLock.acquire` opens a fresh descriptor every time.
    check("19c the service never calls cmd_update, which would refuse itself",
          "cmd_update" not in calls.get("service.py", set()))


def main() -> int:
    # The static gate first, and the order is not taste. It reads the package
    # with `ast` and needs nothing to run; a runtime group that crashes ahead
    # of it takes its verdict down with it, and a mutation caught by a static
    # gate then reports as "detected only by a crash" -- which names no gate.
    # Measured 2026-08-08: the `cmd_update` mutation did exactly that.
    gates_one_update()
    with tempfile.TemporaryDirectory(prefix="mhg-cp13-") as work:
        # `gates_clocks` before `gates_guards`: the sweep is what builds L0,
        # and every guard gate depends on a catalogue existing. With the order
        # reversed, breaking the sweep goes red in gate 3 first and the
        # mutation is filed against a control it has nothing to do with.
        gates_clocks(work)
        gates_guards(work)
        gates_inotify(work)
        gates_vacuum(work)
        gates_no_inotify(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp13():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
