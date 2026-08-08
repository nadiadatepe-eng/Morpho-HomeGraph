#!/usr/bin/env python3
"""CP-10 -- fusing the lexical and the semantic list.

The answer key is `tests/gold/FASIT-cp10.md`, written before this file and
before the code it grades (`4a000a5`). Gate numbers below are that document's.

The failure this checkpoint prevents is a merge that reads *scores*. `bm25()`
is index-relative and negative, cosine is 0 to 1 and positive, and comparing
them produces an order that looks sorted and means nothing. The predecessor's
trap is written into `TODO.md`: a raw score of -999 put the same node first
whatever happened, so a fusion that compared scores passed its gate. Gate 2 is
the case where the two schemes disagree, and it is the load-bearing one.

Gates 8 and 9 are the other half, and they come from CP-9E: the fusion that
was *measured* lived in the measuring tool. If it stays there, the tool grades
itself and the product's merge is unmeasured.

Run:
    python3 tests/test_cp10.py
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import fusion, search  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.store import Store, db_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    """Twelve files, and the number matters.

    The CP-9 corpus had four, and with a cut of ten every file was in every
    answer -- so "the target is in the top ten" was true of any order at all.
    Measured 2026-08-05: a mutation that dropped the lexical list entirely
    left gate 7 green, because the vector list returned the whole corpus.
    Twelve files make the cut real, and the gates below ask for rank one
    rather than membership.
    """
    for name, body in (
            ("shipping.md", "Parcels leave the warehouse on Tuesday and the "
                            "courier signs for each pallet.\n"),
            ("recipes.md", "Sourdough needs a starter, patience and an oven "
                           "that holds its heat.\n"),
            ("weather.md", "The forecast promises rain until Thursday, then "
                           "a cold and clear weekend.\n"),
            ("music.md", "The quartet rehearsed the slow movement twice "
                         "before anyone was satisfied.\n"),
            ("garden.md", "Tomatoes want sun, tying up and rather more water "
                          "than the label claims.\n"),
            ("bicycle.md", "A worn chain wears the sprockets, so replace it "
                           "before it stretches too far.\n"),
            ("ferry.md", "The crossing takes forty minutes and the cafeteria "
                         "closes ten minutes early.\n"),
            ("lamps.md", "Warm bulbs suit a reading corner; the ceiling "
                         "fitting takes a cooler one.\n")):
        write(os.path.join(root, name), body)
    write(os.path.join(root, "barrier.md"),
          "The write barrier refuses a second writer. One session holds the "
          "lock for its whole lifetime, and a second process is turned away "
          "rather than queued.\n")
    write(os.path.join(root, "kake.md"),
          "Gulrotkake med kremost og valnøtter. Riv gulrøttene grovt, bland "
          "inn sukker og egg, og stek formen i tretti minutter.\n")
    write(os.path.join(root, "notes.md"),
          "A short note about nothing much at all, kept here so the corpus "
          "has more than two rows in it.\n")
    write(os.path.join(root, "handlers.py"),
          "def getUserById(conn):\n    return conn.fetch()\n")
    return root


def routes_of(out):
    """`{path: route}` from the fused output, parsed as two columns.

    Split on the *first* gap rather than on whitespace generally: a path may
    contain spaces, and `line.split()` would quietly skip those lines -- a
    gate that cannot read a line does not check it.
    """
    routed = {}
    for line in out.splitlines():
        route, _gap, path = line.partition(" ")
        path = path.strip()
        if path and os.sep in path:
            routed[path] = route
    return routed


def found(out):
    paths = []
    for line in out.splitlines():
        for word in line.split():
            if os.sep in word and not word.endswith(":"):
                paths.append(word)
                break
    return paths


# -- 1, 2, 3, 4, 5, 15a: the merge itself ----------------------------------

def gates_merge():
    # The case the answer key demands, and the whole reason it is written
    # down: `b.py` is *second* in both lists, `a.py` and `c.py` are first in
    # one each. Rank fusion puts b.py on top; comparing the raw numbers --
    # bm25 is negative, cosine is 0 to 1 -- cannot produce that order at all.
    lexical = ["a.py", "b.py"]          # bm25: -9.9, -1.2
    vector = ["c.py", "b.py"]           # cosine: 0.81, 0.79
    merged = fusion.fuse({"lexical": lexical, "vector": vector})
    order = [hit["path"] for hit in merged]

    scores = fusion.rrf_scores([lexical, vector])
    check("1  RRF is 1/(k + rank), k = 60, rank from one, on known values",
          fusion.RRF_K == 60
          and abs(scores["b.py"] - 2.0 / 62) < 1e-12
          and abs(scores["a.py"] - 1.0 / 61) < 1e-12,
          "b=%.6f (want %.6f), a=%.6f (want %.6f)"
          % (scores["b.py"], 2.0 / 62, scores["a.py"], 1.0 / 61))

    # 2: by raw score, `c.py` (0.81) would lead and `a.py` (-9.9) would be
    # last -- a different order from the one ranks give. Without a case where
    # they disagree, a fusion that compares scores passes every gate.
    by_score = ["c.py", "b.py", "a.py"]
    check("2  ranks decide, and the score order would have been different",
          order == ["b.py", "a.py", "c.py"] and order != by_score,
          "ranks %s, scores would give %s" % (order, by_score))

    check("3  a file ranked in both lists beats one ranked first in only one",
          order[0] == "b.py", "%s" % order[:2])

    # 4: the tie-break, with the counterexample that proves it is reachable.
    # Ranks 3 and 45 give 1/63 + 1/105; ranks 10 and 30 give 1/70 + 1/90; both
    # are exactly 8/315. So an equal sum does *not* mean an equal multiset of
    # ranks, and "best rank first" decides a case the score cannot. The rule
    # was removed here once on the opposite argument, and this case is why it
    # came back -- a claim about arithmetic that was never computed.
    def placed(prefix: str, where: dict) -> list:
        """A ranked list with the named files at the given 1-based ranks.

        The padding is named per list. Shared padding would appear in both
        and outscore the two files the case is about -- the first version of
        this gate did exactly that and failed for that reason, not for the
        one it was written for.
        """
        depth = max(where)
        return [where.get(rank, "%s%02d" % (prefix, rank))
                for rank in range(1, depth + 1)]

    # "aaa" sorts first and is *later* in both lists; "zzz" sorts last and is
    # earlier. Equal scores, so only the best rank can decide -- and if it
    # does not, the alphabet does, which is the defect.
    first = placed("left", {3: "zzz.py", 10: "aaa.py"})
    second = placed("right", {30: "aaa.py", 45: "zzz.py"})
    tie = fusion.fuse({"lexical": first, "vector": second})
    scores = fusion.rrf_scores([first, second])
    again = [hit["path"] for hit in
             fusion.fuse({"lexical": lexical, "vector": vector})]
    check("4  an exact tie breaks by best rank, then by path, and holds still",
          abs(scores["zzz.py"] - scores["aaa.py"]) < 1e-15
          and [hit["path"] for hit in tie][:2] == ["zzz.py", "aaa.py"]
          and order == again,
          "zzz %.17f, aaa %.17f, order %s"
          % (scores["zzz.py"], scores["aaa.py"],
             [hit["path"] for hit in tie][:2]))

    # A path repeated inside one list is one hit: otherwise its score carries
    # two contributions while `found_by` remembers one rank, and the number
    # and the provenance disagree about the same file.
    twice = fusion.fuse({"lexical": ["a.py", "a.py", "b.py"], "vector": []})
    check("4b a path repeated inside one list is counted once",
          len(twice) == 2 and abs(twice[0]["score"] - 1.0 / 61) < 1e-12
          and twice[0]["found_by"] == {"lexical": 1},
          "%s" % [(h["path"], round(h["score"], 6), h["found_by"])
                  for h in twice])

    # 5: without this, a fusion that quietly dropped one list would produce
    # the same output as one that did not.
    b_hit = [hit for hit in merged if hit["path"] == "b.py"][0]
    a_hit = [hit for hit in merged if hit["path"] == "a.py"][0]
    check("5  every hit says which lists found it, and at which rank",
          b_hit["found_by"] == {"lexical": 2, "vector": 2}
          and a_hit["found_by"] == {"lexical": 1},
          "b %s, a %s" % (b_hit["found_by"], a_hit["found_by"]))

    check("15a each list is fetched deeper than the ten that are shown",
          fusion.DEPTH > fusion.CUT and fusion.CUT == 10,
          "depth %d, cut %d" % (fusion.DEPTH, fusion.CUT))


# -- 8, 9, 15b: one implementation, and it is the one that ships ------------

def gates_one_implementation():
    """CP-9E measured a fusion that lived in the measuring tool. R3."""
    defined = []
    for folder in ("morpho_homegraph", "tools", "tests"):
        for name in sorted(os.listdir(os.path.join(REPO, folder))):
            if not name.endswith(".py"):
                continue
            path = os.path.join(REPO, folder, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in (
                        "rrf_scores", "fuse"):
                    defined.append("%s/%s:%s" % (folder, name, node.name))
    check("8  there is exactly one RRF in the repository, read as code",
          sorted(defined) == ["morpho_homegraph/fusion.py:fuse",
                              "morpho_homegraph/fusion.py:rrf_scores"],
          "defined in %s" % (defined or "nowhere"))

    with open(os.path.join(REPO, "tools", "cp9e_eval.py"), encoding="utf-8") as fh:
        tool = ast.parse(fh.read())
    imports = [node for node in ast.walk(tool)
               if isinstance(node, ast.ImportFrom)
               and node.module and "fusion" in node.module]
    check("9  the measuring tool imports the fusion instead of owning one",
          bool(imports),
          "imports %s" % ([i.module for i in imports] or "nothing"))

    # 15b: the depth is in the calls, not only in the constant. A fusion over
    # two lists already cut to ten throws away the part it exists for.
    with open(os.path.join(REPO, "morpho_homegraph", "cli.py"),
              encoding="utf-8") as fh:
        source = ast.parse(fh.read())
    deep = []
    for node in ast.walk(source):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "limit" and isinstance(keyword.value, ast.Attribute) \
                    and keyword.value.attr == "DEPTH":
                deep.append(getattr(node.func, "attr", "?"))
    check("15b both retrieval calls in the command ask for that depth",
          sorted(deep) == ["content", "search"],
          "limit=fusion.DEPTH passed to %s" % (sorted(deep) or "nothing"))


# -- 6, 7, 10, 11, 12, 13, 14, 16: the command -----------------------------

def gates_command(work):
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
    home = corpus(os.path.join(work, "home"))
    cli("scan", work)
    added = cli("add", home)
    project_id = added.stdout.split()[0] if added.stdout.strip() else ""
    built = cli("update", project_id)
    if built.returncode != 0:
        check("0  the project builds before anything is fused", False,
              "update exited %s: %s" % (built.returncode, built.stderr[:60]))
        return

    # 13: the default is untouched, and it needs no vectors. CP-9E decided
    # this with numbers: class C landed in the band, so nothing is switched on.
    plain = cli("search", "--project", project_id, "quick")
    lexical_only = cli("search", "--project", project_id, "write barrier")
    check("13 the plain search is unchanged and needs no vectors at all",
          plain.returncode == 0 and lexical_only.returncode == 0
          and any(p.endswith("barrier.md") for p in found(lexical_only.stdout)),
          "exit %s / %s" % (plain.returncode, lexical_only.returncode))

    # 13b: two answer modes at once. Whichever branch ran first would answer
    # a question the user did not ask, and look right doing it.
    both_modes = cli("search", "--project", project_id, "--semantic",
                     "--fused", "barrier")
    check("13b asking for two answer modes at once is refused, not resolved",
          both_modes.returncode == 2
          and "pick one" in (both_modes.stdout + both_modes.stderr),
          "exit %s: %r" % (both_modes.returncode,
                           (both_modes.stderr or both_modes.stdout).strip()[:60]))

    # 10: a fusion with one list is not a fusion (R6). It refuses and names
    # the command that fixes it, rather than quietly answering lexically.
    no_vectors = cli("search", "--project", project_id, "--fused", "barrier")
    check("10 without vectors the fused search refuses and names embed",
          no_vectors.returncode == 1
          and "embed" in (no_vectors.stdout + no_vectors.stderr).lower(),
          "exit %s: %r" % (no_vectors.returncode,
                           (no_vectors.stdout + no_vectors.stderr).strip()[:60]))

    embedded = cli("embed", project_id)
    if embedded.returncode != 0:
        check("0b the corpus embeds before the fused search is asked", False,
              "embed exited %s: %s" % (embedded.returncode,
                                       embedded.stderr.strip()[:60]))
        return

    # 12: the control. Without it, gates 10 and 11 are satisfied by a command
    # that refuses whatever it is given.
    fused = cli("search", "--project", project_id, "--fused", "write barrier")
    check("12 an ordinary fused search exits 0 and returns hits",
          fused.returncode == 0 and found(fused.stdout),
          "exit %s: %s" % (fused.returncode, found(fused.stdout)))

    # 7: CP-9E's class B requirement, now in the product rather than in a
    # measurement: what the lexical layer finds must survive the merge.
    #
    # The claim is checked through the route column rather than through
    # membership: the vector layer answers this question too, so "handlers.py
    # is in the answer" stayed true with the lexical list deleted (measured
    # 2026-08-05 -- the mutation survived that form of the gate). What cannot
    # survive it is the *lexical* route being named for the hit.
    lexical_hit = cli("search", "--project", project_id, "--fused", "user by id")
    order = found(lexical_hit.stdout)
    routes = routes_of(lexical_hit.stdout)
    top = order[0] if order else ""
    check("7  a file only the lexical list found is not lost in the merge",
          lexical_hit.returncode == 0 and top.endswith("handlers.py")
          and routes.get(top) in ("lexical", "both"),
          "exit %s: %s via %s" % (lexical_hit.returncode,
                                  os.path.basename(top), routes.get(top)))

    # 14 and 6: the other direction, and the reason the merge exists. The
    # Norwegian question shares no term with the English file, so FTS is zero
    # by construction -- every hit here came from the vector list.
    question = "skrivebarrieren nekter en andre skriver"
    plain_paraphrase = cli("search", "--project", project_id, question)
    fused_paraphrase = cli("search", "--project", project_id, "--fused", question)
    crossed = found(fused_paraphrase.stdout)
    check("14 a paraphrase the lexical layer cannot answer is answered fused",
          not found(plain_paraphrase.stdout)
          and fused_paraphrase.returncode == 0
          and bool(crossed) and crossed[0].endswith("barrier.md"),
          "plain %s, fused %s" % (found(plain_paraphrase.stdout),
                                  [os.path.basename(p) for p in crossed[:3]]))
    # Read as a route *column*, never as a substring of the output. It was a
    # substring until 2026-08-08, and CP-12's age line ends every answer with
    # `vectors <age>` -- so this gate has been unable to go red since CP-12
    # landed, and said so to nobody. The mutation that proved it ("every hit
    # claims both routes found it") survived a sweep that had recorded 0
    # survivors two checkpoints earlier. Same trap gate 5b was already written
    # to avoid, three lines below.
    paraphrase_routes = list(routes_of(fused_paraphrase.stdout).values())
    check("6  a file only the vector list found reaches the answer, and says so",
          "vector" in paraphrase_routes,
          "routes=%s" % paraphrase_routes[:5])

    # 5 again, end to end: the route is in the output a person reads. Read as
    # the first *column* of each hit line, not as a substring of the output --
    # a path containing the word "vector" would satisfy the substring form
    # without a route ever being printed.
    columns = list(routes_of(fused.stdout).values())
    check("5b every printed hit carries a route in its first column",
          len(columns) == len(found(fused.stdout)) and bool(columns)
          and all(word in ("lexical", "vector", "both") for word in columns),
          "%s" % columns[:4])

    # 16: CP-9's R9 does not stop applying because the answer is fused.
    check("16 the fused answer states the vector layer's coverage",
          "chunks embedded" in fused.stdout,
          "%r" % fused.stdout.strip()[-40:])

    # 11: the other half of R6.
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute("DROP TABLE IF EXISTS %s" % search.TABLE)
                db.commit()
    finally:
        guard.release()
    no_index = cli("search", "--project", project_id, "--fused", "barrier")
    check("11 without a built L4 the fused search refuses and names update",
          no_index.returncode == 1
          and "update" in (no_index.stdout + no_index.stderr).lower(),
          "exit %s: %r" % (no_index.returncode,
                           (no_index.stdout + no_index.stderr).strip()[:60]))


def main() -> int:
    gates_merge()
    gates_one_implementation()
    with tempfile.TemporaryDirectory(prefix="mhg-cp10-") as work:
        gates_command(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp10():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
