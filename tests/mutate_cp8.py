#!/usr/bin/env python3
"""Mutation test for CP-8 -- a search that answers, and answers wrongly.

The failure here never raises. A search with a broken splitter still returns
hits; one with a stale index returns none and calls it "no matches"; one that
ranks by path returns the same rows in a worse order. Every mutation below
leaves a command that runs and exits 0.

The controls are the other half: a search that reports "stale" whenever it
finds nothing passes every gate about reporting, and gates 11 and 18 are what
catch it.

Run:
    python3 tests/mutate_cp8.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the splitter (R3) -------------------------------------------------
    #
    # Locked decision 1 makes this mandatory: without the case boundary, every
    # identifier is one opaque token and the words inside it are unfindable.
    ("camelCase is not a boundary",
     "morpho_homegraph/search.py",
     '    re.compile(r"(?<=[a-z])(?=[A-Z])"),',
     "    # mutated: no case boundary",
     "2  a query for user by id finds a file holding only getUserById"),

    ("digits do not separate from letters",
     "morpho_homegraph/search.py",
     '    re.compile(r"(?<=[A-Za-z])(?=[0-9])"),',
     "    # mutated: utf8 stays one token",
     "5  the splitting table holds for every shape"),

    ("an acronym meeting a word is not a boundary",
     "morpho_homegraph/search.py",
     '    re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])"),',
     "    # mutated: HTTPServer stays one token",
     "5  the splitting table holds for every shape"),

    ("separators are not split on",
     "morpho_homegraph/search.py",
     '_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")',
     '_SEPARATORS = re.compile(r"\\s+")  # mutated',
     "5  the splitting table holds for every shape"),

    ("the tokens keep their case, so a lowercase query misses",
     "morpho_homegraph/search.py",
     "        tokens.extend(part.lower() for part in chunk.split() if part)",
     "        tokens.extend(part for part in chunk.split() if part)  # mutated",
     "5  the splitting table holds for every shape"),

    ("nothing is written to the symbols column",
     "morpho_homegraph/search.py",
     "    return \" \".join(sorted(set(out)))",
     "    return \"\"  # mutated: no symbols at all",
     "2  a query for user by id finds a file holding only getUserById"),

    # -- the layer is replaced whole (R2) ----------------------------------
    ("the index accumulates instead of being replaced",
     "morpho_homegraph/search.py",
     '        db.execute("DELETE FROM %s" % TABLE)',
     "        pass  # mutated: keep what was there",
     "16 a file removed from L2 is gone from the hits after update"),

    ("update stops building the search layer",
     "morpho_homegraph/cli.py",
     "                l4 = search.build(store)",
     '                l4 = {"rows": 0}  # mutated',
     "15 update builds L4: search.build has a caller in the package"),

    # -- an index that cannot answer says so (R6) --------------------------
    #
    # The dangerous ones: every one of these turns a broken index into "no
    # matches", which is a sentence the user believes.
    ("a missing index is reported as fine",
     "morpho_homegraph/search.py",
     '        return "missing", 0, expected',
     '        return "ok", 0, expected  # mutated',
     "9  a missing index reports and exits 1, not zero hits"),

    ("a row count mismatch is tolerated",
     "morpho_homegraph/search.py",
     "    if indexed != expected:\n"
     '        return "stale", indexed, expected',
     "    if False:  # mutated: close enough\n"
     '        return "stale", indexed, expected',
     "10 an under-filled index reports instead of returning silence"),

    ("every index is reported stale, whatever it holds",
     "morpho_homegraph/search.py",
     '    return "ok", indexed, expected',
     '    return "stale", indexed, expected  # mutated',
     "11 zero hits on a complete index is exit 0 and says so"),

    ("the command answers anyway when the index cannot",
     "morpho_homegraph/cli.py",
     "        if condition != \"ok\":",
     "        if False:  # mutated: answer regardless",
     "9  a missing index reports and exits 1, not zero hits"),

    # -- the query is text, not syntax (R8) --------------------------------
    ("the user's words are passed to FTS5 as syntax",
     "morpho_homegraph/search.py",
     "    return \" \".join('\"%s\"' % t for t in terms)",
     "    return \" \".join(terms)  # mutated: raw MATCH syntax",
     "12 FTS5 syntax in a query is text, not syntax"),

    # -- ranking and provenance (R4, R7) -----------------------------------
    ("hits are ordered by path instead of by rank",
     "morpho_homegraph/search.py",
     "        \" ORDER BY rank, path LIMIT ?\" % (TABLE, TABLE, TABLE),",
     "        \" ORDER BY path LIMIT ?\" % (TABLE, TABLE, TABLE),  # mutated",
     "13 the best match ranks first and the order is stable"),

    ("every hit claims to come from the text",
     "morpho_homegraph/search.py",
     '    return [{"path": path, "rank": rank,\n'
     '             "where": "text" if rowid in in_text else "symbols"}\n'
     "            for rowid, path, rank in rows]",
     '    return [{"path": path, "rank": rank, "where": "text"}  # mutated\n'
     "            for rowid, path, rank in rows]",
     "4  a hit says whether it came from the text or from the symbols"),

    # -- two searches, two questions (R5) ----------------------------------
    ("name search answers from the project instead of the catalogue",
     "morpho_homegraph/search.py",
     '    where = " AND ".join(["LOWER(path) LIKE ?"] * len(terms))',
     '    where = " AND ".join(["LOWER(path) LIKE ?"] * len(terms))'
     ' + " AND kind = \'nothing\'"  # mutated',
     "6  a file outside every scope is found by name"),

    # -- FTS5 is required, its absence is a refusal (R1) -------------------
    ("a database without FTS5 is reported as having it",
     "morpho_homegraph/search.py",
     "        return True\n"
     "    except sqlite3.Error:\n"
     "        return False",
     "        return True\n"
     "    except sqlite3.Error:\n"
     "        return True  # mutated: pretend",
     "17 without FTS5 the search refuses, and there is no LIKE fallback"),

    # -- a reader takes no guard (R9) --------------------------------------
    ("search takes the write guard, so an indexing run blocks it",
     "morpho_homegraph/cli.py",
     "    with Store(store_db, read_only=True) as store:\n"
     "        condition, indexed, expected = search.state(store)",
     "    _held = _guard(store_db)  # mutated: a reader that locks\n"
     "    with Store(store_db, read_only=True) as store:\n"
     "        condition, indexed, expected = search.state(store)",
     "14 search answers while another process holds the project guard"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp8.py", prefix="mut8-", timeout=900))
