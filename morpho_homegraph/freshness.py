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

**A file the catalogue no longer holds is `stale`, not `fresh`.** It used to
fall through: `stale` asked whether the catalogue's timestamp differed, and a
file that is simply *gone* has no timestamp to differ. Fresh knowledge about
something that does not exist -- measured by Orchestrator, in the `claude`
account, 2026-08-05.

**A project with no vectors at all reports every text file as `unembedded`.**
There used to be a guard that switched the whole test off when the store held
no vectors, so "we have never embedded anything" and "everything is embedded"
gave the same answer. The guard was me protecting the reader from a true fact,
and the fact is actionable: `embed` is the command. Same measurement.

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

# Three ways a layer can have no age, and they are three different facts. They
# were one (`None`) until Orchestrator, in the `claude` account, measured that
# a broken timestamp and a layer that was never built printed the same word --
# and that a layer nobody opened vanished from the line entirely. That last one
# is R2 broken one level up: the rule says an age must be shown even when
# everything is fresh, and a missing *layer* was communicated by absence.
NEVER = "never built"
UNREADABLE = "stamp unreadable"
UNOPENED = "layer not read"

# The layers, in the order a reader meets them, with the meta key that dates
# each one. L2, L3 and L4 share `last_update` because one command builds all
# three -- written down here rather than left to be inferred from the table.
LAYERS = (("catalogue", "l0_scanned_at"),
          ("content", "last_update"),
          ("vectors", "embed_at"))


def human(seconds) -> str:
    """`3 s`, `2 min`, `4 h`, `2 d` -- or which kind of nothing it is.

    Zero is `0 s`, not the empty string: an age that disappears when it is
    small teaches the reader that a missing age means "fine", and then a
    missing age for a *broken* layer reads as fine too. The three non-ages
    are spelled out for the same reason.
    """
    if seconds in (NEVER, UNREADABLE, UNOPENED):
        return seconds
    if seconds is None:
        return NEVER
    # A stamp written microseconds after the clock was read comes back a shade
    # negative. Below a second that is the two writes racing, not a broken
    # clock, and shouting about it in the ordinary case would train the reader
    # to ignore the shout.
    if seconds < -1.0:
        # Only possible when the clocks disagree -- the catalogue stamped
        # later than the content that was built from it. Said plainly rather
        # than printed as a negative age nobody can act on.
        return "from the future (clocks disagree)"
    if seconds < 90:
        return "%d s" % int(max(seconds, 0))
    if seconds < 90 * 60:
        return "%d min" % int(seconds / 60)
    if seconds < 48 * 3600:
        return "%d h" % int(seconds / 3600)
    return "%d d" % int(seconds / 86400)


def _age(stamp: str | None, now: float):
    """Seconds since `stamp`, or which kind of non-age it is.

    A stamp we cannot parse is **not** the same fact as a layer that was never
    built, and returning one value for both was measured as a real confusion.

    An ISO stamp *without* an offset is read as local time, which is what
    wrote it -- but the same string is two hours apart between two zones, and
    Oslo's own offset moves by an hour between January and August (measured
    2026-08-05). New stamps are written with their offset for that reason;
    old naive ones keep the only reading they ever had.
    """
    if not stamp:
        return NEVER
    try:
        return now - float(stamp)
    except ValueError:
        pass
    try:
        return now - datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return UNREADABLE


def ages(store=None, l0_store=None, now: float | None = None) -> dict:
    """`{layer: seconds, or which kind of non-age it is}`. Always all three.

    "I did not read that layer", "that layer was never built" and "that
    layer's stamp is unreadable" are three different facts, and a reader who
    cannot tell them apart cannot act on any of them. They used to be one
    value and one omission.
    """
    now = time.time() if now is None else now
    found = {}
    for name, key in LAYERS:
        source = l0_store if key.startswith("l0_") else store
        # Every layer is always named. A layer that is missing from the line
        # is a fact told by absence, and that is the failure this whole
        # checkpoint exists to end -- one level up from where it was written.
        found[name] = _age(source.get_meta(key), now) if source is not None \
            else UNOPENED
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
    line now says `catalogue layer not read` so the reader knows which
    comparison is missing.

    **A path the open catalogue does not hold is `stale`, not `fresh`.** The
    file is gone from disk, so our copy is the only thing left -- that is our
    knowledge being out of date, which is what `stale` means. Which of move,
    copy and delete happened is CP-6's question, not this one's.

    This function trusts CP-4's invariant that `reason IS NULL` iff
    `text IS NOT NULL`, which that checkpoint's gate 2 enforces. A row with
    text but no hash would be reported `unembedded` here; measured on the real
    store 2026-08-05, there are 0 of 117 such rows, and the invariant is owned
    where it is written rather than checked twice.
    """
    embedded = {sha for (sha,) in store.db.execute(
        "SELECT DISTINCT sha256 FROM vectors")}
    # "The catalogue was not opened" and "the catalogue is open and empty" are
    # different facts, and using the emptiness of `current` for both was my
    # first attempt at this fix -- which left a deleted file `fresh` in exactly
    # the fixture that found it. The flag says which one it is.
    have_catalogue = l0_store is not None
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
        elif have_catalogue and (path not in current
                                 or current[path] != mtime_ns):
            state[path] = STALE
        elif has_text and sha not in embedded:
            state[path] = UNEMBEDDED
        else:
            state[path] = FRESH
    return state


def tally(state: dict[str, str]) -> dict[str, int]:
    """`{state: count}`, always with all four keys so a zero is visible.

    A value that is none of the four is counted under `?` rather than added as
    a fifth name: the four-key guarantee is about what a reader can rely on
    seeing, and silently growing the vocabulary would make an unknown state
    look like a state somebody defined.
    """
    counted = {FRESH: 0, STALE: 0, UNREAD: 0, UNEMBEDDED: 0}
    for value in state.values():
        counted[value if value in counted else "?"] = \
            counted.get(value if value in counted else "?", 0) + 1
    return counted
