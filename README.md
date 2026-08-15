# Morpho-HomeGraph

Metadata over everything, content where you point.

A local index of a home directory that answers questions about your own files:
what is here, what changed, what links to what, and where a phrase appears. It
runs on one machine, stores everything in SQLite, and has no network side.

    morphofiles-graph scan                  # catalogue the home area (L0)
    morphofiles-graph add <dir>             # register a project
    morphofiles-graph update <id>           # build content, graph, index
    morphofiles-graph search "<words>"      # lexical, or --semantic / --fused
    morphofiles-graph view <id>             # a standalone graph page
    morphofiles-graph status [<id>]         # what each layer holds, and its age

## The layers

| | |
|---|---|
| **L0** catalogue | every path in the home area, shared between projects |
| **L1** journal | what moved between two passes: added, changed, touched, ... |
| **L2** content | the text of what a project's scope selected |
| **L3** graph | links between files, stated or derived, never guessed |
| **L4** search | FTS5 lexical, optional vectors, and an opt-in rank fusion |
| **L5** view | morpho's force-layout engine, unchanged, rendering L3 |

`status` reports the age and coverage of each layer, because an index that has
been half-built must not look finished.

## Requirements

Python 3.12+, and Node only if you want the semantic layer. `numpy` is the sole
Python dependency. Everything else is the standard library, SQLite and FTS5
included.

    pip install -e .

## Running the checks

    uvx --with numpy --with pytest pytest tests/ -q    # 24 modules of gates
    python3 tests/mutate_cp<n>.py                      # one checkpoint's needles
    bash tools/sweep_all.sh                            # every harness, resumable
    python3 tests/condition_coverage.py                # compound conditions

The gates are written before the code they grade, and each one has mutations
aimed at it: a green gate that no mutation can turn red is decoration. The full
sweep reports survivors, crashes and misattributions separately, because those
are three different problems.

## Why it looks like this

`DECISIONS.md` carries the design record: the decisions that would otherwise
look arbitrary, the ones a measurement reversed, and the limits that are known
rather than discovered. It also explains the test shape — answer keys written
before the code, mutations aimed at every gate, and why a surviving mutation
has three possible causes.

## Credit

`NOTICE` is the full list, and it is not only a licence formality: the
force-layout engine is morpho's, copied unchanged with a sha256 per file so
that "unchanged" is a check rather than a claim; rank fusion is Cormack, Clarke
and Buettcher (SIGIR 2009); the embedding model is a sentence-transformers
model used through its transformers.js conversion. Three test-harness files
come from a predecessor project by the same author and say so in their own
docstrings, with source commits.

## Licence

MIT, in `LICENSE`. The vendored engine is Apache 2.0, in `LICENSE-APACHE-2.0`.
