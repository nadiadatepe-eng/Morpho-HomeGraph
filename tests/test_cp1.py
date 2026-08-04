#!/usr/bin/env python3
"""CP-1 -- L0, the catalogue over the whole home area.

The answer key is `tests/gold/FASIT-cp1.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

The load-bearing claim is "this layer never opens a file", and it is checked
by **two detectors that fail differently**: `sys.addaudithook` sees what
Python does and is blind to a C extension that opens without raising an audit
event; `strace` sees every syscall and is blind to which of them was ours.
Neither is the proof alone, and each has a negative control -- a run that
deliberately reads must be caught, and the strace invocation must be shown to
see anything at all. Without those two, "no openat" is equally satisfied by a
walk that visited nothing and a filter that matches nothing.

Run:
    python3 tests/test_cp1.py
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.lock import Locked, StoreLock, Unguarded  # noqa: E402
from morpho_homegraph.scan import (  # noqa: E402
    DEFAULT_DENY, DeniedRoot, WrongStore, _normalise_root, scan, walk)
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, data_home, db_path, l0_path, new_project)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(56)

# A second interpreter that walks a tree with the production code and reports
# nothing but the count. Run under strace, its syscalls are the measurement.
WALKER = """
import sys
sys.path.insert(0, %r)
from morpho_homegraph.scan import walk
print(sum(1 for _ in walk(sys.argv[1])), flush=True)
"""

# The same, except it reads one file. The negative control: if strace cannot
# see this, it cannot see anything, and gate 4 proves nothing.
READER = """
import sys
sys.path.insert(0, %r)
from morpho_homegraph.scan import walk
rows = list(walk(sys.argv[1]))
for path, kind, *_ in rows:
    if kind == "file":
        open(path, "rb").read(16)
        break
