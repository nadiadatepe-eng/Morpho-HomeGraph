#!/usr/bin/env python3
"""Mutation test for CP-12 -- an answer that is silent about its own age.

None of these raise, and none of them changes a single hit. A search that
drops the age line answers exactly as before; a state counter that calls
everything fresh draws a green picture of a stale corpus; an age that rounds
to nothing tells the reader the layer is new. Every mutation below leaves a
command that exits 0 with an answer that looks right.

The one to read twice is "the catalogue's age is left out of a name search":
that is the *measured* failure this checkpoint exists for -- a search for
`fasit-cp8` that found the predecessor's file and not ours, and said nothing
about a catalogue older than the file.

Run:
    python3 tests/mutate_cp12.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the age is in every answer (R1, R2, R3) ---------------------------
    ("the catalogue's age is left out of a name search",
     "morpho_homegraph/cli.py",
     "        print(age)\n"
     "        return 0",
     "        return 0  # mutated: the silence CP-12 exists to end",
     "1  a name search says how old the catalogue is"),

    ("a lexical answer stops saying how old the content is",
     "morpho_homegraph/cli.py",
     '        print("no matches for %r in %d indexed files" % (args.query, indexed))\n'
     "    print(age)",
     '        print("no matches for %r in %d indexed files" % (args.query, indexed))',
     "2  a lexical search says the age of the content and the catalogue"),

    ("the vectors' layer is never dated",
     "morpho_homegraph/embed.py",
     '    store.set_meta("embed_at", "%.3f" % time.time())',
     "    pass  # mutated: the most expensive layer has no clock",
     "3  a semantic search says the vectors' age as well"),

    ("only the layer that was read is reported, never the others",
     "morpho_homegraph/freshness.py",
     "        found[name] = _age(source.get_meta(key), now)",
     "        found[name] = None  # mutated: every layer is 'never'",
     "5  the age is printed even when every layer is fresh"),

    # -- the four states (R5) -----------------------------------------------
    ("a file that was never read is called fresh",
     "morpho_homegraph/freshness.py",
     "        if reason is not None:\n"
     "            state[path] = UNREAD",
     "        if False:  # mutated: unread looks like read\n"
     "            state[path] = UNREAD",
     "7  a file that could not be read is unread, never fresh"),

    ("a file changed after the update is still called fresh",
     "morpho_homegraph/freshness.py",
     "        elif path in current and current[path] != mtime_ns:\n"
     "            state[path] = STALE",
     "        elif False:  # mutated: our copy is always current\n"
     "            state[path] = STALE",
     "6  a file changed after the last update is stale"),

    ("a file with no vector is called fresh",
     "morpho_homegraph/freshness.py",
     "        elif any_vectors and has_text and sha not in embedded:\n"
     "            state[path] = UNEMBEDDED",
     "        elif False:  # mutated: embedded or not, who can tell\n"
     "            state[path] = UNEMBEDDED",
     "8  a file with no vector for its hash is unembedded"),

    # The control: without gate 9, every gate above passes for a counter that
    # says "stale" about everything.
    ("everything is called stale, whatever it is",
     "morpho_homegraph/freshness.py",
     "        else:\n"
     "            state[path] = FRESH",
     "        else:\n"
     "            state[path] = STALE  # mutated: nothing is ever fresh",
     "9  a project that is read, current and embedded is all fresh"),

    ("the comparison uses the size instead of the time",
     "morpho_homegraph/freshness.py",
     '            "SELECT path, mtime_ns FROM files WHERE kind = \'file\'")}',
     '            "SELECT path, size FROM files WHERE kind = \'file\'")}  # mutated',
     "9  a project that is read, current and embedded is all fresh"),

    ("an empty file is asked to be embedded",
     "morpho_homegraph/freshness.py",
     "        elif any_vectors and has_text and sha not in embedded:",
     "        elif any_vectors and sha not in embedded:  # mutated",
     "8b an empty file is fresh, because there is nothing to embed"),

    # -- the picture and the text are one fact (R7, R9) ---------------------
    ("the export drops the per-file state",
     "morpho_homegraph/view.py",
     '                       "state": (state or {}).get(absolute, "fresh")}',
     '                       }  # mutated: no state travels to the picture',
     "10 the export carries the four states, named as the text names them"),

    ("the export does not date itself",
     "morpho_homegraph/view.py",
     '    data["exported_at"] = time.time()\n'
     '    data["ages"] = ages or {}',
     "    pass  # mutated: the page may work the age out itself",
     "11 the export dates itself and every layer"),

    ("the printed counts are not the states that were exported",
     "morpho_homegraph/view.py",
     '            states[node["state"]] = states.get(node["state"], 0) + 1',
     '            states["fresh"] = states.get("fresh", 0) + 1  # mutated',
     "13 the counts in the answer match the states in the export"),

    ("the picture is coloured by kind again, not by freshness",
     "view/js/draw.js",
     "  unembedded: \"#6a5acd\",   // read and current, but no vector for its hash",
     "  // mutated: the state nobody can see",
     "12 the page draws by state, explains each one, and uses no markup"),

    # -- the words (R1, and the clock) --------------------------------------
    ("a small age is printed as nothing at all",
     "morpho_homegraph/freshness.py",
     '    if seconds < 90:\n'
     '        return "%d s" % int(seconds)',
     '    if seconds < 90:\n'
     '        return ""  # mutated: fresh enough to say nothing',
     "14 an age reads as a human says it, and zero is not empty"),

    ("an age from the future is printed as a negative number",
     "morpho_homegraph/freshness.py",
     "    if seconds < 0:",
     "    if False:  # mutated: let the minus sign explain itself",
     "16 an age from the future is said in words, not as a negative"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp12.py", prefix="mut12-", timeout=900))
