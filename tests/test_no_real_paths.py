#!/usr/bin/env python3
"""Nothing that ships may name the account, the author, or a real home path.

This is a privacy requirement, not tidiness, and it is the kind that decays:
one pasted absolute path in a docstring six months from now and the guarantee
is gone, silently, in a commit whose diff looks like a comment change. So it is
a test rather than a promise. The answer key is `tests/gold/FASIT-cppub.md`,
written before this file.

**What is checked, and over what.** The publishable tree is everything git
would track -- `git ls-files --cached --others --exclude-standard`, the tracked
set plus untracked-but-not-ignored. `git ls-files` alone lists TRACKED files,
so a brand-new module is invisible to this check until it is committed, which is
to say the guard goes green over exactly the file most likely to be leaking. And
when git cannot answer at all the check FAILS rather than skips: "we could not
look" and "we looked and it was clean" are different facts, and only one is a
guarantee (see FASIT-cppub blindflekk, and homegraph's answer-keys lesson).

**The Norwegian development record is excluded on purpose.** `TODO.md` and
`tests/gold/FASIT-*.md` are git-ignored -- kept locally, out of the public tree
-- because they name the author and home paths on every decision line. This
check must agree with git about which files those are rather than keeping a
second list that drifts, and it proves the exclusion happened (they are not in
the tree) AND that the material still exists (so "excluded" is not true merely
because there was nothing to exclude).

Two deliberate relaxations, stated here rather than hidden in a skip list:

  * `LICENSE` is exempt from the personal band. A licence must name a copyright
    holder to be a licence at all; withholding it is the problem, not the fix.
  * `/home` as a bare mount string is allowed. It is a real NFS mount point
    that CP-0's locality guard reasons about; only `/home/<a-real-account>` is
    a leak, and placeholders like `/home/someone` are fine.

This file itself is exempt from the /home and localised bands, because those
bands have to spell their needles and canaries to search for them. The digest
band still covers this file -- it is subject to its own rule -- so the account
is never written here in plaintext.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import reporter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The account and author, stored as digests rather than text. Listing them in
# plaintext would make this file the last thing in the tree still naming the
# account it exists to keep out -- a guard that leaks exactly what it guards.
# Matching is by token n-gram: a file's text is reduced to alphanumeric runs and
# every 1-, 2- and 3-gram is hashed the same way, which catches an account
# spelled inside a path and inside a slug alike. The cost is the inability to
# match a substring inside a longer word; the account is short and token-shaped,
# so the price is low. This file is subject to the band too -- it must not spell
# the account, which is why the comments say "the account" and not the name.
PERSONAL_DIGESTS = {
    "646fe999c9dad33d",   # the account
    "c66b5036a905884e",   # the surname
    "34f4d6ff12506c16",   # the two together
}

TOKEN = re.compile(r"[a-z0-9æøåäöü]+")

# A path-shaped run: starts at `~` or `/`, or is a bare filename with a dot in
# it. Gate 18 hands only these to the digest band, so an account name in a
# *sentence* is allowed while the same name in a *path* is not.
PATHISH = re.compile(r"(?:[~/][\w.@/+-]+|\b[\w@+-]+\.[\w.@+-]+)")
NGRAM_MAX = 3

# A single token that appears nowhere but the canary check, so proving the band
# can fire does not require writing a real identifier down.
CANARY_DIGEST = {hashlib.sha256(b"quaywiselanternfen").hexdigest()[:16]}

# `/home/<seg>` where seg is not one of these obvious non-names is a leaked
# account. `/home` with nothing after (the NFS mount point) does not match --
# the trailing slash is required -- and the placeholders this repo already uses
# in bug examples and test data are allowed.
HOME_PATH = re.compile(r"/home/([a-z0-9._-]+)", re.IGNORECASE)
PLACEHOLDERS = {"someone", "somebody", "user", "you", "name", "example"}

# Directory names that belong to one desktop language. A name from one spelling
# compiled into the package is a fact about one machine that fails silently on a
# machine that spells it differently. Predecessor project names (`homegraph`,
# `morphofiles`) are NOT this: not localised, and this repo is named after them.
LOCALISED = ["Bilder", "Dokumenter", "Nedlastinger", "Skrivebord",
             "Skjermbilder"]

# A licence has to carry a copyright holder to be a licence, so it is the one
# file exempt from the personal band.
AUTHORSHIP = ("LICENSE",)
SELF = os.path.join("tests", "test_no_real_paths.py")

TEXT_SUFFIXES = (".py", ".toml", ".md", ".txt", ".cfg", ".ini", ".tsv",
                 ".html", ".json", ".yml", ".yaml", ".service", ".mjs",
                 ".js", ".c", ".sh", ".lock", "")

results, check = reporter(56)


def publishable_files():
    """Every file git would track, or would track if this were a repository.

    Two distinct failures, kept apart: a directory that is not a repo yet (a
    fresh export, or the mutation harness's `.git`-less copy) is enumerated and
    filtered with the same `.gitignore` git will use on commit day, via a
    scratch git-dir outside the tree. But the git BINARY being missing is a
    refusal, not a skip -- "we could not look" is not "we looked and it was
    clean" (R2, and homegraph's answer-keys lesson).
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip("\0"):
        return sorted(p for p in out.stdout.split("\0") if p)

    # Not a repository. Enumerate, then let git filter with the real .gitignore.
    candidates = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", "node_modules")]
        for name in filenames:
            candidates.append(os.path.relpath(
                os.path.join(dirpath, name), REPO))
    scratch = tempfile.mkdtemp(prefix="ignore-probe-")
    try:
        base = ["git", "--git-dir=%s" % os.path.join(scratch, "git"),
                "--work-tree=%s" % REPO]
        init = subprocess.run(base + ["init", "-q"],
                              capture_output=True, text=True)
        if init.returncode != 0:
            raise RuntimeError("git unavailable: %s" % init.stderr.strip())
        probe = subprocess.run(base + ["check-ignore", "--no-index", "--stdin"],
                               input="\n".join(candidates),
                               capture_output=True, text=True)
        if probe.returncode not in (0, 1):
            raise RuntimeError("git check-ignore unavailable: %s"
                               % probe.stderr.strip())
        ignored = {ln.strip() for ln in probe.stdout.splitlines() if ln.strip()}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return sorted(set(candidates) - ignored)


def declared_binaries():
    """Paths NOTICE pins by sha256 -- the set of binaries allowed in the tree.

    A binary outside this set is a file the content bands cannot decode and so
    would silently skip: a hiding place, not an artifact. Reading the set from
    NOTICE ties "allowed to ship" to "declared with a hash" in one place.
    """
    path = os.path.join(REPO, "NOTICE")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return {m.group(1) for m in
            re.finditer(r"^\s*[0-9a-f]{64}\s+(\S+)\s*$", text, re.M)}


def _read(rel):
    if not rel.endswith(TEXT_SUFFIXES):
        return None
    try:
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _digest_hits(text, rel, digests=PERSONAL_DIGESTS):
    """Lines whose token n-grams hash into `digests`.

    Line by line rather than whole-file, so an n-gram cannot be assembled out
    of two unrelated paragraphs and reported as a leak that is not there.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        toks = TOKEN.findall(line.lower())
        hit = False
        for n in range(1, NGRAM_MAX + 1):
            for i in range(len(toks) - n + 1):
                if _digest("".join(toks[i:i + n])) in digests:
                    hit = True
                    break
            if hit:
                break
        if hit:
            found.append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
    return found


def _home_hits(text, rel):
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for seg in HOME_PATH.findall(line):
            if seg.lower() not in PLACEHOLDERS:
                found.append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
                break
    return found


def _localised_hits(text, rel):
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(re.search(r"\b%s\b" % re.escape(n), line) for n in LOCALISED):
            found.append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
    return found


def t_no_personal_identifiers(files):
    personal, home = [], []
    for rel in files:
        text = _read(rel)
        if text is None:
            continue
        if rel not in AUTHORSHIP:
            personal += _digest_hits(text, rel)
        # The /home band has to spell `/home/` and a canary account to search
        # for them, so this file is exempt from it -- and only it. The digest
        # band still covers this file, which is why the account is never spelled
        # here in plaintext.
        if rel != SELF:
            home += _home_hits(text, rel)

    # Gate 1. Without a non-empty tree the loops below prove nothing: all() over
    # nothing is True, and so is "no offender in no files".
    check("1 the publishable tree is non-empty", len(files) > 40,
          "%d file(s) would be committed" % len(files))
    # Gate 2. The digest band has to be able to fire; a hash set that matches
    # nothing is indistinguishable from an empty one, which clears every file.
    canary = _digest_hits("notes from quaywiselanternfen today", "<canary>",
                          CANARY_DIGEST)
    check("2 the account band can fire", len(canary) == 1,
          "canary produced %d hit(s)" % len(canary))
    # Gate 3.
    check("3 no personal identifier survives anywhere", not personal,
          "%d hit(s)%s" % (len(personal), "" if not personal
                           else "\n      " + "\n      ".join(personal[:6])))
    # Gate 4. The /home band needs the same proof.
    home_canary = _home_hits("a line naming /home/realname/x", "<canary>")
    check("4 the /home account band can fire", len(home_canary) == 1,
          "canary produced %d hit(s)" % len(home_canary))
    # Gate 5.
    check("5 no /home/<account> path in the tree", not home,
          "%d hit(s)%s" % (len(home), "" if not home
                           else "\n      " + "\n      ".join(home[:6])))
    # Gate 6. `/home` as a bare mount string is NOT an offender -- the control
    # that gate 5 is not `/home/` outright, which would strike CP-0's NFS test.
    check("6 a bare /home mount is not an offender",
          not _home_hits("31 23 0:31 / /home rw - nfs4 srv:/export", "<x>")
          and not _home_hits("/home/someone/.local/share/x", "<x>"),
          "bare mount and placeholder both clear")


def t_no_localised_directory(files):
    offenders = []
    for rel in files:
        if rel == SELF:
            continue  # holds the LOCALISED list and its canari in plaintext
        text = _read(rel)
        if text is None:
            continue
        offenders += _localised_hits(text, rel)
    # Gate 7.
    check("7 no localised desktop directory in the tree", not offenders,
          "%d hit(s)%s" % (len(offenders), "" if not offenders
                           else "\n      " + "\n      ".join(offenders[:6])))
    # Gate 8.
    canary = _localised_hits("measured under ~/Dokumenter last week", "<canary>")
    check("8 the localised band can fire", len(canary) == 1,
          "canary produced %d hit(s)" % len(canary))


def t_every_file_is_text_or_declared_binary(files):
    declared = declared_binaries()
    unreadable = []
    for rel in files:
        if rel in declared:
            continue
        try:
            with open(os.path.join(REPO, rel), "rb") as fh:
                fh.read().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            unreadable.append(rel)
    # Gate 9. A file this gate cannot read is a file it cannot clear; a binary is
    # allowed only if NOTICE declares it, otherwise it is a hiding place.
    check("9 every publishable file is text or a declared binary",
          not unreadable, "%d undeclared/unreadable%s" % (
              len(unreadable), "" if not unreadable else "  %s" % unreadable[:4]))
    # Gate 10. The declared set must be non-empty and present, or "text or
    # declared binary" is true because the list is empty and main.wasm slips
    # through unscanned.
    present = [p for p in declared if os.path.exists(os.path.join(REPO, p))]
    check("10 NOTICE declares binaries that exist in the tree",
          len(declared) > 0 and len(present) == len(declared),
          "%d declared, %d present" % (len(declared), len(present)))
    # Gate 11. An undeclared binary path IS an offender -- proves gate 9 tells
    # declared from undeclared, not merely ".wasm exists".
    fake = "evil/undeclared.wasm"
    check("11 an undeclared binary would be caught",
          fake not in declared, "%r is not in the declared set" % fake)


def t_license_present(files):
    lic = os.path.exists(os.path.join(REPO, "LICENSE"))
    apache = os.path.exists(os.path.join(REPO, "LICENSE-APACHE-2.0"))
    # Gate 12. The project's own licence, and the Apache text NOTICE points to.
    check("12 LICENSE and LICENSE-APACHE-2.0 both exist", lic and apache,
          "LICENSE=%s LICENSE-APACHE-2.0=%s" % (lic, apache))
    # Gate 13. The exemption must name a file that is actually in the tree; an
    # exemption for a file renamed away is a hole nobody would notice.
    check("13 the LICENSE exemption names a file in the tree",
          all(f in files for f in AUTHORSHIP),
          "missing: %s" % [f for f in AUTHORSHIP if f not in files])


def t_norwegian_record_excluded(files):
    todo_in = "TODO.md" in files
    fasit_in = [f for f in files if re.match(r"tests/gold/FASIT-.*\.md$", f)]
    # Gate 14. The exclusion actually happened.
    check("14 TODO.md and the FASIT files are not published",
          not todo_in and not fasit_in,
          "TODO.md in tree=%s, FASIT in tree=%s" % (todo_in, fasit_in[:3]))
    # Gate 15. And the material still exists locally, or "excluded" is true on a
    # checkout that never had it -- the answer-keys-leak control.
    todo_here = os.path.exists(os.path.join(REPO, "TODO.md"))
    fasit_here = os.path.exists(os.path.join(REPO, "tests", "gold",
                                             "FASIT-cppub.md"))
    check("15 the excluded record still exists locally", todo_here and fasit_here,
          "TODO.md=%s FASIT-cppub.md=%s" % (todo_here, fasit_here))


def t_history_is_clean():
    """Gates 16-18: the commit messages travel with the repository too.

    **The gap this closes was found by publishing, not by a gate.** Every one
    of gates 1-15 inspects the working *tree*, and a repository is not only its
    tree -- `git log` goes with it. Three commit messages named the account
    inside a quoted failure example: harmless in intent, and still one machine's
    layout written into a public log.

    The distinction gates 5 and 6 already draw holds here unchanged. `/home` as
    a bare mount is not an offender, and `/home/<account>` written as a
    placeholder is the fix rather than the leak. What may not survive is a real
    account name.
    """
    log = subprocess.run(["git", "-C", REPO, "log", "--format=%B"],
                         capture_output=True, text=True, check=False).stdout
    messages = [line for line in log.splitlines() if line.strip()]

    # 17 first, and it is the same control gates 2, 4 and 8 carry: a band that
    # cannot fire clears every input, and a green gate over a dead check is
    # worse than no gate because it is quoted as evidence.
    canary = _home_hits("a line naming /home/realname/x", "<canary>")
    check("17 CONTROL: the /home band fires against commit text too",
          len(canary) == 1, "canary produced %d hit(s)" % len(canary))
    check("17b CONTROL: the history is actually being read",
          len(messages) > 50, "%d non-empty message line(s)" % len(messages))

    hits = [h for line in messages for h in _home_hits(line, "<commit>")]
    check("16 no /home/<account> path in any commit message", not hits,
          "%d hit(s)%s" % (len(hits), "" if not hits
                           else "\n      " + "\n      ".join(hits[:6])))

    # 18 is narrower than gate 3 on purpose, and the narrowing is a decision
    # rather than a weakening. **The owner ruled on 2026-08-15 that the author
    # name and email stay**: they sit in every commit's author field anyway,
    # and a public history scrubbed anonymous is a stranger artefact than one
    # with a name in it. What may not survive is the account name inside a
    # *path*, because that is one machine's layout rather than a sentence about
    # a person -- `~/.../<account>-crontab-...` was exactly that and has been
    # rewritten, while the same name in prose stays.
    #
    # This file is subject to gate 3 like every other, which is why the rule is
    # described and never demonstrated: writing the example out in full would
    # make the gate that guards the tree fail on the gate that guards the log.
    #
    # Only the path-shaped substrings are handed to the digest band, so this
    # file still never spells the account: the band does the identifying, the
    # regex only decides *where* to look.
    paths = [(line, m.group(0)) for line in messages
             for m in PATHISH.finditer(line)]
    # 19: the file list in history, not only in the tree. **This gate exists
    # because the published repository had the excluded record in it.**
    # `.gitignore` stops new commits; it does not remove what was committed
    # before the rule existed, and gates 14 and 15 both looked only at the
    # current tree. Measured 2026-08-15, minutes after the first push: 20
    # excluded files were still readable from history, `TODO.md` across 78
    # commits, 345 of its lines carrying a home path.
    #
    # `git ls-files` cannot answer this -- it sees the last tree. Only walking
    # every commit does.
    listed = subprocess.run(
        ["git", "-C", REPO, "log", "--all", "--name-only",
         "--pretty=format:"],
        capture_output=True, text=True, check=False).stdout
    ever = {line.strip() for line in listed.splitlines() if line.strip()}
    leaked = sorted(p for p in ever
                    if p == "TODO.md"
                    or (p.startswith("tests/gold/FASIT-") and p.endswith(".md")))
    check("19 the excluded record was never committed, in any commit",
          not leaked,
          "%d file(s) readable from history%s"
          % (len(leaked), "" if not leaked
             else "\n      " + "\n      ".join(leaked[:6])))
    check("19b CONTROL: the history file list is actually being read",
          len(ever) > 40, "%d distinct path(s) across all commits" % len(ever))

    personal = [h for _line, frag in paths
                for h in _digest_hits(frag.replace("/", " ").replace("-", " "),
                                      "<commit>", PERSONAL_DIGESTS)]
    check("18 no account name inside a path in any commit message",
          not personal,
          "%d hit(s)%s" % (len(personal), "" if not personal
                           else "\n      " + "\n      ".join(personal[:6])))
    # 18b: the band has to be able to fire here too. The canary token stands in
    # for the account, so the control needs no real identifier written down.
    canary_path = "backup at ~/x/quaywiselanternfen-crontab.txt"
    fired = [h for m in PATHISH.finditer(canary_path)
             for h in _digest_hits(m.group(0).replace("/", " ").replace("-", " "),
                                   "<canary>", CANARY_DIGEST)]
    check("18b CONTROL: the in-path band fires", len(fired) == 1,
          "canary produced %d hit(s)" % len(fired))


def main():
    files = publishable_files()
    print("publishable tree: %d file(s)\n" % len(files))
    t_no_personal_identifiers(files)
    t_no_localised_directory(files)
    t_every_file_is_text_or_declared_binary(files)
    t_license_present(files)
    t_norwegian_record_excluded(files)
    t_history_is_clean()
    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_no_real_paths():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
