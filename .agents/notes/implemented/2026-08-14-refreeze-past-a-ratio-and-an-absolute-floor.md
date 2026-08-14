---
status: implemented
date: 2026-08-14
owner: agent
affects:
  - agentevolver/agent/types.py
commits:
  - 95c4d63
  - 8523cb5
---
# Agent Note: Re-freezing takes both a ratio and an absolute floor

## Problem

Freezing the capability catalog is not free forever. Every registered or withdrawn component
lengthens the `<capability-context-changes>` announcement while the frozen catalog grows
staler. A long evolving session would end up carrying a change log the size of the catalog it
patches — paying for both in input tokens, and asking the model to reconcile two documents on
every step to answer what should be one lookup.

Re-taking the catalog fixes that: the announcement goes back to empty. It costs one cache
write of the whole catalog, which is real money — 2x base at the one-hour TTL — so it cannot
be the answer to every change.

The question is where the trade inverts, and a single threshold gets it wrong at one end or
the other.

## Decision

`Agent._REFREEZE_RATIO = 0.25` and `Agent._REFREEZE_MIN_CHARS = 2_000`. The catalog is
re-taken when the accumulated change text exceeds **both** a quarter of the frozen catalog's
size **and** two thousand characters. Either alone is not enough.

The ratio is the trade: a change log at a quarter of the catalog's size is already a
substantial second document, and re-taking it buys back three quarters of what was being
re-read.

The floor exists because the ratio alone misbehaves on a small catalog. The announcement
carries a per-line prefix — `now available: `, `no longer available, do not call: ` — so
against a two-line catalog the first single change already exceeds 25% of it, almost entirely
in prefix text. Firing there means paying a full cache write to retire a few hundred
characters, which is never the trade.

The delta is measured on the change **sections**, not on the wrapped message. The
`<capability-context-changes>` container and its explanatory line are a fixed ~190 characters
that do not grow with the number of changes; counting them made every small catalog re-freeze
on its first change, which is the opposite of what the ratio is for.

Both are `ClassVar`, so they stay constants of the class rather than becoming pydantic fields
on every agent instance.

## What this rules out

**A single ratio.** Fires on the first change against a small catalog, for the reason above.

**A single absolute threshold.** Two thousand characters of change against a 60,000-character
catalog is noise; re-taking there spends a full write to remove 3% of the announcement.

**A count of changes rather than their size.** A change's cost is its text, and one
withdrawn workflow with a long description costs more than five renamed tools.

**Re-freezing on every change.** That is just not freezing, with extra steps.

**Never re-freezing.** The state the thresholds exist to bound: a session whose change log
grows without limit.

## What would make this wrong

Neither number came from an evolution session — there was none to measure. They came from a
cost argument: a cache write is 2x base and a read is 0.1x, so re-taking pays for itself only
when the announcement is large relative to the catalog. Real data from a long self-evolving
run is what should replace them, and the commit that shipped the freeze named this as a
known gap in exactly those words: the threshold wants real evolution-session data rather
than a guess.

Concretely, these are wrong if any of the following turn up:

- A real session where re-freezing fires and the measured `cache_read` rate goes **down**
  afterwards. The ratio is then too low.
- A real session that ends with an announcement larger than its catalog without ever
  crossing 2,000 characters. The floor is then too high — which would mean the per-change
  text is smaller than assumed.
- Cache pricing changing the 2x-write / 0.1x-read shape. The whole argument is that
  arithmetic.
