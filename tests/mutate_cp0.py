#!/usr/bin/env python3
"""Mutation test for CP-0 -- the store and the session guard.

A refusal gate is the easiest thing here to write and the hardest to trust: it
can pass because the barrier works, because the second process failed for an
unrelated reason, or because the two never contended at all. So each mutation
below cuts one specific wire and names the gate that has to notice -- not "the
suite goes red", which a broken import also achieves.

Half of these aim at the **negative controls** rather than at the defects,
because that is where a barrier goes quietly wrong. A guard that refuses
everything passes every refusal gate in this file. A store that assigns
`journal_mode = "wal"` without asking passes every gate that reads the
attribute. A `writing()` that serialises nothing still lands every write.

Two mutations exist to prove the *rewrite* was worth it, not just that the
code works: `the payload decides liveness, not the kernel` puts the previous
design back, and gate 12 -- a holder killed with SIGKILL -- is what says no.

Run:
    python3 tests/mutate_cp0.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the barrier itself ---------------------------------------------
    #
    # The guard is registered but never actually taken: the store is happy,
    # every single-writer run is unchanged, and two writers proceed together.
    # This is what "the barrier is a policy nobody enforces" looks like from
    # the outside -- which is why it has to be tested from the outside.
    ("the guard is registered but never taken",
     "morpho_homegraph/cli.py",
     "    return StoreLock(str(store_db)).acquire()",
     "    from .lock import _HELD  # mutated: registered, never locked\n"
     "    _HELD.add(str(store_db))\n"
     "    return StoreLock(str(store_db))",
     "9  a second writer is refused"),

    ("the guard queues instead of refusing",
     "morpho_homegraph/lock.py",
     "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
     "            fcntl.flock(fd, fcntl.LOCK_EX)  # mutated: wait, do not refuse",
     "9  a second writer is refused"),

    ("the store file is created before the barrier",
     "morpho_homegraph/cli.py",
     "    barrier = _guard_or_refuse(store_db)\n"
     "    if barrier is None:\n"
     "        return 2\n"
     "    try:\n"
     "        with Store(store_db) as store:",
     "    import sqlite3; sqlite3.connect(store_db).close()  # mutated\n"
     "    barrier = _guard_or_refuse(store_db)\n"
     "    if barrier is None:\n"
     "        return 2\n"
     "    try:\n"
     "        with Store(store_db) as store:",
     "8b a refused writer creates no store"),

    # -- the refusal has to be a fact the caller can act on --------------
    ("the refusal no longer names the holder",
     "morpho_homegraph/cli.py",
     '        print("REFUSED  %s\\n(waiting is not offered, and asking that process "\n'
     '              "to do the job is not built: re-run when it is done)" % exc,\n'
     '              file=sys.stderr)',
     '        print("REFUSED  the store is busy", file=sys.stderr)  # mutated',
     "9b the refusal names the holding pid"),

    ("the refusal stops saying there is no queue",
     "morpho_homegraph/lock.py",
     '            "pid %s owns writing to this store, since %s"\n'
     '            % (holder.get("pid", "?"), holder.get("created", "?")))',
     '            "the store is busy (pid %s, %s)"  # mutated\n'
     '            % (holder.get("pid", "?"), holder.get("created", "?")))',
     "10 the refusal says who owns writing"),

    # -- the kernel decides, not a file we wrote --------------------------
    #
    # This puts the previous design back: a payload on disk decides whether
    # someone holds the guard. Every polite exit still works, because a
    # process that releases cleanly rewrites the file. Only a holder that
    # died without the chance shows it.
    ("the payload decides liveness, not the kernel",
     "morpho_homegraph/lock.py",
     "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
     "            if self.read_holder().get(\"pid\") not in (None, os.getpid()):\n"
     "                raise BlockingIOError(\"mutated: the file decides\")",
     "12 a SIGKILLed holder does not block the next writer"),

    # There is deliberately no mutation for gate 15 either, and for a better
    # reason than gate 4's: the payload is not consulted when deciding
    # anything, so "an unreadable payload blocks nobody" is now true by
    # construction. The first attempt at a mutation here made `read_holder`
    # raise -- and changed nothing, because it is only ever called after the
    # kernel has already refused someone. That is trap 2 in the plan: a
    # property that started holding by construction looks stronger in the
    # diff and proves less. Gate 15 stays as the thing that would catch
    # someone reintroducing payload-based decisions, and is recorded here as
    # unreddenable rather than left to look like coverage.

    # -- the lock file must not be deleted, and must not be written early --
    ("the lock file is deleted on release",
     "morpho_homegraph/lock.py",
     "            fcntl.flock(self.fd, fcntl.LOCK_UN)\n"
     "            os.close(self.fd)",
     "            fcntl.flock(self.fd, fcntl.LOCK_UN)\n"
     "            os.close(self.fd)\n"
     "            os.unlink(self.path)  # mutated: delete it, race the inode",
     "18 a clean run leaves the lock file, but free"),

    ("the payload is written before the lock is won",
     "morpho_homegraph/lock.py",
     "        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)\n"
     "        try:",
     "        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)\n"
     "        os.ftruncate(fd, 0)  # mutated: announce before winning\n"
     "        os.write(fd, self._payload())\n"
     "        try:",
     "11 a refused writer leaves the holder's payload alone"),

    # Named gate 8, not 19, and the reason is worth keeping: a guard that is
    # never released shows up first as the *next* writer being refused, not
    # as the release itself failing. Gate 19 would also go red -- but only if
    # the suite reached it, and under this mutation the harness is already
    # holding a guard it thinks it dropped.
    ("the guard is never released",
     "morpho_homegraph/lock.py",
     "        if not self.held:\n"
     "            return\n"
     "        self.held = False\n"
     "        _HELD.discard(self.store_path)",
     "        if not self.held:\n"
     "            return\n"
     "        return  # mutated: hold it for ever",
     "8  a lone writer is not refused"),

    # -- the store enforces the guard, it does not assume it --------------
    ("the store stops asking whether the guard is held",
     "morpho_homegraph/lock.py",
     "    return str(store_path) in _HELD",
     "    return True  # mutated: everyone holds everything",
     "21 a writable store without the guard is refused"),

    ("releasing leaves the store still believing it is guarded",
     "morpho_homegraph/lock.py",
     "        _HELD.discard(self.store_path)",
     "        pass  # mutated: registration outlives the lock",
     "21 a writable store without the guard is refused"),

    ("writes are checked at open but never again",
     "morpho_homegraph/store.py",
     "        if not holds(self.path):\n"
     "            raise Unguarded(self.path)\n"
     "        with self._write_lock:",
     "        with self._write_lock:  # mutated: open-time check only",
     "21b a store handle that outlives its guard cannot write"),

    # -- a guard that cannot be exclusive is not handed out ----------------
    #
    # The whole class this refuses is silent: over NFS with `local_lock`, the
    # lock is taken, granted, and visible to exactly one machine. Nothing
    # fails. Two machines write the same index.
    ("the store is guarded wherever it happens to live",
     "morpho_homegraph/lock.py",
     "        if fstype not in LOCAL_FILESYSTEMS and os.environ.get(ALLOW_REMOTE) != \"1\":\n"
     "            raise NotLocal(self.store_path, fstype)",
     "        pass  # mutated: guard anything, anywhere",
     "25 a store we cannot guard exclusively is refused"),

    ("the override is ignored, so nothing can be overridden",
     "morpho_homegraph/lock.py",
     "        if fstype not in LOCAL_FILESYSTEMS and os.environ.get(ALLOW_REMOTE) != \"1\":",
     "        if fstype not in LOCAL_FILESYSTEMS:  # mutated: no way out",
     "25b the override is honoured when it is set"),

    ("a mount point matches on string prefix, not at a separator",
     "morpho_homegraph/lock.py",
     '        if target != point and not target.startswith(point.rstrip("/") + "/"):\n'
     "            continue",
     "        if not target.startswith(point):  # mutated: /home eats /homegraph\n"
     "            continue",
     "23c a mount point matches at a separator, not a prefix"),

    ("the first matching mount wins, not the longest",
     "morpho_homegraph/lock.py",
     "        if len(point) >= len(best_point):\n"
     "            best_point, best_type = point, fstype",
     "        if not best_type:  # mutated: first match wins\n"
     "            best_point, best_type = point, fstype",
     "23 an NFS mount is not a filesystem we can guard"),

    # -- readers must not be caught by the barrier ------------------------
    ("readers take the guard too",
     "morpho_homegraph/cli.py",
     '    """Reader. Takes no lock, creates no store, answers while a writer writes."""',
     '    StoreLock(str(db_path(_resolve(args.project)))).acquire()  # mutated',
     "16 a reader answers while a writer holds the guard"),

    # -- WAL has to be in force, not merely assigned ----------------------
    #
    # There is deliberately no mutation for `busy_timeout`. Measured
    # 2026-08-03: 5000 ms is `sqlite3.connect`'s own default, so no edit to
    # this package can move the value the gate reads -- a mutation that
    # removed the setting entirely still left the connection at 5000. Gate 4
    # is therefore a recorded value that would catch someone *changing* the
    # number, not a gate this harness can redden. Written down rather than
    # left as a permanent survivor, because a survivor list nobody can act on
    # is how a real one gets ignored.
    ("the journal mode is claimed rather than read",
     "morpho_homegraph/store.py",
     '            else "PRAGMA journal_mode = WAL").fetchone()\n'
     '        self.journal_mode = (row[0] if row else "unknown").lower()',
     '            else "PRAGMA journal_mode = DELETE").fetchone()\n'
     '        self.journal_mode = "wal"  # mutated: claim it without asking',
     "3  WAL is in force on a local file"),

    # -- identity ---------------------------------------------------------
    #
    # The first of these was found in review rather than by a gate, which is
    # why the gate was written afterwards and this mutation exists to prove
    # it can say no.
    ("any existing directory is accepted as an id",
     "morpho_homegraph/cli.py",
     "    if candidate.parent == data_home() and candidate.is_dir():",
     "    if candidate.is_dir():  # mutated: a path passes as an id",
     "7c a path is never accepted as an id"),

    ("no argument resolves at all",
     "morpho_homegraph/cli.py",
     "    if len(hits) == 1:\n"
     "        return hits[0]",
     "    if False:  # mutated: a path never resolves\n"
     "        return hits[0]",
     "7e a path indexed once resolves to its id"),

    ("an ambiguous path picks the first index",
     "morpho_homegraph/cli.py",
     '    raise SystemExit("%s is indexed %d times: %s -- name the id"\n'
     '                     % (value, len(hits), ", ".join(hits)))',
     "    return hits[0]  # mutated: choose rather than refuse",
     "7d a path indexed twice is refused, not chosen"),

    ("the id is a constant, not generated",
     "morpho_homegraph/store.py",
     '    project_id = secrets.token_hex(8)',
     '    project_id = "deadbeefdeadbeef"  # mutated: not generated',
     "5  two adds of one path give two ids"),

    ("a project's path becomes a key",
     "morpho_homegraph/store.py",
     '    "files": (L0,',
     '    "registry": (L0, PROJECT,  # mutated: the reverse index CP-6 forbids\n'
     '                 "CREATE TABLE IF NOT EXISTS registry ("\n'
     '                 "  project_path TEXT PRIMARY KEY, id TEXT NOT NULL)"),\n'
     '    "files": (L0,',
     "7b no project's path is a key"),

    ("L0 is given to every store, not only the shared one",
     "morpho_homegraph/store.py",
     '    "files": (L0,',
     '    "files": (L0, PROJECT,  # mutated: every project carries a copy',
     "1b a project store has exactly the tables projects declare"),

    ("the schema version is never written",
     "morpho_homegraph/store.py",
     "        if current < SCHEMA_VERSION:\n"
     "            self.set_meta(\"schema_version\", str(SCHEMA_VERSION))\n"
     "            current = SCHEMA_VERSION",
     "        if current < SCHEMA_VERSION:\n"
     "            current = SCHEMA_VERSION  # mutated: version never stored",
     "1  the store opens and migrates"),

    # -- one process queues itself ----------------------------------------
    #
    # Both writes still land, every exit code stays 0, and two threads share
    # a cursor. Only a gate that counts concurrency can see it.
    ("writes inside one process are not serialised",
     "morpho_homegraph/store.py",
     "        self._write_lock = threading.RLock()",
     "        self._write_lock = contextlib.nullcontext()  # mutated: no queue",
     "20 writes inside one process are serialised"),

    # -- the negative controls have to be able to fail --------------------
    #
    # If a lone writer is refused, every refusal gate above is measuring
    # something other than contention.
    ("every writer is refused, contended or not",
     "morpho_homegraph/cli.py",
     "    try:\n"
     "        return _guard(store_db)",
     "    try:\n"
     "        raise Locked(str(store_db), {})  # mutated: refuse unconditionally\n"
     "        return _guard(store_db)",
     "8  a lone writer is not refused"),

    # Dropped 2026-08-03: removing `_HELD.add` was meant to be the negative
    # control for the registration, but it does not produce a silent defect --
    # every command dies with a named `Unguarded` before any gate runs, which
    # is a broken build rather than a barrier that quietly stopped working.
    # Mutations are for defects that survive a green suite. The registration's
    # negative control is "the store stops asking whether the guard is held",
    # above, which gate 21 kills.
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp0.py", prefix="mut0-", timeout=900))
