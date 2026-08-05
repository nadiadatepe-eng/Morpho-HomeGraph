#!/usr/bin/env python3
"""CP-9 -- semantic search over L2, keyed on content hashes.

The answer key is `tests/gold/FASIT-cp9.md`, written before this file and
before the code it grades (`ee6974d`). Gate numbers below are that document's.

Three of the rules being graded are measurements, not choices: embedding cannot
block the open (M-3), there is no batch size to turn (M-3b), and the chunk
parameters are M-3's own. So the gates that look like taste -- 2, 4, 16 -- are
the ones holding a measured design in place.

The load-bearing gate is **5**: vectors keyed on `content.sha256` are the whole
reason an update is affordable, and a second embedding that embeds nothing is
the only proof of it. Gate 18 is its control -- without it, a command that
always refuses passes 9, 10, 11 and 19.

Nothing is downloaded. The model is on disk (`MHG_MODELS`, default
`~/.local/share/morpho-homegraph/models`) and the library in the repo's own
`node_modules`; a missing one is a refusal, which gates 10 and 11 require.

Run:
    python3 tests/test_cp9.py
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

import numpy as np  # noqa: E402

import morpho_homegraph  # noqa: E402
from morpho_homegraph import embed  # noqa: E402
from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.store import Store, db_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(60)


def cli(*argv, timeout=300, env=None):
    environment = dict(os.environ)
    environment.update(env or {})
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL, env=environment)
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
    """Four short files. Short on purpose: every gate here pays M-3's rate.

    `barrier.md` and `kake.md` are the multilingual pair -- one English
    paraphrase of a Norwegian query, one unrelated Norwegian text. Measured
    2026-08-04: 0.848 against 0.061, which is why gate 13 can be a ranking and
    not a threshold.
    """
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


def project(root):
    added = cli("add", root)
    return added.stdout.split()[0] if added.stdout.strip() else ""


def found(out):
    """Paths from a search command's stdout, in the order printed."""
    paths = []
    for line in out.splitlines():
        for word in line.split():
            if os.sep in word and not word.endswith(":"):
                paths.append(word)
                break
    return paths


def vectors_in(project_id):
    """`[(sha256, ord, blob)]` straight out of the store, read-only."""
    with Store(db_path(project_id), read_only=True) as store:
        try:
            return store.db.execute(
                "SELECT sha256, ord, vector FROM vectors "
                "ORDER BY sha256, ord").fetchall()
        except sqlite3.Error:
            return []


def write_into(project_id, statement, parameters=()):
    """One write against a project store, guard taken like any other writer."""
    guard = StoreLock(str(db_path(project_id))).acquire()
    try:
        with Store(db_path(project_id)) as store:
            with store.writing() as db:
                db.execute(statement, parameters)
                db.commit()
    finally:
        guard.release()


# -- 2, 3, 16 (no model needed) --------------------------------------------

def gates_chunking():
    """M-3's parameters, read from the code that has to keep using them."""
    text = "x" * 2500
    pieces = embed.chunks_of(text)
    step = embed.CHUNK - embed.OVERLAP
    check("2  1000-char chunks with 100 overlap: count and overlap hold",
          len(pieces) == 3 and len(pieces[0]) == embed.CHUNK
          and pieces[0][-embed.OVERLAP:] == pieces[1][:embed.OVERLAP]
          and step == 900,
          "%d chunks, first %d chars, step %d"
          % (len(pieces), len(pieces[0]), step))

    # 3a: a NUL byte is the binary test, and it belongs here as well as in L2
    # -- a row can reach `vectors` from anywhere `content` can be written.
    check("3a a text with a NUL byte yields no chunks at all",
          embed.chunks_of("some text\0with a nul") == [],
          "%d chunks" % len(embed.chunks_of("some text\0with a nul")))

    # 3b: the ceiling, counted rather than trusted -- the last chunk has to
    # end exactly at the cap, or the ceiling is decoration. Counted in
    # *characters*: L2 has already refused anything over 1 MiB on disk, so
    # this is the backstop for a row that arrived some other way, and the two
    # units differ for text that is not ASCII.
    big = embed.chunks_of("a" * (embed.MAX_BYTES + 5000))
    covered = (len(big) - 1) * step + len(big[-1]) if big else 0
    check("3b a text over the ceiling is cut at it, not embedded whole",
          covered == embed.MAX_BYTES,
          "%d chunks covering %d chars, ceiling %d"
          % (len(big), covered, embed.MAX_BYTES))


