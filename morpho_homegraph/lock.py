#!/usr/bin/env python3
"""One writer per store, for as long as the session lasts. The kernel decides.

**Nothing here is borrowed.** homegraph's `lock.py` was written for a barrier
taken around each write, and it carries the machinery that shape needs: a pid,
a boot-relative start time, a staleness test, orphan clearing, a nonce read
back after `O_CREAT|O_EXCL`. All of it exists because **a lock file outlives
the process that wrote it** -- it is cleanup after an orphan, not a lock.

Here the barrier's lifetime *is* the session's: taken at startup, released
when the process ends, however it ends. A lock with that lifetime should be
one the kernel holds. `fcntl.flock` on a file descriptor kept open for the
life of the process is released by the kernel on exit, on `SIGKILL`, on a
crash, on the power going out. So an orphaned lock cannot exist, and every
mechanism for detecting and clearing one has no work to do.

That is not only simpler, it removes the one thing that was actually broken.
The borrowed scheme had a window where two writers both hold the guard: both
read the same orphan, both judge it stale, A clears it and takes the lock, and
B's already-decided `unlink` then removes A's *live* lock. The read-back does
not close it -- it only catches a loser who writes before the winner reads
back. The race was produced by the orphan clearing. Delete the clearing and
the race goes with it -- the tool that reproduced it went with it too, and
`tools/cp0_contention.py` took its place: twelve processes released at the
same instant, one winner.

**The lock file is never deleted.** Deleting it reintroduces exactly that
class one level down: A holds the lock on inode X and unlinks it, B creates a
new file with inode Y and locks that, and both hold a lock on a different
inode. `index.db.lock` stays on disk forever. What is in it is **payload** --
a pid and a timestamp, so a refusal can name the holder. The payload decides
nothing; the flock does.

**It refuses; it does not queue.** `LOCK_NB`, and a caller who is told which
pid owns writing and that there is no queue to join. A queue would make a
second writer wait on a first that may be rebuilding a 600k-file corpus, and
its caller -- a cron line, a shell loop -- has no way to know it is waiting
rather than working.

**A store that is not on local disk is refused, not guarded.** `flock` over a
network filesystem is the dangerous kind of unreliable: on Linux since 2.6.37
it is emulated with POSIX locks and usually does work across NFS clients --
*unless* the mount carries `local_lock`, which makes every lock local to its
own machine without saying so. Then the barrier looks exactly like a barrier
and two machines write the same index. Rather than guess which case we are
in, `acquire` proves the store is on a filesystem this kernel alone arbitrates
and refuses otherwise, naming the fix: the corpus can live anywhere, only the
index has to be local, and `MORPHO_HOMEGRAPH_HOME` moves the index.

The list is an allowlist. A denylist that misses one filesystem fails in the
direction this whole module exists to avoid.

One property that needed no fixing, measured 2026-08-03 rather than assumed:
`flock` follows the open file description, so a `fork()` **shares** the guard
with the child. An `exec` does not -- `os.open` returns a non-inheritable
descriptor since Python 3.4 (PEP 446), verified with `os.get_inheritable`. So
a spawned subprocess cannot keep a store locked after the service that spawned
it dies, and a forked worker deliberately can.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from typing import Any

# Store paths this process currently guards. `Store` asks before writing, so
# that "the service is the only writer" is enforced by the store rather than
# remembered by whoever opens it -- a rule that lives in one caller is a habit,
# and holds exactly until the second caller.
_HELD: set[str] = set()


def holds(store_path: str) -> bool:
    """Does *this process* hold the guard for `store_path`?"""
    return str(store_path) in _HELD


# Filesystems whose `flock` is decided by this kernel and nobody else, so a
# lock taken here is one every process on this machine can see. An allowlist,
# because a denylist that has not heard of a filesystem lets it through.
LOCAL_FILESYSTEMS = frozenset((
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs",
    "bcachefs", "tmpfs", "overlay", "vfat", "exfat", "ntfs3", "fuseblk",
))

# For a machine that knows its store is fine on a filesystem this does not
# recognise. Named in the refusal, so it is a decision rather than a discovery.
ALLOW_REMOTE = "MORPHO_HOMEGRAPH_ALLOW_REMOTE_STORE"

# mountinfo escapes exactly these four characters in the mount point.
_ESCAPES = (("\\011", "\t"), ("\\012", "\n"), ("\\040", " "), ("\\134", "\\"))


def filesystem_of(path: str, mountinfo: str | None = None) -> str:
    """The filesystem type of the mount containing `path`; `""` if unknowable.

    `mountinfo` is the text of `/proc/self/mountinfo`, taken as an argument so
    the gate can hand it an NFS mount without one being available -- a check
    that can only be exercised on a machine that happens to have the wrong
    filesystem is a check nobody runs.
    """
    if mountinfo is None:
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as fh:
                mountinfo = fh.read()
        except OSError:
            # No /proc: we cannot prove anything about this path, and saying
            # so is the answer. The caller refuses rather than assuming.
            return ""
    target = os.path.abspath(path)
    best_point, best_type = "", ""
    for line in mountinfo.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        sep = fields.index("-")
        if sep + 1 >= len(fields) or sep < 5:
            continue
        point = fields[4]
        for code, char in _ESCAPES:
            point = point.replace(code, char)
        fstype = fields[sep + 1]
        # `/home` must not claim `/homegraph`, so a prefix only counts when it
        # ends at a separator. Longest match wins: mounts nest.
        if target != point and not target.startswith(point.rstrip("/") + "/"):
            continue
        if len(point) >= len(best_point):
            best_point, best_type = point, fstype
    return best_type


class NotLocal(RuntimeError):
    """The store is not on a filesystem whose locks this kernel decides."""

    def __init__(self, store_path: str, fstype: str) -> None:
        self.store_path = store_path
        self.fstype = fstype
        super().__init__(
            "%s is on %s, where a lock may be local to one machine while "
            "looking like a lock on all of them -- two writers would not see "
            "each other. Point the index at local disk with "
            "MORPHO_HOMEGRAPH_HOME (the corpus can stay where it is; only the "
            "index has to be local), or set %s=1 if you know this filesystem "
            "arbitrates flock across every machine that reaches it."
            % (store_path, fstype or "a filesystem that cannot be identified",
               ALLOW_REMOTE))


class Locked(RuntimeError):
    """Another live process holds this store's guard."""

    def __init__(self, path: str, holder: dict[str, Any]) -> None:
        self.path = path
        self.holder = holder
        super().__init__(
            "pid %s owns writing to this store, since %s"
            % (holder.get("pid", "?"), holder.get("created", "?")))


