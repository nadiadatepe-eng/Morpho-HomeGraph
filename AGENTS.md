# AGENTS.md — Morpho-HomeGraph

## Purpose

- Merge homegraph (corpus, index, graph, search) with morphofiles (morpho's engine as the
  view layer), rebuilt from the ground up **without Graphify and without `code-review-graph`**.
- Metadata over everything, content where you point.

## Ownership

- `morpho_homegraph/` owns the implementation and the `morphofiles-graph` command.
- `tests/` owns the gates, the gold answers in `tests/gold/`, and the mutation harnesses.
- `tools/` owns the measurement scripts (M-1 … M-6, equivalence, eval).
- `docs/` owns the archify JSON sources; the rendered HTML lives in `~/Dokumenter/`.
- `view/` and `NOTICE` carry morpho's Apache-2.0 obligation.
- `TODO.md` owns checkpoints, locked decisions, open threads, and measured results. It is
  the source; the global memory file is only a pointer.

## Local Contracts

- **No remote at all.** Not "unpushed" — not connected. Nadi's decision 2026-08-04: private
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

## Work Guidance

- One checkpoint at a time, `TODO.md` updated at the checkpoint rather than at the end.
- Run the mutation harness for a checkpoint, not just its tests. A green gate that no
  mutation can turn red is decoration.
- Open threads in `TODO.md` are decisions for Nadi, not backlog to clear unasked.

## Verification

- `uvx --with numpy --with pytest pytest tests/ -q` → 22 modules, 459 gates
- `python3 tests/mutate.py` — full sweep, also revises stale needles
- `python3 tests/mutate_cp<n>.py` — one checkpoint's harness
- `python3 tests/condition_coverage.py` — compound conditions nothing aims at
- `python3 tests/mutate_no_real_paths.py` — the CP-PUB path gate

## Child DOX Index

- `morpho_homegraph/` — package, CLI, layers L0–L5, service.
- `tests/` — gates, gold answers, mutation harnesses.
- `tools/` — measurement scripts.
- `docs/` — archify diagram sources.
- `contrib/` — the systemd unit.
