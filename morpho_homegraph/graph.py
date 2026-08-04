#!/usr/bin/env python3
"""L3: the graph, where identity is content and every edge says where it came from.

The answer key is `tests/gold/FASIT-cp5.md`, written before this module.

**A node is a content hash, and its paths are plural.** Two files with
identical bytes are one node with two paths, not two nodes (locked decision 1).
There is no `nodes` table: a node *is* a distinct `content.sha256`, and writing
that down a second time would be a copy free to drift from the original.

**Every edge carries `method` and `confidence`, and `method` is
keyword-only with no default.** A default would be inherited by every future
edge whose author did not think about it, and that edge would look exactly as
well-founded as one the file stated. `edge()` without `method=` is a TypeError
from Python itself rather than a validation somebody has to remember to write.

**Provenance is computed when read, never stored.** That is what makes "a
re-assertion updates provenance downward too" hold without a propagation
algorithm: there is nothing to propagate to. A stored `partial` becomes untrue
the moment the edge under it is stated, and no gate would see it. The price is
one lookup over an answer's edges per answer; if that ever gets expensive, a
cache is its own checkpoint with its own staleness gate, not something added in
passing.
"""
from __future__ import annotations

import os
import re
import time

from .store import PROJECT


class WrongStore(RuntimeError):
    """The graph was aimed at a store that is not a project's."""


class BadEdge(ValueError):
    """An edge that cannot be stored as asserted."""

# The one kind CP-5 builds. More kinds are more checkpoints, not more strings
# here.
LINK = "link"

# How an edge came to exist. `stated` methods are what a file literally says;
# the rest are inferences, and their confidence says so.
MD_INLINE = "md_inline"           # [text](relative/path.md), resolved exactly
MD_WIKILINK_PATH = "md_wikilink_path"    # [[dir/name.md]], resolved exactly
MD_WIKILINK_UNIQUE = "md_wikilink_unique"  # [[name]], one file has that name
MD_WIKILINK_SAME_EXT = "md_wikilink_same_ext"  # [[name]], narrowed by suffix

STATED = (MD_INLINE, MD_WIKILINK_PATH)

# One number for both derived methods, on purpose. We have not measured which
# of them is wrong more often, and ranking them with an invented number would
# be exactly the confidence-dressed-as-fact this layer exists to prevent. The
# *method* is the distinction, and it is on every row.
DERIVED_CONFIDENCE = 0.9

# Named `PROV_` because `scope.py` already has a `PARTIAL`, and it means
# something else entirely: a checkbox whose descendants disagree. Two constants
# with one name and two meanings is a confusion waiting for a reader in a
# hurry, and it also makes "produced in exactly one place" uncheckable.
PROV_COMPLETE, PROV_PARTIAL = "complete", "partial"

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
_INLINE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def provenance_note(edges) -> str:
    """`complete` if every edge is something a file stated, else `partial`.

    **The only place this verdict is produced.** Two copies of "which of these
    were guesses" is how one of them ends up always answering no -- and the one
    that always answers no is invisible, because its answer is the reassuring
    one.
    """
    for edge in edges:
        if edge["confidence"] < 1.0 or edge["method"] not in STATED:
            return PROV_PARTIAL
    return PROV_COMPLETE


def edge(db, src: str, dst: str, *, method: str, confidence: float,
         kind: str = LINK) -> None:
    """Assert one edge. `method` is keyword-only and has no default.

    Re-asserting upgrades in place: `(src, dst, kind)` is the key, and the
    higher confidence wins, so a link that was resolved by name and is later
    written out in full stops being a guess instead of sitting beside itself.
    """
    if not 0.0 <= confidence <= 1.0:
        raise BadEdge("confidence must be in [0, 1], got %r" % (confidence,))
    if not method:
        raise BadEdge("method must name how this edge came to exist")
    row = db.execute(
        "SELECT confidence FROM edges WHERE src = ? AND dst = ? AND kind = ?",
        (src, dst, kind)).fetchone()
    if row is None or row[0] < confidence:
        db.execute(
            "INSERT OR REPLACE INTO edges (src, dst, kind, method, confidence)"
            " VALUES (?, ?, ?, ?, ?)", (src, dst, kind, method, confidence))


def _targets(text: str):
    """`(raw_target, is_wikilink)` for every link in `text`."""
    for match in _WIKILINK.finditer(text):
        yield match.group(1).strip(), True
    for match in _INLINE.finditer(text):
        yield match.group(1).strip(), False


