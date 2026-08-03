#!/usr/bin/env python3
"""CP-0 -- shell, store and the write barrier.

The answer key is `tests/gold/FASIT-cp0.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

Two things this harness does on purpose:

  * **Two real processes, not two objects.** The holder is a separate
    interpreter using the production `StoreLock`. Two objects in one process
    share a `threading` view of the world and would pass a barrier that only
    exists in memory.
  * **The negative controls are the load-bearing part.** A lone writer must
    exit 0 (gate 8) or every refusal gate is measuring something other than
    contention, and a reader must answer while the lock is held (gate 16) or
    the barrier is allowed to be a lock that blocks everything.

Run:
    python3 tests/test_cp0.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.lock import Locked, StoreLock, _liveness  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    BUSY_TIMEOUT_MS, SCHEMA_VERSION, Store, db_path, projects)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(52)

# A second interpreter that takes the lock with the production class and holds
# it until told to stop. Writing the lock file by hand would test the format
# rather than the barrier.
HOLDER = """
import sys
sys.path.insert(0, %r)
from morpho_homegraph.lock import StoreLock
lock = StoreLock(sys.argv[1]).acquire()
print(lock.nonce, flush=True)
sys.stdin.readline()
lock.release()
""" % REPO


class holding:
    """Context manager: a real other process holding the lock on `db`."""

    def __init__(self, db):
        self.db = str(db)

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, self.db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=REPO)
        self.nonce = (self.proc.stdout.readline() or "").strip()
        if not self.nonce:
            raise RuntimeError("holder did not start: %s"
                               % self.proc.stderr.read())
        self.pid = self.proc.pid
        return self

    def __exit__(self, *exc):
        try:
            self.proc.stdin.write("go\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.proc.kill()


def cli(*argv, timeout=60):
    """Run `morphofiles-graph <argv>` as its own process."""
    return subprocess.run([sys.executable, "-m", "morpho_homegraph.cli", *argv],
                          capture_output=True, text=True, cwd=REPO,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def read_lock(db):
    """The lock file as a dict; `{}` when it is not there or will not parse."""
    try:
        with open(str(db) + ".lock", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def add_project(tree):
    proc = cli("add", str(tree))
    if proc.returncode != 0:
        raise RuntimeError("add failed: %s" % proc.stderr)
    return proc.stdout.split()[0]


# -- 1-4. the store opens, migrates, and says what it actually got ----------

def gates_store(tree):
    project = add_project(tree)
    db = db_path(project)

    with Store(db) as store:
        first = store.get_meta("schema_version")
        check("1  the store opens and migrates",
              first == str(SCHEMA_VERSION), "schema_version=%s" % first)
        tables = {r[0] for r in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        check("1b the schema is the one CP-0 declares",
              tables == {"meta"}, "tables=%s" % sorted(tables))

    with Store(db) as store:
        check("2  migrating twice changes nothing",
              store.migrate() == SCHEMA_VERSION
              and store.get_meta("schema_version") == first,
              "schema_version=%s" % store.get_meta("schema_version"))
        # Read back, never claimed: on a network filesystem SQLite can refuse
        # WAL and hand back a different mode. The second connection is what
        # makes this a check on the file rather than on the attribute -- a
        # store that assigns `journal_mode = "wal"` without asking passes any
        # gate that only reads the attribute.
        elsewhere = sqlite3.connect(db)
        try:
            on_file = elsewhere.execute("PRAGMA journal_mode").fetchone()[0].lower()
        finally:
            elsewhere.close()
        check("3  WAL is in force on a local file",
              store.journal_mode == "wal" and on_file == "wal",
              "attribute=%s file=%s" % (store.journal_mode, on_file))
        asked = store.db.execute("PRAGMA busy_timeout").fetchone()[0]
        check("4  busy_timeout is set at open",
              store.busy_timeout == BUSY_TIMEOUT_MS and asked == BUSY_TIMEOUT_MS,
              "attribute=%s connection=%s" % (store.busy_timeout, asked))
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
    # reverse index this rule forbids (codex, 2026-08-03). Any DDL naming a
    # path at all is the thing to refuse.
    paths_in_ddl = [row[0] for row in keyed if "path" in (row[0] or "").lower()]
    check("7b nothing is keyed by a path",
          not paths_in_ddl,
          "%d schema objects, %d naming a path" % (len(keyed), len(paths_in_ddl)))


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

    with holding(db) as holder:
        proc = cli("update", project)
        check("9  a second writer is refused",
              proc.returncode == 2, "exit %d" % proc.returncode)
        check("9b the refusal names the holding pid",
              str(holder.pid) in proc.stderr,
              "pid %d in stderr: %s" % (holder.pid, str(holder.pid) in proc.stderr))
        check("10 the refusal says who owns writing and that there is no queue",
              "REFUSED" in proc.stderr and "owns writing" in proc.stderr
              and "waiting is not offered" in proc.stderr,
              proc.stderr.strip().splitlines()[:1])
        # Read tolerantly: a missing lock file is this gate failing, not the
        # harness crashing. A mutation that lets the second writer clear the
        # holder's lock removes the file, and a bare `open` here would take
        # every later gate down with it -- turning a named refusal into an
        # unattributed crash.
        on_disk = read_lock(db)
        check("11 a refused writer leaves the holder's lock alone",
              on_disk.get("nonce") == holder.nonce,
              "nonce %s" % ("intact" if on_disk.get("nonce") == holder.nonce
                            else "REPLACED"))

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
    with holding(scratch_db):
        proc = cli("update", scratch)
        check("8b a refused writer creates no store",
              proc.returncode == 2 and not os.path.exists(str(scratch_db)),
              "exit %d, store %s"
              % (proc.returncode,
                 "created" if os.path.exists(str(scratch_db)) else "absent"))
    proc = cli("update", scratch)
    check("8c the store comes back on the next run",
          proc.returncode == 0 and os.path.exists(str(scratch_db)),
          "exit %d" % proc.returncode)

    check("18 a clean run leaves no lock behind",
          not os.path.exists(str(db) + ".lock"))


# -- 12-15. a lock is not a live process -----------------------------------

def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def gates_staleness(project, db):
    lock_path = str(db) + ".lock"
    stale_pid = dead_pid()
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump({"pid": stale_pid, "start": 1, "created": "earlier",
                   "nonce": "deadbeef", "store": str(db)}, fh)
    proc = cli("update", project)
    check("12 a lock from a dead process is recognised as stale",
          proc.returncode == 0, "exit %d: %s" % (proc.returncode, proc.stderr.strip()))
    check("13 clearing an orphan is announced, not silent",
          str(stale_pid) in proc.stderr and "stale" in proc.stderr,
          proc.stderr.strip().splitlines()[:1])

    # A pid that is alive but started later than the lock is a different
    # process wearing a dead one's number. This is the one that only shows up
    # once a pid has wrapped, which is to say never in a test that does not
    # plant it.
    mine = os.getpid()
    live, why = _liveness({"pid": mine, "start": 1})
    check("14 a live pid with the wrong start time is stale",
          not live, why)
    live, why = _liveness({"pid": mine, "start": _real_start(mine)})
    check("14b a live pid with the right start time is live", live, why)

    live, why = _liveness({"nonsense": True})
    check("15 an unparseable lock does not block a writer", not live, why)

    with open(lock_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    proc = cli("update", project)
    check("15b a writer gets past a corrupt lock file",
          proc.returncode == 0, "exit %d" % proc.returncode)


def _real_start(pid):
    with open("/proc/%d/stat" % pid, "rb") as fh:
        return int(fh.read().rsplit(b")", 1)[-1].split()[19])


# -- 16-17. readers are not caught by the barrier --------------------------

def gates_reader(project, db):
    with holding(db):
        proc = cli("status", project)
        check("16 a reader answers while a writer holds the lock",
              proc.returncode == 0 and "journal_mode" in proc.stdout,
              "exit %d" % proc.returncode)
        check("16b the reader is reading this store",
              project in proc.stdout, "id echoed" if project in proc.stdout else "")

    if os.path.exists(str(db) + ".lock"):
        os.remove(str(db) + ".lock")
    proc = cli("status", project)
    check("17 a reader takes no lock",
          proc.returncode == 0 and not os.path.exists(str(db) + ".lock"))


# -- 19-20. the lock outlives nothing, and one process queues itself -------

def gates_readback(db):
    """R5 -- whoever's nonce survives owns the lock.

    The window is between `O_CREAT|O_EXCL` succeeding and the writer reading
    the file back: another process can clear ours as an orphan and write its
    own into it, and we would hold a lock file that says someone else's name.
    Only the syscall is stubbed; the decision under test is the production
    `acquire`. Without a gate that plants this window, dropping the read-back
    changes no observable behaviour until the day it loses a store.
    """
    class racing(StoreLock):
        def _create(self):
            taken = super()._create()
            if taken:
                with open(self.path, "w", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(),
                               "start": _real_start(os.getpid()),
                               "created": "in the window",
                               "nonce": "someone else"}, fh)
            return taken

    try:
        racing(str(db)).acquire()
        verdict = "acquired"
    except Locked:
        verdict = "refused"
    check("11b a lock overwritten in the window is not ours",
          verdict == "refused", verdict)
    if os.path.exists(str(db) + ".lock"):
        os.remove(str(db) + ".lock")

    # The other half of the same contract, on the way out. A writer whose lock
    # was cleared as an orphan while it worked, and taken by a later writer,
    # must not unlink on release -- that would hand the new holder's store to
    # a third one. No refusal gate can see this: a refused writer never held
    # anything, so its `release` returns before reaching the check.
    mine = StoreLock(str(db)).acquire()
    with open(str(db) + ".lock", "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "start": _real_start(os.getpid()),
                   "created": "after ours was cleared",
                   "nonce": "a later writer"}, fh)
    mine.release()
    survivor = read_lock(db).get("nonce")
    check("18b release does not unlink a lock taken by someone else",
          survivor == "a later writer", "lock %s" % (survivor or "GONE"))
    if os.path.exists(str(db) + ".lock"):
        os.remove(str(db) + ".lock")


def gates_lifetime(db):
    lock = StoreLock(str(db))
    try:
        with lock:
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    check("19 KeyboardInterrupt releases the lock",
          not os.path.exists(str(db) + ".lock"))

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

    with Store(db) as store:
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        landed = sum(1 for n in range(4)
                     if store.get_meta("thread_%d" % n) == str(n))
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
        gates_staleness(project, db)
        gates_reader(project, db)
        gates_readback(db)
        gates_lifetime(db)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp0():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
