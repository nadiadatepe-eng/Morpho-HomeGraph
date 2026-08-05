#!/usr/bin/env python3
"""CP-11 -- the view: attribution, buckets, and a page with no markup path.

The answer key is `tests/gold/FASIT-cp11.md`, written before this file and
before the code it grades (`56a8f8c`). Gate numbers below are that document's.

Two unrelated failures live in this checkpoint, and they need different gates.
**Attribution:** a third-party engine copied in with nobody able to say where
it came from or whether it was changed -- gates 1 to 4, and gate 3 is the one
that makes "unchanged" a check rather than a claim. **Safety:** a page that
builds DOM from strings, when a filename is arbitrary bytes off a disk --
gates 14 and 15, and the protection there is the *absence* of a markup path.

The layout gates run the real engine under node. It loads there as well as in
a browser, so "the same corpus draws the same picture twice" is measured
rather than asserted.

Run:
    python3 tests/test_cp11.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import view  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(REPO, "view")
NOTICE = os.path.join(REPO, "NOTICE")
results, check = reporter(60)


def cli(*argv, timeout=300):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        class TimedOut:
            returncode, stdout = 124, ""
            stderr = "timed out: the command never returned"
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def notice_hashes(text=None):
    """`{path: sha256}` for every file the NOTICE calls copied unchanged."""
    text = text if text is not None else open(NOTICE, encoding="utf-8").read()
    copied = text.split("DERIVED")[0]
    return {path: sha for sha, path in
            re.findall(r"([0-9a-f]{64})\s+(\S+)", copied)}


def node_layout(graph, seed, wasm=None, capacity=None):
    """Run the real engine under node and return the positions, or an error.

    The graph goes through a file, not through argv: 200 000 nodes as a command
    line is `Argument list too long`, which is a failure of the harness rather
    than of the thing under test.
    """
    script = """