def gates_no_batching():
    """16 -- read as code, not as prose. The docstring *explains* batching."""
    source = open(os.path.join(os.path.dirname(os.path.abspath(
        morpho_homegraph.__file__)), "embed.py"), encoding="utf-8").read()
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
    knobs = sorted(n for n in names
                   if re.search(r"batch|group", n, re.IGNORECASE))
    check("16 there is no batch size or group size anywhere in the module",
          not knobs, "identifiers: %s" % (knobs or "none"))


# -- 1, 4, 5, 8, 12, 13, 14, 18 --------------------------------------------

def gates_embedding(project_id):
    first = cli("embed", project_id)
    check("18 an ordinary embedding exits 0",
          first.returncode == 0,
          "exit %s: %s" % (first.returncode,
                           (first.stderr.strip() or first.stdout.strip())[:70]))
    if first.returncode != 0:
        return

    rows = vectors_in(project_id)
    widths = {len(blob) for _sha, _ord, blob in rows}
    norms = [float(np.linalg.norm(np.frombuffer(blob, dtype=np.float32)))
             for _sha, _ord, blob in rows]
    # 1: 384 float32 is 1536 bytes, and a normalised vector read back has norm
    # 1. A blob that lost its dtype, its length or its bytes fails one of the
    # two -- checking the length alone would pass for 1536 bytes of anything.
    check("1  a vector is 384 float32 and comes back unchanged",
          bool(rows) and widths == {embed.DIM * 4}
          and all(abs(n - 1.0) < 1e-3 for n in norms),
          "%d vectors, widths %s, norms %s"
          % (len(rows), sorted(widths),
             ["%.3f" % n for n in norms[:3]]))

    with Store(db_path(project_id), read_only=True) as store:
        meta = {k: store.get_meta(k) for k in
                ("embed_model", "embed_dim", "embed_chunks",
                 "embed_processes")}
    check("8  model id and dimension are written to meta",
          meta["embed_model"] == embed.MODEL_ID
          and meta["embed_dim"] == str(embed.DIM),
          "%s / %s" % (meta["embed_model"], meta["embed_dim"]))

    # 4: R1 counted rather than assumed. Model load is 0.96 s per process
    # (M-3); one process per chunk would put that in front of every chunk.
    check("4  one Node process embeds many chunks",
          meta["embed_processes"] == "1" and int(meta["embed_chunks"] or 0) > 1,
          "%s process(es) for %s chunks"
          % (meta["embed_processes"], meta["embed_chunks"]))

    # 5: the rule that pays for the design, and the `update` in the middle is
    # the whole gate. L2 is replaced *whole* by every update, so a cache keyed
    # on anything the rewrite touches would have to embed everything again
    # here. Without the update this gate would pass for a path-keyed cache
    # too, and R2 would be untested.
    cli("update", project_id)
    second = cli("embed", project_id)
    check("5  a second embedding after a full L2 rewrite embeds zero chunks",
          second.returncode == 0 and "0 chunks embedded" in second.stdout,
          "exit %s: %r" % (second.returncode, second.stdout.strip()[:70]))

    # 5c: the one way a stale vector cannot be seen in the keys. Move the
    # boundaries and `(sha, 3)` still exists with different text under it, so
    # the parameters are compared directly and everything is embedded again.
    write_into(project_id,
               "INSERT INTO meta (key, value) VALUES "
               "('embed_chunking', '500/50') ON CONFLICT(key) "
               "DO UPDATE SET value = '500/50'")
    recut = cli("embed", project_id)
    check("5c changed chunk parameters re-embed the whole store",
          recut.returncode == 0
          and "%d chunks embedded" % len(rows) in recut.stdout,
          "exit %s: %r" % (recut.returncode, recut.stdout.strip()[:70]))

    check("1b re-embedding leaves the stored vectors byte-identical",
          vectors_in(project_id) == rows,
          "%d vectors before, %d after" % (len(rows), len(vectors_in(project_id))))


