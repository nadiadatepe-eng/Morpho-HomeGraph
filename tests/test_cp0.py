#!/usr/bin/env python3
"""CP-0 -- shell, store and the session guard.

The answer key is `tests/gold/FASIT-cp0.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

Three things this harness does on purpose:

  * **Two real processes, not two objects.** The holder is a separate
    interpreter using the production `StoreLock`. Two objects in one process
    share a view of the world that a barrier existing only in memory would
    also satisfy.
  * **A holder is killed with `SIGKILL`, not asked to leave.** The whole
    reason the guard is a kernel lock is that nothing has to clean up after a
    process that died without a chance to. A gate that only ever ends holders
    politely never tests that.
  * **The negative controls are the load-bearing part.** A lone writer must
    exit 0 (gate 8) or every refusal gate is measuring something other than
    contention, and a reader must answer while the guard is held (gate 16) or
    the barrier is allowed to be a lock that blocks everything.

Run:
    python3 tests/test_cp0.py
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.lock import (  # noqa: E402
    ALLOW_REMOTE, LOCAL_FILESYSTEMS, Locked, NotLocal, StoreLock, Unguarded,
    filesystem_of)
from morpho_homegraph.store import (  # noqa: E402
    BUSY_TIMEOUT_MS, SCHEMA_VERSION, Store, db_path, projects)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(52)

# A second interpreter that takes the guard with the production class and
# holds it until told to stop -- or until it is killed. Writing the lock file
# by hand would test the payload format rather than the barrier.
HOLDER = """
import os, sys
sys.path.insert(0, %r)
from morpho_homegraph.lock import StoreLock
lock = StoreLock(sys.argv[1]).acquire()
print(os.getpid(), flush=True)
sys.stdin.readline()
lock.release()
""" % REPO


class holding:
    """Context manager: a real other process holding the guard on `db`."""

    def __init__(self, db):
        self.db = str(db)

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, self.db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=REPO)
        line = (self.proc.stdout.readline() or "").strip()
        if not line:
            raise RuntimeError("holder did not start: %s"
                               % self.proc.stderr.read())
        self.pid = int(line)
        return self

    def kill(self):
        """End the holder the way a crash does: no cleanup, no chance to run."""
        self.proc.kill()
        self.proc.wait(timeout=10)

    def __exit__(self, *exc):
        if self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write("go\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.proc.kill()


class TimedOut:
    """What a command that never returned looks like to a gate.

    A guard that queues instead of refusing does not fail -- it waits, and a
    bare `subprocess.run` would take the whole harness down with a
    `TimeoutExpired` before any gate could say why. Reported as its own exit
    code so "it blocked" reaches the gate as an answer rather than a crash.
    """
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=30):
    """Run `morphofiles-graph <argv>` as its own process."""
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def read_payload(db):
    """The lock file's payload; `{}` when absent or unparseable."""
    try:
        with open(str(db) + ".lock", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def take(db, on_refusal):
    """The guard, or None with `on_refusal` reported red.

    A harness that lets `Locked` escape turns a mutation into a crash, and a
    crash names no gate. Every acquisition in this file that is *expected* to
    succeed goes through here, so a guard that has started refusing everything
    is a red check rather than a stack trace.
    """
    try:
        return StoreLock(str(db)).acquire()
    except Locked as exc:
        check(on_refusal, False, "the guard refused: %s" % exc)
        return None


def second_guard(db, seconds=5):
    """'refused' | 'blocked' | 'acquired' for a second guard on `db`.

    `blocked` is a separate answer from `refused` on purpose: a guard that
    queues does not fail, it waits, and without the alarm the whole harness
    hangs until the mutation driver's timeout -- which reports a crash and
    names no gate. The handler raises, so the interrupted `flock` propagates
    rather than being retried (PEP 475).
    """
    def ring(_sig, _frame):
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, ring)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        StoreLock(str(db)).acquire()
        return "acquired"
    except Locked:
        return "refused"
    except TimeoutError:
        return "blocked"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def add_project(tree):
    proc = cli("add", str(tree))
    if proc.returncode != 0:
        raise RuntimeError("add failed: %s" % proc.stderr)
    return proc.stdout.split()[0]


