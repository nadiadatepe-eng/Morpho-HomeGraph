#!/usr/bin/env python3
"""CP-22 -- the answers survive a rebuild, not just the rows.

The answer key is `tests/gold/FASIT-cp22.md`, written before this file.

**CP-14 already compares an updated store against one built from nothing.**
It grades *state*: rows and keys across nine axes. CP-22 grades *answers*,
and between a row and an answer sits the ranking. Two stores can hold
exactly the same rows and still answer differently, because `search.py`
orders by score and `fusion.py` merges two lists on rank.

The hypothesis, and it is the only real one here:

    the ranking out of L4 is a function of the content, not of the path
    the store took to get there.

**Gates 1-4 were predicted green before this file existed, and the answer
key says why:** `search.build` does `DELETE FROM l4` and reinserts, so a
store that went A->B *is* built on B. The equivalence holds by
construction, which is trap 2 on this project's own list -- "a property
that begins to hold by construction ... looks stronger in the diff and
proves less". Four free gates sold as four proofs is exactly what the
prediction is there to prevent.

**So the value is in gates 5-8**, which are negative controls: they show
that gates 1-4 *could* have gone red. A gate that cannot fail is not a
gate, and here that is not a slogan -- gates 1-4 pass for a reason that
has nothing to do with the property being protected.

Run:
    python3 tests/test_cp22.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report import reporter  # noqa: E402


class TimedOut:
    """A run that never finished. Not a zero exit wearing a disguise."""
    returncode = -1
    stdout = ""
    stderr = "timed out"


def cli(home, *argv, timeout=600):
    """One command against the store rooted at `home`."""
    env = dict(os.environ, MORPHO_HOMEGRAPH_HOME=home)
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO, env=env,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    return path


def project_db(home):
    """The one project index under `home`; `l0` is not a project id."""
    for name in sorted(os.listdir(home)):
        if name != "l0" and os.path.isfile(os.path.join(home, name,
                                                        "index.db")):
            return os.path.join(home, name, "index.db")
    return ""


def project_id(home):
    return os.path.basename(os.path.dirname(project_db(home)))


def build(home, corpus):
    """Register, catalogue, fill the layers. Returns the exit codes.

    **`add` before `scan`, and that order is CP-14's finding, not a
    preference:** `scan` hashes what the *registered* projects would index,
    so a scan run before the project exists hashes nothing and the
    comparison is unfair in a way that looks like a real divergence.

    **`embed` is called, and the first version of this file did not call
    it.** That version passed 9/9 -- because `--fused` REFUSED with exit 1
    on an unembedded project, `answers()` returned `None`, and gate 2
    compared `None` to `None`. A gate that cannot go red, in the file whose
    whole subject is gates that cannot go red. The refusal was correct
    behaviour by the CLI ("Half a fusion is not one"); the fault was
    entirely in the test. Measured cost of embedding this corpus: 1.3 s.
    """
    codes = [cli(home, "add", corpus).returncode,
             cli(home, "scan", corpus).returncode]
    codes.append(cli(home, "update", project_id(home)).returncode)
    codes.append(cli(home, "embed", project_id(home)).returncode)
    return codes


# -- the corpus -----------------------------------------------------------
#
# Written so several files answer the same query, because a query with one
# hit cannot show a reordering. Gate 3 counts the hits rather than trusting
# this comment: a corpus that stopped producing multi-hit answers would make
# gates 1, 2 and 4 pass over empty lists, which is `all()` over nothing.

DOCS = {
    "alpha.md": "The heron stands in shallow water.\n"
                "A heron waits, and the water is still.\n",
    "beta.md": "Shallow water over stone, and a heron upstream.\n",
    "gamma.md": "Stone, water, stone. No bird here at all.\n",
    "delta.md": "The heron is patient; patience is the whole method.\n",
    "epsilon.md": "Notes on water quality in shallow streams.\n",
}

# Each query is chosen to hit more than one file, so a swap is visible.
QUERIES = ("heron", "water", "shallow", "stone")


def corpus(root):
    for name, body in DOCS.items():
        write(os.path.join(root, name), body)
    return root


def answers(home, query, fused=False):
    """The answer to one query as an ordered list of paths.

    **Order is preserved on purpose.** Comparing sets would make gate 4
    unable to see a swap, which is the failure this checkpoint exists to
    catch -- and it would still look like a passing gate.
    """
    argv = ["search", "--project", project_id(home), query]
    if fused:
        argv.insert(3, "--fused")
    run = cli(home, *argv)
    if run.returncode != 0:
        return None
    out = []
    for line in run.stdout.splitlines():
        parts = line.split(None, 1)
        # Result lines are "<where> <path>"; the age line and the
        # "no matches" line are neither, and must not be read as hits.
        if len(parts) == 2 and parts[1].startswith(os.sep):
            out.append(parts[1])
    return out


def _drop_one_l4_row(db_path):
    """Remove a single L4 row. The negative control for gate 1."""
    db = sqlite3.connect(db_path)
    try:
        row = db.execute("SELECT path FROM l4 ORDER BY path LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("DELETE FROM l4 WHERE path = ?", (row[0],))
        db.commit()
        return row[0]
    finally:
        db.close()


def gates(work):
    """Returns (name, ok, detail) for each gate."""
    out = []

    inc_home = os.path.join(work, "inc-home")
    fresh_home = os.path.join(work, "fresh-home")
    body = corpus(os.path.join(work, "corpus"))

    # A -> B: build, then change the corpus, then update again.
    os.makedirs(inc_home, exist_ok=True)
    codes = build(inc_home, body)
    write(os.path.join(body, "zeta.md"),
          "A second heron, downstream, in deeper water.\n")
    codes.append(cli(inc_home, "scan", body).returncode)
    codes.append(cli(inc_home, "update", project_id(inc_home)).returncode)
    # **The re-embed is not tidiness, it is what makes the comparison fair,
    # and leaving it out produced this checkpoint's one real finding.**
    # `update` fills L2/L3/L4 but not the vectors -- `embed` is a separate
    # command by design (CP-9, because M-3 measured the cost). Without this
    # line the incremental store had 5 of 6 files embedded while the rebuilt
    # one had 6 of 6, and the fused answer for "heron" ranked the new file
    # 4th instead of 2nd. That is correct behaviour from a half-embedded
    # index, not a defect -- so the gate was asking an unfair question and
    # gate 2 was right to go red. See FASIT-cp22 "Den ekte divergensen".
    codes.append(cli(inc_home, "embed", project_id(inc_home)).returncode)

    # Built on B from nothing.
    os.makedirs(fresh_home, exist_ok=True)
    codes += build(fresh_home, body)

    out.append(("0 both stores built", all(c == 0 for c in codes),
                "exit codes %s" % (codes,)))

    inc_ans = {q: answers(inc_home, q) for q in QUERIES}
    fresh_ans = {q: answers(fresh_home, q) for q in QUERIES}
    inc_fused = {q: answers(inc_home, q, fused=True) for q in QUERIES}
    fresh_fused = {q: answers(fresh_home, q, fused=True) for q in QUERIES}

    # Paths differ by home, so compare basenames: the question is the
    # ranking, not where the store happens to live.
    def names(lst):
        return None if lst is None else [os.path.basename(p) for p in lst]

    # One comparison, used by gate 1 and by control gate 6. Sharing it is
    # what makes the control able to fail: a weakening here shows up there.
    def compare(a, b):
        return a == b

    # `None` means the command refused, and two refusals compare equal.
    # **Gate 2 was fixed for exactly this and gate 1 was not**, which the
    # mutation harness found: breaking the ORDER BY clause made lexical
    # search refuse outright, and this gate reported "identical" over
    # `None vs None`. The same blind spot, one line apart, survived being
    # fixed once -- so presence is now part of both gates.
    lex_answered = all(inc_ans[q] is not None and fresh_ans[q] is not None
                       for q in QUERIES)
    same_lex = lex_answered and all(
        compare(names(inc_ans[q]), names(fresh_ans[q])) for q in QUERIES)
    out.append(("1 lexical answers identical after a rebuild, and present",
                same_lex,
                "answered=%s; " % lex_answered + "; ".join(
                    "%s: %s vs %s" % (q, names(inc_ans[q]),
                                      names(fresh_ans[q]))
                    for q in QUERIES)))

    # `None` means the command refused. Comparing two refusals is how the
    # first version of this gate passed without ever running a fusion, so
    # the answer being *present* is part of the gate rather than assumed.
    fused_answered = all(inc_fused[q] is not None and fresh_fused[q] is not None
                         for q in QUERIES)
    same_fused = fused_answered and all(
        names(inc_fused[q]) == names(fresh_fused[q]) for q in QUERIES)
    out.append(("2 fused answers identical after a rebuild, and present",
                same_fused,
                "answered=%s; fusion amplifies rank differences, so it is the "
                "sharper lens -- but only if it ran" % fused_answered))

    # Gate 3 is the one that keeps 1, 2 and 4 from passing over nothing.
    multi = sum(1 for q in QUERIES
                if inc_ans[q] is not None and len(inc_ans[q]) >= 2)
    total = sum(len(inc_ans[q] or []) for q in QUERIES)
    out.append(("3 the answers are not empty, and some have several hits",
                total > 0 and multi >= 2,
                "%d hit(s) across %d queries, %d with 2 or more"
                % (total, len(QUERIES), multi)))

    # Gate 4: order is compared as a list. Demonstrated mechanically rather
    # than asserted in a comment -- a reversed copy must not compare equal.
    sample = next((inc_ans[q] for q in QUERIES
                   if inc_ans[q] and len(inc_ans[q]) >= 2), None)
    order_matters = sample is not None and sample != list(reversed(sample))
    out.append(("4 order is compared as a list, not as a set", order_matters,
                "a set comparison could not see a swap: %s" % (names(sample),)))

    # -- negative controls -------------------------------------------------
    #
    # Gates 1-4 pass by construction. Without these, that is decoration.

    hurt = os.path.join(work, "hurt-home")
    shutil.copytree(fresh_home, hurt)
    dropped = _drop_one_l4_row(project_db(hurt))
    hurt_ans = {q: answers(hurt, q) for q in QUERIES}
    now_differs = any(names(hurt_ans[q]) != names(inc_ans[q])
                      for q in QUERIES)
    out.append(("5 CONTROL: dropping one L4 row turns gate 1 red",
                dropped is not None and now_differs,
                "dropped %s" % (os.path.basename(dropped or ""),)))

    # Gate 6: a swap must be caught, and it is applied to the SAME
    # comparison gate 1 uses rather than to a copied list.
    #
    # **The first version compared a hand-swapped list to itself**, which is
    # arithmetic, not a control: it could not fail, and the mutation harness
    # proved it by making gate 1 compare sets and watching the whole suite
    # stay green. Deletion changes membership, so gate 5 still caught that;
    # a pure reordering does not, and nothing tested it. The control now
    # runs the real comparison over a genuinely reordered answer.
    swapped = None
    caught_swap = False
    if sample is not None and len(sample) >= 2:
        swapped = list(sample)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        # `compare` is the one used by gate 1. If it is ever weakened to a
        # set comparison, this control goes red -- which is the point.
        caught_swap = not compare(names(swapped), names(sample))
    out.append(("6 CONTROL: a reordering alone turns gate 1 red",
                caught_swap,
                "same members, different order: %s vs %s -- a set comparison "
                "would call these equal and gate 1 would stop testing order"
                % (names(swapped), names(sample))))

    # Gate 7: gate 3 must itself be able to go red.
    empty = answers(inc_home, "kalligrafi_uten_treff_xyzzy")
    out.append(("7 CONTROL: a query with no hits would turn gate 3 red",
                empty is not None and len(empty) == 0,
                "%r produced %d hit(s)" % ("kalligrafi_uten_treff_xyzzy",
                                           len(empty or []))))

    # Gate 8: name what does take part, and what does not, with numbers.
    #
    # The vectors ARE rebuilt here and DO take part, through the fused
    # answer. What is named instead is the honest exception: the vector
    # *values* are not compared row by row, only the ranking they produce.
    # Two stores could hold different floats and still rank identically,
    # and this checkpoint would not notice. Said out loud rather than left
    # for a reader to discover -- a measured exception is a result, a
    # silent one is a lie.
    def count(db_path, table):
        db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            return db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        except sqlite3.Error:
            return -1
        finally:
            db.close()

    inc_vec = count(project_db(inc_home), "vectors")
    fresh_vec = count(project_db(fresh_home), "vectors")
    out.append(("8 what takes part and what does not is named with numbers",
                inc_vec > 0 and fresh_vec > 0,
                "vectors: %d incremental, %d rebuilt -- both feed the fused "
                "answer in gate 2. NOT compared: the vector values "
                "themselves, only the ranking they produce"
                % (inc_vec, fresh_vec)))

    # Gate 9: the embedding input is the content and nothing else.
    #
    # Taken from OpenViking (harvest 2026-08-20 §2a), which keeps an explicit
    # whitelist of what may enter an embedding and applies **the same policy
    # on reindex**, so that rebuilding the index cannot change retrieval
    # input. Here the property already holds by construction -- `embed`
    # selects `sha256, text` and chunks the text alone -- so this gate is
    # not new behaviour, it is the invariant written down where a future
    # change would trip over it.
    #
    # It belongs in CP-22 rather than in a checkpoint of its own because it
    # is the same question one layer down: CP-22 asks whether the answers
    # survive a rebuild, and this asks whether the *input* to the answers
    # does. A metadata field folded into the embedding text would break
    # both, and this gate names which one.
    same_count = inc_vec == fresh_vec
    src = open(os.path.join(REPO, "morpho_homegraph", "embed.py")).read()
    text_only = "SELECT DISTINCT sha256, text FROM content " in src
    out.append(("9 the embedding input is content only, on both paths",
                same_count and text_only,
                "%d vs %d vectors; embed reads `sha256, text` and chunks the "
                "text alone -- no path, no mtime, no metadata. A rebuild "
                "therefore cannot change retrieval input" % (inc_vec, fresh_vec)))
    return out


def main() -> int:
    """**Reports through `report.py`, and the first version did not.**

    A bespoke print loop here looked identical to a human and was invisible
    to `mutate.py`, which decides that a gate said no by reading a
    tab-separated `FAILED\\t<name>` line. Four of five mutations came back
    as `<crash>` for that reason alone -- the gates were working and the
    harness could not hear them. A crash is not a gate saying no, and a
    harness that cannot tell them apart grades nothing.
    """
    work = tempfile.mkdtemp(prefix="cp22-")
    try:
        results = gates(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _, check = reporter(58)
    for name, ok, detail in results:
        check(name, ok, detail)
    passed = sum(1 for _, ok, _ in results if ok)
    print("%d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


def test_checkpoint_cp22():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    raise SystemExit(main())