def gates_searching(project_id):
    hit = cli("search", "--semantic", "--project", project_id,
              "the write barrier refuses a second writer")
    again = cli("search", "--semantic", "--project", project_id,
                "the write barrier refuses a second writer")
    order = found(hit.stdout)
    check("12 cosine ranks the closest text first, and the order is stable",
          hit.returncode == 0 and order and order[0].endswith("barrier.md")
          and order == found(again.stdout),
          "exit %s: %s" % (hit.returncode, [os.path.basename(p) for p in order]))

    # 13: the whole reason locked decision 9 chose a multilingual model. A
    # ranking, not a threshold -- a number would have been chosen to pass.
    crossing = cli("search", "--semantic", "--project", project_id,
                   "skrivebarrieren nekter en andre skriver")
    ranked = [os.path.basename(p) for p in found(crossing.stdout)]
    check("13 a Norwegian query ranks the English paraphrase above noise",
          crossing.returncode == 0 and "barrier.md" in ranked
          and ("kake.md" not in ranked
               or ranked.index("barrier.md") < ranked.index("kake.md")),
          "exit %s: %s" % (crossing.returncode, ranked))

    check("18b an ordinary semantic search exits 0",
          hit.returncode == 0, "exit %s" % hit.returncode)


# -- 6, 7, 14, 15, 17 ------------------------------------------------------

def gates_expiry(work, home, project_id):
    """R2's duty: a vector that outlives its text is a bug."""
    before = {sha for sha, _ord, _blob in vectors_in(project_id)}

    # 17: a hash that `content` no longer holds. Planted directly, because
    # the mechanism has to work for any hash that falls out -- not only for
    # the ones a file edit produces.
    write_into(project_id,
               "INSERT INTO vectors (sha256, ord, vector) VALUES (?, ?, ?)",
               ("deadbeef", 0, b"\0" * (embed.DIM * 4)))
    cli("embed", project_id)
    check("17 a vector for a hash no longer in content is gone after embedding",
          "deadbeef" not in {sha for sha, _o, _b in vectors_in(project_id)},
          "%d vectors" % len(vectors_in(project_id)))

    # 6: the same rule reached through the real path -- a file is edited, its
    # hash changes, and the vectors for the old text have to go.
    write(os.path.join(home, "notes.md"),
          "Completely different words now, with nothing of the old note left "
          "in this file at all.\n")
    cli("scan", home)
    cli("update", project_id)
    cli("embed", project_id)
    after_edit = {sha for sha, _o, _b in vectors_in(project_id)}
    check("6  editing a file removes the vectors for its old text",
          bool(before - after_edit) and bool(after_edit - before),
          "%d hashes gone, %d new" % (len(before - after_edit),
                                      len(after_edit - before)))

    # 7: deletion, which is not the same event as an edit (CP-6 decides which
    # of the two happened; here both must clear the vectors).
    os.remove(os.path.join(home, "kake.md"))
    cli("scan", home)
    cli("update", project_id)
    cli("embed", project_id)
    after_delete = {sha for sha, _o, _b in vectors_in(project_id)}
    check("7  deleting a file removes its vectors",
          len(after_delete) == len(after_edit) - 1,
          "%d hashes before, %d after" % (len(after_edit), len(after_delete)))

    # 15: `update` does not embed (R3, which is M-3's conclusion). Measured
    # from an emptied table: comparing counts before and after would pass for
    # an update that re-embeds, because the hash reuse makes that a no-op.
    write_into(project_id, "DELETE FROM vectors")
    updated = cli("update", project_id)
    check("15 update fills L2, L3 and L4 and does not embed",
          updated.returncode == 0 and not vectors_in(project_id),
          "exit %s, %d vectors after update"
          % (updated.returncode, len(vectors_in(project_id))))

    # 14: R9, and the point where CP-9 deliberately differs from CP-8. A
    # partly embedded project is the *normal* state after R3, so it answers --
    # and says how far it has come, every time.
    cli("embed", project_id)
    write(os.path.join(home, "late.md"),
          "A file that arrived after the embedding run, holding a sentence "
          "about carrots and nothing else.\n")
    cli("scan", home)
    cli("update", project_id)
    partial = cli("search", "--semantic", "--project", project_id,
                  "the write barrier refuses a second writer")
    numbers = re.search(r"(\d+) of (\d+) chunks embedded", partial.stdout)
    check("14 a partly embedded project answers and says N of M",
          partial.returncode == 0 and found(partial.stdout) and numbers
          and int(numbers.group(1)) < int(numbers.group(2)),
          "exit %s: %r" % (partial.returncode,
                           (numbers.group(0) if numbers
                            else partial.stdout.strip()[:60])))


