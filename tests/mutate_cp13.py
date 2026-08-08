#!/usr/bin/env python3
"""Mutation test for CP-13 -- the service, and the ways it can look like one.

A broken service does not crash. It sits there holding guards and printing
sweep lines while indexing nothing, or it rebuilds the whole project every
time a `.cache` file moves and calls that being current. Both look identical
to `ps`.

So the mutations come in pairs, and roughly half of them aim at the controls
rather than at the defects. `is_layout` returning False makes gates 8 and 9
red; returning True makes gate 10 red -- and a watch that fires on everything
would otherwise pass every gate about noticing changes. The same shape for
`relevant` (6 against 7), for the sweep (12 against 13 and 12b), and for
`maybe_vacuum` (14 against 15).

Run:
    python3 tests/mutate_cp13.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- one writer, and only of what it watches (R1) ----------------------
    ("the service guards only L0, leaving watched projects writable",
     "morpho_homegraph/service.py",
     "    store_paths = [l0_path()] + [db_path(pid) for pid, _ in watched]",
     "    store_paths = [l0_path()]  # mutated: the projects stay open",
     "2  update on a watched project is refused the same way"),

    # The control's own mutation, and the one a "safe" reading of decision 12
    # would produce: guard everything registered. Gate 2 stays green, gate 3
    # goes red, and the service has locked a person out of a project it never
    # intends to touch.
    ("the service guards every registered project, not only the watched",
     "morpho_homegraph/service.py",
     "    watched = watched_projects()\n"
     "    store_paths = [l0_path()] + [db_path(pid) for pid, _ in watched]",
     "    watched = watched_projects()  # mutated\n"
     "    store_paths = [l0_path()] + [db_path(pid) for pid, _ in projects()]",
     "3  CONTROL: an unwatched project still updates while it runs"),

    ("a refused guard is stepped over instead of stopping the start",
     "morpho_homegraph/service.py",
     "        except Locked as exc:\n"
     "            for lock in held:\n"
     "                lock.release()",
     "        except Locked as exc:  # mutated: carry on without it\n"
     "            continue\n"
     "            for lock in held:\n"
     "                lock.release()",
     "4  a second service is refused, exit 2, no store left behind"),

    ("the refusal is silent, so a second service looks like a hang",
     "morpho_homegraph/service.py",
     "            out(\"REFUSED  %s\\n(the service is the only writer while "
     "it runs: \"\n"
     "                \"stop it, or wait for it)\" % exc)",
     "            pass  # mutated: refuse without saying so",
     "4  a second service is refused, exit 2, no store left behind"),

    ("the guards are never released, so Ctrl-C leaves the store locked",
     "morpho_homegraph/service.py",
     "        for lock in held:\n"
     "            lock.release()",
     "        pass  # mutated: the kernel can clean up",
     # 17, not 17b: the subprocess form cannot fail here, because the kernel
     # drops a dead process's flocks whether or not `release()` ran.
     "17 serve releases every guard on the way out, in this process"),

    ("the service starts silently, so nothing can tell when it is the writer",
     "morpho_homegraph/service.py",
     "    out(\"serving  %d guard(s) held, %d watched project(s), sweep every \"\n"
     "        \"%.0f min\" % (len(held), len(watched), sweep_seconds / 60))",
     "    pass  # mutated: start without saying so",
     "4b the service says it is holding the guards before it sweeps"),

    # -- an update never triggers itself (R2) ------------------------------
    ("store writes are treated as corpus changes",
     "morpho_homegraph/service.py",
     "        if not relevant(path, ignore):\n"
     "            return False",
     "        if False:  # mutated: the service reacts to its own writes\n"
     "            return False",
     "6  the service's own store writes never begin a burst"),

    # The control. A predicate that keeps nothing passes gate 6 perfectly and
    # is a service that never indexes anything.
    ("nothing is ever worth keeping",
     "morpho_homegraph/service.py",
     "        if not relevant(path, ignore):\n"
     "            return False",
     "        if True:  # mutated: keep nothing at all\n"
     "            return False",
     "7  CONTROL: one corpus path does trigger exactly one update"),

    ("the burst is never coalesced, so each read is its own update",
     "morpho_homegraph/service.py",
     "            while True:\n"
     "                more = _hits(source.read(debounce), roots, keeps)\n"
     "                if not more:\n"
     "                    break\n"
     "                hits |= more",
     "            pass  # mutated: no debounce window",
     "5  a burst spanning two reads is one update, not one per read"),

    # -- only the flagged projects (R3) ------------------------------------
    ("every project counts as watched",
     "morpho_homegraph/service.py",
     "    return (store.get_meta(WATCH) or \"\") == \"1\"",
     "    return True  # mutated: the flag decides nothing",
     "11a only the flagged project is listed as watched"),

    ("no project ever counts as watched",
     "morpho_homegraph/service.py",
     "    return (store.get_meta(WATCH) or \"\") == \"1\"",
     "    return False  # mutated: the flag can never be set",
     "11a only the flagged project is listed as watched"),

    ("the watch command writes the flag but never clears it",
     "morpho_homegraph/service.py",
     "    store.set_meta(WATCH, \"1\" if on else \"0\")",
     "    store.set_meta(WATCH, \"1\")  # mutated: --off does nothing",
     "21b CONTROL: watch --off clears the flag again"),

    # `Inotify` holds one `_prune` field, so a per-project closure leaves the
    # last project's scope deciding for the first project's new directories.
    # The mutation is the code that was there before the review found it.
    ("the prune covers only the last watched tree",
     "morpho_homegraph/service.py",
     "    def prune(directory: str) -> bool:\n"
     "        for root, by_store, selected in rules:",
     "    def prune(directory: str) -> bool:  # mutated: last tree only\n"
     "        for root, by_store, selected in rules[-1:]:",
     "11c one prune covers every watched tree, not the last one only"),

    # -- a layout change is not a file change (R4) -------------------------
    ("nothing counts as layout, so .gitignore is judged as content",
     "morpho_homegraph/service.py",
     "    return base in (\".gitignore\", \".git\")",
     "    return False  # mutated: only content matters",
     "8  an edited .gitignore triggers an update"),

    # The control for 8 and 9, and the mutation that shows they are not free:
    # a watch that fires on everything makes both green.
    ("everything counts as layout",
     "morpho_homegraph/service.py",
     "    return base in (\".gitignore\", \".git\")",
     "    return True  # mutated: every event is a layout change",
     "10 CONTROL: a change the scope excludes triggers nothing"),

    ("the repo marker appearing is not noticed",
     "morpho_homegraph/service.py",
     "    return base in (\".gitignore\", \".git\")",
     "    return base in (\".gitignore\",)  # mutated",
     "9  a folder becoming a repo (.git appears) triggers an update"),

    ("the scope is consulted before layout, so the ignored file wins",
     "morpho_homegraph/service.py",
     "        if is_layout(path):\n"
     "            return True\n"
     "        return project_scope.contains(path)",
     "        return project_scope.contains(path)  # mutated: order swapped\n"
     "        if is_layout(path):\n"
     "            return True",
     "8  an edited .gitignore triggers an update"),

    ("the scope is not consulted at all",
     "morpho_homegraph/service.py",
     "        return project_scope.contains(path)",
     "        return True  # mutated: anything in the tree counts",
     "10 CONTROL: a change the scope excludes triggers nothing"),

    # -- two clocks, and they do not trigger each other (R5) ---------------
    ("the sweep never runs",
     "morpho_homegraph/service.py",
     "        next_sweep = clock()\n",
     "        next_sweep = clock() + sweep_seconds  # mutated: never at start\n",
     "12 the periodic sweep rebuilds L0 with no event at all"),

    ("the sweep runs every round instead of on its own clock",
     "morpho_homegraph/service.py",
     "                _summary, moved = _sweep(scan_root, out)\n"
     "                next_sweep = clock() + sweep_seconds",
     "                _summary, moved = _sweep(scan_root, out)"
     "  # mutated: the clock never moves",
     "12b CONTROL: a second round with the sweep not due does not sweep"),

    # The control for 12. A sweep that rebuilds every project pays M-3's
    # minutes on the catalogue's schedule, and gate 12 cannot see it.
    ("the broad sweep also rebuilds every watched project",
     "morpho_homegraph/service.py",
     "                _summary, moved = _sweep(scan_root, out)\n"
     "                next_sweep = clock() + sweep_seconds",
     "                _summary, moved = _sweep(scan_root, out)\n"
     "                for _p, _r in watched:  # mutated: broad does narrow\n"
     "                    _update(_p, _r, out)\n"
     "                next_sweep = clock() + sweep_seconds",
     "13 CONTROL: the sweep does not build any project's layers"),

    # -- fragmentation (R6) ------------------------------------------------
    ("VACUUM never runs, so the store grows with use",
     "morpho_homegraph/service.py",
     "    free = fragmentation(store.db)\n"
     "    if free < threshold:\n"
     "        return None",
     "    free = fragmentation(store.db)\n"
     "    if True:  # mutated: never worth it\n"
     "        return None",
     "14 over the threshold VACUUM runs and the file shrinks"),

    # The control. 2.6 s of pause on every sweep, and gate 14 stays green.
    ("VACUUM runs every sweep regardless of the threshold",
     "morpho_homegraph/service.py",
     "    if free < threshold:\n"
     "        return None",
     "    if False:  # mutated: always worth it\n"
     "        return None",
     "15 CONTROL: under the threshold VACUUM does not run"),

    # Gate 15 used to be the one named here, and it was the wrong one: its
    # threshold was `fragmentation(...) + 0.5`, worked out from the very
    # function this mutates, so it stayed green. 14b compares against the
    # pragmas instead.
    ("fragmentation is measured against the free pages, so it is always 1",
     "morpho_homegraph/service.py",
     "    return free / total if total else 0.0",
     "    return 1.0 if free else 0.0  # mutated",
     "14b fragmentation is free pages over all pages, not a constant"),

    # -- a missing event source is a refusal, not a crash (R7) -------------
    ("a machine without inotify takes the whole service down",
     "morpho_homegraph/service.py",
     "            except InotifyUnavailable as exc:",
     # A real exception that this block cannot raise, rather than a name that
     # does not exist: the first mutates the behaviour, the second only breaks
     # the import, and a NameError names no gate.
     "            except ZeroDivisionError as exc:  # mutated: let it propagate",
     "16 without inotify the service says so, sweeps, and exits 0"),

    ("the fallback is silent, so unwatched projects look watched",
     "morpho_homegraph/service.py",
     "                out(\"unwatched  %s\\n(%d project(s) will only be seen "
     "by the \"\n"
     "                    \"%.0f min sweep)\" % (exc, len(watched), "
     "sweep_seconds / 60))",
     "                pass  # mutated: fall back without saying so",
     "16 without inotify the service says so, sweeps, and exits 0"),

    # -- one line per round (R8) -------------------------------------------
    ("the round line drops the layer counts",
     "morpho_homegraph/service.py",
     "    out(\"update   %s  %s  L2 %d read / %d unread  L3 %d edges  "
     "L4 %d rows\"",
     "    out(\"update   %s  %s\" % (project_id, root) or \"\"  # mutated\n"
     "        or \"update   %s  %s  L2 %d read / %d unread  L3 %d edges  "
     "L4 %d rows\"",
     "18 the round names the project, the layers and the time"),

    # -- one definition of an update (R10) ---------------------------------
    #
    # The defect this rule exists for cannot be reached by deleting a line:
    # it is a second build path someone writes because the first one refused
    # them. So the mutation writes the call that would have caused it -- the
    # one that takes a guard this process already holds.
    # Caught by a *static* gate, so `gates_one_update()` runs first in
    # `main()`. When it ran last, the runtime crash this mutation causes took
    # the suite down before the verdict was printed, and a crash names no
    # gate -- measured 2026-08-08.
    ("the service goes through cmd_update, which would refuse itself",
     "morpho_homegraph/service.py",
     "            result = build_layers(store, project_id)",
     "            from .cli import cmd_update  # mutated\n"
     "            result = cmd_update(store) or build_layers(store, project_id)",
     "19c the service never calls cmd_update, which would refuse itself"),

    ("the command builds its own layers instead of the shared function",
     "morpho_homegraph/cli.py",
     "                built = service.build_layers(store, project_id)",
     "                built = {\"recreated\": True}  # mutated: its own path",
     "19a the command builds through service.build_layers"),

    # -- the watch flag reaches an existing project (gate 21) --------------
    ("watch only ever reports, never writes",
     "morpho_homegraph/cli.py",
     "            service.set_watch(store, not args.off)",
     "            pass  # mutated: the flag is never written",
     "21 watch arms an already registered project"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp13.py", prefix="mut13-", timeout=900))
