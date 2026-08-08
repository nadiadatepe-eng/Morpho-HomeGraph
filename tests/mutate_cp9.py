#!/usr/bin/env python3
"""Mutation test for CP-9 -- an embedding layer that runs and lies.

None of these raise. A search over vectors keyed on the path still answers; one
that never drops a stale vector answers *better*, with hits for text that is
gone; one that re-embeds everything every time is only slow. Every mutation
below leaves a command that exits 0.

Two of them are the controls. "the layer always refuses" is what gates 9, 10,
11 and 19 would be satisfied by if gate 18 were not there, and "update embeds
too" is the one M-3's conclusion forbids -- and it is invisible unless the
gate empties the table first, because the hash reuse makes a second embedding
a no-op.

The chunk mutations look like taste and are not: 1000/100 is what makes M-3's
21.0 chunks/s a number about this program.

Run:
    python3 tests/mutate_cp9.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- M-3's chunk parameters (R4) ---------------------------------------
    ("chunks do not overlap",
     "morpho_homegraph/embed.py",
     "OVERLAP = 100",
     "OVERLAP = 0  # mutated: a sentence split at a boundary is lost",
     "2  1000-char chunks with 100 overlap: count and overlap hold"),

    ("the chunk size is halved",
     "morpho_homegraph/embed.py",
     "CHUNK = 1000",
     "CHUNK = 500  # mutated: M-3's rate is now about another split",
     "2  1000-char chunks with 100 overlap: count and overlap hold"),

    ("binary text is embedded as if it were prose",
     "morpho_homegraph/embed.py",
     '    if "\\0" in text[:HEAD_BYTES]:\n'
     "        return []",
     "    if False:  # mutated: NUL is just a character\n"
     "        return []",
     "3a a text with a NUL byte yields no chunks at all"),

    ("the 1 MiB ceiling is not applied",
     "morpho_homegraph/embed.py",
     "    text = text[:MAX_BYTES]",
     "    pass  # mutated: embed the whole file however large",
     "3b a text over the ceiling is cut at it, not embedded whole"),

    # -- one process, many chunks (R1) -------------------------------------
    #
    # Model load is 0.96 s fixed per process (M-3). This mutation is correct
    # in every observable way except the one that matters.
    ("a fresh Node process is started for every chunk",
     "morpho_homegraph/embed.py",
     "        processes = 1\n"
     "        with Embedder() as embedder:\n"
     "            for start in range(0, len(todo), FLUSH_EVERY):\n"
     "                fresh = [(sha, ord_,\n"
     "                          embedder.encode(wanted[(sha, ord_)]).tobytes())\n"
     "                         for sha, ord_ in todo[start:start + FLUSH_EVERY]]",
     "        processes = 0  # mutated: a fresh process for every chunk\n"
     "\n"
     "        def _once(text):  # mutated\n"
     "            nonlocal processes\n"
     "            processes += 1\n"
     "            with Embedder() as one:\n"
     "                return one.encode(text).tobytes()\n"
     "\n"
     "        if True:\n"
     "            for start in range(0, len(todo), FLUSH_EVERY):\n"
     "                fresh = [(sha, ord_, _once(wanted[(sha, ord_)]))\n"
     "                         for sha, ord_ in todo[start:start + FLUSH_EVERY]]",
     "4  one Node process embeds many chunks"),

    # -- keyed on the hash, and the duty that comes with it (R2) -----------
    ("everything is embedded again on every run",
     "morpho_homegraph/embed.py",
     "    todo = sorted(wanted.keys() - have)",
     "    todo = sorted(wanted.keys())  # mutated: no reuse at all",
     "5  a second embedding after a full L2 rewrite embeds zero chunks"),

    ("vectors are keyed on the path instead of the content hash",
     "morpho_homegraph/embed.py",
     '            "SELECT DISTINCT sha256, text FROM content "',
     '            "SELECT DISTINCT path, text FROM content "  # mutated',
     "6  editing a file removes the vectors for its old text"),

    # The stale vector no key can see: same `(sha, ord)`, different text under
    # it. Found by review, not by the gates -- the first version of this file
    # had a comment claiming the key covered it.
    ("moved chunk boundaries reuse vectors cut the old way",
     "morpho_homegraph/embed.py",
     '    if store.get_meta("embed_chunking") not in (None, CHUNKING):',
     "    if False:  # mutated: the parameters are nobody's business",
     "5c changed chunk parameters re-embed the whole store"),

    ("the worker may be silent forever",
     "morpho_homegraph/embed.py",
     "            if not select.select([fd], [], [], READ_TIMEOUT)[0]:",
     "            if False:  # mutated: wait for a process that never answers",
     "20 a worker that never answers is refused, not waited on forever"),

    ("nothing is ever considered stale",
     "morpho_homegraph/embed.py",
     "    stale = sorted(have - wanted.keys())",
     "    stale = []  # mutated: a vector outlives its text",
     "17 a vector for a hash no longer in content is gone after embedding"),

    ("stale vectors are computed and never deleted",
     "morpho_homegraph/embed.py",
     '        db.executemany("DELETE FROM vectors WHERE sha256 = ? AND ord = ?",\n'
     "                       stale)",
     "        pass  # mutated: the delete is worked out and not run",
     "7  deleting a file removes its vectors"),

    # -- what the store records about the model (R6) -----------------------
    ("the dimension is not written to meta",
     "morpho_homegraph/embed.py",
     '    store.set_meta("embed_dim", str(DIM))',
     "    pass  # mutated: nothing records what these vectors are",
     "8  model id and dimension are written to meta"),

    ("a store with another dimension is embedded into anyway",
     "morpho_homegraph/embed.py",
     "    if dim and dim != str(DIM):",
     "    if False:  # mutated: mix them",
     "9  a store recording another dimension refuses instead of mixing"),

    ("a store embedded with another model is topped up",
     "morpho_homegraph/embed.py",
     "    if model and model != MODEL_ID:",
     "    if False:  # mutated: two models in one table",
     "9b a store recording another model refuses the same way"),

    # The control. Without gate 18, a command that always refuses passes every
    # gate about refusing.
    ("the layer refuses whatever the store holds",
     "morpho_homegraph/embed.py",
     "    if model and model != MODEL_ID:",
     "    if True:  # mutated: always refuse",
     "18 an ordinary embedding exits 0"),

    # -- a refusal is a refusal (R7) ---------------------------------------
    ("a refusal is reported as success",
     "morpho_homegraph/cli.py",
     '        print("REFUSED  %s" % exc, file=sys.stderr)\n'
     "        return 2",
     '        print("REFUSED  %s" % exc, file=sys.stderr)\n'
     "        return 0  # mutated: exit 0 on a refusal",
     "10 a missing model refuses, names the path, and writes no vectors"),

    ("the refusal does not say what is missing",
     "morpho_homegraph/cli.py",
     '        print("REFUSED  %s" % exc, file=sys.stderr)\n'
     "        return 2",
     '        print("REFUSED", file=sys.stderr)  # mutated: no path\n'
     "        return 2",
     "10 a missing model refuses, names the path, and writes no vectors"),

    ("a missing node is not checked for",
     "morpho_homegraph/embed.py",
     "    if shutil.which(node_binary()) is None:",
     "    if False:  # mutated: find out when it is spawned",
     "11 a missing node refuses the same way"),

    # -- the vectors themselves (R8) ---------------------------------------
    ("vectors are stored as float64",
     "morpho_homegraph/embed.py",
     "                          embedder.encode(wanted[(sha, ord_)]).tobytes())",
     "                          embedder.encode(wanted[(sha, ord_)])"
     ".astype(np.float64).tobytes())  # mutated",
     "1  a vector is 384 float32 and comes back unchanged"),

    ("hits are ordered worst first",
     "morpho_homegraph/embed.py",
     '    hits.sort(key=lambda hit: (-hit["score"], hit["path"]))',
     '    hits.sort(key=lambda hit: (hit["score"], hit["path"]))  # mutated',
     "12 cosine ranks the closest text first, and the order is stable"),

    ("stored vectors are read back as float64",
     "morpho_homegraph/embed.py",
     "    matrix = np.frombuffer(blobs, dtype=np.float32).reshape(len(rows), DIM)",
     "    matrix = np.frombuffer(blobs, dtype=np.float64)"
     ".reshape(len(rows), DIM)  # mutated",
     "12 cosine ranks the closest text first, and the order is stable"),

    # -- how far the run got (R9), and house rule 6 ------------------------
    ("coverage always reports the project as fully embedded",
     "morpho_homegraph/embed.py",
     "    return embedded, len(wanted)",
     "    return len(wanted), len(wanted)  # mutated: always complete",
     "14 a partly embedded project answers and says N of M"),

    ("a project with no vectors answers 'no matches'",
     "morpho_homegraph/cli.py",
     # `if not embedded:` alone is in `_semantic` and in `_fused`, and
     # `replace(..., 1)` would take whichever comes first. The line above it
     # differs by one name (`expected` against `chunks`), and gate 19 is
     # `_semantic`'s.
     "            embedded, expected = embed.coverage(store)\n"
     "            if not embedded:",
     "            embedded, expected = embed.coverage(store)\n"
     "            if False:  # mutated: silence instead of a refusal",
     "19 a project with no vectors refuses instead of finding nothing"),

    # -- there is no batch size (R5) ---------------------------------------
    #
    # M-3b: groups of 32 measured -40 % speed and 5x memory on both trees.
    # The knob is what gets re-added, so the gate is about the knob existing.
    ("a group size is re-introduced",
     "morpho_homegraph/embed.py",
     "CHUNK = 1000",
     "BATCH_SIZE = 32  # mutated: the knob M-3b removed\nCHUNK = 1000",
     "16 there is no batch size or group size anywhere in the module"),

    # -- embedding is its own command (R3) ---------------------------------
    ("update embeds as well, so the open blocks again",
     "morpho_homegraph/service.py",
     "        l4 = search.build(store)",
     "        l4 = search.build(store)\n"
     "        embed.build(store)  # mutated: M-3's 219.9 s at open",
     "15 update fills L2, L3 and L4 and does not embed"),

    ("the embed command does not embed",
     "morpho_homegraph/cli.py",
     "            tally = embed.build(store)",
     '            tally = {"embedded": 0, "reused": 0, "removed": 0,\n'
     '                     "chunks": 0}  # mutated: no caller left',
     "15b embed.build has a caller in the package, and it is not update"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp9.py", prefix="mut9-", timeout=1800))
