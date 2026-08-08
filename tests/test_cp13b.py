#!/usr/bin/env python3
"""CP-13B -- the unit file, and the measurement that replaces M-4's cron.

The answer key is `tests/gold/FASIT-cp13b.md`, written before this file and
before the code it grades (`c50e92c`). Gate numbers below are that document's.

Two subjects, and neither is the service itself. One is a unit file, graded as
a file: it has to parse, it has to name no machine, and the command line
inside it has to be a command line that runs. The other is a tally of
refusals -- and the gate that matters most there is 10, because an instrument
that can block what it measures is worse than no instrument.

**What cannot be gated here is that the cron is gone.** `crontab -l` is the
machine's state, not the repo's, and a gate reading it would be green on
exactly one machine. That removal is evidenced by command output in `TODO.md`.

Run:
    python3 tests/test_cp13b.py
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter  # noqa: E402

from morpho_homegraph.lock import StoreLock  # noqa: E402
from morpho_homegraph.store import data_home, db_path, l0_path  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT = os.path.join(REPO, "contrib", "morpho-homegraph.service")
results, check = reporter(62)


class TimedOut:
    """What a command that never returned looks like to a gate. From CP-0."""
    returncode = 124
    stdout = ""
    stderr = "timed out: the command never returned (a queue, not a refusal)"


def cli(*argv, timeout=60):
    try:
        return subprocess.run(
            [sys.executable, "-m", "morpho_homegraph.cli", *argv],
            capture_output=True, text=True, cwd=REPO,
            timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return TimedOut()


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def tally_rows():
    """The refusal tally as a list of split rows; empty when there is none."""
    path = data_home() / "refusals.tsv"
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n").split("\t") for line in fh if line.strip()]


# -- 1, 2, 3, 4, 5: the unit file, graded as a file ------------------------

def gates_unit():
    """It parses, it names no machine, and its command line is a command."""
    if not check("1a the unit file is where the install line says it is",
                 os.path.isfile(UNIT), UNIT):
        return
    with open(UNIT, encoding="utf-8") as fh:
        text = fh.read()

    verify = subprocess.run(["systemd-analyze", "verify", "--user", UNIT],
                            capture_output=True, text=True)
    check("1  systemd-analyze verify accepts the unit with nothing to say",
          verify.returncode == 0 and not verify.stderr.strip(),
          "rc=%s %r" % (verify.returncode, verify.stderr.strip()[:60]))
    check("2  Restart=on-failure is set",
          "\nRestart=on-failure" in text)
    check("3  WantedBy=default.target is set, so autostart is the choice",
          "\nWantedBy=default.target" in text)

    # 4: no machine's layout anywhere in the file, including the comments --
    # the install instructions are the most likely place for one to appear.
    homes = re.findall(r"/home/[A-Za-z0-9._-]+", text)
    check("4  the unit names no home directory, only %h",
          not homes and "%h" in text, "found: %s" % (homes[:3] or "none"))

    # 5: the ExecStart line is run, not read. A unit whose command is wrong
    # fails at the next boot, which is the one moment nobody is watching.
    exec_line = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
    workdir = [ln for ln in text.splitlines()
               if ln.startswith("WorkingDirectory=")]
    if not (exec_line and workdir):
        check("5  the unit's own ExecStart runs", False, "no ExecStart/WorkingDirectory")
        return
    argv = shlex.split(exec_line[0].split("=", 1)[1])
    cwd = workdir[0].split("=", 1)[1].replace("%h", os.path.expanduser("~"))
    ran = subprocess.run(argv + ["--help"], capture_output=True, text=True,
                         cwd=REPO if not os.path.isdir(cwd) else cwd,
                         timeout=60)
    check("5  the unit's own ExecStart is a command line that runs",
          ran.returncode == 0 and "serve" in ran.stdout,
          "rc=%s %r" % (ran.returncode, (ran.stderr or ran.stdout)[:60]))


# -- 6, 7, 8, 9, 10, 11: the tally -----------------------------------------

def gates_tally(work):
    """One row per refusal that happened, and nothing the refusal depends on."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "store")
    home = os.path.join(work, "home")
    write(os.path.join(home, "a.md"), "one file, so the scan has something\n")

    # 7 first: the control. A tally that writes a row per call passes 6 and
    # counts nothing, and running it before 6 means the file starts empty for
    # a reason the gate can see rather than by assumption.
    ok_scan = cli("scan", home, timeout=180)
    check("7  CONTROL: a writer that is not refused adds no row",
          ok_scan.returncode == 0 and not tally_rows(),
          "rc=%s, %d row(s)" % (ok_scan.returncode, len(tally_rows())))

    guard = StoreLock(str(l0_path())).acquire()
    try:
        first = cli("scan", home, timeout=120)
        rows = tally_rows()
        check("6  a refused writer adds exactly one row",
              first.returncode == 2 and len(rows) == 1,
              "rc=%s, %d row(s)" % (first.returncode, len(rows)))
        check("9  the row carries the time, the command and the holder's pid",
              bool(rows) and len(rows[0]) == 4 and rows[0][1] == "scan"
              and rows[0][3] == str(os.getpid())
              and rows[0][0].startswith("20"),
              "%s" % (rows[0] if rows else "no row"))
        # 8: a tally that writes once and never again measures nothing, and
        # would pass 6 and 9 unchanged.
        cli("scan", home, timeout=120)
        check("8  a second refusal adds a second row",
              len(tally_rows()) == 2, "%d row(s)" % len(tally_rows()))
    finally:
        guard.release()

    # 11: the tally is in place and an ordinary command is unaffected by it.
    check("11 CONTROL: an ordinary command still exits 0 with the tally there",
          cli("status", timeout=120).returncode == 0)


def gates_tally_cannot_block(work):
    """10: the instrument may never block the thing it measures (R5)."""
    os.environ["MORPHO_HOMEGRAPH_HOME"] = os.path.join(work, "blocked", "store")
    home = os.path.join(work, "blocked", "home")
    write(os.path.join(home, "a.md"), "one file\n")
    if cli("scan", home, timeout=180).returncode != 0:
        check("10 CONTROL: an unwritable tally does not stop the refusal",
              False, "the setup scan did not succeed")
        return
    # A directory where the file goes: `open(..., "a")` raises IsADirectoryError,
    # which is an OSError, and nothing in the refusal path may notice.
    os.makedirs(data_home() / "refusals.tsv", exist_ok=True)

    guard = StoreLock(str(l0_path())).acquire()
    try:
        refused = cli("scan", home, timeout=120)
    finally:
        guard.release()
    check("10 CONTROL: an unwritable tally does not stop the refusal",
          refused.returncode == 2 and "owns writing" in refused.stderr,
          "rc=%s %r" % (refused.returncode, refused.stderr.strip()[:60]))


def main() -> int:
    gates_unit()
    with tempfile.TemporaryDirectory(prefix="mhg-cp13b-") as work:
        gates_tally(work)
        gates_tally_cannot_block(work)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (one test per checkpoint) ------------------------------

def test_checkpoint_cp13b():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
