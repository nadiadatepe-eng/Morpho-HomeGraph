#!/usr/bin/env python3
"""Mutation test for CP-11 -- a view that draws, and draws something else.

None of these raise. A bucket rule with no cap draws every folder through an
extra node; one that never buckets draws a folder of 500 files as 500 spokes;
an export that escapes a filename shows a name nobody has on disk; a layout
without a seed draws a different picture every time and looks alive doing it.
Every mutation below leaves a command that exits 0 and a page that renders.

The attribution mutations are the odd ones out: they do not change a picture
at all. A NOTICE that lists no hashes, or one that calls a derived file
copied, is wrong in a way only a reader ever notices -- which is exactly why
it needs a gate rather than a review.

Run:
    python3 tests/mutate_cp11.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- provenance (R1, R2) ------------------------------------------------
    ("the recorded hash no longer matches the engine",
     "NOTICE",
     "bb87b5c3050492c4e3996da6ee27cf4b9b2fca97cf619e2416f56971c2286304",
     "0000000000000000000000000000000000000000000000000000000000000000",
     "2  every recorded sha256 matches the file on disk"),

    ("an engine file is dropped from the NOTICE",
     "NOTICE",
     "  3be7101103388eb1df621dc9d026cb5225c86079adaa16457aa202dc8d3636c2  view/graphs_engine/build.sh\n",
     "",
     "1  the NOTICE names the source, the commit and every engine file"),

    ("the derived layout is listed as copied unchanged",
     "NOTICE",
     "DERIVED, NOT COPIED.",
     "ALSO COPIED UNCHANGED, or so this line would have it.",
     "4  the derived layout is listed as derived, not as copied"),

    # -- the bucket rule (R4, R5) -------------------------------------------
    ("there is no fanout cap, so every folder is bucketed",
     "morpho_homegraph/view.py",
     "        if len(files) <= K:\n"
     "            continue",
     "        if False:  # mutated: bucket everything\n"
     "            continue",
     "6  a folder at or under the cap gets no bucket at all"),

    # The needle carries its comment: `K = 4` on its own also occurs in the
    # module docstring, and the first version of this mutation edited the
    # *prose* while the code went on bucketing. A mutation that lands in a
    # comment is one that was never tried.
    ("the cap is so high that nothing is ever bucketed",
     "morpho_homegraph/view.py",
     "# measured on filesystems by us -- an honest loan, and the answer key says so.\nK = 4",
     "# measured on filesystems by us -- an honest loan, and the answer key says so.\n"
     "K = 4096  # mutated: one folder, five hundred spokes",
     "5  a folder above the fanout cap gets one bucket per file type"),

    ("every file type lands in one bucket",
     "morpho_homegraph/view.py",
     '            bucket = "%s%s%s" % (folder, SEP, nodes[file_id]["type"])',
     '            bucket = "%s%sall" % (folder, SEP)  # mutated',
     "5  a folder above the fanout cap gets one bucket per file type"),

    ("the bucket adds an edge instead of moving one",
     "morpho_homegraph/view.py",
     "            parent_of[file_id] = bucket",
     "            pass  # mutated: the file keeps hanging off the folder too",
     "7  a bucketed file hangs off the bucket, and off nothing else"),

    # -- the tree, and what it exports (R5, R9) -----------------------------
    ("a folder's parent is never recorded, so the tree falls apart",
     "morpho_homegraph/view.py",
     "                parent_of[child] = walked",
     "                pass  # mutated: no parent for this folder",
     "8  every node but the root has exactly one parent"),

    ("paths are exported as they are on this machine",
     "morpho_homegraph/view.py",
     "        relative = os.path.relpath(absolute, root)",
     "        relative = absolute  # mutated: the home directory travels along",
     "9  no absolute or escaping path anywhere in the exported data"),

    ("the export is not sorted, so two runs differ",
     "morpho_homegraph/view.py",
     '    return {"root": ".", "nodes": sorted(nodes.values(),\n'
     '                                         key=lambda node: node["id"]),\n'
     '            "edges": sorted(edges, key=lambda e: (e["from"], e["to"]))}',
     '    return {"root": ".", "nodes": list(nodes.values()),  # mutated\n'
     '            "edges": edges}',
     "10 two exports agree, and the file is written in sorted order"),

    ("a hostile filename is escaped on the way out",
     "morpho_homegraph/view.py",
     '        nodes[leaf] = {"id": leaf, "kind": "file", "name": parts[-1],',
     '        nodes[leaf] = {"id": leaf, "kind": "file",  # mutated: escaped\n'
     '                       "name": parts[-1].replace("<", "&lt;"),',
     "15 a filename that is live markup survives the export untouched"),

    ("the separator is one a filename may contain",
     "morpho_homegraph/view.py",
     'SEP = "\\0"',
     'SEP = "\\x1f"  # mutated: legal in a POSIX filename, so it can collide',
     "7b a filename shaped like a bucket id collides with nothing"),

    ("the view is written over its own source",
     "morpho_homegraph/view.py",
     "    if out.resolve() == page.resolve() or page.resolve() in out.resolve().parents:",
     "    if False:  # mutated: delete js/ and copy from the hole",
     "16b writing the view into its own source is refused"),

    # -- the picture (R7, R8) -----------------------------------------------
    ("the layout is not seeded",
     "view/js/layout.js",
     "  const random = mulberry32(p.seed);",
     "  const random = Math.random;  // mutated: a new picture every time",
     "11 the same graph and the same seed give identical positions"),

    ("the seed is ignored, so every seed draws the same picture",
     "view/js/layout.js",
     "  const random = mulberry32(p.seed);",
     "  const random = mulberry32(1337);  // mutated: one seed for all",
     "12 a different seed gives a different picture"),

    ("a graph too big for the buffers is drawn anyway",
     "view/js/layout.js",
     "  if (a.points.length < n * 3 || a.links.length < graph.edges.length * 2) {",
     "  if (false) {  // mutated: lose the last nodes in silence",
     "13 a graph larger than the engine's buffers is refused"),

    # -- no markup path (R6) ------------------------------------------------
    ("the hover label is built as markup",
     "view/js/draw.js",
     "    hover.textContent = `${node.kind}  ${node.path.replaceAll(\"\\u0000\", \" > \")}`;",
     "    hover.innerHTML = `${node.kind}  ${node.path}`;  // mutated",
     "14 the page has no markup path, and sets text instead"),

    # -- the command (R10) --------------------------------------------------
    ("a project with no content is drawn as an empty picture",
     "morpho_homegraph/view.py",
     "    if not rows:\n"
     '        raise NothingToDraw("this project has no content yet")',
     "    if False:  # mutated: draw nothing and call it a drawing\n"
     '        raise NothingToDraw("this project has no content yet")',
     "18 a project with no content refuses and names update"),

    ("the view folder is written without the engine",
     "morpho_homegraph/view.py",
     'PAGE = ("index.html", "js", "graphs_engine")',
     'PAGE = ("index.html", "js")  # mutated: a page with no engine',
     "16 the view folder holds the page, the engine and the data"),

    # The control. Without gate 17, everything above is satisfied by a command
    # that refuses whatever it is given.
    ("the view refuses whatever it is given",
     "morpho_homegraph/view.py",
     "    if store.role != PROJECT:",
     "    if True:  # mutated: always refuse",
     "17 an ordinary view exits 0"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp11.py", prefix="mut11-", timeout=900))
