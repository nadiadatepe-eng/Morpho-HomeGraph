#!/usr/bin/env python3
"""Mutation test for CP-4 -- L2, and the claim that "not read" is an answer.

The failure this checkpoint is built against is not a crash. It is a layer
that quietly reports fewer rows than it should: a file skipped instead of
recorded with a reason, a replacement decode that counts as read, a cap that
opens the file before refusing it. All of those leave a green suite and a
smaller number, and a smaller number is what a working filter looks like.

So most mutations below make the layer *succeed more*, not less.

Run:
    python3 tests/mutate_cp4.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- every candidate gets a row, read or not --------------------------
    #
    # The shape the whole checkpoint exists to prevent: unread files silently
    # vanish, and "not read" becomes indistinguishable from "does not exist".
    ("unread files are skipped instead of recorded",
     "morpho_homegraph/content.py",
     "            tally[\"read\" if reason is None else \"unread\"] += 1",
     "            tally[\"read\" if reason is None else \"unread\"] += 1\n"
     "            if reason is not None:  # mutated: drop them\n"
     "                continue",
     "1  one row per file in L0 within the scope"),

    ("a row carries both a reason and the text",
     "morpho_homegraph/content.py",
     "            rows.append((path, size, mtime_ns, sha, text, reason))",
     "            rows.append((path, size, mtime_ns, sha, text or \"\", reason))"
     "  # mutated",
     "2  every row has a reason XOR text, never both or neither"),

    # -- the cap is decided before the file is opened ---------------------
    #
    # Same rows, same reasons, same counts. Only the syscalls differ, which is
    # why the audit hook is the only thing that can see it -- and it is the
    # 208 MB PNG that pays for the difference.
    ("the size cap is checked after reading the file",
     "morpho_homegraph/content.py",
     "    if size > MAX_BYTES:\n"
     "        return TOO_LARGE, None, None\n"
     "    try:\n"
     "        raw = Path(path).read_bytes()\n"
     "    except OSError:",
     "    try:\n"
     "        raw = Path(path).read_bytes()\n"
     "        if size > MAX_BYTES:  # mutated: opened first, refused after\n"
     "            return TOO_LARGE, None, None\n"
     "    except OSError:",
     "3  a file over the cap is refused without being opened"),

    ("an unreadable file is passed over in silence",
     "morpho_homegraph/content.py",
     "        return UNREADABLE, None, None",
     "        return None, \"\", None  # mutated: counted as read, empty",
     "4  a file without read permission is unreadable"),

    # -- the reasons are mechanical, and they are not each other ----------
    ("the head is not inspected, so nothing is binary",
     "morpho_homegraph/content.py",
     "    if b\"\\0\" in head:\n"
     "        return BINARY, None, None",
     "    if False:  # mutated\n"
     "        return BINARY, None, None",
     "5  a NUL in the head is binary"),

    ("declared encryption is reported as binary",
     "morpho_homegraph/content.py",
     "    if any(head.startswith(m) for m in ENCRYPTED_MAGIC):\n"
     "        return ENCRYPTED, None, None",
     "    if any(head.startswith(m) for m in ENCRYPTED_MAGIC):\n"
     "        return BINARY, None, None  # mutated",
     "6  PGP armor is encrypted, not binary"),

    # The silent one. `errors="replace"` returns a string, so the file counts
    # as read, gets stored, and reaches CP-8's index and CP-9's embeddings as
    # mojibake. Nothing raises, and the unread count goes *down*.
    ("undecodable bytes are replacement-decoded and stored",
     "morpho_homegraph/content.py",
     "        text = raw.decode(\"utf-8\")\n"
     "    except UnicodeDecodeError:\n"
     "        return UNDECODABLE, None, None",
     "        text = raw.decode(\"utf-8\", errors=\"replace\")  # mutated\n"
     "    except UnicodeDecodeError:\n"
     "        return UNDECODABLE, None, None",
     "7  cp1252 text is undecodable, not replaced"),

    # -- what is a candidate, and what is not -----------------------------
    ("code is treated as something other than text",
     "morpho_homegraph/content.py",
     "    if b\"\\0\" in head:\n"
     "        return BINARY, None, None\n"
     "    try:\n"
     "        text = raw.decode(\"utf-8\")",
     "    if b\"\\0\" in head or path.endswith(\".py\"):  # mutated\n"
     "        return BINARY, None, None\n"
     "    try:\n"
     "        text = raw.decode(\"utf-8\")",
     "8  code inside the scope is read as text"),

    ("the scope is not consulted, everything in L0 is read",
     "morpho_homegraph/content.py",
     "        if scope.contains(path, is_dir=False)]",
     "        if True]  # mutated",
     "9  a file outside the scope gets no row at all"),

    ("directories and links are candidates too",
     "morpho_homegraph/content.py",
     "            \"SELECT path, size, mtime_ns FROM files WHERE kind = 'file'\")",
     "            \"SELECT path, size, mtime_ns FROM files\")  # mutated",
     "10 directories and symlinks get no row"),

    # -- the layer is replaced, not appended to ---------------------------
    ("the layer keeps whatever the last pass left",
     "morpho_homegraph/content.py",
     "        db.execute(\"DELETE FROM content\")",
     "        pass  # mutated: nothing is cleared",
     "11 a rebuild replaces the layer, it does not accumulate"),

    ("the stored digest is of something else",
     "morpho_homegraph/content.py",
     "    return None, text, journal.hash_bytes(raw)",
     "    return None, text, journal.hash_bytes(raw[:16])  # mutated",
     "12 the stored sha256 is the same digest L1 computes"),

    # -- the store this layer belongs to ----------------------------------
    ("content lands in whatever store it is handed",
     "morpho_homegraph/content.py",
     "    if store.role != PROJECT:",
     "    if False:  # mutated",
     "13 content aimed at the L0 store is refused by role"),

    ("the build writes around the guard",
     "morpho_homegraph/content.py",
     "    with store.writing() as db:",
     "    if True:  # mutated: straight at the connection\n"
     "        db = store.db",
     "14 a build without the write guard writes nothing"),

    # -- the counter gate 15 reads ----------------------------------------
    #
    # Gate 15 alone is satisfied by a counter that is always positive. This is
    # the mutation that proves gate 16 is not decoration: it makes every file
    # unread, which gate 15 is perfectly happy with.
    ("everything is reported unread",
     "morpho_homegraph/content.py",
     "            reason, text, sha = classify(path, size)",
     "            reason, text, sha = classify(path, size)\n"
     "            reason, text = reason or \"binary\", None  # mutated",
     "16 a fixture with no binaries reports zero unread"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp4.py", prefix="mut4-", timeout=600))
