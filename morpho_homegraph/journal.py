#!/usr/bin/env python3
"""L1: what changed between this L0 pass and the previous one.

Cheap first, and **hash confirms** -- only inside the chosen scope, which is
locked decision 7: metadata is indexed everywhere, content only where the user
pointed.

**Six states, not five.** The plan names `added`, `changed`, `touched`,
`unchanged`, `removed`. Outside the scope no file is read, and there
`changed` cannot be told from `touched` -- so calling it either one would be a
claim without evidence. `unconfirmed` is that honesty: size or mtime differs,
and nobody looked.

**`touched` has to be able to fire**, which is the whole reason `files` carries
`content_hash`. In homegraph nothing stored it, the stored hash was NULL for
every row, every rewrite came back as `changed`, and a two-step design that
could only ever produce one of its two answers passed every gate it had.

The known blind spot, stated rather than discovered: **same size and same
mtime means no hash is taken**, so a change that preserves both is reported as
`unchanged`. That is the price of the cheap layer being cheap. Gate 11 plants
it and asserts the wrong answer, so that the next reader finds it written down
instead of finding it out.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ADDED, REMOVED, UNCHANGED, CHANGED, TOUCHED, UNCONFIRMED = (
    "added", "removed", "unchanged", "changed", "touched", "unconfirmed")

# Read in blocks rather than whole: a 200 MB file in the scope should not
# decide the memory ceiling of the pass that hashes it.
BLOCK = 1 << 20


def hash_bytes(raw: bytes) -> str:
    """sha256 of bytes already in hand.

    Here so CP-4 can hash what it just read instead of reading the file a
    second time, and so both layers share one definition of "the hash of this
    content" -- two implementations of the same digest is two things that can
    drift, and the drift would look like a changed file.
    """
    return hashlib.sha256(raw).hexdigest()


def content_hash(path: str) -> str | None:
    """sha256 of `path`, or None when it cannot be read.

    None is not an error state to propagate as "changed": a file we could not
    open is a file we know nothing new about, and the caller reports it as
    unconfirmed for exactly the same reason it does outside the scope.

    Block-wise rather than `hash_bytes(Path(path).read_bytes())`: a 200 MB file
    in the scope should not decide the memory ceiling of the pass that hashes
    it. CP-4 has a 1 MiB cap and can afford the whole-file form; this one has
    no cap.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(BLOCK)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def resolve_candidate(path: str, kind: str, old_hash: str | None,
                      keep) -> tuple[str, str | None]:
    """(state, hash to store) for a row whose size or mtime moved.

    `keep(path)` is the corpus predicate (CP-15 R2): true for anything some
    registered project would index, `.gitignore` included.
    """
    if kind != "file" or not keep(path):
        # Something differs and nobody is going to read the file. Saying
        # `changed` would be a guess wearing a confident name.
        return UNCONFIRMED, old_hash
    now = content_hash(path)
    if now is None:
        return UNCONFIRMED, old_hash
    if old_hash is None:
        # Never hashed -- it was outside the scope last time. A comparison
        # that did not happen is not evidence of a change. This is homegraph's
        # bug pointed the right way: there, a NULL hash silently meant
        # `changed` for every file, for ever.
        return UNCONFIRMED, now
    return (TOUCHED if now == old_hash else CHANGED), now


def build(store, keep=None) -> dict[str, int]:
    """Diff `files_new` against `files`, filling `journal`. Returns the tally.

    `keep(path)` decides what is worth a hash. `None` means nothing is --
    which is what every caller passed implicitly until CP-15, and why
    `content_hash` was NULL for all 430 189 rows of the real store.

    Done in SQL rather than by loading both passes into dictionaries: a home
    area is 729 343 entries (measured 2026-08-03), and the set of rows whose
    size or mtime actually moved between two passes is small. Only that set
    reaches Python, and only the part of it inside the scope is ever opened.
    """
    keep = keep or (lambda _path: False)
    db = store.db
    db.execute("DELETE FROM journal")

    # The three verdicts that need no file read, straight from the join.
    db.execute(
        "INSERT INTO journal (path, state) SELECT n.path, ? FROM files_new n "
        "LEFT JOIN files o ON o.path = n.path WHERE o.path IS NULL", (ADDED,))
    db.execute(
        "INSERT INTO journal (path, state) SELECT o.path, ? FROM files o "
        "LEFT JOIN files_new n ON n.path = o.path WHERE n.path IS NULL",
        (REMOVED,))
    # The blind spot lives in this one: equal size and equal mtime means no
    # hash is taken, so a change preserving both is reported as unchanged.
    # Deliberate -- hashing the whole scope every pass is what makes the cheap
    # layer expensive -- and gated so it is remembered.
    db.execute(
        "INSERT INTO journal (path, state) SELECT n.path, ? FROM files_new n "
        "JOIN files o ON o.path = n.path "
        "WHERE n.size = o.size AND n.mtime_ns = o.mtime_ns", (UNCHANGED,))
    db.execute(
        "UPDATE files_new SET content_hash = ("
        "  SELECT o.content_hash FROM files o WHERE o.path = files_new.path)"
        " WHERE path IN (SELECT path FROM journal WHERE state = ?)",
        (UNCHANGED,))

    # New files inside the scope are hashed now, so the *next* pass has
    # something to compare against. Without this `touched` can never fire,
    # which is exactly how homegraph's two-step design became decoration.
    for path, kind in db.execute(
            "SELECT n.path, n.kind FROM files_new n JOIN journal j "
            "ON j.path = n.path WHERE j.state = ?", (ADDED,)).fetchall():
        if kind == "file" and keep(path):
            db.execute("UPDATE files_new SET content_hash = ? WHERE path = ?",
                       (content_hash(path), path))

    # Everything left differs in size or mtime, and only these are read.
    candidates = db.execute(
        "SELECT n.path, n.kind, o.content_hash FROM files_new n "
        "JOIN files o ON o.path = n.path "
        "WHERE n.size <> o.size OR n.mtime_ns <> o.mtime_ns").fetchall()
    for path, kind, old_hash in candidates:
        state, digest = resolve_candidate(path, kind, old_hash, keep)
        db.execute("INSERT INTO journal (path, state) VALUES (?, ?)",
                   (path, state))
        db.execute("UPDATE files_new SET content_hash = ? WHERE path = ?",
                   (digest, path))

    return {state: count for state, count in db.execute(
        "SELECT state, COUNT(*) FROM journal GROUP BY state")}
