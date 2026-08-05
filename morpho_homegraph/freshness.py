#!/usr/bin/env python3
"""How old is each layer, and how stale is each file? One place, two readers.

The answer key is `tests/gold/FASIT-cp12.md`, written before this module.

**Three clocks, because three commands build the layers.** `scan` writes L0,
`update` writes L2, L3 and L4 in one go, `embed` writes the vectors. A single
"last updated" would hide a day-old catalogue behind a minute-old index, which
is the shape of the failure measured on 2026-08-04: a search for `fasit-cp8`
found the predecessor's file and not ours, because L0 had last been walked
before ours was written -- and the answer said nothing.

**Fresh is relative to the catalogue, not to the disk.** We compare what L2
read against what L0 recorded, and L0 is itself as old as its last walk. So
every answer carries the catalogue's age beside the verdict: without it,
"fresh" is a claim about yesterday.

**Four states, all mechanically decidable** (locked decision 5, once more):
`fresh`, `stale`, `unread`, `unembedded`. No confidence, no heuristic -- and
the picture in CP-11 uses the same four, from the same export, so a colour can
be traced back to a value someone can check.

**An empty file is `fresh`, not `unembedded`.** Measured on this repository
2026-08-05: 14 files came back `unembedded` and every one of them held zero
characters -- `chunks_of("")` is `[]`, so no vector will ever exist for them.
The label was true by the letter and useless in effect: it told the reader to
run `embed`, which would change nothing. A state has to be one the reader can
act on, or it is noise wearing a colour.
"""
from __future__ import annotations

import time
from datetime import datetime

FRESH, STALE, UNREAD, UNEMBEDDED = "fresh", "stale", "unread", "unembedded"

# The layers, in the order a reader meets them, with the meta key that dates
# each one. L2, L3 and L4 share `last_update` because one command builds all
# three -- written down here rather than left to be inferred from the table.
LAYERS = (("catalogue", "l0_scanned_at"),
          ("content", "last_update"),
          ("vectors", "embed_at"))


def human(seconds: float | None) -> str:
    """`3 s`, `2 min`, `4 h`, `2 d`. `never` when the layer was never built.

    Zero is `0 s`, not the empty string: an age that disappears when it is
    small teaches the reader that a missing age means "fine", and then a
    missing age for a *broken* layer reads as fine too.
    """
    if seconds is None:
        return "never"
    if seconds < 0:
        # Only possible when the clocks disagree -- the catalogue stamped
        # later than the content that was built from it. Said plainly rather
        # than printed as a negative age nobody can act on.
        return "from the future (clocks disagree)"
    if seconds < 90:
        return "%d s" % int(seconds)
    if seconds < 90 * 60:
        return "%d min" % int(seconds / 60)
    if seconds < 48 * 3600:
        return "%d h" % int(seconds / 3600)
    return "%d d" % int(seconds / 86400)


def _age(stamp: str | None, now: float) -> float | None:
    """Seconds since `stamp`, which is epoch seconds or an ISO timestamp."""
    if not stamp:
        return None
    try:
        return now - float(stamp)
    except ValueError:
        pass
    try:
        return now - datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def ages(store=None, l0_store=None, now: float | None = None) -> dict:
    """`{layer: seconds or None}` for every layer the caller opened.

    A store that was not opened is not reported as `None` -- it is left out.
    "I did not read that layer" and "that layer was never built" are different
    facts, and a reader who cannot tell them apart cannot act on either.
    """
    now = time.time() if now is None else now
    found = {}
    for name, key in LAYERS:
        source = l0_store if key.startswith("l0_") else store
        if source is None:
            continue
        found[name] = _age(source.get_meta(key), now)
    return found


def describe(found: dict) -> str:
    """The one line every answer ends with. `content 2 min, catalogue 4 h`."""
    order = [name for name, _key in LAYERS if name in found]
    return "  ".join("%s %s" % (name, human(found[name])) for name in order)


def per_file(store, l0_store=None) -> dict[str, str]:
    """`{path: state}` for everything L2 holds. Four states, R5's four.

    `l0_store=None` means the catalogue was not opened, and then no file can
    be called `stale` -- the comparison that decides it is the one we did not
    make. Everything readable is `fresh` in that case, and the caller's age
    line is what tells the reader the comparison is missing.
    """
    embedded = {sha for (sha,) in store.db.execute(
        "SELECT DISTINCT sha256 FROM vectors")}
    any_vectors = bool(embedded)
    current = {}
    if l0_store is not None:
        current = {path: mtime for path, mtime in l0_store.db.execute(
            "SELECT path, mtime_ns FROM files WHERE kind = 'file'")}

    state = {}
    for path, mtime_ns, sha, reason, has_text in store.db.execute(
            "SELECT path, mtime_ns, sha256, reason,"
            " COALESCE(LENGTH(TRIM(text)), 0) > 0 FROM content"):
        if reason is not None:
            state[path] = UNREAD
        elif path in current and current[path] != mtime_ns:
            state[path] = STALE
        elif any_vectors and has_text and sha not in embedded:
            state[path] = UNEMBEDDED
        else:
            state[path] = FRESH
    return state


def tally(state: dict[str, str]) -> dict[str, int]:
    """`{state: count}`, always with all four keys so a zero is visible."""
    counted = {FRESH: 0, STALE: 0, UNREAD: 0, UNEMBEDDED: 0}
    for value in state.values():
        counted[value] = counted.get(value, 0) + 1
    return counted
