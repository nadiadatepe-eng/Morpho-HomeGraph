#!/usr/bin/env python3
"""CP-17: hash the rows that never move, once, and say so.

The defect this exists for is quiet. `journal.build`'s `unchanged` branch
copies the previous hash forward, and for a file that predates the scope that
hash is `NULL`. Every later pass sees equal size and equal mtime, copies the
`NULL` again, and the row is **cold for ever** -- not warming up. A row with no
hash can never be reported `touched`, so the second half of the two-step design
is unreachable for it. Measured on the real catalogue before this was written:
4 773 files in scope, 51 hashed, 4 722 cold.

**Its own command, never a side effect of `scan`** (R1). The cheap pass has to
stay cheap; a round that silently hashes 259 MB the first time it sees a new
scope is the mistake M-3 already forced us away from when it moved embedding
out of `update`.

**What it may not do** is the part worth reading twice. A hash taken now says
what the file contains now. It says *nothing* about whether the file was
unchanged at the previous pass, because no comparison happened. Storing it
indistinguishably from a compared hash would manufacture evidence of a
comparison nobody made -- the same error `unconfirmed` exists to prevent. So
every hash carries `hash_source`, and the first real comparison upgrades a
`backfilled` row to `compared`.
"""
from __future__ import annotations

from .journal import BACKFILLED, content_hash


def cold_rows(store, keep) -> list[tuple[str, int]]:
    """(path, size) for every row that is in scope, a file, and unhashed.

    Not "every row without a hash": outside the scope `NULL` is the correct
    answer rather than a hole, and hashing there would make the shared L0 pay
    for a scope it does not have. `keep` is `service.union_keep()`, the same
    single definition CP-15 R2 settled on -- a second predicate here would be a
    second thing to drift, and the drift would look like changed files.
    """
    return [(path, size) for path, size in store.db.execute(
        "SELECT path, size FROM files "
        "WHERE content_hash IS NULL AND kind = 'file' ORDER BY path")
        if keep(path)]


def backfill(store, keep, *, max_files: int | None = None,
             dry_run: bool = False, progress=None) -> dict:
    """Hash the cold in-scope rows. Returns what it did, or would do.

    Never writes to `journal` (R4): the journal is the record of a comparison
    between two L0 passes, and this is not a pass. Writing `touched` here would
    claim someone looked at the file twice.

    One row per commit rather than one commit at the end (R5). Hashing 259 MB
    can be interrupted, and the honest partial result is *fewer backfilled
    rows* -- never a half-written one, and never a row whose provenance
    outlives the hash it describes. Re-running picks up whatever is still
    `NULL`.
    """
    pending = cold_rows(store, keep)
    total_bytes = sum(size or 0 for _path, size in pending)
    report = {"files": len(pending), "bytes": total_bytes, "hashed": 0,
              "unreadable": 0, "refused": None}

    # The ceiling is stated before the work, not discovered during it (R7).
    if max_files is not None and len(pending) > max_files:
        report["refused"] = (
            "%d cold file(s) is over the --max-files limit of %d; nothing was "
            "hashed. Raise the limit or narrow the scope."
            % (len(pending), max_files))
        return report
    if dry_run:
        return report

    for path, _size in pending:
        digest = content_hash(path)
        if digest is None:
            # Unreadable is not an error to abort on and not a hash to invent.
            # The row stays cold, and the count says how many did.
            report["unreadable"] += 1
            continue
        with store.writing():
            store.db.execute(
                "UPDATE files SET content_hash = ?, hash_source = ? "
                "WHERE path = ? AND content_hash IS NULL",
                (digest, BACKFILLED, path))
            store.db.commit()
        report["hashed"] += 1
        if progress is not None:
            progress(report["hashed"], len(pending))
    return report


def coverage(store, keep) -> dict:
    """How much of the scope carries a hash, split by how it was obtained.

    Split rather than totalled (R6, blind spot 3): a single number mixes hashes
    that can support `touched` with hashes that cannot yet, and reports a store
    as warmer than it is. Without this, 51 of 4 773 reads exactly like 4 773 of
    4 773 -- CP-7B R8's rule that an empty index must not be able to look
    finished.

    **`migrated` is part of the answer, not an exception.** `status` opens L0
    read-only, and a read-only open does not migrate -- so on a catalogue built
    before CP-17 the column simply is not there. Raising would turn a reader
    into a command that dies on a store it is only looking at; returning zeros
    would be worse, because "no hashes" and "cannot tell" are different facts
    and the second must not be printed as the first.
    """
    if not _has_hash_source(store):
        return {"in_scope": 0, "hashed": 0, "compared": 0, "backfilled": 0,
                "percent": 0.0, "migrated": False}
    in_scope = compared = backfilled = 0
    for path, digest, source in store.db.execute(
            "SELECT path, content_hash, hash_source FROM files "
            "WHERE kind = 'file'"):
        if not keep(path):
            continue
        in_scope += 1
        if digest is None:
            continue
        if source == BACKFILLED:
            backfilled += 1
        else:
            compared += 1
    hashed = compared + backfilled
    return {"in_scope": in_scope, "hashed": hashed, "compared": compared,
            "backfilled": backfilled,
            "percent": (100.0 * hashed / in_scope) if in_scope else 0.0,
            "migrated": True}


def _has_hash_source(store) -> bool:
    """Does this catalogue carry the CP-17 column yet?

    Asked of the table rather than of the schema version: a read-only open
    never migrates, so a version number would say the store is current while
    the column it promises is missing.
    """
    return "hash_source" in {row[1] for row in store.db.execute(
        "PRAGMA table_info(files)").fetchall()}
