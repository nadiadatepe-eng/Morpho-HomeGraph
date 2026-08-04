#!/usr/bin/env python3
"""L4: lexical search over L2, and name search over L0.

The answer key is `tests/gold/FASIT-cp8.md`, written before this module.

**FTS5 is a requirement, and its absence is a refusal.** A `LIKE` fallback
would still answer -- worse, in a form nobody can see, which is the worst thing
a search can be. `fts5_available()` asks the connection rather than the build
flags, because the question is whether *this* database can create the table.

**Identifier splitting happens when the row is written.** SQLite's built-in
tokenizers cannot split `getUserById`: `unicode61` splits on separators, not on
case, and a tokenizer that could needs C. So the tokens go into a `symbols`
column beside the text, and locked decision 1 -- code is text -- is what makes
that mandatory rather than a nicety.

**Two searches, because they are two questions.** Content lives only where the
user pointed; names exist for every file on the machine. That is locked
decision 7 (metadata everywhere, content where you point) made visible, and it
is why `names()` reads L0 and never opens a project store.

**An index that cannot answer says so.** Zero hits from an index that was never
built looks exactly like zero hits from a corpus without the word. `state()`
compares the row counts and the caller reports rather than returning silence.
"""
from __future__ import annotations

import re
import sqlite3
import time

from .store import PROJECT

# The FTS5 table, per project store. Not in `store.SCHEMA`: it is a virtual
# table whose availability has to be tested rather than assumed, and a store
# that cannot create it must still open.
TABLE = "l4"

# Rebuilt whole on every update, like every layer below it.
CREATE = ("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING fts5("
          "  path UNINDEXED, text, symbols,"
          "  tokenize='unicode61 remove_diacritics 2')" % TABLE)

# Where an identifier comes apart. Three boundaries, applied in order:
# an acronym meeting a word (HTTPServer -> HTTP Server), a lower-to-upper
# transition (getUser -> get User), and a letter meeting a digit (utf8 -> utf 8).
_BOUNDARIES = (
    re.compile(r"(?<=[A-Za-z])(?=[0-9])"),
    re.compile(r"(?<=[0-9])(?=[A-Za-z])"),
    re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])"),
    re.compile(r"(?<=[a-z])(?=[A-Z])"),
)
_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")


def split_identifier(raw: str) -> list[str]:
    """`getUserById` -> `['get', 'user', 'by', 'id']`. Mechanical, no parser.

    Locked decision 1 says code is text, and the consequence written down with
    it is that this is mandatory: without it, every identifier is one opaque
    token and a search for the words inside it returns nothing.
    """
    tokens = []
    for chunk in _SEPARATORS.split(raw):
        if not chunk:
            continue
        for boundary in _BOUNDARIES:
            chunk = boundary.sub(" ", chunk)
        tokens.extend(part.lower() for part in chunk.split() if part)
    return tokens


def symbols_of(text: str) -> str:
    """Every word in `text` that comes apart, as the split tokens.

    Only the ones that actually split: a word that is already one token would
    otherwise be stored twice, doubling the column for no recall at all.
    """
    out = []
    for word in set(re.findall(r"[0-9A-Za-z_.\-/]+", text)):
        tokens = split_identifier(word)
        if len(tokens) > 1:
            out.extend(tokens)
    return " ".join(sorted(set(out)))


def fts5_available(db) -> bool:
    """Can *this* database create an FTS5 table? Asked, never assumed."""
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts5_probe "
                   "USING fts5(x)")
        db.execute("DROP TABLE IF EXISTS temp.fts5_probe")
        return True
    except sqlite3.Error:
        return False


class NoFTS5(RuntimeError):
    """This SQLite cannot do FTS5, and there is no lesser search on offer."""

    def __init__(self) -> None:
        super().__init__(
            "this SQLite build has no FTS5, so there is no index to search. "
            "A substring fallback is deliberately not offered: it would answer "
            "worse without saying so. Install a python3 whose sqlite3 has FTS5")


