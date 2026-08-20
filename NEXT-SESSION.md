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

## Steps

1. `contrib/sharp-stub/package.json` -> `{"name":"sharp","version":"0.32.6","main":"index.js","license":"Apache-2.0"}`
2. `contrib/sharp-stub/index.js` -> a function that **throws**. Not a no-op:
   a silent `undefined` turns "someone added image input" into a wrong answer
   instead of an error. Message should name why it is stubbed.
3. `package.json`: add `"overrides": {"sharp": "file:contrib/sharp-stub"}`
4. `rm -rf node_modules package-lock.json && npm install` (regenerates the lock)
5. Re-measure the vector against the pre-override values before trusting it.

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