class Unguarded(RuntimeError):
    """A write was attempted by a process that does not hold the guard."""

    def __init__(self, store_path: str) -> None:
        super().__init__(
            "this process does not hold the write guard for %s -- take "
            "StoreLock(...) first; the store is not a thing you write to by "
            "opening it" % store_path)


class StoreLock:
    """The session guard for one store path.

    Taken once at startup and held until the process ends -- not around each
    write. Readers never take it: `status`, and later search and MCP, answer
    while a writer holds it, which is what WAL is for.
    """

    def __init__(self, store_path: str, fingerprint: str | None = None) -> None:
        self.store_path = str(store_path)
        self.path = self.store_path + ".lock"
        self.fingerprint = fingerprint
        self.fd: int | None = None
        self.held = False

    # -- acquisition ------------------------------------------------------

    def _payload(self) -> bytes:
        return json.dumps({
            "pid": os.getpid(),
            "created": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": self.fingerprint,
            "store": self.store_path,
        }).encode("utf-8")

    def read_holder(self) -> dict[str, Any]:
        """The payload as a dict; `{}` when it is absent or will not parse.

        Only ever read after `flock` has already said someone else holds the
        guard, so a `{}` here means the holder has not written its payload yet
        -- a window of one `write` -- and never that the lock is free.
        """
        try:
            with open(self.path, "rb") as fh:
                loaded = json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def acquire(self) -> "StoreLock":
        # Before the lock, not after: a guard that cannot be exclusive should
        # never be handed out, and finding that out once the store is open is
        # finding it out too late.
        fstype = filesystem_of(self.store_path)
        if fstype not in LOCAL_FILESYSTEMS and os.environ.get(ALLOW_REMOTE) != "1":
            raise NotLocal(self.store_path, fstype)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # EWOULDBLOCK. Read who has it *before* closing our descriptor:
            # the payload is the only thing that can name them, and closing
            # first would be one more moment for it to change.
            holder = self.read_holder()
            os.close(fd)
            raise Locked(self.store_path, holder)
        self.fd = fd
        self.held = True
        _HELD.add(self.store_path)
        # Payload after winning, never before: a process that writes its name
        # first and then fails to take the lock has told everyone it holds
        # something it does not.
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, self._payload())
        os.fsync(fd)
        return self

    # -- release ----------------------------------------------------------

    def release(self) -> None:
        """Drop the guard. The file stays; only the lock on it goes.

        Unlinking here is the bug this design exists to avoid -- see the
        module docstring. The payload is left as a record of the last holder,
        which is readable by a person and load-bearing for nobody: it is only
        ever consulted when `flock` has already refused someone.
        """
        if not self.held:
            return
        self.held = False
        _HELD.discard(self.store_path)
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "StoreLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        # Releases on KeyboardInterrupt too. The kernel would drop the lock at
        # exit anyway; doing it here is what lets one process take the guard,
        # hand it back, and take it again without ending.
        self.release()
