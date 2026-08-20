#!/usr/bin/env python3
"""CP-23 -- freshness per directory, counting direct children.

The answer key is `tests/gold/FASIT-cp23.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

**This checkpoint's danger is not that it breaks, it is that it is empty.**
Grouping a dict by `dirname` always works; the question the gold answer put
first is whether the grouping *says* anything, which is why gate 11 is a
measurement against a real store rather than a fixture. Gates 2 and 7 are
negative controls, because a counter that counts the subtree and a counter
that counts the whole journal both produce plausible numbers.

Run:
    python3 tests/test_cp23.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import dirfresh, freshness, journal  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(62)

# The fixture's shape, written here rather than counted from the tree, so
# that gate 2 has a number a subtree-counting bug cannot also satisfy. Root
# holds 2 files directly, `sub/` holds 2, `sub/deep/` holds 1 -- so a counter
# that rolled the subtree up would give the root 5.
DIRECT = {"": 2, "sub": 2, os.path.join("sub", "deep"): 1}


def cli(work, *argv, timeout=300):
    env = dict(os.environ, MORPHO_HOMEGRAPH_HOME=os.path.join(work, "store"))
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO, env=env,
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
    """Three levels, so "direct children" and "the subtree" differ by a lot."""
    write(os.path.join(root, "barrier.md"),
          "The write barrier refuses a second writer, and the lock is held "
          "for the whole session.\n")
    write(os.path.join(root, "notes.md"), "A short note about very little.\n")
    write(os.path.join(root, "sub", "handlers.py"),
          "def getUserById(conn):\n    return 1\n")
    write(os.path.join(root, "sub", "second.md"),
          "The heron stands in the shallow water and does not move.\n")
    write(os.path.join(root, "sub", "deep", "buried.md"),
          "A stone at the bottom, three levels down from the root.\n")
    return root


# -- 1, 2, 3: direct children, and the control that makes 1 mean something --

def gates_direct():
    """Pure-function gates: no store, so the arithmetic is visible."""
    state = {
        "barrier.md": freshness.FRESH,
        "notes.md": freshness.STALE,
        os.path.join("sub", "handlers.py"): freshness.FRESH,
        os.path.join("sub", "second.md"): freshness.UNEMBEDDED,
        os.path.join("sub", "deep", "buried.md"): freshness.UNREAD,
    }
    rows = dirfresh.per_dir(state)

    check("1  a file two levels down is counted in its own directory",
          rows.get(os.path.join("sub", "deep"), {}).get("total") == 1
          and os.path.join("sub", "deep") in rows,
          "%s" % sorted(rows))

    # 2: the negative control for 1. A counter that rolled the subtree up
    # would give the root 5 and `sub` 3 -- both plausible numbers, and both
    # wrong. The expected totals come from DIRECT, written before the code.
    totals = {directory: row["total"] for directory, row in rows.items()}
    check("2  CONTROL: no directory holds its grandchildren's files",
          totals == DIRECT, "%s, expected %s" % (totals, DIRECT))

    # 3: one source, two views. The per-directory sums must equal the
    # per-file tally for the same files, or the grouping invented something.
    per_file_tally = freshness.tally(state)
    summed = {name: 0 for name in per_file_tally if name != "?"}
    for row in rows.values():
        for name, count in row["states"].items():
            summed[name] += count
    check("3  the per-directory states sum to the per-file tally",
          summed == {k: v for k, v in per_file_tally.items() if k != "?"},
          "%s vs %s" % (summed, per_file_tally))

    # Every state present at zero, for the same reason `tally` guarantees it.
    check("3b every directory names all four states, zero included",
          all(set(row["states"]) == {freshness.FRESH, freshness.STALE,
                                     freshness.UNREAD, freshness.UNEMBEDDED}
              for row in rows.values()),
          "%s" % [sorted(r["states"]) for r in rows.values()][:1])


# -- 6, 7: pending changes come from L1, and only the pending states --------

class FakeJournal:
    """A store-shaped thing holding one journal. Enough for `pending_by_dir`.

    A real store would work too and would be slower and less clear: the
    question here is which *states* are counted, not whether SQLite works.

    It carries a `files` table because the real L0 does, and because the
    journal holds directories as well as files -- gate 6i is that fact.
    """

    def __init__(self, rows, dirs=()):
        import sqlite3
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE journal (path TEXT, state TEXT)")
        self.db.execute("CREATE TABLE files (path TEXT, kind TEXT)")
        self.db.executemany("INSERT INTO journal VALUES (?, ?)", rows)
        self.db.executemany(
            "INSERT INTO files VALUES (?, ?)",
            [(path, "dir" if path in set(dirs) else "file")
             for path, _state in rows])


def gates_pending():
    rows = [
        (os.path.join("sub", "a.md"), journal.ADDED),
        (os.path.join("sub", "b.md"), journal.CHANGED),
        (os.path.join("sub", "c.md"), journal.TOUCHED),
        (os.path.join("sub", "d.md"), journal.UNCONFIRMED),
        (os.path.join("sub", "e.md"), journal.UNCHANGED),
        (os.path.join("sub", "f.md"), journal.REMOVED),
        ("g.md", journal.CHANGED),
    ]
    pending = dirfresh.pending_by_dir(FakeJournal(rows))
    check("6  pending counts the four L1 states that are actually pending",
          pending.get("sub") == 4 and pending.get("") == 1,
          "%s" % pending)

    # 7: the negative control. A counter that summed the whole journal would
    # say 6 for `sub` and would look entirely reasonable.
    quiet = dirfresh.pending_by_dir(FakeJournal(
        [(os.path.join("sub", "x.md"), journal.UNCHANGED),
         (os.path.join("sub", "y.md"), journal.REMOVED)]))
    check("7  CONTROL: a journal with nothing pending gives no pending",
          quiet == {}, "%s" % quiet)

    # The scope predicate: L1 describes the home area, L2 one project.
    scoped = dirfresh.pending_by_dir(
        FakeJournal(rows), keep=lambda path: path.startswith("sub"))
    check("6b a pending change outside the project's scope is not counted",
          scoped.get("sub") == 4 and "" not in scoped, "%s" % scoped)

    # 6e: the fix for gate 5 must not turn pending into a number that can
    # never fire. `known` is the paths L2 holds, so a file the catalogue sees
    # and L2 has never read is still pending -- and one it has read is not.
    seen = dirfresh.pending_by_dir(
        FakeJournal(rows), known={os.path.join("sub", "a.md"),
                                  os.path.join("sub", "b.md")})
    check("6e CONTROL: pending still fires for a file L2 has never read",
          seen.get("sub") == 2 and seen.get("") == 1, "%s" % seen)
    all_known = dirfresh.pending_by_dir(
        FakeJournal(rows), known={path for path, _state in rows})
    check("6f CONTROL: nothing is pending when L2 already holds it all",
          all_known == {}, "%s" % all_known)

    # 6i: the journal holds directories too, and L2 only ever holds files.
    # Counting a directory as pending makes a count that can never clear --
    # gate 5's failure in a second disguise, and this is where it was caught.
    with_dir = dirfresh.pending_by_dir(
        FakeJournal([(os.path.join("sub", "a.md"), journal.ADDED),
                     (os.path.join("sub", "nested"), journal.ADDED)],
                    dirs=[os.path.join("sub", "nested")]))
    check("6i CONTROL: a directory in the journal is not a pending file",
          with_dir.get("sub") == 1, "%s" % with_dir)

    # A store with no journal table at all: degrade, do not raise (CP-17).
    class NoJournal:
        import sqlite3 as _s
        db = _s.connect(":memory:")
    try:
        empty = dirfresh.pending_by_dir(NoJournal())
        ok = empty == {}
    except Exception as err:                             # noqa: BLE001
        ok, empty = False, repr(err)
    check("6c a catalogue with no journal answers nothing, and does not raise",
          ok, "%s" % (empty,))
    check("6d no catalogue at all answers nothing",
          dirfresh.pending_by_dir(None) == {}, "")


# -- 8, 9: absence, and a stable order -------------------------------------

def gates_shape():
    # 8: a directory L2 holds no rows for is absent, not present with zeroes.
    rows = dirfresh.per_dir({"a/x.md": freshness.FRESH})
    check("8  a directory with no rows is absent, not present with zeroes",
          set(rows) == {"a"}, "%s" % sorted(rows))

    ranked = dirfresh.ranked(dirfresh.per_dir({
        "worst/1.md": freshness.STALE,
        "worst/2.md": freshness.STALE,
        "mid/1.md": freshness.UNREAD,
        "b_fresh/1.md": freshness.FRESH,
        "a_fresh/1.md": freshness.FRESH,
    }))
    order = [directory for directory, _row in ranked]
    check("9  worst first, and equal rows fall in path order",
          order == ["worst", "mid", "a_fresh", "b_fresh"], "%s" % order)

    again = [d for d, _r in dirfresh.ranked(dirfresh.per_dir({
        "worst/1.md": freshness.STALE,
        "worst/2.md": freshness.STALE,
        "mid/1.md": freshness.UNREAD,
        "b_fresh/1.md": freshness.FRESH,
        "a_fresh/1.md": freshness.FRESH,
    }))]
    check("9b an unchanged store gives the same order twice",
          again == order, "%s" % again)

    # R6: the line names which state is behind the count, because stale,
    # unread and unembedded have three different commands.
    line = dirfresh.describe("sub", dirfresh.per_dir({
        "sub/a.md": freshness.FRESH,
        "sub/b.md": freshness.UNREAD,
    })["sub"])
    check("R6 the line names the state behind the count, not just a fraction",
          "1/2 fresh" in line and "1 unread" in line, "%r" % line.strip())


# -- 4, 5, 10, 12: through the command, against a real store ---------------

def gates_command(work):
    home = corpus(os.path.join(work, "home"))
    cli(work, "scan", work)
    added = cli(work, "add", home)
    project_id = added.stdout.split()[0] if added.stdout.strip() else ""
    built = cli(work, "update", project_id)
    if built.returncode != 0:
        check("0  the project builds before anything is asked", False,
              "update exited %s: %s" % (built.returncode, built.stderr[:80]))
        return
    cli(work, "embed", project_id)

    fresh_run = cli(work, "stale", project_id)
    # 5: the control. Everything read, current and embedded means nothing is
    # behind -- and it is said in words. Without this, gate 4 passes for a
    # counter that calls everything stale.
    check("5  CONTROL: a project that is read, current and embedded is clean",
          fresh_run.returncode == 0
          and "every directory is fresh" in fresh_run.stdout,
          "%r" % fresh_run.stdout.strip().splitlines()[:2])

    # 10: the three clocks, printed whether or not anything is behind. This
    # is CP-12 gate 5 one floor up: "no age" must not become a signal.
    check("10 the age line is printed even when every directory is fresh",
          re.search(r"catalogue \d+ \w+\s+content \d+ \w+\s+vectors \d+ \w+",
                    fresh_run.stdout) is not None,
          "%r" % fresh_run.stdout.strip().splitlines()[-1:])

    # 4: change one file, in one directory, and rescan. The catalogue sees
    # the new mtime; L2 still holds what it read.
    time.sleep(1.1)
    write(os.path.join(home, "sub", "second.md"),
          "The heron has moved, and the water is still.\n")
    cli(work, "scan", work)
    after = cli(work, "stale", project_id)
    lines = [line for line in after.stdout.splitlines()
             if line.strip() and "catalogue" not in line]
    named = {line.split()[0] for line in lines}
    check("4  a change makes its own directory unfresh, not its sibling",
          after.returncode == 0 and "sub" in named
          and "." not in named and os.path.join("sub", "deep") not in named,
          "%s" % sorted(named))

    check("4b the changed directory says stale, and says how many",
          any(line.startswith("sub ") and "1 stale" in line
              for line in lines), "%s" % lines)

    # 6g: pending end to end, and the reason it is a separate fact from
    # `stale`. A brand new file is in the catalogue and has no L2 row at all,
    # so `freshness.per_file` -- which iterates the rows L2 has -- structurally
    # cannot see it. Without this gate the whole pending column is decoration.
    #
    # **It goes in `sub/deep`, which is entirely fresh, and that placement is
    # the gate.** The first version put it in `sub`, which gate 4 had just
    # made stale -- so `not_fresh` was already true, the listing filter's
    # `pending` disjunct was masked, and the mutation that drops `pending`
    # SURVIVED. A directory that is fresh in every file and only behind by a
    # file it has never read is the one fixture that can see it.
    deep = os.path.join("sub", "deep")
    time.sleep(1.1)
    write(os.path.join(home, "sub", "deep", "arrived.md"),
          "A file the catalogue has seen and the project has not read.\n")
    cli(work, "scan", work)
    unread_yet = cli(work, "stale", project_id)
    check("6g a fresh directory behind only by an unread file is pending",
          any(line.startswith(deep + " ") and "1/1 fresh" in line
              and "1 pending" in line
              for line in unread_yet.stdout.splitlines()),
          "%r" % unread_yet.stdout.strip().splitlines()[:3])

    # And it stops being pending once it is read: a count that only ever
    # grows is the same failure gate 5 caught, from the other side.
    cli(work, "update", project_id)
    ingested = cli(work, "stale", project_id, "--all")
    check("6h CONTROL: it stops being pending once update has read it",
          "pending" not in ingested.stdout,
          "%r" % ingested.stdout.strip().splitlines()[:3])

    # `--all` lists the fresh ones too; the default answers the question.
    listed = cli(work, "stale", project_id, "--all")
    check("8b --all lists every directory L2 holds rows for",
          {line.split()[0] for line in listed.stdout.splitlines()
           if line.strip() and "catalogue" not in line}
          == {".", "sub", deep},
          "%r" % listed.stdout.strip())

    check("12 CONTROL: an ordinary run exits 0",
          fresh_run.returncode == 0 and after.returncode == 0
          and listed.returncode == 0,
          "%s %s %s" % (fresh_run.returncode, after.returncode,
                        listed.returncode))


# -- 11: the measurement that decides whether this checkpoint is worth it ---

def gates_measurement():
    """R7's number exists, was produced by a tool, and is written down.

    **This gate was missing from the first version of this file**, found by
    auditing the gold answer against the gate numbers the run actually prints
    -- not by grepping for `check(`, because cp22 builds its names in a loop
    and a source scan reports zero for a module that gates ten. Every other
    checkpoint in the repository reported every gate its answer key names;
    CP-23 reported eleven of twelve. A requirement met in fact and unenforced
    is exactly the silent omission the house rules forbid, and it is worse
    here than elsewhere: gate 11 is the one that can say *this checkpoint was
    not worth building*.

    What is checkable is the shape, not the value. The number itself moves
    with the corpus -- that is the whole point of R7 -- so pinning `54.5` here
    would be a gate against a home area that is allowed to change. What must
    hold is that the measurement is **reproducible from a tool** rather than
    asserted in prose, and that its answer reached the working record.

    `TODO.md` is git-ignored by design (it names the account and home paths),
    so in a fresh clone this degrades to SKIPPED rather than failing --
    the same treatment `test_no_real_paths.py` gate 15 gives the same file.
    A skip is not a pass, and it says which half it could not check.
    """
    tool = os.path.join(REPO, "tools", "m8_dir_mixture.py")
    check("11a the R7 measurement is a tool that can be re-run, not a claim",
          os.path.isfile(tool), tool)

    # **The first version of this gate asserted the tool's header**, which is
    # printed even when there is no project to measure -- so "mixed" appeared
    # in stdout for a tool that had measured nothing, and both needles aimed
    # at gate 11 SURVIVED. A gate that passes on a header is a gate that
    # cannot say "this checkpoint was not worth building", which is the one
    # thing gate 11 exists to be able to say.
    #
    # So the tool is run against a store built here, and the assertion is on
    # the *answer*: a project row, and a mixture percentage. The fixture is
    # deliberately mixed -- one unreadable file and one that is merely absent
    # from the vectors -- so a real measurement has something to find.
    with tempfile.TemporaryDirectory(prefix="mhg-cp23-m8-") as work:
        home = corpus(os.path.join(work, "home"))
        with open(os.path.join(home, "sub", "picture.bin"), "wb") as fh:
            fh.write(b"\x89PNG\x00\x00binary\x00bytes")
        cli(work, "scan", work)
        added = cli(work, "add", home)
        measured_id = added.stdout.split()[0] if added.stdout.strip() else ""
        cli(work, "update", measured_id)
        env = dict(os.environ,
                   MORPHO_HOMEGRAPH_HOME=os.path.join(work, "store"))
        ran = subprocess.run([sys.executable, tool], capture_output=True,
                             text=True, cwd=REPO, env=env, timeout=300)

    percent = re.search(r"mixed ([\d.,]+) %", ran.stdout)
    check("11b the measurement runs and answers the mixture question",
          ran.returncode == 0 and measured_id and measured_id in ran.stdout
          and percent is not None,
          "exit %s, %r" % (ran.returncode, ran.stdout.strip().splitlines()))

    # 11b-control: a header alone must not satisfy 11b. Run the same tool
    # against an empty store -- it exits 0 and prints the header, and the
    # percentage line is absent. Without this, the survivors come back.
    with tempfile.TemporaryDirectory(prefix="mhg-cp23-m8e-") as empty:
        bare = subprocess.run(
            [sys.executable, tool], capture_output=True, text=True, cwd=REPO,
            env=dict(os.environ, MORPHO_HOMEGRAPH_HOME=empty), timeout=300)
    check("11b CONTROL: a header with no project is not a measurement",
          "mixed" in bare.stdout
          and re.search(r"mixed ([\d.,]+) %", bare.stdout) is None,
          "%r" % bare.stdout.strip().splitlines())

    record = os.path.join(REPO, "TODO.md")
    if not os.path.isfile(record):
        # Not a pass. A clone cannot see the working record, and saying so
        # is the honest answer -- silently counting it green would make this
        # gate report coverage it does not have.
        print("SKIP  11c the measured number is written down"
              "                  no TODO.md here (git-ignored by design)")
        return
    body = open(record, encoding="utf-8").read()
    # Two checks, not one conjunction. A needle that removed the tool-name
    # half SURVIVED against `A and B`, because the mixture half alone kept it
    # green -- the same masking that let a disjunct hide in gate 6g. An
    # unattributed number and a missing number are different failures and get
    # different gates.
    check("11c the measured number is written down",
          re.search(r"\d+[.,]\d+ %", body) is not None and "blandet" in body,
          "mixture recorded %s" % ("blandet" in body))
    check("11d the number names the tool that produced it, so it is re-runnable",
          "m8_dir_mixture" in body,
          "tool named %s" % ("m8_dir_mixture" in body))


def main() -> int:
    gates_direct()
    gates_pending()
    gates_shape()
    gates_measurement()
    with tempfile.TemporaryDirectory(prefix="mhg-cp23-") as work:
        gates_command(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp23():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
