#!/usr/bin/env python3
"""CP-6: which of move, copy and delete happened to a project's directory.

The answer key is `tests/gold/FASIT-cp6.md`, written before this module.

**The verdict comes from `exists()` on both paths and from nothing else.**
Locked decision 5: mechanically decidable, never a heuristic with a
confidence. Three outcomes, and the table in the answer key is the whole rule.

**Recognition is one function with two entrances.** `recognise` answers for one
project, `recognise_all` loops over it -- a design decision (2026-08-04) was that
both had to be possible, and the consequence is that neither may hold logic the
other lacks. Two implementations of one question is how one of them starts
answering something else, so the sweep is a loop and not a second copy.

**Nothing is deleted here.** `TODO.md` says a deleted project lives on only in
snapshots, and snapshots are CP-7, which does not exist yet. So CP-6 marks and
CP-7 removes: a deleted project gets `state = deleted`, drops out of listings,
and its directory is left alone. Deleting first would mean it lives nowhere.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .store import PROJECT, Store, db_path, projects

# The three verdicts, plus the one that is not a verdict.
COPY, MOVE, DELETED, PRESENT = "copy", "move", "deleted", "present"
AMBIGUOUS = "ambiguous"

LIVING, GONE = "living", "deleted"


class Moved(RuntimeError):
    """The project is no longer where it was, and here is where it went."""

    def __init__(self, old: str, new: str) -> None:
        super().__init__(
            "this project is no longer at %s -- it moved to %s" % (old, new))
        self.old, self.new = old, new


def _digest(root: str, entries) -> str | None:
    """sha256 over sorted `relative-path\0size` lines, or None if empty.

    **One function for both sides.** The project's own fingerprint comes from
    `content`, a candidate tree's from L0's `files`, and two implementations of
    "is this the same tree" are two things that can start answering
    differently.

    None rather than the digest of an empty list: an empty tree would otherwise
    share a fingerprint with every other empty tree and match one of them.
    "Nothing to go on" is the honest value.
    """
    root = root.rstrip(os.sep)
    lines = sorted("%s\0%d" % (os.path.relpath(path, root), size)
                   for path, size in entries)
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def fingerprint(store) -> str | None:
    """This project's fingerprint, from the paths and sizes L2 recorded.

    Relative path and size rather than content hashes, and the reason is in
    the answer key under R4: L0 carries `content_hash` only for files inside a
    *scope*, and a directory that has just been moved is outside every scope
    by definition. Hashing the candidate instead would mean reading every file
    in every directory tree in the home area to find one moved folder.
    """
    root = store.get_meta("project_path") or ""
    return _digest(root, store.db.execute(
        "SELECT path, size FROM content"))


def verdict(old_path: str, new_path: str | None) -> str:
    """Which of the three happened, from `exists()` and nothing else."""
    old_here = os.path.isdir(old_path)
    new_here = bool(new_path) and os.path.isdir(str(new_path))
    if old_here and new_here:
        return COPY
    if new_here:
        return MOVE
    if old_here:
        return PRESENT
    return DELETED


def candidates_by_inode(l0_store, dev: int, inode: int) -> list[str]:
    """Directories in L0 carrying this (dev, inode). Cheap, exact, and a
    *candidate* -- never a proof.

    A filesystem reuses inodes: delete a directory, make another, and the
    number can come back. So a hit here has to be confirmed against the
    fingerprint before anything is rewritten, or a project ends up pointing at
    a stranger's folder (blind spot 2).
    """
    return [p for (p,) in l0_store.db.execute(
        "SELECT path FROM files WHERE kind = 'dir' AND dev = ? AND inode = ?",
        (dev, inode))]


def _fingerprint_of_tree(root: str, l0_store) -> str | None:
    """The fingerprint a project rooted at `root` would have, from L0 alone.

    A query rather than a read: L0 knows every path and its size. It is how a
    move across filesystems is recognised -- the inode changed, the tree did
    not.
    """
    prefix = root.rstrip(os.sep) + os.sep
    return _digest(root, l0_store.db.execute(
        "SELECT path, size FROM files WHERE kind = 'file' AND path LIKE ?",
        (prefix + "%",)))


def recognise(project_id: str, l0_store) -> dict:
    """What happened to one project, and where it is now if it moved.

    Returns `{"verdict", "path", "candidates", "by"}`. `verdict` is `present`,
    `move`, `deleted` or `ambiguous`; `path` is the new location when one was
    established, and None otherwise. **Nothing is written** -- deciding and
    acting are separate so a caller can show the user an ambiguity instead of
    resolving it for them.

    `by` names *which route found it* -- `inode`, `fingerprint`, or None. It is
    not decoration: without it no gate can tell "the inode found it" from "the
    fallback found it anyway", and three mutations that disabled one route
    each survived on 2026-08-04 for exactly that reason. A mechanism with two
    routes needs the answer to say which one ran.
    """
    with Store(db_path(project_id), read_only=True) as store:
        old = store.get_meta("project_path") or ""
        dev = store.get_meta("root_dev")
        inode = store.get_meta("root_inode")
        mine = fingerprint(store)

    if os.path.isdir(old):
        return {"verdict": PRESENT, "path": old, "candidates": [old],
                "by": None}

    # The inode first, because it is a lookup rather than a search -- and then
    # confirmed, because it is a candidate rather than a proof.
    found: list[str] = []
    by = None
    if dev and inode:
        # Compared as strings, not with `samefile`: `old` is gone by
        # definition here, and `samefile` on a missing path raises.
        found = [p for p in candidates_by_inode(l0_store, int(dev), int(inode))
                 if p != old and os.path.isdir(p)]
        found = [p for p in found
                 if mine is not None
                 and _fingerprint_of_tree(p, l0_store) == mine]
        if found:
            by = "inode"

    if not found and mine is not None:
        # The fallback: the project moved to another filesystem, so the inode
        # is a different number and only the content is still the same.
        found = sorted({p for (p,) in l0_store.db.execute(
            "SELECT DISTINCT path FROM files WHERE kind = 'dir'")
            if os.path.isdir(p) and _fingerprint_of_tree(p, l0_store) == mine})
        if found:
            by = "fingerprint"

    if len(found) > 1:
        # Two trees holding the same content are the same content. Choosing
        # between them would move a whole project's index into the wrong
        # folder, so the answer is "I do not know" and the user is asked.
        return {"verdict": AMBIGUOUS, "path": None,
                "candidates": sorted(found), "by": by}
    if len(found) == 1:
        return {"verdict": MOVE, "path": found[0], "candidates": found,
                "by": by}
    return {"verdict": DELETED, "path": None, "candidates": [], "by": None}


def open_project(project_id: str, l0_store):
    """The project's current path, or `Moved` saying where it went.

    R7: the old location is *refused*, not silently emptied and not deleted.
    "Nothing here" and "it is over there" look the same to a caller that only
    gets an empty answer, and only one of them is true (house rule 6).

    This is what makes `Moved` a mechanism rather than a class nobody raises --
    a read path outside `tests/` calls it, which is the question worth asking
    of every mechanism.
    """
    answer = recognise(project_id, l0_store)
    if answer["verdict"] == PRESENT:
        return answer["path"]
    with Store(db_path(project_id), read_only=True) as store:
        old = store.get_meta("project_path") or ""
    if answer["verdict"] == MOVE:
        raise Moved(old, answer["path"])
    raise Moved(old, "nowhere we can find -- %s" % answer["verdict"])


def recognise_all(l0_store) -> dict[str, dict]:
    """The same question for every registered project.

    A loop over `recognise`, deliberately: a sweep with its own logic is a
    second implementation of one question, and gate 16 compares the two on the
    same state for exactly that reason.
    """
    return {pid: recognise(pid, l0_store) for pid, _path in projects()}


def apply_move(project_id: str, new_path: str) -> int:
    """Point the project at `new_path` and rewrite the paths L2 recorded.

    The edges, the hashes and the provenance are not touched, because none of
    them is a path -- that is what CP-5's identity choice buys here. Returns
    the number of content rows rewritten.

    **The caller holds the write guard**, same as `scan`, `content.build` and
    `graph.build`. Taking it here would mean two places decide when a store is
    writable.
    """
    new_path = str(Path(new_path).expanduser().resolve())
    with Store(db_path(project_id), role=PROJECT) as store:
        old = store.get_meta("project_path") or ""
        with store.writing() as db:
            db.execute(
                "UPDATE content SET path = ? || SUBSTR(path, ?) "
                "WHERE path LIKE ?",
                (new_path, len(old) + 1, old.rstrip(os.sep) + os.sep + "%"))
            moved = db.execute("SELECT CHANGES()").fetchone()[0]
            db.commit()
        store.set_meta("project_path", new_path)
        st = os.stat(new_path)
        store.set_meta("root_dev", str(st.st_dev))
        store.set_meta("root_inode", str(st.st_ino))
    return moved


def mark_deleted(project_id: str) -> None:
    """Mark, do not remove. CP-7 owns removal, and CP-7 does not exist yet.

    Deleting the directory now would mean the project lives nowhere: the
    living store would be rid of it and the snapshots that are supposed to
    hold it have not been built.

    **The caller holds the write guard.**
    """
    with Store(db_path(project_id), role=PROJECT) as store:
        store.set_meta("state", GONE)


def living_projects() -> list[tuple[str, str]]:
    """Registered projects that have not been marked deleted."""
    out = []
    for pid, path in projects():
        try:
            with Store(db_path(pid), read_only=True) as store:
                if (store.get_meta("state") or LIVING) == LIVING:
                    out.append((pid, path))
        except OSError:
            continue
    return out


def remember_root(store, path: str) -> None:
    """Record the root's (dev, inode) so a move can be recognised later."""
    st = os.stat(str(Path(path).expanduser().resolve()))
    store.set_meta("root_dev", str(st.st_dev))
    store.set_meta("root_inode", str(st.st_ino))