import { readFileSync } from "node:fs";
import { layout } from "%s/js/layout.js";
const graph = JSON.parse(readFileSync(process.argv[2], "utf8"));
try {
  const options = { seed: Number(process.argv[3]), steps: 40 };
  if (process.argv[4]) options.capacity = JSON.parse(process.argv[4]);
  const out = await layout(graph, "%s", options);
  process.stdout.write(JSON.stringify({x: [...out.x], y: [...out.y]}));
} catch (error) {
  process.stdout.write(JSON.stringify({error: error.message}));
}
""" % (VIEW, wasm or os.path.join(VIEW, "graphs_engine", "main.wasm"))
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     dir=VIEW) as fh:
        fh.write(script)
        name = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(graph, fh)
        payload = fh.name
    try:
        try:
            done = subprocess.run(
                ["node", name, payload, str(seed)]
                + ([json.dumps(capacity)] if capacity else []),
                capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            # Losing the guard does not make the layout wrong, it makes it
            # *hang*: the octree spins over points that were never written.
            # Reported as a result, because a harness that dies here names no
            # gate -- measured 2026-08-05, that mutation came back CRASH-ONLY.
            return {"error": "the layout did not return within 120 s"}
        try:
            return json.loads(done.stdout or '{"error": "no output at all"}')
        except json.JSONDecodeError:
            # A node process that dies mid-write leaves half a line. Reported
            # as an error rather than raised: a harness that dies here names
            # no gate, and "the engine trapped" is a result, not a crash.
            return {"error": "node died: %s"
                    % (done.stderr.strip().splitlines() or ["no stderr"])[-1]}
    finally:
        os.unlink(name)
        os.unlink(payload)


def tree(root):
    """A corpus with one folder over the fanout cap and one under it."""
    for i in range(6):
        write(os.path.join(root, "many", "note%d.md" % i), "note %d\n" % i)
    write(os.path.join(root, "many", "code.py"), "x = 1\n")
    for i in range(3):
        write(os.path.join(root, "few", "file%d.md" % i), "few %d\n" % i)
    # Five files in the root, so the root is bucketed too: without that the
    # collision below cannot happen at all, and the gate for it passes for a
    # reason it does not claim (measured 2026-08-05 -- the mutation survived).
    for i in range(4):
        write(os.path.join(root, "top%d.md" % i), "top %d\n" % i)
    write(os.path.join(root, "top.md"), "top\n")
    # A legal filename that is also live markup. `</script>` cannot be used
    # here -- a slash is the one byte a Linux filename may not contain, and the
    # first version of this fixture silently made two directories out of it.
    # `<img src=x onerror=...>` needs no slash and runs just as well.
    write(os.path.join(root, "<img src=x onerror=alert(1)>.md"), "hostile\n")
    # A filename that collides with a bucket id under any separator a
    # filesystem permits. `\x1f` was the separator first, and this file would
    # have shared an id with the root's `md` bucket -- one node with two roles
    # and no error anywhere. Only NUL, which POSIX forbids in a name, makes the
    # collision impossible rather than unlikely.
    write(os.path.join(root, "\x1fmd"), "a name that looks like a bucket\n")
    return root


# -- 1, 2, 3, 4: the engine's provenance -----------------------------------

def gates_notice():
    text = open(NOTICE, encoding="utf-8").read()
    engine = []
    for folder, _dirs, files in os.walk(os.path.join(VIEW, "graphs_engine")):
        for name in files:
            engine.append(os.path.relpath(os.path.join(folder, name), REPO))
    listed = notice_hashes(text)
    check("1  the NOTICE names the source, the commit and every engine file",
          "github.com/paradigms-of-intelligence/morpho" in text
          and "3ff923f" in text
          and sorted(listed) == sorted(engine),
          "listed %s, on disk %s" % (sorted(listed), sorted(engine)))

    wrong = {path: (sha, digest(os.path.join(REPO, path)))
             for path, sha in listed.items()
             if sha != digest(os.path.join(REPO, path))}
    check("2  every recorded sha256 matches the file on disk",
          not wrong, "%s" % (list(wrong) or "all match"))

    # 3: the control. Without it, gate 2 is satisfied by a NOTICE that records
    # whatever is there -- "unchanged" would be a sentence, not a check.
    with tempfile.TemporaryDirectory(prefix="mhg-cp11-notice-") as work:
        copy = os.path.join(work, "main.wasm")
        shutil.copy2(os.path.join(VIEW, "graphs_engine", "main.wasm"), copy)
        with open(copy, "ab") as fh:
            fh.write(b"\x00")
        changed = digest(copy) != listed.get("view/graphs_engine/main.wasm")
    check("3  a modified engine file would fail that comparison",
          changed, "modified copy hashes differently: %s" % changed)

    derived = text.split("DERIVED", 1)[-1]
    check("4  the derived layout is listed as derived, not as copied",
          "view/js/layout.js" in derived
          and "view/js/layout.js" not in text.split("DERIVED")[0],
          "listed under DERIVED: %s" % ("view/js/layout.js" in derived))


# -- 5, 6, 7, 8, 9, 10, 15: the exported graph ------------------------------

def gates_graph(work):
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
    home = tree(os.path.join(work, "home"))
    cli("scan", work)
    added = cli("add", home)
    project_id = added.stdout.split()[0] if added.stdout.strip() else ""
    built = cli("update", project_id)
    if built.returncode != 0:
        check("0  the project builds before anything is drawn", False,
              "update exited %s: %s" % (built.returncode, built.stderr[:60]))
        return None, None

    out = os.path.join(work, "out")
    written = cli("view", project_id, "--out", out)
    check("17 an ordinary view exits 0", written.returncode == 0,
          "exit %s: %s" % (written.returncode, written.stderr.strip()[:70]))
    if written.returncode != 0:
        return None, None
    with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    nodes = {node["id"]: node for node in data["nodes"]}
    parents = {}
    for edge in data["edges"]:
        parents.setdefault(edge["to"], []).append(edge["from"])

    buckets = [node for node in data["nodes"] if node["kind"] == "bucket"]
    many = {node["name"] for node in buckets if node["id"].startswith("many")}
    check("5  a folder above the fanout cap gets one bucket per file type",
          many == {"md", "py"},
          "buckets under many/: %s" % sorted(many))

    check("6  a folder at or under the cap gets no bucket at all",
          not any(node["id"].startswith("few") for node in buckets)
          and view.K == 4,
          "K=%d, buckets: %s" % (view.K, [b["id"] for b in buckets]))

    bucketed = [node for node in data["nodes"]
                if node["kind"] == "file" and node["id"].startswith("many/")]
    check("7  a bucketed file hangs off the bucket, and off nothing else",
          bool(bucketed)
          and all(len(parents.get(node["id"], [])) == 1
                  and nodes[parents[node["id"]][0]]["kind"] == "bucket"
                  for node in bucketed),
          "%d files, parents %s"
          % (len(bucketed),
             {nodes[parents[n["id"]][0]]["kind"] for n in bucketed}))

    orphans = [node["id"] for node in data["nodes"]
               if node["id"] != "" and len(parents.get(node["id"], [])) != 1]
    check("8  every node but the root has exactly one parent",
          not orphans, "%s" % (orphans[:3] or "none"))

    # Absolute *and* escaping: `../outside.md` carries no home directory and
    # still points out of the project, which is the same leak wearing a
    # different shape.
    escaping = [node["id"] for node in data["nodes"]
                if node["id"].startswith("/") or work in node["id"]
                or ".." in node["id"].split("/")]
    check("9  no absolute or escaping path anywhere in the exported data",
          not escaping, "%s" % (escaping[:2] or "none"))

    # Two runs of one store are byte-identical *anyway*: Python's dicts keep
    # insertion order, so an unsorted export repeats itself. The property that
    # actually holds the file still is the sort -- it is what makes two stores
    # built from the same tree agree, and what stops one added file from
    # shifting every line. So the gate asserts the order, not only the repeat.
    first = open(os.path.join(out, "data.json"), "rb").read()
    cli("view", project_id, "--out", out)
    ids = [node["id"] for node in data["nodes"]]
    pairs = [(edge["from"], edge["to"]) for edge in data["edges"]]
    check("10 two exports agree, and the file is written in sorted order",
          first == open(os.path.join(out, "data.json"), "rb").read()
          and ids == sorted(ids) and pairs == sorted(pairs),
          "%d bytes, sorted: %s/%s"
          % (len(first), ids == sorted(ids), pairs == sorted(pairs)))

    hostile = [node for node in data["nodes"] if "onerror" in node["name"]]
    check("15 a filename that is live markup survives the export untouched",
          len(hostile) == 1
          and hostile[0]["name"] == "<img src=x onerror=alert(1)>.md",
          "%s" % [node["name"] for node in hostile])

    # 7b: a filename shaped like a bucket id must collide with nothing. The
    # collision does not show up as a duplicate id -- the bucket simply is not
    # created, and the files hang off the *file* whose name it clashed with.
    # So the invariant to check is the one that breaks: only a folder or a
    # bucket ever has children (measured 2026-08-05, the first version of this
    # gate looked at ids and stayed green through it).
    ids = [node["id"] for node in data["nodes"]]
    lookalike = [node for node in data["nodes"] if node["name"] == "\x1fmd"]
    childless = {edge["from"] for edge in data["edges"]}
    files_with_children = [node["id"] for node in data["nodes"]
                           if node["kind"] == "file" and node["id"] in childless]
    check("7b a filename shaped like a bucket id collides with nothing",
          len(ids) == len(set(ids)) and len(lookalike) == 1
          and not files_with_children,
          "%d ids, %d unique, files with children: %s"
          % (len(ids), len(set(ids)), files_with_children[:2] or "none"))

    inside = cli("view", project_id, "--out", os.path.join(VIEW, "out"))
    check("16b writing the view into its own source is refused",
          inside.returncode == 1
          and os.path.isdir(os.path.join(VIEW, "js")),
          "exit %s, js/ still there: %s"
          % (inside.returncode, os.path.isdir(os.path.join(VIEW, "js"))))

    check("16 the view folder holds the page, the engine and the data",
          all(os.path.exists(os.path.join(out, name)) for name in
              ("index.html", "js/draw.js", "js/layout.js",
               "graphs_engine/main.wasm", "data.json"))
          and "http.server" in written.stdout,
          "%s" % sorted(os.listdir(out)))
    return data, project_id


# -- 11, 12, 13: the layout, on the real engine ----------------------------

def gates_layout(data):
    small = {"nodes": data["nodes"][:12],
             "edges": [e for e in data["edges"]
                       if any(n["id"] == e["from"] for n in data["nodes"][:12])
                       and any(n["id"] == e["to"] for n in data["nodes"][:12])]}
    once = node_layout(small, 1337)
    twice = node_layout(small, 1337)
    other = node_layout(small, 4242)
    check("11 the same graph and the same seed give identical positions",
          "error" not in once and once == twice,
          "%s" % (once.get("error") or "%d positions, identical: %s"
                  % (len(once.get("x", [])), once == twice)))

    # 12: the control. Without it, gate 11 passes for a layout that returns
    # zeros -- which is exactly what an engine too small to hold the graph
    # would produce, silently.
    check("12 a different seed gives a different picture",
          "error" not in other and other != once,
          "%s" % (other.get("error") or "different: %s" % (other != once)))

    # 13: writing past a typed view throws nothing in JavaScript -- the value
    # is dropped -- so a graph that does not fit the buffers has to be refused
    # rather than half drawn.
    #
    # The capacity is pinned here, because on this engine the default cannot
    # come up short: `alloc` grows the WebAssembly memory, so asking for more
    # gets more (measured 2026-08-05 -- 200 000 nodes were laid out, not
    # refused). The guard is for a caller who pins one, and this is that
    # caller. Testing it through the default would have been testing that the
    # engine grows.
    big = {"nodes": [{"id": "n%d" % i, "kind": "file", "name": "n",
                      "path": "n%d" % i, "depth": 1} for i in range(4000)],
           "edges": []}
    refused = node_layout(big, 1337, capacity={"maxPoints": 64, "maxLinks": 64})
    check("13 a graph larger than the engine's buffers is refused",
          "error" in refused and "nodes" in refused["error"],
          "%s" % (refused.get("error", "no refusal")[:70]))


# -- 14: no markup path in the page ----------------------------------------

def gates_no_markup():
    forbidden = ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "eval(", "new Function(")
    hits = []
    for folder, _dirs, files in os.walk(VIEW):
        for name in sorted(files):
            if not name.endswith((".js", ".html", ".css")):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            # The comments explaining *why* there is no markup path name these
            # words; the code must not. Prose is not a defect, and a gate that
            # cannot tell them apart makes the explanation unwritable.
            code = "\n".join(line for line in source.splitlines()
                             if not line.lstrip().startswith(("*", "//", "<!--", "/*")))
            hits += ["%s: %s" % (name, word) for word in forbidden
                     if word in code]
    with open(os.path.join(VIEW, "js", "draw.js"), encoding="utf-8") as fh:
        draw = fh.read()
    check("14 the page has no markup path, and sets text instead",
          not hits and "textContent" in draw,
          "%s" % (hits or "none, textContent present"))


def gates_refusal(work):
    """18 -- a project with no content refuses instead of drawing nothing."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "bare", "store")
    empty = os.path.join(work, "bare", "home")
    os.makedirs(empty, exist_ok=True)
    cli("scan", empty)
    added = cli("add", empty)
    project_id = added.stdout.split()[0] if added.stdout.strip() else ""
    cli("update", project_id)
    drawn = cli("view", project_id, "--out", os.path.join(work, "bare", "out"))
    check("18 a project with no content refuses and names update",
          drawn.returncode == 1
          and "update" in (drawn.stdout + drawn.stderr).lower(),
          "exit %s: %r" % (drawn.returncode,
                           (drawn.stdout + drawn.stderr).strip()[:60]))


def main() -> int:
    gates_notice()
    gates_no_markup()
    with tempfile.TemporaryDirectory(prefix="mhg-cp11-") as work:
        data, _project = gates_graph(work)
        if data:
            gates_layout(data)
        gates_refusal(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp11():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
