#!/usr/bin/env python3
"""Mutation harness for the npm surface gates.

The report is `reports/npm-audit-2026-08-20.md`.

**The mutations here damage the lockfile, not the gates.** That is deliberate,
and it is the only direction that proves anything: weakening an expectation
makes a gate *pass*, which the harness would report as a survivor without
telling you whether the gate was ever able to refuse. Damaging the data asks
the question the gates exist for -- would anyone notice.

The one exception is the control pair at the end, where the *data* is fine and
the reading of it is broken, because gate 6 exists precisely to catch a
lockfile read as empty.

Run:
    python3 tests/mutate_npm_surface.py
"""
from __future__ import annotations

import sys

from mutate import run

MUTATIONS = [
    # -- a dependency arrives ---------------------------------------------
    ("a second dependency appears in the manifest",
     "package.json",
     '    "@xenova/transformers": "^2.17.2"',
     '    "@xenova/transformers": "^2.17.2",\n    "left-pad": "^1.3.0"',
     "1  one declared dependency, and it is the one CP-9 chose"),

    # -- the tree grows ---------------------------------------------------
    #
    # One entry, plausible in every way except that nobody added it. Aimed at
    # the count, which is the cheapest thing here and the easiest to let rot.
    ("a package is added to the lockfile without the manifest changing",
     "package-lock.json",
     '    "node_modules/b4a": {',
     '    "node_modules/zzz-smuggled": {\n'
     '      "version": "1.0.0",\n'
     '      "resolved": "https://registry.npmjs.org/zzz/-/zzz-1.0.0.tgz",\n'
     '      "integrity": "sha512-AAAA",\n'
     '      "license": "MIT"\n'
     '    },\n'
     '    "node_modules/b4a": {',
     "2  the tree has not grown or shrunk unnoticed"),

    # -- identity, which a count cannot give -------------------------------
    #
    # Found by an independent recheck, not by writing this file: swapping one
    # package for a typosquat kept the total at 80 and the licence permissive,
    # and **all seven gates stayed green**. A count answers "did the tree
    # grow", never "is it the same tree" -- and the supply-chain change that
    # matters most is precisely the one that keeps the total steady.
    ("a package is swapped for a typosquat, leaving the count unchanged",
     "package-lock.json",
     '    "node_modules/is-arrayish": {',
     '    "node_modules/evil-typosquat": {',
     "2b every package is one we have already seen, by name"),

    # -- reproducibility goes away ----------------------------------------
    #
    # A `resolved` URL with no hash: `npm ci` fetches it and cannot tell if
    # what came back is what was locked.
    ("a package loses its integrity hash, so npm ci stops repeating",
     "package-lock.json",
     '      "integrity": "sha512-aiqre1Nr0B/6DgE2N5vwTc+2/oQZ4Wh1t4NznYY4E00y8LCt6NqdRv81so00oo27D8MVKTpUa/MwUUtBLXCoDw==",',
     '      "_integrity_removed": "mutated",',
     "3  every fetched package is integrity-hashed, so npm ci repeats"),

    # -- code at install time ---------------------------------------------
    #
    # This is the highest-consequence change npm can make quietly: a package
    # that did not run code at install now does.
    ("a third package gains an install script",
     "package-lock.json",
     '    "node_modules/tar-fs": {\n      "version": "3.1.3",',
     '    "node_modules/tar-fs": {\n      "hasInstallScript": true,\n'
     '      "version": "3.1.3",',
     "4  only the known packages may run code at install time"),

    # -- a licence obligation arrives -------------------------------------
    #
    # Copyleft beside morpho's Apache-2.0 should be a decision, not a
    # surprise found later by someone reading NOTICE.
    ("a package arrives under a copyleft licence",
     "package-lock.json",
     '      "license": "MIT",\n      "engines": {\n        "node": ">=18"\n      }',
     '      "license": "GPL-3.0",\n      "engines": {\n        "node": ">=18"\n      }',
     "5  no licence arrives that we have not already accepted"),

    # -- the control, and why it is here ----------------------------------
    #
    # Every gate above is a comparison against a set or a count, and all of
    # them pass against an empty `packages`. Without gate 6 this whole file is
    # green for a lockfile that says nothing -- CP-7B R8, one ecosystem over.
    ("the lockfile is read as empty, so every set comparison passes",
     "tests/test_npm_surface.py",
     "    packages = {key: value for key, value in lock[\"packages\"].items() if key}",
     "    packages = {}  # mutated: nothing to compare against",
     "6  CONTROL: the lockfile is not empty and does carry licences"),

    # A v1 lockfile has no `packages` key at all, so `.get` would give `{}`
    # and every gate would read an empty tree. Gate 7 names the assumption.
    ("the lockfile claims a version whose shape these gates cannot read",
     "package-lock.json",
     '  "lockfileVersion": 3,',
     '  "lockfileVersion": 1,',
     "7  the lockfile is the version these gates can actually read"),
]


if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_npm_surface.py",
                 prefix="mhg-mut-npm-", timeout=300))