# -- 9, 10, 11, 19 ---------------------------------------------------------

def gates_refusals(work):
    """Every way this layer can be unable to answer, and what it does then."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "refuse", "store")
    home = corpus(os.path.join(work, "refuse", "home"))
    cli("scan", home)
    project_id = project(home)
    cli("update", project_id)

    # 19: house rule 6, not the answer key -- a layer that was never built is
    # missing, not "partly done", and zero hits from it is not an answer.
    # Without this, gate 14's "N of M" line would satisfy the honesty rule for
    # a project holding no vectors at all.
    empty = cli("search", "--semantic", "--project", project_id, "anything")
    check("19 a project with no vectors refuses instead of finding nothing",
          empty.returncode == 1
          and "embed" in (empty.stdout + empty.stderr).lower(),
          "exit %s: %r" % (empty.returncode,
                           (empty.stdout + empty.stderr).strip()[:60]))

    # 10: no model. The path is named because the fix is to put it there, and
    # nothing at all is written -- a half-embedded store from a failed run is
    # worse than an empty one.
    missing = cli("embed", project_id,
                  env={"MHG_MODELS": os.path.join(work, "no-such-models")})
    check("10 a missing model refuses, names the path, and writes no vectors",
          missing.returncode == 2
          and "no-such-models" in (missing.stdout + missing.stderr)
          and not vectors_in(project_id),
          "exit %s: %r" % (missing.returncode,
                           (missing.stderr.strip() or "-")[:70]))

    # 11: no node. Same shape -- a refusal, not a run without vectors.
    no_node = cli("embed", project_id,
                  env={"MHG_NODE": os.path.join(work, "no-such-node")})
    check("11 a missing node refuses the same way",
          no_node.returncode == 2
          and "no-such-node" in (no_node.stdout + no_node.stderr)
          and not vectors_in(project_id),
          "exit %s: %r" % (no_node.returncode,
                           (no_node.stderr.strip() or "-")[:70]))

    # 9: vectors from two models cannot be compared, so a store that says it
    # holds another dimension refuses rather than mixing. Silence here is the
    # failure that looks like a poor corpus instead of a broken setup.
    cli("embed", project_id)
    write_into(project_id,
               "INSERT INTO meta (key, value) VALUES ('embed_dim', '999') "
               "ON CONFLICT(key) DO UPDATE SET value = '999'")
    mixed = cli("embed", project_id)
    searched = cli("search", "--semantic", "--project", project_id, "barrier")
    check("9  a store recording another dimension refuses instead of mixing",
          mixed.returncode == 2 and searched.returncode != 0
          and "999" in (mixed.stdout + mixed.stderr),
          "embed %s, search %s: %r"
          % (mixed.returncode, searched.returncode,
             (mixed.stderr.strip() or "-")[:60]))

    # 9b: the other half of R6, and it needs its own store state -- with the
    # dimension left at 999 the check above would answer for this one, and a
    # model check that had stopped working would look tested.
    write_into(project_id,
               "INSERT INTO meta (key, value) VALUES ('embed_dim', '384') "
               "ON CONFLICT(key) DO UPDATE SET value = '384'")
    write_into(project_id,
               "INSERT INTO meta (key, value) VALUES "
               "('embed_model', 'Xenova/some-other-model') "
               "ON CONFLICT(key) DO UPDATE SET value = "
               "'Xenova/some-other-model'")
    other = cli("embed", project_id)
    check("9b a store recording another model refuses the same way",
          other.returncode == 2
          and "some-other-model" in (other.stdout + other.stderr),
          "exit %s: %r" % (other.returncode,
                           (other.stderr.strip() or "-")[:60]))


def gates_wiring():
    """The production caller, and the one that must not exist (R3)."""
    package = os.path.dirname(os.path.abspath(morpho_homegraph.__file__))
    callers = set()
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py") or name == "embed.py":
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        # The *enclosing function* is recorded, not just the file: "and it is
        # not update" is half of what this gate claims, and a check that only
        # counts callers stays green when the call lands in `cmd_update`.
        for holder in ast.walk(tree):
            if not isinstance(holder, ast.FunctionDef):
                continue
            for node in ast.walk(holder):
                # Call nodes, never grep: a docstring naming `embed.build`
                # counts as a caller to a text search and to nothing else
                # (measured 2026-08-04; it cost CP-7B five empty layers).
                if isinstance(node, ast.Call) \
                        and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "build" \
                        and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id == "embed":
                    callers.add("%s:%s" % (name, holder.name))
    check("15b embed.build has a caller in the package, and it is not update",
          bool(callers) and not any(c.endswith(":cmd_update") for c in callers),
          "called from %s" % (", ".join(sorted(callers)) or "nowhere"))


def gates_hang(work):
    """20 -- a worker that stops answering is refused, not waited on.

    Not in the answer key: it comes from the review of this checkpoint's own
    code. An unbounded read is the one place this design can lock a user out
    of their own store -- the guard is held for the whole command, so a hung
    Node process holds it until someone finds the pid.

    Run on a thread with a deadline, because a gate for a hang that hangs is
    the failure it is testing for.
    """
    fake = os.path.join(work, "sleeping-node")
    write(fake, "#!/bin/sh\nsleep 30\n")
    os.chmod(fake, 0o755)
    os.environ["MHG_NODE"] = fake
    embed.READ_TIMEOUT = 1.0
    outcome = {}

    def probe():
        try:
            embed.Embedder().close()
            outcome["exc"] = None
        except BaseException as exc:  # noqa: BLE001 -- the gate reports it
            outcome["exc"] = exc

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(15)
    check("20 a worker that never answers is refused, not waited on forever",
          not worker.is_alive() and isinstance(outcome.get("exc"), embed.Refused),
          "still running" if worker.is_alive() else "raised %r"
          % (outcome.get("exc"),))
    embed.READ_TIMEOUT = 300.0
    os.environ.pop("MHG_NODE", None)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp9-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        home = corpus(os.path.join(work, "home"))
        cli("scan", home)
        project_id = project(home)
        built = cli("update", project_id)
        if built.returncode != 0:
            check("0  the project builds before anything is embedded", False,
                  "update exited %s: %s"
                  % (built.returncode, built.stderr.strip()[:60]))
        else:
            gates_embedding(project_id)
            gates_searching(project_id)
            gates_expiry(work, home, project_id)
        gates_chunking()
        gates_no_batching()
        gates_refusals(work)
        gates_wiring()
        gates_hang(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp9():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