# -- 1-4. the store opens, migrates, and says what it actually got ----------

def gates_store(tree):
    project = add_project(tree)
    db = db_path(project)

    with Store(db, read_only=True) as store:
        first = store.get_meta("schema_version")
        check("1  the store opens and migrates",
              first == str(SCHEMA_VERSION), "schema_version=%s" % first)
        tables = {r[0] for r in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # A *project* store, so `files` is deliberately absent: L0 is shared
        # and lives in its own store (decided 2026-08-03 after M-1 measured it
        # at 204.8 MB -- one copy per project would be that, times the number
        # of projects, of identical data). An equality rather than a subset,
        # so a table nobody declared shows up as a failure rather than as
        # nothing.
        check("1b a project store has exactly the tables projects declare",
              tables == {"meta", "scope"}, "tables=%s" % sorted(tables))

    guard = take(db, "2  migrating twice changes nothing")
    if guard is None:
        check("3  WAL is in force on a local file", False, "not reached")
        check("4  busy_timeout is set at open", False, "not reached")
        return project, db

    # `try/finally`, not `with guard`: the guard is already held, and entering
    # it would take it a second time -- which the kernel refuses, correctly.
    try:
        with Store(db) as store:
            check("2  migrating twice changes nothing",
                  store.migrate() == SCHEMA_VERSION
                  and store.get_meta("schema_version") == first,
                  "schema_version=%s" % store.get_meta("schema_version"))
            # Read back, never claimed: on a network filesystem SQLite can
            # refuse WAL and hand back a different mode. The second connection
            # is what makes this a check on the file rather than on the
            # attribute -- a store that assigns `journal_mode = "wal"` without
            # asking passes any gate that only reads the attribute.
            elsewhere = sqlite3.connect(db)
            try:
                on_file = elsewhere.execute(
                    "PRAGMA journal_mode").fetchone()[0].lower()
            finally:
                elsewhere.close()
            check("3  WAL is in force on a local file",
                  store.journal_mode == "wal" and on_file == "wal",
                  "attribute=%s file=%s" % (store.journal_mode, on_file))
            asked = store.db.execute("PRAGMA busy_timeout").fetchone()[0]
            check("4  busy_timeout is set at open",
                  store.busy_timeout == BUSY_TIMEOUT_MS
                  and asked == BUSY_TIMEOUT_MS,
                  "attribute=%s connection=%s" % (store.busy_timeout, asked))
    finally:
        guard.release()
    return project, db


# -- 5-7. the id is generated, and the registry only goes id -> path --------

def gates_identity(tree):
    a = add_project(tree)
    b = add_project(tree)
    check("5  two adds of one path give two ids", a != b, "%s / %s" % (a, b))

    resolved = str(tree)
    # Three ways an id could be a function of the path, not one: a hash of it,
    # a substring of it, or a slug built from it. Testing only the first two
    # leaves `home-nadi-code` passing a gate named "not derived from the path"
    # -- found by codex 2026-08-03. The shape test is what closes the third:
    # `token_hex(8)` is 16 lowercase hex digits, and no slug of a real path is.
    derived = {h(resolved.encode()).hexdigest()
               for h in (hashlib.md5, hashlib.sha1, hashlib.sha256)}
    shaped = len(a) == 16 and all(c in "0123456789abcdef" for c in a)
    check("6  the id is not derived from the path",
          shaped and not any(d.startswith(a) or a in d for d in derived)
          and a not in resolved,
          "id=%s shape=%s" % (a, "16 hex" if shaped else "WRONG"))

    registry = dict(projects())
    check("7  the registry answers id -> path",
          registry.get(a) == os.path.realpath(resolved),
          "%s -> %s" % (a, registry.get(a)))
    with Store(db_path(a), read_only=True) as store:
        keyed = store.db.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index')"
        ).fetchall()
    # The direction is the guarantee: nothing anywhere is keyed by a path, so
    # no future caller can treat a path as an identity without walking every
    # project and meeting the ambiguity CP-6 exists to decide.
    #
    # Searching for the literal `project_path` was not enough -- a
    # `registry(path PRIMARY KEY, id)` table passes that and is exactly the
    # reverse index this rule forbids (codex, 2026-08-03).
    #
    # Narrowed 2026-08-03, argued in `tests/gold/FASIT-cp1.md`: "no DDL names
    # a path at all" was too broad and went red on correct code the moment
    # CP-1 added `files(path PRIMARY KEY)`. L0 *is* a path index -- nothing
    # else would serve as its key. The rule being guarded is CP-6's, and it is
    # about *a project's* path: `project_path` lives as a value in `meta` and
    # must never be a key or a unique column anywhere.
    keyed_by_project_path = [
        row[0] for row in keyed
        if "project_path" in (row[0] or "")
        and any(word in (row[0] or "").upper()
                for word in ("PRIMARY KEY", "UNIQUE", "CREATE INDEX"))]
    check("7b no project's path is a key",
          not keyed_by_project_path,
          "%d schema objects, %d keyed by a project path"
          % (len(keyed), len(keyed_by_project_path)))


