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

**One npm dependency, and the tree behind it is gated on its shape rather than
on advisories.** `numpy` is the only Python dependency and `@xenova/transformers`
the only declared JavaScript one, but that single line brings 80 locked packages
and 252 MB. Audited 2026-08-20: `npm ci` is reproducible (no entry lacks an
integrity hash), nothing is copyleft, and two packages may run code at install
time. The gate checks *that* shape — a new dependency, a new licence or a new
install script must be a decision — and deliberately does not run `npm audit`,
because an answer that changes with the network turns red on a day nobody
touched the repository, and a gate that fires for reasons outside the change is
a gate people learn to ignore.

The distinction the audit turned on is between a dependency being *present* and
being *loaded*. Five advisories stand against this tree, one of them critical,
and the critical one never executes: it arrives under a backend that is
imported and never asked to do anything. That was measured by loading the model
the way the worker does and reading the module cache — 13 of 68 packages — not
inferred from the dependency graph, which would have said the opposite.

The same distinction decided what to *do*. Exactly one advisory was on the
execution path, `sharp`: 17 MB of image processing, plus the only install-time
network fetch the lockfile does not describe, in a repository that embeds text
only. It was replaced with a local stub that **throws** — a silent no-op would
turn "someone added image input" into a wrong answer instead of an error — and
the 384-dimension vectors came back **bit-identical**, worst elementwise
difference 0.0 across four inputs including the empty string, which takes a
different path through the tokenizer. The tree went from 68 directories and
252 MB to 15 and 229 MB. The removal was justified by what loads, not by what
the advisory count says, and it was verified against a hash captured before the
change rather than by re-reading the new tree and finding it self-consistent.

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

**Freshness is relative to the catalogue, never to the disk, and per directory
that is inherited whole.** A file changed since the last `scan` is reported
fresh, which is why every answer carries the catalogue's own age beside the
verdict. Grouping those states by directory adds no new observation and cannot
be fresher than what it groups.

**A directory is a path prefix here, not an object.** The content layer holds
files, so "the directory" is the `dirname` of a row. An empty directory on disk
therefore does not exist for us at all, and a directory we have read nothing in
is absent from the count rather than present with zeroes — printing `0 fresh`
for something never read is an empty layer that looks finished.

**There is exactly one writer per row, so two sources cannot yet disagree.**
`backfill` only fills a hash where none exists, the `hash_source` migration only
labels rows that already had one, and `resolve_candidate` keeps the old hash and
says `unconfirmed` rather than guessing. That is a property worth stating,
because it is what makes conflict detection unnecessary here *today* — and it is
the first thing a second writer would take away. A change that lets a fact be
derived from something moving underneath it, or compares a rebuilt store against
an existing one, reintroduces the problem and needs an answer before it lands.

## Where the last five checkpoints come from

CP-19 to CP-23 were specified on 2026-08-20 from ideas read out of three external
projects. Nothing was cloned, installed or added as a dependency; the notes live
in `reports/harvest-2026-08-20.md` with the licence of each source and an explicit
list of what was deliberately left behind.

**Conflict detection belongs before deduplication, not after** (semantica, MIT).
Their pipeline runs `extract → conflict detection → deduplication`. The ordering
is the whole idea: deduplication merges what is *the same*, so it must not run
until something has caught what *claims* to be the same while disagreeing.

This was written up as CP-19 and then **withdrawn the same day**, because reading
the three places that write a hash showed a conflict cannot currently occur — see
the single-writer property above. A gate against something that cannot happen
cannot go red, which is the first trap on this project's own list. The idea is
kept as an open question tied to whichever change introduces a second writer,
not as work to schedule. Recording the retraction next to the idea is the point:
the ordering is still right, the timing was not.

**A summary should describe a directory, not a file** (OpenViking, Apache-2.0).
Summaries generated per directory, with file summaries as inputs aggregated
upward, make the index proportional to the tree rather than to the file count.
On this home area that is the difference between roughly 450 000 summaries and a
few thousand. Two details are load-bearing and are the reason the idea was worth
reading twice: sampling must be deterministic, so that re-summarising an unchanged
tree produces no diff at all, and freshness must count direct children so a
summary that lags can say so instead of looking current. Their own documentation
records an unsolved problem with refreshing parents on every child change, which
is written into CP-20 so it is not inherited by accident.

