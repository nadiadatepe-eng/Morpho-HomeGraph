#!/usr/bin/env python3
"""Mutation test for CP-6 -- move, copy, delete, and the two ways to get it wrong.

The failure here is not an exception. It is a project whose index quietly
starts describing a different folder, and there are exactly two mechanisms that
can do it: an inode the filesystem handed out twice, and a fingerprint shared
by two trees. So the mutations below mostly make recognition *more* willing --
accept the inode without confirming it, pick the first of several candidates,
treat a deletion as a move.

The other half aims at the opposite failure, the one that looks safe: a
mechanism that always refuses passes every gate about refusing.

Run:
    python3 tests/mutate_cp6.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the table, and only exists() deciding it --------------------------
    ("a copy is read as a move",
     "morpho_homegraph/identity.py",
     "    if old_here and new_here:\n"
     "        return COPY",
     "    if old_here and new_here:\n"
     "        return MOVE  # mutated",
     "1  both paths present is a copy"),

    ("a move is read as a deletion",
     "morpho_homegraph/identity.py",
     "    if new_here:\n"
     "        return MOVE",
     "    if new_here:\n"
     "        return DELETED  # mutated",
     "2  the old gone and the new present is a move"),

    ("a deletion is read as the project still being there",
     "morpho_homegraph/identity.py",
     "    return DELETED\n\n\ndef candidates_by_inode",
     "    return PRESENT  # mutated\n\n\ndef candidates_by_inode",
     "3  both gone is a deletion"),

    # -- the inode is a candidate, never a proof ---------------------------
    #
    # The dangerous one. A filesystem reuses inode numbers, and accepting the
    # hit without confirming the tree points a whole project's index at a
    # stranger's folder. Nothing raises; the index simply describes the wrong
    # place from then on.
    ("an inode hit is taken as proof, unconfirmed",
     "morpho_homegraph/identity.py",
     "        found = [p for p in found\n"
     "                 if mine is not None\n"
     "                 and _fingerprint_of_tree(p, l0_store) == mine]",
     "        found = list(found)  # mutated: the inode is enough",
     "17 an inode hit with the wrong content is not a move"),

    ("the inode is never consulted, only the fingerprint",
     "morpho_homegraph/identity.py",
     "    if dev and inode:",
     "    if False:  # mutated",
     "4  a move is recognised by the inode, and the path is named"),

    ("the fallback is dropped, so only the inode can find it",
     "morpho_homegraph/identity.py",
     "    if not found and mine is not None:",
     "    if False:  # mutated: no fallback",
     "13 the fingerprint alone finds it when the inode cannot"),

    # -- ambiguity is refused, not chosen ----------------------------------
    ("two candidates are resolved by taking the first",
     "morpho_homegraph/identity.py",
     "    if len(found) > 1:",
     "    if False:  # mutated: pick whichever sorts first",
     "6  two identical trees are refused, not chosen between"),

    ("recognition always refuses, whatever it finds",
     "morpho_homegraph/identity.py",
     "    if len(found) == 1:\n"
     "        return {\"verdict\": MOVE, \"path\": found[0],"
     " \"candidates\": found,\n"
     "                \"by\": by}",
     "    if found:  # mutated: never sure enough\n"
     "        return {\"verdict\": AMBIGUOUS, \"path\": None,"
     " \"candidates\": found,\n"
     "                \"by\": by}",
     "12 with the trees no longer identical, exactly one answer"),

    # -- the fingerprint ---------------------------------------------------
    ("an empty tree gets a fingerprint like every other empty tree",
     "morpho_homegraph/identity.py",
     "    if not lines:\n"
     "        return None",
     "    if False:  # mutated: empty is a value like any other\n"
     "        return None",
     "19 an empty project matches no empty directory"),

    ("size is dropped, so only the shape of the tree counts",
     "morpho_homegraph/identity.py",
     "    lines = sorted(\"%s\\0%d\" % (os.path.relpath(path, root), size)\n"
     "                   for path, size in entries)",
     "    lines = sorted(os.path.relpath(path, root)  # mutated\n"
     "                   for path, size in entries)",
     "17 an inode hit with the wrong content is not a move"),

    ("the fingerprint uses absolute paths, so a move never matches",
     "morpho_homegraph/identity.py",
     "    lines = sorted(\"%s\\0%d\" % (os.path.relpath(path, root), size)",
     "    lines = sorted(\"%s\\0%d\" % (path, size)  # mutated",
     "4  a move is recognised by the inode, and the path is named"),

    # -- a move keeps everything that is not a path ------------------------
    ("applying a move rebuilds instead of rewriting",
     "morpho_homegraph/identity.py",
     "            db.execute(\n"
     "                \"UPDATE content SET path = ? || SUBSTR(path, ?) \"\n"
     "                \"WHERE path LIKE ?\",",
     "            db.execute(\"DELETE FROM edges\")  # mutated\n"
     "            db.execute(\n"
     "                \"UPDATE content SET path = ? || SUBSTR(path, ?) \"\n"
     "                \"WHERE path LIKE ?\",",
     "8  a move loses no edge and changes no provenance"),

    ("the paths are left pointing at where the project used to be",
     "morpho_homegraph/identity.py",
     "                (new_path, len(old) + 1, old.rstrip(os.sep) + os.sep"
     " + \"%\"))",
     "                (old, len(old) + 1, old.rstrip(os.sep) + os.sep"
     " + \"%\"))  # mutated",
     "9  the recorded paths follow, the hashes do not move"),

    # There is deliberately NO mutation for "the inode is not refreshed after
    # a move", and the reason is the same one that keeps gate 5 from running.
    # `shutil.move` within one filesystem is `rename`, and **rename preserves
    # the inode** -- so the refresh is a no-op in everything a harness can
    # build without root. It matters only after a cross-filesystem move, where
    # the copy gets a new inode, and that is the case we cannot stage.
    # Measured 2026-08-04: with the refresh deleted, gate 18 stays green.
    # Written down as untested rather than left looking covered.

    # -- the old path is refused, not emptied ------------------------------
    ("a vanished project answers with nothing instead of where it went",
     "morpho_homegraph/identity.py",
     "    if answer[\"verdict\"] == MOVE:\n"
     "        raise Moved(old, answer[\"path\"])",
     "    if answer[\"verdict\"] == MOVE:\n"
     "        return None  # mutated: an empty answer",
     "10 the old path is refused with where it went, not emptied"),

    # -- marking, not deleting ---------------------------------------------
    ("a deleted project stays in the listings",
     "morpho_homegraph/identity.py",
     "                if (store.get_meta(\"state\") or LIVING) == LIVING:\n"
     "                    out.append((pid, path))",
     "                out.append((pid, path))  # mutated",
     "15 marking removes it from listings and leaves the store alone"),

    # -- one question, two entrances ---------------------------------------
    #
    # The sweep growing its own logic is the failure Nadi's "both must be
    # possible" invites, and it is invisible until the two disagree.
    ("the sweep answers from the registry instead of asking",
     "morpho_homegraph/identity.py",
     "    return {pid: recognise(pid, l0_store) for pid, _path in projects()}",
     "    return {pid: {\"verdict\": PRESENT, \"path\": path,  # mutated\n"
     "                  \"candidates\": [path]}\n"
     "            for pid, path in projects()}",
     "16 the sweep and the single answer agree on the same state"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp6.py", prefix="mut6-", timeout=600))
