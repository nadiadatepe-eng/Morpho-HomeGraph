#!/usr/bin/env python3
"""Mutation test for CP-7B -- the update path, and the ways it can look done.

The failure here is not an exception. It is an index that is empty, or stale,
or built in the wrong order, while the command exits 0 and says nothing. So
most of the mutations below make `update` *more* willing: build the graph
first, keep the scope it saved last time, index a project the catalogue has
never seen, update one that has been moved or deleted.

The rest aim at the opposite failure. A command that refuses whenever the
result would be empty passes every gate about refusing, and gates 6 and 18 are
the controls that catch it.

Run:
    python3 tests/mutate_cp7b.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the order (R1) ----------------------------------------------------
    #
    # The dangerous one: a graph built before the content it reads writes zero
    # edges and exits 0. "No links in this project" and "the graph ran too
    # early" are the same output.
    ("the graph is built before the content it reads",
     "morpho_homegraph/service.py",
     "        l2 = content.build(store, l0, chosen)\n"
     "        l3 = graph.build(store, scope_root=root)",
     "        l3 = graph.build(store, scope_root=root)  # mutated\n"
     "        l2 = content.build(store, l0, chosen)",
     "2  the graph has edges, so it ran after the content"),

    ("the content layer is never built",
     "morpho_homegraph/service.py",
     "        l2 = content.build(store, l0, chosen)",
     "        l2 = {\"read\": 0, \"unread\": 0}  # mutated",
     "1  update fills scope, L2 and L3 in one command"),

    ("the graph layer is never built",
     "morpho_homegraph/service.py",
     "        l3 = graph.build(store, scope_root=root)",
     "        l3 = {\"edges\": 0, \"ambiguous\": 0, \"outside\": 0}"
     "  # mutated",
     "1  update fills scope, L2 and L3 in one command"),

    ("the scope is never saved, so no reader can see what was used",
     "morpho_homegraph/service.py",
     "        scope.save(store, chosen)",
     "        pass  # mutated: the scope stays in this process",
     "13 the scope used is saved, and loads back with the same answers"),

    # -- the scope is recomputed, never reused (R6) ------------------------
    ("the saved scope is loaded and reused instead of recomputed",
     "morpho_homegraph/service.py",
     "        chosen = chosen_scope(root)",
     "        chosen = scope.load(store) or chosen_scope(root)  # mutated",
     "11 editing .gitignore between runs changes what L2 holds"),

    ("every folder is treated as a repo, so JUNK is never excluded",
     "morpho_homegraph/service.py",
     "    if scope.is_repo(root):\n"
     "        chosen, _patterns = scope.from_repo(root)\n"
     "        return chosen\n"
     "    return scope.from_folder(root)",
     "    chosen, _patterns = scope.from_repo(root)  # mutated\n"
     "    return chosen",
     "10 a folder without .git excludes the junk directories"),

    ("every folder is treated as a plain folder, so .gitignore is ignored",
     "morpho_homegraph/service.py",
     "    if scope.is_repo(root):\n"
     "        chosen, _patterns = scope.from_repo(root)\n"
     "        return chosen\n"
     "    return scope.from_folder(root)",
     "    return scope.from_folder(root)  # mutated",
     "11 editing .gitignore between runs changes what L2 holds"),

    # -- L0 is read, never built here (R2) ---------------------------------
    ("update takes the L0 guard as well, so a scan blocks every update",
     "morpho_homegraph/service.py",
     "    with Store(l0_path(), read_only=True, role=L0) as l0:",
     "    _l0_guard = StoreLock(str(l0_path())).acquire()  # mutated: one writer\n"
     "    with Store(l0_path(), read_only=True, role=L0) as l0:",
     "3  update succeeds while another process holds the L0 guard"),

    # -- an empty layer always has a reason, and it is said out loud (R3) --
    ("a catalogue that has never seen the project is used anyway",
     "morpho_homegraph/service.py",
     "        if not knows(l0, root):",
     "        if False:  # mutated: index it empty and exit 0",
     "5  an L0 that does not know the project root is refused"),

    ("a missing catalogue is treated as an empty one",
     "morpho_homegraph/service.py",
     "    if not l0_path().is_file():",
     "    if False:  # mutated",
     "4  a missing L0 is refused, naming the command that builds it"),

    ("the refusal does not name the command that builds the catalogue",
     "morpho_homegraph/service.py",
     "        raise Refused(\"the catalogue has not been built: \"\n"
     "                      \"morphofiles-graph scan\")",
     "        raise Refused(\"nothing to do here\")  # mutated",
     "4  a missing L0 is refused, naming the command that builds it"),

    # The control's own mutation. `knows` answering no to everything refuses
    # every project, which passes gates 4 and 5 and breaks the command.
    ("the catalogue is judged not to know any root at all",
     "morpho_homegraph/scan.py",
     "    return bool(l0_store.db.execute(",
     "    return False and bool(l0_store.db.execute(  # mutated",
     "6  a catalogued but empty folder is 0 rows and exit 0, not a refusal"),

    ("knowing the root requires a file under it, so an empty folder is refused",
     "morpho_homegraph/scan.py",
     "        \"SELECT 1 FROM files WHERE path = ? OR path LIKE ? LIMIT 1\",\n"
     "        (root, root + os.sep + \"%\")).fetchone())",
     "        \"SELECT 1 FROM files WHERE kind = 'file' AND path LIKE ?"
     " LIMIT 1\",  # mutated\n"
     "        (root + os.sep + \"%\",)).fetchone())",
     "6  a catalogued but empty folder is 0 rows and exit 0, not a refusal"),

    # -- moved and deleted (R4, R5) ----------------------------------------
    ("a moved project is rebuilt at its old path instead of being refused",
     "morpho_homegraph/service.py",
     "            root = identity.open_project(project_id, l0)\n"
     "        except identity.Moved as exc:\n"
     "            raise Refused(str(exc)) from exc",
     "            root = identity.open_project(project_id, l0)\n"
     "        except identity.Moved:  # mutated: use the old path\n"
     "            pass",
     "7  a moved project is refused, naming where it went, nothing written"),

    ("a project marked deleted is updated like any other",
     "morpho_homegraph/service.py",
     "    if (store.get_meta(\"state\") or identity.LIVING)"
     " != identity.LIVING:",
     "    if False:  # mutated: deleted is just a label",
     "8  a deleted project is refused, and the refusal names the way back"),

    ("the deleted refusal does not name the way back",
     "morpho_homegraph/service.py",
     "        raise Refused(\"%s is marked deleted and is waiting to be"
     " retired -- \"\n"
     "                      \"restore it first (see snapshot.restore)\""
     " % project_id)",
     "        raise Refused(\"%s is marked deleted\" % project_id)  # mutated",
     "8  a deleted project is refused, and the refusal names the way back"),

    # -- the guard (R9) ----------------------------------------------------
    ("the refusal path is dropped, so a contended update dies instead",
     "morpho_homegraph/cli.py",
     # The needle reaches into the `try:` below on purpose: `cmd_scan` has the
     # same three lines, it comes first in the file, and `replace(..., 1)`
     # would land there instead -- where no CP-7B gate is looking. That is the
     # two-copies trap `cli.py` documents on `_guard_or_refuse` itself.
     "    barrier = _guard_or_refuse(store_db, \"update\")\n"
     "    if barrier is None:\n"
     "        return 2\n"
     "    try:\n"
     "        with Store(store_db) as store:\n"
     "            try:\n"
     "                built = service.build_layers(store, project_id)",
     "    barrier = _guard(store_db)  # mutated: no refusal path\n"
     "    try:\n"
     "        with Store(store_db) as store:\n"
     "            try:\n"
     "                built = service.build_layers(store, project_id)",
     "16 a second update on the same project is refused, not queued"),

    # -- what a reader can see (R8) ----------------------------------------
    #
    # The rule the finding paid for: the layers were empty for five
    # checkpoints because `status` showed id, path, schema and a timestamp,
    # all of which are true of a store holding nothing.
    ("status stops showing the layers",
     "morpho_homegraph/cli.py",
     "        print(\"%-14s %d rules\" % (\"scope\", rules))",
     "        pass  # mutated: back to id, path and a timestamp",
     "14 status shows the layers, so an empty index cannot look finished"),

    ("status reports the layer counts from meta instead of from the rows",
     "morpho_homegraph/cli.py",
     "        rules, rows, edges = store.db.execute(\n"
     "            \"SELECT (SELECT COUNT(*) FROM scope),\"\n"
     "            \"       (SELECT COUNT(*) FROM content),\"\n"
     "            \"       (SELECT COUNT(*) FROM edges)\").fetchone()",
     "        rules = int(store.get_meta(\"l2_read\") or 0)  # mutated\n"
     "        rows = int(store.get_meta(\"l2_read\") or 0)\n"
     "        edges = int(store.get_meta(\"l3_edges\") or 0)",
     "14 status shows the layers, so an empty index cannot look finished"),

    ("update stops recording when it last ran",
     "morpho_homegraph/service.py",
     "    store.set_meta(\"last_update\", stamp())\n"
     "    return {\"recreated\": False,",
     "    pass  # mutated: no timestamp\n"
     "    return {\"recreated\": False,",
     "17 update records last_update and the per-layer counts"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp7b.py", prefix="mut7b-", timeout=900))