# -- 7c-7e. an argument is an id or a path, and never both -----------------

def gates_resolution(work, tree):
    """Found in review, not by a gate: joining an absolute path throws the left
    side away, so `data_home() / "/home/nadi"` is `/home/nadi`. A test that
    only ever passes real ids never meets it, and the cost is an index.db
    written inside whatever directory the user named.
    """
    outsider = os.path.join(work, "not-a-project")
    os.makedirs(outsider, exist_ok=True)
    proc = cli("update", outsider)
    planted = os.path.join(outsider, "index.db")
    check("7c a path is never accepted as an id",
          proc.returncode != 0 and not os.path.exists(planted),
          "exit %d, %s" % (proc.returncode,
                           "INDEX PLANTED" if os.path.exists(planted) else "nothing written"))

    # `tree` was added twice by the gates above, so by CP-6's rule it is a
    # question rather than an answer.
    proc = cli("status", str(tree))
    check("7d a path indexed twice is refused, not chosen",
          proc.returncode != 0 and "indexed" in (proc.stderr + proc.stdout),
          proc.stderr.strip().splitlines()[:1])

    # Positive control: without it, 7c and 7d are satisfied by a resolver that
    # refuses everything.
    single = os.path.join(work, "single")
    os.makedirs(single, exist_ok=True)
    project = add_project(single)
    proc = cli("status", single)
    check("7e a path indexed once resolves to its id",
          proc.returncode == 0 and project in proc.stdout, "exit %d" % proc.returncode)


# -- 8-11. one writer at a time, and the refusal is a fact -----------------

