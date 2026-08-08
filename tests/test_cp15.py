#!/usr/bin/env python3
"""CP-15 -- L1 gets a scope, and the journal gets a reader.

The answer key is `tests/gold/FASIT-cp15.md`, written before this file and
before the code it grades (`f276ed2`). Gate numbers below are that document's.

**Gates 14 and 15 are discharged in `tests/test_cp14.py`, not here.** The
answer key says CP-14's gates 11 and 12 are re-decided rather than deleted,
and that is where they live -- rerunning CP-14's whole corpus from this file
to assert it a second time would be a copy that can drift. What this file
carries in their place is 15b: the finding that turned up while making them
true, which is that a project registered *after* a scan is not hashed until
the next one.

The rest is the warm-up (6, 7, 8), which is the part nobody would guess:
switching the scope on does not backfill, so a file that existed before it
needs **two** changes before `changed` can fire for it.

Run:
    python3 tests/test_cp15.py
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

from morpho_homegraph import service  # noqa: E402
from morpho_homegraph.journal import (  # noqa: E402
    CHANGED, TOUCHED, UNCHANGED, UNCONFIRMED)
from morpho_homegraph.store import db_path, l0_path  # noqa: E402

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
    # A distinct mtime per write, so "size and mtime both moved" is a fact and
    # not a race with the filesystem's timestamp granularity.
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


def states():
    db = _l0()
    try:
        return dict(db.execute("SELECT path, state FROM journal"))
    finally:
        db.close()


def hashes():
    db = _l0()
    try:
        return dict(db.execute("SELECT path, content_hash FROM files"))
    finally:
        db.close()


# -- 1, 2, 3, 4, 5, 16, 17: the union --------------------------------------

def gates_union(work):
    """What gets hashed, and what decides it."""
    home = fresh_home(work, "union")

    # 16 first: an empty registry is not an error, and it is what every scan
    # did until today -- so this is also the old behaviour, kept as a control.
    empty = cli("scan", home)
    check("16 CONTROL: a scan with no registered projects exits 0, hashes none",
          empty.returncode == 0 and set(hashes().values()) <= {None},
          "rc=%s, %d distinct hash values"
          % (empty.returncode, len(set(hashes().values()))))

    root = repo_at(os.path.join(home, "proj"))
    write(os.path.join(root, "kept.md"), "in scope\n")
    write(os.path.join(root, "notes", "skipped.md"), "gitignored\n")
    write(os.path.join(home, "outside.md"), "under no project at all\n")
    project_id = add(root)
    check("1  scan takes the union of every registered project's scope",
          cli("scan", home).returncode == 0
          and hashes().get(os.path.join(root, "kept.md")) is not None,
          "kept.md hash: %r"
          % (hashes().get(os.path.join(root, "kept.md")) or "")[:12])
    check("3  a file inside a registered project's scope gets a hash",
          hashes().get(os.path.join(root, "kept.md")) is not None)
    # 4 and 5 are the controls that stop "the union" from being "everything".
    check("4  CONTROL: a file .gitignore excludes gets no hash",
          hashes().get(os.path.join(root, "notes", "skipped.md")) is None)
    check("5  CONTROL: a file outside every project gets no hash",
          hashes().get(os.path.join(home, "outside.md")) is None)

    # 2: the saved scope is falsified, and must change nothing. A union read
    # from the stores would follow the lie; one recomputed from disk cannot.
    with sqlite3.connect(db_path(project_id)) as store:
        store.execute("DELETE FROM scope")
        store.execute("INSERT INTO scope (path, mode) VALUES (?, 'exclude')",
                      (root,))
        store.commit()
    cli("scan", home)
    followed_the_lie = hashes().get(os.path.join(root, "kept.md")) is None
    check("2  CONTROL: the union is recomputed from disk, not loaded",
          not followed_the_lie,
          "a falsified stored scope changed the answer" if followed_the_lie
          else "")

    # 16b: the default is reachable -- `scan(store, root)` with no predicate
    # is what a catalogue-only build is, and it must hash nothing. Gate 16
    # goes through the CLI, which always hands over a union, so it cannot
    # reach the default at all; the mutation that flipped it survived a sweep
    # on 2026-08-08 for exactly that reason.
    from morpho_homegraph.lock import StoreLock
    from morpho_homegraph.store import L0, Store
    bare = os.path.join(work, "bare", "l0", "index.db")
    os.makedirs(os.path.dirname(bare), exist_ok=True)
    guard = StoreLock(bare).acquire()
    try:
        with Store(bare, role=L0) as l0:
            from morpho_homegraph.scan import scan as scan_fn
            scan_fn(l0, root)
            bare_hashes = {h for (h,) in l0.db.execute(
                "SELECT DISTINCT content_hash FROM files")}
    finally:
        guard.release()
    check("16b CONTROL: a catalogue built with no predicate hashes nothing",
          bare_hashes <= {None}, "distinct values: %d" % len(bare_hashes))

    hashed = sum(1 for h in hashes().values() if h)
    check("17 the ceiling is measured, not assumed",
          hashed > 0,
          "%d of %d catalogued paths hashed this sweep"
          % (hashed, len(hashes())))
    return project_id


# -- 6, 7, 8, 9, 15b: the warm-up ------------------------------------------

def gates_warmup(work):
    """Switching the scope on does not backfill, and that is R3."""
    home = fresh_home(work, "warmup")
    root = repo_at(os.path.join(home, "proj"))
    old = write(os.path.join(root, "old.md"), "existed before the scope\n")

    # The world as it was: a scan with nothing registered hashes nothing.
    cli("scan", home)
    project_id = add(root)
    # 15b: registering does not reach back into the catalogue. The scan that
    # ran before the project existed hashed nothing, and the next scan sees
    # the file as unchanged -- so it carries a NULL forward. Found while making
    # CP-14's gates 11 and 12 true, where the same order made a comparison
    # that looked fair and was not.
    cli("scan", home)
    check("15b a project registered after a scan is not hashed until the next"
          " change", hashes().get(old) is None
          and states().get(old) == UNCHANGED,
          "hash=%r state=%r" % (hashes().get(old), states().get(old)))

    write(old, "changed once, and nothing to compare against\n")
    cli("scan", home)
    check("6  the first change after the switch is unconfirmed, not a verdict",
          states().get(old) == UNCONFIRMED and hashes().get(old) is not None,
          "state=%r, hash now stored=%s"
          % (states().get(old), hashes().get(old) is not None))

    write(old, "changed twice, and now there is a hash to compare\n")
    cli("scan", home)
    check("7  the second change is changed, so the warm-up is exactly two",
          states().get(old) == CHANGED, "state=%r" % states().get(old))

    # 8: a file that arrives after the switch is hashed on the way in, so it
    # is warm immediately -- the other half of R3, and the reason the warm-up
    # is a property of the switch and not of the layer.
    new = write(os.path.join(root, "new.md"), "arrived after the switch\n")
    cli("scan", home)
    write(new, "and then changed once, with a hash already stored\n")
    cli("scan", home)
    check("8  a file added after the switch is changed on its first change",
          states().get(new) == CHANGED, "state=%r" % states().get(new))

    # 9: the whole reason there are two steps.
    same = "and then changed once, with a hash already stored\n"
    write(new, same)
    cli("scan", home)
    check("9  the same bytes back is touched, not changed",
          states().get(new) == TOUCHED, "state=%r" % states().get(new))
    return project_id


# -- 10, 11, 12, 13: the reader --------------------------------------------

class Scripted:
    """No events, ever. The sweep is the only thing under test here."""

    def read(self, timeout):
        return []

    def close(self):
        pass


def gates_reader(work):
    """The sweep acts on what the journal says, within the guards it holds."""
    home = fresh_home(work, "reader")
    watched_root = repo_at(os.path.join(home, "watched"))
    other_root = repo_at(os.path.join(home, "other"))
    write(os.path.join(watched_root, "a.md"), "one\n")
    write(os.path.join(other_root, "b.md"), "two\n")
    watched_id, other_id = add(watched_root), add(other_root)
    cli("watch", watched_id)
    cli("scan", home)
    cli("update", watched_id)
    cli("update", other_id)

    def serve_once():
        lines = []
        service.serve(scan_root=home, sweep_seconds=10 ** 6, debounce=0.0,
                      source=Scripted(), out=lines.append,
                      sleep=lambda _s: None, rounds=1)
        return lines

    # 11 first: nothing has moved since the last update, so a sweep must build
    # nothing. Without it, gate 10 is green for a service that rebuilds every
    # project every round -- which is what CP-13 gate 13 forbids.
    settled = serve_once()
    check("11 CONTROL: a sweep whose journal is quiet updates nothing",
          not [ln for ln in settled if ln.startswith("update ")],
          "%d update line(s)" % len([ln for ln in settled
                                     if ln.startswith("update ")]))

    # 10: the change happens while the service is *not* running, which is
    # exactly what inotify cannot see. CP-13 blind spot 1, closed.
    write(os.path.join(watched_root, "a.md"), "one, edited while down\n")
    write(os.path.join(other_root, "b.md"), "two, edited while down\n")
    lines = serve_once()
    updated = [ln for ln in lines if ln.startswith("update ")]
    check("10 the sweep updates a watched project that changed while down",
          any(watched_id in ln for ln in updated), "%s" % (updated or "none"))
    # 12: the unwatched one is named, not written.
    told = [ln for ln in lines if ln.startswith("changed ")]
    # Not written **and not attempted**. A sweep that tries and is refused
    # produces a `REFUSED` line rather than an `update` one, so checking the
    # update lines alone is green for a service that reaches for every guard
    # on the machine every round -- measured 2026-08-08, when exactly that
    # mutation survived.
    refused = [ln for ln in lines if ln.startswith("REFUSED")]
    check("12 an unwatched project with changes is reported, not written "
          "and not attempted",
          any(other_id in ln and "morphofiles-graph update" in ln
              for ln in told)
          and not any(other_id in ln for ln in updated)
          and not refused,
          "%s" % (refused or told or "none"))
    # 13: and CP-13 gate 3 is untouched by all of it.
    check("13 CONTROL: an unwatched project is still updatable by hand",
          cli("update", other_id).returncode == 0)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp15-") as work:
        gates_union(work)
        gates_warmup(work)
        gates_reader(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp15():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
