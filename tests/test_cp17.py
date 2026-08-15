#!/usr/bin/env python3
"""CP-17 -- the cold rows, and a backfill that is not allowed to lie.

The answer key is `tests/gold/FASIT-cp17.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

The defect these gates exist for: `journal.build`'s `unchanged` branch copies
the previous hash forward, and the previous hash is `NULL`. A file that never
changes therefore never gets a hash, and a row without a hash can never be
reported `touched`. Measured on the real store before any of this was written:
4 773 files in scope, 51 hashed, 4 722 cold.

The rule that shapes every gate here is R3. A hash taken now is **not**
evidence that the file was unchanged at the previous pass, because no
comparison happened. Writing it in and letting it look like a confirmed
comparison forges evidence we do not have -- the same class of error that
`unconfirmed` exists to prevent. So the column carries *how* it was obtained,
and gates 4 through 7 are all about keeping those two apart.

Run:
    python3 tests/test_cp17.py
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.journal import BACKFILLED, COMPARED  # noqa: E402
from morpho_homegraph.store import l0_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(64)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=300):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    time.sleep(0.01)
    return path


def fresh_home(work, name):
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, name, "store")
    home = os.path.join(work, name, "home")
    os.makedirs(home, exist_ok=True)
    return home


def repo_at(root, ignore="notes/\n"):
    write(os.path.join(root, ".gitignore"), ignore)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    write(os.path.join(root, ".git", "HEAD"), "ref: refs/heads/main\n")
    return root


def add(root):
    out = cli("add", root)
    return out.stdout.split()[0] if out.stdout.strip() else ""


def _l0():
    return sqlite3.connect("file:%s?mode=ro" % l0_path(), uri=True)


def rows():
    """path -> (content_hash, hash_source) for the whole catalogue."""
    db = _l0()
    try:
        return {p: (h, s) for p, h, s in db.execute(
            "SELECT path, content_hash, hash_source FROM files")}
    finally:
        db.close()


def journal_tally():
    db = _l0()
    try:
        return dict(db.execute(
            "SELECT state, COUNT(*) FROM journal GROUP BY state"))
    finally:
        db.close()


def go_cold(home, root, name, body="cold\n"):
    """Produce the defect: a row in scope, unchanged, with no hash.

    The file is written *before* the project is registered, so the pass that
    catalogues it has no scope and stores NULL. Every later pass sees equal
    size and mtime and copies that NULL forward. This is the shape of all
    4 722 rows measured on the real store, reproduced rather than simulated --
    a fixture that just wrote NULL into the column would pass gate 1 for an
    implementation that never handles the real case.
    """
    path = write(os.path.join(root, name), body)
    cli("scan", home)
    return path


# -- 1, 2, 3, 15: what backfill touches ------------------------------------

def gates_scope(work):
    home = fresh_home(work, "scope")
    root = repo_at(os.path.join(home, "proj"))
    kept = go_cold(home, root, "kept.md")
    skipped = go_cold(home, root, os.path.join("notes", "skipped.md"))
    outside = write(os.path.join(home, "outside.md"), "no project\n")
    add(root)
    cli("scan", home)

    # The precondition. Without it every gate below is green for a store that
    # was never cold, which is the fixture failing rather than the code.
    before = rows()
    check("0  PRECONDITION: an unchanged in-scope file is cold after a scan",
          before.get(kept, (None, None))[0] is None,
          "kept.md hash: %r" % (before.get(kept, (None, None))[0],))

    # 15 is measured here, before backfill runs: the cheap pass must not have
    # quietly done the work itself.
    check("15 CONTROL: scan does not hash cold unchanged rows by itself",
          before.get(kept, (None, None))[0] is None)

    out = cli("backfill")
    after = rows()
    check("1  backfill gives a cold in-scope row a hash",
          out.returncode == 0 and after.get(kept, (None, None))[0] is not None,
          "rc=%s hash=%r" % (out.returncode,
                             (after.get(kept, (None, None))[0] or "")[:12]))
    check("2  CONTROL: a cold row outside every project stays untouched",
          after.get(outside, (None, None))[0] is None)
    check("3  CONTROL: a row .gitignore excludes is not backfilled",
          after.get(skipped, (None, None))[0] is None)

    # The hash has to be the file's, not merely non-NULL.
    import hashlib
    with open(kept, "rb") as fh:
        expected = hashlib.sha256(fh.read()).hexdigest()
    check("1b the backfilled hash is sha256 of the content",
          after.get(kept, (None, None))[0] == expected,
          "stored %r" % (after.get(kept, (None, None))[0] or "")[:12])


# -- 4, 5, 6, 7: compared is not backfilled --------------------------------

def gates_provenance(work):
    home = fresh_home(work, "prov")
    root = repo_at(os.path.join(home, "proj"))
    cold = go_cold(home, root, "cold.md")
    add(root)
    cli("scan", home)
    cli("backfill")
    check("4  a backfilled row is marked backfilled",
          rows().get(cold, (None, None))[1] == BACKFILLED,
          "source=%r" % (rows().get(cold, (None, None))[1],))

    # A file that arrives *after* the project is registered is hashed by the
    # `added` branch, from a real pass -- that is `compared`.
    fresh = write(os.path.join(root, "fresh.md"), "one\n")
    cli("scan", home)
    check("5  CONTROL: a row hashed by a real pass is marked compared",
          rows().get(fresh, (None, None))[1] == COMPARED,
          "source=%r" % (rows().get(fresh, (None, None))[1],))

    # 6: the warm-up completes. The backfilled row changes, the pass compares
    # it, and the provenance is upgraded -- the hash is now evidence.
    write(cold, "cold changed\n")
    cli("scan", home)
    got = rows().get(cold, (None, None))
    check("6  a backfilled row that then changes becomes changed and compared",
          got[1] == COMPARED and journal_tally().get("changed", 0) >= 1,
          "source=%r journal=%s" % (got[1], journal_tally()))

    # 7: and one that does *not* change keeps its provenance. Without this,
    # gate 6 is green for an implementation that stamps `compared` on
    # everything it sees.
    #
    # The cold row has to be made in a *second* project registered later:
    # `go_cold` only produces a cold row while no scope covers it, and by now
    # this project is registered, so a file written here is hashed by the
    # `added` branch and is `compared` from birth. Getting this wrong made
    # gate 7 red on the first run -- the fixture, not the code.
    still = write(os.path.join(root, "still.md"), "never edited\n")
    cli("scan", home)          # `added` inside a live scope, so compared
    later = repo_at(os.path.join(home, "later"))
    quiet = go_cold(home, later, "quiet.md")   # no scope yet -> cold
    add(later)
    cli("scan", home)
    cli("backfill")
    check("7  PRECONDITION: the row was backfilled before the quiet pass",
          rows().get(quiet, (None, None))[1] == BACKFILLED,
          "source=%r" % (rows().get(quiet, (None, None))[1],))
    cli("scan", home)          # a pass in which nothing changed
    check("7  CONTROL: a backfilled row that does not change stays backfilled",
          rows().get(quiet, (None, None))[1] == BACKFILLED,
          "source=%r" % (rows().get(quiet, (None, None))[1],))
    check("7b CONTROL: an untouched compared row stays compared",
          rows().get(still, (None, None))[1] == COMPARED)


# -- 8, 9, 10: backfill is not a pass --------------------------------------

def gates_isolation(work):
    home = fresh_home(work, "iso")
    root = repo_at(os.path.join(home, "proj"))
    go_cold(home, root, "a.md")
    go_cold(home, root, "b.md")
    add(root)
    cli("scan", home)

    before = journal_tally()
    cli("backfill")
    check("8  backfill writes no journal row: the tally is unchanged",
          journal_tally() == before,
          "%s -> %s" % (before, journal_tally()))

    second = cli("backfill")
    check("9  backfill is idempotent: the second run hashes 0 files",
          second.returncode == 0
          and second.stdout.startswith("0 file(s) hashed of 0 cold"),
          second.stdout.strip().splitlines()[-1] if second.stdout else "")

    # 10: a partial run leaves whole rows only. Interrupting a subprocess
    # mid-hash is a race; instead the store is inspected for the invariant the
    # interruption could break -- no row may carry a hash without a source, or
    # a source without a hash. That is what "no half-written row" means here,
    # and it is checkable without winning a race.
    broken = [p for p, (h, s) in rows().items()
              if (h is None) != (s is None)]
    check("10 CONTROL: no row carries a hash without a source, or the reverse",
          not broken, "%d broken row(s): %s" % (len(broken), broken[:3]))


# -- 11, 12: status says how much is covered -------------------------------

def gates_coverage(work):
    home = fresh_home(work, "cov")
    root = repo_at(os.path.join(home, "proj"))
    go_cold(home, root, "one.md")
    go_cold(home, root, "two.md")
    project_id = add(root)
    cli("scan", home)

    cold_status = cli("status", project_id).stdout
    # 12 first: before any hash exists the coverage must be stated as 0, not
    # left blank. CP-7B R8 -- an empty index must not be able to look finished.
    check("12 CONTROL: a project with no hashes shows 0 %%, not a blank",
          "hashed" in cold_status and "0 %" in cold_status,
          [ln for ln in cold_status.splitlines() if "hashed" in ln] or "absent")

    cli("backfill")
    warm = cli("status", project_id).stdout
    line = [ln for ln in warm.splitlines() if "hashed" in ln]
    # The counts, not the words. Asserting that the string "backfilled"
    # appears is green for an implementation that prints the label and totals
    # everything into `compared` -- measured: that mutation survived the first
    # sweep. Here all three hashes came from backfill, so the split is
    # 0 compared / 3 backfilled and nothing else can satisfy it.
    check("11 status reports hashed/in_scope and counts backfilled separately",
          bool(line) and "3/3 hashed" in warm and "100 %" in warm
          and "0 compared" in warm and "3 backfilled" in warm,
          line or "absent")


# -- 13, 14: the ceiling is stated before the work -------------------------

def gates_ceiling(work):
    home = fresh_home(work, "ceil")
    root = repo_at(os.path.join(home, "proj"))
    for n in range(3):
        go_cold(home, root, "f%d.md" % n, "body %d\n" % n)
    add(root)
    cli("scan", home)
    # Counted from the command's own dry run, not from the fixture: `rows()`
    # includes paths outside every scope, which are cold and must stay cold,
    # so counting NULLs there over-counts. Hard-coding 3 was wrong for the
    # same reason in the other direction -- `.gitignore` is in scope and cold
    # too. Both mistakes made gates 13 and 14 red for correct code.
    preview = cli("backfill", "--dry-run")
    # Parsed defensively: a mutation that makes `--dry-run` do the work prints
    # a different first word, and a test that crashes on it reports
    # `<crash>` instead of naming a gate -- which is a mutation detected by
    # nothing anyone can read. See [[a-crash-in-front-of-a-gate-names-no-gate]].
    head = preview.stdout.split()[0] if preview.stdout.split() else ""
    cold = int(head) if head.isdigit() else -1

    check("13 backfill states file count and bytes before hashing",
          preview.returncode == 0 and ("%d file" % cold) in preview.stdout
          and "byte" in preview.stdout.lower(),
          preview.stdout.strip().splitlines()[-1] if preview.stdout else "")
    check("13b CONTROL: --dry-run hashes nothing",
          all(h is None for h, _ in rows().values()),
          "%d hashed" % sum(1 for h, _ in rows().values() if h))

    # 14: above the limit it refuses rather than spending the time unasked,
    # and the refusal says why.
    refused = cli("backfill", "--max-files", str(cold - 1))
    said = refused.stdout + refused.stderr
    check("14 above the limit backfill refuses and names the reason",
          refused.returncode != 0 and ("%d" % cold) in said
          and "--max-files" in said,
          said.strip().splitlines()[-1] if said else "")
    check("14b CONTROL: a refusal hashes nothing",
          all(h is None for h, _ in rows().values()))
    check("14c the same run under the limit proceeds",
          cli("backfill", "--max-files", "99").returncode == 0
          and any(h for h, _ in rows().values()))


def gates_migration(work):
    """16, 17: the column reaches a store that already exists.

    Not in the answer key's original list, and added because the condition
    detector said the two branches in `Store.migrate` had nothing aimed at
    them. It is also the only part of CP-17 that touches the real catalogue:
    `CREATE TABLE IF NOT EXISTS` is a no-op against a live file, so without
    this the column exists only in stores created after today -- and the store
    that matters is the one already holding 485 735 rows.
    """
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.store import L0, PROJECT, Store

    path = os.path.join(work, "mig", "l0", "index.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # A store as it looked before CP-17: the table exists, the column does not.
    guard = StoreLock(path).acquire()
    try:
        with Store(path, role=L0) as store:
            with store.writing():
                store.db.execute("ALTER TABLE files DROP COLUMN hash_source")
                store.db.execute(
                    "INSERT INTO files (path, kind, size, mtime_ns, inode, dev)"
                    " VALUES ('/old', 'file', 1, 1, 1, 1)")
                # A row that already carried a hash when the column vanished:
                # the pre-CP-17 shape, and what gate 16c is about.
                store.db.execute(
                    "INSERT INTO files (path, kind, size, mtime_ns, inode,"
                    " dev, content_hash)"
                    " VALUES ('/hashed', 'file', 1, 1, 2, 1, 'deadbeef')")
                store.db.commit()
        # Reopening runs migrate() against a table that is already there.
        with Store(path, role=L0) as store:
            columns = {row[1] for row in store.db.execute(
                "PRAGMA table_info(files)").fetchall()}
            kept = store.db.execute(
                "SELECT COUNT(*) FROM files").fetchone()[0]
            labelled = tuple(row[0] for row in store.db.execute(
                "SELECT hash_source FROM files WHERE path = '/hashed'"))
    finally:
        guard.release()
    check("16 the column is added to a store that already exists",
          "hash_source" in columns, "columns: %s" % sorted(columns))
    check("16b CONTROL: the migration keeps the rows that were there",
          kept == 2, "%d row(s) survived" % kept)
    # 16c: found on the real catalogue after 16 and 16b were both green. A row
    # that already had a hash when the column arrived was put there by
    # `journal.build`, the only writer that existed before CP-17 -- so it is
    # `compared`, and leaving it NULL breaks gate 10's invariant on every
    # store that predates today. 45 real rows were in that state.
    check("16c the migration labels hashes that predate the column",
          labelled == ("compared",), "sources: %s" % (labelled,))

    # 17: role-gated. A project store has no `files` table at all, and asking
    # for a column on a table that does not exist would raise rather than skip.
    project = os.path.join(work, "mig", "proj", "index.db")
    os.makedirs(os.path.dirname(project), exist_ok=True)
    guard = StoreLock(project).acquire()
    try:
        with Store(project, role=PROJECT) as store:
            tables = {row[0] for row in store.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        guard.release()
    check("17 CONTROL: a project store is unaffected and still opens",
          "files" not in tables and "content" in tables,
          "tables: %s" % sorted(tables))

    # 18: found on the real catalogue, not by these tests. `status` opens L0
    # read-only, and a read-only open does not migrate -- so against a store
    # built before CP-17 the coverage query hit `no such column` and printed a
    # traceback. A reader must degrade and say why; every gate above used a
    # store this process had already migrated, which is exactly the blind spot
    # a fixture has and a real machine does not.
    from morpho_homegraph.backfill import coverage
    guard = StoreLock(path).acquire()
    try:
        with Store(path, role=L0) as store:
            with store.writing():
                store.db.execute("ALTER TABLE files DROP COLUMN hash_source")
                store.db.commit()
        stale = sqlite3.connect("file:%s?mode=ro" % path, uri=True)

        class _RO:
            db = stale

        try:
            report = coverage(_RO(), lambda _p: True)
        except sqlite3.OperationalError as exc:
            # The probe failing is the thing this gate is about, so it has to
            # be *reported*, not raised: a crash in front of a gate names no
            # gate, and the mutation that broke the probe came back
            # `<crash>` until this caught it.
            report = {"migrated": "raised %s" % exc, "hashed": -1}
        finally:
            stale.close()
    finally:
        guard.release()
    check("18 a catalogue without the column reports unknown, not a crash",
          report["migrated"] is False and report["hashed"] == 0,
          "%s" % report)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp17-") as work:
        gates_scope(work)
        gates_provenance(work)
        gates_isolation(work)
        gates_coverage(work)
        gates_ceiling(work)
        gates_migration(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp17():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
