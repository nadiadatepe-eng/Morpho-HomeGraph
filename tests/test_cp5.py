#!/usr/bin/env python3
"""CP-5 -- L3, the graph.

The answer key is `tests/gold/FASIT-cp5.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

Two claims carry this checkpoint, and both are easy to gate badly. The first
is that identity is *content*, so a fixture without duplicate files cannot tell
a hash-keyed graph from a path-keyed one -- there are two identical files in
here for that reason alone. The second is that a `partial` answer means
something, which a note that always says `partial` satisfies just as well:
gate 16 is the control that makes gate 8 worth running.

Run:
    python3 tests/test_cp5.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import content, graph  # noqa: E402
from morpho_homegraph.lock import StoreLock, Unguarded  # noqa: E402
from morpho_homegraph.scan import scan  # noqa: E402
from morpho_homegraph.scope import Scope  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, l0_path, new_project)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(58)


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def build_tree(root):
    """A corpus with one of everything the rules name.

    The two identical files are the point of the fixture: without them, a
    graph keyed on paths passes every gate here.
    """
    made = {}
    # a.md states a link by path (stated) and one by bare name (derived).
    # a -> b is DERIVED (a bare name that has to be resolved), b -> c is
    # STATED (a path that resolves exactly). That order is deliberate: the
    # two-hop answer from `a` then contains exactly one derived edge, the one
    # gate 10 re-asserts, so gate 11 can see it change. With the derived edge
    # anywhere else, the two-hop answer stays partial for a reason that has
    # nothing to do with what was re-asserted -- which is how that gate first
    # failed, 2026-08-04.
    made["a"] = write(os.path.join(root, "a.md"),
                      "see [[b]] and [[gone]]\n"
                      "and [ute](https://example.org)\n")
    made["b"] = write(os.path.join(root, "b.md"),
                      "b links on to [c](sub/c.md)\n")
    # c -> twin is DERIVED, and it is the *second* hop out of b. Gate 11c
    # needs an answer whose first edge is stated and whose later edge is not:
    # a read path that stops at the first edge agrees with a correct one on
    # every other answer in this fixture.
    made["c"] = write(os.path.join(root, "sub", "c.md"),
                      "leaf, see [[twin1]]\n")
    # Two files, identical bytes: one node, two paths.
    made["twin1"] = write(os.path.join(root, "twin1.md"), "identical\n")
    made["twin2"] = write(os.path.join(root, "sub", "twin2.md"), "identical\n")
    # An ambiguous name: two files called `dup.md`, so [[dup]] resolves to
    # neither. Locked decision 5 -- ambiguity is refused, not picked between.
    write(os.path.join(root, "one", "dup.md"), "first\n")
    write(os.path.join(root, "two", "dup.md"), "second\n")
    made["asks"] = write(os.path.join(root, "asks.md"), "which [[dup]]?\n")
    # R9: a stem shared by two *different* suffixes. `~/.claude/memory` is
    # 150 of these, and before the rule existed every wikilink in it was
    # ambiguous and the layer produced no edges at all.
    made["side_md"] = write(os.path.join(root, "side.md"), "the markdown one\n")
    made["side_emb"] = write(os.path.join(root, "side.embedding"),
                             "the sidecar one\n")
    # The two linkers must differ in content, or R1 makes them one node and
    # that node carries *both* edges -- which is correct and is gate 21, but
    # it is not what gates 17 and 18 are asking about.
    made["from_md"] = write(os.path.join(root, "from_md.md"),
                            "from the markdown side: see [[side]]\n")
    made["from_emb"] = write(os.path.join(root, "from_emb.embedding"),
                             "from the sidecar side: see [[side]]\n")
    # Identical text in two differently-suffixed files: one node, and the
    # suffix rule fires once per *path*, so the node ends up with both edges.
    made["twin_md"] = write(os.path.join(root, "twin_link.md"),
                            "both of us say [[side]]\n")
    made["twin_emb"] = write(os.path.join(root, "twin_link.embedding"),
                             "both of us say [[side]]\n")
    # Three files sharing a stem AND a suffix: narrowing cannot make this one
    # unique, so it stays refused.
    for n in ("one", "two", "three"):
        write(os.path.join(root, n, "trip.md"), "trip in %s\n" % n)
    made["asks3"] = write(os.path.join(root, "asks3.md"), "which [[trip]]?\n")
    # A file that could not be read gets no node.
    made["binary"] = os.path.join(root, "image.bin")
    with open(made["binary"], "wb") as fh:
        fh.write(b"\x89PNG\0\0\0")
    return made


def sha_of(store, path):
    row = store.db.execute(
        "SELECT sha256 FROM content WHERE path = ?", (path,)).fetchone()
    return row[0] if row else None


def gates_pure():
    """4, 5, 6, 16 -- the edge contract, no store required."""
    # Caught broadly, passing only on TypeError: give `method` a default and
    # the call gets as far as the store, and an AttributeError from `None`
    # would take the suite down instead of naming this gate.
    try:
        graph.edge(None, "a", "b", confidence=1.0)  # type: ignore[call-arg]
        fired = "nothing"
    except TypeError as exc:
        fired = "TypeError" if "method" in str(exc) else str(exc)[:30]
    except Exception as exc:  # noqa: BLE001
        fired = type(exc).__name__
    check("4  edge() without method= is refused by Python itself",
          fired == "TypeError", "raised %s" % fired)

    class FakeDb:
        def execute(self, *_a):
            raise AssertionError("the store was touched before validation")

    for bad in (-0.1, 1.1):
        # `FakeDb` raises if it is reached at all, so an unchecked
        # confidence surfaces as an AssertionError rather than as a store
        # write. Caught broadly and passed only on `BadEdge` -- the point is
        # *which* refusal fired, and a crash names none.
        try:
            graph.edge(FakeDb(), "a", "b", method=graph.MD_INLINE,
                       confidence=bad)
            fired = "nothing"
        except graph.BadEdge:
            fired = "BadEdge"
        except Exception as exc:  # noqa: BLE001
            fired = type(exc).__name__
        check("5  a confidence outside [0, 1] is refused (%r)" % bad,
              fired == "BadEdge", "refused by %s" % fired)

    stated = [{"method": graph.MD_INLINE, "confidence": 1.0},
              {"method": graph.MD_WIKILINK_PATH, "confidence": 1.0}]
    mixed = stated + [{"method": graph.MD_WIKILINK_UNIQUE, "confidence": 0.9}]
    check("6  a stated edge carries confidence 1.0",
          all(e["confidence"] == 1.0 for e in stated), "by construction")
    check("7  an answer resting only on stated edges is complete",
          graph.provenance_note(stated) == graph.PROV_COMPLETE,
          graph.provenance_note(stated))
    check("8  one derived edge makes the answer partial",
          graph.provenance_note(mixed) == graph.PROV_PARTIAL,
          graph.provenance_note(mixed))
    # The discriminating input: a derived method that a future caller handed
    # confidence 1.0. Checking the number alone lets it read as stated, and
    # every other input in this gate agrees with both versions.
    confident_guess = [{"method": graph.MD_WIKILINK_UNIQUE, "confidence": 1.0}]
    check("8c the method decides, not only the number",
          graph.provenance_note(confident_guess) == graph.PROV_PARTIAL,
          graph.provenance_note(confident_guess))
    # Without this, a note hard-coded to `partial` passes gate 8 and proves
    # nothing at all.
    check("16 a note that cannot say complete is caught",
          graph.provenance_note([]) == graph.PROV_COMPLETE
          and graph.provenance_note(stated) == graph.PROV_COMPLETE,
          "empty and all-stated both complete")

    # 12: one function produces the verdict. A read path with its own copy is
    # the failure the answer key names, and it is a *structural* claim, so it
    # is checked structurally rather than by exercising every caller.
    sources = []
    pkg = os.path.join(REPO, "morpho_homegraph")
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py"):
            with open(os.path.join(pkg, name), encoding="utf-8") as fh:
                sources.append((name, fh.read()))
    # The constants themselves, not `return` in front of them: a second copy
    # of the rule can perfectly well be an inline conditional, and that is the
    # form it took when the sweep planted one on 2026-08-04.
    producers = [(name, line.strip())
                 for name, text in sources
                 for line in text.splitlines()
                 if ("PROV_PARTIAL" in line or "PROV_COMPLETE" in line)
                 and not line.strip().startswith("#")
                 and "PROV_COMPLETE, PROV_PARTIAL =" not in line]
    check("12 the provenance verdict is produced in exactly one function",
          len(producers) == 2 and {n for n, _ in producers} == {"graph.py"},
          "%d return sites in %s"
          % (len(producers), sorted({n for n, _ in producers})))


def gates_built(made, root):
    project_id, db = new_project()
    l0_db = l0_path()
    l0_db.parent.mkdir(parents=True, exist_ok=True)
    scope = Scope().add(root)
    guard0 = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, root, deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    content.build(store, l0, scope)
                    # A build that raises would take gates 1-11 with it and
                    # report a crash, which names no gate. Reading rows L2
                    # could not read is exactly how it raises.
                    try:
                        tally = graph.build(store, scope_root=root)
                    except Exception as exc:  # noqa: BLE001
                        check("3  a file L2 could not read has no node", False,
                              "the build raised %s" % type(exc).__name__)
                        tally = None
                    if tally is not None:
                        _gates_graph(store, made, root, tally)
            finally:
                guard.release()

            # 14: the graph is a project's, not the shared store's.
            try:
                graph.build(l0)
                fired = "nothing"
            except graph.WrongStore:
                fired = "WrongStore"
            except Exception as exc:  # noqa: BLE001
                fired = type(exc).__name__
            check("14 a graph aimed at the L0 store is refused by role",
                  fired == "WrongStore", "refused by %s" % fired)

            # 15: opened under the guard, guard dropped, then written -- so
            # what is under test is the write and not Store's open-time check.
            _id2, fresh_db = new_project()
            outlived = StoreLock(str(fresh_db)).acquire()
            fresh = Store(fresh_db, role=PROJECT)
            outlived.release()
            try:
                graph.build(fresh)
                refused = False
            except Unguarded:
                refused = True
            written = fresh.db.execute(
                "SELECT COUNT(*) FROM edges").fetchone()[0]
            fresh.close()
            check("15 a build without the write guard writes nothing",
                  refused and written == 0,
                  "%s, %d edges written"
                  % ("refused" if refused else "ALLOWED", written))
    finally:
        guard0.release()


def _gates_graph(store, made, root, tally):
    twin1 = sha_of(store, made["twin1"])
    twin2 = sha_of(store, made["twin2"])
    check("1  two files with identical bytes are one node with two paths",
          twin1 == twin2 is not None
          and graph.paths_of(store, twin1) == sorted([made["twin1"],
                                                      made["twin2"]]),
          "%d paths on one hash" % len(graph.paths_of(store, twin1)))

    files_read = store.db.execute(
        "SELECT COUNT(*) FROM content WHERE text IS NOT NULL").fetchone()[0]
    node_list = graph.nodes(store)
    check("2  nodes are never more than the files that were read",
          len(node_list) < files_read,
          "%d nodes, %d files read" % (len(node_list), files_read))

    check("3  a file L2 could not read has no node",
          sha_of(store, made["binary"]) is None
          and made["binary"] not in sum(
              (graph.paths_of(store, n) for n in node_list), []),
          "the binary is in content with a reason, not in the graph")

    a, b, c = (sha_of(store, made[k]) for k in ("a", "b", "c"))
    one_hop = graph.neighbours(store, a, hops=1)
    check("8b a.md's own answer is partial -- its one edge is derived",
          one_hop["provenance"] == graph.PROV_PARTIAL
          and set(one_hop["nodes"]) == {b},
          "%s, %d neighbours"
          % (one_hop["provenance"], len(one_hop["nodes"])))

    check("9  an ambiguous [[name]] produces no edge, and is counted",
          tally["ambiguous"] == 2
          and not store.db.execute(
              "SELECT 1 FROM edges WHERE src = ?",
              (sha_of(store, made["asks"]),)).fetchall(),
          "%d ambiguous, %d outside"
          % (tally["ambiguous"], tally["outside"]))

    # 10 and 11: the derived a -> c edge is re-asserted as stated, and both
    # the edge's own answer and the two-hop answer through b must change.
    # Nothing is recomputed and no cache is invalidated -- provenance is read,
    # not stored, which is the whole reason this can be gated at all.
    before_one = graph.neighbours(store, a, hops=1)["provenance"]
    before_two = graph.neighbours(store, a, hops=2)["provenance"]
    with store.writing() as db:
        graph.edge(db, a, b, method=graph.MD_WIKILINK_PATH, confidence=1.0)
        db.commit()
    after_one = graph.neighbours(store, a, hops=1)["provenance"]
    after_two = graph.neighbours(store, a, hops=2)["provenance"]
    check("10 re-asserting a derived edge turns partial into complete",
          before_one == graph.PROV_PARTIAL and after_one == graph.PROV_COMPLETE,
          "%s -> %s" % (before_one, after_one))
    check("11 the same holds two hops out, not only on the edge itself",
          before_two == graph.PROV_PARTIAL and after_two == graph.PROV_COMPLETE,
          "%s -> %s" % (before_two, after_two))

    # -- R9, gates 17-20 -------------------------------------------------
    md, emb = sha_of(store, made["side_md"]), sha_of(store, made["side_emb"])
    from_md = graph.neighbours(store, sha_of(store, made["from_md"]), hops=1)
    from_emb = graph.neighbours(store, sha_of(store, made["from_emb"]), hops=1)
    check("17 an ambiguous stem resolves to the linker's own suffix",
          from_md["nodes"] == [md],
          "a .md link landed on %s"
          % ("the .md" if from_md["nodes"] == [md] else from_md["nodes"]))
    # The rule is symmetric. If it merely preferred `.md` it would pass gate 17
    # and be wrong about every other corpus.
    check("18 the same rule sends an .embedding link to the .embedding",
          from_emb["nodes"] == [emb],
          "an .embedding link landed on %s"
          % ("the .embedding" if from_emb["nodes"] == [emb] else
             from_emb["nodes"]))
    check("19 narrowing without uniqueness is still refused",
          not store.db.execute(
              "SELECT 1 FROM edges WHERE src = ?",
              (sha_of(store, made["asks3"]),)).fetchall(),
          "three files share stem and suffix, no edge")
    method_used = store.db.execute(
        "SELECT method FROM edges WHERE src = ?",
        (sha_of(store, made["from_md"]),)).fetchone()
    check("20 the narrowed edge names its own method",
          method_used and method_used[0] == graph.MD_WIKILINK_SAME_EXT,
          method_used[0] if method_used else "no edge")

    # 21: R1 meets R9. Identical text in a `.md` and an `.embedding` is one
    # node, but resolution reads the *path's* suffix -- so the single node
    # comes out holding both edges. Correct under both rules, surprising to
    # read, and pinned here so it cannot change without someone deciding to.
    twin = sha_of(store, made["twin_md"])
    check("21 one node, two paths, two differently-resolved edges",
          twin == sha_of(store, made["twin_emb"])
          and sorted(graph.neighbours(store, twin, hops=1)["nodes"])
          == sorted([md, emb]),
          "the shared node links to both the .md and the .embedding")

    # 11c: from b the first edge is stated and the derived one is a hop
    # further out. An answer that reads only its first edge calls this
    # complete, and agrees with a correct one everywhere else in this fixture.
    from_b_one = graph.neighbours(store, b, hops=1)
    from_b_two = graph.neighbours(store, b, hops=2)
    check("11c a guess further out still makes the answer partial",
          from_b_one["provenance"] == graph.PROV_COMPLETE
          and from_b_two["provenance"] == graph.PROV_PARTIAL,
          "one hop %s, two hops %s"
          % (from_b_one["provenance"], from_b_two["provenance"]))


def gates_stated_only(work):
    """7b and 13 -- a corpus with nothing to derive, built twice."""
    root = os.path.join(work, "stated")
    write(os.path.join(root, "x.md"), "only [y](y.md)\n")
    write(os.path.join(root, "y.md"), "leaf\n")
    project_id, db = new_project()
    l0_db = l0_path()
    guard0 = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, root, deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    content.build(store, l0, Scope().add(root))
                    first = graph.build(store, scope_root=root)
                    rows_first = store.db.execute(
                        "SELECT src, dst, method FROM edges").fetchall()
                    check("7c a graph of only stated edges derives nothing",
                          first["derived"] == 0 and first["edges"] == 1,
                          "%d edges, %d derived"
                          % (first["edges"], first["derived"]))

                    # The corpus has to *change* between the two builds. An
                    # edge key is `(src, dst, kind)`, so rebuilding an
                    # unchanged corpus writes the same rows either way and a
                    # layer that never clears looks identical to one that
                    # does -- which is how this gate first passed against a
                    # build with no DELETE, 2026-08-04. Removing the link is
                    # the only version that can tell them apart.
                    x_md = os.path.join(root, "x.md")
                    write(x_md, "the link is gone now\n")
                    with Store(l0_db, role=L0) as l0b:
                        scan(l0b, root, deny=())
                        content.build(store, l0b, Scope().add(root))
                    second = graph.build(store, scope_root=root)
                    rows_second = store.db.execute(
                        "SELECT src, dst, method FROM edges").fetchall()
                    check("13 a rebuild replaces the graph, it does not "
                          "accumulate",
                          len(rows_first) == 1 and rows_second == []
                          and second["edges"] == 0,
                          "%d edges before the link was removed, %d after"
                          % (len(rows_first), len(rows_second)))
            finally:
                guard.release()
    finally:
        guard0.release()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp5-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        root = os.path.join(work, "tree")
        os.makedirs(root)
        made = build_tree(root)
        gates_pure()
        gates_built(made, root)
        gates_stated_only(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp5():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
