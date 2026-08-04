#!/usr/bin/env python3
"""CP-4 -- L2, the content of what the scope selected.

The answer key is `tests/gold/FASIT-cp4.md`, written before this file and
before the code it grades. Gate numbers below are that document's.

The load-bearing claim is that **not read** and **does not exist** are
different answers, so most of these gates are about the rows that carry a
reason rather than the ones that carry text. Gate 2 checks the XOR instead of
trusting the writer, and gate 16 is the negative control for gate 15 -- "unread
> 0 on a real tree" is equally satisfied by a counter that is always positive.

Run:
    python3 tests/test_cp4.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph import journal  # noqa: E402
from morpho_homegraph.content import (  # noqa: E402
    BINARY, ENCRYPTED, MAX_BYTES, TOO_LARGE, UNDECODABLE, UNREADABLE,
    WrongStore, build, classify)
from morpho_homegraph.lock import StoreLock, Unguarded  # noqa: E402
from morpho_homegraph.scan import scan  # noqa: E402
from morpho_homegraph.scope import Scope  # noqa: E402
from morpho_homegraph.store import (  # noqa: E402
    L0, PROJECT, Store, l0_path, new_project)

results, check = reporter(56)


def build_tree(root):
    """One file per outcome the fasit names, plus the ones that must be read.

    The names say what each is for: a fixture nobody can read is a fixture
    nobody maintains.
    """
    inside = os.path.join(root, "inside")
    outside = os.path.join(root, "outside")
    os.makedirs(os.path.join(inside, "sub"))
    os.makedirs(outside)

    def write(where, name, data):
        path = os.path.join(where, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    made = {
        # Read successfully. `code.py` is here because locked decision 1 says
        # code is text -- a run that skipped it would still pass every other
        # gate.
        "text": write(inside, "notes.md", "# hei\nnorsk ærlig æøå\n".encode()),
        "code": write(inside, "code.py", b"def f():\n    return 1\n"),
        "nested": write(os.path.join(inside, "sub"), "deep.txt", b"deep\n"),
        # One per reason.
        "binary": write(inside, "image.bin", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"),
        "encrypted": write(inside, "secret.asc",
                           b"-----BEGIN PGP MESSAGE-----\nhQIMA\n"),
        "undecodable": write(inside, "eula.txt",
                             b"NVIDIA \x96 PLEASE READ\n"),
        "too_large": write(inside, "huge.log", b"x" * (MAX_BYTES + 1)),
        # Outside the scope: must produce no row at all, neither read nor
        # unread. Without gate 9 the numbers in gate 15 are unreadable --
        # measured 2026-08-04, `.git/objects` alone is 12 of 12 non-UTF-8
        # files in a code tree.
        "outside": write(outside, "elsewhere.txt", b"not mine\n"),
    }
    # A symlink and a directory inside the scope: candidates for a row only if
    # rule R8 is broken.
    os.symlink(made["text"], os.path.join(inside, "link.md"))
    return inside, outside, made


def l2_rows(store):
    return {row[0]: row for row in store.db.execute(
        "SELECT path, size, mtime_ns, sha256, text, reason FROM content")}


def gates_classify(made):
    """3, 5, 6, 7 -- one file, one reason, no store in the way."""
    big = os.path.getsize(made["too_large"])
    opened: list[str] = []

    def spy(event, args):
        if event == "open":
            opened.append(str(args[0]))

    sys.addaudithook(spy)
    reason, text, sha = classify(made["too_large"], big)
    check("3  a file over the cap is refused without being opened",
          reason == TOO_LARGE and text is None
          and not any(os.path.abspath(p) == made["too_large"] for p in opened),
          "%s, %d opens of it" % (reason, sum(
              1 for p in opened if os.path.abspath(p) == made["too_large"])))

    for key, want, num in (("binary", BINARY, "5  a NUL in the head is binary"),
                           ("encrypted", ENCRYPTED,
                            "6  PGP armor is encrypted, not binary"),
                           ("undecodable", UNDECODABLE,
                            "7  cp1252 text is undecodable, not replaced")):
        path = made[key]
        reason, text, _ = classify(path, os.path.getsize(path))
        check(num, reason == want and text is None,
              "%s -> %s" % (os.path.basename(path), reason))

    # R7 has a second half: the text must not come back mangled. A
    # replacement decode would return a string containing U+FFFD and count as
    # read, which is the silent version of this failure.
    _r, text, _s = classify(made["undecodable"],
                            os.path.getsize(made["undecodable"]))
    check("7b no replacement characters are ever stored",
          text is None, "text is %r" % (text,))

    # 4: unreadable. Skipped rather than faked when running as root, because
    # root can read a 0o000 file and the gate would be green for the wrong
    # reason.
    blocked = made["nested"] + ".blocked"
    with open(blocked, "wb") as fh:
        fh.write(b"secret\n")
    os.chmod(blocked, 0o000)
    try:
        reason, text, _ = classify(blocked, os.path.getsize(blocked))
        if os.geteuid() == 0:
            check("4  a file without read permission is unreadable", False,
                  "SKIPPED -- running as root, the gate cannot fail here")
        else:
            check("4  a file without read permission is unreadable",
                  reason == UNREADABLE and text is None, str(reason))
    finally:
        os.chmod(blocked, 0o644)
        os.unlink(blocked)


def gates_build(inside, outside, made):
    project_id, db = new_project()
    l0_db = l0_path()
    l0_db.parent.mkdir(parents=True, exist_ok=True)
    scope = Scope().add(inside)

    l0_guard = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, os.path.dirname(inside), deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    tally = build(store, l0, scope)
                    rows = l2_rows(store)
                    _gates_rows(store, l0, scope, rows, tally, made, outside)
            finally:
                guard.release()

            # 13: content in the shared store is refused. The role check is
            # the one that should speak; a missing table would be the reserve
            # under it, and a gate that accepted only a crash would name no
            # gate at all.
            # Caught broadly but passing only on `WrongStore`: the role check
            # is the one that must speak. The missing `content` table is the
            # reserve underneath it, and a gate that accepted *any* exception
            # cannot tell them apart -- the sweep proved that on 08-04 by
            # deleting the role check and staying green. Catching broadly and
            # failing is not a crash; it names this gate.
            try:
                build(l0, l0, scope)
                fired = "nothing"
            except WrongStore:
                fired = "WrongStore"
            except Exception as exc:  # noqa: BLE001
                fired = type(exc).__name__
            check("13 content aimed at the L0 store is refused by role",
                  fired == "WrongStore",
                  "refused by %s" % fired)

            # 14: no write guard, no write. The store is opened *while* the
            # guard is held and the guard dropped afterwards, so what is under
            # test is `build`'s write and not `Store`'s open-time check. CP-1
            # found the difference the hard way on 08-03: a version leaning on
            # the open-time check stayed green with the scan writing straight
            # to the connection. Raising is not the property -- writing
            # nothing is.
            _id, fresh_db = new_project()
            outlived = StoreLock(str(fresh_db)).acquire()
            fresh = Store(fresh_db, role=PROJECT)
            outlived.release()
            try:
                build(fresh, l0, scope)
                refused = False
            except Unguarded:
                refused = True
            written = fresh.db.execute(
                "SELECT COUNT(*) FROM content").fetchone()[0]
            fresh.close()
            check("14 a build without the write guard writes nothing",
                  refused and written == 0,
                  "%s, %d rows written"
                  % ("refused" if refused else "ALLOWED", written))
    finally:
        l0_guard.release()


def _gates_rows(store, l0, scope, rows, tally, made, outside):
    expected = {p for (p,) in l0.db.execute(
        "SELECT path FROM files WHERE kind = 'file'") if scope.contains(p)}
    check("1  one row per file in L0 within the scope",
          bool(expected) and set(rows) == expected,
          "L2 %d, L0∩scope %d, differing %d"
          % (len(rows), len(expected), len(set(rows) ^ expected)))

    broken = [p for p, r in rows.items()
              if (r[5] is None) == (r[4] is None)]
    check("2  every row has a reason XOR text, never both or neither",
          not broken, "%d rows break it" % len(broken))

    check("8  code inside the scope is read as text",
          rows.get(made["code"], (None,) * 6)[4] == "def f():\n    return 1\n",
          "code.py text stored")

    check("9  a file outside the scope gets no row at all",
          made["outside"] not in rows and not any(
              p.startswith(outside + os.sep) for p in rows),
          "%d rows under the outside tree" % sum(
              1 for p in rows if p.startswith(outside + os.sep)))

    # R8: the symlink and the directories are inside the scope but are not
    # candidates. They must not appear as read *or* as unread.
    non_files = [p for p in rows if os.path.isdir(p) or os.path.islink(p)]
    check("10 directories and symlinks get no row",
          not non_files, "%d non-file rows" % len(non_files))

    check("12 the stored sha256 is the same digest L1 computes",
          rows[made["text"]][3] == journal.content_hash(made["text"]),
          "sha256 matches journal.content_hash")

    unread = {p: r[5] for p, r in rows.items() if r[5] is not None}
    check("15 the unread count is not zero on a tree that has binaries",
          tally["unread"] > 0 and len(unread) == tally["unread"],
          "%d unread: %s" % (tally["unread"],
                             ", ".join(sorted(set(unread.values())))))


def gates_replace_and_negative(inside):
    """11 and 16 -- rerun, and the control that gate 15 needs."""
    project_id, db = new_project()
    l0_db = l0_path()
    scope = Scope().add(inside)
    l0_guard = StoreLock(str(l0_db)).acquire()
    try:
        with Store(l0_db, role=L0) as l0:
            scan(l0, os.path.dirname(inside), deny=())
            guard = StoreLock(str(db)).acquire()
            try:
                with Store(db, role=PROJECT) as store:
                    first = build(store, l0, scope)
                    before = set(l2_rows(store))
                    # The second build is wrapped: a layer that appends
                    # instead of replacing hits `content.path`'s UNIQUE
                    # constraint and takes the whole suite down with it. A
                    # crash names no gate, so the failure is caught and
                    # reported as this one -- found by the sweep, 08-04.
                    try:
                        second = build(store, l0, scope)
                        after = set(l2_rows(store))
                        detail = "%d -> %d rows" % (len(before), len(after))
                        ok = before == after and first == second
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, (
                            "the rebuild raised %s -- it appended"
                            % type(exc).__name__)
                    check("11 a rebuild replaces the layer, it does not "
                          "accumulate", ok, detail)
            finally:
                guard.release()
    finally:
        l0_guard.release()

    # 16: a scope containing only readable UTF-8 text must report zero unread.
    # Without it, gate 15 is satisfied by a counter that is always positive.
    with tempfile.TemporaryDirectory(prefix="mhg-cp4-clean-") as clean:
        with open(os.path.join(clean, "a.txt"), "w") as fh:
            fh.write("bare tekst\n")
        with open(os.path.join(clean, "b.py"), "w") as fh:
            fh.write("x = 1\n")
        project_id, db2 = new_project()
        l0_guard = StoreLock(str(l0_db)).acquire()
        try:
            with Store(l0_db, role=L0) as l0:
                scan(l0, clean, deny=())
                guard = StoreLock(str(db2)).acquire()
                try:
                    with Store(db2, role=PROJECT) as store:
                        tally = build(store, l0, Scope().add(clean))
                        check("16 a fixture with no binaries reports zero "
                              "unread",
                              tally["unread"] == 0 and tally["read"] == 2,
                              "%d read, %d unread"
                              % (tally["read"], tally["unread"]))
                finally:
                    guard.release()
        finally:
            l0_guard.release()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mhg-cp4-") as work:
        os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
        tree = os.path.join(work, "tree")
        os.makedirs(tree)
        inside, outside, made = build_tree(tree)
        gates_classify(made)
        gates_build(inside, outside, made)
        gates_replace_and_negative(inside)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp4():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
