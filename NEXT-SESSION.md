# NEXT: apply the sharp override (approved by the owner 2026-08-20)

State at handoff: `main` = `f89f8d1`, clean, level with origin. Suite 27 passed,
npm gate 8/8, dox-gates exit 0.

## The decision

The owner approved adopting the `sharp` override. Evidence it rests on, measured
twice on independent trees (`reports/npm-audit-2026-08-20.md`):

- `sharp` is 17 MB of image processing on the **execution path** and this
  repository embeds text only. It is the **one advisory that actually loads**
  (4 libvips CVEs), and the only install-time network fetch the lockfile does
  not describe.
- Stubbed out: **bit-identical 384-dim vectors**, worst elementwise difference
  **0.0** across 4 inputs (English, Norwegian, a third sentence, empty string),
  two trees, two stubs. `protobufjs` never loads either way.
- Tree goes **68 -> 15 directories, 252 MB -> 229 MB**.

## Steps — DRY-RUN 2026-08-20, so these are measured, not predicted

The first version of this plan was written from memory and **was wrong in a way
that would have cost the next session an hour**. It was dry-run in a throwaway
clone; what follows is what actually happened.

1. `contrib/sharp-stub/package.json` ->
   `{"name":"sharp","version":"0.32.6","main":"index.js","license":"Apache-2.0"}`
2. `contrib/sharp-stub/index.js` -> a function that **throws**. Not a no-op: a
   silent `undefined` turns "someone added image input" into a wrong answer
   instead of an error.
3. **The override needs an ABSOLUTE path.** This is the trap:

       "overrides": { "sharp": "file:/absolute/path/to/contrib/sharp-stub" }

   Three other spellings were tried and **all three leave a broken symlink**
   that npm reports as a clean `rc=0` install:

   | spelling | result |
   |---|---|
   | `file:contrib/sharp-stub` | `sharp -> @xenova/transformers/contrib/sharp-stub` — **broken** |
   | `file:../../contrib/sharp-stub` | `sharp -> contrib/sharp-stub` — **broken** |
   | `file:contrib/...` + `workspaces` | install fails, rc=1 |
   | **absolute** `file:/…/contrib/sharp-stub` | `sharp -> ../contrib/sharp-stub` — **resolves** |

   npm resolves a `file:` override relative to the **dependent**, not the
   project root. The failure is silent until the model loads, and then it is
   `ERR_MODULE_NOT_FOUND: Cannot find package 'sharp' imported from
   .../@xenova/transformers/src/utils/image.js`. **Always `test -e
   node_modules/sharp` after installing** — a green `npm install` proves nothing.

   The absolute path does **not** leak into the lockfile: it is stored as
   `"resolved": "contrib/sharp-stub", "link": true`. Check `package.json`
   before committing, though, since that keeps the literal string.
4. `rm -rf node_modules package-lock.json && npm install`
5. Compare against the baseline hash below. **In the dry run it MATCHED**, so
   the override is behaviour-preserving; that is a result to reproduce, not to
   assume.

## What the gates actually do — six red, not two

The earlier draft said "gates 2 and 2b will go red". **Measured: six of eight
go red, and four needles rot.** Do not treat this as breakage; it is the
ratchet, and every line of it needs a decision:

    FAIL 2   26 entries, expected 80        <- re-baseline ENTRIES
    FAIL 2b  name set changed               <- re-baseline PACKAGES
    FAIL 3   node_modules/sharp has no integrity hash
    FAIL 4   node_modules/sharp "has an install script"
    FAIL 5   licence '?' (a link entry carries none)
    FAIL 6   CONTROL: 26 packages, 25 licensed

Gates 3, 4, 5 and 6 fire because a `link: true` entry is a **different kind of
thing** from a fetched package: no integrity, no licence, no registry. The
honest fix is to teach those four gates that a linked local override is exempt
**by name**, and to say so at the line — not to widen the allowlists, which
would also excuse a real unhashed dependency. Gate 6's denominator needs the
same care: `25 licensed` is correct once one entry is a link.

Rotted needles (`mutate_npm_surface.py`), all four reporting `needle missing`:
`is-arrayish` swap, the sha512 string, `tar-fs` version, the `engines` block.
Re-point them at strings the new lock actually contains. **Do not delete them**
— the typosquat needle in particular is the one that caught a real hole.

## The baseline is already captured — compare, do not re-derive

Taken **before** the override, with `sharp` still real:

    ~/.claude/handoff/sharp-baseline.json   4 texts x 384 dims
    sha256 of the vectors: 07105d3c71f9ae515f7260201b412b08f2a1563de48e27f62d30fefef4daf0c3
    probe: ~/.claude/handoff/base.mjs

After the override, re-run the probe and compare. **The sha256 must match
exactly.** Anything else means the override changed retrieval input, and then
it does not go in, whatever the size saving:

    MHG_NODE_MODULES=$PWD/node_modules \
    MHG_MODELS=$HOME/.local/share/morpho-homegraph/models \
    MHG_MODEL=Xenova/paraphrase-multilingual-MiniLM-L12-v2 \
    node ~/.claude/handoff/base.mjs > /tmp/after.json
    python3 -c "import json,hashlib;d=json.load(open('/tmp/after.json'));print(hashlib.sha256(json.dumps(d['vectors'],sort_keys=True).encode()).hexdigest())"

The empty-string case is the fourth text on purpose: it takes a different path
through the tokenizer and is where a single-input result could have been luck.

## THE TRAP, written down because it is easy to miss

`tests/test_npm_surface.py` gates **2** (`ENTRIES = 80`) and **2b**
(`PACKAGES`, the name set) **will both go red** — that is them working. They
are the ratchet, so:

- Re-baseline both to the new tree, and put **the reason in the commit
  message**, exactly as `test_cp16_baseline.py` demands for its own ratchet.
- Do **not** quietly edit the constants. A number that moves silently is the
  thing those gates exist to prevent.
- `tests/mutate_npm_surface.py` needles reference concrete lockfile strings
  (`node_modules/is-arrayish`, a sha512, `node_modules/tar-fs` version, an
  `engines` block). Several will rot when the lock is regenerated — the sweep
  reports them as SKIP/needle-missing. Re-point them; do not delete them.

## Verify before committing

    python3 tests/test_npm_surface.py          # expect red first, then 8/8
    python3 tests/mutate_npm_surface.py        # expect 8 killed, 0 survivors
    uvx --with numpy --with pytest pytest tests/ -q
    bash ~/.claude/tools/dox-gates.sh

Then update `reports/npm-audit-2026-08-20.md` (it currently says the override
is an open thread), `TODO.md`, and `~/AGENTS.md` (its Morpho entry calls the
override an open thread for the owner). Push.

## Also still open, unrelated

`~/.claude/TODO.md` line 18 notes the GitHub repo description is still missing
(the token had no scope for it).