def build(store, scope_root: str | None = None) -> dict:
    """Build this project's edges from the text L2 already read.

    Replaced whole, like every layer below it.
    """
    if store.role != PROJECT:
        raise WrongStore(
            "the graph belongs to a project store, not a %r one: %s"
            % (store.role, store.path))
    started = time.perf_counter()

    rows = store.db.execute(
        "SELECT path, sha256, text FROM content WHERE text IS NOT NULL"
    ).fetchall()
    by_path = {path: sha for path, sha, _text in rows}
    # Name -> the hashes of every file with that basename. A name resolving to
    # more than one file is refused rather than picked between (locked
    # decision 5), and refusing needs the count, not the first hit.
    # `(hash, suffix)` rather than just the hash: R9 narrows an ambiguous name
    # by the suffix of the file the link is written in, and that needs the
    # suffix of each candidate.
    by_name: dict[str, set[tuple[str, str]]] = {}
    for path, sha, _text in rows:
        base = os.path.basename(path)
        suffix = os.path.splitext(base)[1]
        by_name.setdefault(base, set()).add((sha, suffix))
        by_name.setdefault(os.path.splitext(base)[0], set()).add((sha, suffix))

    tally = {"edges": 0, "stated": 0, "derived": 0,
             "ambiguous": 0, "outside": 0}

    with store.writing() as db:
        db.execute("DELETE FROM edges")
        for path, sha, text in rows:
            here = os.path.dirname(path)
            for raw, is_wiki in _targets(text):
                target = _resolve_exact(raw, here, by_path, scope_root)
                if target is not None:
                    method = MD_WIKILINK_PATH if is_wiki else MD_INLINE
                    edge(db, sha, target, method=method, confidence=1.0)
                    tally["stated"] += 1
                    continue
                if not is_wiki:
                    # An inline link that does not resolve points outside what
                    # we read. No edge: asserting one would be a claim about a
                    # node we do not have.
                    tally["outside"] += 1
                    continue
                target, method = _resolve_by_name(
                    raw, os.path.splitext(path)[1], by_name)
                if target is not None:
                    edge(db, sha, target, method=method,
                         confidence=DERIVED_CONFIDENCE)
                    tally["derived"] += 1
                elif raw in by_name:
                    tally["ambiguous"] += 1
                else:
                    tally["outside"] += 1
        tally["edges"] = db.execute(
            "SELECT COUNT(*) FROM edges").fetchone()[0]
        db.commit()

    store.set_meta("l3_edges", str(tally["edges"]))
    store.set_meta("l3_ambiguous", str(tally["ambiguous"]))
    store.set_meta("l3_outside", str(tally["outside"]))
    store.set_meta("l3_seconds", "%.3f" % (time.perf_counter() - started))
    return tally


def _resolve_by_name(raw: str, source_suffix: str, by_name) -> tuple:
    """`(hash, method)` for a bare `[[name]]`, or `(None, None)`.

    **R9, and the reason it is not a heuristic.** Refusing ambiguity means not
    *guessing* between equal candidates. Narrowing by the suffix of the file
    the link is written in does not guess: it uses something the source states
    about itself, and it still refuses when the narrowing leaves more than one.
    A heuristic would have a winner to fall back on; this has none.

    Measured 2026-08-04, which is why the rule exists at all: `~/.claude/memory`
    holds 150 `.md` and 150 `.embedding` sharing all 150 stems, so every single
    `[[name]]` was ambiguous and the layer produced zero edges on a corpus made
    of wikilinks.
    """
    candidates = by_name.get(raw, set())
    if len(candidates) == 1:
        return next(iter(candidates))[0], MD_WIKILINK_UNIQUE
    same = [sha for sha, suffix in candidates if suffix == source_suffix]
    if len(same) == 1:
        return same[0], MD_WIKILINK_SAME_EXT
    return None, None


def _resolve_exact(raw: str, here: str, by_path: dict[str, str],
                   scope_root: str | None) -> str | None:
    """The hash of the file `raw` names by path, or None."""
    for candidate in _candidate_paths(raw, here, scope_root):
        if candidate in by_path:
            return by_path[candidate]
    return None


def _candidate_paths(raw: str, here: str, scope_root: str | None):
    if raw.startswith(("http://", "https://", "mailto:")):
        return
    cleaned = raw.split("#", 1)[0]
    if not cleaned:
        return
    if os.path.isabs(cleaned):
        yield os.path.normpath(cleaned)
        return
    yield os.path.normpath(os.path.join(here, cleaned))
    if scope_root:
        yield os.path.normpath(os.path.join(scope_root, cleaned))


def neighbours(store, node: str, hops: int = 1) -> dict:
    """Every node reachable from `node` in `hops`, and how sure we are.

    The provenance verdict comes from `provenance_note` and nowhere else --
    this is a *read path*, and a read path with its own copy of the rule is
    exactly the failure the answer key names.
    """
    seen = {node}
    frontier = [node]
    used: list[dict] = []
    for _ in range(hops):
        nxt = []
        for current in frontier:
            for dst, kind, method, confidence in store.db.execute(
                    "SELECT dst, kind, method, confidence FROM edges "
                    "WHERE src = ?", (current,)):
                used.append({"src": current, "dst": dst, "kind": kind,
                             "method": method, "confidence": confidence})
                if dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
        frontier = nxt
    return {"nodes": sorted(seen - {node}), "edges": used,
            "provenance": provenance_note(used)}


def paths_of(store, node: str) -> list[str]:
    """Every path carrying this node's content. Plural on purpose (R1)."""
    return sorted(p for (p,) in store.db.execute(
        "SELECT path FROM content WHERE sha256 = ?", (node,)))


def nodes(store) -> list[str]:
    """The distinct content hashes L2 managed to read."""
    return sorted(h for (h,) in store.db.execute(
        "SELECT DISTINCT sha256 FROM content WHERE sha256 IS NOT NULL"))
