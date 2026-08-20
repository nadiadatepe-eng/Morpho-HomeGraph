# AGENTS.md — Morpho-HomeGraph

## Purpose

- Merge homegraph (corpus, index, graph, search) with morphofiles (morpho's engine as the
  view layer), rebuilt from the ground up **without Graphify and without `code-review-graph`**.
- Metadata over everything, content where you point.

## Ownership

- `morpho_homegraph/` owns the implementation and the `morphofiles-graph` command.
- `tests/` owns the gates, the gold answers in `tests/gold/`, and the mutation harnesses.
- `tools/` owns the measurement scripts (M-1 … M-6, equivalence, eval).
- `reports/` owns harvest notes (ideas read out of external projects, each with what was
  taken, what was deliberately left, and the licence of the source) and audits of our own
  dependency surface. Candidates and findings, not decisions — a report never promotes
  itself into a checkpoint.
- `docs/` owns the archify JSON sources; the rendered HTML lives outside the repo, in the
  owner's local documents directory.
- `view/` and `NOTICE` carry morpho's Apache-2.0 obligation.
- `TODO.md` owns checkpoints, locked decisions, open threads, and measured results. It is
  the source; the global memory file is only a pointer.

## Local Contracts

- **No remote at all.** Not "unpushed" — not connected. Owner's decision 2026-08-04: private
  first, public last. `CP-PUB` gates real paths *before* visibility changes.
- Mark work done with a commit sha or a measured number. "Done" without evidence gets
  quoted onward as fact.
- Write the gold answer from the specification before the code, so the specification is in
  the history first.
- The service is the only writer. The guard is `fcntl.flock` per session, held for the
  process lifetime, enforced by the store rather than by CLI policy. `lock.py` is not
  borrowed (locked decision #12).
- Borrowed code carries a `SOURCE_SHA256` that a gate re-hashes, not a sentence of credit.
  A missing source file makes the gate write `SKIPPED`, which does not count as passing.
- A later checkpoint can make an earlier gate always-true. Re-check old gates when adding
  a layer, and revise mutation needles rather than trusting them.
- Needles written with escaped quotes are invisible to the condition detector. Use single
  quotes.
- The npm surface is gated by `tests/test_npm_surface.py` against the shape of
  `package-lock.json`, not against `npm audit` — an answer that changes with the network
  makes a gate people learn to ignore. A new dependency, a new licence or a new install
  script must be a decision.
- A stored hash carries `hash_source`: `compared` came from a real comparison between two
  passes, `backfilled` from reading the file with no comparison behind it. The two are never
  merged, and `content_hash` and `hash_source` are null together or not at all.
- A schema change reaches a live store only through `ADDED_COLUMNS`; `CREATE TABLE IF NOT
  EXISTS` is a no-op against a file that already exists. A read-only open never migrates, so
  readers must degrade and say why rather than raise.

## Work Guidance

- One checkpoint at a time, `TODO.md` updated at the checkpoint rather than at the end.
- Run the mutation harness for a checkpoint, not just its tests. A green gate that no
  mutation can turn red is decoration.
- Open threads in `TODO.md` are decisions for the owner, not backlog to clear unasked.

## Verification

- `uvx --with numpy --with pytest pytest tests/ -q` → 27 modules
- `python3 tests/mutate.py` — full sweep, also revises stale needles
- `python3 tests/mutate_cp<n>.py` — one checkpoint's harness
- `python3 tests/condition_coverage.py` — compound conditions nothing aims at
- `python3 tests/mutation_coverage.py` — checks nothing aims a mutation at
- `python3 tests/gate_coverage.py` — requirements an answer key names that no check
  reports; runs every module, so it is a tool you invoke rather than a suite gate
- `python3 tests/mutate_no_real_paths.py` — the CP-PUB path gate

## Child DOX Index

- `morpho_homegraph/` — package, CLI, layers L0–L5, service, backfill.
- `tests/` — gates, gold answers, mutation harnesses.
- `tools/` — measurement scripts.
- `docs/` — archify diagram sources.
- `reports/` — harvest notes from external projects, and audits of our own dependency
  surface (`npm-audit-2026-08-20.md`).
- `contrib/` — the systemd unit.
