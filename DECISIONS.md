# Design decisions

The working record for this project is a Norwegian document that stays out of
the published repository: it names one machine's layout on nearly every line.
This file is not a translation of it. It is the part a reader who has never
seen that document needs in order to understand why the code looks like this —
the decisions that would otherwise look arbitrary, and the ones that were
reversed.

Every number here was measured on the date given. A figure without a date is a
figure nobody re-ran.

## What this is not

No cloud storage, no login, no telemetry, no global install, no Electron, no
force-layout engine of our own. Those are not omissions; each was considered
and declined. If a need for one appears it gets written as a checkpoint with a
reason, not assumed into existence.

## The decisions that shape everything else

**Code is text, not structure.** No per-language parser to maintain. The
consequence is accepted rather than hidden: identifier splitting becomes
mandatory, because without it `getUserById` is one token and no search finds
`user`.

**Metadata everywhere, content only where you point.** The pruning sits between
L1 and L2. Cataloguing a home area is cheap and reading it is not, so the
catalogue covers everything and the expensive layers cover a scope you chose.

**A project is any folder you point at.** Not a heuristic that discovers
projects: user choice removes the discovery problem entirely, and a wrong guess
about what is a project is a wrong index nobody asked for.

**Overlapping scope selections: the innermost wins.** The same rule as CSS
specificity, which means it can be explained in one sentence in a settings
panel.

**Move, copy and delete are decided by `exists()` on both paths.** Mechanically
decidable, rather than a heuristic carrying a confidence number that no caller
knows how to act on.

**The service is the only writer, and the barrier is per session.** The lock is
taken at start-up and held for the process lifetime. It refuses rather than
queues, and the store enforces it — not the CLI, because a rule enforced by the
caller is a rule the next caller forgets. Measured over 6.8 days of real use
(2026-08-08 to 08-15): **2 refusals, one per 81 hours.** That number is the answer to "why not a
database server": one concurrent transaction every 81 hours is the entire prize
a server process would buy.

**History is snapshots, not versioned edges.** Versioned-edge machinery was
designed and then deleted from the design. Snapshots answer the same questions
with one mechanism instead of three.

## Decisions that were reversed by a measurement

These are here because a design record that only lists what survived is a
record of confidence, not of evidence.

**The static embedding matrix was chosen, then dropped.** Measured 2026-07-25: a static
multilingual matrix embedded 6,203 nodes in 9 s against 20 min for a transformer — 136×
faster, and "one rank or two behind" on quality. It was still declined: on a
local machine "delivers the right answer" outranks seconds, and the eval set
showed the gap was not one rank.

**Embedding moved out of `update` and became its own command.** First open was
measured 2026-08-03 at 220–317 s on a ~200-file tree, 3–5× over the threshold that would
have made `update` feel broken. Batching was tried and made it *slower*.

**The lock was going to be borrowed from the predecessor and was not.** Its
scheme had a window where two writers could both believe they held the guard.
The borrowed version is cited in the file that replaced it.

**An inherited number turned out to be wrong, and splitting it changed what we
knew.** The predecessor's notes claimed "+6 % recall" for identifier splitting.
Measured 2026-08-05 per *reason* for splitting: case boundaries gave **+47 %** on this
repo and **+30 %** on the predecessor's, while `snake_case` and `dotted.path`
gave **+0 %** — SQLite's `unicode61` already splits those. A blended average of
~15–23 % would have hidden which corpora the feature is worth paying for.

**Two checkpoints were reopened by a measured number, not by a review.** The
catalogue had no denylist (2026-08-04): 729,343 entries became **420,105**, and warm scan
time 5.13 s → 2.02 s. And `.gitignore` patterns were being computed and never
used — `gitignored()` had no caller outside `tests/`.

## How the tests are built, and why that shape

**The answer key is written before the code**, and committed first, so the
specification is in the history ahead of the implementation. This protects
against fitting the answer to the code. It does *not* protect against a wrong
measurement in the premise — that happened, and the correction is recorded in
the answer key rather than edited away.

**Every gate has mutations aimed at it.** A green gate that no mutation can
turn red is decoration. A surviving mutation has three possible causes and they
must not be conflated: a weak gate, an equivalent mutant, or **dead code**.
Only the first is a gate failure. One survivor in the full sweep turned out to
be a function with no production caller left.

**Half the mutations aim at negative controls, not at defects.** A guard that
fires on everything passes every positive check in the suite it guards.

**A mechanism called only from `tests/` is not a mechanism.** Ask of every
documented behaviour: what calls this outside the tests? Four such cases existed
in the predecessor project, all with green gates. Two more were found here.

**A fixture uniform along an axis cannot test that axis.** Seven mutations
survived for that reason (2026-08-02) before a detector existed to name the branches no
fixture reaches. The detector lists them mechanically; the choice between
deleting the code and extending the fixture is made case by case, because
automatic deletion would remove code merely because a test was thin.

**Needles rot silently.** A mutation whose anchor text has changed is never
applied — and an unapplied mutation is scored as a *survivor*, or not counted at
all. A full sweep after two checkpoints (2026-08-15) found **8 rotted needles** out of 426.
Nothing fails loudly in that state; the sweep simply reports clean results for
code nothing attacked.

**An empty index must not be able to look finished.** Repeatedly the same
defect: `status` reported id, path, schema and a timestamp — all true of a
store holding nothing. Every layer now reports coverage, and where coverage is
partial the line names the command that fixes it.

**Attribution is a hash, not a sentence.** Borrowed files record the sha256 of
their source, and a gate re-hashes it. A missing source makes the gate write
`SKIPPED` rather than pass, because "nothing to check" and "nothing wrong" are
the same green otherwise.

## Known limits, stated rather than discovered

**Equal size and equal mtime means no hash is taken**, so a change preserving
both is reported as unchanged. That is the price of the cheap layer being
cheap, and a gate plants the case so the next reader finds it written down.

**`.gitignore` is read, but git is not.** `.git/info/exclude`, `core.excludesFile`
and global ignore rules do not exist for us. A file git ignores through one of
those, we index.

**A backfilled hash is not evidence of an unchanged file.** It was taken now,
with no comparison behind it, so it is stored with its provenance and upgraded
on the first real comparison.

**The graph draws links, it does not infer them.** An inline link that does not
resolve produces no edge: asserting one would be a claim about a node we do not
have.
