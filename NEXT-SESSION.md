# NEXT: the sharp override is DONE — do not redo it

State: `main` = `37474a9`, clean, pushed. Suite 27 passed, npm gate **11/11**,
mutation 10 killed / 0 survivors, path gate 22/22 + 11 killed, dox-gates rc=0.

## What happened, in case a stale plan says otherwise

Adopted 2026-08-20. Tree 68 → 15 directories, 252 → 229 MB. The probe returns
sha256 `07105d3c…4daf0c3` — **exact match** against the baseline captured before
the change, worst elementwise difference 0.0.

**The handoff plan said to use an absolute `file:` path. Do not.** It resolves,
but it writes a real home path into a published file, which CP-PUB forbids and
`tests/test_no_real_paths.py` would have caught at commit time. The plan had
only tested two levels up; **three** levels resolves and is portable:

    "overrides": { "sharp": "file:../../../contrib/sharp-stub" }

npm resolves a `file:` override relative to the **dependent**, not the project
root. Shorter spellings leave a broken symlink that installs `rc=0`; the failure
surfaces later as `ERR_MODULE_NOT_FOUND` when the model loads. Gate 2c pins the
string, gate 2d checks the filesystem.

Everything else is in `reports/npm-audit-2026-08-20.md`, `DECISIONS.md`, and
`TODO.md` (open thread 8, closed).

## Actually open

- **Two commits are local and NOT pushed** (`37474a9`, `5254939`). Nadi's rule
  2026-08-20: **every push to a public remote needs its own approval, asked
  immediately before it.** An earlier approval, however recent, does not carry.
  Ask, name what would become public, and wait.
- `~/.claude/TODO.md` line 18: the GitHub repo description is still missing —
  the token had no scope for it. Needs a token with `repo`, or Nadi sets it in
  the web UI.
- PolyBrains has its own `NEXT-SESSION.md` and is untouched by this: P13 is
  built and pre-registered but **not run**, and `p13_sep004` must be replaced
  with 0.02 and smoke-tested before launching.

## Maintenance done 2026-08-20

`update` + `embed` after five days: **semantic 39 % → 100 %** (1406/1406
chunks), lexical 115/115, L1 116/116.

**`l3 0 edges` was measured, not fixed, and the layer is healthy.** The corpus
has four local inline links in total, all of them docstring or fixture examples
pointing at files that do not exist. This repository cross-references in prose,
so L3 correctly refuses to assert edges to nodes it does not have. Do not make
it guess. Full reasoning in `TODO.md` (local, gitignored).
