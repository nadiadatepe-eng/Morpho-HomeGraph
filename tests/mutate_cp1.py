#!/usr/bin/env python3
"""Mutation test for CP-1 -- L0, and the claim that it never opens a file.

"Never opens a file" is the kind of property that is easy to state, easy to
gate, and easy to gate badly: a walk that visited nothing opens nothing, a
filter that matches nothing finds nothing, and an `strace` invocation that
failed to start reports no syscalls at all. So the mutations below include the
ones that break the *detectors* rather than the code -- if gate 3 stays green
when the walk reads every file, the detector is the thing that is broken.

Run:
    python3 tests/mutate_cp1.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the claim the whole layer rests on -------------------------------
    #
    # Nothing about the recorded metadata changes: same rows, same counts,
    # same sizes. Only the syscalls differ, which is why the two detectors
    # are the only thing that can see it.
    ("the walk opens every file it stats",
     "morpho_homegraph/scan.py",
     "            kind = _kind(entry)",
     "            kind = _kind(entry)\n"
     "            if kind == FILE:  # mutated: read what we only meant to stat\n"
     "                open(entry.path, 'rb').read(1)",
     "3  the audit hook sees no file opened during a walk"),

    # -- the deny-list ----------------------------------------------------
    #
    # The first one is the reason the fixture has a `.cachexyz` next to the
    # `.cache`: a string prefix removes rows, and removing rows is what a
    # working deny-list looks like from the outside.
    ("the deny-list matches on characters, not path separators",
     "morpho_homegraph/scan.py",
     "    return any(path == d or path.startswith(d + os.sep) for d in deny)",
     "    return any(path.startswith(d) for d in deny)  # mutated",
     "21 a neighbour that only starts the same is kept"),

    ("denied directories are skipped as rows but still descended into",
     "morpho_homegraph/scan.py",
     "            if _denied(entry.path, deny):\n"
     "                yield (\"!\" + entry.path, \"pruned\", 0, 0, 0, 0)\n"
     "                continue",
     "            if _denied(entry.path, deny) and not entry.is_dir(\n"
     "                    follow_symlinks=False):  # mutated\n"
     "                yield (\"!\" + entry.path, \"pruned\", 0, 0, 0, 0)\n"
     "                continue",
     "22 nothing under a denied directory is reached"),

    ("pruning happens, but leaves no trace to count",
     "morpho_homegraph/scan.py",
     "                yield (\"!\" + entry.path, \"pruned\", 0, 0, 0, 0)\n"
     "                continue",
     "                continue  # mutated: pruned without a marker",
     "25 pruned paths are counted, not silently dropped"),

    ("a root on the deny-list scans to an empty result",
     "morpho_homegraph/scan.py",
     "    if _denied(root, deny):\n"
     "        # Otherwise: zero rows written over a full store, which is data loss\n"
     "        # wearing the shape of a successful scan.\n"
     "        raise DeniedRoot(\"the root to scan is on the deny-list: %s\" % root)",
     "    if False:  # mutated: the empty scan is allowed through\n"
     "        raise DeniedRoot(\"the root to scan is on the deny-list: %s\" % root)",
     "26 a root that is itself denied is refused"),

    # Every other deny gate passes its own list, so all of them survive this
    # one. It is the mutation that proves gate 27 is not decoration.
    ("the shipped deny-list is empty",
     "morpho_homegraph/scan.py",
     "DEFAULT_DENY = (\n"
     "    \"~/GoogleDrive\",",
     "DEFAULT_DENY = (  # mutated: nothing is denied by default\n"
     "    \"~/nothing-is-here\",",
     "27 the shipped default denies the cloud drives and the cache"),

    ("normalising the root can strip it away entirely",
     "morpho_homegraph/scan.py",
     "    return str(Path(root).expanduser()).rstrip(os.sep) or os.sep",
     "    return str(Path(root).expanduser()).rstrip(os.sep)  # mutated",
     "28 normalising a root never empties it"),

    # -- symlinks are their own thing -------------------------------------
    ("the walk follows symlinks when stat-ing",
     "morpho_homegraph/scan.py",
     "                st = entry.stat(follow_symlinks=False)",
     "                st = entry.stat(follow_symlinks=True)  # mutated",
     "7  a symlink is its own row with its own inode"),

    ("a symlink is classified by what it points at",
     "morpho_homegraph/scan.py",
     "    if entry.is_symlink():\n"
     "        return LINK",
     "    if False:  # mutated: a link is whatever its target is\n"
     "        return LINK",
     "7  a symlink is its own row with its own inode"),

    # -- mtime precision, which CP-2 depends on ---------------------------
    #
    # Every gate about counts, paths and sizes stays green. The damage lands
    # one checkpoint later, where "same size, same mtime" is supposed to be a
    # named blind spot rather than a rounding artefact.
    ("mtime is stored as float seconds",
     "morpho_homegraph/scan.py",
     "            yield (entry.path, kind, st.st_size, st.st_mtime_ns,",
     "            yield (entry.path, kind, st.st_size, st.st_mtime,  # mutated",
     "13 mtime is an integer count of nanoseconds"),

    # -- a home area is not a tidy tree -----------------------------------
    ("an unreadable directory stops the walk",
     "morpho_homegraph/scan.py",
     "        except (PermissionError, OSError):",
     "        except (KeyboardInterrupt,):  # mutated: let it propagate",
     "9  an unreadable directory is reported, not fatal"),

    ("an unreadable directory is passed over in silence",
     "morpho_homegraph/scan.py",
     '            yield ("!" + current, "unreadable", 0, 0, 0, 0)\n'
     "            continue",
     "            continue  # mutated: say nothing about what we could not read",
     "9  an unreadable directory is reported, not fatal"),

    ("a file that vanishes mid-walk raises",
     "morpho_homegraph/scan.py",
     "            except OSError:\n"
     "                # Deleted between listing and stat. A home area is alive, and\n"
     "                # a walk that raises here fails more often the busier the\n"
     "                # machine is.\n"
     "                continue",
     "            except KeyboardInterrupt:  # mutated: let it propagate\n"
     "                continue",
     "10 a file that vanishes mid-walk is skipped, not fatal"),

    # -- the walk has to actually walk ------------------------------------
    #
    # The negative controls. A layer that yields nothing opens nothing, and
    # would pass every "never opens a file" gate in the suite.
    ("the walk never descends",
     "morpho_homegraph/scan.py",
     "                if key not in seen_dirs:\n"
     "                    seen_dirs.add(key)\n"
     "                    pending.append(entry.path)",
     "                pass  # mutated: top level only",
     "1  the row count matches find on the same tree"),

    ("directories are not rows, only routes",
     "morpho_homegraph/scan.py",
     "            yield (entry.path, kind, st.st_size, st.st_mtime_ns,\n"
     "                   st.st_ino, st.st_dev)",
     "            if kind != DIR:  # mutated: directories are not counted\n"
     "                yield (entry.path, kind, st.st_size, st.st_mtime_ns,\n"
     "                       st.st_ino, st.st_dev)",
     "1  the row count matches find on the same tree"),

    # -- L0 is a write, and writes are guarded ----------------------------
    ("the scan writes around the guard",
     "morpho_homegraph/scan.py",
     "    with store.writing() as db:",
     "    if True:  # mutated: straight to the connection\n"
     "        db = store.db",
     "12 a scan without the write guard writes nothing"),

    ("the scan records no time",
     "morpho_homegraph/scan.py",
     '    store.set_meta("l0_seconds", "%.3f" % elapsed)',
     '    store.set_meta("l0_seconds", "0")  # mutated: no time recorded',
     "14 the scan records its count and its time"),

    ("the scan keeps whatever the last one left",
     "morpho_homegraph/scan.py",
     '        db.execute("DELETE FROM files")',
     "        pass  # mutated: rows accumulate across scans",
     "15 a rescan replaces the layer, it does not accumulate"),

    # -- L0 is shared, and shared has to be enforced ----------------------
    #
    # Decided 2026-08-03 after M-1 measured L0 at 204.8 MB. Every one of these
    # leaves a working product that quietly stores the same catalogue once per
    # project, which is a disk-usage bug nobody notices until there are ten
    # projects.
    ("a scan lands wherever it is pointed",
     "morpho_homegraph/scan.py",
     "    if store.role != L0:",
     "    if False:  # mutated: any store will do",
     "17 a scan aimed at a project store is refused by role"),

    ("the shared store is not the one holding L0",
     "morpho_homegraph/store.py",
     '    "files": (L0,',
     '    "files": (PROJECT,  # mutated: L0 lives in projects instead',
     "18 the shared L0 store is the one that has L0"),

    # One lock for everything: correct, in that two writers never collide.
    # Also means a nightly L0 refresh locks every project out of its own
    # index for as long as it runs, and nothing about a single-writer run
    # can see that.
    ("one guard covers every store, not one per store",
     "morpho_homegraph/lock.py",
     '        self.path = self.store_path + ".lock"',
     '        self.path = os.path.join(  # mutated: a single global guard\n'
     '            os.path.dirname(os.path.dirname(self.store_path)), "all.lock")',
     "19 an L0 refresh does not lock a project out"),

    # There is no mutation for the `(dev, inode)` directory dedupe, and the
    # reason is written down rather than left as a silent gap: the condition
    # it exists for -- the same directory reachable by two names -- needs a
    # bind mount, and creating one needs root. It is guarded code with no
    # gate, which the plan's trap 3 warns about. See FASIT-cp1.md, blind
    # spot 4: it is kept because an unbounded walk is a hang rather than a
    # wrong number, and it is recorded as untested rather than implied to be
    # covered.
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp1.py", prefix="mut1-", timeout=900))
