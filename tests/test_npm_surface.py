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
ENTRIES = 80

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
    @xenova/transformers b4a bare-events bare-fs bare-path bare-stream
    bare-url base64-js bl buffer chownr color color-convert color-name
    color-string decompress-response deep-extend detect-libc end-of-stream
    events-universal expand-template fast-fifo flatbuffers fs-constants
    github-from-package guid-typescript ieee754 inherits ini is-arrayish
    long mimic-response minimist mkdirp-classic napi-build-utils node-abi
    node-addon-api once onnx-proto onnxruntime-common onnxruntime-node
    onnxruntime-web platform prebuild-install
    prebuild-install/node_modules/tar-fs
    prebuild-install/node_modules/tar-stream protobufjs pump rc
    readable-stream safe-buffer semver sharp simple-concat simple-get
    simple-swizzle streamx string_decoder strip-json-comments tar-fs
    tar-stream teex text-decoder tunnel-agent undici-types util-deprecate
    wrappy
""".split())

# The two packages that may run code during `npm install`. `sharp` also
# downloads a prebuilt libvips from a URL that is not in the lockfile -- it is
# sha512-verified against hashes in sharp's own package.json, but it is the
# one fetch `npm ci` does not describe, so it is named here.
INSTALL_SCRIPTS = {"node_modules/sharp", "node_modules/protobufjs"}

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
    present = {key[len("node_modules/"):] for key in packages}
    drift = sorted(present ^ PACKAGES)
    check("2b every package is one we have already seen, by name",
          not drift, "%s" % drift[:4])

    # Reproducibility. Without this `npm ci` is a fetch, not an install: an
    # entry with a `resolved` URL and no hash can come back different.
    unhashed = sorted(key for key, value in packages.items()
                      if value.get("resolved") and not value.get("integrity"))
    check("3  every fetched package is integrity-hashed, so npm ci repeats",
          unhashed == [], "%s" % unhashed[:4])

    scripted = {key for key, value in packages.items()
                if value.get("hasInstallScript")}
    check("4  only the known packages may run code at install time",
          scripted == INSTALL_SCRIPTS,
          "%s" % sorted(scripted ^ INSTALL_SCRIPTS))

    unknown = sorted({value.get("license", "?") for value in packages.values()}
                     - ALLOWED)
    check("5  no licence arrives that we have not already accepted",
          unknown == [], "%s" % unknown)

    # 6: the control. Gates 4 and 5 compare against a set, and a set built
    # from an empty tree matches nothing and passes everything. This is the
    # denominator, and CP-7B R8 is the reason it is here: an empty layer must
    # not be able to look finished.
    licensed = [value for value in packages.values() if value.get("license")]
    check("6  CONTROL: the lockfile is not empty and does carry licences",
          len(packages) > 50 and len(licensed) > 50,
          "%d packages, %d licensed" % (len(packages), len(licensed)))

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