print(len(rows), flush=True)
"""


def build_tree(root):
    """A fixture that does not change while `find` and the walk both look at it.

    Counting against a live home area is a weak gate: files appear and vanish
    between the two passes. Everything the rules talk about is planted here
    instead -- a symlink, a cycle, a directory with no read permission.
    """
    os.makedirs(os.path.join(root, "src", "deep"), exist_ok=True)
    os.makedirs(os.path.join(root, "empty"), exist_ok=True)
    for name, body in (("a.py", "x = 1\n"), ("b.md", "# title\n" * 40),
                       ("src/c.py", "def f():\n    return 2\n"),
                       ("src/deep/d.txt", "deep\n")):
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    os.symlink(os.path.join(root, "a.py"), os.path.join(root, "link-to-a"))
    # A cycle. A walk that follows symlinks does not terminate here, and one
    # that dedupes by path rather than by (dev, inode) counts the tree twice.
    os.symlink("..", os.path.join(root, "src", "up"))
    closed = os.path.join(root, "closed")
    os.makedirs(closed, exist_ok=True)
    with open(os.path.join(closed, "secret.txt"), "w") as fh:
        fh.write("hidden\n")
    os.chmod(closed, 0o000)
    return closed


def find_count(root):
    """`find` over the same tree: everything except the root itself."""
    proc = subprocess.run(["find", root, "-mindepth", "1"],
                          capture_output=True, text=True, timeout=120)
    return [line for line in proc.stdout.splitlines() if line]


# -- 1-2, 7-11, 13. the walk itself ----------------------------------------

def gates_walk(tree, closed):
    # An unreadable directory is the one condition in this fixture that can
    # end the walk rather than appear in it, so it is caught here: a walk that
    # raises is gate 9 failing, not the harness dying. A crash names no gate.
    try:
        rows = list(walk(tree))
    except OSError as exc:
        check("9  an unreadable directory is reported, not fatal", False,
              "the walk raised: %r" % exc)
        for gate in ("1  the row count matches find on the same tree",
                     "2  every row carries all six fields",
                     "7  a symlink is its own row with its own inode",
                     "8  a symlink cycle terminates and doubles nothing",
                     "11 two walks of an untouched tree give the same set",
                     "13 mtime is an integer count of nanoseconds"):
            check(gate, False, "not reached")
        return
    kept = [r for r in rows if r[1] != "unreadable"]
    paths = {r[0] for r in kept}

    # `find` cannot enter the unreadable directory either, so both sides see
    # the directory and not its contents. That is the comparison being made.
    expected = set(find_count(tree))
    check("1  the row count matches find on the same tree",
          paths == expected and paths,
          "walk %d, find %d, differing %d"
          % (len(paths), len(expected), len(paths ^ expected)))

    check("2  every row carries all six fields",
          all(r[0] and r[1] and r[3] > 0 and r[4] > 0 and r[5] > 0
              for r in kept),
          "%d rows" % len(kept))

    link = [r for r in kept if r[0].endswith("link-to-a")]
    target = [r for r in kept if r[0].endswith("/a.py")]
    check("7  a symlink is its own row with its own inode",
          len(link) == 1 and len(target) == 1
          and link[0][1] == "link" and link[0][4] != target[0][4],
          "link inode %s, target inode %s"
          % (link[0][4] if link else "-", target[0][4] if target else "-"))

    # If the cycle were followed, this walk would not have returned at all --
    # so reaching this line is half the gate. The other half is that nothing
    # was counted twice on the way.
    check("8  a symlink cycle terminates and doubles nothing",
          len(paths) == len(kept),
          "%d rows, %d distinct paths" % (len(kept), len(paths)))

    unreadable = [r[0][1:] for r in rows if r[1] == "unreadable"]
    check("9  an unreadable directory is reported, not fatal",
          unreadable == [closed] and closed in paths,
          "unreadable=%s" % unreadable)

    again = {r[0] for r in walk(tree) if r[1] != "unreadable"}
    check("11 two walks of an untouched tree give the same set",
          again == paths, "%d symmetric difference" % len(again ^ paths))

    mtimes = [r[3] for r in kept]
    check("13 mtime is an integer count of nanoseconds",
          all(isinstance(m, int) for m in mtimes) and max(mtimes) > 10 ** 17,
          "max=%s" % max(mtimes))


def build_deny_tree(root):
    """A denied directory, a neighbour that merely starts the same, and content
    under both. The neighbour is the whole reason this fixture is not one
    directory: a deny-list that compares string prefixes passes every gate here
    except 21."""
    denied = os.path.join(root, ".cache")
    neighbour = os.path.join(root, ".cachexyz")
    for d in (denied, neighbour):
        os.makedirs(os.path.join(d, "inner"), exist_ok=True)
        with open(os.path.join(d, "inner", "f.txt"), "w") as fh:
            fh.write("x")
    with open(os.path.join(root, "keep.txt"), "w") as fh:
        fh.write("y")
    return denied, neighbour


def gates_deny(work):
    root = os.path.join(work, "denytree")
    os.makedirs(root)
    denied, neighbour = build_deny_tree(root)

    rows = list(walk(root, deny=(denied,)))
    paths = {p for p, kind, *_ in rows if kind != "pruned"}
    pruned = [p for p, kind, *_ in rows if kind == "pruned"]

    check("20 a denied directory leaves no row behind", denied not in paths,
          "%d rows kept" % len(paths))
    check("21 a neighbour that only starts the same is kept",
          neighbour in paths and os.path.join(neighbour, "inner", "f.txt") in paths,
          "%s survives .cache" % os.path.basename(neighbour))
    under = [p for p in paths if p.startswith(denied + os.sep)]
    check("22 nothing under a denied directory is reached", not under,
          "%d rows under it" % len(under))

    none_denied = {p for p, kind, *_ in walk(root, deny=()) if kind != "pruned"}
    miss = {p for p, kind, *_ in walk(root, deny=("/nowhere/at/all",))
            if kind != "pruned"}
    check("23 an empty deny-list is legal and prunes nothing",
          bool(none_denied) and len(none_denied) > len(paths),
          "%d without vs %d with" % (len(none_denied), len(paths)))
    check("24 a deny entry that matches nothing changes nothing",
          miss == none_denied, "%d rows either way" % len(miss))

    # Without this, gates 20-22 are equally satisfied by a walk that reached
    # nothing at all -- the pruning has to be visible as a number.
    check("25 pruned paths are counted, not silently dropped",
          len(pruned) == 1 and pruned[0].endswith(denied),
          "%d pruned" % len(pruned))

    try:
        list(walk(root, deny=(root,)))
        check("26 a root that is itself denied is refused", False, "no error")
    except DeniedRoot:
        check("26 a root that is itself denied is refused", True,
              "DeniedRoot, store untouched")

    # Every gate above passes an explicit list, so all of them stay green if
    # the shipped default is emptied. The default is the thing that actually
    # runs, so it gets a gate of its own.
    required = {"~/GoogleDrive", "~/OneDrive", "~/.cache"}
    check("27 the shipped default denies the cloud drives and the cache",
          required <= set(DEFAULT_DENY),
          "missing %s" % (sorted(required - set(DEFAULT_DENY)) or "nothing"))

    # Stripping trailing separators turns "/" into "", and os.scandir("")
    # reports a single unreadable row rather than raising -- a scan of the
    # filesystem root would answer "one entry" and no error. Checked on the
    # helper: observing it through a walk means walking the whole filesystem.
    norm = {r: _normalise_root(r) for r in ("/", "/home/nadi/", "/home/nadi")}
    check("28 normalising a root never empties it",
          norm["/"] == os.sep
          and norm["/home/nadi/"] == norm["/home/nadi"] == "/home/nadi",
          "%r -> %r" % ("/", norm["/"]))


def gates_vanishing(work):
    """R6 -- a file removed between listing and stat is skipped, not fatal.

    Planted rather than raced: `scandir` is made to return an entry whose file
    is already gone. Waiting for the real race to happen would be a gate that
    passes because it never fired.
    """
    tree = os.path.join(work, "vanishing")
    os.makedirs(tree, exist_ok=True)
    for n in range(5):
        with open(os.path.join(tree, "f%d" % n), "w") as fh:
            fh.write("x")

    import morpho_homegraph.scan as scanmod
    real_scandir = os.scandir

    class Ghost:
        """An entry whose file disappeared after the listing was taken."""
        path = os.path.join(tree, "gone")
        def stat(self, follow_symlinks=True):
            raise FileNotFoundError(2, "No such file or directory", self.path)
        def is_symlink(self): return False
        def is_dir(self, follow_symlinks=True): return False
        def is_file(self, follow_symlinks=True): return True

    class Listing:
        def __init__(self, it): self.it = it
        def __enter__(self): return list(self.it) + [Ghost()]
        def __exit__(self, *exc): return False

    scanmod.os.scandir = lambda path: Listing(real_scandir(path))
    try:
        rows = list(walk(tree))
        survived = True
    except OSError:
        rows = []
        survived = False
    finally:
        scanmod.os.scandir = real_scandir
    check("10 a file that vanishes mid-walk is skipped, not fatal",
          survived and len(rows) == 5,
          "%d rows, walk %s" % (len(rows), "returned" if survived else "RAISED"))


# -- 3, 5. the audit hook: what Python did ---------------------------------

def gates_audit_hook(tree):
    """R2, first detector. Blind to anything that opens without an audit event."""
    opened = []
    corpus = os.path.abspath(tree)

    def spy(event, args):
        if event == "open" and args and isinstance(args[0], str):
            if os.path.abspath(args[0]).startswith(corpus):
                opened.append(args[0])

    sys.addaudithook(spy)
    rows = list(walk(tree))
    files_opened = [p for p in opened
                    if not os.path.isdir(p)]
    check("3  the audit hook sees no file opened during a walk",
          not files_opened and len(rows) > 5,
          "%d rows, %d files opened" % (len(rows), len(files_opened)))

    # R3: the same hook, with a read deliberately performed. A detector that
    # cannot see a read it was pointed at is not a detector.
    target = next(r[0] for r in rows if r[1] == "file")
    with open(target, "rb") as fh:
        fh.read(16)
    check("5  the audit hook sees a read when there is one",
          any(os.path.abspath(p) == os.path.abspath(target) for p in opened),
          "%d opens recorded" % len(opened))


# -- 4, 6. strace: what the kernel was asked ------------------------------

def straced(script, tree):
    """(openat paths, whether strace ran). `None` when strace is unavailable."""
    if shutil.which("strace") is None:
        return None, False
    with tempfile.NamedTemporaryFile(suffix=".strace", delete=False) as tmp:
        log = tmp.name
    try:
        proc = subprocess.run(
            ["strace", "-f", "-e", "trace=openat", "-o", log,
             sys.executable, "-c", script % REPO, tree],
            capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return None, False
        with open(log, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    finally:
        os.unlink(log)
    # Only successful opens of paths inside the fixture. A failed openat is
    # the kernel refusing, not us reading.
    found = []
    for m in re.finditer(r'openat\([^,]+, "([^"]+)"[^)]*\)\s*=\s*(-?\d+)', text):
        path, rc = m.group(1), int(m.group(2))
        if rc >= 0 and os.path.abspath(path).startswith(os.path.abspath(tree)):
            found.append(path)
    return found, True


def gates_strace(tree):
    opened, ran = straced(WALKER, tree)
    if not ran:
        # Not green and not silently skipped: a check that reports success
        # when it measured nothing is the empty gate this project is named
        # after.
        check("4  strace sees no file opened during a walk", False,
              "strace unavailable -- NOT RUN, not passed")
        check("6  strace can see an open at all", False,
              "strace unavailable -- NOT RUN, not passed")
        return
    files = [p for p in opened if not os.path.isdir(p)]
    check("4  strace sees no file opened during a walk",
          not files, "%d directory opens, %d file opens"
          % (len(opened) - len(files), len(files)))

    # R3 and the control for the measurement itself: the same command around
    # a walk that reads one file must see it. If it does not, gate 4 was
    # measuring an strace invocation that sees nothing.
    read_opens, ran = straced(READER, tree)
    read_files = [p for p in (read_opens or []) if not os.path.isdir(p)]
    check("6  strace can see an open at all",
          ran and len(read_files) >= 1,
          "%d file opens when the walk reads one" % len(read_files))


# -- 12. the scan is a write, and writes are guarded -----------------------

def l0_store():
    """The shared L0 store, created on demand. Returns its path."""
    path = l0_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def gates_store(tree):
    store_db = l0_store()
    guard = StoreLock(str(store_db)).acquire()
    try:
        with Store(store_db, role=L0) as store:
            # A scan that cannot run against the shared store is gate 14
            # failing. Letting it raise would take gates 15 to 19 with it and
            # report a crash instead of naming this one.
            try:
                summary = scan(store, tree)
                rows = store.db.execute(
                    "SELECT COUNT(*) FROM files").fetchone()[0]
                check("14 the scan records its count and its time",
                      rows == summary["count"] > 5
                      and float(store.get_meta("l0_seconds")) > 0,
                      "%d rows in %s s" % (rows, store.get_meta("l0_seconds")))
            except (WrongStore, sqlite3.Error) as exc:
                check("14 the scan records its count and its time", False,
                      "the shared store could not take L0: %r" % exc)
    finally:
        guard.release()

    # CP-0 R8 reaches here too: L0 is the first thing that writes a lot, and
    # it must not be the first thing that writes unguarded.
    #
    # The store is opened *while the guard is held* and the guard dropped
    # afterwards, so what is under test is the scan's write and not `Store`'s
    # open-time check. Opening unguarded is refused by CP-0 gate 21 already,
    # and a version of this gate that leaned on it stayed green with the
    # scan writing straight to the connection -- found by the sweep, 08-03.
    # Its own project, so the table starts empty and "was anything written"
    # is answerable. On the shared one it was not: the scan wrote its rows
    # unguarded and only died later, on `set_meta`, and the gate read that as
    # a refusal. Raising is not the property -- writing nothing is.
    fresh_db = data_home() / "l0-unguarded" / "index.db"
    fresh_db.parent.mkdir(parents=True, exist_ok=True)
    outlived = StoreLock(str(fresh_db)).acquire()
    fresh = Store(fresh_db, role=L0)
    outlived.release()
    try:
        scan(fresh, tree)
        refused = False
    except Unguarded:
        refused = True
    written = fresh.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    fresh.close()
    check("12 a scan without the write guard writes nothing",
          refused and written == 0,
          "%s, %d rows written" % ("refused" if refused else "ALLOWED", written))

    # R8: the layer is refreshed, not appended to. Nothing above sees this --
    # gate 11 compares two walks in memory, and a store that accumulates rows
    # across scans passes every one of them while reporting files that were
    # deleted a week ago.
    victim = os.path.join(tree, "b.md")
    guard = StoreLock(str(store_db)).acquire()
    try:
        with Store(store_db, role=L0) as store:
            try:
                before = scan(store, tree)["count"]
                os.remove(victim)
                after = scan(store, tree)["count"]
                rows = store.db.execute(
                    "SELECT COUNT(*) FROM files").fetchone()[0]
                stale = store.db.execute(
                    "SELECT COUNT(*) FROM files WHERE path = ?", (victim,)
                ).fetchone()[0]
                check("15 a rescan replaces the layer, it does not accumulate",
                      after == before - 1 and rows == after and stale == 0,
                      "%d -> %d rows, %d stale" % (before, after, stale))
            except (WrongStore, sqlite3.Error) as exc:
                # Same reason as gate 14 above: a scan that cannot run is this
                # gate failing, and letting it raise would take gates 16 to 19
                # with it and report a crash that names nothing.
                check("15 a rescan replaces the layer, it does not accumulate",
                      False, "the scan could not run: %r" % exc)
    finally:
        guard.release()


# -- 16-19. L0 is shared, and shared means one copy with its own guard ------

def gates_shared_l0(tree):
    """Decided 2026-08-03 after M-1 measured L0 at 204.8 MB.

    One copy per project would be that number times the number of projects,
    of byte-identical data. The gates below are what make "shared" a fact
    about the code rather than a sentence in the plan.
    """
    project_id, project_db = new_project()
    guard = StoreLock(str(project_db)).acquire()
    try:
        with Store(project_db, role=PROJECT) as store:
            tables = {r[0] for r in store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            # `scope` came with CP-3, `content` with CP-4, `edges` with CP-5. One per
            # checkpoint that adds a project-side table, and the equality is
            # what makes a table nobody declared show up as a failure -- it
            # has caught CP-2's `journal` and CP-3's `scope` already.
            check("16 a project store has no L0 table to fill by accident",
                  tables == {"meta", "scope", "content", "edges"}, "tables=%s" % sorted(tables))
            # Refused rather than merely undeclared: without the check, a
            # caller that created the table first would be allowed to fill it.
            #
            # Both refusals are caught so the harness cannot crash, but only
            # the role check passes the gate. The missing table is a backstop,
            # not the mechanism: accepting it would make this gate green with
            # the role check deleted -- measured, 2026-08-03, the mutation
            # survived exactly that way. What is under test is that the store
            # says *why*, not merely that nothing was written.
            try:
                scan(store, tree)
                how = "ALLOWED"
            except WrongStore:
                how = "refused by the role check"
            except sqlite3.Error as exc:
                how = "refused only by the missing table (%s)" % exc
            check("17 a scan aimed at a project store is refused by role",
                  how == "refused by the role check", how)
    finally:
        guard.release()

    # Created here rather than relying on an earlier block having built it.
    # These are schema gates and they run before any behaviour gate, so a
    # scan that cannot run cannot also hide the reason -- measured
    # 2026-08-03: with `files` moved to the project role, the mutation was
    # attributed to gate 14 because gate 18 never got to speak.
    l0_guard = StoreLock(str(l0_store())).acquire()
    try:
        with Store(l0_path(), role=L0) as store:
            tables = {r[0] for r in store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            # Grows one entry per checkpoint that adds a shared table --
            # `journal` arrived with CP-2 and this gate said so, which is what
            # an equality is for. A subset check would have let a table nobody
            # declared in through the same door.
            check("18 the shared L0 store is the one that has L0",
                  tables == {"meta", "files", "journal"},
                  "tables=%s" % sorted(tables))
    finally:
        l0_guard.release()

    # The two guards are separate because they are separate store paths, which
    # is why moving L0 out needed no change to decision 12. Without this, a
    # nightly L0 refresh would lock every project out of its own index.
    l0_guard = StoreLock(str(l0_path())).acquire()
    try:
        project_guard = StoreLock(str(project_db)).acquire()
        both = True
        project_guard.release()
    except Locked:
        both = False
    finally:
        l0_guard.release()
    check("19 an L0 refresh does not lock a project out", both)
    return db_path(project_id)


def main():
    with tempfile.TemporaryDirectory(prefix="mhg-cp1-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        tree = os.path.join(work, "tree")
        os.makedirs(tree)
        closed = build_tree(tree)
        try:
            gates_walk(tree, closed)
            gates_deny(work)
            gates_vanishing(work)
            gates_audit_hook(tree)
            gates_strace(tree)
            gates_shared_l0(tree)
            gates_store(tree)
        finally:
            # Without this the temporary directory cannot be removed, and the
            # harness leaves a 0o000 directory behind on every run.
            os.chmod(closed, 0o755)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp1():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
