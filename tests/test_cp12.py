#!/usr/bin/env python3
"""CP-12 -- how old is each layer, and how stale is each file.

The answer key is `tests/gold/FASIT-cp12.md`, written before this file and
before the code it grades (`99004ac`). Gate numbers below are that document's.

The failure this checkpoint closes was measured, not imagined: on 2026-08-04 a
search for `fasit-cp8` found the predecessor's `FASIT-CP8.md` and **not ours**,
because the catalogue had last been walked before ours was written -- and the
answer said nothing. Gate 1 is that failure, and gate 5 is the control that
stops "no age shown" from becoming a signal the reader has to interpret.

Run:
    python3 tests/test_cp12.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import freshness  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(REPO, "view")
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


def corpus(root):
    write(os.path.join(root, "barrier.md"),
          "The write barrier refuses a second writer, and the lock is held "
          "for the whole session.\n")
    write(os.path.join(root, "notes.md"), "A short note about very little.\n")
    write(os.path.join(root, "handlers.py"), "def getUserById(conn):\n    return 1\n")
    # Empty, and it stays empty: no chunk can ever come out of it, so calling
    # it `unembedded` would be an instruction the reader cannot carry out.
    write(os.path.join(root, "empty.log"), "")
    # Binary: this one can never be `fresh`, because it was never read.
    with open(os.path.join(root, "picture.bin"), "wb") as fh:
        fh.write(b"\x89PNG\x00\x00binary\x00bytes")
    return root


# -- 14, 16: the words the ages are said in --------------------------------

def gates_wording():
    said = [freshness.human(v) for v in (0, 45, 200, 7200, 3 * 86400, None, -5)]
    check("14 an age reads as a human says it, and zero is not empty",
          said[:5] == ["0 s", "45 s", "3 min", "2 h", "3 d"]
          and said[5] == "never",
          "%s" % said[:6])

    # 16: only reachable when the clocks disagree -- the catalogue stamped
    # later than the content built from it. Said in words rather than printed
    # as a negative number nobody can act on. This machine's clock was 12 days
    # out on 2026-08-04, so it is not a theoretical case.
    check("16 an age from the future is said in words, not as a negative",
          "future" in said[6] and "clocks" in said[6], "%r" % said[6])


# -- 1 to 5, 15: the age is in every answer --------------------------------

def gates_answers(work):
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
    home = corpus(os.path.join(work, "home"))
    cli("scan", work)
    added = cli("add", home)
    project_id = added.stdout.split()[0] if added.stdout.strip() else ""
    built = cli("update", project_id)
    if built.returncode != 0:
        check("0  the project builds before anything is asked", False,
              "update exited %s: %s" % (built.returncode, built.stderr[:60]))
        return None
    embedded = cli("embed", project_id)

    names = cli("search", "--names", "barrier")
    check("1  a name search says how old the catalogue is",
          names.returncode == 0 and "catalogue" in names.stdout
          and re.search(r"catalogue \d+ (s|min|h|d)", names.stdout) is not None,
          "%r" % names.stdout.strip().splitlines()[-1:])

    lexical = cli("search", "--project", project_id, "write barrier")
    check("2  a lexical search says the age of the content and the catalogue",
          lexical.returncode == 0
          and re.search(r"catalogue \d+ \w+\s+content \d+ \w+", lexical.stdout)
          is not None,
          "%r" % lexical.stdout.strip().splitlines()[-1:])

    semantic = cli("search", "--project", project_id, "--semantic", "the lock")
    check("3  a semantic search says the vectors' age as well",
          semantic.returncode == 0
          and re.search(r"vectors \d+ \w+", semantic.stdout) is not None,
          "%r" % semantic.stdout.strip().splitlines()[-1:])

    fused = cli("search", "--project", project_id, "--fused", "the lock")
    line = fused.stdout.strip().splitlines()[-1] if fused.stdout.strip() else ""
    check("4  a fused search says all three",
          fused.returncode == 0
          and all(word in line for word in ("catalogue", "content", "vectors")),
          "%r" % line)

    # 5: the control. If the age only appeared when a layer were stale,
    # "no age" would be a signal the reader has to learn -- and an answer that
    # forgot to print it would read as "everything is fine".
    check("5  the age is printed even when every layer is fresh",
          re.search(r"catalogue \d+ s", lexical.stdout) is not None,
          "%r" % lexical.stdout.strip().splitlines()[-1:])

    check("15 embed, search and view all still exit 0",
          embedded.returncode == 0 and lexical.returncode == 0
          and fused.returncode == 0,
          "embed %s, search %s, fused %s"
          % (embedded.returncode, lexical.returncode, fused.returncode))
    return project_id


# -- 6 to 10, 13: the four states ------------------------------------------

def gates_states(work, project_id):
    home = os.path.join(work, "home")
    out = os.path.join(work, "out")

    drawn = cli("view", project_id, "--out", out)
    with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    states = {node["id"]: node.get("state") for node in data["nodes"]
              if node["kind"] == "file"}

    # 9: the control first. Everything read, current and embedded is `fresh`,
    # and without this gates 6 to 8 pass for a counter that always says stale.
    readable = {path: state for path, state in states.items()
                if not path.endswith(".bin")}
    check("9  a project that is read, current and embedded is all fresh",
          set(readable.values()) == {"fresh"},
          "%s" % sorted(set(readable.values())))

    check("7  a file that could not be read is unread, never fresh",
          states.get("picture.bin") == "unread",
          "picture.bin is %s" % states.get("picture.bin"))

    # 6: change a file *after* the update. The catalogue sees the new mtime,
    # L2 still holds what it read -- which is exactly "our copy is behind".
    time.sleep(1.1)
    write(os.path.join(home, "notes.md"), "The note is different now.\n")
    cli("scan", work)
    after = cli("view", project_id, "--out", out)
    with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
        changed = json.load(fh)
    states = {node["id"]: node.get("state") for node in changed["nodes"]
              if node["kind"] == "file"}
    check("6  a file changed after the last update is stale",
          states.get("notes.md") == "stale"
          and states.get("barrier.md") == "fresh",
          "notes %s, barrier %s"
          % (states.get("notes.md"), states.get("barrier.md")))

    # 8: rebuild the content so nothing is stale, and now the file whose text
    # is new has no vector for its hash.
    cli("update", project_id)
    third = cli("view", project_id, "--out", out)
    with open(os.path.join(out, "data.json"), encoding="utf-8") as fh:
        rebuilt = json.load(fh)
    states = {node["id"]: node.get("state") for node in rebuilt["nodes"]
              if node["kind"] == "file"}
    check("8  a file with no vector for its hash is unembedded",
          states.get("notes.md") == "unembedded",
          "notes.md is %s" % states.get("notes.md"))

    # 8b: measured on this repository the first time the picture was drawn --
    # 14 files came back `unembedded` and every one held zero characters. The
    # label was true and unusable: it asked for a command that changes nothing.
    check("8b an empty file is fresh, because there is nothing to embed",
          states.get("empty.log") == "fresh",
          "empty.log is %s" % states.get("empty.log"))

    check("10 the export carries the four states, named as the text names them",
          set(states.values()) <= {"fresh", "stale", "unread", "unembedded"}
          and "unembedded" in states.values(),
          "%s" % sorted(set(states.values())))

    check("11 the export dates itself and every layer",
          isinstance(rebuilt.get("exported_at"), (int, float))
          and set(rebuilt.get("ages", {})) == {"catalogue", "content", "vectors"},
          "exported_at %s, ages %s"
          % (type(rebuilt.get("exported_at")).__name__,
             sorted(rebuilt.get("ages", {}))))

    # 13: the counts the command prints are the states in the file. Two
    # readings of one fact must agree, or one of them is decoration.
    printed = dict(re.findall(r"(fresh|stale|unread|unembedded) (\d+)",
                              third.stdout))
    counted = {}
    for state in states.values():
        counted[state] = counted.get(state, 0) + 1
    check("13 the counts in the answer match the states in the export",
          {k: int(v) for k, v in printed.items()} == counted,
          "printed %s, exported %s" % (printed, counted))

    check("15b every view in this gate exited 0",
          drawn.returncode == 0 and after.returncode == 0
          and third.returncode == 0,
          "%s %s %s" % (drawn.returncode, after.returncode, third.returncode))


# -- 12: the legend, in the page -------------------------------------------

def gates_legend():
    with open(os.path.join(VIEW, "js", "draw.js"), encoding="utf-8") as fh:
        source = fh.read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith(("*", "//", "/*")))
    states = ("fresh", "stale", "unread", "unembedded")
    # Naming the states is not enough: the words survive in the legend even
    # when the *drawing* has stopped using them, and then the picture is
    # coloured by kind again while the key still promises freshness (measured
    # 2026-08-05 -- that mutation survived the first version of this gate).
    # So the check is that a colour exists for each state, and that the fill
    # reads the node's state.
    palette = re.search(r"STATE_COLOURS = \{(.*?)\}", code, re.S)
    defined = re.findall(r"(\w+):", palette.group(1)) if palette else []
    check("12 the page draws by state, explains each one, and uses no markup",
          sorted(defined) == sorted(states)
          and "STATE_COLOURS[node.state]" in code
          and all(state in code for state in states)
          and "createElement" in code and "textContent" in code
          and "innerHTML" not in code,
          "coloured: %s, fill reads state: %s"
          % (sorted(defined), "STATE_COLOURS[node.state]" in code))


def main() -> int:
    gates_wording()
    gates_legend()
    with tempfile.TemporaryDirectory(prefix="mhg-cp12-") as work:
        project_id = gates_answers(work)
        if project_id:
            gates_states(work, project_id)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp12():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