def gates_barrier(tree, project, db):
    for i in (1, 2, 3):
        proc = cli("update", project)
        if proc.returncode != 0:
            check("8  a lone writer is not refused", False,
                  "run %d exited %d: %s" % (i, proc.returncode, proc.stderr.strip()))
            break
    else:
        check("8  a lone writer is not refused", True, "3 sequential runs exit 0")

    # A holder that cannot start is these four gates failing, not the harness
    # dying: a crash names no gate, and a mutation attributed to nothing is a
    # mutation that taught us nothing.
    try:
        with holding(db) as holder:
            proc = cli("update", project)
            check("9  a second writer is refused",
                  proc.returncode == 2, "exit %d" % proc.returncode)
            check("9b the refusal names the holding pid",
                  str(holder.pid) in proc.stderr,
                  "pid %d in stderr: %s"
                  % (holder.pid, str(holder.pid) in proc.stderr))
            check("10 the refusal says who owns writing and that there is no queue",
                  "REFUSED" in proc.stderr and "owns writing" in proc.stderr
                  and "waiting is not offered" in proc.stderr,
                  proc.stderr.strip().splitlines()[:1])
            check("11 a refused writer leaves the holder's payload alone",
                  read_payload(db).get("pid") == holder.pid,
                  "payload names pid %s" % read_payload(db).get("pid"))
    except RuntimeError as exc:
        for gate in ("9  a second writer is refused",
                     "9b the refusal names the holding pid",
                     "10 the refusal says who owns writing and that there is no queue",
                     "11 a refused writer leaves the holder's payload alone"):
            check(gate, False, "no holder could take the guard: %s" % exc)

    # R1: the guard is taken before the store is opened. `sqlite3.connect`
    # creates the file it does not find, so a writer that opens first leaves a
    # store behind every time it is refused -- with the right exit code, which
    # is why no refusal gate above can see it. The state planted here is real:
    # a project directory whose database was deleted. It gets its own project,
    # because deleting the one every later gate reads from would make those
    # gates measure the deletion instead.
    scratch = add_project(tree)
    scratch_db = db_path(scratch)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(str(scratch_db) + suffix):
            os.remove(str(scratch_db) + suffix)
    try:
        with holding(scratch_db):
            proc = cli("update", scratch)
            check("8b a refused writer creates no store",
                  proc.returncode == 2 and not os.path.exists(str(scratch_db)),
                  "exit %d, store %s"
                  % (proc.returncode,
                     "created" if os.path.exists(str(scratch_db)) else "absent"))
    except RuntimeError as exc:
        check("8b a refused writer creates no store", False,
              "no holder could take the guard: %s" % exc)
    proc = cli("update", scratch)
    check("8c the store comes back on the next run",
          proc.returncode == 0 and os.path.exists(str(scratch_db)),
          "exit %d" % proc.returncode)

    # R5: the lock file is never deleted, so "clean run" cannot mean "no file
    # left". What has to be true is that the file is *free* -- and the only
    # honest way to ask is to take it.
    survivor = os.path.exists(str(db) + ".lock")
    free = StoreLock(str(db))
    try:
        free.acquire()
        taken = True
    except Locked:
        taken = False
    finally:
        free.release()
    check("18 a clean run leaves the lock file, but free",
          survivor and taken,
          "file %s, next holder %s" % ("kept" if survivor else "DELETED",
                                       "took it" if taken else "REFUSED"))


# -- 12, 15. nothing has to clean up after a process that died -------------

def gates_death(project, db):
    """The gate the whole rewrite is for.

    Under the previous design a holder that died had to be *detected* -- pid
    plus boot-relative start time, then an unlink -- and two processes doing
    that detection at once could both end up holding the guard. Here the
    kernel drops the lock when the process dies, so there is nothing to
    detect and nothing to clear. `SIGKILL` is the point: it is the one exit
    that leaves a holder no chance to tidy up.
    """
    # A holder that cannot start is this gate failing, not the harness dying.
    # Under a mutation that lets a file decide who holds the guard, nobody can
    # take it after the first writer -- and a crash there would attribute the
    # mutation to whatever gate happened to run last.
    try:
        with holding(db) as holder:
            killed_pid = holder.pid
            holder.kill()
            proc = cli("update", project)
            check("12 a SIGKILLed holder does not block the next writer",
                  proc.returncode == 0,
                  "killed pid %d, next writer exit %d"
                  % (killed_pid, proc.returncode))
    except RuntimeError as exc:
        check("12 a SIGKILLed holder does not block the next writer", False,
              "no holder could take the guard: %s" % exc)

    # The payload is not the lock. A corrupt one can name nobody and must
    # still not stand between a writer and a store nobody is holding.
    with open(str(db) + ".lock", "w", encoding="utf-8") as fh:
        fh.write("{not json")
    proc = cli("update", project)
    check("15 an unreadable payload blocks nobody",
          proc.returncode == 0, "exit %d" % proc.returncode)


# -- 16-17. readers are not caught by the barrier --------------------------

