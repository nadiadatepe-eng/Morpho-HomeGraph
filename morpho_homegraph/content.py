#!/usr/bin/env python3
"""L2: the content of what the scope selected, code included, as text.

L0 says what exists, L1 says what moved, the scope says what concerns us --
this is the first layer that opens a file. The answer key is
`tests/gold/FASIT-cp4.md`, written before this module.

**Every candidate gets a row, read or not.** A path that could not be read
carries a `reason` and no `text`. Without that, "not read" and "does not
exist" are the same absence, and telling them apart is the whole delivery of
this checkpoint. The invariant is `reason IS NULL` iff `text IS NOT NULL`, and
gate 2 checks the XOR rather than trusting this file.

**The reasons are mechanical, never probable.** Locked decision 5 applies:
a heuristic with a confidence is not an answer. Undeclared encryption is
reported `binary`, because an AES block with no header is random bytes and a
counter that called it `encrypted` would be a confidence dressed as a fact.
Only a file that says so itself, in exact bytes, gets `encrypted`.

**UTF-8 strictly, never `errors="replace"`.** Replacement decoding produces
text that *looks* read, which CP-8 would index and CP-9 would embed: one silent
defect propagating into two layers. Measured 2026-08-04, the cost of being
strict is 1 file in 11 949 under `~/Dokumenter` and 0 under `~/homegraph` once
`.git/objects` is out of scope.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import journal
from .store import PROJECT


class WrongStore(RuntimeError):
    """L2 was aimed at a store that does not hold a project's content."""

# Why each reason fired. Ordered as they are tried, and that order is part of
# the answer: the size cap is decided from L0's number so the file is never
# opened, which is the point of having a cap at all.
TOO_LARGE, UNREADABLE, ENCRYPTED, BINARY, UNDECODABLE = (
    "too_large", "unreadable", "encrypted", "binary", "undecodable")

# 1 MiB, the same cap `tools/m3_first_open.mjs` uses for M-3. Two different
# caps would make the M-3 number untransferable to CP-9, which is the one
# place it gets used. Policy, not a model -- change it and the numbers in
# gate 15 change with it.
MAX_BYTES = 1 << 20

# Enough of the head to decide "is this text", and no more. git uses 8000 for
# the same call; the precision is not in the number, it is in there being *a*
# number instead of the whole file. Measured 2026-08-04: this is a *cheap*
# binary detector, not a complete one -- a PDF whose first 8 KB hold no NUL
# reaches the decode step and is caught there as `undecodable` instead.
HEAD_BYTES = 8192

# Files that declare their own encryption, in exact bytes. Anything not on
# this list is not called encrypted, however random it looks.
ENCRYPTED_MAGIC = (
    b"-----BEGIN PGP MESSAGE-----",
    b"-----BEGIN PGP PRIVATE KEY BLOCK-----",
    b"age-encryption.org/v1",
    b"\x85",  # OpenPGP binary packet: old-format, public-key encrypted session key
)


def classify(path: str, size: int) -> tuple[str | None, str | None, str | None]:
    """Return `(reason, text, sha256)` for one file. Exactly one of the first
    two is None.

    The cap is checked against `size` -- L0's number, taken from `stat` -- so a
    208 MB PNG costs no read at all. Opening first and discovering the size
    afterwards is having already paid.
    """
    if size > MAX_BYTES:
        return TOO_LARGE, None, None
    try:
        raw = Path(path).read_bytes()
    except OSError:
        # Permission, a vanished file, a device that says no. All the same to
        # this layer: something we know nothing about, written down as such.
        return UNREADABLE, None, None

    head = raw[:HEAD_BYTES]
    if any(head.startswith(m) for m in ENCRYPTED_MAGIC):
        return ENCRYPTED, None, None
    if b"\0" in head:
        return BINARY, None, None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return UNDECODABLE, None, None
    return None, text, journal.hash_bytes(raw)


def build(store, l0_store, scope) -> dict[str, int]:
    """Fill this project's `content` from `l0_store`'s file list ∩ `scope`.

    Replaced whole, like `files` and `journal`: a layer that accumulates is one
    where "the previous pass" has stopped meaning anything.
    """
    if store.role != PROJECT:
        raise WrongStore(
            "content belongs to a project store, not a %r one: %s"
            % (store.role, store.path))
    started = time.perf_counter()
    tally: dict[str, int] = {"read": 0, "unread": 0}

    # Only `kind == "file"`. Directories, links and the rest have no content,
    # and they are not "unread" either -- they were never candidates, and
    # counting them would move the number gate 15 reads.
    candidates = [
        (path, size, mtime_ns)
        for path, size, mtime_ns in l0_store.db.execute(
            "SELECT path, size, mtime_ns FROM files WHERE kind = 'file'")
        if scope.contains(path)]

    with store.writing() as db:
        db.execute("DELETE FROM content")
        rows = []
        for path, size, mtime_ns in candidates:
            reason, text, sha = classify(path, size)
            tally["read" if reason is None else "unread"] += 1
            if reason is not None:
                tally[reason] = tally.get(reason, 0) + 1
            rows.append((path, size, mtime_ns, sha, text, reason))
        db.executemany(
            "INSERT INTO content (path, size, mtime_ns, sha256, text, reason)"
            " VALUES (?, ?, ?, ?, ?, ?)", rows)
        db.commit()

    store.set_meta("l2_read", str(tally["read"]))
    store.set_meta("l2_unread", str(tally["unread"]))
    store.set_meta("l2_seconds", "%.3f" % (time.perf_counter() - started))
    return tally
