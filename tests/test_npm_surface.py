#!/usr/bin/env python3
"""The npm install surface: does anything here notice when it changes?

Written 2026-08-20, from `reports/npm-audit-2026-08-20.md`. That audit found
that **none of the 25 test modules read `package-lock.json`** -- a dependency
could be added, a licence could change, or an install script could appear, and
every gate in this repository would stay green. This file is that hole closed.

It is deliberately not a vulnerability scanner. `npm audit` needs the network
and its answer changes underneath a fixed lockfile, so a gate built on it
would go red on a day nobody touched this repository -- and a gate that goes
red for reasons outside the change is a gate people learn to ignore. What is
gated here is the **shape** of the dependency tree, which only changes when
someone changes it:

  * one declared dependency, and its name,
  * every entry integrity-hashed, so `npm ci` is reproducible,
  * no copyleft licence arriving unnoticed beside morpho's Apache-2.0,
  * the set of packages allowed to run code at install time.

The counts are written down rather than computed, because a gate that derives
its expectation from the file it is checking cannot fail.

Run:
    python3 tests/test_npm_surface.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report import reporter  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results, check = reporter(58)

# Measured 2026-08-20 and written here, not counted from the lockfile.
DECLARED = {"@xenova/transformers": "^2.17.2"}

# Re-baselined 2026-08-20 when the `sharp` override was adopted: 80 -> 27.
# The reason is in the commit message, as this ratchet requires. `sharp` was
# 17 MB of image processing on the execution path of a repository that embeds
# text only, the one advisory in the tree that actually loads (four libvips
# CVEs), and the only install-time network fetch the lockfile does not
# describe. Replacing it with `contrib/sharp-stub` leaves the 384-dim vectors
# **bit-identical** -- worst elementwise difference 0.0 across four inputs,
# sha256 07105d3c...4daf0c3 unchanged -- and takes the tree 252 MB -> 229 MB.
# See `reports/npm-audit-2026-08-20.md`.
#
# 26 = 25 installed packages + `contrib/sharp-stub`, which is a **local source
# directory**, not something fetched. (The root entry is filtered out below, as
# it always was.) That distinction is what gates 3, 4, 5 and 6 below now have
# to make.
ENTRIES = 26

# The linked local override. It is exempted **by name** in the gates below,
# never by widening an allowlist: a `link: true` entry is a different kind of
# thing from a fetched package (no registry, no integrity, no licence field on
# the link itself), and a rule broad enough to excuse it would also excuse a
# real unhashed dependency arriving from the network.
LINK = "node_modules/sharp"
LINK_SOURCE = "contrib/sharp-stub"
OVERRIDES = {"sharp": "file:../../../contrib/sharp-stub"}

# **The names, not only the count.** An independent recheck swapped one
# package for `evil-typosquat` -- same 80 entries, same licence, same
# integrity shape -- and all seven gates stayed green. A count answers "did
# the tree grow", never "is it the same tree", and a supply-chain change that
# matters most is precisely the one that keeps the total steady.
#
# Written out rather than derived, for the reason gate 2 already gives: an
# expectation computed from the file it checks cannot fail.
PACKAGES = frozenset("""
    @huggingface/jinja @protobufjs/aspromise @protobufjs/base64
    @protobufjs/codegen @protobufjs/eventemitter @protobufjs/fetch
    @protobufjs/float @protobufjs/inquire @protobufjs/path
    @protobufjs/pool @protobufjs/utf8 @types/long @types/node
    @xenova/transformers flatbuffers guid-typescript long onnx-proto
    onnxruntime-common onnxruntime-node onnxruntime-web platform
    protobufjs sharp undici-types
