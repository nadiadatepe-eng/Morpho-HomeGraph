#!/usr/bin/env python3
"""Mutation test for CP-0 -- the store and the write barrier.

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

Run:
    python3 tests/mutate_cp0.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the barrier itself ---------------------------------------------
    #
    # The state the package would be in without CP-0: writers open the store
    # and go. Every single-writer behaviour is unchanged, which is why no
    # other checkpoint could ever see this.
    ("no guard is taken at all",
     "morpho_homegraph/cli.py",
     "    try:\n"
     "        barrier = _guard(store_db)\n"
     "    except Locked as exc:",
     "    try:\n"
     "        barrier = StoreLock(str(store_db))  # mutated: never acquired\n"
     "    except Locked as exc:",
     "9  a second writer is refused"),

    ("the second writer waits instead of refusing",
     "morpho_homegraph/lock.py",
     "            if live:\n"
     "                raise Locked(self.store_path, holder)",
     "            if live:\n"
     "                import time; time.sleep(0.05)  # mutated: queue, do not refuse\n"
     "                continue",
     "9  a second writer is refused"),

    ("the guard is taken after the store is opened",
     "morpho_homegraph/cli.py",
     "    store_db = db_path(_resolve(args.project))\n"
     "    try:\n"
     "        barrier = _guard(store_db)",
     "    store_db = db_path(_resolve(args.project))\n"
     "    Store(store_db).close()  # mutated: store first, barrier second\n"
     "    try:\n"
     "        barrier = _guard(store_db)",
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
     '            "pid %s owns writing to this store, since %s%s" % (pid, since, extra))',
     '            "the store is busy (pid %s, %s)%s" % (pid, since, extra))  # mutated',
     "10 the refusal says who owns writing"),

    # -- liveness: a pid is not a process --------------------------------
    #
    # Works perfectly until a pid is reused, then the store is unwritable
    # until someone deletes the file by hand. The gate plants that state.
    ("a live pid is enough; start time is not checked",
     "morpho_homegraph/lock.py",
     '    if now == recorded:\n'
     '        return True, "running"',
     '    if now is not None:\n'
     '        return True, "running"  # mutated: pid alone decides',
     "14 a live pid with the wrong start time is stale"),

    ("a dead holder's lock is treated as live",
     "morpho_homegraph/lock.py",
     '    if now is None:\n'
     '        return False, "no such process"',
     '    if now is None:\n'
     '        return True, "no such process"  # mutated',
     "12 a lock from a dead process is recognised as stale"),

    ("an unparseable lock blocks every writer",
     "morpho_homegraph/lock.py",
     '        return False, "unreadable lock file"',
     '        return True, "unreadable lock file"  # mutated',
     "15 an unparseable lock does not block a writer"),

    ("orphans are cleared in silence",
     "morpho_homegraph/lock.py",
     '            if self.on_stale is not None:\n'
     '                self.on_stale("cleared a stale lock left by pid %s (%s)"\n'
     '                              % (holder.get("pid", "?"), why))',
     "            if False:  # mutated: clear quietly\n"
     "                pass",
     "13 clearing an orphan is announced, not silent"),

    # -- the read-back, which nothing else can see ------------------------
    #
    # Dropping it changes no observable behaviour until two processes clear
    # the same orphan and the loser deletes the winner's fresh file.
    ("the nonce is never read back",
     "morpho_homegraph/lock.py",
     "                held = _read(self.path)\n"
     "                if held.get(\"nonce\") == self.nonce:\n"
     "                    self.held = True\n"
     "                    return self\n"
     "                raise Locked(self.store_path, held)",
     "                self.held = True  # mutated: trust O_CREAT|O_EXCL alone\n"
     "                return self",
     "11b a lock overwritten in the window is not ours"),

    # -- the lock must not outlive, nor overreach -------------------------
    ("the lock is never released",
     "morpho_homegraph/lock.py",
     "        try:\n"
     "            os.unlink(self.path)\n"
     "        except FileNotFoundError:\n"
     "            pass\n\n"
     "    def __enter__",
     "        pass  # mutated: lock file left behind\n\n"
     "    def __enter__",
     "18 a clean run leaves no lock behind"),

    ("release unlinks whatever lock is there, not only its own",
     "morpho_homegraph/lock.py",
     '        holder = _read(self.path)\n'
     '        if holder.get("nonce") != self.nonce:',
     '        holder = _read(self.path)  # mutated: release anyone\'s lock\n'
     '        if False:',
     # Gate 18b, not 11. Gate 11's refused writer never held anything, so its
     # `release` returns at `if not self.held` and never reaches this check --
     # which is why 18b had to be written before this mutation could be
     # attributed to anything.
     "18b release does not unlink a lock taken by someone else"),

    ("the lock leaks when the writer is interrupted",
     "morpho_homegraph/lock.py",
     "    def __exit__(self, *exc: object) -> None:\n"
     "        # Releases on KeyboardInterrupt too: an interrupted writer must leave\n"
     "        # nothing behind, and a lock file is something.\n"
     "        self.release()",
     "    def __exit__(self, *exc: object) -> None:\n"
     "        if exc[0] is None:  # mutated: leak the lock on the error path\n"
     "            self.release()",
     "19 KeyboardInterrupt releases the lock"),

    # -- readers must not be caught by the barrier ------------------------
    ("readers take the write barrier too",
     "morpho_homegraph/cli.py",
     '    """Reader. Takes no lock, creates no store, answers while a writer writes."""',
     '    StoreLock(str(db_path(_resolve(args.project)))).acquire()  # mutated',
     "16 a reader answers while a writer holds the lock"),

    # -- WAL and the timeout have to be in force, not merely assigned -----
    ("the journal mode is claimed rather than read",
     "morpho_homegraph/store.py",
     '            else "PRAGMA journal_mode = WAL").fetchone()\n'
     '        self.journal_mode = (row[0] if row else "unknown").lower()',
     '            else "PRAGMA journal_mode = DELETE").fetchone()\n'
     '        self.journal_mode = "wal"  # mutated: claim it without asking',
     "3  WAL is in force on a local file"),

    # There is deliberately no mutation for `busy_timeout`. Measured
    # 2026-08-03: 5000 ms is `sqlite3.connect`'s own default, so no edit to
    # this package can move the value the gate reads -- a mutation that
    # removed the setting entirely still left the connection at 5000. Gate 4
    # is therefore a recorded value that would catch someone *changing* the
    # number, not a gate this harness can redden. Written down rather than
    # left as a permanent survivor, because a survivor list nobody can act on
    # is how a real one gets ignored.

    # -- identity ---------------------------------------------------------
    #
    # Found in review rather than by a gate, which is why the gate was written
    # afterwards and this mutation exists to prove it can say no.
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
    # Aimed at `update` alone, not at `_guard`: refusing in `_guard` also
    # breaks `add`, the suite cannot build a project, and the run reports a
    # crash instead of naming the gate. A mutation that takes the harness
    # down proves nothing about the gate it was written for.
    ("every writer is refused, contended or not",
     "morpho_homegraph/cli.py",
     "    try:\n"
     "        barrier = _guard(store_db)\n"
     "    except Locked as exc:",
     "    try:\n"
     "        raise Locked(str(store_db), {})  # mutated: refuse unconditionally\n"
     "        barrier = _guard(store_db)\n"
     "    except Locked as exc:",
     "8  a lone writer is not refused"),

    ("nothing is ever considered live",
     "morpho_homegraph/lock.py",
     "    try:\n"
     '        pid = int(holder["pid"])',
     '    return False, "mutated: never live"\n'
     "    try:\n"
     '        pid = int(holder["pid"])',
     "14b a live pid with the right start time is live"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp0.py", prefix="mut0-", timeout=600))
