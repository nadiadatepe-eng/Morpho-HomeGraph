#!/usr/bin/env python3
"""Back up the files that are deliberately outside git.

**`TODO.md` and `tests/gold/FASIT-*.md` are gitignored on purpose** -- they
name the account and the home paths, and `CP-PUB` gates that. The cost is
that this machine is the only place they exist, and a bad write has no
`git checkout` behind it.

That cost was paid on 2026-08-20: a script wrote `TODO.md` twice in one
run, the second write re-read the file it had already replaced, and 2 422
lines became 0. It was recovered only because three copies happened to be
lying around in `/tmp` from mutation-test trees -- luck, not a mechanism.

So: keep the last `KEEP` snapshots, oldest pruned first, written atomically
(tmp + rename) so an interrupted backup cannot replace a good snapshot with
half a file. Cheap enough to run before any scripted edit and from cron.

Run:
    python3 tools/backup_untracked.py          # take a snapshot
    python3 tools/backup_untracked.py --list   # what is held
"""
from __future__ import annotations

import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, ".todo-backups")
KEEP = 20

# Only what git will not hold. Adding a tracked file here would be a second
# copy of something that already has history, which is a place for the two
# to disagree.
PATTERNS = ("TODO.md",)
GOLD = os.path.join("tests", "gold")


def targets() -> list[str]:
    out = [p for p in PATTERNS if os.path.isfile(os.path.join(REPO, p))]
    gold_dir = os.path.join(REPO, GOLD)
    if os.path.isdir(gold_dir):
        out += [os.path.join(GOLD, n) for n in sorted(os.listdir(gold_dir))
                if n.startswith("FASIT-") and n.endswith(".md")]
    return out


def snapshot() -> str:
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    root = os.path.join(DEST, stamp)
    for rel in targets():
        src = os.path.join(REPO, rel)
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # tmp + rename: an interrupted copy must not leave a short file
        # wearing a good name. The same rule the wiki side of ai-memory
        # uses, and the reason it is here rather than a plain copy2.
        tmp = dst + ".part"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    return root


def prune() -> list[str]:
    if not os.path.isdir(DEST):
        return []
    kept = sorted(d for d in os.listdir(DEST)
                  if os.path.isdir(os.path.join(DEST, d)))
    dropped = kept[:-KEEP] if len(kept) > KEEP else []
    for d in dropped:
        shutil.rmtree(os.path.join(DEST, d), ignore_errors=True)
    return dropped


def main(argv: list[str]) -> int:
    if "--list" in argv:
        if not os.path.isdir(DEST):
            print("no snapshots yet")
            return 0
        for d in sorted(os.listdir(DEST)):
            files = sum(len(f) for _, _, f in os.walk(os.path.join(DEST, d)))
            print("%s  %d file(s)" % (d, files))
        return 0
    files = targets()
    if not files:
        print("nothing to back up -- no TODO.md and no FASIT files")
        return 1
    root = snapshot()
    dropped = prune()
    print("snapshot %s  (%d file(s))" % (os.path.basename(root), len(files)))
    for f in files:
        print("   %s" % f)
    if dropped:
        print("pruned %d old snapshot(s), keeping %d" % (len(dropped), KEEP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