""".split())

# The packages that may run code during `npm install`. `sharp` used to be here
# too, and the libvips download it did -- the one fetch `npm ci` did not
# describe -- is precisely why it is now a link instead.
INSTALL_SCRIPTS = {"node_modules/protobufjs"}

# Permissive only. This repository already carries one attribution obligation
# (morpho, Apache-2.0, in NOTICE); a second arriving through npm should be a
# decision, not a surprise.
ALLOWED = {
    "MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC",
    "(MIT OR WTFPL)", "(BSD-2-Clause OR MIT OR Apache-2.0)",
    # flatbuffers 1.12.0 says this and ships an Apache-2.0 LICENSE.txt.
    "SEE LICENSE IN LICENSE.txt",
}


def load():
    with open(os.path.join(REPO, "package.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    with open(os.path.join(REPO, "package-lock.json"), encoding="utf-8") as fh:
        lock = json.load(fh)
    return manifest, lock


def main() -> int:
    manifest, lock = load()
    packages = {key: value for key, value in lock["packages"].items() if key}

    check("1  one declared dependency, and it is the one CP-9 chose",
          manifest.get("dependencies") == DECLARED,
          "%s" % manifest.get("dependencies"))

    check("2  the tree has not grown or shrunk unnoticed",
          len(packages) == ENTRIES,
          "%d entries, expected %d" % (len(packages), ENTRIES))

    # 2b: identity, which the count cannot give. A swap keeps the total.
    #
    # `contrib/sharp-stub` is keyed by its **path**, not by `node_modules/…`,
    # so it is named separately rather than run through the prefix strip --
    # which would otherwise reduce it to the nonsense token `-stub` and make
    # this gate red for a reason that has nothing to do with drift.
    present = {key[len("node_modules/"):] for key in packages
               if key.startswith("node_modules/")}
    drift = sorted(present ^ PACKAGES)
    check("2b every package is one we have already seen, by name",
          not drift, "%s" % drift[:4])

    # 2b-source: and the only entry that is NOT under node_modules/ is the
    # override's source. Without this, a second local path could be added to
    # the lockfile and 2b would never look at it.
    outside = sorted(key for key in packages
                     if not key.startswith("node_modules/"))
    check("2b-src the only non-node_modules entry is the override's source",
          outside == [LINK_SOURCE], "%s" % outside)

    # 2c: the override is declared, and spelled the one way that works. Three
    # other `file:` spellings were tried and all three left a **broken**
    # symlink that npm reported as a clean rc=0 install; the failure surfaced
    # only later, as ERR_MODULE_NOT_FOUND when the model loaded. npm resolves
    # a `file:` override relative to the *dependent*, not the project root.
    # An absolute path also resolves, but would name a real home directory in
    # a published file, which CP-PUB forbids -- so the spelling is load-bearing
    # twice over and is gated rather than remembered.
    check("2c the sharp override is declared, and spelled the way that resolves",
          manifest.get("overrides") == OVERRIDES,
          "%s" % manifest.get("overrides"))

    # 2d: and the link actually points at something. A green `npm install`
    # proves nothing here; only the filesystem does. Skipped rather than
    # failed when node_modules is absent, because this file is also read in
    # trees that were never installed -- and a skip says so out loud.
    installed = os.path.join(REPO, "node_modules", "sharp")
    if os.path.lexists(installed):
        target = os.readlink(installed) if os.path.islink(installed) else "(not a link)"
        check("2d the linked override resolves on disk, not just in the lockfile",
              os.path.exists(installed),
              "node_modules/sharp -> %s" % target)
    else:
        check("2d the linked override resolves on disk, not just in the lockfile",
              True, "SKIPPED: node_modules absent, nothing installed to check")

    # Reproducibility. Without this `npm ci` is a fetch, not an install: an
    # entry with a `resolved` URL and no hash can come back different.
    # The link is exempt by name: it has a `resolved` (a local path) and no
    # integrity, because there is nothing fetched to hash. Everything else
    # still has to be hashed, including any future link that is not this one.
    unhashed = sorted(key for key, value in packages.items()
                      if value.get("resolved") and not value.get("integrity")
                      and not (key == LINK and value.get("link")))
    check("3  every fetched package is integrity-hashed, so npm ci repeats",
          unhashed == [], "%s" % unhashed[:4])

    scripted = {key for key, value in packages.items()
                if value.get("hasInstallScript")}
    check("4  only the known packages may run code at install time",
          scripted == INSTALL_SCRIPTS,
          "%s" % sorted(scripted ^ INSTALL_SCRIPTS))

    # The link entry carries no licence field -- there is no package metadata
    # on a symlink. Its licence lives on the source entry `contrib/sharp-stub`
    # (Apache-2.0), which is in `packages` and is checked like anything else,
    # so exempting the link loses no coverage.
    unknown = sorted({value.get("license", "?")
                      for key, value in packages.items()
                      if not (key == LINK and value.get("link"))}
                     - ALLOWED)
    check("5  no licence arrives that we have not already accepted",
          unknown == [], "%s" % unknown)

    # 6: the control. Gates 4 and 5 compare against a set, and a set built
    # from an empty tree matches nothing and passes everything. This is the
    # denominator, and CP-7B R8 is the reason it is here: an empty layer must
    # not be able to look finished.
    #
    # The denominator moved with the tree (80 -> 27) and needs the same care as
    # the numerator: exactly one entry, the link, is legitimately unlicensed,
    # so the bar is "all but one", written as such rather than loosened to a
    # smaller number that a second unlicensed package could also clear.
    licensed = [value for value in packages.values() if value.get("license")]
    links = [value for value in packages.values() if value.get("link")]
    check("6  CONTROL: the lockfile is not empty and does carry licences",
          len(packages) > 20 and len(licensed) == len(packages) - len(links),
          "%d packages, %d licensed, %d link(s)"
          % (len(packages), len(licensed), len(links)))

    # 7: lockfileVersion 3 is what `packages` above assumes. Version 1 has no
    # `packages` key at all, and every gate here would then read `{}` and pass.
    check("7  the lockfile is the version these gates can actually read",
          lock.get("lockfileVersion") == 3,
          "v%s" % lock.get("lockfileVersion"))

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_npm_surface():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