**"The index is derived" is tested for state, not for answers** (ai-memory, MIT).
Their split — hand-editable markdown as the source of truth, the database as a
rebuildable index — is the split this project already claims, and CP-14 already
compares an updated index against one built from nothing across nine axes. What
that does not cover is ranking: two stores can hold the same rows and still answer
differently, because search fuses and orders. CP-22 is the narrow extension that
runs the same queries against both, with a negative control that must go red.

**Built and measured 2026-08-20: the answers do survive a rebuild.** Nine gates,
five mutations each killed by the gate that names them. The property holds, but
it holds *by construction* -- `search.build` deletes and reinserts, so an
updated store is a rebuilt store. That was predicted in writing before the code,
which is the only reason the green result means anything.

What the checkpoint actually caught was three gates of its own that could not
go red: two comparing a pair of refusals rather than a pair of answers, and one
control comparing a hand-swapped list against itself. The second of those sat
one line from the first and survived the first being fixed, found only because
an unrelated mutation made search refuse. **Fixing one instance of a blind spot
is not fixing the blind spot.**

One real divergence was measured and is correct behaviour rather than a defect:
a store with five of six files embedded ranks the sixth fourth instead of
second. `update` does not fill the vectors -- `embed` is a separate command by
design -- so comparing a half-embedded store against a fully embedded one asks
an unfair question. Independently, that
project arrived at a single writer with a read-only pool, which is locked decision
12 here reached from the other direction; two projects landing in the same place is
weak evidence the decision was right, and no evidence at all that the
implementation is.

**A retrieval change is measured before it is adopted, and this one died at the
first measurement.** CP-21 would have filtered on short summaries and loaded full
content only for survivors, to cut token cost. Step one of its own specification
was to measure what an answer costs today. It costs almost nothing: `search`
returns paths and nothing else — `SELECT rowid, path, bm25(...)`, one line per
hit, 313 characters for a four-hit answer. There is no content in the response to
cascade away.

The idea solves a problem the borrowed-from system has and this one does not,
because *metadata over everything, content where you point* (locked decision 7)
already makes retrieval a cascade whose last step is the reader. Closed as not
applicable rather than deferred.

**Three of the four checkpoints drawn from the harvest did not survive contact
with the code, and all three failed before any of it was written.** CP-19's
premise was false, CP-21's was void, and CP-20's headline number was off by an
order of magnitude when measured. That ratio is the argument for testing a
premise before writing a specification, not against borrowing ideas: the
borrowing cost three measurements and bought two gates plus three
implementations that were never built.

**One point survived CP-20's rejection, and it was measured on its own before
it was built.** Rejecting a checkpoint wholesale is as unexamined as accepting
one: CP-20 was three mechanisms in a coat, and only the sampling and the upward
refresh died on the numbers. Freshness *counting direct children* was left
written down as not-taken, and CP-23 took it — after asking the question that
would have killed it too. The measurement is not "does grouping work", since
grouping always works, but **are directories mixed**: do a directory's direct
children hold more than one state, so that a per-directory view says something
the per-file view did not. Measured 2026-08-20: **54.5 %** of this project's
directories are mixed, against the **2.4 %** of directories where CP-20's
sampling threshold would ever have fired. Sampling addressed a rarity; mixture
is the common case. Same source, same day, opposite verdicts, and the
difference is a number rather than a preference.

CP-23 aggregates nothing upward, which is deliberate: that is the mechanism
CP-20 was rejected for, and it carries the source project's own unsolved write
amplification with it. What it produced instead was a distinction the code did
not previously have. "Behind" turned out to be two facts, not one: a file we
hold and have not re-read is `stale`, and a file the catalogue can see and we
have **never** read is *pending* — invisible to every count CP-12 makes,
because those iterate the rows the content layer has. The first attempt
conflated them and reported a freshly built project as maximally behind; a
number that peaks precisely when nothing is wrong is worse than no number.
