"""The event source: ctypes over libc inotify, and the guards around it.

**Borrowed, and the attribution is a hash rather than a sentence.** This is
`~/homegraph/homegraph/watch.py` @ `2ce3462d42a72bfebe43c0010e297d8f5e12b552`
(2026-07-24), from the predecessor project, with `relevant_to_corpus` and
`watch_loop` left behind -- the first was a two-line composition and the second
was a loop with one clock. `service.py` has two (CP-13 R5), so its loop is
written here rather than borrowed, and the debounce-and-coalesce policy inside
it is the borrowed idea even though the lines are not.

**The predecessor refused to have a service at all**, and said so in this
file: "homegraph has no long-running services, and a watch that survives its
terminal would be exactly that." That was right *there*, where nothing
arbitrated between two writers. Here `lock.py` does, and locked decision 12
says who it is for. The refusal is cited, not inherited.

What stayed is the part that talks to the kernel and the part that decides
whether an event is worth anything:

    relevant()    -- the self-trigger guard: an update writes the stores, and
                     an update must not trigger itself. Pure.
    store_prune() -- the same store paths excluded a layer earlier, so the
                     kernel never reports them at all.
    Inotify       -- the real event source. Linux only, which is all this
                     machine is; unavailable elsewhere is reported, not
                     crashed (CP-13 R7).

Nothing in this module writes. The only writer is the service's trigger.
"""
from __future__ import annotations

import ctypes
import os
import select
import struct
from collections.abc import Callable, Iterable

Prune = Callable[[str], bool]


def relevant(path: str, ignore: Iterable[str]) -> bool:
    """True if `path` is a corpus change worth an update.

    False for the stores an update writes -- the store file itself, its SQLite
    `-wal` / `-shm` / `-journal` siblings, and the writer's own `.`-suffixed
    artifacts (`.lock`, `.tmp`). Without this guard the first update's own
    writes land back in the watch as fresh events and it updates forever.

    **Here this is less pressed and more necessary at the same time.** The
    stores live in `~/.local/share/morpho-homegraph/`, outside every project
    tree -- until somebody registers `~` or `~/.local` as a project, and then
    the whole store is inside the watched tree. That is the case this holds
    for, and it is why it is belt as well as `store_prune`'s braces.
    """
    p = os.path.abspath(path)
    for db in ignore:
        if p == db or p.startswith(db + "-") or p.startswith(db + "."):
            return False
    return True


def store_prune(root: str, stores: Iterable[str]) -> Prune:
    """A prune predicate that excludes every directory that holds a store.

    `relevant` keeps a store write from *triggering* an update, but a watched
    store still arms an inotify watch and the flood wakes the loop -- so prune
    the directory that holds each store and the kernel never reports it.

    Only stores strictly below `root` are pruned. A store sitting *at* `root`
    would make this prune the whole tree, and one *above* `root` is never
    walked.

    Paths are compared by `realpath`, not `abspath`: `os.walk` reaches a
    directory by its real path, so a store reached through a symlinked
    component must resolve to the same string or the prune fires on a path
    that is never walked and misses the one that is.
    """
    root = os.path.realpath(root)
    store_dirs = {d for d in
                  (os.path.dirname(os.path.realpath(p)) for p in stores)
                  if d.startswith(root + os.sep)}

    def prune(directory: str) -> bool:
        d = os.path.realpath(directory)
        return any(d == sd or d.startswith(sd + os.sep) for sd in store_dirs)

    return prune


class Source:
    """What the service's loop needs from an event source.

    `read(timeout)` returns the events available within `timeout` seconds as a
    list of (path, mask); an empty list means the timeout elapsed with nothing.
    `timeout=None` blocks until at least one event arrives. A KeyboardInterrupt
    raised out of `read` is how a stop propagates -- the loop does not catch it.
    """

    def read(self, timeout: float | None) -> list[tuple[str, int]]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# The real event source. Linux only.
# --------------------------------------------------------------------------

IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000