def gates_reader(project, db):
    try:
        with holding(db):
            proc = cli("status", project)
            check("16 a reader answers while a writer holds the guard",
                  proc.returncode == 0 and "journal_mode" in proc.stdout,
                  "exit %d" % proc.returncode)
            check("16b the reader is reading this store",
                  project in proc.stdout,
                  "id echoed" if project in proc.stdout else "")
    except RuntimeError as exc:
        check("16 a reader answers while a writer holds the guard", False,
              "no holder could take the guard: %s" % exc)
        check("16b the reader is reading this store", False, "not reached")

    if os.path.exists(str(db) + ".lock"):
        os.remove(str(db) + ".lock")
    proc = cli("status", project)
    check("17 a reader takes no guard",
          proc.returncode == 0 and not os.path.exists(str(db) + ".lock"),
          "exit %d, lock file %s"
          % (proc.returncode,
             "CREATED" if os.path.exists(str(db) + ".lock") else "not created"))


# -- 23-24. a guard that cannot be exclusive is not handed out -------------

MOUNTINFO = (
    "23 28 0:22 / / rw,relatime shared:1 - xfs /dev/nvme0n1p5 rw\n"
    "31 23 0:31 / /home rw,relatime shared:2 - nfs4 srv:/export rw,local_lock=all\n"
    "44 23 0:44 / /homegraph rw,relatime shared:3 - ext4 /dev/sdb1 rw\n"
    # The line that makes gate 23c able to fail. `/home` versus `/homegraph`
    # does not: longest-match rescues a string-prefix test there, so the gate
    # passed either way and the mutation survived. What separates the two
    # rules is a mount point that is a *longer* string prefix of the target
    # while not being a path prefix at all -- found by the sweep, 2026-08-03.
    "55 23 0:55 / /homegraph/re rw,relatime shared:4 - nfs4 srv:/trap rw\n"
)


def gates_locality():
    """`flock` over NFS can be local to one machine without saying so.

    That is the failure this refuses: the barrier looks like a barrier and two
    machines write the same index. The mount table is handed in rather than
    read, because a check that only runs on a machine with the wrong
    filesystem is a check nobody runs.
    """
    check("23 an NFS mount is not a filesystem we can guard",
          filesystem_of("/home/nadi/.local/share/x", MOUNTINFO) == "nfs4",
          filesystem_of("/home/nadi/.local/share/x", MOUNTINFO))
    check("23b a local mount is recognised as local",
          filesystem_of("/var/lib/x", MOUNTINFO) in LOCAL_FILESYSTEMS,
          filesystem_of("/var/lib/x", MOUNTINFO))
    # `/home` must not swallow `/homegraph`. Longest *path-boundary* match,
    # not longest string prefix -- the two differ exactly here.
    check("23c a mount point matches at a separator, not a prefix",
          filesystem_of("/homegraph/repo", MOUNTINFO) == "ext4",
          filesystem_of("/homegraph/repo", MOUNTINFO))
    # A machine with no /proc cannot prove locality, and unprovable is refused
    # rather than assumed -- the same rule as everywhere else here.
    check("23d an unidentifiable filesystem is not local",
          filesystem_of("/somewhere", "") not in LOCAL_FILESYSTEMS,
          "fstype=%r" % filesystem_of("/somewhere", ""))

    # And the refusal has to be actionable: it names the override and the fix.
    text = str(NotLocal("/x/index.db", "nfs4"))
    check("24 the refusal names the fix and the override",
          "MORPHO_HOMEGRAPH_HOME" in text and ALLOW_REMOTE in text
          and "nfs4" in text,
          text[:60] + "...")


