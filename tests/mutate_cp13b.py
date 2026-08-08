#!/usr/bin/env python3
"""Mutation test for CP-13B -- the unit file and the refusal tally.

The tally is the dangerous half. It sits on the *unhappy* path, where the
command is already failing, and a defect there looks exactly like the refusal
working: exit 2, the right message, and a file nobody reads until the number
is quoted. So the mutations come in pairs -- one that makes it count too much
(killed by the control, gate 7) and one that makes it count too little or not
at all (killed by 6, 8 or 9).

The unit file is graded as a file, so its mutations edit the file: a home
directory in a comment, a missing `Restart=`, an `ExecStart` naming a
subcommand that does not exist. The last one is the reason gate 5 runs the
command instead of reading it.

Run:
    python3 tests/mutate_cp13b.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- the tally counts what happened, not what was called (R3, R4) ------
    ("nothing is ever recorded",
     "morpho_homegraph/cli.py",
     "        _record_refusal(command, store_db, exc.holder)",
     "        pass  # mutated: the refusal is printed and forgotten",
     "6  a refused writer adds exactly one row"),

    # The control's own mutation: a row per call rather than per refusal.
    # Gate 6 stays green for it, and the number becomes "how often did anyone
    # take the guard", which is not the question M-4 asked.
    ("a row is written whether or not the guard was refused",
     "morpho_homegraph/cli.py",
     "    try:\n"
     "        return _guard(store_db)\n"
     "    except Locked as exc:",
     "    _record_refusal(command, store_db, {})  # mutated: every call\n"
     "    try:\n"
     "        return _guard(store_db)\n"
     "    except Locked as exc:",
     "7  CONTROL: a writer that is not refused adds no row"),

    ("the tally is rewritten each time instead of appended to",
     "morpho_homegraph/cli.py",
     "        with open(path, \"a\", encoding=\"utf-8\") as fh:",
     "        with open(path, \"w\", encoding=\"utf-8\") as fh:  # mutated",
     "8  a second refusal adds a second row"),

    # -- the denominator (R6) ----------------------------------------------
    ("the row drops the holder's pid",
     "morpho_homegraph/cli.py",
     "            fh.write(\"%s\\t%s\\t%s\\t%s\\n\"\n"
     "                     % (service.stamp(), command, store_db,\n"
     "                        holder.get(\"pid\", \"?\")))",
     "            fh.write(\"%s\\t%s\\t%s\\n\"  # mutated: no denominator\n"
     "                     % (service.stamp(), command, store_db))",
     "9  the row carries the time, the command and the holder's pid"),

    ("every row is filed under the same command name",
     "morpho_homegraph/cli.py",
     "    barrier = _guard_or_refuse(store_db, \"scan\")",
     "    barrier = _guard_or_refuse(store_db, \"write\")  # mutated",
     "9  the row carries the time, the command and the holder's pid"),

    # -- the instrument may not block what it measures (R5) ----------------
    #
    # The one that matters most. With the guard swallowed, an unwritable tally
    # takes down the refusal itself -- on the path that was already failing,
    # which is where nobody is looking.
    ("a tally that cannot be written takes the refusal down with it",
     "morpho_homegraph/cli.py",
     "    except OSError:\n"
     "        pass",
     "    except ValueError:  # mutated: OSError now escapes\n"
     "        pass",
     "10 CONTROL: an unwritable tally does not stop the refusal"),

    ("the refusal is recorded before it is reported, so a failure hides it",
     "morpho_homegraph/cli.py",
     "        print(\"REFUSED  %s\\n(waiting is not offered, and asking that "
     "process \"\n"
     "              \"to do the job is not built: re-run when it is done)\" "
     "% exc,\n"
     "              file=sys.stderr)\n"
     "        _record_refusal(command, store_db, exc.holder)",
     "        _record_refusal(command, store_db, exc.holder)  # mutated: "
     "order swapped\n"
     "        raise",
     "10 CONTROL: an unwritable tally does not stop the refusal"),

    # -- the unit file, graded as a file (R2, R7, R8) ----------------------
    ("the unit stops restarting itself",
     "contrib/morpho-homegraph.service",
     "Restart=on-failure",
     "Restart=no",
     "2  Restart=on-failure is set"),

    ("the unit stops starting itself, so autostart is silently not the choice",
     "contrib/morpho-homegraph.service",
     "WantedBy=default.target",
     "WantedBy=basic.target",
     "3  WantedBy=default.target is set, so autostart is the choice"),

    ("a home directory creeps back in, in a comment",
     "contrib/morpho-homegraph.service",
     "WorkingDirectory=%h/Morpho-HomeGraph",
     "# e.g. WorkingDirectory=/home/someone/Morpho-HomeGraph\n"
     "WorkingDirectory=%h/Morpho-HomeGraph",
     "4  the unit names no home directory, only %h"),

    # The reason gate 5 runs the line instead of reading it: this one parses,
    # verifies, and fails at the next boot.
    ("ExecStart names a subcommand that does not exist",
     "contrib/morpho-homegraph.service",
     "ExecStart=/usr/bin/python3 -m morpho_homegraph.cli serve",
     "ExecStart=/usr/bin/python3 -m morpho_homegraph.cli daemon",
     "5  the unit's own ExecStart is a command line that runs"),

    ("ExecStart names a module that does not exist",
     "contrib/morpho-homegraph.service",
     "ExecStart=/usr/bin/python3 -m morpho_homegraph.cli serve",
     "ExecStart=/usr/bin/python3 -m morpho_homegraph.service serve",
     "5  the unit's own ExecStart is a command line that runs"),

    ("the unit gains a directive systemd does not know",
     "contrib/morpho-homegraph.service",
     "Type=simple",
     "Type=simple\nRestartWhenever=yes",
     "1  systemd-analyze verify accepts the unit with nothing to say"),
]

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp13b.py", prefix="mut13b-", timeout=600))
