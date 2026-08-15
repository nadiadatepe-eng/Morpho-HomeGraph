#!/usr/bin/env python3
"""L4, second half: semantic search over L2, keyed on content hashes.

The answer key is `tests/gold/FASIT-cp9.md`, written before this module. CP-8
finds words that are there; this finds text that means the same without
sharing a word, which is why locked decision 9 chose a *multilingual* model --
measured 2026-08-04, "the write barrier refuses a second writer" against
"skrivebarrieren nekter en andre skriver" is 0.848, an unrelated sentence 0.061.

**Three of the rules here are measurements, not preferences.**

  * **Embedding is its own command, never a step in `update` (R3).** M-3 set a
    60-second threshold before measuring and got 219.9 s and 317.1 s on two of
    the *smallest* trees in the home area. So the design changed there and
    then: `update` fills L2, L3 and L4 and is done, `embed` runs after, and the
    project is usable while it does -- CP-8 answers lexically in the meantime.
  * **One Node process, many chunks (R1).** Model load is 0.96 s fixed per
    process. One process per chunk would put that in front of every chunk.
  * **There is no batching and no group size (R5).** M-3b measured groups of
    32 at -40 % speed and 5x memory, on both trees at once. The rule is
    written down because a number that lives only in a table gets "optimised"
    back in by the next reader.

**Vectors are keyed on `content.sha256`, never on a path (R2).** L2 is
replaced whole by every `update`, so path-keyed vectors would cost the whole
embedding again each time. The duty that comes with the key: a vector whose
hash has left `content` is deleted -- a vector that outlives its text ranks
confidently for content that is gone.

**A missing model or a missing `node` is a refusal, never a zero vector
(R7).** Same rule as CP-8: a layer that answers worse without saying so is
worse than one that does not answer.

**What these gates do not measure is whether the search finds anything.** That
is CP-9E, and it is named here rather than covered by green gates -- the
predecessor shipped every gate green and could not say whether the layer
worked.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .content import HEAD_BYTES, MAX_BYTES
from .store import PROJECT

# Locked decision 9. The id is the directory under the model home, exactly as
# transformers.js resolves it.
# The model is third-party work and is named as such rather than treated as a
# setting: `paraphrase-multilingual-MiniLM-L12-v2` is a sentence-transformers
# model (Reimers and Gurevych, UKP Lab), distributed for transformers.js in the
# `Xenova/` conversion. Nothing of it is vendored here -- it is fetched to a
# local cache and the identifier is the credit.
MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384

# M-3's own chunk parameters. Changing either makes 21.0 chunks/s a number
# about a different measurement, so they are constants and not flags: a new
# split is a decision that owes a new measurement beside it.
CHUNK = 1000
OVERLAP = 100

# The chunk parameters as the store records them. **A key of `(sha256, ord)`
# alone is not enough:** move the boundaries and chunk 3 of an unchanged text
# is different text under the same key, so the old vector would be reused for
# words it never saw. The recorded value is what makes that visible -- when it
# differs, every vector in the store was cut another way and is re-embedded.
CHUNKING = "%d/%d" % (CHUNK, OVERLAP)

# How long the worker may be silent before it is treated as gone. Generous:
# a single inference is milliseconds and the model load is about a second, so
# five minutes is only ever reached by a process that has stopped answering.
# Without it, a hung worker holds the project's write guard forever, and the
# only way out is finding the pid -- an unbounded read is the one place this
# design can deadlock a user out of their own store.
READ_TIMEOUT = 300.0

# How often the vectors so far are committed. **This is not the group size R5
# forbids** -- the chunks still go to the model one at a time, which is what
# M-3b measured; this is only how many finished vectors wait in memory before
# they are written down. It exists because an embedding run is minutes long
# (M-3: 219.9 s on one of the smallest trees), and an interrupted run that
# saved nothing has to start over from the beginning. With the hash key, one
# that saved half starts from half.
FLUSH_EVERY = 200


class Refused(RuntimeError):
    """This layer cannot answer, and says so instead of answering badly."""


def models_home() -> Path:
    """Where the model lives. `MHG_MODELS` overrides.

    Deliberately *not* derived from `store.data_home()`, though the default
    sits beside it: the model is 130 MB of weights shared by every store, and
    a test or a second install pointing `MORPHO_HOMEGRAPH_HOME` elsewhere must
    not turn "my index moved" into "the model is missing".
    """
    override = os.environ.get("MHG_MODELS")
    root = Path(override) if override else Path(
        "~/.local/share/morpho-homegraph/models")
    return root.expanduser()


def node_modules() -> Path:
    """The repo's own `node_modules`. `MHG_NODE_MODULES` overrides.

    Resolved from this file rather than from the working directory: depending
    on another tool's installation is what made M-3's borrowed path work on
    exactly one machine.
    """
    override = os.environ.get("MHG_NODE_MODULES")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "node_modules"


def node_binary() -> str:
    """`node`, or whatever `MHG_NODE` names."""
    return os.environ.get("MHG_NODE") or "node"


def chunks_of(text: str) -> list[str]:
    """M-3's split: 1000 characters, 100 of overlap, 1 MiB ceiling.

    Byte-for-byte the same walk as `tools/m3_first_open.mjs`, because that is
    what makes M-3's rate transferable to this module rather than a number
    about a different program.

    The NUL test is here as well as in L2 (CP-4): `content` is the usual
    source, but any writer that reaches the table would otherwise get binary
    junk embedded as if it were prose.
    """
    if "\0" in text[:HEAD_BYTES]:
        return []
    # Cut in characters against a byte cap, which is the looser of the two:
    # L2 has already refused anything over 1 MiB on disk (CP-4), so this is
    # the backstop for a row that arrived some other way, not the usual path.
    text = text[:MAX_BYTES]
    out: list[str] = []
    step = CHUNK - OVERLAP
    for start in range(0, len(text), step):
        piece = text[start:start + CHUNK].strip()
        if piece:
            out.append(piece)
        if start + CHUNK >= len(text):
            break
    return out


def check_prerequisites() -> None:
    """Model, library and `node`, all three named by their path if missing.

    Cheap on purpose -- three stats, no model load -- because it runs before
    every embedding whether or not there is a chunk to embed. A refusal that
    only fires when the corpus happens to have changed is one nobody can rely
    on, and R7 is that no absence may pass silently.
    """
    model_dir = models_home() / MODEL_ID
    if not (model_dir / "config.json").is_file():
        raise Refused(
            "no model at %s -- copy %s there, or set MHG_MODELS. Nothing is "
            "downloaded on your behalf" % (model_dir, MODEL_ID))
    modules = node_modules()
    if not (modules / "@xenova" / "transformers").is_dir():
        raise Refused(
            "no @xenova/transformers under %s -- run `npm install` in the "
            "repository, or set MHG_NODE_MODULES" % modules)
    if shutil.which(node_binary()) is None:
        raise Refused(
            "cannot run %s -- install Node, or set MHG_NODE to the binary"
            % node_binary())


class Embedder:
    """One long-lived Node process. Chunks in, vectors out, JSON lines.

    Refuses at construction rather than at first use: the model and the
    library are checked before anything is written, so a store never ends up
    half embedded because the run died on chunk two.
    """

    def __init__(self) -> None:
        check_prerequisites()
        worker = Path(__file__).with_name("embed_worker.mjs")
        environment = dict(os.environ)
        environment.update(MHG_NODE_MODULES=str(node_modules()),
                           MHG_MODELS=str(models_home()),
                           MHG_MODEL=MODEL_ID)
        # **stderr goes to a file, not to a pipe.** Nobody reads the worker's
        # stderr until something goes wrong, and an unread pipe holds 64 kB
        # before it blocks the writer -- so a chatty warning would deadlock the
        # embedding somewhere in the middle of a long run, with both sides
        # waiting for the other. A file never blocks, and it is still there to
        # quote when the worker dies.
        self.log = tempfile.TemporaryFile(mode="w+")
        # No `except OSError` here, on purpose: `check_prerequisites` above is
        # the *one* place a missing or unrunnable `node` is refused, and it
        # tests X_OK as well as existence. A second refusal with its own
        # message would mean neither of them could be shown to fail -- CP-0
        # measured exactly that, two copies of one refusal and two mutations
        # surviving because each was caught by the other's copy.
        # Bytes, not text: the deadline below has to be enforced on the file
        # descriptor, and a `TextIOWrapper` in front of it holds a buffer that
        # `select` cannot see into -- it would report "nothing to read" for a
        # line already sitting in the wrapper.
        self.proc = subprocess.Popen(
            [node_binary(), str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, env=environment)
        self._buffer = b""
        ready = self._read("the worker died before it was ready")
        if ready.get("dim") != DIM:
            self.close()
            raise Refused(
                "the model reports %s dimensions, this package stores %d -- "
                "one of the two is not %s" % (ready.get("dim"), DIM, MODEL_ID))

    def _line(self) -> str:
        """One line from the worker, or `""` when it stops producing them.

        Byte by byte with a deadline on every read, rather than one `select`
        in front of a `readline`. A vector is about 9 kB as JSON and `PIPE_BUF`
        is 4096, so the write is *not* atomic: one ready descriptor can mean
        the first half of a line, and the readline after it would block for
        the rest with no deadline left anywhere.
        """
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buffer:
            if not select.select([fd], [], [], READ_TIMEOUT)[0]:
                return ""
            chunk = os.read(fd, 65536)
            if not chunk:
                return ""
            self._buffer += chunk
        line, _sep, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8")

    def _read(self, what_died: str) -> dict:
        line = self._line()
        if not line:
            alive = self.proc.poll() is None
            self.log.seek(0)
            stderr = self.log.read()
            self.close()
            raise Refused("%s: %s" % (
                what_died,
                "silent for %.0f s and still running -- it was stopped, "
                "re-run the command" % READ_TIMEOUT if alive
                else stderr.strip()[-300:] or "it exited without a word"))
        answer = json.loads(line)
        if "error" in answer:
            self.close()
            raise Refused(answer["error"])
        return answer

    def encode(self, text: str) -> np.ndarray:
        """One chunk to one unit vector. `float32`, which is what is stored."""
        self.proc.stdin.write((json.dumps(text) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        vector = np.asarray(self._read("the worker died mid-chunk")["vector"],
                            dtype=np.float32)
        if vector.shape != (DIM,):
            raise Refused("the model returned %s values, expected %d"
                          % (vector.shape, DIM))
        return vector

    def close(self) -> None:
        # stdin first: the worker's loop ends when its input does, so this is
        # what makes the process leave on its own rather than be killed.
        for stream in (self.proc.stdin, self.proc.stdout, self.log):
            if stream and not stream.closed:
                stream.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                # Waited for, not only killed: without this the child stays a
                # zombie for the life of the process that gave up on it.
                self.proc.wait()

    def __enter__(self) -> "Embedder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _check_role_and_model(store) -> None:
    """Refuse a store that records another model or another dimension (R6).

    Vectors from two models cannot be compared. Returning fewer hits instead
    of refusing makes a broken setup look like a thin corpus, and those two
    get fixed in entirely different places.
    """
    if store.role != PROJECT:
        raise Refused("vectors belong to a project store, not a %r one"
                      % store.role)
    model, dim = store.get_meta("embed_model"), store.get_meta("embed_dim")
    if model and model != MODEL_ID:
        raise Refused(
            "this store was embedded with %s, not %s -- vectors from two "
            "models cannot be compared. Re-embed it or keep them apart"
            % (model, MODEL_ID))
    if dim and dim != str(DIM):
        raise Refused(
            "this store records %s dimensions, the model gives %d -- vectors "
            "from two models cannot be compared. Re-embed it" % (dim, DIM))


def _wanted(store) -> dict[tuple[str, int], str]:
    """`{(sha256, ord): chunk}` for everything L2 currently holds.

    Distinct on the hash, not on the path: two files with identical bytes are
    one node (locked decision 1 and CP-5), and embedding both would pay twice
    for one text.
    """
    wanted: dict[tuple[str, int], str] = {}
    for sha, text in store.db.execute(
            "SELECT DISTINCT sha256, text FROM content "
            "WHERE text IS NOT NULL AND sha256 IS NOT NULL"):
        for ord_, chunk in enumerate(chunks_of(text)):
            wanted[(sha, ord_)] = chunk
    return wanted


def build(store) -> dict[str, int]:
    """Embed what is not embedded yet, drop what no longer has a text.

    The caller holds the guard. **Not called from `update`** -- R3, which is
    M-3's conclusion rather than a preference.

    Returns `{"embedded", "reused", "removed", "chunks"}`. `embedded` is zero
    on a second run over unchanged content, and that number is the whole
    argument for keying on the hash.
    """
    _check_role_and_model(store)
    check_prerequisites()
    started = time.perf_counter()
    wanted = _wanted(store)
    have = {(sha, ord_) for sha, ord_ in
            store.db.execute("SELECT sha256, ord FROM vectors")}

    # One rule for three of the four ways a vector goes stale: the file was
    # edited, the file was deleted, or the row was written by hand. All three
    # are "this key is no longer wanted".
    stale = sorted(have - wanted.keys())
    # The fourth way needs its own answer, because it does *not* show up as an
    # unwanted key: move the chunk boundaries and `(sha, 3)` still exists, with
    # different text under it. Nothing in the keys can see that, so the
    # parameters are compared directly and the whole store is re-embedded.
    if store.get_meta("embed_chunking") not in (None, CHUNKING):
        stale = sorted(have)
        have = set()
    todo = sorted(wanted.keys() - have)

    # Written before the first vector, not after the last: from the moment a
    # vector exists, the store has to be able to say which model cut which
    # chunks. A run that is interrupted otherwise leaves vectors nobody can
    # identify.
    store.set_meta("embed_model", MODEL_ID)
    store.set_meta("embed_dim", str(DIM))
    store.set_meta("embed_chunking", CHUNKING)
    with store.writing() as db:
        db.executemany("DELETE FROM vectors WHERE sha256 = ? AND ord = ?",
                       stale)
        db.commit()

    # The worker starts only when there is something to embed -- the 0.96 s
    # model load is not paid to embed nothing. The prerequisites were checked
    # above, so a missing model still refuses on a run with no new chunks.
    embedded, processes = 0, 0
    if todo:
        processes = 1
        with Embedder() as embedder:
            for start in range(0, len(todo), FLUSH_EVERY):
                fresh = [(sha, ord_,
                          embedder.encode(wanted[(sha, ord_)]).tobytes())
                         for sha, ord_ in todo[start:start + FLUSH_EVERY]]
                with store.writing() as db:
                    db.executemany("INSERT INTO vectors (sha256, ord, vector)"
                                   " VALUES (?, ?, ?)", fresh)
                    db.commit()
                embedded += len(fresh)

    # The vector layer's own clock (CP-12 R10). Without it the layer that is
    # most expensive to build is the one nobody can date.
    store.set_meta("embed_at", "%.3f" % time.time())
    store.set_meta("embed_chunks", str(len(wanted)))
    store.set_meta("embed_processes", str(processes))
    store.set_meta("embed_seconds", "%.3f" % (time.perf_counter() - started))
    return {"embedded": embedded, "reused": len(wanted) - embedded,
            "removed": len(stale), "chunks": len(wanted)}


def coverage(store) -> tuple[int, int]:
    """`(embedded, expected)` chunks. Printed on every search (R9).

    A partly embedded project is the *normal* state after R3, so unlike CP-8's
    lexical index this is not an error -- but nobody may read three hits as
    "everything we have" without being told how far the run got.
    """
    wanted = _wanted(store)
    embedded = sum(1 for key in
                   store.db.execute("SELECT sha256, ord FROM vectors")
                   if (key[0], key[1]) in wanted)
    return embedded, len(wanted)


def search(store, query: str, limit: int = 10) -> list[dict]:
    """Cosine over every stored vector. Best first, ties broken by path.

    A full scan, O(n) per query (R8). **The ceiling is named:** at 5 000
    chunks this is milliseconds, and an ANN index is the upgrade if the number
    ever hurts -- it is not built before then.
    """
    _check_role_and_model(store)
    rows = store.db.execute(
        "SELECT sha256, ord, vector FROM vectors").fetchall()
    if not rows:
        return []
    blobs = b"".join(bytes(blob) for _s, _o, blob in rows)
    # A blob of the wrong width is a store written by something else, and
    # reshaping it would either raise a numpy error nobody can act on or --
    # worse, if the total happens to divide -- silently mix two vectors into
    # one. Refused with the numbers, like every other mismatch here.
    if len(blobs) != len(rows) * DIM * 4:
        raise Refused(
            "%d vectors hold %d bytes, not %d -- these were not written by "
            "this package. Re-embed the project"
            % (len(rows), len(blobs), len(rows) * DIM * 4))
    matrix = np.frombuffer(blobs, dtype=np.float32).reshape(len(rows), DIM)
    with Embedder() as embedder:
        wanted = embedder.encode(query)
    # Normalised at both ends rather than trusting `normalize: true`: a vector
    # read back from a store someone else wrote is an assumption, and dividing
    # by the norms is what makes this cosine instead of a dot product.
    norms = np.linalg.norm(matrix, axis=1) * float(np.linalg.norm(wanted))
    scores = np.divide(matrix @ wanted, norms,
                       out=np.zeros(len(rows), dtype=np.float32),
                       where=norms > 0)

    paths: dict[str, list[str]] = {}
    for path, sha in store.db.execute(
            "SELECT path, sha256 FROM content WHERE sha256 IS NOT NULL"):
        paths.setdefault(sha, []).append(path)

    best: dict[str, tuple[float, int]] = {}
    for (sha, ord_, _blob), score in zip(rows, scores):
        if sha not in best or score > best[sha][0]:
            best[sha] = (float(score), ord_)
    hits = [{"path": path, "score": score, "ord": ord_}
            for sha, (score, ord_) in best.items()
            for path in sorted(paths.get(sha, []))]
    # The path breaks ties so two identical runs print identical output. An
    # order that drifts is one no test can hold.
    hits.sort(key=lambda hit: (-hit["score"], hit["path"]))
    return hits[:limit]