# What we ask the kernel to report. Creation, deletion, moves, and completed
# writes (IN_CLOSE_WRITE, not every IN_MODIFY mid-write) -- enough to know the
# tree moved without a flood of partial-write events. IN_DELETE_SELF /
# IN_MOVE_SELF let us drop a watch whose directory is gone.
_WATCH_MASK = (IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM
               | IN_MOVED_TO | IN_CLOSE_WRITE | IN_DELETE_SELF | IN_MOVE_SELF)

# struct inotify_event: int wd; uint32 mask, cookie, len; then `len` name bytes.
_HDR = struct.Struct("iIII")


class InotifyUnavailable(RuntimeError):
    """inotify could not be initialised -- not Linux, or the fd limit is hit."""


class Inotify(Source):
    def __init__(self) -> None:
        try:
            self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
            self._libc.inotify_init1.argtypes = [ctypes.c_int]
            self._libc.inotify_init1.restype = ctypes.c_int
            self._libc.inotify_add_watch.argtypes = [
                ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self._libc.inotify_add_watch.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise InotifyUnavailable("no libc inotify on this platform: %s"
                                     % exc) from exc
        self._fd = self._libc.inotify_init1(0)
        if self._fd < 0:
            raise InotifyUnavailable(
                "inotify_init1 failed (errno %d)" % ctypes.get_errno())
        self._wd2path: dict[int, str] = {}
        self._prune: Prune | None = None

    def add_tree(self, root: str, prune: Prune | None = None) -> int:
        """Watch `root` and every directory under it, `prune` permitting.

        A watch is per-directory; recursion is ours to maintain. `prune(dir)`
        returning True excludes that directory and everything under it -- on a
        real home this is what keeps the watch off `.cache`, `.venv`, `.git`
        and the rest. Measured on the predecessor, 2026-07-23: **676 watches
        against 51 661 naive.**

        New subdirectories created at runtime are pruned by the same predicate
        (see `read`). Directories that cannot be watched (permissions, a race
        with deletion) are skipped rather than aborting the walk -- an
        unwatchable corner is a gap in coverage, not a reason to refuse the
        whole tree.

        Returns the number of watches this call armed, which is the number the
        service reports and gate 11 counts.
        """
        self._prune = prune
        before = len(self._wd2path)
        self._walk_add(root, prune)
        return len(self._wd2path) - before

    def _walk_add(self, root: str, prune: Prune | None) -> None:
        self._add_one(root)
        for dirpath, dirnames, _files in os.walk(root):
            kept = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if prune is not None and prune(full):
                    continue
                self._add_one(full)
                kept.append(d)
            # Mutating dirnames stops os.walk descending into pruned trees, so
            # a pruned directory costs one predicate call, not a walk of all
            # its contents.
            dirnames[:] = kept

    def _add_one(self, path: str) -> None:
        wd = self._libc.inotify_add_watch(
            self._fd, os.fsencode(path), _WATCH_MASK)
        if wd >= 0:
            self._wd2path[wd] = path

    def read(self, timeout: float | None) -> list[tuple[str, int]]:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return []
        buf = os.read(self._fd, 65536)
        events: list[tuple[str, int]] = []
        i = 0
        while i < len(buf):
            wd, mask, _cookie, length = _HDR.unpack_from(buf, i)
            i += _HDR.size
            name = buf[i:i + length].split(b"\0", 1)[0]
            i += length
            base = self._wd2path.get(wd)
            if base is None:
                continue
            path = os.path.join(base, os.fsdecode(name)) if name else base
            if mask & (IN_CREATE | IN_MOVED_TO) and mask & IN_ISDIR:
                # A directory just appeared; watch it before its contents
                # start changing, or the first files created inside it are
                # missed -- unless the same prune rule that shaped the initial
                # walk excludes it.
                if self._prune is None or not self._prune(path):
                    self._walk_add(path, self._prune)
            if mask & (IN_IGNORED | IN_DELETE_SELF):
                self._wd2path.pop(wd, None)
                continue
            events.append((path, mask))
        return events

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