def build(store, l2_rows=None) -> dict:
    """Rebuild L4 from this project's `content`. The caller holds the guard.

    Replaced whole, like L2 and L3 -- a layer that accumulates is one where
    "the previous pass" has stopped meaning anything.
    """
    if store.role != PROJECT:
        raise RuntimeError("L4 belongs to a project store, not a %r one"
                           % store.role)
    if not fts5_available(store.db):
        raise NoFTS5()
    started = time.perf_counter()
    with store.writing() as db:
        db.execute(CREATE)
        db.execute("DELETE FROM %s" % TABLE)
        rows = l2_rows if l2_rows is not None else db.execute(
            "SELECT path, text FROM content WHERE text IS NOT NULL")
        indexed = 0
        for path, text in rows:
            db.execute(
                "INSERT INTO %s (path, text, symbols) VALUES (?, ?, ?)" % TABLE,
                (path, text, symbols_of(text)))
            indexed += 1
        db.commit()
    store.set_meta("l4_rows", str(indexed))
    store.set_meta("l4_seconds", "%.3f" % (time.perf_counter() - started))
    return {"rows": indexed}


def state(store) -> tuple[str, int, int]:
    """`('ok'|'missing'|'stale', indexed, expected)`.

    The whole of R6: zero hits from an index that was never built is
    indistinguishable from zero hits from a corpus without the word, so the
    caller has to be able to tell them apart before it prints anything.
    """
    expected = store.db.execute(
        "SELECT COUNT(*) FROM content WHERE text IS NOT NULL").fetchone()[0]
    try:
        indexed = store.db.execute(
            "SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
    except sqlite3.Error:
        return "missing", 0, expected
    if indexed != expected:
        return "stale", indexed, expected
    return "ok", indexed, expected


def as_query(raw: str) -> str:
    """The user's words as FTS5 terms, never as FTS5 syntax.

    `foo AND (bar` is a reasonable thing to type and invalid MATCH syntax; `*`
    and `NEAR` would quietly change what was asked. Every term is quoted, so
    the text is treated as text. A trust boundary, not a nicety.
    """
    terms = [t.replace('"', '') for t in re.findall(r"[^\s]+", raw)]
    terms = [t for t in terms if re.search(r"[0-9A-Za-z]", t)]
    return " ".join('"%s"' % t for t in terms)


def content(store, raw: str, limit: int = 20) -> list[dict]:
    """Search this project's L2. Returns hits, best first, ties by path.

    `bm25` decides the order; the path breaks ties so two identical runs print
    identical output -- an order that drifts is one no test can hold.
    """
    query = as_query(raw)
    if not query:
        return []
    rows = store.db.execute(
        "SELECT rowid, path, bm25(%s) AS rank FROM %s WHERE %s MATCH ?"
        " ORDER BY rank, path LIMIT ?" % (TABLE, TABLE, TABLE),
        (query, limit)).fetchall()
    # Which column matched, asked as a second query with FTS5's own column
    # filter. A hit that cannot say whether the splitting found it leaves the
    # mandatory splitting unfalsifiable -- and `column MATCH ...` is not FTS5
    # syntax, so this is the form that works rather than the one that reads
    # better (verified 2026-08-04).
    in_text = {rowid for (rowid,) in store.db.execute(
        "SELECT rowid FROM %s WHERE %s MATCH ?" % (TABLE, TABLE),
        ("text : (%s)" % query,))}
    return [{"path": path, "rank": rank,
             "where": "text" if rowid in in_text else "symbols"}
            for rowid, path, rank in rows]


def names(l0_store, raw: str, limit: int = 20) -> list[dict]:
    """Search L0's paths. The whole home area, and no scope anywhere in it.

    Locked decision 7 is the reason this is a different function and not a
    flag on the one above: metadata is indexed everywhere, content only where
    the user pointed, so these two questions have different answers by design.
    """
    terms = [t.lower() for t in re.findall(r"[0-9A-Za-z_.\-]+", raw)]
    if not terms:
        return []
    where = " AND ".join(["LOWER(path) LIKE ?"] * len(terms))
    rows = l0_store.db.execute(
        "SELECT path, kind FROM files WHERE %s ORDER BY LENGTH(path), path"
        " LIMIT ?" % where,
        [*("%%%s%%" % t for t in terms), limit]).fetchall()
    return [{"path": path, "kind": kind} for path, kind in rows]