def gates_locality_policy(db):
    """The lookup answering correctly is not the same as the guard acting on it.

    Faked by narrowing the allowlist rather than by finding a real NFS mount:
    the decision under test is the refusal, and a gate that can only run on a
    machine with the wrong filesystem never runs.
    """
    import morpho_homegraph.lock as lockmod
    original = lockmod.LOCAL_FILESYSTEMS
    lockmod.LOCAL_FILESYSTEMS = frozenset()
    was_set = os.environ.pop(ALLOW_REMOTE, None)
    try:
        guard = StoreLock(str(db))
        try:
            guard.acquire()
            verdict = "acquired"
        except NotLocal:
            verdict = "refused"
        finally:
            guard.release()
        check("25 a store we cannot guard exclusively is refused",
              verdict == "refused", verdict)

        # Negative control: without it, gate 25 is satisfied by a guard that
        # refuses every store on every filesystem.
        os.environ[ALLOW_REMOTE] = "1"
        override = StoreLock(str(db))
        try:
            override.acquire()
            honoured = "acquired"
        except NotLocal:
            honoured = "still refused"
        finally:
            override.release()
        check("25b the override is honoured when it is set",
              honoured == "acquired", honoured)
    finally:
        lockmod.LOCAL_FILESYSTEMS = original
        os.environ.pop(ALLOW_REMOTE, None)
        if was_set is not None:
            os.environ[ALLOW_REMOTE] = was_set


# -- 19-22. the guard's edges ----------------------------------------------

def gates_lifetime(db):
    lock = StoreLock(str(db))
    try:
        with lock:
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    after = StoreLock(str(db))
    try:
        after.acquire()
        released = True
    except Locked:
        released = False
    finally:
        after.release()
    check("19 KeyboardInterrupt releases the guard", released)

    # R8. Opening writable is already a write -- it sets the journal mode and
    # migrates -- so this is refused at open, not at the first statement.
    try:
        Store(db).close()
        refused = False
    except Unguarded:
        refused = True
    check("21 a writable store without the guard is refused", refused)

    # The check at open is not enough on its own: a `Store` object can outlive
    # the guard that was held when it was opened, and a handle that goes on
    # writing after another process has taken over is the same collision by a
    # slower route.
    outlived = StoreLock(str(db)).acquire()
    store = Store(db)
    outlived.release()
    try:
        store.set_meta("after the guard went", "1")
        still_writes = True
    except Unguarded:
        still_writes = False
    finally:
        store.close()
    check("21b a store handle that outlives its guard cannot write",
          not still_writes)

    # One session, one guard, including against itself. The kernel does not
    # know or care that the second holder is us. Three answers, not two:
    # "blocked" is what a guard that queues looks like, and it has to reach
    # the report rather than hang the harness.
    try:
        held = StoreLock(str(db)).acquire()
    except Locked as exc:
        check("22 a second guard in the same process is refused", False,
              "the first guard was already refused: %s" % exc)
        check("20 writes inside one process are serialised, not refused",
              False, "not reached")
        return
    second = second_guard(db)
    check("22 a second guard in the same process is refused",
          second == "refused", second)

    # Inside one process writes queue; they do not refuse. Concurrency is
    # counted rather than inferred from "both writes landed" -- both writes
    # land with no lock at all, which is exactly the state this gate exists
    # to tell apart.
    peak = [0]
    inside = [0]
    seen = threading.Lock()

    def writer(n):
        with store.writing():
            with seen:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.01)
            with seen:
                inside[0] -= 1
            store.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("thread_%d" % n, str(n)))
            store.db.commit()

    try:
        with Store(db) as store:
            threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            landed = sum(1 for n in range(4)
                         if store.get_meta("thread_%d" % n) == str(n))
    finally:
        held.release()
    check("20 writes inside one process are serialised, not refused",
          peak[0] == 1 and landed == 4,
          "peak concurrency %d, %d/4 writes landed" % (peak[0], landed))


def main():
    with tempfile.TemporaryDirectory(prefix="mhg-cp0-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        tree = os.path.join(work, "tree")
        os.makedirs(tree)

        project, db = gates_store(tree)
        gates_identity(tree)
        gates_resolution(work, tree)
        gates_barrier(tree, project, db)
        gates_death(project, db)
        gates_reader(project, db)
        gates_locality()
        gates_locality_policy(db)
        gates_lifetime(db)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp0():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
