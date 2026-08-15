#!/usr/bin/env python3
"""Mutation test for CP-PUB -- the no-real-paths privacy gate.

The gate is the only written reason nothing that ships names the account, the
author, or a real home path. A band that silently stops matching makes every
file clean, which is indistinguishable from a clean tree -- so what these
mutations break are the bands, and what must go red are the *canaries*: the
checks that prove a band can fire at all. That is exactly their job.

The scanner lives in the test file itself; there is no production module to
weaken. Two mutations go outside it: `.gitignore` decides what is publishable
and is as much the gate as the scanner is, and the fallback file list is what
the `.git`-less mutation tree actually exercises.

Baseline verified before this harness: the suite is 15/15 in a `.git`-less copy
(the `.gitignore`-based path the mutation trees use). Without that, every
mutation would score as killed by a gate that was already red.
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # The account digest band can no longer match. Without the canary a hash set
    # that never hits is indistinguishable from a clean tree, and that clears
    # every file below it.
    ("the account digest band stops matching",
     "tests/test_no_real_paths.py",
     '    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]',
     '    return hashlib.sha256((text + "!").encode("utf-8")).hexdigest()[:16]',
     "the account band can fire"),

    # The /home band stops matching. A `_home_hits` that never fires would let a
    # freshly pasted account path through, the same always-green failure the
    # digest band guards against.
    ("the /home band stops matching",
     "tests/test_no_real_paths.py",
     'HOME_PATH = re.compile(r"/home/([a-z0-9._-]+)", re.IGNORECASE)',
     'HOME_PATH = re.compile(r"/HOMEZZ/([a-z0-9._-]+)", re.IGNORECASE)',
     "the /home account band can fire"),

    # The localised band stops matching. A desktop directory name compiled into
    # the package would then ship unseen.
    ("the localised band stops matching",
     "tests/test_no_real_paths.py",
     '        if any(re.search(r"\\b%s\\b" % re.escape(n), line) for n in LOCALISED):',
     "        if False:  # mutated: the localised band matches nothing",
     "the localised band can fire"),

    # The publishable tree shrinks to five files. `all()` over almost nothing is
    # also True, and a gate that looked at five files reports as clean as one
    # that looked at all. This mutates the fallback return -- the path the
    # `.git`-less mutation tree exercises.
    ("the publishable tree shrinks to five files",
     "tests/test_no_real_paths.py",
     "    return sorted(set(candidates) - ignored)",
     "    return sorted(set(candidates) - ignored)[:5]",
     "the publishable tree is non-empty"),

    # TODO.md stops being ignored and would be published -- the account and the
    # author on every decision line. One line out of `.gitignore` is the whole
    # leak, and the diff looks like tidy-up.
    ("TODO.md stops being ignored",
     ".gitignore",
     "TODO.md\ntests/gold/FASIT-*.md",
     "tests/gold/FASIT-*.md",
     "TODO.md and the FASIT files are not published"),

    # NOTICE's declared-binary list empties, so "text or declared binary" is
    # true because the list is empty and main.wasm slips through unscanned.
    ("the declared-binary list can no longer be parsed",
     "tests/test_no_real_paths.py",
     r'            re.finditer(r"^\s*[0-9a-f]{64}\s+(\S+)\s*$", text, re.M)}',
     r'            re.finditer(r"^\s*[0-9a-f]{99}\s+(\S+)\s*$", text, re.M)}',
     "NOTICE declares binaries that exist in the tree"),

    # -- the history gates (16-18), added after publishing found the gap ----
    ("the history check reads nothing and clears everything",
     "tests/test_no_real_paths.py",
     '    log = subprocess.run(["git", "-C", REPO, "log", "--format=%B"],',
     '    log = subprocess.run(["git", "-C", REPO, "log", "--format=%H"],',
     "17b CONTROL: the history is actually being read"),

    # Emptying `hits` also empties `messages` for gate 17b, which runs first
    # and is the honest killer. Gate 16 can only go red on a *real* leak, and
    # a mutation cannot plant one without editing history -- so 17b guarding
    # the input is what makes 16 trustworthy, and this needle proves 17b works.
    ("a leaked path in a commit message is not looked for",
     "tests/test_no_real_paths.py",
     '    hits = [h for line in messages for h in _home_hits(line, "<commit>")]',
     '    hits = []  # mutated: commit messages are never scanned',
     "17b CONTROL: the history is actually being read"),

    ("the in-path band stops distinguishing a path from prose",
     "tests/test_no_real_paths.py",
     r'PATHISH = re.compile(r"(?:[~/][\w.@/+-]+|\b[\w@+-]+\.[\w.@+-]+)")',
     r'PATHISH = re.compile(r"(?!)")  # mutated: nothing is path-shaped',
     "18b CONTROL: the in-path band fires"),

    # Gate 19 exists because the published repo carried the excluded record in
    # its history. These two make sure it cannot go quiet the same way.
    ("the history file list is never read, so nothing can leak",
     "tests/test_no_real_paths.py",
     '    ever = {line.strip() for line in listed.splitlines() if line.strip()}',
     '    ever = set()  # mutated: history is never inspected',
     "19b CONTROL: the history file list is actually being read"),

    ("the excluded-file match stops recognising the FASIT files",
     "tests/test_no_real_paths.py",
     '                    or (p.startswith("tests/gold/FASIT-") and p.endswith(".md")))',
     '                    or False)  # mutated: only TODO.md counts',
     # Gate 19 can only go red on a *real* leak, and a mutation cannot plant
     # one without editing history -- so the honest killer is the control that
     # guards its input. Same shape as gate 16 and 17b.
     "17b CONTROL: the history is actually being read"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                         # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_no_real_paths.py",
                 prefix="mutnrp-", timeout=300))
